import json

from swarm_lib import extract


def write_jsonl(path, lines):
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def test_structured_output_preferred(tmp_path):
    tp = tmp_path / "agent.jsonl"
    write_jsonl(tp, [
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "SWARM-TASK run=/r task=t1 hash=h\ndo the thing"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "working on it"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "x",
             "input": {"summary": "found 3 issues"}}]}},
    ])
    info = extract.extract_output(str(tp))
    assert info["structured"] is True
    assert info["output"] == {"summary": "found 3 issues"}
    assert "SWARM-TASK" in info["first_user"]


def test_falls_back_to_last_text(tmp_path):
    tp = tmp_path / "agent.jsonl"
    write_jsonl(tp, [
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "SWARM-TASK run=/r task=t2 hash=h\ngo"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "first answer"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "final answer"}]}},
    ])
    info = extract.extract_output(str(tp))
    assert info["structured"] is False
    assert info["output"] == "final answer"


def test_payload_fallback_and_missing_file(tmp_path):
    info = extract.extract_output(str(tmp_path / "nope.jsonl"), last_assistant_message="from payload")
    assert info["output"] == "from payload"
    assert info["first_user"] is None


def test_string_content_user_message(tmp_path):
    tp = tmp_path / "agent.jsonl"
    write_jsonl(tp, [
        {"type": "user", "message": {"role": "user", "content": "SWARM-TASK run=/r task=t3 hash=h\ngo"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "ok"}]}},
    ])
    assert "SWARM-TASK" in extract.extract_output(str(tp))["first_user"]


def test_first_user_list_content(tmp_path):
    """first_user returns the first user text block from a list-content transcript."""
    tp = tmp_path / "agent.jsonl"
    write_jsonl(tp, [
        {"type": "system", "message": {"role": "system", "content": "sys prompt"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "SWARM-TASK run=/r task=t1 hash=h\ndo it"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "working"}]}},
    ])
    result = extract.first_user(str(tp))
    assert result == "SWARM-TASK run=/r task=t1 hash=h\ndo it"


def test_first_user_string_content(tmp_path):
    """first_user returns the first user text from a string-content transcript."""
    tp = tmp_path / "agent.jsonl"
    write_jsonl(tp, [
        {"type": "user", "message": {"role": "user", "content": "SWARM-TASK run=/r task=t2 hash=h\ngo"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "ok"}]}},
    ])
    result = extract.first_user(str(tp))
    assert result == "SWARM-TASK run=/r task=t2 hash=h\ngo"


def test_first_user_missing_file(tmp_path):
    """first_user returns None for a missing file."""
    result = extract.first_user(str(tmp_path / "nope.jsonl"))
    assert result is None
