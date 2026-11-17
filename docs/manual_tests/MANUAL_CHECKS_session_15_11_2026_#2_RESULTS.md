# Manual check results — recorder v1.2.1

Checklists: `MANUAL_CHECKS_session_15_11_2026_#2.md` (M1–M4).
Executed by the user on 2026-11-16, build `APPV = 1.2.1`, branch `dev/v1.2.0` at commit `d04bef5`.
Automated suite at the same commit: 244 tests, OK.

**Machine:** Windows 11 Home Single Language 10.0.26300, Intel Core i7-13700H, Python 3.13.5.

Evidence was screenshots and console output reviewed in the session chat; this file is the record. Numbers below were read from `tools/validate_plsq.py` output, the statistics block and the tester console.

| Check | Result |
|---|---|
| M1.1 basic PLSQ recording | PASS |
| M1.2 JSON recording, 1 universe | PASS |
| M1.3 universe counter (I-07) | PASS |
| M1.4 format vs extension (I-06) | not run — deferred to session #3, section R |
| M1.5 folders (I-04) | not run — deferred |
| M1.6 long notes (I-05) | not run — deferred |
| M1.7 reduced blend (I-11 / D11) | not run — deferred |
| M1.8 large installation, 64,000 chips | PASS |
| M1.9 closing the window during a recording | **ISSUE** → `docs/BACKLOG.md` B1 |
| M2 real source (TouchDesigner) | PASS, with findings below |
| M3 old Blueprint JSON parser | PASS |
| M4 exe build and `.bat` files | PASS |

---

## M1.1 — basic PLSQ recording

104 chips, 30 fps, duration 6 s, `.plsq`. Source: `packets_tester.bat 127.0.0.1 6454 104 1 --duration 30`
(tester sent 1,801 packets in 30 s at 60 pkt/s, 0 send errors).

`validate_plsq`: `Status: OK`. Size 144,512 bytes = 69,632 + 180 × 416. `frame_count` 180, `frame_stride` 416,
`flags` 0x2 (FINALIZED), `universes_count` 1, `chips_per_universe` 128, `format_version` 1, `header_size` 128.
`missing_frames`, `late_frames`, `partial_source_frames`, `lost_packets`, `short_packets`,
`first_frame_partial` — all 0. The two optional keys are printed as `[extra]`.

The recorder sampling at 30 fps from a 60 pkt/s source is expected behaviour, not a loss.

## M1.2 — JSON recording

Same source, JSON format, 1 universe. File 471 KB, 180 frames, stride 416. All 27 required keys present plus the
two optional ones (`short_packets`, `first_frame_partial`) as strings.

Payload values verified against the tester's `rainbow` pattern: chip 0 = `0.0, 0.0, 0.0, 0.0`;
chip 1 = `0.027, 0.02, 0.012, 0.0` = (7, 5, 3, 0)/255, W channel zero. Normalization matches D10.

The statistics block kept `Server: RUNNING` after saving (the server is stopped only by the user).

## M1.3 — universe counter (I-07)

Read from "Universes (calculated)" without starting a recording.

| Chips | Start channel | Shown | Expected | |
|---|---|---|---|---|
| 104 | 1 | 1 | 1 | ok |
| 128 | 1 | 1 (stride 512) | 1 | ok |
| 129 | 1 | 2 (stride 516), JSON enabled | 2 | ok |
| 256 | 2 | 3 | 3 (127 + 128 + 1) | ok |
| 384 | 1 | 3, JSON greyed out | 3, JSON blocked (D3) | ok |
| 64,000 | 1 | 500 (stride 256,000), JSON greyed | 500 | ok |
| 64,000 | 2 | 501 | 501 | ok |
| 100 | 512 | `?` | layout error | ok |

The last row is correct by design: with `start_channel = 512` no chip fits into the first universe
(4 channels needed), and `mapping.build_spans` raises *"Not a single chip fits into the universe: lower the
start channel"*. Usability note (not a defect): the window shows `?` without the reason — see `BACKLOG.md` B2.

## M1.8 — 64,000 chips, 60 fps

Settings: 64,000 chips, 60 fps, duration 20 s, Loop blend 30, `.plsq`, "Start recording on first packet" and
"Wait for complete source frames" on. Source: `packets_tester.bat 127.0.0.1 6454 64000 1 --fps 60 --duration 60`.

During the recording (statistics block): 29,929.5 pkt/s received (expected 30,000); all 500 universes at
exactly 60 pkt/s (`avg = min = max`); 302/1230 frames in 5.02 s (60.2 fps); queue 0; written 66.4 MB;
`SO_RCVBUF` 32,768 KiB (full 32 MiB granted). Total received over the run: 739,000 packets.

`validate_plsq`: `Status: OK`. Size 307,269,632 bytes = 69,632 + 1200 × 256,000. `frame_count` 1200,
`frame_stride` 256,000, `universes_count` 500, `fps` 60, `post_loop_blend_frames` 30,
`recorded_frames_raw` 1230. All quality counters 0.

**Finalization took about 1 s** — the user could not capture the "Finalizing..." state before it ended, and the
window stayed responsive. This matches the measured blend cost (0.88 s for 30 blend frames at 64,000 chips,
see `ERRATA_session_#1.md` §4). Finalization time depends on the blend length, not on the recording length.

The app reported "293.0 MB" (MiB) where `validate_plsq` reports "307.27 MB" (MB) — units, not a size mismatch;
`BACKLOG.md` B2.

## M1.9 — closing the window during a recording — ISSUE

Started the same 64,000-chip recording and closed the window with the X button about 4 s in.

Result: no `.plsq` was produced; a `M1_9.plsq.part` of 57,568 KB was left on disk; **no dialog and no warning**.

This is the coded behaviour: `App._close` calls `PlsqRecordingSession.abort()`. It is safe (a `.part` is never
mistaken for a finished sequence: no FINALIZED flag, no meta) but the recording is lost silently. Logged as
`docs/BACKLOG.md` B1 (close-window dialog + `tools/recover_part.py`), low priority, target 1.3.5–1.3.9.

## M2 — real source: TouchDesigner

TouchDesigner 2025.32820, project `test_project_milano.13.toe`, `DMX Out` → Art-Net, Format "Packet Per Sample",
Rate 30, timeline 30 fps, output to `127.0.0.1:6454`. Recorder: 1,680 chips, 14 universes, 30 fps, 30 s,
"Wait for complete source frames" on. Captured in parallel with Wireshark (`artnet` filter, loopback).

Recording `sequence_1.plsq`: `Status: OK`, 6,117,632 bytes = 69,632 + 900 × 6,720. 1,680 chips, 14 universes,
30 fps, 900 frames, stride 6,720. All quality counters 0 (including `partial_source_frames`).
During the recording: ~63.3 pkt/s per universe, 888.7 pkt/s total, 512 channels per universe.

**Findings about the real source (the main value of this check):**

1. **No ArtSync.** Only `ArtDMX` (OpCode 0x5000) was seen in the capture. The frame assembler therefore commits
   on the "all mapped universes updated" rule, and it did so for every frame (`partial_source_frames = 0`).
2. **All 14 universes are sent in every frame**, not only the changed ones; each carries the full 512 channels
   (562 bytes on the wire). U14 needs only 64 bytes of its 512 — the rest is ignored by the mapping.
3. **Packet order inside a burst is not ascending** (observed U4, U3, U2, U1, U5…U14), the whole burst within
   about 4 ms.
4. **Sequence numbers are independent per universe** (U1 at 5, U2–U4 at 12, U5–U11 at 245, U12 at 250 at the same
   moment). The recorder's per-universe counters reported 0 losses — this is the first confirmation of that logic
   against a real source rather than the tester.
5. **The source sends about 60 sets per second although Rate is 30** ("Packet Per Sample" emits one packet per
   CHOP sample). Intervals between bursts were 13–15 ms. Recording at 30 fps keeps the last assembled frame, so
   the file is valid, but if the animation really carries 60 distinct states per second, half of them are not in
   a 30 fps recording. To be settled with the designer per sequence.
6. ICMP "Port unreachable" appears in the capture after 44.8 s — that is the moment the server was stopped;
   expected.

Note: `model_name` in this file contains a typo, `Drfiting Lights Milano Model` (the tag is spelled correctly).
The file is otherwise good and is used as a test vector for the UE plugin.

**Not run:** the same recording with "Wait for complete source frames" off. Skipped deliberately: the assembler
produced zero partial frames, so the comparison would add nothing.

## M3 — old Blueprint JSON parser

`M1_2.json` (written by 1.2.1, containing the two new optional keys) was loaded by the user's existing
Pre-Installed Blueprint parser in UE 5.6. Loaded without errors or warnings. Conclusion: the optional keys stay
in the JSON output; no change needed.

## M4 — exe build and `.bat` files

`pyinstaller --onefile --noconsole main.py` with PyInstaller 6.22.3 (contrib hooks 2026.10), Python 3.13.5.
Build completed with no errors; Tcl/Tk hooks were picked up. `dist\main.exe` starts, a recording made from the
exe passes `validate_plsq` (`Status: OK`). `run.bat` (without `debug`) opens the window with no console and
`packets_tester.bat` runs. No Defender or SmartScreen problem worth reporting.

Note: `build/`, `dist/` and `main.spec` are not in `.gitignore` — to be added.

---

## Test files produced by these checks

| File | What it is | Keep for |
|---|---|---|
| `sequence_1.plsq` | real TouchDesigner recording, 1,680 chips, 14 universes, 30 fps, 900 frames, 6,117,632 B | UE plugin tests |
| `m1_1.plsq` | 104 chips, 30 fps, 180 frames, 144,512 B | UE plugin tests |
| `M1_2.json` | JSON, 1 universe, 104 chips, 180 frames | JSON path, Blueprint parser |
| `M1_9.plsq.part` | interrupted recording, 64,000 chips, ~57 MB, no FINALIZED flag | `NotFinalized` test vector |
| 64,000-chip recording, 307,269,632 B | deleted by the user after the check | regenerate for plugin load tests (P8) |

These live outside the repository (in the recorder's working folder); the only sequence in git is
`tests/golden/conformance_v1.plsq`.

## Open items after these checks

- M1 items 4–7 deferred to the session #3 checklist, section R (the input fields change in 1.3.0).
- `BACKLOG.md` B1 (close-window dialog, `recover_part.py`) and B2 (small UX items).
- `.gitignore`: `build/`, `dist/`, `main.spec`.
