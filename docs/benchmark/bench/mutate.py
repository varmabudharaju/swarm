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

    files = sorted(f for f in tree.rglob("*.py") if not is_test(f))
    key = []
    for f in files:
        if len(key) >= a.n:
            break
        try:
            src = f.read_text(encoding="utf-8")
            sites = _sites(ast.parse(src))
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = src.splitlines(keepends=True)
        applied = 0
        for lineno, search, replace, cat, desc in sites:
            if applied >= a.max_per_file or len(key) >= a.n:
                break
            i = lineno - 1
            if i >= len(lines) or lines[i].count(search) != 1:
                continue
            new_line = lines[i].replace(search, replace, 1)
            trial = "".join(lines[:i] + [new_line] + lines[i + 1:])
            try:
                ast.parse(trial)  # must still compile
            except SyntaxError:
                continue
            lines[i] = new_line
            rel = str(f.relative_to(tree))
            key.append({"file": rel, "line": lineno, "category": cat, "description": desc})
            applied += 1
        if applied:
            f.write_text("".join(lines), encoding="utf-8")
            print(f"mutated {f.relative_to(tree)} x{applied}")
    marker.write_text("mutated for benchmark - do not audit this file\n")
    Path(a.key).write_text(json.dumps({"bugs": key, "total": len(key)}, indent=2))
    print(f"\n{len(key)} bugs across {len({b['file'] for b in key})} files -> {a.key}")
    if len(key) < a.n:
        print(f"WARNING: wanted {a.n}, planted {len(key)} - raise --max-per-file or --n down")


if __name__ == "__main__":
    main()
