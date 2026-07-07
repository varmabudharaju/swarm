# Per-run model ladders — design

Date: 2026-07-07
Status: approved

## Problem

swarm's model tiering assumes a four-tier ladder ending in `fable`
(`haiku → sonnet → opus → fable`), with fable as the implicit top tier for
judgment/synthesis work. Fable will not be available going forward for most
users, and different users want different cost/quality trade-offs. The ladder
must become a per-run choice — defaulting to an Opus-topped economy ladder —
while keeping fable available as an opt-in for users who still have it.

The orchestration itself (LLM-as-judge decomposition + adversarial review
gate, parallel DAG execution, checkpointing, resume, worktree quarantine)
already works and is out of scope. This is a re-basing of the tier system,
not a rebuild.

## Design

### Named ladders

| Ladder | Models | Top tier (judgment/synthesis) |
|---|---|---|
| `economy` (default) | haiku, sonnet, opus | opus |
| `duo` | sonnet, opus | opus |
| `premium` | haiku, sonnet, opus, fable | fable |

At `/swarm <goal>` time, the foreman asks the user one question — which
ladder to use for this run — defaulting to `economy`. The main (foreman)
session is recommended to run on Opus with extended thinking; the existing
session-tier cap already prevents type-defaults from silently escalating
above the session's own tier.

### Where the policy lives: `allowed_models` in graph.json

The chosen ladder is written into `graph.json` as an explicit model list:

```json
"allowed_models": ["haiku", "sonnet", "opus"]
```

Rationale (alternatives considered):

- **Graph field (chosen)** — covered by the content hash (tamper-evident),
  survives resume in a different session, and is enforced by validation,
  not just suggested.
- Skill-only convention — no code change, but unenforced: a mistagged
  `fable` task in an economy run would silently launch, and resume has no
  record of the choice.
- Executor-arg only — not durable: a resume could run under a different
  policy than the original launch.

### Validation (`swarm_lib/graph.py`)

- `allowed_models`, when present, must be a non-empty list and a subset of
  the known models `{haiku, sonnet, opus, fable}`. `fable` stays in the
  known set — opt-in, not removed.
- Every task with an explicit `model` must have it in `allowed_models`.
- Omitted `allowed_models` → all four allowed (backward compatible; existing
  runs resume untouched).

### CLI (`swarm_lib/cli.py`)

`swarm args` passes `allowed_models` through into the workflow args JSON
(null when absent). `--session-model` keeps all four choices.

### Executor (`workflows/run_graph.mjs`)

`effectiveModel(task, sessionModel, allowedModels)`:

1. Explicit `t.model` wins unchanged (validation already guaranteed it is
   allowed).
2. Type-based default is clamped **into** the allowed ladder: if the default
   tier is not allowed, use the nearest allowed tier above it on the
   universal ladder (`duo`: a haiku-grade default rides up to sonnet); if
   none above, the nearest allowed below.
3. Session-tier cap applies as today (defaults never exceed the launching
   session's tier).
4. `null` (synthesize/inherit) stays null → session model.

`validateGraph` in the executor gains the same allowed-models checks as a
belt-and-braces layer. The loud failure-fallback path (final retry on the
session model, reported in `fallbacks`) is unchanged.

### Skill + docs

- `skills/swarm/SKILL.md`: new step before decomposition — ask the user
  which ladder (AskUserQuestion, default economy); tier-assignment guidance
  says "top of the chosen ladder" instead of naming fable.
- `skills/swarm/references/graph-format.md`: document `allowed_models`, the
  three ladder names, and updated tier guidance.
- `README.md`: tiering section reframed around the three ladders; note that
  the foreman session should run Opus with thinking; fallback example no
  longer assumes fable.

### Testing

- pytest: `allowed_models` validation (subset, non-empty, task-model
  membership, omitted-is-permissive), `swarm args` passthrough.
- node:test: `effectiveModel` clamping under each ladder × task type ×
  session model; executor `validateGraph` checks.
- Update the existing fable-referencing tests to the new expectations.

## Out of scope

- Per-task `effort` tuning (YAGNI for now).
- Any change to checkpointing, resume, locking, worktree isolation, or the
  scheduler loop.
- Settings-level persistent ladder preference (per-run ask only, for now).
