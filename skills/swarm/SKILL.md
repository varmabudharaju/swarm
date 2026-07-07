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

- Custom agent types (swarm-reader/verifier/implementer) resolve only in sessions started AFTER `swarm install`. If agentType resolution fails mid-session, re-author the graph with `agent_type: null` (workers then run as default subagents driven by their prompts/packets) or restart the session.

- NEVER invoke the swarm-run workflow with a graph that failed `swarm validate`.
- NEVER resume without `swarm args --resume` (it takes the resume lock).
- Implement tasks ALWAYS get `isolation: "worktree"` + the swarm-implementer
  agent; integrate tasks ALWAYS run quarantined in their own worktree; the final
  merge to the user's branch happens in THIS session with user approval - never
  inside the workflow.
- Every task schema includes `summary: {type: string, maxLength: 2000}` plus
  whatever typed fields the task needs. Implement-task schemas must also include
  branch, worktree_path, files_touched, commits.
- Unattended implement swarms require an auto-accepting permission posture (acceptEdits or a Bash allowlist) - state this to the user BEFORE launching; otherwise the run stalls at the first permission prompt.

## Process

1. **Scout** (cheap, inline): list relevant files/dirs, read the plan/spec if one
   exists. Just enough to decompose honestly - don't deep-read what workers will read.
2. **Check headroom**: read the newest `~/.claude/tend/sessions/*/ctx.json`
   `rate_limits`. If `five_hour.used_percentage > 85`, tell the user the
   `resets_at` time and offer to defer. Otherwise set `agent_ceiling` in the
   graph from remaining headroom (rough guide: each task ~1-3% of a 5h window).
3. **Choose the ladder**: ask the user ONE question (AskUserQuestion) - which
   model ladder for this run - unless they already said so in the goal:
   - `economy` (default): haiku, sonnet, opus - opus tops judgment/synthesis
   - `duo`: sonnet, opus only
   - `premium`: haiku, sonnet, opus, fable (only if their plan has fable)
   Set the chosen list as `allowed_models` in graph.json and assign every
   task's `model` from inside it. Judgment/synthesis tasks: omit `model`
   (inherit the session - run your main session on opus with thinking).
4. **Decompose** into `graph.json` per `references/graph-format.md`, following a
   shape from `references/shapes.md`. Maximize width honestly: every task that
   CAN be independent IS independent; deps are data dependencies, never phases.
   The scheduler itself imposes no width cap; going wider than the Workflow
   runtime's concurrent-agent slots (~16) buys queueing, not speed, so there's
   no throughput reason to split narrower than the true dependency structure.
   Verify tiers: one verifier per 4-6 sibling finding-tasks; per-task verify only
   for results feeding implement tasks.
   Assign `model` explicitly per task from the chosen ladder (lowest tier that
   fits - see references/graph-format.md "Model tiers"): weigh quality stakes
   (does the result feed implement tasks?), ambiguity, complexity, token cost,
   retry economics. Mechanical checks -> haiku; bounded scans/verifies ->
   sonnet; real coding -> opus; judgment/synthesis -> omit (inherit, the top
   of your ladder). Defaults are a safety net, not a reason to skip the decision.
   Pair with `effort` (see graph-format.md "Effort tiers"): `low` on
   mechanical tasks and tight scans; omit elsewhere.
5. **Packets**: write one `packets/<id>.md` per task per
   `references/packet-guide.md`. Self-containment test: could a stranger with
   only this packet + prompt do the work? If not, the packet is incomplete.
6. **Validate**: run `swarm validate <run-dir>/graph.json`. Fix every error;
   treat warnings as design feedback, not noise.
7. **Review gate**: spawn ONE swarm-verifier agent with the goal + graph.json
   content; ask it to attack the decomposition (missing tasks, fake width, fan-in
   mush, packet gaps, model tiers over- or under-provisioned for the task).
   Fix what it finds.
8. **Launch**: `ARGS=$(swarm args <run-dir>/graph.json --session-model <your-tier>)`
   where `<your-tier>` is THIS session's model tier (haiku|sonnet|opus|fable -
   you know your own model), then invoke the Workflow tool:
   `{name: "swarm-run", args: <parsed ARGS JSON>}`.
9. **Finish**: when the workflow returns, act on its state:
   - completed cleanly -> `swarm finish <run-dir> --status completed`, then
     synthesize/present results (read full result files, not just summaries).
   - if the result has a non-empty `fallbacks` map, tell the user which tasks
     did not run on their intended tier (e.g. "design-api: opus->inherit").
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
   rebuilds the completed map. If refused, show the user the lock owner and age from the error message; only after their explicit confirmation delete resume.lock and retry.
3. Orphan implement branches: for any implement task WITHOUT a result file but
   WITH a `swarm/<run>/<task>` branch, delete branch + worktree (partial work
   from a dead run) and tell the user what was discarded.
4. **Ask the user** with precise numbers: done/pending/failed counts, discarded
   partials, estimated remaining cost. Only on approval invoke
   `{name: "swarm-run", args: <ARGS>}`. Re-check rate-limit headroom (step 2 of Process) before re-launching.
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
