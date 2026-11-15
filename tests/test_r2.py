"""
Tests for R2: write_json() producing the full 27-key meta, build_meta() integration,
3-universe block, key order, value types.

Run:  python -m unittest tests.test_r2
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from main import APPV, write_json
from plsq import META_KEYS, build_meta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(
    *,
    file_format="json",
    chips=104,
    fps=30,
    frame_count=180,
    start_universe=1,
    start_channel=1,
    chips_per_universe=128,
    universes_count=1,
    skip=0,
    blend=0,
    **kw,
):
    return build_meta(
        file_format=file_format,
        recorder_version=APPV,
        model_chips_count=chips,
        fps=fps,
        frame_count=frame_count,
        frame_stride=chips * 4,
        start_universe=start_universe,
        start_channel=start_channel,
        chips_per_universe=chips_per_universe,
        universes_count=universes_count,
        post_skip_frames=skip,
        post_loop_blend_frames=blend,
        **kw,
    )


def _frames(chips=104, count=3, fill=128):
    return [bytes([fill] * (chips * 4)) for _ in range(count)]


# ---------------------------------------------------------------------------
# write_json output structure
# ---------------------------------------------------------------------------

class TestWriteJsonStructure(unittest.TestCase):

    def _write(self, chips=104, universes_count=1, frame_count=3):
        meta = _meta(chips=chips, frame_count=frame_count, universes_count=universes_count)
        frames = _frames(chips, frame_count)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            write_json(path, meta, frames)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        finally:
            os.unlink(path)

    def test_output_is_valid_json(self):
        obj = self._write()
        self.assertIsInstance(obj, dict)

    def test_has_exactly_27_meta_keys_plus_sequence(self):
        obj = self._write()
        # 27 meta keys + "sequence"
        self.assertEqual(len(obj), 28)
        self.assertIn("sequence", obj)

    def test_all_27_meta_keys_present(self):
        obj = self._write()
        for k in META_KEYS:
            self.assertIn(k, obj, "missing key: %s" % k)

    def test_all_meta_values_are_strings(self):
        obj = self._write()
        for k in META_KEYS:
            self.assertIsInstance(obj[k], str, "key %s value is not a string" % k)

    def test_meta_keys_in_spec_order(self):
        """Keys appear in the file in spec §5 order before 'sequence'."""
        meta = _meta()
        frames = _frames()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            write_json(path, meta, frames)
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            # Extract the key order by scanning for quoted key names before "sequence"
            sequence_pos = raw.index('"sequence"')
            before_seq = raw[:sequence_pos]
            found = []
            for k in META_KEYS:
                pos = before_seq.find(json.dumps(k) + ":")
                if pos >= 0:
                    found.append((pos, k))
            found.sort()
            self.assertEqual([k for _, k in found], META_KEYS)
        finally:
            os.unlink(path)

    def test_file_format_is_json(self):
        obj = self._write()
        self.assertEqual(obj["file_format"], "json")

    def test_recorder_version_is_appv(self):
        obj = self._write()
        self.assertEqual(obj["recorder_version"], APPV)

    def test_sequence_data_present(self):
        obj = self._write(frame_count=5)
        self.assertIn("data", obj["sequence"])
        self.assertEqual(len(obj["sequence"]["data"]), 5 * 104 * 4)

    def test_frame_count_matches(self):
        obj = self._write(frame_count=7)
        self.assertEqual(obj["sequence_frame_count"], "7")

    def test_frame_stride_matches(self):
        obj = self._write(chips=104)
        self.assertEqual(obj["sequence_frame_stride"], str(104 * 4))

    def test_duration_sec_correct(self):
        obj = self._write(frame_count=180)
        self.assertEqual(obj["sequence_duration_sec"], str(round(180 / 30, 3)))

    def test_sequence_id_is_uuid_string(self):
        import re
        obj = self._write()
        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        self.assertRegex(obj["sequence_id"], uuid_re)

    def test_two_universes_allowed(self):
        """universes_count=2 must not raise."""
        obj = self._write(universes_count=2)
        self.assertEqual(obj["input_mapping_universes_count"], "2")

    def test_one_universe_allowed(self):
        obj = self._write(universes_count=1)
        self.assertEqual(obj["input_mapping_universes_count"], "1")


# ---------------------------------------------------------------------------
# 3-universe block (D3)
# ---------------------------------------------------------------------------

class TestWriteJsonThreeUniverseBlock(unittest.TestCase):

    def test_three_universes_raises_value_error(self):
        meta = _meta(universes_count=3)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                write_json(path, meta, _frames())
            self.assertIn("3", str(cm.exception))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_500_universes_raises(self):
        meta = _meta(universes_count=500, chips=64000)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(ValueError):
                write_json(path, meta, _frames(64000))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_error_does_not_create_file_content(self):
        """File must not be (partially) written when ValueError is raised."""
        meta = _meta(universes_count=3)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "out.json")
            with self.assertRaises(ValueError):
                write_json(path, meta, _frames())
            self.assertFalse(os.path.exists(path))


# ---------------------------------------------------------------------------
# build_meta() / write_json() roundtrip: values survive JSON serialization
# ---------------------------------------------------------------------------

class TestWriteJsonRoundtrip(unittest.TestCase):

    def test_104_chips_roundtrip(self):
        chips = 104
        meta = _meta(
            chips=chips, fps=30, frame_count=180,
            missing_frames=2, late_frames=1,
            partial_source_frames=0, lost_packets=0,
        )
        frames = _frames(chips, 180, fill=200)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            size = write_json(path, meta, frames)
            self.assertGreater(size, 0)
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
            self.assertEqual(obj["model_chips_count"], str(chips))
            self.assertEqual(obj["sequence_frame_count"], "180")
            self.assertEqual(obj["missing_frames"], "2")
            self.assertEqual(obj["late_frames"], "1")
            self.assertEqual(obj["partial_source_frames"], "0")
            self.assertEqual(obj["lost_packets"], "0")
            # Verify frame data: fill=200 → round(200/255, 3)
            expected_val = round(200 / 255.0, 3)
            first_val = float(obj["sequence"]["data"][0])
            self.assertAlmostEqual(first_val, expected_val, places=3)
        finally:
            os.unlink(path)

    def test_post_processing_fields(self):
        meta = _meta(
            frame_count=170, skip=5, blend=5,
            recorded_frames_raw=180,
        )
        frames = _frames(104, 170)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            write_json(path, meta, frames)
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
            self.assertEqual(obj["post_skip_frames"], "5")
            self.assertEqual(obj["post_loop_blend_frames"], "5")
            self.assertEqual(obj["recorded_frames_raw"], "180")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Blueprint-compat: keys that the old parser used keep their names and formats
# ---------------------------------------------------------------------------

class TestBlueprintCompat(unittest.TestCase):
    """
    Keys the original Blueprint parser consumed:
      model_name, model_chips_tag, model_chips_count,
      sequence_name, sequence_version,
      sequence_duration_sec, sequence_fps, sequence_frame_count,
      sequence_frame_stride, developer_notes,
      sequence.data (flat array in "sequence" sub-object)
    All must still be present and have the same string format as before.
    """

    def setUp(self):
        meta = _meta(
            chips=104, fps=30, frame_count=180,
            model_name="Crystal Links Model 1",
            model_chips_tag="crystal_links_model_1_chip",
            sequence_name="Test Loop",
            sequence_version="1.0",
            developer_notes="Notes here",
        )
        frames = _frames(104, 180, fill=0)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self._path = f.name
        write_json(self._path, meta, frames)
        with open(self._path, encoding="utf-8") as fh:
            self.obj = json.load(fh)

    def tearDown(self):
        os.unlink(self._path)

    def test_model_name(self):
        self.assertEqual(self.obj["model_name"], "Crystal Links Model 1")

    def test_model_chips_tag(self):
        self.assertEqual(self.obj["model_chips_tag"], "crystal_links_model_1_chip")

    def test_model_chips_count_is_string_int(self):
        self.assertEqual(self.obj["model_chips_count"], "104")

    def test_sequence_fps_is_string_int(self):
        self.assertEqual(self.obj["sequence_fps"], "30")

    def test_sequence_frame_count_is_string_int(self):
        self.assertEqual(self.obj["sequence_frame_count"], "180")

    def test_sequence_frame_stride_is_string_int(self):
        self.assertEqual(self.obj["sequence_frame_stride"], str(104 * 4))

    def test_sequence_duration_sec_is_string_float(self):
        v = float(self.obj["sequence_duration_sec"])
        self.assertAlmostEqual(v, 6.0, places=3)

    def test_sequence_data_subobject(self):
        self.assertIn("sequence", self.obj)
        self.assertIn("data", self.obj["sequence"])

    def test_sequence_data_values_in_0_1(self):
        vals = [float(x) for x in self.obj["sequence"]["data"]]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in vals))


if __name__ == "__main__":
    unittest.main()
