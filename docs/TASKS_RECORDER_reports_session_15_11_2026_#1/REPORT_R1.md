# R1 — Core module `plsq.py`

**Date:** 2026-11-15  
**Tests after:** 44 (all pass) — `tests/test_plsq.py`

---

## Goal

A standalone, testable implementation of the PLSQ binary format that the recorder, tools, and future tests share. No Tkinter import allowed; no third-party packages.

---

## What changed

### New file: `mapping.py`

Non-GUI mapping helpers extracted from `main.py` so that `plsq.py` and tools can import them without pulling in Tkinter.

| Symbol | Description |
|---|---|
| `effective_chips_per_universe(setting)` | Returns 128 when `setting == 0` (auto), otherwise returns `setting`. |
| `build_spans(start_universe, start_channel, channels_per_chip, chips, chips_per_universe)` | Returns a list of `(universe, first_channel_in_universe, channel_count, frame_offset)` tuples describing the ArtNet → frame layout. |

### New file: `plsq.py`

Implements `docs/PLSQ_SPEC.md` in full.

**`PlsqError(code, message)`** — exception with a string error code (section 10 of the spec). All reader functions raise `PlsqError`; `validate()` catches and collects.

**`build_meta(...)`** — returns the 27-key `dict` in the fixed order of spec section 5. Accepts named arguments for all meta fields; fills `sequence_id` (UUID4), `recorded_at` (local ISO 8601 with UTC offset), and `format_version = "1"` automatically. Shared by both the JSON and PLSQ writers.

**`PlsqWriter`** — streaming writer:
| Method | Description |
|---|---|
| `open(part_path, chips, fps, start_universe, start_channel, chips_per_universe)` | Writes the fixed 136-byte header; keeps the file open for appending. |
| `append_frame(data: bytes)` | Appends `stride` raw bytes to the payload. |
| `read_frames(start, count)` | Reads frames back from the open file (used by the blend rewrite step). |
| `rewrite_frames(start, frames)` | Seeks and overwrites frames in-place (used by loop blend finalization). |
| `finalize(meta_dict)` | Writes the meta JSON block, sets the `FINALIZED` flag in the header, flushes, and closes. |
| `abort()` | Closes the file without finalizing; leaves the `.part` file in place. |

**Reader functions:**
- `read_info(path)` — reads header + meta only, never touches the payload.
- `read_frames(path, start, count)` — reads a range of frames from a finalized file.
- `validate(path)` — runs all structural checks and returns a list of problem strings; raises nothing for a bad file.

**CRC-32** is verified on both the header block and the meta block at read time.

---

## How it was verified

**Unit tests — `tests/test_plsq.py` (44 tests):**

| Class | Coverage |
|---|---|
| `TestRoundTrip` | Write → finalize → read back byte-identical for 1-universe and 2-universe layouts, specific frame ranges. |
| `TestRewriteFrames` | Rewrite the first frame; verify only target frames change. |
| `TestMapping` | `effective_chips_per_universe` boundary values; `build_spans` single- and two-universe layouts; `count_universes`. |
| `TestLargeFile` | 64,000 chips × 120 frames written and read back; throughput > 150 MB/s (well above the 10× real-time target of ≈ 15 MB/s). |
| `TestErrorCodes` | 24 tests — each error code from spec section 10 is produced by a deliberately corrupted or truncated file: bad magic, bad header size, unsupported version, unsupported layout, `COMPRESSED` flag, header CRC mismatch, not finalized, payload truncated, meta out of bounds, meta CRC mismatch, meta parse error, missing meta key, meta mismatch (chips_count, frame_stride, universes_count), out-of-range field values (chips, fps, start_channel), I/O error on nonexistent file. |
| `TestBuildMeta` | 27 keys present in spec order; all values are strings; `file_format` default; duration calculation. |
| `TestAbort` | `abort()` closes the file and leaves the `.part` path on disk. |

---

## What could not be verified

Nothing in this task is Windows-specific or GUI-specific. All behavior is exercised by the unit tests.
