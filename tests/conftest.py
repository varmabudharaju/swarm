import json
import pytest


@pytest.fixture(autouse=True)
def swarm_home(tmp_path, monkeypatch):
    home = tmp_path / "swarm-home"
    monkeypatch.setenv("SWARM_HOME", str(home))
    return home


def make_run(tmp_path, run_id="r1", tasks=None, graph_hash="h1"):
    """Create a run dir with graph.json; returns the run dir Path."""
    from swarm_lib import paths

    rd = paths.run_dir(str(tmp_path / "proj"), run_id)
    rd.mkdir(parents=True, exist_ok=True)
    graph = {"version": 1, "run_id": run_id, "goal": "g", "graph_hash": graph_hash,
             "project": str(tmp_path / "proj"), "tasks": tasks or []}
    paths.write_json_atomic(rd / "graph.json", graph)
    return rd


def task(id, type="research", deps=None, prompt="Investigate the thing thoroughly and report.",
         agent_type="swarm-reader", **kw):
    schema = kw.pop("schema", {"type": "object", "properties": {
        "summary": {"type": "string", "maxLength": 2000}}, "required": ["summary"]})
    return {"id": id, "title": id, "type": type, "prompt": prompt, "deps": deps or [],
            "agent_type": agent_type, "packet": f"packets/{id}.md", "schema": schema,
            "max_retries": 1, **kw}
