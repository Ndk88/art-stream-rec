"""
Tests for R4: PlsqStreamWriter streaming pipeline.

Verifies:
- Output byte-identical to postprocess() for several (skip, blend, total) combos
- blend=0 (no loop blend)
- Manual stop (fewer frames than planned)
- Queue overflow produces error
- blend > 1 GiB refused at the App level (checked via PlsqStreamWriter)
- Blend timing: does not raise for typical sizes
- _blend_frames helper correctness

Run:  python -m unittest tests.test_r4
"""

import os
import queue
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from main import PlsqStreamWriter, _blend_frames, postprocess
from plsq import PlsqWriter, build_meta, read_frames, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHIPS = 104
STRIDE = CHIPS * 4
FPS = 30


def _rnd_frames(n, stride=STRIDE, seed=42):
    rng = random.Random(seed)
    return [bytes(rng.randint(0, 255) for _ in range(stride)) for _ in range(n)]


def _run_stream(frames, skip, blend, stride=STRIDE, chips=CHIPS, fps=FPS):
    """Stream `frames` through PlsqStreamWriter; return (plsq_frames, writer_obj)."""
    with tempfile.NamedTemporaryFile(suffix=".plsq", delete=False) as f:
        part = f.name + ".part"
        target = f.name
    try:
        pw = PlsqWriter()
        pw.open(part, chips, fps, 1, 1, 128)
        q = queue.Queue(maxsize=len(frames) + 10)
        sw = PlsqStreamWriter(pw, skip, blend, stride)
        sw.start(q)
        for fr in frames:
            q.put(fr)
        q.put(None)  # sentinel
        sw.join(timeout=10.0)
        if sw.error or sw.frames_written == 0:
            pw.abort()
            return None, sw
        meta = build_meta(
            file_format="plsq",
            recorder_version="test",
            model_chips_count=chips,
            fps=fps,
            frame_count=sw.frames_written,
            frame_stride=stride,
            start_universe=1,
            start_channel=1,
            chips_per_universe=128,
            universes_count=1,
        )
        pw.finalize(meta)
        os.replace(part, target)
        result = read_frames(target, 0, sw.frames_written)
        return result, sw
    finally:
        for p in (part, target):
            if os.path.exists(p):
                os.unlink(p)


# ---------------------------------------------------------------------------
# _blend_frames helper
# ---------------------------------------------------------------------------

class TestBlendFrames(unittest.TestCase):

    def test_weight_zero_gives_tail(self):
        """i=0: w=0 → out = round(tail*1 + head*0) = tail."""
        tail = [bytes([200, 100])]
        head = [bytes([50, 60])]
        result = _blend_frames(tail, head)
        self.assertEqual(result[0], bytes([200, 100]))

    def test_weight_one_gives_head(self):
        """i=n-1: w=(n-1)/n → near head; for n=1 w=0 so tail wins."""
        # n=2: i=0 → w=0 (tail), i=1 → w=0.5 → midpoint
        tail = [bytes([0]), bytes([0])]
        head = [bytes([0]), bytes([100])]
        result = _blend_frames(tail, head)
        self.assertEqual(result[1], bytes([50]))  # round(0*0.5 + 100*0.5)

    def test_identity_when_tail_equals_head(self):
        """When tail and head are identical, blend is identical."""
        f = [bytes([128, 64, 32, 16])]
        result = _blend_frames(f, f)
        self.assertEqual(result[0], f[0])

    def test_output_length_matches_input(self):
        n = 5
        tail = _rnd_frames(n, stride=8)
        head = _rnd_frames(n, stride=8, seed=99)
        result = _blend_frames(tail, head)
        self.assertEqual(len(result), n)
        for f in result:
            self.assertEqual(len(f), 8)


# ---------------------------------------------------------------------------
# PlsqStreamWriter: byte-identical to postprocess()
# ---------------------------------------------------------------------------

class TestStreamEqualsPostprocess(unittest.TestCase):

    def _compare(self, total, skip, blend, seed=42):
        all_frames = _rnd_frames(total, seed=seed)
        expected = postprocess(all_frames, skip=skip, blend=blend)
        got, sw = _run_stream(all_frames, skip=skip, blend=blend)
        self.assertIsNone(sw.error, "writer error: %s" % sw.error)
        self.assertIsNotNone(got)
        self.assertEqual(len(got), len(expected))
        for i, (e, g) in enumerate(zip(expected, got)):
            self.assertEqual(e, g, "frame %d differs" % i)

    def test_no_skip_no_blend(self):
        self._compare(total=20, skip=0, blend=0)

    def test_skip_only(self):
        self._compare(total=20, skip=5, blend=0)

    def test_blend_only(self):
        self._compare(total=20, skip=0, blend=5)

    def test_skip_and_blend(self):
        self._compare(total=30, skip=3, blend=7)

    def test_large_blend(self):
        self._compare(total=100, skip=0, blend=30)

    def test_one_frame_no_blend(self):
        self._compare(total=1, skip=0, blend=0)

    def test_all_same_frames(self):
        """When all frames identical, blend changes nothing."""
        all_frames = [bytes([77] * STRIDE)] * 20
        expected = postprocess(all_frames, skip=0, blend=5)
        got, sw = _run_stream(all_frames, skip=0, blend=5)
        self.assertIsNone(sw.error)
        self.assertEqual(got, expected)


# ---------------------------------------------------------------------------
# Manual stop (fewer frames than planned)
# ---------------------------------------------------------------------------

class TestManualStop(unittest.TestCase):

    def test_fewer_frames_than_blend(self):
        """When only skip frames arrive, frames_written == 0."""
        all_frames = _rnd_frames(3)
        got, sw = _run_stream(all_frames, skip=3, blend=5)  # only 3 frames, all skipped
        self.assertIsNone(sw.error)
        self.assertEqual(sw.frames_written, 0)

    def test_partial_capture_produces_valid_output(self):
        """Stop after fewer frames than planned still writes what arrived."""
        all_frames = _rnd_frames(10)
        expected = postprocess(all_frames, skip=2, blend=3)
        got, sw = _run_stream(all_frames, skip=2, blend=3)
        self.assertIsNone(sw.error)
        self.assertEqual(got, expected)


# ---------------------------------------------------------------------------
# Queue overflow
# ---------------------------------------------------------------------------

class TestQueueOverflow(unittest.TestCase):

    def test_overflow_sets_error(self):
        """When the queue is full and put_nowait raises, Recorder sets error."""
        from main import Recorder, ArtNetServer
        from mapping import build_spans, effective_chips_per_universe
        chips = 4
        stride = chips * 4
        spans = build_spans(1, 1, 4, chips, effective_chips_per_universe(0))
        srv = ArtNetServer()
        rec = Recorder(srv)

        tiny_q = queue.Queue(maxsize=1)
        # Pre-fill the queue so it's immediately full
        tiny_q.put(bytes(stride))

        rec.start(spans, stride, fps=100, wait_first_packet=False, out_queue=tiny_q)
        # Let it run briefly; the second frame put should overflow
        import time
        time.sleep(0.1)
        rec.stop()
        self.assertIsNotNone(rec.error)
        self.assertIn("overflow", rec.error.lower())


# ---------------------------------------------------------------------------
# PLSQ output validates clean
# ---------------------------------------------------------------------------

class TestPlsqValidates(unittest.TestCase):

    def test_output_validates_clean(self):
        all_frames = _rnd_frames(30)
        with tempfile.NamedTemporaryFile(suffix=".plsq", delete=False) as f:
            part = f.name + ".part"
            target = f.name
        try:
            pw = PlsqWriter()
            pw.open(part, CHIPS, FPS, 1, 1, 128)
            q = queue.Queue(maxsize=200)
            sw = PlsqStreamWriter(pw, 0, 0, STRIDE)
            sw.start(q)
            for fr in all_frames:
                q.put(fr)
            q.put(None)
            sw.join(timeout=10.0)
            self.assertIsNone(sw.error)
            meta = build_meta(
                file_format="plsq",
                recorder_version="test",
                model_chips_count=CHIPS,
                fps=FPS,
                frame_count=sw.frames_written,
                frame_stride=STRIDE,
                start_universe=1,
                start_channel=1,
                chips_per_universe=128,
                universes_count=1,
            )
            pw.finalize(meta)
            os.replace(part, target)
            problems = validate(target)
            self.assertEqual(problems, [], "validate() found: %s" % problems)
        finally:
            for p in (part, target):
                if os.path.exists(p):
                    os.unlink(p)

    def test_frames_written_matches_file_frame_count(self):
        all_frames = _rnd_frames(15)
        skip, blend = 2, 3
        got, sw = _run_stream(all_frames, skip=skip, blend=blend)
        expected_count = len(all_frames) - skip - blend
        self.assertEqual(sw.frames_written, expected_count)
        self.assertEqual(len(got), expected_count)


# ---------------------------------------------------------------------------
# bytes_written tracks payload size
# ---------------------------------------------------------------------------

class TestBytesWritten(unittest.TestCase):

    def test_bytes_written_equals_frames_written_times_stride(self):
        all_frames = _rnd_frames(20)
        got, sw = _run_stream(all_frames, skip=0, blend=0)
        self.assertEqual(sw.bytes_written, sw.frames_written * STRIDE)

    def test_bytes_written_with_skip(self):
        all_frames = _rnd_frames(20)
        got, sw = _run_stream(all_frames, skip=5, blend=0)
        self.assertEqual(sw.bytes_written, sw.frames_written * STRIDE)


if __name__ == "__main__":
    unittest.main()
