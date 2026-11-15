# Report I-03: lost_packets baseline at recording start

**Date:** 2026-11-15  
**Branch:** dev/v1.2.0  
**Environment:** Windows 11 10.0.26300, Python 3.13.5, Intel Core i7-13700H  

---

## Goal

Ensure that sequence-gap losses detected before recording starts (while the server is idle or the user is configuring the session) do not appear in the PLSQ or JSON file meta.

---

## What changed

### session.py — PlsqRecordingSession.start()

```python
# Reset per-universe sequence tracking so the gap between pre-recording
# and recording packets does not create false in-recording loss counts.
with self._server.lock:
    self._server._seq_last.clear()

# Snapshot lost_packets baseline at recording start
self._lost_packets_start = self._server.snapshot()[6]
```

In `_complete_finalize()`:
```python
lost_total = self._server.snapshot()[6]
lost_recording = max(0, lost_total - self._lost_packets_start)
```

### main.py — App._toggle_record() (commit `918431a`, supplemented in `1219878`)

```python
# I-03: reset sequence tracking and snapshot baseline at recording start
with self.server.lock:
    self.server._seq_last.clear()
self._lost_packets_start = self.server.snapshot()[6]
```

In `_finish_recording()`:
```python
_, _, _, _, _, _, _lost_total, _ = self.server.snapshot()
lost_pkts = max(0, _lost_total - getattr(self, "_lost_packets_start", 0))
```

Both `_finish_plsq` and `_finish_json` receive the baselined value.

### _seq_last reset

`ArtNetServer._seq_last` maps `port_address → last_sequence_number`. Without reset, the gap between the last pre-recording packet and the first recording packet (possibly from a new sender session with sequence restarted at 1) could be counted as in-recording losses.

After the reset:
- The first packet from each universe during recording has no predecessor → no gap counted.
- Subsequent packets in the same recording session track sequence normally.

The reset is applied in both `PlsqRecordingSession.start()` (session.py) and `App._toggle_record()` (main.py).

### When the baseline is taken (added in the final pass, A2/C3)

The `lost_packets` baseline and the `_seq_last` reset happen **when recording is started**: when the user presses
Record (`App._toggle_record()`, JSON path) or when `PlsqRecordingSession.start()` is called (PLSQ path, also called
from `_toggle_record()`). They are **not** taken when the sampling clock starts. With "Start recording on first
packet" on, `Recorder._capture()` first waits for the first assembled frame (`committed_frame is not None`) and only
then sets its clock `t0`. Sequence gaps detected between pressing Record and that first assembled frame are counted
in the file's `lost_packets`, although no frame of the file was sampled in that interval.

Since the final pass (commit `bd2513e`) both paths call the same two functions in `session.py`:
`start_loss_tracking(server)` (reset + baseline, at start) and `recording_lost_packets(server, baseline)` (at the
end). `tests/test_i02_i03.py` calls these functions directly, and
`TestLostPacketsRealServer.test_gap_before_start_excluded_gap_after_counted` checks the behavior over real UDP with a
real `ArtNetServer`: a gap before `start()` is not in the file's `lost_packets`, a gap after `start()` is.

---

## Tests

**File:** `tests/test_i02_i03.py` — `TestLostPacketsBaseline`

| Test | Checks |
|------|--------|
| `test_plsq_pre_recording_losses_excluded` | Pre-seeded `server.lost_packets = 100`; no additional losses during recording; `result()["lost_packets"] == 0`; file meta `int("lost_packets") == 0` |
| `test_plsq_in_recording_losses_counted` | Pre-seeded 100; +5 injected after `start()`; `result()["lost_packets"] == 5` |
| `test_json_pre_recording_losses_excluded` | Original version: called `build_meta` directly with a value the test computed itself (`max(0, (50+3) - 50)`), so no application code was checked. **Rewritten in the final pass (A2):** drives `start_loss_tracking` / `recording_lost_packets`, the same functions the JSON path of `main.py` uses. |
| `TestLostPacketsRealServer.test_gap_before_start_excluded_gap_after_counted` | Added in the final pass (A2): real `ArtNetServer` over UDP + `PlsqRecordingSession`; gap of 3 before start and a seq jump across the start are excluded, gap of 2 after start is in the file meta. |

Command: `python -m unittest tests.test_i02_i03 -v`  
Result (session #2): 7 tests, all pass. Result after the final pass: 8 tests, all pass.

---

## Limitations

1. **main.py fix not covered by automated test.** The `App._toggle_record → _finish_recording` flow requires the full Tkinter GUI and cannot be exercised in a unit test without a display. The fix was verified by code review only. This path will be replaced by `PlsqRecordingSession` in I-08, at which point the session.py tests cover it directly.

2. **_seq_last reset in main.py not independently tested.** The reset was added alongside the baseline snapshot in both files. Its effect would only be observable if a sender restarts (sequence resets to 1) between pre-recording and recording phases, which is not covered by the current test suite.

**Update (final pass):** the calculation is now shared (`start_loss_tracking` / `recording_lost_packets`), so the
functions used by `main.py` are tested; the Tkinter call sites themselves are still not tested. The `_seq_last` reset
is covered by the real-UDP integration test on the `PlsqRecordingSession` path (seq jump 6 → 20 across the start is
not counted).

---

## Commits

```
918431a I-03: snapshot lost_packets baseline at recording start in main.py
4768fea I-09/I-10: fix FrameAssembler on_artsync partial flag and short-packet handling
         (contains session.py with PlsqRecordingSession._lost_packets_start)
1219878 I-10 tests, _seq_last reset, 500-uni stress x50, stress_first_frame.py
         (adds _seq_last.clear() to both main.py and session.py)
```
