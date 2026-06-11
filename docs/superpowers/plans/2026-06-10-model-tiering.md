# Model Tiering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-task model tiers in swarm (planner judgment + executor safety-net defaults + failure fallback) and an advisory delegation guard in tend, per `docs/superpowers/specs/2026-06-10-model-tiering-design.md`.

**Architecture:** swarm: the graph gains an optional `model` field (validated allow-list); `run_graph.mjs` resolves an effective model per task (explicit > type default capped at session tier > inherit) and drops the override on the final retry, reporting fallbacks. tend: a new `agentguard` module beside `readguard` nudges sessions that spawn subagents without a model, using the session tier from the existing ctx.json tee.

**Tech Stack:** Node 18+ (`node --test`) for the executor, Python 3.11 + pytest for everything else. Two repos: `/Users/varma/swarm` (Tasks 1–6), `/Users/varma/tend` (Tasks 7–9).

**Conventions:** swarm tests: `cd /Users/varma/swarm && python3 -m pytest tests/ -q` (this also runs the Node suite via `test_node_suite_passes`). Node-only: `node --test tests/node/*.test.mjs`. tend tests: `cd /Users/varma/tend && python3 -m pytest tests/ -q`. Branches: `feat/model-tiering` (swarm), `feat/delegation-guard` (tend). Commits: conventional prefixes, **never any Co-Authored-By line**. Check pytest exit codes directly — do not pipe to `tail` before branching on success.

---

### Task 1: Executor — effective-model resolution

**Files:**
- Modify: `/Users/varma/swarm/workflows/run_graph.mjs` (exports + `attempt`)
- Test: `/Users/varma/swarm/tests/node/executor.test.mjs`

- [ ] **Step 1: Create the swarm branch**

```bash
cd /Users/varma/swarm && git checkout -b feat/model-tiering
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/node/executor.test.mjs`. Note the import line at the top of the file must gain `effectiveModel`:

```js
import { runGraph, validateGraph, buildPrompt, effectiveModel, RESERVE_TOKENS } from '../../workflows/run_graph.mjs'
```

```js
test('effectiveModel: type defaults, explicit override, synthesize inherits', () => {
  assert.equal(effectiveModel(T('a'), null), 'sonnet')                       // research
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), null), 'opus')
  assert.equal(effectiveModel(T('a', [], { type: 'integrate' }), null), 'opus')
  assert.equal(effectiveModel(T('a', [], { type: 'synthesize' }), null), null)
  assert.equal(effectiveModel(T('a', [], { model: 'haiku' }), null), 'haiku') // explicit wins
})

test('effectiveModel: session tier caps defaults but never explicit values', () => {
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), 'sonnet'), 'sonnet') // opus capped
  assert.equal(effectiveModel(T('a'), 'opus'), 'sonnet')                              // below cap: kept
  assert.equal(effectiveModel(T('a', [], { model: 'fable' }), 'sonnet'), 'fable')     // explicit exceeds
  assert.equal(effectiveModel(T('a', [], { type: 'synthesize' }), 'opus'), null)      // inherit stays inherit
})

test('runGraph passes the effective model to agent opts', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('r'), T('i', [], { type: 'implement' }), T('s', [], { type: 'synthesize' })]), a.fn, null, null)
  const byLabel = Object.fromEntries(a.calls.map(c => [c.opts.label, c.opts]))
  assert.equal(byLabel['research:r'].model, 'sonnet')
  assert.equal(byLabel['implement:i'].model, 'opus')
  assert.equal('model' in byLabel['synthesize:s'], false)  // inherit = option absent
})

test('runGraph respects session_model cap from args', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('i', [], { type: 'implement' })], {}, { session_model: 'sonnet' }), a.fn, null, null)
  assert.equal(a.calls[0].opts.model, 'sonnet')
})

test('validateGraph rejects unknown model values', () => {
  const errs = validateGraph([T('a', [], { model: 'gpt5' })], {})
  assert.ok(errs.some(e => e.includes('unknown model')))
  assert.deepEqual(validateGraph([T('a', [], { model: 'haiku' })], {}), [])
})
```

- [ ] **Step 3: Run to verify they fail**

Run: `node --test tests/node/*.test.mjs 2>&1 | tail -5`
Expected: FAIL — `effectiveModel` is not exported.

- [ ] **Step 4: Implement**

In `workflows/run_graph.mjs`, add below the existing constants (`FLOOR_TOKENS` line):

```js
export const LADDER = ['haiku', 'sonnet', 'opus', 'fable']
export const TYPE_MODEL = {
  research: 'sonnet', review: 'sonnet', verify: 'sonnet',
  implement: 'opus', integrate: 'opus',
  synthesize: null, // inherit the session model
}

export function effectiveModel(t, sessionModel) {
  if (t.model) return t.model // planner's explicit choice always wins
  let m = TYPE_MODEL[t.type] ?? null
  if (m && LADDER.includes(sessionModel)) {
    m = LADDER[Math.min(LADDER.indexOf(m), LADDER.indexOf(sessionModel))]
  }
  return m
}
```

In `validateGraph`, add inside the per-task checks (next to the task-id regex loop):

```js
  for (const t of tasks) {
    if (t.model && !LADDER.includes(t.model)) errors.push(`${t.id}: unknown model ${t.model}`)
  }
```

In `runGraph`'s `attempt`, compute the model once and pass it (fallback handling comes in Task 2 — here just pass it on every try):

```js
  const attempt = async (t) => {
    const intended = effectiveModel(t, argsObj.session_model)
    let tries = 0
    while (tries <= (t.max_retries ?? 1)) {
      let res
      try {
        res = await agentFn(buildPrompt(argsObj, t, completed), {
          label: `${t.type}:${t.id}`,
          phase: t.type,
          schema: t.schema,
          ...(t.agent_type ? { agentType: t.agent_type } : {}),
          ...(t.isolation ? { isolation: t.isolation } : {}),
          ...(intended ? { model: intended } : {}),
        })
      } catch (e) {
        if (logFn) logFn(`swarm: ${t.id} threw: ${e && e.message ? e.message : e}`)
        res = null
      }
      if (res !== null && res !== undefined) return res
      if (budget && budget.total && budget.remaining() < RESERVE_TOKENS) return BUDGET_NULL
      tries++
    }
    return null
  }
```

- [ ] **Step 5: Run the full swarm suite**

Run: `python3 -m pytest tests/ -q` — Expected: all pass (pytest wraps the Node suite).

- [ ] **Step 6: Commit**

```bash
git add workflows/run_graph.mjs tests/node/executor.test.mjs
git commit -m "feat: per-task effective model - explicit > capped type default > inherit"
```

---

### Task 2: Executor — final-retry fallback + fallbacks report

**Files:**
- Modify: `/Users/varma/swarm/workflows/run_graph.mjs` (`attempt`, `runGraph` return)
- Test: `/Users/varma/swarm/tests/node/executor.test.mjs`

- [ ] **Step 1: Write the failing tests**

Append:

```js
test('final retry drops the model override and records the fallback', async () => {
  const calls = []
  const fn = async (prompt, opts) => {
    calls.push(opts.model ?? 'inherit')
    return opts.model ? null : { summary: 'ok on inherit' } // tier "unavailable"
  }
  const logs = []
  const out = await runGraph(ARGS([T('a')]), fn, (m) => logs.push(m), null)
  assert.deepEqual(calls, ['sonnet', 'inherit'])      // max_retries 1: try tier, then inherit
  assert.equal(out.completed.a.summary, 'ok on inherit')
  assert.deepEqual(out.fallbacks, { a: 'sonnet->inherit' })
  assert.ok(logs.some(l => l.includes("model 'sonnet' unavailable or failing")))
})

test('max_retries 0 keeps the intended model on its only attempt', async () => {
  const calls = []
  const fn = async (prompt, opts) => { calls.push(opts.model ?? 'inherit'); return null }
  const out = await runGraph(ARGS([T('a', [], { max_retries: 0 })]), fn, null, null)
  assert.deepEqual(calls, ['sonnet'])                 // never silently downgraded
  assert.deepEqual(out.failed, ['a'])
  assert.deepEqual(out.fallbacks, {})
})

test('no fallback recorded when the tier works first try', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a')]), a.fn, null, null)
  assert.deepEqual(out.fallbacks, {})
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/node/*.test.mjs 2>&1 | tail -5` — Expected: FAIL (`fallbacks` undefined; second call still passes `sonnet`).

- [ ] **Step 3: Implement**

In `runGraph`, declare `const fallbacks = {}` next to `const failedSet = new Set()`. Replace `attempt` with the fallback-aware version (whole function):

```js
  const attempt = async (t) => {
    const intended = effectiveModel(t, argsObj.session_model)
    const maxTries = t.max_retries ?? 1
    let tries = 0
    while (tries <= maxTries) {
      // Final retry of a tiered task runs on the session model: it is by
      // definition being served, so an unavailable/failing tier degrades
      // loudly instead of failing the task outright.
      const fallback = intended && tries === maxTries && tries > 0
      if (fallback) {
        fallbacks[t.id] = `${intended}->inherit`
        if (logFn) logFn(`swarm: ${t.id}: model '${intended}' unavailable or failing - retrying on session model`)
      }
      const model = fallback ? null : intended
      let res
      try {
        res = await agentFn(buildPrompt(argsObj, t, completed), {
          label: `${t.type}:${t.id}`,
          phase: t.type,
          schema: t.schema,
          ...(t.agent_type ? { agentType: t.agent_type } : {}),
          ...(t.isolation ? { isolation: t.isolation } : {}),
          ...(model ? { model } : {}),
        })
      } catch (e) {
        if (logFn) logFn(`swarm: ${t.id} threw: ${e && e.message ? e.message : e}`)
        res = null
      }
      if (res !== null && res !== undefined) return res
      if (budget && budget.total && budget.remaining() < RESERVE_TOKENS) return BUDGET_NULL
      tries++
    }
    return null
  }
```

Add `fallbacks` to BOTH return objects of `runGraph` (the early `fatal` return gets `fallbacks: {}`, the final return gets `fallbacks`).

- [ ] **Step 4: Run the full swarm suite**

Run: `python3 -m pytest tests/ -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add workflows/run_graph.mjs tests/node/executor.test.mjs
git commit -m "feat: final-retry fallback to session model with loud fallbacks report"
```

---

### Task 3: Validator + CLI — model allow-list, --session-model, args passthrough

**Files:**
- Modify: `/Users/varma/swarm/swarm_lib/graph.py`, `/Users/varma/swarm/swarm_lib/cli.py:76-84,160-163`
- Test: `/Users/varma/swarm/tests/test_graph.py`, `/Users/varma/swarm/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Check `tests/conftest.py` for the existing `task()`/`make_run()` helpers and reuse them. Append to `tests/test_graph.py`:

```python
def test_model_allow_list():
    from swarm_lib import graph as g

    base = {"version": 1, "tasks": [task("a", model="haiku"), task("b", model="gpt5")]}
    issues = g.validate(base)
    msgs = [i["msg"] for i in g.errors(issues)]
    assert any("unknown model gpt5" in m for m in msgs)
    assert not any("haiku" in m for m in msgs)


def test_model_omitted_is_valid():
    from swarm_lib import graph as g

    assert not [i for i in g.errors(g.validate({"version": 1, "tasks": [task("a")]}))
                if i["code"] == "model"]
```

(If `task()` in conftest does not accept arbitrary kwargs, extend it: `def task(id, deps=None, **extra): return {..., **extra}` — keep existing defaults intact.)

Append to `tests/test_cli.py` (match its existing style for invoking `cli.main` and reading stdout via capsys):

```python
def test_args_includes_model_and_session_model(tmp_path, swarm_home, capsys):
    import json
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a", model="haiku"), task("b")])
    gr = paths.read_json(rd / "graph.json")
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json"), "--session-model", "fable"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_model"] == "fable"
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["a"]["model"] == "haiku"
    assert by_id["b"]["model"] is None


def test_args_rejects_bad_session_model(tmp_path, swarm_home):
    import pytest
    from swarm_lib import cli

    with pytest.raises(SystemExit):
        cli.main(["args", "/nonexistent.json", "--session-model", "gpt5"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_graph.py tests/test_cli.py -q` — Expected: new tests FAIL (no model error code, no `--session-model` flag, KeyError `session_model`).

- [ ] **Step 3: Implement**

`swarm_lib/graph.py` — add next to `TYPES`:

```python
MODELS = {"haiku", "sonnet", "opus", "fable"}
```

and inside the per-task loop in `validate` (after the `type` check):

```python
        if t.get("model") is not None and t["model"] not in MODELS:
            err("model", f"{tid}: unknown model {t['model']} (use haiku|sonnet|opus|fable)")
```

`swarm_lib/cli.py` — in `cmd_args`'s `out` dict add `"session_model": a.session_model,` and in the task projection add `"model": t.get("model"),`. In `main()`, on the `args` subparser add:

```python
    p.add_argument("--session-model", default=None,
                   choices=["haiku", "sonnet", "opus", "fable"])
```

- [ ] **Step 4: Run the full swarm suite**

Run: `python3 -m pytest tests/ -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add swarm_lib/graph.py swarm_lib/cli.py tests/test_graph.py tests/test_cli.py
git commit -m "feat: model allow-list validation and --session-model passthrough in args"
```

---

### Task 4: Docs — graph-format + SKILL guidance

**Files:**
- Modify: `/Users/varma/swarm/skill/references/graph-format.md`, `/Users/varma/swarm/skill/SKILL.md`

- [ ] **Step 1: graph-format.md**

In the example task JSON, after `"isolation": null,` add `"model": "sonnet",`. After the "Task types" paragraph, append:

```markdown
## Model tiers

Optional per-task `model`: one of `haiku | sonnet | opus | fable` (tier
aliases only — versions are not addressable). Set it EXPLICITLY on every task
as a per-task judgment weighing quality stakes, ambiguity, complexity, token
cost, and retry economics — lowest tier that fits:

- `fable`/omit: decomposition-grade reasoning, ambiguous goals, final synthesis
- `opus`: real coding (implement/integrate, debugging, refactors)
- `sonnet`: clear-goal bounded work (scans, diff review, adversarial verify)
- `haiku`: mechanical (output/schema checks, extraction, formatting, capture)

Omitted -> the executor applies safety-net defaults by type
(research/review/verify -> sonnet; implement/integrate -> opus; synthesize ->
inherit), capped at the launching session's tier (`session_model`). Explicit
values are never capped. If a tier fails every retry, the final retry re-runs
on the session model and the run report lists it under `fallbacks`.
```

- [ ] **Step 2: SKILL.md**

In **Process step 3 (Decompose)**, append to the existing text:

```markdown
   Assign `model` explicitly per task (lowest tier that fits - see
   references/graph-format.md "Model tiers"): weigh quality stakes (does the
   result feed implement tasks?), ambiguity, complexity, token cost, retry
   economics. Mechanical checks -> haiku; bounded scans/verifies -> sonnet;
   real coding -> opus; judgment/synthesis -> omit (inherit). Defaults are a
   safety net, not a reason to skip the decision.
```

In **Process step 6 (Review gate)**, extend the attack list: change "(missing tasks, fake width, fan-in mush, packet gaps)" to "(missing tasks, fake width, fan-in mush, packet gaps, model tiers over- or under-provisioned for the task)".

In **Process step 7 (Launch)**, change the command to:

```markdown
7. **Launch**: `ARGS=$(swarm args <run-dir>/graph.json --session-model <your-tier>)`
   where `<your-tier>` is THIS session's model tier (haiku|sonnet|opus|fable -
   you know your own model), then invoke the Workflow tool:
   `{name: "swarm-run", args: <parsed ARGS JSON>}`.
```

In **Process step 8 (Finish)**, add a bullet:

```markdown
   - if the result has a non-empty `fallbacks` map, tell the user which tasks
     did not run on their intended tier (e.g. "design-api: fable->inherit").
```

- [ ] **Step 3: Commit**

```bash
git add skill/references/graph-format.md skill/SKILL.md
git commit -m "docs: model-tier guidance - per-task judgment, session-model launch, fallback reporting"
```

---

### Task 5: Agent definitions — tier frontmatter

**Files:**
- Modify: `/Users/varma/swarm/agents/swarm-reader.md`, `swarm-verifier.md`, `swarm-implementer.md` (frontmatter only)

- [ ] **Step 1: Add `model:` to each frontmatter block**

`swarm-reader.md` and `swarm-verifier.md`: add line `model: sonnet` before the closing `---`. `swarm-implementer.md`: add `model: opus`. (Executor/graph options take precedence; this covers ad-hoc spawns.)

- [ ] **Step 2: Run suite + commit**

Run: `python3 -m pytest tests/ -q` — Expected: all pass (frontmatter is not parsed by tests; this is a regression check).

```bash
git add agents/
git commit -m "feat: default model tiers in agent frontmatter (reader/verifier sonnet, implementer opus)"
```

---

### Task 6: swarm — merge + refresh installed artifacts

- [ ] **Step 1: Merge and verify**

```bash
cd /Users/varma/swarm && git checkout master && git merge --no-ff feat/model-tiering -m "Merge feat/model-tiering: per-task model tiers with fallback"
python3 -m pytest tests/ -q   # verify on merged result; then:
git branch -d feat/model-tiering
```

- [ ] **Step 2: Refresh the installed workflow/skill/agents**

```bash
swarm install 2>/dev/null || python3 -m swarm_lib.cli install
```

Expected: "swarm installed... Restart your Claude Code session to activate." Verify the embedded executor picked up the change: `grep -c "effectiveModel" ~/.claude/workflows/swarm-run.js` → ≥ 1. Note to user: new graphs launched from THIS session still use the old in-session skill copy; a restarted session gets the new one.

---

### Task 7: tend — session tier from ctx.json

**Files:**
- Modify: `/Users/varma/tend/tend/ctxmetrics.py`
- Test: `/Users/varma/tend/tests/test_ctxmetrics.py`

- [ ] **Step 1: Branch + failing test**

```bash
cd /Users/varma/tend && git checkout -b feat/delegation-guard
```

Append to `tests/test_ctxmetrics.py` (check its imports; it uses `paths.write_json_atomic` + `ctxmetrics`):

```python
def test_session_model_tier_mapping(tend_home):
    from tend import ctxmetrics, paths

    cases = {"Fable 5": "fable", "Opus 4.8": "opus", "claude-sonnet-4-6": "sonnet",
             "Haiku 4.5": "haiku", "Mystery Model": None}
    for name, tier in cases.items():
        paths.write_json_atomic(paths.session_dir("sm") / "ctx.json",
                                {"model": {"display_name": name}})
        assert ctxmetrics.session_model_tier("sm") == tier, name


def test_session_model_tier_no_ctx(tend_home):
    from tend import ctxmetrics

    assert ctxmetrics.session_model_tier("nope") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_ctxmetrics.py -q` — Expected: FAIL, no attribute `session_model_tier`.

- [ ] **Step 3: Implement** — append to `tend/ctxmetrics.py`:

```python
TIERS = ("haiku", "sonnet", "opus", "fable")


def session_model_tier(sid):
    """Best-effort tier of the session's model, from the statusline tee."""
    ctx = read_ctx(sid) or {}
    name = ((ctx.get("model") or {}).get("display_name") or "").lower()
    for tier in TIERS:
        if tier in name:
            return tier
    return None
```

- [ ] **Step 4: Run + commit**

Run: `python3 -m pytest tests/ -q` — Expected: all pass.

```bash
git add tend/ctxmetrics.py tests/test_ctxmetrics.py
git commit -m "feat: session model tier from ctx.json tee"
```

---

### Task 8: tend — delegation guard + config + hook routing

**Files:**
- Create: `/Users/varma/tend/tend/agentguard.py`
- Modify: `/Users/varma/tend/tend/config.py` (DEFAULTS, Config, `_coerce`), `/Users/varma/tend/tend/hook.py` (PreToolUse routing)
- Test: `/Users/varma/tend/tests/test_agentguard.py` (new), `/Users/varma/tend/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agentguard.py`:

```python
from conftest import make_event

from tend import agentguard, paths


def seed_model(name, sid="s1"):
    paths.write_json_atomic(paths.session_dir(sid) / "ctx.json",
                            {"model": {"display_name": name}})


def spawn_event(**kw):
    base = dict(hook_event_name="PreToolUse", tool_name="Agent",
                tool_input={"prompt": "do a thing", "subagent_type": "Explore"})
    base.update(kw)
    return make_event(**base)


def test_nudges_when_model_absent():
    seed_model("Fable 5")
    out = agentguard.handle(spawn_event())
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "inherit fable" in ctx
    assert "lowest tier that fits" in ctx


def test_task_tool_name_also_guarded():
    seed_model("Opus 4.8")
    ctx = agentguard.handle(spawn_event(tool_name="Task"))["hookSpecificOutput"]["additionalContext"]
    assert "inherit opus" in ctx


def test_silent_when_model_set():
    seed_model("Fable 5")
    ev = spawn_event(tool_input={"prompt": "x", "model": "haiku"})
    assert agentguard.handle(ev) is None


def test_silent_for_other_tools():
    assert agentguard.handle(spawn_event(tool_name="Read")) is None


def test_silent_when_session_is_haiku():
    seed_model("Haiku 4.5")
    assert agentguard.handle(spawn_event()) is None


def test_generic_wording_when_model_unknown():
    ctx = agentguard.handle(spawn_event())["hookSpecificOutput"]["additionalContext"]
    assert "the session model" in ctx


def test_config_toggle_disables(tend_home):
    seed_model("Fable 5")
    tend_home.mkdir(parents=True, exist_ok=True)
    (tend_home / "config.yaml").write_text("delegation_guard: false\n")
    assert agentguard.handle(spawn_event()) is None


def test_dispatched_from_hook(tend_home):
    from tend import hook

    seed_model("Fable 5")
    out = hook.dispatch(spawn_event())
    assert "additionalContext" in out["hookSpecificOutput"]
```

Append to `tests/test_config.py`:

```python
def test_delegation_guard_default_and_bool_validation(tend_home):
    assert config.load().delegation_guard is True
    tend_home.mkdir(parents=True, exist_ok=True)
    (tend_home / "config.yaml").write_text("delegation_guard: 42\n")  # not a bool
    assert config.load().delegation_guard is True
    (tend_home / "config.yaml").write_text("delegation_guard: false\n")
    assert config.load().delegation_guard is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_agentguard.py tests/test_config.py -q` — Expected: FAIL (no module `agentguard`, no Config field).

- [ ] **Step 3: Implement**

Create `tend/agentguard.py`:

```python
"""Pillar 1c: advisory model-tier nudge for subagent spawns. Never blocks."""
from . import config, ctxmetrics

SPAWN_TOOLS = {"Task", "Agent"}

LADDER_TEXT = (
    "Pick the lowest tier that fits: haiku = mechanical (verify outputs/extract/"
    "format/capture); sonnet = clear-goal bounded work (scan/review/simple edits); "
    "opus = real coding; inherit = design/synthesis/judgment."
)


def handle(event):
    if event.get("tool_name") not in SPAWN_TOOLS:
        return None
    cfg = config.load(event.get("cwd"))
    if not cfg.delegation_guard:
        return None
    if (event.get("tool_input") or {}).get("model"):
        return None
    tier = ctxmetrics.session_model_tier(event.get("session_id"))
    if tier == "haiku":
        return None  # already the floor; nothing to save
    inherit = tier or "the session model"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"[tend] This subagent has no model set - it will inherit {inherit}. "
                + LADDER_TEXT
            ),
        }
    }
```

`tend/config.py`: add `"delegation_guard": True,` to `DEFAULTS` (after `urge_pct`), add `delegation_guard: bool` to the `Config` dataclass, and in `_coerce` insert ABOVE the existing `if isinstance(value, bool): return None` line:

```python
    if isinstance(DEFAULTS[key], bool):
        return value if isinstance(value, bool) else None
```

`tend/hook.py`: in `dispatch`, import `agentguard` in the lazy import line and route PreToolUse through both guards:

```python
    from . import agentguard, anchor, boundary, ledger, offload, precompact, readguard, sessionstart
```

```python
    handlers = {
        "PostToolUse": offload.handle,
        "PreToolUse": lambda e: readguard.handle(e) or agentguard.handle(e),
        "UserPromptSubmit": anchor.handle,
        "Stop": boundary.handle,
        "SessionStart": sessionstart.handle,
        "PreCompact": precompact.handle,
    }
```

- [ ] **Step 4: Run the full tend suite**

Run: `python3 -m pytest tests/ -q` — Expected: all pass (165+).

- [ ] **Step 5: Commit**

```bash
git add tend/agentguard.py tend/config.py tend/hook.py tests/test_agentguard.py tests/test_config.py
git commit -m "feat: advisory delegation guard - tier nudge for model-less subagent spawns"
```

---

### Task 9: tend — merge + live verification

- [ ] **Step 1: Merge**

```bash
cd /Users/varma/tend && git checkout master && git merge --no-ff feat/delegation-guard -m "Merge feat/delegation-guard: model-tier nudge for subagent spawns"
python3 -m pytest tests/ -q   # verify merged result; then:
git branch -d feat/delegation-guard
```

- [ ] **Step 2: Live smoke (editable install serves immediately)**

```bash
echo '{"hook_event_name":"PreToolUse","session_id":"smoke-v02","cwd":"/Users/varma/tend","tool_name":"Agent","tool_input":{"prompt":"scan files"}}' | python3 -m tend.hook
```

Expected: JSON with `additionalContext` containing "no model set" and "lowest tier that fits"; exit 0. Then confirm Read guard still routes:

```bash
echo '{"hook_event_name":"PreToolUse","session_id":"smoke-v02","cwd":"/Users/varma/tend","tool_name":"Read","tool_input":{"file_path":"/etc/hosts"}}' | python3 -m tend.hook; echo "exit=$?"
```

Expected: no output (small file), `exit=0`.

- [ ] **Step 3: Update `/Users/varma/tend/.claude/tend/STATE.md`**

Set `## Now` to: `Model tiering SHIPPED: swarm (executor defaults+fallback, validator, CLI, docs, agent tiers) and tend delegation guard both merged to master. Still pending: professional README + GitHub push for tend.` Add to `## Files touched`: `tend/agentguard.py (new), ctxmetrics.py, config.py, hook.py — delegation guard`. Commit:

```bash
git add .claude/tend/STATE.md && git commit -m "docs: record model-tiering ship state"
```
