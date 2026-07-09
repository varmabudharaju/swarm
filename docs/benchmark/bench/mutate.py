#!/usr/bin/env python3
"""Plant N realistic semantic bugs across a large real codebase, spread over
distinct files, and emit an exact answer key. Uses AST to locate genuine
operator sites (never strings/comments), then edits the single operator token
on that line so the diff is minimal and the bug is not obvious from a glance.

Mutations (all recompile by construction — same AST shape, one operator swap):
  a <  b  ->  a <= b     (inequality-boundary / off-by-one)
  a >  b  ->  a >= b      (inequality-boundary)
  a == b  ->  a != b      (inverted-equality)
  x and y ->  x or y      (inverted-boolean)
  x or y  ->  x and y     (inverted-boolean)

These need READING to catch: `<=` is correct in thousands of places, so an
auditor must reason about THIS site's intent — it is not grep-able as a class.

Deterministic: files sorted, first valid site per file taken, until N. Rerun
-> identical key. Idempotent via a .mutated marker. Skips test files.

Usage: mutate.py <tree> --n 30 --key <answer_key.json> [--max-per-file 1]
"""
import argparse, ast, json, sys
from pathlib import Path

SWAPS = [
    (ast.Lt,    " < ",  " <= ", "inequality-boundary", "off-by-one: < weakened to <="),
    (ast.Gt,    " > ",  " >= ", "inequality-boundary", "off-by-one: > weakened to >="),
    (ast.Eq,    " == ", " != ", "inverted-equality",   "equality flipped to inequality"),
    (ast.NotEq, " != ", " == ", "inverted-equality",   "inequality flipped to equality"),
]
BOOL = {ast.And: (" and ", " or ", "and->or"), ast.Or: (" or ", " and ", "or->and")}


def _sites(tree):
    """Yield (lineno, search, replace, category, desc) for single-op Compares
    and BoolOps. Only lines where `search` occurs exactly once qualify (no
    ambiguity about which token we swap)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            for cls, s, r, cat, desc in SWAPS:
                if op is cls:
                    out.append((node.lineno, s, r, cat, desc))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL:
            s, r, desc = BOOL[type(node.op)]
            out.append((node.lineno, s, r, "inverted-boolean", desc))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tree")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--key", required=True)
    ap.add_argument("--max-per-file", type=int, default=1)
    a = ap.parse_args()
    tree = Path(a.tree)
    marker = tree / ".mutated"
    if marker.exists():
        sys.exit("already mutated - refuse to double-inject")

    def is_test(f):
        parts = f.relative_to(tree).parts
        return (any(p in ("test", "tests", "__pycache__") for p in parts)
                or f.name.startswith("test_") or f.name.endswith("_test.py"))

    def first_valid_site(f):
        """Return (lines, lineno, search, replace, cat, desc) for the first
        applicable, unambiguous, still-compiling site in f, or None."""
        try:
            src = f.read_text(encoding="utf-8")
            sites = _sites(ast.parse(src))
        except (SyntaxError, UnicodeDecodeError):
            return None
        lines = src.splitlines(keepends=True)
        for lineno, search, replace, cat, desc in sites:
            i = lineno - 1
            if i >= len(lines) or lines[i].count(search) != 1:
                continue
            trial = "".join(lines[:i] + [lines[i].replace(search, replace, 1)] + lines[i + 1:])
            try:
                ast.parse(trial)
            except SyntaxError:
                continue
            return (lines, i, search, replace, cat, desc)
        return None

    # SCATTER, don't cluster: an earlier bug filled the alphabetically-first N
    # files, so every bug landed in one corner and the coverage test was void.
    # Collect ALL eligible files (>=1 valid site), then pick N spread evenly
    # across the sorted tree so bugs span the whole codebase (one per file).
    files = sorted(f for f in tree.rglob("*.py") if not is_test(f))
    eligible = [f for f in files if first_valid_site(f)]
    if len(eligible) < a.n:
        sys.exit(f"only {len(eligible)} eligible files, need {a.n}")
    step = len(eligible) / a.n
    chosen = [eligible[int(i * step)] for i in range(a.n)]

    key = []
    for f in chosen:
        lines, i, search, replace, cat, desc = first_valid_site(f)
        lines[i] = lines[i].replace(search, replace, 1)
        f.write_text("".join(lines), encoding="utf-8")
        rel = str(f.relative_to(tree))
        key.append({"file": rel, "line": i + 1, "category": cat, "description": desc})
        print(f"mutated {rel}")
    # Spread guard: a coverage benchmark is void if bugs cluster in one corner.
    # Require them to span many distinct directories (>=60% of bug count).
    dirs = {str(Path(b["file"]).parent) for b in key}
    need = max(2, int(a.n * 0.6))
    if len(dirs) < need:
        sys.exit(f"ARENA INVALID: {len(key)} bugs span only {len(dirs)} dirs "
                 f"(need >={need}) - bugs are clustered, coverage test would be void")
    marker.write_text("mutated for benchmark - do not audit this file\n")
    Path(a.key).write_text(json.dumps({"bugs": key, "total": len(key)}, indent=2))
    print(f"\n{len(key)} bugs across {len(dirs)} distinct dirs -> {a.key}")


if __name__ == "__main__":
    main()
