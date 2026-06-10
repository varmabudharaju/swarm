"""SWARM_HOME resolution, run-dir layout, atomic JSON I/O."""
import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("SWARM_HOME", str(Path.home() / ".claude" / "swarm")))


def project_slug(project) -> str:
    return str(Path(project).resolve()).replace("-", "__").replace("/", "-")


def runs_dir(project) -> Path:
    return home() / "runs" / project_slug(project)


def run_dir(project, run_id) -> Path:
    return runs_dir(project) / run_id


def log_path() -> Path:
    return home() / "swarm.log"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path, obj, indent=None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent), encoding="utf-8")
    tmp.replace(p)
