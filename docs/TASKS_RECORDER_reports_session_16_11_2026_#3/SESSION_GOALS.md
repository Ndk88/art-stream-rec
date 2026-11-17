# Session #3: recorder 1.3.0 (UI polish + sequence file viewer)

Read first: `CLAUDE.md`, `docs/PLSQ_SPEC.md`, README section "User interface", then this file.
Manual checks that the user will run after you: `docs/manual_tests/MANUAL_CHECKS_session_16_11_2026_#3.md` (read it: it states the expected behavior from the user's side and must match what you build; if you deviate, say so in the report).

Target version: `APPV = "1.3.0"`. Work on the current branch. Do not switch branches, do not push, do not merge, do not rewrite history.

## 0. Scope and rules for this session

- **In scope:** Stage C (cosmetics, items C1..C9) and Stage D (viewing sequence files in the app, items D1..D6). Stage C first, then Stage D.
- **Out of scope (deferred, see `docs/BACKLOG.md`):** close-window dialog, `recover_part.py`, anything about `.part` recovery. Do not implement them.
- The `.plsq` format and `docs/PLSQ_SPEC.md` do not change. The write path of `plsq.py` is not touched; adding read helpers is allowed.
- UI strings in English. Dark theme and existing colors; new colors become named constants next to the existing ones. Errors go to the statistics block (red); popups only for choices about files and unsaved/overwritten data.
- stdlib only at runtime. No new dependencies.
- Tests: `unittest`, **logic only**. Put non-trivial logic into modules without tkinter (for example `uilogic.py`, `fileinfo.py`) so it is testable. The suite does not open windows. No new benchmarks and no new stress runs in this session. The full suite must pass at every commit.
- One commit per item (`C1: ...`, `C4-7: ...`, `D1: ...`). Commit messages in English.
- Numbers in reports only from commands you ran. If something was not measured or not checked, write "not verified".
- Stop after Stage C and again after Stage D: finish the reports of the stage, give a short summary and wait for the user's "continue".
- Reports go to `docs/TASKS_RECORDER_reports_session_16_11_2026_#3/`: `REPORT_C.md`, `REPORT_D.md`, `REPORT_FINAL.md` (what changed, command + output as proof, what was not verified, deviations from this file). README is updated for every user-visible change.
- Optional, only if it takes less than about 15 minutes: `tools/gui_smoke.py` that creates the `App`, runs `update()` a few times, checks that the main widgets exist and the labels equal the list in C4-7, then destroys it. Do not add it to the unittest suite.
- If something here conflicts with the code or the spec, stop and ask instead of guessing.

---

## Stage C. Cosmetics

Numbering follows the user's list.

### C1. Activity indicator ("heartbeat") in the statistics block

**Purpose:** show at a glance that the window is not frozen, at every stage from server start to a successfully saved file.

**Placement:** the very top of the statistics block, above the first text line, always visible (also when the server is stopped). Look: a thin row of small segments (rectangles or dots) with a bright group that moves left to right (see `ui-reworks.jpg`: green segments right above the text area).

**Behavior:**

| State | Look |
|---|---|
| Server stopped | segments dim, no motion |
| Server running, not recording | slow moving bright group, green |
| Waiting for first packet / recording | faster movement, red (recording color of the UI) |
| Finalizing | moving, amber (`WARN_FG`-like) |
| Loading a file for the viewer (D2) | moving, green |
| Just saved (done) | one short bright green flash (about 1.5 s), then back to the state of the moment |

**Implementation requirements (important):**
- The animation runs from the Tk event loop with its own `after` timer (about 50 ms), independent of the 100 ms statistics refresh. **No thread draws it.** If the GUI thread blocks, the indicator must visibly stop; that is the whole point.
- Positions and colors come from a pure function in a tkinter-free module, for example `heartbeat_cells(state, t, n) -> list of intensities 0..1` plus `heartbeat_state(server_running, plsq_state, rec_state, viewer_loading, saved_at, now) -> state name`.
- The indicator must not add noticeable CPU load (a small Canvas, redraw only changed items).
- All long operations stay off the GUI thread (they are already, plus D2 loading, which you write that way).

**Acceptance (automatic):** `heartbeat_cells` is deterministic; length = n; in `idle` all cells are equal and do not change with `t`; in moving states the pattern changes with `t` and repeats with the period of that state; recording is faster than listening; `heartbeat_state` returns the right state for each combination of inputs (table above), including the flash that ends after 1.5 s. **Manual:** M-C1 in the manual checks.

### C2. Hover for buttons

All buttons (Start/Stop Server, Start/Stop Record, Browse, the new toolbar buttons of D1) get a hover color slightly different from the base color: a bit lighter. The red states (Stop Server / Stop Record) get a slightly lighter red. **Disabled buttons do not react to hover.** The hover color follows the current base color (the base changes when the state changes; moving the mouse out restores the **current** base, not the old one).

**Acceptance (automatic):** pure `hover_color(hex, amount)` returns a valid lighter color, never exceeds `#ffffff`, is idempotent in format (`#rrggbb`). **Manual:** M-C2.

### C3. Titles and units

- The title "JSON Fields:" becomes **"Sequence Data:"** (same color and font as before; the blue in the sketch is only a note by the user).
- "Sequence Duration" gets the unit: a small muted label **"sec"** at the right end inside the field, as in the sketch. The label text "(0 = manual)" stays.

**Acceptance:** README and docs updated; the label constants test (C4-7) covers the title. **Manual:** M-C3.

### C4-7. Label capitalization (Title Case) for the left column and the section titles

New labels (exactly):

| Old | New |
|---|---|
| Start channel | Start Channel |
| Channels per chip | Channels Per Chip |
| Chips per universe (0 = auto) | Chips Per Universe (0 = auto) |
| Skip first frames (count) | Skip First Frames (count) |
| Loop blend (frames) | Loop Blend (frames) |
| Input mapping: | Input Mapping: |
| Post-processing (off by default): | Post-Processing (off by default): |
| Output format: | Output Format: |

All other left-column labels already follow Title Case and stay. "Universes (calculated)" stays as it is. The right column (checkboxes, hints) is not changed. Keep one list of labels in code (a constant) so a test can compare it.

**Acceptance (automatic):** a test compares the constant with the table above. **Manual:** M-C4.

### C8. The Post-processing hint must adapt to the window width

Cause: a fixed `wraplength` (470, and a similar fixed 500 in the right block). Make the wrap length follow the width of its container on `<Configure>` (the window is resizable, minimum 1000x860). With a wide window the text must occupy as many lines as needed, not stay at two.

**Acceptance (automatic):** a pure helper that computes the wrap length from the container width (with a lower bound, no negative values). **Manual:** M-C8.

### C9. Numeric fields accept only numbers

| Field | Rule while typing |
|---|---|
| Model Chips Amount, Start Universe, Start Channel, Chips Per Universe, Skip First Frames count, Loop Blend count, UDP port | digits only (non-negative integer); empty allowed |
| Sequence Duration | non-negative decimal; one separator, dot or comma; empty allowed; `6`, `6.0`, `6,5`, `.5`, `6.` are accepted while typing |
| IP | digits and dots only, at most 15 characters; empty allowed |
| Model Name, Model Chips Tag, Sequence Name, **Sequence Version**, Developer Notes | free text, unchanged |

- Invalid characters are **not entered**, including when pasted from the clipboard (a paste that would produce an invalid string is rejected entirely).
- No automatic range correction while typing and no silent replacement of values: the existing range checks at "Start Record" stay (messages as before).
- Values set by the program (the viewer's "copy to fields", D3, and the recorder's own updates of read-only fields) must work with the validation enabled. Tk quirk: a `validatecommand` that changes the variable disables validation; make sure validation stays active after programmatic `set()` (re-arm if needed) and say how you did it in the report.

**Acceptance (automatic):** pure `is_valid_input(kind, proposed_text) -> bool` with kinds `uint`, `ufloat`, `ip`, and a table-driven test: accepted `""`, `"0"`, `"104"`, `"64000"`; rejected `"12a"`, `"-1"`, `" 1"`, `"1 "`, `"1e3"`; ufloat accepted `"6"`, `"6.0"`, `"6,5"`, `".5"`, `"6."`, rejected `"6.5.1"`, `"6,,5"`, `"6.,5"`, `"a"`; ip accepted `"127.0.0.1"`, `"1."`, `"..."` (while typing), rejected `"127.0.0.a"`, `"1234567890123456"`. **Manual:** M-C9.

### Stage C wrap-up

Full suite passes, README updated (labels, units, hover, numeric fields, indicator), `REPORT_C.md`. Stop and wait.

---

## Stage D. Viewing sequence files inside the app

### D0. Goal and decisions (already agreed with the user)

The user wants to identify a recorded file quickly without the frame data and to start a new recording based on an existing file. Decisions:

1. The data is shown **in the statistics block** (a "view mode"), a toolbar with three icon buttons sits above it.
2. Both `.plsq` and `.json` files can be opened.
3. Button 2 copies the file's description into the input fields (with a confirmation, see D3).
4. Button 3 closes the view and clears the statistics area.
5. Icons: Windows glyph fonts with a text fallback, plus tooltips.

### D1. Toolbar

A new row between the "Start Server" button and the statistics block (see `ui-reworks.jpg`): buttons 1 and 2 on the left, button 3 on the right. About 30x30 px, the same dark style and hover as other buttons (C2).

| # | Action | Tooltip | Glyph (Segoe MDL2 Assets / Segoe Fluent Icons; verify each code point exists, choose similar ones if not) |
|---|---|---|---|
| 1 | Open a file | "Open sequence file (.plsq, .json)" | folder / open file (for example `\uE838` or `\uE8E5`) |
| 2 | Copy data to fields | "Copy file data to the input fields" | download or "move into" (for example `\uE896`) |
| 3 | Clear | "Clear (closes the file view)" | delete / cancel (for example `\uE74D`) |

- Check available fonts with `tkinter.font.families()`. If neither font exists, use text labels "Open", "To fields", "Clear" (and a text fallback for the glyphs if a code point is missing). Document in README which fonts are used.
- Tooltips: a small dark label appears after about 500 ms of hover, disappears on leave/click. A reusable `Tooltip` helper.
- The toolbar must leave room for more buttons later (a frame with a simple layout).

**Button availability:**

| State | 1 Open | 2 To fields | 3 Clear |
|---|---|---|---|
| Idle / server running, live view | enabled | disabled | enabled (clears the error lines) |
| File viewed, file valid/readable | enabled | enabled | enabled (closes the view) |
| File viewed, file invalid | enabled | disabled | enabled |
| Waiting / recording / finalizing | disabled | disabled | enabled (clears the error lines only) |
| Loading a file | disabled | disabled | disabled |

### D2. View mode in the statistics block

- A file opens with a standard open dialog (filter: "Sequences (*.plsq *.json)", all files as a second filter; initial folder = folder of the output path field). Loading runs in a worker thread (a `.plsq` header read is instant, but the file can be on a network drive); the result is picked up by the existing GUI polling. While loading, the text shows "Loading ..." and the indicator (C1) is in the loading state.
- While a file is shown, the live statistics refresh does **not** overwrite the text. The first line is `Viewing: <file name>   (Clear returns to live statistics)`.
- The view ends when: button 3 is pressed; the server is started; a recording is started; or a **new error** arrives from the server/recorder (the error is then shown in red as usual). Closing the window needs no special handling.
- Opening a file is blocked (button disabled) during waiting/recording/finalizing/loading.

**What the text shows** (labels as shown, values from the file, long values wrapped or cut):

```
Viewing: sequence_1.plsq   (Clear returns to live statistics)

File:       sequence_1.plsq
Folder:     D:\...\artnet_to_json_sequence_recorder
Size:       6.12 MB (6117632 bytes)
Format:     PLSQ v1
Status:     VALID (header, metadata and size checked; frame data not scanned)

Model:      Drifting Lights Milano Model (tag: drifting_lights_milano_model_chip)
Chips:      1680    Universes: 14 (start U1, channel 1, 128 chips per universe)
Sequence:   Random Snake   version 1.0
Timing:     30.0 s   30 fps   900 frames   stride 6720 bytes
Processing: skip 0 frame(s), loop blend 0 frame(s)
Recorded:   2026-11-16T17:46:01+01:00 by recorder 1.2.1
Sequence ID: f80315f4-4bb2-413c-ae5e-d0d9c28ff397
Quality:    missing 0 | late 0 | lost 0 | partial 0 | short 0 | first frame partial: no
Notes:      <first ~10 lines / ~600 characters; "... (N more characters)">
```

- Quality counters that are non-zero (or `first_frame_partial` = yes) are highlighted with the existing warning tag (yellow). Keys missing in older files show `n/a` (the two optional keys are absent in files from recorder 1.2.0 and older).
- **Status rules**
  - `.plsq`: use `plsq.read_info` (header + meta only, instant). Success: `VALID ...` (as above). A `PlsqError` becomes red text `INVALID: <ErrorCode>: <message>` (all codes from the spec). `NotFinalized` additionally says: `NOT FINALIZED: the recording was interrupted (.part files are not complete sequences)`.
  - Do **not** call `validate()` for the viewer (it can scan the whole file).
  - `.json`: see D5.
- Formatting is done by a pure function `format_file_info(info) -> list[(text, tag)]` so it can be tested.

### D3. Button 2: copy the file's data to the input fields

Pure function `info_to_fields(info) -> dict` (testable) maps the file to the app's variables.

| File key | Field | Notes |
|---|---|---|
| `model_name`, `model_chips_tag`, `model_chips_count`, `sequence_name`, `sequence_version`, `developer_notes` | same-named fields | text/numbers as stored |
| `sequence_duration_sec` | Sequence Duration | as stored (it is the real duration of the file) |
| `sequence_fps` | Sequence Fps | only if 30 or 60; otherwise the fps is not changed and a warning line is shown |
| `input_mapping_start_universe`, `input_mapping_start_channel` | Start Universe, Start Channel | |
| `input_mapping_chips_per_universe` | Chips Per Universe | **128 becomes 0 (auto)**; any other value is copied exactly (D9) |
| `post_skip_frames` > 0 | Skip First Frames: checkbox on + count; if 0: checkbox off | |
| `post_loop_blend_frames` > 0 | Loop Blend: checkbox on + count; if 0: checkbox off | |

**Not copied:** frame count and stride (recalculated by the app), `sequence_id`, `recorded_at`, `recorder_version`, all quality counters, the output path and the output format.

- Before overwriting, show a small dialog ("Replace the current field values with the data from `<file>`?" buttons "Replace" / "Cancel") **only if** at least one target field currently differs from the value to be set. If nothing differs, copy silently.
- After copying, the read-only fields (frame count, stride, universes) must be recomputed by the usual logic. The view stays open, and one yellow line is added: `Fields updated from file (frame count and stride are recalculated).`
- The copy must work with the numeric validation of C9 active.

### D4. Button 3

- In view mode: closes the view and returns to live statistics.
- In live mode: clears the accumulated error/warning lines of the statistics block (the existing error list), nothing else. It never touches a running recording.

### D5. JSON support

Files written by this recorder have the meta keys first and the `"sequence": {"data": [...]}` block last. A JSON file can be large, so **never `json.load` the whole file for the viewer**. Algorithm (in `fileinfo.py`):

1. Read the first 1 MiB. Find the `"sequence"` key at the top level (the position after the last meta value); take the text before it, close the object, `json.loads` it.
2. If the marker is not found in that window (unexpected layout) and the file is smaller than 50 MB, fall back to a normal `json.load`; if larger, show `INVALID: cannot read metadata (unsupported layout)`.
3. Read the last 64 bytes: the last non-whitespace character must be `}`; otherwise the status is `INVALID: file is truncated`.
4. Required display keys missing: status `READABLE (missing keys: a, b, c)` in yellow and `n/a` in the text. Parse error: `INVALID: JSON parse error: ...`.
5. Status for a good file: `VALID (metadata readable; frame data not checked)`.
6. Frame data is not parsed at all. The size shown is the file size. `Format: JSON`.

### D6. Documentation and version

- README: new section "Viewing sequence files" (toolbar, view mode, button states, what is shown, what is copied, JSON handling), the updated labels, numeric fields, indicator; version 1.3.0 everywhere (`APPV`, title, README).
- `docs/manual_tests/MANUAL_CHECKS_session_16_11_2026_#3.md` stays in the repo; if your implementation differs from it, update it and say so.

### Stage D acceptance (automatic)

Add `tests/test_fileinfo.py` and `tests/test_info_to_fields.py` (names are suggestions). They cover:

1. **`.plsq` info:** `tests/golden/conformance_v1.plsq` -> `format_file_info` contains: `VALID`, model name, 130 chips, 30 fps, 10 frames, universes 2, the Cyrillic notes `Тест UTF-8: привет`; `n/a` is shown for the two optional keys (the golden file does not have them).
2. A file written with the current writer including `short_packets` and `first_frame_partial` -> the values are shown; non-zero counters get the warning tag.
3. **Errors:** corrupted copies produce `INVALID: HeaderCrcMismatch`, `BadMagic`, `Truncated`, `NotFinalized` (the last with the interrupted-recording text); the viewer never raises.
4. **`.json`:** a file written by `write_json` (1 universe) -> same fields as the source meta; a truncated JSON -> `INVALID: file is truncated`; a JSON with some meta keys removed -> `READABLE (missing keys: ...)`; a large synthetic JSON (several MB of data) is read without calling `json.load` on the whole file (check with a monkeypatch or a counter) and finishes fast.
5. **`info_to_fields`:** from the golden file and from a generated file with `chips_per_universe=100`, skip 3, blend 30: `128 -> "0"`, `100 -> "100"`, skip/blend checkboxes and counts; unsupported fps (for example 25) leaves fps unchanged and returns a warning; excluded keys never appear in the result.
6. **Confirmation logic:** a pure helper `fields_differ(current, new)` is true/false for differing/equal inputs.
7. **View-mode state machine** (pure class or functions): transitions for open, clear, start server, start record, new error; button availability table of D1 for every state.
8. The whole suite passes; report the number of tests before and after.

### Stage D manual acceptance

See `docs/manual_tests/MANUAL_CHECKS_session_16_11_2026_#3.md` (the user does it). State clearly in `REPORT_D.md` what you could not check without a window.

---

## Final report (`REPORT_FINAL.md`)

Commits of the session (hash + one line), test counts per stage, a table "item -> acceptance checked automatically / needs manual check", deviations from this file, open questions, and the reminder for the user to run the manual checks and rebuild the exe (M4) at the end.
