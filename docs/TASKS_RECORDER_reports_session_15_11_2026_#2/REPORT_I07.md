# Report I-07: Fix universes display — count_universes accounts for start_channel

**Date:** 2026-11-15
**Branch:** dev/v1.2.0
**Commit:** 4a2b54d

---

## Goal

The live "Universes" display in the stats block used `ceil(chips / eff_cpu)`
which ignored the `start_channel` offset.  When `start_channel > 1`,
the first universe holds fewer chips than subsequent ones, so the total
universe count is higher.  Replace with `count_universes()` from
`mapping.py` which already implements the correct calculation.

---

## What changed

### main.py — `_refresh` (mapping import already present)

Before:

```python
import math
n_uni = math.ceil(chips / eff_cpu)
```

After:

```python
from mapping import build_spans, count_universes, effective_chips_per_universe
...
n_uni = count_universes(su, sc, cpu_raw, chips)
```

`count_universes(start_universe, start_channel, chips_per_universe, chips)`
returns `len(build_spans(...))`, which correctly accounts for the channel
offset in universe 1.

---

## Example

| chips | start_channel | eff_cpu | old formula | count_universes |
|------:|:-------------:|--------:|:-----------:|:---------------:|
| 128   | 1             | 128     | 1           | 1               |
| 128   | 65            | 128     | 1           | **2**           |
| 104   | 1             | 128     | 1           | 1               |
| 104   | 65            | 128     | 1           | **2**           |

---

## Verification

- Full test suite: **220 tests, OK**
- `count_universes` is already covered by existing mapping tests.
- Live display not automatically testable (requires Tkinter).

---

## Could not verify

- Live Tkinter display (requires running `main.py` with `start_channel > 1`).
