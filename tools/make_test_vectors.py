#!/usr/bin/env python3
"""
Generate PLSQ test vectors for the UE plugin (task X1).

Two sets of small, exactly known files:

1. Error vectors (tests/golden/errors/): one file per reader error code of
   docs/PLSQ_SPEC.md section 10 plus a few valid edge cases, and expected.json
   with the expected result of each file.
2. Frame-stamp files (tests/golden/stamp/): valid files in which every frame
   carries its own index, so a reader can prove it returned exactly frame f.

Usage:
    python tools/make_test_vectors.py
        Regenerate both committed sets (errors + small stamp files).
        Without --not-finalized-source the NotFinalized vector is skipped.

    python tools/make_test_vectors.py --not-finalized-source <file.plsq.part>
        Same, and cut the NotFinalized vector from a real interrupted
        recording (the source is only read, never modified).

    python tools/make_test_vectors.py --errors-only / --stamps-only
    python tools/make_test_vectors.py --errors-dir <dir> --stamp-dir <dir>

    python tools/make_test_vectors.py --stamp --chips 64000 --fps 60 \\
        --seconds 60 --out .bench_out/stamp_64000c_60fps_3600f.plsq
        Generate a (large) frame-stamp file through PlsqWriter (streaming).

    python tools/make_test_vectors.py --verify-stamp <file.plsq>
        [--frames a:b] [--sample N] [--seed S]
        Check every frame (or a range / a random sample) against the
        frame-stamp pattern. Prints OK or the first mismatch.
        Exit code 0 = OK, 1 = mismatch, 2 = file cannot be read.
"""

import hashlib
import json
import os
import random
import struct
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import plsq  # noqa: E402
from mapping import count_universes, effective_chips_per_universe  # noqa: E402
from plsq import PlsqError, PlsqWriter, build_meta  # noqa: E402

# Recorder version these vectors belong to (fixed, so that regeneration with a
# later recorder still gives byte-identical files).
VECTORS_RECORDER_VERSION = "1.2.2"
RECORDED_AT = "2026-01-01T00:00:00+00:00"

ERRORS_DIR = os.path.join(_ROOT, "tests", "golden", "errors")
STAMP_DIR = os.path.join(_ROOT, "tests", "golden", "stamp")
NOT_FINALIZED_NAME = "err_not_finalized.plsq.part"
NOT_FINALIZED_FRAMES = 2

# ---------------------------------------------------------------------------
# Error vectors: base file (130 chips, 30 fps, 10 frames, 2 universes)
# ---------------------------------------------------------------------------

BASE_CHIPS = 130
BASE_FPS = 30
BASE_FRAMES = 10
BASE_START_UNIVERSE = 1
BASE_START_CHANNEL = 1
BASE_CPU_SETTING = 0  # auto -> 128


def _base_meta(**extra):
    eff = effective_chips_per_universe(BASE_CPU_SETTING)
    meta = build_meta(
        file_format="plsq",
        sequence_id="00000000-0000-4000-8000-0000000000e1",
        recorded_at=RECORDED_AT,
        recorder_version=VECTORS_RECORDER_VERSION,
        model_name="Test Vectors",
        model_chips_tag="test_vectors_chip",
        model_chips_count=BASE_CHIPS,
        sequence_name="Error vector base",
        sequence_version="1.0",
        fps=BASE_FPS,
        frame_count=BASE_FRAMES,
        frame_stride=BASE_CHIPS * 4,
        start_universe=BASE_START_UNIVERSE,
        start_channel=BASE_START_CHANNEL,
        chips_per_universe=eff,
        universes_count=count_universes(
            BASE_START_UNIVERSE, BASE_START_CHANNEL, eff, BASE_CHIPS
        ),
        developer_notes="PLSQ test vector (task X1), base file 130 chips x 10 frames",
        short_packets=extra.pop("short_packets", None),
        first_frame_partial=extra.pop("first_frame_partial", None),
    )
    meta.update(extra)
    return meta


def _base_frame(f):
    """Same pattern as the conformance file: (f*7 + c*3 + k) mod 256."""
    stride = BASE_CHIPS * 4
    return bytes((f * 7 + (i // 4) * 3 + (i % 4)) % 256 for i in range(stride))


def _write_plsq(path, chips, fps, frame_count, frame_fn, meta, start_universe=1,
                start_channel=1, cpu_setting=0):
    """Write a valid PLSQ file through PlsqWriter (<path>.tmp then rename)."""
    tmp = path + ".tmp"
    w = PlsqWriter()
    w.open(tmp, chips, fps, start_universe, start_channel, cpu_setting)
    try:
        for f in range(frame_count):
            w.append_frame(frame_fn(f))
        w.finalize(meta)
    except BaseException:
        w.abort()
        raise
    os.replace(tmp, path)


def _write_base_file(path, meta):
    _write_plsq(
        path, BASE_CHIPS, BASE_FPS, BASE_FRAMES, _base_frame, meta,
        BASE_START_UNIVERSE, BASE_START_CHANNEL, BASE_CPU_SETTING,
    )


# ---------------------------------------------------------------------------
# Byte-level edits (exactly one defect each)
# ---------------------------------------------------------------------------

def _with_header(data, **fields):
    """Return data with header fields replaced and header CRC recomputed."""
    h = plsq._unpack_header(data[:plsq.HEADER_SIZE])
    h.update(fields)
    return plsq._pack_header(h) + data[plsq.HEADER_SIZE:]


def _with_meta_bytes(data, meta_bytes):
    """Replace the meta block (zero-filled to capacity) and fix size, CRC, header."""
    h = plsq._unpack_header(data[:plsq.HEADER_SIZE])
    off = h["meta_offset"]
    assert len(meta_bytes) <= plsq.META_CAPACITY
    block = meta_bytes + b"\x00" * (plsq.META_CAPACITY - len(meta_bytes))
    data = data[:off] + block + data[off + plsq.META_CAPACITY:]
    return _with_header(
        data, meta_size=len(meta_bytes), meta_crc32=plsq._crc32(meta_bytes)
    )


def _meta_bytes_of(data):
    h = plsq._unpack_header(data[:plsq.HEADER_SIZE])
    return data[h["meta_offset"]:h["meta_offset"] + h["meta_size"]]


def _flip_meta_byte(data, needle):
    """Change one meta byte (first byte of `needle`), keep meta CRC as is."""
    h = plsq._unpack_header(data[:plsq.HEADER_SIZE])
    meta = _meta_bytes_of(data)
    pos = meta.index(needle)
    abs_pos = h["meta_offset"] + pos
    old = data[abs_pos]
    new = old ^ 0x20  # ASCII letter case swap: JSON stays valid
    return data[:abs_pos] + bytes([new]) + data[abs_pos + 1:]


# ---------------------------------------------------------------------------
# Error vector table
# ---------------------------------------------------------------------------

def _build_error_vectors(base, base_meta):
    """Return {name: (bytes, result, code, note)} for all synthetic vectors."""
    payload_end = plsq.PAYLOAD_OFFSET + BASE_FRAMES * BASE_CHIPS * 4
    v = {}

    v["err_truncated_short.plsq"] = (
        base[:127], "error", "Truncated",
        "first 127 bytes of a valid file",
    )
    cut = plsq.PAYLOAD_OFFSET + 4 * BASE_CHIPS * 4 + 260
    v["err_truncated_payload.plsq"] = (
        base[:cut], "error", "Truncated",
        "valid header and meta, file cut inside frame 4 (%d of %d bytes)"
        % (cut, payload_end),
    )
    v["err_bad_magic.plsq"] = (
        _with_header(base, magic=b"PLSX"), "error", "BadMagic",
        "magic = PLSX, header CRC recomputed",
    )
    v["err_unsupported_version.plsq"] = (
        _with_header(base, format_version=2), "error", "UnsupportedVersion",
        "format_version = 2, header CRC recomputed",
    )
    v["err_bad_header_size.plsq"] = (
        _with_header(base, header_size=64), "error", "BadHeaderSize",
        "header_size = 64, header CRC recomputed",
    )
    bad = bytearray(base)
    bad[18] ^= 0x01  # fps 30 -> 31
    v["err_header_crc.plsq"] = (
        bytes(bad), "error", "HeaderCrcMismatch",
        "header byte 18 (fps low byte) changed 30 -> 31, CRC not updated",
    )
    v["err_unsupported_flags.plsq"] = (
        _with_header(base, flags=plsq.FLAG_FINALIZED | plsq.FLAG_COMPRESSED),
        "error", "UnsupportedFlags",
        "flags = FINALIZED | COMPRESSED (3), header CRC recomputed",
    )
    v["err_unsupported_layout.plsq"] = (
        _with_header(base, channels_per_chip=3), "error", "UnsupportedLayout",
        "channels_per_chip = 3 (frame_stride left at 520), header CRC recomputed",
    )
    v["err_out_of_range.plsq"] = (
        _with_header(base, fps=0), "error", "OutOfRange",
        "fps = 0, header CRC recomputed",
    )
    v["err_inconsistent_header.plsq"] = (
        _with_header(base, frame_stride=516), "error", "InconsistentHeader",
        "frame_stride = 516 (chips_count x 4 = 520), payload_size left at 5200 "
        "(so payload_size != frame_count x frame_stride is violated too, same "
        "code), header CRC recomputed",
    )
    v["err_meta_out_of_bounds.plsq"] = (
        _with_header(base, meta_size=1), "error", "MetaOutOfBounds",
        "meta_size = 1, header CRC recomputed",
    )
    v["err_meta_crc.plsq"] = (
        _flip_meta_byte(base, b"Test Vectors"), "error", "MetaCrcMismatch",
        "first letter of model_name changed T -> t, meta CRC not updated",
    )
    m = dict(base_meta)
    m["lost_packets"] = 0
    v["err_meta_parse.plsq"] = (
        _with_meta_bytes(base, plsq._encode_meta(m)), "error", "MetaParseError",
        "lost_packets is the JSON number 0, not a string; meta size, meta CRC "
        "and header CRC recomputed",
    )
    m = dict(base_meta)
    del m["model_name"]
    v["err_missing_meta_key.plsq"] = (
        _with_meta_bytes(base, plsq._encode_meta(m)), "error", "MissingMetaKey",
        "required key model_name removed; meta size, meta CRC and header CRC "
        "recomputed",
    )
    m = dict(base_meta)
    m["sequence_fps"] = "60"
    v["err_meta_mismatch.plsq"] = (
        _with_meta_bytes(base, plsq._encode_meta(m)), "error", "MetaMismatch",
        "sequence_fps = 60, header fps = 30; meta size, meta CRC and header CRC "
        "recomputed",
    )
    v["ok_trailing_data.plsq"] = (
        base + b"\xa5" * 16, "warning", "TrailingData",
        "valid file + 16 bytes 0xA5 after the payload",
    )
    v["ok_no_optional_keys.plsq"] = (
        base, "ok", "",
        "valid, meta without short_packets and first_frame_partial",
    )
    return v


def _check_vector(path, result, code):
    """Return None if read_info and validate give the expected result, else text."""
    try:
        plsq.read_info(path)
        got_read = ""
    except PlsqError as exc:
        got_read = exc.code
    problems = plsq.validate(path)
    got_val = problems[0].split(":")[0] if problems else ""
    want_read = code if result == "error" else ""
    if got_read != want_read:
        return "read_info gave %r, expected %r" % (got_read, want_read)
    if got_val != code:
        return "validate first problem %r, expected %r" % (got_val, code)
    return None


def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            b = fp.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def make_not_finalized(source, out_path, log=print):
    """Copy header + meta area + 2 whole frames of a real .part file (read only)."""
    sha_before = _sha256_file(source)
    with open(source, "rb") as fp:
        raw = fp.read(plsq.HEADER_SIZE)
        if len(raw) < plsq.HEADER_SIZE:
            raise ValueError("source shorter than 128 bytes: %s" % source)
        h = plsq._unpack_header(raw)
        if h["magic"] != plsq.MAGIC or h["flags"] & plsq.FLAG_FINALIZED:
            raise ValueError("source is not an unfinalized PLSQ file: %s" % source)
        size = h["payload_offset"] + NOT_FINALIZED_FRAMES * h["frame_stride"]
        fp.seek(0)
        data = fp.read(size)
    if len(data) != size:
        raise ValueError(
            "source too short: need %d bytes, got %d" % (size, len(data))
        )
    sha_after = _sha256_file(source)
    if sha_before != sha_after:
        raise RuntimeError("source changed while reading: %s" % source)
    with open(out_path, "wb") as fp:
        fp.write(data)
    log("  NotFinalized source: %s" % source)
    log("    size %d bytes, SHA-256 before = after = %s" % (
        os.path.getsize(source), sha_after))
    note = (
        "real interrupted %d-chip %d fps recording (placeholder header: "
        "FINALIZED = 0, frame_count = 0, meta_size = 2), cut to "
        "payload_offset + %d x frame_stride (%d) = %d bytes"
        % (h["chips_count"], h["fps"], NOT_FINALIZED_FRAMES, h["frame_stride"],
           size)
    )
    return note


def spec_commit_hash():
    """Git hash of the last commit that touched docs/PLSQ_SPEC.md, or ''."""
    try:
        out = subprocess.run(
            ["git", "-C", _ROOT, "log", "-1", "--format=%H", "--",
             "docs/PLSQ_SPEC.md"],
            capture_output=True, text=True, timeout=10,
        )
        h = out.stdout.strip()
        return h if out.returncode == 0 and len(h) == 40 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def generate_errors(out_dir, not_finalized_source=None, spec_commit=None,
                    log=print):
    """Write all error vectors + expected.json into out_dir.

    Returns the expected.json dict. Raises RuntimeError if a vector does not
    give its expected result in plsq.read_info() / plsq.validate().
    """
    os.makedirs(out_dir, exist_ok=True)
    base_meta = _base_meta()
    base_path = os.path.join(out_dir, "_base.tmp")
    _write_base_file(base_path, base_meta)
    with open(base_path, "rb") as fp:
        base = fp.read()
    os.remove(base_path)

    entries = {}
    for name, (data, result, code, note) in _build_error_vectors(
        base, base_meta
    ).items():
        with open(os.path.join(out_dir, name), "wb") as fp:
            fp.write(data)
        entries[name] = (result, code, note)

    # Valid files with different meta: written through PlsqWriter.
    _write_base_file(
        os.path.join(out_dir, "ok_optional_keys.plsq"),
        _base_meta(short_packets=3, first_frame_partial=True),
    )
    entries["ok_optional_keys.plsq"] = (
        "ok", "", "valid, meta with short_packets = 3 and first_frame_partial = 1"
    )
    _write_base_file(
        os.path.join(out_dir, "ok_unknown_key.plsq"),
        _base_meta(x_test_unknown="kept"),
    )
    entries["ok_unknown_key.plsq"] = (
        "ok", "", "meta key x_test_unknown = kept (must be passed through)"
    )

    nf_path = os.path.join(out_dir, NOT_FINALIZED_NAME)
    if not_finalized_source:
        note = make_not_finalized(not_finalized_source, nf_path, log=log)
        entries[NOT_FINALIZED_NAME] = ("error", "NotFinalized", note)
    else:
        log("  SKIPPED: %s (no --not-finalized-source given; no synthetic "
            "replacement is made)" % NOT_FINALIZED_NAME)
        if os.path.exists(nf_path):
            log("    existing file kept unchanged and listed: %s" % nf_path)
            entries[NOT_FINALIZED_NAME] = ("error", "NotFinalized", None)

    # Self-check every vector.
    failures = []
    for name, (result, code, _note) in sorted(entries.items()):
        msg = _check_vector(os.path.join(out_dir, name), result, code)
        if msg:
            failures.append("%s: %s" % (name, msg))
    if failures:
        raise RuntimeError("vector self-check failed:\n  " + "\n  ".join(failures))

    if spec_commit is None:
        spec_commit = spec_commit_hash()
        if not spec_commit:
            log("  WARNING: git hash of docs/PLSQ_SPEC.md not found; "
                "spec_commit = unknown")
            spec_commit = "unknown"

    old = _load_json(os.path.join(out_dir, "expected.json"))
    files = {}
    for name, (result, code, note) in entries.items():
        if note is None:  # kept NotFinalized file: keep the old note
            note = old.get("files", {}).get(name, {}).get("note", "")
        files[name] = {"result": result, "code": code, "note": note}
    expected = {
        "format_version": "1",
        "generated_by": "tools/make_test_vectors.py",
        "recorder_version": VECTORS_RECORDER_VERSION,
        "spec_commit": spec_commit,
        "files": files,
    }
    with open(os.path.join(out_dir, "expected.json"), "w", encoding="utf-8",
              newline="\n") as fp:
        fp.write(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    log("  %d error vectors + expected.json -> %s" % (len(files), out_dir))
    return expected


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Frame-stamp files
# ---------------------------------------------------------------------------

# t[x] = (x + 7f) mod 256 depends only on f mod 256 (7 is odd, 7f mod 256).
_TABLES = [bytes(((x + 7 * f) & 0xFF) for x in range(256)) for f in range(256)]

STAMP_SMALL = (
    # (file name, chips, fps, frame_count)
    ("stamp_130c_30fps_300f.plsq", 130, 30, 300),
    ("stamp_1c_60fps_10f.plsq", 1, 60, 10),
)


def stamp_base(chips):
    """base(c, k) = (3c + k) mod 256 for all bytes of one frame."""
    return bytes(((3 * (i >> 2) + (i & 3)) & 0xFF) for i in range(chips * 4))


def stamp_frame(base, f):
    """Frame f of the frame-stamp pattern (docs: tests/golden/stamp/README.md)."""
    b = bytearray(base.translate(_TABLES[f & 0xFF]))
    s = struct.pack("<I", f)
    b[0:4] = s
    b[-4:] = s
    return bytes(b)


def _stamp_meta(chips, fps, frame_count, start_universe, start_channel, eff):
    return build_meta(
        file_format="plsq",
        sequence_id="00000000-0000-4000-8000-0000000000f1",
        recorded_at=RECORDED_AT,
        recorder_version=VECTORS_RECORDER_VERSION,
        model_name="Frame Stamp",
        model_chips_tag="frame_stamp_chip",
        model_chips_count=chips,
        sequence_name="frame-stamp v1",
        sequence_version="1",
        fps=fps,
        frame_count=frame_count,
        frame_stride=chips * 4,
        start_universe=start_universe,
        start_channel=start_channel,
        chips_per_universe=eff,
        universes_count=count_universes(start_universe, start_channel, eff, chips),
        developer_notes=(
            "frame-stamp v1: chip 0 and last chip = frame index as uint32 LE "
            "(R=byte0..W=byte3); other bytes = (3*chip + channel + 7*frame) mod 256"
        ),
    )


def generate_stamp(out_path, chips, fps, frame_count, start_universe=1,
                   start_channel=1, cpu_setting=0):
    """Write a frame-stamp file through PlsqWriter. Returns elapsed seconds."""
    eff = effective_chips_per_universe(cpu_setting)
    meta = _stamp_meta(chips, fps, frame_count, start_universe, start_channel, eff)
    base = stamp_base(chips)
    d = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(d, exist_ok=True)
    t0 = time.perf_counter()
    _write_plsq(
        out_path, chips, fps, frame_count, lambda f: stamp_frame(base, f), meta,
        start_universe, start_channel, cpu_setting,
    )
    return time.perf_counter() - t0


def generate_small_stamps(out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    for name, chips, fps, frames in STAMP_SMALL:
        path = os.path.join(out_dir, name)
        generate_stamp(path, chips, fps, frames)
        problems = plsq.validate(path)
        if problems:
            raise RuntimeError("%s not valid: %s" % (name, problems))
        log("  %s (%d bytes)" % (path, os.path.getsize(path)))


def _parse_range(text, frame_count):
    a, sep, b = text.partition(":")
    if not sep:
        raise ValueError("--frames must be a:b")
    start = int(a) if a.strip() else 0
    stop = int(b) if b.strip() else frame_count
    start, stop, _ = slice(start, stop).indices(frame_count)
    return start, stop


def verify_stamp(path, frames=None, sample=None, seed=0):
    """Check a frame-stamp file. Returns (ok, message).

    frames: "a:b" (Python slice semantics) or None for all frames.
    sample: number of random frames (seeded) instead of a range.
    Raises PlsqError if the file cannot be read as PLSQ.
    """
    h, _meta = plsq.read_info(path)
    chips, stride, n = h["chips_count"], h["frame_stride"], h["frame_count"]
    base = stamp_base(chips)
    if sample is not None:
        rng = random.Random(seed)
        idx = sorted(rng.sample(range(n), min(int(sample), n)))
        runs = [(i, 1) for i in idx]
    else:
        start, stop = _parse_range(frames, n) if frames else (0, n)
        chunk = max(1, min(h["fps"], (16 << 20) // stride))
        runs = [(f, min(chunk, stop - f)) for f in range(start, stop, chunk)]
    checked = 0
    with open(path, "rb") as fp:
        for first, count in runs:
            fp.seek(h["payload_offset"] + first * stride)
            raw = fp.read(count * stride)
            if len(raw) != count * stride:
                return False, "short read at frame %d" % first
            for j in range(count):
                f = first + j
                actual = raw[j * stride:(j + 1) * stride]
                expected = stamp_frame(base, f)
                if actual != expected:
                    i = next(
                        i for i in range(stride) if actual[i] != expected[i]
                    )
                    return False, (
                        "MISMATCH frame %d chip %d channel %d (%s): expected %d, "
                        "actual %d" % (f, i // 4, i % 4, "RGBW"[i % 4],
                                       expected[i], actual[i])
                    )
                checked += 1
    return True, "OK: %d of %d frames checked" % (checked, n)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        description="Generate / verify PLSQ test vectors (task X1)."
    )
    p.add_argument("--errors-dir", default=ERRORS_DIR)
    p.add_argument("--stamp-dir", default=STAMP_DIR)
    p.add_argument("--not-finalized-source", default=None,
                   help="real interrupted .plsq.part file (read only)")
    p.add_argument("--spec-commit", default=None,
                   help="override the spec git hash written to expected.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--errors-only", action="store_true")
    g.add_argument("--stamps-only", action="store_true")
    g.add_argument("--stamp", action="store_true",
                   help="generate one frame-stamp file (needs --chips --fps "
                        "--seconds|--frame-count --out)")
    g.add_argument("--verify-stamp", metavar="FILE")
    p.add_argument("--chips", type=int)
    p.add_argument("--fps", type=int)
    p.add_argument("--seconds", type=float)
    p.add_argument("--frame-count", type=int)
    p.add_argument("--start-universe", type=int, default=1)
    p.add_argument("--start-channel", type=int, default=1)
    p.add_argument("--chips-per-universe", type=int, default=0)
    p.add_argument("--out")
    p.add_argument("--frames", help="verify range a:b (slice semantics)")
    p.add_argument("--sample", type=int, help="verify N random frames")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    if args.verify_stamp:
        try:
            ok, msg = verify_stamp(args.verify_stamp, args.frames, args.sample,
                                   args.seed)
        except (PlsqError, OSError, ValueError) as exc:
            print("ERROR: %s" % exc)
            return 2
        print(msg)
        return 0 if ok else 1

    if args.stamp:
        if not (args.chips and args.fps and args.out
                and (args.seconds or args.frame_count)):
            p.error("--stamp needs --chips, --fps, --seconds or --frame-count, "
                    "--out")
        n = args.frame_count or int(round(args.seconds * args.fps))
        elapsed = generate_stamp(
            args.out, args.chips, args.fps, n, args.start_universe,
            args.start_channel, args.chips_per_universe,
        )
        size = os.path.getsize(args.out)
        print("Written: %s" % args.out)
        print("  %d chips, %d fps, %d frames, %d bytes (%.1f MB)"
              % (args.chips, args.fps, n, size, size / 1e6))
        print("  time %.2f s, %.1f MB/s" % (elapsed, size / 1e6 / elapsed))
        return 0

    if not args.stamps_only:
        print("Error vectors:")
        generate_errors(args.errors_dir, args.not_finalized_source,
                        args.spec_commit)
    if not args.errors_only:
        print("Frame-stamp files:")
        generate_small_stamps(args.stamp_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
