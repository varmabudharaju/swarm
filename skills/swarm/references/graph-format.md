# graph.json format (version 1)

Location: `~/.claude/swarm/runs/<project-slug>/<run-id>/graph.json`.
Generate run ids as `YYYY-MM-DD-<short-slug>`. Compute graph_hash with
`swarm validate --print-hash` after editing tasks (or let `swarm validate` tell
you the expected value).

```json
{
  "version": 1,
  "run_id": "2026-06-10-auth-audit",
  "goal": "one paragraph",
  "project": "/abs/project/path",
  "graph_hash": "<from swarm validate>",
  "budget_tokens": null,
  "agent_ceiling": null,
  "allowed_models": ["haiku", "sonnet", "opus"],
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
      "model": "sonnet",
      "effort": null,
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
```

Rules enforced by `swarm validate` (errors block launch): version 1; unique ids;
known types; deps exist; no cycles; fan-in <= 8; every schema has
summary:string maxLength<=2000; non-empty prompts; graph_hash matches.
Warnings: >25 tasks, median prompt <400 chars, verify ratio >30%, barrier smell.

Task types: research, review (read-only; swarm-reader), verify (swarm-verifier),
implement (swarm-implementer + isolation worktree), integrate (general-purpose +
isolation worktree), synthesize (general-purpose).

Implement-task schema must add: branch, worktree_path, files_touched, commits.

## Model tiers

Graph-level `allowed_models` (optional) is the run's model policy, chosen by
the user at launch: `economy` = haiku|sonnet|opus (default), `duo` =
sonnet|opus, `premium` = haiku|sonnet|opus|fable. It is folded into graph_hash, enforced by
validation (every explicit task model must be inside it), and the executor
clamps type-defaults into it (nearest allowed tier above, else below).
Omitted -> all four models allowed (pre-ladder graphs keep their hashes).

Per-task `model`: one of `haiku | sonnet | opus | fable` (tier aliases only —
versions are not addressable), drawn from `allowed_models`. Set it EXPLICITLY
on every task as a per-task judgment weighing quality stakes, ambiguity,
complexity, token cost, and retry economics — lowest tier that fits:

- top of ladder/omit: decomposition-grade reasoning, ambiguous goals, synthesis
- `opus`: real coding (implement/integrate, debugging, refactors)
- `sonnet`: clear-goal bounded work (scans, diff review, adversarial verify)
- `haiku`: mechanical (output/schema checks, extraction, formatting, capture)

Omitted -> the executor applies safety-net defaults by type
(research/review/verify -> sonnet; implement/integrate -> opus; synthesize ->
inherit), clamped into `allowed_models` and then capped at the launching
session's tier (`session_model`) — the session cap wins. Explicit values are
never clamped or capped. If a tier fails every retry, the final retry re-runs
on the session model and the run report lists it under `fallbacks`.

## Effort tiers

Optional per-task `effort`: one of `low | medium | high | xhigh | max` — the
second token-economy lever, orthogonal to `model` (it survives the model
fallback retry). Omitted -> the worker inherits the session's effort. Set
`low` on mechanical/haiku-grade tasks and tightly bounded scans; omit for
judgment, synthesis, and anything feeding implement tasks. Unknown values are
rejected at validation (`effort` error).
