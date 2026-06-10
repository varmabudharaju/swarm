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
