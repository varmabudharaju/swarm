# swarm — test evidence

Date: 2026-06-10 · branch `build/v1` · 72 Python tests + 19 Node scheduler tests passing.

## Suites

- `python3 -m pytest -q` → 72 passed (paths, marker, extract, graph validation,
  runs/locks, hooks, installer, CLI, integration incl. subprocess entry points).
- `node --test 'tests/node/*.test.mjs'` → 19 passed (max-parallelism, dependency
  order, no-double-launch, transitive skip, throw containment, budget
  reservation + pause, null disambiguation, ceiling incl. 0, resume
  short-circuit, id charset, schema gate, sentinel identity).

## Live install (real `~/.claude`)

`swarm install` merged hooks non-destructively — SubagentStop: agent-pd + tend +
swarm; SessionStart: tend + swarm; statusline and all other settings untouched.
Skill, generated workflow, and 3 agent definitions landed in their managed
paths. The `swarm` skill and `swarm-run` workflow registered live mid-session;
custom agent *types* require a session restart (verified by probe; documented in
the skill).

## Task 0 spike (design-critical)

SubagentStop fires for workflow agents (`agent_type: workflow-subagent`) with
`agent_transcript_path` + `last_assistant_message` — verified in agent-pd audit
logs. Hook-written checkpointing is therefore sound for workflow workers.

## End-to-end demo: review swarm over /Users/varma/tend (9 tasks)

Graph: 6 parallel reviewers → 2 cluster verifiers → 1 synthesizer. Authored per
the skill: packets per task, `swarm validate` (clean), adversarial graph-review
gate (found schema-enforcement gaps — fixed before launch).

1. **Launch**: all 6 reviewers ran concurrently (live in `/workflows` view).
   First-launch failure was itself a product fix: args arrived stringified →
   footer now parses string args (committed).
2. **Checkpoints**: results/<id>.json files appeared mid-run, written by the
   SubagentStop hook with zero worker cooperation.
   ![mid-run](screenshots/01-swarm-status-midrun.png)
3. **Interrupt**: workflow killed (TaskStop) at 4/9 — simulating a rate-limit
   death. `swarm status` showed exact partial state from disk. The SessionStart
   nag a fresh session would see:
   ![nag](screenshots/02-swarm-resume-nag.png)
4. **Resume**: `swarm args --resume` took the lock and rebuilt the completed
   map; re-invoked workflow short-circuited 4 tasks instantly — `v-core`
   launched immediately (cluster already complete). Exactly 5 agents ran on the
   resumed leg (engine usage: agent_count=5).
5. **Finish**: 9/9 done, `swarm finish --status completed`, nag silenced.
   ![completed](screenshots/01-swarm-status-completed.png)

## Demo output (real value, not synthetic)

The demo run produced a verified review of tend: 39 findings from 6 reviewers,
adjudicated by 2 verifiers → **31 confirmed (2 high), 4 refuted, 2 uncertain**.
Full synthesized report: `/Users/varma/tend/docs/swarm-review-2026-06-10.md`.
Highlights: ledger partial-line read can permanently lose records; negative
`tokens_since_state_mark` after `/compact` silently disables the staleness net;
offloaded dict tool-responses are saved as one JSON line (Read offset/limit
can't navigate); uninstall can drop a co-resident hook entry. These feed tend
v0.2.
