import time

from conftest import make_run, task

from swarm_lib import paths, runs


def write_result(rd, tid, hash="h1", version=1, summary="s"):
    paths.write_json_atomic(runs.results_dir(rd) / f"{tid}.json",
                            {"version": version, "task": tid, "hash": hash,
                             "status": "ok", "output": {"summary": summary},
                             "summary": summary, "ts": time.time()})


def test_scan_results_filters_version_and_hash(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a"), task("b")])
    write_result(rd, "a")
    write_result(rd, "b", hash="WRONG")
    write_result(rd, "c", version=2)
    completed, bad = runs.scan_results(rd, "h1")
    assert list(completed) == ["a"]
    assert sorted(bad) == ["b.json", "c.json"]


def test_state_roundtrip_and_abandon(tmp_path):
    rd = make_run(tmp_path)
    assert runs.read_state(rd) is None
    runs.write_state(rd, "completed")
    assert runs.read_state(rd)["status"] == "completed"
    runs.abandon(rd)
    assert runs.read_state(rd)["status"] == "abandoned"


def test_lock_take_and_stale(tmp_path):
    rd = make_run(tmp_path)
    assert runs.take_lock(rd, "s1") is True
    assert runs.take_lock(rd, "s2") is False  # fresh lock held
    # stale lock can be overridden
    paths.write_json_atomic(runs.lock_path(rd), {"owner": "s1", "ts": time.time() - 3 * 3600})
    assert runs.take_lock(rd, "s2") is True
    runs.release_lock(rd)
    assert not runs.lock_path(rd).exists()


def test_pending_runs(tmp_path):
    proj = str(tmp_path / "proj")
    rd1 = make_run(tmp_path, "r1", tasks=[task("a"), task("b")])      # interrupted
    write_result(rd1, "a")
    rd2 = make_run(tmp_path, "r2", tasks=[task("a")])                  # completed
    runs.write_state(rd2, "completed")
    rd3 = make_run(tmp_path, "r3", tasks=[task("a")])                  # paused
    runs.write_state(rd3, "paused_for_budget")
    pend = runs.pending_runs(proj)
    by_id = {p["run_id"]: p for p in pend}
    assert set(by_id) == {"r1", "r3"}
    assert by_id["r1"]["done"] == 1 and by_id["r1"]["total"] == 2
    assert by_id["r1"]["status"] == "interrupted"
    assert by_id["r3"]["status"] == "paused_for_budget"


def test_pending_runs_ages_out(tmp_path):
    import os
    proj = str(tmp_path / "proj")
    rd = make_run(tmp_path, "old", tasks=[task("a")])
    old = time.time() - 8 * 86400
    os.utime(rd, (old, old))
    assert runs.pending_runs(proj) == []
