# Context packets

One file per task: `packets/<id>.md`. The worker Reads it first. It is the ONLY
context the worker gets besides its prompt and dep summaries.

Structure every packet as:

1. **Goal** - the run's goal in 2-3 sentences, then this task's place in it.
2. **Scope** - exactly which files/dirs/questions this task owns. Name what is
   OUT of scope (especially neighbors owned by sibling tasks).
3. **Constraints** - project conventions, interfaces to respect, versions,
   commands that work here (e.g. `python3 -m pytest`).
4. **Inputs** - where to look first; for implement tasks: the branch naming line
   `git checkout -b swarm/<run-id>/<task-id>` with values filled in.
5. **Output contract** - what each schema field must contain; what a GOOD
   summary looks like for this task (one example sentence).
6. **Do not** - the sharp edges: files not to touch, approaches known to fail
   (from dead-ends), anything that would collide with sibling tasks.

Self-containment test: a stranger with only packet+prompt can do the work.
Never write "as discussed" or "see above" - there is no above.
