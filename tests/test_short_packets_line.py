"""
Tests for the I-10 statistics line (session.short_packets_warning).
The GUI itself is not tested; it only displays this string in yellow.

Run:  python -m unittest tests.test_short_packets_line
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mapping import build_spans
from session import FrameAssembler, short_packets_warning


class TestShortPacketsWarning(unittest.TestCase):
    def test_none_when_zero(self):
        self.assertIsNone(short_packets_warning(0, None))
        self.assertIsNone(short_packets_warning(0, 7))

    def test_format(self):
        self.assertEqual(
            short_packets_warning(12, 3), "short packets: 12 (first universe 3)"
        )

    def test_universe_zero_is_shown(self):
        self.assertEqual(
            short_packets_warning(1, 0), "short packets: 1 (first universe 0)"
        )

    def test_unknown_universe(self):
        self.assertEqual(short_packets_warning(5, None), "short packets: 5")

    def test_from_assembler_counters(self):
        """A real short packet on U2 produces the expected line."""
        spans = build_spans(1, 1, 4, 130, 128)  # U1: 128 chips, U2: 2 chips
        fa = FrameAssembler()
        fa.start(spans, 130 * 4)
        buf = bytearray(18 + 512)
        mv = memoryview(buf)
        fa.on_universe(1, mv, len(buf), 512)  # full
        fa.on_universe(2, mv, 18 + 4, 4)      # 4 bytes, mapping needs 8
        self.assertEqual(
            short_packets_warning(fa.short_packets, fa.first_short_uni),
            "short packets: 1 (first universe 2)",
        )


if __name__ == "__main__":
    unittest.main()
