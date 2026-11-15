#!/usr/bin/env python3
"""
Generate a synthetic PLSQ file for testing.

Usage:
    python tools/gen_synthetic.py --chips N --fps 30|60 --seconds S --out file.plsq [--pattern ramp|rainbow|static]

Performance: builds one base pattern of stride bytes, then derives every frame by
rotating a memoryview slice — no per-byte Python loops in the frame loop.
Target: at least 200 MB/s write throughput.
"""

import os
import sys
import time
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plsq import PlsqWriter, build_meta
from mapping import count_universes, effective_chips_per_universe


def _build_base(pattern, chips, stride):
    """Build base pattern of exactly stride bytes without per-byte frame loops."""
    if pattern == "static":
        return bytes([128]) * stride
    elif pattern == "ramp":
        unit = bytes(range(256))
        reps = stride // 256 + 1
        return (unit * reps)[:stride]
    elif pattern == "rainbow":
        base = bytearray(stride)
        for i in range(chips):
            base[i * 4] = (i * 7) % 256
            base[i * 4 + 1] = (i * 5) % 256
            base[i * 4 + 2] = (i * 3) % 256
            # W channel stays 0
        return bytes(base)
    else:
        raise ValueError("Unknown pattern: %r (choose ramp, rainbow, static)" % pattern)


def _write_frames(writer, base, total_frames, rotation_step):
    """Write total_frames frames using memoryview slicing (no data copy per frame)."""
    if rotation_step == 0 or total_frames == 0:
        for _ in range(total_frames):
            writer.append_frame(base)
        return
    stride = len(base)
    # Double the base so we can always take a slice without wrapping
    double = base + base
    mv = memoryview(double)
    for f in range(total_frames):
        offset = (f * rotation_step) % stride
        writer.append_frame(mv[offset:offset + stride])


def generate(out_path, chips, fps, seconds, pattern="ramp", start_universe=0,
             start_channel=1, chips_per_universe=0, quiet=False):
    """Generate a synthetic PLSQ file. Returns (elapsed_sec, bytes_written)."""
    eff_cpu = effective_chips_per_universe(chips_per_universe)
    universes_count = count_universes(start_universe, start_channel, eff_cpu, chips)
    stride = chips * 4
    total_frames = int(round(seconds * fps))
    if total_frames < 1:
        total_frames = 1

    part = out_path + ".part"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    base = _build_base(pattern, chips, stride)
    rotation_step = 0 if pattern == "static" else 4  # rotate by 1 chip per frame

    w = PlsqWriter()
    w.open(part, chips, fps, start_universe, start_channel, chips_per_universe)

    t0 = time.perf_counter()
    _write_frames(w, base, total_frames, rotation_step)
    elapsed_write = time.perf_counter() - t0

    meta = build_meta(
        file_format="plsq",
        sequence_id=str(uuid.uuid4()),
        recorder_version="synthetic",
        model_name="Synthetic",
        model_chips_tag="synthetic_chip",
        model_chips_count=chips,
        sequence_name="synthetic",
        sequence_version="1.0",
        fps=fps,
        frame_count=total_frames,
        frame_stride=stride,
        start_universe=start_universe,
        start_channel=start_channel,
        chips_per_universe=eff_cpu,
        universes_count=universes_count,
    )
    w.finalize(meta)
    os.replace(part, out_path)

    elapsed = time.perf_counter() - t0
    payload_bytes = total_frames * stride

    if not quiet:
        mb = payload_bytes / 1e6
        mb_s = payload_bytes / elapsed_write / 1e6 if elapsed_write > 0 else 0
        print(
            "Written: %s  |  %d frames  %.1f MB  %.0f MB/s"
            % (out_path, total_frames, mb, mb_s)
        )

    return elapsed, payload_bytes


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Generate a synthetic PLSQ file.")
    p.add_argument("--chips", type=int, required=True, help="Number of chips (1..64000)")
    p.add_argument("--fps", type=int, choices=[30, 60], required=True)
    p.add_argument("--seconds", type=float, required=True, help="Duration in seconds")
    p.add_argument("--out", required=True, help="Output .plsq path")
    p.add_argument("--pattern", choices=["ramp", "rainbow", "static"], default="ramp")
    p.add_argument("--start-universe", type=int, default=0)
    p.add_argument("--start-channel", type=int, default=1)
    p.add_argument("--chips-per-universe", type=int, default=0)
    args = p.parse_args(argv)

    if not (1 <= args.chips <= 64000):
        p.error("chips must be in 1..64000")

    generate(
        args.out, args.chips, args.fps, args.seconds,
        pattern=args.pattern,
        start_universe=args.start_universe,
        start_channel=args.start_channel,
        chips_per_universe=args.chips_per_universe,
    )


if __name__ == "__main__":
    main()
