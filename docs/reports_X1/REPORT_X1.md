# Report X1: PLSQ test vectors for the UE plugin

Branch `dev/v1.2.2` (from `master` = `badf6c8`, release v1.2.1). Date 2026-11-22.
Machine: Windows 11, Python 3.13.5. Nothing was pushed.

## 1. What was added

| Commit | Content |
|---|---|
| `Bump APPV to 1.2.2; add task X1` | `APPV = "1.2.2"` in `main.py`, `docs/TASK_X1_TEST_VECTORS.md` |
| `X1: Add PLSQ error vectors and make_test_vectors tool` | `tools/make_test_vectors.py`, `tests/golden/errors/` (20 files + `expected.json` + `README.md`), `tests/test_test_vectors.py`, `.gitignore` allow-list |
| `X1: Add frame-stamp test files and verify mode` | `tests/golden/stamp/` (2 files + `README.md`) |
| `X1: Update README, CLAUDE.md and add report` | `README.md` (files table, section "Test vectors for the UE plugin", example `recorder_version`), short note in `CLAUDE.md` section 7, this report |
| `X1: Add .gitattributes: tests/golden/** -text` | `.gitattributes` (follow-up decision 2, section 6) |
| `X1: Move payload Truncated check after InconsistentHeader in spec` | `docs/PLSQ_SPEC.md` section 10, `tests/test_test_vectors.py` (class `TestCheckOrder`) (follow-up decision 1) |
| `X1: Regenerate expected.json for the updated spec commit` | `tests/golden/errors/expected.json`: only `spec_commit` changed |
| `X1: Update report after spec and gitattributes decisions` | this report |

Between the spec commit and the `expected.json` commit one test (`test_spec_commit_matches_spec_history`) fails
by design: `expected.json` can only name the spec commit after that commit exists. It passes again from the next
commit on.

Note on the commit split: `tools/make_test_vectors.py` and `tests/test_test_vectors.py` are one file each and were
committed whole in the first `X1:` commit, so the stamp generator, `--verify-stamp` and the stamp tests are already
in that commit; the second `X1:` commit only adds the generated stamp files and their README. The message of the
second commit ("... and verify mode") is therefore slightly inaccurate; history was not rewritten.

### `tools/make_test_vectors.py` (stdlib only, uses `plsq.py` and `mapping.py`)

- No arguments: regenerate `tests/golden/errors/` and `tests/golden/stamp/`. `--errors-only`, `--stamps-only`,
  `--errors-dir`, `--stamp-dir`, `--spec-commit` (override).
- `--not-finalized-source <file>`: cut `err_not_finalized.plsq.part` from a real `.part` file. The source is opened
  read-only; its SHA-256 is computed before and after and the script fails if they differ. Without the argument the
  script prints `SKIPPED: err_not_finalized.plsq.part (no --not-finalized-source given; no synthetic replacement is
  made)`; an existing file is kept unchanged and stays listed in `expected.json` with its old note.
- After writing, every error vector is checked: `plsq.read_info()` must raise the expected code (or succeed for
  `ok`/`warning`) and the first problem of `plsq.validate()` must have the expected code. If not, the script stops.
- `--stamp --chips N --fps F (--seconds S | --frame-count N) --out <path>` [`--start-universe`, `--start-channel`,
  `--chips-per-universe`]: frame-stamp file through `PlsqWriter` (streaming, one frame in memory). Prints time and MB/s.
- `--verify-stamp <file>` [`--frames a:b` | `--sample N --seed S`]: prints `OK: n of N frames checked` or
  `MISMATCH frame f chip c channel k (X): expected e, actual a`. Exit 0 / 1 / 2 (not readable as PLSQ).
- Generation uses one `base` frame and per frame `bytes.translate` with one of 256 precomputed tables
  `t[x] = (x + 7f) mod 256`, then writes the two stamps (no per-byte Python loop per frame).

No changes to `plsq.py`: the tool uses the existing private helpers `plsq._pack_header`, `_unpack_header`,
`_encode_meta`, `_crc32` (read/pack only) instead of adding new public helpers.

## 2. Files (size, SHA-256)

SHA-256 of the committed content. The repository has `core.autocrlf = true`; since the commit
`X1: Add .gitattributes` the rule `tests/golden/** -text` disables line-ending conversion for all golden files, so
`expected.json` and the READMEs are checked out with `\n` exactly as committed and the hashes below hold for a
fresh checkout too (verified, section 4). The `.plsq`/`.part` files were binary for git before as well.

| File | Bytes | SHA-256 |
|---|---|---|
| `tests/golden/errors/README.md` | 1936 | `8b26c94bfcb9bdfc2ec05a9803208beb44ad1efdab88e02705f28e3526dd5e17` |
| `tests/golden/errors/err_bad_header_size.plsq` | 74832 | `2cf86b1dd3d9287f110c4fddc26059218d0c22fb4fc97054431bfd268b645593` |
| `tests/golden/errors/err_bad_magic.plsq` | 74832 | `7bffc99929ac0601c38e1e9dc3f65fb5ca09530f1f313925c5c8e04468afb49b` |
| `tests/golden/errors/err_header_crc.plsq` | 74832 | `1b0651a10df6b53300d5edbfbfed2bc8754bb3f120c6f2b1aa46d1a63ec6fb99` |
| `tests/golden/errors/err_inconsistent_header.plsq` | 74832 | `614665b3019b618ea178a57d32f7b6352b8f16fffd28dfc9e75f3555d28b2ded` |
| `tests/golden/errors/err_meta_crc.plsq` | 74832 | `6de9b70b78f6eeb6a1cd97f3139039e2b16e3335ed5f6ddf0c52632f2b92090c` |
| `tests/golden/errors/err_meta_mismatch.plsq` | 74832 | `ac75ec3dcf445fff7d453e03c6ccf37958bf10d691b2eaa9008b5353c63d47de` |
| `tests/golden/errors/err_meta_out_of_bounds.plsq` | 74832 | `860d23885aff7d34f9d9a3d6663340df6821b75bca4110050e40bd98a1b08d0a` |
| `tests/golden/errors/err_meta_parse.plsq` | 74832 | `a9c3c6d75bbf9d8dcc1ef08df7b2df2d60f803e7097d249dbf3c729d03e06d75` |
| `tests/golden/errors/err_missing_meta_key.plsq` | 74832 | `b76ec24752fb0619e2b9d29c56dca8302c172e991acbc7fbe110d5f1794e10e4` |
| `tests/golden/errors/err_not_finalized.plsq.part` | 581632 | `5d88a320c08701d3f92296e6826453f30fe6667960419a25d577ac5d876dbeee` |
| `tests/golden/errors/err_out_of_range.plsq` | 74832 | `bdbeba4888d434623ed1faaaf0bb2620da0e784eddc0156e7b4551dc7e5c1a49` |
| `tests/golden/errors/err_truncated_payload.plsq` | 71972 | `f2966bd0f81e3bc2af0df8a82dfae05d28e1412b6063062487c12d20c809ecdf` |
| `tests/golden/errors/err_truncated_short.plsq` | 127 | `cf8de5948fec62dbadabd42e7cc8c913670d9e1486f07cfd511ce73d33a2318b` |
| `tests/golden/errors/err_unsupported_flags.plsq` | 74832 | `022ceffd18048a48f6a6abe83d37c66868299ba8a81359cca78f936c11c28979` |
| `tests/golden/errors/err_unsupported_layout.plsq` | 74832 | `9deec4d67e566b408eff927be1dba736b73621dcc83047acc762e88816a8ca4f` |
| `tests/golden/errors/err_unsupported_version.plsq` | 74832 | `678322cab4d7e93b2f24d7568befa9e8c5c27e7d427f9e64d8dbb39245e49b8e` |
| `tests/golden/errors/expected.json` | 3761 | `f1f85064adee17f80808710d18cafe3571ef6a07f0e39fdb040c93bbc99874f0` |
| `tests/golden/errors/ok_no_optional_keys.plsq` | 74832 | `af3d356bcaaeaf13ff43e988963276250fda8b6231913c6efea087647b8fdb20` |
| `tests/golden/errors/ok_optional_keys.plsq` | 74832 | `a316d6fc18bb75a24d802d8def620547eab8a10a5f5a518037fc7e52e5ee5f2c` |
| `tests/golden/errors/ok_trailing_data.plsq` | 74848 | `e0d67b58908afe886724928c1a726bf53ac2d97238f22ac84b78d882c1172081` |
| `tests/golden/errors/ok_unknown_key.plsq` | 74832 | `2484bd1e5902d41969c56559e16b21ad2ef9c4f6064b75da3b020d53765eb1d6` |
| `tests/golden/stamp/README.md` | 2191 | `e95edf0a2d078e4f33fa0575fd7effb06b7713e9b53854d95c7eb8b527fc9f8d` |
| `tests/golden/stamp/stamp_130c_30fps_300f.plsq` | 225632 | `e4afd970798c0fcd105d9bc5f1518be513ff179ef16ea1cfc4371b033cc4452d` |
| `tests/golden/stamp/stamp_1c_60fps_10f.plsq` | 69672 | `eac1028600a7be552bee0a156b24745d438eb74c1f91e550a166e1c2fcac3f3b` |

`expected.json`: `spec_commit = 1076efc858f8fd9796e0c60e0831a934406ba732` (last commit that touched
`docs/PLSQ_SPEC.md`: the section 10 order change on this branch). The first version of `expected.json` named
`7f48a24c7876dd7e4fe47cc9f20bc3bb0cf70634` (SHA-256 `5e08067b...4305`); all other files are unchanged by the
regeneration.

NotFinalized source (read only): `art-stream-uep/test_data/M1_9.plsq.part`, 193,861,632 bytes, SHA-256
`4a0be2ea74d31df69abdaefc66569984b13341d1976181a7f0a5a694d4f8f111` before and after (checked by the script and
again with `sha256sum` at the end).

## 3. Choices made (where the task left room)

- Base file: 130 chips, 30 fps, 10 frames, start 1/1, chips per universe auto (128) = 2 universes; payload pattern
  of the conformance file `(7f + 3c + k) mod 256`; fixed `sequence_id = 00000000-0000-4000-8000-0000000000e1`,
  `recorded_at = 2026-01-01T00:00:00+00:00`, `recorder_version = 1.2.2` (constant in the script, so a later
  recorder regenerates identical files).
- `err_truncated_short`: first 127 bytes (boundary value). `err_truncated_payload`: cut inside frame 4
  (71,972 of 74,832 bytes).
- `err_bad_magic`: `PLSX`, header CRC recomputed.
- `err_header_crc`: header byte 18 (fps low byte) changed 30 -> 31, CRC not updated (a reader that skipped the CRC
  check would then fail later at `MetaMismatch`, not pass).
- `err_inconsistent_header`: only `frame_stride` changed to 516. Since `payload_size` stays 5200, the rule
  `payload_size = frame_count x frame_stride` is violated too; both are `InconsistentHeader`, so the expected code
  is unique. Making `payload_size` consistent instead would either make the file look truncated (larger stride) or
  add trailing data (smaller stride). Documented in the `note`.
- `err_unsupported_layout`: `channels_per_chip = 3`, `frame_stride` left at 520 (the later inconsistency is not
  reached).
- `err_meta_crc`: first letter of `model_name` changed `T` -> `t` (JSON stays valid).
- `err_meta_parse`: `lost_packets` is the JSON number `0`.
- `ok_optional_keys`: `short_packets = "3"`, `first_frame_partial = "1"` (non-default values, so a reader cannot
  pass the test by inventing defaults).
- `ok_trailing_data`: 16 bytes `0xA5`; `validate()` reports exactly one problem, `TrailingData`.
- `ok_no_optional_keys` is byte-identical to the base file.
- Stamp meta: `model_name = "Frame Stamp"`, `sequence_name = "frame-stamp v1"`, `sequence_id = ...0000000000f1`.

Every vector fails at the intended check in both `plsq.read_info()` and `plsq.validate()` with the current
`plsq.py`; no defect needed a different value than the task table.

## 4. Commands and output

Full suite (before the task: 244 tests):

```
> python -m unittest discover -q          (after the first four commits)
Ran 267 tests in 32.846s
OK
> python -m unittest discover -q          (final, after the spec order tests)
Ran 272 tests in 32.638s
OK
```

New module alone (23 tests at first, 28 after `TestCheckOrder`):

```
> python -m unittest tests.test_test_vectors
Ran 23 tests in 1.539s
OK
> set PLSQ_NOT_FINALIZED_SOURCE=<missing path> & python -m unittest tests.test_test_vectors
test_regenerate_not_finalized_identical ... skipped 'NotFinalized source not available (...); err_not_finalized.plsq.part not regenerated'
Ran 23 tests in 1.165s
OK (skipped=1)
> python -m unittest tests.test_test_vectors          (final)
Ran 28 tests in 1.613s
OK
```

`TestCheckOrder` (spec section 10 after the change): the codes of the section 10 table, read from
`docs/PLSQ_SPEC.md`, are exactly in the order `IoError, Truncated, BadMagic, ..., OutOfRange, InconsistentHeader,
Truncated, MetaOutOfBounds, ..., MetaMismatch`; files with two defects built from `err_truncated_payload.plsq`
give the same first code in `read_info()` and `validate()`:

| Second defect (plus the cut payload) | Expected and actual |
|---|---|
| header byte 18 changed, CRC not updated | `HeaderCrcMismatch` |
| `fps = 0`, CRC recomputed | `OutOfRange` |
| `frame_stride = 516`, CRC recomputed | `InconsistentHeader` |
| `meta_size = 1`, CRC recomputed | `Truncated` (before `MetaOutOfBounds`) |

`plsq.validate()` already had this order (`_validate_body`: ... `OutOfRange`, `InconsistentHeader`, payload
`Truncated`, `MetaOutOfBounds`, ...), the same as `read_info()` (`_check_header`); `plsq.py` was not changed.

The tests cover: result and code of every file in `read_info()` and as first problem of `validate()`;
`expected.json` lists exactly the files in the folder and has the format of the task; `IoError` with a missing path;
meta of the `ok_*` files (unknown key passed through, optional keys present/absent); regeneration into a temp folder
is byte-identical (NotFinalized separately, skipped when the source is missing); the skip message; stamp files
valid, frames equal to the README reference function, `--verify-stamp` OK (all, range, sample); one changed byte,
two swapped frames and a frame glued from two halves are detected with the right frame/chip/channel; CLI exit codes;
custom stamp generation.

Generation:

```
> python tools/make_test_vectors.py --not-finalized-source ...\art-stream-uep\test_data\M1_9.plsq.part
Error vectors:
  NotFinalized source: .../M1_9.plsq.part
    size 193861632 bytes, SHA-256 before = after = 4a0be2ea74d31df69abdaefc66569984b13341d1976181a7f0a5a694d4f8f111
  20 error vectors + expected.json -> ...\tests\golden\errors
Frame-stamp files:
  ...\tests\golden\stamp\stamp_130c_30fps_300f.plsq (225632 bytes)
  ...\tests\golden\stamp\stamp_1c_60fps_10f.plsq (69672 bytes)
> python tools/make_test_vectors.py --verify-stamp tests/golden/stamp/stamp_130c_30fps_300f.plsq
OK: 300 of 300 frames checked
> python tools/make_test_vectors.py --verify-stamp tests/golden/stamp/stamp_1c_60fps_10f.plsq
OK: 10 of 10 frames checked
```

Large stamp file (not committed; `.bench_out/` is git-ignored), three runs:

```
> python tools/make_test_vectors.py --stamp --chips 64000 --fps 60 --seconds 60 --out .bench_out/stamp_64000c_60fps_3600f.plsq
Written: .bench_out/stamp_64000c_60fps_3600f.plsq
  64000 chips, 60 fps, 3600 frames, 921669632 bytes (921.7 MB)
  time 0.61 s, 1523.3 MB/s
  (runs 2 and 3: 0.76 s, 1219.2 MB/s; 0.72 s, 1286.3 MB/s)
> python tools/validate_plsq.py .bench_out/stamp_64000c_60fps_3600f.plsq
Status: OK
> python tools/make_test_vectors.py --verify-stamp .bench_out/stamp_64000c_60fps_3600f.plsq
OK: 3600 of 3600 frames checked          (1.15 s)
> python tools/make_test_vectors.py --verify-stamp .bench_out/stamp_64000c_60fps_3600f.plsq --sample 50 --seed 7
OK: 50 of 3600 frames checked
SHA-256 = f507d8ebaeaec76473ad0c8f876570fc87c3eec3a1c207a2787827e664ad1f1a
```

The time is measured until `PlsqWriter.finalize()` returns (file closed, no `fsync`), so it mostly measures writes
into the Windows file cache, not necessarily the disk speed (the disk speed itself
was not measured). The file is deterministic: the
SHA-256 above can be used to check a copy or a regeneration on another machine.

`.gitignore`: `git check-ignore -v` shows the new rules apply to `tests/golden/errors/*` and
`tests/golden/stamp/*` only; `tests/golden/foo.plsq` and `x.json` stay ignored by `*.plsq` / `*.json`;
`git status` showed no other new tracked files. `tests/golden/conformance_v1.plsq`, `plsq.py`, `mapping.py` and
`session.py` are unchanged; in `main.py` only `APPV` changed; `docs/PLSQ_SPEC.md` changed only in section 10 (by
decision, section 6).

Fresh checkout of the golden files (after `.gitattributes` and the final `expected.json`): all 25 files of
`tests/golden/errors/` and `tests/golden/stamp/` were checked out again with `git checkout-index -f
--prefix=.bench_out/recheckout2/` (a real checkout with attributes and `core.autocrlf = true` applied, into the
git-ignored `.bench_out/`). For each file the SHA-256 of the checked-out copy, of the git blob and of the working tree
file are equal and equal to the table in section 2 (`25 same`). `git ls-files --eol tests/golden` shows
`attr/-text` for all 26 files (23 binary, 3 text files with `lf` in the index and in the working tree). A control
checkout without `.gitattributes` was attempted but is not conclusive (git still read the attributes from the
index), so the CRLF effect without the rule is not demonstrated here.

## 5. What could not be verified

- The C++ reader of the UE plugin (`art-stream-uep`) was not run against these files; this is task P1/P2 there.
- The copy into `art-stream-uep/spec/golden/` and the update of `SPEC_SOURCE.md` were not done (section 8 of the
  task: done by the user).
- Disk-bound write speed of the large stamp file (see note above).

## 6. Questions and decisions

Both questions below were raised in the first version of this report; the user decided:

- Question 1: **option (b)**. `docs/PLSQ_SPEC.md` section 10 now has two `Truncated` rows: "file shorter than 128
  bytes" stays second; "file shorter than `payload_offset + payload_size`" follows `InconsistentHeader`, before
  `MetaOutOfBounds`. One sentence after the table gives the reason (size fields are used only after the header CRC
  and the header consistency have been checked). `format_version` and the file format are unchanged; `plsq.py`
  already followed this order. Tests: `TestCheckOrder` (section 4).
- Question 2: **yes**, `.gitattributes` with `tests/golden/** -text` (section 2 and 4).

No open questions remain from this task. Original text of the questions:

1. **Order of the payload `Truncated` check: `plsq.py` differs from the spec order.** The spec (section 10) lists
   `Truncated` (including "shorter than `payload_offset + payload_size`") as the second check, before `BadMagic`.
   `plsq.py` checks the 128-byte case first but the payload case only after `OutOfRange` and
   `InconsistentHeader` (in both `read_info()` and `validate()`). A file with two defects, for example `fps = 0`
   and a cut payload, gives `Truncated` by the spec order and `OutOfRange` in `plsq.py`. **None of the vectors is
   affected**: each has exactly one defect, and the result is the same with both orders (checked case by case,
   e.g. `err_truncated_payload` has a fully valid header). I did not stop the task for this because the files do
   not depend on it, but it needs a decision before the C++ reader is written: (a) fix `plsq.py` to the spec order,
   or (b) change the spec so that the payload-size check comes after the header is trusted (CRC, ranges,
   consistency), which is what `plsq.py` does and arguably the safer order (the spec order uses
   `payload_offset`/`payload_size` before the header CRC has been checked). I recommend (b); I made no change.
2. Should `tests/golden/errors/*` and `tests/golden/stamp/*` get a `.gitattributes` entry (`-text` or `binary`) so
   that `expected.json` and the READMEs keep `\n` line endings and fixed hashes on checkout? Not done, because the
   task allows only the `.gitignore` change.
