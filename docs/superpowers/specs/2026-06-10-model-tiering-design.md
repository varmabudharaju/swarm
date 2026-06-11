# Cost-aware model tiering — design

Date: 2026-06-10
Scope: swarm (primary) + tend (delegation guard)
Status: approved in brainstorming; awaiting implementation plan

## Motivation

Claude usage stats on this machine: 63% of usage comes from subagent-heavy
sessions, and every subagent spawned without an explicit model inherits the
session model — usually the premium tier (fable). Most swarm tasks do not need
it: coding tasks run fine on opus, bounded scans and adversarial verification
on sonnet, mechanical output checks on haiku. The orchestration layer should
decide the tier per task; sessions outside swarm runs should get the same
rubric as an advisory nudge from tend (installed in every session).

## The tier ladder (shared rubric)

Addressable tiers are exactly the Agent API's four aliases — version pinning
(e.g. "opus 4.7 vs 4.8") is NOT addressable; `opus` resolves to the current
Opus. Ladder order: `haiku < sonnet < opus < fable`.

| Tier | Right for |
|---|---|
| `fable` / inherit | Decomposition, architecture/design, ambiguous goals, final synthesis, judgment-heavy review |
| `opus` | Real coding: implement/integrate tasks, debugging, refactoring, multi-file changes |
| `sonnet` | Clear-goal bounded work: research/scans, diff review, adversarial verification, simple edits |
| `haiku` | Mechanical: output/schema verification, extraction, formatting, capture-style rote runs |

Rule: **choose the lowest tier that fits.** Escalate one tier when the goal is
murky, the packet is thin, or the task failed on the cheap tier before.

## Relativity principle

Nothing is hardcoded to "fable". "Inherit" always means *the session model*.
Defaults never auto-escalate above the session tier; only an explicit `model`
written in the graph may request a tier above it.

## swarm changes

### 1. Graph format (`references/graph-format.md`)

Tasks gain an optional field:

```json
"model": "haiku" | "sonnet" | "opus" | "fable"   // optional
```

Omitted → executor applies type defaults (below). The field is part of the
graph like any other (covered by `graph_hash`); old graphs without it remain
valid.

### 2. Validator (`swarm_lib/graph.py`)

- Error: `model` present but not one of the four values.
- No warning tier for model choices — the planner's judgment is trusted;
  defaults make forgetting safe.

### 3. CLI (`swarm_lib/cli.py`)

`swarm args` accepts `--session-model <tier>` (validated against the same
four values). The skill instructs the planning session to pass its own tier
(Claude knows its model from its system prompt). The value is included in the
emitted args JSON as `session_model`. Omitted → no capping (defaults used
as written).

### 4. Executor (`workflows/run_graph.mjs`)

```js
const LADDER = ['haiku', 'sonnet', 'opus', 'fable']
const TYPE_MODEL = {
  research: 'sonnet', review: 'sonnet', verify: 'sonnet',
  implement: 'opus',  integrate: 'opus',
  synthesize: null,   // inherit session model
}
```

Effective model for a task:
1. Explicit `t.model` → use as written (may exceed session tier — deliberate).
2. Else `TYPE_MODEL[t.type]`; if `session_model` is known, cap at it
   (min by ladder index). `null` → omit the option entirely (inherit).

Passed to the agent call as `...(m ? { model: m } : {})` alongside the
existing `agentType`/`isolation` options.

### 5. Failure-driven fallback (availability adaptation)

There is no reliable way to pre-query tier availability from a workflow, so
adaptation happens on failure: in the retry loop, the **final** retry of a
task that was running with a model override drops the override and inherits
the session model (which is by definition being served). Every fallback is:

- logged mid-run: `swarm: <id>: model '<tier>' unavailable or failing — fell
  back to session model`
- recorded in the executor's return value in a `fallbacks` map
  (`{taskId: "<tier>->inherit"}`) — NOT inside the task's schema-validated
  result — so the finish report names every task that did not run on its
  intended tier.

If the inherit retry also fails, the task fails exactly as today and the
report says so. Side effect (desirable): a task that failed twice on a cheap
tier gets one attempt on the session model before being declared failed —
self-healing for "haiku wasn't enough" mistakes.

### 6. Skill guidance (`skill/SKILL.md`)

- Decompose step gets the ladder table + lowest-tier-that-fits rule +
  downgrade guidance (mechanical verifies → haiku) + escalate guidance
  (murky goal → one tier up, never above session tier via defaults).
- Launch step: `ARGS=$(swarm args <run-dir>/graph.json --session-model <tier>)`.
- Finish step: surface the `fallbacks` map to the user.

### 7. Agent definitions (`agents/*.md`)

Frontmatter defaults for ad-hoc spawns outside runs (executor/graph options
take precedence when both exist):

- `swarm-reader`: `model: sonnet`
- `swarm-verifier`: `model: sonnet`
- `swarm-implementer`: `model: opus`

## tend changes — advisory delegation guard

New PreToolUse behavior beside the existing read guard, same philosophy:
nudge, never block, fail-open.

- Trigger: `tool_name` is `Task` or `Agent` (both spellings supported) AND
  `tool_input` has no `model` set.
- Session model: read from the existing `ctx.json` statusline tee
  (`model.display_name`, matched case-insensitively to a tier). Unknown
  display names → generic wording ("the session model"). Session tier
  `haiku` → stay silent (nothing to save).
- Injected text (additionalContext, ~4 lines):

  > [tend] This subagent has no model set — it will inherit <session model>.
  > Pick the lowest tier that fits: haiku = mechanical
  > (verify outputs / extract / format / capture); sonnet = clear-goal bounded
  > work (scan / review / simple edits); opus = real coding;
  > inherit = design / synthesis / judgment.

- Config: `delegation_guard: true` in tend's DEFAULTS (off switch in
  config.yaml); respects the global `tend off` kill switch like every hook.

## Testing

- swarm validator (pytest): model allow-list accept/reject; `--session-model`
  validation.
- swarm executor (plain Node, existing harness): defaults-by-type matrix;
  explicit override wins; synthesize omits the option; session-model capping;
  final-retry fallback drops the override and reports in `fallbacks`.
- tend guard (pytest, mirrors readguard suite): nudges when model absent;
  silent when model present, when tool is not a spawn, when session tier is
  haiku, and when `delegation_guard: false`.

## Out of scope

- Model **version** pinning (API exposes tier aliases only).
- Hard enforcement/blocking of model choices (violates tend's fail-open,
  never-block philosophy; swarm trusts explicit graph values).
- Live availability querying (no API surface from hooks/workflows).
- Per-task cost accounting/reporting (future work; tend's ledger could grow
  per-agent attribution later).
