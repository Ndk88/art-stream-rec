#!/usr/bin/env python3
"""
Baseline receiver benchmark (pre-R5).

Runs ArtNetServer + sampler core without the GUI while packets_tester.py
sends from a child process. Reports:
  - sent vs received packets
  - lost packets (sequence gaps, approximate: reorders counted as losses)
  - late-frame percentage (sampler missed its schedule by >1.5 periods)
  - missing-frame percentage (a mapped universe had no data at sample time)
  - receiver process CPU time
  - requested vs actual SO_RCVBUF
  - whether the sender hit lag warnings (sender was bottleneck, not receiver)

Usage:
    python tools/bench_receiver.py --chips 1700 --fps 30 --duration 10
    python tools/bench_receiver.py --chips 64000 --fps 60 --duration 60 --procs 4
    python tools/bench_receiver.py --matrix                # full matrix, 10 s each
    python tools/bench_receiver.py --matrix --duration 60  # full matrix, 60 s each
"""

import argparse
import os
import platform
import socket
import subprocess
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from main import ArtNetServer, parse_artdmx
from mapping import build_spans, effective_chips_per_universe

RCVBUF_WANT = 32 * 1024 * 1024   # 32 MiB requested; kernel may cap
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7454               # separate from the recorder (6454)
DEFAULT_DURATION = 10.0

MATRIX = [
    (1_700,  30),
    (1_700,  60),
    (10_000, 60),
    (32_000, 60),
    (64_000, 30),
    (64_000, 60),
]


# ---------------------------------------------------------------------------
# Extended server: SO_RCVBUF + per-universe sequence gap tracking
# ---------------------------------------------------------------------------

class _BenchServer(ArtNetServer):
    """ArtNetServer + SO_RCVBUF + per-universe sequence number tracking."""

    def __init__(self):
        super().__init__()
        self._seq_last = {}     # port_address -> last seq byte seen
        self.lost_packets = 0
        self.rcvbuf_actual = 0
        self.first_packet_time = 0.0  # perf_counter of first ArtDMX packet

    def _loop(self):
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_WANT)
        except OSError:
            pass
        self.rcvbuf_actual = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

        sock = self._sock
        while self.running:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as exc:
                if self.running:
                    self.error = "Server socket error: %s" % exc
                    self.running = False
                break
            pkt = parse_artdmx(data)
            now = time.perf_counter()
            with self.lock:
                self.total_packets += 1
                self.total_bytes += len(data)
                self.last_packet_time = now
                if pkt is None:
                    self.ignored_packets += 1
                    continue
                port_address, seq, dmx = pkt
                if self.first_packet_time == 0.0:
                    self.first_packet_time = now
                if seq != 0:
                    last = self._seq_last.get(port_address)
                    if last is not None:
                        expected = last % 255 + 1
                        if expected != seq:
                            gap = (seq - expected + 255) % 255
                            # Discard apparent gaps > 128 as likely reordering
                            if 0 < gap <= 128:
                                self.lost_packets += gap
                    self._seq_last[port_address] = seq
                self.latest[port_address] = dmx.ljust(512, b"\x00")[:512]
                st = self.uni_stats.setdefault(port_address, {"count": 0, "length": 0})
                st["count"] += 1
                st["length"] = len(dmx)


# ---------------------------------------------------------------------------
# Sampler: drift-free schedule, full sample work, frames discarded
# ---------------------------------------------------------------------------

class _BenchSampler:
    """
    Replicates Recorder._capture() without accumulating frames in memory.
    Performs the same bytearray assembly per sample so the CPU measurement
    reflects real recorder work.
    """

    def __init__(self, server):
        self.server = server
        self.fps = 30.0
        self.spans = []
        self.stride = 0
        self.frame_count = 0
        self.late = 0
        self.missing = 0
        self.elapsed = 0.0
        self.state = "idle"
        self._stop = threading.Event()
        self._thread = None

    def start(self, spans, stride, fps):
        self.spans, self.stride = spans, stride
        self.fps = float(fps)
        self.frame_count = self.late = self.missing = 0
        self.elapsed = 0.0
        self._stop.clear()
        self.state = "recording"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self):
        try:
            self._capture()
        finally:
            self.state = "done"

    def _capture(self):
        period = 1.0 / self.fps
        t0 = time.perf_counter()
        k = 0
        while not self._stop.is_set():
            target = t0 + k * period
            slack = target - time.perf_counter()
            if slack > 0.002:
                time.sleep(slack - 0.002)
            while not self._stop.is_set() and time.perf_counter() < target:
                pass
            if self._stop.is_set():
                break
            now = time.perf_counter()
            if now - target > 1.5 * period:
                self.late += 1
            # Full sample (same work as Recorder._sample; result discarded)
            with self.server.lock:
                latest = self.server.latest
                out = bytearray(self.stride)
                miss = False
                for uni, ch, n, off in self.spans:
                    buf = latest.get(uni)
                    if buf is None:
                        miss = True
                        continue
                    out[off:off + n] = buf[ch:ch + n]
            self.frame_count += 1
            if miss:
                self.missing += 1
            k += 1
            self.elapsed = now - t0


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _auto_procs(universes, fps):
    """Estimate sender processes needed to generate the target packet rate."""
    pkt_rate = universes * fps
    if pkt_rate <= 5_000:
        return 1
    elif pkt_rate <= 15_000:
        return 2
    else:
        return 4


def run_once(chips, fps, duration=DEFAULT_DURATION,
             host=DEFAULT_HOST, port=DEFAULT_PORT, procs=0, quiet=False):
    """
    Run one benchmark point. Returns a result dict with all measured metrics.
    Spawns packets_tester.py as a child process for the send side.
    """
    eff_cpu = effective_chips_per_universe(0)
    spans = build_spans(1, 1, 4, chips, eff_cpu)
    universes = len(spans)
    stride = chips * 4
    n_procs = procs if procs > 0 else _auto_procs(universes, fps)
    expected_pkt_rate = universes * fps

    if not quiet:
        print("  chips=%-6d  fps=%d  universes=%d  sender_procs=%d  duration=%.0fs"
              % (chips, fps, universes, n_procs, duration), flush=True)

    server = _BenchServer()
    try:
        server.start(host, port)
    except OSError as exc:
        raise OSError("Cannot bind %s:%d — %s" % (host, port, exc)) from exc
    time.sleep(0.15)

    sampler = _BenchSampler(server)
    sampler.start(spans, stride, fps)

    cpu_start = time.process_time()
    t_wall_start = time.perf_counter()

    tester = os.path.join(_ROOT, "packets_tester.py")
    cmd = [
        sys.executable, "-u", tester,
        host, str(port), str(chips), "1",
        "--fps", str(fps),
        "--duration", str(duration),
        "--procs", str(n_procs),
        "--pattern", "ramp",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        stdout, _ = proc.communicate(timeout=duration + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()

    cpu_end = time.process_time()
    t_wall_end = time.perf_counter()
    actual_duration = t_wall_end - t_wall_start

    time.sleep(0.3)   # let trailing packets arrive
    sampler.stop()
    server.stop()

    # Parse main-process sent count from tester output
    sent_main = 0
    sender_lag_warnings = 0
    for line in stdout.splitlines():
        if "Main process sent" in line:
            try:
                sent_main = int(line.split("Main process sent")[1].split()[0])
            except (IndexError, ValueError):
                pass
        if "WARNING:" in line and "behind schedule" in line:
            sender_lag_warnings += 1

    received = server.total_packets
    lost_seq = server.lost_packets
    frame_count = sampler.frame_count
    late = sampler.late
    missing = sampler.missing

    # Use first→last packet window for pkt/s (excludes sender startup delay)
    send_window = server.last_packet_time - server.first_packet_time
    send_window_pkt_rate = received / send_window if send_window > 0 else 0.0

    return {
        "chips": chips,
        "fps": fps,
        "universes": universes,
        "stride": stride,
        "duration": duration,
        "actual_duration": actual_duration,
        "send_window": send_window,
        "n_procs": n_procs,
        "expected_pkt_rate": expected_pkt_rate,
        "actual_pkt_rate": send_window_pkt_rate,
        "sent_main": sent_main,
        "received": received,
        "lost_seq": lost_seq,
        "frame_count": frame_count,
        "late": late,
        "late_pct": 100.0 * late / frame_count if frame_count else 0.0,
        "missing": missing,
        "missing_pct": 100.0 * missing / frame_count if frame_count else 0.0,
        "cpu_sec": cpu_end - cpu_start,
        "rcvbuf_want": RCVBUF_WANT,
        "rcvbuf_actual": server.rcvbuf_actual,
        "sender_lag_warnings": sender_lag_warnings,
    }


def _print_result(r, indent="  "):
    p = indent
    exp = r["expected_pkt_rate"]
    pct_recv = (100.0 * r["actual_pkt_rate"] / exp) if exp > 0 else 0.0
    print(
        "%srecv: %d pkt  (%.0f pkt/s = %.1f%% of expected %d pkt/s, window %.1fs)"
        % (p, r["received"], r["actual_pkt_rate"], pct_recv, exp, r["send_window"])
    )
    print("%slost (seq gaps): %d" % (p, r["lost_seq"]))
    print(
        "%ssampler: %d frames  late: %d (%.2f%%)  missing: %d (%.2f%%)"
        % (p, r["frame_count"], r["late"], r["late_pct"], r["missing"], r["missing_pct"])
    )
    print("%sreceiver CPU: %.2f s" % (p, r["cpu_sec"]))
    print("%sSO_RCVBUF: requested %d MiB, actual %d KiB"
          % (p, r["rcvbuf_want"] >> 20, r["rcvbuf_actual"] >> 10))
    if r["sender_lag_warnings"]:
        print("%s** SENDER LAG WARNINGS: %d (sender was bottleneck, not receiver) **"
              % (p, r["sender_lag_warnings"]))


def _machine_info():
    info = []
    info.append("OS:       %s %s %s" % (platform.system(), platform.release(), platform.version()))
    info.append("Machine:  %s" % platform.machine())
    info.append("CPU:      %s" % (platform.processor() or platform.machine()))
    info.append("Python:   %s (%d-bit)" % (platform.python_version(),
                                            8 * __import__("struct").calcsize("P")))
    return "\n".join(info)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Baseline Art-Net receiver benchmark (pre-R5).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--chips", type=int, default=1700,
                    help="Chip count for a single run (default %(default)s)")
    ap.add_argument("--fps", type=int, choices=[30, 60], default=30,
                    help="Frames per second (default %(default)s)")
    ap.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                    help="Seconds per benchmark point (default %(default)s)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--procs", type=int, default=0,
                    help="Sender processes (0 = auto)")
    ap.add_argument("--matrix", action="store_true",
                    help="Run all %(count)d matrix points" % {"count": len(MATRIX)})
    args = ap.parse_args(argv)

    print(_machine_info())
    print()

    if args.matrix:
        results = []
        print("Matrix: %d points x %.0f s each\n" % (len(MATRIX), args.duration))
        for chips, fps in MATRIX:
            ts = time.strftime("%H:%M:%S")
            print("[%s] chips=%d  fps=%d" % (ts, chips, fps))
            try:
                r = run_once(chips, fps, args.duration, args.host, args.port, args.procs)
                _print_result(r)
                results.append(r)
            except OSError as exc:
                print("  ERROR: %s" % exc)
            print()

        if results:
            print("=" * 72)
            print("SUMMARY TABLE")
            print("=" * 72)
            hdr = "%-7s  %-4s  %-5s  %-8s  %-6s  %-5s  %-5s  %-4s"
            print(hdr % ("chips", "fps", "uni", "pkt/s", "lost", "late%", "miss%", "lag?"))
            print("-" * 72)
            for r in results:
                print("%-7d  %-4d  %-5d  %-8.0f  %-6d  %-5.2f  %-5.2f  %s"
                      % (r["chips"], r["fps"], r["universes"],
                         r["actual_pkt_rate"], r["lost_seq"],
                         r["late_pct"], r["missing_pct"],
                         "YES" if r["sender_lag_warnings"] else "no"))
    else:
        r = run_once(args.chips, args.fps, args.duration, args.host, args.port, args.procs)
        _print_result(r)

    return 0


if __name__ == "__main__":
    sys.exit(main())
