#!/usr/bin/env python3
"""
End-to-end PLSQ recording benchmark.

Runs ArtNetServer + FrameAssembler + Recorder + PlsqStreamWriter against
a packets_tester.py child process and measures the full recording pipeline.

Usage:
  python tools/bench_e2e.py --quick               # 10 s per point, full matrix
  python tools/bench_e2e.py --chips 1700 --fps 60 --duration 30
  python tools/bench_e2e.py --matrix --duration 60
  python tools/bench_e2e.py --chips 64000 --fps 60 --artsync --stagger-ms 0.05

Options:
  --chips N          Total chips (1..64000). Matrix mode ignores this.
  --fps 30|60        Frame rate. Matrix mode ignores this.
  --duration SEC     Recording seconds (default 60; --quick overrides to 10).
  --artsync          Ask tester to send ArtSync after each frame.
  --stagger-ms MS    Inter-universe delay in the sender (ms).
  --complete-frames on|off   Enable/disable frame assembler (default on).
  --skip N           Skip first N frames.
  --blend N          Loop-blend N frames.
  --out-dir DIR      Where to write .plsq files (default: system temp).
  --matrix           Run all 6 standard matrix points + 2 extra runs.
  --quick            Use 10 s instead of --duration for each run.
  --no-content-check Skip per-frame content validation (fast path for large chips).

recv % = packets counted by ArtNetServer over the whole run / packets sent by
all tester processes (sum of each process's final count). n/a if unparseable.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mapping import build_spans, effective_chips_per_universe
from plsq import validate, read_frames as plsq_read_frames
from session import ArtNetServer, PlsqRecordingSession

PYTHON = sys.executable
HOST = "127.0.0.1"
PORT = 6454
TESTER_EXIT_TIMEOUT = 20.0  # seconds to wait for the tester after recording

# ---- Windows memory / CPU helpers ------------------------------------------

try:
    import ctypes
    import ctypes.wintypes as _wt

    class _PMCE(ctypes.Structure):
        _fields_ = [
            ("cb",                          _wt.DWORD),
            ("PageFaultCount",              _wt.DWORD),
            ("PeakWorkingSetSize",          ctypes.c_size_t),
            ("WorkingSetSize",              ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage",     ctypes.c_size_t),
            ("QuotaPagedPoolUsage",         ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage",  ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage",      ctypes.c_size_t),
            ("PagefileUsage",               ctypes.c_size_t),
            ("PeakPagefileUsage",           ctypes.c_size_t),
            ("PrivateUsage",                ctypes.c_size_t),
        ]

    # Own WinDLL instances with explicit prototypes: with the ctypes defaults
    # the pseudo-handle returned by GetCurrentProcess() (-1) is truncated to a
    # 32-bit int, both calls fail silently and CPU/memory read as 0.
    _psapi   = ctypes.WinDLL("psapi")
    _k32     = ctypes.WinDLL("kernel32")
    _k32.GetCurrentProcess.restype = _wt.HANDLE
    _k32.GetCurrentProcess.argtypes = []
    _k32.GetProcessTimes.restype = _wt.BOOL
    _k32.GetProcessTimes.argtypes = [_wt.HANDLE] + [ctypes.POINTER(_wt.FILETIME)] * 4
    _psapi.GetProcessMemoryInfo.restype = _wt.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [
        _wt.HANDLE, ctypes.POINTER(_PMCE), _wt.DWORD
    ]
    _HAS_WIN = True
except (AttributeError, OSError, ImportError):
    _HAS_WIN = False

try:
    import resource as _resource
    _HAS_RES = True
except ImportError:
    _HAS_RES = False


def _mem_bytes():
    if _HAS_WIN:
        try:
            pmc = _PMCE()
            pmc.cb = ctypes.sizeof(pmc)
            _psapi.GetProcessMemoryInfo(
                _k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb
            )
            return pmc.WorkingSetSize
        except Exception:
            pass
    if _HAS_RES:
        try:
            return _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss * 1024
        except Exception:
            pass
    return None


def _cpu_seconds():
    if _HAS_WIN:
        try:
            cre = _wt.FILETIME()
            ex  = _wt.FILETIME()
            ku  = _wt.FILETIME()
            us  = _wt.FILETIME()
            _k32.GetProcessTimes(
                _k32.GetCurrentProcess(),
                ctypes.byref(cre), ctypes.byref(ex),
                ctypes.byref(ku), ctypes.byref(us),
            )
            k_ns = (ku.dwHighDateTime << 32 | ku.dwLowDateTime) * 100
            u_ns = (us.dwHighDateTime << 32 | us.dwLowDateTime) * 100
            return (k_ns + u_ns) / 1e9
        except Exception:
            pass
    if _HAS_RES:
        try:
            ru = _resource.getrusage(_resource.RUSAGE_SELF)
            return ru.ru_utime + ru.ru_stime
        except Exception:
            pass
    return None


def _drive_free_gb(path):
    try:
        import shutil
        return shutil.disk_usage(os.path.abspath(path)).free / 1e9
    except Exception:
        return float("nan")


# ---- tester subprocess helpers ---------------------------------------------

_RE_FINAL = re.compile(r"Stopped\. (Main process|Worker (\d+)) sent (\d+) packets")
_RE_WORKER_TOTAL = re.compile(r"Worker (\d+) \| total (\d+) \|")
_RE_MAIN_TOTAL = re.compile(r"\bsent \d+ \(.*\| total (\d+) \|")
_RE_PROCS = re.compile(r"Multi-process: (\d+) processes")


def _parse_sent(text):
    """Total packets sent by ALL tester processes, or None if not parseable.

    For each process (main + workers) take its final 'Stopped. ... sent N
    packets' value; if a process has no final line, take its last periodic
    'total N'. Sum over processes. Returns None when no process could be
    parsed, or when the tester announced N processes and fewer were found.
    """
    final = {}
    last_total = {}
    expected = 1
    for line in text.splitlines():
        m = _RE_FINAL.search(line)
        if m:
            key = "main" if m.group(2) is None else "w" + m.group(2)
            final[key] = int(m.group(3))
            continue
        m = _RE_WORKER_TOTAL.search(line)
        if m:
            last_total["w" + m.group(1)] = int(m.group(2))
            continue
        m = _RE_MAIN_TOTAL.search(line)
        if m:
            last_total["main"] = int(m.group(1))
            continue
        m = _RE_PROCS.search(line)
        if m:
            expected = int(m.group(1))
    keys = set(final) | set(last_total)
    if not keys or len(keys) < expected:
        return None
    return sum(final.get(k, last_total.get(k, 0)) for k in keys)


def _start_tester(chips, fps, duration, artsync, stagger_ms, n_uni):
    procs = 1
    if n_uni > 125:
        procs = min(4, (n_uni + 124) // 125)
    cmd = [
        PYTHON,
        os.path.join(_ROOT, "packets_tester.py"),
        HOST, str(PORT), str(chips), "1",
        "--fps", str(fps),
        "--pattern", "ramp",
        "--duration", "%.1f" % (duration + 3.0),
    ]
    if artsync:
        cmd.append("--artsync")
    if stagger_ms > 0:
        cmd += ["--stagger-ms", "%.3f" % stagger_ms]
    if procs > 1:
        cmd += ["--procs", str(procs)]
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def _drain_tester(proc, buf, lock):
    """Thread target: relay tester stdout in real-time and accumulate into buf."""
    for line in proc.stdout:
        line = line.rstrip("\n")
        print("[tester] %s" % line, flush=True)
        with lock:
            buf.append(line)


def _stop_tester(proc, timeout=5.0):
    """Terminate tester and wait for it to exit. stdout must be drained separately."""
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ---- ring builder (matches packets_tester logic) ---------------------------

def _build_ring(chips, stride, fps):
    """Return list of ring frames using ramp pattern (same as tester default)."""
    ring_size = fps * 2
    unit = bytes(range(256))
    base = (unit * (stride // 256 + 1))[:stride]
    step = 4
    double = base + base
    mv = memoryview(double)
    frames = []
    for i in range(ring_size):
        off = (i * step) % stride
        frames.append(bytes(mv[off: off + stride]))
    return frames


def _describe_torn(idx, frame, ring, spans):
    """Diagnostics for one torn frame: nearest ring frame, first differing
    byte, the universes involved and which ring frame the differing universes
    match instead. Returns a list of log lines."""
    def ndiff(a, b):
        return sum(1 for x, y in zip(a, b) if x != y)

    def uni_of(offset):
        for uni, _ch, n_ch, off in spans:
            if off <= offset < off + n_ch:
                return uni, offset - off
        return None, None

    dists = [ndiff(frame, r) for r in ring]
    k = min(range(len(ring)), key=dists.__getitem__)
    near = ring[k]
    diff_pos = [i for i, (x, y) in enumerate(zip(frame, near)) if x != y]
    lines = ["First torn frame: #%d (len %d)" % (idx, len(frame))]
    if not diff_pos:
        lines.append("  identical to ring[%d]?" % k)
        return lines
    first = diff_pos[0]
    u, u_off = uni_of(first)
    lines.append(
        "  nearest ring frame: ring[%d], %d differing bytes (of %d)"
        % (k, len(diff_pos), len(frame))
    )
    lines.append(
        "  first differing byte: %d (universe U%s, byte %s in universe), "
        "got %d expected %d" % (first, u, u_off, frame[first], near[first])
    )
    unis = []
    for pos in diff_pos:
        uu, _ = uni_of(pos)
        if not unis or unis[-1] != uu:
            unis.append(uu)
    lines.append(
        "  differing bytes %d..%d, universes involved: %s"
        % (diff_pos[0], diff_pos[-1], ",".join("U%s" % x for x in unis[:20])
           + (" ..." if len(unis) > 20 else ""))
    )
    for uu in unis[:5]:
        span = next(sp for sp in spans if sp[0] == uu)
        _uni, _ch, n_ch, off = span
        part = frame[off:off + n_ch]
        match = [j for j, r in enumerate(ring) if r[off:off + n_ch] == part]
        lines.append(
            "  U%s (frame bytes %d..%d): matches ring%s"
            % (uu, off, off + n_ch - 1, match[:6] if match else " frame: none")
        )
    return lines


# ---- queue depth tracker ---------------------------------------------------

def _track_q(q_ref, result, stop_ev):
    mx = 0
    while not stop_ev.is_set():
        if q_ref[0] is not None:
            d = q_ref[0].qsize()
            if d > mx:
                mx = d
        time.sleep(0.01)
    result[0] = mx


# ---- single run ------------------------------------------------------------

def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run_one(chips, fps, duration, out_dir, complete_frames=True,
            artsync=False, stagger_ms=0.0, skip=0, blend=0,
            label=None, content_check=True):

    if label is None:
        label = "%d/%d" % (chips, fps)

    eff_cpu = effective_chips_per_universe(0)
    spans   = build_spans(1, 1, 4, chips, eff_cpu)
    stride  = chips * 4
    n_uni   = len(spans)
    q_cap   = max(300, int(2 * fps) + 20)

    out_path = os.path.join(
        out_dir, "bench_%d_%dfps_%ds.plsq" % (chips, fps, int(duration))
    )

    _log("=== %s | chips=%d fps=%d dur=%.0fs uni=%d assembler=%s ===" % (
        label, chips, fps, duration, n_uni, "on" if complete_frames else "off"
    ))
    exp_bytes = chips * 4 * int(duration * fps)
    _log("Disk: %.1f GB free, need ~%.2f GB" % (
        _drive_free_gb(out_dir), exp_bytes / 1e9
    ))

    # Start server
    server = ArtNetServer()
    server.start(HOST, PORT)
    _log("Listening on %s:%d" % (HOST, PORT))

    # Start tester and relay its output in real-time
    tester = _start_tester(chips, fps, duration, artsync, stagger_ms, n_uni)
    tester_buf  = []
    tester_lock = threading.Lock()
    tester_reader = threading.Thread(
        target=_drain_tester, args=(tester, tester_buf, tester_lock), daemon=True
    )
    tester_reader.start()

    # Wait for first packets (up to 4 s)
    t_wait = time.perf_counter()
    while time.perf_counter() - t_wait < 4.0:
        if server.snapshot()[2] > 0:
            break
        time.sleep(0.05)
    pkts_now = server.snapshot()[2]
    if pkts_now > 0:
        _log("First packets arrived after %.2f s" % (time.perf_counter() - t_wait))
    else:
        _log("WARNING: no packets received after %.2f s wait on port %d" % (
            time.perf_counter() - t_wait, PORT
        ))

    # Create session
    meta_extra = dict(
        recorder_version="bench_e2e",
        model_name="bench",
        model_chips_tag="bench_chip",
        sequence_name="bench_%s" % label.replace("/", "_"),
        sequence_version="1.0",
        developer_notes="End-to-end benchmark",
    )
    session = PlsqRecordingSession(
        server,
        chips=chips, fps=fps, out_path=out_path,
        spans=spans, stride=stride,
        start_universe=1, start_channel=1, eff_cpu=eff_cpu,
        skip=skip, blend=blend,
        final_frames=int(duration * fps),
        wait_first_packet=True,
        complete_frames=complete_frames,
        meta_extra=meta_extra,
    )

    cpu0 = _cpu_seconds()
    mem0 = _mem_bytes()

    session.start()
    if session.state == "error":
        _log("SESSION START ERROR: %s" % session.error)
        tester_out = _stop_tester(tester)
        server.stop()
        return None

    # Queue depth tracker
    q_ref    = [session._queue]
    q_result = [0]
    q_stop   = threading.Event()
    q_thread = threading.Thread(
        target=_track_q, args=(q_ref, q_result, q_stop), daemon=True
    )
    q_thread.start()

    # Poll loop
    t0            = time.perf_counter()
    mem_at_10     = None
    last_log      = t0
    last_pkt_cnt  = server.snapshot()[2]
    last_pkt_time = time.perf_counter()
    NO_PKT_TIMEOUT = 5.0
    tester_exited  = False

    while session.state not in ("done", "error"):
        # --- tester early-exit detection ---
        tester_rc = tester.poll()
        if tester_rc is not None and not tester_exited:
            tester_exited = True
            with tester_lock:
                out_so_far = "\n".join(tester_buf)
            if tester_rc != 0:
                _log("ERROR: tester exited early (rc=%d) while session is '%s'. "
                     "Tester output:\n%s" % (tester_rc, session.state, out_so_far))
                session.abort()
                break
            else:
                _log("WARNING: tester exited (rc=0) while session is '%s'" % session.state)

        # --- 5-second no-packets timeout ---
        cur_pkts = server.snapshot()[2]
        if cur_pkts > last_pkt_cnt:
            last_pkt_cnt  = cur_pkts
            last_pkt_time = time.perf_counter()
        elif time.perf_counter() - last_pkt_time >= NO_PKT_TIMEOUT:
            with tester_lock:
                out_so_far = "\n".join(tester_buf)
            _log(
                "ERROR: No packets received for %.0fs on port %d. "
                "Session state='%s'. Tester output:\n%s"
                % (NO_PKT_TIMEOUT, PORT, session.state, out_so_far)
            )
            session.abort()
            _stop_tester(tester)
            tester_reader.join(timeout=3.0)
            q_stop.set()
            q_thread.join(timeout=1.0)
            server.stop()
            return {
                "label": label, "chips": chips, "fps": fps,
                "error": "No packets for %ds on port %d" % (int(NO_PKT_TIMEOUT), PORT),
            }

        session.poll()
        now     = time.perf_counter()
        elapsed = now - t0

        if mem_at_10 is None and elapsed >= 10.0:
            mem_at_10 = _mem_bytes()

        if now - last_log >= 5.0:
            rec = session.recorder
            fc  = rec.frames_captured if rec else 0
            bw  = session.bytes_written
            qs  = session.queue_size
            _log("  %s  frames=%d  written=%.1fMB  q=%d/%d" % (
                session.state, fc, bw / (1024 * 1024), qs, q_cap
            ))
            last_log = now

        time.sleep(0.1)

    q_stop.set()
    q_thread.join(timeout=1.0)

    t_elapsed = time.perf_counter() - t0
    cpu1 = _cpu_seconds()
    mem1 = _mem_bytes()

    # Let the tester finish its --duration so every process prints its final
    # count; terminate only if it does not exit in time (then the parser falls
    # back to the last periodic totals).
    try:
        tester.wait(timeout=TESTER_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        _log("WARNING: tester still running after %.0f s, terminating"
             % TESTER_EXIT_TIMEOUT)
        _stop_tester(tester)
    tester_reader.join(timeout=3.0)
    with tester_lock:
        tester_out = "\n".join(tester_buf)
    pkts_sent  = _parse_sent(tester_out)
    time.sleep(0.5)  # let the server drain its socket buffer
    snapshot   = server.snapshot()
    # The server was started before the tester, so its counter covers every
    # packet of the run (same window as the tester's sent count).
    pkts_recv  = snapshot[2]
    server.stop()

    if session.state == "error":
        _log("SESSION ERROR: %s" % session.error)
        return {
            "label": label, "chips": chips, "fps": fps,
            "error": session.error,
        }

    res = session.result()

    # Validate file
    problems = validate(out_path)

    # Content check: every frame should match a ring frame
    torn  = 0
    zero  = 0
    if content_check and os.path.exists(out_path):
        _log("Reading %d frames for content check..." % res["frames_written"])
        try:
            rec_frames = plsq_read_frames(out_path, 0, res["frames_written"])
            ring = _build_ring(chips, stride, fps)
            ring_set = set(ring)
            zero_frame = bytes(stride)
            first_torn = None
            for i, f in enumerate(rec_frames):
                if f == zero_frame:
                    zero += 1
                elif f not in ring_set:
                    torn += 1
                    if first_torn is None:
                        first_torn = i
            if first_torn is not None:
                for line in _describe_torn(
                    first_torn, rec_frames[first_torn], ring, spans
                ):
                    _log(line)
        except Exception as exc:
            _log("Content check failed: %s" % exc)

    # Metrics
    recv_ratio = pkts_recv / pkts_sent if pkts_sent else None
    exp_frames = int(duration * fps)
    frames_diff = res["frames_written"] - exp_frames
    q_max = q_result[0]
    q_pct = 100.0 * q_max / q_cap if q_cap else 0

    fc = res["frames_captured"] or res["frames_written"]
    late_pct    = 100.0 * res["late_frames"]    / fc if fc else 0
    missing_pct = 100.0 * res["missing_frames"] / res["frames_written"] if res["frames_written"] else 0

    mem_growth = None
    if mem_at_10 is not None and mem1 is not None:
        mem_growth = (mem1 - mem_at_10) / (1024 * 1024)

    cpu_sec = None
    if cpu0 is not None and cpu1 is not None:
        cpu_sec = cpu1 - cpu0

    file_mb = os.path.getsize(out_path) / (1024 * 1024) if os.path.exists(out_path) else 0

    _log("--- Results: %s ---" % label)
    _log("  Sent/Received  : %s / %d  recv=%s" % (
        str(pkts_sent) if pkts_sent is not None else "n/a", pkts_recv,
        ("%.2f%%" % (100.0 * recv_ratio)) if recv_ratio is not None else "n/a"
    ))
    _log("  lost_packets   : %d" % res["lost_packets"])
    _log("  late_frames    : %d (%.2f%%)" % (res["late_frames"], late_pct))
    _log("  missing_frames : %d (%.2f%%)" % (res["missing_frames"], missing_pct))
    _log("  partial_src    : %d" % res["partial_source_frames"])
    _log("  Frames written : %d (expected %d, diff %+d)" % (
        res["frames_written"], exp_frames, frames_diff
    ))
    _log("  Torn / Zero    : %d / %d" % (torn, zero))
    _log("  Queue max/cap  : %d/%d (%.0f%%)" % (q_max, q_cap, q_pct))
    _log("  File size      : %.1f MB" % file_mb)
    if cpu_sec is not None:
        _log("  CPU time       : %.1f s" % cpu_sec)
    if mem_growth is not None:
        _log("  Mem growth     : %+.1f MB (from 10s mark to end)" % mem_growth)
    if problems:
        _log("  VALIDATE: FAIL — %s" % "; ".join(problems))
    else:
        _log("  Validate       : OK")

    return {
        "label":                label,
        "chips":                chips,
        "fps":                  fps,
        "duration_actual":      t_elapsed,
        "pkts_sent":            pkts_sent,
        "pkts_recv":            pkts_recv,
        "recv_ratio":           recv_ratio,
        "lost_packets":         res["lost_packets"],
        "late_pct":             late_pct,
        "missing_pct":          missing_pct,
        "partial_source_frames": res["partial_source_frames"],
        "frames_written":       res["frames_written"],
        "expected_frames":      exp_frames,
        "frames_diff":          frames_diff,
        "torn_frames":          torn,
        "zero_frames":          zero,
        "q_max_pct":            q_pct,
        "file_mb":              file_mb,
        "validate_ok":          not problems,
        "cpu_seconds":          cpu_sec,
        "mem_growth_mb":        mem_growth,
        "error":                None,
    }


# ---- matrix ----------------------------------------------------------------

MATRIX = [
    (1700,  30),
    (1700,  60),
    (10000, 60),
    (32000, 60),
    (64000, 30),
    (64000, 60),
]


def _print_table(results):
    _log("")
    _log("=" * 88)
    _log("BENCHMARK SUMMARY")
    _log("=" * 88)
    _log("%-26s %7s %6s %8s %7s %7s %8s %6s %6s" % (
        "Label", "Sent", "Recv%", "Lost", "Late%", "Miss%", "Torn", "Q%", "Valid"
    ))
    _log("-" * 88)
    for r in results:
        if r is None:
            continue
        if r.get("error"):
            _log("%-26s  ERROR: %s" % (r["label"], r["error"]))
            continue
        recv = r["recv_ratio"]
        _log("%-26s %7s %6s %8d %6.2f%% %6.2f%% %8d %5.0f%% %6s" % (
            r["label"],
            str(r["pkts_sent"]) if r["pkts_sent"] is not None else "n/a",
            ("%.1f%%" % (recv * 100.0)) if recv is not None else "n/a",
            r["lost_packets"],
            r["late_pct"],
            r["missing_pct"],
            r["torn_frames"],
            r["q_max_pct"],
            "OK" if r["validate_ok"] else "FAIL",
        ))


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="End-to-end PLSQ recording benchmark.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[0].strip(),
    )
    ap.add_argument("--chips",           type=int,  default=None)
    ap.add_argument("--fps",             type=int,  choices=[30, 60], default=60)
    ap.add_argument("--duration",        type=float, default=60.0)
    ap.add_argument("--artsync",         action="store_true")
    ap.add_argument("--stagger-ms",      type=float, default=0.0)
    ap.add_argument("--complete-frames", choices=["on", "off"], default="on")
    ap.add_argument("--skip",            type=int,  default=0)
    ap.add_argument("--blend",           type=int,  default=0)
    ap.add_argument("--out-dir",         default=None)
    ap.add_argument("--matrix",          action="store_true")
    ap.add_argument("--quick",           action="store_true",
                    help="10 s per point instead of --duration")
    ap.add_argument("--no-content-check", action="store_true",
                    help="Skip per-frame ring comparison (faster for huge files)")
    args = ap.parse_args()

    out_dir = args.out_dir or tempfile.gettempdir()
    os.makedirs(out_dir, exist_ok=True)

    duration = 10.0 if args.quick else args.duration
    content_check = not args.no_content_check

    _log("bench_e2e.py  out_dir=%s  free=%.1f GB" % (out_dir, _drive_free_gb(out_dir)))
    _log("mem API: %s   cpu API: %s" % (
        "win32" if _HAS_WIN else ("resource" if _HAS_RES else "none"),
        "win32" if _HAS_WIN else ("resource" if _HAS_RES else "none"),
    ))

    run_kw = dict(
        complete_frames=(args.complete_frames == "on"),
        artsync=args.artsync,
        stagger_ms=args.stagger_ms,
        skip=args.skip,
        blend=args.blend,
        content_check=content_check,
    )

    if args.matrix or args.chips is None:
        results = []
        for chips, fps in MATRIX:
            r = run_one(chips, fps, duration, out_dir, **run_kw)
            results.append(r)
            time.sleep(1.0)
        # Extra run 1: 64k/60 with artsync + stagger
        r = run_one(64000, 60, duration, out_dir,
                    label="64k/60 +artsync+stagger",
                    artsync=True, stagger_ms=0.05,
                    complete_frames=(args.complete_frames == "on"),
                    skip=args.skip, blend=args.blend,
                    content_check=content_check)
        results.append(r)
        time.sleep(1.0)
        # Extra run 2: 64k/60 without assembler
        r = run_one(64000, 60, duration, out_dir,
                    label="64k/60 no-assembler",
                    complete_frames=False,
                    artsync=args.artsync, stagger_ms=args.stagger_ms,
                    skip=args.skip, blend=args.blend,
                    content_check=content_check)
        results.append(r)
        _print_table(results)
    else:
        run_one(args.chips, args.fps, duration, out_dir,
                label="%d/%d" % (args.chips, args.fps), **run_kw)


if __name__ == "__main__":
    main()
