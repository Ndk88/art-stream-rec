# R5 — Receiver performance, complete source frames, preflight, compact statistics

**Date:** 2026-11-15  
**Tests after:** 29 (all pass) — `tests/test_r5.py`  
**Total tests at this point:** 135

---

## Goal

A receive path that survives 30,000 packets/s with no torn frames, counts lost packets, blocks recording on bad universe coverage, and keeps the statistics block readable at 500 universes.

---

## What changed

### `main.py` — `ArtNetServer`

**Fast receive loop** (`_loop()`):  
- `recvfrom_into` into a preallocated `bytearray(2048)` with `memoryview` — zero per-packet allocation.
- `SO_RCVBUF` requested at 32 MiB before the first receive; actual value read back via `getsockopt` and stored in `rcvbuf_actual`.
- `struct.unpack_from` on the preallocated buffer — no object creation per packet.
- `ArtSync` (OpCode `0x5200`) now forwarded to the frame assembler.

**Lost-packet counting:**  
Per-universe `_seq_last` dict. When a universe packet arrives with a non-zero sequence byte: `expected = last % 255 + 1`. If `received != expected`, gap size = `(received - expected) % 255`. Gaps ≤ 128 → `lost_packets += gap`; gaps > 128 treated as reorders (not counted). Documented as approximate.

**`snapshot()` expanded to 8-tuple:**  
`(latest, ustats, total, ignored, nbytes, last_time, lost_packets, rcvbuf_actual)`

**`set_assembler(asm)`** — thread-safe setter; called at recording start and stop.

### `main.py` — `FrameAssembler`

New class. Configured with `start(spans, stride)` before recording begins.

**Working frame buffer:** a `bytearray(stride)` written by the receive thread at span offsets.

**Commit logic** (atomic `bytes` swap via GIL):  
- **(a) All-universes commit:** when every mapped universe has been updated since the last commit, `self.committed_frame = bytes(working)`. GIL ensures the reference swap is atomic; the sampler reads the reference without a lock.
- **(b) ArtSync commit:** `on_artsync()` commits if any mapped universe data has arrived since the last commit.
- **(c) Partial commit:** a universe arrives for the second time before all others are updated — commit what exists, increment `partial_count`, reset the seen-set.

**`enabled` flag:** when `False`, all universe updates are accepted but no commits occur; the sampler falls back to assembling from latest per-universe values directly (backward-compat mode).

### `main.py` — `Recorder`

- `start()` gains `assembler=` parameter; when set, `_capture()` reads `assembler.committed_frame` instead of assembling from `server._latest`.
- `partial_source_frames` property added.

### `main.py` — `App`

- **"Wait for complete source frames (recommended)"** checkbox (default on).
- **Preflight check** in `_toggle_record()`: if packets are already arriving, compares expected universes (from spans) with received. Missing expected → error + blocked start. Unexpected inside range → warning (non-blocking).
- **Compact statistics** in `_refresh()`: when > 16 universes arrive, shows a summary (count, range, avg/min/max pkt/s, consistency check) plus up to 5 irregular universes instead of the full per-universe list.
- `APPV = "1.1.0"`.

---

## How it was verified

**Unit tests — `tests/test_r5.py` (29 tests):**

| Class | Coverage |
|---|---|
| `TestFrameAssemblerSingleUniverse` | Initial state; first complete set commits; `enabled=False` suppresses commit; partial count is zero after a full commit. |
| `TestFrameAssemblerTwoUniverses` | No commit after only one universe; commit after both; `enabled=False`; ArtSync triggers partial commit; frame data is correct after commit. |
| `TestCommittedFrameAtomicity` | Stress test: reader thread reads `committed_frame` while writer thread commits new frames at high rate for 0.5 s. Verifies no torn frame is ever observed (no length change, no mix of two distinct values mid-frame). |
| `TestLostPacketCounting` | No gaps → `lost_packets = 0`; single gap of 3 → `lost_packets = 3`; multiple gaps across two universes; gap > 128 treated as reorder (not counted); sequence wraps 255 → 1 correctly; seq = 0 disabled (must not affect count). |
| `TestSnapshotArity` | `server.snapshot()` returns exactly 8 values. |
| `TestRecorderWithAssembler` | With assembler enabled, frames before first commit are counted `missing`. |
| `TestStaggerWithArtSync` | Integration test: `ArtNetServer` + `FrameAssembler`, 15 frames with 2 ms stagger between universes, ArtSync after each frame. Verifies `partial_source_frames == 0` — the assembler never sees a repeated universe when ArtSync resets the seen-set. |

**Benchmark (R8 re-measure after R5):** 100% packet receive rate at all 6 matrix points, 0 lost, 0 late frames, peak CPU 2.14 s/10 s (−18% vs baseline).

---

## What could not be verified

- **GUI: preflight dialog appearance and blocking behavior** — the test covers the logic; the error display in the statistics block requires a running Tkinter window on Windows.
- **Compact statistics readability at 500 universes** — the formatting was designed and unit-tested for the compact path; visual appearance in the stats widget was not verified without a live session.
- **"Wait for complete source frames" checkbox UX** — toggling the checkbox and observing the recorder behavior with a real Art-Net source was not done in an automated test.
