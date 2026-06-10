# swarm — Graph-First Multi-Agent Orchestrator for Claude Code

**Date:** 2026-06-10
**Status:** Approved design (user delegated review; adversarially critiqued by a design-review agent, verdict "build with fixes" — all fixes incorporated below)
**Repo:** `/Users/varma/swarm` · Python package `swarm_lib`, CLI `swarm` (PyPI name `ctx-swarm`) · plus a skill, a workflow script, and agent definitions installed into `~/.claude/`
**Sibling:** [tend](/Users/varma/tend) — context-hygiene harness (built, installed). swarm reuses its install/hook patterns but installs its OWN hooks; the projects stay decoupled.

## Problem

Multi-agent runs today use 3-4 agents because that is how workflows get *authored*,
not because of engine limits (the Workflow engine allows min(16, cores-2)
concurrent agents, 1000 per run). There is no persistent plan, no real dependency
scheduling (phases act as barriers), no enforced context transfer, and a run that
dies with the session — rate limits included — is gone.

swarm fixes authoring and adds the missing run substrate: a persistent typed task
graph, a generic DAG executor that runs every unblocked task immediately, durable
hook-written checkpoints, and cross-session resume that always asks first.

## Verified platform facts the design rests on

| Fact | Verified how |
|---|---|
| Workflow scripts: plain JS, NO filesystem/Node APIs/Date.now; `agent()`, `parallel`, `pipeline`, `log`, `phase`, `args`, `budget`; 16-concurrency cap; `agent()` → null on skip/terminal error; meta literal required | Workflow tool contract |
| Subagents cannot spawn subagents; do not inherit parent conversation; have real tools per agent-definition allowlist; `isolation:'worktree'` per agent | Platform docs (researched 2026-06-09) |
| Native workflow resume is same-session only | Workflow tool contract |
| **SubagentStop hook payload includes `agent_id`, `agent_type`, `agent_transcript_path`, and `last_assistant_message`** | Ground-truthed in this machine's agent-pd audit logs (2026-06-10) |
| Multiple hooks per event coexist in settings.json (tend + agent-pd run side by side today) | Live settings.json |
| tend tees exact context % AND `rate_limits` (five_hour/seven_day `used_percentage`, `resets_at`) per session to `~/.claude/tend/sessions/<sid>/ctx.json` | Built and verified in project 1 |

## Architecture

```
swarm/  (repo)                          installs to:
  skill/SKILL.md + references/      →   ~/.claude/skills/swarm/        the brain (authoring + resume protocol)
  workflows/swarm-run.js            →   ~/.claude/workflows/           the engine (pure DAG scheduler)
  agents/swarm-reader.md
         swarm-verifier.md
         swarm-implementer.md       →   ~/.claude/agents/              least-privilege workers
  swarm_lib/ (Python)               →   `swarm` CLI + swarm hooks      validate/status/abandon/install + checkpoint & nag hooks

run state (OUTSIDE any repo — survives worktrees, no merge conflicts):
  ~/.claude/swarm/runs/<project-slug>/<run-id>/
    graph.json          canonical graph (content-hashed)
    packets/<id>.md     context packet per task (referenced by absolute path)
    results/<id>.json   hook-written checkpoint per finished task (versioned schema)
    run-state.json      status: completed|paused_for_budget|failed-partial|abandoned
                        (absence of this file = run was interrupted)
    resume.lock         concurrent-resume guard
```

`<project-slug>` = sanitized absolute project path (tend-style `-Users-varma-foo`).

## Data flow (two channels, kept consistent by construction)

- **In-flight:** `agent()` returns a schema-validated object — the scheduler feeds
  capped summaries to dependents. The script never touches disk.
- **Durable:** a **SubagentStop hook** (`python3 -m swarm_lib.hook`) writes
  `results/<id>.json` deterministically — never by agent volition. Every task
  prompt begins with a marker line:
  `SWARM-TASK run=<abs-run-dir> task=<id> hash=<graph-hash>`.
  The hook reads the payload's `agent_transcript_path`, finds the marker in the
  first user message, extracts the final structured output (StructuredOutput tool
  input if present, else `last_assistant_message` text), and atomically writes
  `{version: 1, task, hash, status, output, ts}`. Checkpoint coverage therefore
  equals "agent finished" exactly — no divergence window, no Write tool needed for
  readers, and `swarm status` works mid-run from another terminal.

## Task graph (graph.json, version 1)

```json
{
  "version": 1, "run_id": "...", "goal": "...", "graph_hash": "<sha256 of tasks>",
  "project": "/abs/project/path", "budget_tokens": null, "agent_ceiling": null,
  "tasks": [{
    "id": "t1", "title": "...",
    "type": "research|review|implement|verify|integrate|synthesize",
    "prompt": "...",                          // task instructions
    "packet": "packets/t1.md",                // context packet, read by agent via absolute path
    "deps": [], "agent_type": "swarm-reader|swarm-verifier|swarm-implementer|general-purpose",
    "isolation": null,                        // "worktree" for implement/integrate
    "schema": { "...": "every schema includes summary: {type: string, maxLength: 2000}" },
    "max_retries": 1
  }]
}
```

Args passed to the workflow carry task metadata only (ids/deps/titles/types/
agent params/absolute packet+results paths + graph_hash) — packet bodies stay on
disk; agents Read them. This keeps args far under the 512KB ceiling at any
realistic graph size. **`summary` maxLength in every schema is the enforcement
mechanism for "distilled"** — not an aspiration. Dependents receive dep summaries
inline plus absolute paths to full dep result files.

## Decomposition rules (enforced, not aspirational)

Skill-authored graphs must pass BOTH gates before any task launches:

1. **Mechanical validation** — run by `swarm validate` AND re-run inside
   `runGraph` itself (a skipped CLI step cannot bypass it): schema shape, cycle
   detection, dangling deps, **fan-in ≤ 8** (wide reductions must use trees:
   cluster → cluster-synthesize → final), barrier smell (a task depending on every
   task of a prior type with a near-empty prompt), granularity floor (warn when
   task count > 25 or median prompt+packet < 400 chars), verify-ratio ≤ 30%.
2. **Graph-review gate** — one cheap swarm-verifier agent receives goal + graph
   and returns `{verdict, issues[]}`; the skill fixes issues before launching.

Verify policy (cost-tiered, not blanket doubling): one verifier audits 4-6
sibling results (adversaries compare better with cross-context anyway);
per-task verify only for results that feed implement tasks.

Width targets the real cap (~16), not infinity — beyond the cap, width buys
queueing, not speed.

## DAG executor (`swarm-run.js`)

Core: pure function `runGraph(graph, completed, agentFn, log, budget)` — no
workflow-runtime dependencies, unit-tested under plain Node (v22 present) with a
mock agentFn, and liftable into an SDK app later by swapping agentFn.

Scheduler contract (each item has a named Node test):
- **Maximal scheduling:** launch every task whose deps ⊆ completed, immediately,
  concurrently; completion of a task triggers readiness re-scan.
- **`launched` set** prevents double-launch from re-scans.
- **Failure isolation:** 1 retry, then mark failed; **transitive skip** of all
  dependents; terminate when nothing is running AND nothing is launchable;
  always return full partial state `{completed, failed, skipped, paused}`.
- **null disambiguation:** after a null, check `budget.remaining()` — only retry
  when the null was not a budget/terminal skip.
- **Budget reservation:** reserve estimated per-agent cost at launch (reconcile on
  completion) so 16 in-flight agents cannot overshoot the floor; below floor →
  stop launching, return `paused_for_budget`.
- **Resume sanity:** a `completed` map whose ids/hash don't match the graph →
  refuse before launching anything.
- Per-task agent prompt = marker line + "Read your packet at <abs path>" +
  prompt + dep summaries (inline, capped) + dep result paths.
- Optional `args.agent_ceiling` caps total agents this run (rate-limit headroom).

## Implementation lanes (worktrees)

- Deterministic branches: `swarm/<run-id>/<task-id>`; implement-task result
  schema includes `{branch, worktree_path, files_touched[], commits[], summary}`.
- File-disjointness is linted on *declared scope* at validate time but treated as
  unenforceable in reality (new files, lockfiles, registries collide):
  **integrate's packet treats conflict resolution as its primary job**, with its
  own budget.
- **Integrate is quarantined:** runs with `isolation:'worktree'` on
  `swarm/<run-id>/integration`, merges implement branches in dependency order,
  runs the test suite, never touches the user's checkout. The final merge to the
  user's branch is a session-level, user-approved step after the workflow returns.
- **Resume policy for implement tasks is delete-and-restart, never continue:** a
  `swarm/<run>/<task>` branch with no result file is an orphan from a dead run —
  the skill deletes branch + worktree before re-running and tells the user
  "task X had partial commits; discarding."

## Resume protocol (cross-session, always asks first)

1. Durable state accrues per-task via the SubagentStop hook (above).
2. When the workflow returns, the session writes `run-state.json` with terminal
   `status` (`completed` / `paused_for_budget` / `failed-partial`). A run dir
   without it is interrupted.
3. **swarm's own SessionStart hook** (separate from tend's) scans
   `~/.claude/swarm/runs/<slug-of-cwd>/` — one cheap glob — and injects at most
   one line: "Interrupted swarm run <id> (N/M tasks done) — say '/swarm resume'."
   Edge handling: N/M = M/M → offers *finalize* (synthesis only), not restart;
   `paused_for_budget` says so; nags age out after 7 days; `swarm abandon <run>`
   silences one forever.
4. `/swarm resume`: skill takes `resume.lock` (session id + timestamp; stale
   override after 2h), reads results/, verifies `graph_hash` on every result file
   against graph.json (mismatch → refuse), rebuilds `completed`, cleans orphan
   implement branches, **asks the user** with a precise summary (done/pending/
   discarded-partials/estimated remaining cost), then re-invokes the executor
   with `{graph-args, completed}`. Completed tasks short-circuit instantly.
5. Rate-limit awareness: before any launch the skill reads the newest tend
   ctx.json; if `five_hour.used_percentage` > 85, it reports `resets_at` and
   offers to defer; otherwise it sets `agent_ceiling` from remaining headroom.

## Workers (least privilege; unattended-safe)

| Agent | Tools | Notes |
|---|---|---|
| `swarm-reader` | Read, Glob, Grep, WebFetch, WebSearch | no Write needed — checkpoints are hook-written |
| `swarm-verifier` | Read, Glob, Grep, Bash | adversarial refutation stance; runs tests |
| `swarm-implementer` | full file tools + Bash, `disallowedTools: ["Bash(git push*)", "Bash(curl*)", "Bash(wget*)", "WebFetch"]` | TDD; commits to its swarm branch with plain messages (NO attribution lines — hard rule); reports branch/files/commits in schema |

Unattended runs require an auto-accepting permission posture for implementers;
the skill states this requirement up front rather than hiding it. Audit trail is
agent-pd (already installed, hash-chained, records every PostToolUse per agent) —
swarm documents this rather than rebuilding it. Known overhead: every tool call
of 16 parallel agents fires the tend + agent-pd + swarm hook chain; measured
~25-50ms per hook process — acceptable, revisit before raising default width.

## CLI (`swarm`, Python — tend conventions: fail-open hooks, parse-or-refuse installs, atomic writes)

| Command | Does |
|---|---|
| `swarm validate <graph.json>` | full mechanical validation (same checks as engine-side) |
| `swarm status [run-id]` | per-task done/pending/failed from results/, mid-run capable |
| `swarm abandon <run-id>` | sets run-state status=abandoned (silences nag) |
| `swarm install` / `swarm uninstall` | idempotent copy of skill/workflow/agents + hook registration (SubagentStop, SessionStart) into settings.json, tend-style backup |

Cut from v1 (YAGNI, per critique): `swarm runs` (ls suffices), per-task model
overrides (agent-type frontmatter defaults suffice), migration/review shape
templates (research-sweep + implement-from-plan templates cover the mechanics).

## Error handling

- Hooks fail-open (tend pattern): checkpoint-hook crash never breaks a session or
  a run — the in-flight channel still completes the workflow; only durability of
  that one task is lost, and `swarm status` shows the gap.
- Executor never throws away completed work; every exit path returns state.
- Installer: parse-or-refuse on settings.json, refreshing backups, symlink-safe
  (port of tend's hardened installer).
- Result files: versioned (`version: 1`); readers refuse unknown versions.

## Testing

- **Node (executor):** mock-agentFn tests named for each scheduler contract
  item: max-parallelism, dependency order, no-double-launch, transitive skip +
  termination, null/budget disambiguation, budget reservation/pause, resume
  short-circuit, hash-mismatch refusal.
- **pytest (swarm_lib):** validate (cycles/fan-in/barrier/granularity/ratio),
  checkpoint hook against a fixture subagent transcript + real captured
  SubagentStop payload, status/abandon, installer merge/idempotency/refusal.
- **Live:** spike first (Plan task 1): confirm SubagentStop fires for Workflow
  agents with `agent_transcript_path` (already confirmed for Task-tool agents
  from audit logs). End-to-end demo: a review swarm over the tend repo,
  interrupted deliberately, resumed in a new session — with capture screenshots
  in docs/test-evidence.md.

## SDK lift path (explicitly preserved)

`runGraph` is pure; graph.json + results/ are engine-agnostic. A future SDK
orchestrator swaps agentFn for SDK query calls and replaces the hook-checkpoint
with direct writes — nothing else changes. Out of scope v1: >16 concurrency,
cross-machine runs, dashboards.
