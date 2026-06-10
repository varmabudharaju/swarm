import json

from conftest import make_run, task

from swarm_lib import cli, paths, runs


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


def test_args_resume_takes_lock_and_loads_completed(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b", deps=["a"])])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": gr["graph_hash"],
                             "status": "ok", "output": {"summary": "done a"},
                             "summary": "done a", "ts": 0})
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["completed"] == {"a": {"summary": "done a"}}
    assert runs.lock_path(rd).exists()
    # second resume refused while lock fresh
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
