# PLSQ frame-stamp files

Valid `.plsq` files in which every frame carries its own frame index, so a
reader can prove it returned exactly frame `f` and not a neighbour or a mix of
two frames (UE plugin task P2).

| File | Chips | FPS | Frames | Universes | Size, bytes |
|---|---|---|---|---|---|
| `stamp_130c_30fps_300f.plsq` | 130 | 30 | 300 | 2 (start 1 / channel 1) | 225,632 |
| `stamp_1c_60fps_10f.plsq` | 1 | 60 | 10 | 1 (start 1 / channel 1) | 69,672 |

Meta: `sequence_name = "frame-stamp v1"`, `developer_notes` describes the
pattern in one line.

## Pattern "frame-stamp v1" (normative for these files)

For frame `f` (0-based), chip `c` (0-based), channel `k` in 0..3 (R, G, B, W):

- **Stamp chips**: chip 0 and the last chip (`chips_count - 1`) hold the frame
  index as `uint32` little-endian: R = byte 0 (lowest), G = byte 1,
  B = byte 2, W = byte 3. With 1 chip the stamp occupies the only chip.
- **Every other byte**: `(base(c, k) + 7 x f) mod 256` with
  `base(c, k) = (3 x c + k) mod 256`.

A reader checks one frame by: stamp at chip 0 = stamp at the last chip = `f`,
and every other byte matches the formula. A frame glued from two frames has
different stamps at the two ends.

Reference function:

```python
import struct

def expected_frame(chips, f):
    b = bytearray(((3 * c + k + 7 * f) % 256)
                  for c in range(chips) for k in range(4))
    stamp = struct.pack("<I", f)
    b[0:4] = stamp
    b[-4:] = stamp
    return bytes(b)
```

## Regenerate / verify

```
python tools/make_test_vectors.py --stamps-only
python tools/make_test_vectors.py --verify-stamp tests/golden/stamp/stamp_130c_30fps_300f.plsq
```

Large files are not committed; generate them into a git-ignored folder, for
example:

```
python tools/make_test_vectors.py --stamp --chips 64000 --fps 60 --seconds 60 --out .bench_out/stamp_64000c_60fps_3600f.plsq
python tools/make_test_vectors.py --verify-stamp .bench_out/stamp_64000c_60fps_3600f.plsq
```

`--verify-stamp` also takes `--frames a:b` (Python slice semantics) or
`--sample N [--seed S]`; it prints `OK` or the first mismatch (frame, chip,
channel, expected, actual). Exit code 0 = OK, 1 = mismatch, 2 = not readable.
