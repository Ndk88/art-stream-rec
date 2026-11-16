# Session #2 Summary — Issues I-01..I-11

**Branch:** dev/v1.2.0  
**Date:** 2026-11-15  
**Tests after each change:** `python -m unittest discover -q`

---

## Status table

| ID | Description | Status | Report |
|----|-------------|--------|--------|
| I-01 | session.py extraction + bench_e2e | ⚠ Partial: no "before" run (replaced by the deterministic I-02 test); App moved to the session in I-08; recv % fixed in the final pass (A1) | REPORT_I01.md |
| I-02 | First-frame race (Recorder._capture) | ✅ Done | REPORT_I02.md |
| I-03 | lost_packets baseline at recording start | ✅ Done | REPORT_I03.md |
| I-04 | Preflight before confirm dialog | ✅ Done | REPORT_I04.md |
| I-05 | 64 KB meta pre-check before PLSQ start | ✅ Done | REPORT_I05.md |
| I-06 | fmt_var authoritative (path extension ≠ format → refuse) | ✅ Done | REPORT_I06.md |
| I-07 | Fix universes display (use count_universes accounting for start_channel) | ✅ Done | REPORT_I07.md |
| I-08 | Async finalization in App via PlsqRecordingSession | ✅ Done | REPORT_I08.md |
| I-09 | on_artsync partial flag (partial when _updated ⊊ _expected) | ✅ Done | REPORT_I09.md |
| I-10 | Short-packet zero-fill + short_packets counter + GUI warning line | ✅ Done (GUI line added in the final pass, A3, `406afe2`) | REPORT_I10.md |
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
| `d85c145` A1 + `tests/test_bench_parse.py` | full suite not run at this commit; `test_bench_parse` + `test_packets_tester`: 43 | OK |
| `bd2513e` A2: shared loss accounting + real-UDP test | 226 | OK |
| `406afe2` A3: short-packets line + `tests/test_short_packets_line.py` | 231 | OK |
| `0d37bd5` C1 (docs only, before C5) | 231 | OK |

Command: `python -m unittest discover -q`  
Latest result (final pass, after C1): `Ran 231 tests in 30.185s — OK`. The final number for the branch is in REPORT_FINAL.md.

Rows up to `4a2b54d` are from session #2 and were not re-run in the final pass.

---

## Changed files

| File | Issues |
|------|--------|
| `session.py` | I-01 (created), I-02, I-09, I-10, I-03 (_seq_last) |
| `main.py` | I-01 (import cleanup), I-03 (baseline), I-03 (_seq_last), I-04 (preflight order), I-05 (meta pre-check), I-06 (fmt_var authoritative), I-07 (count_universes), I-08 (PlsqRecordingSession, remove _finish_plsq), I-11 (_finish_json D11, blend_warn display) |
| `plsq.py` | I-05/I-11 (encoded_meta_size) |
| `tools/bench_e2e.py` | I-01 (created); final pass: A1 (recv %), Win32 CPU/memory fix, A4 (torn diagnostics) |
| `packets_tester.py` (final pass) | A1: per-process sent counts, final line per process |
| `session.py` (final pass) | A2 (`start_loss_tracking`, `recording_lost_packets`), A3 (`short_packets_warning`, session result keys) |
| `main.py` (final pass) | A2 (shared loss functions in the JSON path), A3 (short-packets line) |
| `tests/test_i02_i03.py` (final pass) | A2 (JSON test rewritten, real-UDP integration test) |
| `tests/test_bench_parse.py` | A1 (created, 5 tests) |
| `tests/test_short_packets_line.py` | A3 (created, 5 tests) |
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
3. ~~**I-10 GUI warning not yet implemented**~~ (done in the final pass, A3, `406afe2`): the statistics block warning "short packets: N (…)" for `short_packets > 0` remains deferred. The counter is recorded in stats but no yellow warning line is shown.
4. **I-01 "before" benchmark not run**: no compatible run exists at `c141c8e`; the I-02 race is proven by the
   deterministic test (REPORT_I02.md). The official 60 s "after" matrix was run in the final pass (B1, see
   `docs/BENCHMARK_RECORDER.md`).
5. **Final pass: `.bench_out` logs committed by mistake** in `d85c145` (missing newline at the end of
   `.gitignore`); removed by the follow-up commit `3360538` instead of amending (history is not rewritten).
6. **Final pass: one commit outside the item list**: `6eacf3d` fixes the Win32 CPU/memory helpers of `bench_e2e`
   (they always returned 0), needed so that B1 does not report false zeros.

---

## Open questions for the user

1. **Torn frames with the test source at 32000/60 (27) and 64k/60 +artsync+stagger (3,516)**: not explained;
   facts and one hypothesis each in `docs/BENCHMARK_RECORDER.md`. Whether this happens with a real ArtSync source
   is unknown until M2.
2. **The first recorded frame can be a partial commit.** `wait_first_packet` waits for any `committed_frame`; in
   both torn cases above the first torn frame was frame #0. Should the recorder wait for the first *complete*
   commit instead? Not changed without your decision.
3. **`lost_packets` includes losses between pressing Record and the first assembled frame** (REPORT_I03.md). Is
   that the intended window, or should the baseline move to the clock start?
4. **`short_packets` is not stored in the file meta** (only shown in the GUI and returned in the session result).
   D8 says every field the recorder knows is in the meta; adding a key changes `docs/PLSQ_SPEC.md`, so it was not
   done. Should it be added?
5. **Manual checks not done yet** (by the user): M1 (in particular items 1 and 8), M2 (real Madrix /
   TouchDesigner), M3 (Blueprint JSON parser), M4 (exe build, `.bat` files) from
   `docs/manual_tests/MANUAL_CHECKS_session_15_11_2026_#2.md`.
