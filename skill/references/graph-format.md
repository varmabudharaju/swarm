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
