from conftest import task

from swarm_lib import graph


def g(tasks, **kw):
    base = {"version": 1, "run_id": "r", "goal": "g", "project": "/p", "tasks": tasks}
    base.update(kw)
    base.setdefault("graph_hash", graph.compute_hash(base))
    return base


def codes(issues, level=None):
    return [i["code"] for i in issues if level is None or i["level"] == level]


def test_valid_graph_no_errors():
    issues = graph.validate(g([task("a"), task("b", deps=["a"])]))
    assert graph.errors(issues) == []


def test_hash_is_stable_and_checked():
    gr = g([task("a")])
    assert gr["graph_hash"] == graph.compute_hash(gr)
    gr["graph_hash"] = "wrong"
    assert "hash" in codes(graph.errors(graph.validate(gr)))


def test_cycle_detected():
    gr = g([task("a", deps=["b"]), task("b", deps=["a"])])
    assert "cycle" in codes(graph.errors(graph.validate(gr)))


def test_dangling_and_dup():
    assert "dangling" in codes(graph.errors(graph.validate(g([task("a", deps=["zz"])]))))
    assert "dup-id" in codes(graph.errors(graph.validate(g([task("a"), task("a")]))))


def test_fan_in_cap():
    deps = [f"d{i}" for i in range(9)]
    tasks = [task(d) for d in deps] + [task("big", deps=deps)]
    assert "fan-in" in codes(graph.errors(graph.validate(g(tasks))))


def test_schema_summary_required():
    bad = task("a", schema={"type": "object", "properties": {}})
    assert "schema-summary" in codes(graph.errors(graph.validate(g([bad]))))


def test_warns():
    many = [task(f"t{i}", prompt="short") for i in range(26)]
    issues = graph.validate(g(many))
    w = codes(issues, "warn")
    assert "count" in w and "granularity" in w


def test_verify_ratio_warn():
    tasks = [task("a"), task("b"), task("v1", type="verify"), task("v2", type="verify")]
    assert "verify-ratio" in codes(graph.validate(g(tasks)), "warn")


def test_barrier_smell():
    readers = [task(f"r{i}") for i in range(5)]
    barrier = task("syn", type="synthesize", deps=[f"r{i}" for i in range(5)], prompt="merge")
    issues = graph.validate(g(readers + [barrier]))
    assert "barrier" in codes(issues, "warn")


def test_id_charset_validation():
    # spaces and slashes should produce id-charset errors
    bad_space = task("my task")
    bad_slash = task("a/b")
    ok_id = task("ok-task_1.x")
    gr_space = g([bad_space])
    gr_slash = g([bad_slash])
    gr_ok = g([ok_id])
    assert "id-charset" in codes(graph.errors(graph.validate(gr_space)))
    assert "id-charset" in codes(graph.errors(graph.validate(gr_slash)))
    assert "id-charset" not in codes(graph.errors(graph.validate(gr_ok)))


def test_validate_malformed_task_no_crash():
    """A raw dict without id/type/schema must return errors but never raise."""
    raw_task = {"prompt": "x"}
    gr = {
        "version": 1,
        "run_id": "r",
        "goal": "g",
        "project": "/p",
        "tasks": [raw_task],
    }
    gr["graph_hash"] = graph.compute_hash(gr)
    issues = graph.validate(gr)
    error_codes = codes(issues, "error")
    assert "id" in error_codes
    assert "type" in error_codes
    assert "schema-summary" in error_codes


def test_model_allow_list():
    from conftest import task
    from swarm_lib import graph as g

    base = {"version": 1, "tasks": [task("a", model="haiku"), task("b", model="gpt5")]}
    issues = g.validate(base)
    msgs = [i["msg"] for i in g.errors(issues)]
    assert any("b: unknown model gpt5" in m for m in msgs)
    assert not any(m.startswith("a:") for m in msgs)  # haiku on task a is legal


def test_model_omitted_is_valid():
    from conftest import task
    from swarm_lib import graph as g

    issues = g.validate({"version": 1, "tasks": [task("a")]})
    assert not [i for i in g.errors(issues) if i["code"] == "model"]
