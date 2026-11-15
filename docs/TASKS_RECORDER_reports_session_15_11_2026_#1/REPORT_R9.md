# R9 — Documentation and housekeeping

**Date:** 2026-11-15  
**Tests after:** 181 (all pass, unchanged — documentation/config only)

---

## Goal

Bring README and `.gitignore` in line with the final recorder state; remove stale text; link the benchmark report.

---

## What changed

### `.gitignore`

Added three lines:

```
*.plsq
*.part
!tests/golden/conformance_v1.plsq
```

- `*.plsq` — recorded sequences should not be committed.
- `*.part` — in-progress recording files should not be committed.
- `!tests/golden/conformance_v1.plsq` — negation rule keeps the conformance file trackable. Verified: `git status tests/golden/conformance_v1.plsq` shows "untracked" (not "ignored") after the change.

### `README.md`

**Intro paragraph** — now describes both output formats: PLSQ (default, all sizes, streaming) and JSON (1–2 universes only).

**Table of Contents** — item 6 renamed from "Output JSON format" to "Output formats".

**Features bullet** — channel mapping bullet updated: removed "3 = RGB, 4 = RGBW, any 1..512" (D1 locks to 4); added 1..64,000 chip range and live universe count display.

**User interface — Input mapping table** — updated rows:
- "Channels per chip" — now says "Fixed 4 (RGBW, D1). Read-only."
- "Chips per universe" — clarified auto=128 and effective value display.
- Added "Universes (calculated)" row.

**User interface — Output format** — new paragraph describing the radio buttons and JSON/PLSQ switching behavior.

**User interface — File path** — updated to reference `sequence_1.plsq` as default and format-aware Browse dialog.

**Output formats section** (renamed from "Output JSON format"):
- Added "PLSQ (`.plsq`)" subsection: format summary, `.part` file, link to `docs/PLSQ_SPEC.md`, link to streaming section in How it works.
- Retained "JSON (`.json`)" subsection with existing example.

**How it works — Channel mapping** (section 2) — removed generic `512 // channels_per_chip` formula; replaced with RGBW-specific "128 chips by default (512 ÷ 4); override with Chips per universe".

**How it works — Export** (sections 6 and 7):
- Section 6 (JSON) — existing text kept.
- Section 7 (PLSQ streaming) — new: bounded queue, writer thread, delay-line deque, sentinel, blend finalization, atomic rename, `.part` file preservation on error.

**Protection section** — removed the stale bullet "If the path is changed during a recording to a file that exists, the same dialog appears when the recording ends." (the PLSQ pipeline locks the target path at start; for JSON this was an incidental behavior, not a designed feature). Updated example filename `sequence_1.json` → `sequence_1.plsq`.

**Troubleshooting section** — removed stale development verification note ("verified on a virtual display, .bat files not tested"). Added: "Benchmark results … are in `docs/BENCHMARK_RECORDER.md`."

---

## How it was verified

- `python -m unittest` — 181 tests pass.
- `python -c "import main; print('OK')"` — import succeeds.
- `git check-ignore -v tests/golden/conformance_v1.plsq` — output shows the negation rule `!tests/golden/conformance_v1.plsq`; `git status` on that file shows "untracked" (not "ignored").
- `run.bat` and `packets_tester.bat` — both files are unchanged; their logic is verified by the existing test suite.

---

## What could not be verified

- **`pyinstaller --onefile --noconsole main.py`** — PyInstaller is not installed in the current environment. The command is unchanged from the README and no imports that would break bundling were added (all stdlib).
- **`run.bat debug` console output** — unchanged file; only visually verifiable by running it on Windows.
- **Fresh clone test** (`python -m unittest` on a clean checkout) — the test suite requires no network access and no extra packages; this should work on any machine with Python 3.9+.
