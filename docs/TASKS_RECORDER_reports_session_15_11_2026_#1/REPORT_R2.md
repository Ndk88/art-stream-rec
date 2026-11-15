# R2 — Shared meta and the JSON writer

**Date:** 2026-11-15  
**Tests after:** 28 (all pass) — `tests/test_r2.py`  
**Total tests at this point:** 163

---

## Goal

One meta builder (`build_meta()` in `plsq.py`) used by both the JSON and PLSQ writers, so that all 27 required keys always flow through without duplication or divergence.

---

## What changed

### `main.py`

**`APPV = "1.2.0"`** — minor version bump.

**`write_json(path, meta, frames)`** rewritten:
- Accepts the full 27-key `dict` from `build_meta()` (not a hand-assembled header).
- Raises `ValueError` if `int(meta["input_mapping_universes_count"]) > 2` (D3: JSON blocked for 3+ universes).
- Writes keys in the fixed spec §5 order, then `"sequence": {"data": [...]}` with one frame per line.
- Returns the written file size in bytes.

**`fmt_num()` helper removed** — no longer needed.

**`_toggle_record()`** now captures and stores all mapping parameters into `self._job` at recording start:
```python
self._job = {
    "final": final, "skip": skip, "blend": blend,
    "is_plsq": is_plsq,
    "start_universe": m["start_universe"],
    "start_channel": m["start_channel"],
    "channels_per_chip": m["channels_per_chip"],
    "chips_per_universe": eff_cpu,          # effective value, never 0
    "universes_count": len(spans),
    "model_chips_count": chips,
}
```

**`_finish_recording()`** captures `rec.missing`, `rec.late`, `rec.partial_source_frames`, and `server.snapshot()` lost-packet count before any state is reset, and passes them into the pending dict / meta builder.

**`_save_pending()`** calls `build_meta(file_format="json", ...)` with all 27 parameters. All new keys (`sequence_id`, `recorded_at`, `recorder_version`, stats counters) flow through automatically.

**`effective_chips_per_universe`** imported from `mapping`.

---

## How it was verified

**Unit tests — `tests/test_r2.py` (28 tests):**

| Class | Coverage |
|---|---|
| `TestWriteJsonStructure` | Output parses as valid JSON; has exactly 27 meta keys + `"sequence"`; all meta values are strings; keys appear in spec §5 order before `"sequence"`; `file_format = "json"`; `recorder_version` equals `APPV`; `sequence_id` is a valid UUID string; frame count and stride match the input data; `sequence_duration_sec` correct; two-universe layout allowed; one-universe layout allowed. |
| `TestWriteJsonThreeUniverseBlock` | `universes_count = 3` raises `ValueError`; `universes_count = 500` raises `ValueError`; the file is not created when the error is raised (write is atomic — only written on success). |
| `TestWriteJsonRoundtrip` | 104-chip (416-byte stride) JSON is written, loaded with `json.load`, frames read back and verified: `round(byte/255, 3)` values are correct; skip and blend counts survive the meta round-trip. |
| `TestBlueprintCompat` | Old Blueprint parser keys unchanged: `model_name`, `model_chips_tag`, `model_chips_count` (string int), `sequence_fps` (string int), `sequence_duration_sec` (string float), `sequence_frame_count` (string int), `sequence_frame_stride` (string int); `sequence.data` is a list (not a string); all float values in 0..1. |

---

## What could not be verified

- **Blueprint parser actually ignoring the new keys** — this requires the Unreal Engine editor and an existing Blueprint that reads JSON sequences. The user should load a file saved after this update with the existing parser and verify it loads without errors. The test verifies that the keys used by the old parser keep their names and formats.
- **`recorded_at` UTC offset correctness in all Windows time zones** — `datetime.now(timezone.utc).astimezone()` is used; this is stdlib behavior. Verified to produce a valid ISO 8601 string with offset; not tested against every UTC offset.
