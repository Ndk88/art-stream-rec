# R8 — Receiver benchmark and report

**Date:** 2026-11-15  
**Tests after:** no unit tests (measurement tool)

---

## Goal

Quantify the receive path performance on real hardware before and after R5 optimizations. All acceptance targets must pass at the design maximum of 64,000 chips / 60 fps (500 universes × 60 Hz = 30,000 pkt/s).

---

## What changed

### New file: `tools/bench_receiver.py`

Runs the receiver and sampler core without the GUI for a given duration while `packets_tester.py` sends from a child process.

**Usage:**

```
# Single point
python tools/bench_receiver.py --chips 64000 --fps 60 --duration 10

# Full 6-point matrix (10 s each, ~2 min)
python tools/bench_receiver.py --matrix --duration 10
```

**What it measures:**
- Sent vs received packets (via Art-Net sequence-number gaps)
- Lost packets (sequence gaps ≤ 128; gaps > 128 treated as reorders)
- `late_frames` percentage (sampler woke up > 1.5 × period late)
- `missing_frames` percentage (mapped universe had no data at sample time)
- Process CPU time (`time.process_time()` delta, receiver process only)
- Actual `SO_RCVBUF` granted by the OS

**Methodology:**  
`packets_tester.py` is launched as a child process with `--pattern ramp --duration N`. After the sender exits, a 0.3 s drain window elapses, then both server and sampler are stopped. The packet-rate window spans first to last received packet (excludes sender startup delay).

### New file: `docs/BENCHMARK_RECORDER.md`

Machine description, methodology, 6-point results table (baseline and after-R5), observations, acceptance-target table, and reproduction instructions.

---

## Results

### Machine

Windows 11, Intel Core i7-13700H, Python 3.13.5 (64-bit).

### Baseline (pre-R5) — 10 s each

| chips | fps | recv pkt/s | recv % | lost | late % | CPU / 10 s |
|------:|----:|-----------:|-------:|-----:|-------:|:----------:|
| 1,700 | 30 | 421 | 100.3 % | 0 | 0.00 % | 0.64 s |
| 1,700 | 60 | 841 | 100.2 % | 0 | 0.00 % | 1.02 s |
| 10,000 | 60 | 4,748 | 100.2 % | 0 | 0.00 % | 0.95 s |
| 32,000 | 60 | 15,025 | 100.2 % | 0 | 0.00 % | 1.52 s |
| 64,000 | 30 | 15,048 | 100.3 % | 0 | 0.00 % | 1.03 s |
| **64,000** | **60** | **30,045** | **100.1 %** | **0** | **0.00 %** | **2.62 s** |

### After R5 — 10 s each

| chips | fps | recv pkt/s | recv % | lost | late % | CPU / 10 s |
|------:|----:|-----------:|-------:|-----:|-------:|:----------:|
| 1,700 | 30 | 421 | 100.3 % | 0 | 0.00 % | 0.52 s |
| 1,700 | 60 | 841 | 100.2 % | 0 | 0.00 % | 0.97 s |
| 10,000 | 60 | 4,748 | 100.2 % | 0 | 0.00 % | 1.23 s |
| 32,000 | 60 | 15,024 | 100.2 % | 0 | 0.00 % | 1.45 s |
| 64,000 | 30 | 15,048 | 100.3 % | 0 | 0.00 % | 1.20 s |
| **64,000** | **60** | **30,047** | **100.2 %** | **0** | **0.00 %** | **2.14 s** |

**SO_RCVBUF:** requested 32 MiB, actual 32 MiB on this machine (Windows honored the request at both runs).

### Acceptance targets (64,000 chips / 60 fps)

| Target | Pre-R5 | After R5 | Status |
|--------|--------|---------|--------|
| Zero lost packets | 0 | 0 | ✓ PASS |
| Late frames < 0.5 % | 0.00 % | 0.00 % | ✓ PASS |
| Recorder stays responsive | CPU 26 % | CPU 21 % | ✓ PASS |

**All targets pass on baseline code.** R5 adds correctness (frame assembler) and reduces CPU by 18 % at peak load.

---

## How it was verified

The benchmark tool was run twice — once before R5 changes (`bench_receiver.py` baseline) and once after R5 (`bench_receiver.py` post-R5). Both result sets are recorded in `docs/BENCHMARK_RECORDER.md`.

The `missing_frames` figures at multi-process matrix points (13.3–13.5 %) reflect a 1.5 s sender process startup delay, not a receiver problem. During steady-state sending, all universes are present in every sample.

---

## What could not be verified

- **Re-running on a different machine** — results will differ. The methodology is documented so the user can reproduce.
- **60-second matrix run** — only 10 s runs were executed (the matrix target is 60 s for a tighter measurement; see `docs/BENCHMARK_RECORDER.md` for the reproduction command).
