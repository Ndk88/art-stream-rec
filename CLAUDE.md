# CLAUDE.md

Project context for Claude Code. Read this file first, then `docs/PLSQ_SPEC.md`, then the task file you were asked to work from (`docs/TASKS_RECORDER.md` or `docs/TASKS_UE_PLUGIN.md`).

## 1. What this project is

Large light installations are built from thousands of RGBW "chips". A designer drives them from TouchDesigner or Madrix 5 over Art-Net. This project has two parts:

1. **Recorder** (`main.py`, Python 3.9+, Tkinter, stdlib only, Windows is the primary platform). It listens for Art-Net (ArtDMX) on a local UDP port (default `127.0.0.1:6454`), samples the stream at a fixed frame rate and saves a baked sequence. The code exists and works for small installations; it is being extended.
2. **Unreal Engine plugin** (C++, UE 5.6, to be written). It plays baked sequences at runtime and drives the model's lights. The host UE project is Blueprint-only, so the plugin is built separately with `RunUAT BuildPlugin` and delivered with binaries.

A baked sequence is a JSON file (small installations only) or a binary `.plsq` file (any installation). Parsing big JSON in Blueprint is too slow for large installations; that is the reason for this work. The JSON pipeline stays for small installations.

## 2. Locked decisions

Do not change these without asking the user.

| # | Decision |
|---|---|
| D1 | Always 4 channels per chip: RGBW. Channel order inside a chip is the DMX order as received (R, G, B, W). No RGBWA, no other layouts. |
| D2 | Binary format: PLSQ, extension `.plsq`, defined in `docs/PLSQ_SPEC.md`. Compression is reserved in the header but not implemented in v1. |
| D3 | JSON export stays only for small installations: allowed when the layout uses **fewer than 3 universes** (1 or 2). With 3 or more universes JSON saving is blocked and only `.plsq` is offered. |
| D4 | Design range: from 1 universe (up to 128 chips) up to **64,000 chips** (500 universes at 128 chips per universe). Every decision is checked against the top of this range. The 104-chip test stand is inside it. |
| D5 | The recorder has two frame-rate modes only: **30 and 60 fps**. |
| D6 | The UE plugin streams `.plsq` files in chunks from the start. It never loads the whole payload into memory. |
| D7 | Outputs in UE: (a) "fake light": instanced static meshes with an emissive material that reads a runtime texture; (b) real lights: **one light component per chip** (Point/Spot/Rect). No clusters. If clusters are ever needed the user will ask separately. |
| D8 | Every field the recorder knows about a sequence (everything except the data bytes) is stored in the file meta, with the same keys in JSON and `.plsq`, and is readable from Blueprint as a struct and as a JSON string (to forward over Pixel Streaming to a React frontend). |
| D9 | `chips_per_universe`: the UI default is `0` = auto (128 for RGBW). Files always store the **effective** value (never `0`). The user can override it to match the patch of the source. |
| D10 | Playback is time-based: frame index = floor(time x fps). Normalization: byte / 255 is treated as a linear value, as in the existing pipeline (Make Linear Color). |
| D11 | When a recording is stopped early and the number of exported frames (`final`) is less than the requested loop-blend length (`blend`), the recorder applies a shortened blend `blend_applied = min(blend, final)` in both formats (PLSQ and JSON), writes `blend_applied` to `post_loop_blend_frames`, and shows a persistent warning line in the statistics block: *"Loop blend reduced from X to Y frames: the recording is too short"*. This is not an error. If `final <= 0` it remains an error and nothing is saved (PLSQ: `.part` file kept; JSON: raw recording kept in memory). |

## 3. Scale reference (RGBW, 4 bytes per chip)

| Chips | Universes (128/universe) | Bytes per frame | MB/s at 60 fps |
|---|---|---|---|
| 104 (test stand) | 1 | 416 | 0.02 |
| 1,700 | 14 | 6,800 | 0.41 |
| 10,000 | 79 | 40,000 | 2.4 |
| 64,000 | 500 | 256,000 | 15.4 |

At 64,000 chips and 60 fps the Art-Net input is about 30,000 packets per second (about 16 MB/s) and one hour of recording is about 55 GB.

## 4. Out of scope (do not implement unless the user asks)

- Light clusters / light proxies in UE.
- Compression of the `.plsq` payload (only the reserved flag).
- Reading JSON sequences inside the UE plugin (the existing Blueprint JSON pipeline keeps serving JSON files).
- sACN, ArtPoll replies, 16-bit channel pairs.
- Interpolation between frames.

## 5. Assumptions taken (change only after asking)

- A1. The plugin reads `.plsq` only (see section 4).
- A2. The user will verify `chips_per_universe` against the real source patch; the recorder default is auto.
- A3. The meaning of the letters "PLSQ" is not defined; just call the format PLSQ.

## 6. Working agreements

- If a requirement is ambiguous or conflicts with the spec, ask the user. Do not silently change `docs/PLSQ_SPEC.md`; if the spec must change, say so, propose the change and wait.
- Python: stdlib only at runtime. If a hot path seems to need numpy or a C extension, stop and ask.
- Windows first: no POSIX-only code, keep `run.bat` and the tester `.bat` working, keep `pyinstaller --onefile --noconsole main.py` working.
- Keep the existing UI conventions: dark theme and existing colors; errors go to the statistics block (red, time-stamped); popups only for choices about files and unsaved recordings.
- Code style as in `main.py` (black-like, 88 columns). Code, comments and UI strings in English.
- Tests with `unittest`; run them before you finish. Keep tests deterministic and fast; heavy benchmarks live under `tools/`.
- Never delete user data silently (existing outputs, `.part` files).
- Update `README.md` for every user-visible change.
- At the end of each task report: what changed, how it was verified, what could not be verified (for example Windows-only behavior or UE editor steps).

## 7. Target repository layout

Recorder side:

```
main.py                  GUI + app entry point (existing)
plsq.py                  NEW: format reader/writer/validator, no tkinter
(core modules)           optional: move non-GUI code out of main.py for testability
packets_tester.py        test Art-Net source (extended)
tools/                   validate_plsq.py, convert_json_to_plsq.py, gen_synthetic.py,
                         make_golden.py, bench_receiver.py
tests/                   unittest + tests/golden/conformance_v1.plsq
                         + tests/golden/errors/, tests/golden/stamp/ (test vectors for the
                         UE plugin, made by tools/make_test_vectors.py)
docs/                    PLSQ_SPEC.md, TASKS_*.md, BENCHMARK_RECORDER.md (after R8)
```

Plugin side (separate folder or project):

```
Plugins/LightSequence/   LightSequence.uplugin, Source/LightSequence/...
```

## 8. Definition of done (every task)

- The acceptance criteria of the task are met and demonstrated (test output or measurement).
- Existing behavior that the task does not intentionally change still works.
- README and docs are updated.
- The final report is written as described in section 6.
