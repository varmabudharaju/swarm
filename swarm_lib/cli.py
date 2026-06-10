"""swarm CLI: validate | args | finish | status | abandon | install | uninstall."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import graph as graph_mod
from . import install as install_mod
from . import paths, runs


def cmd_validate(a) -> int:
    gr = paths.read_json(a.graph)
    if gr is None:
        print(f"cannot read graph: {a.graph}")
        return 1
    if a.print_hash:
        print(graph_mod.compute_hash(gr))
        return 0
    issues = graph_mod.validate(gr)
    for i in issues:
        print(f"{i['level']}[{i['code']}]: {i['msg']}")
    if graph_mod.errors(issues):
        return 1
    print(f"ok: {len(gr.get('tasks', []))} tasks, hash {graph_mod.compute_hash(gr)}")
    return 0


def cmd_args(a) -> int:
    gpath = Path(a.graph).resolve()
    gr = paths.read_json(gpath)
    if gr is None:
        print(f"cannot read graph: {gpath}", file=sys.stderr)
        return 1
    errs = graph_mod.errors(graph_mod.validate(gr))
    if errs:
        for i in errs:
            print(f"error[{i['code']}]: {i['msg']}", file=sys.stderr)
        return 1
    rd = gpath.parent
    completed = {}
    if a.resume:
        import time as _time
        owner = os.environ.get("CLAUDE_SESSION_ID", f"cli-{os.getpid()}-{_time.time()}")
        if not runs.take_lock(rd, owner):
            print("resume.lock held by another session (fresh) - refusing. "
                  "Wait, or delete resume.lock if you are sure.", file=sys.stderr)
            return 1
        found, bad = runs.scan_results(rd, gr.get("graph_hash"))
        for b in bad:
            print(f"warning: ignoring bad result file {b}", file=sys.stderr)
        completed = {k: {"summary": v.get("summary", "")} for k, v in found.items()}
    out = {
        "run_dir": str(rd),
        "graph_hash": gr.get("graph_hash"),
        "results_dir": str(runs.results_dir(rd)),
        "agent_ceiling": gr.get("agent_ceiling"),
        "tasks": [{
            "id": t["id"], "title": t.get("title", ""), "type": t["type"],
            "prompt": t["prompt"], "deps": t.get("deps", []),
            "agent_type": t.get("agent_type"), "isolation": t.get("isolation"),
            "schema": t.get("schema"), "max_retries": t.get("max_retries", 1),
            "packet_path": str(rd / t.get("packet", f"packets/{t['id']}.md")),
        } for t in gr["tasks"]],
        "completed": completed,
    }
    print(json.dumps(out))
    return 0


def cmd_finish(a) -> int:
    rd = Path(a.run_dir).resolve()
    runs.write_state(rd, a.status)
    runs.release_lock(rd)
    print(f"run-state written: {a.status}")
    return 0


def cmd_status(a) -> int:
    rd = Path(a.run_dir).resolve()
    gr = paths.read_json(rd / "graph.json")
    if not gr:
        print(f"no graph.json in {rd}")
        return 1
    completed, bad = runs.scan_results(rd, gr.get("graph_hash"))
    st = runs.read_state(rd)
    status = (st or {}).get("status", "in progress / interrupted")
    print(f"run    {rd.name}  [{status}]")
    extra = f", {len(bad)} bad result files" if bad else ""
    print(f"tasks  {len(completed)}/{len(gr['tasks'])} done{extra}")
    for t in gr["tasks"]:
        mark = "done" if t["id"] in completed else "pending"
        print(f"  [{mark:>7}] {t['type']:<11} {t['id']:<16} {t.get('title', '')[:56]}")
    return 0


def cmd_abandon(a) -> int:
    runs.abandon(Path(a.run_dir).resolve())
    print("abandoned")
    return 0


def cmd_install(a) -> int:
    try:
        install_mod.install(a.settings, a.claude_dir)
    except install_mod.SettingsError as e:
        print(str(e))
        return 1
    print(f"swarm installed: hooks in {a.settings}; skill/workflow/agents in {a.claude_dir}")
    print("Restart your Claude Code session to activate.")
    return 0


def cmd_uninstall(a) -> int:
    try:
        install_mod.uninstall(a.settings, a.claude_dir)
    except install_mod.SettingsError as e:
        print(str(e))
        return 1
    print("swarm removed")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="swarm",
                                     description="Graph-first multi-agent orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    home_claude = str(Path.home() / ".claude")

    p = sub.add_parser("validate")
    p.add_argument("graph")
    p.add_argument("--print-hash", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("args")
    p.add_argument("graph")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(fn=cmd_args)

    p = sub.add_parser("finish")
    p.add_argument("run_dir")
    p.add_argument("--status", required=True,
                   choices=["completed", "paused_for_budget", "failed-partial"])
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("status")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("abandon")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_abandon)

    for name, fn in (("install", cmd_install), ("uninstall", cmd_uninstall)):
        p = sub.add_parser(name)
        p.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
        p.add_argument("--claude-dir", default=home_claude)
        p.set_defaults(fn=fn)

    a = parser.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
