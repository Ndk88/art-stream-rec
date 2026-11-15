#!/usr/bin/env python3
"""
Convert a JSON sequence (old recorder format) to a PLSQ file.

Usage:
    python tools/convert_json_to_plsq.py in.json out.plsq

Handles both the old 10-key format and the new 27-key format.
Floats are converted back with round(v * 255).
Missing meta keys are filled with documented defaults.
"""

import json
import os
import sys
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plsq import PlsqWriter, build_meta, validate, PlsqError
from mapping import count_universes, effective_chips_per_universe


def _str_int(value, default=0):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _str_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def convert(src_path, dst_path, quiet=False):
    """Convert src_path (JSON) to dst_path (PLSQ). Raises ValueError on bad input."""

    # --- Read source JSON ---
    with open(src_path, "r", encoding="utf-8") as fp:
        raw = json.load(fp)

    if not isinstance(raw, dict):
        raise ValueError("Root of JSON is not an object")
    if "sequence" not in raw or "data" not in raw["sequence"]:
        raise ValueError("JSON has no 'sequence.data' array")

    data = raw["sequence"]["data"]
    if not isinstance(data, list):
        raise ValueError("sequence.data is not a list")

    # --- Extract meta fields (gracefully handle missing keys) ---
    chips_count = _str_int(raw.get("model_chips_count", 0))
    if chips_count < 1:
        raise ValueError("model_chips_count missing or invalid: %r" % raw.get("model_chips_count"))

    fps_raw = _str_float(raw.get("sequence_fps", 0))
    fps = int(fps_raw)
    if fps < 1:
        raise ValueError("sequence_fps missing or invalid: %r" % raw.get("sequence_fps"))

    frame_stride_raw = _str_int(raw.get("sequence_frame_stride", 0))
    if frame_stride_raw < 1:
        raise ValueError("sequence_frame_stride missing or invalid")

    # Infer channels_per_chip
    if frame_stride_raw % chips_count != 0:
        raise ValueError(
            "frame_stride %d is not divisible by chips_count %d"
            % (frame_stride_raw, chips_count)
        )
    channels_per_chip = frame_stride_raw // chips_count
    if channels_per_chip != 4:
        raise ValueError(
            "Only RGBW (4 channels per chip) is supported; got %d channels per chip"
            % channels_per_chip
        )

    frame_stride = frame_stride_raw

    # Verify data length
    expected_elements = len(data)
    if expected_elements % frame_stride != 0:
        raise ValueError(
            "data length %d is not a multiple of frame_stride %d"
            % (expected_elements, frame_stride)
        )
    frame_count = expected_elements // frame_stride

    if frame_count < 1:
        raise ValueError("No frames in source file")

    # --- Mapping (use file values if present, else defaults) ---
    start_universe = _str_int(raw.get("input_mapping_start_universe", 1))
    start_channel = _str_int(raw.get("input_mapping_start_channel", 1))
    cpu_raw = _str_int(raw.get("input_mapping_chips_per_universe", 0))
    eff_cpu = effective_chips_per_universe(cpu_raw)
    universes_count = count_universes(start_universe, start_channel, eff_cpu, chips_count)

    # --- Build now-timestamp for recorded_at ---
    from datetime import datetime
    now = datetime.now().astimezone()
    def _utc_offset(dt):
        off = dt.utcoffset()
        if off is None:
            return "+00:00"
        total = int(off.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        h, m = divmod(total // 60, 60)
        return "%s%02d:%02d" % (sign, h, m)
    recorded_at = now.strftime("%Y-%m-%dT%H:%M:%S") + _utc_offset(now)

    # --- Write PLSQ ---
    part = dst_path + ".part"
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)

    w = PlsqWriter()
    w.open(part, chips_count, fps, start_universe, start_channel, eff_cpu)

    for f in range(frame_count):
        start_idx = f * frame_stride
        frame_floats = data[start_idx:start_idx + frame_stride]
        frame_bytes = bytes(int(round(v * 255)) for v in frame_floats)
        w.append_frame(frame_bytes)

    meta = build_meta(
        file_format="plsq",
        sequence_id=str(uuid.uuid4()),
        recorded_at=recorded_at,
        recorder_version=str(raw.get("recorder_version", "converted")),
        model_name=str(raw.get("model_name", "")),
        model_chips_tag=str(raw.get("model_chips_tag", "")),
        model_chips_count=chips_count,
        sequence_name=str(raw.get("sequence_name", "")),
        sequence_version=str(raw.get("sequence_version", "")),
        fps=fps,
        frame_count=frame_count,
        frame_stride=frame_stride,
        start_universe=start_universe,
        start_channel=start_channel,
        chips_per_universe=eff_cpu,
        universes_count=universes_count,
        post_skip_frames=_str_int(raw.get("post_skip_frames", 0)),
        post_loop_blend_frames=_str_int(raw.get("post_loop_blend_frames", 0)),
        recorded_frames_raw=_str_int(raw.get("recorded_frames_raw", frame_count)),
        missing_frames=_str_int(raw.get("missing_frames", 0)),
        late_frames=_str_int(raw.get("late_frames", 0)),
        partial_source_frames=_str_int(raw.get("partial_source_frames", 0)),
        lost_packets=_str_int(raw.get("lost_packets", 0)),
        developer_notes=str(raw.get("developer_notes", "")),
    )
    w.finalize(meta)
    os.replace(part, dst_path)

    # --- Validate result ---
    problems = [p for p in validate(dst_path) if not p.startswith("TrailingData")]
    if problems:
        raise PlsqError(
            "ConversionError",
            "Output failed validation:\n" + "\n".join("  " + p for p in problems),
        )

    if not quiet:
        import os as _os
        size = _os.path.getsize(dst_path)
        print(
            "Converted: %s -> %s  (%d frames, %.1f KB)"
            % (src_path, dst_path, frame_count, size / 1024.0)
        )

    return frame_count, frame_stride


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Convert a JSON sequence to PLSQ.")
    p.add_argument("src", help="Input .json file")
    p.add_argument("dst", help="Output .plsq file")
    args = p.parse_args(argv)
    try:
        convert(args.src, args.dst)
    except (ValueError, PlsqError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
