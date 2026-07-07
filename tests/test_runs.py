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


def test_scan_results_json_array_is_bad(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a")])
    rdir = runs.results_dir(rd)
    rdir.mkdir(parents=True, exist_ok=True)
    paths.write_json_atomic(rdir / "a.json", [{"version": 1, "task": "a"}])
    completed, bad = runs.scan_results(rd)
    assert "a.json" in bad
    assert "a" not in completed


def test_scan_results_missing_task_is_bad(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a")])
    rdir = runs.results_dir(rd)
    rdir.mkdir(parents=True, exist_ok=True)
    paths.write_json_atomic(rdir / "a.json", {"version": 1, "hash": "h1", "status": "ok"})
    completed, bad = runs.scan_results(rd)
    assert "a.json" in bad
    assert "a" not in completed


def test_scan_results_task_mismatch_is_bad(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a"), task("b")])
    write_result(rd, "a")
    # Write b.json but claim task is "a"
    rdir = runs.results_dir(rd)
    paths.write_json_atomic(rdir / "b.json", {"version": 1, "task": "a", "hash": "h1",
                                               "status": "ok", "output": {"summary": "s"},
                                               "summary": "s", "ts": time.time()})
    completed, bad = runs.scan_results(rd, "h1")
    assert "b.json" in bad
    # b.json claiming to be task "a" must NOT overwrite the real a.json entry
    assert completed.get("a") is not None
    assert "b" not in completed


def test_take_lock_same_owner_refreshes(tmp_path):
    rd = make_run(tmp_path)
    assert runs.take_lock(rd, "s1") is True
    assert runs.take_lock(rd, "s1") is True   # same owner re-takes fresh lock
    assert runs.take_lock(rd, "s2") is False  # different owner blocked


def test_pending_runs_uses_results_mtime(tmp_path):
    import os
    proj = str(tmp_path / "proj")
    rd = make_run(tmp_path, "stale", tasks=[task("a")])
    # Create results/ dir first (fresh mtime)
    rdir = runs.results_dir(rd)
    rdir.mkdir(parents=True, exist_ok=True)
    # Then age the run dir itself (8 days old) — AFTER results/ exists so os.utime
    # on rd doesn't get overwritten by mkdir touching the parent
    old = time.time() - 8 * 86400
    os.utime(rd, (old, old))
    # results/ dir retains a fresh mtime - run should still appear in pending
    assert rdir.stat().st_mtime > time.time() - 86400  # results/ is fresh
    pend = runs.pending_runs(proj)
    assert any(p["run_id"] == "stale" for p in pend)


def _aged_run(tmp_path, run_id, status=None, age_days=30.0, fresh_lock=False, project="projA"):
    """Run dir with a chosen state whose age (dir mtime + state ts) is age_days."""
    import json
    import os
    import time as _time

    from swarm_lib import paths, runs

    rd = paths.run_dir(str(tmp_path / project), run_id)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "graph.json").write_text('{"version": 1, "tasks": []}')
    old = _time.time() - age_days * 86400
    if status is not None:
        (rd / "run-state.json").write_text(json.dumps({"status": status, "ts": old}))
    if fresh_lock:
        (rd / "resume.lock").write_text(json.dumps({"owner": "x", "ts": _time.time()}))
    os.utime(rd, (old, old))
    return rd


def test_gc_candidates_selects_only_old_terminal_unlocked(tmp_path, swarm_home):
    from swarm_lib import runs

    keep_young = _aged_run(tmp_path, "young-completed", "completed", age_days=2)
    take_completed = _aged_run(tmp_path, "old-completed", "completed", age_days=30)
    take_abandoned = _aged_run(tmp_path, "old-abandoned", "abandoned", age_days=30)
    keep_failed = _aged_run(tmp_path, "old-failed", "failed-partial", age_days=30)
    keep_paused = _aged_run(tmp_path, "old-paused", "paused_for_budget", age_days=30)
    keep_interrupted = _aged_run(tmp_path, "old-interrupted", None, age_days=30)
    keep_locked = _aged_run(tmp_path, "old-locked", "completed", age_days=30, fresh_lock=True)

    got = {c["run_id"] for c in runs.gc_candidates(days=14)}
    assert got == {"old-completed", "old-abandoned"}


def test_gc_candidates_include_failed_and_days_knobs(tmp_path, swarm_home):
    from swarm_lib import runs

    _aged_run(tmp_path, "old-failed", "failed-partial", age_days=30)
    _aged_run(tmp_path, "older-completed", "completed", age_days=30, project="projB")

    assert {c["run_id"] for c in runs.gc_candidates(days=14, include_failed=True)} == \
        {"old-failed", "older-completed"}
    # a huge --days keeps everything
    assert runs.gc_candidates(days=90, include_failed=True) == []
