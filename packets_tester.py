#!/usr/bin/env python3
"""
Test Art-Net source: sends RGBW frames at a fixed rate and prints a log.

  python packets_tester.py                           -> 127.0.0.1:6454, 104 chips, start-universe 1
  python packets_tester.py 127.0.0.1 6454 104 1     -> host port chips start-universe
  python packets_tester.py 127.0.0.1 6454 64000 0 --fps 60 --procs 4

Always 4 channels per chip (RGBW). Universe layout follows the same mapping as the recorder.

New options: --fps, --chips-per-universe, --pattern, --duration, --artsync, --stagger-ms, --procs
"""

import argparse
import multiprocessing
import os
import socket
import sys
import time

LOG_EVERY = 1.0        # seconds between status lines
RING_SECONDS = 2       # seconds of frames to precompute in the ring
SEQ_OFFSET = 12        # byte offset of the sequence field in ArtDMX header

# ArtSync packet (OpCode 0x5200, 14 bytes)
ARTSYNC_PKT = b"Art-Net\x00" + bytes([0x00, 0x52, 0x00, 0x0E, 0x00, 0x00])

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mapping import build_spans, effective_chips_per_universe


# ---------------------------------------------------------------------------
# Pattern and ring helpers
# ---------------------------------------------------------------------------

def _build_base(pattern, chips, stride):
    if pattern == "static":
        return bytes([128]) * stride
    if pattern == "ramp":
        unit = bytes(range(256))
        return (unit * (stride // 256 + 1))[:stride]
    # rainbow: R/G/B cycle by chip index, W = 0
    base = bytearray(stride)
    for i in range(chips):
        base[i * 4]     = (i * 7) % 256
        base[i * 4 + 1] = (i * 5) % 256
        base[i * 4 + 2] = (i * 3) % 256
    return bytes(base)


def _build_ring(pattern, chips, stride, ring_size):
    """Return a list of `ring_size` bytes objects (frames), derived by rotating the base."""
    base = _build_base(pattern, chips, stride)
    if pattern == "static":
        return [base] * ring_size  # all frames reference the same object
    step = 4  # rotate by one chip (4 bytes) per frame
    double = base + base
    mv = memoryview(double)
    frames = []
    for i in range(ring_size):
        off = (i * step) % stride
        frames.append(bytes(mv[off:off + stride]))
    return frames


# ---------------------------------------------------------------------------
# ArtDMX packet helpers
# ---------------------------------------------------------------------------

def _make_artdmx_bytearray(universe, payload):
    """Build an ArtDMX bytearray. Sequence byte (offset 12) is left at 0."""
    n = len(payload)
    if n % 2:
        payload = payload + b"\x00"  # Art-Net requires even DMX length
        n += 1
    pkt = bytearray(18 + n)
    pkt[0:8] = b"Art-Net\x00"
    pkt[8] = 0x00  # OpCode 0x5000 LSB
    pkt[9] = 0x50  # OpCode 0x5000 MSB
    pkt[10] = 0    # Protocol version high
    pkt[11] = 14   # Protocol version low
    # pkt[12] = 0  sequence placeholder (updated at send time)
    # pkt[13] = 0  physical port
    pkt[14] = universe & 0xFF
    pkt[15] = (universe >> 8) & 0x7F
    pkt[16] = n >> 8
    pkt[17] = n & 0xFF
    pkt[18:] = payload
    return pkt


def _precompute_packets(spans, ring):
    """Return ring_pkts[f][s] = bytearray for frame f, span s.
    Sequence byte at offset 12 is a mutable placeholder; caller sets it before each send."""
    result = []
    for frame in ring:
        mv = memoryview(frame)
        frame_pkts = []
        for uni, ch, n_ch, off in spans:
            payload = bytes(mv[off:off + n_ch])
            frame_pkts.append(_make_artdmx_bytearray(uni, payload))
        result.append(frame_pkts)
    return result


# ---------------------------------------------------------------------------
# Core send loop (runs in main process and in worker processes)
# ---------------------------------------------------------------------------

def _send_loop(spans, ring_pkts, ring_size, sock, addr, fps, duration,
               stagger_sec, use_artsync, t0, log_fn=None):
    """Drift-free send loop. Returns (sent, send_errors)."""
    seqs = [1] * len(spans)   # per-span sequence numbers, 1..255 wrapping, skip 0
    period = 1.0 / fps
    lag_warn = period * 1.5    # warn if >1.5 periods late
    n = sent = errors = 0
    win_sent = win_errors = 0
    win_start = t0

    # Wait until the shared start time
    dt = t0 - time.perf_counter()
    if dt > 0:
        time.sleep(dt)

    while True:
        target = t0 + n * period

        # Sleep-then-spin for precise timing
        slack = target - time.perf_counter()
        if slack > 0.002:
            time.sleep(slack - 0.002)
        while time.perf_counter() < target:
            pass

        lag = time.perf_counter() - target
        if lag > lag_warn and log_fn:
            log_fn(
                "WARNING: %.1f ms behind schedule (%d universes, stagger %.1f ms/uni)"
                % (lag * 1000, len(spans), stagger_sec * 1000)
            )

        # Send all spans for this frame
        f = n % ring_size
        frame_pkts = ring_pkts[f]
        for s_idx in range(len(spans)):
            pkt = frame_pkts[s_idx]
            pkt[SEQ_OFFSET] = seqs[s_idx]  # update sequence byte in place
            try:
                sock.sendto(pkt, addr)
                sent += 1
                win_sent += 1
            except OSError as exc:
                errors += 1
                win_errors += 1
                if log_fn:
                    log_fn("send error: %s" % exc)
            seqs[s_idx] = seqs[s_idx] % 255 + 1   # 1 → 2 → … → 255 → 1
            if stagger_sec > 0:
                time.sleep(stagger_sec)

        if use_artsync:
            try:
                sock.sendto(ARTSYNC_PKT, addr)
                sent += 1
                win_sent += 1
            except OSError:
                errors += 1

        n += 1

        # Periodic log (main process only)
        if log_fn:
            now = time.perf_counter()
            if now - win_start >= LOG_EVERY:
                elapsed = now - win_start
                log_fn(
                    "sent %d (%.1f pkt/s x %d uni) | total %d | send errors %d (+%d)"
                    % (win_sent, win_sent / elapsed, len(spans), sent, errors, win_errors)
                )
                win_sent = win_errors = 0
                win_start = now

        # Stop after duration
        if duration > 0 and time.perf_counter() - t0 >= duration:
            break

    return sent, errors


# ---------------------------------------------------------------------------
# Worker process entry (must be module-level for Windows multiprocessing)
# ---------------------------------------------------------------------------

def _worker_entry(spans, pattern, chips, stride, ring_size, addr, fps,
                  duration, stagger_ms, t0):
    """Entry point for worker processes spawned by --procs."""
    ring = _build_ring(pattern, chips, stride, ring_size)
    ring_pkts = _precompute_packets(spans, ring)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _send_loop(
            spans, ring_pkts, ring_size, sock, addr, fps, duration,
            stagger_ms / 1000.0, False, t0,
        )
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Test Art-Net source: sends RGBW frames at a fixed rate and prints a log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("host",    nargs="?", default="127.0.0.1")
    ap.add_argument("port",    nargs="?", type=int, default=6454)
    ap.add_argument("chips",   nargs="?", type=int, default=104,
                    help="Total chips (1..64000)")
    ap.add_argument("universe", nargs="?", type=int, default=1,
                    help="Start universe (Art-Net port-address, 0-based)")
    ap.add_argument("--fps", type=int, choices=[30, 60], default=60,
                    help="Packets per second per universe (default 60)")
    ap.add_argument("--chips-per-universe", type=int, default=0, metavar="N",
                    help="Chips per universe, 0 = auto (128)")
    ap.add_argument("--pattern", choices=["rainbow", "ramp", "static"], default="rainbow",
                    help="Colour pattern (default rainbow)")
    ap.add_argument("--duration", type=float, default=0.0, metavar="SECONDS",
                    help="Stop after this many seconds (0 = run forever)")
    ap.add_argument("--artsync", action="store_true",
                    help="Send ArtSync (OpCode 0x5200) after all universes of each frame")
    ap.add_argument("--stagger-ms", type=float, default=0.0, metavar="MS",
                    help="Delay (ms) between universe sends within a frame")
    ap.add_argument("--procs", type=int, default=1,
                    help="Split universes across N processes (default 1)")
    args = ap.parse_args(argv)

    # --- Validate ---
    if not (1 <= args.port <= 65535):
        _log("ERROR: port must be 1..65535"); return 2
    if not (1 <= args.chips <= 64000):
        _log("ERROR: chips must be 1..64000"); return 2
    if not (0 <= args.universe <= 32767):
        _log("ERROR: universe must be 0..32767"); return 2
    if args.procs < 1:
        _log("ERROR: procs must be >= 1"); return 2
    if args.stagger_ms < 0:
        _log("ERROR: stagger-ms must be >= 0"); return 2

    try:
        ip = socket.gethostbyname(args.host)
    except OSError as exc:
        _log("ERROR: cannot resolve '%s': %s" % (args.host, exc)); return 1

    addr = (ip, args.port)
    chips = args.chips
    fps = args.fps
    eff_cpu = effective_chips_per_universe(args.chips_per_universe)
    stride = chips * 4
    ring_size = fps * RING_SECONDS

    try:
        spans = build_spans(args.universe, 1, 4, chips, eff_cpu)
    except ValueError as exc:
        _log("ERROR: layout: %s" % exc); return 2

    universes_count = len(spans)
    stagger_total_ms = args.stagger_ms * universes_count
    total_pkt_rate = universes_count * fps + (fps if args.artsync else 0)

    _log(
        "Target %s:%d | chips %d | universes %d (U%d-U%d, %d chips/uni) | %d fps | %s"
        % (ip, args.port, chips, universes_count,
           spans[0][0], spans[-1][0], eff_cpu, fps, args.pattern)
    )
    if args.artsync:
        _log("ArtSync: enabled (OpCode 0x5200 after each frame)")
    if args.stagger_ms > 0:
        frame_ms = 1000.0 / fps
        _log("Stagger: %.2f ms/uni × %d uni = %.1f ms/frame (frame = %.1f ms)"
             % (args.stagger_ms, universes_count, stagger_total_ms, frame_ms))
        if stagger_total_ms > frame_ms:
            _log("WARNING: stagger total %.1f ms > frame %.1f ms — sender will lag"
                 % (stagger_total_ms, frame_ms))
    if args.procs > 1:
        _log("Multi-process: %d processes" % args.procs)
    if args.duration > 0:
        _log("Duration: %.1f s" % args.duration)
    _log("Expected: ~%d pkt/s | Ctrl+C to stop" % total_pkt_rate)

    # --- Build ring ---
    _log("Precomputing frame ring (%d frames)..." % ring_size)
    ring = _build_ring(args.pattern, chips, stride, ring_size)

    # --- Split spans for multi-process ---
    startup_secs = 0.1 if args.procs == 1 else 1.5
    t0 = time.perf_counter() + startup_secs

    workers = []
    if args.procs > 1:
        n_procs = min(args.procs, universes_count)
        g = (universes_count + n_procs - 1) // n_procs
        groups = [spans[i:i + g] for i in range(0, universes_count, g)]
        # spawn all but the last group as worker processes
        for grp in groups[:-1]:
            p = multiprocessing.Process(
                target=_worker_entry,
                args=(grp, args.pattern, chips, stride, ring_size,
                      addr, fps, args.duration, args.stagger_ms, t0),
                daemon=True,
            )
            p.start()
            workers.append(p)
        main_spans = groups[-1]
        use_artsync = args.artsync  # only main sends ArtSync
    else:
        main_spans = spans
        use_artsync = args.artsync

    # --- Precompute packets for main process ---
    ring_pkts = _precompute_packets(main_spans, ring)

    # --- Open socket ---
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        _log("ERROR: cannot create UDP socket: %s" % exc); return 1

    _log("Sending...")
    sent = errors = 0
    try:
        sent, errors = _send_loop(
            main_spans, ring_pkts, ring_size, sock, addr, fps, args.duration,
            args.stagger_ms / 1000.0, use_artsync, t0, log_fn=_log,
        )
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        for p in workers:
            p.terminate()
            p.join(timeout=1.0)

    _log("Stopped. Main process sent %d packets, %d send errors." % (sent, errors))
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
