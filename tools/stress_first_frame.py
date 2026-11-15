#!/usr/bin/env python3
"""
Stress test for I-02: first-frame race in Recorder._capture.

Runs ArtNetServer + FrameAssembler + Recorder over real UDP (127.0.0.1:7457)
with 64,000 chips (500 universes). Compares the buggy behaviour (old
wait-any-in-latest) against the fixed behaviour (wait-for-committed_frame)
across N runs and reports how many first frames were all-zeros.

Usage:
    python tools/stress_first_frame.py [--runs 200] [--port 7457]

Expected output on a machine where the race reproduces:
    BUGGY  200 runs: black=N (N > 0)
    FIXED  200 runs: black=0

A non-zero buggy count proves the race exists.
A zero fixed count proves the fix eliminates it.
"""

import argparse
import os
import queue
import socket
import sys
import time
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import session as _sess
from main import FrameAssembler, Recorder
from session import ArtNetServer
from mapping import build_spans, effective_chips_per_universe

HOST = "127.0.0.1"
CHIPS = 64000  # 500 universes


def _make_artdmx(uni, data, seq=1):
    n = len(data)
    if n % 2:
        data = data + b"\x00"
        n += 1
    pkt = bytearray(18 + n)
    pkt[0:8] = b"Art-Net\x00"
    pkt[8] = 0x00
    pkt[9] = 0x50
    pkt[12] = seq & 0xFF
    pkt[14] = uni & 0xFF
    pkt[15] = (uni >> 8) & 0x7F
    pkt[16] = n >> 8
    pkt[17] = n & 0xFF
    pkt[18:] = data
    return bytes(pkt)


def _send_frame(sock, addr, spans, fill=0xAA):
    for uni, ch, n_ch, _off in spans:
        sock.sendto(_make_artdmx(uni, bytes([fill] * n_ch)), addr)


_ORIG_CAPTURE = _sess.Recorder._capture


def _buggy_capture(self, wait_first_packet):
    """Old (buggy) wait: fires as soon as any universe appears in server.latest."""
    if wait_first_packet:
        while not self._stop.is_set():
            with self.server.lock:
                have = any(s[0] in self.server.latest for s in self.spans)
            if have:
                break
            time.sleep(0.005)
        if self._stop.is_set():
            return
        self.state = "recording"
    period = 1.0 / self.fps
    t0 = time.perf_counter()
    k = 0
    while not self._stop.is_set():
        target = t0 + k * period
        while not self._stop.is_set():
            dt = target - time.perf_counter()
            if dt <= 0:
                break
            time.sleep(dt - 0.002 if dt > 0.004 else 0)
        if self._stop.is_set():
            break
        frame, miss = self._sample()
        if frame is None:
            frame = bytes(self.stride)
        if self._out_queue is not None:
            try:
                self._out_queue.put_nowait(frame)
            except queue.Full:
                break
        self.frames_captured += 1
        k += 1
        if self.max_frames and k >= self.max_frames:
            break


def run_stress(n_runs, use_fix, port):
    if use_fix:
        _sess.Recorder._capture = _ORIG_CAPTURE
    else:
        _sess.Recorder._capture = _buggy_capture

    spans = build_spans(1, 1, 4, CHIPS, effective_chips_per_universe(0))
    stride = CHIPS * 4

    server = ArtNetServer()
    server.start(HOST, port)
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (HOST, port)

    black = 0
    timeouts = 0

    t0 = time.perf_counter()
    for i in range(n_runs):
        fa = FrameAssembler()
        fa.start(spans, stride)
        with server.lock:
            server.latest.clear()
        server.set_assembler(fa)

        out_q = queue.Queue(maxsize=10)
        rec = Recorder(server)
        rec.start(spans, stride, fps=30, max_frames=1,
                  wait_first_packet=True, assembler=fa, out_queue=out_q)

        _send_frame(send_sock, addr, spans)

        deadline = time.perf_counter() + 2.0
        while rec.state != "done" and time.perf_counter() < deadline:
            time.sleep(0.002)

        if rec.state != "done":
            timeouts += 1
        else:
            frame = out_q.get_nowait()
            if frame == bytes(stride):
                black += 1

        server.set_assembler(None)
        time.sleep(0.005)

    elapsed = time.perf_counter() - t0
    send_sock.close()
    server.stop()
    _sess.Recorder._capture = _ORIG_CAPTURE
    return black, timeouts, elapsed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--port", type=int, default=7457)
    args = ap.parse_args()

    print("stress_first_frame.py  chips=%d (500 uni)  runs=%d  port=%d"
          % (CHIPS, args.runs, args.port))

    b_buggy, to_buggy, t_buggy = run_stress(args.runs, use_fix=False, port=args.port)
    print("BUGGY  %d runs in %.2f s: black=%d (%.1f%%)  timeouts=%d"
          % (args.runs, t_buggy, b_buggy, 100.0 * b_buggy / args.runs, to_buggy))

    b_fixed, to_fixed, t_fixed = run_stress(args.runs, use_fix=True, port=args.port)
    print("FIXED  %d runs in %.2f s: black=%d (%.1f%%)  timeouts=%d"
          % (args.runs, t_fixed, b_fixed, 100.0 * b_fixed / args.runs, to_fixed))

    if b_buggy > 0 and b_fixed == 0:
        print("PASS: race reproduced on buggy code, eliminated by fix.")
    elif b_buggy == 0:
        print("NOTE: race not reproduced on this machine in %d runs. "
              "Try increasing --runs." % args.runs)
    elif b_fixed > 0:
        print("FAIL: fixed code still produces black frames!")

    return 0 if b_fixed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
