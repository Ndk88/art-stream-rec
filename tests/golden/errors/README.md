# PLSQ error vectors

Small `.plsq` files for reader tests (UE plugin task P1). One file per reader
error code of `docs/PLSQ_SPEC.md` section 10, plus a few valid edge cases.
`expected.json` lists every file in this folder with the expected result:

- `result`: `error` (the reader must reject the file with `code`), `warning`
  (the file is valid, the validator reports `code` as a warning) or `ok`;
- `code`: spec error code (`""` for `ok`);
- `note`: what was changed.

`IoError` has no file: test it with a path that does not exist.

## How the files are made

`tools/make_test_vectors.py` writes a valid base file with `PlsqWriter` +
`build_meta` (130 chips, 30 fps, 10 frames, start universe 1, start channel 1,
chips per universe 128, so 2 universes; byte of frame `f`, chip `c`, channel
`k` = `(7f + 3c + k) mod 256`; fixed `sequence_id`, `recorded_at`, notes).
Every `err_*` file is the base file with **exactly one defect**. Dependent
checksums are recomputed (header CRC after a header edit, meta size and meta
CRC after a meta edit), unless the defect is the checksum itself. Each file
fails at its intended check as the first failing check, in `plsq.read_info()`
and as the first problem of `plsq.validate()`; the script checks this after
writing and stops if it is not so.

`err_not_finalized.plsq.part` is cut from a real interrupted 64,000-chip,
60 fps recording made with recorder v1.2.1 (placeholder header with
`FINALIZED = 0`, `frame_count = 0`, `meta_size = 2`): header + meta area +
2 whole frames = 581,632 bytes. The source is only read.

## Regenerate

```
python tools/make_test_vectors.py --errors-only --not-finalized-source <M1_9.plsq.part>
```

Without `--not-finalized-source` the NotFinalized vector is skipped (an
existing file is kept unchanged); no synthetic replacement is made. The output
is deterministic: regenerating gives byte-identical `.plsq` files
(`tests/test_test_vectors.py` checks this).
