# swarm benchmark — solo session vs swarm, with ground truth

> **For the executing session:** this file is self-contained. You do not need
> the conversation that produced it. Work through the runbook top to bottom;
> each session records its numbers into `docs/benchmark/results/`. Announce
> which session (S1/S2/S3) you are executing before starting.

## What we are measuring, and why this design

**Claim under test:** a swarm run (parallel right-sized workers + adversarial
verification) beats one assistant doing the same job alone — on speed, on
cost per unit of quality, and on the quality itself.

**Why seeded bugs:** "find bugs in a repo" has no ground truth, so recall and
precision are unmeasurable and both conditions can declare victory. We fix
that by injecting a known set of defects into a copy of a real codebase and
keeping a private answer key. Recall = seeded bugs found; precision = found
claims that are real (seeded or verifiably genuine); everything is scoreable.

**Fairness rules (non-negotiable):**
1. Both conditions get the **same goal prompt, verbatim** (below).
2. Both run in a **fresh session** on the **same session model**, same day.
3. Neither condition may read `docs/benchmark/answer_key.json` (it lives
   OUTSIDE the audited tree; the seeding script also plants nothing that
   names it). The scorer (S3) is the only reader.
4. One wall-clock budget: **45 minutes** per condition. If a condition is
   still running at 45 min, stop it and score what it has (swarm checkpoints
   make partial credit natural; solo gets whatever it wrote down).
5. No re-runs to shop for a better number. If a run crashes for external
   reasons (rate limit outage), note it, resume/restart once, and say so in
   the report.

## The task

Adversarial bug audit of a seeded copy of **tend** (`~/tend`, the sibling
CLI tool — real code, non-trivial, previously audited so its baseline bug
density is roughly known).

**Goal prompt (verbatim for BOTH conditions):**

> Audit the codebase at ~/swarm-bench/tend-seeded for real bugs: logic
> errors, data-loss paths, swallowed failures, resource leaks, incorrect
> defaults, and broken validation. Report every finding as file, line,
> one-sentence claim, and the evidence, ranked by severity. Only report
> defects you have verified against the code — no style notes, no
> hypotheticals. Write the final report to
> ~/swarm-bench/report-<condition>.md with one finding per bullet.

## Sessions runbook

### S0 (any session, once): build the arena

1. `mkdir -p ~/swarm-bench && cp -R ~/tend ~/swarm-bench/tend-seeded && rm -rf ~/swarm-bench/tend-seeded/.git`
   (no git history = no `git log` shortcuts for either condition).
2. Save the two harness scripts below to `docs/benchmark/bench/` and run:
   `python3 docs/benchmark/bench/seed_bugs.py ~/swarm-bench/tend-seeded --key docs/benchmark/answer_key.json`
   It injects **12 bugs** into the `carryover/` package (categories:
   inverted conditions, ordering, dead validation, atomicity, wrong default,
   encoding, resource leak, boundary, swallowed exception) and writes the
   key with exact file/line/category. **Do not commit the seeded tree; DO
   commit the seeding script. The key is committed only after S3 scores.**
3. Sanity: `python3 -m pytest -q` inside the seeded tree may fail — that is
   fine and realistic; note how many of the 12 are test-detectable in the
   key (`caught_by_tests` field) so the report can discuss it.
4. Record the tend commit hash the copy came from in `results/arena.json`.

### S1 (fresh session): SOLO baseline

1. Verify freshness: this session must NOT load the swarm skill or spawn
   subagents/workflows — it is one assistant with normal tools.
2. Record start: `date +%s` and the newest `~/.claude/tend/sessions/*/ctx.json`
   token counters into `results/solo-meta.json`.
3. Paste the goal prompt with `<condition>` = `solo`. Work until done or 45 min.
4. Record end time + token counters again (delta = solo tokens; all tokens
   are session-model tokens). Save `results/solo-meta.json`.

### S2 (fresh session): SWARM condition

1. Verify the PyPI install is live (`pip show swarm-cc`) and agents resolve.
2. Record start time + ctx.json counters (foreman tokens) into
   `results/swarm-meta.json`.
3. Run `/swarm <goal prompt>` with `<condition>` = `swarm`. Choose the
   **economy** ladder when asked. Let the run go unattended (acceptEdits
   posture not needed — read-only audit).
4. When the workflow completes, record: end time, foreman ctx delta, and
   from the completion notification the `subagent_tokens`, `agent_count`,
   `duration_ms`. Copy the run dir path into `results/swarm-meta.json`;
   the per-task checkpoint files' `ts` fields are the parallelism timeline.
5. `swarm finish <run-dir> --status completed` as usual.

### S3 (fresh session): score, chart, report

1. Score both reports against the key:
   `python3 docs/benchmark/bench/score.py docs/benchmark/answer_key.json ~/swarm-bench/report-solo.md ~/swarm-bench/report-swarm.md -o docs/benchmark/results/scores.json`
   Matching rule (in the script): same file AND |line difference| <= 5 AND
   category keyword overlap — then human-confirm each match (the script
   prints a confirm table; overrule it where it is wrong and note it).
   Non-seeded findings: adversarially verify each (spawn a verifier or check
   by hand); real ones count toward precision, not recall.
2. Compute cost: convert tokens to dollars using CURRENT per-MTok prices —
   read them fresh (claude-api reference) at scoring time; parameterize in
   `results/prices.json`. Solo cost = all tokens at the session-model price.
   Swarm cost = foreman tokens at session price + subagent tokens split by
   tier (the run's graph tells you which tasks ran on which tier; weight by
   each task's share if per-task tokens are unavailable, and say so).
3. Charts — read the **dataviz skill BEFORE writing any chart code**, then
   produce four figures into `docs/benchmark/figures/`:
   - `f1-time.png` — wall-clock, solo vs swarm (bar).
   - `f2-cost.png` — dollars, solo (one bar) vs swarm (stacked by tier).
   - `f3-quality.png` — recall and precision, grouped bars per condition.
   - `f4-timeline.png` — swarm task Gantt from checkpoint `ts` values,
     the visual proof of parallelism (solo = one long bar above it).
4. Pictures — `shotlist` captures (real Terminal): `swarm status <run-dir>`
   of the completed benchmark run, and the scores table output.
5. Write `docs/benchmark/REPORT.md`: headline table (time, cost, recall,
   precision, cost-per-confirmed-finding), the four figures, screenshots,
   honest caveats (single run, one task family, seeded-bug realism), then
   commit everything INCLUDING the answer key (now safe) and push.

## Metrics definitions

| Metric | Definition |
|---|---|
| Wall-clock | goal submitted -> final report file written |
| Tokens | solo: session ctx delta; swarm: foreman delta + workflow subagent_tokens |
| Cost | tokens x current per-MTok price for the tier that consumed them |
| Recall | seeded bugs matched / 12 |
| Precision | (matched seeded + verified-genuine) / total findings reported |
| Cost per confirmed finding | cost / (matched seeded + verified genuine) |

## Harness scripts

### bench/seed_bugs.py (commit this; run in S0)

```python
#!/usr/bin/env python3
"""Inject 12 known bugs into a copy of tend; emit a private answer key.
Each mutation is a small, realistic defect. Idempotent: refuses to run twice
(marker file). Usage: seed_bugs.py <tree> --key <answer_key.json>"""
import argparse, json, re, sys
from pathlib import Path

# (pattern, replacement, category, description) applied to the FIRST match
# in the named file. Line numbers are recorded at apply time, not hardcoded.
# The tend repo's real code lives in carryover/ (tend/ is a deprecated shim
# package - auditors will discover that themselves). Idiom availability was
# verified against tend HEAD on 2026-07-08 (22 modules, ~1.8k lines).
MUTATIONS = [
    ("carryover/ledger.py",   r"if not ",                "if ",                  "inverted-condition",  "ledger branch inverted"),
    ("carryover/ledger.py",   r"reverse=True",           "reverse=False",        "ordering",            "records sorted in the wrong direction"),
    ("carryover/config.py",   r"\.strip\(\)",            ".rstrip()",            "validation-gap",      "leading whitespace defeats parsing"),
    ("carryover/paths.py",    r"\.replace\(",            ".rename(",             "atomicity",           "non-atomic write can corrupt on crash"),
    ("carryover/paths.py",    r"default=(\w+)",          "default=0",            "wrong-default",       "config default disabled"),
    ("carryover/hookio.py",   r'encoding="utf-8"',       'encoding="ascii"',     "encoding",            "non-ascii session data crashes"),
    ("carryover/offload.py",  r"os\.close\(fd\)",       "pass",                 "resource-leak",       "file descriptor never closed - leak"),
    ("carryover/advisor.py",  r" >= ",                   " > ",                  "boundary",            "threshold fires one unit late"),
    ("carryover/cli.py",      r" >= ",                   " > ",                  "boundary",            "second boundary defect"),
    ("carryover/boundary.py", r"if not ",                "if ",                  "inverted-condition",  "guard inverted"),
    ("carryover/anchor.py",   r"except ([\w.]+(?: as \w+)?):", r"except BaseException:", "swallowed-exception", "over-broad catch swallows real failures"),
    ("carryover/hook.py",     r"if name in INGEST:",     "if name not in INGEST:", "inverted-condition", "ledger ingest fires for the wrong events"),
]
# NOTE FOR S0: apply, then EYEBALL each mutated site - a mutation that lands
# on a semantically harmless spot (e.g. inverting a cosmetic branch) should
# be moved to a meatier line in the same file by hand. If a pattern misses,
# hand-seed the same category in the same file. Record every manual change
# in the key with "manual": true. The key, not this list, is ground truth.

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tree"); ap.add_argument("--key", required=True)
    a = ap.parse_args()
    tree = Path(a.tree)
    marker = tree / ".seeded"
    if marker.exists(): sys.exit("already seeded - refuse to double-inject")
    key = []
    for rel, pat, repl, cat, desc in MUTATIONS:
        p = tree / rel
        if not p.exists():
            print(f"MISS (no file) {rel} - seed manually"); continue
        src = p.read_text(encoding="utf-8")
        m = re.search(pat, src)
        if not m:
            print(f"MISS (no match) {rel}: {pat} - seed manually"); continue
        line = src[:m.start()].count("\n") + 1
        p.write_text(src[:m.start()] + re.sub(pat, repl, m.group(0)) + src[m.end():], encoding="utf-8")
        key.append({"file": rel, "line": line, "category": cat, "description": desc})
        print(f"seeded {rel}:{line} [{cat}]")
    marker.write_text("seeded for benchmark - do not audit this file\n")
    Path(a.key).write_text(json.dumps({"bugs": key, "total": len(key)}, indent=2))
    print(f"{len(key)} bugs -> {a.key}  (target: 12; hand-seed any misses)")

if __name__ == "__main__":
    main()
```

### bench/score.py (commit this; run in S3)

```python
#!/usr/bin/env python3
"""Score a findings report against the answer key.
A report line matches a seeded bug if it names the same file, a line within
+/-5, and shares a category keyword. Prints a confirm table (human overrules
welcome), then emits scores JSON. Usage:
score.py <answer_key.json> <report-solo.md> <report-swarm.md> -o scores.json"""
import argparse, json, re
from pathlib import Path

CATEGORY_WORDS = {
    "inverted-condition": ["invert", "condition", "wrong branch", "negat", "backwards"],
    "off-by-one": ["off-by-one", "off by one", "last", "boundary", "range"],
    "swallowed-exception": ["swallow", "silent", "ignored", "pass", "suppress"],
    "wrong-default": ["default"], "wrong-exit-code": ["exit", "status code"],
    "encoding": ["encoding", "ascii", "unicode", "utf"],
    "atomicity": ["atomic", "corrupt", "partial write", "rename"],
    "resource-leak": ["leak", "cleanup", "close", "finally"],
    "boundary": ["boundary", ">=", "one early", "threshold"],
    "validation-gap": ["whitespace", "strip", "validat"],
    "ordering": ["order", "sorted", "nondeterminis"],
}

def findings(report_path):
    out = []
    for ln in Path(report_path).read_text(encoding="utf-8").splitlines():
        m = re.search(r"([\w/\.]+\.py)[:,]?\s*(?:line\s*)?(\d+)", ln)
        if m: out.append({"file": m.group(1), "line": int(m.group(2)), "text": ln.lower()})
    return out

def score(key, fs):
    matched, used = [], set()
    for bug in key["bugs"]:
        hit = None
        for i, f in enumerate(fs):
            if i in used or not f["file"].endswith(bug["file"].split("/")[-1]): continue
            if abs(f["line"] - bug["line"]) > 5: continue
            if any(w in f["text"] for w in CATEGORY_WORDS.get(bug["category"], [])):
                hit = i; break
        if hit is not None: used.add(hit); matched.append({**bug, "matched_line": fs[hit]["line"]})
    return {"recall_matches": matched, "recall": len(matched) / key["total"],
            "total_findings": len(fs), "unmatched_findings": len(fs) - len(used)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key"); ap.add_argument("solo"); ap.add_argument("swarm")
    ap.add_argument("-o", required=True)
    a = ap.parse_args()
    key = json.loads(Path(a.key).read_text())
    out = {c: score(key, findings(p)) for c, p in [("solo", a.solo), ("swarm", a.swarm)]}
    for c, s in out.items():
        print(f"\n== {c}: recall {s['recall']:.0%} ({len(s['recall_matches'])}/{key['total']}), "
              f"{s['total_findings']} findings, {s['unmatched_findings']} unmatched (verify by hand for precision)")
        for m in s["recall_matches"]: print(f"  HIT {m['file']}:{m['line']} [{m['category']}]")
    Path(a.o).write_text(json.dumps(out, indent=2))
    print(f"\n-> {a.o}  (add precision after hand-verifying unmatched findings)")

if __name__ == "__main__":
    main()
```

## What "swarm wins" must look like (pre-registered expectations)

Writing these down BEFORE running keeps us honest:

- **Time:** swarm should finish in well under half the solo wall-clock (6-7
  parallel scanners vs one serial reader) — if it doesn't, overhead
  (decomposition + gate + packets) ate the parallelism and we say so.
- **Quality:** swarm recall should be >= solo (more eyes, each with a small
  scope) and precision clearly higher (adversarial verification screens
  false positives; solo has no checker).
- **Cost:** swarm's raw token count will likely be HIGHER (more agents),
  but cost-per-confirmed-finding should be lower because most tokens run on
  sonnet/haiku instead of the session model. If raw cost wins too, say so;
  if it loses, the ladder discount is the story, not a spin.
- If solo wins anything, the report says it plainly. A benchmark that can't
  lose is marketing.

## Deliverables checklist (S3 output)

- [ ] `docs/benchmark/results/{arena,solo-meta,swarm-meta,scores,prices}.json`
- [ ] `docs/benchmark/figures/f1..f4.png` (dataviz-skill compliant)
- [ ] Terminal screenshots via shotlist (run status + scores table)
- [ ] `docs/benchmark/REPORT.md` with the headline table + figures + caveats
- [ ] `docs/benchmark/answer_key.json` committed (post-scoring only)
- [ ] README gets a one-line link to the report once it exists
