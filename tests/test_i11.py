"""
Tests for I-11: D11 blend reduction in PLSQ and JSON paths.

Run:  python -m unittest tests.test_i11
"""

import os
import queue
import random
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from main import PlsqStreamWriter, postprocess, _blend_frames
from plsq import PlsqWriter, build_meta, read_frames, validate
from mapping import build_spans, effective_chips_per_universe


CHIPS = 104
STRIDE = CHIPS * 4
FPS = 30


def _rnd_frames(n, stride=STRIDE, seed=11):
    rng = random.Random(seed)
    return [bytes(rng.randint(0, 255) for _ in range(stride)) for _ in range(n)]


def _run_stream(frames, skip, blend, stride=STRIDE, chips=CHIPS, fps=FPS):
    """Stream through PlsqStreamWriter; return (plsq_frames_list_or_None, sw)."""
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
        q.put(None)
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


def _spans_for(chips, start_universe=1):
    eff_cpu = effective_chips_per_universe(0)
    return build_spans(start_universe, 1, 4, chips, eff_cpu)


# ---------------------------------------------------------------------------
# PLSQ streaming D11
# ---------------------------------------------------------------------------

class TestD11BlendReductionPlsq(unittest.TestCase):
    """I-11: PlsqStreamWriter applies reduced blend when final < blend."""

    def _compare_clamped(self, total, skip, blend, seed=11):
        """Verify PLSQ output equals postprocess with blend_applied = min(blend, final)."""
        all_frames = _rnd_frames(total, seed=seed)
        avail = total - skip
        final = avail - blend
        self.assertGreater(final, 0, "Need final > 0 for D11 test")
        blend_applied = min(blend, final)
        expected = postprocess(all_frames, skip=skip, blend=blend_applied, final=final)
        got, sw = _run_stream(all_frames, skip=skip, blend=blend)
        self.assertIsNone(sw.error, "writer error: %s" % sw.error)
        self.assertIsNotNone(got, "frames_written=0, expected %d frames" % len(expected))
        self.assertEqual(len(got), len(expected),
                         "frame count: got %d, expected %d" % (len(got), len(expected)))
        for i, (e, g) in enumerate(zip(expected, got)):
            self.assertEqual(e, g, "frame %d differs" % i)

    def test_final_less_than_blend_byte_identical(self):
        """15 frames blend=10 → final=5, blend_applied=5; output equals postprocess."""
        self._compare_clamped(total=15, skip=0, blend=10)

    def test_final_one_blend_ten(self):
        """11 frames blend=10 → final=1, blend_applied=1."""
        self._compare_clamped(total=11, skip=0, blend=10)

    def test_full_blend_unchanged(self):
        """20 frames blend=5 → final=15 >= blend → no D11, standard blend."""
        self._compare_clamped(total=20, skip=0, blend=5)

    def test_final_zero_gives_zero_frames_written(self):
        """10 frames blend=10 → final=0 → frames_written=0 (error path, no file)."""
        all_frames = _rnd_frames(10)
        got, sw = _run_stream(all_frames, skip=0, blend=10)
        self.assertIsNone(sw.error)
        self.assertEqual(sw.frames_written, 0)

    def test_frames_written_matches_final(self):
        """frames_written reflects exported frames only (not delay-line frames)."""
        all_frames = _rnd_frames(15)
        got, sw = _run_stream(all_frames, skip=0, blend=10)
        self.assertEqual(sw.frames_written, 5)


# ---------------------------------------------------------------------------
# JSON path D11 (via postprocess directly)
# ---------------------------------------------------------------------------

class TestD11BlendReductionJson(unittest.TestCase):
    """I-11: JSON path applies D11 clamping before postprocess."""

    def test_postprocess_with_clamped_blend_succeeds(self):
        """postprocess(blend=blend_applied) works when final < original blend."""
        all_frames = _rnd_frames(15)
        final = 5
        blend_applied = 5
        frames = postprocess(all_frames, skip=0, blend=blend_applied, final=final)
        self.assertEqual(len(frames), final)

    def test_postprocess_unclamped_raises(self):
        """postprocess raises if original blend > final (D11 not applied)."""
        all_frames = _rnd_frames(15)
        with self.assertRaises(ValueError):
            postprocess(all_frames, skip=0, blend=10, final=5)

    def test_postprocess_final_zero_raises(self):
        """final <= 0 is an error regardless of blend."""
        all_frames = _rnd_frames(10)
        with self.assertRaises(ValueError):
            postprocess(all_frames, skip=0, blend=10, final=0)

    def test_weight_zero_gives_pure_tail(self):
        """Frame 0 of blend: w=0 → output = tail (fully tail-dominant)."""
        all_frames = _rnd_frames(15)
        final = 5
        blend_applied = 5
        result = postprocess(all_frames, skip=0, blend=blend_applied, final=final)
        tail0 = all_frames[final]   # frame at index 5
        head0 = all_frames[0]       # frame at index 0
        expected_0 = bytes(int(round(tail0[j] * 1.0 + head0[j] * 0.0)) for j in range(len(head0)))
        self.assertEqual(result[0], expected_0)

    def test_plsq_json_byte_identical_with_clamped_blend(self):
        """PLSQ stream and postprocess produce identical output for final < blend."""
        all_frames = _rnd_frames(15)
        final = 5
        blend_applied = 5
        json_frames = postprocess(all_frames, skip=0, blend=blend_applied, final=final)
        plsq_frames, sw = _run_stream(all_frames, skip=0, blend=10)
        self.assertIsNone(sw.error)
        self.assertEqual(len(plsq_frames), len(json_frames))
        for i, (j_, p_) in enumerate(zip(json_frames, plsq_frames)):
            self.assertEqual(j_, p_, "frame %d: JSON vs PLSQ differ" % i)


# ---------------------------------------------------------------------------
# PlsqRecordingSession blend_warning
# ---------------------------------------------------------------------------

def _make_mock_server():
    """Return a minimal ArtNetServer instance with no socket."""
    from session import ArtNetServer
    server = ArtNetServer.__new__(ArtNetServer)
    server.lock = threading.Lock()
    server.latest = {}
    server.uni_stats = {}
    server.total_packets = 0
    server.ignored_packets = 0
    server.total_bytes = 0
    server.last_packet_time = 0.0
    server.lost_packets = 0
    server.rcvbuf_actual = 0
    server._assembler = None
    server.running = False
    server._seq_last = {}
    return server


def _poll_session(sess, timeout=10.0):
    """Poll session to completion (done or error)."""
    deadline = time.perf_counter() + timeout
    while sess.state not in ("done", "error"):
        sess.poll()
        time.sleep(0.05)
        if time.perf_counter() > deadline:
            break
    return sess


class TestD11BlendWarnPlsqSession(unittest.TestCase):
    """I-11: PlsqRecordingSession.result() contains blend_warning when blend was reduced.

    Uses final_frames to let the recorder run naturally (at 30fps from a mock server
    that returns all-zero frames) and stop after the expected number of captures.
    """

    CHIPS = 4

    def _make_session(self, out_path, blend, final_frames):
        from session import PlsqRecordingSession
        eff_cpu = effective_chips_per_universe(0)
        chips = self.CHIPS
        spans = _spans_for(chips)
        stride = chips * 4
        return PlsqRecordingSession(
            _make_mock_server(),
            chips=chips, fps=FPS, out_path=out_path,
            spans=spans, stride=stride,
            start_universe=1, start_channel=1, eff_cpu=eff_cpu,
            skip=0, blend=blend, final_frames=final_frames,
            wait_first_packet=False, complete_frames=False,
        )

    def test_blend_warning_set_when_reduced(self):
        """final_frames=5, blend=10 → max_frames=15; recorder captures 15 → 5 exported → warn."""
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "test.plsq")
            # max_frames = skip(0) + final_frames(5) + blend(10) = 15
            sess = self._make_session(out, blend=10, final_frames=5)
            sess.start()
            self.assertNotEqual(sess.state, "error", sess.error)
            _poll_session(sess)
            self.assertEqual(sess.state, "done", sess.error)
            res = sess.result()
            self.assertEqual(res["frames_written"], 5)
            self.assertEqual(res["blend_applied"], 5)
            self.assertIsNotNone(res["blend_warning"])
            self.assertIn("10", res["blend_warning"])
            self.assertIn("5", res["blend_warning"])

    def test_no_warning_when_full_blend_applied(self):
        """final_frames=15, blend=5 → max_frames=20; 15 exported → no warning."""
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "test.plsq")
            sess = self._make_session(out, blend=5, final_frames=15)
            sess.start()
            self.assertNotEqual(sess.state, "error", sess.error)
            _poll_session(sess)
            self.assertEqual(sess.state, "done", sess.error)
            res = sess.result()
            self.assertEqual(res["frames_written"], 15)
            self.assertEqual(res["blend_applied"], 5)
            self.assertIsNone(res["blend_warning"])

    def test_blend_warning_message_format(self):
        """Warning message mentions both original and applied blend values."""
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "test.plsq")
            # final_frames=10, blend=30 → max_frames=40 → 10 exported
            # blend_applied = min(30, 10) = 10 → warning "30 → 10"
            sess = self._make_session(out, blend=30, final_frames=10)
            sess.start()
            self.assertNotEqual(sess.state, "error", sess.error)
            _poll_session(sess)
            self.assertEqual(sess.state, "done", sess.error)
            res = sess.result()
            self.assertIsNotNone(res["blend_warning"])
            self.assertIn("30", res["blend_warning"])
            self.assertIn("10", res["blend_warning"])


if __name__ == "__main__":
    unittest.main()
