# R4 — Streaming PLSQ recording pipeline

**Date:** 2026-11-15  
**Tests after:** 18 (all pass) — `tests/test_r4.py`  
**Total tests at this point:** 181

---

## Goal

Record any installation size (up to 64,000 chips / 60 fps) to a PLSQ file while recording, without holding the full recording in RAM. Post-processing (skip, blend) is applied during and after streaming with no memory growth.

---

## What changed

### `main.py` — `_blend_frames(tail_frames, head_frames)`

Pure-Python cross-fade helper used by both `postprocess()` and `PlsqStreamWriter`.

```python
out[i][j] = round(tail[i][j] * (1 - i/N) + head[i][j] * (i/N))
```

For 64,000 chips × 30 blend frames = 7.68 M iterations ≈ 0.08 s — well under the 10 s safety limit.

### `main.py` — `PlsqStreamWriter`

A writer thread that consumes frames from a `queue.Queue`:

| Phase | Behavior |
|---|---|
| **Skip** | First `skip` frames are dropped; never passed to the writer. |
| **Delay-line** | A `deque(maxlen=blend)` holds the last `blend` frames. Frames that roll out are written to the PLSQ file via `PlsqWriter.append_frame()`. |
| **Sentinel** | `None` in the queue signals that the recorder has stopped. |
| **Blend finalization** | On sentinel: if `blend > 0`, reads the first `blend` frames back from the `.part` file (`PlsqWriter.read_frames()`), computes the cross-fade with the deque tail, rewrites them in place (`PlsqWriter.rewrite_frames()`). Times the operation; sets `error` if it exceeds 10 s. |
| **Tracking** | `frames_written`, `bytes_written` are updated after each frame write. |
| **Error** | `OSError` or blend timeout sets `self.error`; the caller should call `pw.abort()`. |

### `main.py` — `Recorder`

- `start()` gains `out_queue=` parameter.
- When `out_queue` is set, `_capture()` calls `out_queue.put_nowait(frame)` instead of appending to `self.frames`.
- On `queue.Full`: sets `self.error = "Queue overflow: disk too slow..."` and exits.
- `frames_captured` counter increments regardless of path.

### `main.py` — `App`

**`_toggle_record()` for PLSQ path:**
- Checks for leftover `.part` file → `ChoiceDialog` if found.
- Checks blend delay-line size: `blend × stride > 1 GiB` → error (not started).
- Opens `PlsqWriter` on `.part` path.
- Creates `queue.Queue(maxsize=max(300, int(2*fps)+20))`.
- Creates and starts `PlsqStreamWriter`; passes queue to `Recorder.start(out_queue=...)`.
- Stores `_plsq_sw`, `_plsq_writer`, `_plsq_queue`, `_plsq_part`, `_plsq_target` on `App`.

**`_finish_recording()` → `_finish_plsq()` or `_finish_json()`** based on `job["is_plsq"]`.

**`_finish_plsq()`:**
1. Sends `None` sentinel to the queue (timeout 2 s).
2. Joins the writer thread (timeout 30 s).
3. On writer error: calls `pw.abort()`, keeps `.part`, shows error.
4. On 0 frames written: same.
5. Otherwise: `build_meta(file_format="plsq", ...)` → `pw.finalize(meta)` → `os.replace(part, target)`.

**`_refresh()`:** checks `_plsq_sw.error` every 250 ms; stops recorder on failure. Shows `"REC PLSQ N frames  queue: N  written: N MB"`.

**`App._close()`:** stops recorder → `_finish_recording()` handles PLSQ finalization before the window is destroyed.

---

## How it was verified

**Unit tests — `tests/test_r4.py` (18 tests):**

| Class | Coverage |
|---|---|
| `TestBlendFrames` | `i=0` (weight 0): output equals `tail`; `i=n-1` (weight 0.5 for n=2): midpoint; identical inputs produce identical output; output length and byte width match input. |
| `TestStreamEqualsPostprocess` | For 7 `(skip, blend, total)` combinations — `(0,0,20)`, `(5,0,20)`, `(0,5,20)`, `(3,7,30)`, `(0,30,100)`, `(0,0,1)`, `all_same_frames` — stream output is byte-identical to `postprocess()` on the same random frames. |
| `TestManualStop` | When only `skip` frames arrive → `frames_written == 0`; partial capture (10 frames with skip=2, blend=3) produces the same output as `postprocess()`. |
| `TestQueueOverflow` | Pre-fills a size-1 queue; starts `Recorder` with `out_queue`; after 0.1 s `rec.error` is set and contains "overflow". |
| `TestPlsqValidates` | 30-frame output written via `PlsqStreamWriter` passes `validate()` with no problems; `frames_written` equals the file's frame count. |
| `TestBytesWritten` | `bytes_written == frames_written × stride` with skip=0,blend=0 and with skip=5,blend=0. |

---

## What could not be verified

- **Actual disk-full scenario on Windows** — queue overflow is tested (pre-filled queue triggers `rec.error`); a real disk-full during `PlsqWriter.append_frame()` would trigger the writer's `OSError` path and set `sw.error`, which `_refresh()` checks. This path was not triggered with a real full disk.
- **GUI statistics display during a live PLSQ recording** — the `"REC PLSQ N frames  queue: N  written: N MB"` line requires a running Tkinter window; not verified without a Windows live session.
- **`.part` leftover dialog** — the `ChoiceDialog` for an existing `.part` file requires the GUI; not verified without a Windows live session.
- **64,000 chips × 60 fps × 30 s end-to-end streaming** — the streaming test uses 100 small frames; the design ensures constant memory but the full-load test with a synthetic source was not part of the automated suite.
