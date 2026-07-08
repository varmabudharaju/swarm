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
