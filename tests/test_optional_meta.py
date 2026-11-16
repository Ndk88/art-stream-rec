"""
Optional meta keys `short_packets` and `first_frame_partial` (spec section 5):
readers must not require them, writers write them as strings.

Run:  python -m unittest tests.test_optional_meta
"""

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mapping import build_spans
from plsq import META_KEYS, PlsqWriter, build_meta, read_frames, read_info, validate
from session import ArtNetServer, PlsqRecordingSession, write_json

GOLDEN = os.path.join(ROOT, "tests", "golden", "conformance_v1.plsq")
OPTIONAL = ("short_packets", "first_frame_partial")


def _meta(chips=4, frames=3, **opt):
    return build_meta(
        file_format="plsq", fps=30, frame_count=frames, frame_stride=chips * 4,
        start_universe=1, start_channel=1, chips_per_universe=128,
        universes_count=1, model_chips_count=chips, **opt,
    )


class TestReaderWithoutOptionalKeys(unittest.TestCase):
    def test_golden_file_has_no_optional_keys_and_reads_clean(self):
        _h, meta = read_info(GOLDEN)
        for k in OPTIONAL:
            self.assertNotIn(k, meta)
        self.assertEqual(validate(GOLDEN), [])
        self.assertEqual(len(read_frames(GOLDEN, 0, 10)), 10)

    def test_optional_keys_not_in_required_list(self):
        for k in OPTIONAL:
            self.assertNotIn(k, META_KEYS)


class TestBuildMeta(unittest.TestCase):
    def test_without_values_exactly_required_keys(self):
        m = _meta()
        self.assertEqual(list(m), META_KEYS)

    def test_with_values_appended_as_strings(self):
        m = _meta(short_packets=12, first_frame_partial=True)
        self.assertEqual(list(m)[:len(META_KEYS)], META_KEYS)
        self.assertEqual(list(m)[len(META_KEYS):], list(OPTIONAL))
        self.assertEqual(m["short_packets"], "12")
        self.assertEqual(m["first_frame_partial"], "1")
        self.assertEqual(_meta(first_frame_partial=False)["first_frame_partial"], "0")
        self.assertNotIn("short_packets", _meta(first_frame_partial=False))


class TestWriters(unittest.TestCase):
    def test_plsq_writer_writes_and_reader_returns_them(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "opt.plsq")
            w = PlsqWriter()
            w.open(path, 4, 30, 1, 1, 128)
            for _ in range(3):
                w.append_frame(bytes(16))
            w.finalize(_meta(short_packets=7, first_frame_partial=False))
            self.assertEqual(validate(path), [])
            _h, meta = read_info(path)
        self.assertEqual(meta["short_packets"], "7")
        self.assertEqual(meta["first_frame_partial"], "0")

    def test_json_writer_writes_them(self):
        m = _meta(short_packets=3, first_frame_partial=True)
        m["file_format"] = "json"
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "opt.json")
            write_json(path, m, [bytes(16)] * 3)
            with open(path, encoding="utf-8") as fp:
                doc = json.load(fp)
        self.assertEqual(doc["short_packets"], "3")
        self.assertEqual(doc["first_frame_partial"], "1")
        self.assertEqual(list(doc)[-1], "sequence")


class TestSessionWritesOptionalKeys(unittest.TestCase):
    def _record(self, complete_frames):
        server = ArtNetServer()           # not started; frames are queued by hand
        chips = 4
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "s.plsq")
            sess = PlsqRecordingSession(
                server, chips=chips, fps=30, out_path=out,
                spans=build_spans(1, 1, 4, chips, 128), stride=chips * 4,
                start_universe=1, start_channel=1, eff_cpu=128,
                final_frames=3, wait_first_packet=False,
                complete_frames=complete_frames,
            )
            sess.start()
            self.assertEqual(sess.state, "recording", sess.error)
            for _ in range(3):
                sess._queue.put(bytes([9] * chips * 4))
            sess.request_stop()
            deadline = time.perf_counter() + 10.0
            while sess.poll() not in ("done", "error"):
                self.assertLess(time.perf_counter(), deadline)
                time.sleep(0.02)
            self.assertEqual(sess.state, "done", sess.error)
            self.assertEqual(validate(out), [])
            return read_info(out)[1]

    def test_assembler_on_writes_both(self):
        meta = self._record(complete_frames=True)
        self.assertEqual(meta["short_packets"], "0")
        self.assertEqual(meta["first_frame_partial"], "0")

    def test_assembler_off_omits_short_packets(self):
        meta = self._record(complete_frames=False)
        self.assertNotIn("short_packets", meta)
        self.assertEqual(meta["first_frame_partial"], "0")


if __name__ == "__main__":
    unittest.main()
