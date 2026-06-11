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

Worker activity is audited machine-wide by agent-pd (hash-chained per-session logs in ~/.claude/pd/audit/).

## Managed paths

`swarm install` owns these locations and will overwrite/delete them on
reinstall/uninstall - do not hand-edit:
`~/.claude/skills/swarm/`, `~/.claude/agents/swarm-{reader,verifier,implementer}.md`,
`~/.claude/workflows/swarm-run.js` (generated; edit sources in this repo).
