# Per-Run Model Ladders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each swarm run declare which model ladder it uses — `economy` (haiku/sonnet/opus, default), `duo` (sonnet/opus), or `premium` (+fable) — stored and enforced as `allowed_models` in graph.json, clamped by the executor.

**Architecture:** `allowed_models` is an optional graph-level list validated by `swarm_lib/graph.py`, folded into the content hash when present (omitted → legacy hash, so old runs resume untouched), passed through by `swarm args`, and honored by `workflows/run_graph.mjs` which clamps type-based model defaults into the allowed set before applying the existing session-tier cap. Explicit per-task models must be inside the set (validation error otherwise) and are never clamped or capped at runtime.

**Tech Stack:** Python 3.11 (stdlib only), Node ESM (`node:test`), pytest.

## Global Constraints

- Known models, in ladder order: `haiku, sonnet, opus, fable` (`MODELS` in graph.py, `LADDER` in run_graph.mjs). `fable` stays known — opt-in, not removed.
- Named ladders: `economy` = `["haiku","sonnet","opus"]` (default), `duo` = `["sonnet","opus"]`, `premium` = `["haiku","sonnet","opus","fable"]`.
- Omitted `allowed_models` → all four allowed AND the graph hash must be byte-identical to pre-change hashes (backward compat is a hard requirement).
- Session-tier cap on defaults is applied AFTER ladder clamping and wins (a run never costs more than the launching session's tier; running cheaper than the ladder floor is acceptable).
- The failure-fallback path (final retry on session model, recorded in `fallbacks`) is unchanged and ignores the ladder — the session model is definitionally available.
- No new dependencies. Tests: `python3 -m pytest -q` runs both suites (pytest drives `node --test`).
- All commits authored solely by the repo's configured git user (varmabudharaju) — no co-author trailers.

## File Structure

- Modify: `swarm_lib/graph.py` — hash + validation (single responsibility: graph rules).
- Modify: `swarm_lib/cli.py` — one-line passthrough in `cmd_args`.
- Modify: `workflows/run_graph.mjs` — `clampToLadder()` helper, `effectiveModel()` third param, `validateGraph()` policy check, `runGraph()` wiring.
- Modify: `tests/test_graph.py`, `tests/test_cli.py`, `tests/node/executor.test.mjs`.
- Modify: `skills/swarm/SKILL.md`, `skills/swarm/references/graph-format.md`, `README.md`.

---

### Task 1: Python — `allowed_models` validation + hash coverage

**Files:**
- Modify: `swarm_lib/graph.py` (compute_hash at :16-18; validate at :45-46 area)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: existing `MODELS` set, `err()` closure inside `validate`.
- Produces: `compute_hash(graph)` folds `allowed_models` into the blob **only when the key is present and not None**; `validate(graph)` emits error codes `allowed-models` (malformed list) and `model-policy` (task model outside the set). Task 2 and callers rely on `graph.get("allowed_models")` staying a plain list or None.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph.py`:

```python
def test_allowed_models_valid_subset_ok():
    gr = g([task("a", model="sonnet")], allowed_models=["sonnet", "opus"])
    assert graph.errors(graph.validate(gr)) == []


def test_allowed_models_rejects_unknown_empty_nonlist_dupes():
    for bad, why in (
        (["sonnet", "gpt5"], "unknown"),
        ([], "empty"),
        ("sonnet", "non-list"),
        (["sonnet", "sonnet"], "duplicate"),
    ):
        gr = g([task("a")], allowed_models=bad)
        assert "allowed-models" in codes(graph.errors(graph.validate(gr))), why


def test_model_policy_task_model_must_be_in_allowed():
    gr = g([task("a", model="fable")], allowed_models=["sonnet", "opus"])
    assert "model-policy" in codes(graph.errors(graph.validate(gr)))
    # in-set explicit model is fine
    ok = g([task("a", model="opus")], allowed_models=["sonnet", "opus"])
    assert "model-policy" not in codes(graph.errors(graph.validate(ok)))


def test_model_policy_skipped_when_allowed_models_malformed():
    """A malformed allowed_models reports allowed-models, not a bogus model-policy."""
    gr = g([task("a", model="sonnet")], allowed_models="sonnet")
    cs = codes(graph.errors(graph.validate(gr)))
    assert "allowed-models" in cs and "model-policy" not in cs


def test_hash_unchanged_when_allowed_models_absent():
    """Backward compat: pre-ladder graphs must keep their existing hashes."""
    tasks = [task("a"), task("b", deps=["a"])]
    legacy = {"version": 1, "tasks": tasks}
    assert graph.compute_hash(legacy) == graph.compute_hash({"version": 1, "tasks": tasks, "other": 1})


def test_hash_covers_allowed_models_when_present():
    tasks = [task("a")]
    h_economy = graph.compute_hash({"tasks": tasks, "allowed_models": ["haiku", "sonnet", "opus"]})
    h_duo = graph.compute_hash({"tasks": tasks, "allowed_models": ["sonnet", "opus"]})
    h_legacy = graph.compute_hash({"tasks": tasks})
    assert h_economy != h_duo
    assert h_economy != h_legacy
    # and validate() catches post-hoc edits to allowed_models
    gr = g([task("a")], allowed_models=["sonnet", "opus"])
    gr["allowed_models"] = ["haiku", "sonnet", "opus"]
    assert "hash" in codes(graph.errors(graph.validate(gr)))
```

(The `g()` helper at tests/test_graph.py:6-10 already merges `**kw` into the graph dict and computes the hash after, so `allowed_models=` flows in and gets hashed correctly.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_graph.py -q -k "allowed or policy or hash_"`
Expected: the new tests FAIL (hash tests fail because compute_hash ignores allowed_models; validation tests fail because no such codes exist yet). Pre-existing tests still pass.

- [ ] **Step 3: Implement**

In `swarm_lib/graph.py`, replace `compute_hash`:

```python
def compute_hash(graph) -> str:
    payload = graph.get("tasks", [])
    if graph.get("allowed_models") is not None:
        # Fold the model policy into the hash so editing it after results
        # exist is caught. Absent policy keeps the legacy tasks-only blob,
        # so pre-ladder runs keep their hashes and stay resumable.
        payload = {"allowed_models": graph["allowed_models"], "tasks": payload}
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
```

In `validate()`, insert after `tasks = graph.get("tasks") or []` (graph.py:32):

```python
    am = graph.get("allowed_models")
    am_ok = am is None
    if am is not None:
        if not isinstance(am, list) or not am:
            err("allowed-models", "allowed_models must be a non-empty list")
        elif len(set(am)) != len(am):
            err("allowed-models", "allowed_models contains duplicates")
        elif any(m not in MODELS for m in am):
            bad = [m for m in am if m not in MODELS]
            err("allowed-models",
                f"unknown model(s) in allowed_models: {', '.join(map(str, bad))} "
                f"(use haiku|sonnet|opus|fable)")
        else:
            am_ok = True
```

And inside the per-task loop, right after the existing unknown-model check (graph.py:45-46):

```python
        if (t.get("model") in MODELS and am_ok and am is not None
                and t["model"] not in am):
            err("model-policy",
                f"{tid}: model {t['model']} not in allowed_models {am}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_graph.py -q`
Expected: all pass (including every pre-existing hash/model test — proves backward compat).

- [ ] **Step 5: Commit**

```bash
git add swarm_lib/graph.py tests/test_graph.py
git commit -m "feat(graph): validate and hash per-run allowed_models policy"
```

---

### Task 2: CLI — pass `allowed_models` through `swarm args`

**Files:**
- Modify: `swarm_lib/cli.py:71-86` (the `out` dict in `cmd_args`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `gr` (parsed graph.json) in `cmd_args`.
- Produces: workflow args JSON gains top-level `"allowed_models": <list|null>`. Task 3's `runGraph` reads `argsObj.allowed_models`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_args_passes_allowed_models_through(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli, graph as g, paths

    rd = make_run(tmp_path, tasks=[task("a", model="sonnet")])
    gr = paths.read_json(rd / "graph.json")
    gr["allowed_models"] = ["sonnet", "opus"]
    gr["graph_hash"] = g.compute_hash(gr)
    paths.write_json_atomic(rd / "graph.json", gr)
    assert cli.main(["args", str(rd / "graph.json"), "--session-model", "opus"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["allowed_models"] == ["sonnet", "opus"]


def test_args_allowed_models_null_when_absent(tmp_path, swarm_home, capsys):
    import json
    from conftest import make_run, task
    from swarm_lib import cli

    rd = make_run(tmp_path, tasks=[task("a")])
    assert cli.main(["args", str(rd / "graph.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["allowed_models"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cli.py -q -k allowed`
Expected: FAIL with `KeyError: 'allowed_models'`.

- [ ] **Step 3: Implement**

In `swarm_lib/cli.py`, in the `out = {` dict (after `"session_model": a.session_model,` at :76), add:

```python
        "allowed_models": gr.get("allowed_models"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add swarm_lib/cli.py tests/test_cli.py
git commit -m "feat(cli): swarm args emits allowed_models for the executor"
```

---

### Task 3: Executor — clamp defaults into the ladder, enforce policy

**Files:**
- Modify: `workflows/run_graph.mjs` (:15-22 `effectiveModel`, :39-41 model check in `validateGraph`, :100 call site in `runGraph`)
- Test: `tests/node/executor.test.mjs`

**Interfaces:**
- Consumes: `argsObj.allowed_models` (list or null/undefined) from Task 2's args JSON.
- Produces: `clampToLadder(model, allowed) -> model` (exported), `effectiveModel(t, sessionModel, allowedModels) -> string|null` (third param optional; omitted = all allowed), `validateGraph(tasks, completed, allowedModels)` (third param optional).

- [ ] **Step 1: Write the failing tests**

Append to `tests/node/executor.test.mjs` (imports at :3 gain `clampToLadder`):

```js
test('clampToLadder: in-set and null pass through; below rides up; above rides down', () => {
  assert.equal(clampToLadder('sonnet', ['sonnet', 'opus']), 'sonnet')
  assert.equal(clampToLadder(null, ['sonnet', 'opus']), null)          // inherit untouched
  assert.equal(clampToLadder('haiku', ['sonnet', 'opus']), 'sonnet')   // duo: nearest above
  assert.equal(clampToLadder('fable', ['haiku', 'sonnet', 'opus']), 'opus') // economy: nearest below
  assert.equal(clampToLadder('sonnet', ['haiku', 'opus']), 'opus')     // gap set: above wins first
  assert.equal(clampToLadder('opus', null), 'opus')                    // no policy: untouched
  assert.equal(clampToLadder('opus', []), 'opus')                      // empty treated as no policy
})

test('effectiveModel: ladder clamps defaults, then session cap still wins', () => {
  // duo ladder: research default sonnet is in-set; implement default opus in-set
  assert.equal(effectiveModel(T('a'), null, ['sonnet', 'opus']), 'sonnet')
  // ladder floors a haiku-grade default up (none of TYPE_MODEL is haiku today,
  // but explicit-null synthesize must stay inherit regardless of ladder)
  assert.equal(effectiveModel(T('a', [], { type: 'synthesize' }), 'opus', ['sonnet', 'opus']), null)
  // session cap applies AFTER clamping and wins even below the ladder floor
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), 'sonnet', ['sonnet', 'opus']), 'sonnet')
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), 'haiku', ['sonnet', 'opus']), 'haiku')
  // explicit model is never clamped at runtime (validation owns that contract)
  assert.equal(effectiveModel(T('a', [], { model: 'fable' }), 'opus', ['sonnet', 'opus']), 'fable')
})

test('validateGraph rejects task models outside allowed_models', () => {
  const errs = validateGraph([T('a', [], { model: 'fable' })], {}, ['sonnet', 'opus'])
  assert.ok(errs.some(e => e.includes('not in allowed_models')))
  assert.deepEqual(validateGraph([T('a', [], { model: 'opus' })], {}, ['sonnet', 'opus']), [])
  assert.deepEqual(validateGraph([T('a', [], { model: 'fable' })], {}), []) // no policy: legal
})

test('runGraph threads allowed_models into spawn models', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('r'), T('i', [], { type: 'implement' })], {},
    { allowed_models: ['sonnet', 'opus'] }), a.fn, null, null)
  const byLabel = Object.fromEntries(a.calls.map(c => [c.opts.label, c.opts]))
  assert.equal(byLabel['research:r'].model, 'sonnet')
  assert.equal(byLabel['implement:i'].model, 'opus')
})

test('runGraph refuses a graph whose task model violates the run policy', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a', [], { model: 'fable' })], {},
    { allowed_models: ['sonnet', 'opus'] }), a.fn, null, null)
  assert.ok(out.fatal.some(e => e.includes('not in allowed_models')))
  assert.equal(a.calls.length, 0)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/node/`
Expected: FAIL — `clampToLadder` is not exported; policy tests fail.

- [ ] **Step 3: Implement**

In `workflows/run_graph.mjs`, after `TYPE_MODEL` (:13), add and rewrite:

```js
// Clamp a type-default model into the run's allowed ladder: nearest allowed
// tier above on the universal LADDER, else nearest below. Explicit per-task
// models never pass through here — validation enforces their membership.
export function clampToLadder(m, allowed) {
  if (!m || !allowed || !allowed.length || allowed.includes(m)) return m
  const i = LADDER.indexOf(m)
  for (let j = i + 1; j < LADDER.length; j++) if (allowed.includes(LADDER[j])) return LADDER[j]
  for (let j = i - 1; j >= 0; j--) if (allowed.includes(LADDER[j])) return LADDER[j]
  return m
}

export function effectiveModel(t, sessionModel, allowedModels) {
  if (t.model) return t.model // planner's explicit choice always wins
  let m = clampToLadder(TYPE_MODEL[t.type] ?? null, allowedModels)
  if (m && LADDER.includes(sessionModel)) {
    // Session cap applies after the ladder and wins: a run may go cheaper
    // than its ladder floor, never dearer than the launching session.
    m = LADDER[Math.min(LADDER.indexOf(m), LADDER.indexOf(sessionModel))]
  }
  return m
}
```

In `validateGraph(tasks, completed)` → `validateGraph(tasks, completed, allowedModels)`; extend the model check at :39-41:

```js
  for (const t of tasks) {
    if (t.model && !LADDER.includes(t.model)) errors.push(`${t.id}: unknown model ${t.model}`)
    else if (t.model && allowedModels && allowedModels.length && !allowedModels.includes(t.model)) {
      errors.push(`${t.id}: model ${t.model} not in allowed_models [${allowedModels.join(', ')}]`)
    }
  }
```

In `runGraph` (:85 and :100):

```js
  const fatal = validateGraph(tasks, argsObj.completed || {}, argsObj.allowed_models)
  ...
    const intended = effectiveModel(t, argsObj.session_model, argsObj.allowed_models)
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q` (drives node suite too)
Expected: all pass, including every pre-existing effectiveModel/cap/fallback test unchanged.

- [ ] **Step 5: Commit**

```bash
git add workflows/run_graph.mjs tests/node/executor.test.mjs
git commit -m "feat(executor): clamp model defaults into the run's allowed ladder"
```

---

### Task 4: Skill + docs — ladder question, format reference, README

**Files:**
- Modify: `skills/swarm/SKILL.md` (Process steps 2-3 and step 7)
- Modify: `skills/swarm/references/graph-format.md` (example JSON + "Model tiers" section)
- Modify: `README.md` ("Right-sized brains" section :75-114, fallback example :101)

**Interfaces:**
- Consumes: field name `allowed_models`, ladder names economy/duo/premium from Global Constraints.
- Produces: user-facing contract; no code.

- [ ] **Step 1: SKILL.md — insert a ladder step and update tier guidance**

Insert a new step 3 after "Check headroom" in the Process list (renumber the rest):

```markdown
3. **Choose the ladder**: ask the user ONE question (AskUserQuestion) - which
   model ladder for this run - unless they already said so in the goal:
   - `economy` (default): haiku, sonnet, opus - opus tops judgment/synthesis
   - `duo`: sonnet, opus only
   - `premium`: haiku, sonnet, opus, fable (only if their plan has fable)
   Set the chosen list as `allowed_models` in graph.json and assign every
   task's `model` from inside it. Judgment/synthesis tasks: omit `model`
   (inherit the session - run your main session on opus with thinking).
```

In the (now) decompose step, change `real coding -> opus; judgment/synthesis -> omit (inherit)` guidance to reference "the top of the chosen ladder" and in step 7 change `(haiku|sonnet|opus|fable - you know your own model)` to `(haiku|sonnet|opus|fable)` — unchanged set, since session model is about the running session, not the ladder.

- [ ] **Step 2: graph-format.md — document the field**

Add `"allowed_models": ["haiku", "sonnet", "opus"],` to the example JSON (after `"agent_ceiling": null,`) and replace the "Model tiers" section's opening with:

```markdown
## Model tiers

Graph-level `allowed_models` (optional) is the run's model policy, chosen by
the user at launch: `economy` = haiku|sonnet|opus (default), `duo` =
sonnet|opus, `premium` = +fable. It is folded into graph_hash, enforced by
validation (every explicit task model must be inside it), and the executor
clamps type-defaults into it (nearest allowed tier above, else below).
Omitted -> all four models allowed (pre-ladder graphs keep their hashes).

Per-task `model`: one of `haiku | sonnet | opus | fable` (tier aliases only -
versions are not addressable), drawn from `allowed_models`. Lowest tier that
fits:

- top of ladder/omit: decomposition-grade reasoning, ambiguous goals, synthesis
- `opus`: real coding (implement/integrate, debugging, refactors)
- `sonnet`: clear-goal bounded work (scans, diff review, adversarial verify)
- `haiku`: mechanical (output/schema checks, extraction, formatting, capture)
```

Keep the existing safety-net-defaults paragraph, appending: "Defaults are clamped into `allowed_models` before the session cap; the session cap wins."

- [ ] **Step 3: README — reframe tiering around ladders**

In "Right-sized brains (model tiering)": lead with the three ladders table (economy default / duo / premium), state the foreman asks once per run, recommend running the main session on Opus with extended thinking, and change the fallback example `design-api: fable->inherit` to `design-api: opus->inherit`. Update the mermaid decision tree's final node from "inherit the session model" framing only if it names fable (it doesn't — leave it). Replace the tier table's `top model (inherit)` row wording with `top of your ladder (inherit)`.

- [ ] **Step 4: Verify docs render + suite still green**

Run: `python3 -m pytest -q`
Expected: all pass. Skim the three files for contradictions with graph.py/run_graph.mjs behavior.

- [ ] **Step 5: Commit**

```bash
git add skills/swarm/SKILL.md skills/swarm/references/graph-format.md README.md
git commit -m "docs: per-run model ladders (economy/duo/premium) in skill + README"
```

---

### Task 5: Ship — push branches, stacked PRs

**Files:** none (git/GitHub only)

- [ ] **Step 1: Full suite + validation smoke test**

```bash
python3 -m pytest -q
```
Expected: all pass. Then a real end-to-end smoke: author a tiny graph with `allowed_models: ["sonnet","opus"]` and a `fable` task in the scratchpad, confirm `swarm validate` rejects it with `model-policy`, fix the model to `opus`, confirm it passes and `swarm args` emits the field.

- [ ] **Step 2: Push both branches**

```bash
git push -u origin feat/plugin-packaging
git push -u origin feat/model-ladders
```

- [ ] **Step 3: Stacked PRs**

PR 1: `feat/plugin-packaging` → `master` (plugin packaging + ladders spec doc).
PR 2: `feat/model-ladders` → `feat/plugin-packaging` (this feature), with a detailed description: problem, ladder table, enforcement layers (hash/validate/clamp), edge cases covered, test evidence.

---

## Edge-Case Register (all covered by tests above)

| # | Edge case | Covered in |
|---|---|---|
| 1 | `allowed_models` omitted → permissive + hash byte-identical to legacy | Task 1 |
| 2 | Empty list / non-list / duplicates / unknown model in list → `allowed-models` error | Task 1 |
| 3 | Explicit task model outside the set → `model-policy` error (Python) + fatal (executor) | Tasks 1, 3 |
| 4 | Malformed list must not cascade into bogus `model-policy` errors | Task 1 |
| 5 | Post-hoc edit of `allowed_models` after hashing → hash mismatch, resume refused | Task 1 |
| 6 | Clamp below-ladder default up; above-ladder default down; gap sets prefer above | Task 3 |
| 7 | `null` (synthesize/inherit) passes through clamp untouched | Task 3 |
| 8 | Session cap applies after clamp and wins, even below the ladder floor | Task 3 |
| 9 | Explicit model never clamped/capped at runtime | Task 3 |
| 10 | Failure fallback to session model ignores ladder (unchanged behavior) | existing test at executor.test.mjs:222 |
| 11 | `swarm args` emits `null` when field absent (old graphs resume) | Task 2 |
