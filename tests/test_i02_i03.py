"""
Tests for I-02 (first-frame race) and I-03 (lost_packets baseline).

Run:  python -m unittest tests.test_i02_i03
"""

import os
import queue
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from main import FrameAssembler, Recorder
from mapping import build_spans, effective_chips_per_universe
from plsq import read_frames as plsq_read_frames


# ---------------------------------------------------------------------------
# Minimal mocks (no real UDP)
# ---------------------------------------------------------------------------

class _MockServer:
    """Minimal server stand-in: only the attributes Recorder touches."""
    def __init__(self, lost_packets=0):
        self.lock = threading.Lock()
        self.latest = {}
        self.lost_packets = lost_packets  # pre-seeded

    def snapshot(self):
        with self.lock:
            return ({}, {}, 0, 0, 0, 0.0, self.lost_packets, 0)

    def set_assembler(self, asm):
        pass


def _spans_for(chips, start_universe=1):
    eff_cpu = effective_chips_per_universe(0)
    return build_spans(start_universe, 1, 4, chips, eff_cpu)


def _make_artdmx(universe, data, seq=1):
    n = len(data)
    if n % 2:
        data = data + b"\x00"
        n += 1
    pkt = bytearray(18 + n)
    pkt[0:8] = b"Art-Net\x00"
    pkt[8] = 0x00
    pkt[9] = 0x50
    pkt[12] = seq & 0xFF
    pkt[14] = universe & 0xFF
    pkt[15] = (universe >> 8) & 0x7F
    pkt[16] = n >> 8
    pkt[17] = n & 0xFF
    pkt[18:] = data
    return pkt


def _deliver(assembler, universe, data, seq=1):
    pkt = bytearray(_make_artdmx(universe, data, seq))
    mv = memoryview(pkt)
    assembler.on_universe(universe, mv, len(pkt), len(data))


def _feed_one_complete_frame(fa, spans, fill=0xAA):
    """Deliver one complete set of universe packets to assembler."""
    for uni, ch, n_ch, _off in spans:
        payload = bytes([fill] * n_ch)
        _deliver(fa, uni, payload)


# ---------------------------------------------------------------------------
# I-02: first-frame race — Recorder must not sample a black first frame
# ---------------------------------------------------------------------------

class TestFirstFrameRace(unittest.TestCase):
    """I-02: wait_first_packet must wait for committed_frame, not any packet."""

    def _run_one(self, chips, fill=0xAB):
        """Single pass: returns first frame bytes and None-frame count."""
        spans  = _spans_for(chips)
        stride = chips * 4
        fa     = FrameAssembler()
        fa.start(spans, stride)
        server = _MockServer()
        out_q  = queue.Queue(maxsize=50)

        rec = Recorder(server)
        rec.start(spans, stride, fps=30, max_frames=1,
                  wait_first_packet=True, assembler=fa, out_queue=out_q)

        # Assembler has no data yet — recorder must stay in "waiting"
        time.sleep(0.05)
        self.assertEqual(rec.state, "waiting",
                         "Recorder should wait for first committed frame")
        self.assertTrue(out_q.empty(), "No frame should be queued yet")

        # Feed one complete frame → assembler commits → recorder should fire
        _feed_one_complete_frame(fa, spans, fill=fill)

        # Wait for recorder to capture frame 0 and finish (max_frames=1)
        deadline = time.perf_counter() + 1.0
        while rec.state != "done" and time.perf_counter() < deadline:
            time.sleep(0.01)

        self.assertEqual(rec.state, "done", "Recorder did not finish in time")
        self.assertFalse(out_q.empty(), "Expected one frame in queue")
        frame = out_q.get_nowait()
        return frame

    def test_single_universe_first_frame_not_black(self):
        frame = self._run_one(chips=4, fill=0xAA)
        self.assertNotEqual(frame, bytes(len(frame)),
                            "First frame must not be all-zeros")
        self.assertEqual(frame, bytes([0xAA] * len(frame)))

    def test_multi_universe_first_frame_not_black(self):
        """Two universes: first frame must reflect committed data, not zeros."""
        frame = self._run_one(chips=130, fill=0xBB)
        self.assertNotEqual(frame, bytes(len(frame)))

    def test_stress_50_runs_no_black_first_frame(self):
        """Race must never produce a zero/black first frame across 50 iterations."""
        for i in range(50):
            fill = (i * 7 + 1) & 0xFF or 1  # always non-zero fill
            chips = 4 if i % 2 == 0 else 130
            frame = self._run_one(chips=chips, fill=fill)
            zero = bytes(len(frame))
            self.assertNotEqual(frame, zero,
                                "Run %d (chips=%d fill=%d): first frame was all-zeros"
                                % (i, chips, fill))

    def test_stress_500_universes_no_black_first_frame(self):
        """High universe-count stress: 500 universes, 10 iterations."""
        chips = 64000  # 500 universes
        spans  = _spans_for(chips)
        stride = chips * 4
        FILL   = 0xCC

        for i in range(10):
            fa     = FrameAssembler()
            fa.start(spans, stride)
            server = _MockServer()
            out_q  = queue.Queue(maxsize=10)

            rec = Recorder(server)
            rec.start(spans, stride, fps=30, max_frames=1,
                      wait_first_packet=True, assembler=fa, out_queue=out_q)

            time.sleep(0.02)
            self.assertEqual(rec.state, "waiting")
            _feed_one_complete_frame(fa, spans, fill=FILL)

            deadline = time.perf_counter() + 2.0
            while rec.state != "done" and time.perf_counter() < deadline:
                time.sleep(0.01)

            self.assertEqual(rec.state, "done",
                             "Run %d: recorder timed out" % i)
            frame = out_q.get_nowait()
            self.assertNotEqual(frame, bytes(stride),
                                "Run %d: 500-universe first frame was all-zeros" % i)


# ---------------------------------------------------------------------------
# I-03: lost_packets baseline — pre-recording losses must not leak into meta
# ---------------------------------------------------------------------------

class TestLostPacketsBaseline(unittest.TestCase):
    """I-03: snapshot at recording start; pre-recording losses excluded."""

    def _make_session(self, chips, pre_lost, out_path):
        """Build a PlsqRecordingSession with a server that has pre_lost losses."""
        from session import ArtNetServer, PlsqRecordingSession

        # We need a real ArtNetServer because PlsqRecordingSession calls
        # server.snapshot()[6] for the baseline.  Inject pre_lost directly.
        server = ArtNetServer.__new__(ArtNetServer)
        # Minimal ArtNetServer initialisation (no socket)
        import threading as _t
        server.lock = _t.Lock()
        server.latest = {}
        server.uni_stats = {}
        server.total_packets = 0
        server.ignored_packets = 0
        server.total_bytes = 0
        server.last_packet_time = 0.0
        server.lost_packets = pre_lost   # <-- pre-recording losses
        server.rcvbuf_actual = 0
        server._assembler = None
        server.running = False
        server._seq_last = {}

        spans  = _spans_for(chips)
        stride = chips * 4
        eff_cpu = effective_chips_per_universe(0)

        return PlsqRecordingSession(
            server,
            chips=chips, fps=30, out_path=out_path,
            spans=spans, stride=stride,
            start_universe=1, start_channel=1, eff_cpu=eff_cpu,
            skip=0, blend=0, final_frames=3,
            wait_first_packet=False,
            complete_frames=False,
        ), server

    def test_plsq_pre_recording_losses_excluded(self):
        """PLSQ: lost_packets in meta == in-recording losses only."""
        from session import ArtNetServer, PlsqRecordingSession
        from plsq import validate

        chips = 4
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "test.plsq")
            session, server = self._make_session(chips, pre_lost=100, out_path=out)

            session.start()
            self.assertNotEqual(session.state, "error",
                                "session.start() failed: %s" % session.error)

            # Feed 3 frames manually (no real UDP; assembler disabled)
            frame = bytes([0x11] * chips * 4)
            if session._queue is not None:
                for _ in range(3):
                    session._queue.put(frame)

            # Request stop → finalizes with 3 frames
            session.request_stop()
            # Drain the session state machine
            deadline = time.perf_counter() + 10.0
            while session.state not in ("done", "error"):
                session.poll()
                time.sleep(0.05)
                if time.perf_counter() > deadline:
                    break

            self.assertEqual(session.state, "done",
                             "Session error: %s" % session.error)
            res = session.result()
            self.assertEqual(res["lost_packets"], 0,
                             "Pre-recording losses leaked into result: got %d"
                             % res["lost_packets"])

            # Also check the written file meta
            problems = validate(out)
            self.assertFalse(problems, "Validate: %s" % problems)

            from plsq import read_info
            _h, meta = read_info(out)
            # lost_packets is stored as a string in the meta block
            self.assertEqual(int(meta.get("lost_packets", -1)), 0,
                             "File meta lost_packets != 0: %s"
                             % meta.get("lost_packets"))

    def test_plsq_in_recording_losses_counted(self):
        """PLSQ: losses that happen during recording ARE counted."""
        chips = 4
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "test.plsq")
            session, server = self._make_session(chips, pre_lost=100, out_path=out)

            session.start()
            self.assertNotEqual(session.state, "error")

            # Inject 5 additional losses AFTER start() (simulating in-recording gaps)
            with server.lock:
                server.lost_packets += 5

            frame = bytes([0x22] * chips * 4)
            if session._queue is not None:
                for _ in range(3):
                    session._queue.put(frame)

            session.request_stop()
            deadline = time.perf_counter() + 10.0
            while session.state not in ("done", "error"):
                session.poll()
                time.sleep(0.05)
                if time.perf_counter() > deadline:
                    break

            self.assertEqual(session.state, "done",
                             "Session error: %s" % session.error)
            res = session.result()
            self.assertEqual(res["lost_packets"], 5,
                             "Expected 5 in-recording losses, got %d"
                             % (res["lost_packets"],))

    def test_json_pre_recording_losses_excluded(self):
        """JSON: build_meta with baselined lost_packets must reflect only in-recording losses."""
        from plsq import build_meta
        from session import write_json
        import json as _json

        chips = 4
        spans = _spans_for(chips)
        stride = chips * 4

        # Simulate: pre-recording total = 50, in-recording addition = 3
        pre_lost = 50
        in_recording_lost = 3
        baselined = max(0, (pre_lost + in_recording_lost) - pre_lost)

        frame = bytes([0xAA] * stride)
        frames = [frame] * 3
        meta = build_meta(
            file_format="json",
            fps=30,
            frame_count=len(frames),
            frame_stride=stride,
            start_universe=1,
            start_channel=1,
            chips_per_universe=effective_chips_per_universe(0),
            universes_count=len(spans),
            model_chips_count=chips,
            post_skip_frames=0,
            post_loop_blend_frames=0,
            recorded_frames_raw=len(frames),
            missing_frames=0,
            late_frames=0,
            partial_source_frames=0,
            lost_packets=baselined,
        )
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.json")
            write_json(path, meta, frames)
            with open(path, "r", encoding="utf-8") as f:
                doc = _json.load(f)
        # build_meta stores lost_packets as a string
        self.assertEqual(int(doc["lost_packets"]), in_recording_lost,
                         "JSON meta lost_packets should be %d, got %s"
                         % (in_recording_lost, doc.get("lost_packets")))


if __name__ == "__main__":
    unittest.main()
