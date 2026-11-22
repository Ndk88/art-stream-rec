"""
Tests for task X1: PLSQ error vectors and frame-stamp files
(tests/golden/errors/, tests/golden/stamp/, tools/make_test_vectors.py).

Run:  python -m unittest tests.test_test_vectors
"""

import contextlib
import io
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import plsq  # noqa: E402
from plsq import PlsqError  # noqa: E402

import make_test_vectors as mtv  # noqa: E402

ERRORS_DIR = os.path.join(ROOT, "tests", "golden", "errors")
STAMP_DIR = os.path.join(ROOT, "tests", "golden", "stamp")
NOT_DATA = {"expected.json", "README.md"}

# Real interrupted recording used for err_not_finalized.plsq.part (read only).
# Override with the environment variable PLSQ_NOT_FINALIZED_SOURCE.
NOT_FINALIZED_SOURCE = os.environ.get(
    "PLSQ_NOT_FINALIZED_SOURCE",
    os.path.join(
        os.path.dirname(ROOT), "art-stream-uep", "test_data", "M1_9.plsq.part"
    ),
)


def _quiet(*_a, **_k):
    pass


def _load_expected():
    with open(os.path.join(ERRORS_DIR, "expected.json"), encoding="utf-8") as fp:
        return json.load(fp)


def _read(path):
    with open(path, "rb") as fp:
        return fp.read()


def expected_frame(chips, f):
    """Reference function, copied from tests/golden/stamp/README.md."""
    b = bytearray(((3 * c + k + 7 * f) % 256)
                  for c in range(chips) for k in range(4))
    stamp = struct.pack("<I", f)
    b[0:4] = stamp
    b[-4:] = stamp
    return bytes(b)


# ---------------------------------------------------------------------------
# Error vectors
# ---------------------------------------------------------------------------

class TestErrorVectors(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.expected = _load_expected()
        cls.files = cls.expected["files"]

    def test_expected_lists_exactly_the_files(self):
        present = set(os.listdir(ERRORS_DIR)) - NOT_DATA
        self.assertEqual(present, set(self.files))

    def test_all_vectors_of_the_task_present(self):
        codes = {e["code"] for e in self.files.values() if e["result"] == "error"}
        want = {
            "Truncated", "BadMagic", "UnsupportedVersion", "BadHeaderSize",
            "HeaderCrcMismatch", "UnsupportedFlags", "NotFinalized",
            "UnsupportedLayout", "OutOfRange", "InconsistentHeader",
            "MetaOutOfBounds", "MetaCrcMismatch", "MetaParseError",
            "MissingMetaKey", "MetaMismatch",
        }
        self.assertEqual(codes, want)
        self.assertEqual(len(self.files), 20)

    def test_expected_json_format(self):
        e = self.expected
        self.assertEqual(
            list(e), ["files", "format_version", "generated_by",
                      "recorder_version", "spec_commit"],
        )
        self.assertEqual(e["format_version"], "1")
        self.assertEqual(e["generated_by"], "tools/make_test_vectors.py")
        self.assertEqual(e["recorder_version"], "1.2.2")
        self.assertRegex(e["spec_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(list(self.files), sorted(self.files))
        for name, entry in self.files.items():
            self.assertEqual(list(entry), ["code", "note", "result"], name)
            for v in entry.values():
                self.assertIsInstance(v, str, name)
            self.assertIn(entry["result"], ("error", "warning", "ok"), name)
            self.assertEqual(entry["code"] == "", entry["result"] == "ok", name)

    def test_read_info_result(self):
        for name, entry in self.files.items():
            with self.subTest(name=name):
                path = os.path.join(ERRORS_DIR, name)
                if entry["result"] == "error":
                    with self.assertRaises(PlsqError) as cm:
                        plsq.read_info(path)
                    self.assertEqual(cm.exception.code, entry["code"])
                else:
                    plsq.read_info(path)

    def test_validate_first_problem(self):
        for name, entry in self.files.items():
            with self.subTest(name=name):
                problems = plsq.validate(os.path.join(ERRORS_DIR, name))
                if entry["result"] == "ok":
                    self.assertEqual(problems, [])
                else:
                    self.assertTrue(problems)
                    self.assertEqual(problems[0].split(":")[0], entry["code"])
                if entry["result"] == "warning":
                    self.assertEqual(len(problems), 1)

    def test_io_error_without_file(self):
        missing = os.path.join(ERRORS_DIR, "does_not_exist.plsq")
        with self.assertRaises(PlsqError) as cm:
            plsq.read_info(missing)
        self.assertEqual(cm.exception.code, "IoError")
        self.assertTrue(plsq.validate(missing)[0].startswith("IoError"))

    def test_meta_of_ok_files(self):
        _, m = plsq.read_info(os.path.join(ERRORS_DIR, "ok_unknown_key.plsq"))
        self.assertEqual(m["x_test_unknown"], "kept")
        _, m = plsq.read_info(os.path.join(ERRORS_DIR, "ok_optional_keys.plsq"))
        self.assertEqual(m["short_packets"], "3")
        self.assertEqual(m["first_frame_partial"], "1")
        _, m = plsq.read_info(os.path.join(ERRORS_DIR, "ok_no_optional_keys.plsq"))
        self.assertNotIn("short_packets", m)
        self.assertNotIn("first_frame_partial", m)
        self.assertEqual(len(m), len(plsq.META_KEYS))

    def test_not_finalized_shape(self):
        path = os.path.join(ERRORS_DIR, mtv.NOT_FINALIZED_NAME)
        h = plsq._unpack_header(_read(path)[:plsq.HEADER_SIZE])
        self.assertEqual(h["flags"] & plsq.FLAG_FINALIZED, 0)
        self.assertEqual(h["chips_count"], 64000)
        self.assertEqual(
            os.path.getsize(path), h["payload_offset"] + 2 * h["frame_stride"]
        )
        self.assertEqual(os.path.getsize(path), 581632)

    def test_regenerate_identical(self):
        tmp = tempfile.mkdtemp()
        try:
            exp = mtv.generate_errors(
                tmp, None, spec_commit=self.expected["spec_commit"], log=_quiet
            )
            names = set(os.listdir(tmp)) - NOT_DATA
            self.assertEqual(names, set(self.files) - {mtv.NOT_FINALIZED_NAME})
            for name in sorted(names):
                with self.subTest(name=name):
                    self.assertEqual(
                        _read(os.path.join(tmp, name)),
                        _read(os.path.join(ERRORS_DIR, name)),
                    )
            want = dict(self.expected)
            want["files"] = {k: v for k, v in self.files.items()
                             if k != mtv.NOT_FINALIZED_NAME}
            self.assertEqual(exp, want)
        finally:
            shutil.rmtree(tmp)

    def test_regenerate_not_finalized_identical(self):
        if not os.path.isfile(NOT_FINALIZED_SOURCE):
            self.skipTest(
                "NotFinalized source not available (%s); "
                "err_not_finalized.plsq.part not regenerated" % NOT_FINALIZED_SOURCE
            )
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, mtv.NOT_FINALIZED_NAME)
            note = mtv.make_not_finalized(NOT_FINALIZED_SOURCE, out, log=_quiet)
            self.assertEqual(_read(out),
                             _read(os.path.join(ERRORS_DIR, mtv.NOT_FINALIZED_NAME)))
            self.assertEqual(note, self.files[mtv.NOT_FINALIZED_NAME]["note"])
        finally:
            shutil.rmtree(tmp)

    def test_skip_message_without_source(self):
        tmp = tempfile.mkdtemp()
        lines = []
        try:
            mtv.generate_errors(tmp, None, spec_commit="0" * 40, log=lines.append)
        finally:
            shutil.rmtree(tmp)
        self.assertTrue(any("SKIPPED" in s and mtv.NOT_FINALIZED_NAME in s
                            for s in lines))

    def test_spec_commit_matches_spec_history(self):
        h = mtv.spec_commit_hash()
        if not h:
            self.skipTest("git not available")
        self.assertEqual(self.expected["spec_commit"], h)


# ---------------------------------------------------------------------------
# Frame-stamp files
# ---------------------------------------------------------------------------

class TestStampFiles(unittest.TestCase):

    def test_files_present_and_valid(self):
        present = set(os.listdir(STAMP_DIR)) - NOT_DATA
        self.assertEqual(present, {n for n, *_ in mtv.STAMP_SMALL})
        for name, chips, fps, frames in mtv.STAMP_SMALL:
            with self.subTest(name=name):
                path = os.path.join(STAMP_DIR, name)
                self.assertEqual(plsq.validate(path), [])
                h, m = plsq.read_info(path)
                self.assertEqual((h["chips_count"], h["fps"], h["frame_count"]),
                                 (chips, fps, frames))
                self.assertEqual(h["start_universe"], 1)
                self.assertEqual(h["start_channel"], 1)
                self.assertEqual(m["sequence_name"], "frame-stamp v1")
                self.assertEqual(os.path.getsize(path),
                                 69632 + frames * chips * 4)
        h, _ = plsq.read_info(os.path.join(STAMP_DIR, "stamp_130c_30fps_300f.plsq"))
        self.assertEqual(h["universes_count"], 2)

    def test_frames_match_reference_function(self):
        for name, chips, _fps, frames in mtv.STAMP_SMALL:
            got = plsq.read_frames(os.path.join(STAMP_DIR, name), 0, frames)
            for f in range(frames):
                self.assertEqual(got[f], expected_frame(chips, f), (name, f))

    def test_stamp_frame_large_index(self):
        # Stamp uses all 4 bytes; translate tables wrap at 256.
        for f in (255, 256, 70000, 3599):
            self.assertEqual(mtv.stamp_frame(mtv.stamp_base(5), f),
                             expected_frame(5, f))

    def test_verify_ok(self):
        for name, _c, _fps, frames in mtv.STAMP_SMALL:
            ok, msg = mtv.verify_stamp(os.path.join(STAMP_DIR, name))
            self.assertTrue(ok, msg)
            self.assertIn("%d of %d" % (frames, frames), msg)

    def test_verify_range_and_sample(self):
        path = os.path.join(STAMP_DIR, "stamp_130c_30fps_300f.plsq")
        ok, msg = mtv.verify_stamp(path, frames="100:150")
        self.assertTrue(ok)
        self.assertIn("50 of 300", msg)
        ok, msg = mtv.verify_stamp(path, sample=20, seed=1)
        self.assertTrue(ok)
        self.assertIn("20 of 300", msg)

    def test_regenerate_identical(self):
        tmp = tempfile.mkdtemp()
        try:
            mtv.generate_small_stamps(tmp, log=_quiet)
            for name, *_ in mtv.STAMP_SMALL:
                self.assertEqual(_read(os.path.join(tmp, name)),
                                 _read(os.path.join(STAMP_DIR, name)), name)
        finally:
            shutil.rmtree(tmp)

    def _corrupt_copy(self, edit):
        src = os.path.join(STAMP_DIR, "stamp_130c_30fps_300f.plsq")
        data = bytearray(_read(src))
        edit(data)
        fd, path = tempfile.mkstemp(suffix=".plsq")
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        self.addCleanup(os.remove, path)
        return path

    def test_one_byte_corruption_detected(self):
        stride = 520
        pos = 69632 + 123 * stride + 57 * 4 + 2  # frame 123, chip 57, B

        def edit(d):
            d[pos] ^= 0xFF
        path = self._corrupt_copy(edit)
        self.assertEqual(plsq.validate(path), [])  # payload is not checksummed
        ok, msg = mtv.verify_stamp(path)
        self.assertFalse(ok)
        self.assertIn("frame 123 chip 57 channel 2", msg)

    def test_swapped_frames_detected(self):
        stride = 520
        a, b = 69632 + 10 * stride, 69632 + 11 * stride

        def edit(d):
            d[a:a + stride], d[b:b + stride] = d[b:b + stride], d[a:a + stride]
        path = self._corrupt_copy(edit)
        ok, msg = mtv.verify_stamp(path)
        self.assertFalse(ok)
        self.assertIn("frame 10 chip 0 channel 0", msg)

    def test_glued_frame_detected(self):
        # First half of frame 20 + second half of frame 21: ends disagree.
        stride = 520
        a = 69632 + 20 * stride
        b = 69632 + 21 * stride
        half = stride // 2

        def edit(d):
            d[a + half:a + stride] = d[b + half:b + stride]
        path = self._corrupt_copy(edit)
        ok, msg = mtv.verify_stamp(path)
        self.assertFalse(ok)
        self.assertIn("frame 20 chip 65", msg)

    def test_cli_exit_codes(self):
        good = os.path.join(STAMP_DIR, "stamp_1c_60fps_10f.plsq")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mtv.main(["--verify-stamp", good]), 0)
        self.assertTrue(out.getvalue().startswith("OK"))

        def edit(d):
            d[69632 + 7 * 520] ^= 1
        bad = self._corrupt_copy(edit)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mtv.main(["--verify-stamp", bad]), 1)
        self.assertTrue(re.search(r"MISMATCH frame 7 chip 0 channel 0",
                                  out.getvalue()))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                mtv.main(["--verify-stamp",
                          os.path.join(ERRORS_DIR, "err_bad_magic.plsq")]), 2)

    def test_cli_generate_custom_stamp(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "s.plsq")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mtv.main(["--stamp", "--chips", "300", "--fps", "60",
                               "--seconds", "0.5", "--out", path])
            self.assertEqual(rc, 0)
            self.assertIn("MB/s", out.getvalue())
            self.assertEqual(plsq.validate(path), [])
            h, _ = plsq.read_info(path)
            self.assertEqual((h["frame_count"], h["universes_count"]), (30, 3))
            self.assertTrue(mtv.verify_stamp(path)[0])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
