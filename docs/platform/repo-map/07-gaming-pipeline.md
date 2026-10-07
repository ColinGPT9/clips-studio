# Gaming pipeline as a reference implementation (repo map, read-only)

Repo: /home/user/clips-studio (branch claude/open-platform-w4eh9g). All paths repo-relative. "(inferred)" marks conclusions not stated verbatim in the code.

## 1. One-paragraph picture

"Gaming / Reaction" is NOT a separate pipeline. It is two hooks the shared pipeline (`core/pipeline.py`) calls plus a scoring *profile* fed into the shared scorer:

| Hook | Where the shared pipeline calls it | What it is |
|---|---|---|
| Scoring inputs (profile + chat signal + game-sound signal) | `core/pipeline.py:435-437` `_gaming_scoring_inputs(...)` → passed to `find_clips(... gaming=, chat=, sounds=)` at `core/pipeline.py:446-447` | `analysis/gaming.py`, `analysis/chat_moments.py`, `analysis/game_audio.py` (+ `analysis/game_text.py`, `analysis/game_vision.py` used inside fusion, see §4) |
| `prepare()` once per video | `core/pipeline.py:598` `_gaming_prepare(video.path, candidates, clip_dir, config)` → `gaming/run.py:prepare` | find the streamer's webcam across several clips |
| `render()` per clip | `core/pipeline.py:1331-1335` `_try_gaming_render(...)` → `gaming/run.py:render` | render ONE clip in a split/game-only layout with its own FFmpeg graph (`gaming/compose.py`) |

Everything else (download, transcription, candidate finding, dedupe, metadata, captions, outro, watermark, DB registration, publishing) is the shared pipeline's. The gaming code only *decorates* it: it changes the scoring prompt/evidence and takes over the final frame composition for a clip (and may decline, returning `None`, in which case the standard renderer takes the clip: `core/pipeline.py:817-829`).

## 2. How the gaming pipeline is selected

| Flag | API field (`server/api.py`) | Where it lands in config | Selector | Meaning |
|---|---|---|---|---|
| `gaming` | `server/api.py:54,87,120,213` (job/batch/patch/watch bodies) → `_process_options` `server/api.py:461-462` | `cfg["clips"]["gaming"] = True` `server/jobs.py:202-205` | `core/modes.py:55-65 is_gaming(config_or_opts)` (checks top-level `gaming` or `clips.gaming`) | Gaming / Split-Screen layout **and** gaming scoring |
| `gaming_scoring` | `server/api.py:55,463-466` | `cfg["clips"]["gaming_scoring"]` `server/jobs.py:198-201` | `core/modes.py:68-79 gaming_scoring()` (true if `is_gaming` or the flag) | score as a gaming stream with the standard/Vertical Live layout |
| `gaming_layout` | `server/api.py:56,467-475` validated by `core/modes.py:115-162 clean_gaming()`; stamped `"by": "user"` | `cfg["clips"]["gaming_layout"]` `server/jobs.py:207-208` | read by `gaming/run.py:prepare` via `config["clips"].get("gaming_layout")` (`gaming/run.py:99`) | split drawn before processing |
| `gaming_remember` | `server/api.py:57,476-477` | `cfg["clips"]["gaming_remember"]` `server/jobs.py:209-210` | `core/pipeline.py:288-292` → `db.set_creator_gaming_layout` | remember layout per creator |
| per-clip `gaming` in `render_opts` | re-render body `server/api.py:165-168` (`gaming: dict|None`, `gaming_off`) → `_clean_render_gaming` `server/api.py:548-555`, `1747-1750` | `opts["gaming"]` of a clip's render options | `core/pipeline.py:1194-1195` `modes.is_gaming(opts) or modes.is_gaming(config)` | editor-driven re-render keeps/changes the split |

Mutual exclusion is enforced at the API: gaming cannot combine with vertical_live/podcast/longform (`server/api.py:532-536`), gaming_scoring not with podcast/longform (`server/api.py:537-539`), sport not with gaming/gaming_scoring (`server/api.py:541-544`); the same guard for automated (watched-channel) jobs in `server/automation.py:188-190`. In the renderer: `core/pipeline.py:1194` (`not landscape and not vertical_live and not podcast`).

UI: `ui/src/renderer/src/components/queue/AddVideos.tsx:66` lists `LAYOUT_MODES = ['podcast','vertical_live','gaming','longform']`; persisted in localStorage keys `generate-gaming` / `generate-gaming-scoring` (`AddVideos.tsx:145-146`); the "Vertical Live content: Gaming / reaction" dropdown sets `gaming_scoring` (`AddVideos.tsx:475`, `QueueItemSettings.tsx:265-282`). Job options are typed in `ui/src/renderer/src/lib/types.ts:370-378` (`gaming`, `gaming_scoring`, `gaming_layout: GamingSettings`, `gaming_remember`). The layout is drawn in `ui/src/renderer/src/components/GamingLayoutEditor.tsx` with presets from `ui/src/renderer/src/lib/gamingLayout.ts` (a TS mirror of `gaming/layouts.json` (inferred from the import at `AddVideos.tsx:11`)). API client calls: `ui/src/renderer/src/lib/api.ts:338-355` (re-render with `gaming` / `gaming_off`), `:388-395` (creator gaming layout GET/PUT).

## 3. Input the gaming code receives

### 3a. Scoring side (`analysis/`)
Built by `core/pipeline.py:832-870 _gaming_scoring_inputs(config, video, db, games, hype_out, heard)` → returns `(GamingProfile, ChatSignal|None, GameSounds|None)`.

| Input | Source | Shape |
|---|---|---|
| game metadata | `video.games` from the downloader (`sources/ytdlp_common.py:101-123 games_from_info(info, platform)` → `[{"name","start","end"}, +"hint"]`: Twitch chapters, Kick category, YouTube tags), persisted in `videos.games` (`core/state.py:875-890 set_video_games/video_games`), re-fetched for cached files by `sources/dispatch.py:77 game_info(url)` in the hype thread (`core/pipeline.py:355-359`) | list of dicts |
| video title | `video.title` → `analysis/gaming.py:170 profile_for(config, games, title)` (genre fallback from title/hints) | str |
| chat replay messages | `analysis/hype.py:64 audience_signals(url, video_id, duration)` → `(curve, messages)`; messages kept only when `gaming_scoring or sport_name` (`core/pipeline.py:351-352`) | `[(second, user, text)]` (inferred from `_twitch_chat` return type `analysis/hype.py:117`) |
| game sound | `analysis/game_audio.py:70 listen(path, groups)` run in a daemon thread beside Whisper (`core/pipeline.py:364-383`), only if `analysis/panns.py:53 available()` | `{group: np.ndarray per second}` |
| settings | `config["scoring"]` (`profiles.gaming.weights` override, `analysis/gaming.py:183-186`), `config["clips"]` (min/max duration), `scoring.read_screen`, `scoring.look_at_game`, `game_bonus`, `game_moment_max`, `menu_penalty` (`analysis/fusion.py:141,586,380-386`) | dict |
| transcript | `segments` from the shared `transcribe()` (`core/pipeline.py:398-407`) — the gaming code never transcribes | `list[Segment]` |
| video path | `video.path` (the shared download, `data_dir/downloads/<id>.mp4`, see `server/api.py:1890`) | Path |

### 3b. Layout side (`gaming/run.py`)
- `prepare(source: Path, candidates: list, config: dict, work_dir: Path) -> dict` (`gaming/run.py:96`): receives the full-length source video, the final `ClipCandidate` list (after scoring), the merged config (`config["clips"]["gaming_layout"]`, `config["tracking"]["detector"|"sample_fps"]`), and the clip directory for scratch files.
- `render(intermediate: Path, output: Path, g: dict, config: dict, ass_path: Path|None=None, vf_extra: str="", normalize: bool=True) -> dict|None` (`gaming/run.py:196`): receives the already-cut (and possibly edited) clip file, not the source (`core/pipeline.py:1331`), the per-clip gaming settings dict `g` (keys documented in `gaming/run.py:1-40`: `cam, by, preset, order, divider, safe, game_align, game_box, game_fit, ui_box, cam2, panels, places, cam_position`), the burned-caption `.ass` path, an extra FFmpeg video-filter string (colour filters), and the audio-normalize flag.

## 4. Stages in order (with signatures)

### Scoring stages (inside the shared `find_clips`, `analysis/fusion.py:54`)
1. **Profile**: `analysis/gaming.py:170 profile_for(config, games, title) -> GamingProfile` (`genre`, `game`, `games`, `split_layout`, `weights`; methods `game_at(start,end)`, `genre_track(seconds)`, `guidance(kind, start, end)`, `spec`). Genre lookup: `genre_of(name)` (`analysis/gaming.py:44`), `genre_spec(genre)` (`:159`), `games_summary(games)` (`:164`). Knowledge: `knowledge()` loads `config/gaming.yaml` once (`:36-39`).
2. **Chat signal** (pure Python): `analysis/chat_moments.py:101 chat_signal(messages, duration, classes, lag, ignore) -> ChatSignal` (`curve` per second 0..1, `events` `(sec, "CHAT: ...")`).
3. **Game sound** (model): `analysis/game_audio.py:70 listen(path, groups, tag=None, chunks=None) -> dict[str, np.ndarray]|None` (prepass thread) then `analysis/game_audio.py:131 sound_signal(heard, groups, genres, genre_sounds, max_events=60) -> GameSounds|None` (`game`, `people` curves, `events`).
4. **Voice jump + fusion of the game channel**: `analysis/fusion.py:127-138` `_voice_jump(audio_raw, segments)` (`analysis/fusion.py:754`) + `_soft_or` (`:738`) of (voice 0.6, chat 0.9, game sound 0.7, people sound 0.5) → `game_curve` (`analysis/fusion.py:139`).
5. **On-screen text (OCR)**: `analysis/fusion.py:881 _read_screen(video_path, game_curve, segments, clips_cfg, gaming)` → `analysis/game_text.py:141 read_screen(path, windows, genre_at, lexicon, grab=None, read=None, budget=120s, max_frames=160) -> ScreenText` (`events`, `menus`, `frames`). Only in game-moment windows, strongest first; gated by `scoring.read_screen` and `profile.reads_screen` (`analysis/fusion.py:141`).
6. **Prompt guidance + events**: `GamingProfile.guidance("clips"|"windows"|"rerank")` (`analysis/gaming.py:112-156`) is injected as `{mode_guidance}` into `config/prompts/score_clips.txt` / `score_windows.txt` by `analysis/highlights.py:33 find_highlights(segments, llm, *, ..., events, guidance, events_title)` (`:78-80`) and `analysis/highlights.py:105 score_windows(...)` (batch 8, `analysis/fusion.py:219-225`). Events title: `"GAME / CHAT / AUDIO EVENTS (from signal analysis):"` (`analysis/fusion.py:154`).
7. **Event windows as candidates**: `analysis/fusion.py:195-203` `_event_windows(game_curve, ...)` — each in-game moment becomes its own candidate window.
8. **Fusion**: `analysis/fusion.py:636 _fuse(c, weights, reaction, speech_ratio, game=True)` — quiet stretches shift text weight to the game channel; game bonus/witness agreement `analysis/fusion.py:376-430` writes `c.subscores["game"|"game_why"|"game_bonus"]` (UI types `ui/src/renderer/src/lib/types.ts:68-72`); reaction channel left neutral (`tests/test_gaming_scoring.py` docstring).
9. **Vision LLM look**: `analysis/fusion.py:849 _look_at_game(finalists, video_path, llm, gaming, segments, events)` → `analysis/game_vision.py:108 look_at(candidates, video_path, llm, gaming, segments, events, grab=None, budget=240s, max_candidates=12, talk=None) -> int`; prompt `config/prompts/look_at_game.txt` (placeholders `{game} {highlights} {count} {times} {transcript} {events}`, JSON `{frames, gameplay, moment, strength}`); verdict ±10 max (`MAX_ADJUST`, `analysis/game_vision.py:32`) into `c.subscores["seen"]`. Gated by `llm.sees_images()` and `game_vision.can_see(llm)` (red-square probe, `analysis/game_vision.py:50-69`); cloud models skipped (privacy).
10. **Rerank**: `analysis/fusion.py:1000-1020 _rerank(batch, segments, llm, gaming)` uses `gaming.guidance("rerank")`.

### Layout stages (per video then per clip)
11. **`prepare()`** (`gaming/run.py:96-146`): a) `saved_layout()` cleans a user/creator layout (`:76`); b) `panels.panels_in_video(source)` finds solid chat/splits panels (`gaming/panels.py:205`, OpenCV only); c) if no `cam` given: cuts up to 4 (max 8) 40-s probe clips spread through the video with the shared `video.cutter.cut_clip` (`gaming/run.py:118-131`), runs `detect.find_cam(probe, detector, sample_fps) -> ClipFinding` (`gaming/detect.py:375`), `detect.stills(probe)` (`:451`), `detect.video_cam(findings) -> box|None` (`:392`), `detect.snap_to_frame(cam, frames)` (`:477`). Returns `{**saved, "cam": [x,y,w,h]|None, "by": "video"|"user"|"creator", "panels": [...]}`.
12. **`render()`** (`gaming/run.py:196-243`): decides this clip's webcam (trusted person choice vs. TalkNet evidence: `_trusted_look` `:149`, `detect.sample_tracks` `:147`, `detect.candidates` `:229`, `detect.present_at` `:699`, `detect.speaking_scores` `:241`, `detect.judge` `:360`, `detect.clip_cam` `:629`), finds heads `detect.head_in_box(clip, box, detector, n=8)` (`:538`), builds a geometry `Plan` with `gaming/layout.py:368 plan(src_w, src_h, settings, heads) -> Plan` (dataclasses `Element`/`Plan` `gaming/layout.py:54-77`; presets from `gaming/layouts.json`), renders with `gaming/compose.py:85 render(clip_path, output_path, p, ass_path, vf_extra, normalize) -> Path` (one FFmpeg `-filter_complex` encode, same encoder/audio args as the standard renderer: `video.encoding.video_encoder_args/audio_filter_args`), and computes `framing.face_clear(head, src, dest, shift, safe_zone)` (`gaming/framing.py:103`). Returns `None` (standard renderer takes over) or the settings to persist: `{**g, "layout": "split"|"fill", "used_preset", "used_cam", "face"?}`.

## 5. Output: what it hands back

- **Scoring**: nothing of its own. It returns evidence objects (`GamingProfile`, `ChatSignal`, `GameSounds`) that the shared `find_clips` consumes; the shared scorer still produces the `ClipCandidate` list (`core/models.py:44-63`: `start, end, score, hook, reason, source, engagement, trending, subscores`). The gaming-specific marks are extra `subscores` keys `game`, `game_why`, `game_bonus`, `seen` (`analysis/fusion.py:397-430`, `analysis/game_vision.py` via `_look`).
- **Layout**: a **finished rendered clip file** at `render_path` (not a candidate): `compose.render` writes the 1080x1920 MP4 with captions burned (`gaming/compose.py:85-106`), and the shared pipeline then does outro/watermark/metadata/registration (`core/pipeline.py:1445-1480`). The dict `render()` returns is persisted under `render_opts["gaming"]` (`core/pipeline.py:1472-1474`) so the editor and re-renders keep the split (`core/pipeline.py:1618-1621`).
- Declining: `render()` returns `None` → the shared renderer frames the clip and `gaming` is stripped from the saved opts (`core/pipeline.py:1451-1452`).

## 6. Progress and error reporting

- No `progress.emit` calls in `gaming/` or `analysis/game_*.py` (grep: only `print()` lines and one `cancel.check_active()` in `analysis/game_vision.py:146`). Progress stages are the shared pipeline's: `download`, `transcribe`, `analyze`, `render` (`core/pipeline.py:205,390,424,612`) via `core/progress.py:30 emit(**event)`. Inside analysis, `analysis/highlights.py:71-72` emits `analyze current/total`.
- Status text goes to stdout (`print("      Gaming: ...")`, e.g. `gaming/run.py:109,134-138`), which the job log captures (inferred from the "job log says why" sentence in `docs/GAMING.md:373`).
- Errors: every entry point is wrapped. `core/pipeline.py:803-814 _gaming_prepare` → on exception returns `{"gaming": {}}`; `core/pipeline.py:817-829 _try_gaming_render` → `None` (standard layout); `core/pipeline.py:832-846` → generic profile `GamingProfile(weights=STANDARD_WEIGHTS)`; chat/sounds/OCR/vision each `try/except` to `None` (`core/pipeline.py:848-870`, `analysis/fusion.py:896-900`). Hard failures inside compose raise `RuntimeError("gaming render failed: <ffmpeg stderr>")` (`gaming/compose.py:104-105`) which the wrapper swallows. Cancellation: `core/cancel.py:46 check(video_id)`, `:52 check_active()`, `:80 wait(thread, timeout, video_id)` used by the pipeline around the prepass threads (`core/pipeline.py:428-432`).

## 7. Model dependencies and where they load

| Model | Used for | Loaded in | Gate / install |
|---|---|---|---|
| YOLOv8n-pose (ultralytics) | person/head tracks for webcam detection, `people_on`, `head_in_box` | shared `video/tracker.py:82-108 _get_model()` (single process-wide instance, lock), imported read-only by `gaming/detect.py:46-58` | `config/settings.yaml:125-129 tracking.detector/sample_fps`; weights via `core.binaries.yolo_weights` (`video/tracker.py:100`); `requirements.txt:31` |
| TalkNet (active speaker) | who the streamer is (`speaking_scores`, `judge`) | shared `video/tracker.py` helpers (`_mouth_patch`, `_clip_pcm`, ...) reused by `gaming/detect.py:46-58`; TalkNet itself in `video/asd.py` (imported at `video/tracker.py:501,547`) | part of the standard tracker |
| faster-whisper | transcript | shared `transcription/transcriber.py:188 transcribe(...)` called by `core/pipeline.py:398` | `config.whisper.model/device`; `requirements.txt:25` |
| PANNs MobileNetV1 (torch) | game sounds | `analysis/panns.py:114 load()` (cached), `:142 tag(windows)`; weights `models/panns_mobilenetv1.pth` (`:33,46-50`), labels `config/audioset_labels.txt` | `analysis/panns.py:53 available()` (torch + weights file); fetched by `scripts/fetch_panns.py` (`analysis/panns.py:24-25`) |
| RapidOCR (onnxruntime) | on-screen banners/menus | `analysis/game_text.py:103-110 _ocr()` lazy singleton | `analysis/game_text.py:57 available()`; `requirements.txt:38,42` |
| Vision-capable local LLM (Gemma 3/4 via Ollama) | look at frames | shared backend `llm/base.py:30 LLMBackend` with `.look(prompt, images)` (`:56`) and `.sees_images()` (`analysis/fusion.py:860`); backend chosen by `create_backend(config["llm"])` (`core/pipeline.py:431`) | `scoring.look_at_game` (`analysis/fusion.py:586`); never cloud |
| OpenCV | panels, stills, snapping, frame grabs | `gaming/panels.py:26`, `gaming/detect.py`, `analysis/game_text.py`, `analysis/game_vision.py` | `requirements.txt:33` |
| FFmpeg | compose render, probes, PCM decode | `core.binaries.ffmpeg()` (`gaming/compose.py:23`, `analysis/game_audio.py:50 _pcm_chunks`) | bundled binary |

## 8. Files read / written

| Reads | Writes |
|---|---|
| `config/gaming.yaml` (`analysis/gaming.py:23,36`) | probe clips `work_dir/gaming_probe_<start>.mp4`, deleted after (`gaming/run.py:118-131`, `core.paths.discard`) |
| `gaming/layouts.json` (`gaming/framing.py:27`) | rendered clip `output` MP4 (`gaming/compose.py:85`) |
| `config/prompts/look_at_game.txt` (`analysis/game_vision.py:23`) | DB: `videos.games` (`core/state.py:875`), `creators.gaming_layout` (`core/state.py:782`, column added at `:496-499`), clip `render_opts` JSON with `"gaming"` (`core/pipeline.py:1472`) |
| `models/panns_mobilenetv1.pth`, `config/audioset_labels.txt` (`analysis/panns.py:33-34`) | editor preview frames `data_dir/previews/frame_<clip>_<t>.jpg` (`server/api.py:1909`) |
| source video `data_dir/downloads/<video_id>.mp4` (`server/api.py:1890`); the cut `intermediate` clip (`core/pipeline.py:1331`) | nothing under `gaming/` writes config |

## 9. Configuration

- `config/gaming.yaml` (293 lines) is **per-genre, not per-game**. Top-level keys: `chat_lag_seconds`, `chat_classes` (hype/funny/surprise/scare/fail/clip: `words`, `suffixes`, `patterns`, `message_patterns`), `chat_ignore`, `genres` (14 genres: `label`, `highlights` prose, `callouts` list), `sound_groups` (AudioSet class groups with `side: game|people`), `genre_sounds` (genre × group weight 0..1), `screen_text` (`events` per genre word lists, `menu` word list), `games` (genre → list of game-name phrases; ~150 names). A game is mapped to a genre by longest phrase match (`analysis/gaming.py:44-54`). There are **no per-game entries** beyond the name→genre list; everything else (highlights text, callouts, sounds, banners) is per genre. The Twitch/Kick non-game categories are a constant `NOT_GAMES` in `analysis/gaming.py:58-60`.
- `config/settings.yaml`: `scoring.weights` (`:75-83`), optional `scoring.profiles.gaming.weights` (read at `analysis/gaming.py:183-185`; not present in the shipped file), `tracking.detector`, `tracking.sample_fps` (`:125-129`). Fusion knobs read from `config["scoring"]`: `read_screen`, `look_at_game`, `game_bonus` (5), `game_moment_max` (7), `menu_penalty` (6) (`analysis/fusion.py:141,380-386,586`).
- `gaming/layouts.json`: `canvas [1080,1920]`, `safe_zones {tiktok, reels, shorts, all, none}`, `headroom 0.06`, `presets` (12: split, basecam, half, fullscreen, blurred, small_cam, circle_cam, game_ui, mosaic, dual_cam, duo_split) each `{type: stack|full|pip, rows|cams, divider [min,max,default], order, game_fit, pip_width, pip_aspect, shape, overlay, ui_share}`.
- Constants hard-coded in modules: probe counts (`gaming/run.py:50-55`), detection thresholds (`gaming/detect.py:60-103`), OCR/vision budgets (`analysis/game_text.py:31-49`, `analysis/game_vision.py:25-32`), sound thresholds (`analysis/game_audio.py:29-37`), chat burst thresholds (`analysis/chat_moments.py:21-30`).

## 10. Resource needs (as documented)

- PANNs: ~600x realtime on GPU, 120x on CPU, 24 MB weights, runs beside Whisper in a thread, decode in 60-s chunks (`docs/GAMING.md:333-342`, `analysis/game_audio.py:17-19,29`); 900-s join cap (`core/pipeline.py:432`).
- OCR: 0.4-0.8 s/frame CPU, ≤160 frames, ≤120 s per video (`docs/GAMING.md:343-350`, `analysis/game_text.py:34-35`).
- Vision LLM: 4 frames at 896 px per clip, ≤12 clips, ≤240 s per video, ~10 s/clip on GPU (`analysis/game_vision.py:25-31`, `docs/GAMING.md:359-373`).
- Webcam detection: YOLO + TalkNet on up to 8 × 40-s probes per video and again per clip at `sample_fps` 8 (2 fps quick look for trusted layouts) (`gaming/run.py:50-56`); shares the single YOLO instance and its lock (`video/tracker.py:82-86`), so parallel render workers serialise on it (inferred).
- Compose: a single FFmpeg encode per clip, no frames through Python (`gaming/compose.py:1`).
- Chat fetch: ≤800 pages, 180-s budget (`analysis/hype.py:42-43`).

## 11. Game-specific knowledge today

Represented per **genre** in `config/gaming.yaml` (prose highlights, callouts, sound weights, banner words) plus a flat **game-name → genre** phrase list. Per-game runtime data is only the platform's `games` list (`name,start,end,hint`) stored per video. Per-creator data: the remembered layout (`creators.gaming_layout`). There is no per-game module, no per-game prompt, no per-game detector; the Sports package in contrast is per-sport (`sports/core/profile.py:29 SportProfile` with `reads_screen`, `screen_lexicon`, `sound_weights`, `guidance`, `scoring_types`, `extra_moments`, `clip_span`, ...) and `analysis/fusion.py:77-82` makes a sport profile stand in for the gaming profile (duck-typed: `game_at`, `genre_track`, `guidance`, `spec`, `weights`, `games`, `game`, optional `reads_screen`, `screen_lexicon`). That duck-typed profile surface is effectively the existing "scoring profile contract" (inferred).

## 12. Shared pipeline vs. gaming-implemented

| Done by the shared pipeline (`core/pipeline.py`) | Done by the gaming code |
|---|---|
| download + game metadata capture (`:205-250`, `sources/`) | genre mapping, prompt guidance (`analysis/gaming.py`) |
| creator identity, remembered layout load/save (`:252-296`) | chat classification (`analysis/chat_moments.py`) |
| hype/chat fetch thread (`:343-362`, `analysis/hype.py`) | game sound listen + signal (`analysis/game_audio.py`, `analysis/panns.py`) |
| transcription (`:390-407`) | OCR of banners/menus (`analysis/game_text.py`) |
| audio/visual signals thread, LLM backend creation (`:431`) | vision-LLM look at frames (`analysis/game_vision.py`) |
| candidate finding, dedupe, rejection logging (`:437-460`, `analysis/highlights.py`, `analysis/fusion.py`) | webcam detection (`gaming/detect.py`), panels (`gaming/panels.py`) |
| metadata, title cards, cutting, edits, captions `.ass`, colour filters (`:1123-1330`) | layout geometry (`gaming/layout.py`, `layouts.json`, `gaming/framing.py`) |
| outro, watermark, render-opts persistence, DB registration, exports/publishing (`:1445-1480`, `_register_clip`) | the FFmpeg composition of the frame (`gaming/compose.py`) |
| progress/cancel, remote rendering dispatch (`:612-658`) | editor helpers: suggest/snap/people/panels endpoints call into `gaming/` (`server/api.py:1795-1933`) |

## 13. SDK surface vs. plugin-implemented (derived from this pipeline)

| Core platform functionality the SDK should expose | Pipeline-specific functionality a developer implements |
|---|---|
| job option/flag registration + mutual-exclusion rules (`server/api.py:461-477,532-544`, `server/jobs.py:198-210`, `core/modes.py:55-79`) | the option's name, its settings schema/validator (cf. `core/modes.py:115 clean_gaming`) |
| source video path, duration, title, platform metadata incl. `games` list (`sources/ytdlp_common.py:101`, `core/state.py:875`) | mapping metadata to its own profile (`analysis/gaming.py:170 profile_for`) |
| transcript `segments` (`transcription/transcriber.py:188`) | per-genre/per-game knowledge files (`config/gaming.yaml`) |
| chat replay messages + audience curve (`analysis/hype.py:64`) | its own per-second signal curves + `(sec, "KIND: text")` events (`chat_moments.chat_signal`, `game_audio.sound_signal`, `game_text.read_screen`) |
| prompt-guidance + events injection into scoring (`analysis/highlights.py:33,105` `guidance`, `events`, `events_title`), the scoring-profile duck type (`weights`, `guidance()`, `game_at()`, `genre_track()`, `spec`) | the guidance text and callouts (`GamingProfile.guidance`) |
| fusion hooks: extra subscore keys and capped bonuses (`analysis/fusion.py:376-430`, `_fuse game=`), signal-peak windows as candidates (`:195-203`) | which curves count, thresholds, witness logic |
| shared models: YOLO/TalkNet tracker (`video/tracker.py:82`), PANNs tagger (`analysis/panns.py:114,142`), OCR (`analysis/game_text.py:103`), LLM `generate/generate_json/look/sees_images` (`llm/base.py:30-71`) | model-specific post-processing (`gaming/detect.py:360 judge`, banner lexicons) |
| frame grabbing (`video.capture.video_capture`, `server/api.py:1874-1920`), clip cutting (`video.cutter.cut_clip`), `probe_size` (`core/modes.py:189`) | prepass per video (`gaming/run.py:prepare`) |
| render hook per clip with `intermediate, output, opts, config, ass_path, vf_extra, normalize` and a `None`-means-decline contract (`core/pipeline.py:817-829`) | the layout plan + FFmpeg filter graph (`gaming/layout.py:368`, `gaming/compose.py:65,85`) |
| encoder/audio args, captions `.ass`, outro, watermark, canvas size (`video.encoding`, `core/pipeline.py:1445`) | safe zones/presets (`gaming/layouts.json`) |
| per-clip persisted opts (`render_opts["gaming"]`, `core/pipeline.py:1472`) and per-creator memory (`core/state.py:770-788`) | what to persist |
| progress/cancel (`core/progress.py:30`, `core/cancel.py:46-80`), job log (stdout) | its own status lines |
| editor endpoints pattern: frames/people/suggest/snap/panels (`server/api.py:1795-1933`) and UI layout editor (`GamingLayoutEditor.tsx`) | plugin UI panels (today hard-coded TSX) |
| tests harness with model stand-ins (`grab`, `read`, `tag` params: `analysis/game_text.py:141`, `game_vision.py:108`, `game_audio.py:70`) | its own tests |

## 14. Open questions

1. There is no formal interface for the scoring profile: `analysis/fusion.py:77-82` duck-types `SportProfile` as `gaming`; an SDK would need to name that protocol (`weights`, `games`, `game`, `spec`, `game_at`, `genre_track`, `guidance`, optional `reads_screen`, `screen_lexicon`) — (inferred).
2. The render hook is hard-wired by `if gaming:` in `core/pipeline.py:1331` and the mutual-exclusion table lives in `server/api.py:532-544`/`server/automation.py:188-190`; a registry would have to replace both without changing behaviour.
3. Progress for prepass work (sounds, OCR, vision) is print-only; whether the platform should add a `progress.emit` stage for plugin prepasses is a design choice.
4. `gaming/` imports private tracker helpers (`video/tracker.py` `_get_model`, `_mouth_patch`, ...) at `gaming/detect.py:46-58`; a public SDK surface for the tracker is missing.
5. UI: job options, chips (`ui/src/renderer/src/lib/queue.ts:66`) and the layout editor are static TSX; no dynamic option rendering exists for a plugin to declare settings.
6. Knowledge is per genre; a per-game plugin would need a precedence rule over `config/gaming.yaml`'s genre tables (inferred).
