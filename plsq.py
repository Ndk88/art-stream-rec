"""
PLSQ format reader, writer, and validator. No tkinter, stdlib only.

Reference: docs/PLSQ_SPEC.md
"""

import json
import os
import struct
import uuid
import zlib
from datetime import datetime

from mapping import build_spans, count_universes, effective_chips_per_universe

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC = b"PLSQ"
FORMAT_VERSION = 1
HEADER_SIZE = 128
META_OFFSET = 128
META_CAPACITY = 65536
PAYLOAD_OFFSET = 69632  # 17 * 4096
CHANNELS_PER_CHIP = 4
FLAG_COMPRESSED = 1 << 0
FLAG_FINALIZED = 1 << 1

# struct layout: 128 bytes, little-endian
# Fields: magic, format_version, header_size, flags, chips_count,
#         channels_per_chip, fps, frame_count, frame_stride, reserved,
#         payload_offset, payload_size, meta_offset, meta_size, meta_crc32,
#         start_universe, start_channel, chips_per_universe, universes_count,
#         (52 padding bytes), header_crc32
_HDR_FMT = struct.Struct("<4sHHIIHHIIIQQQIIHHHH52xI")
assert _HDR_FMT.size == HEADER_SIZE

_HDR_FIELDS = (
    "magic", "format_version", "header_size", "flags",
    "chips_count", "channels_per_chip", "fps",
    "frame_count", "frame_stride", "reserved",
    "payload_offset", "payload_size", "meta_offset",
    "meta_size", "meta_crc32",
    "start_universe", "start_channel", "chips_per_universe", "universes_count",
    "header_crc32",
)

# Required meta keys in spec order (section 5)
META_KEYS = [
    "file_format", "format_version", "sequence_id", "recorded_at",
    "recorder_version", "model_name", "model_chips_tag", "model_chips_count",
    "sequence_name", "sequence_version", "sequence_duration_sec", "sequence_fps",
    "sequence_frame_count", "sequence_frame_stride",
    "input_mapping_start_universe", "input_mapping_start_channel",
    "input_mapping_channels_per_chip", "input_mapping_chips_per_universe",
    "input_mapping_universes_count",
    "post_skip_frames", "post_loop_blend_frames", "recorded_frames_raw",
    "missing_frames", "late_frames", "partial_source_frames", "lost_packets",
    "developer_notes",
]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class PlsqError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# CRC-32
# ---------------------------------------------------------------------------

def _crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Header pack / unpack
# ---------------------------------------------------------------------------

def _pack_header(h):
    """Pack header dict into 128 bytes, computing and embedding header_crc32."""
    body = _HDR_FMT.pack(
        h["magic"],
        h["format_version"],
        h["header_size"],
        h["flags"],
        h["chips_count"],
        h["channels_per_chip"],
        h["fps"],
        h["frame_count"],
        h["frame_stride"],
        h.get("reserved", 0),
        h["payload_offset"],
        h["payload_size"],
        h["meta_offset"],
        h["meta_size"],
        h["meta_crc32"],
        h["start_universe"],
        h["start_channel"],
        h["chips_per_universe"],
        h["universes_count"],
        0,  # header_crc32 placeholder
    )
    crc = _crc32(body[:124])
    return body[:124] + struct.pack("<I", crc)


def _unpack_header(raw):
    """Unpack 128 raw bytes into a header dict. Does not validate fields."""
    vals = _HDR_FMT.unpack(raw)
    return dict(zip(_HDR_FIELDS, vals))


# ---------------------------------------------------------------------------
# Meta encode / decode
# ---------------------------------------------------------------------------

def _encode_meta(meta_dict):
    return json.dumps(meta_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_meta(data):
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlsqError("MetaParseError", str(exc))
    if not isinstance(obj, dict):
        raise PlsqError("MetaParseError", "meta is not a JSON object")
    for k, v in obj.items():
        if not isinstance(v, str):
            raise PlsqError(
                "MetaParseError", "meta value for key %r is not a string" % k
            )
    return obj


# ---------------------------------------------------------------------------
# build_meta
# ---------------------------------------------------------------------------

def _utc_offset_str(dt):
    off = dt.utcoffset()
    if off is None:
        return "+00:00"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    h, m = divmod(total // 60, 60)
    return "%s%02d:%02d" % (sign, h, m)


def build_meta(
    *,
    file_format="plsq",
    sequence_id=None,
    recorded_at=None,
    recorder_version="1.0.0",
    model_name="",
    model_chips_tag="",
    model_chips_count,
    sequence_name="",
    sequence_version="",
    fps,
    frame_count,
    frame_stride,
    start_universe,
    start_channel,
    channels_per_chip=CHANNELS_PER_CHIP,
    chips_per_universe,
    universes_count,
    post_skip_frames=0,
    post_loop_blend_frames=0,
    recorded_frames_raw=None,
    missing_frames=0,
    late_frames=0,
    partial_source_frames=0,
    lost_packets=0,
    developer_notes="",
):
    """Return the 27-key meta dict in spec order. All values are strings."""
    if sequence_id is None:
        sequence_id = str(uuid.uuid4())
    if recorded_at is None:
        now = datetime.now().astimezone()
        recorded_at = now.strftime("%Y-%m-%dT%H:%M:%S") + _utc_offset_str(now)
    if recorded_frames_raw is None:
        recorded_frames_raw = frame_count

    duration_sec = str(round(int(frame_count) / int(fps), 3))
    return {
        "file_format": str(file_format),
        "format_version": "1",
        "sequence_id": str(sequence_id),
        "recorded_at": str(recorded_at),
        "recorder_version": str(recorder_version),
        "model_name": str(model_name),
        "model_chips_tag": str(model_chips_tag),
        "model_chips_count": str(int(model_chips_count)),
        "sequence_name": str(sequence_name),
        "sequence_version": str(sequence_version),
        "sequence_duration_sec": duration_sec,
        "sequence_fps": str(int(fps)),
        "sequence_frame_count": str(int(frame_count)),
        "sequence_frame_stride": str(int(frame_stride)),
        "input_mapping_start_universe": str(int(start_universe)),
        "input_mapping_start_channel": str(int(start_channel)),
        "input_mapping_channels_per_chip": str(int(channels_per_chip)),
        "input_mapping_chips_per_universe": str(int(chips_per_universe)),
        "input_mapping_universes_count": str(int(universes_count)),
        "post_skip_frames": str(int(post_skip_frames)),
        "post_loop_blend_frames": str(int(post_loop_blend_frames)),
        "recorded_frames_raw": str(int(recorded_frames_raw)),
        "missing_frames": str(int(missing_frames)),
        "late_frames": str(int(late_frames)),
        "partial_source_frames": str(int(partial_source_frames)),
        "lost_packets": str(int(lost_packets)),
        "developer_notes": str(developer_notes),
    }


# ---------------------------------------------------------------------------
# PlsqWriter  (streaming)
# ---------------------------------------------------------------------------

class PlsqWriter:
    """Streaming PLSQ writer. Use open() -> append_frame() * N -> finalize()."""

    def __init__(self):
        self._fp = None
        self._part_path = None
        self._chips = 0
        self._fps = 0
        self._frame_stride = 0
        self._frame_count = 0
        self._start_universe = 0
        self._start_channel = 1
        self._chips_per_universe = 128
        self._universes_count = 0

    def open(self, part_path, chips, fps, start_universe, start_channel, chips_per_universe):
        """Create the .part file and write a placeholder header."""
        eff_cpu = effective_chips_per_universe(chips_per_universe)
        universes_count = count_universes(start_universe, start_channel, eff_cpu, chips)
        frame_stride = chips * CHANNELS_PER_CHIP

        self._part_path = part_path
        self._chips = chips
        self._fps = fps
        self._frame_stride = frame_stride
        self._frame_count = 0
        self._start_universe = start_universe
        self._start_channel = start_channel
        self._chips_per_universe = eff_cpu
        self._universes_count = universes_count

        self._fp = open(part_path, "w+b")
        placeholder = {
            "magic": MAGIC,
            "format_version": FORMAT_VERSION,
            "header_size": HEADER_SIZE,
            "flags": 0,  # FINALIZED = 0
            "chips_count": chips,
            "channels_per_chip": CHANNELS_PER_CHIP,
            "fps": fps,
            "frame_count": 0,
            "frame_stride": frame_stride,
            "reserved": 0,
            "payload_offset": PAYLOAD_OFFSET,
            "payload_size": 0,
            "meta_offset": META_OFFSET,
            "meta_size": 2,  # smallest valid placeholder
            "meta_crc32": 0,
            "start_universe": start_universe,
            "start_channel": start_channel,
            "chips_per_universe": eff_cpu,
            "universes_count": universes_count,
        }
        self._fp.write(_pack_header(placeholder))
        self._fp.seek(PAYLOAD_OFFSET)

    def append_frame(self, data):
        """Append one frame (bytes of length frame_stride) to the payload."""
        if len(data) != self._frame_stride:
            raise ValueError(
                "Frame size %d != expected %d" % (len(data), self._frame_stride)
            )
        self._fp.write(data)
        self._frame_count += 1

    def read_frames(self, start, count):
        """Read frames [start, start+count) from the open file. Returns list of bytes."""
        stride = self._frame_stride
        self._fp.seek(PAYLOAD_OFFSET + start * stride)
        raw = self._fp.read(count * stride)
        return [raw[i * stride:(i + 1) * stride] for i in range(count)]

    def rewrite_frames(self, start, frames):
        """Overwrite frames in place (used for loop-blend)."""
        self._fp.seek(PAYLOAD_OFFSET + start * self._frame_stride)
        for f in frames:
            self._fp.write(f)

    def finalize(self, meta_dict):
        """Write meta + final header with FINALIZED=1, flush, close."""
        meta_bytes = _encode_meta(meta_dict)
        meta_size = len(meta_bytes)
        if meta_size > META_CAPACITY:
            raise PlsqError("MetaOutOfBounds", "meta too large: %d bytes" % meta_size)
        meta_crc = _crc32(meta_bytes)

        # Write meta block, zero-pad to META_CAPACITY
        self._fp.seek(META_OFFSET)
        self._fp.write(meta_bytes)
        pad = META_CAPACITY - meta_size
        if pad > 0:
            self._fp.write(b"\x00" * pad)

        # Write final header
        frame_count = self._frame_count
        final_hdr = {
            "magic": MAGIC,
            "format_version": FORMAT_VERSION,
            "header_size": HEADER_SIZE,
            "flags": FLAG_FINALIZED,
            "chips_count": self._chips,
            "channels_per_chip": CHANNELS_PER_CHIP,
            "fps": self._fps,
            "frame_count": frame_count,
            "frame_stride": self._frame_stride,
            "reserved": 0,
            "payload_offset": PAYLOAD_OFFSET,
            "payload_size": frame_count * self._frame_stride,
            "meta_offset": META_OFFSET,
            "meta_size": meta_size,
            "meta_crc32": meta_crc,
            "start_universe": self._start_universe,
            "start_channel": self._start_channel,
            "chips_per_universe": self._chips_per_universe,
            "universes_count": self._universes_count,
        }
        self._fp.seek(0)
        self._fp.write(_pack_header(final_hdr))
        self._fp.flush()
        self._fp.close()
        self._fp = None

    def abort(self):
        """Close the file without finalizing; keep the .part file."""
        if self._fp:
            self._fp.close()
            self._fp = None


# ---------------------------------------------------------------------------
# Internal reader helpers
# ---------------------------------------------------------------------------

def _open_read_header(path):
    """Open file, read 128-byte header. Returns (fp, header_dict, raw_bytes).
    Raises PlsqError on IoError or Truncated."""
    try:
        fp = open(path, "rb")
    except OSError as exc:
        raise PlsqError("IoError", str(exc))
    try:
        raw = fp.read(HEADER_SIZE)
    except OSError as exc:
        fp.close()
        raise PlsqError("IoError", str(exc))
    if len(raw) < HEADER_SIZE:
        fp.close()
        raise PlsqError("Truncated", "file shorter than 128 bytes")
    return fp, _unpack_header(raw), raw


def _check_header(fp, h, raw, file_size):
    """Run spec checks in order after Truncated; raises PlsqError on first failure."""

    def _fail(code, msg=""):
        fp.close()
        raise PlsqError(code, msg)

    if h["magic"] != MAGIC:
        _fail("BadMagic", "magic=%r" % h["magic"])
    if h["format_version"] != FORMAT_VERSION:
        _fail("UnsupportedVersion", "format_version=%d" % h["format_version"])
    if h["header_size"] < HEADER_SIZE:
        _fail("BadHeaderSize", "header_size=%d" % h["header_size"])
    if h["header_crc32"] != _crc32(raw[:124]):
        _fail(
            "HeaderCrcMismatch",
            "stored=%08x computed=%08x" % (h["header_crc32"], _crc32(raw[:124])),
        )
    if h["flags"] & FLAG_COMPRESSED:
        _fail("UnsupportedFlags", "COMPRESSED is set")
    if not (h["flags"] & FLAG_FINALIZED):
        _fail("NotFinalized")
    if h["channels_per_chip"] != CHANNELS_PER_CHIP:
        _fail("UnsupportedLayout", "channels_per_chip=%d" % h["channels_per_chip"])

    # OutOfRange
    _out_of_range_raise(fp, h)

    # InconsistentHeader
    _inconsistent_raise(fp, h)

    # Truncated (payload)
    if file_size < h["payload_offset"] + h["payload_size"]:
        _fail(
            "Truncated",
            "file %d < payload_end %d"
            % (file_size, h["payload_offset"] + h["payload_size"]),
        )

    # MetaOutOfBounds
    if not (2 <= h["meta_size"] <= META_CAPACITY):
        _fail("MetaOutOfBounds", "meta_size=%d" % h["meta_size"])
    if h["meta_offset"] + h["meta_size"] > file_size:
        _fail("MetaOutOfBounds", "meta extends beyond file")


def _out_of_range_raise(fp, h):
    errs = []
    if not (1 <= h["chips_count"] <= 64000):
        errs.append("chips_count=%d" % h["chips_count"])
    if not (1 <= h["fps"] <= 240):
        errs.append("fps=%d" % h["fps"])
    if h["frame_count"] < 1:
        errs.append("frame_count=%d" % h["frame_count"])
    su, uc = h["start_universe"], h["universes_count"]
    if not (0 <= su <= 32767) or (uc > 0 and su + uc - 1 > 32767):
        errs.append("start_universe=%d universes_count=%d" % (su, uc))
    if not (1 <= h["start_channel"] <= 512):
        errs.append("start_channel=%d" % h["start_channel"])
    if not (1 <= h["chips_per_universe"] <= 128):
        errs.append("chips_per_universe=%d" % h["chips_per_universe"])
    if errs:
        fp.close()
        raise PlsqError("OutOfRange", "; ".join(errs))


def _inconsistent_raise(fp, h):
    errs = []
    if h["frame_stride"] != h["chips_count"] * 4:
        errs.append("frame_stride mismatch")
    if h["payload_size"] != h["frame_count"] * h["frame_stride"]:
        errs.append("payload_size mismatch")
    if h["payload_offset"] % 4096 != 0:
        errs.append("payload_offset not 4096-aligned")
    if h["payload_offset"] < h["meta_offset"] + h["meta_size"]:
        errs.append("payload_offset < meta_offset + meta_size")
    try:
        expected_uc = count_universes(
            h["start_universe"], h["start_channel"],
            h["chips_per_universe"], h["chips_count"],
        )
        if h["universes_count"] != expected_uc:
            errs.append(
                "universes_count=%d != computed %d" % (h["universes_count"], expected_uc)
            )
    except ValueError as exc:
        errs.append("mapping error: %s" % exc)
    if errs:
        fp.close()
        raise PlsqError("InconsistentHeader", "; ".join(errs))


def _read_meta(fp, h):
    """Read, CRC-check, parse, and validate meta. Returns meta dict."""
    fp.seek(h["meta_offset"])
    meta_bytes = fp.read(h["meta_size"])
    if len(meta_bytes) != h["meta_size"]:
        fp.close()
        raise PlsqError("IoError", "could not read meta")

    if _crc32(meta_bytes) != h["meta_crc32"]:
        fp.close()
        raise PlsqError(
            "MetaCrcMismatch",
            "stored=%08x computed=%08x" % (h["meta_crc32"], _crc32(meta_bytes)),
        )

    try:
        meta = _decode_meta(meta_bytes)
    except PlsqError:
        fp.close()
        raise

    for k in META_KEYS:
        if k not in meta:
            fp.close()
            raise PlsqError("MissingMetaKey", "missing: %r" % k)

    _meta_mismatch_raise(fp, h, meta)
    return meta


def _meta_mismatch_raise(fp, h, meta):
    pairs = [
        ("chips_count",        "model_chips_count"),
        ("fps",                "sequence_fps"),
        ("frame_count",        "sequence_frame_count"),
        ("frame_stride",       "sequence_frame_stride"),
        ("start_universe",     "input_mapping_start_universe"),
        ("start_channel",      "input_mapping_start_channel"),
        ("channels_per_chip",  "input_mapping_channels_per_chip"),
        ("chips_per_universe", "input_mapping_chips_per_universe"),
        ("universes_count",    "input_mapping_universes_count"),
    ]
    errs = []
    for hf, mk in pairs:
        if str(h[hf]) != meta.get(mk, ""):
            errs.append("%s vs %s" % (hf, mk))
    exp_dur = str(round(h["frame_count"] / h["fps"], 3))
    if meta.get("sequence_duration_sec") != exp_dur:
        errs.append("sequence_duration_sec")
    if meta.get("file_format") != "plsq":
        errs.append("file_format")
    if meta.get("format_version") != "1":
        errs.append("format_version")
    if errs:
        fp.close()
        raise PlsqError("MetaMismatch", "; ".join(errs))


# ---------------------------------------------------------------------------
# Public reader API
# ---------------------------------------------------------------------------

def read_info(path):
    """Read header + meta from a PLSQ file. Returns (header_dict, meta_dict).
    Raises PlsqError with the first spec error code encountered."""
    try:
        file_size = os.path.getsize(path)
    except OSError as exc:
        raise PlsqError("IoError", str(exc))
    fp, h, raw = _open_read_header(path)
    _check_header(fp, h, raw, file_size)
    meta = _read_meta(fp, h)
    fp.close()
    return h, meta


def read_frames(path, start, count):
    """Read `count` frames from `path` starting at index `start`.
    Returns a list of bytes objects of length frame_stride each."""
    h, _ = read_info(path)
    stride = h["frame_stride"]
    with open(path, "rb") as fp:
        fp.seek(h["payload_offset"] + start * stride)
        raw = fp.read(count * stride)
    return [raw[i * stride:(i + 1) * stride] for i in range(count)]


def validate(path):
    """Check a PLSQ file for all spec errors.
    Returns a list of problem strings (empty = valid). Never raises."""
    problems = []
    try:
        file_size = os.path.getsize(path)
        fp = open(path, "rb")
    except OSError as exc:
        problems.append("IoError: %s" % exc)
        return problems
    try:
        _validate_body(fp, file_size, problems)
    finally:
        fp.close()
    return problems


def _validate_body(fp, file_size, problems):
    try:
        raw = fp.read(HEADER_SIZE)
    except OSError as exc:
        problems.append("IoError: %s" % exc)
        return

    if len(raw) < HEADER_SIZE:
        problems.append("Truncated: file shorter than 128 bytes")
        return

    h = _unpack_header(raw)

    if h["magic"] != MAGIC:
        problems.append("BadMagic: %r" % h["magic"])
        return

    if h["format_version"] != FORMAT_VERSION:
        problems.append("UnsupportedVersion: %d" % h["format_version"])
        return

    if h["header_size"] < HEADER_SIZE:
        problems.append("BadHeaderSize: %d" % h["header_size"])

    computed_hcrc = _crc32(raw[:124])
    if h["header_crc32"] != computed_hcrc:
        problems.append(
            "HeaderCrcMismatch: stored=%08x computed=%08x"
            % (h["header_crc32"], computed_hcrc)
        )
        return  # can't trust field values; stop

    if h["flags"] & FLAG_COMPRESSED:
        problems.append("UnsupportedFlags: COMPRESSED is set")
    if not (h["flags"] & FLAG_FINALIZED):
        problems.append("NotFinalized")
    if h["channels_per_chip"] != CHANNELS_PER_CHIP:
        problems.append("UnsupportedLayout: channels_per_chip=%d" % h["channels_per_chip"])

    _validate_out_of_range(h, problems)
    _validate_inconsistent(h, problems)

    if file_size < h["payload_offset"] + h["payload_size"]:
        problems.append(
            "Truncated: file %d < payload_end %d"
            % (file_size, h["payload_offset"] + h["payload_size"])
        )

    meta_ok = True
    if not (2 <= h["meta_size"] <= META_CAPACITY):
        problems.append("MetaOutOfBounds: meta_size=%d" % h["meta_size"])
        meta_ok = False
    elif h["meta_offset"] + h["meta_size"] > file_size:
        problems.append("MetaOutOfBounds: meta extends beyond file")
        meta_ok = False

    if meta_ok:
        fp.seek(h["meta_offset"])
        meta_bytes = fp.read(h["meta_size"])
        computed_mcrc = _crc32(meta_bytes)
        if computed_mcrc != h["meta_crc32"]:
            problems.append(
                "MetaCrcMismatch: stored=%08x computed=%08x"
                % (h["meta_crc32"], computed_mcrc)
            )
        else:
            try:
                meta = _decode_meta(meta_bytes)
            except PlsqError as exc:
                problems.append("MetaParseError: %s" % exc)
                meta = None
            if meta is not None:
                for k in META_KEYS:
                    if k not in meta:
                        problems.append("MissingMetaKey: %r" % k)
                if not any(p.startswith("MissingMetaKey") for p in problems):
                    _validate_meta_mismatch(h, meta, problems)

    tail = h["payload_offset"] + h["payload_size"]
    if file_size > tail:
        problems.append("TrailingData: %d extra bytes" % (file_size - tail))


def _validate_out_of_range(h, problems):
    if not (1 <= h["chips_count"] <= 64000):
        problems.append("OutOfRange: chips_count=%d" % h["chips_count"])
    if not (1 <= h["fps"] <= 240):
        problems.append("OutOfRange: fps=%d" % h["fps"])
    if h["frame_count"] < 1:
        problems.append("OutOfRange: frame_count=%d" % h["frame_count"])
    su, uc = h["start_universe"], h["universes_count"]
    if not (0 <= su <= 32767) or (uc > 0 and su + uc - 1 > 32767):
        problems.append("OutOfRange: start_universe=%d universes_count=%d" % (su, uc))
    if not (1 <= h["start_channel"] <= 512):
        problems.append("OutOfRange: start_channel=%d" % h["start_channel"])
    if not (1 <= h["chips_per_universe"] <= 128):
        problems.append("OutOfRange: chips_per_universe=%d" % h["chips_per_universe"])


def _validate_inconsistent(h, problems):
    if h["frame_stride"] != h["chips_count"] * 4:
        problems.append("InconsistentHeader: frame_stride mismatch")
    if h["payload_size"] != h["frame_count"] * h["frame_stride"]:
        problems.append("InconsistentHeader: payload_size mismatch")
    if h["payload_offset"] % 4096 != 0:
        problems.append("InconsistentHeader: payload_offset not 4096-aligned")
    if h["payload_offset"] < h["meta_offset"] + h["meta_size"]:
        problems.append("InconsistentHeader: payload_offset < meta_offset + meta_size")
    try:
        if 1 <= h["chips_per_universe"] <= 128 and 1 <= h["chips_count"] <= 64000:
            expected_uc = count_universes(
                h["start_universe"], h["start_channel"],
                h["chips_per_universe"], h["chips_count"],
            )
            if h["universes_count"] != expected_uc:
                problems.append(
                    "InconsistentHeader: universes_count=%d != computed %d"
                    % (h["universes_count"], expected_uc)
                )
    except ValueError as exc:
        problems.append("InconsistentHeader: mapping error: %s" % exc)


def _validate_meta_mismatch(h, meta, problems):
    pairs = [
        ("chips_count",        "model_chips_count"),
        ("fps",                "sequence_fps"),
        ("frame_count",        "sequence_frame_count"),
        ("frame_stride",       "sequence_frame_stride"),
        ("start_universe",     "input_mapping_start_universe"),
        ("start_channel",      "input_mapping_start_channel"),
        ("channels_per_chip",  "input_mapping_channels_per_chip"),
        ("chips_per_universe", "input_mapping_chips_per_universe"),
        ("universes_count",    "input_mapping_universes_count"),
    ]
    for hf, mk in pairs:
        if str(h[hf]) != meta.get(mk, ""):
            problems.append("MetaMismatch: %s vs %s" % (hf, mk))
    if h["fps"] > 0:
        exp_dur = str(round(h["frame_count"] / h["fps"], 3))
        if meta.get("sequence_duration_sec") != exp_dur:
            problems.append(
                "MetaMismatch: sequence_duration_sec=%r expected %r"
                % (meta.get("sequence_duration_sec"), exp_dur)
            )
    if meta.get("file_format") != "plsq":
        problems.append("MetaMismatch: file_format=%r" % meta.get("file_format"))
    if meta.get("format_version") != "1":
        problems.append("MetaMismatch: format_version=%r" % meta.get("format_version"))
