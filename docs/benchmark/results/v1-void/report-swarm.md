# Bug audit — ~/swarm-bench/tend-seeded (swarm condition)

Adversarial audit: 9 parallel finder agents (per-module slices + full test-suite run + shim check) followed by 2 independent adversarial verify gates. 22 raw findings were produced; 3 were refuted as false positives and 4 were cross-verifier duplicates. The 15 below are confirmed, ranked by severity. Every claim was verified against the code; test-provable defects were reproduced by actually running the shipped suite.

## High

- **carryover/hook.py:13** — The ledger-ingest gate is inverted (`if name not in INGEST:`), so `ledger.ingest()` is skipped for the four events it must fire on (PostToolUse/UserPromptSubmit/Stop/PreCompact) and runs for every other event. Evidence: `INGEST` (line 6) holds exactly those four events, yet the `not in` test dispatches ingest only for everything else; confirmed empirically — `tests/test_ledger.py::test_posttooluse_ingests_and_offloads` fails. Double-confirmed by both verify gates. (logic-error, inverted condition)

- **carryover/boundary.py:7** — The Stop handler's guard is inverted (`if sid: return None`), so the handler returns immediately on every real invocation (session_id is always present) and STATE.md freshness/boundary tracking never runs. Evidence: sibling handlers use `if not sid` (anchor.py:10, precompact.py:15); all 6 tests in `tests/test_boundary.py` fail with KeyError because `flags.update` is never reached. Double-confirmed. (logic-error, inverted condition)

- **carryover/ledger.py:172** — `top_results()` sorts with `reverse=False`, returning the n *smallest* tool results instead of the largest offenders it exists to surface. Evidence: `sorted(..., key=tokens, reverse=False)[:n]`; `tests/test_ledger.py::test_top_results_sorted` expects the 500-token item for n=1 and gets the 2-token one; cli.py:62/83 label the output "top results"/"tool results by size", confirming largest-first intent. Double-confirmed. (logic-error, wrong sort ordering)

- **carryover/config.py:80** — The bracket-list parser only `.rstrip()`s each item (never strips leading whitespace), so `offload_tools: [Bash, Grep, Glob]` yields `' Grep'`, `' Glob'` — names that never match in offload.py:12's exact membership test, silently disabling offload for every tool after the first. Evidence: reproduced in a shell — `_parse_value('[Bash, Grep, Glob]') -> ['Bash', ' Grep', ' Glob']`; offload.py:12 does exact `tool not in cfg.offload_tools`. (broken-validation / leading-whitespace parsing gap)

## Medium

- **carryover/ledger.py:41** — `mark_degraded()` has an inverted guard (`if sid: return`), so it returns immediately for every real (truthy) session id and the degraded flag is never set after an ingest crash. Evidence: guard should be `if not sid`; called from hook.py:20 on the ingest-exception path; `test_handler_still_runs_when_ingest_crashes` fails. (logic-error, inverted condition)

- **carryover/hookio.py:31** — `append_log()` opens the diagnostic log with `encoding="ascii"`, so any non-ASCII traceback raises UnicodeEncodeError that the enclosing `except BaseException: pass` (line 33) swallows — the diagnostic is silently lost exactly when it is needed. Evidence: the logged text is `traceback.format_exc()`, which routinely carries non-ASCII paths/messages; ascii encode raises, line 33 suppresses. (encoding defect + swallowed failure)

- **carryover/offload.py:89** — `_index_append` opens a raw fd via `os.open` (line 85) but its `finally` block is `pass` instead of `os.close(fd)`, leaking one file descriptor per offloaded tool output. Evidence: the fd is never wrapped in `os.fdopen` (unlike `_save` at line 61) and no `os.close` exists in the function; repeated offloads accumulate fds toward the OS limit. (resource-leak)

- **carryover/cli.py:147** — `cmd_find`'s cap check `if total > args.max:` runs before the print-and-increment, so it emits max+1 matches before truncating. Evidence: for `--max 3` the guard is false at total=0..3, printing a 4th line; `tests/test_find.py::test_find_max_cap_and_clipped_note` asserts exactly 3 and fails; fix is `>=`. (boundary off-by-one)

- **carryover/cli.py:177** — `cmd_handoff` reads STATE.md with strict `encoding="utf-8"` and no `errors=` fallback, so a STATE.md containing any non-UTF-8 byte crashes the `handoff` command with an uncaught UnicodeDecodeError. Evidence: the hardened sibling read at cli.py:141 uses `errors="replace"`; this one does not, so a hand-edited file tracebacks instead of degrading. (encoding crash on legal input)

- **carryover/config.py:45** — `_coerce`'s `offload_tools` branch is `return [value]` for ANY string, so a malformed unterminated bracket value (`[Bash, Grep`) becomes the bogus single tool `'[Bash, Grep'` instead of being rejected — contradicting the line-79 comment that claims `_coerce` will reject it. Evidence: reproduced — coercing raw `'[Bash, Grep'` returns `['[Bash, Grep']`, silently breaking offload matching instead of falling back to defaults. (broken-validation)

- **carryover/install.py:39** — `_load_settings` decodes settings.json inside a try that catches only `json.JSONDecodeError`, so invalid UTF-8 bytes raise an uncaught UnicodeDecodeError instead of the graceful `SettingsError`, and `install`/`uninstall`/`wrap-statusline` crash with a raw traceback. Evidence: UnicodeDecodeError is not a JSONDecodeError subclass (sibling ValueErrors); cli.py catches only `install.SettingsError`; contradicts the parse-or-refuse contract asserted in tests/test_install.py. (broken-validation)

- **bench/outcome.py:297** — The session dict stores `artifact[:6000]` and that truncated copy is what the blind judge scores, so artifacts over 6000 chars are quality-judged on cut-off code while the mechanical score (line 285) used the full artifact. Evidence: no full copy is retained; `run_judge` (line 327) feeds the stored field to `build_judge_prompt` (line 204) unmodified. (logic-error on large-artifact path)

## Low

- **carryover/cli.py:142** — `cmd_find`'s `except OSError: continue` silently drops any unreadable output file from the search with no warning, so results can be incomplete while appearing exhaustive. Evidence: the catch emits no signal of the skipped file; output claims a complete scan. (swallowed failure)

- **carryover/state.py:91** — `seed()` catches bare `except OSError: return` (beyond the documented FileExistsError race), silently swallowing permission/disk-full errors with no diagnostic. Evidence: over-broad catch masks PermissionError/ENOSPC; a missing STATE.md is then indistinguishable from a handled state. (swallowed failure)

- **carryover/retention.py:36** — `sweep()` increments `stats['removed']` unconditionally even when `shutil.rmtree(ignore_errors=True)` (line 35) silently fails, so `tend clean` reports sessions as removed that remain on disk. Evidence: cli.py:187 prints `removed N session(s)` from this counter regardless of deletion outcome. (swallowed failure → misleading count)

---

*Refuted during adversarial verification (excluded above): cli.py:119 `--session` path-traversal claim (no privilege boundary in a local, user-invoked CLI); anchor.py:50 `except BaseException` (no demonstrable trigger — upstream `run_fail_open` already owns interrupt handling); bench/mechanical.py:268 latency-loop `except: pass` (mirrors production's fail-open dispatch; correctness asserted separately by `measure_invariants`).*
