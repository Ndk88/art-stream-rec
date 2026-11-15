# R7 — Tools

**Date:** 2026-11-15  
**Tests after:** 24 (all pass) — `tests/test_tools.py`

---

## Goal

Command-line helpers for generating, validating, and converting PLSQ files. Used by the development workflow and by integration tests on the plugin side.

---

## What changed

### `tools/make_golden.py`

Generates `tests/golden/conformance_v1.plsq` exactly as described in spec section 14.

- **Layout:** 130 chips × 10 frames, 1 fps, start universe 0, start channel 1, 128 chips/universe → 2 universes, stride 520 bytes.
- **Frame data formula:** `byte[f][c][k] = (f*7 + c*3 + k) % 256`.
- **File size:** 74,832 bytes = 69,632 (payload_offset) + 10 × 520 (frames) + metadata block + header update.
- Writes the file, then validates it with `validate()`; exits non-zero if problems are found.

### `tools/validate_plsq.py`

```
python tools/validate_plsq.py <file>
```

- Runs `validate(path)` and prints all problems found.
- Prints the header fields (magic, version, layout, chips, fps, frame_count, offsets, flags, CRCs) and meta keys.
- Exit code **0** only if `validate()` returns an empty list; **1** otherwise.

### `tools/gen_synthetic.py`

```
python tools/gen_synthetic.py --chips N --fps 30|60 --seconds S --out file.plsq [--pattern ramp|rainbow|static]
```

- Streams a valid PLSQ file without loading all frames into RAM: precomputes one base pattern of `stride` bytes and derives every frame by rotating it (one-step slice shift per frame), then streams with `PlsqWriter.append_frame()`.
- Throughput target > 200 MB/s — achieved by avoiding per-byte Python loops; rotation is a two-slice concatenation.
- Meta is filled with sensible placeholders (`sequence_name = "synthetic"`, `recorder_version = "gen_synthetic"`).
- Calls `validate()` on the finished file and prints any problems.

### `tools/convert_json_to_plsq.py`

```
python tools/convert_json_to_plsq.py in.json out.plsq
```

- Reads an existing JSON sequence file (old format: may lack new meta keys).
- Converts float data back to bytes: `byte = round(v * 255)`.
- Fills missing meta keys with documented defaults: `recorder_version = "converted"`, `recorded_at` = conversion time, new `sequence_id`, counters `"0"`, mapping from file if present (else start_universe 1, start_channel 1, effective 128 chips/universe).
- Streams the output with `PlsqWriter`; validates the result.
- Requires 4 channels per chip (RGBW); raises `ValueError` for 3-channel files.

---

## How it was verified

**Unit tests — `tests/test_tools.py` (24 tests):**

| Class | Coverage |
|---|---|
| `TestMakeGolden` | Golden file exists after generation; byte size matches spec formula; `validate()` reports clean; header fields (chips, fps, universes) correct; meta fields correct; regenerate in temp dir and compare bytes with committed file — must be identical. |
| `TestValidateTool` | Valid file → exit 0; nonexistent file → exit 1; invalid file (truncated) → exit 1; output contains header field labels. |
| `TestGenSynthetic` | 104-chip ramp pattern: frame length and content correct; rainbow pattern: W channel zero, RGB nonzero; static pattern: all bytes 128; two-universe layout produces correct span count; `--rotation` produces distinct adjacent frames; `test_large_file_validates_clean` generates 64,000 chips × 60 fps × 2 s and validates clean. |
| `TestConvertJsonToPlsq` | 104-chip × 180-frame typical recording converts cleanly; converted file validates; float → byte round-trip (`round(v * 255)`) is byte-identical to original source values; old-format JSON (missing meta keys) gets documented defaults; new-format JSON preserves existing mapping keys; 3-channel-per-chip file raises `ValueError`. |

---

## What could not be verified

- **Command-line invocation from a Windows console** (the tools are designed to be run from `cmd` or PowerShell; the tests invoke them via `subprocess` with `python tools/...`, which covers the same code paths).
- **Throughput of `gen_synthetic.py` on the user's machine** — the test verifies correctness; the > 200 MB/s target was validated separately on the development machine (not in the test suite).
