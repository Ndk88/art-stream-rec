# Issues and solutions: recorder session 15.11.2026 #1

This is a follow-up to the work in `docs/TASKS_RECORDER_reports_session_15_11_2026_#1/` (tasks R1..R9 of `docs/TASKS_RECORDER.md`, branch `dev/v1.2.0`). The result was reviewed against the code, the tests (181 pass) and a few measurements. The format implementation (`plsq.py`) matches `docs/PLSQ_SPEC.md`; the golden file matches spec section 14. The items below are the defects and gaps that remain. Fix them on the same branch, then write new reports into a **new** folder (section 5).

Read first: `CLAUDE.md`, `docs/PLSQ_SPEC.md`. This file does not change the spec. It adds one recorder decision (D11, section 1).

## 0. Ground rules

- Do not edit the reports in `docs/TASKS_RECORDER_reports_session_15_11_2026_#1/`. They are history. Corrections go into an errata file in the new folder (section 5).
- Python stdlib only at runtime. Tools under `tools/` may use optional helpers (for example `ctypes` for Windows memory counters) but must still run without third-party packages.
- Windows first. Keep `run.bat`, `packets_tester.bat` and `pyinstaller --onefile --noconsole main.py` working.
- Keep the UI conventions: dark theme, errors and warnings in the statistics block (not popups), popups only for choices about files.
- Every number in a report must come from a command you ran (give the command and the result). If something was not measured, write "not measured". Do not estimate performance from reading code. Several statements in the session #1 reports were wrong for exactly this reason.
- One commit per issue on `dev/v1.2.0`, no history rewriting. Bump `APPV` to `1.2.1` at the end.
- If a fix conflicts with the spec or with a locked decision in `CLAUDE.md`, stop and ask the user.
- Update `README.md` for every user-visible change.

## 1. Decision D11 (issue I-11), add it to `CLAUDE.md`

> **D11.** When a recording is stopped manually (or early) and fewer exported frames remain than the requested loop-blend length (`final < blend`), the recorder applies a shortened blend `blend_applied = min(blend, final)` in **both** formats (PLSQ and JSON), writes `blend_applied` to `post_loop_blend_frames`, and shows a persistent warning line in the statistics block ("Loop blend reduced from X to Y frames: the recording is too short"). It is not a silent change and not an error. If `final <= 0` it remains an error and nothing is lost (PLSQ: `.part` kept; JSON: raw recording kept in memory, as today).

Reasoning: a shorter crossfade still gives a smoother loop than none, and failing a finished recording would throw away data (in the streaming pipeline the tail frames live only in RAM). This overrides the sentence "validate `blend <= final`" in task R4 for the manual-stop case; the pre-start check for a fixed duration (`blend > final` is refused before recording) stays.

## 2. Order of work

1. **I-01** first (extract non-GUI pipeline, end-to-end benchmark). Run the benchmark before the other fixes to get a baseline. **Stop condition:** if the targets in I-01 are not met, stop, put measurements and analysis in the report, and ask the user before changing the architecture.
2. Core logic fixes: **I-02, I-03, I-09, I-10, I-11**.
3. GUI/controller fixes: **I-04, I-05, I-06, I-07, I-08**.
4. Re-run the I-01 benchmark (after), update the benchmark document, write the reports.

---

## 3. Issues

### I-01. No end-to-end test of the real recording pipeline (priority: high)

**Problem.** `tools/bench_receiver.py` exercises only `ArtNetServer` plus a sampler that reads `server.latest`. It does not use `FrameAssembler`, `Recorder`, the output queue, `PlsqStreamWriter` or `PlsqWriter`. The R4 acceptance run (64,000 chips x 60 fps x 30 s, file validates, memory flat) was never executed. The "recv %" column is a rate inside a time window, not a delivery ratio; a lost tail or a lost universe is invisible. The conclusion "R5 reduced CPU by 18%" does not apply to the real recording path. The risky part is the full path: receive, assemble, queue, write 15 MB/s to disk, and the 250 ms statistics refresh at 500 universes.

**Required fix.**
1. **Extract the non-GUI lifecycle of a PLSQ recording out of `App`** into a module (suggested `session.py`) with a class used by both `App` and the benchmark (for example `PlsqRecordingSession`: `start(...)`, `request_stop()`, `poll()` returning state, `result()`). Covers: validation of inputs, assembler setup, writer/queue/stream-writer start, recorder start, finish (sentinel, join, meta, finalize, atomic replace). No copied logic between the GUI and the tool. This extraction is also needed for I-08.
2. **New `tools/bench_e2e.py`** that runs that session against a real folder on a real disk (default: system temp; print the drive and free space), with `packets_tester.py` as a child process (`--pattern ramp`). Options: `--chips --fps --duration` (default 60) `--artsync --stagger-ms --complete-frames on|off --skip --blend --out-dir --matrix --quick` (quick = 10 s).
3. Metrics to print and store:
   - packets sent (parsed from the sender's final line) vs received, exact ratio;
   - `lost_packets` (sequence gaps), `late_frames %`, `missing_frames %`, `partial_source_frames`;
   - output queue maximum depth and capacity; frames written vs expected (`duration x fps`);
   - process CPU time, peak memory, memory growth between the 10th second and the end (on Windows via `ctypes`, otherwise `resource`; if unavailable, say so);
   - `validate()` of the resulting file;
   - **content check:** every recorded frame must equal one of the frames of the tester's ring (rebuild the ring with the tester's own builder); report `torn_frames`;
   - number of leading all-zero frames.
4. **Matrix** (60 s each): 1,700x30, 1,700x60, 10,000x60, 32,000x60, 64,000x30, 64,000x60. Plus two extra runs at 64,000x60: with `--artsync --stagger-ms 0.05`, and with `--complete-frames off` (for comparison).
5. Update `docs/BENCHMARK_RECORDER.md`: add the end-to-end section; label the old tables "receive path only"; fix the wording of "recv %" (rate vs ratio); state the run length of every table.

**Acceptance (64,000 chips, 60 fps, 60 s, complete frames on):** received == sent; `lost_packets == 0`; `late_frames < 0.5%`; `torn_frames == 0`; no leading zero frames; frames written within +-2 of expected; maximum queue depth below 50% of capacity; memory growth below 100 MB; file validates. Run the benchmark once before the other fixes and once after all fixes.

---

### I-02. Recording can start with a black first frame (priority: high)

**Problem.** In waiting mode ("Start recording on first packet") the recorder starts when **any** mapped universe appears in `server.latest` (`have = any(...)` in `Recorder._capture`). With the frame assembler enabled the sampler reads `committed_frame`, which may still be `None`; the code then writes an all-zero frame and counts it as `missing`. Reproduced at 500 universes (one trial in five produced a black frame 0); not reproducible with 2 universes. It is a timing race.

**Required fix.** When an assembler is active and waiting mode is on, wait until `assembler.committed_frame is not None` before starting the clock. (Partial commits eventually happen when a universe repeats, so this cannot wait forever if one universe never arrives, but the wait loop must still react to the stop event.) Without an assembler keep the current behavior. In the non-waiting mode zero frames before the first commit remain acceptable and counted as `missing`.

**Tests.**
- Deterministic unit test with a fake server/assembler whose `committed_frame` appears after a delay: the first recorded frame must not be zeros.
- Stress script/test: 500-universe bursts (as in the reproduction), at least 50 trials, zero black first frames.

---

### I-03. `lost_packets` in the file counts losses from before the recording (priority: high)

**Problem.** `ArtNetServer.lost_packets` is cumulative since the server was started (reset only in `start()`), and `_finish_recording` reads the total at the end. Losses during the waiting period or while the source was being configured end up in the file's `lost_packets`.

**Required fix.** Take a baseline snapshot of the counter when the recording actually starts (the moment the sampling clock starts), and write `lost_end - lost_start`. The statistics block should show the per-recording value while recording and the server total while idle. Also reset the per-universe last-sequence memory at the recording start so a gap spanning the start is not counted.

**Tests.** Feed gaps before and during a recording; the value in the saved meta contains only the in-recording ones. Both formats.

---

### I-04. Writer creation can fail silently, and the folder may not exist (priority: medium)

**Problem.** In `_toggle_record` the call `PlsqWriter.open(...)` is not guarded and nothing creates the destination folder (the JSON path does, `os.makedirs`). A path into a non-existent folder, a read-only folder or a locked file raises inside a Tk callback; with `pythonw` (`run.bat` without console) the user sees nothing. The assembler stays attached to the server after the failure. Also, `_confirm_can_record()` (overwrite dialogs) runs before `_preflight()`, so the user can answer dialogs for a recording that is then refused.

**Required fix.**
- Create the folder (`os.makedirs(..., exist_ok=True)`) and open the writer inside `try/except OSError`; on failure show the error in the statistics block ("Could not create ...: reason") and leave all state unchanged.
- Wrap everything after the assembler attach in `try/finally` so that any failure detaches the assembler and resets the job state.
- Run the preflight check **before** `_confirm_can_record()`.

**Tests.** Non-GUI tests for the session class: non-existent folder is created; unwritable path produces a clean error and no attached assembler; preflight refusal happens before any file dialog is requested.

---

### I-05. The 64 KB meta limit is not checked before recording (priority: medium)

**Problem.** Spec section 5 requires the recorder to validate before the recording starts that the meta fits in 65,536 bytes. Currently a long `developer_notes` is discovered by `finalize()` after the recording, and the result stays as a `.part` file.

**Required fix.** Before starting a PLSQ recording, build the meta with worst-case counter values (for example 12-digit numbers for every counter and frame values), encode it exactly as `finalize()` does (UTF-8, `ensure_ascii=False`, compact separators), and refuse to start if it exceeds 65,536 bytes. The message states the current size and the limit. Cyrillic and other multi-byte text and JSON escapes (quotes, newlines) count at their encoded size. The check applies to PLSQ only.

**Tests.** 70,000 ASCII characters: refused. 40,000 Cyrillic characters (about 80 KB encoded): refused. 30,000 Cyrillic characters: accepted and finalizes successfully. Text with many quotes and newlines near the limit.

---

### I-06. Output format is taken from the path extension, not from the selector (priority: medium)

**Problem.** `is_plsq = path.lower().endswith(".plsq")`. The format radio buttons only rewrite the extension in the path field. If the user types another extension, the radio button and the real format disagree (a JSON written into a file the UI calls `.plsq`, or the reverse).

**Required fix.** The selected format (`fmt_var`) is authoritative. At record start, if the path extension does not match the selected format (`.plsq` or `.json`, case-insensitive), refuse to start with a clear error in the statistics block ("Path extension .xyz does not match the selected format PLSQ: change the path or the format"). Do not silently rename the user's file. The existing JSON-with-3-or-more-universes block stays.

**Tests.** Table-driven test of the decision function (format, extension, universes) as a pure function outside Tk.

---

### I-07. "Universes (calculated)" ignores Start channel (priority: medium)

**Problem.** `_refresh` computes `ceil(chips / effective_chips_per_universe)`. With Start channel above 1 the first universe holds fewer chips, so the real count can be higher: 256 chips with start channel 2 shows 2 universes, but `build_spans` gives 3. Recording is protected (the check uses `len(spans)`), but the displayed number and the JSON radio button availability are wrong.

**Required fix.** Compute the displayed value with the same function that the writer and validator use (`count_universes`, or `len(build_spans(...))`) via one pure helper in `mapping.py`; handle `ValueError` (for example a start channel where not a single chip fits) by showing a clear message instead of a number. Use that helper everywhere (GUI display, JSON gating, validation).

**Tests.** (chips, start channel, chips per universe) to count: (104,1,0)->1, (128,1,0)->1, (129,1,0)->2, (256,2,0)->3, (384,1,0)->3, (64000,1,0)->500, (100,512,0) -> error message, (130,1,64)->3.

---

### I-08. Finalizing a recording freezes the window and can race the writer thread (priority: medium)

**Problem.** `_finish_plsq` runs on the Tk thread: it waits up to 2 s to put the sentinel, then `sw.join(timeout=30)`, then continues. During the join (disk backlog, loop-blend computation) the window does not respond. If the join times out, `pw.finalize()` is called while the writer thread may still be writing.

**Required fix.** Make finalization asynchronous with explicit states: `finalizing` is entered on stop; the sentinel is queued without blocking (retry on the next `_refresh` tick if the queue is full); `_refresh` polls the writer thread and shows "Finalizing... (written N / blend M)" in the statistics block; Start Record and Start/Stop Server are disabled meanwhile. `PlsqWriter.finalize()` is called **only** after the writer thread has ended; if it has not ended after a generous timeout (default 120 s), show an error, keep the `.part` file, do not call `finalize()`. Closing the window during finalization may block while waiting (with the same timeout) and must never call `finalize()` concurrently. This logic belongs in the session class from I-01 so that it is testable without Tk.

**Tests.** With a deliberately slow fake writer: `finalize()` is never called while the thread is alive; state transitions are correct; the poll function returns quickly (never blocks the caller for more than a few milliseconds).

---

### I-09. ArtSync with an incomplete frame is not counted as partial (priority: low)

**Problem.** `FrameAssembler.on_artsync` calls `_commit(partial=False)` whenever some universe arrived. A complete frame is committed as soon as the last universe arrives (and `_updated` is cleared), so a non-empty `_updated` at ArtSync time means the frame was **incomplete**; it should count as partial. The docstring says so, the code does not. `partial_source_frames` is under-counted exactly when packets are lost.

**Required fix.** In `on_artsync`: if `_updated` is non-empty, commit with `partial=True`; if it is empty (frame already committed), do nothing. Fix the docstring. Update the existing tests to assert the counter.

**Tests.** (a) all universes then ArtSync: no commit by ArtSync, `partial_count == 0`; (b) one of two universes then ArtSync: committed, `partial_count == 1`; (c) staggered universes with ArtSync after each frame: `partial_count == 0`.

---

### I-10. A short ArtDMX packet is silently accepted as "updated" (priority: low)

**Problem.** In `FrameAssembler.on_universe`, if the packet carries fewer channels than the mapping needs (`src_end > 18 + dmx_len`) nothing is copied but the universe is still marked updated. Some sources trim trailing zero channels, others send truncated data by mistake. The stale bytes in the working buffer stay, with no indication.

**Required fix.** Copy the channels that are present, **zero-fill** the rest of that universe's span (trailing missing DMX channels are zero by convention), mark the universe updated, and count the event in a new counter `short_packets` (not stored in the file; no spec change). Show it in the statistics block as a warning line when non-zero: "short packets: N (e.g. U7 sends 416 channels, mapping needs 512)", naming the first offending universe.

**Tests.** A packet with 100 of 512 needed channels: first 100 copied, rest zero (also overwriting previously non-zero data), counter incremented, universe counted as updated; a full packet: no increment.

---

### I-11. Different behavior of the two formats on manual stop with a short recording (decision D11)

**Problem.** `PlsqStreamWriter` silently shortens the blend (`min(blend, len(tail), frames_written)`), while the JSON path through `postprocess()` raises an error when `blend > final`. The two formats behave differently, and the PLSQ path gives no notice.

**Required fix.** Implement D11 (section 1) in both paths. `blend_applied = min(blend, final)`; the weights use `blend_applied` (`w = i / blend_applied`); the tail frames are `frames[final + i]` for `i < blend_applied`, exactly as the streaming delay line does now. The JSON path calls `postprocess` with the clamped value. `post_loop_blend_frames` in the meta is `blend_applied`. A warning line appears in the statistics block when `blend_applied < blend`.

**Tests.** Extend the stream-equals-`postprocess` equivalence tests (R4) with cases where `final < blend`: byte-identical output between the PLSQ stream pipeline and the in-memory pipeline with the clamped blend, same `post_loop_blend_frames`, warning present; `final <= 0` still an error with data preserved.

---

## 4. Manual verification (for the user, on Windows, after the fixes)

Claude Code cannot run the GUI; these will be checked by hand:

- I-04: choose a path in a folder that does not exist: the folder is created and recording works; choose a path on a read-only location: a red error appears in the statistics block and the app stays usable.
- I-05: paste a very long text into Developer Notes: recording refuses to start with a size message.
- I-06/I-07: format radio vs typed extension; universe counter at 104 / 128 / 129 / 256 with start channel 2 / 384 / 64,000 chips; JSON radio availability follows the corrected count.
- I-08: stop a long 64,000-chip recording with loop blend: the window keeps responding, "Finalizing..." is shown, the file validates afterwards.
- I-10/I-11: warning lines are visible and readable in the statistics block.
- General: end-to-end save of both formats with `packets_tester`, then `python tools/validate_plsq.py <file>`.

## 5. Reports for this round

Create a **new** folder `docs/TASKS_RECORDER_reports_session_15_11_2026_#2/` (do not touch folder `#1`). In it:

- `REPORT_I01.md` ... `REPORT_I11.md`: one per issue, same structure as in session #1 (Goal, What changed, How verified, What could not be verified). **How verified** lists the exact commands and their results; performance claims need measurements (for I-01 include the full benchmark tables, before and after).
- `REPORT_SUMMARY.md`: a table with one row per issue (status, tests added, files changed, open items), the total test count with the command that printed it, and the before/after benchmark comparison.
- `ERRATA_session_#1.md`: corrections to statements in the session #1 reports, each verified against the code or by a measurement. Start with these known ones and check the others yourself:
  - R1: "fixed 136-byte header" is wrong; the header is 128 bytes.
  - R7: golden file described as "1 fps, start universe 0"; the file has 30 fps and start universe 1 (as in spec section 14). The size explanation "+ metadata block + header update" is misleading: 69,632 + 10 x 520 = 74,832, the meta block lies inside the first 69,632 bytes.
  - R4: "7.68 M iterations of the blend take about 0.08 s" is wrong; it takes seconds (measured about 1.1 s for 30 frames at 64,000 chips on a reference machine; give your own measurement).
  - R5: "zero per-packet allocation" is not true: the receive loop still builds `bytes(...)` with padding per packet and takes the server lock for every packet. State what is actually true, with a measurement if you claim a number.
  - R5/R8: the "CPU -18%" statement compares receive-path-only runs and does not involve the assembler or the writer.
  - R8: "recv %" is a rate inside a window, not a delivery ratio; runs were 10 s, not 60 s.
  - Test counts differ between reports (135 / 163 / 181); list the real final count.
