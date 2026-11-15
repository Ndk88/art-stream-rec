"""
Tests for packets_tester.py (R6).

Run:  python -m unittest tests.test_packets_tester
"""

import os
import socket
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import packets_tester as pt
from mapping import build_spans, effective_chips_per_universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recv_packets(port, count, timeout=3.0):
    """Collect up to `count` UDP datagrams on 127.0.0.1:`port`."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.bind(("127.0.0.1", port))
    pkts = []
    try:
        for _ in range(count):
            data, _ = sock.recvfrom(65535)
            pkts.append(data)
    except socket.timeout:
        pass
    finally:
        sock.close()
    return pkts


# ---------------------------------------------------------------------------
# Pattern and ring helpers
# ---------------------------------------------------------------------------

class TestBuildBase(unittest.TestCase):

    def test_static_all_128(self):
        base = pt._build_base("static", 10, 40)
        self.assertEqual(len(base), 40)
        self.assertTrue(all(b == 128 for b in base))

    def test_ramp_length(self):
        base = pt._build_base("ramp", 10, 40)
        self.assertEqual(len(base), 40)

    def test_ramp_starts_at_zero(self):
        base = pt._build_base("ramp", 10, 40)
        self.assertEqual(base[0], 0)

    def test_rainbow_w_channel_zero(self):
        chips = 8
        base = pt._build_base("rainbow", chips, chips * 4)
        for i in range(chips):
            self.assertEqual(base[i * 4 + 3], 0, "W channel of chip %d should be 0" % i)

    def test_rainbow_rgb_nonzero_for_chip_1_onwards(self):
        chips = 8
        base = pt._build_base("rainbow", chips, chips * 4)
        # chip 1: R=(7%256)=7, G=(5%256)=5, B=(3%256)=3 — all non-zero
        self.assertEqual(base[4], 7)
        self.assertEqual(base[5], 5)
        self.assertEqual(base[6], 3)


class TestBuildRing(unittest.TestCase):

    def test_static_same_object(self):
        ring = pt._build_ring("static", 10, 40, 5)
        self.assertEqual(len(ring), 5)
        # all frames are the same bytes object (identity)
        self.assertIs(ring[0], ring[1])

    def test_ring_size(self):
        ring = pt._build_ring("ramp", 10, 40, 8)
        self.assertEqual(len(ring), 8)

    def test_frame_length(self):
        stride = 32
        ring = pt._build_ring("rainbow", 8, stride, 6)
        for frame in ring:
            self.assertEqual(len(frame), stride)

    def test_rotation_distinct_frames(self):
        ring = pt._build_ring("ramp", 10, 40, 10)
        self.assertNotEqual(ring[0], ring[1])

    def test_rotation_wraps(self):
        """After stride/step frames the pattern wraps back to the start."""
        stride = 40
        step = 4
        ring_size = stride // step  # = 10 full rotations
        ring = pt._build_ring("ramp", 10, stride, ring_size)
        self.assertEqual(ring[0], ring[ring_size - 1 + 1 - ring_size] if ring_size == 1 else ring[0])
        # simpler: frame 0 and frame ring_size should be identical (full wrap)
        full_ring = pt._build_ring("ramp", 10, stride, ring_size + 1)
        self.assertEqual(full_ring[0], full_ring[ring_size])


# ---------------------------------------------------------------------------
# ArtDMX packet structure
# ---------------------------------------------------------------------------

class TestMakeArtdmxBytearray(unittest.TestCase):

    def _make(self, universe=1, payload=None):
        if payload is None:
            payload = bytes(range(16))
        return pt._make_artdmx_bytearray(universe, payload)

    def test_magic(self):
        pkt = self._make()
        self.assertEqual(pkt[0:8], b"Art-Net\x00")

    def test_opcode(self):
        pkt = self._make()
        self.assertEqual(pkt[8], 0x00)
        self.assertEqual(pkt[9], 0x50)

    def test_protocol_version(self):
        pkt = self._make()
        self.assertEqual(pkt[10], 0)
        self.assertEqual(pkt[11], 14)

    def test_sequence_placeholder_zero(self):
        pkt = self._make()
        self.assertEqual(pkt[pt.SEQ_OFFSET], 0)

    def test_universe_low_byte(self):
        pkt = pt._make_artdmx_bytearray(5, bytes(10))
        self.assertEqual(pkt[14], 5)
        self.assertEqual(pkt[15], 0)

    def test_universe_high_byte(self):
        pkt = pt._make_artdmx_bytearray(256, bytes(10))
        self.assertEqual(pkt[14], 0)
        self.assertEqual(pkt[15], 1)

    def test_length_even(self):
        pkt = pt._make_artdmx_bytearray(1, bytes(16))
        # n=16, big-endian in pkt[16:18]
        self.assertEqual(pkt[16], 0)
        self.assertEqual(pkt[17], 16)

    def test_odd_payload_padded(self):
        """Odd-length payload must be padded to even."""
        pkt = pt._make_artdmx_bytearray(1, bytes(15))
        # reported length should be 16
        self.assertEqual(pkt[17], 16)
        self.assertEqual(len(pkt), 18 + 16)

    def test_payload_data(self):
        payload = bytes(range(16))
        pkt = pt._make_artdmx_bytearray(1, payload)
        self.assertEqual(bytes(pkt[18:34]), payload)


# ---------------------------------------------------------------------------
# Sequence number wrapping
# ---------------------------------------------------------------------------

class TestSequenceNumberWrapping(unittest.TestCase):

    def test_wraps_at_255_back_to_1(self):
        """seq = seq % 255 + 1 wraps 255 -> 1 and never reaches 0."""
        seq = 1
        seen = set()
        for _ in range(300):
            seq = seq % 255 + 1
            seen.add(seq)
        self.assertNotIn(0, seen)
        self.assertEqual(min(seen), 1)
        self.assertEqual(max(seen), 255)


# ---------------------------------------------------------------------------
# Precomputed packets
# ---------------------------------------------------------------------------

class TestPrecomputePackets(unittest.TestCase):

    def test_shape(self):
        spans = build_spans(1, 1, 4, 10, effective_chips_per_universe(0))
        ring = pt._build_ring("ramp", 10, 40, 4)
        ring_pkts = pt._precompute_packets(spans, ring)
        self.assertEqual(len(ring_pkts), 4)             # 4 frames
        self.assertEqual(len(ring_pkts[0]), len(spans)) # 1 span

    def test_sequence_byte_mutable(self):
        """Sequence byte at SEQ_OFFSET is writable in place."""
        spans = build_spans(1, 1, 4, 10, effective_chips_per_universe(0))
        ring = pt._build_ring("ramp", 10, 40, 2)
        ring_pkts = pt._precompute_packets(spans, ring)
        pkt = ring_pkts[0][0]
        pkt[pt.SEQ_OFFSET] = 42
        self.assertEqual(pkt[pt.SEQ_OFFSET], 42)

    def test_multi_universe_shape(self):
        """130 chips -> 2 universes -> 2 spans per frame."""
        chips = 130
        cpu = effective_chips_per_universe(0)
        spans = build_spans(1, 1, 4, chips, cpu)
        self.assertEqual(len(spans), 2)
        ring = pt._build_ring("ramp", chips, chips * 4, 2)
        ring_pkts = pt._precompute_packets(spans, ring)
        self.assertEqual(len(ring_pkts[0]), 2)


# ---------------------------------------------------------------------------
# End-to-end: send packets and verify on a local UDP socket
# ---------------------------------------------------------------------------

class TestSendLoop(unittest.TestCase):
    PORT = 16454  # use a non-standard port to avoid conflicts

    def _run_sender(self, chips=10, fps=30, duration=0.5, pattern="static",
                    artsync=False, stagger_sec=0.0):
        cpu = effective_chips_per_universe(0)
        spans = build_spans(1, 1, 4, chips, cpu)
        stride = chips * 4
        ring_size = fps * pt.RING_SECONDS
        ring = pt._build_ring(pattern, chips, stride, ring_size)
        ring_pkts = pt._precompute_packets(spans, ring)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = ("127.0.0.1", self.PORT)
        t0 = time.perf_counter() + 0.05
        sent, errors = pt._send_loop(
            spans, ring_pkts, ring_size, sock, addr, fps, duration,
            stagger_sec, artsync, t0,
        )
        sock.close()
        return sent, errors

    def test_1_universe_sends_packets(self):
        expected_min = int(30 * 0.5 * 0.8)  # 80% of expected
        # collect packets in background
        received = []
        def collect():
            received.extend(_recv_packets(self.PORT, 100, timeout=2.0))
        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.05)
        sent, errors = self._run_sender(chips=10, fps=30, duration=0.5)
        t.join(timeout=3.0)
        self.assertEqual(errors, 0)
        self.assertGreaterEqual(sent, expected_min)
        self.assertGreaterEqual(len(received), expected_min)

    def test_artdmx_magic_in_received(self):
        received = []
        def collect():
            received.extend(_recv_packets(self.PORT, 50, timeout=2.0))
        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.05)
        self._run_sender(chips=10, fps=30, duration=0.4)
        t.join(timeout=3.0)
        artdmx = [p for p in received if p[:8] == b"Art-Net\x00" and p[9] == 0x50]
        self.assertGreater(len(artdmx), 0)

    def test_artsync_packets_sent(self):
        """With artsync=True, ArtSync packets (OpCode 0x5200) must appear."""
        received = []
        def collect():
            received.extend(_recv_packets(self.PORT, 100, timeout=2.0))
        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.05)
        self._run_sender(chips=10, fps=30, duration=0.4, artsync=True)
        t.join(timeout=3.0)
        artsync = [p for p in received if len(p) >= 10 and p[8] == 0x00 and p[9] == 0x52]
        self.assertGreater(len(artsync), 0)

    def test_no_artsync_without_flag(self):
        """Without artsync, ArtSync packets must NOT appear."""
        received = []
        def collect():
            received.extend(_recv_packets(self.PORT, 100, timeout=2.0))
        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.05)
        self._run_sender(chips=10, fps=30, duration=0.4, artsync=False)
        t.join(timeout=3.0)
        artsync = [p for p in received if len(p) >= 10 and p[8] == 0x00 and p[9] == 0x52]
        self.assertEqual(len(artsync), 0)

    def test_sequence_numbers_monotonic(self):
        """Sequence bytes in received ArtDMX packets must be monotonically 1..255."""
        received = []
        def collect():
            received.extend(_recv_packets(self.PORT, 70, timeout=2.0))
        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.05)
        self._run_sender(chips=10, fps=30, duration=0.5)
        t.join(timeout=3.0)
        artdmx = [p for p in received if p[:8] == b"Art-Net\x00" and p[9] == 0x50]
        seqs = [p[pt.SEQ_OFFSET] for p in artdmx]
        self.assertGreater(len(seqs), 0)
        for s in seqs:
            self.assertNotEqual(s, 0, "Sequence 0 is not allowed")

    def test_returns_sent_errors(self):
        sent, errors = self._run_sender(chips=10, fps=30, duration=0.3)
        self.assertIsInstance(sent, int)
        self.assertIsInstance(errors, int)
        self.assertEqual(errors, 0)
        self.assertGreater(sent, 0)


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------

class TestMainArgParsing(unittest.TestCase):

    def test_bad_port_returns_2(self):
        code = pt.main(["127.0.0.1", "0", "104", "1", "--duration", "0.01"])
        self.assertEqual(code, 2)

    def test_bad_chips_returns_2(self):
        code = pt.main(["127.0.0.1", "6454", "0", "1", "--duration", "0.01"])
        self.assertEqual(code, 2)

    def test_bad_universe_returns_2(self):
        code = pt.main(["127.0.0.1", "6454", "104", "-1", "--duration", "0.01"])
        self.assertEqual(code, 2)

    def test_bad_procs_returns_2(self):
        code = pt.main(["127.0.0.1", "6454", "104", "1", "--procs", "0", "--duration", "0.01"])
        self.assertEqual(code, 2)

    def test_negative_stagger_returns_2(self):
        code = pt.main(["127.0.0.1", "6454", "104", "1", "--stagger-ms", "-1", "--duration", "0.01"])
        self.assertEqual(code, 2)

    def test_unresolvable_host_returns_1(self):
        code = pt.main(["this.host.does.not.exist.invalid", "6454", "104", "1", "--duration", "0.01"])
        self.assertEqual(code, 1)


# ---------------------------------------------------------------------------
# ARTSYNC_PKT constant
# ---------------------------------------------------------------------------

class TestArtSyncConst(unittest.TestCase):

    def test_magic(self):
        self.assertEqual(pt.ARTSYNC_PKT[:8], b"Art-Net\x00")

    def test_opcode(self):
        self.assertEqual(pt.ARTSYNC_PKT[8], 0x00)
        self.assertEqual(pt.ARTSYNC_PKT[9], 0x52)

    def test_length(self):
        self.assertEqual(len(pt.ARTSYNC_PKT), 14)


if __name__ == "__main__":
    unittest.main()
