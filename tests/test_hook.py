import io
import json

from conftest import make_run, task

from swarm_lib import hook, marker, paths, runs


def agent_transcript(tmp_path, first_user, final_structured=None, final_text="done"):
    tp = tmp_path / "agent.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": first_user}]}}]
    if final_structured is not None:
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "x", "input": final_structured}]}})
    else:
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": final_text}]}})
    tp.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return str(tp)


def stop_event(tp, agent_id="a1"):
    return {"hook_event_name": "SubagentStop", "agent_id": agent_id,
            "agent_transcript_path": tp, "session_id": "s1", "cwd": "/tmp"}


def test_checkpoint_written_for_swarm_agent(tmp_path):
    rd = make_run(tmp_path, tasks=[task("t1")])
    mk = marker.build(str(rd), "t1", "h1")
    tp = agent_transcript(tmp_path, mk + "\ndo it", {"summary": "found stuff", "items": [1]})
    hook.handle_subagent_stop(stop_event(tp))
    r = paths.read_json(runs.results_dir(rd) / "t1.json")
    assert r["version"] == 1 and r["task"] == "t1" and r["hash"] == "h1"
    assert r["structured"] is True
    assert r["output"]["summary"] == "found stuff"
    assert r["summary"] == "found stuff"


def test_text_fallback_summary_capped(tmp_path):
    rd = make_run(tmp_path, tasks=[task("t2")])
    mk = marker.build(str(rd), "t2", "h1")
    tp = agent_transcript(tmp_path, mk + "\ngo", final_text="x" * 5000)
    hook.handle_subagent_stop(stop_event(tp))
    r = paths.read_json(runs.results_dir(rd) / "t2.json")
    assert r["structured"] is False
    assert len(r["summary"]) == 2000


def test_non_swarm_agent_ignored(tmp_path):
    tp = agent_transcript(tmp_path, "ordinary prompt, no marker")
    assert hook.handle_subagent_stop(stop_event(tp)) is None
    # nothing written anywhere under swarm home
    assert not list((paths.home()).rglob("*.json"))


def test_marker_pointing_at_missing_run_ignored(tmp_path):
    mk = marker.build(str(tmp_path / "ghost-run"), "t1", "h1")
    tp = agent_transcript(tmp_path, mk + "\ngo")
    assert hook.handle_subagent_stop(stop_event(tp)) is None


def test_task_not_in_graph_ignored(tmp_path):
    """Marker task='zz' on a run whose graph only has task 't1' → no results file."""
    rd = make_run(tmp_path, tasks=[task("t1")])
    mk = marker.build(str(rd), "zz", "h1")
    tp = agent_transcript(tmp_path, mk + "\ngo", {"summary": "result"})
    assert hook.handle_subagent_stop(stop_event(tp)) is None
    # no result file for "zz"
    assert not (runs.results_dir(rd) / "zz.json").exists()


def test_dict_output_long_summary_capped_at_2000(tmp_path):
    """Dict output with 5000-char summary string → stored summary length 2000."""
    rd = make_run(tmp_path, tasks=[task("t3")])
    mk = marker.build(str(rd), "t3", "h1")
    tp = agent_transcript(tmp_path, mk + "\ngo", {"summary": "y" * 5000})
    hook.handle_subagent_stop(stop_event(tp))
    r = paths.read_json(runs.results_dir(rd) / "t3.json")
    assert r["structured"] is True
    assert len(r["summary"]) == 2000


def test_dict_output_with_dict_summary_coerced_to_string(tmp_path):
    """Dict output with a dict summary value → stored summary is a string."""
    rd = make_run(tmp_path, tasks=[task("t4")])
    mk = marker.build(str(rd), "t4", "h1")
    tp = agent_transcript(tmp_path, mk + "\ngo", {"summary": {"a": 1}})
    hook.handle_subagent_stop(stop_event(tp))
    r = paths.read_json(runs.results_dir(rd) / "t4.json")
    assert r["structured"] is True
    assert isinstance(r["summary"], str)


def test_session_start_nags_interrupted(tmp_path, monkeypatch):
    rd = make_run(tmp_path, tasks=[task("a"), task("b")])
    proj = str(tmp_path / "proj")
    out = hook.handle_session_start({"hook_event_name": "SessionStart",
                                     "source": "startup", "cwd": proj})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "swarm" in ctx and "0/2" in ctx and "resume" in ctx


def test_session_start_finalize_when_all_done(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a")])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": "h1", "status": "ok",
                             "output": {}, "summary": "s", "ts": 0})
    out = hook.handle_session_start({"source": "clear", "cwd": str(tmp_path / "proj")})
    assert "finalize" in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_quiet_paths(tmp_path):
    assert hook.handle_session_start({"source": "resume", "cwd": str(tmp_path)}) is None
    assert hook.handle_session_start({"source": "startup", "cwd": str(tmp_path)}) is None


def test_main_fail_open(monkeypatch, capsys, swarm_home):
    monkeypatch.setattr("sys.stdin", io.StringIO("NOT JSON"))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


# --- Item 6: hook id-charset re-check ---

def test_evil_task_id_rejected_no_file_written(tmp_path):
    """Marker with task='../evil' → no result file written, result is None."""
    rd = make_run(tmp_path, tasks=[task("t1")])
    # Manually build a marker with an evil task id
    mk_text = f"SWARM-TASK run={rd} task=../evil hash=h1"
    tp = agent_transcript(tmp_path, mk_text + "\ndo it", {"summary": "evil"})
    result = hook.handle_subagent_stop(stop_event(tp))
    assert result is None
    # no result file written for the evil task
    assert not (runs.results_dir(rd) / "..evil.json").exists()
    assert not list(runs.results_dir(rd).glob("*.json")) if runs.results_dir(rd).exists() else True
