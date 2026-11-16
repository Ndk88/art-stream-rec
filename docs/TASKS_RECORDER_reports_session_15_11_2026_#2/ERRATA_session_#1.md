# Errata: session #1 reports

Corrections to `docs/TASKS_RECORDER_reports_session_15_11_2026_#1/`.  
The original files are not edited — corrections go here.

---

## 1. R6: "Windows not tested" for console output paths

The session #1 R6 report noted that Windows-specific behaviour was not verified for some output paths.

**Finding in session #2 (I-01, commit `c141c8e`):** `packets_tester.py` line 295 contained the Unicode character `×` (U+00D7, MULTIPLICATION SIGN). On Windows consoles with encoding CP1251 (the default on some Russian-language Windows installations), printing this character via `print()` raises:

```
UnicodeEncodeError: 'charmap' codec can't encode character '\xd7' in position 32: character maps to <undefined>
```

This caused the `--artsync --stagger-ms` extra matrix run in `bench_e2e` to crash on startup before sending any packets. The character was replaced with ASCII `x`.

**Affected path:** `packets_tester.py`, function `main()`, the stagger log line.  
**Fix:** `c141c8e I-01: fix packets_tester Unicode crash, fix _parse_sent, add e2e baseline doc`

---

## 2. R1: header size is 128 bytes, not 136

`REPORT_R1.md` (table of `PlsqWriter` methods) says `open()` "writes the fixed 136-byte header".

**Checked (final pass):** `plsq.py` has `HEADER_SIZE = 128`; `_HDR_FMT.size` = 128; `docs/PLSQ_SPEC.md` section 3
"Header (128 bytes)"; `validate_plsq.py` on the golden file prints `header_size: 128`, `meta_offset: 128`.
The code and the spec agree; only the report text was wrong.

---

## 3. R7: golden file is 30 fps and start universe 1; file size formula

`REPORT_R7.md` describes `tools/make_golden.py` as "130 chips x 10 frames, **1 fps, start universe 0**" and the file
size as "69,632 (payload_offset) + 10 x 520 (frames) + metadata block + header update".

**Checked (final pass):** `tools/make_golden.py` has `FPS = 30`, `START_UNIVERSE = 1` (`START_CHANNEL = 1`,
`CHIPS_PER_UNIVERSE = 128`, `UNIVERSES_COUNT = 2`). `python tools/validate_plsq.py tests/golden/conformance_v1.plsq`
prints `fps: 30`, `start_universe: 1`, size 74,832 bytes. The size is exactly
`payload_offset + frame_count x frame_stride` = 69,632 + 10 x 520 = 74,832. The meta block is **not** added on top:
it lives inside the reserved area before the payload (`meta_offset` 128, `meta_size` 826).

---

## 4. R4: loop-blend time is about 0.9 s at 64,000 chips, not 0.08 s

`REPORT_R4.md` says: "For 64,000 chips x 30 blend frames = 7.68 M iterations ≈ 0.08 s".

**Measured (final pass)** on the same machine (Windows 11, i7-13700H, Python 3.13.5), time of
`session._blend_frames()` only (the per-byte loop; reading the head frames back from the `.part` file and rewriting
them are not included):

| chips | blend frames | bytes | `_blend_frames` |
|------:|-------------:|------:|----------------:|
| 1,700 | 30 | 204,000 | 0.02 s |
| 64,000 | 30 | 7,680,000 | 0.88 s |
| 64,000 | 60 | 15,360,000 | 1.75 s |

The "0.08 s" figure is off by about 11x. The result is still well under the 10 s safety limit of
`PlsqStreamWriter`, so no code change follows from this.

---

## 5. R5: "zero per-packet allocation" is not true

`REPORT_R5.md` says the receive loop uses `recvfrom_into` into a preallocated buffer with "zero per-packet allocation"
and "`struct.unpack_from` on the preallocated buffer — no object creation per packet".

**Checked in the code** (session #1 end, commit `8ab3747`, `main.py`; the same lines are now in `session.py`):

- `struct` is imported, but `struct.unpack_from` is **not used** anywhere in the receive loop. Header fields are read
  by indexing the `bytearray`.
- Per ArtDMX packet the loop still creates objects:
  - `sock.recvfrom_into()` returns a new `(n, addr)` tuple;
  - `padded = bytes(_mv[18:18 + cap]) + b"\x00" * (512 - cap)` creates a memoryview slice, a `bytes` copy, a padding
    `bytes` object and the concatenated result;
  - `self.uni_stats.setdefault(port_address, {"count": 0, "length": 0})` builds a new dict literal on every call,
    even when the key already exists;
  - in `FrameAssembler.on_universe`, `mv[src_start:src_end]` creates a memoryview slice;
  - per committed frame, `FrameAssembler._commit` creates `bytes(self._working)` and a new `set()`.

What is correct: the receive buffer itself (`bytearray(2048)`) is preallocated and reused. The measured CPU
reduction in R8 (−18 % at 64k/60) is a measurement of `bench_receiver.py` and is not affected by this wording, but
it cannot be attributed to "zero allocation".

---

## 6. R8: "recv %" is a rate, and the runs were 10 s

`REPORT_R8.md` and the R8 tables in `docs/BENCHMARK_RECORDER.md` show "recv %" values of 100.1-100.3 %.

**Checked in `tools/bench_receiver.py`:** `recv %` = `received / send_window` (packets per second between the first
and the last received packet) divided by `universes x fps`. It is a **rate inside a time window**, not a delivery
ratio (received / sent): a lost tail, a lost start or a silent universe does not show up in it, and values above
100 % are normal (window shorter than the nominal duration). All R8 points ran **10 s** each
(`--duration 10`); there was no 60 s R8 run.

A delivery ratio exists since the final pass in `tools/bench_e2e.py` (A1: received / sum of packets sent by all
tester processes); see the final-pass section of `docs/BENCHMARK_RECORDER.md`.

---

## 7. Test counts in session #1 reports

**Checked (final pass):** the code of commit `8ab3747` (end of session #1) was exported with `git archive` and the
suite was run there:

```
python -m unittest discover -q          -> Ran 181 tests ... OK
tests.test_plsq            -> Ran 44     tests.test_r2    -> Ran 28
tests.test_r4              -> Ran 18     tests.test_r5    -> Ran 29
tests.test_packets_tester  -> Ran 38     tests.test_tools -> Ran 24
tests.test_plsq.TestErrorCodes -> Ran 25
```

Discrepancies between the reports:

1. The "Tests after" line means different things in different reports. R1, R2, R4, R5, R6 and R7 give the number of
   tests **in one file** (44, 28, 18, 29, 38, 24); R3 and R9 give the **whole suite** (181). No report gives the
   suite total at the moment its own task was finished. The per-file numbers themselves are correct and add up to
   181.
2. `REPORT_R1.md` says `TestErrorCodes` has **24** tests; the class has **25**.

---

## 8. No other errata at this time

All items above were checked against the code or measured in the final pass (2026-11-15).
