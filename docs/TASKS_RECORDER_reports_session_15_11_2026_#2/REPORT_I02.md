# Report I-02: fix first-frame race in Recorder._capture

**Date:** 2026-11-15  
**Branch:** dev/v1.2.0  
**Environment:** Windows 11 10.0.26300, Python 3.13.5, Intel Core i7-13700H  

---

## Goal

Eliminate the zero/black first frame that the recorder produces when `FrameAssembler` is active and `wait_first_packet=True`.

---

## What changed

**File:** `session.py`, `Recorder._capture` (~line 348)

Old behaviour: waited for `any(s[0] in self.server.latest for s in self.spans)` — fires as soon as the first packet from **any** universe appears in `server.latest`.

New behaviour: when an assembler is active, waits for `assembler.committed_frame is not None` — fires only after a **complete** frame has been assembled (all mapped universes contributed).

```python
# Old (buggy)
while not self._stop.is_set():
    with self.server.lock:
        have = any(s[0] in self.server.latest for s in self.spans)
    if have:
        break
    time.sleep(0.005)

# New (fixed)
asm = self._assembler
while not self._stop.is_set():
    if asm is not None and asm.enabled:
        if asm.committed_frame is not None:
            break
    else:
        with self.server.lock:
            have = any(s[0] in self.server.latest for s in self.spans)
        if have:
            break
    time.sleep(0.005)
```

---

## Tests

### tests/test_i02_i03.py — TestFirstFrameRace

| Test | What it checks | Old code result | Fixed result |
|------|----------------|-----------------|--------------|
| `test_single_universe_first_frame_not_black` | 4 chips, 1 uni; first frame = known data | FAIL: recorder stays in `waiting` (`'waiting' != 'done'`); `server.latest` is empty with the mock | Pass |
| `test_multi_universe_first_frame_not_black` | 130 chips, 2 uni; first frame ≠ zeros | FAIL: stays in `waiting` | Pass |
| `test_stress_50_runs_no_black_first_frame` | 50 iterations mix 1/2 uni; Mock | FAIL: stays in `waiting` (first iteration) | Pass |
| `test_stress_500_universes_no_black_first_frame` | 50 iterations, 500 uni; Mock | FAIL: stays in `waiting` (first iteration) | Pass |

**"Old code result" verified in the final pass (C2).** The current `tests/test_i02_i03.py` was copied next to the
code of commit `c141c8e` (last commit before the I-02 fix, exported with `git archive` to a temp folder) and run:

```
python -m unittest tests.test_i02_i03_current.TestFirstFrameRace -v
FAIL: test_multi_universe_first_frame_not_black ... AssertionError: 'waiting' != 'done'
FAIL: test_single_universe_first_frame_not_black ... AssertionError: 'waiting' != 'done'
FAIL: test_stress_500_universes_no_black_first_frame ... AssertionError: 'waiting' != 'done'
FAIL: test_stress_50_runs_no_black_first_frame ... AssertionError: 'waiting' != 'done'
Ran 4 tests in 5.205s
FAILED (failures=4)
```

"Hangs" means: the old recorder never leaves `waiting`, and the test fails on its 1-2 s timeout.

The Mock-based tests confirm the fix works (wait-for-committed_frame path), but do NOT reproduce the real race: `server.latest` stays empty with `_MockServer` so the old code would hang rather than produce a black frame.

### Deterministic verification with old code (not committed)

Temporary patch applied inline; `server.latest` manually pre-populated to trigger old wait condition:

```
python -c "... server.latest[spans[0][0]] = bytes(512) ..."
# Output:
Buggy: first frame all-zeros = True
Fixed: first frame all-zeros = False
```

(Script shown in session transcript; not committed.)

---

## Real-UDP stress series

### Why Mock does not reproduce the race

In `ArtNetServer`, the first UDP packet from any universe updates `server.latest[uni]`. The old `wait_first_packet` check then immediately breaks the wait loop, and the recorder samples `asm.committed_frame` — which is `None` for a multi-universe layout because only 1 of N universes has arrived. With `_MockServer`, `server.latest` is always empty, so the old code never breaks out of the wait loop (hangs). The race only manifests with real UDP where `latest` is populated.

### tools/stress_first_frame.py

Command: `python tools/stress_first_frame.py --runs 50`

```
stress_first_frame.py  chips=64000 (500 uni)  runs=50  port=7457
BUGGY  50 runs in 1.06 s: black=1 (2.0%)  timeouts=0
FIXED  50 runs in 1.10 s: black=0 (0.0%)  timeouts=0
PASS: race reproduced on buggy code, eliminated by fix.
```

Command: `python tools/stress_first_frame.py --runs 200`

```
stress_first_frame.py  chips=64000 (500 uni)  runs=200  port=7457
BUGGY  200 runs in 2.46 s: black=2 (1.0%)  timeouts=0
FIXED  200 runs in 2.52 s: black=0 (0.0%)  timeouts=0
PASS: race reproduced on buggy code, eliminated by fix.
```

**Statistical note:** the race rate on this machine is approximately 1–2 %. (Correction, final pass: an earlier version said "user reported ~20 %". That number was the reviewer's own measurement on a different machine, Linux, 500 universes: about 1 trial in 5. It was not reported by the user.) One black frame in 50 runs is a low count, but combined with the deterministic test (`Buggy: all-zeros = True`) it proves the race exists and the fix eliminates it.

---

## Process notes

- `session.py` was committed in commit `1510284` (labeled I-02) rather than in a separate I-01 commit — a hygiene violation. History not rebased.
- The first stress series used `_MockServer` and did not reproduce the race (documented above). The script was replaced with `tools/stress_first_frame.py` which uses a real `ArtNetServer` and real UDP packets.

---

## Commits

```
1510284 I-02: fix first-frame race in Recorder._capture
1219878 I-10 tests, _seq_last reset, 500-uni stress x50, stress_first_frame.py
```
