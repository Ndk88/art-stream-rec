# Task X1: PLSQ test vectors for the UE plugin (recorder repository)

Self-contained task for one Claude Code session in the repository **`art-stream-rec`** (recorder). Read this file first, then `CLAUDE.md`, then `docs/PLSQ_SPEC.md` (sections 3-10 and 14), then `plsq.py`.

Replies to the user in Russian. Code, comments, docs and commit messages in English.

## 1. Why

The UE plugin (separate repository `art-stream-uep`) reads `.plsq` files in C++. Its tests need two sets of small, exactly known files that only this repository can produce correctly, because it holds the reference writer `plsq.py`:

1. **Error vectors**: one broken file per reader error code of `docs/PLSQ_SPEC.md` section 10, plus a few valid edge cases, with a machine-readable list of the expected result (`expected.json`). Used in plugin task P1.
2. **Frame-stamp files**: valid files where every frame carries its own frame index, so that a reader can prove it returned exactly frame `f` and not a neighbour or a mix of two frames. Used in plugin task P2.

The recorder application itself, the file format and `docs/PLSQ_SPEC.md` **do not change** in this task.

## 2. Branch and version (rules for this task)

The `CLAUDE.md` on `master` has no git section; these rules apply:

- Start state: `master` = release `v1.2.1` (commit `badf6c8` or later). Check with `git status` that the working tree is clean (an untracked copy of this task file in `docs/` is expected) and with `git log -1` where `master` is. If anything else differs, stop and ask.
- Create the branch **`dev/v1.2.2` from `master`** (the user asks for it with this task). The branch name equals `APPV` in `main.py`.
- **First commit** on the branch: `APPV = "1.2.2"` in `main.py` and this task file added as `docs/TASK_X1_TEST_VECTORS.md`. Message: `Bump APPV to 1.2.2; add task X1`.
- Do **not** touch the branches `dev/v1.2.1` (frozen release branch) and `dev/v1.3.0` (paused). Do not take anything from `dev/v1.3.0`.
- One commit per logical step, message starting with `X1:`, English, imperative. Code, tests and docs of one step in the same commit.
- Never rewrite history. **No push, merge, tag, branch rename or delete, pull request** without an explicit request in the session.
- At the end everything is committed on `dev/v1.2.2`; the final message gives the branch, the last commit hash and states that nothing was pushed.

## 3. What may be changed

Allowed:
- new `tools/make_test_vectors.py` (stdlib only, Python 3.9+, uses `plsq.py` and `mapping.py`);
- new tests under `tests/` (unittest, deterministic, fast: the whole new test module must run in a few seconds);
- generated files under `tests/golden/errors/` and `tests/golden/stamp/` (small, committed) and the `.gitignore` allow-list for exactly these files;
- `README.md` (section about tools), `CLAUDE.md` (only if a short note about the new tool is useful);
- report `docs/reports_X1/REPORT_X1.md`.

Not allowed (stop and ask if it seems necessary):
- changes to `main.py` other than `APPV`, to `session.py`, to the write/read logic of `plsq.py`, to `mapping.py`;
- any change to `docs/PLSQ_SPEC.md`; if a vector cannot be made without a spec change, or the spec looks ambiguous, describe it and ask;
- changes to `tests/golden/conformance_v1.plsq`;
- writing anywhere outside this repository. The source file of the `NotFinalized` vector (section 4.3) is **read only**.

Adding a small public helper to `plsq.py` that only **reads** or **packs** (no behaviour change for existing callers) is allowed if it avoids duplicating code; mention it in the report.

## 4. Error vectors: `tests/golden/errors/`

### 4.1 Principle

Every error file has **exactly one defect** and must fail at the intended check of spec section 10 **as the first failing check**, both in `plsq.read_info()` and in `plsq.validate()` (first problem reported). To keep exactly one defect, recompute dependent checksums after the change (header CRC after editing a header field, meta CRC after editing meta), unless the defect *is* the checksum. Base file for the edits: a small valid file written with `PlsqWriter` + `build_meta` (for example 130 chips, 30 fps, 10 frames, start universe 1, start channel 1, so it spans 2 universes; deterministic: fixed `sequence_id`, `recorded_at`, notes, as in `tools/make_golden.py`).

### 4.2 Files

| File | Defect | Expected |
|---|---|---|
| `err_truncated_short.plsq` | file shorter than 128 bytes | `Truncated` |
| `err_truncated_payload.plsq` | valid header, file cut inside the payload | `Truncated` |
| `err_bad_magic.plsq` | magic not `PLSQ` | `BadMagic` |
| `err_unsupported_version.plsq` | `format_version` = 2 | `UnsupportedVersion` |
| `err_bad_header_size.plsq` | `header_size` = 64 | `BadHeaderSize` |
| `err_header_crc.plsq` | one header byte changed, CRC not updated | `HeaderCrcMismatch` |
| `err_unsupported_flags.plsq` | bit 0 `COMPRESSED` set | `UnsupportedFlags` |
| `err_not_finalized.plsq.part` | real interrupted recording, section 4.3 | `NotFinalized` |
| `err_unsupported_layout.plsq` | `channels_per_chip` = 3 | `UnsupportedLayout` |
| `err_out_of_range.plsq` | `fps` = 0 | `OutOfRange` |
| `err_inconsistent_header.plsq` | `frame_stride` != `chips_count x 4` | `InconsistentHeader` |
| `err_meta_out_of_bounds.plsq` | `meta_size` = 1 | `MetaOutOfBounds` |
| `err_meta_crc.plsq` | one meta byte changed, meta CRC not updated | `MetaCrcMismatch` |
| `err_meta_parse.plsq` | meta is valid JSON but one value is a number, not a string | `MetaParseError` |
| `err_missing_meta_key.plsq` | required key `model_name` removed | `MissingMetaKey` |
| `err_meta_mismatch.plsq` | `sequence_fps` differs from header `fps` | `MetaMismatch` |
| `ok_trailing_data.plsq` | valid file + 16 extra bytes at the end | OK, warning `TrailingData` |
| `ok_no_optional_keys.plsq` | valid, without `short_packets` / `first_frame_partial` | OK |
| `ok_optional_keys.plsq` | valid, with both optional keys | OK |
| `ok_unknown_key.plsq` | valid, with one extra meta key `x_test_unknown` = `"kept"` | OK, key passed through |

`IoError` has no file (it is tested with a non-existent path). If the order of checks in `plsq.py` makes one of these impossible to reach as the first failure, or a defect needs a different value to reach its check, choose the smallest working change and document it in `expected.json` (`note`) and in the report. If `plsq.py` and the spec disagree, stop and ask (the spec wins).

### 4.3 The `NotFinalized` vector (real recording)

Source (read only): `D:\PRECIOSA\PixelStreaming_Project_1\_dmx_\art-stream-uep\test_data\M1_9.plsq.part`, a real interrupted 64,000-chip recording made with recorder v1.2.1 on 2026-11-22 (193,861,632 bytes, 757 whole frames, not finalized). The script takes it with an argument (for example `--not-finalized-source <path>`); output = header + meta area + 2 whole frames (`payload_offset + 2 x frame_stride` = 581,632 bytes), source never modified (check its SHA-256 before and after). Without the argument the script must say clearly that this vector was skipped (and must not invent a synthetic replacement silently).

### 4.4 `tests/golden/errors/expected.json`

```json
{
  "format_version": "1",
  "generated_by": "tools/make_test_vectors.py",
  "recorder_version": "1.2.2",
  "spec_commit": "<git hash of docs/PLSQ_SPEC.md used>",
  "files": {
    "err_bad_magic.plsq": {"result": "error", "code": "BadMagic", "note": ""},
    "ok_trailing_data.plsq": {"result": "warning", "code": "TrailingData", "note": ""},
    "ok_unknown_key.plsq": {"result": "ok", "code": "", "note": "meta key x_test_unknown = kept"}
  }
}
```

One entry per file in the folder; values as strings; keys sorted. A `README.md` in the folder states how the files were made and how to regenerate them.

## 5. Frame-stamp files: `tests/golden/stamp/`

### 5.1 Pattern (normative for these files)

For frame `f` (0-based), chip `c` (0-based), channel `k` in 0..3 (R, G, B, W):

- **Stamp chips**: chip 0 **and** the last chip (`chips_count - 1`) hold the frame index as `uint32` little-endian: R = byte 0 (lowest), G = byte 1, B = byte 2, W = byte 3.
- **Every other byte**: `(base(c, k) + 7 x f) mod 256` with `base(c, k) = (3 x c + k) mod 256`.

So a reader checks one frame by: stamp at chip 0 = stamp at last chip = `f`, and every other byte matches the formula. A frame glued from two frames has different stamps at the two ends. Generation must be fast (no per-byte Python loop per frame: build `base` once, then per frame `bytes.translate` with a table `t[x] = (x + 7f) mod 256`, then write the two stamps).

### 5.2 Files

- Committed (small): `stamp_130c_30fps_300f.plsq` (130 chips, 30 fps, 300 frames, start universe 1, start channel 1: 2 universes; size 69,632 + 300 x 520 = 225,632 bytes) and `stamp_1c_60fps_10f.plsq` (1 chip, 60 fps, 10 frames: chip 0 is also the last chip; the stamp then occupies the only chip). Meta: valid, deterministic, `sequence_name` names the pattern (for example `frame-stamp v1`), `developer_notes` describes the pattern in one line.
- Large files are **not** committed. The script can generate them on request into a git-ignored folder, for example `--stamp --chips 64000 --fps 60 --seconds 60 --out <path>` (= 3,600 frames, about 922 MB). Write through `PlsqWriter` (streaming, never the whole payload in memory); report the time and MB/s on the user's machine for this size.
- `tests/golden/stamp/README.md`: the pattern of 5.1 in words and with a 10-line Python reference function `expected_frame(chips, f) -> bytes`.

### 5.3 Verify mode

`--verify-stamp <file>` reads a stamp file and checks every frame (or `--frames a:b` / a random sample `--sample N`) against the pattern; prints the first mismatch (frame, chip, channel, expected, actual) or `OK`. Used by the tests here and later by the user to check large generated files.

## 6. Tests (`tests/test_test_vectors.py` or similar)

- Every file in `tests/golden/errors/` gives the result and code from `expected.json` in `plsq.read_info()` and as the first problem of `plsq.validate()`.
- `expected.json` lists exactly the files present in the folder (no missing, no extra).
- Regenerating into a temp folder gives byte-identical files to the committed ones (determinism), except `err_not_finalized.plsq.part` when the source is not available (then skipped with a clear message).
- Stamp files: valid in `plsq.validate()`; `--verify-stamp` returns OK for every frame; a deliberately corrupted copy (one byte, one swapped frame) is detected.
- The full existing suite still passes: `python -m unittest discover -q` (244 tests before this task + the new ones).

## 7. `.gitignore`

The repository ignores `*.plsq`, `*.part`, `*.json`. Add an allow-list **only** for `tests/golden/errors/*` and `tests/golden/stamp/*` (the files of this task and their READMEs). Check with `git status` / `git check-ignore -v` that nothing else becomes tracked.

## 8. Hand-over to the plugin repository (not done in this session)

The user copies `tests/golden/errors/` and `tests/golden/stamp/` into `art-stream-uep/spec/golden/errors/` and `art-stream-uep/spec/golden/stamp/` and updates `art-stream-uep/spec/SPEC_SOURCE.md` (commit hash of `dev/v1.2.2`, SHA-256 list). Do not write into `art-stream-uep` from this session. The report gives the exact list of files with SHA-256 so the copy can be checked.

## 9. Report and definition of done

`docs/reports_X1/REPORT_X1.md`: what was added, every file with size and SHA-256, the commands run and their output (tests, verify, the large stamp generation with time and MB/s), anything chosen differently from this task and why, what could not be verified, open questions.

Done when: all files of sections 4 and 5 exist (or a clearly reported reason), the tests of section 6 pass, the report exists, everything is committed on `dev/v1.2.2`, nothing pushed.
