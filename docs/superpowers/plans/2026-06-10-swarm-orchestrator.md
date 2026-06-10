# swarm Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `swarm`: a graph-first multi-agent orchestrator for Claude Code — persistent task graphs, a pure DAG-executor workflow, hook-written checkpoints, and cross-session resume.

**Architecture:** Python package `swarm_lib` (CLI + two hooks) provides the durable substrate (graph validation, run state, checkpoint writing via SubagentStop, resume nag via SessionStart, hardened installer). A pure JS scheduler `runGraph` (Node-tested) is generated into `~/.claude/workflows/swarm-run.js`. A skill + three least-privilege agent definitions complete the product.

**Tech Stack:** Python 3.11 (`python3`, pytest), Node 22 (`node --test`), no runtime deps beyond stdlib (PyYAML not needed here).

**Conventions for every task:**
- Repo: `/Users/varma/swarm`. Run Python tests from repo root: `python3 -m pytest`. Node tests: `node --test tests/node/`.
- Commit messages plain, imperative, **NEVER any Co-Authored-By/attribution lines**.
- Hooks are fail-open: any exception → log to `$SWARM_HOME/swarm.log` → exit 0.
- `SWARM_HOME` env var overrides `~/.claude/swarm` (tests rely on this).

**Spec:** `docs/superpowers/specs/2026-06-10-swarm-orchestrator-design.md` (approved).

---

## File structure (locked in)

```
swarm/
  pyproject.toml  .gitignore  README.md
  swarm_lib/
    __init__.py
    paths.py        # SWARM_HOME, project slugs, run dirs, atomic JSON
    marker.py       # SWARM-TASK marker build/parse
    extract.py      # subagent-transcript output extraction
    graph.py        # graph validation + content hash
    runs.py         # results scan, run-state, locks, pending-run detection
    hook.py         # __main__: SubagentStop checkpoint + SessionStart nag
    install.py      # hardened settings.json merge + artifact copy + workflow generation
    cli.py          # validate | status | abandon | args | finish | install | uninstall
  workflows/
    run_graph.mjs          # pure scheduler (canonical source, Node-testable)
    swarm-run.header.js    # workflow meta literal
    swarm-run.footer.js    # harness invocation lines
  agents/
    swarm-reader.md  swarm-verifier.md  swarm-implementer.md
  skill/
    SKILL.md
    references/graph-format.md  references/packet-guide.md  references/shapes.md
  tests/
    conftest.py
    test_paths.py test_marker.py test_extract.py test_graph.py
    test_runs.py  test_hook.py   test_install.py test_cli.py
    node/executor.test.mjs
```

Build order: Task 0 (controller spike) → 1 paths → 2 marker+extract → 3 graph → 4 runs → 5 hook → 6 run_graph.mjs → 7 static artifacts → 8 installer → 9 CLI → 10 integration+README → 11 (controller: live install + demo).

---

### Task 0: CONTROLLER SPIKE — SubagentStop for Workflow agents

**Executed by the controller in the main session, not a subagent** (subagents cannot invoke the Workflow tool).

- [ ] **Step 1:** Invoke the Workflow tool with this trivial script:

```js
export const meta = { name: 'swarm-spike', description: 'one trivial agent to test SubagentStop', phases: [{ title: 'Spike' }] }
phase('Spike')
const r = await agent("Reply with exactly the word: SPIKE-OK", { label: 'spike' })
return { r }
```

- [ ] **Step 2:** After it completes, check the agent-pd audit log of the current session for a `SubagentStop` event from that workflow agent and confirm the payload contains `agent_transcript_path` (and note whether `last_assistant_message` is present):

```bash
T=$(ls -t ~/.claude/pd/audit/*.jsonl | head -1)
python3 -c "
import json
for line in open('$T'):
    try: o=json.loads(line)
    except: continue
    if o.get('event')=='SubagentStop':
        print(json.dumps(o.get('_extra', {}), indent=1)[:400])
"
```

Expected: at least one event whose `_extra.agent_transcript_path` points at a `subagents/agent-*.jsonl` file. **If absent for workflow agents:** STOP and revise the design (fallback per spec: Write-tool checkpoints + schema returns). If present: proceed.

---

### Task 1: Skeleton + paths

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `swarm_lib/__init__.py`, `swarm_lib/paths.py`
- Test: `tests/conftest.py`, `tests/test_paths.py`

- [ ] **Step 1: Packaging files**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "ctx-swarm"
version = "0.1.0"
description = "Graph-first multi-agent orchestrator for Claude Code"
requires-python = ">=3.11"

[project.scripts]
swarm = "swarm_lib.cli:main"

[tool.setuptools.packages.find]
include = ["swarm_lib*"]
```

`.gitignore`:
```
__pycache__/
*.egg-info/
.pytest_cache/
build/
dist/
```

`README.md` (stub, replaced in Task 10):
```markdown
# swarm

Graph-first multi-agent orchestrator for Claude Code. See
`docs/superpowers/specs/2026-06-10-swarm-orchestrator-design.md`.
```

`swarm_lib/__init__.py`: empty.

- [ ] **Step 2: Editable install**

Run: `python3 -m pip install --user -e /Users/varma/swarm`
Expected: `Successfully installed ctx-swarm-0.1.0`

- [ ] **Step 3: conftest + failing paths test**

`tests/conftest.py`:
```python
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
```

`tests/test_paths.py`:
```python
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
```

- [ ] **Step 4: Run to verify failure**

Run: `python3 -m pytest tests/test_paths.py -v`
Expected: FAIL (module missing).

- [ ] **Step 5: Implement `swarm_lib/paths.py`**

```python
"""SWARM_HOME resolution, run-dir layout, atomic JSON I/O."""
import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("SWARM_HOME", str(Path.home() / ".claude" / "swarm")))


def project_slug(project) -> str:
    return str(Path(project).resolve()).replace("/", "-")


def runs_dir(project) -> Path:
    return home() / "runs" / project_slug(project)


def run_dir(project, run_id) -> Path:
    return runs_dir(project) / run_id


def log_path() -> Path:
    return home() / "swarm.log"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path, obj, indent=None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent), encoding="utf-8")
    tmp.replace(p)
```

- [ ] **Step 6: Run to verify pass**

Run: `python3 -m pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: project skeleton and paths module"
```

---

### Task 2: marker + extract

**Files:**
- Create: `swarm_lib/marker.py`, `swarm_lib/extract.py`
- Test: `tests/test_marker.py`, `tests/test_extract.py`

- [ ] **Step 1: Failing marker test**

`tests/test_marker.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_marker.py -v` — FAIL (module missing).

- [ ] **Step 3: Implement `swarm_lib/marker.py`**

```python
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
```

- [ ] **Step 4: marker tests pass**

Run: `python3 -m pytest tests/test_marker.py -v` — 3 passed.

- [ ] **Step 5: Failing extract test**

Subagent transcripts are JSONL like the session transcripts: lines with
`{"type": "user"|"assistant", "message": {"role", "content": [blocks]}}`. The
agent's structured output appears as an assistant `tool_use` block named
`StructuredOutput` whose `input` is the object.

`tests/test_extract.py`:
```python
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
```

- [ ] **Step 6: Run to verify failure**

Run: `python3 -m pytest tests/test_extract.py -v` — FAIL.

- [ ] **Step 7: Implement `swarm_lib/extract.py`**

```python
"""Extract a swarm task agent's final output from its transcript JSONL."""
import json


def extract_output(transcript_path, last_assistant_message=None):
    """Returns {first_user, output, structured}. Prefers a StructuredOutput
    tool call's input; falls back to the last assistant text, then to the
    payload-provided last_assistant_message."""
    structured = None
    last_text = None
    first_user = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                role = msg.get("role")
                content = msg.get("content")
                if first_user is None and role == "user" and isinstance(content, str):
                    first_user = content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if first_user is None and role == "user" and btype == "text":
                        first_user = block.get("text")
                    if role == "assistant":
                        if btype == "tool_use" and block.get("name") == "StructuredOutput":
                            structured = block.get("input")
                        elif btype == "text" and (block.get("text") or "").strip():
                            last_text = block.get("text")
    except OSError:
        pass
    if structured is not None:
        output = structured
    else:
        output = last_text or last_assistant_message or ""
    return {"first_user": first_user, "output": output, "structured": structured is not None}
```

- [ ] **Step 8: All pass + commit**

Run: `python3 -m pytest tests/test_marker.py tests/test_extract.py -v` — 7 passed.

```bash
git add -A && git commit -m "feat: task marker and transcript output extraction"
```

---

### Task 3: graph validation + hash

**Files:**
- Create: `swarm_lib/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Failing test**

`tests/test_graph.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_graph.py -v` — FAIL.

- [ ] **Step 3: Implement `swarm_lib/graph.py`**

```python
"""Task-graph validation and content hashing. Mirrors the engine-side checks in run_graph.mjs."""
import hashlib
import json
import statistics

TYPES = {"research", "review", "implement", "verify", "integrate", "synthesize"}
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
        if t.get("type") not in TYPES:
            err("type", f"{tid}: unknown type {t.get('type')}")
        for d in t.get("deps", []):
            if d not in idset:
                err("dangling", f"{tid}: dep {d} does not exist")
        if len(t.get("deps", [])) > FAN_IN_MAX:
            err("fan-in", f"{tid}: fan-in {len(t['deps'])} > {FAN_IN_MAX}")
        summary = ((t.get("schema") or {}).get("properties") or {}).get("summary") or {}
        if summary.get("type") != "string" or summary.get("maxLength", 10**9) > SUMMARY_MAX:
            err("schema-summary",
                f"{tid}: schema must include summary: string with maxLength <= {SUMMARY_MAX}")
        if not t.get("prompt"):
            err("prompt", f"{tid}: empty prompt")
    if graph.get("graph_hash") and graph["graph_hash"] != compute_hash(graph):
        err("hash", "graph_hash does not match tasks")
    if not any(i["code"] in ("dangling", "dup-id") for i in issues):
        indeg = {t["id"]: len(t.get("deps", [])) for t in tasks}
        children = {t["id"]: [] for t in tasks}
        for t in tasks:
            for d in t.get("deps", []):
                children[d].append(t["id"])
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
            bytype.setdefault(t["type"], set()).add(t["id"])
        for t in tasks:
            deps = set(t.get("deps", []))
            for ty, members in bytype.items():
                if (ty != t["type"] and deps and deps >= members and len(members) > 3
                        and len(t.get("prompt", "")) < 200):
                    warn("barrier", f"{t['id']}: depends on all {len(members)} {ty} tasks "
                                    "with a thin prompt - likely a phase barrier")
    return issues


def errors(issues) -> list:
    return [i for i in issues if i["level"] == "error"]
```

- [ ] **Step 4: Pass + commit**

Run: `python3 -m pytest tests/test_graph.py -v` — 9 passed.

```bash
git add -A && git commit -m "feat: graph validation and content hashing"
```

---

### Task 4: runs (results scan, state, locks, pending detection)

**Files:**
- Create: `swarm_lib/runs.py`
- Test: `tests/test_runs.py`

- [ ] **Step 1: Failing test**

`tests/test_runs.py`:
```python
import time

from conftest import make_run, task

from swarm_lib import paths, runs


def write_result(rd, tid, hash="h1", version=1, summary="s"):
    paths.write_json_atomic(runs.results_dir(rd) / f"{tid}.json",
                            {"version": version, "task": tid, "hash": hash,
                             "status": "ok", "output": {"summary": summary},
                             "summary": summary, "ts": time.time()})


def test_scan_results_filters_version_and_hash(tmp_path):
    rd = make_run(tmp_path, tasks=[task("a"), task("b")])
    write_result(rd, "a")
    write_result(rd, "b", hash="WRONG")
    write_result(rd, "c", version=2)
    completed, bad = runs.scan_results(rd, "h1")
    assert list(completed) == ["a"]
    assert sorted(bad) == ["b.json", "c.json"]


def test_state_roundtrip_and_abandon(tmp_path):
    rd = make_run(tmp_path)
    assert runs.read_state(rd) is None
    runs.write_state(rd, "completed")
    assert runs.read_state(rd)["status"] == "completed"
    runs.abandon(rd)
    assert runs.read_state(rd)["status"] == "abandoned"


def test_lock_take_and_stale(tmp_path):
    rd = make_run(tmp_path)
    assert runs.take_lock(rd, "s1") is True
    assert runs.take_lock(rd, "s2") is False  # fresh lock held
    # stale lock can be overridden
    paths.write_json_atomic(runs.lock_path(rd), {"owner": "s1", "ts": time.time() - 3 * 3600})
    assert runs.take_lock(rd, "s2") is True
    runs.release_lock(rd)
    assert not runs.lock_path(rd).exists()


def test_pending_runs(tmp_path):
    proj = str(tmp_path / "proj")
    rd1 = make_run(tmp_path, "r1", tasks=[task("a"), task("b")])      # interrupted
    write_result(rd1, "a")
    rd2 = make_run(tmp_path, "r2", tasks=[task("a")])                  # completed
    runs.write_state(rd2, "completed")
    rd3 = make_run(tmp_path, "r3", tasks=[task("a")])                  # paused
    runs.write_state(rd3, "paused_for_budget")
    pend = runs.pending_runs(proj)
    by_id = {p["run_id"]: p for p in pend}
    assert set(by_id) == {"r1", "r3"}
    assert by_id["r1"]["done"] == 1 and by_id["r1"]["total"] == 2
    assert by_id["r1"]["status"] == "interrupted"
    assert by_id["r3"]["status"] == "paused_for_budget"


def test_pending_runs_ages_out(tmp_path):
    import os
    proj = str(tmp_path / "proj")
    rd = make_run(tmp_path, "old", tasks=[task("a")])
    old = time.time() - 8 * 86400
    os.utime(rd, (old, old))
    assert runs.pending_runs(proj) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_runs.py -v` — FAIL.

- [ ] **Step 3: Implement `swarm_lib/runs.py`**

```python
"""Run-dir state: results scanning, run-state.json, resume locks, pending-run detection."""
import time
from pathlib import Path

from . import paths

RESULT_VERSION = 1
NAG_MAX_AGE_DAYS = 7
LOCK_STALE_HOURS = 2.0


def results_dir(run_dir) -> Path:
    return Path(run_dir) / "results"


def packets_dir(run_dir) -> Path:
    return Path(run_dir) / "packets"


def state_path(run_dir) -> Path:
    return Path(run_dir) / "run-state.json"


def lock_path(run_dir) -> Path:
    return Path(run_dir) / "resume.lock"


def scan_results(run_dir, graph_hash=None):
    completed, bad = {}, []
    d = results_dir(run_dir)
    if not d.is_dir():
        return completed, bad
    for p in sorted(d.glob("*.json")):
        r = paths.read_json(p)
        if not r or r.get("version") != RESULT_VERSION:
            bad.append(p.name)
            continue
        if graph_hash and r.get("hash") != graph_hash:
            bad.append(p.name)
            continue
        completed[r["task"]] = r
    return completed, bad


def read_state(run_dir):
    return paths.read_json(state_path(run_dir))


def write_state(run_dir, status, extra=None) -> None:
    obj = {"status": status, "ts": time.time()}
    if extra:
        obj.update(extra)
    paths.write_json_atomic(state_path(run_dir), obj, indent=2)


def abandon(run_dir) -> None:
    write_state(run_dir, "abandoned")


def take_lock(run_dir, owner, stale_hours=LOCK_STALE_HOURS) -> bool:
    existing = paths.read_json(lock_path(run_dir))
    if existing and time.time() - existing.get("ts", 0) < stale_hours * 3600:
        return False
    paths.write_json_atomic(lock_path(run_dir), {"owner": owner, "ts": time.time()})
    return True


def release_lock(run_dir) -> None:
    lock_path(run_dir).unlink(missing_ok=True)


def pending_runs(project) -> list:
    """Runs worth nagging about: interrupted (no run-state) or paused_for_budget,
    younger than NAG_MAX_AGE_DAYS."""
    out = []
    rd = paths.runs_dir(project)
    if not rd.is_dir():
        return out
    for d in sorted(rd.iterdir()):
        if not d.is_dir() or not (d / "graph.json").exists():
            continue
        status = (read_state(d) or {}).get("status")
        if status in ("completed", "abandoned", "failed-partial"):
            continue
        if (time.time() - d.stat().st_mtime) / 86400 > NAG_MAX_AGE_DAYS:
            continue
        graph = paths.read_json(d / "graph.json") or {}
        completed, _ = scan_results(d, graph.get("graph_hash"))
        out.append({"run_id": d.name, "run_dir": str(d), "done": len(completed),
                    "total": len(graph.get("tasks", [])),
                    "status": status or "interrupted"})
    return out
```

- [ ] **Step 4: Pass + commit**

Run: `python3 -m pytest tests/test_runs.py -v` — 5 passed.

```bash
git add -A && git commit -m "feat: run state, results scanning, locks, pending detection"
```

---

### Task 5: hooks (checkpoint writer + resume nag)

**Files:**
- Create: `swarm_lib/hook.py`
- Test: `tests/test_hook.py`

- [ ] **Step 1: Failing test**

`tests/test_hook.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_hook.py -v` — FAIL.

- [ ] **Step 3: Implement `swarm_lib/hook.py`**

```python
"""swarm hooks: SubagentStop checkpoint writer + SessionStart resume nag. Fail-open."""
import datetime
import json
import sys
import time
import traceback
from pathlib import Path

from . import extract, marker, paths, runs


def handle_subagent_stop(event):
    tp = event.get("agent_transcript_path") or (event.get("_extra") or {}).get(
        "agent_transcript_path")
    if not tp or not Path(tp).exists():
        return None
    info = extract.extract_output(tp, event.get("last_assistant_message"))
    mk = marker.parse(info.get("first_user") or "")
    if not mk:
        return None  # not a swarm task agent
    run_dir = Path(mk["run"])
    if not (run_dir / "graph.json").exists():
        return None
    out = info["output"]
    summary = out.get("summary", "") if isinstance(out, dict) else str(out)[:2000]
    result = {
        "version": runs.RESULT_VERSION,
        "task": mk["task"],
        "hash": mk["hash"],
        "status": "ok",
        "structured": info["structured"],
        "output": out,
        "summary": summary,
        "agent_id": event.get("agent_id"),
        "ts": time.time(),
    }
    paths.write_json_atomic(runs.results_dir(run_dir) / f"{mk['task']}.json", result, indent=1)
    return None


def handle_session_start(event):
    if event.get("source") not in ("startup", "clear"):
        return None
    pend = runs.pending_runs(event.get("cwd") or ".")
    if not pend:
        return None
    r = pend[0]
    if r["total"] and r["done"] >= r["total"]:
        line = (f"[swarm] Run '{r['run_id']}' has all {r['total']} tasks done but was never "
                "finalized - say '/swarm resume' to finalize (synthesis only).")
    elif r["status"] == "paused_for_budget":
        line = (f"[swarm] Run '{r['run_id']}' is paused for budget at {r['done']}/{r['total']} "
                "tasks - say '/swarm resume' to continue, or 'swarm abandon' to drop it.")
    else:
        line = (f"[swarm] Interrupted swarm run '{r['run_id']}' ({r['done']}/{r['total']} tasks "
                "done) - say '/swarm resume' to continue, or 'swarm abandon' to drop it.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}}


HANDLERS = {"SubagentStop": handle_subagent_stop, "SessionStart": handle_session_start}


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        fn = HANDLERS.get(event.get("hook_event_name"))
        out = fn(event) if fn else None
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
    except BaseException:
        try:
            paths.home().mkdir(parents=True, exist_ok=True)
            with open(paths.log_path(), "a") as f:
                f.write(f"--- {datetime.datetime.now().isoformat()}\n{traceback.format_exc()}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Pass + commit**

Run: `python3 -m pytest tests/test_hook.py -v` — 8 passed. Full suite: `python3 -m pytest -q` — 29 passed.

```bash
git add -A && git commit -m "feat: checkpoint and resume-nag hooks"
```

---

### Task 6: pure DAG scheduler (run_graph.mjs) + Node tests

**Files:**
- Create: `workflows/run_graph.mjs`
- Test: `tests/node/executor.test.mjs`

- [ ] **Step 1: Write the failing Node tests**

`tests/node/executor.test.mjs`:
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { runGraph, validateGraph, buildPrompt, RESERVE_TOKENS } from '../../workflows/run_graph.mjs'

const T = (id, deps = [], extra = {}) => ({
  id, title: id, type: 'research', prompt: `do ${id}`, deps,
  agent_type: 'swarm-reader', packet_path: `/run/packets/${id}.md`,
  schema: { type: 'object' }, max_retries: 1, ...extra,
})
const ARGS = (tasks, completed = {}, extra = {}) => ({
  run_dir: '/run', graph_hash: 'H', results_dir: '/run/results',
  agent_ceiling: null, tasks, completed, ...extra,
})
const okAgent = (result = (t) => ({ summary: `ok` })) => {
  const calls = []
  let inFlight = 0, maxInFlight = 0
  const fn = async (prompt, opts) => {
    calls.push({ prompt, opts })
    inFlight++; maxInFlight = Math.max(maxInFlight, inFlight)
    await new Promise(r => setTimeout(r, 5))
    inFlight--
    return { summary: `done ${opts.label}` }
  }
  return { fn, calls, max: () => maxInFlight }
}

test('independent tasks all launch concurrently', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c'), T('d')]), a.fn, null, null)
  assert.equal(Object.keys(out.completed).length, 4)
  assert.equal(a.max(), 4)
})

test('dependency order respected and summaries flow', async () => {
  const order = []
  const fn = async (prompt, opts) => { order.push(opts.label); return { summary: `S-${opts.label}` } }
  const out = await runGraph(ARGS([T('a'), T('b', ['a'])]), fn, null, null)
  assert.deepEqual(order, ['research:a', 'research:b'])
  assert.equal(out.completed.b.summary, 'S-research:b')
})

test('dep summaries and result paths injected into prompt', async () => {
  let bPrompt = ''
  const fn = async (prompt, opts) => {
    if (opts.label === 'research:b') bPrompt = prompt
    return { summary: `S-${opts.label}` }
  }
  await runGraph(ARGS([T('a'), T('b', ['a'])]), fn, null, null)
  assert.ok(bPrompt.includes('S-research:a'))
  assert.ok(bPrompt.includes('/run/results/a.json'))
  assert.ok(bPrompt.startsWith('SWARM-TASK run=/run task=b hash=H'))
})

test('no double launch in diamond', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('a'), T('b', ['a']), T('c', ['a']), T('d', ['b', 'c'])]), a.fn, null, null)
  const ids = a.calls.map(c => c.opts.label).sort()
  assert.deepEqual(ids, ['research:a', 'research:b', 'research:c', 'research:d'])
})

test('failure isolates: transitive skip, independents complete, partial state returned', async () => {
  const fn = async (p, o) => o.label === 'research:bad' ? null : { summary: 'ok' }
  const tasks = [T('bad', [], { max_retries: 0 }), T('child', ['bad']), T('grand', ['child']), T('solo')]
  const out = await runGraph(ARGS(tasks), fn, null, null)
  assert.deepEqual(out.failed, ['bad'])
  assert.deepEqual(out.skipped.sort(), ['child', 'grand'])
  assert.ok('solo' in out.completed)
})

test('retry once then success', async () => {
  let calls = 0
  const fn = async () => (++calls === 1 ? null : { summary: 'ok' })
  const out = await runGraph(ARGS([T('a')]), fn, null, null)
  assert.equal(calls, 2)
  assert.ok('a' in out.completed)
})

test('null under exhausted budget pauses instead of failing', async () => {
  const budget = { total: 100000, remaining: () => RESERVE_TOKENS - 1 }
  const fn = async () => null
  const out = await runGraph(ARGS([T('a')]), fn, null, budget)
  assert.equal(out.paused, 'paused_for_budget')
  assert.deepEqual(out.failed, [])
  assert.deepEqual(out.pending, ['a'])
})

test('budget reservation limits launches', async () => {
  // affords exactly: remaining > RESERVE*(inflight+1)+FLOOR -> with 100k: 1 inflight ok, 2nd not
  const budget = { total: 1, remaining: () => RESERVE_TOKENS * 2 + 10000 }
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c')]), a.fn, null, budget)
  assert.equal(out.paused, 'paused_for_budget')
  assert.ok(a.calls.length < 3)
})

test('agent ceiling pauses with pending work', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c')], {}, { agent_ceiling: 2 }), a.fn, null, null)
  assert.equal(out.paused, 'agent_ceiling')
  assert.equal(a.calls.length, 2)
  assert.equal(out.pending.length, 1)
})

test('resume short-circuits completed tasks', async () => {
  const a = okAgent()
  const out = await runGraph(
    ARGS([T('a'), T('b', ['a'])], { a: { summary: 'precomputed' } }), a.fn, null, null)
  assert.deepEqual(a.calls.map(c => c.opts.label), ['research:b'])
  assert.ok(a.calls[0].prompt.includes('precomputed'))
  assert.ok('a' in out.completed && 'b' in out.completed)
})

test('fatal validation refuses to launch anything', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a', ['ghost'])]), a.fn, null, null)
  assert.ok(out.fatal.length > 0)
  assert.equal(a.calls.length, 0)
  const out2 = await runGraph(ARGS([T('a')], { ghost: { summary: 'x' } }), a.fn, null, null)
  assert.ok(out2.fatal.length > 0)
})

test('cycle detected at runtime', async () => {
  const out = await runGraph(ARGS([T('a', ['b']), T('b', ['a'])]), okAgent().fn, null, null)
  assert.ok(out.fatal.some(e => e.includes('cycle')))
})

test('validateGraph flags fan-in over 8', () => {
  const deps = Array.from({ length: 9 }, (_, i) => `d${i}`)
  const tasks = [...deps.map(d => T(d)), T('big', deps)]
  assert.ok(validateGraph(tasks, {}).some(e => e.includes('fan-in')))
})

test('agent opts pass through agentType and isolation', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('a', [], { agent_type: 'swarm-implementer', isolation: 'worktree', type: 'implement' })]), a.fn, null, null)
  assert.equal(a.calls[0].opts.agentType, 'swarm-implementer')
  assert.equal(a.calls[0].opts.isolation, 'worktree')
  assert.equal(a.calls[0].opts.phase, 'implement')
})
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/node/`
Expected: FAIL — cannot find `workflows/run_graph.mjs`.

- [ ] **Step 3: Implement `workflows/run_graph.mjs`**

```javascript
// Pure DAG scheduler for swarm. No workflow-runtime dependencies:
// agentFn/logFn/budget are injected, so this file runs under plain Node for tests
// and is embedded into ~/.claude/workflows/swarm-run.js by the installer.
export const RESERVE_TOKENS = 30000
export const FLOOR_TOKENS = 20000

export function validateGraph(tasks, completed) {
  const errors = []
  const ids = new Set()
  for (const t of tasks) {
    if (ids.has(t.id)) errors.push(`duplicate id ${t.id}`)
    ids.add(t.id)
  }
  for (const t of tasks) {
    for (const d of t.deps) if (!ids.has(d)) errors.push(`${t.id}: dangling dep ${d}`)
    if (t.deps.length > 8) errors.push(`${t.id}: fan-in ${t.deps.length} > 8`)
  }
  for (const c of Object.keys(completed)) if (!ids.has(c)) errors.push(`completed id ${c} not in graph`)
  const indeg = new Map(tasks.map(t => [t.id, t.deps.length]))
  const children = new Map(tasks.map(t => [t.id, []]))
  for (const t of tasks) for (const d of t.deps) if (children.has(d)) children.get(d).push(t.id)
  const queue = tasks.filter(t => t.deps.length === 0).map(t => t.id)
  let seen = 0
  while (queue.length) {
    const n = queue.pop()
    seen++
    for (const c of children.get(n)) {
      indeg.set(c, indeg.get(c) - 1)
      if (indeg.get(c) === 0) queue.push(c)
    }
  }
  if (seen !== tasks.length && errors.length === 0) errors.push('dependency cycle detected')
  return errors
}

export function buildPrompt(argsObj, t, completed) {
  const lines = [
    `SWARM-TASK run=${argsObj.run_dir} task=${t.id} hash=${argsObj.graph_hash}`,
    'You are one worker in a swarm run. Work ONLY on this task. Your final output MUST match',
    'the required schema; keep summary under 2000 chars of dense, factual content.',
    `First, Read your context packet at: ${t.packet_path}`,
    '',
    t.prompt,
  ]
  if (t.deps.length) {
    lines.push('', '## Results from tasks you depend on')
    for (const d of t.deps) {
      const r = completed[d] || {}
      lines.push(`### ${d}`, String(r.summary || '').slice(0, 2200),
        `(full result on disk: ${argsObj.results_dir}/${d}.json)`)
    }
  }
  return lines.join('\n')
}

export async function runGraph(argsObj, agentFn, logFn, budget) {
  const tasks = argsObj.tasks
  const fatal = validateGraph(tasks, argsObj.completed || {})
  if (fatal.length) return { fatal, completed: {}, failed: [], skipped: [], pending: tasks.map(t => t.id) }
  const completed = { ...(argsObj.completed || {}) }
  const failedSet = new Set()
  const skippedSet = new Set()
  const launched = new Set(Object.keys(completed))
  const running = new Map()
  let agentsUsed = 0
  let paused = null

  const canAfford = () => !budget || !budget.total ||
    budget.remaining() > RESERVE_TOKENS * (running.size + 1) + FLOOR_TOKENS

  const attempt = async (t) => {
    let tries = 0
    while (tries <= (t.max_retries ?? 1)) {
      const res = await agentFn(buildPrompt(argsObj, t, completed), {
        label: `${t.type}:${t.id}`,
        phase: t.type,
        schema: t.schema,
        ...(t.agent_type ? { agentType: t.agent_type } : {}),
        ...(t.isolation ? { isolation: t.isolation } : {}),
      })
      if (res !== null && res !== undefined) return res
      if (budget && budget.total && budget.remaining() < RESERVE_TOKENS) return { __budget_null: true }
      tries++
    }
    return null
  }

  while (true) {
    let changed = true
    while (changed) {
      changed = false
      for (const t of tasks) {
        if (launched.has(t.id) || skippedSet.has(t.id)) continue
        if (t.deps.some(d => failedSet.has(d) || skippedSet.has(d))) {
          skippedSet.add(t.id)
          changed = true
        }
      }
    }
    if (!paused) {
      for (const t of tasks) {
        if (launched.has(t.id) || skippedSet.has(t.id)) continue
        if (!t.deps.every(d => d in completed)) continue
        if (argsObj.agent_ceiling && agentsUsed >= argsObj.agent_ceiling) { paused = 'agent_ceiling'; break }
        if (!canAfford()) { paused = 'paused_for_budget'; break }
        launched.add(t.id)
        agentsUsed++
        if (logFn) logFn(`swarm: launch ${t.type}:${t.id} (${running.size + 1} in flight)`)
        running.set(t.id, attempt(t).then(result => ({ id: t.id, result })))
      }
    }
    if (running.size === 0) break
    const { id, result } = await Promise.race(running.values())
    running.delete(id)
    if (result && result.__budget_null) {
      launched.delete(id) // stays pending; resumable
      paused = 'paused_for_budget'
    } else if (result !== null && result !== undefined) {
      completed[id] = result
      if (logFn) logFn(`swarm: ${id} done (${Object.keys(completed).length}/${tasks.length})`)
    } else {
      failedSet.add(id)
      if (logFn) logFn(`swarm: ${id} FAILED after retries`)
    }
  }
  return {
    fatal: [],
    completed,
    failed: [...failedSet],
    skipped: [...skippedSet],
    paused,
    agentsUsed,
    pending: tasks.filter(t => !(t.id in completed) && !failedSet.has(t.id) && !skippedSet.has(t.id)).map(t => t.id),
  }
}
```

- [ ] **Step 4: Run Node tests**

Run: `node --test tests/node/`
Expected: 14 pass, 0 fail.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: pure DAG scheduler with budget reservation and resume"
```

---

### Task 7: static artifacts (workflow header/footer, agents, skill)

**Files:**
- Create: `workflows/swarm-run.header.js`, `workflows/swarm-run.footer.js`, `agents/swarm-reader.md`, `agents/swarm-verifier.md`, `agents/swarm-implementer.md`, `skill/SKILL.md`, `skill/references/graph-format.md`, `skill/references/packet-guide.md`, `skill/references/shapes.md`

No tests in this task (pure content); the installer task tests generation/copying.

- [ ] **Step 1: Workflow wrapper pieces**

`workflows/swarm-run.header.js`:
```javascript
export const meta = {
  name: 'swarm-run',
  description: 'Generic DAG executor for swarm task graphs (generated from /Users/varma/swarm - do not edit here)',
  whenToUse: 'Invoked by the swarm skill with prepared args {run_dir, graph_hash, results_dir, agent_ceiling, tasks, completed}. Not for ad-hoc use.',
}
```

`workflows/swarm-run.footer.js`:
```javascript
const __out = await runGraph(args, (p, o) => agent(p, o), log, budget)
return __out
```

- [ ] **Step 2: Agent definitions**

`agents/swarm-reader.md`:
```markdown
---
name: swarm-reader
description: Read-only research/review worker for swarm task graphs. Distills findings into structured output. Never modifies anything.
tools: Read, Glob, Grep, WebFetch, WebSearch
---

You are a swarm task worker. Your prompt begins with a SWARM-TASK marker line -
leave it alone and do not echo it. Read your context packet first; it defines
your scope. Work ONLY on your assigned task: do not wander into other tasks'
scopes, do not modify anything, do not install anything.

Your final output is consumed by a scheduler, not a human: return dense, factual
data matching the required schema. The summary field must be self-contained -
a reader with zero other context must understand your findings from it alone.
Include file:line references for every claim about code. If you found nothing,
say so explicitly; never pad.
```

`agents/swarm-verifier.md`:
```markdown
---
name: swarm-verifier
description: Adversarial verifier for swarm task graphs. Tries to REFUTE findings or decompositions; runs tests when needed.
tools: Read, Glob, Grep, Bash
---

You are a swarm verification worker. Your prompt begins with a SWARM-TASK marker
line - leave it alone. Read your context packet first.

Your stance is adversarial: assume the findings or plan you are given are WRONG
and try to refute them with evidence (read the actual code, run the actual
tests). Confirm only what survives your attack. For each item, return a verdict
with concrete evidence (file:line, command output). When uncertain, say
uncertain - do not rubber-stamp. Return dense structured data per the schema;
summary must be self-contained.
```

`agents/swarm-implementer.md`:
```markdown
---
name: swarm-implementer
description: Implementation worker for swarm task graphs. TDD inside an isolated worktree; commits with plain messages; reports branch and files.
disallowedTools: ["Bash(git push*)", "Bash(curl*)", "Bash(wget*)", "WebFetch"]
---

You are a swarm implementation worker running in an ISOLATED git worktree. Your
prompt begins with a SWARM-TASK marker line - leave it alone. Read your context
packet first; it defines exactly what to build and the file scope you own.

Rules:
- Work ONLY inside your worktree and ONLY on your assigned scope.
- TDD: write the failing test, see it fail, implement, see it pass.
- Immediately create your task branch as instructed in your packet
  (git checkout -b swarm/<run-id>/<task-id>), commit all work to it with plain,
  imperative messages. NEVER add Co-Authored-By or any attribution lines.
- Never push. Never install global packages. Never touch files outside your
  declared scope (lockfiles included) unless your packet explicitly grants it.
- Your structured result MUST include: branch, worktree_path (run pwd),
  files_touched, commits (shas + messages), test results, and a self-contained
  summary.
```

- [ ] **Step 3: Skill**

`skill/SKILL.md`:
```markdown
---
name: swarm
description: Use when the user asks to run a swarm, orchestrate many agents in parallel, fan out a big task, /swarm <goal>, or resume/finalize an interrupted swarm run (/swarm resume). Decomposes goals into dependency graphs executed by the swarm-run workflow with durable checkpoints.
---

# swarm - graph-first orchestration

Turn a goal into a typed task graph, execute it with maximal parallelism via the
`swarm-run` workflow, and survive any interruption. Durable state lives at
`~/.claude/swarm/runs/<project-slug>/<run-id>/`; checkpoints are written by the
SubagentStop hook automatically - workers never manage their own persistence.

**Announce at start:** "Using the swarm skill to orchestrate this."

## Hard rules

- NEVER invoke the swarm-run workflow with a graph that failed `swarm validate`.
- NEVER resume without `swarm args --resume` (it takes the resume lock).
- Implement tasks ALWAYS get `isolation: "worktree"` + the swarm-implementer
  agent; integrate tasks ALWAYS run quarantined in their own worktree; the final
  merge to the user's branch happens in THIS session with user approval - never
  inside the workflow.
- Every task schema includes `summary: {type: string, maxLength: 2000}` plus
  whatever typed fields the task needs. Implement-task schemas must also include
  branch, worktree_path, files_touched, commits.

## Process

1. **Scout** (cheap, inline): list relevant files/dirs, read the plan/spec if one
   exists. Just enough to decompose honestly - don't deep-read what workers will read.
2. **Check headroom**: read the newest `~/.claude/tend/sessions/*/ctx.json`
   `rate_limits`. If `five_hour.used_percentage > 85`, tell the user the
   `resets_at` time and offer to defer. Otherwise set `agent_ceiling` in the
   graph from remaining headroom (rough guide: each task ~1-3% of a 5h window).
3. **Decompose** into `graph.json` per `references/graph-format.md`, following a
   shape from `references/shapes.md`. Maximize width honestly: every task that
   CAN be independent IS independent; deps are data dependencies, never phases.
   Target width near 16 (the concurrency cap); going wider buys queueing, not speed.
   Verify tiers: one verifier per 4-6 sibling finding-tasks; per-task verify only
   for results feeding implement tasks.
4. **Packets**: write one `packets/<id>.md` per task per
   `references/packet-guide.md`. Self-containment test: could a stranger with
   only this packet + prompt do the work? If not, the packet is incomplete.
5. **Validate**: run `swarm validate <run-dir>/graph.json`. Fix every error;
   treat warnings as design feedback, not noise.
6. **Review gate**: spawn ONE swarm-verifier agent with the goal + graph.json
   content; ask it to attack the decomposition (missing tasks, fake width, fan-in
   mush, packet gaps). Fix what it finds.
7. **Launch**: `ARGS=$(swarm args <run-dir>/graph.json)`, then invoke the
   Workflow tool: `{name: "swarm-run", args: <parsed ARGS JSON>}`.
8. **Finish**: when the workflow returns, act on its state:
   - completed cleanly -> `swarm finish <run-dir> --status completed`, then
     synthesize/present results (read full result files, not just summaries).
   - `paused == "paused_for_budget"` or `"agent_ceiling"` ->
     `swarm finish <run-dir> --status paused_for_budget`; tell the user what
     remains and how to resume.
   - failures -> report which tasks failed/skipped and why; ask the user whether
     to retry (resume re-runs only missing tasks) before
     `swarm finish <run-dir> --status failed-partial`.
   - implement runs: after integrate's worktree branch passes tests, show the
     user the merge plan and ONLY on approval merge `swarm/<run>/integration`
     into their branch.

## Resume (/swarm resume)

1. Find the run: `swarm status <run-dir>` (the SessionStart nag names it; or
   `ls ~/.claude/swarm/runs/<project-slug>/`).
2. `ARGS=$(swarm args <run-dir>/graph.json --resume)` - takes the resume lock;
   refuses if another session holds it fresh. Scans results/, verifies hashes,
   rebuilds the completed map.
3. Orphan implement branches: for any implement task WITHOUT a result file but
   WITH a `swarm/<run>/<task>` branch, delete branch + worktree (partial work
   from a dead run) and tell the user what was discarded.
4. **Ask the user** with precise numbers: done/pending/failed counts, discarded
   partials, estimated remaining cost. Only on approval invoke
   `{name: "swarm-run", args: <ARGS>}`.
5. Finish as above. If all tasks were already done (finalize case), skip the
   workflow and go straight to synthesis + `swarm finish --status completed`.

## Red flags - stop and fix the graph

| Smell | Reality |
|---|---|
| 3 mega-tasks | You skipped decomposition. Split until tasks are single-purpose. |
| "phase 1 -> phase 2" deps | Barriers, not data deps. Wire tasks to the specific results they consume. |
| One task depends on 10+ others | Fan-in mush. Build a reduction tree. |
| Verify task per finding task everywhere | Cost doubling. Cluster-verify 4-6 siblings. |
| Packet says "see the conversation" | Workers have no conversation. Self-contained or broken. |
| Editing graph.json after results exist | Hash mismatch will (correctly) refuse to resume. New run instead. |
```

- [ ] **Step 4: References**

`skill/references/graph-format.md`:
```markdown
# graph.json format (version 1)

Location: `~/.claude/swarm/runs/<project-slug>/<run-id>/graph.json`.
Generate run ids as `YYYY-MM-DD-<short-slug>`. Compute graph_hash with
`swarm validate --print-hash` after editing tasks (or let `swarm validate` tell
you the expected value).

​```json
{
  "version": 1,
  "run_id": "2026-06-10-auth-audit",
  "goal": "one paragraph",
  "project": "/abs/project/path",
  "graph_hash": "<from swarm validate>",
  "budget_tokens": null,
  "agent_ceiling": null,
  "tasks": [
    {
      "id": "scan-routes",
      "title": "Scan HTTP routes for auth gaps",
      "type": "research",
      "prompt": "Full self-contained instructions. 400+ chars typical.",
      "packet": "packets/scan-routes.md",
      "deps": [],
      "agent_type": "swarm-reader",
      "isolation": null,
      "schema": {
        "type": "object",
        "properties": {
          "summary": {"type": "string", "maxLength": 2000},
          "findings": {"type": "array", "items": {"type": "object"}}
        },
        "required": ["summary"]
      },
      "max_retries": 1
    }
  ]
}
​```

Rules enforced by `swarm validate` (errors block launch): version 1; unique ids;
known types; deps exist; no cycles; fan-in <= 8; every schema has
summary:string maxLength<=2000; non-empty prompts; graph_hash matches.
Warnings: >25 tasks, median prompt <400 chars, verify ratio >30%, barrier smell.

Task types: research, review (read-only; swarm-reader), verify (swarm-verifier),
implement (swarm-implementer + isolation worktree), integrate (general-purpose +
isolation worktree), synthesize (general-purpose).

Implement-task schema must add: branch, worktree_path, files_touched, commits.
```

`skill/references/packet-guide.md`:
```markdown
# Context packets

One file per task: `packets/<id>.md`. The worker Reads it first. It is the ONLY
context the worker gets besides its prompt and dep summaries.

Structure every packet as:

1. **Goal** - the run's goal in 2-3 sentences, then this task's place in it.
2. **Scope** - exactly which files/dirs/questions this task owns. Name what is
   OUT of scope (especially neighbors owned by sibling tasks).
3. **Constraints** - project conventions, interfaces to respect, versions,
   commands that work here (e.g. `python3 -m pytest`).
4. **Inputs** - where to look first; for implement tasks: the branch naming line
   `git checkout -b swarm/<run-id>/<task-id>` with values filled in.
5. **Output contract** - what each schema field must contain; what a GOOD
   summary looks like for this task (one example sentence).
6. **Do not** - the sharp edges: files not to touch, approaches known to fail
   (from dead-ends), anything that would collide with sibling tasks.

Self-containment test: a stranger with only packet+prompt can do the work.
Never write "as discussed" or "see above" - there is no above.
```

`skill/references/shapes.md`:
```markdown
# Graph shapes

## Research sweep (read-only)

N independent research tasks (one per question/subsystem/modality), cluster
verifiers (one per 4-6 researchers), reduction tree into synthesize.

​```
r1..r12 (research, no deps)
v1 (verify, deps r1-r4)  v2 (verify, deps r5-r8)  v3 (verify, deps r9-r12)
syn-a (synthesize, deps v1,v2)   syn-b (synthesize, deps v3)
final (synthesize, deps syn-a, syn-b)
​```

Width 12 honest parallel reads; nothing waits that doesn't have to.

## Implement from plan

For a plan with tasks T1..Tn: implement tasks are file-disjoint lanes; deps only
where one lane consumes another's interface; per-lane verify for anything that
feeds another implement; one quarantined integrate at the end.

​```
impl-core (implement)            impl-cli (implement, deps: impl-core)
impl-docs (implement)            verify-core (verify, deps: impl-core)
integrate (integrate, deps: impl-cli, impl-docs, verify-core)
​```

integrate merges `swarm/<run>/<task>` branches in dependency order onto
`swarm/<run>/integration`, runs the full test suite, resolves trivial conflicts,
escalates real ones in its result. The merge to the user's branch happens in the
main session with user approval - never inside the workflow.

## Resume semantics (any shape)

Completed tasks short-circuit from the results map. Implement tasks with partial
work but no result are delete-and-restart (orphan branch cleanup) - never
continued. All of this is automatic via `swarm args --resume`; your job is only
to ask the user first.
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: workflow wrapper, worker agents, swarm skill"
```

---

### Task 8: installer

**Files:**
- Create: `swarm_lib/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Failing test**

`tests/test_install.py`:
```python
import json
from pathlib import Path

from swarm_lib import install


EXISTING = {
    "hooks": {
        "SubagentStop": [{"hooks": [{"type": "command", "command": "python3 -m agent_pd.hook"}]},
                          {"hooks": [{"type": "command", "command": '"/py" -m tend.hook'}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": '"/py" -m tend.hook'}]}],
    },
    "statusLine": {"type": "command", "command": '"/py" -m tend.statusline'},
    "model": "claude-fable-5[1m]",
}


def claude_dir(tmp_path):
    d = tmp_path / "claude"
    d.mkdir(exist_ok=True)
    return d


def test_install_registers_hooks_and_copies_artifacts(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"][ev] for h in e["hooks"]]
        assert any("-m swarm_lib.hook" in c for c in cmds)
        assert any("agent_pd" in c or "tend" in c for c in cmds)  # preserved
    assert s["statusLine"]["command"] == '"/py" -m tend.statusline'  # untouched
    wf = (cd / "workflows" / "swarm-run.js").read_text()
    assert wf.startswith("export const meta")
    assert "async function runGraph" in wf
    assert "export function" not in wf and "export async" not in wf  # stripped
    assert "return __out" in wf
    assert (cd / "skills" / "swarm" / "SKILL.md").exists()
    assert (cd / "skills" / "swarm" / "references" / "packet-guide.md").exists()
    for a in ("swarm-reader", "swarm-verifier", "swarm-implementer"):
        assert (cd / "agents" / f"{a}.md").exists()
    assert (tmp_path / "settings.json.bak-swarm").exists()


def test_install_idempotent(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    once = json.loads(sp.read_text())
    install.install(sp, cd)
    assert json.loads(sp.read_text()) == once


def test_uninstall_reverts(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(EXISTING))
    cd = claude_dir(tmp_path)
    install.install(sp, cd)
    install.uninstall(sp, cd)
    s = json.loads(sp.read_text())
    for ev in ("SubagentStop", "SessionStart"):
        cmds = [h["command"] for e in s["hooks"].get(ev, []) for h in e["hooks"]]
        assert not any("swarm_lib" in c for c in cmds)
        assert cmds  # tend/agent-pd entries survive
    assert not (cd / "workflows" / "swarm-run.js").exists()
    assert not (cd / "skills" / "swarm").exists()
    assert not (cd / "agents" / "swarm-reader.md").exists()


def test_corrupted_settings_refused(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text('{"hooks": [BROKEN')
    before = sp.read_text()
    try:
        install.install(sp, claude_dir(tmp_path))
        assert False, "should have raised"
    except install.SettingsError:
        pass
    assert sp.read_text() == before


def test_generated_workflow_parses_as_module(tmp_path):
    text = install.generate_workflow()
    assert text.count("export const meta") == 1
    assert "SWARM-TASK run=" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_install.py -v` — FAIL.

- [ ] **Step 3: Implement `swarm_lib/install.py`**

```python
"""Install swarm into ~/.claude: hooks (parse-or-refuse settings merge, tend-hardened),
workflow generation, skill + agent copies. Fully reversible."""
import json
import os
import shutil
import sys
from pathlib import Path

HOOK_EVENTS = ["SubagentStop", "SessionStart"]
HOOK_MARKER = "-m swarm_lib.hook"
AGENT_FILES = ["swarm-reader.md", "swarm-verifier.md", "swarm-implementer.md"]


class SettingsError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def hook_command() -> str:
    return f'"{sys.executable}" {HOOK_MARKER}'


def generate_workflow() -> str:
    wf = repo_root() / "workflows"
    header = (wf / "swarm-run.header.js").read_text(encoding="utf-8")
    body = (wf / "run_graph.mjs").read_text(encoding="utf-8")
    footer = (wf / "swarm-run.footer.js").read_text(encoding="utf-8")
    body = (body.replace("export async function", "async function")
                .replace("export function", "function")
                .replace("export const", "const"))
    return "\n".join([header.strip(), "", body.strip(), "", footer.strip(), ""])


def _load_settings(sp: Path) -> dict:
    if not sp.exists():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"{sp} exists but is not valid JSON ({e}). Fix it or restore "
            f"{sp.name}.bak-swarm before running swarm install/uninstall."
        ) from e


def _has_marker(entries) -> bool:
    return any(HOOK_MARKER in (h.get("command") or "")
               for e in entries for h in (e.get("hooks") or []))


def _write_settings(sp: Path, settings: dict) -> None:
    backup = sp.with_name(sp.name + ".bak-swarm")
    mode = None
    if sp.exists():
        mode = sp.stat().st_mode
        backup.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(backup, mode)
    tmp = sp.with_name(f"{sp.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(sp)
    if mode is not None:
        os.chmod(sp, mode)


def install(settings_path, claude_dir) -> None:
    sp = Path(settings_path).resolve()
    cd = Path(claude_dir)
    settings = _load_settings(sp)
    hooks = settings.setdefault("hooks", {})
    for ev in HOOK_EVENTS:
        entries = hooks.setdefault(ev, [])
        if not _has_marker(entries):
            entries.append({"hooks": [{"type": "command", "command": hook_command()}]})
    _write_settings(sp, settings)

    (cd / "workflows").mkdir(parents=True, exist_ok=True)
    (cd / "workflows" / "swarm-run.js").write_text(generate_workflow(), encoding="utf-8")
    skill_dst = cd / "skills" / "swarm"
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
    shutil.copytree(repo_root() / "skill", skill_dst)
    (cd / "agents").mkdir(parents=True, exist_ok=True)
    for name in AGENT_FILES:
        shutil.copy2(repo_root() / "agents" / name, cd / "agents" / name)


def uninstall(settings_path, claude_dir) -> None:
    sp = Path(settings_path).resolve()
    cd = Path(claude_dir)
    settings = _load_settings(sp)
    hooks = settings.get("hooks", {})
    changed = False
    for ev in list(hooks):
        filtered = [e for e in hooks[ev] if not _has_marker([e])]
        if len(filtered) != len(hooks[ev]):
            changed = True
            hooks[ev] = filtered
            if not hooks[ev]:
                del hooks[ev]
    if changed:
        _write_settings(sp, settings)
    (cd / "workflows" / "swarm-run.js").unlink(missing_ok=True)
    shutil.rmtree(cd / "skills" / "swarm", ignore_errors=True)
    for name in AGENT_FILES:
        (cd / "agents" / name).unlink(missing_ok=True)
```

- [ ] **Step 4: Pass + commit**

Run: `python3 -m pytest tests/test_install.py -v` — 5 passed.

```bash
git add -A && git commit -m "feat: hardened installer with workflow generation"
```

---

### Task 9: CLI

**Files:**
- Create: `swarm_lib/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Failing test**

`tests/test_cli.py`:
```python
import json

from conftest import make_run, task

from swarm_lib import cli, paths, runs


def graph_with_hash(tmp_path, tasks):
    from swarm_lib import graph as g
    rd = make_run(tmp_path, tasks=tasks)
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    return rd, gr


def test_validate_ok_and_fail(tmp_path, capsys):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["validate", str(rd / "graph.json")]) == 0
    bad = paths.read_json(rd / "graph.json")
    bad["tasks"][0]["deps"] = ["ghost"]
    paths.write_json_atomic(rd / "graph.json", bad)
    assert cli.main(["validate", str(rd / "graph.json")]) == 1
    assert "dangling" in capsys.readouterr().out


def test_validate_print_hash(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["validate", str(rd / "graph.json"), "--print-hash"]) == 0
    assert gr["graph_hash"] in capsys.readouterr().out


def test_args_builds_workflow_args(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b", deps=["a"])])
    assert cli.main(["args", str(rd / "graph.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_dir"] == str(rd)
    assert out["graph_hash"] == gr["graph_hash"]
    assert out["results_dir"] == str(rd / "results")
    assert out["tasks"][0]["packet_path"] == str(rd / "packets" / "a.md")
    assert out["completed"] == {}


def test_args_resume_takes_lock_and_loads_completed(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b", deps=["a"])])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": gr["graph_hash"],
                             "status": "ok", "output": {"summary": "done a"},
                             "summary": "done a", "ts": 0})
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["completed"] == {"a": {"summary": "done a"}}
    assert runs.lock_path(rd).exists()
    # second resume refused while lock fresh
    assert cli.main(["args", str(rd / "graph.json"), "--resume"]) == 1


def test_finish_writes_state_and_releases_lock(tmp_path):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    runs.take_lock(rd, "x")
    assert cli.main(["finish", str(rd), "--status", "completed"]) == 0
    assert runs.read_state(rd)["status"] == "completed"
    assert not runs.lock_path(rd).exists()


def test_status_lists_tasks(tmp_path, capsys):
    rd, gr = graph_with_hash(tmp_path, [task("a"), task("b")])
    paths.write_json_atomic(runs.results_dir(rd) / "a.json",
                            {"version": 1, "task": "a", "hash": gr["graph_hash"],
                             "status": "ok", "output": {}, "summary": "s", "ts": 0})
    assert cli.main(["status", str(rd)]) == 0
    out = capsys.readouterr().out
    assert "1/2" in out and "done" in out and "pending" in out


def test_abandon(tmp_path):
    rd, _ = graph_with_hash(tmp_path, [task("a")])
    assert cli.main(["abandon", str(rd)]) == 0
    assert runs.read_state(rd)["status"] == "abandoned"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_cli.py -v` — FAIL.

- [ ] **Step 3: Implement `swarm_lib/cli.py`**

```python
"""swarm CLI: validate | args | finish | status | abandon | install | uninstall."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import graph as graph_mod
from . import install as install_mod
from . import paths, runs


def cmd_validate(a) -> int:
    gr = paths.read_json(a.graph)
    if gr is None:
        print(f"cannot read graph: {a.graph}")
        return 1
    if a.print_hash:
        print(graph_mod.compute_hash(gr))
        return 0
    issues = graph_mod.validate(gr)
    for i in issues:
        print(f"{i['level']}[{i['code']}]: {i['msg']}")
    if graph_mod.errors(issues):
        return 1
    print(f"ok: {len(gr.get('tasks', []))} tasks, hash {graph_mod.compute_hash(gr)}")
    return 0


def cmd_args(a) -> int:
    gpath = Path(a.graph).resolve()
    gr = paths.read_json(gpath)
    if gr is None:
        print(f"cannot read graph: {gpath}", file=sys.stderr)
        return 1
    errs = graph_mod.errors(graph_mod.validate(gr))
    if errs:
        for i in errs:
            print(f"error[{i['code']}]: {i['msg']}", file=sys.stderr)
        return 1
    rd = gpath.parent
    completed = {}
    if a.resume:
        owner = os.environ.get("CLAUDE_SESSION_ID", f"cli-{os.getpid()}")
        if not runs.take_lock(rd, owner):
            print("resume.lock held by another session (fresh) - refusing. "
                  "Wait, or delete resume.lock if you are sure.", file=sys.stderr)
            return 1
        found, bad = runs.scan_results(rd, gr.get("graph_hash"))
        for b in bad:
            print(f"warning: ignoring bad result file {b}", file=sys.stderr)
        completed = {k: {"summary": v.get("summary", "")} for k, v in found.items()}
    out = {
        "run_dir": str(rd),
        "graph_hash": gr.get("graph_hash"),
        "results_dir": str(runs.results_dir(rd)),
        "agent_ceiling": gr.get("agent_ceiling"),
        "tasks": [{
            "id": t["id"], "title": t.get("title", ""), "type": t["type"],
            "prompt": t["prompt"], "deps": t.get("deps", []),
            "agent_type": t.get("agent_type"), "isolation": t.get("isolation"),
            "schema": t.get("schema"), "max_retries": t.get("max_retries", 1),
            "packet_path": str(rd / t.get("packet", f"packets/{t['id']}.md")),
        } for t in gr["tasks"]],
        "completed": completed,
    }
    print(json.dumps(out))
    return 0


def cmd_finish(a) -> int:
    rd = Path(a.run_dir).resolve()
    runs.write_state(rd, a.status)
    runs.release_lock(rd)
    print(f"run-state written: {a.status}")
    return 0


def cmd_status(a) -> int:
    rd = Path(a.run_dir).resolve()
    gr = paths.read_json(rd / "graph.json")
    if not gr:
        print(f"no graph.json in {rd}")
        return 1
    completed, bad = runs.scan_results(rd, gr.get("graph_hash"))
    st = runs.read_state(rd)
    status = (st or {}).get("status", "in progress / interrupted")
    print(f"run    {rd.name}  [{status}]")
    extra = f", {len(bad)} bad result files" if bad else ""
    print(f"tasks  {len(completed)}/{len(gr['tasks'])} done{extra}")
    for t in gr["tasks"]:
        mark = "done" if t["id"] in completed else "pending"
        print(f"  [{mark:>7}] {t['type']:<11} {t['id']:<16} {t.get('title', '')[:56]}")
    return 0


def cmd_abandon(a) -> int:
    runs.abandon(Path(a.run_dir).resolve())
    print("abandoned")
    return 0


def cmd_install(a) -> int:
    try:
        install_mod.install(a.settings, a.claude_dir)
    except install_mod.SettingsError as e:
        print(str(e))
        return 1
    print(f"swarm installed: hooks in {a.settings}; skill/workflow/agents in {a.claude_dir}")
    print("Restart your Claude Code session to activate.")
    return 0


def cmd_uninstall(a) -> int:
    try:
        install_mod.uninstall(a.settings, a.claude_dir)
    except install_mod.SettingsError as e:
        print(str(e))
        return 1
    print("swarm removed")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="swarm",
                                     description="Graph-first multi-agent orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    home_claude = str(Path.home() / ".claude")

    p = sub.add_parser("validate")
    p.add_argument("graph")
    p.add_argument("--print-hash", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("args")
    p.add_argument("graph")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(fn=cmd_args)

    p = sub.add_parser("finish")
    p.add_argument("run_dir")
    p.add_argument("--status", required=True,
                   choices=["completed", "paused_for_budget", "failed-partial"])
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("status")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("abandon")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_abandon)

    for name, fn in (("install", cmd_install), ("uninstall", cmd_uninstall)):
        p = sub.add_parser(name)
        p.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
        p.add_argument("--claude-dir", default=home_claude)
        p.set_defaults(fn=fn)

    a = parser.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Pass + commit**

Run: `python3 -m pytest tests/test_cli.py -v` — 7 passed. Full: `python3 -m pytest -q` — 41 passed.

```bash
git add -A && git commit -m "feat: swarm CLI"
```

---

### Task 10: integration tests + README

**Files:**
- Create: `tests/test_integration.py`
- Modify: `README.md`

- [ ] **Step 1: Integration test (subprocess entry points + full checkpoint cycle)**

`tests/test_integration.py`:
```python
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
    res = subprocess.run(["node", "--test", "tests/node/"], capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
```

- [ ] **Step 2: Run**

Run: `python3 -m pytest tests/test_integration.py -v` — 3 passed.

- [ ] **Step 3: Real README**

Replace `README.md` with:
```markdown
# swarm

Graph-first multi-agent orchestrator for Claude Code. Say `/swarm <goal>` and a
typed task graph is decomposed, validated, adversarially reviewed, then executed
by a generic DAG workflow with maximal parallelism. Every finished task is
checkpointed to disk by a SubagentStop hook, so a run survives rate limits,
killed sessions, and crashes - the next session offers `/swarm resume`, which
asks before re-running only the missing work.

## Install

    python3 -m pip install --user -e .
    swarm install        # hooks into settings.json; copies skill/workflow/agents
    # restart your Claude Code session

## Pieces

| Piece | Where it lands | Role |
|---|---|---|
| `swarm` skill | `~/.claude/skills/swarm/` | decomposition + resume protocol |
| `swarm-run` workflow | `~/.claude/workflows/swarm-run.js` | pure DAG scheduler (generated) |
| worker agents | `~/.claude/agents/swarm-*.md` | least-privilege reader/verifier/implementer |
| hooks | settings.json (SubagentStop, SessionStart) | checkpoints + resume nag |
| run state | `~/.claude/swarm/runs/<project>/<run-id>/` | graph, packets, results, state |

## CLI

    swarm validate <graph.json> [--print-hash]
    swarm args <graph.json> [--resume]     # workflow args; --resume takes the lock
    swarm status <run-dir>
    swarm finish <run-dir> --status completed|paused_for_budget|failed-partial
    swarm abandon <run-dir>
    swarm install / swarm uninstall

Tests: `python3 -m pytest` (Python) and `node --test tests/node/` (scheduler).
Spec: `docs/superpowers/specs/2026-06-10-swarm-orchestrator-design.md`.
Sibling project: [tend](/Users/varma/tend) - context hygiene; swarm reads its
rate-limit tee for launch headroom.
```

- [ ] **Step 4: Full suites + commit**

Run: `python3 -m pytest -q` — 44 passed. `node --test tests/node/` — 14 pass.

```bash
git add -A && git commit -m "test: integration tests; real README"
```

---

### Task 11: CONTROLLER — live install, demo swarm, interrupt/resume, evidence

**Executed by the controller in the main session** (touches real settings.json; invokes the Workflow tool, which subagents cannot).

- [ ] **Step 1:** `python3 -m pip install --user -e /Users/varma/swarm && swarm install` (via `python3 -m swarm_lib.cli install` if PATH lacks `swarm`). Verify settings.json: swarm hooks on SubagentStop/SessionStart alongside tend + agent-pd; skill/workflow/agents files in place.
- [ ] **Step 2:** Hook smoke test: feed a fixture SubagentStop payload (real captured shape) through `python3 -m swarm_lib.hook`; confirm a result file appears.
- [ ] **Step 3:** Demo swarm: author a small real review graph over `/Users/varma/tend` (6-8 research/review tasks + 2 cluster verifiers + synthesize), packets included, `swarm validate`, then invoke `{name: "swarm-run", args}` via the Workflow tool. Confirm: parallel launches in `/workflows` view, checkpoint files appearing mid-run, `swarm status` mid-run from a terminal.
- [ ] **Step 4:** Interrupt/resume demo: kill the workflow mid-run (TaskStop), confirm `swarm status` shows partial completion, run `swarm args --resume`, re-invoke, confirm completed tasks short-circuit and the run finishes; `swarm finish --status completed`.
- [ ] **Step 5:** Evidence: `capture` screenshots (`swarm status` mid-run + after resume; the SessionStart nag in a fresh session), write `docs/test-evidence.md`, commit.
- [ ] **Step 6:** Merge to master per finishing-a-development-branch.

---

## Self-review checklist (done at plan-writing time)

- **Spec coverage:** marker/checkpoint data flow (T2, T5), graph + validation incl. fan-in/barrier/verify-ratio (T3), run state/locks/nags (T4, T5), pure scheduler with budget reservation, null disambiguation, transitive skip, ceiling, resume (T6), workers + skill + packet/shape guides incl. delete-and-restart resume and quarantined integrate (T7), generated workflow with stripped exports (T8), CLI surface validate/args/finish/status/abandon/install (T9), integration + README (T10), spike + live demo + interrupt/resume evidence (T0, T11). Rate-limit headroom: skill Process step 2 (T7). SDK lift: runGraph purity (T6).
- **Type consistency:** `runs.scan_results(run_dir, graph_hash)` used by hook/cli/tests identically; result-file schema {version, task, hash, status, structured, output, summary, agent_id, ts} consistent in T5/T9/T10 fixtures; args object keys (run_dir, graph_hash, results_dir, agent_ceiling, tasks[].packet_path, completed) match T6 scheduler and T9 producer; marker format identical in T2 and T6 buildPrompt.
- **No placeholders:** every code step contains complete runnable code.
