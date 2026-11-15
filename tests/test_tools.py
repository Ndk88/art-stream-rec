"""
Tests for R7 tools: make_golden, validate_plsq, gen_synthetic, convert_json_to_plsq.

Run:  python -m unittest tests.test_tools
"""

import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import plsq
from plsq import read_info, validate, read_frames, build_meta
from mapping import count_universes, effective_chips_per_universe

import make_golden
import validate_plsq as val_tool
import gen_synthetic
import convert_json_to_plsq as conv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOLDEN_PATH = os.path.join(ROOT, "tests", "golden", "conformance_v1.plsq")


def _write_json(path, chips, fps, frames_data, meta_extra=None):
    """Write a minimal old-style JSON sequence file."""
    stride = len(frames_data[0]) if frames_data else chips * 4
    count = len(frames_data)

    # Build float data
    flat_floats = []
    for frame in frames_data:
        flat_floats.extend(round(b / 255.0, 3) for b in frame)

    meta = {
        "model_name": "Test",
        "model_chips_tag": "test_chip",
        "model_chips_count": str(chips),
        "sequence_name": "Test Seq",
        "sequence_version": "1.0",
        "sequence_duration_sec": str(round(count / fps, 3)),
        "sequence_fps": str(fps),
        "sequence_frame_count": str(count),
        "sequence_frame_stride": str(stride),
        "developer_notes": "",
    }
    if meta_extra:
        meta.update(meta_extra)

    obj = dict(meta)
    obj["sequence"] = {"data": flat_floats}

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# make_golden tests
# ---------------------------------------------------------------------------

class TestMakeGolden(unittest.TestCase):

    def test_golden_file_exists(self):
        self.assertTrue(os.path.exists(GOLDEN_PATH), "Golden file not found: " + GOLDEN_PATH)

    def test_golden_file_size(self):
        """74,832 = 69,632 + 10 * 520 (spec section 14)."""
        self.assertEqual(os.path.getsize(GOLDEN_PATH), 74832)

    def test_golden_file_valid(self):
        problems = [p for p in validate(GOLDEN_PATH) if not p.startswith("TrailingData")]
        self.assertEqual(problems, [], problems)

    def test_golden_header_fields(self):
        h, meta = read_info(GOLDEN_PATH)
        self.assertEqual(h["chips_count"], 130)
        self.assertEqual(h["fps"], 30)
        self.assertEqual(h["frame_count"], 10)
        self.assertEqual(h["frame_stride"], 520)
        self.assertEqual(h["start_universe"], 1)
        self.assertEqual(h["start_channel"], 1)
        self.assertEqual(h["chips_per_universe"], 128)
        self.assertEqual(h["universes_count"], 2)

    def test_golden_meta_fields(self):
        _, meta = read_info(GOLDEN_PATH)
        self.assertEqual(meta["model_name"], "Conformance")
        self.assertEqual(meta["model_chips_tag"], "conformance_chip")
        self.assertEqual(meta["sequence_name"], "Conformance Sequence")
        self.assertEqual(meta["recorder_version"], "test")
        self.assertEqual(meta["sequence_id"], "00000000-0000-4000-8000-000000000000")
        self.assertEqual(meta["recorded_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(meta["developer_notes"], "Тест UTF-8: привет")
        self.assertEqual(meta["sequence_duration_sec"], "0.333")

    def test_golden_frame_data(self):
        """Verify frame data formula: (f*7 + c*3 + k) % 256."""
        frames = read_frames(GOLDEN_PATH, 0, 10)
        for f_idx, frame in enumerate(frames):
            for i, b in enumerate(frame):
                c, k = i // 4, i % 4
                expected = (f_idx * 7 + c * 3 + k) % 256
                self.assertEqual(
                    b, expected,
                    "frame %d, chip %d, channel %d: got %d expected %d"
                    % (f_idx, c, k, b, expected),
                )

    def test_regenerate_matches_committed(self):
        """Regenerate golden in a temp dir and compare bytes with the committed file."""
        with tempfile.TemporaryDirectory() as tmp:
            regen_path = os.path.join(tmp, "regen.plsq")
            make_golden.generate(regen_path)
            with open(GOLDEN_PATH, "rb") as f:
                committed = f.read()
            with open(regen_path, "rb") as f:
                regenerated = f.read()
            self.assertEqual(committed, regenerated, "Regenerated file differs from committed golden")


# ---------------------------------------------------------------------------
# validate_plsq tool tests
# ---------------------------------------------------------------------------

class TestValidateTool(unittest.TestCase):

    def test_valid_file_exit_0(self):
        out = io.StringIO()
        code = val_tool.run(GOLDEN_PATH, out=out)
        self.assertEqual(code, 0)
        self.assertIn("Status: OK", out.getvalue())

    def test_invalid_file_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.plsq")
            with open(bad, "wb") as f:
                f.write(b"NOPE" + b"\x00" * 200)
            out = io.StringIO()
            code = val_tool.run(bad, out=out)
            self.assertEqual(code, 1)

    def test_nonexistent_file_exit_1(self):
        out = io.StringIO()
        code = val_tool.run("/nonexistent/path/x.plsq", out=out)
        self.assertEqual(code, 1)

    def test_output_contains_header_fields(self):
        out = io.StringIO()
        val_tool.run(GOLDEN_PATH, out=out)
        text = out.getvalue()
        self.assertIn("chips_count", text)
        self.assertIn("fps", text)
        self.assertIn("model_name", text)
        self.assertIn("Conformance", text)


# ---------------------------------------------------------------------------
# gen_synthetic tests
# ---------------------------------------------------------------------------

class TestGenSynthetic(unittest.TestCase):

    def _make_and_validate(self, chips, fps, seconds, pattern="ramp", **kw):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "synth.plsq")
            gen_synthetic.generate(out, chips, fps, seconds, pattern=pattern, quiet=True, **kw)
            problems = [p for p in validate(out) if not p.startswith("TrailingData")]
            self.assertEqual(problems, [], "pattern=%s chips=%d: %r" % (pattern, chips, problems))
            h, meta = read_info(out)
            return h, meta

    def test_ramp_104_chips(self):
        h, meta = self._make_and_validate(104, 30, 2.0, pattern="ramp")
        self.assertEqual(h["chips_count"], 104)
        self.assertEqual(h["fps"], 30)
        self.assertEqual(h["frame_count"], 60)

    def test_rainbow_pattern(self):
        h, _ = self._make_and_validate(128, 60, 1.0, pattern="rainbow")
        self.assertEqual(h["chips_count"], 128)

    def test_static_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "static.plsq")
            gen_synthetic.generate(out, 10, 30, 1.0, pattern="static", quiet=True)
            frames = read_frames(out, 0, 30)
            # All frames identical for static
            self.assertTrue(all(f == frames[0] for f in frames))

    def test_two_universes(self):
        h, _ = self._make_and_validate(130, 30, 1.0)
        self.assertEqual(h["universes_count"], 2)

    def test_sequence_name_is_synthetic(self):
        _, meta = self._make_and_validate(10, 30, 1.0)
        self.assertEqual(meta["sequence_name"], "synthetic")

    def test_large_file_validates_clean(self):
        """64,000 chips x 60 fps x 2 s — validates clean (smaller than 10 s for fast tests)."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "large.plsq")
            gen_synthetic.generate(out, 64000, 60, 2.0, quiet=True)
            problems = [p for p in validate(out) if not p.startswith("TrailingData")]
            self.assertEqual(problems, [])
            h, _ = read_info(out)
            self.assertEqual(h["chips_count"], 64000)
            self.assertEqual(h["frame_count"], 120)

    def test_rotation_produces_distinct_frames(self):
        """Ramp/rainbow patterns with rotation: adjacent frames differ."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rot.plsq")
            gen_synthetic.generate(out, 10, 30, 2.0, pattern="ramp", quiet=True)
            f0, f1 = read_frames(out, 0, 2)
            self.assertNotEqual(f0, f1)


# ---------------------------------------------------------------------------
# convert_json_to_plsq tests
# ---------------------------------------------------------------------------

class TestConvertJsonToPlsq(unittest.TestCase):

    def _make_frames(self, chips, count):
        stride = chips * 4
        return [bytes((f * 7 + i) % 256 for i in range(stride)) for f in range(count)]

    def test_round_trip_104_chips(self):
        """Convert JSON → PLSQ, verify bytes match round(v*255)."""
        chips, fps, count = 104, 30, 10
        original_frames = self._make_frames(chips, count)

        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "seq.json")
            plsq_path = os.path.join(tmp, "seq.plsq")

            _write_json(json_path, chips, fps, original_frames)
            frame_count, stride = conv.convert(json_path, plsq_path, quiet=True)

            self.assertEqual(frame_count, count)
            self.assertEqual(stride, chips * 4)

            # Read back and compare byte-exact
            result_frames = read_frames(plsq_path, 0, count)
            for f_idx, (orig, result) in enumerate(zip(original_frames, result_frames)):
                for i, (ob, rb) in enumerate(zip(orig, result)):
                    # orig byte -> JSON float -> back to byte
                    expected = round(round(ob / 255.0, 3) * 255)
                    self.assertEqual(
                        rb, expected,
                        "frame %d byte %d: got %d expected %d" % (f_idx, i, rb, expected),
                    )

    def test_converted_validates_clean(self):
        chips, fps, count = 104, 30, 20
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "seq.json")
            plsq_path = os.path.join(tmp, "seq.plsq")
            _write_json(json_path, chips, fps, self._make_frames(chips, count))
            conv.convert(json_path, plsq_path, quiet=True)
            problems = [p for p in validate(plsq_path) if not p.startswith("TrailingData")]
            self.assertEqual(problems, [])

    def test_meta_defaults_applied(self):
        """Old-format JSON: missing keys get sensible defaults."""
        chips, fps, count = 10, 30, 5
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "old.json")
            plsq_path = os.path.join(tmp, "out.plsq")
            _write_json(json_path, chips, fps, self._make_frames(chips, count))
            conv.convert(json_path, plsq_path, quiet=True)
            _, meta = read_info(plsq_path)
            self.assertEqual(meta["input_mapping_start_universe"], "1")
            self.assertEqual(meta["input_mapping_start_channel"], "1")
            self.assertEqual(meta["input_mapping_chips_per_universe"], "128")
            self.assertNotEqual(meta["sequence_id"], "")

    def test_new_format_meta_preserved(self):
        """New-format JSON with extra mapping keys: values are carried over."""
        chips, fps, count = 10, 60, 3
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "new.json")
            plsq_path = os.path.join(tmp, "out.plsq")
            _write_json(
                json_path, chips, fps, self._make_frames(chips, count),
                meta_extra={
                    "input_mapping_start_universe": "5",
                    "input_mapping_start_channel": "1",
                    "input_mapping_chips_per_universe": "128",
                    "developer_notes": "preserved",
                },
            )
            conv.convert(json_path, plsq_path, quiet=True)
            h, meta = read_info(plsq_path)
            self.assertEqual(h["start_universe"], 5)
            self.assertEqual(meta["developer_notes"], "preserved")

    def test_wrong_channels_raises(self):
        """3 channels per chip should raise ValueError."""
        chips, fps, count = 10, 30, 3
        stride = chips * 3  # 3 channels
        frames = [bytes(stride) for _ in range(count)]
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "rgb.json")
            # Build JSON manually with 3-channel stride
            flat = [0.0] * (count * stride)
            obj = {
                "model_name": "X", "model_chips_tag": "x", "model_chips_count": str(chips),
                "sequence_name": "", "sequence_version": "", "sequence_duration_sec": "1.0",
                "sequence_fps": str(fps), "sequence_frame_count": str(count),
                "sequence_frame_stride": str(stride), "developer_notes": "",
                "sequence": {"data": flat},
            }
            with open(json_path, "w") as f:
                json.dump(obj, f)
            with self.assertRaises(ValueError):
                conv.convert(json_path, os.path.join(tmp, "out.plsq"), quiet=True)

    def test_180_frames_104_chips(self):
        """Simulate a typical old recording: 104 chips x 4 ch x 180 frames at 30 fps."""
        chips, fps, count = 104, 30, 180
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "big.json")
            plsq_path = os.path.join(tmp, "big.plsq")
            _write_json(json_path, chips, fps, self._make_frames(chips, count))
            frame_count, stride = conv.convert(json_path, plsq_path, quiet=True)
            self.assertEqual(frame_count, 180)
            self.assertEqual(stride, 416)
            problems = [p for p in validate(plsq_path) if not p.startswith("TrailingData")]
            self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
