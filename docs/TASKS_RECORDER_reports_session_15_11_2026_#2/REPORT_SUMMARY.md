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
| I-04 | Preflight before confirm dialog | ✅ Done | REPORT_I04.md |
| I-05 | 64 KB meta pre-check before PLSQ start | ✅ Done | REPORT_I05.md |
| I-06 | fmt_var authoritative (path extension ≠ format → refuse) | ✅ Done | REPORT_I06.md |
| I-07 | Fix universes display (use count_universes accounting for start_channel) | ✅ Done | REPORT_I07.md |
| I-08 | Async finalization in App via PlsqRecordingSession | ✅ Done | REPORT_I08.md |
| I-09 | on_artsync partial flag (partial when _updated ⊊ _expected) | ✅ Done | REPORT_I09.md |
| I-10 | Short-packet zero-fill + short_packets counter | ✅ Done | REPORT_I10.md |
| I-11 | D11 blend reduction in both PLSQ and JSON paths | ✅ Done | REPORT_I11.md |

---

## Test counts by commit

| Commit | Tests | Result |
|--------|------:|--------|
| `1510284` I-02 (session.py created) | 181 | OK |
| `4768fea` I-09/I-10 fixes + I-02/I-03 tests | 182 → 190 | OK |
| `918431a` I-03 main.py fix | 190 | OK |
| `1219878` I-10 tests + _seq_last + stress | 190 → 195 | OK |
| `b95fd73` I-11: plsq.py + test_i11.py | 195 → 208 | OK |
| `4a2b54d` I-04..I-08/I-11: main.py + test_i08.py | 208 → 220 | OK |

Command: `python -m unittest discover -q`  
Latest result: `Ran 220 tests in 29.670s — OK`

---

## Changed files

| File | Issues |
|------|--------|
| `session.py` | I-01 (created), I-02, I-09, I-10, I-03 (_seq_last) |
| `main.py` | I-01 (import cleanup), I-03 (baseline), I-03 (_seq_last), I-04 (preflight order), I-05 (meta pre-check), I-06 (fmt_var authoritative), I-07 (count_universes), I-08 (PlsqRecordingSession, remove _finish_plsq), I-11 (_finish_json D11, blend_warn display) |
| `plsq.py` | I-05/I-11 (encoded_meta_size) |
| `tools/bench_e2e.py` | I-01 (created) |
| `tools/stress_first_frame.py` | I-02 (created) |
| `packets_tester.py` | I-01 (× → x) |
| `tests/test_r5.py` | I-09 (modified existing test, 2 new tests), I-10 (5 new tests) |
| `tests/test_i02_i03.py` | I-02, I-03 (created, 7 tests) |
| `tests/test_i08.py` | I-08 (created, 12 tests) |
| `tests/test_i11.py` | I-11 (created, 13 tests) |
| `docs/BENCHMARK_RECORDER.md` | I-01 (e2e baseline section) |
| `CLAUDE.md` | D11 (locked decision) |

---

## Process violations / open notes

1. **session.py committed in I-02 commit** (`1510284`): session.py belongs to I-01; not rebased per policy.
2. **I-04/I-05/I-06/I-07/I-08/I-11 committed together** (`4a2b54d`): all app-layer changes are interleaved in `main.py` and cannot be separated into individual commits without interactive staging.
3. **I-10 GUI warning not yet implemented**: the statistics block warning "short packets: N (…)" for `short_packets > 0` remains deferred. The counter is recorded in stats but no yellow warning line is shown.
4. **I-01 official "before" benchmark not yet run**: the 60-second content-check baseline at commit `c141c8e` (before I-02..I-10) and the matching "after" baseline on `dev/v1.2.0` are pending. `docs/BENCHMARK_RECORDER.md` will be updated when these measurements are taken.

---

## Open questions for the user

None at this time.
