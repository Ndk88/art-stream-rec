# Report I-05: 64 KB meta pre-check before PLSQ start

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commit:** 4a2b54d

---

## Goal

The PLSQ meta block is exactly 65 536 bytes.  Refuse to start a PLSQ
recording if the metadata (model name, paths, counters at their maximum
possible values) would not fit — before the user has waited for the whole
recording to complete.

---

## What changed

### plsq.py — new `encoded_meta_size(meta_dict)`

```python
def encoded_meta_size(meta_dict):
    """Return byte length of meta_dict as PLSQ JSON (UTF-8, compact separators)."""
    return len(_encode_meta(meta_dict))
```

Reuses the existing private `_encode_meta` helper so the size measurement
is byte-for-byte identical to what `finalize()` would write.

### main.py — `_toggle_record` (PLSQ branch)

Before creating the session, build a worst-case meta dict with all numeric
counters set to 999 999 999 999, then measure its encoded size:

```python
meta_check = build_meta(
    ...,
    frame_count=999999999999,
    post_skip_frames=999999999999,
    post_blend_frames=999999999999,
    post_loop_blend_frames=999999999999,
)
meta_sz = encoded_meta_size(meta_check)
if meta_sz > 65536:
    self._err("Meta block too large: %d bytes (limit 65 536)..." % meta_sz)
    return
```

---

## Verification

- Full test suite: **220 tests, OK**
- Typical meta for the 104-chip test stand is well under 2 KB; the check
  will only fire if `model_name` or other string fields are extremely long
  (thousands of characters). Automated boundary tests not written (require
  constructing a session with a very long model name).

---

## Could not verify

- Automatic test for the "> 65536 bytes" branch (would require a model
  name > 60 KB long; not a realistic user scenario but could be added).
