#!/usr/bin/env python3
"""
Validate a PLSQ file and print its header and meta fields.

Usage:
    python tools/validate_plsq.py <file.plsq>

Exit code 0 if the file is valid, 1 if there are problems.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plsq import read_info, validate, PlsqError, META_KEYS


def _fmt(label, value, width=30):
    return "  %-*s %s" % (width, label + ":", value)


def run(path, out=None):
    """Validate path, print results to out (default stdout). Returns exit code."""
    if out is None:
        out = sys.stdout

    if not os.path.exists(path):
        print("ERROR: file not found: %s" % path, file=out)
        return 1

    file_size = os.path.getsize(path)
    print("=== %s ===" % os.path.basename(path), file=out)
    print("  Size: %d bytes (%.2f MB)" % (file_size, file_size / 1e6), file=out)
    print(file=out)

    # Try to read header + meta
    try:
        h, meta = read_info(path)
        _print_header(h, out)
        _print_meta(meta, out)
    except PlsqError as exc:
        print("Cannot read header/meta: [%s] %s" % (exc.code, exc), file=out)

    # Full validation for all problems
    problems = validate(path)
    warnings = [p for p in problems if p.startswith("TrailingData")]
    errors = [p for p in problems if not p.startswith("TrailingData")]

    print(file=out)
    if not errors:
        print("Status: OK", file=out)
    else:
        print("Status: %d problem(s):" % len(errors), file=out)
        for i, p in enumerate(errors, 1):
            print("  [%d] %s" % (i, p), file=out)

    for w in warnings:
        print("Warning: %s" % w, file=out)

    return 0 if not errors else 1


def _print_header(h, out):
    print("Header:", file=out)
    fields = [
        ("chips_count",        h["chips_count"]),
        ("channels_per_chip",  h["channels_per_chip"]),
        ("fps",                h["fps"]),
        ("frame_count",        h["frame_count"]),
        ("frame_stride",       h["frame_stride"]),
        ("flags",              "0x%08x" % h["flags"]),
        ("start_universe",     h["start_universe"]),
        ("start_channel",      h["start_channel"]),
        ("chips_per_universe", h["chips_per_universe"]),
        ("universes_count",    h["universes_count"]),
        ("payload_offset",     h["payload_offset"]),
        ("payload_size",       h["payload_size"]),
        ("meta_offset",        h["meta_offset"]),
        ("meta_size",          h["meta_size"]),
        ("format_version",     h["format_version"]),
        ("header_size",        h["header_size"]),
    ]
    for label, value in fields:
        print(_fmt(label, value), file=out)


def _print_meta(meta, out):
    print(file=out)
    print("Meta:", file=out)
    for k in META_KEYS:
        v = meta.get(k, "(missing)")
        print(_fmt(k, repr(v) if "\n" in str(v) else v), file=out)
    extra = [k for k in meta if k not in META_KEYS]
    for k in extra:
        print(_fmt(k + " [extra]", meta[k]), file=out)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Validate a PLSQ file.")
    p.add_argument("file", help="Path to .plsq file")
    args = p.parse_args(argv)
    sys.exit(run(args.file))


if __name__ == "__main__":
    main()
