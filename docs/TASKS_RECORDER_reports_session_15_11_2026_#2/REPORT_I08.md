# Report I-08: Async finalization — App migrated to PlsqRecordingSession

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commit:** 4a2b54d

---

## Goal

Replace the inline PLSQ recording pipeline in `App` (manual
`PlsqWriter` + `PlsqStreamWriter` + `queue` + `_finish_plsq`) with the
`PlsqRecordingSession` state machine from `session.py`.  After this
change there is exactly one PLSQ recording implementation in the
codebase.  Finalization must never block the GUI tick.

---

## What changed

### main.py — App class

**Removed:**
- `self._plsq_sw` — `PlsqStreamWriter` instance
- `self._plsq_writer` — `PlsqWriter` instance
- `self._plsq_queue` — `queue.Queue`
- `self._plsq_part` — `.part` file path string
- `self._plsq_target` — final file path string
- `_finish_plsq()` — entire method deleted

**Added:**
- `self._plsq_session = None` — active `PlsqRecordingSession` or None
- `self._blend_warn = None` — D11 warning string; cleared on new recording
- `_on_plsq_session_complete()` — called by `_refresh` when session reaches
  done/error; populates `last_saved`, `saved_path`, `_blend_warn`

**`_toggle_record` (PLSQ branch):**
```python
self._plsq_session = PlsqRecordingSession(
    self.server, chips=chips, fps=fps, out_path=path,
    spans=spans, stride=stride,
    start_universe=..., start_channel=..., eff_cpu=eff_cpu,
    skip=skip, blend=blend, final_frames=final,
    wait_first_packet=self.wait_var.get(),
    complete_frames=self.complete_frames_var.get(),
    meta_extra=meta_extra,
)
self._plsq_session.start()
if self._plsq_session.state == "error":
    self._err(self._plsq_session.error)
    self._plsq_session = None
    return
```

Stop path: clicking "Stop Record" calls `_plsq_session.request_stop()`.

**`_refresh` (called every 100 ms):**
```python
if self._plsq_session is not None:
    self._plsq_session.poll()           # advances state machine
    if self._plsq_session.state in ("done", "error"):
        self._on_plsq_session_complete()
```

**`_update_buttons`:**
- `finalizing` state disables both the server and record buttons and shows
  `"Finalizing..."` as the button label.

**`_close`:**
```python
if self._plsq_session is not None:
    if self._plsq_session.state in ("waiting", "recording", "finalizing"):
        self._plsq_session.abort()
    self._plsq_session = None
```

**`_finish_recording`** (JSON path only):
Stripped of the PLSQ branch; now handles only the JSON `Recorder`.

---

## State machine transitions (I-08 contract)

```
idle
  └─ start() → waiting | recording     (wait_first_packet flag)
                  └─ request_stop()
                       └─ finalizing   (writer thread completes)
                            └─ done | error
```

`poll()` advances the machine; it never blocks (all I/O is on worker
threads).  `finalize()` on `PlsqWriter` is only called after the writer
thread has exited.

---

## Tests

**tests/test_i08.py** — 12 new tests:

| Class | Tests |
|-------|-------|
| `TestSessionStateTransitions` | 6 — idle, recording, waiting, done result, error, start-twice |
| `TestFinalizeNotCalledEarly` | 1 — slow write loop; finalize must follow thread exit |
| `TestPollIsNonBlocking` | 1 — 20 rapid polls must each take < 50 ms |
| `TestAbort` | 3 — keeps .part file, error state, safe before start |
| `TestRequestStop` | 1 — triggers finalization during recording |

---

## Verification

- `python -m unittest tests.test_i08 -v` → **12 tests, OK**
- `python -m unittest discover -q` → **220 tests, OK**

---

## Could not verify

- Tkinter button label "Finalizing…" and disabled state (requires running
  `main.py` manually with a live Art-Net source).
- `_close()` during active finalization (manual test with long recording).
