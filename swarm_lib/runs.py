"""Run-dir state: results scanning, run-state.json, resume locks, pending-run detection."""
import time
from pathlib import Path

from . import paths

RESULT_VERSION = 1
NAG_MAX_AGE_DAYS = 7
LOCK_STALE_HOURS = 2.0


def results_dir(run_dir) -> Path:
    return Path(run_dir) / "results"


def packets_dir(run_dir) -> Path:
    return Path(run_dir) / "packets"


def state_path(run_dir) -> Path:
    return Path(run_dir) / "run-state.json"


def lock_path(run_dir) -> Path:
    return Path(run_dir) / "resume.lock"


def scan_results(run_dir, graph_hash=None):
    completed, bad = {}, []
    d = results_dir(run_dir)
    if not d.is_dir():
        return completed, bad
    for p in sorted(d.glob("*.json")):
        r = paths.read_json(p)
        if (not isinstance(r, dict) or r.get("version") != RESULT_VERSION
                or not r.get("task") or r.get("task") != p.stem):
            bad.append(p.name)
            continue
        if graph_hash and r.get("hash") != graph_hash:
            bad.append(p.name)
            continue
        completed[r["task"]] = r
    return completed, bad


def read_state(run_dir):
    return paths.read_json(state_path(run_dir))


def write_state(run_dir, status, extra=None) -> None:
    obj = {"status": status, "ts": time.time()}
    if extra:
        obj.update(extra)
    paths.write_json_atomic(state_path(run_dir), obj, indent=2)


def abandon(run_dir) -> None:
    write_state(run_dir, "abandoned")


def take_lock(run_dir, owner, stale_hours=LOCK_STALE_HOURS) -> bool:
    existing = paths.read_json(lock_path(run_dir))
    if existing and existing.get("owner") == owner:
        paths.write_json_atomic(lock_path(run_dir), {"owner": owner, "ts": time.time()})
        return True
    if existing and time.time() - existing.get("ts", 0) < stale_hours * 3600:
        return False
    paths.write_json_atomic(lock_path(run_dir), {"owner": owner, "ts": time.time()})
    return True


def release_lock(run_dir) -> None:
    lock_path(run_dir).unlink(missing_ok=True)


def pending_runs(project) -> list:
    """Runs worth nagging about: interrupted (no run-state) or paused_for_budget,
    younger than NAG_MAX_AGE_DAYS."""
    out = []
    rd = paths.runs_dir(project)
    if not rd.is_dir():
        return out
    for d in sorted(rd.iterdir()):
        if not d.is_dir() or not (d / "graph.json").exists():
            continue
        status = (read_state(d) or {}).get("status")
        if status in ("completed", "abandoned", "failed-partial"):
            continue
        mtimes = [d.stat().st_mtime]
        rdir = results_dir(d)
        if rdir.is_dir():
            mtimes.append(rdir.stat().st_mtime)
        if (time.time() - max(mtimes)) / 86400 > NAG_MAX_AGE_DAYS:
            continue
        graph = paths.read_json(d / "graph.json") or {}
        completed, _ = scan_results(d, graph.get("graph_hash"))
        out.append({"run_id": d.name, "run_dir": str(d), "done": len(completed),
                    "total": len(graph.get("tasks", [])),
                    "status": status or "interrupted"})
    return out
