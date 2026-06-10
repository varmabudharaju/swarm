"""swarm hooks: SubagentStop checkpoint writer + SessionStart resume nag. Fail-open."""
import datetime
import json
import sys
import time
import traceback
from pathlib import Path

from . import extract, marker, paths, runs


def handle_subagent_stop(event):
    tp = event.get("agent_transcript_path") or (event.get("_extra") or {}).get(
        "agent_transcript_path")
    if not tp or not Path(tp).exists():
        return None
    fu = extract.first_user(tp)
    mk = marker.parse(fu or "")
    if not mk:
        return None  # not a swarm task agent
    info = extract.extract_output(tp, event.get("last_assistant_message"))
    run_dir = Path(mk["run"])
    graph = paths.read_json(run_dir / "graph.json")
    if not isinstance(graph, dict):
        return None
    if mk["task"] not in {t.get("id") for t in graph.get("tasks", [])}:
        return None
    out = info["output"]
    summary = str(out.get("summary", "") if isinstance(out, dict) else out)[:2000]
    result = {
        "version": runs.RESULT_VERSION,
        "task": mk["task"],
        "hash": mk["hash"],
        "status": "ok",
        "structured": info["structured"],
        "output": out,
        "summary": summary,
        "agent_id": event.get("agent_id"),
        "ts": time.time(),
    }
    paths.write_json_atomic(runs.results_dir(run_dir) / f"{mk['task']}.json", result, indent=1)
    return None


def handle_session_start(event):
    if event.get("source") not in ("startup", "clear"):
        return None
    pend = runs.pending_runs(event.get("cwd") or ".")
    if not pend:
        return None
    r = pend[0]
    if r["total"] and r["done"] >= r["total"]:
        line = (f"[swarm] Run '{r['run_id']}' has all {r['total']} tasks done but was never "
                "finalized - say '/swarm resume' to finalize (synthesis only).")
    elif r["status"] == "paused_for_budget":
        line = (f"[swarm] Run '{r['run_id']}' is paused for budget at {r['done']}/{r['total']} "
                "tasks - say '/swarm resume' to continue, or 'swarm abandon' to drop it.")
    else:
        line = (f"[swarm] Interrupted swarm run '{r['run_id']}' ({r['done']}/{r['total']} tasks "
                "done) - say '/swarm resume' to continue, or 'swarm abandon' to drop it.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}}


HANDLERS = {"SubagentStop": handle_subagent_stop, "SessionStart": handle_session_start}


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        fn = HANDLERS.get(event.get("hook_event_name"))
        out = fn(event) if fn else None
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
    except BaseException:
        try:
            paths.home().mkdir(parents=True, exist_ok=True)
            with open(paths.log_path(), "a") as f:
                f.write(f"--- {datetime.datetime.now().isoformat()}\n{traceback.format_exc()}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
