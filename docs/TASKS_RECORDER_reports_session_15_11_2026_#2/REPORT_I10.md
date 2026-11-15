# Report I-10: fix FrameAssembler short-packet handling

**Date:** 2026-11-15  
**Branch:** dev/v1.2.0  
**Environment:** Windows 11 10.0.26300, Python 3.13.5, Intel Core i7-13700H  

---

## Goal

When an ArtDMX packet covers fewer DMX channels than the mapped span, the assembler must:
- Copy the available channels into the working buffer.
- Zero-fill the remainder of the span (trailing missing channels → 0 by convention).
- Mark the universe as updated (count toward a full frame).
- Track the event in `short_packets` and `first_short_uni` attributes.

Previously the entire packet was silently skipped if `src_end > 18 + dmx_len`.

---

## What changed

**File:** `session.py`, `FrameAssembler`

### New attributes

```python
self.short_packets = 0        # packets that covered fewer channels than mapped
self.first_short_uni = None   # port_address of first short packet
```

Both are reset to initial values in `start()`.

### on_universe fix

```python
# Before (buggy — entire packet skipped if short)
if src_end <= 18 + dmx_len:
    self._working[off:off + n_ch] = mv[src_start:src_end]
self._updated.add(port_address)

# After (fixed — copy what's available, zero-fill rest)
pkt_end = 18 + dmx_len
if src_end <= pkt_end:
    self._working[off:off + n_ch] = mv[src_start:src_end]
else:
    avail = max(0, pkt_end - src_start)
    if avail > 0:
        self._working[off:off + avail] = mv[src_start:src_start + avail]
    self._working[off + avail:off + n_ch] = bytes(n_ch - avail)
    self.short_packets += 1
    if self.first_short_uni is None:
        self.first_short_uni = port_address
self._updated.add(port_address)
```

---

## Tests

**File:** `tests/test_r5.py` — `TestShortPacketHandling` (5 tests)

| Test | What it checks |
|------|----------------|
| `test_short_packet_first_channels_copied` | 100-byte packet on 512-ch span: first 100 bytes = packet; bytes 100-511 zeroed over previously-written 0xFF |
| `test_short_packet_increments_counter` | `short_packets` goes 0→1→2; `first_short_uni` fixed on first occurrence |
| `test_short_packet_marks_universe_updated` | Universe is added to `_updated` → triggers full commit in single-universe layout |
| `test_full_packet_does_not_increment_counter` | Full-length packet: `short_packets` stays 0, `first_short_uni` stays None |
| `test_start_resets_counters` | After `start()` call: `short_packets == 0`, `first_short_uni is None` |

Command: `python -m unittest tests.test_r5.TestShortPacketHandling -v`  
Result (after commit `1219878`): 5 tests pass.

Command: `python -m unittest discover -q`  
Result: `Ran 195 tests in 24.579s — OK`

---

## Not yet done

The warning line in the GUI statistics block ("short packets: N (e.g. U7 sends 416 channels, mapping needs 512)") is not implemented. It will be added as part of the GUI statistics pass (along with I-04…I-07 GUI work).

---

## Commits

```
4768fea I-09/I-10: fix FrameAssembler on_artsync partial flag and short-packet handling
1219878 I-10 tests, _seq_last reset, 500-uni stress x50, stress_first_frame.py
```
