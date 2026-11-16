"""
Tests: with the frame assembler and "start on first packet", the recorder
starts on the first COMPLETE assembled frame; if none arrives within the
timeout it starts on a partial one and sets first_frame_partial.

Run:  python -m unittest tests.test_first_full_frame
"""

import os
import queue
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mapping import build_spans
from session import FIRST_FULL_TIMEOUT, FrameAssembler, Recorder

CHIPS = 300            # 3 universes: 128 + 128 + 44 chips
STRIDE = CHIPS * 4


class _MockServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {}


def _deliver(fa, uni, fill):
    span = next(s for s in fa._spans_by_uni.items() if s[0] == uni)
    n_ch = span[1][1]
    buf = bytearray(18 + 512)
    buf[18:18 + n_ch] = bytes([fill]) * n_ch
    fa.on_universe(uni, memoryview(buf), len(buf), 512)


def _wait_done(rec, timeout=3.0):
    deadline = time.perf_counter() + timeout
    while rec.state != "done" and time.perf_counter() < deadline:
        time.sleep(0.01)


class TestFirstFullFrame(unittest.TestCase):
    def setUp(self):
        self.spans = build_spans(1, 1, 4, CHIPS, 128)
        self.assertEqual(len(self.spans), 3)
        self.fa = FrameAssembler()
        self.fa.start(self.spans, STRIDE)
        self.q = queue.Queue(maxsize=50)
        self.rec = Recorder(_MockServer())

    def test_default_timeout_is_2s(self):
        self.assertEqual(FIRST_FULL_TIMEOUT, 2.0)
        self.assertEqual(self.rec.first_full_timeout, 2.0)

    def test_partial_then_full_starts_on_full_frame(self):
        self.rec.start(self.spans, STRIDE, fps=30, max_frames=1,
                       wait_first_packet=True, assembler=self.fa, out_queue=self.q)
        # Only U1, U2, then U1 again: a partial commit with U3 still zero
        _deliver(self.fa, 1, 0x11)
        _deliver(self.fa, 2, 0x22)
        _deliver(self.fa, 1, 0x11)
        self.assertEqual(self.fa.partial_count, 1)
        self.assertIsNotNone(self.fa.committed_frame)
        self.assertIsNone(self.fa.last_full_frame)
        time.sleep(0.2)
        self.assertEqual(self.rec.state, "waiting",
                         "must not start on a partial commit")
        self.assertTrue(self.q.empty())
        # Now the rest of the set -> complete commit
        _deliver(self.fa, 2, 0x22)
        _deliver(self.fa, 3, 0x33)
        _wait_done(self.rec)
        self.assertEqual(self.rec.state, "done")
        frame0 = self.q.get_nowait()
        self.assertNotIn(0, frame0, "zeros of the partial commit leaked into frame 0")
        self.assertEqual(frame0[0], 0x11)
        self.assertEqual(frame0[-1], 0x33)
        self.assertFalse(self.rec.first_frame_partial)
        self.assertEqual(self.rec.missing, 0)

    def test_never_full_times_out_and_records_partial(self):
        self.rec.first_full_timeout = 0.3   # keep the test fast
        self.rec.start(self.spans, STRIDE, fps=30, max_frames=5,
                       wait_first_packet=True, assembler=self.fa, out_queue=self.q)
        stop = threading.Event()

        def source():   # U1 and U2 only, forever: U3 never arrives
            while not stop.is_set():
                _deliver(self.fa, 1, 0x44)
                _deliver(self.fa, 2, 0x55)
                time.sleep(0.01)

        t = threading.Thread(target=source, daemon=True)
        t.start()
        try:
            time.sleep(0.15)
            self.assertEqual(self.rec.state, "waiting", "started before the timeout")
            _wait_done(self.rec)
        finally:
            stop.set()
            t.join(timeout=1.0)
        self.assertEqual(self.rec.state, "done")
        self.assertTrue(self.rec.first_frame_partial)
        self.assertEqual(self.rec.frames_captured, 5, "recording must go on")
        frame0 = self.q.get_nowait()
        u3_off = self.spans[2][3]
        self.assertEqual(frame0[0], 0x44)
        self.assertEqual(frame0[u3_off:], bytes(STRIDE - u3_off),
                         "U3 never arrived, so it is zero in the partial frame")

    def test_flag_reset_on_next_start(self):
        self.rec.first_frame_partial = True
        self.rec.start(self.spans, STRIDE, fps=30, max_frames=1,
                       wait_first_packet=True, assembler=self.fa, out_queue=self.q)
        self.assertFalse(self.rec.first_frame_partial)
        self.rec.stop()

    def test_full_set_is_full_partial_artsync_is_not(self):
        # A complete set commits as soon as the last universe arrives (an
        # ArtSync after it finds nothing new); an ArtSync with only part of
        # the set is a partial commit and must not count as complete.
        for uni, fill in ((1, 1), (2, 2), (3, 3)):
            _deliver(self.fa, uni, fill)
        self.assertIsNotNone(self.fa.last_full_frame)
        fa2 = FrameAssembler()
        fa2.start(self.spans, STRIDE)
        _deliver(fa2, 1, 1)
        _deliver(fa2, 2, 2)
        fa2.on_artsync()                      # partial ArtSync
        self.assertIsNone(fa2.last_full_frame)
        self.assertEqual(fa2.partial_count, 1)


if __name__ == "__main__":
    unittest.main()
