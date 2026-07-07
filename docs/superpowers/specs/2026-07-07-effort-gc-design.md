# Effort tiers + run GC + audit-gate fixes — design

Date: 2026-07-07
Status: approved (user: "lets add effort tiers and gc ... and fix the blindspots")

## 1. Per-task effort tiers

Second token-economy lever alongside `model`. Optional per-task `"effort"`:
one of `low | medium | high | xhigh | max` (mirrors the workflow runtime's
accepted values). Omitted → inherit the session's effort (runtime default).

- `swarm_lib/graph.py`: unknown effort → error code `effort`.
- `swarm_lib/cli.py` args: per-task passthrough (`"effort": t.get("effort")`).
- `workflows/run_graph.mjs`: `EFFORTS` allow-list, `validateGraph` check,
  spawn opts gain `...(t.effort ? { effort: t.effort } : {})`. Effort is
  orthogonal to the model-fallback path (kept on fallback retries).
- Guidance (SKILL.md/graph-format.md): `low` for mechanical/haiku-grade
  tasks and bounded scans; omit for judgment/synthesis; no type defaults
  (YAGNI — omission is already the right default).

## 2. `swarm gc`

Reclaim old run dirs safely. `swarm gc [--days N] [--include-failed]
[--delete]`.

- Candidates: run dirs (must contain graph.json) across ALL project slugs
  under `$SWARM_HOME/runs/`, whose run-state status is terminal
  (`completed` or `abandoned`; plus `failed-partial` only with
  `--include-failed`), older than `--days` (default 14; age = now − max(dir
  mtime, run-state ts)), and not holding a fresh resume.lock.
- Never candidates: no run-state (interrupted), `paused_for_budget`, fresh
  lock — these are resumable.
- Default mode LISTS candidates and exits 0 (safe dry-run); `--delete`
  actually removes (rmtree) and reports each.
- Implementation: `runs.gc_candidates(days, include_failed)` +
  `cli.cmd_gc`.

## 3. Audit-gate blind-spot fixes (proved by the review-gate verifier)

1. **validate() crash**: `allowed_models: ["haiku", ["x"]]` raises
   `TypeError: unhashable type: 'list'` (dup-check runs `set(am)` before
   entry type-checking). Fix: all-strings check before the dup check; error
   code stays `allowed-models`.
2. **Ambiguous premium wording**: `references/graph-format.md` says
   `premium = +fable`; README/SKILL say haiku|sonnet|opus|fable. Make the
   reference explicit: `premium = haiku|sonnet|opus|fable`.
3. **Plugin artifacts have zero test coverage**: new
   `tests/test_plugin_artifacts.py` — manifests parse as JSON with the
   fields the README instructions rely on; hooks.json declares SubagentStop
   + SessionStart with non-empty command strings and references the same
   hook module as `install.hook_command()`; `bash -n bin/swarm` passes.

## Out of scope

Effort type-defaults; gc archiving (delete-only with safe default); any
change to scheduler/resume/locking semantics.
