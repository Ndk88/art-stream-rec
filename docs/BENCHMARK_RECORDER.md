# Receiver Benchmark Report

## Runs

| Run | Date | Code state | Note |
|-----|------|------------|------|
| Baseline | 2026-11-15 | pre-R5 | Current `ArtNetServer` + `Recorder` core, no optimisations |
| After R5  | 2026-11-15 | post-R5 | `recvfrom_into`, SO_RCVBUF 32 MiB, `FrameAssembler`, lost-packet counter |
| E2E final | 2026-11-15 | after I-02..I-11 + final pass | `bench_e2e.py`, full pipeline, 60 s, content check (section at the end) |

---

## Machine (baseline)

| Field | Value |
|-------|-------|
| OS | Windows 11 Home Single Language 10.0.26300 |
| CPU | Intel Core i7-13700H (Intel64 Family 6 Model 154 Stepping 3) |
| Architecture | AMD64 (64-bit) |
| Python | 3.13.5 (64-bit, CPython) |

---

## Methodology

`tools/bench_receiver.py` starts `ArtNetServer` + a frame sampler (identical logic to `Recorder._capture`, but frames are discarded immediately so RAM stays flat) on port 7454. `packets_tester.py` is launched as a child process with `--pattern ramp --duration 10`. After the sender finishes, a 0.3 s drain window elapses and then both server and sampler are stopped.

**Packet-rate measurement window**: first to last received packet (excludes sender startup delay, which is 0.1 s for 1 sender process, 1.5 s for 2–4 processes). The percentage column compares this rate to the theoretical maximum (`universes × fps`).

**Lost packets**: counted from Art-Net sequence-number gaps per universe (`expected = last % 255 + 1`). Apparent gaps > 128 are treated as reorders and not counted as losses (approximate; documented limitation).

**Late frames**: sampler woke up more than 1.5 × period late (same definition as `Recorder.late`).

**Missing frames**: at least one mapped universe had no data at sample time. At start-up all universes are initially missing; the first N missing frames correspond to the sender warm-up period (0.1 s × fps for 1 proc, 1.5 s × fps for 2–4 procs).

**SO_RCVBUF**: requested 32 MiB before the first `recvfrom` call; actual value read back with `getsockopt`.

**CPU**: `time.process_time()` delta for the benchmark process only (receiver side; sender is a separate OS process).

Each point ran for **10 seconds** of active sending.

---

## Baseline results (pre-R5)

| chips | fps | universes | recv pkt/s | expected | recv % | lost (seq) | late % | missing % | CPU / 10 s |
|------:|----:|----------:|-----------:|---------:|-------:|-----------:|-------:|----------:|:----------:|
| 1,700 | 30 | 14 | 421 | 420 | 100.3 % | 0 | 0.00 % | 1.59 % | 0.64 s |
| 1,700 | 60 | 14 | 841 | 840 | 100.2 % | 0 | 0.00 % | 1.75 % | 1.02 s |
| 10,000 | 60 | 79 | 4,748 | 4,740 | 100.2 % | 0 | 0.00 % | 1.75 % | 0.95 s |
| 32,000 | 60 | 250 | 15,025 | 15,000 | 100.2 % | 0 | 0.00 % | 13.31 % | 1.52 s |
| 64,000 | 30 | 500 | 15,048 | 15,000 | 100.3 % | 0 | 0.00 % | 13.45 % | 1.03 s |
| **64,000** | **60** | **500** | **30,045** | **30,000** | **100.1 %** | **0** | **0.00 %** | **13.31 %** | **2.62 s** |

**SO_RCVBUF**: requested 32 MiB, actual 32 MiB (Windows honored the request on this machine).

---

## Observations

### 1. Receiver is not the bottleneck

The receiver accepted **100 % of sent packets** at every matrix point, including the worst case of 500 universes × 60 Hz = **30,000 pkt/s**. Zero sequence gaps were detected, meaning no packets were dropped by the OS socket buffer or the application receive loop.

### 2. Sampler keeps its schedule

Zero late frames across all matrix points. The drift-free `target = t0 + k × period` scheduler inside `Recorder._capture` holds its timing even under a 30,000 pkt/s inbound load.

### 3. CPU headroom

At 64,000 chips / 60 fps the receiver consumed **2.62 s of CPU per 10 s wall time** (≈ 26 %). The GIL is not preventing progress; the receive thread and the sampler thread interleave without measurable conflict at this load.

### 4. Missing frames during sender warm-up

The 13.3–13.5 % missing-frame rate for 32,000–64,000 chips reflects the **sender startup delay**, not a receiver issue:
- 1 sender process: `startup_secs = 0.1 s` → ~6 missing frames at 60 fps (1.75 %)
- 2–4 sender processes: `startup_secs = 1.5 s` → ~90 missing frames at 60 fps (13.3 %)

During the active sending phase (after warm-up) **all universes are present in every sample**. In a real recording session the sampler waits for the first packet before starting the frame clock (`wait_first_packet=True`, the GUI default), which eliminates this effect. R5 will also add a frame assembler that commits only when all mapped universes have been updated.

### 5. Acceptance targets

The R8 acceptance targets for 64,000 chips / 60 fps:

| Target | Pre-R5 result | Status |
|--------|--------------|--------|
| Zero lost packets | 0 | ✓ PASS |
| `late_frames` below 0.5 % | 0.00 % | ✓ PASS |
| Recorder process stays responsive | CPU 26 % | ✓ PASS |

**All targets are met by the baseline code.** R5 receiver optimisations (large SO_RCVBUF, `recvfrom_into`, pre-allocated buffers, frame assembler, lost-packet counter) are still valuable for correctness and robustness, but the raw throughput and latency targets are already satisfied by the current implementation on this hardware.

---

## Results after R5 (2026-11-15, 10 s each)

| chips | fps | universes | recv pkt/s | expected | recv % | lost (seq) | late % | missing % | CPU / 10 s |
|------:|----:|----------:|-----------:|---------:|-------:|-----------:|-------:|----------:|:----------:|
| 1,700 | 30 | 14 | 421 | 420 | 100.3 % | 0 | 0.00 % | 1.90 % | 0.52 s |
| 1,700 | 60 | 14 | 841 | 840 | 100.2 % | 0 | 0.00 % | 1.75 % | 0.97 s |
| 10,000 | 60 | 79 | 4,748 | 4,740 | 100.2 % | 0 | 0.00 % | 1.75 % | 1.23 s |
| 32,000 | 60 | 250 | 15,024 | 15,000 | 100.2 % | 0 | 0.00 % | 13.31 % | 1.45 s |
| 64,000 | 30 | 500 | 15,048 | 15,000 | 100.3 % | 0 | 0.00 % | 13.45 % | 1.20 s |
| **64,000** | **60** | **500** | **30,047** | **30,000** | **100.2 %** | **0** | **0.00 %** | **13.43 %** | **2.14 s** |

**SO_RCVBUF**: requested 32 MiB, actual 32 MiB (unchanged — Windows honored it at baseline too).

### Changes vs baseline

| Metric | Baseline | After R5 | Delta |
|--------|---------|---------|-------|
| Peak pkt/s (64k/60fps) | 30,045 | 30,047 | ≈ same |
| Peak CPU / 10 s | 2.62 s | 2.14 s | −18 % |
| Lost packets | 0 | 0 | — |
| Late frames | 0 | 0 | — |

CPU dropped 18 % at peak load: `recvfrom_into` with a preallocated `bytearray(2048)` buffer eliminates per-packet allocation, reducing pressure on the allocator and GC.

The `missing_frames` percentage at the multi-process matrix points (13.3–13.5 %) is unchanged because the benchmark tool does not use the `FrameAssembler` (it tracks the bench-tool's own sampler, not the recorder's assembler). In a real recording session the assembler eliminates missing frames during steady-state: it waits for all mapped universes before committing a frame and the sampler only reads committed frames.

---

## How to reproduce

```
# Single point
python tools/bench_receiver.py --chips 64000 --fps 60 --duration 10

# Full matrix (10 s each, ~2 min)
python tools/bench_receiver.py --matrix --duration 10

# Full matrix (60 s each, ~6 min)
python tools/bench_receiver.py --matrix --duration 60
```

---

## End-to-end baseline (pre-I-02..I-11, 2026-11-15, 10 s each)

Measured with `tools/bench_e2e.py --quick --no-content-check` on `dev/v1.2.0` immediately after I-01 (session.py extraction + bench_e2e creation).  Unlike `bench_receiver.py`, this tool runs the full pipeline: `ArtNetServer → FrameAssembler → Recorder → PlsqStreamWriter` with an atomic-rename finalize; results are PLSQ files validated by `plsq.validate()`.

| Label | chips | fps | lost | late % | missing % | torn | zero | validate |
|-------|------:|----:|-----:|-------:|----------:|-----:|-----:|----------|
| 1700/30 | 1,700 | 30 | 0 | 0.00 % | 0.33 % (1 f) | 0 | 0 | OK |
| 1700/60 | 1,700 | 60 | 0 | 0.00 % | 0.17 % (1 f) | 0 | 0 | OK |
| 10000/60 | 10,000 | 60 | 0 | 0.00 % | 0.17 % (1 f) | 0 | 0 | OK |
| 32000/60 | 32,000 | 60 | 0 | 0.00 % | 0.17 % (1 f) | 0 | 0 | OK |
| 64000/30 | 64,000 | 30 | 0 | 0.00 % | 0.33 % (1 f) | 0 | 0 | OK |
| **64000/60** | **64,000** | **60** | **0** | **0.00 %** | **0.17 % (1 f)** | **0** | **0** | **OK** |
| 64k/60 +artsync+stagger | 64,000 | 60 | 0 | 0.00 % | 0.83 % (5 f) | 0 | 0 | OK |
| 64k/60 no-assembler | 64,000 | 60 | 0 | 0.00 % | 0.00 % | 0 | 0 | OK |

**Queue**: never exceeded 1 frame / 300 capacity at any matrix point.

**Known pre-fix issues visible in this baseline:**

| Issue | Effect in table |
|-------|----------------|
| I-02: first-frame race | 1 missing/zero frame at every size (FrameAssembler not yet seeded before Recorder starts sampling) |
| artsync+stagger had extra missing frames | 5 missing frames at 64k/60; root cause under investigation |

**Acceptance targets (64,000 chips / 60 fps):**

| Target | Result | Status |
|--------|--------|--------|
| Zero lost packets | 0 | ✓ PASS |
| `late_frames` below 0.5 % | 0.00 % | ✓ PASS |
| Zero torn frames | 0 | ✓ PASS |
| File validates | OK | ✓ PASS |

All R8 acceptance targets are met by the pre-I-02 code on this hardware.  The 1-frame missing/zero issue will be fixed by I-02 (seed the assembler before starting the Recorder).

```
# Reproduce this baseline
python tools/bench_e2e.py --quick --no-content-check
# Single point with content check
python tools/bench_e2e.py --chips 64000 --fps 60 --duration 10
```

---

## End-to-end results after I-02..I-11 (final pass, 2026-11-15, 60 s each)

Measured with `tools/bench_e2e.py --matrix --duration 60 --out-dir .bench_out` (content check on) on `dev/v1.2.0`
at commit `fefa9a8` (after A1: recv % fix, Win32 CPU/memory fix, A2, A3, A4). Machine: Windows 11 10.0.26300,
Intel Core i7-13700H, Python 3.13.5. Pipeline: `ArtNetServer → FrameAssembler → Recorder → PlsqRecordingSession →
PlsqStreamWriter` with atomic-rename finalize. Output files were written to `.bench_out/` and deleted afterwards.

**"Before" run (commit `c141c8e`): not done.** No compatible run exists (`session.py` and the current `bench_e2e.py`
do not exist at that commit). The I-02 race is proven by a deterministic test instead (REPORT_I02.md: the current
`TestFirstFrameRace` fails 4/4 on `c141c8e`, passes on the fixed code).

**Column definitions**

- `sent`: packets sent by **all** tester processes (sum of each process's final count, A1).
- `recv %`: packets counted by `ArtNetServer` over the whole run / `sent`. A delivery ratio, not a rate.
- `lost`: sequence-gap losses during the recording (file meta `lost_packets`).
- `late`, `missing`: recorder counters (file meta), % of 3,600 (60 fps) or 1,800 (30 fps) frames.
- `partial`: assembler partial commits (`partial_source_frames`).
- `torn`: recorded frames that are not equal to any frame of the tester's ring; `zero`: all-zero frames.
- `queue`: maximum writer-queue depth / capacity.
- `CPU`: CPU time of the bench process (receiver + assembler + recorder + writer; the tester is a separate process)
  during the recording. `mem`: working-set change from the 10 s mark to the end.

| Point | procs | sent | recv % | lost | late % | missing % | partial | torn | zero | queue | CPU | mem | validate |
|-------|------:|-----:|-------:|-----:|-------:|----------:|--------:|-----:|-----:|------:|----:|----:|----------|
| 1700/30 | 1 | 26,474 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | 1/300 | 3.9 s | -0.0 MB | OK |
| 1700/60 | 1 | 52,934 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | 1/300 | 7.5 s | +0.0 MB | OK |
| 10000/60 | 1 | 298,699 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | 1/300 | 4.5 s | +0.1 MB | OK |
| 32000/60 | 2 | 945,250 | 100.00 % | 0 | 0.00 % | 0.00 % | 24 | **27** | 0 | 1/300 | 11.5 s | +0.0 MB | OK |
| 64000/30 | 4 | 945,500 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | 1/300 | 6.8 s | +0.1 MB | OK |
| **64000/60** | 4 | **1,890,500** | **100.00 %** | **0** | **0.00 %** | **0.00 %** | **0** | **0** | **0** | **1/300** | **15.0 s** | **+0.1 MB** | **OK** |
| 64k/60 +artsync+stagger (stress, test source; not an acceptance point) | 4 | 427,854 | 100.00 % | 0 | 0.00 % | 0.00 % | 897 | **3,516** | 0 | 1/300 | 12.7 s | +0.5 MB | OK |
| 64k/60 no-assembler | 4 | 1,890,500 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | 1/300 | 15.1 s | -0.7 MB | OK |

Every point wrote exactly the expected number of frames (3,600 or 1,800, diff +0). SO_RCVBUF was not printed by
`bench_e2e` (not measured in this run).

### 10000/60: torn not reproduced

The earlier report had 3,600 torn of 3,600 frames at 10000/60. In this pass 10000/60 was run three times with
content check (two separate 60 s runs for A4, plus the matrix run): **torn = 0 every time**. Not reproduced. The
previous explanation ("stride of the last incomplete universe") is not confirmed by any measurement and has been
removed.

### 32000/60: 27 torn frames in the matrix run (fact)

- Diagnostics of the first torn frame: frame #0. Universes U1..U84 match ring frame 4; universes U85..U250 match
  ring frame 3 (42,496 differing bytes from the nearest ring frame, first one at byte 43,008 = U85 byte 0).
- The tester ran in 2 processes (U1..U125 and U126..U250). The split at U85 is **not** at the process boundary.
- The assembler made 24 partial commits in this run.
- Hypothesis: the two tester processes send their halves independently; when one drifts by part of a frame
  relative to the other, a universe repeats before the set is complete and the assembler commits a partial frame
  (rule (c) in `FrameAssembler`) that mixes two source frames. The first torn frame is frame #0, so the recorder
  also started on such a partial commit (`wait_first_packet` waits for any `committed_frame`, partial or not).

### Stress scenario 64k/60 +artsync+stagger (test source; not an acceptance point)

Test source: with `--procs` > 1 only the main `packets_tester.py` process sends ArtSync. This run is kept as a
stress scenario and is **not** part of the acceptance table below. Result: 3,516 torn frames with ArtSync and the
assembler on (fact).


- `--stagger-ms 0.05` x 500 universes = 25 ms per frame > 16.7 ms frame period: the tester printed 853 "behind
  schedule" warnings and sent 427,854 packets in total, vs 1,890,500 without stagger.
- 897 partial commits. Diagnostics of the first torn frame: frame #0; universes U1..U5 (and more) match no ring
  frame; byte 1 is 0 where ring frame 0 has 1, i.e. these universes had not been received yet when the frame was
  committed and are still zero in the working buffer.
- Hypothesis: ArtSync is sent only by the main tester process, right after its own group (U376..U500); the three
  worker processes send their groups without sync and, with the stagger, at a different pace. Each ArtSync then
  commits a frame whose universes come from different source frames (and, in frame #0, some not received at all).
  This is a property of the test source, not confirmed against a real ArtSync source (see manual check M2).

### 32000/60 re-runs after Q1 (first complete frame), 2026-11-16

After Q1 (commit `a866282`: with the assembler and "start on first packet" the recording starts on the first
complete assembled frame, 2 s timeout) and Q3 (`7f48a24`), 32000/60 was run three more times, 60 s each, content
check on, at commit `7f48a24`:

```
python tools/bench_e2e.py --chips 32000 --fps 60 --duration 60 --out-dir .bench_out   (x3)
```

| Run | sent / recv | recv % | lost | late % | missing % | partial | torn | zero | frame #0 torn | queue | CPU | validate | meta `short_packets` / `first_frame_partial` |
|-----|-------------|-------:|-----:|-------:|----------:|--------:|-----:|-----:|:-------------:|------:|----:|----------|----------|
| 1 | 945,250 / 945,250 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | no | 1/300 | 8.4 s | OK | `"0"` / `"0"` |
| 2 | 945,250 / 945,250 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | no | 1/300 | 11.7 s | OK | `"0"` / `"0"` |
| 3 | 945,250 / 945,250 | 100.00 % | 0 | 0.00 % | 0.00 % | 0 | 0 | 0 | no | 1/300 | 10.0 s | OK | `"0"` / `"0"` |

Tester: 2 processes, no "behind schedule" warnings. **Limitation:** in all three re-runs the assembler made no
partial commit at all (`partial` = 0, vs 24 in the matrix run), so the situation that Q1 fixes (a partial commit
before the first complete one) did not occur. The re-runs show 0 torn, but they do not prove that Q1 removed the 27
torn frames of the matrix run; the Q1 behavior is covered by unit tests (`tests/test_first_full_frame.py`).

### Acceptance (per point)

Targets: zero lost packets, `late` below 0.5 %, zero missing frames, zero torn frames, file validates. The stress
scenario 64k/60 +artsync+stagger is not an acceptance point (see above).

| Point | lost = 0 | late < 0.5 % | missing = 0 | torn = 0 | validate | Result |
|-------|:--------:|:------------:|:-----------:|:--------:|:--------:|--------|
| 1700/30 | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |
| 1700/60 | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |
| 10000/60 | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |
| 32000/60, matrix run (before Q1) | ✓ | ✓ | ✓ | ✗ (27) | ✓ | **FAIL (torn)** |
| 32000/60, 3 re-runs after Q1 | ✓ | ✓ | ✓ | ✓ (0, 0, 0) | ✓ | PASS |
| 64000/30 | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |
| 64000/60 | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |
| 64k/60 no-assembler | ✓ | ✓ | ✓ | ✓ | ✓ | PASS |

The no-assembler point has 0 torn in this run (the earlier report had 18); with the assembler off, torn frames are
possible by design, this run just did not catch one.

```
# Reproduce (60 s per point, content check on, ~10 min, ~2.2 GB of temporary files)
python tools/bench_e2e.py --matrix --duration 60 --out-dir .bench_out
# Single point
python tools/bench_e2e.py --chips 10000 --fps 60 --duration 60 --out-dir .bench_out
```

Note: `--quick` always uses 10 s and overrides `--duration`; the command `--quick --duration 60` given in the
previous version of this section runs 10 s per point.
