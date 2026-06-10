from swarm_lib import marker


def test_roundtrip():
    line = marker.build("/Users/v/.claude/swarm/runs/-p/r1", "t1", "abc123")
    assert line == "SWARM-TASK run=/Users/v/.claude/swarm/runs/-p/r1 task=t1 hash=abc123"
    parsed = marker.parse("preamble\n" + line + "\nrest of prompt")
    assert parsed == {"run": "/Users/v/.claude/swarm/runs/-p/r1", "task": "t1", "hash": "abc123"}


def test_run_dir_with_spaces():
    line = marker.build("/Users/v/My Projects/runs/r1", "t2", "h")
    assert marker.parse(line)["run"] == "/Users/v/My Projects/runs/r1"


def test_no_marker():
    assert marker.parse("just a normal prompt\nno marker here") is None
    assert marker.parse("") is None
    assert marker.parse(None) is None
