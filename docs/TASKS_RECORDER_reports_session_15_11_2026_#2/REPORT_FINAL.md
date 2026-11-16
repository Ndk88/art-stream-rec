# Final pass: recorder (session #2, items A–D)

**Date:** 2026-11-15 / 2026-11-16  
**Branch:** dev/v1.2.0 (start: `41c3787`), not merged, not pushed, history not rewritten  
**Environment:** Windows 11 10.0.26300, Intel Core i7-13700H, Python 3.13.5  
**Rule for numbers:** every number below comes from a command run in this pass. Anything not measured is marked
"not measured".

---

## 1. What was done

| Item | Commit | Change |
|------|--------|--------|
| A1 | `d85c145` | `bench_e2e._parse_sent`: sent = sum of the final "sent N packets" values of **all** tester processes (fallback: the last periodic total of that process; `None` → `n/a`, no recv %). `packets_tester.py`: worker processes report their counts through shared values; the main process prints a periodic line and a final line for each worker. `bench_e2e` waits for the tester to finish instead of killing it; recv = server packet counter over the whole run. `tests/test_bench_parse.py` (5 tests, including a 4-process output). |
| A1 fixup | `3360538` | `.gitignore` had no newline at the end, so `.bench_out/` merged with the last line and two logs were committed in `d85c145`; fixed and untracked by a new commit (no amend). |
| (extra) | `6eacf3d` | `bench_e2e` Win32 CPU/memory helpers always returned 0 (the `GetCurrentProcess()` pseudo-handle was truncated by default ctypes prototypes). Found while verifying A1; fixed so that B1 does not show false zeros. |
| A2 | `bd2513e` | `session.start_loss_tracking()` / `session.recording_lost_packets()`: one implementation of the I-03 loss window, used by `PlsqRecordingSession` and by the JSON path in `main.py`. `test_json_pre_recording_losses_excluded` now calls these functions; new `TestLostPacketsRealServer` (real `ArtNetServer` over UDP: a gap before `start()` and a seq jump across the start are not in the file's `lost_packets`, a gap after is). |
| A3 | `406afe2` | I-10 GUI: yellow statistics line `short packets: N (first universe U)` when N > 0, live from the assembler and kept from the PLSQ result until the next recording. Pure formatter `session.short_packets_warning()`, `tests/test_short_packets_line.py` (5 tests). README updated. |
| A4 | `8d33854` | `bench_e2e` prints diagnostics for the first torn frame (nearest ring frame, first differing byte and its universe, universes involved, which ring frame each one matches). |
| C3 | `a9718c3` | REPORT_I03: baseline and `_seq_last` reset happen at Record / `start()`, not at the clock start; losses in between go into the file. |
| C4 | `fefa9a8` | REPORT_I01: the original cause of the "0 packets" hang was not established; only diagnostics and timeouts were added. |
| C2 | `3f433cf` | REPORT_I02: "old code result" verified on `c141c8e`; "~20 %" attributed correctly. |
| B1/B2 | `aec3594` | Official 60 s matrix, all 8 points, content check on; "after I-02..I-11" section of `docs/BENCHMARK_RECORDER.md` rewritten with per-point acceptance. |
| C1 | `0d37bd5` | ERRATA session #1: R1, R7, R4, R5, R8, test counts. README: removed "no per-packet allocation". |
| C5 | `38cb64f` | REPORT_SUMMARY: statuses, test table, process notes, open questions. |
| C6 | this commit | This report; note in REPORT_I10 that the GUI line is done. |

---

## 2. Commands and output

### A1: recv % at 10000/60 and 64000/60 (10 s each)

```
python tools/bench_e2e.py --chips 10000 --fps 60 --duration 10 --out-dir .bench_out
  Stopped. Main process sent 61699 packets, 0 send errors.
  Sent/Received  : 61699 / 61699  recv=100.00%
  Torn / Zero    : 0 / 0
  Validate       : OK

python tools/bench_e2e.py --chips 64000 --fps 60 --duration 10 --out-dir .bench_out
  Multi-process: 4 processes
  Stopped. Worker 1 sent 97625 packets, 0 send errors.
  Stopped. Worker 2 sent 97625 packets, 0 send errors.
  Stopped. Worker 3 sent 97625 packets, 0 send errors.
  Stopped. Main process sent 97625 packets, 0 send errors.
  Sent/Received  : 390500 / 390500  recv=100.00%
  Torn / Zero    : 0 / 0
  Validate       : OK
```

For comparison only (quoted, not re-measured in this pass): the previous version of `BENCHMARK_RECORDER.md` showed
101.1 % and 418.7 % for these points with the old parser.

### Win32 CPU/memory helper fix

```
before: cpu 0.0  mem 0          (after a 3*10^7-step Python loop)
after:  cpu delta 1.140625  mem MB 18.72
```

### A4: torn at 10000/60, 60 s, content check, two runs

```
python tools/bench_e2e.py --chips 10000 --fps 60 --duration 60 --out-dir .bench_out   (x2)
run 1: Sent/Received 298699 / 298699 recv=100.00%  Frames written 3600  Torn / Zero 0 / 0  Validate OK
run 2: Sent/Received 298699 / 298699 recv=100.00%  Frames written 3600  Torn / Zero 0 / 0  Validate OK
```

Both logs contain "Reading 3600 frames for content check...", so the check ran. **Result: not reproduced.** The
10000/60 point of the B1 matrix also gave torn = 0 (third run). The 30-minute diagnostic budget was not needed.

### B1: official matrix, 60 s, content check

```
python tools/bench_e2e.py --matrix --duration 60 --out-dir .bench_out

Label                         Sent  Recv%     Lost   Late%   Miss%     Torn     Q%  Valid
1700/30                      26474 100.0%        0   0.00%   0.00%        0     0%     OK
1700/60                      52934 100.0%        0   0.00%   0.00%        0     0%     OK
10000/60                    298699 100.0%        0   0.00%   0.00%        0     0%     OK
32000/60                    945250 100.0%        0   0.00%   0.00%       27     0%     OK
64000/30                    945500 100.0%        0   0.00%   0.00%        0     0%     OK
64000/60                   1890500 100.0%        0   0.00%   0.00%        0     0%     OK
64k/60 +artsync+stagger     427854 100.0%        0   0.00%   0.00%     3516     0%     OK
64k/60 no-assembler        1890500 100.0%        0   0.00%   0.00%        0     0%     OK
```

Full table (partial, zero, queue, CPU, memory), the torn diagnostics and the per-point acceptance are in
`docs/BENCHMARK_RECORDER.md`. Acceptance: 6 PASS, 2 FAIL on torn (32000/60, 64k/60 +artsync+stagger). The output
files in `.bench_out/` were deleted after the run (the deletion was done by the user; the agent's delete command was
blocked by the permission settings). `D:\bench_after_results` was not touched.

The "before" run at `c141c8e` was **not done**: no compatible run exists; the I-02 race is proven by the
deterministic test.

### C2: I-02 tests against the old code (`c141c8e`)

```
git archive c141c8e | tar -x -C %TEMP%\claude_old_c141
(copy current tests/test_i02_i03.py there as tests/test_i02_i03_current.py)
python -m unittest tests.test_i02_i03_current.TestFirstFrameRace -v
FAIL x4: AssertionError: 'waiting' != 'done'
Ran 4 tests in 5.205s
FAILED (failures=4)
```

### C1: loop-blend timing and session #1 test suite

```
_blend_frames timing (session.py):
chips=1700 blend=30 bytes=204000  _blend_frames: 0.02 s
chips=64000 blend=30 bytes=7680000  _blend_frames: 0.88 s
chips=64000 blend=60 bytes=15360000  _blend_frames: 1.75 s

git archive 8ab3747 (end of session #1), python -m unittest discover -q:
Ran 181 tests ... OK   (test_plsq 44, test_r2 28, test_r4 18, test_r5 29,
                        test_packets_tester 38, test_tools 24; TestErrorCodes 25)
```

### Unit tests

```
python -m unittest discover -q
Ran 231 tests in 30.145s
OK
```

(Run right before the C6 commit. C6 changes only documentation; the final run for D is in the hand-over message.)

---

## 3. What was not verified

- **Windows GUI by hand.** The A3 line was checked only by a headless smoke test (`App()` created and withdrawn,
  `_refresh()` called with a fake assembler: the line `short packets: 3 (first universe 5)` appeared with the `warn`
  tag). Nobody looked at the window. Manual checks M1-M4 are still open.
- **A real source** (Madrix 5, TouchDesigner): not tested. All measurements use `packets_tester.py`. Whether the torn
  frames seen at 32000/60 and with artsync+stagger also happen with a real ArtSync source is unknown (M2).
- **exe build** (`pyinstaller --onefile --noconsole main.py`) and `run.bat` / `packets_tester.bat`: not run in this
  pass (M4). The tester change (shared `multiprocessing.Value` for workers) was run from Python only, not from a
  frozen exe.
- **SO_RCVBUF** in the e2e runs: not measured (`bench_e2e` does not print it).
- **Memory/CPU of the tester process**: not measured (the CPU and memory columns are for the recorder side only).
- **The original "0 packets" hang of session #2**: cause not established, not reproduced.

---

## 4. Open questions

See `REPORT_SUMMARY.md`, section "Open questions for the user" (torn with the test source; first frame can be a
partial commit; loss window starts at Record; `short_packets` not in the file meta / D8; manual checks M1-M4).
