# Art Stream Sequence Recorder

A small desktop tool that listens for **Art-Net (ArtDMX)** packets on a local UDP port, records the stream sent by
**TouchDesigner**, **Madrix 5** or any other Art-Net source at a fixed frame rate, and saves the recording as:

- a **PLSQ sequence** (compact binary, `.plsq`) — the default for all installation sizes; frames are streamed to disk
  during recording so RAM use stays flat even for 64,000-chip / 60 fps sessions (≈ 55 GB per hour).
- a **JSON sequence** (floats in the range 0..1, `.json`) — for small installations with **1 or 2 universes**
  (up to 256 RGBW chips); refused for larger layouts.

The recorder is a single Python file with a Tkinter window. It needs no extra packages.

---

## Contents

1. [Features](#features)
2. [Requirements and files](#requirements-and-files)
3. [Quick start](#quick-start)
4. [User interface](#user-interface)
5. [Recording step by step](#recording-step-by-step)
6. [Output formats](#output-formats)
7. [How it works (algorithms)](#how-it-works-algorithms)
8. [Protection of recordings and files](#protection-of-recordings-and-files)
9. [Test source: packets_tester](#test-source-packets_tester)
10. [Troubleshooting and notes](#troubleshooting-and-notes)
11. [Building an .exe](#building-an-exe)

---

## Features

- Local **UDP server** (default `127.0.0.1:6454`, the standard Art-Net port) with Start/Stop button.
- Parses **ArtDMX** and **ArtSync** packets (Art-Net port-address 0..32767); other packet types are counted and ignored.
- **Live statistics**: packet counter and rate, lost-packet count (from Art-Net sequence gaps), socket receive buffer size,
  last packet age, every universe arriving with its rate and channel count (compact summary for >16 universes),
  recording progress, and counters of `missing`, `late`, and `partial source` frames.
- **Fixed frame rate recording** (fps is configurable): the stream is sampled on a drift-free schedule, so a source
  that sends 60 packets per second can be recorded at 30 fps.
- **Complete source frame assembler** (default on): when a source spans multiple universes, the recorder waits for all
  mapped universes to update before committing a frame, so the sampler never mixes channel values from two different
  source frames. Commits are also triggered by an ArtSync packet or by a repeated universe (partial commit). The
  assembler can be turned off with the "Wait for complete source frames" checkbox for sources that send only changed
  universes.
- **Preflight check**: when Start Record is pressed and universes are already arriving, the recorder checks that every
  expected universe (from the mapping) is present. Missing expected universes block the recording; unexpected universes
  produce a warning.
- **Channel mapping**: start universe (0..32,767), start channel, **channels per chip fixed at 4** (RGBW, D1), and
  chips per universe (0 = auto 128). Chips can span several universes. _Model Chips Amount_ is validated to 1..64,000.
  The effective chips-per-universe and the calculated universe count are shown live in the UI.
- **Automatic stop** after `Sequence Duration × Sequence Fps` frames, or manual stop (duration `0`).
- Optional **start on the first received packet**, so the recording does not begin with empty frames.
- Optional **post-processing** (both are off by default):
  - _Skip first frames_ - drops the first N recorded frames (useful if the first frames are unstable).
  - _Loop blend_ - cross-fades the end of the recording into its beginning so the sequence loops without a jump.
- **PLSQ streaming export** (`.plsq`): frames are streamed directly to disk during recording via a bounded queue and a writer thread — RAM use stays flat regardless of recording length. Loop blend uses a delay-line deque; at stop the first `blend` frames are rewritten with the cross-fade, then the file is finalized and atomically renamed from `.part` to target.
- **JSON export** with a full 27-key header (spec §5) and a flat float array. Available for layouts using **1 or 2 universes** only; refused for 3 or more (use PLSQ for large installations).
- **Errors are shown in the statistics block** (red, with time stamps), not in pop-up windows.
- **Protection of data**: asks before overwriting a file, offers a new file name, keeps unsaved recordings in memory,
  and warns before closing the window with an unsaved recording.
- A **test Art-Net source** (`packets_tester.py`) with a console log, and launcher `.bat` files for both programs.

---

## Requirements and files

- **Python 3.9 or newer.** On Windows use the installer from python.org and tick _Add python.exe to PATH_; Tkinter is
  included. On Linux install it with `sudo apt install python3-tk`.
- No third-party packages.

| File                               | Purpose                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `main.py`                          | The recorder application (server, recorder, post-processing, JSON export, GUI).                         |
| `session.py`                       | Non-GUI recording classes: `ArtNetServer`, `Recorder`, `FrameAssembler`, `PlsqStreamWriter`, `PlsqRecordingSession`. |
| `mapping.py`                       | Art-Net universe mapping helpers shared by `main.py` and `plsq.py` (no GUI).                            |
| `plsq.py`                          | PLSQ binary format reader, writer, and validator (no GUI, stdlib only).                                 |
| `run.bat`                          | Launches `main.py` without a console window. `run.bat debug` launches it with a console to show errors. |
| `packets_tester.py`                | Test Art-Net source: multi-universe RGBW load up to 64,000 chips / 500 universes at 60 fps.             |
| `packets_tester.bat`               | Launches `packets_tester.py` in a console that stays open.                                              |
| `tests/test_plsq.py`               | Unit tests for `plsq.py` and `mapping.py`. Run with `python -m unittest`.                               |
| `tests/test_r2.py`                 | Unit tests for R2: `write_json()` structure, 27-key meta, 3-universe block, Blueprint compat.           |
| `tests/test_r4.py`                 | Unit tests for R4: `PlsqStreamWriter` pipeline, byte-identity with `postprocess()`, queue overflow.     |
| `tests/test_r5.py`                 | Unit tests for R5: `FrameAssembler`, lost-packet counting, snapshot arity, stagger integration.         |
| `tests/test_i02_i03.py`            | Unit tests for I-02/I-03: first-frame race fix, lost-packet baseline.                                   |
| `tests/test_i08.py`                | Unit tests for I-08: `PlsqRecordingSession` lifecycle, async finalization, abort, poll timing.          |
| `tests/test_i11.py`                | Unit tests for I-11: D11 blend reduction in PLSQ and JSON paths, blend_warning in session result.       |
| `tests/test_short_packets_line.py` | Unit tests for the I-10 `short packets` statistics line formatter.                                      |
| `tests/test_first_full_frame.py`    | Unit tests: recording starts on the first complete assembled frame; 2 s timeout to a partial one. |
| `tests/test_optional_meta.py`      | Unit tests: optional meta keys `short_packets` / `first_frame_partial` (reader does not require them, writers write them). |
| `tests/test_bench_parse.py`        | Unit tests for the `bench_e2e.py` sent-packet parser (sum over all tester processes).                   |
| `tests/test_tools.py`              | Unit tests for the tools above.                                                                         |
| `tests/test_packets_tester.py`     | Unit tests for `packets_tester.py`.                                                                     |
| `tests/golden/conformance_v1.plsq` | Conformance file (spec section 14). Generated by `tools/make_golden.py`.                                |
| `tools/make_golden.py`             | Regenerate the conformance file.                                                                        |
| `tools/validate_plsq.py`           | Validate a `.plsq` file, print header/meta, exit 0 if clean.                                            |
| `tools/gen_synthetic.py`           | Generate a synthetic `.plsq` file of any size for testing.                                              |
| `tools/convert_json_to_plsq.py`    | Convert an old JSON sequence to `.plsq`.                                                                |
| `tools/bench_receiver.py`          | Receiver benchmark: measures pkt/s, lost packets, late frames, CPU.                                     |
| `tools/bench_e2e.py`               | End-to-end pipeline benchmark: full capture → write → finalize cycle.                                   |
| `docs/BENCHMARK_RECORDER.md`       | Receiver benchmark results (pre-R5, after R5) and end-to-end results with per-point acceptance.         |

---

## Quick start

1. Double-click `run.bat` (or run `python main.py`). If the window does not appear, run `run.bat debug` and read the
   error message.
2. Press **Start Server**.
3. Start your Art-Net source (TouchDesigner, Madrix 5, or `packets_tester.bat`). The statistics block must list the
   universe(s) that arrive.
4. Check the _Input mapping_ values so they match your patch.
5. Press **Start Record Input Data**. With the default duration of 6 s at 30 fps the recording stops by itself and the
   file is saved to the path at the bottom of the window.

---

## User interface

### Left column

**JSON Fields** - values that are written to the header of the file. All of them are saved as strings.

| Field                          | Meaning                                                                                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Model Name                     | Human-readable model name.                                                                                                                  |
| Model Chips Tag                | Tag of the chips of the model (for example an Unreal Engine actor tag).                                                                     |
| Model Chips Amount             | Number of chips. Together with _Channels per chip_ it defines the frame size.                                                               |
| Sequence Name                  | Name of the sequence.                                                                                                                       |
| Sequence Version               | Version string of the sequence.                                                                                                             |
| Sequence Duration (0 = manual) | Target duration in seconds. `0` means that you stop the recording by hand. After a manual stop the field is updated with the real duration. |
| Sequence Fps                   | Recording and playback frame rate — **30 or 60**, selected via radio buttons.                                                               |
| Sequence Frame Count           | Read-only. Number of frames that will be exported (duration × fps) and, after a recording, the real number.                                 |
| Sequence Frame Stride          | Read-only. Bytes per frame = chips × 4.                                                                                                     |
| Developer Notes                | Free text. **The default text is saved to every file unless you change it.**                                                                |

**Input mapping** - which Art-Net channels are packed into each frame.

| Field                         | Meaning                                                                                                                         |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Start Universe                | Art-Net universe of the first chip, as shown in the _Universes received_ list (0..32,767; default `1`).                         |
| Start channel                 | First DMX channel of the first chip, 1..512.                                                                                    |
| Channels per chip             | Fixed **4** (RGBW, D1). Read-only.                                                                                              |
| Chips per universe (0 = auto) | How many chips fit into one universe. `0` = automatic (128 for 4-channel RGBW). The effective value is shown next to the entry. |
| Universes (calculated)        | Read-only. Number of universes this layout needs, accounting for the start-channel offset. Updates live.                        |

**Post-processing (off by default)**

| Option                    | Meaning                                                                          |
| ------------------------- | -------------------------------------------------------------------------------- |
| Skip first frames (count) | Number of recorded frames to drop at the start.                                  |
| Loop blend (frames)       | Number of frames used to cross-fade the end of the recording into its beginning. |

The two entries are active only when their check box is ticked. Extra frames are recorded automatically.

If the recording is stopped early and there are fewer exported frames than the requested loop-blend length,
the blend is automatically shortened to fit (`blend_applied = min(blend, final)`) and a warning is shown in the
statistics block: **"Loop blend reduced from X to Y frames: the recording is too short"**. This is not an error;
the file is still saved. If the recording is so short that there are no exported frames at all, the recording is
kept in memory and an error is shown — the file is not saved.

**Output format** - radio buttons: `.plsq` (default) or `JSON (.json)`. Switching the format swaps the file extension
in the path field. JSON is disabled and forced back to PLSQ when the calculated universes reach 3 or more.
The selected format is authoritative: if the file path extension does not match the selected format, the
recording is refused with an error so you can correct it before anything is written.

**File path** - output file path (default `sequence_1.plsq` in the current folder). **Browse...** opens a
_Save as_ dialog filtered by the selected format.

### Right column

- **IP** and **UDP port** of the server (default `127.0.0.1` and `6454`).
- **Start Server / Stop Server** - the button turns red while the server is running.
- **Statistics block** - live status (see below).
- **Start Record Input Data / Stop Record Input Data** - enabled only while the server is running; red while recording.
- **Start recording on first packet** (check box under the record button, on by default).

### Statistics block

```
Server: RUNNING  127.0.0.1:6454
Packets: 1234  (60.0 pkt/s)  last: 0.01 s ago  lost: 0
SO_RCVBUF: 32768 KiB
Universes received (<=16):
  U1      60.0 pkt/s   512 ch
Universes received (>16 — compact):
  14 universes  U1-U14  avg 60.0 / min 59 / max 61 pkt/s

REC  75/180 frames  (2.47 s)
missing: 0   late: 0   partial src: 0
short packets: 12 (first universe 3)
Preflight: Expected U1-U14, received U1-U14 OK

Saved: D:\json_folder\sequence_1.json
180 frames, 331 KB

Errors:
[12:31:07] ...
```

- `Packets` - total ArtDMX/other packets received since the server started and the rate over the last second.
  `lost` - packets inferred missing from Art-Net sequence-number gaps (approximate: gaps > 128 are treated as reorders).
- `SO_RCVBUF` - actual UDP receive-buffer size granted by the OS (the recorder requests 32 MiB before the first receive).
- `Universes received` - for 16 or fewer universes: each universe with its packet rate and channel count.
  For more than 16 universes: a compact summary showing count, range, average/min/max rate, and up to 5 irregular
  universes (rate below 90 % of median or unusual channel count).
- `REC n/N` - recorded frames out of the planned total (planned total = skip + exported frames + blend). A line with the
  plan is shown when post-processing is on.
- `missing` - frames in which the assembler had no committed frame yet (all channels are 0 for that sample).
- `late` - frames that were sampled more than 1.5 frame periods late (the computer was too busy).
- `partial src` - source frames committed before all mapped universes arrived (a universe repeated before the set was
  complete). Non-zero only when ArtSync is not used and the source sends universes out of order.
- `short packets: N (first universe U)` - yellow line, shown only when N > 0: N ArtDMX packets carried fewer DMX
  channels than the input mapping needs for their universe; the missing channels are recorded as 0. `U` is the
  universe of the first such packet. Usually the source patch does not match _Start channel_ / _Chips per universe_.
  Counted by the frame assembler, so only when _Wait for complete source frames_ is on. Shown live during the recording and kept
  after it until the next recording starts.
- `first frame is partial` - yellow line: with _Wait for complete source frames_ and _Start recording on first packet_
  the recording waits for the first complete source frame; if none arrived within 2 s after the first (partial)
  one, the recording started on a partial frame. Universes that had not arrived yet are 0 in that frame.
- `Preflight` - shown when recording starts; lists expected vs. received universes.
- `Errors` - the last 6 errors with time stamps, shown in red. The list is cleared when a server or a recording starts
  successfully.

---

## Recording step by step

1. **Start Server.** If the port is busy, an error appears in the statistics block (see _Troubleshooting_).
2. **Start the source** and watch the `Universes received` list. Set _Start Universe_ to a universe that really arrives.
3. **Fill in the JSON fields** and set _Sequence Duration_ and _Sequence Fps_.
4. _(Optional)_ tick **Skip first frames** and/or **Loop blend**.
5. Check the **file path**.
6. Press **Start Record Input Data.**
   - With _Start recording on first packet_ the clock starts on the first complete source frame (with _Wait for
     complete source frames_ on), or when the first packet of a mapped universe arrives (option off). If no complete
     frame arrives within 2 s, the recording starts on a partial frame and shows `first frame is partial`.
   - If a file with this name already exists you are asked what to do (see below).
7. The recording **stops automatically** when the planned number of frames is reached; with duration `0` press
   **Stop Record Input Data**.
8. The file is **saved automatically** and the statistics block shows the path, the frame count and the file size.

---

## Output formats

### PLSQ (`.plsq`)

A compact binary format defined in `docs/PLSQ_SPEC.md`. The payload is a flat sequence of raw RGBW bytes: `chips × 4` bytes per frame, frames concatenated. A metadata JSON block is stored in a reserved area between the 128-byte header and the payload (offset 128) and the file is marked FINALIZED. All 27 required meta keys and the optional keys (the same set as the JSON format) are stored verbatim.

The recorder writes to a `.part` file during recording and renames it to the target path atomically on success. If an error occurs (disk full, writer crash), the `.part` file is kept and an error is shown. See [How it works → Export: PLSQ (streaming)](#7-export-plsq-streaming) for the pipeline details.

### JSON (`.json`)

Available for layouts using **1 or 2 universes** (up to 256 RGBW chips with the default 128 chips/universe). Refused for 3 or more universes. For larger installations use the PLSQ format.

The file contains the 27 required meta keys (spec §5) in the fixed order below, then the optional keys, then `"sequence"`:

```json
{
  "file_format": "json",
  "format_version": "1",
  "sequence_id": "3f2a1b4c-...",
  "recorded_at": "2026-11-15T14:32:10+03:00",
  "recorder_version": "1.2.1",
  "model_name": "Crystal Links Model 1",
  "model_chips_tag": "crystal_links_model_1_chip",
  "model_chips_count": "104",
  "sequence_name": "Random Colored Loop Flashing",
  "sequence_version": "1.0",
  "sequence_duration_sec": "6.0",
  "sequence_fps": "30",
  "sequence_frame_count": "180",
  "sequence_frame_stride": "416",
  "input_mapping_start_universe": "1",
  "input_mapping_start_channel": "1",
  "input_mapping_channels_per_chip": "4",
  "input_mapping_chips_per_universe": "128",
  "input_mapping_universes_count": "1",
  "post_skip_frames": "0",
  "post_loop_blend_frames": "0",
  "recorded_frames_raw": "180",
  "missing_frames": "0",
  "late_frames": "0",
  "partial_source_frames": "0",
  "lost_packets": "0",
  "developer_notes": "",
  "short_packets": "0",
  "first_frame_partial": "0",
  "sequence": {
    "data": [
      0.502, 0.502, ...,
      ...
    ]
  }
}
```

- All meta values are **strings**. The `sequence` key follows last.
- Optional keys (spec §5, readers must not require them; files from older recorders and the conformance file do not
  have them): `short_packets` - number of short ArtDMX packets during the recording (written only when _Wait for
  complete source frames_ is on); `first_frame_partial` - `"1"` if the recording started on a partial frame (see
  `first frame is partial`), else `"0"`. The same keys are written to `.plsq` meta.
- `sequence_id` is a UUID v4 generated at save time. `recorded_at` is the local ISO 8601 time with UTC offset.
- `sequence.data` is a **flat array of floats** in the range 0..1 (DMX byte / 255, rounded to 3 decimals). The file has
  **one frame per line**, UTF-8 encoded with `\n` line endings.
- Layout of the array (all indexes are 0-based):

```
value = data[ frame * stride + chip * channels_per_chip + channel ]
stride = chips * channels_per_chip          (sequence_frame_stride)
```

For 104 RGBW chips the stride is 416, so the colour of chip `c` in frame `f` is
`R = data[f*416 + c*4]`, `G = data[f*416 + c*4 + 1]`, `B = data[f*416 + c*4 + 2]`, `W = data[f*416 + c*4 + 3]`.

**Note for Blueprint integration**: the existing Blueprint JSON parser reads the keys it already used (`model_name`, `model_chips_count`, `sequence_fps`, `sequence_frame_count`, `sequence_frame_stride`, `sequence.data`) — these keep the same names and string formats. New keys are simply unknown to old parsers and are ignored. Verify that your Blueprint parser does not fail on unknown keys before updating saved files.

---

## How it works (algorithms)

### 1. Receiving Art-Net

A UDP socket is bound to the chosen IP and port with a 32 MiB receive buffer (`SO_RCVBUF`; the actual value granted by the
OS is shown in the statistics). The socket is read in a background thread with a 0.2 s timeout using `recvfrom_into` into
a preallocated, reused 2 KiB buffer (the per-universe copy of the data is still a new object per packet). Two packet
types are handled:

**ArtDMX (OpCode 0x5000)**

| Bytes     | Content                             |
| --------- | ----------------------------------- |
| 0-7       | ID `Art-Net\0`                      |
| 8-9       | OpCode `0x5000` (low byte first)    |
| 10-11     | Protocol version                    |
| 12        | Sequence (1..255; 0 = disabled)     |
| 13        | Physical                            |
| 14        | SubUni (low 8 bits of the universe) |
| 15        | Net (7 bits)                        |
| 16-17     | Data length (high byte first)       |
| 18 and up | DMX data                            |

`port-address = (Net << 8) | SubUni` (0-based; this is the number shown as `U<n>` in the statistics). The data is stored
as the **latest value of that universe**. The sequence byte (when non-zero) is used to count lost packets: if
`received != last % 255 + 1`, the gap is counted as lost (gaps > 128 are treated as reorders). The application does not
answer ArtPoll.

**ArtSync (OpCode 0x5200)** — triggers the frame assembler to commit the current working frame immediately if any mapped
universe data has been received since the last commit.

Packets that match neither opcode are counted as ignored.

### 2. Channel mapping

The chips are laid out one after another over universes by `build_spans`:

- with 4 channels per chip (RGBW, fixed), a universe holds **128 chips** by default (512 ÷ 4); _Chips per universe_
  lets you override this to match your source patch;
- the first universe starts at _Start channel_; every following universe starts at channel 1;
- the result is a list of spans _(universe, first channel, number of channels, offset in the frame)_, so one frame is
  assembled by copying a slice of the latest data of each universe.

Chips never straddle a universe boundary.

### 3. Fixed frame rate sampling

The recorder thread takes a snapshot of the latest data at the moments `t0 + k / fps` (`k` = 0, 1, 2, ...) measured with
`time.perf_counter()`. Because every moment is computed from `t0`, errors do not accumulate (no drift). The thread sleeps
until about 2 ms before the moment and then waits actively for better accuracy.

- This is **sample and hold**: the newest received value is used, with no interpolation. A 60 Hz source recorded at
  30 fps simply skips every second packet. The time resolution is therefore limited by the source packet rate.
- When **Wait for complete source frames** is enabled (default), the sampler reads the `committed_frame` from the
  `FrameAssembler` instead of assembling the frame from per-universe latest values. This eliminates torn frames (mixing
  channel values from two consecutive source frames) when the source spans multiple universes. If no frame has been
  committed yet, the sampler records zeros and counts the frame as `missing`.
- A frame is counted as `late` if it is taken more than 1.5 periods after its planned moment, and as `missing` if no
  committed frame is available (assembler mode) or a mapped universe has not been received yet (legacy mode).
- With _Start recording on first packet_ and the assembler on, the clock `t0` is set on the first **complete**
  commit of the assembler (all mapped universes updated, or ArtSync with the full set); frame 0 is that frame. Partial
  commits before it are ignored. If only partial commits arrive for 2 s (counted from the first one), recording
  starts on the latest partial commit and `first_frame_partial` is set (yellow line `first frame is partial`). With
  the assembler off, `t0` is set when the first packet of a mapped universe arrives.

### 4. Number of frames

- Duration `> 0`: exported frames `final = round(duration x fps)`. The recorder collects
  `skip + final + blend` frames and stops.
- Duration `= 0`: you stop manually, and `final = recorded - skip - blend`. If there are not enough frames, nothing is
  exported and an error is shown (the raw recording is kept in memory).

### 5. Post-processing

**Skip first frames.** The first `skip` frames are removed.

**Loop blend.** After the `final` exported frames, `blend` extra frames are recorded (the continuation of the animation).
The first `blend` exported frames are replaced by a linear cross-fade from that continuation to the original frames:

```
out[i] = round( tail[i] * (1 - i/N) + head[i] * (i/N) ),   i = 0 .. N-1,   N = blend
```

where `tail[i]` is the recorded frame number `final + i` (after skipping) and `head[i]` is the original frame `i`.
The first exported frame therefore equals the true continuation of the last one, so the step from the last frame to
the first one is as small as any other step, and the sequence loops without a visible jump. The remaining frames are
not changed. The blend is calculated on the 8-bit values and `blend` must not exceed the number of exported frames.

> If your animation is truly periodic and its period equals the exported duration, any window of that length loops
> seamlessly and you do not need _Loop blend_. For non-periodic animations (for example noise) use it.

### 6. Export: JSON

Every byte 0..255 is converted with a lookup table to the text of `round(byte / 255, 3)` (`0` becomes `0.0`, `255`
becomes `1.0`). The JSON text is assembled by hand to keep one frame per line, and the file is written as UTF-8.

### 7. Export: PLSQ (streaming)

The PLSQ pipeline never holds the full recording in RAM regardless of recording length.

**During recording** each captured frame is placed in a bounded in-memory queue (maximum ≈ 2 s of frames at the
selected fps). A writer thread drains the queue and appends raw RGBW bytes to a `.part` file on disk. If the queue
fills up (disk too slow), an error is set and recording stops gracefully.

**Loop blend delay-line**: the writer keeps a sliding deque of the last `blend` frames in RAM. Frames that have
scrolled out of the deque are flushed to the `.part` file immediately. Only `blend × stride` bytes stay in RAM
at any time; for 64,000 chips and 30 blend frames that is about 7.5 MiB.

**At stop**: the recorder sends a sentinel to the queue. The writer applies the cross-fade to the first `blend` frames
already on disk (read → rewrite in place), appends the metadata block, sets the FINALIZED flag, and returns.
The file is then atomically renamed from `.part` to the target path.
While the writer is finishing, the recorder button shows **Finalizing…** and both the server and record buttons
are disabled. The GUI remains responsive throughout (finalization runs on the writer thread; the UI polls it
every 100 ms).

**`.part` file**: if a `.part` file already exists when you press _Start Record_, the recorder asks whether to
overwrite it or cancel. If a write error occurs the `.part` file is left in place and reported — it is never
silently deleted.

---

## Protection of recordings and files

- **Starting a recording when the file already exists** (for example the previous recording was just saved there) opens
  a dialog with three choices: **Overwrite existing file**, **Save as new file...**, **Cancel**.
  _Save as new file..._ opens a _Save as_ dialog with a suggested free name (`sequence_1.plsq` becomes
  `sequence_2.plsq`; a name without a trailing number gets `_1`). Cancelling that dialog returns to the three choices;
  **Cancel** does not start the recording. The old file is never touched unless you choose to overwrite it.
- **A recording that could not be saved** (the save was postponed, the disk write failed, or post-processing failed) is
  kept in memory, and the reason is shown in the _Errors_ list. If post-processing fails, the raw recording is kept.
  The next **Start Record** offers _Save it now / Discard it / Cancel_, and so does closing the window.
- Pressing **Cancel** or **Don't save yet** in the dialogs never deletes anything.

---

## Test source: packets_tester

`packets_tester.py` sends RGBW ArtDMX frames at a fixed rate across any number of universes (1 chip to 64,000 chips / 500 universes). It is a load generator and a conformance source.

```
python packets_tester.py [host] [port] [chips] [universe] [options]
packets_tester.bat       [host] [port] [chips] [universe]
```

Defaults: `127.0.0.1 6454 104 1` (104 chips, 1 universe, 60 fps, rainbow pattern).

**Options:**

| Option                            | Default        | Description                                     |
| --------------------------------- | -------------- | ----------------------------------------------- |
| `--fps 30\|60`                    | `60`           | Packets per second per universe                 |
| `--chips-per-universe N`          | `0` (auto=128) | Match your source patch                         |
| `--pattern rainbow\|ramp\|static` | `rainbow`      | Colour pattern                                  |
| `--duration SECONDS`              | `0` (forever)  | Stop after N seconds                            |
| `--artsync`                       | off            | Append ArtSync (OpCode 0x5200) after each frame; with `--procs` > 1 only the main process sends it (test source limitation) |
| `--stagger-ms MS`                 | `0`            | Delay between consecutive universe sends        |
| `--procs N`                       | `1`            | Split universes across N OS processes           |

**Console log:**

```
[12:00:00] Target 127.0.0.1:6454 | chips 104 | universes 1 (U1-U1, 128 chips/uni) | 60 fps | rainbow
[12:00:00] Expected: ~60 pkt/s | Ctrl+C to stop
[12:00:01] sent 60 (60.0 pkt/s × 1 uni) | total 60 | send errors 0 (+0)
```

For 500 universes at 60 fps the expected rate is ~30,000 pkt/s. If the sender falls behind schedule by more than 1.5 frame periods, a `WARNING: X ms behind schedule` line is printed.

Invalid arguments, an unresolvable host and send errors are reported in the log. `Ctrl+C` stops the sender and prints totals.

> UDP is connectionless: the tester cannot know whether anybody is listening. `send errors 0` does not prove that the
> packets are received — check the statistics block of the recorder.

---

## Troubleshooting and notes

| Problem                                                 | What to check                                                                                                                            |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `Could not start the server ... Address already in use` | Another program (for example Unreal Engine) already listens on this IP and port. Choose another IP/port here and point the source to it. |
| Nothing arrives (`nothing received yet`)                | IP and port, the destination set in the source, and the Windows firewall prompt on the first start.                                      |
| `missing` is greater than 0                             | _Start Universe_ or the other mapping values do not match the universes in the `Universes received` list.                                |
| `late` is greater than 0                                | The computer was too busy. A few late frames are harmless; many mean that the timing is poor.                                            |
| The window does not appear with `run.bat`               | Run `run.bat debug` and read the message.                                                                                                |

- **Universe numbering.** Art-Net universes are 0-based, while some programs show them from 1. Always use the numbers in
  the `Universes received` list. The default _Start Universe_ is 1, to match the bundled tester.
- Only **ArtDMX** is supported (no sACN). Values have **8-bit** resolution; 16-bit channel pairs are stored as two
  separate values.
- **Developer Notes** has a default text that is written into every file unless you edit it.
- Errors are shown in the statistics block, not in pop-up windows. Pop-up dialogs are used only for choices about files
  and unsaved recordings.
- Benchmark results (pkt/s, CPU, lost packets, torn frames, per-point acceptance) for the full chip/fps matrix are in
  `docs/BENCHMARK_RECORDER.md`. The run with `--artsync --stagger-ms` is a stress scenario of the test source, not an
  acceptance point.

---

## Building an .exe

```
pip install pyinstaller
pyinstaller --onefile --noconsole main.py
```

The executable appears in the `dist` folder.
