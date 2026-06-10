from swarm_lib import paths


def test_home_respects_env(swarm_home):
    assert paths.home() == swarm_home


def test_project_slug():
    assert paths.project_slug("/Users/varma/foo") == "-Users-varma-foo"


def test_run_dir_layout(swarm_home):
    d = paths.run_dir("/Users/varma/foo", "2026-06-10-audit")
    assert d == swarm_home / "runs" / "-Users-varma-foo" / "2026-06-10-audit"


def test_json_roundtrip_atomic(swarm_home):
    p = swarm_home / "a" / "b.json"
    paths.write_json_atomic(p, {"x": 1})
    assert paths.read_json(p) == {"x": 1}
    assert paths.read_json(swarm_home / "nope.json", {"d": 1}) == {"d": 1}
    assert not list(p.parent.glob("*.tmp"))
