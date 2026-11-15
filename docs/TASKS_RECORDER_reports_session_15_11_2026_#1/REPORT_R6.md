# R6 — Extend `packets_tester.py`

**Date:** 2026-11-15  
**Tests after:** 38 (all pass) — `tests/test_packets_tester.py`

---

## Goal

A realistic Art-Net load generator that can reach 500 universes × 60 Hz (≈ 30,000 pkt/s) and provoke the conditions the recorder must survive: torn frames (via `--stagger-ms`), ArtSync-driven commit (via `--artsync`), and multi-process sends (via `--procs`).

---

## What changed

`packets_tester.py` was substantially extended. Positional arguments (`host port chips universe`) are unchanged so the existing `packets_tester.bat` launcher still works.

### Key additions

**Chip/universe range extended to design maximum.**  
Chips: 1..64,000. Universe: 0..32,767. Layout computed by the shared `build_spans` from `mapping.py` (not an independent copy).

**New command-line options:**

| Option | Default | Description |
|---|---|---|
| `--fps 30\|60` | `60` | Target packets per second per universe |
| `--chips-per-universe N` | `0` (auto = 128) | Match the recorder's patch override |
| `--pattern rainbow\|ramp\|static` | `rainbow` | Colour pattern |
| `--duration SECONDS` | `0` (forever) | Stop after N seconds |
| `--artsync` | off | Append one ArtSync packet (OpCode 0x5200) after each complete frame |
| `--stagger-ms X` | `0` | Delay between consecutive universe sends within one frame |
| `--procs N` | `1` | Split universes across N OS processes for high loads |

**Performance: ring-buffer design.**  
One ring of `fps × 2` precomputed ArtDMX `bytearray` packets is built at startup. Each frame is derived from the base pattern by rotating a slice (two-slice concatenation, no per-byte Python loop). The sequence byte is written directly into each packet's byte at `SEQ_OFFSET` before sending. The drift-free `target = t0 + k/fps` scheduler from the original code is kept.

**Behind-schedule warning.**  
If the sender falls more than 1.5 × period behind schedule, a `WARNING: X ms behind schedule` line is printed. This distinguishes a slow sender from a slow receiver.

**Art-Net sequence numbers.**  
Per-universe, 1..255 wrapping, never 0 (0 means disabled per the Art-Net spec). The receiver's lost-packet counter relies on these.

**ArtSync packet constant.**  
`ARTSYNC` is a 14-byte `bytes` constant matching OpCode 0x5200; appended after each full frame when `--artsync` is active.

**Console log** format unchanged: once per second, packets sent, rate, and errors.

---

## How it was verified

**Unit tests — `tests/test_packets_tester.py` (38 tests):**

| Class | Coverage |
|---|---|
| `TestArtSyncConst` | Correct magic (`Art-Net\0`), OpCode `0x5200`, packet length 14 bytes. |
| `TestMakeArtdmxBytearray` | Valid Art-Net header bytes, opcode, universe low/high byte split, protocol version, data length even, payload data. |
| `TestBuildBase` | Ramp pattern starts at zero, correct length; rainbow W channel is zero, RGB channels nonzero for chip 1+; static pattern all 128. |
| `TestBuildRing` | Ring size = `fps × 2`; each entry has correct frame length; rotation produces distinct adjacent frames; static pattern returns same object for all ring entries (no copy). |
| `TestPrecomputePackets` | Shape of the precomputed packet array (frames × universes). |
| `TestSendLoop` | 1-universe send loop delivers at least 1 packet; received ArtDMX magic is correct; returned tuple contains `(sent, errors)`. |

**Additional checks in tests:**
- `test_artsync_opcode` — the constant bytes contain OpCode `0x5200` at the correct offset.
- `test_sequence_placeholder_zero` — raw precomputed packets have `0` at `SEQ_OFFSET`; writability confirmed.
- `test_sequence_bytes_monotonic` — sequence bytes in a live send are 1..255 monotonically increasing without 0.
- Validation of argument bounds: chips 1..64000, universe 0..32767, procs ≥ 1, stagger-ms ≥ 0, port 1..65535.

---

## What could not be verified

- **Actual packet rate at 500 universes / 60 Hz on the user's machine** — the test sends only a small burst (1 universe, default options). Whether the full load reaches 30,000 pkt/s without behind-schedule warnings depends on the network stack and OS scheduler.
- **`--stagger-ms` provoking torn frames in the recorder** — the visual effect and the assembler's response to staggered universes was verified in R5's integration test (`TestStaggerWithArtSync`) which confirms `partial_source_frames == 0` with ArtSync.
- **`--procs N` multi-process behavior on Windows** — `multiprocessing.Process` with `spawn` start method (Windows default). The process split and coordination logic was not tested under Windows specifically; the unit tests cover the single-process code path.
- **`packets_tester.bat` with new options** — the bat file passes `%*` through, so new options are forwarded. This was not interactively tested.
