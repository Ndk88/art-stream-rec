#!/usr/bin/env python3
"""
Art Stream Sequence Recorder (local UDP server with a small GUI).

Listens for Art-Net (ArtDMX) packets on a given IP/port (default 127.0.0.1:6454),
records the stream from TouchDesigner / Madrix 5 at a fixed frame rate and saves it
as a binary .plsq file (any size) or JSON (1-2 universes only).

Run:      python main.py        (Python 3.9+, no extra packages)
Build:    pyinstaller --onefile --noconsole main.py
"""

import os
import queue
import threading
import time

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # the core can still be imported without tkinter (for tests)
    tk = None

from mapping import build_spans, count_universes, effective_chips_per_universe
from plsq import PlsqWriter, build_meta, encoded_meta_size
from session import (
    ARTNET_ID, OP_DMX, OP_SYNC, RCVBUF_SIZE,
    parse_artdmx, postprocess, _blend_frames, write_json, _LUT,
    path_key, next_free_path,
    ArtNetServer, FrameAssembler, Recorder, PlsqStreamWriter,
    PlsqRecordingSession,
    start_loss_tracking, recording_lost_packets, short_packets_warning,
    FIRST_FRAME_PARTIAL_WARNING,
)

APPV = "1.2.1"


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
        self.errors = []  # [(time, message)]
        self.saved_path = None
        self.pending = None
        self._approved = None
        self._was_recording = False
        self._job = None
        self._plsq_session = None   # active PlsqRecordingSession (PLSQ only)
        self._blend_warn = None     # D11 warning, cleared on new recording
        self._short_warn = None     # I-10 warning of the last PLSQ recording
        self._first_partial = False  # last PLSQ recording started on a partial frame
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
        # Stop active PLSQ session
        if self._plsq_session is not None:
            if self._plsq_session.state in ("waiting", "recording"):
                self._plsq_session.request_stop()
            return
        # Stop active JSON recorder
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
        path = self.path_var.get().strip()
        # I-06: fmt_var is authoritative; path extension must match selected format
        fmt = self.fmt_var.get()
        ext = os.path.splitext(path)[1].lower()
        if fmt == "plsq" and ext != ".plsq":
            self._err(
                "Path extension %r does not match the selected format PLSQ. "
                "Change the path or the format." % (ext or "(none)",)
            )
            return
        if fmt == "json" and ext != ".json":
            self._err(
                "Path extension %r does not match the selected format JSON. "
                "Change the path or the format." % (ext or "(none)",)
            )
            return
        is_plsq = (fmt == "plsq")
        # I-04: run preflight BEFORE confirm dialog (avoids wasted confirm on bad universe state)
        ok, pf_msgs = self._preflight(spans)
        for msg in pf_msgs:
            self._err(msg)
        if not ok:
            return
        if not self._confirm_can_record():
            return
        # D3: JSON is not available for 3+ universes
        if not is_plsq and len(spans) >= 3:
            self._err(
                "JSON format is not available for %d universes (limit: 2). "
                "Use PLSQ format." % len(spans)
            )
            return
        eff_cpu = effective_chips_per_universe(m["chips_per_universe"])
        if is_plsq:
            # For PLSQ: check for leftover .part file
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
            if blend > 0:
                delay_bytes = blend * stride
                if delay_bytes > 1 << 30:
                    self._err(
                        "Loop blend delay line would use %.1f GiB (blend=%d x stride=%d). "
                        "Reduce blend frames or chip count."
                        % (delay_bytes / (1 << 30), blend, stride)
                    )
                    return
            # I-05: pre-check meta size (PLSQ only)
            meta_check = build_meta(
                file_format="plsq",
                recorder_version=APPV,
                model_name=self.vars["model_name"].get().strip(),
                model_chips_tag=self.vars["model_chips_tag"].get().strip(),
                sequence_name=self.vars["sequence_name"].get().strip(),
                sequence_version=self.vars["sequence_version"].get().strip(),
                developer_notes=self.vars["developer_notes"].get().strip(),
                fps=int(fps),
                frame_count=999999999999,
                frame_stride=stride,
                start_universe=m["start_universe"],
                start_channel=m["start_channel"],
                chips_per_universe=eff_cpu,
                universes_count=len(spans),
                model_chips_count=chips,
                post_skip_frames=999999999999,
                post_loop_blend_frames=999999999999,
                recorded_frames_raw=999999999999,
                missing_frames=999999999999,
                late_frames=999999999999,
                partial_source_frames=999999999999,
                lost_packets=999999999999,
            )
            meta_sz = encoded_meta_size(meta_check)
            if meta_sz > 65536:
                self._err(
                    "Meta block too large: %d bytes (limit 65,536). "
                    "Shorten the text fields (model name, sequence name, developer notes)."
                    % meta_sz
                )
                return
        self._clear_errors()
        self._blend_warn = None
        self._short_warn = None
        self._first_partial = False
        self.vars["sequence_frame_stride"].set(str(stride))
        if is_plsq:
            # I-08: delegate PLSQ recording lifecycle to PlsqRecordingSession
            meta_extra = {
                "recorder_version": APPV,
                "model_name": self.vars["model_name"].get().strip(),
                "model_chips_tag": self.vars["model_chips_tag"].get().strip(),
                "sequence_name": self.vars["sequence_name"].get().strip(),
                "sequence_version": self.vars["sequence_version"].get().strip(),
                "developer_notes": self.vars["developer_notes"].get().strip(),
                "channels_per_chip": m["channels_per_chip"],
            }
            self._plsq_session = PlsqRecordingSession(
                self.server,
                chips=chips, fps=fps, out_path=path,
                spans=spans, stride=stride,
                start_universe=m["start_universe"],
                start_channel=m["start_channel"],
                eff_cpu=eff_cpu,
                skip=skip, blend=blend, final_frames=final,
                wait_first_packet=self.wait_var.get(),
                complete_frames=self.complete_frames_var.get(),
                meta_extra=meta_extra,
            )
            self._plsq_session.start()
            if self._plsq_session.state == "error":
                self._err(self._plsq_session.error)
                self._plsq_session = None
                return
        else:
            # JSON: inline Recorder (in-memory frames)
            assembler = None
            if self.complete_frames_var.get():
                assembler = FrameAssembler()
                assembler.start(spans, stride)
            self.server.set_assembler(assembler)
            # I-03: reset sequence tracking and snapshot baseline at recording start
            self._lost_packets_start = start_loss_tracking(self.server)
            self.rec.start(
                spans, stride, fps,
                max_frames=(skip + final + blend) if final else 0,
                wait_first_packet=self.wait_var.get(),
                assembler=assembler,
                out_queue=None,
            )
            self._was_recording = True
            self._job = {
                "final": final, "skip": skip, "blend": blend,
                "start_universe": m["start_universe"],
                "start_channel": m["start_channel"],
                "channels_per_chip": m["channels_per_chip"],
                "chips_per_universe": eff_cpu,
                "universes_count": len(spans),
                "model_chips_count": chips,
            }
        self._update_buttons()

    def _preflight(self, spans):
        """Check expected vs received universes before recording starts.
        Returns (ok, [error/warning strings]). ok=False blocks recording."""
        expected = set(s[0] for s in spans)
        _, ustats, _, _, _, _, _, _ = self.server.snapshot()
        received = set(ustats.keys())
        if not received:
            return True, []
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
                continue
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
        """Called when the JSON recorder (self.rec) completes. PLSQ is handled by _plsq_session."""
        self.server.set_assembler(None)
        self._was_recording = False
        self._update_buttons()
        job = self._job
        fps = self.rec.fps
        rec_missing = self.rec.missing
        rec_late = self.rec.late
        rec_partial = self.rec.partial_source_frames
        lost_pkts = recording_lost_packets(
            self.server, getattr(self, "_lost_packets_start", 0)
        )
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
        self._finish_json(job, fps, mapping_fields, stats_fields)

    def _on_plsq_session_complete(self):
        """Called from _refresh when _plsq_session reaches done or error state."""
        sess = self._plsq_session
        self._plsq_session = None
        if sess.state == "error":
            self._err(sess.error)
            self._update_buttons()
            return
        res = sess.result()
        if res.get("blend_warning"):
            self._blend_warn = res["blend_warning"]
        self._short_warn = short_packets_warning(
            res.get("short_packets", 0), res.get("first_short_uni")
        )
        self._first_partial = bool(res.get("first_frame_partial"))
        try:
            size = os.path.getsize(res["path"])
        except OSError:
            size = 0
        self.last_saved = "Saved: %s\n%d frames, %.1f MB" % (
            res["path"], res["frames_written"], size / (1024 * 1024)
        )
        self.saved_path = res["path"]
        self._approved = None
        self._update_buttons()

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
        if final <= 0:
            self.pending = dict(base, frames=list(raw), skip=0, blend=0,
                                **mapping_fields, **stats_fields)
            self._err(
                "Not enough recorded frames (have %d after skip, need at least %d). "
                "Raw recording kept in memory." % (avail, job["blend"] + 1)
            )
            self.last_saved = (
                "Not saved: the raw recording (%d frames) is kept in memory.\n"
                "Start Record will offer to save it." % len(raw)
            )
            return
        # D11: clamp blend if fewer exported frames than requested
        blend_applied = min(job["blend"], final)
        if blend_applied < job["blend"]:
            self._blend_warn = (
                "Loop blend reduced from %d to %d frames: the recording is too short"
                % (job["blend"], blend_applied)
            )
        try:
            frames = postprocess(raw, job["skip"], blend_applied, final)
        except ValueError as e:
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
        # Store blend_applied so post_loop_blend_frames in meta reflects actual blend
        self.pending = dict(base, frames=frames, skip=job["skip"], blend=blend_applied,
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
        sess = self._plsq_session
        is_finalizing = sess is not None and sess.state == "finalizing"
        active = (
            self.rec.state in ("waiting", "recording")
            or (sess is not None and sess.state in ("waiting", "recording"))
        )
        if is_finalizing:
            self.btn_server.config(
                text=("Stop Server\n" if running else "Start Server\n") + addr,
                bg=RED if running else BTN,
                activebackground=RED if running else BTN_HOVER,
                highlightbackground=RED_BORDER if running else BORDER,
                state="disabled",
            )
            self.btn_rec.config(
                text="Finalizing...",
                bg=BTN,
                activebackground=BTN_HOVER,
                highlightbackground=BORDER,
                state="disabled",
            )
        else:
            self.btn_server.config(
                text=("Stop Server\n" if running else "Start Server\n") + addr,
                bg=RED if running else BTN,
                activebackground=RED if running else BTN_HOVER,
                highlightbackground=RED_BORDER if running else BORDER,
                state="normal",
            )
            self.btn_rec.config(
                text="Stop Record\nInput Data" if active else "Start Record\nInput Data",
                bg=RED if active else BTN,
                activebackground=RED if active else BTN_HOVER,
                highlightbackground=RED_BORDER if active else BORDER,
                state="normal" if (running or active) else "disabled",
            )

    def _close(self):
        if self._plsq_session is not None:
            if self._plsq_session.state in ("waiting", "recording", "finalizing"):
                self._plsq_session.abort()
            self._plsq_session = None
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
        # I-08: poll PLSQ session state machine
        if self._plsq_session is not None:
            self._plsq_session.poll()
            if self._plsq_session.state in ("done", "error"):
                self._on_plsq_session_complete()

        if self.server.error:
            self._err(self.server.error)
            self.server.error = None
        if self.rec.error:
            self._err(self.rec.error)
            self.rec.error = None
        if not self.server.running:
            if self.rec.state in ("waiting", "recording"):
                self.rec.stop()
            if self._plsq_session is not None and self._plsq_session.state in ("waiting", "recording"):
                self._plsq_session.request_stop()
        if self._was_recording and self.rec.state == "done":
            self._finish_recording()

        # Live derived fields
        try:
            chips = int(self.vars["model_chips_count"].get())
            cpc = int(self.map_vars["channels_per_chip"].get())
            cpu_raw = int(self.map_vars["chips_per_universe"].get())
            eff_cpu = effective_chips_per_universe(cpu_raw)
            # I-07: use count_universes (accounts for start_channel) instead of ceil formula
            su = int(self.map_vars["start_universe"].get())
            sc = int(self.map_vars["start_channel"].get())
            n_uni = count_universes(su, sc, cpu_raw, chips)
            self.universes_var.set(str(n_uni))
            self.eff_cpu_var.set(
                "auto → %d" % eff_cpu if cpu_raw == 0 else str(eff_cpu)
            )
            rec_idle = self.rec.state not in ("waiting", "recording") and self._plsq_session is None
            if rec_idle:
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

        # JSON format: disable radio when 3+ universes (using corrected count)
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

        # Recording status display
        sess = self._plsq_session
        if sess is not None:
            # PLSQ session display
            sess_state = sess.state
            if sess_state == "waiting":
                L.append(("Recording: waiting for the first packet...", None))
            elif sess_state == "recording":
                rec = sess.recorder
                if rec is not None:
                    fc = rec.frames_captured
                    tgt = ("/%d" % rec.max_frames) if rec.max_frames else ""
                    L.append((
                        "REC PLSQ  %d%s frames  (%.2f s)  queue: %d  written: %.1f MB"
                        % (fc, tgt, rec.elapsed, sess.queue_size,
                           sess.bytes_written / (1024 * 1024)),
                        None,
                    ))
                    if sess.skip or sess.blend:
                        L.append((
                            "plan: skip %d + export %s + blend %d"
                            % (sess.skip, sess.final_frames or "?", sess.blend),
                            None,
                        ))
                    partial = rec.partial_source_frames
                    L.append((
                        "missing: %d   late: %d   partial_src: %d"
                        % (rec.missing, rec.late, partial),
                        None,
                    ))
            elif sess_state == "finalizing":
                L.append((
                    "Finalizing PLSQ... (written %.1f MB)"
                    % (sess.bytes_written / (1024 * 1024),),
                    None,
                ))
        else:
            # JSON recorder display
            st = self.rec.state
            if st == "waiting":
                L.append(("Recording: waiting for the first packet...", None))
            elif st == "recording":
                tgt = ("/%d" % self.rec.max_frames) if self.rec.max_frames else ""
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
                fc = len(self.rec.frames)
                L.append((
                    "Recorded: %d frames  missing: %d  late: %d  partial_src: %d"
                    % (fc, self.rec.missing, self.rec.late, partial),
                    None,
                ))
                if self.rec.missing:
                    L.append((
                        "Warning: some frames had no data for a universe from Input mapping.",
                        "warn",
                    ))

        # I-10: short packets (live from the assembler, else the last PLSQ result)
        asm = sess.assembler if sess is not None else self.rec._assembler
        if asm is not None:
            short_line = short_packets_warning(asm.short_packets, asm.first_short_uni)
        else:
            short_line = self._short_warn
        if short_line:
            L.append((short_line, "warn"))
        # First frame started on a partial commit (no complete frame within 2 s)
        rec_now = sess.recorder if sess is not None else self.rec
        if (rec_now is not None and rec_now.first_frame_partial) or (
            sess is None and self._first_partial
        ):
            L.append((FIRST_FRAME_PARTIAL_WARNING, "warn"))

        if self.last_saved:
            L.append(("", None))
            L.extend((line, None) for line in self.last_saved.split("\n"))
        if self._blend_warn:
            L.append(("", None))
            L.append((self._blend_warn, "warn"))
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
