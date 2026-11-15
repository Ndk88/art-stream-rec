# Report I-06: fmt_var authoritative — extension mismatch refused

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commit:** 4a2b54d

---

## Goal

The format radio button (`fmt_var`: "plsq" / "json") is the single
authoritative source of the output format.  If the user types a path
whose extension does not match the selected format, refuse immediately
with an error — before any recording starts.

---

## What changed

### main.py — `_toggle_record` (immediately after resolving `path`)

```python
fmt = self.fmt_var.get()
ext = os.path.splitext(path)[1].lower()
if fmt == "plsq" and ext != ".plsq":
    self._err(
        "Path extension %r does not match the selected format PLSQ. "
        "Either change the extension to .plsq or switch the format to JSON."
        % (ext or "(none)",)
    )
    return
if fmt == "json" and ext != ".json":
    self._err(
        "Path extension %r does not match the selected format JSON. "
        "Either change the extension to .json or switch the format to PLSQ."
        % (ext or "(none)",)
    )
    return
is_plsq = (fmt == "plsq")
```

The old code derived `is_plsq` from the path extension, which silently
accepted mismatches.

---

## Verification

- Full test suite: **220 tests, OK**
- Cannot be verified with an automated test because the check depends on
  Tkinter state (`fmt_var`). Manual check: selecting PLSQ format and
  typing a `.json` path now shows the error without opening any dialog.

---

## Could not verify

- Live Tkinter interaction (requires running `main.py` manually).
