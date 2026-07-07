import json

from conftest import make_run, task

from swarm_lib import cli, install, paths, runs


def graph_with_hash(tmp_path, tasks):
    from swarm_lib import graph as g
    rd = make_run(tmp_path, tasks=tasks)
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    return rd, gr


def test_validate_ok_and_fail(tmp_path, capsys):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["validate", str(rd / "graph.json")]) == 0
    bad = paths.read_json(rd / "graph.json")
    bad["tasks"][0]["deps"] = ["ghost"]
    paths.write_json_atomic(rd / "graph.json", bad)
    assert cli.main(["validate", str(rd / "graph.json")]) == 1
    assert "dangling" in capsys.readouterr().out


def test_validate_print_hash(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["validate", str(rd / "graph.json"), "--print-hash"]) == 0
    assert gr["graph_hash"] in capsys.readouterr().out


def test_args_builds_workflow_args(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b", deps=["a"])])
    assert cli.main(["args", str(rd / "graph.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_dir"] == str(rd)
    assert out["graph_hash"] == gr["graph_hash"]
    assert out["results_dir"] == str(rd / "results")
    assert out["tasks"][0]["packet_path"] == str(rd / "packets" / "a.md")
    assert out["completed"] == {}


def test_args_resume_takes_lock_and_loads_completed(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b", deps=["a"])])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": gr["graph_hash"],
                             "status": "ok", "output": {"summary": "done a"},
                             "summary": "done a", "ts": 0})
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["completed"] == {"a": {"summary": "done a"}}
    assert runs.lock_path(rd).exists()
    # second resume refused while lock fresh (different pid-based owner)
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 1


def test_finish_writes_state_and_releases_lock(tmp_path):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    runs.take_lock(rd, "x")
    assert cli.main(["finish", str(rd), "--status", "completed"]) == 0
    assert runs.read_state(rd)["status"] == "completed"
    assert not runs.lock_path(rd).exists()


def test_status_lists_tasks(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b")])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": gr["graph_hash"],
                             "status": "ok", "output": {}, "summary": "s", "ts": 0})
    assert cli.main(["status", str(rd)]) == 0
    out = capsys.readouterr().out
    assert "1/2" in out and "done" in out and "pending" in out


def test_abandon(tmp_path):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["abandon", str(rd)]) == 0
    assert runs.read_state(rd)["status"] == "abandoned"


# --- Item 1: lock owner env var + refusal message ---

def test_resume_reentrant_same_session(tmp_path, capsys, monkeypatch):
    """Same CLAUDE_CODE_SESSION_ID → re-entrant, both calls return 0."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-A")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0
    capsys.readouterr()
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0


def test_resume_refused_different_session_stderr_rm(tmp_path, capsys, monkeypatch):
    """sess-A takes lock; sess-B is refused and stderr mentions rm '."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-A")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-B")
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 1
    err = capsys.readouterr().err
    assert "rm '" in err


# --- Item 2: refuse missing graph_hash ---

def test_args_refuses_missing_graph_hash(tmp_path, capsys):
    """cmd_args returns 1 and prints error when graph_hash absent."""
    rd = make_run(tmp_path, tasks=[task("a")], graph_hash=None)
    gr = paths.read_json(rd / "graph.json")
    gr.pop("graph_hash", None)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json")]) == 1
    err = capsys.readouterr().err
    assert "graph_hash" in err and "swarm validate --print-hash" in err


def test_validate_warns_missing_graph_hash(tmp_path, capsys):
    """cmd_validate prints warn[hash] but still exits 0 when graph_hash absent."""
    rd = make_run(tmp_path, tasks=[task("a")], graph_hash=None)
    gr = paths.read_json(rd / "graph.json")
    gr.pop("graph_hash", None)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["validate", str(rd / "graph.json")]) == 0
    out = capsys.readouterr().out
    assert "warn[hash]" in out and "swarm validate --print-hash" in out


# --- Item 3: non-dict graph guard ---

def test_args_non_dict_graph_exits_1(tmp_path, capsys):
    """graph file containing [1,2] → exit 1, no traceback."""
    import json as _json
    gpath = tmp_path / "graph.json"
    gpath.write_text(_json.dumps([1, 2]))
    result = cli.main(["args", str(gpath)])
    assert result == 1
    err = capsys.readouterr().err
    assert "cannot read graph" in err


def test_validate_non_dict_graph_exits_1(tmp_path, capsys):
    """validate: graph file containing [1,2] → exit 1, no traceback."""
    import json as _json
    gpath = tmp_path / "graph.json"
    gpath.write_text(_json.dumps([1, 2]))
    result = cli.main(["validate", str(gpath)])
    assert result == 1
    assert "cannot read graph" in capsys.readouterr().out


# --- Item 4: finish/abandon refuse missing runs ---

def test_finish_refuses_missing_run(tmp_path, capsys):
    """finish on a dir with no graph.json → exit 1."""
    rd = tmp_path / "no-such-run"
    rd.mkdir()
    assert cli.main(["finish", str(rd), "--status", "completed"]) == 1
    assert "no run at" in capsys.readouterr().out


def test_abandon_refuses_missing_run(tmp_path, capsys):
    """abandon on a dir with no graph.json → exit 1."""
    rd = tmp_path / "no-such-run"
    rd.mkdir()
    assert cli.main(["abandon", str(rd)]) == 1
    assert "no run at" in capsys.readouterr().out


def test_args_includes_model_and_session_model(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a", model="haiku"), task("b")])
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json"), "--session-model", "fable"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_model"] == "fable"
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["a"]["model"] == "haiku"
    assert by_id["b"]["model"] is None


def test_args_passes_allowed_models_through(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a", model="sonnet")])
    gr = paths.read_json(rd / "graph.json")
    gr["allowed_models"] = ["sonnet", "opus"]
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json"), "--session-model", "opus"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["allowed_models"] == ["sonnet", "opus"]


def test_args_allowed_models_null_when_absent(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a")])
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["allowed_models"] is None


def test_args_rejects_bad_session_model(tmp_path, swarm_home):
    import pytest
    from swarm_lib import cli

    with pytest.raises(SystemExit):
        cli.main(["args", "/nonexistent.json", "--session-model", "gpt5"])


def test_args_passes_effort_through(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a", effort="low"), task("b")])
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["a"]["effort"] == "low"
    assert by_id["b"]["effort"] is None


def test_gc_lists_by_default_and_deletes_with_flag(tmp_path, swarm_home, capsys):
    from pathlib import Path

    from swarm_lib import cli
    from test_runs import _aged_run

    victim = _aged_run(tmp_path, "old-completed", "completed", age_days=30)
    survivor = _aged_run(tmp_path, "old-paused", "paused_for_budget", age_days=30)

    # default: dry-run - lists, deletes nothing
    assert cli.main(["gc"]) == 0
    out = capsys.readouterr().out
    assert "would delete" in out and "old-completed" in out and "--delete" in out
    assert victim.exists()

    # --delete: removes the candidate, never the resumable run
    assert cli.main(["gc", "--delete"]) == 0
    out = capsys.readouterr().out
    assert "deleting" in out
    assert not victim.exists()
    assert survivor.exists()

    # nothing left eligible
    assert cli.main(["gc"]) == 0
    assert "nothing eligible" in capsys.readouterr().out


# --- install-workflow: plugin bootstrap must never break session start ---

def test_install_workflow_dispatch_happy_path(tmp_path, swarm_home):
    """cli.main(["install-workflow", ...]) exits 0 and writes the workflow file."""
    cd = tmp_path / "claude"
    cd.mkdir()
    assert cli.main(["install-workflow", "--claude-dir", str(cd)]) == 0
    wf = cd / "workflows" / "swarm-run.js"
    assert wf.exists()
    assert wf.read_text().startswith("export const meta")


def test_install_workflow_fails_open_and_logs(tmp_path, swarm_home, monkeypatch):
    """Runs on every plugin SessionStart: any error must be swallowed (exit 0)
    and recorded as one line in the swarm log, never a raw traceback into the hook."""
    def boom(_claude_dir):
        raise OSError("boom-install-workflow")

    monkeypatch.setattr(install, "install_workflow", boom)
    cd = tmp_path / "claude"
    cd.mkdir()
    assert cli.main(["install-workflow", "--claude-dir", str(cd)]) == 0
    log = paths.log_path()
    assert log.exists()
    assert "boom-install-workflow" in log.read_text()


# --- install / uninstall error paths surfaced through cli.main dispatch ---

def test_install_dispatch_corrupted_settings_exits_1(tmp_path, swarm_home, capsys):
    """Invalid settings.json → cmd_install prints the SettingsError and exits 1."""
    sp = tmp_path / "settings.json"
    sp.write_text('{"hooks": [BROKEN')
    cd = tmp_path / "claude"
    cd.mkdir()
    assert cli.main(["install", "--settings", str(sp), "--claude-dir", str(cd)]) == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_uninstall_dispatch_corrupted_settings_exits_1(tmp_path, swarm_home, capsys):
    """Invalid settings.json → cmd_uninstall prints the SettingsError and exits 1."""
    sp = tmp_path / "settings.json"
    sp.write_text('{"hooks": [BROKEN')
    cd = tmp_path / "claude"
    cd.mkdir()
    assert cli.main(["uninstall", "--settings", str(sp), "--claude-dir", str(cd)]) == 1
    assert "not valid JSON" in capsys.readouterr().out
