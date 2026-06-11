"""Task-graph validation and content hashing. Mirrors the engine-side checks in run_graph.mjs."""
import hashlib
import json
import re
import statistics

TYPES = {"research", "review", "implement", "verify", "integrate", "synthesize"}
MODELS = {"haiku", "sonnet", "opus", "fable"}
FAN_IN_MAX = 8
VERIFY_RATIO_MAX = 0.30
TASK_COUNT_WARN = 25
PROMPT_MEDIAN_WARN = 400
SUMMARY_MAX = 2000


def compute_hash(graph) -> str:
    blob = json.dumps(graph.get("tasks", []), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def validate(graph) -> list:
    issues = []

    def err(code, msg):
        issues.append({"level": "error", "code": code, "msg": msg})

    def warn(code, msg):
        issues.append({"level": "warn", "code": code, "msg": msg})

    if graph.get("version") != 1:
        err("version", "graph version must be 1")
    tasks = graph.get("tasks") or []
    ids = [t.get("id") for t in tasks]
    if len(ids) != len(set(ids)):
        err("dup-id", "duplicate task ids")
    idset = set(ids)
    for t in tasks:
        tid = t.get("id")
        if not t.get("id"):
            err("id", "task missing id")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]+", t["id"]):
            err("id-charset", f"{t['id']!r}: id must match [A-Za-z0-9_.-]+")
        if t.get("type") not in TYPES:
            err("type", f"{tid}: unknown type {t.get('type')}")
        if t.get("model") is not None and t["model"] not in MODELS:
            err("model", f"{tid}: unknown model {t['model']} (use haiku|sonnet|opus|fable)")
        for d in t.get("deps", []):
            if d not in idset:
                err("dangling", f"{tid}: dep {d} does not exist")
        if len(t.get("deps", [])) > FAN_IN_MAX:
            err("fan-in", f"{tid}: fan-in {len(t.get('deps', []))} > {FAN_IN_MAX}")
        summary = ((t.get("schema") or {}).get("properties") or {}).get("summary") or {}
        if summary.get("type") != "string" or summary.get("maxLength", 10**9) > SUMMARY_MAX:
            err("schema-summary",
                f"{tid}: schema must include summary: string with maxLength <= {SUMMARY_MAX}")
        if not t.get("prompt"):
            err("prompt", f"{tid}: empty prompt")
    if graph.get("graph_hash") and graph["graph_hash"] != compute_hash(graph):
        err("hash", "graph_hash does not match tasks")
    if not any(i["code"] in ("dangling", "dup-id", "id") for i in issues):
        indeg = {t.get("id"): len(t.get("deps", [])) for t in tasks}
        children = {t.get("id"): [] for t in tasks}
        for t in tasks:
            for d in t.get("deps", []):
                children[d].append(t.get("id"))
        queue = [i for i, n in indeg.items() if n == 0]
        seen = 0
        while queue:
            n = queue.pop()
            seen += 1
            for c in children[n]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        if seen != len(tasks):
            err("cycle", "dependency cycle detected")
    if len(tasks) > TASK_COUNT_WARN:
        warn("count", f"{len(tasks)} tasks > {TASK_COUNT_WARN}")
    if tasks:
        med = statistics.median(len(t.get("prompt", "")) for t in tasks)
        if med < PROMPT_MEDIAN_WARN:
            warn("granularity", f"median prompt {med:.0f} chars < {PROMPT_MEDIAN_WARN}")
        nver = sum(1 for t in tasks if t.get("type") == "verify")
        if nver > VERIFY_RATIO_MAX * len(tasks):
            warn("verify-ratio", f"{nver} verify tasks exceed 30% of graph")
        bytype = {}
        for t in tasks:
            bytype.setdefault(t.get("type"), set()).add(t.get("id"))
        for t in tasks:
            deps = set(t.get("deps", []))
            for ty, members in bytype.items():
                if (ty != t.get("type") and deps and deps >= members and len(members) > 3
                        and len(t.get("prompt", "")) < 200):
                    warn("barrier", f"{t.get('id')}: depends on all {len(members)} {ty} tasks "
                                    "with a thin prompt - likely a phase barrier")
    return issues


def errors(issues) -> list:
    return [i for i in issues if i["level"] == "error"]
