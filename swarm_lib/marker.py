"""SWARM-TASK marker: first line of every swarm task prompt; parsed by the checkpoint hook."""
import re

PREFIX = "SWARM-TASK"
_RE = re.compile(r"^SWARM-TASK run=(?P<run>.*?) task=(?P<task>\S+) hash=(?P<hash>\S+)\s*$")


def build(run_dir, task_id, graph_hash) -> str:
    return f"{PREFIX} run={run_dir} task={task_id} hash={graph_hash}"


def parse(text):
    for line in (text or "").splitlines():
        m = _RE.match(line.strip())
        if m:
            return {"run": m.group("run"), "task": m.group("task"), "hash": m.group("hash")}
    return None
