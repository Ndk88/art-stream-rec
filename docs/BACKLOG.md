# Backlog (deferred work)

Items that are agreed but not scheduled for the current session. Order is not a promise. Target versions are intentions.

## B1. Close-window dialog and recovery of interrupted recordings (low priority, target 1.3.5 - 1.3.9)

**Problem (found in manual check M1.9, 2026-11-16).** Closing the window during a PLSQ recording calls `abort()`: the recording is lost silently and a `.part` file is left behind; there is no final `.plsq`. The `.part` is safe (never mistaken for a finished sequence) but the user gets no warning.

**B1.1. Close-window dialog**
- Closing during `waiting`/`recording`: show a dialog (`ask_choice`, existing style) with **"Stop and save"** (default: finish the recording with the frames recorded so far through the normal finalization, including the short-recording blend rule D11, then close), **"Close without saving"** (abort; the `.part` stays; its path is shown in the statistics/log), **"Cancel"**.
- If no frame has been written yet: no dialog, just abort.
- During `finalizing`: wait for the end (at most `FINALIZE_TIMEOUT`), then close without a dialog.
- JSON recordings: the same three choices; no frames are lost when the window is closed during a JSON recording.
- Tests without GUI (the dialog choice is injected).

**B1.2. `tools/recover_part.py <file.part> [-o out.plsq]`**
- Important fact: `PlsqWriter.open()` writes only a placeholder header (`chips`, `fps`, `stride`, `start_universe`, `start_channel`, `chips_per_universe`, `universes_count`, `frame_count = 0`, `meta_size = 2`, flags without FINALIZED) and no metadata. The real meta (model name, tag, sequence name, notes, `recorded_at`, counters) is written only at finalization. After a crash the `.part` contains **frames and layout only**.
- The tool therefore takes the descriptive fields from optional arguments (`--model-name`, `--model-chips-tag`, `--sequence-name`, `--sequence-version`, `--developer-notes`), fills the rest with neutral defaults, marks the file as recovered in `developer_notes`, and sets the quality counters it cannot know to `"0"`.
- Frame count = `floor((file size - payload_offset) / frame_stride)`; a half-written last frame is dropped (and reported). The source `.part` is never modified; the output is a new file written through the normal writer path (`.part`, then rename).
- Errors: header CRC invalid or `chips`/`stride` missing -> a clear message and exit code != 0.
- Tests: `.part` cut in the middle of a frame; `.part` with a whole number of frames; broken header; recovered file passes `validate`.

**B1.3. Viewer link (after the viewer of session #3 exists).** Opening a `.part` in the viewer shows "NOT FINALIZED, N complete frames can be recovered" and offers to run the recovery.

## B2. Small UX items (no priority)

- Show the reason next to "Universes (calculated)" when it shows "?" (for example "no chip fits at this start channel").
- Units: the window shows sizes in MiB (293.0 MB for 307,269,632 bytes) while `validate_plsq` uses MB; unify the labels.
- Window drag and drop of a file onto the app is not possible with the standard library alone (needs a non-stdlib package); not planned.

## B3. Manual checks still to do after session #3

The quick items 4-7 of M1 (format vs extension, folders, long notes, short recording with blend reduction) are in `docs/manual_tests/MANUAL_CHECKS_session_16_11_2026_#3.md` (section R), because session #3 changes the input fields.
