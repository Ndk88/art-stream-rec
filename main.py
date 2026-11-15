#!/usr/bin/env python3
"""
Art-Net JSON sequence recorder (local UDP server with a small GUI).

Listens for Art-Net (ArtDMX) packets on a given IP/port (default 127.0.0.1:6454),
records the stream from TouchDesigner / Madrix 5 at a fixed frame rate and saves it as JSON:
a header (all values stored as strings) plus "sequence": {"data": [...]} with floats in 0..1.

Run:      python main.py        (Python 3.9+, no extra packages)
Build:    pyinstaller --onefile --noconsole main.py
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

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # the core can still be imported without tkinter (for tests)
    tk = None

from mapping import build_spans, effective_chips_per_universe
from plsq import PlsqWriter, build_meta

APPV = "1.2.0"
ARTNET_ID = b"Art-Net\x00"
OP_DMX  = 0x5000
OP_SYNC = 0x5200
RCVBUF_SIZE = 32 * 1024 * 1024   # 32 MiB requested; kernel may cap on Windows


# ----------------------------------------------------------------------------
#  Core: Art-Net parsing, receiving, recording, post-processing, export
# ----------------------------------------------------------------------------
def parse_artdmx(data):
    """ArtDMX packet -> (port_address, sequence, dmx_bytes), or None if it is not ArtDMX."""
    if len(data) < 18 or data[:8] != ARTNET_ID:
        return None
    if (data[8] | (data[9] << 8)) != OP_DMX:
        return None
    length = (data[16] << 8) | data[17]
    port_address = ((data[15] & 0x7F) << 8) | data[14]  # Net:SubNet:Universe (0-based)
    return port_address, data[12], bytes(data[18 : 18 + length])


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
        self.partial_count = 0        # partial commits (repeat-universe or ArtSync with partial data)
        self._working = None          # bytearray; written only by the receive thread
        self._expected = frozenset()  # universes that must all arrive for a full commit
        self._updated = set()         # universes updated since last commit
        self._spans_by_uni = {}       # port_address -> (ch_start, n_channels, frame_offset)

    def start(self, spans, stride):
        """Configure for a new recording. Must be called before the server begins
        calling on_universe()."""
        self._spans_by_uni = {uni: (ch, n, off) for uni, ch, n, off in spans}
        self._expected = frozenset(self._spans_by_uni)
        self._working = bytearray(stride)
        self._updated = set()
        self.committed_frame = None
        self.partial_count = 0

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
            # Universe repeated before all mapped universes arrived → partial commit
            self._commit(partial=True)

        src_start = 18 + ch
        src_end = src_start + n_ch
        if src_end <= 18 + dmx_len:
            self._working[off:off + n_ch] = mv[src_start:src_end]
        self._updated.add(port_address)

        if self._updated >= self._expected:
            self._commit(partial=False)

    def on_artsync(self):
        """Force a commit on ArtSync if at least one universe arrived."""
        if not self.enabled or not self._updated:
            return
        self._commit(partial=False)

    def _commit(self, partial):
        self.committed_frame = bytes(self._working)  # atomic reference swap under GIL
        self._updated = set()
        if partial:
            self.partial_count += 1


class ArtNetServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {}      # port_address -> bytes(512), most recent data
        self.uni_stats = {}   # port_address -> {"count": int, "length": int}
        self.total_packets = 0
        self.ignored_packets = 0
        self.total_bytes = 0
        self.last_packet_time = 0.0
        self.lost_packets = 0   # approximate sequence-gap count
        self.rcvbuf_actual = 0  # actual SO_RCVBUF reported by the kernel
        self.running = False
        self.error = None
        self.host = ""
        self.port = 0
        self._sock = None
        self._thread = None
        self._assembler = None          # FrameAssembler | None
        self._seq_last = {}             # port_address -> last seq byte (1..255)

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
        # Request a large receive buffer before the first recvfrom
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

                asm = self._assembler   # read under GIL; atomic
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



class Recorder:
    """Samples the latest server data at a fixed fps and collects frames (bytes 0..255)."""

    def __init__(self, server):
        self.server = server
        self.frames = []
        self.frames_captured = 0  # counts all frames put to frames or out_queue
        self.stride = 0
        self.fps = 30.0
        self.max_frames = 0
        self.spans = []
        self.state = "idle"  # idle | waiting | recording | done
        self.missing = 0    # frames with at least one universe missing
        self.late = 0       # frames sampled more than 1.5 periods late
        self.elapsed = 0.0
        self.error = None
        self._assembler = None  # FrameAssembler | None
        self._out_queue = None  # queue.Queue | None (PLSQ streaming mode)
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
            now = time.perf_counter()
            if now - target > 1.5 * period:
                self.late += 1
            frame, miss = self._sample()
            if frame is None:
                frame = bytes(self.stride)  # zeros while no frame committed yet
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


def postprocess(frames, skip=0, blend=0, final=None):
    """
    Trim and loop-blend a recording.

    skip   - number of first recorded frames to drop.
    blend  - number of extra frames recorded after the final frame; the first `blend` frames of the
             result fade from that continuation into the original frames, so the last frame flows into
             the first one without a jump.
    final  - number of frames in the result (default: everything that is left after skip and blend).
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

            if frame is None:  # sentinel: sampler finished
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

        # Apply loop blend: rewrite the first min(blend, frames_written) frames
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


_LUT = [json.dumps(round(i / 255.0, 3)) for i in range(256)]  # 0 -> "0.0", 255 -> "1.0"


def write_json(path, meta, frames):
    """Write a JSON sequence file.

    meta must be the 27-key dict returned by build_meta() with file_format="json".
    Raises ValueError when universes_count > 2 (D3: JSON only for small installs).
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
#  Window
# ----------------------------------------------------------------------------
BG, FIELD, FIELD_RO, BORDER = "#141414", "#2b2b2b", "#222222", "#444444"
FG, FG_DIM = "#e6e6e6", "#8f8f8f"
BTN, BTN_HOVER = "#343434", "#404040"
RED, RED_BORDER = "#8f2d2d", "#5e1c1c"
ERR_FG, WARN_FG = "#ff8f8f", "#e3b341"
MAX_ERRORS = 6
FIELDS = [
    ("Model Name", "model_name", "Crystal Links Model 1"),
    ("Model Chips Tag", "model_chips_tag", "crystal_links_model_1_chip"),
    ("Model Chips Amount", "model_chips_count", "104"),
    ("Sequence Name", "sequence_name", "Random Colored Loop Flashing"),
    ("Sequence Version", "sequence_version", "1.0"),
    ("Sequence Duration (0 = manual)", "sequence_duration_sec", "6.0"),
    # Sequence Fps rendered as radio buttons (30 / 60) — not an Entry
    ("Sequence Frame Count", "sequence_frame_count", ""),
    ("Sequence Frame Stride", "sequence_frame_stride", ""),
    ("Developer Notes", "developer_notes", "Notes by developer or smb who worked..."),
]
READONLY = {"sequence_frame_count", "sequence_frame_stride"}
MAP_READONLY = {"channels_per_chip"}
MAPPING = [
    ("Start Universe", "start_universe", "1"),
    ("Start channel", "start_channel", "1"),
    ("Channels per chip", "channels_per_chip", "4"),
    ("Chips per universe (0 = auto)", "chips_per_universe", "0"),
]


class ChoiceDialog(tk.Toplevel if tk else object):
    """Small dark modal dialog with a message and a row of buttons; result is the button value."""

    def __init__(self, parent, title, message, buttons):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        tk.Label(
            self,
            text=message,
            bg=BG,
            fg=FG,
            justify="left",
            anchor="w",
            wraplength=500,
            padx=22,
            pady=18,
            font=("Segoe UI", 10),
        ).pack(fill="x")
        row = tk.Frame(self, bg=BG)
        row.pack(padx=16, pady=(0, 16))
        for label, value in buttons:
            tk.Button(
                row,
                text=label,
                command=lambda v=value: self._pick(v),
                bg=BTN,
                fg=FG,
                activebackground=BTN_HOVER,
                activeforeground=FG,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=BORDER,
                cursor="hand2",
                font=("Segoe UI", 10),
            ).pack(side="left", padx=5, ipadx=12, ipady=6)
        self.protocol("WM_DELETE_WINDOW", lambda: self._pick(None))
        self.bind("<Escape>", lambda _e: self._pick(None))
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()

    def _pick(self, value):
        self.result = value
        self.destroy()


def ask_choice(parent, title, message, buttons):
    dlg = ChoiceDialog(parent, title, message, buttons)
    parent.wait_window(dlg)
    return dlg.result


class App(tk.Tk if tk else object):
    def __init__(self):
        super().__init__()
        self.title(f"Art Stream Sequence Recorder v.{APPV}")
        self.configure(bg=BG)
        self.geometry("1140x900")
        self.minsize(1000, 860)
        self.server = ArtNetServer()
        self.rec = Recorder(self.server)
        self.vars = {k: tk.StringVar(value=d) for _, k, d in FIELDS}
        self.map_vars = {k: tk.StringVar(value=d) for _, k, d in MAPPING}
        self.host_var, self.port_var = tk.StringVar(value="127.0.0.1"), tk.StringVar(
            value="6454"
        )
        self.path_var = tk.StringVar(value=os.path.join(os.getcwd(), "sequence_1.plsq"))
        self.fps_var = tk.IntVar(value=30)
        self.fmt_var = tk.StringVar(value="plsq")
        self.universes_var = tk.StringVar(value="")
        self.eff_cpu_var = tk.StringVar(value="")
        self.wait_var = tk.BooleanVar(value=True)
        self.complete_frames_var = tk.BooleanVar(value=True)
        self.skip_on, self.skip_n = tk.BooleanVar(value=False), tk.StringVar(value="1")
        self.blend_on, self.blend_n = tk.BooleanVar(value=False), tk.StringVar(
            value="30"
        )
        self.last_saved = ""
        self.errors = []  # [(time, message)] shown in the statistics block
        self.saved_path = None  # file written by the last successful save
        self.pending = None  # finished recording that has not been saved yet (JSON)
        self._approved = None  # path key the user agreed to overwrite
        self._was_recording = False
        self._job = None  # settings of the current recording (target frames, skip, blend)
        # PLSQ streaming state
        self._plsq_sw = None      # PlsqStreamWriter | None
        self._plsq_writer = None  # PlsqWriter | None
        self._plsq_queue = None   # queue.Queue | None
        self._plsq_part = None    # path to .part file
        self._plsq_target = None  # final target path
        self._prev = (time.perf_counter(), 0, {})
        self._rates = (0.0, {})
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(250, self._refresh)

    # ---- widgets -------------------------------------------------------------
    def _lbl(self, parent, text):
        return tk.Label(
            parent,
            text=text,
            bg=FIELD,
            fg=FG,
            anchor="w",
            padx=6,
            pady=5,
            highlightthickness=1,
            highlightbackground=BORDER,
        )

    def _entry(self, parent, var, readonly=False, width=None):
        e = tk.Entry(
            parent,
            textvariable=var,
            bg=FIELD_RO if readonly else FIELD,
            fg=FG_DIM if readonly else FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor="#8a8a8a",
            readonlybackground=FIELD_RO,
            disabledbackground=FIELD_RO,
            disabledforeground=FG_DIM,
            selectbackground="#555555",
            selectforeground=FG,
            state="readonly" if readonly else "normal",
        )
        if width:
            e.config(width=width)
        return e

    def _btn(self, parent, command):
        return tk.Button(
            parent,
            command=command,
            bg=BTN,
            fg=FG,
            activebackground=BTN_HOVER,
            activeforeground=FG,
            disabledforeground=FG_DIM,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            font=("Segoe UI", 11),
            cursor="hand2",
        )

    def _check(self, parent, text, var, command=None, boxed=False):
        bg = FIELD if boxed else BG
        kw = (
            dict(highlightthickness=1, highlightbackground=BORDER, padx=6, pady=4)
            if boxed
            else {}
        )
        return tk.Checkbutton(
            parent,
            text=text,
            variable=var,
            command=command,
            bg=bg,
            fg=FG,
            selectcolor=FIELD,
            activebackground=bg,
            activeforeground=FG,
            anchor="w",
            **kw,
        )

    def _build(self):
        outer = tk.Frame(
            self, bg=BG, highlightthickness=2, highlightbackground="#bdbdbd"
        )
        outer.pack(fill="both", expand=True, padx=14, pady=14)
        outer.columnconfigure(0, weight=11, uniform="cols")
        outer.columnconfigure(1, weight=9, uniform="cols")
        outer.rowconfigure(0, weight=1)

        left = tk.Frame(outer, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(22, 12), pady=12)
        left.columnconfigure(1, weight=1)
        title = dict(bg=BG, fg=FG, font=("Segoe UI", 11))
        tk.Label(left, text="JSON Fields:", **title).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        _row = 1
        for _label, _key, _d in FIELDS:
            self._lbl(left, _label).grid(
                row=_row, column=0, sticky="nsew", padx=(0, 10), pady=1
            )
            self._entry(left, self.vars[_key], readonly=_key in READONLY).grid(
                row=_row, column=1, sticky="nsew", pady=1, ipady=5
            )
            _row += 1
            if _key == "sequence_duration_sec":
                self._lbl(left, "Sequence Fps").grid(
                    row=_row, column=0, sticky="nsew", padx=(0, 10), pady=1
                )
                _fps_frm = tk.Frame(
                    left,
                    bg=FIELD,
                    highlightthickness=1,
                    highlightbackground=BORDER,
                )
                for _fps_val in (30, 60):
                    tk.Radiobutton(
                        _fps_frm,
                        text=str(_fps_val),
                        variable=self.fps_var,
                        value=_fps_val,
                        bg=FIELD,
                        fg=FG,
                        selectcolor=FIELD,
                        activebackground=FIELD,
                        activeforeground=FG,
                        highlightthickness=0,
                    ).pack(side="left", padx=(8, 0), pady=4)
                _fps_frm.grid(row=_row, column=1, sticky="nsew", pady=1)
                _row += 1
        r = _row
        tk.Label(left, text="Input mapping:", **title).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(14, 6)
        )
        for j, (label, key, _d) in enumerate(MAPPING):
            self._lbl(left, label).grid(
                row=r + 1 + j, column=0, sticky="nsew", padx=(0, 10), pady=1
            )
            if key == "chips_per_universe":
                _cpu_frm = tk.Frame(left, bg=BG)
                _cpu_frm.grid(row=r + 1 + j, column=1, sticky="nsew", pady=1)
                _cpu_frm.columnconfigure(0, weight=1)
                self._entry(_cpu_frm, self.map_vars[key]).grid(
                    row=0, column=0, sticky="ew", ipady=5
                )
                tk.Label(
                    _cpu_frm,
                    textvariable=self.eff_cpu_var,
                    bg=BG,
                    fg=FG_DIM,
                    padx=8,
                    font=("Segoe UI", 9),
                ).grid(row=0, column=1, sticky="w")
            else:
                self._entry(
                    left, self.map_vars[key], readonly=key in MAP_READONLY
                ).grid(row=r + 1 + j, column=1, sticky="nsew", pady=1, ipady=5)
        _r_univ = r + 1 + len(MAPPING)
        self._lbl(left, "Universes (calculated)").grid(
            row=_r_univ, column=0, sticky="nsew", padx=(0, 10), pady=1
        )
        self._entry(left, self.universes_var, readonly=True).grid(
            row=_r_univ, column=1, sticky="nsew", pady=1, ipady=5
        )
        r2 = _r_univ + 1
        tk.Label(left, text="Post-processing (off by default):", **title).grid(
            row=r2, column=0, columnspan=2, sticky="w", pady=(14, 6)
        )
        self._check(
            left, "Skip first frames (count)", self.skip_on, self._sync_post, boxed=True
        ).grid(row=r2 + 1, column=0, sticky="nsew", padx=(0, 10), pady=1)
        self.skip_entry = self._entry(left, self.skip_n)
        self.skip_entry.grid(row=r2 + 1, column=1, sticky="nsew", pady=1, ipady=5)
        self._check(
            left, "Loop blend (frames)", self.blend_on, self._sync_post, boxed=True
        ).grid(row=r2 + 2, column=0, sticky="nsew", padx=(0, 10), pady=1)
        self.blend_entry = self._entry(left, self.blend_n)
        self.blend_entry.grid(row=r2 + 2, column=1, sticky="nsew", pady=1, ipady=5)
        tk.Label(
            left,
            text="Extra frames are recorded automatically: skipped ones at the start, blend frames at the end.",
            bg=BG,
            fg=FG_DIM,
            anchor="w",
            justify="left",
            wraplength=470,
            font=("Segoe UI", 9),
        ).grid(row=r2 + 3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        fmt_frm = tk.Frame(left, bg=BG)
        fmt_frm.grid(row=r2 + 4, column=0, columnspan=2, sticky="ew", pady=(10, 2))
        tk.Label(fmt_frm, text="Output format:", bg=BG, fg=FG).pack(side="left")
        tk.Radiobutton(
            fmt_frm,
            text=".plsq",
            variable=self.fmt_var,
            value="plsq",
            command=self._on_fmt_change,
            bg=BG,
            fg=FG,
            selectcolor=FIELD,
            activebackground=BG,
            activeforeground=FG,
            highlightthickness=0,
        ).pack(side="left", padx=(10, 0))
        self._json_radio = tk.Radiobutton(
            fmt_frm,
            text="JSON (.json)",
            variable=self.fmt_var,
            value="json",
            command=self._on_fmt_change,
            bg=BG,
            fg=FG,
            selectcolor=FIELD,
            activebackground=BG,
            activeforeground=FG,
            highlightthickness=0,
        )
        self._json_radio.pack(side="left", padx=(8, 0))
        tk.Label(
            fmt_frm,
            text="  JSON: up to 2 universes only",
            bg=BG,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        ).pack(side="left")
        pf = tk.Frame(left, bg=BG)
        pf.grid(row=r2 + 5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        pf.columnconfigure(0, weight=1)
        self._entry(pf, self.path_var).grid(row=0, column=0, sticky="ew", ipady=6)
        tk.Button(
            pf,
            text="Browse...",
            command=self._browse,
            bg=BTN,
            fg=FG,
            relief="flat",
            cursor="hand2",
            activebackground=BTN_HOVER,
            activeforeground=FG,
            highlightthickness=1,
            highlightbackground=BORDER,
        ).grid(row=0, column=1, padx=(10, 0), ipady=3, ipadx=8)
        self._sync_post()

        right = tk.Frame(outer, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 22), pady=12)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        ipf = tk.Frame(right, bg=BG)
        ipf.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        tk.Label(ipf, text="IP", bg=BG, fg=FG).pack(side="left")
        self._entry(ipf, self.host_var, width=14).pack(
            side="left", padx=(6, 14), ipady=3
        )
        tk.Label(ipf, text="UDP port", bg=BG, fg=FG).pack(side="left")
        self._entry(ipf, self.port_var, width=7).pack(side="left", padx=(6, 0), ipady=3)
        self.btn_server = self._btn(right, self._toggle_server)
        self.btn_server.grid(row=1, column=0, sticky="ew", ipady=14, pady=(0, 14))
        self.stats = tk.Text(
            right,
            bg=FIELD,
            fg=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            font=("Consolas", 10),
            wrap="word",
            height=12,
            width=20,
            padx=10,
            pady=8,
            state="disabled",
            cursor="arrow",
        )
        self.stats.tag_configure("err", foreground=ERR_FG)
        self.stats.tag_configure("warn", foreground=WARN_FG)
        self.stats.grid(row=2, column=0, sticky="nsew", pady=(0, 14))
        self.btn_rec = self._btn(right, self._toggle_record)
        self.btn_rec.grid(row=3, column=0, sticky="ew", ipady=16)
        self._check(right, "Start recording on first packet", self.wait_var).grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )
        self._check(
            right,
            "Wait for complete source frames (recommended)",
            self.complete_frames_var,
        ).grid(row=5, column=0, sticky="w", pady=(2, 0))
        self._update_buttons()

    def _sync_post(self):
        self.skip_entry.config(state="normal" if self.skip_on.get() else "disabled")
        self.blend_entry.config(state="normal" if self.blend_on.get() else "disabled")

    def _on_fmt_change(self):
        """Swap path extension when the format radio button changes."""
        p = self.path_var.get().strip()
        base, ext = os.path.splitext(p)
        new_fmt = self.fmt_var.get()
        if new_fmt == "plsq" and ext.lower() == ".json":
            self.path_var.set(base + ".plsq")
        elif new_fmt == "json" and ext.lower() == ".plsq":
            self.path_var.set(base + ".json")

    # ---- errors (shown in the statistics block) ---------------------------------
    def _err(self, msg):
        self.errors.append((time.strftime("%H:%M:%S"), msg))
        del self.errors[:-MAX_ERRORS]

    def _clear_errors(self):
        self.errors = []

    # ---- actions -------------------------------------------------------------
    def _browse(self):
        if self.fmt_var.get() == "plsq":
            default_ext = ".plsq"
            ftypes = [("PLSQ Sequence", "*.plsq"), ("All files", "*.*")]
        else:
            default_ext = ".json"
            ftypes = [("JSON Sequence", "*.json"), ("All files", "*.*")]
        p = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            filetypes=ftypes,
            initialfile=os.path.basename(self.path_var.get()),
            initialdir=os.path.dirname(self.path_var.get()) or None,
        )
        if p:
            self.path_var.set(p)

    def _ints(self):
        try:
            return {k: int(v.get().strip()) for k, v in self.map_vars.items()}
        except ValueError:
            raise ValueError("Input mapping fields must be whole numbers")

    def _post_values(self):
        try:
            skip = int(self.skip_n.get().strip()) if self.skip_on.get() else 0
            blend = int(self.blend_n.get().strip()) if self.blend_on.get() else 0
        except ValueError:
            raise ValueError("Skip and blend values must be whole numbers")
        if skip < 0 or blend < 0:
            raise ValueError("Skip and blend values must be 0 or greater")
        return skip, blend

    def _toggle_server(self):
        if self.server.running:
            if self.rec.state in ("waiting", "recording"):
                self.rec.stop()
            self.server.stop()
        else:
            try:
                self.server.start(self.host_var.get().strip(), int(self.port_var.get()))
                self._clear_errors()
            except (OSError, ValueError) as e:
                self._err(
                    "Could not start the server: %s. The port may already be in use "
                    "(for example, Unreal Engine listens on the same Art-Net port): "
                    "choose another IP/port." % e
                )
        self._update_buttons()

    def _toggle_record(self):
        if self.rec.state in ("waiting", "recording"):
            self.rec.stop()
            return
        if not self.server.running:
            self._err("Cannot record: start the server first.")
            return
        try:
            m = self._ints()
            skip, blend = self._post_values()
            chips = int(self.vars["model_chips_count"].get())
            fps = self.fps_var.get()
            dur = float(self.vars["sequence_duration_sec"].get().replace(",", ".") or 0)
            if chips < 1 or chips > 64000:
                raise ValueError(
                    "Model Chips Amount must be between 1 and 64,000 (got %d)" % chips
                )
            if dur < 0:
                raise ValueError("Duration must be 0 or greater")
            su = m["start_universe"]
            if su < 0 or su > 32767:
                raise ValueError(
                    "Start Universe must be between 0 and 32,767 (got %d)" % su
                )
            stride = chips * m["channels_per_chip"]
            spans = build_spans(
                m["start_universe"],
                m["start_channel"],
                m["channels_per_chip"],
                chips,
                m["chips_per_universe"],
            )
            if su + len(spans) - 1 > 32767:
                raise ValueError(
                    "Start Universe %d + %d universes reaches universe %d, "
                    "which exceeds the maximum of 32,767"
                    % (su, len(spans), su + len(spans) - 1)
                )
            final = int(round(dur * fps)) if dur > 0 else 0
            if final and blend > final:
                raise ValueError(
                    "Loop blend must not exceed the number of exported frames (%d)"
                    % final
                )
        except ValueError as e:
            self._err(str(e))
            return
        if not self._confirm_can_record():
            return
        # Preflight: if packets are already arriving, check for missing/unexpected universes
        ok, pf_msgs = self._preflight(spans)
        for msg in pf_msgs:
            self._err(msg)
        if not ok:
            return
        # Determine output format from path extension
        path = self.path_var.get().strip()
        is_plsq = path.lower().endswith(".plsq")
        # D3: JSON is not available for 3+ universes
        if not is_plsq and len(spans) >= 3:
            self._err(
                "JSON format is not available for %d universes (limit: 2). "
                "Use PLSQ format." % len(spans)
            )
            return
        # For PLSQ: check for leftover .part file
        if is_plsq:
            part = path + ".part"
            if os.path.exists(part) and self._approved != path_key(part):
                choice = ask_choice(
                    self,
                    "Leftover recording found",
                    "A partial recording exists:\n%s\n\nOverwrite it or cancel?" % part,
                    [("Overwrite leftover", "overwrite"), ("Cancel", None)],
                )
                if choice != "overwrite":
                    return
        # For PLSQ: refuse if blend delay-line exceeds 1 GiB
        if is_plsq and blend > 0:
            delay_bytes = blend * stride
            if delay_bytes > 1 << 30:
                self._err(
                    "Loop blend delay line would use %.1f GiB (blend=%d × stride=%d). "
                    "Reduce blend frames or chip count." % (delay_bytes / (1 << 30), blend, stride)
                )
                return
        # Build frame assembler (when "complete source frames" is on)
        assembler = None
        if self.complete_frames_var.get():
            assembler = FrameAssembler()
            assembler.start(spans, stride)
        self.server.set_assembler(assembler)
        self._clear_errors()
        self.vars["sequence_frame_stride"].set(str(stride))
        eff_cpu = effective_chips_per_universe(m["chips_per_universe"])
        self._job = {
            "final": final, "skip": skip, "blend": blend,
            "is_plsq": is_plsq,
            "start_universe": m["start_universe"],
            "start_channel": m["start_channel"],
            "channels_per_chip": m["channels_per_chip"],
            "chips_per_universe": eff_cpu,
            "universes_count": len(spans),
            "model_chips_count": chips,
        }
        # Start the pipeline
        out_queue = None
        if is_plsq:
            part = path + ".part"
            self._plsq_target = path
            self._plsq_part = part
            pw = PlsqWriter()
            pw.open(part, chips, int(fps), m["start_universe"], m["start_channel"],
                    eff_cpu)
            self._plsq_writer = pw
            q_size = max(300, int(2 * fps) + 20)
            out_queue = queue.Queue(maxsize=q_size)
            self._plsq_queue = out_queue
            sw = PlsqStreamWriter(pw, skip, blend, stride)
            sw.start(out_queue)
            self._plsq_sw = sw
        self.rec.start(
            spans,
            stride,
            fps,
            max_frames=(skip + final + blend) if final else 0,
            wait_first_packet=self.wait_var.get(),
            assembler=assembler,
            out_queue=out_queue,
        )
        self._was_recording = True
        self._update_buttons()

    def _preflight(self, spans):
        """Check expected vs received universes before recording starts.
        Returns (ok, [error/warning strings]). ok=False blocks recording."""
        expected = set(s[0] for s in spans)
        _, ustats, _, _, _, _, _, _ = self.server.snapshot()
        received = set(ustats.keys())
        if not received:
            return True, []   # nothing yet → wait mode, fine
        missing = expected - received
        if missing:
            return False, [
                "Preflight failed: expected universes not receiving data: %s"
                % ", ".join("U%d" % u for u in sorted(missing))
            ]
        u_min, u_max = min(expected), max(expected)
        unexpected = {u for u in received if u_min <= u <= u_max and u not in expected}
        warnings = []
        if unexpected:
            sample = sorted(unexpected)[:5]
            warnings.append(
                "Preflight warning: unexpected universes in range U%d-U%d: %s%s"
                % (u_min, u_max,
                   ", ".join("U%d" % u for u in sample),
                   " ..." if len(unexpected) > 5 else "")
            )
        return True, warnings

    # ---- protecting recorded data and existing files ---------------------------------
    def _ask_file_conflict(self, path, cancel_label):
        if self.saved_path and path_key(self.saved_path) == path_key(path):
            head = "The previous recording was saved to this file:"
        else:
            head = "This file already exists:"
        msg = "%s\n\n%s\n\nOverwrite it, or save the new recording as a new file?" % (
            head,
            path,
        )
        return ask_choice(
            self,
            "File already exists",
            msg,
            [
                ("Overwrite existing file", "overwrite"),
                ("Save as new file...", "new"),
                (cancel_label, None),
            ],
        )

    def _choose_new_path(self, path):
        suggestion = next_free_path(path)
        if os.path.splitext(path)[1].lower() == ".plsq":
            default_ext = ".plsq"
            ftypes = [("PLSQ Sequence", "*.plsq"), ("All files", "*.*")]
        else:
            default_ext = ".json"
            ftypes = [("JSON Sequence", "*.json"), ("All files", "*.*")]
        return filedialog.asksaveasfilename(
            title="Save as new file",
            defaultextension=default_ext,
            filetypes=ftypes,
            initialdir=os.path.dirname(suggestion) or None,
            initialfile=os.path.basename(suggestion),
        )

    def _resolve_existing(self, path, cancel_label):
        """The target file exists: let the user overwrite it or pick a new name.
        Returns the path to use, or None if the user backed out."""
        while True:
            choice = self._ask_file_conflict(path, cancel_label)
            if choice == "overwrite":
                return path
            if choice == "new":
                new = self._choose_new_path(path)
                if new:
                    return new
                continue  # the save dialog was cancelled: ask again
            return None

    def _confirm_can_record(self):
        """Before a new recording: do not lose an unsaved recording or a saved file."""
        if self.pending is not None:
            choice = ask_choice(
                self,
                "Unsaved recording",
                "The previous recording (%d frames) has not been saved.\n\n"
                "Starting a new recording will discard it."
                % len(self.pending["frames"]),
                [
                    ("Save it now", "save"),
                    ("Discard it", "discard"),
                    ("Cancel", None),
                ],
            )
            if choice == "save":
                self._save_pending()
                if self.pending is not None:
                    return False
            elif choice == "discard":
                self.pending = None
            else:
                return False
        path = self.path_var.get().strip()
        if path and os.path.exists(path) and self._approved != path_key(path):
            chosen = self._resolve_existing(path, "Cancel")
            if chosen is None:
                return False
            self.path_var.set(chosen)
            self._approved = path_key(chosen)
        return True

    def _finish_recording(self):
        self.server.set_assembler(None)   # detach assembler now that recording ended
        self._was_recording = False
        self._update_buttons()
        job = self._job
        fps = self.rec.fps
        # Capture stats from the recorder/server before anything can reset them
        rec_missing = self.rec.missing
        rec_late = self.rec.late
        rec_partial = self.rec.partial_source_frames
        _, _, _, _, _, _, lost_pkts, _ = self.server.snapshot()
        mapping_fields = {
            "start_universe": job["start_universe"],
            "start_channel": job["start_channel"],
            "channels_per_chip": job["channels_per_chip"],
            "chips_per_universe": job["chips_per_universe"],
            "universes_count": job["universes_count"],
            "model_chips_count": job["model_chips_count"],
        }
        stats_fields = {
            "missing": rec_missing,
            "late": rec_late,
            "partial_source_frames": rec_partial,
            "lost_packets": lost_pkts,
        }
        if job.get("is_plsq"):
            self._finish_plsq(job, fps, mapping_fields, stats_fields)
        else:
            self._finish_json(job, fps, mapping_fields, stats_fields)

    def _finish_plsq(self, job, fps, mapping_fields, stats_fields):
        """Finalize a PLSQ streaming recording."""
        sw = self._plsq_sw
        pw = self._plsq_writer
        part = self._plsq_part
        target = self._plsq_target

        # Send sentinel so the writer thread knows the sampler is done
        q = self._plsq_queue
        if q is not None:
            try:
                q.put(None, timeout=2.0)
            except Exception:
                pass
        self._plsq_queue = None
        if sw is not None:
            sw.join(timeout=30.0)

        # Clean up state references
        self._plsq_sw = None
        self._plsq_writer = None
        self._plsq_part = None
        self._plsq_target = None

        if sw is not None and sw.error:
            self._err(
                "PLSQ writer error: %s — partial file kept at %s" % (sw.error, part)
            )
            try:
                pw.abort()
            except Exception:
                pass
            return

        final = sw.frames_written if sw is not None else 0
        if final == 0:
            self._err(
                "PLSQ recording produced 0 exported frames "
                "(check skip/blend settings). Partial file kept at %s" % part
            )
            try:
                pw.abort()
            except Exception:
                pass
            return

        # Finalize the writer: write meta, set FINALIZED flag
        blend_applied = min(job["blend"], final) if job["blend"] else 0
        meta = build_meta(
            file_format="plsq",
            recorder_version=APPV,
            model_name=self.vars["model_name"].get(),
            model_chips_tag=self.vars["model_chips_tag"].get(),
            sequence_name=self.vars["sequence_name"].get(),
            sequence_version=self.vars["sequence_version"].get(),
            developer_notes=self.vars["developer_notes"].get(),
            fps=int(fps),
            frame_count=final,
            frame_stride=self.rec.stride,
            post_skip_frames=job["skip"],
            post_loop_blend_frames=blend_applied,
            recorded_frames_raw=self.rec.frames_captured,
            start_universe=mapping_fields["start_universe"],
            start_channel=mapping_fields["start_channel"],
            channels_per_chip=mapping_fields["channels_per_chip"],
            chips_per_universe=mapping_fields["chips_per_universe"],
            universes_count=mapping_fields["universes_count"],
            model_chips_count=mapping_fields["model_chips_count"],
            missing_frames=stats_fields["missing"],
            late_frames=stats_fields["late"],
            partial_source_frames=stats_fields["partial_source_frames"],
            lost_packets=stats_fields["lost_packets"],
        )
        try:
            pw.finalize(meta)
        except Exception as e:
            self._err(
                "PLSQ finalize error: %s — partial file kept at %s" % (e, part)
            )
            return
        try:
            os.replace(part, target)
        except OSError as e:
            self._err(
                "Could not rename %s → %s: %s" % (part, target, e)
            )
            return
        size = os.path.getsize(target)
        self.last_saved = "Saved: %s\n%d frames, %.1f MB" % (
            target, final, size / (1024 * 1024)
        )
        self.saved_path = target
        self._approved = None

    def _finish_json(self, job, fps, mapping_fields, stats_fields):
        """Finalize a JSON (in-memory) recording."""
        raw = self.rec.frames
        if not raw:
            self.last_saved = "Nothing to save: 0 frames recorded."
            return
        avail = len(raw) - job["skip"]
        final = (
            min(job["final"], avail - job["blend"])
            if job["final"]
            else avail - job["blend"]
        )
        base = {"fps": fps, "stride": self.rec.stride, "recorded": len(raw)}
        try:
            frames = postprocess(raw, job["skip"], job["blend"], final)
        except ValueError as e:
            # keep the raw recording so that nothing is lost
            self.pending = dict(base, frames=list(raw), skip=0, blend=0,
                                **mapping_fields, **stats_fields)
            self._err("Post-processing failed: %s" % e)
            self.last_saved = (
                "Not saved: the raw recording (%d frames) is kept in memory.\n"
                "Start Record will offer to save it." % len(raw)
            )
            return
        self.vars["sequence_frame_count"].set(str(len(frames)))
        self.vars["sequence_duration_sec"].set(str(round(len(frames) / fps, 3)))
        self.pending = dict(base, frames=frames, skip=job["skip"], blend=job["blend"],
                            **mapping_fields, **stats_fields)
        self._save_pending()

    def _save_pending(self):
        p = self.pending
        if p is None:
            return
        path = self.path_var.get().strip()
        if not path:
            self._err("Not saved: the file path is empty (recording kept in memory).")
            return
        if os.path.exists(path) and self._approved != path_key(path):
            chosen = self._resolve_existing(path, "Don't save yet")
            if chosen is None:
                self.last_saved = (
                    "Not saved: the recording (%d frames) is kept in memory.\n"
                    "Start Record will offer to save it." % len(p["frames"])
                )
                return
            path = chosen
            self.path_var.set(path)
        meta = build_meta(
            file_format="json",
            recorder_version=APPV,
            model_name=self.vars["model_name"].get(),
            model_chips_tag=self.vars["model_chips_tag"].get(),
            model_chips_count=p["model_chips_count"],
            sequence_name=self.vars["sequence_name"].get(),
            sequence_version=self.vars["sequence_version"].get(),
            fps=int(p["fps"]),
            frame_count=len(p["frames"]),
            frame_stride=p["stride"],
            start_universe=p["start_universe"],
            start_channel=p["start_channel"],
            channels_per_chip=p["channels_per_chip"],
            chips_per_universe=p["chips_per_universe"],
            universes_count=p["universes_count"],
            post_skip_frames=p["skip"],
            post_loop_blend_frames=p["blend"],
            recorded_frames_raw=p["recorded"],
            missing_frames=p["missing"],
            late_frames=p["late"],
            partial_source_frames=p["partial_source_frames"],
            lost_packets=p["lost_packets"],
            developer_notes=self.vars["developer_notes"].get(),
        )
        try:
            size = write_json(path, meta, p["frames"])
        except ValueError as e:
            self._err(str(e))
            self.last_saved = "Not saved: the recording is kept in memory."
            return
        except OSError as e:
            self._err("Could not write the file: %s (recording kept in memory)" % e)
            self.last_saved = "Not saved: the recording is kept in memory."
            return
        extra = ""
        if p["skip"] or p["blend"]:
            extra = "\n(recorded %d: skipped %d, blended %d)" % (
                p["recorded"],
                p["skip"],
                p["blend"],
            )
        self.last_saved = "Saved: %s\n%d frames, %.0f KB%s" % (
            path,
            len(p["frames"]),
            size / 1024.0,
            extra,
        )
        self.saved_path = path
        self.pending = None
        self._approved = None

    def _update_buttons(self):
        running = self.server.running
        addr = "(%s) UDP: %s" % (self.host_var.get(), self.port_var.get())
        self.btn_server.config(
            text=("Stop Server\n" if running else "Start Server\n") + addr,
            bg=RED if running else BTN,
            activebackground=RED if running else BTN_HOVER,
            highlightbackground=RED_BORDER if running else BORDER,
        )
        rec = self.rec.state in ("waiting", "recording")
        self.btn_rec.config(
            text="Stop Record\nInput Data" if rec else "Start Record\nInput Data",
            bg=RED if rec else BTN,
            activebackground=RED if rec else BTN_HOVER,
            highlightbackground=RED_BORDER if rec else BORDER,
            state="normal" if (running or rec) else "disabled",
        )

    def _close(self):
        if self.rec.state in ("waiting", "recording"):
            self.rec.stop()
        if self._was_recording and self.rec.state == "done":
            self._finish_recording()
        if self.pending is not None:
            choice = ask_choice(
                self,
                "Unsaved recording",
                "The last recording (%d frames) has not been saved."
                % len(self.pending["frames"]),
                [
                    ("Save it now", "save"),
                    ("Close without saving", "close"),
                    ("Cancel", None),
                ],
            )
            if choice == "save":
                self._save_pending()
                if self.pending is not None:
                    return
            elif choice != "close":
                return
        try:
            self.server.stop()
        finally:
            self.destroy()

    # ---- statistics ----------------------------------------------------------
    def _refresh(self):
        if self.server.error:
            self._err(self.server.error)
            self.server.error = None
        if self.rec.error:
            self._err(self.rec.error)
            self.rec.error = None
        # If the PLSQ writer failed (e.g. disk full), stop the recording
        if self._plsq_sw is not None and self._plsq_sw.error:
            self._err("PLSQ writer: %s" % self._plsq_sw.error)
            if self.rec.state in ("waiting", "recording"):
                self.rec.stop()
        if not self.server.running and self.rec.state in ("waiting", "recording"):
            self.rec.stop()  # the source is gone: finish what has been recorded
        if self._was_recording and self.rec.state == "done":
            self._finish_recording()
        # Live derived fields — always update (also during recording)
        try:
            chips = int(self.vars["model_chips_count"].get())
            cpc = int(self.map_vars["channels_per_chip"].get())
            cpu_raw = int(self.map_vars["chips_per_universe"].get())
            eff_cpu = effective_chips_per_universe(cpu_raw)
            n_uni = (chips + eff_cpu - 1) // eff_cpu
            self.universes_var.set(str(n_uni))
            self.eff_cpu_var.set(
                "auto → %d" % eff_cpu if cpu_raw == 0 else str(eff_cpu)
            )
            if self.rec.state not in ("waiting", "recording"):
                self._set_ro("sequence_frame_stride", str(chips * cpc))
                dur = float(
                    self.vars["sequence_duration_sec"].get().replace(",", ".") or 0
                )
                fps = self.fps_var.get()
                if self.rec.state == "idle":
                    self._set_ro(
                        "sequence_frame_count",
                        str(int(round(dur * fps))) if dur > 0 else "",
                    )
        except (ValueError, ZeroDivisionError):
            self.universes_var.set("?")
            self.eff_cpu_var.set("")
        # JSON format: disable radio when 3+ universes, force back to plsq if needed
        try:
            n_uni_str = self.universes_var.get()
            if n_uni_str and n_uni_str != "?":
                if int(n_uni_str) >= 3:
                    if self.fmt_var.get() == "json":
                        self.fmt_var.set("plsq")
                        self._on_fmt_change()
                    self._json_radio.config(state="disabled")
                else:
                    self._json_radio.config(state="normal")
        except (ValueError, AttributeError):
            pass
        latest, ustats, total, ignored, nbytes, last, lost_pkts, rcvbuf = self.server.snapshot()
        now = time.perf_counter()
        pt, pn, pu = self._prev
        if now - pt >= 0.9:
            dt = now - pt
            self._rates = (
                (total - pn) / dt,
                {u: (s["count"] - pu.get(u, 0)) / dt for u, s in ustats.items()},
            )
            self._prev = (now, total, {u: s["count"] for u, s in ustats.items()})
        rate, urates = self._rates
        L = []
        if self.server.running:
            L.append(
                ("Server: RUNNING  %s:%d" % (self.server.host, self.server.port), None)
            )
            if rcvbuf:
                L.append(("SO_RCVBUF: %d KiB" % (rcvbuf >> 10), None))
            ago = "-" if not last else "%.2f s ago" % (now - last)
            L.append(("Packets: %d  (%.1f pkt/s)  last: %s" % (total, rate, ago), None))
            if ignored:
                L.append(("Ignored (not ArtDMX): %d" % ignored, None))
            if lost_pkts:
                L.append(("Lost (seq gaps, approx): %d" % lost_pkts, "warn"))
            L.append(("Universes received:", None))
            if ustats:
                unis = sorted(ustats)
                if len(unis) <= 16:
                    for u in unis:
                        L.append((
                            "  U%-4d %6.1f pkt/s   %3d ch"
                            % (u, urates.get(u, 0.0), ustats[u]["length"]),
                            None,
                        ))
                else:
                    # Compact summary for large installations
                    u_min, u_max = unis[0], unis[-1]
                    rates_list = [urates.get(u, 0.0) for u in unis]
                    lengths = [ustats[u]["length"] for u in unis]
                    avg_r = sum(rates_list) / len(rates_list)
                    min_r = min(rates_list)
                    max_r = max(rates_list)
                    median_r = sorted(rates_list)[len(rates_list) // 2]
                    median_l = sorted(lengths)[len(lengths) // 2]
                    L.append((
                        "  U%d-U%d  count=%d  avg=%.0f  min=%.0f  max=%.0f pkt/s"
                        % (u_min, u_max, len(unis), avg_r, min_r, max_r),
                        None,
                    ))
                    if len(set(lengths)) > 1:
                        L.append(("  WARNING: inconsistent channel lengths", "warn"))
                    irregular = [
                        (u, urates.get(u, 0.0), ustats[u]["length"])
                        for u in unis
                        if urates.get(u, 0.0) < 0.9 * median_r
                        or ustats[u]["length"] != median_l
                    ]
                    if irregular:
                        L.append(("  Irregular (%d):" % len(irregular), None))
                        for u, r_, l_ in irregular[:5]:
                            L.append(("    U%d  %.1f pkt/s  %d ch" % (u, r_, l_), None))
            else:
                L.append(("  (nothing received yet)", None))
        else:
            L.append(("Server: STOPPED", None))
        L.append(("", None))
        st = self.rec.state
        is_plsq = self._job is not None and self._job.get("is_plsq")
        if st == "waiting":
            L.append(("Recording: waiting for the first packet...", None))
        elif st == "recording":
            tgt = ("/%d" % self.rec.max_frames) if self.rec.max_frames else ""
            fc = self.rec.frames_captured
            if is_plsq:
                sw = self._plsq_sw
                q = self._plsq_queue
                written = sw.bytes_written if sw else 0
                q_depth = q.qsize() if q else 0
                L.append((
                    "REC PLSQ  %d%s frames  (%.2f s)  queue: %d  written: %.1f MB"
                    % (fc, tgt, self.rec.elapsed, q_depth, written / (1024 * 1024)),
                    None,
                ))
            else:
                L.append((
                    "REC  %d%s frames  (%.2f s)"
                    % (len(self.rec.frames), tgt, self.rec.elapsed),
                    None,
                ))
            if self._job and (self._job["skip"] or self._job["blend"]):
                L.append((
                    "plan: skip %d + export %s + blend %d"
                    % (self._job["skip"], self._job["final"] or "?", self._job["blend"]),
                    None,
                ))
            partial = self.rec.partial_source_frames
            L.append((
                "missing: %d   late: %d   partial_src: %d"
                % (self.rec.missing, self.rec.late, partial),
                None,
            ))
        elif st == "done":
            partial = self.rec.partial_source_frames
            fc = self.rec.frames_captured
            L.append((
                "Recorded: %d frames  missing: %d  late: %d  partial_src: %d"
                % (fc if is_plsq else len(self.rec.frames), self.rec.missing, self.rec.late, partial),
                None,
            ))
            if self.rec.missing:
                L.append((
                    "Warning: some frames had no data for a universe from Input mapping.",
                    "warn",
                ))
        if self.last_saved:
            L.append(("", None))
            L.extend((line, None) for line in self.last_saved.split("\n"))
        if self.errors:
            L.append(("", None))
            L.append(("Errors:", "err"))
            L.extend(("[%s] %s" % (t, m), "err") for t, m in self.errors)
        self.stats.config(state="normal")
        self.stats.delete("1.0", "end")
        for text, tag in L:
            self.stats.insert("end", text + "\n", tag or ())
        self.stats.config(state="disabled")
        self._update_buttons()
        self.after(250, self._refresh)

    def _set_ro(self, key, value):
        if self.vars[key].get() != value:
            self.vars[key].set(value)


def main():
    if tk is None:
        raise SystemExit(
            "tkinter is not installed (on Linux: sudo apt install python3-tk)"
        )
    App().mainloop()


if __name__ == "__main__":
    main()
