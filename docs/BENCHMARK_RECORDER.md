# Receiver Benchmark Report

## Runs

| Run | Date | Code state | Note |
|-----|------|------------|------|
| Baseline | 2026-11-15 | pre-R5 | Current `ArtNetServer` + `Recorder` core, no optimisations |
| After R5  | 2026-11-15 | post-R5 | `recvfrom_into`, SO_RCVBUF 32 MiB, `FrameAssembler`, lost-packet counter |

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
