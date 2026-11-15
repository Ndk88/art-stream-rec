# Report I-09: fix FrameAssembler.on_artsync partial flag

**Date:** 2026-11-15  
**Branch:** dev/v1.2.0  
**Environment:** Windows 11 10.0.26300, Python 3.13.5, Intel Core i7-13700H  

---

## Goal

`on_artsync()` must mark the commit as partial (`partial_count++`) when ArtSync fires before all mapped universes have contributed to the current working frame.

---

## What changed

**File:** `session.py`, `FrameAssembler.on_artsync()`

```python
# Before (buggy)
def on_artsync(self):
    if not self.enabled or not self._updated:
        return
    self._commit(partial=False)   # always full — wrong

# After (fixed)
def on_artsync(self):
    if not self.enabled or not self._updated:
        return
    self._commit(partial=(self._updated < self._expected))
```

`self._expected` is a `frozenset` of all mapped universe port addresses. `self._updated` is the set updated since the last commit. The expression `self._updated < self._expected` is True when ArtSync fires with an incomplete set.

---

## Tests

**File:** `tests/test_r5.py` — `TestFrameAssemblerTwoUniverses`

### Existing test modified

`test_artsync_commits_partial`: previously asserted `partial_count == 0` (the old wrong behaviour). Now asserts `partial_count == 1`.

The old assertion actively encoded the bug as a test expectation. The comment was updated to explain the I-09 fix.

### New tests added

**`test_artsync_full_not_partial`** (first version had a bug — did not call `on_artsync()`; rewritten):

```python
def test_artsync_full_not_partial(self):
    _deliver(self.fa, self.u0, self._data0())
    _deliver(self.fa, self.u1, self._data1())
    # on_universe for u1 triggered a full commit (_updated == _expected)
    frame_before = self.fa.committed_frame
    self.assertIsNotNone(frame_before)
    self.assertEqual(self.fa.partial_count, 0)
    # on_artsync with no pending updates (_updated is empty) → no commit
    self.fa.on_artsync()
    self.assertIs(self.fa.committed_frame, frame_before)
    self.assertEqual(self.fa.partial_count, 0)
```

**`test_artsync_case_c_all_universes_arrive_before_sync`** (case (c) from I-09 description):

```python
def test_artsync_case_c_all_universes_arrive_before_sync(self):
    for _ in range(5):
        _deliver(self.fa, self.u0, self._data0())
        _deliver(self.fa, self.u1, self._data1())
        # Full commit happens in on_universe; ArtSync with empty _updated is a no-op
        self.fa.on_artsync()
    self.assertEqual(self.fa.partial_count, 0)
```

Command: `python -m unittest tests.test_r5 -v`  
Result: all tests pass (182 tests total at time of commit `4768fea`).

---

## Commits

```
4768fea I-09/I-10: fix FrameAssembler on_artsync partial flag and short-packet handling
```
