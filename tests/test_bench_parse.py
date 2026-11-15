"""
Tests for tools/bench_e2e.py::_parse_sent (sum of packets over all tester
processes).

Run:  python -m unittest tests.test_bench_parse
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.bench_e2e import _parse_sent

# Output shape of `packets_tester.py ... 64000 1 --procs 4` (500 universes,
# split 125 per process): one main process plus three workers.
FOUR_PROCS = """\
[12:00:00] Target 127.0.0.1:6454 | chips 64000 | universes 500 (U1-U500, 128 chips/uni) | 60 fps | ramp
[12:00:00] Multi-process: 4 processes
[12:00:00] Duration: 13.0 s
[12:00:00] Expected: ~30000 pkt/s | Ctrl+C to stop
[12:00:00] Precomputing frame ring (120 frames)...
[12:00:00] Sending...
[12:00:02] sent 7500 (7500.0 pkt/s x 125 uni) | total 7500 | send errors 0 (+0)
[12:00:02] Worker 1 | total 7375 | send errors 0
[12:00:02] Worker 2 | total 7250 | send errors 0
[12:00:02] Worker 3 | total 7500 | send errors 0
[12:00:14] sent 7500 (7500.0 pkt/s x 125 uni) | total 97500 | send errors 0 (+0)
[12:00:14] Worker 1 | total 97000 | send errors 0
[12:00:14] Worker 2 | total 96875 | send errors 0
[12:00:14] Worker 3 | total 97250 | send errors 0
[12:00:15] Stopped. Worker 1 sent 97625 packets, 0 send errors.
[12:00:15] Stopped. Worker 2 sent 97500 packets, 0 send errors.
[12:00:15] Stopped. Worker 3 sent 97625 packets, 0 send errors.
[12:00:15] Stopped. Main process sent 97625 packets, 0 send errors.
"""


class TestParseSent(unittest.TestCase):
    def test_four_processes_final_lines_summed(self):
        self.assertEqual(_parse_sent(FOUR_PROCS), 97625 * 3 + 97500)

    def test_missing_final_line_falls_back_to_last_total(self):
        text = FOUR_PROCS.replace(
            "[12:00:15] Stopped. Worker 2 sent 97500 packets, 0 send errors.\n", ""
        )
        self.assertEqual(_parse_sent(text), 97625 * 3 + 96875)

    def test_final_line_preferred_over_periodic(self):
        text = (
            "sent 600 (600.0 pkt/s x 10 uni) | total 600 | send errors 0 (+0)\n"
            "sent 600 (600.0 pkt/s x 10 uni) | total 1200 | send errors 0 (+0)\n"
            "Stopped. Main process sent 1260 packets, 0 send errors.\n"
        )
        self.assertEqual(_parse_sent(text), 1260)

    def test_missing_process_gives_none(self):
        lines = [ln for ln in FOUR_PROCS.splitlines() if "Worker 3" not in ln]
        self.assertIsNone(_parse_sent("\n".join(lines)))

    def test_unparseable_gives_none(self):
        self.assertIsNone(_parse_sent(""))
        self.assertIsNone(_parse_sent("Traceback (most recent call last):\n"))


if __name__ == "__main__":
    unittest.main()
