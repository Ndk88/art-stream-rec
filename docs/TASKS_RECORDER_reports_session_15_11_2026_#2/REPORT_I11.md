# Report I-11: D11 blend reduction in PLSQ and JSON paths

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commits:** b95fd73 (plsq.py + tests), 4a2b54d (main.py)

---

## Goal

When a recording is stopped early and the number of exported frames
(`final`) is less than the requested loop-blend length (`blend`), apply a
shortened blend `blend_applied = min(blend, final)` in both the PLSQ and
JSON output paths.  Store `blend_applied` in the file meta as
`post_loop_blend_frames` and display a persistent warning in the
statistics block.  If `final <= 0` it remains an error.

---

## Decision reference

Locked decision D11 (CLAUDE.md):
> When `final < blend`, apply `blend_applied = min(blend, final)` in both
> formats (PLSQ and JSON), write `blend_applied` to
> `post_loop_blend_frames`, and show a persistent warning line:
> *"Loop blend reduced from X to Y frames: the recording is too short"*.

---

## What changed

### plsq.py

Added `encoded_meta_size(meta_dict)` — returns byte length of meta as
compact UTF-8 JSON (reuses `_encode_meta`).  Used by the I-05 pre-check;
no D11 logic here (D11 for the PLSQ path was already implemented in
`session.py` via `PlsqRecordingSession`).

### session.py (already implemented in prior session)

`PlsqRecordingSession._run()` already computed:
```python
blend_applied = min(blend, final)
```
and stored `blend_warning` in `result()` when `blend_applied < blend`.

### main.py — `_finish_json`

Before this fix, `_finish_json` passed the original `blend` value straight
to `postprocess()`, which would raise `ValueError` when `blend > final`.

After:

```python
avail = len(raw) - job["skip"]
final = (min(job["final"], avail - job["blend"])
         if job["final"] else avail - job["blend"])

if final <= 0:
    # keep raw in memory, show error
    self.pending = dict(base, frames=list(raw), skip=0, blend=0, ...)
    self._err("Not enough recorded frames ...")
    return

# D11: clamp blend
blend_applied = min(job["blend"], final)
if blend_applied < job["blend"]:
    self._blend_warn = (
        "Loop blend reduced from %d to %d frames: the recording is too short"
        % (job["blend"], blend_applied)
    )

frames = postprocess(raw, job["skip"], blend_applied, final)
self.pending = dict(..., blend=blend_applied, ...)
```

### main.py — `_refresh`

D11 warning appended to stats block with `"warn"` (yellow) tag:

```python
if self._blend_warn:
    L.append(("", None))
    L.append((self._blend_warn, "warn"))
```

`_blend_warn` is cleared at the start of each new recording.

### main.py — `_on_plsq_session_complete`

For PLSQ recordings, `blend_warning` from `sess.result()` is transferred
to `self._blend_warn` for display.

---

## Tests — tests/test_i11.py (13 new tests)

### PLSQ path — `TestD11BlendReductionPlsq`

| Test | Scenario |
|------|----------|
| `test_final_less_than_blend_byte_identical` | 15 frames, blend=10 → final=5, blend_applied=5 |
| `test_final_one_blend_ten` | 11 frames, blend=10 → final=1, blend_applied=1 |
| `test_full_blend_unchanged` | 20 frames, blend=5 → standard blend, no D11 |
| `test_final_zero_gives_zero_frames_written` | 10 frames, blend=10 → final=0 → error path |
| `test_frames_written_matches_final` | 15 frames, blend=10 → frames_written=5 |

### JSON path — `TestD11BlendReductionJson`

| Test | Scenario |
|------|----------|
| `test_postprocess_with_clamped_blend_succeeds` | blend_applied=5 when final=5 |
| `test_postprocess_unclamped_raises` | original blend > final → ValueError |
| `test_postprocess_final_zero_raises` | final=0 → ValueError |
| `test_weight_zero_gives_pure_tail` | frame[0] of blend = tail only (w=0) |
| `test_plsq_json_byte_identical_with_clamped_blend` | PLSQ and JSON output match |

### Session blend_warning — `TestD11BlendWarnPlsqSession`

| Test | Scenario |
|------|----------|
| `test_blend_warning_set_when_reduced` | final=5, blend=10 → warning set, frames_written=5 |
| `test_no_warning_when_full_blend_applied` | final=15, blend=5 → no warning |
| `test_blend_warning_message_format` | warning contains both original and applied values |

---

## Verification

- `python -m unittest tests.test_i11 -v` → **13 tests, OK**
- `python -m unittest discover -q` → **220 tests, OK**

---

## Could not verify

- Live Tkinter stats block display of the yellow warning line (requires
  running `main.py` manually with a recording that triggers D11).
