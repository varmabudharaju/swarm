# swarm benchmark — solo session vs swarm, on a real codebase too big for one context

> **For the executing session:** this file is self-contained. You do not need
> the conversation that produced it. Work through the runbook top to bottom;
> each session records its numbers into `docs/benchmark/results/`. Announce
> which session (S1/S2/S3) you are executing before starting.

## The point of this design

**Claim under test:** on a real codebase too large to hold in one context,
swarm (parallel workers, each with a small slice + adversarial verification)
finds materially more of what's there than one assistant working alone.

**Why a small clean repo is NOT a fair test — and this is:** if the whole
codebase fits in one context, a solo agent reads all of it and has no
handicap; swarm's machinery is pure overhead. A test only discriminates when
the task exceeds what one agent can cover. So the target is **networkx**
(real, famous, ~280 source files / ~112k LOC of pure-Python algorithms) with
**30 real logic bugs spread across 30 different files**. To find all 30 you
must actually read 30 files scattered through `algorithms/`. A solo agent in
a time box realistically reads 15–30 files and misses the rest; swarm
assigns all ~280 files across ~15 workers so nothing goes unread. **The
metric that separates them is recall — coverage — which is exactly swarm's
structural advantage.**

**Why seeded bugs on real code:** real code makes it the real scenario
(auditing a large library you don't know); injected bugs give an *exact*
answer key so recall and precision are objectively scoreable. The bugs are
operator inversions (`==`↔`!=`, `<`→`<=`, `and`↔`or`) planted by
`bench/mutate.py` — genuinely bug-bearing, and each needs you to read the
surrounding logic's intent to judge (the same operator is correct in
thousands of other places, so it is not grep-able as a class).

**Fairness rules (non-negotiable):**
1. Both conditions get the **same goal prompt, verbatim** (below).
2. Both run in a **fresh session** on the **same session model**, same day,
   and — importantly — **sequentially, not at once** (concurrent runs fight
   for the same 5-hour token budget and unfairly slow whichever overlaps).
3. Neither condition may read `docs/benchmark/answer_key.json` (it lives
   OUTSIDE the audited tree). The audited tree carries a `.mutated` marker
   file telling auditors not to audit it — leave that file alone. S3 is the
   only reader of the key.
4. One wall-clock budget: **45 minutes** per condition. Stop at 45 min and
   score what exists (swarm checkpoints make partial credit natural; solo
   gets whatever it wrote down).
5. No re-runs to shop for a better number. If a run dies for external reasons
   (rate-limit outage), note it, resume/restart once, and say so.

## The task

Adversarial logic-bug audit of a seeded copy of **networkx** at
`~/swarm-bench/networkx-seeded` (280 files; 30 seeded bugs; already built —
see S0 if you need to rebuild).

**Goal prompt (verbatim for BOTH conditions; only `<condition>` differs):**

> Audit the Python codebase at ~/swarm-bench/networkx-seeded for logic bugs —
> especially inverted or off-by-one comparison operators (`==` vs `!=`, `<`
> vs `<=`, `>` vs `>=`) and inverted boolean logic (`and` vs `or`) that are
> wrong for the surrounding code's intent. Cover as much of the codebase as
> you can. Report every finding as `file:line — one-sentence claim`, one per
> bullet, ranked by confidence. Only report bugs you have verified by reading
> the surrounding code. Skip any file named `.mutated`. Write the final
> report to ~/swarm-bench/report-<condition>.md.

## Sessions runbook

### S0 (already done once; rebuild only if the arena is missing)

```bash
NX=$(python3 -c "import site,pathlib; print(pathlib.Path(site.getusersitepackages())/'networkx')")
rm -rf ~/swarm-bench && mkdir -p ~/swarm-bench && cp -R "$NX" ~/swarm-bench/networkx-seeded
python3 docs/benchmark/bench/mutate.py ~/swarm-bench/networkx-seeded --n 30 --key docs/benchmark/answer_key.json
```
The mutator plants 30 recorded operator bugs across 30 files, verifies each
still parses, and writes the key. Confirm the tree still imports
(`PYTHONPATH=~/swarm-bench/networkx-seeded python3 -c "import networkx"`).
**Commit the mutator; do NOT commit the seeded tree; commit the key only
after S3 scores.** Arena facts are in `results/arena.json`.

### S1 (fresh session): SOLO baseline

1. Freshness: this session must NOT load the swarm skill or spawn
   subagents/workflows — one assistant, normal tools.
2. Record start: `date +%s` and the newest `~/.claude/tend/sessions/*/ctx.json`
   token counters into `results/solo-meta.json`.
3. Paste the goal prompt with `<condition>` = `solo`. Work until done or 45 min.
4. Record end time + counters (delta = solo tokens, all at the session-model
   price). Save `results/solo-meta.json` (also note how many distinct files
   it actually opened — that is the coverage story).

### S2 (fresh session): SWARM condition

1. Verify PyPI install is live (`pip show swarm-cc`) and agents resolve.
2. Record start time + ctx.json counters (foreman tokens) into
   `results/swarm-meta.json`.
3. Run `/swarm <goal prompt>` with `<condition>` = `swarm`. Choose the
   **economy** ladder when asked. Read-only audit, so no acceptEdits needed;
   let it run unattended.
4. On completion record: end time, foreman ctx delta, and from the
   completion notification `subagent_tokens`, `agent_count`, `duration_ms`.
   Save the run-dir path into `results/swarm-meta.json` (checkpoint `ts`
   fields are the parallelism timeline). `swarm finish <run-dir> --status completed`.

### S3 (fresh session): score, chart, report

1. Score both reports:
   `python3 docs/benchmark/bench/score.py docs/benchmark/answer_key.json ~/swarm-bench/report-solo.md ~/swarm-bench/report-swarm.md -o docs/benchmark/results/scores.json`
   (Match rule: same file + line within ±3. The script prints each HIT;
   sanity-check a few by hand and note any override.) Unmatched findings are
   candidate false positives — hand-verify each against the source; ones that
   are real count toward precision (not recall).
2. Cost: read CURRENT per-MTok prices (claude-api reference) into
   `results/prices.json`. Solo cost = all tokens at the session-model price.
   Swarm cost = foreman tokens at session price + subagent tokens by tier
   (the run's graph says which task ran on which tier; if per-task token
   counts are unavailable, split `subagent_tokens` by each tier's task share
   and say so).
3. Charts — **read the dataviz skill BEFORE any chart code**, then write four
   figures into `docs/benchmark/figures/`:
   - `f1-recall.png` — recall (bugs found / 30), solo vs swarm (bars). The headline.
   - `f2-coverage.png` — distinct files read, solo vs swarm (bars) — the *why*.
   - `f3-cost.png` — dollars: solo (one bar) vs swarm (stacked by tier) + a
     cost-per-confirmed-bug annotation.
   - `f4-timeline.png` — swarm task Gantt from checkpoint `ts` (parallelism
     made visible); solo shown as one long serial bar above it.
4. Screenshots via `shotlist` (real Terminal): `swarm status <run-dir>` and
   the `score.py` output.
5. Write `docs/benchmark/REPORT.md`: headline table (recall, precision,
   files covered, wall-clock, cost, cost-per-confirmed-bug), the four
   figures, screenshots, and honest caveats. Commit everything INCLUDING the
   answer key (safe now) and push; add a one-line link from the README.

## Metrics

| Metric | Definition |
|---|---|
| Recall | seeded bugs matched / 30 (the headline) |
| Coverage | distinct source files the condition actually read |
| Precision | (matched seeded + hand-verified genuine) / total findings |
| Wall-clock | goal submitted → report file written |
| Tokens | solo: ctx delta; swarm: foreman delta + workflow `subagent_tokens` |
| Cost | tokens × current per-MTok price for the consuming tier |
| Cost per confirmed bug | cost / (matched seeded + verified genuine) |

## Pre-registered expectations (written before running — keeps us honest)

- **Recall:** swarm should clearly beat solo, because recall here is bounded
  by coverage and swarm covers ~280 files vs solo's realistic 15–30. If it
  doesn't, either solo grepped its way to coverage (note it) or the bugs were
  too subtle for anyone to find (see caveat) — say which.
- **Coverage:** the mechanism chart. Expect swarm to have read ~10× the files.
- **Wall-clock:** swarm may be SLOWER — it does more (verification pass) and
  pays decomposition overhead. That is fine and expected; wall-clock is not
  the headline here. Report it straight.
- **Cost:** swarm's raw tokens will be higher (more agents); cost-per-
  confirmed-bug should be lower because most tokens run on sonnet/haiku, not
  the session model. If raw cost also wins, say so; if it loses, the ladder
  discount is the honest story.
- If solo wins anything, the report says so plainly. A benchmark that can't
  lose is marketing.

**Honest caveats to put in the report:** single run, one codebase, one bug
family (operator inversions); these bugs are subtle so ABSOLUTE recall may be
modest for both — the DELTA between conditions is the result, not the
absolute numbers. Seeded ≠ naturally-occurring bugs (standard mutation-
testing tradeoff, taken for exact ground truth).

## Harness scripts (committed under `docs/benchmark/bench/`)

- `mutate.py` — AST-based operator mutator for a large real tree (the main
  test; dry-run verified: 30 bugs / 30 files, tree still imports).
- `score.py` — matches a findings report against the key on file + line±3
  (validated on synthetic reports: solo 40% vs swarm 87%, false positives
  flagged).
- `seed_bugs.py` — the original small-repo variant (pattern mutations into a
  tend copy); kept for a quick secondary run, not used by the networkx test.

## Deliverables checklist (S3)

- [ ] `results/{arena,solo-meta,swarm-meta,scores,prices}.json`
- [ ] `figures/f1..f4.png` (dataviz-skill compliant)
- [ ] shotlist screenshots (run status + scores)
- [ ] `REPORT.md` with headline table, figures, caveats
- [ ] `answer_key.json` committed (post-scoring)
- [ ] README link to the report
