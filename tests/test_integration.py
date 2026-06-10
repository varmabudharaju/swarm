import glob
import json
import os
import subprocess
import sys

from conftest import make_run, task

from swarm_lib import marker, paths, runs


def run_hook(payload, home):
    env = dict(os.environ, SWARM_HOME=str(home))
    return subprocess.run([sys.executable, "-m", "swarm_lib.hook"], input=payload,
                          capture_output=True, text=True, env=env, timeout=30)


def test_checkpoint_cycle_end_to_end(tmp_path, swarm_home):
    """graph -> simulated SubagentStop -> result file -> args --resume sees it."""
    from swarm_lib import graph as g
    rd = make_run(tmp_path, tasks=[task("a"), task("b", deps=["a"])])
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)

    agent_tp = tmp_path / "agent.jsonl"
    mk = marker.build(str(rd), "a", gr["graph_hash"])
    agent_tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": mk + "\ngo"}]}}) + "\n" + json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "x",
             "input": {"summary": "a complete"}}]}}) + "\n")
    res = run_hook(json.dumps({"hook_event_name": "SubagentStop", "agent_id": "ag1",
                               "agent_transcript_path": str(agent_tp)}), swarm_home)
    assert res.returncode == 0
    assert paths.read_json(runs.results_dir(rd) / "a.json")["summary"] == "a complete"

    env = dict(os.environ, SWARM_HOME=str(swarm_home))
    out = subprocess.run([sys.executable, "-m", "swarm_lib.cli", "args",
                          str(rd / "graph.json"), "--resume"],
                         capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0
    assert json.loads(out.stdout)["completed"] == {"a": {"summary": "a complete"}}


def test_garbage_stdin_exits_zero(tmp_path):
    res = run_hook("GARBAGE", tmp_path / "home")
    assert res.returncode == 0 and res.stdout == ""


def test_node_suite_passes():
    res = subprocess.run(["node", "--test", *glob.glob("tests/node/*.test.mjs")],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
