# Recorder tasks (Python)

Context: `CLAUDE.md` (decisions D1..D10) and `docs/PLSQ_SPEC.md`. Existing code: `main.py`, `packets_tester.py`, `README.md`.

How to work: do one task per session unless told otherwise. Each task has a goal, the required changes and acceptance criteria. Run the tests, commit, then report as described in `CLAUDE.md` section 6.

## Recommended order

`R1 -> R7 -> R6 -> R8 (baseline) -> R5 -> R8 (re-measure) -> R2 -> R4 -> R3 -> R9`

Reason: the receiver at 500 universes x 60 Hz (about 30,000 packets/s through Python) is the biggest technical risk. Measure the current code first, fix it, and only then build the rest on top.

---

## R1. Core module `plsq.py` (no tkinter)

**Goal.** A tested implementation of the PLSQ format that the recorder, the tools and the tests share.

**Changes.**
- New `plsq.py` implementing `docs/PLSQ_SPEC.md`: header pack/unpack with the struct from section 3, CRC-32, meta encode/decode, `PlsqError(code, message)` with the codes of section 10.
- Helpers: `effective_chips_per_universe(setting)`, `count_universes(start_universe, start_channel, chips_per_universe, chips)` using the existing `build_spans` logic (move `build_spans` into a non-GUI module that both `main.py` and `plsq.py` can import; `main.py` must not be imported by `plsq.py`).
- `build_meta(...)` returning the 27-key dict in the order of section 5 (shared by the JSON and PLSQ writers; used by R2).
- `PlsqWriter` (streaming): `open(part_path, chips, fps, start_universe, start_channel, chips_per_universe)`, `append_frame(bytes)`, `read_frames(start, count)`, `rewrite_frames(start, frames)`, `finalize(meta_dict)` (writes meta + final header with `FINALIZED`, flushes, closes), `abort()` (closes, keeps the file).
- Reader: `read_info(path)` (header + meta only, never touches the payload), `read_frames(path, start, count)`, `validate(path)` returning a list of problems and raising nothing for a bad file.
- Large values use Python ints; offsets are 64-bit.

**Acceptance.**
- `unittest` tests: round trip (write, finalize, read back byte-exact); each reader error code is produced by a deliberately corrupted file (flip a header byte, flip a meta byte, clear `FINALIZED`, truncate the file, wrong magic, `COMPRESSED` set); meta mismatch cases; a file of 64,000 chips x 120 frames written and read back; writer throughput at 64,000 chips is at least 10x the real-time need (256,000 B x 60 fps).
- No import of tkinter in `plsq.py`; no third-party packages.

---

## R7. Tools

**Goal.** Command-line helpers, also used by the plugin side for testing.

**Changes.**
- `tools/make_golden.py`: generates `tests/golden/conformance_v1.plsq` exactly as in spec section 14; a test regenerates it in memory and compares bytes with the committed file.
- `tools/validate_plsq.py <file>`: runs `validate`, prints header and meta fields and every problem; exit code 0 only if clean.
- `tools/gen_synthetic.py --chips N --fps 30|60 --seconds S --out file.plsq [--pattern ramp|rainbow|static]`: streams a valid file using `PlsqWriter`. It must be fast (target at least 200 MB/s): build one base pattern of `stride` bytes and derive every frame by slicing/rotating it, not by per-byte Python loops. Meta values are sensible placeholders (`sequence_name = "synthetic"`).
- `tools/convert_json_to_plsq.py in.json out.plsq`: reads an existing JSON sequence (old files may lack the new meta keys), converts floats back with `round(v * 255)`, fills missing meta keys with documented defaults (`recorder_version = "converted"`, `recorded_at` = conversion time, new `sequence_id`, counters `"0"`, mapping from the file if present else start_universe 1, start_channel 1, chips_per_universe effective 128), then validates the result. Existing `example_sequence_1.txt`-style files (104 chips x 4 channels, 180 frames) must convert.

**Acceptance.** Golden file test passes; `gen_synthetic` creates 64,000 chips x 60 fps x 10 s and `validate_plsq` reports it clean; the converter round-trips a JSON written by the current recorder with identical bytes after `round(v*255)`.

---

## R6. Extend `packets_tester.py` (test source for large installations)

**Goal.** Generate realistic Art-Net load up to 500 universes at 60 Hz and provoke the problems the recorder must survive.

**Changes.**
- Keep the existing positional arguments (`host port chips universe`) so the `.bat` launcher still works. New limits: chips 1..64,000, always 4 channels per chip (RGBW), universes laid out like the recorder does (shared mapping helper, not a copy).
- New options: `--fps 30|60` (default 60), `--chips-per-universe N` (0 = auto 128), `--pattern rainbow|ramp|static`, `--duration SECONDS`, `--artsync` (send an ArtSync packet, OpCode 0x5200, after all universes of every frame), `--stagger-ms X` (delay between universes of one frame to provoke tearing), `--procs N` (split universes over N processes for very large loads).
- Art-Net sequence numbers: per universe, 1..255 wrapping, never 0 (the receiver uses gaps to count lost packets).
- Performance: for large installations do not compute colors per frame in Python; precompute a ring of frames at start (for example 2 seconds, rainbow/ramp pattern by slicing) and replay it. Keep the drift-free scheduler. If the sender falls behind schedule it prints a warning with the lag, so a slow sender is distinguishable from a slow receiver.
- Log lines as today (once per second: packets sent, rate, errors).
- Update the `.bat` header comments and README usage. Check the actual file names in the repo: README says `run_packets_tester.bat`, the file may be named `packets_tester.bat`; make docs and files agree.

**Acceptance.** A run with 1 universe, default arguments behaves as before (log format may gain fields). A run with 64,000 chips at 60 fps reaches the target packet rate on the user's machine or prints the lag warning; `--artsync` and `--stagger-ms` visibly change the packet stream (verify with a small receiving test script).

---

## R8. Receiver benchmark and report

**Goal.** Numbers, not guesses, for the receive path.

**Changes.**
- `tools/bench_receiver.py`: runs the receiver and sampler core without the GUI for a given duration while the tester sends from another process. Prints: sent vs received packets, lost packets (sequence gaps), `late_frames` percentage, process CPU time, requested vs actual `SO_RCVBUF`, whether the sampler hit its schedule.
- Matrix (chips, fps): (1,700, 30), (1,700, 60), (10,000, 60), (32,000, 60), (64,000, 30), (64,000, 60), 60 seconds each.
- Write `docs/BENCHMARK_RECORDER.md` with the machine description, the table and a conclusion. Run once as a **baseline on the current code before R5**, and again after R5.

**Acceptance targets (64,000 chips, 60 fps = 500 universes x 60 Hz).** Zero lost packets; `late_frames` below 0.5%; the recorder process stays responsive. If the target is not reachable after R5, stop and report the measurements with options (for example a separate receiver process with shared memory, or a C extension). Do not implement those options without approval.

---

## R5. Receiver performance, complete source frames, preflight, compact statistics

**Goal.** A receive path that survives 30,000 packets/s and records consistent frames.

**Changes.**
1. **Fast receive loop.** Large `SO_RCVBUF` (for example 32 MB; read back and log the real value, Windows may cap it); `recvfrom_into` into a preallocated buffer with `memoryview`; `struct.unpack_from` for the header; no per-packet object allocation; copy channel data straight into preallocated per-universe buffers. Re-evaluate the per-packet lock: single slice assignments and reads are atomic under the GIL, but prove with a test that the sampler never sees a torn universe.
2. **Lost packet counting.** Use the Art-Net sequence byte per universe (0 means disabled): count gaps (`expected = last % 255 + 1`). The count is approximate with reordering; document that. Feed it to `lost_packets`.
3. **Complete source frames (anti-tearing).** With many universes one source frame arrives as many packets; sampling "the latest value of each universe" can mix frame N and N-1. Implement a frame assembler configured with the mapping at record start:
   - Write incoming mapped-universe data directly into a working frame buffer at the span offset.
   - Commit the working buffer to the "committed frame" (an immutable `bytes`, swapped atomically) when (a) all mapped universes have been updated since the last commit, or (b) an ArtSync packet (OpCode 0x5200) arrives, or (c) a universe arrives for the second time before all others did (the source started the next frame): commit as partial and count `partial_source_frames`.
   - The sampler thread only reads the committed frame; it does no assembly work.
   - GUI checkbox "Wait for complete source frames (recommended)", default on. Off restores the old sample-the-latest behavior (for sources that send only changed universes).
4. **Preflight check** when the user presses Start Record and universes are already arriving: compare the expected universe set (from the mapping) with the received set. If expected universes are missing while others arrive, show an error in the statistics block and do not start. If nothing arrives yet (waiting mode), start as usual. Unexpected received universes inside or near the range produce a warning line.
5. **Compact statistics.** When more than 16 universes arrive, replace the per-universe list by a summary: count, range (`U1-U500`), average / min / max packet rate, channel-length consistency, plus at most 5 irregular universes (rate below 90% of the median, or length different from the rest). Also show `lost`, `partial`, and the preflight status line (`Expected U1-U500, received U1-U500 OK`).

**Acceptance.** Unit tests for the assembler (complete frames, ArtSync commit, repeat-before-complete partial commit, off mode); a stress test with the tester at `--stagger-ms` shows `partial_source_frames == 0` when ArtSync is on and consistent frames otherwise; no torn universe in the atomicity test; benchmark (R8) meets the targets; the statistics block stays readable at 500 universes; GUI refresh does not stall the receiver.

---

## R2. Shared meta and the JSON writer

**Goal.** One meta builder for both formats; JSON gets all new keys.

**Changes.**
- JSON export writes the 27 keys of spec section 5 in that order (`file_format = "json"`), then `sequence.data` as today (one frame per line, `round(byte/255, 3)`).
- `input_mapping_chips_per_universe` is the effective value, `input_mapping_universes_count` is written, `sequence_id` is a new UUID, `recorded_at` is the local ISO time with offset, `recorder_version` is `APPV`.
- JSON saving refuses when `universes_count > 2` (D3): the writer raises, the GUI blocks earlier (R3).
- Bump `APPV` (minor version).

**Acceptance.** Tests: output parses as JSON, has exactly the 27 keys + `sequence`, values are strings, the JSON of a 104-chip sequence loads in the old Blueprint parser conceptually unchanged (keys it already used keep their names and formats); the 3-universe case raises. Note in the report that the user should verify that the existing Blueprint parser ignores the new keys.

---

## R4. Streaming PLSQ recording pipeline

**Goal.** Record any installation size to disk while recording, with post-processing done without holding the recording in RAM.

**Design (required).**
- Applies to `.plsq` only. The JSON path (at most 2 universes) keeps the current in-memory pipeline and `postprocess()` unchanged.
- The target path is fixed when the recording starts (the existing "file exists" dialog flow runs at start). Changing the path field during a recording affects the next recording. Update the README text that currently says otherwise.
- The sampler puts frames into a bounded queue (about 2 seconds of frames). A writer thread consumes it. If the queue overflows (disk too slow), stop the recording, finalize what exists, and show an error.
- Skip: the first `skip` frames are discarded, never written.
- Loop blend with a delay line: the writer thread keeps the last `blend` frames in a deque; when the deque exceeds `blend`, the oldest frame is written as an exported frame. When capture stops, the deque holds the `blend` tail frames and `frames_written` is `final`. This is equivalent to `postprocess(frames, skip, blend, final)`: `final = captured_after_skip - blend`.
- At stop (automatic or manual): validate `final > 0` and `blend <= final`; if `blend > 0` read the first `blend` frames back from the file, compute `out[i] = round(tail[i] * (1 - i/blend) + head[i] * (i/blend))` and rewrite them in place; then `finalize(meta)`; then `os.replace(part, target)`.
- Refuse to start when `blend x frame_stride` exceeds 1 GiB (the delay line is in RAM) and tell the user.
- If a leftover `<target>.part` exists, ask with the existing choice dialog (overwrite the leftover / cancel); never delete silently.
- Failures (disk full, finalize error): keep the `.part`, show its path in the Errors list. There is no "pending recording in memory" for PLSQ.
- Closing the window during a PLSQ recording stops and finalizes it first.
- Statistics block shows queue depth and bytes written.
- Blend arithmetic must be fast enough: if pure Python takes more than about 10 seconds at 64,000 chips, stop and ask about an optional accelerator.

**Acceptance.** A test proves the streaming pipeline output is byte-identical to `postprocess()` on the same random frames for several (skip, blend, total) combinations including manual stop and `blend = 0`; a 64,000-chip x 60 fps x 30 s run with a synthetic source produces a file that `validate_plsq` accepts, with RAM use staying flat; simulated disk-full / queue-overflow produces an error, keeps the `.part`, and does not crash the GUI.

---

## R3. GUI changes

**Goal.** Expose the decisions in the interface without breaking its look and conventions.

**Changes.**
- `Sequence Fps`: two radio buttons, 30 and 60 (default 30), replacing the free entry.
- `Channels per chip`: fixed 4, shown read-only (D1). Remove the "(3 = RGB, 4 = RGBW)" wording.
- `Model Chips Amount` validated to 1..64,000. `Start Universe` 0..32,767 and `start_universe + universes_count - 1 <= 32,767`.
- `Chips per universe (0 = auto)` stays; next to it show the effective value ("auto -> 128").
- New read-only line "Universes (calculated)" updated live from the mapping.
- Format selector (radio): `.plsq` and `JSON`. Default `.plsq`. JSON is disabled and forced to `.plsq` when the calculated universes are 3 or more; show a note "JSON is available for up to 2 universes". Switching the format swaps the extension of the path field (`.plsq` <-> `.json`) when the current extension is one of these. Default path `sequence_1.plsq` in the current folder; the Browse dialog uses the selected format's filter.
- Checkbox "Wait for complete source frames (recommended)" (from R5).
- Statistics block additions from R4/R5 (queue, bytes written, lost, partial, preflight).
- All new validation errors go to the Errors list, not popups.

**Acceptance.** Manual checklist in the report (screenshots welcome): format switching with 2 and 3 universes, fps radios, live universe count at 104 / 128 / 129 / 384 / 64,000 chips, saving both formats end to end with the tester; existing dialogs (overwrite / new name / unsaved recording) still work for the JSON path. Mention what could only be tested on Windows by the user.

---

## R9. Documentation and housekeeping

**Changes.**
- README: describe `.plsq` and JSON, the 2-universe JSON limit, the 1..64,000 chip range, 30/60 fps, the streaming pipeline and `.part` files, completeness logic, new statistics, tester options, tools, benchmark results; remove outdated text (RGB 3-channel example, "path changed during recording" behavior, "no third-party packages" stays true).
- `.gitignore`: add `*.plsq` and `*.part` (keep `tests/golden/conformance_v1.plsq` tracked with a negation rule).
- Check that `run.bat`, the tester `.bat` and the PyInstaller command still work.
- Add `docs/BENCHMARK_RECORDER.md` link to README.

**Acceptance.** README matches the behavior; a fresh clone runs the tests with `python -m unittest`.
