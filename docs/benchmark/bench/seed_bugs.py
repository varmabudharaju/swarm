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
