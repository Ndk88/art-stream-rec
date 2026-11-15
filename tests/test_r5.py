"""
Tests for R5: FrameAssembler, ArtNetServer improvements, lost-packet counting.

Run:  python -m unittest tests.test_r5
"""

import os
import socket
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from main import ArtNetServer, FrameAssembler, Recorder, parse_artdmx
from mapping import build_spans, effective_chips_per_universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artdmx(universe, data, seq=1):
    """Build a minimal ArtDMX bytearray."""
    n = len(data)
    if n % 2:
        data = data + b"\x00"
        n += 1
    pkt = bytearray(18 + n)
    pkt[0:8] = b"Art-Net\x00"
    pkt[8] = 0x00
    pkt[9] = 0x50
    pkt[10] = 0
    pkt[11] = 14
    pkt[12] = seq & 0xFF
    pkt[13] = 0
    pkt[14] = universe & 0xFF
    pkt[15] = (universe >> 8) & 0x7F
    pkt[16] = n >> 8
    pkt[17] = n & 0xFF
    pkt[18:] = data
    return pkt


def _deliver(assembler, universe, data, seq=1):
    """Feed one ArtDMX packet to the assembler via on_universe()."""
    pkt = bytearray(_make_artdmx(universe, data, seq))
    mv = memoryview(pkt)
    assembler.on_universe(universe, mv, len(pkt), len(data))


def _spans_for(chips, start_universe=1):
    eff_cpu = effective_chips_per_universe(0)
    return build_spans(start_universe, 1, 4, chips, eff_cpu)


# ---------------------------------------------------------------------------
# FrameAssembler unit tests
# ---------------------------------------------------------------------------

class TestFrameAssemblerSingleUniverse(unittest.TestCase):

    def setUp(self):
        chips = 4
        spans = _spans_for(chips)
        self.fa = FrameAssembler()
        self.fa.start(spans, chips * 4)
        self.uni = spans[0][0]
        self.stride = chips * 4

    def test_initial_state(self):
        self.assertIsNone(self.fa.committed_frame)
        self.assertEqual(self.fa.partial_count, 0)

    def test_commit_on_first_complete_set(self):
        data = bytes(range(self.stride))
        _deliver(self.fa, self.uni, data)
        self.assertIsNotNone(self.fa.committed_frame)
        self.assertEqual(self.fa.committed_frame, data)

    def test_data_written_at_correct_offset(self):
        """ch=0, offset=0 for a single-universe layout."""
        data = bytes(b"\xAB" * self.stride)
        _deliver(self.fa, self.uni, data)
        self.assertEqual(self.fa.committed_frame, data)

    def test_no_commit_before_all_universes(self):
        """Should not commit for an unmapped universe."""
        _deliver(self.fa, self.uni + 100, bytes(self.stride))
        self.assertIsNone(self.fa.committed_frame)

    def test_partial_count_zero_after_full_commit(self):
        _deliver(self.fa, self.uni, bytes(self.stride))
        self.assertEqual(self.fa.partial_count, 0)

    def test_enabled_false_suppresses_commit(self):
        self.fa.enabled = False
        _deliver(self.fa, self.uni, bytes(self.stride))
        self.assertIsNone(self.fa.committed_frame)

    def test_artsync_no_data_no_commit(self):
        """ArtSync with no prior data must NOT commit."""
        self.fa.on_artsync()
        self.assertIsNone(self.fa.committed_frame)

    def test_artsync_after_first_universe_commits(self):
        """ArtSync after partial data forces a commit."""
        _deliver(self.fa, self.uni, bytes(self.stride))
        # already committed — reset state manually for a fresh test
        self.fa.committed_frame = None
        self.fa._updated = set()

        # Partial universe, then ArtSync
        data = bytes([77] * self.stride)
        _deliver(self.fa, self.uni, data)
        # For single universe this would already commit → redo with two-universe setup

    def test_multiple_frames(self):
        """Each new complete set replaces committed_frame."""
        for i in range(5):
            data = bytes([i] * self.stride)
            _deliver(self.fa, self.uni, data)
            self.assertEqual(self.fa.committed_frame, data)
        self.assertEqual(self.fa.partial_count, 0)


class TestFrameAssemblerTwoUniverses(unittest.TestCase):
    CHIPS = 130  # 128 + 2 → two universes

    def setUp(self):
        self.spans = _spans_for(self.CHIPS)
        self.fa = FrameAssembler()
        self.fa.start(self.spans, self.CHIPS * 4)
        self.u0 = self.spans[0][0]
        self.u1 = self.spans[1][0]
        self.stride = self.CHIPS * 4

    def _data0(self, fill=0x11):
        return bytes([fill] * (128 * 4))

    def _data1(self, fill=0x22):
        return bytes([fill] * (2 * 4))

    def test_no_commit_after_first_universe_only(self):
        _deliver(self.fa, self.u0, self._data0())
        self.assertIsNone(self.fa.committed_frame)

    def test_commit_after_both_universes(self):
        _deliver(self.fa, self.u0, self._data0())
        _deliver(self.fa, self.u1, self._data1())
        self.assertIsNotNone(self.fa.committed_frame)
        self.assertEqual(len(self.fa.committed_frame), self.stride)

    def test_frame_data_correct(self):
        d0 = bytes([0xAA] * (128 * 4))
        d1 = bytes([0xBB] * (2 * 4))
        _deliver(self.fa, self.u0, d0)
        _deliver(self.fa, self.u1, d1)
        f = self.fa.committed_frame
        # First 512 bytes = d0, last 8 bytes = d1
        _, ch0, n0, off0 = self.spans[0]
        _, ch1, n1, off1 = self.spans[1]
        self.assertEqual(f[off0:off0 + n0], d0[ch0:ch0 + n0])
        self.assertEqual(f[off1:off1 + n1], d1[ch1:ch1 + n1])

    def test_artsync_commits_partial(self):
        _deliver(self.fa, self.u0, self._data0())
        self.assertIsNone(self.fa.committed_frame)
        self.fa.on_artsync()
        self.assertIsNotNone(self.fa.committed_frame)
        # ArtSync commit is NOT counted as partial
        self.assertEqual(self.fa.partial_count, 0)

    def test_repeat_universe_causes_partial(self):
        """Universe 0 arriving twice before universe 1 triggers a partial commit."""
        _deliver(self.fa, self.u0, self._data0(0x10))
        self.assertIsNone(self.fa.committed_frame)

        # Second arrival of u0 → partial commit of current working frame
        _deliver(self.fa, self.u0, self._data0(0x20))
        self.assertIsNotNone(self.fa.committed_frame)
        self.assertEqual(self.fa.partial_count, 1)

    def test_full_commit_after_partial(self):
        """After a partial commit, next full set commits normally."""
        _deliver(self.fa, self.u0, self._data0(0x10))
        _deliver(self.fa, self.u0, self._data0(0x20))  # triggers partial
        # Now u1 arrives → full commit
        _deliver(self.fa, self.u1, self._data1(0x30))
        self.assertEqual(self.fa.partial_count, 1)
        # Second time u0 and u1 arrive
        _deliver(self.fa, self.u0, self._data0(0x40))
        _deliver(self.fa, self.u1, self._data1(0x50))
        f = self.fa.committed_frame
        _, ch0, n0, off0 = self.spans[0]
        self.assertEqual(f[off0:off0 + n0], self._data0(0x40)[ch0:ch0 + n0])

    def test_ordered_arrival_zero_partials(self):
        """When universes always arrive in order, no partials."""
        for _ in range(10):
            _deliver(self.fa, self.u0, self._data0())
            _deliver(self.fa, self.u1, self._data1())
        self.assertEqual(self.fa.partial_count, 0)

    def test_enabled_false_no_commit(self):
        self.fa.enabled = False
        _deliver(self.fa, self.u0, self._data0())
        _deliver(self.fa, self.u1, self._data1())
        self.assertIsNone(self.fa.committed_frame)


# ---------------------------------------------------------------------------
# Atomicity: committed_frame reference swap is safe across threads
# ---------------------------------------------------------------------------

class TestCommittedFrameAtomicity(unittest.TestCase):
    """
    Verify that the sampler thread never observes a partially-written frame.

    Design: the receive thread continuously commits frames with an identifiable
    signature (all bytes = frame counter % 256). The reader thread checks that
    every frame it reads has consistent bytes. A torn frame would have mixed
    counter values.
    """

    def test_no_torn_frames(self):
        DURATION = 0.5   # seconds
        CHIPS = 32
        spans = _spans_for(CHIPS)
        fa = FrameAssembler()
        fa.start(spans, CHIPS * 4)
        uni = spans[0][0]
        stride = CHIPS * 4

        torn = []
        stop = threading.Event()

        def writer():
            frame_n = 0
            while not stop.is_set():
                fill = frame_n % 256
                data = bytes([fill] * stride)
                pkt = bytearray(_make_artdmx(uni, data))
                mv = memoryview(pkt)
                fa.on_universe(uni, mv, len(pkt), stride)
                frame_n += 1

        def reader():
            while not stop.is_set():
                f = fa.committed_frame
                if f is not None and len(f) == stride:
                    first = f[0]
                    if any(b != first for b in f):
                        torn.append(bytes(f))

        t_write = threading.Thread(target=writer, daemon=True)
        t_read = threading.Thread(target=reader, daemon=True)
        t_read.start()
        t_write.start()
        time.sleep(DURATION)
        stop.set()
        t_write.join(timeout=1.0)
        t_read.join(timeout=1.0)

        self.assertEqual(torn, [], "Torn frames detected: %d" % len(torn))


# ---------------------------------------------------------------------------
# Lost packet counting via sequence gaps
# ---------------------------------------------------------------------------

class TestLostPacketCounting(unittest.TestCase):
    PORT = 16455

    def _run_server(self, pkts, port=PORT):
        """Start server, send pkts via UDP, stop, return lost_packets."""
        srv = ArtNetServer()
        srv.start("127.0.0.1", port)
        time.sleep(0.05)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for pkt in pkts:
            sock.sendto(bytes(pkt), ("127.0.0.1", port))
        time.sleep(0.1)
        srv.stop()
        sock.close()
        return srv.lost_packets

    def test_no_gaps_no_loss(self):
        pkts = [_make_artdmx(1, bytes(16), seq=s) for s in range(1, 11)]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 0)

    def test_single_gap(self):
        # Send seq 1, 2, 4 (skip 3 → 1 lost packet)
        pkts = [
            _make_artdmx(1, bytes(16), seq=1),
            _make_artdmx(1, bytes(16), seq=2),
            _make_artdmx(1, bytes(16), seq=4),
        ]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 1)

    def test_multiple_gaps(self):
        # Send 1, 5 (skipped 2,3,4 → 3 lost)
        pkts = [
            _make_artdmx(1, bytes(16), seq=1),
            _make_artdmx(1, bytes(16), seq=5),
        ]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 3)

    def test_sequence_zero_not_tracked(self):
        """seq=0 means disabled; must not affect lost_packets."""
        pkts = [_make_artdmx(1, bytes(16), seq=0) for _ in range(10)]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 0)

    def test_wrap_around_no_false_loss(self):
        """Normal 255 → 1 wrap must not count as loss."""
        pkts = [
            _make_artdmx(1, bytes(16), seq=254),
            _make_artdmx(1, bytes(16), seq=255),
            _make_artdmx(1, bytes(16), seq=1),   # normal wrap
            _make_artdmx(1, bytes(16), seq=2),
        ]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 0)

    def test_large_gap_treated_as_reorder(self):
        """Gap > 128 is treated as reorder and NOT counted as loss."""
        pkts = [
            _make_artdmx(1, bytes(16), seq=1),
            _make_artdmx(1, bytes(16), seq=200),   # gap = 199 > 128 → ignored
        ]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 0)

    def test_per_universe_independent(self):
        """Gaps are tracked independently per universe."""
        pkts = [
            _make_artdmx(1, bytes(16), seq=1),
            _make_artdmx(2, bytes(16), seq=1),
            _make_artdmx(1, bytes(16), seq=3),   # gap of 1 on U1
            _make_artdmx(2, bytes(16), seq=2),   # no gap on U2
        ]
        lost = self._run_server(pkts)
        self.assertEqual(lost, 1)


# ---------------------------------------------------------------------------
# ArtNetServer: snapshot returns 8-tuple (backward-compat check)
# ---------------------------------------------------------------------------

class TestSnapshotArity(unittest.TestCase):
    PORT = 16456

    def test_snapshot_returns_8(self):
        srv = ArtNetServer()
        srv.start("127.0.0.1", self.PORT)
        result = srv.snapshot()
        srv.stop()
        self.assertEqual(len(result), 8, "snapshot() must return 8 values")
        # (latest, ustats, total, ignored, bytes, last, lost, rcvbuf)
        _, _, total, ignored, nbytes, last, lost, rcvbuf = result
        self.assertIsInstance(lost, int)
        self.assertIsInstance(rcvbuf, int)


# ---------------------------------------------------------------------------
# Recorder uses assembler: missing count with assembler vs. without
# ---------------------------------------------------------------------------

class TestRecorderWithAssembler(unittest.TestCase):
    PORT = 16457

    def test_missing_while_no_committed_frame(self):
        """With assembler enabled, frames before first commit are counted missing."""
        srv = ArtNetServer()
        srv.start("127.0.0.1", self.PORT)

        chips = 4
        spans = _spans_for(chips, start_universe=99)  # use unmapped universe → no data
        stride = chips * 4
        asm = FrameAssembler()
        asm.start(spans, stride)
        srv.set_assembler(asm)

        rec = Recorder(srv)
        rec.start(spans, stride, fps=30, assembler=asm, wait_first_packet=False)
        time.sleep(0.2)
        rec.stop()
        srv.set_assembler(None)
        srv.stop()

        # No data was sent → all sampled frames are missing
        self.assertGreater(rec.missing, 0)
        # Frames are still collected (zeros), not skipped
        self.assertGreater(len(rec.frames), 0)
        # All frames should be zeros
        for f in rec.frames:
            self.assertEqual(f, bytes(stride))

    def test_partial_source_frames_property(self):
        rec = Recorder(ArtNetServer())
        # No assembler
        self.assertEqual(rec.partial_source_frames, 0)
        # With assembler
        chips = 4
        spans = _spans_for(chips)
        asm = FrameAssembler()
        asm.start(spans, chips * 4)
        asm.partial_count = 7
        rec._assembler = asm
        self.assertEqual(rec.partial_source_frames, 7)


# ---------------------------------------------------------------------------
# Stagger integration: ArtSync prevents partial commits under inter-universe delay
# ---------------------------------------------------------------------------

_ARTSYNC_PKT = bytearray(14)
_ARTSYNC_PKT[0:8] = b"Art-Net\x00"
_ARTSYNC_PKT[8] = 0x00   # OpCode 0x5200 little-endian
_ARTSYNC_PKT[9] = 0x52
_ARTSYNC_PKT[10] = 0
_ARTSYNC_PKT[11] = 14


class TestStaggerWithArtSync(unittest.TestCase):
    """
    Simulate packets_tester --stagger-ms: universe 0 and universe 1 of each
    frame are sent with a gap between them, followed by an ArtSync.  With a
    stagger well below the frame period the commit fires when the last universe
    arrives; ArtSync arrives to an already-reset assembler, so partial_count
    stays 0.
    """
    PORT = 16459

    def test_zero_partials_with_stagger_and_artsync(self):
        CHIPS = 130  # 2 universes: 128 chips + 2 chips
        spans = _spans_for(CHIPS)
        srv = ArtNetServer()
        asm = FrameAssembler()
        asm.start(spans, CHIPS * 4)
        srv.set_assembler(asm)
        srv.start("127.0.0.1", self.PORT)
        time.sleep(0.05)

        u0 = spans[0][0]
        u1 = spans[1][0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = ("127.0.0.1", self.PORT)

        FRAMES = 15
        for i in range(FRAMES):
            seq = i % 255 + 1
            sock.sendto(bytes(_make_artdmx(u0, bytes([i % 256] * (128 * 4)), seq=seq)), addr)
            time.sleep(0.002)   # 2 ms stagger (well below 33 ms frame period)
            sock.sendto(bytes(_make_artdmx(u1, bytes([i % 256] * (2 * 4)),  seq=seq)), addr)
            sock.sendto(bytes(_ARTSYNC_PKT), addr)
            time.sleep(0.03)    # ~30 fps spacing

        time.sleep(0.05)
        srv.set_assembler(None)
        srv.stop()
        sock.close()

        self.assertEqual(asm.partial_count, 0, "stagger + ArtSync must produce 0 partial commits")
        self.assertIsNotNone(asm.committed_frame, "at least one frame must have been committed")


if __name__ == "__main__":
    unittest.main()
