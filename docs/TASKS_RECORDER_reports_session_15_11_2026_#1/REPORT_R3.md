# R3 — GUI changes

**Date:** 2026-11-15  
**Tests after:** 181 (all pass, unchanged — GUI-only changes)

---

## Goal

Expose the locked decisions (D1–D10) in the interface: fixed 4-channel RGBW layout, 30/60 fps only, 1..64,000 chip range, universe validation, format selection, and live derived-value display — without breaking existing behavior or the dark-theme look.

---

## What changed

### Module-level constants (`main.py`)

- `FIELDS` list: removed `"Sequence Fps"` entry — fps is now rendered as radio buttons, not a text entry.
- `MAP_READONLY = {"channels_per_chip"}` added — channels_per_chip is rendered read-only.
- `MAPPING` list: label changed from `"Channels per chip (3 = RGB, 4 = RGBW)"` to `"Channels per chip"`; default value changed from `"3"` to `"4"`.

### New `App` state variables (`__init__`)

| Variable | Type | Purpose |
|---|---|---|
| `fps_var` | `tk.IntVar(value=30)` | Selected fps (30 or 60); replaces `self.vars["sequence_fps"]`. |
| `fmt_var` | `tk.StringVar(value="plsq")` | Selected output format: `"plsq"` or `"json"`. |
| `universes_var` | `tk.StringVar(value="")` | Live "Universes (calculated)" display. |
| `eff_cpu_var` | `tk.StringVar(value="")` | Live effective chips-per-universe display (`"auto → 128"` or a number). |

Default path changed from `sequence_1.json` to `sequence_1.plsq`.

### `_build()` changes

**Sequence Fps**: the FIELDS loop inserts a row of two `tk.Radiobutton` widgets (`30` / `60`, linked to `fps_var`) immediately after the "Sequence Duration" row.

**Channels per chip**: rendered as a `readonly` entry (grey background, no cursor) showing the fixed value `"4"`.

**Chips per universe**: a small frame wraps the entry and a `tk.Label` showing `eff_cpu_var` to its right (`"auto → 128"` when 0 is entered).

**Universes (calculated)**: a new read-only entry row after the MAPPING section, showing `universes_var`.

**Output format selector**: a row of two radio buttons (`.plsq` / `JSON (.json)`) placed before the path row; a note label `"JSON: up to 2 universes only"` follows. The JSON radio button is stored as `self._json_radio` for enable/disable control.

**Path/Browse**: path row shifted down one row; Browse button now uses format-appropriate `filetypes` from `_browse()`.

### `_on_fmt_change()`

New method. Called by both format radio buttons. Swaps the path extension between `.plsq` and `.json` when the current extension is one of the two known extensions; leaves other extensions unchanged.

### `_browse()`

Updated: chooses `defaultextension` and `filetypes` based on `fmt_var.get()`.

### `_choose_new_path()`

Updated: derives extension from the passed `path` argument; uses PLSQ or JSON filter accordingly. Affects the "Save as new file..." dialog when a file already exists.

### `_toggle_record()` changes

- Reads fps from `self.fps_var.get()` (int, always 30 or 60).
- **Validation:**
  - `Model Chips Amount`: must be 1..64,000; error if outside range.
  - `Start Universe`: must be 0..32,767; error if outside range.
  - `start_universe + len(spans) − 1 ≤ 32,767`; error if exceeded.
  - JSON format with ≥ 3 universes: blocked with an error message (even if the user bypassed the disabled radio by typing the extension manually).

### `_refresh()` changes

- Runs live derived-value computation on every 250 ms tick (regardless of recording state):
  - `eff_cpu = effective_chips_per_universe(cpu_raw)`.
  - `n_uni = (chips + eff_cpu − 1) // eff_cpu` (ceiling division).
  - Updates `universes_var` and `eff_cpu_var`.
- When `n_uni ≥ 3`: disables `self._json_radio`; if `fmt_var == "json"`, forces it back to `"plsq"` and calls `_on_fmt_change()`.
- Reads fps from `self.fps_var.get()` for the frame-count derived field.

---

## How it was verified

- `python -c "import main; print('OK')"` — import succeeds; no syntax errors.
- `python -m unittest` — all 181 existing tests pass; no regression.

**Manual checklist (requires Windows GUI — not verified in this session):**

| Item | Expected behavior |
|---|---|
| Fps radio buttons | Clicking 30 or 60 selects the value; recording uses the selected fps. |
| Channels per chip display | Shows `4`, not editable. |
| Chips per universe = 0 | "auto → 128" appears to the right. |
| Chips per universe = 64 | "64" appears to the right. |
| Model Chips Amount = 104 | Universes (calculated) = 1. |
| Model Chips Amount = 128 | Universes (calculated) = 1. |
| Model Chips Amount = 129 | Universes (calculated) = 2. |
| Model Chips Amount = 384 | Universes (calculated) = 3; JSON radio grayed out; fmt forced to .plsq. |
| Model Chips Amount = 64000 | Universes (calculated) = 500. |
| Format switch .plsq → JSON | Path extension changes from `.plsq` to `.json`. |
| Format switch JSON → .plsq | Path extension changes from `.json` to `.plsq`. |
| Browse with .plsq selected | Dialog shows `*.plsq` filter. |
| Browse with JSON selected | Dialog shows `*.json` filter. |
| Record end-to-end (PLSQ) | File saved, validates clean, statistics show MB written. |
| Record end-to-end (JSON, 1 universe) | File saved, parses as JSON. |
| Overwrite / new file dialog | Still works for both formats. |
| Start Universe = 32768 | Error shown, recording blocked. |
| Model Chips Amount = 65000 | Error shown, recording blocked. |

---

## What could not be verified

Everything in the checklist above requires a live Tkinter window on Windows.
