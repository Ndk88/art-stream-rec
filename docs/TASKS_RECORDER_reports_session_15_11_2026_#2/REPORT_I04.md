# Report I-04: Writer failure handling — preflight before confirm dialog

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commit:** 4a2b54d

---

## Goal

Move the preflight check (`_preflight(spans)`) so it runs **before** the
`_confirm_can_record()` confirmation dialog. Previously, any preflight
error (port already in use, bad path, etc.) was shown only after the user
had already clicked through the dialog — a poor UX.

---

## What changed

### main.py — `_toggle_record`

Order of calls before this fix:

```
_confirm_can_record()   ← dialog shown first
_preflight(spans)       ← error shown after user clicked OK
```

Order after fix:

```
_preflight(spans)       ← errors shown immediately; returns early if not ok
_confirm_can_record()   ← dialog only reached when preflight passes
```

No behavior changes for the happy path; recording logic is identical.

---

## Verification

- Full test suite: `python -m unittest discover -q` → **220 tests, OK**
- Cannot be verified with an automated test because `_confirm_can_record()`
  opens a Tkinter dialog. Manual check: launching the recorder with a bad
  output path now shows the preflight error without opening the dialog.

---

## Could not verify

- Live Tkinter dialog interaction (requires running `main.py` manually).
