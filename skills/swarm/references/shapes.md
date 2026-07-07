# Graph shapes

## Research sweep (read-only)

N independent research tasks (one per question/subsystem/modality), cluster
verifiers (one per 4-6 researchers), reduction tree into synthesize.

```
r1..r12 (research, no deps)
v1 (verify, deps r1-r4)  v2 (verify, deps r5-r8)  v3 (verify, deps r9-r12)
syn-a (synthesize, deps v1,v2)   syn-b (synthesize, deps v3)
final (synthesize, deps syn-a, syn-b)
```

Width 12 honest parallel reads; nothing waits that doesn't have to.

## Implement from plan

For a plan with tasks T1..Tn: implement tasks are file-disjoint lanes; deps only
where one lane consumes another's interface; per-lane verify for anything that
feeds another implement; one quarantined integrate at the end.

```
impl-core (implement)            impl-cli (implement, deps: impl-core)
impl-docs (implement)            verify-core (verify, deps: impl-core)
integrate (integrate, deps: impl-cli, impl-docs, verify-core)
```

integrate merges `swarm/<run>/<task>` branches in dependency order onto
`swarm/<run>/integration`, runs the full test suite, resolves trivial conflicts,
escalates real ones in its result. The merge to the user's branch happens in the
main session with user approval - never inside the workflow.

## Resume semantics (any shape)

Completed tasks short-circuit from the results map. Implement tasks with partial
work but no result are delete-and-restart (orphan branch cleanup) - never
continued. All of this is automatic via `swarm args --resume`; your job is only
to ask the user first.
