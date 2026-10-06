# Clips Kitty repo map — video processing, FFmpeg, rendering, captions, remote render, long-form, editor

Branch `claude/open-platform-w4eh9g`. All paths repo-relative. "(inferred)" marks conclusions not stated in code.

## 1. Rendering entry points (Python functions)

| Capability | Function (signature) | File |
|---|---|---|
| Cut a time range (re-encode, source aspect) | `cut_clip(source: Path, candidate: ClipCandidate, output_path: Path, ass_path: Path\|None=None, vf_extra: str="", normalize: bool=False) -> Path` | `video/cutter.py:21` |
| Crop to 9:16 from a tracking result | `render_vertical(clip_path: Path, tracking: dict, output_path: Path, ass_path=None, vf_extra="", cam_position="top", normalize=True) -> Path` — dispatches on `tracking["mode"]` ∈ `track` / `split` / `fit_blur` | `video/cropper.py:33` |
| Compute the tracking (YOLOv8 pose + Haar + TalkNet) | `compute_tracking(intermediate, model_name=..., sample_fps=..., force_fit_blur=...)` (called at `core/pipeline.py:1340`); returns `{"mode":"track","path":[(t,cx)...]}` / `{"mode":"split","webcam_box":(x,y,w,h)}` / fit_blur (docstring `video/tracker.py:1-12`) | `video/tracker.py:808` |
| Podcast shot-by-shot framing | `analyze(clip_path, model_name="yolov8n-pose.pt", sample_fps=8.0) -> dict`, `render_clip(intermediate, output_path, decision, ass_path=None, vf_extra="", normalize=True)` (wraps `render_vertical`) | `video/podcast.py:68`, `:227` |
| Caption lines as editable data | `build_caption_lines(segments, candidate, words_per_caption=3, turns=None) -> list[dict]` → `[{"start","end","text",("speaker")}]`, clip-relative seconds | `video/captions.py:87` |
| Burn captions (write ASS; FFmpeg burns via `subtitles=` filter) | `build_captions(segments, candidate, output_path, style=None, lines=None, canvas=(1080,1920), language="en") -> Path\|None` | `video/captions.py:454` |
| Colour filter / adjust chain | `filter_chain(name)`, `adjust_chain(adjust)`, `combined_chain(preset, adjust) -> str` (FFmpeg `-vf` fragment); `PRESETS` registry validates names | `video/filters.py:12,69,98,132` |
| Post style (highlights title card) | `resolve(caption_style) -> str`, `card_position(...)`, `caption_style_for(...)`, `render_card(headline, subline, size, out_path, position="lower", language="en") -> Path\|None`, `apply_card(video_path, card_png) -> None` (overlay in place) | `video/post_style.py:123,129,134,678,738` |
| End card / outro | `append(clip: Path, config: dict) -> bool`, `finish(src, dst, config) -> bool` (concat pre-card scratch → final), `enabled(config) -> bool`, `ensure_outro(fmt, config) -> Path`, `has_outro(clip, window_seconds)`, `backfill(db, config, video_id=None)` | `video/outro.py:1192,1398,1064,991,1337,1358` |
| Thumbnail candidates (local, Haar faces, PIL text) | `generate(video_path: Path, hook: str, targets: list[Path]) -> list[Path]` | `video/thumbnail.py:191` |
| Hook title overlay (ASS) | `ensure_hook(ass_path, target, hook: dict, canvas=(1080,1920), font="Arial Black") -> Path` | `video_editor/overlay.py:48` |
| Watermark text / image | `ensure_text(...)`, `apply_image(video_path, cfg, canvas, asset_dir) -> None`, `has_text(cfg)`, `has_image(cfg, asset_dir)` | `video_editor/watermark.py:167,202,65,69` |
| Editor edit list → one FFmpeg pass | `apply_edits(input_path, edit: EditList, output_path, ass_path=None, normalize=False) -> Path` | `video_editor/export.py:21` |
| Hardware encoder args | `video_encoder_args(config=None) -> list[str]`, `using_hardware_encoder() -> bool`, `hwaccel_input_args()`, `audio_filter_args(normalize=True)` | `video/encoding.py:177,186,105,89` |
| Source normalisation | `ensure_h264_source(path, config=None) -> bool` (AV1/VP9/HEVC → H.264 in place), `source_codec(path)`, `source_probe(path) -> dict`, `readable_video(path) -> bool` | `video/encoding.py:244,204,217,285` |
| Import a local file | `import_local_source(src, dest, codec: str) -> bool` | `video/encoding.py:309` |
| GPU-decoded frame sampler | `sampled_frames(clip_path, every, width, height, hwaccel=True)` generator of `(frame_index, bgr)` | `video/encoding.py:112` |
| Safe OpenCV capture | `video_capture(path, *, required=True)` context manager | `video/capture.py:30` |
| Scene-cut helpers | `is_cut(prev_small, cur_small, threshold=25.0)`, `refine_cuts(clip_path, regions, threshold)`, `pick_focus(...)`, `HoldMove` | `video/framing.py:48,62,102,134` |
| Preview frames before processing | `frame(cache_dir, at, *, url=None, path=None) -> Path` (yt-dlp media URL + FFmpeg seek; platform allowlist) | `sources/preview_frames.py:208` |
| Subtitle files | `write_srt(lines, path)`, `write_vtt(lines, path)` | `multilingual/subtitles.py:21,34` |
| Translated-caption burn | `clean_base(clip_row, config, data_dir, work_dir) -> Path\|None`, `burn(...)` | `multilingual/burn.py:37,67` |

**The orchestrator** is `core/pipeline.py:_render_files(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en") -> tuple[Path, str]` (`core/pipeline.py:1123`). Docstring: "Pure file work — cut, track, crop, captions, color. NO database access and NO LLM call" (`core/pipeline.py:1132-1135`). Order (from `core/pipeline.py:1240-1485`): build ASS (captions → hook → watermark text) → `cut_clip` to `.source.mp4` (end padded +0.4 s, `:1303`) → `apply_edits` if `edit` → gaming / podcast / sport / `compute_tracking` + `render_vertical` (or `cut_clip` straight for landscape / vertical-live) → title card (`_title_card`) → watermark image → `outro.finish`. Returns `(final_path, render_opts_json)`.

Output naming: `clip_{start:05d}-{end:05d}.mp4` in `clip_dir` (`core/pipeline.py:1151-1152`); scratch files `.pre-card.mp4`, `.source.mp4`, `.edited.mp4`, `.plain.mp4`, `.ass`, `.card.png`.

## 2. The render request — `render_opts` dict

Persisted per clip as JSON in the `clips` table column `render_opts` (`core/pipeline.py:763,1594`). All keys optional. From `core/pipeline.py:1136-1139,1170-1230,1456-1482` and `video_editor/timeline.py:3-16`:

| Key | Type / values | Read at |
|---|---|---|
| `captions` | bool (default True) | `core/pipeline.py:1233` |
| `caption_style` | dict, merged over `DEFAULT_STYLE` (§5); may also carry `post_style`, `card_position` | `:1223`, `video/post_style.py:123-131` |
| `caption_lines` | `[{"start","end","text",("speaker")}]` user-corrected lines | `:1234` |
| `crop` | `"track"` \| `"center"` \| `"letterbox"` \| `"bias_left"` \| `"bias_right"` | `:1326,1342` |
| `filter` | preset name from `video/filters.py:PRESETS` | `:1190` |
| `adjust` | `{"brightness","saturation","contrast"}` within `ADJUST_RANGES` | `:1191`, `video/filters.py:85` |
| `edit` | editor edit list: `keep`, `mutes`, `muted_words`, `volume`, `mute_all`, `fade_in`, `fade_out`, `speed`, `hook{text,seconds}`, `music{path,volume,duck}` | `video_editor/timeline.py:3-16,42-54` |
| `normalize_audio` | bool (default True) | `:1177` |
| `podcast` | bool | `:1182` |
| `vertical_live` | bool (via `core/modes.is_vertical_live`) | `:1190` |
| `gaming` | dict (gaming layout; `core/modes.clean_gaming`) | `:1198-1200`, `core/modes.py:115` |
| `sport` | sport name (`core/modes.sport`) | `:1195` |
| `profile` | longform profile name (`longform/profiles.py:PROFILES`); landscape 1920x1080 when present | `:1173` |
| `watermark` | branding dict (schema in `video_editor/watermark.py:12-24`) | `:1265` |
| `headline`, `subline` | highlights title-card text | `:1467` |
| `speaker_edits`, `speaker_turns` | second-speaker colouring data (`#126`) | `:1241,1454` |

Clip time range comes from `ClipCandidate(start, end, score, hook, reason, source, engagement, trending, subscores)` (`core/models.py:44-63`); transcript from `Segment(start, end, text, words: list|None)` (`core/models.py:29-40`).

## 3. FFmpeg: location and invocation

- **Location** `core/binaries.py:60-82`: `_resolve(name, folder)` → (1) env `CLIPS_STUDIO_FFMPEG` / `CLIPS_STUDIO_FFPROBE`, (2) beside the frozen exe (`exe_dir`, `exe_dir/ffmpeg`, `exe_dir/_internal/ffmpeg`, `_MEIPASS`), (3) repo `vendor/ffmpeg`, (4) `shutil.which` or bare name. `ffmpeg()` / `ffprobe()` return a str path. `missing()` lists absent binaries for preflight (`core/binaries.py:221`). Same module resolves `ollama()`, `whisper_model(size)`, `yolo_weights(name)`, `haar_cascade(filename)`.
- **Encoder selection** `video/encoding.py:26-80,177-197`: candidates in order `nvenc` → `amf` → `qsv`, each probed once per process by a real 0.1 s encode of `color=black:s=256x256` with the exact args (`_probe`, `:40`). `CPU_ARGS = ["-c:v","libx264","-preset","veryfast","-crf","20"]`. Candidate args: nvenc `h264_nvenc -preset p5 -rc vbr -cq 23 -b:v 0`; amf `h264_amf -quality quality -b:v 8M -maxrate 12M`; qsv `h264_qsv -global_quality 23 -preset medium` (`:30-34`). Config `video.encoder` = `auto|nvenc|amf|qsv|cpu` (`:14-16,177-183`); a forced encoder failing its probe falls back to CPU (`:62-66`).
- **CPU fallback at render time**: `cut_clip` retries once with `CPU_ARGS` when the GPU encoder refuses real footage (10-bit input etc.), via `_swap_encoder` (`video/cutter.py:62-78`); `apply_card` imports the same trio (`video/post_style.py:747`). Tested in `tests/test_render_diagnosis.py:76`.
- **Decode**: `-hwaccel auto` before `-i` (`video/encoding.py:105-109`); `sampled_frames` pipes raw frames from FFmpeg for the tracker (`:112`), tested by `tests/test_gpu_decode.py`.
- **Audio**: `-af aresample=async=1[,loudnorm=I=-14:TP=-1.5:LRA=11]` (`video/encoding.py:83-102`); `-c:a aac -b:a 128k`, `-movflags +faststart`, `-vsync cfr` (`video/cutter.py:35-50`).
- Every call is `subprocess.run([...], capture_output=True)`; captions burned with `-vf subtitles=<name>.ass` run with `cwd=ass_path.parent` to dodge Windows path escaping (`video/cutter.py:52-58`).

## 4. Remote render protocol (`remote_render/`)

Package map in `remote_render/__init__.py:1-23`. User doc `docs/REMOTE-RENDERING.md`. Tests `tests/test_remote_render.py`.

- **Transport**: separate FastAPI app (`gateway.py:47-51`, title "Clips Kitty render gateway", no docs) on `0.0.0.0:8766` (default `settings.py:DEFAULTS["port"]`), HTTPS with a self-signed cert generated once (`tls.py:18`), worker pins the SHA-256 fingerprint at pairing (TOFU, `tls.py:1-9,73`). Worker connects OUT; nothing on the worker listens (`worker.py:5-7`).
- **Auth**: pairing code `ABCD-EFGH` (8 chars, 10-minute TTL, single use, 5 wrong tries void it — `queue.py:112-140`, doc `REMOTE-RENDERING.md` Security). `POST /v1/pair {code, name, caps}` → `{worker_id, secret, protocol}` (`gateway.py:72-82`). Every later route needs headers `X-Worker-Id` + `Authorization: Bearer <secret>`; main PC stores only `sha256(secret)` compared with `hmac.compare_digest` (`gateway.py:56-62`, `queue.py:142-145`).
- **Routes** (`gateway.py:72-205`): `POST /v1/heartbeat {caps, draining, max_jobs, running[]}`; `POST /v1/claim` → job JSON or 204; `GET /v1/jobs/{id}/piece` (Range-resumable MP4); `GET /v1/jobs/{id}/assets/{name}` (allowlisted branding PNG/JPG/WebP); `POST /v1/jobs/{id}/progress {stage, progress}`; `GET /v1/jobs/{id}/result` → `{received}`; `PUT /v1/jobs/{id}/result?offset=N` (chunked append, 409 with `{received}` on mismatch); `POST /v1/jobs/{id}/complete {size, sha256, render_opts}` (size + sha + ffprobe-duration check); `POST /v1/jobs/{id}/fail {error, log}`.
- **Job spec** (`dispatch.py:141-150`): `{"protocol": 1, "video_id", "label", "start", "end", "offset", "candidate": asdict(ClipCandidate) minus subscores, "segments": [{start,end,text,words}], "render_opts", "language", "config": render_config(config)}`. `render_config` allowlists only config sections `clips`, `tracking`, `video` (`protocol.py:15-24`); job id = sha256 of (video_id, start, end, render_opts, render_config, PROTOCOL)[:32] (`protocol.py:27-33`). Piece = stream-copied stretch from the keyframe before start−3 s; `offset` shifts times into the piece (`piece.py:1-18,99`).
- **Capabilities** (`protocol.py:109-131`): `{protocol, version, name, os, cpu, cores, ram_gb, cpu_percent, gpu{name,vram_total_gb,vram_used_gb,utilisation}, encoders[], ffmpeg, framing}`; matching in `compatible(job_needs_framing, caps)` (`:134-142`) and `needs_framing(render_cfg, render_opts)` (`:36-45`).
- **Worker side** (`worker.py`): `pair(data_dir, main, code, name="")` (`:56`), `Worker.run()` loop with 10 s heartbeat (`HEARTBEAT=10.0`, `:27`), each job rendered in a child process `main.py render-worker --run-job DIR` (`:84`, `:358`) which calls `core.pipeline._render_files(job_dir/"piece.mp4", candidate, segments, out_dir, config, spec["render_opts"], spec["language"])` (`:366-381`). Entry: `main.py:193,266`.
- **Queue** (`queue.py`): own SQLite `data/remote_render/queue.db` with tables `workers`, `pairing`, `jobs`; states `queued → assigned → transferring → rendering → uploading → verifying → completed | failed | cancelled | local` (`:11-16`). Silent worker (45 s) requeues its jobs (`REMOTE-RENDERING.md` "If something goes wrong").
- **Pipeline side** (`dispatch.py:28`): `renderer_for(config)` returns a `RemoteRenderer` only when enabled and mode ≠ local; `render_all()` yields `(candidate, meta, get_result)` like the local futures. Modes `local|auto|worker:<id>` (`settings.py:16-18`).
- **Local-API control routes** (127.0.0.1:8765, `service.py:install`): `GET/PUT /remote-render`, `POST /remote-render/pairing-code`, `DELETE /remote-render/workers/{id}`, `POST /remote-render/workers/{id}/hold`, `PUT /remote-render/this-pc`, `POST /remote-render/this-pc/unpair`, `POST /remote-render/videos/{video_id}/render-locally`, `POST /remote-render/jobs/{job_id}/retry` (`service.py:181-255`).
- Settings stored via `core/secrets` (DPAPI on Windows) under name `remote_render` (`settings.py:1-5,13`).

**Assessment (inferred)**: yes, this is a complete "remote worker" protocol — pull-based job queue, capability advertisement + matching, pairing/TOFU TLS, resumable transfers, checksummed results, versioned `PROTOCOL`. It is purpose-built for one job type (`_render_files` on a piece) with an allowlisted config; a platform worker abstraction could generalise `spec`/`caps`/`compatible` rather than replace them.

## 5. Sources: download and local import

- **Dispatcher** `sources/dispatch.py`: `identify(url) -> (source_name, video_id|None)` (`:14`) recognises `local:<id>`, Twitch (`twitch.is_twitch_url`), Kick, else YouTube; `download(url, output_dir, vertical=False) -> DownloadedVideo` (`:146`); `metadata(url) -> (title, channel)` (`:111`); `game_info(url)` (`:71`); `description(url)` (`:92`); `platform_of_link(url)` (`:37`). Docstring: "Adding a platform means one new source module and one branch here" (`:3-6`). Host matching via `sources/urlmatch.host_matches(url, domain)` (`sources/urlmatch.py:14`).
- **`DownloadedVideo`** (`core/models.py:11-25`): `video_id: str, title: str, path: Path, duration: float, channel: str = "", games: list = [] ({"name","start","end"}), description: str = ""`.
- **Per-platform** `download(url, output_dir, vertical=False) -> DownloadedVideo`: `sources/youtube.py:162` (yt-dlp, format `bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/...` → `%(id)s.mp4`, refuses `is_live`), `sources/twitch.py:37` (VODs only, ids `tw_<n>`), `sources/kick.py:81` (ids `kick_<uuid>`). Vertical Live variants use `sources/vertical.py` (`SUFFIX="__vertical"`, `YOUTUBE_FORMAT`, `HLS_FORMAT`, `:17-34`). Shared yt-dlp options (progress hook + cancel + bundled FFmpeg dir) in `sources/ytdlp_common.py:15,34`.
- **Cache-or-download** in the pipeline: `core/pipeline._cached_or_download(url, data_dir, db, vertical=False)` (`core/pipeline.py:1010`); `convert_slow_source(video, config)` runs `ensure_h264_source` for AV1/VP9/HEVC (`:175-189`).
- **Local file import**: HTTP `POST /videos/local` (`server/api.py:867`), body `LocalVideoIn{path, title, channel, platform, captions, caption_style, long_clips, podcast, vertical_live, gaming*, source_url, longform, watermark_profile_id, filter, min_score, focus, sport, max_clips, force, webhook_url, webhook_secret}` (`server/api.py:203-236`). It ffprobes the codec, derives `video_id = "local_" + md5(path|size|mtime)[:12]`, calls `video.encoding.import_local_source(src, dest=downloads/<id>.mp4, codec)` (H.264 stream-copied; HEVC/AV1/VP9 copied then converted by the job; PCM audio re-encoded), upserts the video row and queues a `process` job with `url="local:<id>"` (`server/api.py:880-975`). `GET /videos/local/shape?path=` reports width/height/orientation (`server/api.py:854`).
- Source frames before processing: `GET /sources/frame|people|snap|suggest` (`server/api.py:1807-1880`) backed by `sources/preview_frames.frame` (platform allowlist, yt-dlp media URL + FFmpeg seek, `sources/preview_frames.py:208`).

## 6. Caption data shapes and styles

- **Transcript words**: `transcription/transcriber.py:152,168` — Whisper `word_timestamps=True`; each `Segment.words` entry is `{"start": float, "end": float, "word": str}` (absolute video seconds, rounded to 0.01). Transcript file `data/transcripts/<video_id>.json` = `{"segments": [{start,end,text,words}]}` (read at `server/api.py:1724-1727`).
- **Clip-relative words**: `clip_words(words, start, end) -> [{"start","end","word"}]` (`video/captions.py:233`), served by `GET /clips/{id}/words` (`server/api.py:1981`).
- **Caption lines**: `[{"start","end","text",("speaker")}]` clip-relative (`video/captions.py:87-124`); `GET/PUT /clips/{id}/captions` (`server/api.py:1545,1556`); PUT queues a `render` job with `render_opts.caption_lines`. `refit_caption_lines(...)` re-times saved lines when a clip's range changes (`video/captions.py:399`, used `server/jobs.py:721`).
- **Caption style dict** = `DEFAULT_STYLE` (`video/captions.py:20-31`): `font` (whitelist `FONTS`, `:36-55`), `font_size` (clamped 40..140 at 1920 tall, `:590`), `color` hex, `position` bottom|middle|top (`_POSITIONS`, `:84`), `words_per_caption`, `uppercase`, `highlight`, `highlight_color`, `second_speaker`, `second_speaker_color`. Non-Latin languages swap the font via `SCRIPT_FONTS` / `caption_font_for(language, chosen)` (`:59-82`). Rendering = one ASS `Style: Default,...` line (`_header`, `:587-610`) + `Dialogue:` events; the highlight mode emits per-word events (`_highlighted`, `:550`). Post style `highlights` forces `CAPTION_LOOK` (`Impact`, `#F5FA00`, middle) over the user's size (`video/post_style.py:51-58,134-137`).
- Extra ASS overlays merged into the same file: hook title (`video_editor/overlay.py`), watermark text (`video_editor/watermark.py:167`). Translated captions: `multilingual/subtitles.write_srt/write_vtt` and `multilingual/burn.burn(base_video, lines, language, out_path, caption_style, config)` (`multilingual/burn.py:67`), driven by the `translate` job (`server/api.py:2091` → `server/jobs.py:402`).

## 7. The video editor (`video_editor/`)

- Package docstring `video_editor/__init__.py:1-12`: "Lightweight Shorts finishing editor. Applies a small, non-destructive edit list (stored in the clip's render_opts under 'edit') during the normal re-render". All times are clip-relative seconds in the clip's ORIGINAL timeline; mutes apply before cuts.
- **Timeline model** `EditList` (`video_editor/timeline.py:42-54`): `duration, keep: list[(a,b)]|None, mutes, volume (0..2), mute_all, fade_in/fade_out (0..3), speed (0.5..3), hook {text≤120, seconds 1..10}, music {path, volume 0..1, duck}`. `EditList.from_dict(d, duration) -> EditList|None` validates/clamps (`:56-110`); `remap(t)` maps original → final time (`:127`); `final_duration()`.
- **Export** `apply_edits(input_path, edit, output_path, ass_path=None, normalize=False)` builds one `-filter_complex` (mutes → trim/concat → loudnorm/atempo/volume/afade → optional music with `sidechaincompress` ducking → `setpts` speed → `subtitles=`) (`video_editor/export.py:21-108`). `cuts.concat_graph(keep, audio_in)` (`video_editor/cuts.py:9`), `audio.mute_filter`, `audio.master_filter` (`video_editor/audio.py:9,19`), `captions.remap_lines(lines, edit)` (`video_editor/captions.py:16`).
- **Where it runs**: inside `_render_files` between `cut_clip` and the vertical render (`core/pipeline.py:1327`), or as the final encode on the landscape path (`:1407`).
- **HTTP**: `POST /clips/{id}/preview` body `PreviewIn{edit, caption_lines, crop, caption_style, watermark, normalize_audio, gaming, gaming_off, headline, subline, speaker_edits}` renders synchronously through `_render_files` into `data/previews/clip_<id>.mp4` and returns `{"url": "/media/preview/<id>"}` (`server/api.py:1705-1790`, `:158-179`). `POST /clips/{id}/render` body `RenderIn{start, end, render_opts}` queues a `render` job (`server/api.py:1679`, `:238-241`), handled by `JobWorker._rerender_clip` which merges persisted + incoming `render_opts`, refits captions, re-runs `_render_files` and `_register_clip` (`server/jobs.py:678-790`). `POST /clips/{id}/tighten` proposes keep ranges (`server/api.py:1577`). `POST /clips/{id}/ai-edit` → `analysis/clip_edit.interpret_edit` emits validated `render_opts` (`crop`, `filter`, `caption_style`, start/end) and queues a render (`server/api.py:1617-1677`, `analysis/clip_edit.py:94-125`). UI client functions `rerenderClip`, `previewClip`, `clipWords`, `captions` in `ui/src/renderer/src/lib/api.ts:261-360`.
- Rendered clip served by `GET /media/{clip_id}` (`server/api.py:2001`); export copies by `POST /clips/{id}/export {folder}` and `POST /export/batch {clip_ids, folder}` (`:2243-2272`).

## 8. Long-form modes (`longform/`)

- Modes in `longform/profiles.py:PROFILES`: `short_clips` (10–60 s), `clips_140` (10–140 s), `highlights` (8–90 s building blocks, assembled to an 8–20 min video), `edited_stream` (full VOD minus downtime). All output 1920x1080 under `Longform/<label>/` inside the video's clip dir; the Shorts profile is "implicit and untouched (no 'profile' key in render_opts)" (`longform/profiles.py:1-6`).
- Entry `process_longform(url, config, db, options) -> None` with `options["mode"]` (`longform/process.py:28,46`); reuses `_cached_or_download`, `transcribe`, `find_clips`, `_render_files`, `_register_clip` (`:35-45`). Queued from `POST /jobs` / `POST /videos/local` with `longform: {"mode": ...}` (`server/api.py:39-63`, `server/jobs.py:260-268`).
- `highlights`: `select_highlights(candidates, duration)` (`longform/highlight_select.py:25`, FLOOR 8 min / CEILING 20 min, chronological, spread across 8 sections) → `assemble(source, keep, output_path, video_id, on_progress=None, config=None) -> Path` (`longform/assemble.py:25`; per-range GPU encodes + concat demuxer, end card as last part) → `chapters.with_chapters(description, keep, candidates)` (`longform/chapters.py:87`).
- `edited_stream`: `detect_keep_ranges(...)` from audio/visual per-second arrays (`longform/downtime.py:25`) → `assemble`.
- Landscape single-clip render: `render_opts["profile"]` makes `_render_files` use canvas 1920x1080 and a `scale/pad` fit (`core/pipeline.py:1173,1193-1198`).

## 9. Other render modes reachable through `render_opts`/config

- Vertical Live (`core/modes.py:44`, `fit_filter`, `probe_size`, doc `docs/VERTICAL-LIVE.md`): one FFmpeg encode of the whole 9:16 frame, no tracking.
- Gaming / split-screen: `gaming/run.render(intermediate, output, g, config, ass_path=None, vf_extra="", normalize=True) -> dict|None` (`gaming/run.py:183`), tried first by `_try_gaming_render` (`core/pipeline.py:817`, called at `:1331-1332`).
- Sports framing: `_sport_framing(clip_path, config, sport_name) -> dict|None` returns a tracking dict (`core/pipeline.py:1550`).
- Podcast: §1.
- Highlights post style + title card: `_title_card(clip, opts, position, png, language)` (`core/pipeline.py:1522`).
- Outro: `clips.outro` config (default on, `config/settings.yaml:71`), appended by `outro.finish` in `_render_files` (`core/pipeline.py:1446`) and as the last concat part in longform `assemble`.
- Config knobs: `video.encoder`, `video.parallel_renders` (default 3), `tracking.detector` (`yolov8n-pose.pt`), `tracking.sample_fps` (8), `clips.vertical/captions/outro` (`config/settings.yaml:60-129`).

## 10. HTTP surface relevant to video (all on `127.0.0.1:8765`, no auth — `remote_render/gateway.py:3-4`)

| Endpoint | What it does | Backed by |
|---|---|---|
| `POST /jobs` `JobIn` | queue a URL for the full pipeline (download → transcribe → score → render) | `server/api.py:803`, `core/pipeline.process_video` (`core/pipeline.py:192`) |
| `POST /videos/local` `LocalVideoIn` | import a local file + queue | `server/api.py:867`, `video/encoding.import_local_source` |
| `GET /videos/local/shape?path=` | probe size/orientation | `server/api.py:854`, `core/modes.probe_size` |
| `POST /clips/{id}/render` `RenderIn` | async re-render with new range/opts | `server/api.py:1679` → `server/jobs.py:678` |
| `POST /clips/{id}/preview` `PreviewIn` | sync render through the real path to a preview file | `server/api.py:1705` → `_render_files` |
| `GET/PUT /clips/{id}/captions` | caption lines read / save+re-render | `server/api.py:1545,1556` |
| `GET /clips/{id}/words` | clip-relative word timestamps | `server/api.py:1981` |
| `POST /clips/{id}/tighten` | propose keep ranges (no render) | `server/api.py:1577` |
| `POST /clips/{id}/ai-edit` | LLM → render_opts → render job | `server/api.py:1617` |
| `GET /media/{id}`, `GET /media/preview/{id}` | serve MP4s | `server/api.py:2001,1974` |
| `POST /clips/{id}/export`, `POST /export/batch` | copy files to a folder | `server/api.py:2243,2247` |
| `GET /sources/frame`, `/sources/people`, `/sources/snap`, `/sources/suggest`; `GET /clips/{id}/source-frame|snap|panels|people` | frames + person detection for layout setup | `server/api.py:1807-1933` |
| `GET /clips/{id}/frame?t=`, `POST /clips/{id}/thumbnail/generate?count=`, `GET /clips/{id}/thumbnail/generated/{i}`, `POST /clips/{id}/thumbnail` | thumbnail picker; generate uses `video.thumbnail.generate` | `server/youtube_api.py:538,682,715,723` |
| `POST /translate` + `/clips/{id}/translations*` | translate + burn/subtitle export job | `server/api.py:2091-2240`, `server/jobs.py:402` |
| `GET/PUT /remote-render`, `/remote-render/*` | remote render settings, pairing, workers | `remote_render/service.py:181-255` |
| `GET /branding`, `POST /branding/asset`, ... | watermark profiles and image assets | `server/api.py:2274-2360` |

MCP server (`server/mcp.py`) is a stdio translation layer over the same HTTP API ("No second engine", `server/mcp.py:14-18`); tools include `queue_video`, `queue_local_file`, `clip_captions`, `export_clip` — no direct render/preview tool (`server/mcp.py:291-440`). Docs: `docs/API.md`.

## 11. Capability list: what a plugin could call today

| Capability | Python function | File | Reachable over HTTP? |
|---|---|---|---|
| Download a URL (YouTube/Twitch/Kick) | `sources.dispatch.download(url, output_dir, vertical=False) -> DownloadedVideo` | `sources/dispatch.py:146` | Only as part of a full job: `POST /jobs` (`server/api.py:803`). No standalone download endpoint. |
| Import a local file | `video.encoding.import_local_source(src, dest, codec) -> bool` | `video/encoding.py:309` | Yes, `POST /videos/local` (`server/api.py:867`) — but it also queues the full pipeline. |
| Probe a source (codec/pix_fmt/size, readability) | `source_codec`, `source_probe`, `readable_video`; `core.modes.probe_size` | `video/encoding.py:204,217,285`; `core/modes.py:189` | Partly: `GET /videos/local/shape` (size only, `server/api.py:854`). |
| Normalise a slow codec to H.264 | `ensure_h264_source(path, config=None) -> bool` | `video/encoding.py:244` | No (runs inside the job, `core/pipeline.py:175`). |
| Cut a time range (re-encode) | `cut_clip(source, candidate, output_path, ass_path=None, vf_extra="", normalize=False)` | `video/cutter.py:21` | No standalone; only via a full render (`/clips/{id}/render`, `/preview`). |
| Cut a stretch without re-encoding (keyframe piece) | `remote_render.piece.cut(source, start, end, dest) -> offset` | `remote_render/piece.py:99` | No (gateway serves pieces to paired workers only, `GET /v1/jobs/{id}/piece`). |
| Track the subject / choose layout | `video.tracker.compute_tracking(path, model_name, sample_fps, force_fit_blur)`; `video.podcast.analyze(...)` | `video/tracker.py:808`; `video/podcast.py:68` | No. |
| Crop to 9:16 from a tracking dict | `video.cropper.render_vertical(clip_path, tracking, output_path, ass_path, vf_extra, cam_position, normalize)` | `video/cropper.py:33` | No standalone; inside `_render_files`. |
| Build caption lines / word list | `build_caption_lines(segments, candidate, words_per_caption, turns)`, `clip_words(...)` | `video/captions.py:87,233` | Yes: `GET /clips/{id}/captions`, `GET /clips/{id}/words` (`server/api.py:1545,1981`). |
| Write an ASS caption file | `build_captions(segments, candidate, output_path, style, lines, canvas, language) -> Path|None` | `video/captions.py:454` | No (file only reachable as a burned result). |
| Burn captions into an existing video | `multilingual.burn.burn(base_video, lines, language, out_path, caption_style, config)` | `multilingual/burn.py:67` | Via the `translate` job with `burn=True` (`POST /translate`, `server/api.py:2091`). |
| Colour filter / adjustments | `video.filters.combined_chain(preset, adjust) -> str`; `PRESETS` | `video/filters.py:132,12` | Via `render_opts.filter/adjust` on `/clips/{id}/render` and `/preview`; job-wide `filter` on `POST /jobs`. |
| Apply an edit list (cuts/mutes/speed/music/hook) | `video_editor.export.apply_edits(input_path, edit, output_path, ass_path=None, normalize=False)` | `video_editor/export.py:21` | Via `render_opts.edit` on `/clips/{id}/render` and `/preview` (`server/api.py:1679,1705`). |
| Hook title overlay | `video_editor.overlay.ensure_hook(ass_path, target, hook, canvas, font)` | `video_editor/overlay.py:48` | Via `render_opts.edit.hook`. |
| Watermark text / image | `video_editor.watermark.ensure_text(...)`, `apply_image(video_path, cfg, canvas, asset_dir)` | `video_editor/watermark.py:167,202` | Via `render_opts.watermark`, branding profiles `/branding*` (`server/api.py:2274-2360`). |
| Highlights title card | `post_style.render_card(headline, subline, size, out_path, position, language)`, `apply_card(video_path, card_png)` | `video/post_style.py:678,738` | Via `render_opts.headline/subline` + `caption_style.post_style="highlights"` on `/render`, `/preview`. |
| Append the end card | `video.outro.append(clip, config) -> bool`; `finish(src, dst, config)` | `video/outro.py:1192,1398` | Only implicitly (config `clips.outro`); no endpoint. |
| Full clip render (the orchestrator) | `core.pipeline._render_files(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en") -> (Path, str)` | `core/pipeline.py:1123` | Yes: `POST /clips/{id}/preview` (sync) and `POST /clips/{id}/render` (queued) — for an EXISTING clip row only; no "render arbitrary range of arbitrary file" endpoint. |
| Register a rendered clip in the library | `core.pipeline._register_clip(db, video_id, candidate, final_path, meta, render_opts_json, config)` | `core/pipeline.py:1563` | No (internal; `render` job does it). |
| Thumbnail candidates | `video.thumbnail.generate(video_path, hook, targets) -> list[Path]` | `video/thumbnail.py:191` | Yes: `POST /clips/{id}/thumbnail/generate` (`server/youtube_api.py:682`). |
| Single frame extraction | `sources.preview_frames.frame(cache_dir, at, url=, path=)`; `_clip_frame` | `sources/preview_frames.py:208`; `server/api.py:1878` | Yes: `GET /sources/frame`, `GET /clips/{id}/source-frame`, `GET /clips/{id}/frame` (`server/youtube_api.py:538`). |
| GPU-decoded frame sampling | `video.encoding.sampled_frames(clip_path, every, width, height, hwaccel=True)` | `video/encoding.py:112` | No. |
| Encoder / FFmpeg discovery | `core.binaries.ffmpeg()/ffprobe()/missing()`; `video.encoding.video_encoder_args()`, `using_hardware_encoder()`; `remote_render.protocol.capabilities()` | `core/binaries.py:74,79,221`; `video/encoding.py:177,186`; `remote_render/protocol.py:109` | Partly: `GET /health/preflight` (`server/api.py:718`) and worker caps via `GET /remote-render` (`service.py:181`). |
| Longform assembly | `longform.assemble.assemble(source, keep, output_path, video_id, on_progress, config)`; `select_highlights`; `detect_keep_ranges` | `longform/assemble.py:25`; `highlight_select.py:25`; `downtime.py:25` | Only via `POST /jobs {longform:{mode}}`. |
| SRT / VTT writing | `multilingual.subtitles.write_srt(lines, path)`, `write_vtt` | `multilingual/subtitles.py:21,34` | Via the `translate` job (`subtitles=True`). |
| Dispatch a render to a remote worker | `remote_render.dispatch.renderer_for(config)`, `RemoteRenderer.render_all(...)` | `remote_render/dispatch.py:28,118` | Indirectly (any job when mode≠local); control via `/remote-render*`. |

## 12. Open questions / observations for the platform work (inferred unless cited)

1. `_render_files` and `_register_clip` are underscore-private yet are the de-facto public render contract — already imported by `server/api.py:1732`, `server/jobs.py:686`, `remote_render/worker.py:366`, `multilingual/burn.py:43`, `longform/process.py:35-44`. A plugin SDK would need a public alias/façade rather than a rewrite (inferred).
2. There is no HTTP endpoint to render an arbitrary `(file, start, end, render_opts)` without a clip row; the preview endpoint is the closest (`server/api.py:1705`). Is a "render job for a plugin-supplied candidate" needed, or do plugins only produce `ClipCandidate`s for the existing loop?
3. `render_opts` has no schema object; it is a loosely-merged dict validated piecemeal (`EditList.from_dict`, `filters.is_valid`, `_clean_render_gaming`, `clip_edit._clean_caption_style`). The remote protocol hashes it verbatim into `job_id` (`remote_render/protocol.py:27-33`), so a plugin adding keys changes job identity — fine, but any schema should be versioned with `PROTOCOL`.
4. The tracking dict (`{"mode": "track"|"split"|"fit_blur", ...}`) is the natural plugin seam for "framing providers" (sports, gaming, podcast already return it: `core/pipeline.py:1331-1393`); it is undocumented outside `video/tracker.py:1-12`.
5. Remote worker caps (`protocol.capabilities`) only know `framing`/`encoders`; a worker running a plugin would need to advertise plugin ids/versions for `compatible()` (`remote_render/protocol.py:134`).
6. Caption fonts are whitelisted against Windows-stock names (`video/captions.py:36-55`); plugin-supplied fonts would need a path-based mechanism (post_style already resolves font files for PIL at `video/post_style.py:155-216`).
7. The gateway is the only authenticated server in the repo; the local API has no auth (`remote_render/gateway.py:3-4`). Which one a marketplace "integration" talks to is a design choice.
8. `sources/dispatch.identify/download` dispatch by hard-coded branch (`sources/dispatch.py:14-27,146-164`), the explicit extension seam for "source providers".
