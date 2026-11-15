# Errata: session #1 reports

Corrections to `docs/TASKS_RECORDER_reports_session_15_11_2026_#1/`.  
The original files are not edited — corrections go here.

---

## 1. R6: "Windows not tested" for console output paths

The session #1 R6 report noted that Windows-specific behaviour was not verified for some output paths.

**Finding in session #2 (I-01, commit `c141c8e`):** `packets_tester.py` line 295 contained the Unicode character `×` (U+00D7, MULTIPLICATION SIGN). On Windows consoles with encoding CP1251 (the default on some Russian-language Windows installations), printing this character via `print()` raises:

```
UnicodeEncodeError: 'charmap' codec can't encode character '\xd7' in position 32: character maps to <undefined>
```

This caused the `--artsync --stagger-ms` extra matrix run in `bench_e2e` to crash on startup before sending any packets. The character was replaced with ASCII `x`.

**Affected path:** `packets_tester.py`, function `main()`, the stagger log line.  
**Fix:** `c141c8e I-01: fix packets_tester Unicode crash, fix _parse_sent, add e2e baseline doc`

---

## 2. No other errata at this time

Additional findings will be appended here as session #2 work continues.
