# Benchmark v1 was void — postmortem (2026-07-08)

**What happened:** the first benchmark arena was invalid. `mutate.py`
originally iterated files in sorted order and filled the first 30 valid sites,
so all 30 seeded bugs landed in the alphabetically-first 39 of 288 files — 24
of them in just `algorithms/approximation/` + `algorithms/bipartite/`. The
test's premise ("bugs scattered so one agent can't cover them all") never held.

**v1 numbers (both VOID, kept for the record):**
- Solo (opus): 80% recall (24/30), 0 false positives — but only because the
  bugs were in a compact, alphabetically-first corner a systematic reader
  covers completely.
- Swarm v1 (economy: sonnet readers, precision-first packet): 0% recall
  (0/30). Confounded three ways: (1) clustered arena, (2) a precision-first
  reader prompt that dismissed provable bugs, (3) sonnet readers vs opus solo.

**Root cause:** cluster bug in the mutator (mine). Secondary: reader packet
optimized for precision, not recall; and an unintended tier asymmetry
(economy ladder put readers on sonnet while the solo baseline was opus).

**Fixes applied:**
- `mutate.py` now selects N eligible files spread evenly across the whole
  sorted tree (one bug per file) and asserts bugs span >= 60% of `--n`
  distinct directories, exiting loudly otherwise. Rebuilt arena: 30 bugs
  across 20 dirs, `__init__.py` … `utils/configs.py`.
- Re-run guidance updated (see PLAN.md): recall-first reader packet; readers
  at the solo baseline's tier (opus) to satisfy the "same session model both
  arms" fairness rule and isolate coverage.

**Lesson for the harness:** validate the ARENA (bug distribution), not just
the decomposition, before running. The swarm's pre-launch review gate caught
a real answer-key leak (a pristine networkx copy on disk) but had no reason to
check bug spread — that check now lives in the mutator.
