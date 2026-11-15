#!/usr/bin/env python3
"""
Generate tests/golden/conformance_v1.plsq (spec section 14).

Usage:
    python tools/make_golden.py
    python tools/make_golden.py --out <path>
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plsq import PlsqWriter, build_meta

# ---------------------------------------------------------------------------
# Conformance parameters (spec section 14)
# ---------------------------------------------------------------------------

CHIPS = 130
FPS = 30
FRAME_COUNT = 10
START_UNIVERSE = 1
START_CHANNEL = 1
CHIPS_PER_UNIVERSE = 128   # effective value
UNIVERSES_COUNT = 2        # ceil(130/128) = 2

META = dict(
    file_format="plsq",
    sequence_id="00000000-0000-4000-8000-000000000000",
    recorded_at="2026-01-01T00:00:00+00:00",
    recorder_version="test",
    model_name="Conformance",
    model_chips_tag="conformance_chip",
    model_chips_count=CHIPS,
    sequence_name="Conformance Sequence",
    sequence_version="1.0",
    fps=FPS,
    frame_count=FRAME_COUNT,
    frame_stride=CHIPS * 4,
    start_universe=START_UNIVERSE,
    start_channel=START_CHANNEL,
    chips_per_universe=CHIPS_PER_UNIVERSE,
    universes_count=UNIVERSES_COUNT,
    post_skip_frames=0,
    post_loop_blend_frames=0,
    recorded_frames_raw=FRAME_COUNT,
    missing_frames=0,
    late_frames=0,
    partial_source_frames=0,
    lost_packets=0,
    developer_notes="Тест UTF-8: привет",
)


def _golden_frame(f):
    """Frame f bytes: byte at chip c, channel k = (f*7 + c*3 + k) % 256."""
    stride = CHIPS * 4
    return bytes((f * 7 + (i // 4) * 3 + (i % 4)) % 256 for i in range(stride))


def generate(out_path):
    """Generate the conformance file at out_path (writes <out_path>.part then renames)."""
    part = out_path + ".part"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    w = PlsqWriter()
    w.open(part, CHIPS, FPS, START_UNIVERSE, START_CHANNEL, CHIPS_PER_UNIVERSE)
    for f in range(FRAME_COUNT):
        w.append_frame(_golden_frame(f))
    w.finalize(build_meta(**META))
    os.replace(part, out_path)


def default_path():
    return os.path.join(_ROOT, "tests", "golden", "conformance_v1.plsq")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Generate PLSQ conformance file.")
    p.add_argument("--out", default=default_path(), help="Output path")
    args = p.parse_args(argv)
    generate(args.out)
    print("Written: %s" % args.out)


if __name__ == "__main__":
    main()
