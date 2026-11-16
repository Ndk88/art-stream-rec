"""
Art-Net server, frame assembly, recording, and PLSQ session lifecycle.
No tkinter; stdlib only.  Shared by main.py and tools/bench_e2e.py.
"""

import collections
import json
import os
import queue
import re
import socket
import struct
import threading
import time

from mapping import build_spans, effective_chips_per_universe
from plsq import PlsqWriter, build_meta

ARTNET_ID = b"Art-Net\x00"
OP_DMX  = 0x5000
OP_SYNC = 0x5200
RCVBUF_SIZE = 32 * 1024 * 1024   # 32 MiB requested; kernel may cap on Windows

FINALIZE_TIMEOUT = 120.0  # seconds before giving up on the writer thread
FIRST_FULL_TIMEOUT = 2.0  # max wait for the first complete assembled frame
FIRST_FRAME_PARTIAL_WARNING = "first frame is partial"


# ----------------------------------------------------------------------------
#  Art-Net parsing
# ----------------------------------------------------------------------------

def parse_artdmx(data):
    """ArtDMX packet -> (port_address, sequence, dmx_bytes), or None if not ArtDMX."""
    if len(data) < 18 or data[:8] != ARTNET_ID:
        return None
    if (data[8] | (data[9] << 8)) != OP_DMX:
        return None
    length = (data[16] << 8) | data[17]
    port_address = ((data[15] & 0x7F) << 8) | data[14]
    return port_address, data[12], bytes(data[18 : 18 + length])


# ----------------------------------------------------------------------------
#  FrameAssembler
# ----------------------------------------------------------------------------

class FrameAssembler:
    """
    Anti-tearing frame assembler for multi-universe sources.

    Groups incoming per-universe packets into complete source frames.
    Commits a new `committed_frame` (immutable bytes) when:
      (a) all mapped universes have been updated since the last commit, or
      (b) an ArtSync packet (OpCode 0x5200) arrives, or
      (c) a mapped universe arrives a second time before all others: commit the
          partial working frame and start a new one with the duplicate as the
          first update (counted in `partial_count`).

    `committed_frame` is replaced by a plain reference swap, which is atomic
    under CPython's GIL; the sampler thread reads it without a lock.

    When `enabled` is False the assembler is a no-op; callers fall back to the
    old sample-the-latest behavior.
    """

    def __init__(self):
        self.enabled = True
        self.committed_frame = None   # bytes | None; read by sampler thread
        self.last_full_frame = None   # bytes | None; last complete (non-partial) commit
        self.partial_count = 0        # partial commits (repeat-universe or ArtSync with partial data)
        self.short_packets = 0        # packets that covered fewer channels than mapped
        self.first_short_uni = None   # port_address of first short packet (for stats warning)
        self._working = None          # bytearray; written only by the receive thread
        self._expected = frozenset()  # universes that must all arrive for a full commit
        self._updated = set()         # universes updated since last commit
        self._spans_by_uni = {}       # port_address -> (ch_start, n_channels, frame_offset)

    def start(self, spans, stride):
        """Configure for a new recording. Must be called before on_universe()."""
        self._spans_by_uni = {uni: (ch, n, off) for uni, ch, n, off in spans}
        self._expected = frozenset(self._spans_by_uni)
        self._working = bytearray(stride)
        self._updated = set()
        self.committed_frame = None
        self.last_full_frame = None
        self.partial_count = 0
        self.short_packets = 0
        self.first_short_uni = None

    def on_universe(self, port_address, mv, pkt_len, dmx_len):
        """
        Called from the receive thread for each valid ArtDMX packet.
        mv is a memoryview of the full receive buffer; payload starts at byte 18.
        """
        if not self.enabled:
            return
        span = self._spans_by_uni.get(port_address)
        if span is None:
            return
        ch, n_ch, off = span

        if port_address in self._updated:
            self._commit(partial=True)

        src_start = 18 + ch
        src_end = src_start + n_ch
        pkt_end = 18 + dmx_len
        if src_end <= pkt_end:
            self._working[off:off + n_ch] = mv[src_start:src_end]
        else:
            # Short packet: copy available bytes, zero-fill the rest
            avail = max(0, pkt_end - src_start)
            if avail > 0:
                self._working[off:off + avail] = mv[src_start:src_start + avail]
            self._working[off + avail:off + n_ch] = bytes(n_ch - avail)
            self.short_packets += 1
            if self.first_short_uni is None:
                self.first_short_uni = port_address
        self._updated.add(port_address)

        if self._updated >= self._expected:
            self._commit(partial=False)

    def on_artsync(self):
        """Force a commit on ArtSync if at least one universe arrived."""
        if not self.enabled or not self._updated:
            return
        self._commit(partial=(self._updated < self._expected))

    def _commit(self, partial):
        frame = bytes(self._working)
        self.committed_frame = frame
        self._updated = set()
        if partial:
            self.partial_count += 1
        else:
            self.last_full_frame = frame


def short_packets_warning(short_packets, first_universe):
    """Statistics line for I-10 short packets, or None when there are none.

    A short packet carries fewer DMX channels than the input mapping expects
    for its universe; the missing channels are recorded as 0.
    """
    if not short_packets:
        return None
    if first_universe is None:
        return "short packets: %d" % short_packets
    return "short packets: %d (first universe %d)" % (short_packets, first_universe)


# ----------------------------------------------------------------------------
#  ArtNetServer
# ----------------------------------------------------------------------------

class ArtNetServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {}      # port_address -> bytes(512)
        self.uni_stats = {}   # port_address -> {"count": int, "length": int}
        self.total_packets = 0
        self.ignored_packets = 0
        self.total_bytes = 0
        self.last_packet_time = 0.0
        self.lost_packets = 0
        self.rcvbuf_actual = 0
        self.running = False
        self.error = None
        self.host = ""
        self.port = 0
        self._sock = None
        self._thread = None
        self._assembler = None
        self._seq_last = {}

    def start(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((host, port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.2)
        with self.lock:
            self.latest.clear()
            self.uni_stats.clear()
            self.total_packets = self.ignored_packets = self.total_bytes = 0
            self.last_packet_time = 0.0
            self.lost_packets = 0
            self.rcvbuf_actual = 0
        self._seq_last.clear()
        self.error = None
        self._sock, self.host, self.port, self.running = sock, host, port, True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._sock:
            self._sock.close()
        self._sock = self._thread = None

    def set_assembler(self, assembler):
        """Attach or detach a FrameAssembler. Thread-safe (guarded by lock)."""
        with self.lock:
            self._assembler = assembler

    def _loop(self):
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_SIZE)
        except OSError:
            pass
        actual = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        with self.lock:
            self.rcvbuf_actual = actual

        _buf = bytearray(2048)
        _mv = memoryview(_buf)
        sock = self._sock

        while self.running:
            try:
                n, _addr = sock.recvfrom_into(_mv)
            except socket.timeout:
                continue
            except OSError as exc:
                if self.running:
                    self.error = "Server socket error: %s" % exc
                    self.running = False
                break

            if n < 10 or _buf[:8] != ARTNET_ID:
                with self.lock:
                    self.total_packets += 1
                    self.total_bytes += n
                    self.ignored_packets += 1
                continue

            opcode = _buf[8] | (_buf[9] << 8)
            now = time.perf_counter()

            if opcode == OP_DMX and n >= 18:
                dmx_len = (_buf[16] << 8) | _buf[17]
                if 18 + dmx_len > n:
                    dmx_len = n - 18
                port_address = ((_buf[15] & 0x7F) << 8) | _buf[14]
                seq = _buf[12]

                with self.lock:
                    self.total_packets += 1
                    self.total_bytes += n
                    self.last_packet_time = now
                    if seq != 0:
                        last_s = self._seq_last.get(port_address)
                        if last_s is not None:
                            expected_s = last_s % 255 + 1
                            if expected_s != seq:
                                gap = (seq - expected_s + 255) % 255
                                if 0 < gap <= 128:
                                    self.lost_packets += gap
                        self._seq_last[port_address] = seq
                    cap = min(dmx_len, 512)
                    padded = bytes(_mv[18:18 + cap]) + b"\x00" * (512 - cap)
                    self.latest[port_address] = padded
                    st = self.uni_stats.setdefault(port_address, {"count": 0, "length": 0})
                    st["count"] += 1
                    st["length"] = dmx_len

                asm = self._assembler
                if asm is not None:
                    asm.on_universe(port_address, _mv, n, dmx_len)

            elif opcode == OP_SYNC:
                with self.lock:
                    self.total_packets += 1
                    self.total_bytes += n
                    self.last_packet_time = now
                asm = self._assembler
                if asm is not None:
                    asm.on_artsync()

            else:
                with self.lock:
                    self.total_packets += 1
                    self.total_bytes += n
                    self.ignored_packets += 1

    def snapshot(self):
        with self.lock:
            return (
                dict(self.latest),
                {k: dict(v) for k, v in self.uni_stats.items()},
                self.total_packets,
                self.ignored_packets,
                self.total_bytes,
                self.last_packet_time,
                self.lost_packets,
                self.rcvbuf_actual,
            )


# ----------------------------------------------------------------------------
#  In-recording packet loss (I-03), shared by the JSON and PLSQ paths
# ----------------------------------------------------------------------------

def start_loss_tracking(server):
    """Begin counting packet loss for a new recording; returns the baseline.

    Clears the per-universe sequence tracking (so the jump between the last
    pre-recording packet and the first recording packet is not counted) and
    returns the server's lost_packets counter at this moment. Called when the
    recording is started (Record / session.start()), not when the sampling
    clock starts after the first assembled frame: losses in between belong to
    the recording.
    """
    with server.lock:
        server._seq_last.clear()
        return server.lost_packets


def recording_lost_packets(server, baseline):
    """Sequence-gap losses counted since start_loss_tracking() returned baseline."""
    return max(0, server.snapshot()[6] - baseline)


# ----------------------------------------------------------------------------
#  Recorder
# ----------------------------------------------------------------------------

class Recorder:
    """Samples the latest server data at a fixed fps and collects frames (bytes 0..255)."""

    def __init__(self, server):
        self.server = server
        self.frames = []
        self.frames_captured = 0
        self.stride = 0
        self.fps = 30.0
        self.max_frames = 0
        self.spans = []
        self.state = "idle"
        self.missing = 0
        self.late = 0
        self.elapsed = 0.0
        self.error = None
        self.first_frame_partial = False
        self.first_full_timeout = FIRST_FULL_TIMEOUT
        self._assembler = None
        self._out_queue = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def partial_source_frames(self):
        return self._assembler.partial_count if self._assembler is not None else 0

    def start(self, spans, stride, fps, max_frames=0, wait_first_packet=True,
              assembler=None, out_queue=None):
        self.frames, self.spans, self.stride = [], spans, stride
        self.fps, self.max_frames = float(fps), int(max_frames)
        self.frames_captured = 0
        self.missing = self.late = 0
        self.elapsed = 0.0
        self.error = None
        self.first_frame_partial = False
        self._assembler = assembler
        self._out_queue = out_queue
        self._stop.clear()
        self.state = "waiting" if wait_first_packet else "recording"
        self._thread = threading.Thread(
            target=self._run, args=(wait_first_packet,), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _sample(self):
        asm = self._assembler
        if asm is not None and asm.enabled:
            frame = asm.committed_frame
            return frame, (frame is None)
        with self.server.lock:
            latest = self.server.latest
            out, miss = bytearray(self.stride), False
            for uni, ch, n, off in self.spans:
                buf = latest.get(uni)
                if buf is None:
                    miss = True
                    continue
                out[off : off + n] = buf[ch : ch + n]
        return bytes(out), miss

    def _run(self, wait_first_packet):
        try:
            self._capture(wait_first_packet)
        except Exception as exc:
            self.error = "Recorder failed: %s" % exc
        finally:
            self.state = "done"

    def _capture(self, wait_first_packet):
        first_frame = None
        if wait_first_packet:
            asm = self._assembler
            t_first_commit = None
            while not self._stop.is_set():
                if asm is not None and asm.enabled:
                    # Start on the first COMPLETE assembled frame (all mapped
                    # universes updated, or ArtSync with the full set). If only
                    # partial commits arrive for first_full_timeout seconds,
                    # start on a partial one and flag it.
                    full = asm.last_full_frame
                    if full is not None:
                        first_frame = full
                        break
                    part = asm.committed_frame
                    if part is not None:
                        now = time.perf_counter()
                        if t_first_commit is None:
                            t_first_commit = now
                        elif now - t_first_commit >= self.first_full_timeout:
                            first_frame = part
                            self.first_frame_partial = True
                            break
                else:
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
            now = time.perf_counter()
            if now - target > 1.5 * period:
                self.late += 1
            if first_frame is not None:
                frame, miss = first_frame, False
                first_frame = None
            else:
                frame, miss = self._sample()
            if frame is None:
                frame = bytes(self.stride)
            if self._out_queue is not None:
                try:
                    self._out_queue.put_nowait(frame)
                except queue.Full:
                    self.error = (
                        "Queue overflow: disk too slow — PLSQ recording stopped."
                    )
                    break
            else:
                self.frames.append(frame)
            self.frames_captured += 1
            if miss:
                self.missing += 1
            k += 1
            self.elapsed = now - t0
            if self.max_frames and k >= self.max_frames:
                break


# ----------------------------------------------------------------------------
#  Post-processing helpers
# ----------------------------------------------------------------------------

def postprocess(frames, skip=0, blend=0, final=None):
    """
    Trim and loop-blend a recording.

    skip   - number of first recorded frames to drop.
    blend  - number of extra frames recorded after the final frame.
    final  - number of frames in the result (default: everything after skip and blend).
    """
    frames = frames[skip:]
    if final is None:
        final = len(frames) - blend
    if final <= 0 or final + blend > len(frames):
        raise ValueError(
            "Not enough recorded frames: need %d, have %d"
            % (max(final, 0) + blend + skip, len(frames) + skip)
        )
    if blend > final:
        raise ValueError("Loop blend must not exceed the number of exported frames")
    out = [bytearray(f) for f in frames[:final]]
    for i in range(blend):
        w = i / float(blend)
        tail, head = frames[final + i], out[i]
        for j in range(len(head)):
            head[j] = int(round(tail[j] * (1.0 - w) + head[j] * w))
    return [bytes(f) for f in out]


def _blend_frames(tail_frames, head_frames):
    """Blend tail into head: out[i] = round(tail[i]*(1-w) + head[i]*w), w = i/len."""
    n = len(head_frames)
    result = []
    for i in range(n):
        w = i / float(n)
        t, h = tail_frames[i], head_frames[i]
        out = bytearray(len(h))
        for j in range(len(h)):
            out[j] = int(round(t[j] * (1.0 - w) + h[j] * w))
        result.append(bytes(out))
    return result


# ----------------------------------------------------------------------------
#  PlsqStreamWriter
# ----------------------------------------------------------------------------

class PlsqStreamWriter:
    """Consumes frames from a queue, writes them to a PlsqWriter (streaming .plsq).

    Call start(q), then join() after the sampler sends the None sentinel.
    After join(): check error; if None, frames_written and bytes_written are valid.
    """

    def __init__(self, writer, skip, blend, stride):
        self._writer = writer
        self._skip = skip
        self._blend = blend
        self._stride = stride
        self.frames_written = 0
        self.bytes_written = 0
        self.error = None
        self._thread = None

    def start(self, q):
        self._q = q
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            self._write_loop()
        except Exception as exc:
            self.error = str(exc)

    def _write_loop(self):
        skip_left = self._skip
        blend = self._blend
        delay = collections.deque()

        while True:
            try:
                frame = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            self._q.task_done()

            if frame is None:
                break

            if skip_left > 0:
                skip_left -= 1
                continue

            if blend > 0:
                delay.append(frame)
                if len(delay) > blend:
                    to_write = delay.popleft()
                    self._writer.append_frame(to_write)
                    self.frames_written += 1
                    self.bytes_written += self._stride
            else:
                self._writer.append_frame(frame)
                self.frames_written += 1
                self.bytes_written += self._stride

        tail = list(delay)
        if blend > 0 and tail and self.frames_written > 0:
            actual_blend = min(blend, len(tail), self.frames_written)
            t_start = time.perf_counter()
            head = self._writer.read_frames(0, actual_blend)
            blended = _blend_frames(tail[:actual_blend], head)
            elapsed = time.perf_counter() - t_start
            if elapsed > 10.0:
                self.error = (
                    "Loop blend took %.1f s (> 10 s limit). "
                    "Consider reducing blend length or chip count." % elapsed
                )
                return
            self._writer.rewrite_frames(0, blended)


# ----------------------------------------------------------------------------
#  JSON export helpers
# ----------------------------------------------------------------------------

_LUT = [json.dumps(round(i / 255.0, 3)) for i in range(256)]


def write_json(path, meta, frames):
    """Write a JSON sequence file.

    meta must be the dict returned by build_meta() with file_format="json"
    (27 required keys, plus the optional keys when given).
    Raises ValueError when universes_count > 2 (D3).
    """
    n_uni = int(meta["input_mapping_universes_count"])
    if n_uni > 2:
        raise ValueError(
            "JSON export is not available for 3 or more universes (got %d). "
            "Use PLSQ format instead." % n_uni
        )
    lines = ["{"]
    for k, v in meta.items():
        lines.append(
            "  %s: %s,"
            % (json.dumps(k, ensure_ascii=False), json.dumps(v, ensure_ascii=False))
        )
    lines.append('  "sequence": {')
    lines.append('    "data": [')
    rows = [", ".join(_LUT[b] for b in fr) for fr in frames]
    lines.append(",\n".join("      " + r for r in rows))
    lines.append("    ]")
    lines.append("  }")
    lines.append("}")
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("\n".join(lines) + "\n")
    return os.path.getsize(path)


def path_key(path):
    """Normalized form of a path, for comparing two paths."""
    return os.path.normcase(os.path.abspath(path))


def next_free_path(path):
    """sequence_1.json -> sequence_2.json (first name that does not exist yet)."""
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    m = re.match(r"^(.*?)(\d+)$", stem)
    if m:
        base, num, width = m.group(1), int(m.group(2)), len(m.group(2))
    else:
        base, num, width = stem + "_", 0, 1
    while True:
        num += 1
        cand = os.path.join(folder, "%s%s%s" % (base, str(num).zfill(width), ext))
        if not os.path.exists(cand):
            return cand


# ----------------------------------------------------------------------------
#  PlsqRecordingSession
# ----------------------------------------------------------------------------

class PlsqRecordingSession:
    """
    Non-GUI PLSQ recording lifecycle shared by App and bench_e2e.

    States: idle → waiting | recording → finalizing → done | error

    Usage (non-GUI / bench):
        sess = PlsqRecordingSession(server, chips=N, ...)
        sess.start()
        while sess.poll() not in ("done", "error"):
            time.sleep(0.1)
        if sess.state == "done":
            r = sess.result()  # dict
    """

    def __init__(
        self, server, *,
        chips, fps, out_path, spans, stride,
        start_universe, start_channel, eff_cpu,
        skip=0, blend=0, final_frames=0,
        wait_first_packet=True, complete_frames=True,
        meta_extra=None,
    ):
        self._server = server
        self._chips = chips
        self._fps = fps
        self.out_path = out_path
        self.spans = spans
        self.stride = stride
        self._start_universe = start_universe
        self._start_channel = start_channel
        self._eff_cpu = eff_cpu
        self.skip = skip
        self.blend = blend
        self.final_frames = final_frames
        self._wait_first_packet = wait_first_packet
        self._complete_frames = complete_frames
        self._meta_extra = meta_extra or {}

        self.state = "idle"
        self.error = None
        self.frames_written = 0
        self.blend_applied = 0
        self.blend_warning = None

        self._assembler = None
        self._queue = None
        self._stream_writer = None
        self._plsq_writer = None
        self._recorder = None
        self._part_path = out_path + ".part"
        self._sentinel_sent = False
        self._finalize_deadline = None
        self._lost_packets_start = 0
        self._result = None

    # ---- public properties --------------------------------------------------

    @property
    def recorder(self):
        return self._recorder

    @property
    def assembler(self):
        return self._assembler

    @property
    def queue_size(self):
        return self._queue.qsize() if self._queue is not None else 0

    @property
    def queue_capacity(self):
        return self._queue.maxsize if self._queue is not None else 0

    @property
    def bytes_written(self):
        return self._stream_writer.bytes_written if self._stream_writer else 0

    # ---- lifecycle ----------------------------------------------------------

    def start(self):
        """Set up and start the recording pipeline. Non-blocking."""
        if self.state != "idle":
            raise RuntimeError("Session already started (state=%s)" % self.state)

        # Set up frame assembler
        if self._complete_frames:
            self._assembler = FrameAssembler()
            self._assembler.start(self.spans, self.stride)
        self._server.set_assembler(self._assembler)

        # Open PLSQ writer
        self._plsq_writer = PlsqWriter()
        folder = os.path.dirname(os.path.abspath(self._part_path))
        try:
            os.makedirs(folder, exist_ok=True)
            self._plsq_writer.open(
                self._part_path, self._chips, int(self._fps),
                self._start_universe, self._start_channel, self._eff_cpu,
            )
        except OSError as exc:
            self._server.set_assembler(None)
            self._assembler = None
            self.state = "error"
            self.error = "Could not open recording file: %s" % exc
            return

        # I-03: reset sequence tracking and take the lost_packets baseline
        self._lost_packets_start = start_loss_tracking(self._server)

        # Queue + stream writer
        q_size = max(300, int(2 * self._fps) + 20)
        self._queue = queue.Queue(maxsize=q_size)
        self._stream_writer = PlsqStreamWriter(
            self._plsq_writer, self.skip, self.blend, self.stride
        )
        self._stream_writer.start(self._queue)

        # Recorder
        self._recorder = Recorder(self._server)
        max_frames = (
            (self.skip + self.final_frames + self.blend) if self.final_frames else 0
        )
        self._recorder.start(
            self.spans, self.stride, self._fps, max_frames,
            self._wait_first_packet, self._assembler, self._queue,
        )
        self.state = "waiting" if self._wait_first_packet else "recording"

    def request_stop(self):
        """Signal the recorder to stop. Non-blocking."""
        if self._recorder and self.state in ("waiting", "recording"):
            self._recorder.stop()

    def poll(self):
        """Drive the state machine. Must be called repeatedly. Returns current state."""
        if self.state in ("idle", "done", "error"):
            return self.state

        # Propagate recorder error
        if self._recorder and self._recorder.error and self.state != "finalizing":
            self.error = self._recorder.error
            self._begin_finalize()
            return self.state

        # Update recording state from recorder
        if self.state == "waiting" and self._recorder:
            if self._recorder.state == "recording":
                self.state = "recording"

        # Detect recorder completion → begin finalization
        if self.state in ("waiting", "recording") and self._recorder:
            if self._recorder.state == "done":
                self._begin_finalize()
                return self.state

        # Drive finalization
        if self.state == "finalizing":
            self._poll_finalize()

        return self.state

    # ---- finalization internals ---------------------------------------------

    def _begin_finalize(self):
        self.state = "finalizing"
        self._server.set_assembler(None)
        self._sentinel_sent = False
        self._finalize_deadline = time.monotonic() + FINALIZE_TIMEOUT
        # First attempt to send the sentinel immediately
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
                self._sentinel_sent = True
            except queue.Full:
                pass

    def _poll_finalize(self):
        """Non-blocking finalization tick. Called by poll()."""
        # Retry sentinel if queue was full
        if not self._sentinel_sent and self._queue is not None:
            try:
                self._queue.put_nowait(None)
                self._sentinel_sent = True
            except queue.Full:
                return  # Still full — try next tick

        # Timeout guard
        if time.monotonic() > self._finalize_deadline:
            self.state = "error"
            self.error = (
                "PLSQ finalization timed out after %.0f s — partial file kept at %s"
                % (FINALIZE_TIMEOUT, self._part_path)
            )
            try:
                self._plsq_writer.abort()
            except Exception:
                pass
            return

        # Wait for writer thread to finish
        sw = self._stream_writer
        if sw is not None and sw.is_alive:
            return  # Still writing / blending — check again next tick

        self._complete_finalize()

    def _complete_finalize(self):
        """Writer thread ended: build meta, finalize file, rename .part → target."""
        sw = self._stream_writer

        if sw is not None and sw.error:
            self.state = "error"
            self.error = "PLSQ writer error: %s — partial file kept at %s" % (
                sw.error, self._part_path,
            )
            try:
                self._plsq_writer.abort()
            except Exception:
                pass
            return

        final = sw.frames_written if sw is not None else 0
        if final == 0:
            self.state = "error"
            self.error = (
                "PLSQ recording produced 0 exported frames "
                "(check skip/blend settings) — partial file kept at %s" % self._part_path
            )
            try:
                self._plsq_writer.abort()
            except Exception:
                pass
            return

        # D11: blend_applied = min(blend, final)
        blend_applied = min(self.blend, final) if self.blend else 0
        if self.blend and blend_applied < self.blend:
            self.blend_warning = (
                "Loop blend reduced from %d to %d frames: the recording is too short"
                % (self.blend, blend_applied)
            )

        self.frames_written = final
        self.blend_applied = blend_applied

        # Lost packets in-recording only
        lost_recording = recording_lost_packets(
            self._server, self._lost_packets_start
        )

        rec = self._recorder
        asm = self._assembler
        meta_kw = dict(self._meta_extra)
        meta = build_meta(
            file_format="plsq",
            fps=int(self._fps),
            frame_count=final,
            frame_stride=self.stride,
            start_universe=self._start_universe,
            start_channel=self._start_channel,
            chips_per_universe=self._eff_cpu,
            universes_count=len(self.spans),
            model_chips_count=self._chips,
            post_skip_frames=self.skip,
            post_loop_blend_frames=blend_applied,
            recorded_frames_raw=rec.frames_captured if rec else final,
            missing_frames=rec.missing if rec else 0,
            late_frames=rec.late if rec else 0,
            partial_source_frames=rec.partial_source_frames if rec else 0,
            lost_packets=lost_recording,
            short_packets=asm.short_packets if asm else None,
            first_frame_partial=rec.first_frame_partial if rec else False,
            **meta_kw,
        )

        try:
            self._plsq_writer.finalize(meta)
        except Exception as exc:
            self.state = "error"
            self.error = "PLSQ finalize error: %s — partial file kept at %s" % (
                exc, self._part_path,
            )
            return

        try:
            os.replace(self._part_path, self.out_path)
        except OSError as exc:
            self.state = "error"
            self.error = "Could not rename %s → %s: %s" % (
                self._part_path, self.out_path, exc,
            )
            return

        self._result = {
            "path": self.out_path,
            "frames_written": final,
            "blend_applied": blend_applied,
            "blend_warning": self.blend_warning,
            "lost_packets": lost_recording,
            "missing_frames": rec.missing if rec else 0,
            "late_frames": rec.late if rec else 0,
            "partial_source_frames": rec.partial_source_frames if rec else 0,
            "frames_captured": rec.frames_captured if rec else final,
            "first_frame_partial": rec.first_frame_partial if rec else False,
            "short_packets": asm.short_packets if asm else 0,
            "first_short_uni": asm.first_short_uni if asm else None,
        }
        self.state = "done"

    def result(self):
        """After state == 'done', return outcome dict. None if error or not done."""
        return self._result

    def abort(self):
        """Emergency stop: stop recorder, close writer, keep .part file."""
        if self._recorder:
            self._recorder.stop()
        self._server.set_assembler(None)
        if self._plsq_writer:
            try:
                self._plsq_writer.abort()
            except Exception:
                pass
        if self.state not in ("done", "error"):
            self.state = "error"
        if not self.error:
            self.error = "Aborted"
