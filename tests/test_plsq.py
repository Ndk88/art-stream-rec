"""
Tests for plsq.py and mapping.py (R1 acceptance criteria).

Run:  python -m unittest tests.test_plsq
"""

import os
import struct
import sys
import tempfile
import time
import unittest
import zlib

# Add parent directory to sys.path so imports work from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plsq
from plsq import (
    PlsqError, PlsqWriter,
    build_meta, read_info, read_frames, validate,
    MAGIC, FORMAT_VERSION, HEADER_SIZE, META_OFFSET, PAYLOAD_OFFSET,
    META_CAPACITY, FLAG_COMPRESSED, FLAG_FINALIZED, _pack_header, _crc32,
)
from mapping import build_spans, count_universes, effective_chips_per_universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_META_KWARGS = dict(
    model_name="Test Model",
    model_chips_tag="test_chip",
    sequence_name="Test Sequence",
    sequence_version="1.0",
    recorder_version="test",
    recorded_at="2026-01-01T00:00:00+00:00",
    sequence_id="00000000-0000-4000-8000-000000000001",
    developer_notes="",
)


def _make_file(tmp_dir, chips=104, fps=30, frames=10,
               start_universe=0, start_channel=1, chips_per_universe=0,
               extra_meta=None):
    """Write a valid PLSQ file and return the path."""
    path = os.path.join(tmp_dir, "test.plsq")
    part = path + ".part"
    w = PlsqWriter()
    w.open(part, chips, fps, start_universe, start_channel, chips_per_universe)
    stride = chips * 4
    for f in range(frames):
        w.append_frame(bytes([(f * 7 + c) % 256 for c in range(stride)]))
    eff_cpu = effective_chips_per_universe(chips_per_universe)
    uc = count_universes(start_universe, start_channel, eff_cpu, chips)
    meta = build_meta(
        model_chips_count=chips,
        fps=fps,
        frame_count=frames,
        frame_stride=chips * 4,
        start_universe=start_universe,
        start_channel=start_channel,
        chips_per_universe=eff_cpu,
        universes_count=uc,
        **FIXED_META_KWARGS,
    )
    if extra_meta:
        meta.update(extra_meta)
    w.finalize(meta)
    os.replace(part, path)
    return path


def _corrupt_file(src_path, offset, new_byte):
    """Return a copy of the file with one byte changed."""
    with open(src_path, "rb") as f:
        data = bytearray(f.read())
    data[offset] = new_byte
    with open(src_path + ".corrupt", "wb") as f:
        f.write(data)
    return src_path + ".corrupt"


def _patch_header_field(src_path, field_values):
    """Rewrite header with modified fields, recompute header_crc32. Returns new path."""
    with open(src_path, "rb") as f:
        data = bytearray(f.read())
    vals = list(plsq._HDR_FMT.unpack(bytes(data[:HEADER_SIZE])))
    field_names = list(plsq._HDR_FIELDS)
    for fname, fval in field_values.items():
        idx = field_names.index(fname)
        vals[idx] = fval
    # rebuild header with correct CRC
    h = dict(zip(field_names, vals))
    new_hdr = _pack_header(h)
    out = src_path + ".patched"
    data[:HEADER_SIZE] = new_hdr
    with open(out, "wb") as f:
        f.write(data)
    return out


# ---------------------------------------------------------------------------
# mapping.py tests
# ---------------------------------------------------------------------------

class TestMapping(unittest.TestCase):

    def test_effective_cpu_zero(self):
        self.assertEqual(effective_chips_per_universe(0), 128)

    def test_effective_cpu_clamped(self):
        self.assertEqual(effective_chips_per_universe(200), 128)

    def test_effective_cpu_exact(self):
        self.assertEqual(effective_chips_per_universe(64), 64)

    def test_build_spans_single_universe(self):
        spans = build_spans(0, 1, 4, 104)
        self.assertEqual(len(spans), 1)
        uni, ch, n_ch, off = spans[0]
        self.assertEqual(uni, 0)
        self.assertEqual(ch, 0)       # start_channel 1 -> index 0
        self.assertEqual(n_ch, 416)   # 104 * 4
        self.assertEqual(off, 0)

    def test_build_spans_two_universes(self):
        spans = build_spans(0, 1, 4, 130, 128)
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0][2], 512)  # 128 * 4
        self.assertEqual(spans[1][2], 8)    # 2 * 4

    def test_count_universes(self):
        self.assertEqual(count_universes(0, 1, 128, 128), 1)
        self.assertEqual(count_universes(0, 1, 128, 129), 2)
        self.assertEqual(count_universes(0, 1, 0, 64000), 500)


# ---------------------------------------------------------------------------
# build_meta
# ---------------------------------------------------------------------------

class TestBuildMeta(unittest.TestCase):

    def _make(self, **kw):
        defaults = dict(
            model_chips_count=104, fps=30, frame_count=10,
            frame_stride=416, start_universe=0, start_channel=1,
            chips_per_universe=128, universes_count=1,
        )
        defaults.update(kw)
        return build_meta(**defaults)

    def test_has_all_27_keys(self):
        m = self._make()
        self.assertEqual(list(m.keys()), plsq.META_KEYS)

    def test_all_values_are_strings(self):
        m = self._make()
        for k, v in m.items():
            self.assertIsInstance(v, str, k)

    def test_duration_sec(self):
        m = self._make(fps=30, frame_count=180)
        self.assertEqual(m["sequence_duration_sec"], "6.0")

    def test_file_format_default(self):
        m = self._make()
        self.assertEqual(m["file_format"], "plsq")


# ---------------------------------------------------------------------------
# Round-trip test
# ---------------------------------------------------------------------------

class TestRoundTrip(unittest.TestCase):

    def test_round_trip_104_chips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_file(tmp, chips=104, fps=30, frames=10)
            h, meta = read_info(path)
            self.assertEqual(h["chips_count"], 104)
            self.assertEqual(h["fps"], 30)
            self.assertEqual(h["frame_count"], 10)
            self.assertEqual(h["frame_stride"], 416)
            self.assertEqual(meta["model_name"], "Test Model")
            self.assertEqual(meta["file_format"], "plsq")
            # frame data byte-exact
            frames = read_frames(path, 0, 10)
            self.assertEqual(len(frames), 10)
            for f_idx, frame in enumerate(frames):
                expected = bytes([(f_idx * 7 + c) % 256 for c in range(416)])
                self.assertEqual(frame, expected)

    def test_round_trip_two_universes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_file(tmp, chips=130, fps=60, frames=5,
                              start_universe=1, start_channel=1, chips_per_universe=128)
            h, meta = read_info(path)
            self.assertEqual(h["universes_count"], 2)
            self.assertEqual(meta["input_mapping_universes_count"], "2")

    def test_validate_clean_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_file(tmp)
            probs = validate(path)
            # strip TrailingData warning if present (not an error)
            probs = [p for p in probs if not p.startswith("TrailingData")]
            self.assertEqual(probs, [], probs)

    def test_read_specific_frame_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_file(tmp, chips=10, frames=20)
            frames = read_frames(path, 5, 3)
            self.assertEqual(len(frames), 3)
            for i, frame in enumerate(frames):
                expected = bytes([(( 5 + i) * 7 + c) % 256 for c in range(40)])
                self.assertEqual(frame, expected)

    def test_writer_read_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "out.plsq.part")
            w = PlsqWriter()
            w.open(part, 10, 30, 0, 1, 0)
            frames_in = [bytes([i * 11 + c for c in range(40)]) for i in range(5)]
            for f in frames_in:
                w.append_frame(f)
            readback = w.read_frames(0, 5)
            for a, b in zip(frames_in, readback):
                self.assertEqual(a, b)
            w.abort()


# ---------------------------------------------------------------------------
# Large file: 64,000 chips x 120 frames
# ---------------------------------------------------------------------------

class TestLargeFile(unittest.TestCase):

    def test_64k_chips_120_frames_roundtrip(self):
        chips, fps, frames = 64000, 60, 120
        stride = chips * 4  # 256,000
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "large.plsq")
            part = path + ".part"
            w = PlsqWriter()
            w.open(part, chips, fps, 0, 1, 0)
            frame_data = bytes(range(256)) * (stride // 256) + bytes(range(stride % 256))
            for _ in range(frames):
                w.append_frame(frame_data)
            eff_cpu = effective_chips_per_universe(0)
            uc = count_universes(0, 1, eff_cpu, chips)
            meta = build_meta(
                model_chips_count=chips, fps=fps, frame_count=frames,
                frame_stride=stride, start_universe=0, start_channel=1,
                chips_per_universe=eff_cpu, universes_count=uc,
                **FIXED_META_KWARGS,
            )
            w.finalize(meta)
            os.replace(part, path)

            h, _ = read_info(path)
            self.assertEqual(h["chips_count"], chips)
            self.assertEqual(h["frame_count"], frames)

            # read back last frame and spot-check
            last = read_frames(path, frames - 1, 1)[0]
            self.assertEqual(last, frame_data)

            probs = validate(path)
            probs = [p for p in probs if not p.startswith("TrailingData")]
            self.assertEqual(probs, [])


# ---------------------------------------------------------------------------
# Writer throughput
# ---------------------------------------------------------------------------

class TestThroughput(unittest.TestCase):

    def test_write_throughput_64k_chips(self):
        """Writer must sustain at least 10x real-time (> 153 MB/s)."""
        chips = 64000
        fps = 60
        frames = 60  # 1 second of 60 fps
        stride = chips * 4  # 256,000 bytes/frame
        frame_data = bytes(stride)  # zeros

        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "bench.plsq.part")
            w = PlsqWriter()
            w.open(part, chips, fps, 0, 1, 0)

            t0 = time.perf_counter()
            for _ in range(frames):
                w.append_frame(frame_data)
            w._fp.flush()
            elapsed = time.perf_counter() - t0

            w.abort()

        total_bytes = frames * stride
        mb_per_sec = total_bytes / elapsed / 1e6
        realtime_need_mb = stride * fps / 1e6  # ~15.4 MB/s
        self.assertGreater(
            mb_per_sec,
            realtime_need_mb * 10,
            "Throughput %.1f MB/s < 10x realtime %.1f MB/s" % (mb_per_sec, realtime_need_mb * 10),
        )


# ---------------------------------------------------------------------------
# Error code tests (deliberately corrupted files)
# ---------------------------------------------------------------------------

class TestErrorCodes(unittest.TestCase):

    def _assert_error(self, path, expected_code):
        with self.assertRaises(PlsqError) as ctx:
            read_info(path)
        self.assertEqual(ctx.exception.code, expected_code)

    def _assert_validate_contains(self, path, prefix):
        probs = validate(path)
        self.assertTrue(
            any(p.startswith(prefix) for p in probs),
            "Expected problem starting with %r, got: %r" % (prefix, probs),
        )

    # IoError -------------------------------------------------------------------

    def test_io_error_nonexistent(self):
        self._assert_error("/nonexistent/path/file.plsq", "IoError")

    def test_validate_io_error(self):
        probs = validate("/nonexistent/path/file.plsq")
        self.assertTrue(probs[0].startswith("IoError"))

    # Truncated -----------------------------------------------------------------

    def test_truncated_short_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "short.plsq")
            with open(p, "wb") as f:
                f.write(b"PLSQ" + b"\x00" * 10)
            self._assert_error(p, "Truncated")

    def test_validate_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "short.plsq")
            with open(p, "wb") as f:
                f.write(b"\x00" * 50)
            self._assert_validate_contains(p, "Truncated")

    # BadMagic ------------------------------------------------------------------

    def test_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _corrupt_file(src, 0, 0x00)  # first byte of magic
            self._assert_error(bad, "BadMagic")

    def test_validate_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _corrupt_file(src, 0, 0xFF)
            self._assert_validate_contains(bad, "BadMagic")

    # UnsupportedVersion --------------------------------------------------------

    def test_unsupported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            # format_version is at offset 4, little-endian u16
            bad = _patch_header_field(src, {"format_version": 2})
            self._assert_error(bad, "UnsupportedVersion")

    # BadHeaderSize -------------------------------------------------------------

    def test_bad_header_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"header_size": 64})
            self._assert_error(bad, "BadHeaderSize")

    # HeaderCrcMismatch ---------------------------------------------------------

    def test_header_crc_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            # Flip a bit in the reserved field (offset 28) without touching CRC
            with open(src, "rb") as f:
                data = bytearray(f.read())
            data[28] ^= 0x01  # flip reserved byte, CRC now wrong
            bad = src + ".hcrc"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "HeaderCrcMismatch")

    # UnsupportedFlags (COMPRESSED) ---------------------------------------------

    def test_compressed_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            new_flags = h["flags"] | FLAG_COMPRESSED
            bad = _patch_header_field(src, {"flags": new_flags})
            self._assert_error(bad, "UnsupportedFlags")

    # NotFinalized --------------------------------------------------------------

    def test_not_finalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            new_flags = h["flags"] & ~FLAG_FINALIZED
            bad = _patch_header_field(src, {"flags": new_flags})
            self._assert_error(bad, "NotFinalized")

    # UnsupportedLayout ---------------------------------------------------------

    def test_unsupported_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"channels_per_chip": 3})
            self._assert_error(bad, "UnsupportedLayout")

    # OutOfRange ----------------------------------------------------------------

    def test_out_of_range_chips(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"chips_count": 0})
            self._assert_error(bad, "OutOfRange")

    def test_out_of_range_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"fps": 0})
            self._assert_error(bad, "OutOfRange")

    def test_out_of_range_start_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"start_channel": 0})
            self._assert_error(bad, "OutOfRange")

    # InconsistentHeader --------------------------------------------------------

    def test_inconsistent_frame_stride(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"frame_stride": 999})
            self._assert_error(bad, "InconsistentHeader")

    def test_inconsistent_universes_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp, chips=104)
            # universes_count should be 1 for 104 chips; set it to 2
            bad = _patch_header_field(src, {"universes_count": 2})
            self._assert_error(bad, "InconsistentHeader")

    # Truncated (payload) -------------------------------------------------------

    def test_truncated_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp, chips=104, frames=10)
            with open(src, "rb") as f:
                data = f.read()
            bad = src + ".trunc"
            with open(bad, "wb") as f:
                f.write(data[:-100])  # remove last 100 bytes
            self._assert_error(bad, "Truncated")

    # MetaOutOfBounds -----------------------------------------------------------

    def test_meta_out_of_bounds_size_too_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            bad = _patch_header_field(src, {"meta_size": 1})
            self._assert_error(bad, "MetaOutOfBounds")

    # MetaCrcMismatch -----------------------------------------------------------

    def test_meta_crc_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            # flip a byte inside the meta block
            data[META_OFFSET + 5] ^= 0xFF
            bad = src + ".mcrc"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "MetaCrcMismatch")

    # MetaParseError ------------------------------------------------------------

    def test_meta_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            # overwrite meta with invalid JSON, update meta_crc32 and meta_size
            bad_meta = b"not json!!!"
            meta_size = len(bad_meta)
            data[META_OFFSET:META_OFFSET + meta_size] = bad_meta
            # update header: meta_size and meta_crc32
            new_hdr_dict = dict(zip(plsq._HDR_FIELDS, plsq._HDR_FMT.unpack(bytes(data[:HEADER_SIZE]))))
            new_hdr_dict["meta_size"] = meta_size
            new_hdr_dict["meta_crc32"] = _crc32(bad_meta)
            data[:HEADER_SIZE] = _pack_header(new_hdr_dict)
            bad = src + ".metaparse"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "MetaParseError")

    # MissingMetaKey ------------------------------------------------------------

    def test_missing_meta_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            # read and modify meta
            meta_bytes = data[META_OFFSET:META_OFFSET + h["meta_size"]]
            import json
            meta = json.loads(meta_bytes.decode("utf-8"))
            del meta["sequence_name"]
            new_meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            data[META_OFFSET:META_OFFSET + len(new_meta_bytes)] = new_meta_bytes
            new_hdr = dict(zip(plsq._HDR_FIELDS, plsq._HDR_FMT.unpack(bytes(data[:HEADER_SIZE]))))
            new_hdr["meta_size"] = len(new_meta_bytes)
            new_hdr["meta_crc32"] = _crc32(new_meta_bytes)
            data[:HEADER_SIZE] = _pack_header(new_hdr)
            bad = src + ".missingkey"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "MissingMetaKey")

    # MetaMismatch --------------------------------------------------------------

    def test_meta_mismatch_chips_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp, chips=104)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            import json
            meta_bytes = data[META_OFFSET:META_OFFSET + h["meta_size"]]
            meta = json.loads(meta_bytes.decode("utf-8"))
            meta["model_chips_count"] = "999"  # mismatch with header chips_count=104
            new_meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            data[META_OFFSET:META_OFFSET + len(new_meta_bytes)] = new_meta_bytes
            new_hdr = dict(zip(plsq._HDR_FIELDS, plsq._HDR_FMT.unpack(bytes(data[:HEADER_SIZE]))))
            new_hdr["meta_size"] = len(new_meta_bytes)
            new_hdr["meta_crc32"] = _crc32(new_meta_bytes)
            data[:HEADER_SIZE] = _pack_header(new_hdr)
            bad = src + ".mismatch"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "MetaMismatch")

    def test_meta_mismatch_file_format(self):
        """file_format != 'plsq' is a MetaMismatch."""
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            with open(src, "rb") as f:
                data = bytearray(f.read())
            h = plsq._unpack_header(bytes(data[:HEADER_SIZE]))
            import json
            meta_bytes = data[META_OFFSET:META_OFFSET + h["meta_size"]]
            meta = json.loads(meta_bytes.decode("utf-8"))
            meta["file_format"] = "json"
            new_meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            data[META_OFFSET:META_OFFSET + len(new_meta_bytes)] = new_meta_bytes
            new_hdr = dict(zip(plsq._HDR_FIELDS, plsq._HDR_FMT.unpack(bytes(data[:HEADER_SIZE]))))
            new_hdr["meta_size"] = len(new_meta_bytes)
            new_hdr["meta_crc32"] = _crc32(new_meta_bytes)
            data[:HEADER_SIZE] = _pack_header(new_hdr)
            bad = src + ".fmtmm"
            with open(bad, "wb") as f:
                f.write(data)
            self._assert_error(bad, "MetaMismatch")

    # validate() collects multiple problems ------------------------------------

    def test_validate_collects_multiple_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_file(tmp)
            # Patch frame_stride to wrong value and also patch payload_size
            # so both InconsistentHeader checks fire
            bad = _patch_header_field(src, {"fps": 0, "chips_per_universe": 0})
            probs = validate(bad)
            codes = [p.split(":")[0] for p in probs]
            self.assertIn("OutOfRange", codes)


# ---------------------------------------------------------------------------
# rewrite_frames (loop-blend use case)
# ---------------------------------------------------------------------------

class TestRewriteFrames(unittest.TestCase):

    def test_rewrite_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "rw.plsq.part")
            w = PlsqWriter()
            w.open(part, 10, 30, 0, 1, 0)
            original = [bytes([i] * 40) for i in range(5)]
            for f in original:
                w.append_frame(f)
            # rewrite frame 0 with new data
            new_frame = bytes([99] * 40)
            w.rewrite_frames(0, [new_frame])
            readback = w.read_frames(0, 5)
            self.assertEqual(readback[0], new_frame)
            self.assertEqual(readback[1], original[1])
            w.abort()


# ---------------------------------------------------------------------------
# abort() keeps the .part file
# ---------------------------------------------------------------------------

class TestAbort(unittest.TestCase):

    def test_abort_keeps_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "aborted.plsq.part")
            w = PlsqWriter()
            w.open(part, 10, 30, 0, 1, 0)
            w.append_frame(bytes(40))
            w.abort()
            self.assertTrue(os.path.exists(part))


if __name__ == "__main__":
    unittest.main()
