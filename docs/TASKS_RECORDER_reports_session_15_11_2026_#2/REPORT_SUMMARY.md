# Session #2 Summary — Issues I-01..I-11

**Branch:** dev/v1.2.0  
**Date:** 2026-11-15  
**Tests after each change:** `python -m unittest discover -q`

---

## Status table

| ID | Description | Status | Report |
|----|-------------|--------|--------|
| I-01 | session.py extraction + bench_e2e | ✅ Done | REPORT_I01.md |
| I-02 | First-frame race (Recorder._capture) | ✅ Done | REPORT_I02.md |
| I-03 | lost_packets baseline at recording start | ✅ Done | REPORT_I03.md |
| I-04 | Writer failure handling (makedirs, try/finally) | ⬜ Not started | — |
| I-05 | 64 KB meta pre-check before PLSQ start | ⬜ Not started | — |
| I-06 | fmt_var authoritative (path extension ≠ format → refuse) | ⬜ Not started | — |
| I-07 | Fix universes display (use build_spans accounting for start_channel) | ⬜ Not started | — |
| I-08 | Async finalization in App via PlsqRecordingSession | ⬜ Not started | — |
| I-09 | on_artsync partial flag (partial when _updated ⊊ _expected) | ✅ Done | REPORT_I09.md |
| I-10 | Short-packet zero-fill + short_packets counter | ✅ Done | REPORT_I10.md |
| I-11 | D11 blend reduction in both PLSQ and JSON paths | ⬜ Not started | — |

---

## Test counts by commit

| Commit | Tests | Result |
|--------|------:|--------|
| `1510284` I-02 (session.py created) | 181 | OK |
| `4768fea` I-09/I-10 fixes + I-02/I-03 tests | 182 → 190 | OK |
| `918431a` I-03 main.py fix | 190 | OK |
| `1219878` I-10 tests + _seq_last + stress | 190 → 195 | OK |

Command: `python -m unittest discover -q`  
Latest result: `Ran 195 tests in 24.579s — OK`

---

## Changed files

| File | Issues |
|------|--------|
| `session.py` | I-01 (created), I-02, I-09, I-10, I-03 (_seq_last) |
| `main.py` | I-01 (import cleanup), I-03 (baseline), I-03 (_seq_last) |
| `tools/bench_e2e.py` | I-01 (created) |
| `tools/stress_first_frame.py` | I-02 (created) |
| `packets_tester.py` | I-01 (× → x) |
| `tests/test_r5.py` | I-09 (modified existing test, 2 new tests), I-10 (5 new tests) |
| `tests/test_i02_i03.py` | I-02, I-03 (created, 7 tests) |
| `docs/BENCHMARK_RECORDER.md` | I-01 (e2e baseline section) |
| `CLAUDE.md` | D11 (locked decision) |

---

## Process violations / open notes

1. **session.py committed in I-02 commit** (`1510284`): session.py belongs to I-01; not rebased per policy.
2. **main.py I-03 fix not covered by automated test**: the `App._toggle_record → _finish_recording` path requires Tkinter GUI. Will be automatically covered when I-08 migrates `App` to `PlsqRecordingSession`.
3. **I-10 GUI warning not yet implemented**: the statistics block warning "short packets: N (…)" is deferred to the GUI pass with I-04..I-07.
4. **"Before" benchmark ran at 10 s/point with --no-content-check**: torn frames not measured in that run. 60-second full-content baseline to be run after all fixes (after I-11).

---

## Open questions for the user

None at this time.
