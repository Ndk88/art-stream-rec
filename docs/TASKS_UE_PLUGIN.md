# UE plugin tasks (C++, Unreal Engine 5.6)

Context: `CLAUDE.md` (decisions D1..D10) and `docs/PLSQ_SPEC.md`. The plugin reads `.plsq` files produced by the recorder.

How to work: one task per session unless told otherwise. Each task has a goal, required changes and acceptance criteria. Report as described in `CLAUDE.md` section 6.

**Verify every engine API against the UE 5.6 source or documentation before using it.** Names and signatures below are intentions, not guarantees. If an API is deprecated or differs in 5.6, say so in the report and pick the supported alternative. You cannot create or edit Blueprint or material assets; steps that need the editor are written for the user (section "Manual editor steps").

## Recommended order

`P0 -> P1 -> P2 -> P3 -> P5 -> P4 -> P6 -> P7 -> P8`

---

## P0. Plugin scaffolding and packaging

**Goal.** A buildable runtime plugin that can be delivered to a Blueprint-only project.

**Changes.**
- `Plugins/LightSequence/LightSequence.uplugin` (EngineVersion 5.6.0, Win64, one Runtime module `LightSequence`, `CanContainContent` true).
- Module with a log category `LogLightSequence`.
- A script or documented command for packaging with `RunUAT BuildPlugin` (`-Plugin=... -Package=... -TargetPlatforms=Win64`), so the output contains binaries and can be copied into the host project's `Plugins` folder. The host project has no C++ toolchain requirement.
- A small C++ test project or the user's UE install is used for development; do not require changes in the user's production project.

**Acceptance.** The packaged plugin loads in a Blueprint-only project on the same engine version and its classes appear in Blueprint.

---

## P1. PLSQ reader (pure C++, no UObject)

**Goal.** Read and validate header and meta exactly as in the spec.

**Changes.**
- `FPlsqHeader`, `FPlsqInfo` (typed fields plus `TMap<FString, FString> AllFields` for every meta key), `EPlsqError` with the codes of spec section 10.
- `FPlsqFile::ReadInfo(Path, OutInfo, OutError)`: reads 128 bytes, then the meta block, validates everything in the order of section 10, never touches the payload.
- CRC-32 as specified (own table implementation is fine). Meta is parsed with the engine's JSON module; all values stay strings; unknown keys are kept.

**Acceptance.** Automation tests (`IMPLEMENT_SIMPLE_AUTOMATION_TEST`) read `tests/golden/conformance_v1.plsq` and check every property of spec section 14 including the Cyrillic `developer_notes`; each corrupted file from the Python test suite produces the expected error code (copy those files into the plugin's test resources).

---

## P2. Chunk streaming engine

**Goal.** Play files of any size (up to 64,000 chips at 60 fps, hours long) without loading the payload and without file I/O on the game thread (D6).

**Design (required).**
- `FPlsqStreamer`: `Open(Path)`, `Close()`, `Seek(FrameIndex)`, `CopyFrame(FrameIndex, uint8* Dest) -> bool`, metrics (underrun count, buffered chunks).
- Chunk = `ChunkSeconds` of frames (default 1.0 s, so `fps` frames; at least 1 frame). Ring of `ReadAheadChunks` chunks (default 4, clamp 2..16) plus the current chunk. Preallocate chunk buffers once; no per-frame allocation.
- Memory cap `MaxBufferMB` (default 512). If `chunk_bytes x chunks` exceeds it, reduce the read-ahead and log a warning.
- A dedicated background thread (`FRunnable`) or UE task reads chunks sequentially with `IPlatformFile` / `IFileHandle` (or the async file API if better in 5.6). Chunk offset = `payload_offset + chunk_index x chunk_bytes`. The last chunk can be shorter.
- The game thread only calls `CopyFrame`, which copies one frame (up to 256 KB) into a caller-owned buffer. If the needed chunk is not ready it returns false (the player keeps the previous frame and counts an underrun).
- `Seek` cancels read-ahead, loads the target chunk first, then refills the ring. Looping: after the last chunk the next chunk is chunk 0.
- Open completes (the `OnLoaded` moment) only after the first chunk is ready, so Play starts without a stall.
- I/O failures are reported through the error path; never crash.

**Acceptance.** Automation or functional test with a generated synthetic file (64,000 chips, 60 fps, 60 s, made by `tools/gen_synthetic.py`): sequential playback for the full length with zero underruns on the user's machine; random seeks land on the correct frame content (verify with the `(f*7 + c*3 + k) mod 256` pattern of a pattern-compatible synthetic file or the golden file); steady memory.

---

## P3. `ULightSequencePlayer` (ActorComponent)

**Goal.** The Blueprint-facing player.

**Changes.**
- Properties: `FilePath`, `bLoop`, `PlayRate` (> 0), `ChunkSeconds`, `ReadAheadChunks`, `MaxBufferMB`.
- Functions (BlueprintCallable): `OpenFile`, `CloseFile`, `Play`, `Pause`, `Stop`, `SeekToFrame`, `SeekToTime`, `GetCurrentFrame`, `GetCurrentTime`, `IsPlaying`, `GetUnderrunCount`.
- Events (dynamic multicast delegates): `OnLoaded(Info)`, `OnFinished`, `OnLooped`, `OnError(ErrorCode, Message)`, `OnBuffering`, `OnBufferReady`. No event carries the frame bytes (256 KB per frame must never be copied into a Blueprint array).
- Time-based playback in `TickComponent`: `Time += DeltaTime x PlayRate`; `Frame = floor(Time x fps)` clamped to `frame_count - 1`; when the engine runs slower than the sequence fps, frames are skipped, not slowed down. End of file: loop or finish.
- The player owns a staging frame buffer; each tick, if the frame index changed, it copies the frame from the streamer once and hands it to the active outputs (P4).
- Optional debug function `GetChipColor(ChipIndex)` (returns a linear color from the staging frame).

**Acceptance.** Test Blueprint (user-built, see manual steps) or automation test: play, pause, seek, loop and finish behave correctly at 30 and 60 fps content with the engine running at 30, 60 and 144 fps; the player's own game-thread cost (without outputs) stays below 0.5 ms per tick at 64,000 chips (measure with Unreal Insights and report).

---

## P5. Info API for Blueprint and Pixel Streaming

**Goal.** Every field except the payload reaches Blueprint and the frontend (D8).

**Changes.**
- `USTRUCT(BlueprintType) FLightSequenceInfo`: typed convenience fields (`ModelName`, `ModelChipsTag`, `ChipsCount`, `SequenceName`, `SequenceVersion`, `DurationSec`, `Fps`, `FrameCount`, `FrameStride`, `StartUniverse`, `StartChannel`, `ChipsPerUniverse`, `UniversesCount`, `RecordedAt`, `RecorderVersion`, `SequenceId`, `DeveloperNotes`, ...), plus `TMap<FString, FString> AllFields` holding every meta key exactly as stored, plus `FileName`, `FileSizeBytes`, `FileFormat`.
- `ULightSequencePlayer::GetSequenceInfo()` and `GetSequenceInfoAsJson()`: a compact JSON object with all meta keys (values as strings, unknown keys included) plus `file_name` and `file_size_bytes`. This string is what the user sends over Pixel Streaming.
- `ULightSequenceLibrary` (Blueprint function library): `ReadSequenceInfo(Path, bSuccess, Info, ErrorCode)` reading only header + meta (no payload, safe to call for many files); `ListSequences(DirectoryPath) -> TArray<FLightSequenceInfo>` for `.plsq` files.

- Optional meta keys (recorder 1.2.x, see `docs/PLSQ_SPEC.md` section 5): `short_packets` and `first_frame_partial`. They may be absent in older files. The reader must not require them; `FLightSequenceInfo` exposes them as `ShortPackets` (int32, 0 if absent) and `bFirstFramePartial` (bool, false if absent) and keeps them in `AllFields` and in the JSON string only when present. The player shows a warning in the Output Log (and the info JSON stays untouched) when `short_packets > 0` or `first_frame_partial == "1"`.

**Acceptance.** Test confirms that every one of the 27 required keys, the two optional keys (when present; and a file without them still loads), and an extra unknown key injected into a test file, appear in `GetSequenceInfoAsJson()` as stored; `ListSequences` on a folder with 100 files returns quickly (no payload read, report the time).

---

## P4. Outputs: fake light texture, ISM data, per-chip lights

**Goal.** Drive the model at 64,000 chips (D7).

**4a. Texture output (fake light).**
- Runtime texture, RGBA8, no mips, no compression, sRGB off, filter Nearest, address Clamp. Width `TextureWidth` (default 2048), height `ceil(chips / width)`; chip `i` is pixel `(i % W, i / W)`. Prefer `PF_R8G8B8A8` so the frame bytes (R, G, B, W) copy straight into rows; if the chosen update path needs B8G8R8A8, swizzle R/B during the copy and document it. The 4th channel (W) lands in alpha.
- Update only when the frame index changes, through the supported 5.6 mechanism for updating a transient texture without re-creating the resource each frame (verify `UpdateTextureRegions`, a render-command RHI update, or a better 5.6 option). Expose `GetChipTexture()` and a helper to assign it to a dynamic material instance parameter.
- Padding pixels in the last row are zero.

**4b. ISM helper.**
- `AssignChipIndices(UInstancedStaticMeshComponent*, OptionalInstanceToChipMap)`: ensures `NumCustomDataFloats >= 1` and writes the chip index into custom data slot 0 of every instance in one batch, marking render state dirty once. Default assumption: instance `i` is chip `i`.
- Chip index is stored as a float (exact up to 16,777,216; fine for 64,000).

**4c. Per-chip light components.**
- `SetChipLights(const TArray<ULightComponent*>&)`: element `i` is chip `i`. Per frame, for every chip whose 4 bytes changed since the previous frame (keep the previous frame buffer and diff), set the light color. Color = (R, G, B) / 255 as linear values (D10); white handling is an enum `EWhiteMode`: `Ignore`, `AddToRGB` (default: `R' = min(1, R + W)`, same for G and B), configurable per player. Use the cheapest color-update API that does not rebuild the light's render state; verify by profiling.
- Light updates run on the game thread (component setters). Measure and report; do not add clusters (out of scope). If the cost is too high, report the measurements and options, and wait for the user.

**Acceptance.** For each output mode alone and for both together on a synthetic 64,000-chip, 60 fps file: report game-thread ms per frame, render thread / GPU impact and memory, captured with Unreal Insights. The texture path must show one texture update per frame and no per-instance updates.

---

## P6. Folder scanning and hot-add

**Goal.** Files copied into the sequences folder while the app runs show up without a rebuild.

**Changes.**
- Default folder `Saved/LightSequences` next to the project (editor) and next to the executable's `Saved` (packaged); the path is a property and can be overridden.
- `ULightSequenceWatcher` (ActorComponent or subsystem): polls the folder on a timer (default 2 s), diffs by file name + size + modified time, ignores files that are still growing or end with `.part`, and fires `OnLibraryChanged(NewFiles, RemovedFiles)`.

**Acceptance.** Copying a `.plsq` into the folder during play-in-editor and in a packaged build fires the event once the copy is complete; a half-written file is not announced.

---

## P7. Packaging and verification in a Blueprint-only project

**Changes.**
- Build the plugin with the P0 command; install it into a clean Blueprint-only test project and into a packaged build of it.
- Run the automation tests in the editor and the playback test in the packaged build.

**Acceptance.** All tests pass; a short report lists engine version, platform and what was tested.

---

## P8. Load tests and report

**Changes.** On the user's machine, with synthetic files from `tools/gen_synthetic.py` (chips: 128, 1,700, 10,000, 32,000, 64,000; fps 30 and 60; durations 10 s and 10 min):
- time from `OpenFile` to `OnLoaded`;
- player cost, texture output cost, light output cost (separately);
- underruns, memory;
- hitches at loop point and after seeks.

Write `docs/BENCHMARK_PLUGIN.md` with the table, an Unreal Insights note per scenario and a conclusion.

**Acceptance.** The report exists and states plainly which scenarios meet real-time at 60 fps and which do not.

---

## Manual editor steps (for the user, document them in the plugin README)

1. **Material for fake lights.** Use `PerInstanceCustomData[0]` as the chip index `i`. With texture width `W` and height `H`: `u = (fmod(i, W) + 0.5) / W`, `v = (floor(i / W) + 0.5) / H`. Sample the chip texture with mip level 0 (no mip bias) and plug RGB into Emissive (alpha is the white channel; combine as needed). Provide the texture as a texture parameter set from `GetChipTexture()`.
2. **Blueprint wiring.** Add `ULightSequencePlayer` to the model actor; call `OpenFile` with a path from `ListSequences`; bind `OnLoaded` (use `GetSequenceInfoAsJson` to send the fields through Pixel Streaming); call `Play`.
3. **Chip order.** The order of chips in the file must equal the order of chips in the model (instance index or light array index equals chip index).
