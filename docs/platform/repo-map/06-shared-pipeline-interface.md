# Shared pipeline interface: Standard vs Gaming vs Sports (vs Longform / Podcast / Vertical Live)

Repo: /home/user/clips-studio, branch claude/open-platform-w4eh9g. All paths repo-relative. Read-only survey.
Everything not marked "(inferred)" is read directly from the cited line.

## 0. Orientation: there is no mode enum, there are flag predicates

- One Shorts entry point: `process_video(url: str, config: dict, db: StateDB, force: bool = False) -> list[RenderedClip]` — core/pipeline.py:192.
  Callers: server/jobs.py:270 (queue worker), server/jobs.py:667 (`_both_formats`), main.py:204 (CLI `process`), core/scheduler.py:97 (channel daemon).
- Longform has a second orchestrator that re-imports the Shorts stage functions: `process_longform(url, config, db, options) -> None` — longform/process.py:28-45 (imports `_cached_or_download, _register_clip, _render_files, _safe_name, _with_usable_model, convert_slow_source` from core.pipeline). Called at server/jobs.py:268.
- "Modes" are booleans/dicts in `config["clips"]`, read through predicates in core/modes.py:
  `is_vertical_live` :44, `is_gaming` :55, `gaming_scoring` :68, `sport` :82 (returns the sport name), `needs_framing` :159, `measures_reaction` :165.
- The worker copies the job payload into those keys: server/jobs.py:191-216 (`cfg["clips"]["podcast"|"vertical_live"|"gaming_scoring"|"gaming"|"gaming_layout"|"gaming_remember"|"sport"|"focus"]`). The payload is built from the API body by `_process_options` server/api.py:440-545 (fields on `JobIn` server/api.py:39-62). Mutual exclusion is enforced at server/api.py:527-543 (HTTP 400) and silently at server/automation.py:183-191 (watch presets).
- The scoring engine is one function for every mode: `find_clips(video_path, segments, llm, config, signals=None, creator_context=None, weight_bias=None, audience=None, measure_reaction=True, gaming=None, chat=None, sounds=None, intent=None, sport=None) -> tuple[list[ClipCandidate], list[Rejection]]` — analysis/fusion.py:54-70. A sport profile is aliased onto the `gaming` argument at analysis/fusion.py:77-82, which is the single most important fact for the plugin contract: **Sports already runs through the Gaming interface.**
- The renderer is one function for every mode: `_render_files(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en") -> tuple[Path, str]` — core/pipeline.py:1123-1138 ("Pure file work ... NO database access and NO LLM call, so it is safe to run in a worker thread").
- The DB funnel is one function for every mode: `_register_clip(db, video_id, candidate, final_path, meta, render_opts_json, config=None) -> RenderedClip | None` — core/pipeline.py:1563-1580 (docstring: "this is the one place every finished video passes through ... a mode added later cannot miss it either").

## 1. Comparison table

Columns: Standard | Gaming (Gaming/Reaction layout + gaming scoring) | Sports | Longform | Podcast | Vertical Live (VL).

### 1.1 How it is selected

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| Default: no flag set, so `gaming_profile, sport_profile` stay None (core/pipeline.py:432-438) and `find_clips` gets no `gaming=`/`sport=` kwarg (:446-449); render takes the `else` tracked branch (:1361-1396). | `clips.gaming` → `modes.is_gaming` (core/modes.py:55) picks layout prepare/render (core/pipeline.py:598, :1194, :1331); `clips.gaming_scoring` or `clips.gaming` → `modes.gaming_scoring` (core/modes.py:68) picks scoring inputs (core/pipeline.py:338, :369, :435). Set from payload at server/jobs.py:198-212; payload from API server/api.py:461-478 (`gaming_layout` cleaned by `modes.clean_gaming` core/modes.py:115). | `clips.sport` dict → `modes.sport` (core/modes.py:82) → `sport_name` (core/pipeline.py:340). Branches at :223 (vertical fold), :387 (`MatchReading`), :397-401 (hotwords), :433 (finish), :470 (outcome), :513-528 (titles), :668 (reels), :1365 (framing). Set at server/jobs.py:215-218, validated by `sports.clean` server/api.py:480-486 / sports/__init__.py:72-134. | `payload["longform"]` → server/jobs.py:266-268 → `process_longform`; `options["mode"]` looked up in `PROFILES` longform/profiles.py:8-33 (short_clips, clips_140, highlights, edited_stream). | `clips.podcast` (server/jobs.py:191-193); only consulted at render (core/pipeline.py:1180, :1336). | `clips.vertical_live` (server/jobs.py:194-197); download (core/pipeline.py:206 `vertical=`), source check :217-221, CPU sharing skipped :585, render :1187, :1209, :1314. Sports folds into VL for a 9:16 source :223-229. |

### 1.2 Input received (types + where produced)

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `url, config, db` (core/pipeline.py:192). `DownloadedVideo` (core/models.py:12-27) from `_cached_or_download` (:206, def :1010). `list[Segment]` (core/models.py:30-42) from `transcribe(...)` (:402-411; transcription/transcriber.py:188). `signals=(audio_raw, visual_raw)` from background thread (:315-331) → `find_clips` (:439-445) plus `creator_context`, `weight_bias`, `audience` curve (:442-444; analysis/hype.py:64). | Same, plus: `video.games` (core/models.py:21; Twitch/Kick chapters) or `db.video_games` (:342); chat `messages` from hype (:352-353); `sounds_out["heard"]` from PANNs thread (:369-383). `_gaming_scoring_inputs(config, video, db, games, hype_out, heard)` (:832-879) → `(GamingProfile, ChatSignal|None, GameSounds|None)` (analysis/gaming.py:71, analysis/chat_moments.py:34, analysis/game_audio.py:41) → `find_clips(gaming=, chat=, sounds=)` (:446-447) and `measure_reaction=False` (:445; core/modes.py:165). | Same base, plus: `clips.sport` option; `video.description` (core/models.py:26; refreshed by `_listening_for` :995-1007 via `sources.dispatch.description`); Whisper `hotwords` hint (:397-411). `MatchReading(config, video)` (:881-930) starts PANNs + sport `prepass` threads at construction (:887-894); `finish(hype_out)` (:916-930) → `_sport_inputs` (:933-992) → `(SportProfile, chat, sounds)` with `profile.curves` (:954-971) and prepass attrs e.g. `profile.board` set by `setattr` (:942-943). → `find_clips(sport=, chat=, sounds=)` (:448-449). | `options: dict` (longform/process.py:28, :49). Same download/transcribe (:58-60, :96-101); `find_clips` with per-profile `min/max_duration` copied into a deep-copied cfg (:107-110, :116-121); sport allowed via `MatchReading` (:86-88, :115). | No analysis difference; flag read only in `_render_files` (core/pipeline.py:1180). | No analysis difference except download of the vertical copy (core/pipeline.py:1019-1029; sources/vertical.py `SUFFIX`) and `NotVerticalError` before work (:217-221; core/modes.py:34-43). |

### 1.3 Output returned (types + consumer)

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `list[RenderedClip]` (core/models.py:88-93) returned at core/pipeline.py:682; `[]` early at :303 (already made) and :482 (no candidates). Each clip is a `clips` row via `_register_clip` → `db.add_clip` (core/state.py:920-948). Consumers: main.py:204-208 prints; server/jobs.py:270 ignores the return value and sets job status (:287); UI reads DB + progress events. Intermediate: `(candidates, rejections)` from `find_clips` (analysis/fusion.py:54-70; `ClipCandidate` core/models.py:45-73, `Rejection` :77-83). `_render_files` → `(final_path, render_opts_json)` (:1482). | Same, plus `render_opts_json["gaming"] = gaming_kept` (core/pipeline.py:1474), where `gaming_run.render` returns the layout settings dict (`layout`, `used_preset`, `used_cam`, `face`) or `None` (gaming/run.py:183-186, :240-242). `None` → key stripped (:1451-1452). | Same, plus `outcome["sport"] = sport_profile.report_data` (core/pipeline.py:470-473; produced sports/core/clips.py:198-229) stored by `db.set_outcome` (:473; core/state.py:993); reels appended to `rendered` (:668-669; `_sport_reels` :685-754) as extra `RenderedClip`s with synthetic `ClipCandidate(start, end, score, hook=reel.title, subscores=scores)` (:744-745); `render_opts_json["sport"] = sport_name` (:1480); clip `subscores["sport_*"]` (sports/core/clips.py:78-105). | Returns `None` (longform/process.py:28). short_clips/clips_140: clips with `render_opts {"profile": mode}` (:146, :171-172). highlights/edited_stream: ONE assembled file registered with a synthetic candidate (:277-296, :353-360). | `render_opts_json["podcast"] = True` (core/pipeline.py:1466). | `render_opts_json["vertical_live"] = True` (:1471). |

### 1.4 Job lifecycle hooks (DB status transitions, cancel checkpoints)

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `db.upsert_video` :234; `cancel.clear` :231; status `downloaded` :304 + `cancel.check` :305; `transcribed` :420 + `cancel.check` :422; `cancel.wait` on prepass threads :427-430; `analyzed` :463; `db.set_outcome` :473; `done` early :481; `cancel.check_active` per finished render :650 and on `_render_files` entry :1147; `set_process_seconds` :672; `done` :673. Job-level (server/jobs.py): `finish_job(done)` :287, `cancelled` :290-293 on `CancelledError`, `failed` :294-306 (traceback + `scrub_secrets`). Cancel API: core/cancel.py:36-99 (`request_cancel, check, check_active, wait, CancelledError`). | Extra thread `game-sounds-prepass` (core/pipeline.py:369-383) joined by `cancel.wait(sounds_thread, 900)` :430. Creator memory: `db.set_creator_gaming_layout` / `creator_gaming_layout` (:288-296; core/state.py:782, :770). `db.set_video_games` (:250-251, :841). | `MatchReading` threads at :887-894; joined in `finish()` :923-929 with `(900, sports.prepass_wait(...))`. Post-metadata hook `check_titles` :522-528. Post-render hook `_sport_reels` :668-669. | Same statuses: longform/process.py:71, :101, :131, :135, :208; `_record_match` for a sport :122-123, :217. | None extra. | `NotVerticalError` raised before any status (:219-220); API retry offers standard with `drop=("vertical_live",)` (server/api.py:1057). |

### 1.5 Progress reporting

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `progress.emit(**event)` (core/progress.py:28-37; handler installed by server/jobs.py:123 `on_progress`, which stamps `job_id`/`span` and `broadcaster.publish`). Stages: `download` :205, `downloaded` :208, `converting source to H.264` :188, `transcribe` :390, `analyze` :424, `render` (clip, total) :612-614, `done` (clips, seconds) :674-676. Inside fusion: `signals` analysis/fusion.py:96, `reactions` :359, `ranking` :620. CLI: no handler, prints only. | No stage of its own; gaming/ and sports/ never call `progress` (grep `progress\.` over gaming/ sports/: no hits). Prepass progress is `print` only (core/pipeline.py:372-384; gaming/run.py:114-117, :150-154). | Same as Gaming: prints only (core/pipeline.py:898-914, :927-929, :974-990). | Same stage names (longform/process.py:57, :60, :91, :105, :165, :209); highlights/edited add `signals` :322 and map `assemble(on_progress=...)` to `render` :270-272, :347-349. | None extra. | None extra. |

### 1.6 Error handling

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `find_clips` exceptions propagate to the job (server/jobs.py:294-306). Per-clip render failures caught in `_finish` (core/pipeline.py:616-626) with `_render_failure_reason` (:34-63) and repeat folding (:619-626, :659-660). Background signals/hype failures swallowed (:322-326, :357-358). | Every call into gaming/ is guarded by the host: `_gaming_prepare` → `{"gaming": {}}` on error (:803-814); `_try_gaming_render` → `None` = standard renderer (:817-829, used :1331-1335); `_gaming_scoring_inputs` → generic `GamingProfile(weights=STANDARD_WEIGHTS)` (:838-846), chat/sounds `None` (:847-863); sounds thread prints (:379-380). gaming/run.py:66-67 states the convention: "Both are called inside the pipeline's guards: any exception means the standard renderer takes the clip". | `MatchReading._listen/_prepass` catch-all (:896-914); `finish` tolerates live threads (:926-929); `_sport_inputs` chat/sounds guarded (:949-950, :971-972); `_sport_framing` → `None` = face tracker (:1550-1560); `_sport_reels` whole-block (:697, :752-753) + per-reel (:724-731) guards; fusion `look` hook guarded, `CancelledError` re-raised (analysis/fusion.py:596-602). NOT guarded: `check_titles` (core/pipeline.py:523-528), `sport_detect.moments` / `sport_clips.choose` (analysis/fusion.py:276-285, :542-544), `sports.profile_for` (:941). | `_finish` catches render errors (longform/process.py:166-170); unknown mode → `ValueError` (:51-54). | No guard of its own (podcast.analyze/render_clip at core/pipeline.py:1345-1360 run inside the per-clip try of `_finish`). | `NotVerticalError` is the deliberate pre-flight failure (core/modes.py:34-43). |

### 1.7 Model dependencies

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| Whisper (transcription/transcriber.py:90 `_load_model`; `whisper.model/device` config/settings.yaml:48-51), LLM via `create_backend(_with_usable_model(config["llm"]))` (core/pipeline.py:431; llm/base.py:30-60 `LLMBackend.generate/name/sees_images/look/chat`), YOLOv8-pose for reaction windows + tracking (`config["tracking"]["detector"]` default `yolov8n-pose.pt` config/settings.yaml:126; `compute_tracking` video/tracker.py:808), FFmpeg/ffprobe (core/binaries.py:74, :79). | Plus: PANNs sound tagger (analysis/panns.py:33 `panns_mobilenetv1.pth`, `available()` :53; optional, core/pipeline.py:374-376); OCR for on-screen text (analysis/game_text.py:57 `available()`; analysis/fusion.py:141-148, :881-905); image-capable LLM for `_look_at_game` (analysis/fusion.py:586, :849-879; `llm.sees_images()`); YOLOv8-pose + speaking scores for webcam detection (gaming/detect.py:147 `sample_tracks`, :241 `speaking_scores`, :375 `find_cam`, :538 `head_in_box`; model name from `config["tracking"]["detector"]` gaming/run.py:119, :190, :227). Knowledge file config/gaming.yaml (analysis/gaming.py:24). | Plus: PANNs (core/pipeline.py:900-905), OCR scoreboard (sports/soccer/__init__.py:30-47; sports/basketball/__init__.py:59-70; both gated on `game_text.available()`), YOLOv8 `yolov8n.pt` at imgsz 1280 for ball/action framing (sports/soccer/__init__.py:15-27; config/sports.yaml:140, :406), image LLM via profile `look` (analysis/fusion.py:595-602; sports/basketball/profile.py:326). Knowledge file config/sports.yaml (sports/__init__.py:21, :36). | Whisper + LLM; highlights/edited use `longform.assemble` (longform/process.py:242, :318). | YOLOv8-pose through tracker helpers (video/podcast.py:50-58 imports `_get_model, _detect, ...` from video.tracker). | FFmpeg only; torch/cv2 never loaded at render (core/pipeline.py:585-588, :1209-1213, :1409-1414). |

### 1.8 Files read / written

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `data_dir/downloads/` (core/pipeline.py:1029), `data_dir/transcripts/` (:404), `data_dir/clips/<channel>/<title [id]>/clip_SSSSS-EEEEE.mp4` (:498-503, :1151-1152), `.pre-card.mp4` (:1168), `.source.mp4` scratch (:1316-1318), `.ass` captions (:1265-1271; discarded :1422), `.card.png` (:1428-1436), `state.db`; `data_dir/logs/job_N.log` (server/jobs.py:150). | Probe cuts `clip_dir/gaming_probe_SSSSS.mp4` (gaming/run.py:131-143, discarded :143); reads config/gaming.yaml; writes `render_opts["gaming"]` on the clip row; `creators` gaming layout (core/state.py:770-790). | Reels `clip_dir / reels.file_name(reel)` (+ `.pre-card.mp4`) (core/pipeline.py:722-724; sports/core/reels.py:128, :172); outcome JSON (`set_outcome`); reads config/sports.yaml; scoreboard OCR reads the source file (sports/soccer/scoreboard.py:314). | `clip_dir/Longform/<subdir>/` (longform/profiles.py:11-33; process.py:140-145), `highlights.mp4` (:265). | None extra. | Separate vertical download copy (core/pipeline.py:1019-1029). |

### 1.9 Configuration keys

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| `clips.{min_score,max_clips_per_video,min_duration,max_duration,vertical,captions,outro,caption_style,filter,watermark,focus,required_hashtags}` (config/settings.yaml:60-71; core/pipeline.py:507, :535, :1180-1200, :1314), `scoring.*` (:75-103), `analysis.*` (:105-115), `video.parallel_renders` (:121; core/pipeline.py:583), `tracking.{detector,sample_fps}` (:125-129), `whisper.*`, `llm.*`, `transcription.*`, `content_language`, `paths.data_dir`. | `clips.gaming`, `clips.gaming_scoring`, `clips.gaming_layout` (keys documented gaming/run.py:3-39; validated core/modes.py:115-157), `clips.gaming_remember` (core/pipeline.py:288); `scoring.profiles.gaming.weights` (analysis/gaming.py:187-189); `scoring.{read_screen,look_at_game,game_bonus,game_moment_max,menu_penalty}` (analysis/fusion.py:141, :380-384, :586); `tracking.*`. | `clips.sport = {name, highlights, period, footage?, teams?, reels?, events?, request?}` (sports/__init__.py:72-134); `scoring.profiles.sports.weights` (sports/core/profile.py:247-255); per-sport entries in config/sports.yaml (`events, callouts, patterns, read_screen, screen_text, sounds, sound_groups, curves, framing, highlights_choices, periods, footage_choices, scoring_events, celebration_seconds, replay_within_seconds, replay_words, crowd_lag_seconds`) read through `sports.spec` (sports/__init__.py:36) and `SportProfile` accessors (sports/core/profile.py:49-245). | `payload.longform.mode`, `.shorts` (server/jobs.py:260-268). | `clips.podcast`. | `clips.vertical_live`. |

### 1.10 Video processing it does itself vs delegates

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| Host does all of it in `_render_files`: `cut_clip` (video/cutter.py:21 at core/pipeline.py:1318), `compute_tracking` (:1378-1383), `render_vertical` (video/cropper.py:33 at :1393-1396), captions `build_captions` (:1265), title card/watermark/outro (:1428-1446). | Host cuts the intermediate (:1318), then delegates the ENTIRE composition to `gaming_run.render(intermediate, render_path, g, config, ass_path, vf_extra, normalize)` (:825-826) → `layout.plan` + `compose.render` with its own `-filter_complex` (gaming/run.py:234-235; gaming/compose.py:78-106). Plugin writes the output path the host chose; host still applies card/watermark/outro afterwards (:1428-1446). `None` → host's own path. | Detection delegated inside fusion: `sport_detect.moments` (analysis/fusion.py:276-285; sports/core/detect.py:94) + `highlights.score_windows` for its windows (:292-297) + `sport_clips.choose/report` (:542-545). Render: host cuts, asks `sports.framing(name, intermediate, config)` for a tracking dict (:1365-1367; sports/__init__.py:174-181) and renders itself with `render_vertical` (:1393). Reels: host joins with `sports.core.reels.join` (:725) and `outro.finish` (:732). | short_clips/clips_140 → host `_render_files` with landscape fit (core/pipeline.py:1203-1208, :1409-1414). highlights/edited → `longform.assemble` makes the whole file (longform/process.py:268-273, :345-350); host only registers. | Host cuts (:1318), delegates framing decision + render to `podcast_mod.analyze` / `podcast_mod.render_clip` (:1345-1360; video/podcast.py:68, :227) which itself calls `render_vertical` (:239). | Host does everything: one `cut_clip` with `fit_filter` (:1211-1213, :1409-1414). |

### 1.11 Resource requirements

| Standard | Gaming | Sports | Longform | Podcast | VL |
|---|---|---|---|---|---|
| GPU for Whisper (config/settings.yaml:51) and NVENC when available (:118); `parallel_renders` worker threads (core/pipeline.py:583, :634-651); `_share_the_cpu` caps cv2/torch threads (:66-106, :585-588). One video at a time (core/queue.py:22). | Plus CPU minutes for PANNs on a long VOD (core/pipeline.py:365-367), up to 8 probe cuts × 40 s (gaming/run.py:70-72, :126-143), per-clip YOLO + speaking passes (gaming/run.py:200-216), OCR budget 120 s (analysis/game_text.py:35), vision budget 240 s (analysis/game_vision.py:31). | Plus scoreboard OCR up to the video's own length (sports/basketball/__init__.py:50-57: 25 min on a 79-min game), default 900 s wait (sports/__init__.py:195-204), per-clip ball detection at imgsz 1280 (config/sports.yaml:140, :406). | As Standard. | YOLO per shot at render. | Lightest: no cv2/torch at render (:585-588). |

## 2. The contract Gaming and Sports already share

### 2.1 The scoring-profile duck type (consumed by analysis/fusion.py)

Fusion never type-checks; it reads these names off whatever object arrives as `gaming` (or `sport`, aliased at analysis/fusion.py:77-82). `GamingProfile` (analysis/gaming.py:71-148) and `SportProfile` (sports/core/profile.py:29-245, section "the interface fusion's gaming path uses" :49-115) both provide:

| Member | Read at | Notes |
|---|---|---|
| `weights: dict` | analysis/fusion.py:86 | text/visual/reaction/audio/engagement; "game" key popped (analysis/gaming.py:189; sports/core/profile.py:254) |
| `games: list` | :152 | `[{"name","start","end"}]`; empty for a sport (sports/core/profile.py:33) |
| `game: str` | :157 | "" for a sport (:66-68) |
| `spec: dict` with `label` | :156 | `genre_spec` (analysis/gaming.py:79-80) / `sports.spec` (:57-59) |
| `guidance(kind="clips"|"windows"|"rerank", start=None, end=None) -> str` | :150, :153, :221, :294, :1020 | prompt block |
| `game_at(start, end=None) -> (game, genre)` | :222-223, :897 | |
| `genre_track(seconds) -> list[str]` | core/pipeline.py:860, :958 (for `sound_signal`) | |
| `reads_screen: bool` (optional, getattr) | analysis/fusion.py:141 | |
| `screen_lexicon: dict` (optional, getattr) | :895 | |
| `spec_for(genre)` | sports/core/profile.py:73 docstring says analysis/game_vision.py uses it (inferred; not re-verified in game_vision.py) | |

Sport-only members fusion/pipeline read (all via `sport.` or `getattr`): `option` (analysis/fusion.py:275), `curves` (:277), `board` (:280), `label` (:289), `event_label` (:515), `one_play_per_clip` (:287, :565), `look(finalists, video_path, llm)` (:595-602), `report_data` written (:545) and read (core/pipeline.py:470), `title_rules()` / `check_titles(candidates, metas, regenerate)` (core/pipeline.py:513-528), `option["reels"]` (:668). Members consumed by sports/core/{detect,clips,select}.py only: `scoring_types, celebration, sound_curves, confirmed_type, context_weight, extra_moments, clip_span, events_spec, importance, window_of, crowd_lag, replay_within, replay_said, callouts_in, classify, resolve_footage, footage` (sports/core/profile.py:117-245).

### 2.2 The per-package hook surface (what "adding a sport" is today)

sports/__init__.py is already a tiny plugin loader: `SPORTS = {"soccer": "sports.soccer", "basketball": "sports.basketball"}` (:23) + a config/sports.yaml entry (docstring :13-15). Hooks resolved by `importlib.import_module` + `getattr`:

| Hook | Resolver | Implementations |
|---|---|---|
| `profile(config, option, video) -> SportProfile` (required) | sports/__init__.py:165-171 | sports/soccer/__init__.py:9, sports/basketball/__init__.py:16 |
| `framing(clip_path, config) -> dict | None` | :174-181 | soccer :15, basketball :33 — returns the `render_vertical` tracking dict |
| `prepass(video_path, duration) -> dict` (attrs set on the profile) | :184-192 | soccer :30, basketball :59 |
| `prepass_wait(duration) -> float` | :195-204 | basketball :50 |
| `READS_DESCRIPTION: bool` | :207-215 | basketball :12-13 |
| `hotwords(option, video) -> str | None` | :218-225 | basketball :24 |

Gaming is the other half of the same shape, as two module functions called inside host guards (gaming/run.py:1-67 docstring):
- `prepare(source: Path, candidates: list, config: dict, work_dir: Path) -> dict` (gaming/run.py:103) — once per video, after candidates are known (core/pipeline.py:598).
- `render(intermediate: Path, output: Path, g: dict, config: dict, ass_path=None, vf_extra="", normalize=True) -> dict | None` (gaming/run.py:183) — per clip; `None` = host renders (core/pipeline.py:1334-1335).
- Scoring side: `analysis.gaming.profile_for(config, games, title) -> GamingProfile` (analysis/gaming.py:170) and `knowledge()` (:35).

### 2.3 Protocol sketch (names from the real call sites; types from core/models.py and analysis/metadata.py)

```python
from core.models import DownloadedVideo, Segment, ClipCandidate, Rejection, RenderedClip   # core/models.py:12, 30, 45, 77, 88
from analysis.metadata import ClipMetadata                                                 # analysis/metadata.py:60-67

class ScoringProfile(Protocol):                      # analysis/gaming.py:71 / sports/core/profile.py:29
    weights: dict; games: list; game: str; spec: dict
    def guidance(self, kind: str = "clips", start: float | None = None, end: float | None = None) -> str: ...
    def game_at(self, start: float, end: float | None = None) -> tuple[str, str]: ...
    def genre_track(self, seconds: int) -> list[str]: ...
    # optional: reads_screen: bool; screen_lexicon: dict; look(finalists, video_path, llm) -> int
    # optional (sport today): title_rules() -> str; check_titles(cands, metas, regenerate) -> list[ClipMetadata]; report_data: dict

class ModePipeline(Protocol):                        # the union of what pipeline.py asks of gaming/ and sports/
    key: str                                         # the clips.<key> flag; core/modes.py predicate
    # analysis side, started before Whisper and joined after it (core/pipeline.py:369-387, :427-430, :433-438)
    def start_prepass(self, config: dict, video: DownloadedVideo) -> "Prepass": ...      # MatchReading.__init__ :881-894
    def hotwords(self, config: dict, video: DownloadedVideo) -> str | None: ...           # :399 / sports/__init__.py:218
    def finish(self, prepass, hype_out: dict) -> tuple[ScoringProfile | None, "ChatSignal | None", "GameSounds | None"]: ...  # :434 / :436
    # render side (worker thread: NO db, NO llm — core/pipeline.py:1123-1138)
    def prepare_render(self, source: Path, candidates: list[ClipCandidate], config: dict, work_dir: Path) -> dict: ...   # gaming/run.py:103
    def render(self, intermediate: Path, output: Path, opts: dict, config: dict, *, ass_path: Path | None, vf_extra: str, normalize: bool) -> dict | None: ...   # gaming/run.py:183; None = host renders
    def framing(self, intermediate: Path, config: dict) -> dict | None: ...               # sports/__init__.py:174 → video/cropper.py:33-60 tracking dict {mode: track|split|fit_blur, path|region|webcam_box, face_y?, rows?}
    # post-render (main thread, db allowed)
    def after_render(self, db, video_id: str, made: list[RenderedClip], clip_dir: Path, config: dict, segments: list[Segment]) -> list[RenderedClip]: ...   # _sport_reels :685
    # API/validation side
    def clean_options(self, raw) -> dict: ...                                                # sports.clean :72 / modes.clean_gaming :114
```

Return conventions already in force and worth keeping: every hook is called inside a host `try` and a falsy/None result means "the host does it the standard way" (core/pipeline.py:803-829, :1550-1560); render hooks receive `ass_path, vf_extra, normalize` and must burn captions themselves (gaming/compose.py:66-70; video/podcast.py:227-240); the dict a render hook returns is persisted verbatim in `render_opts` under the mode's key and handed back on re-render (core/pipeline.py:1456-1482, :1474, :1480; server/api.py:549-557).

## 3. Where an adapter hooks in core/pipeline.py without touching existing branches

Every mode-specific site is an `if`/`elif` chain keyed on `modes.*` or on a local (`gaming_scoring`, `sport_name`, `match`). A third-party pipeline adds one more arm, or is folded into the existing variable the arm already feeds:

| # | Lines | Existing shape | Where the new arm goes |
|---|---|---|---|
| 1 | core/pipeline.py:217-229 | `if modes.is_vertical_live(config): ... elif modes.sport(config): ...` source checks | `elif plugin:` (e.g. a plugin that demands an orientation) |
| 2 | :337-341 | `gaming_scoring = modes.gaming_scoring(config)`; `sport_name = modes.sport(config)` | `plugin = registry.for_config(config)` next to them; `modes.py` gets one more predicate (same file as the others) |
| 3 | :352-353, :359-362 | hype thread keeps `messages` when `gaming_scoring or sport_name` | extend the condition with `or plugin` |
| 4 | :368-383 and :387 | `if gaming_scoring and not sport_name:` sounds thread; `match = MatchReading(config, video) if sport_name else None` | `elif plugin: prepass = plugin.start_prepass(config, video)` |
| 5 | :397-401 | `if sport_name: hint = _listening_for(config, video, url)` | `elif plugin: hint = plugin.hotwords(config, video)` |
| 6 | :427-430 | `cancel.wait(...)` per thread | `plugin` prepass joins here (or inside its `finish`, as `MatchReading.finish` does :923-929) |
| 7 | :433-438 | `if match is not None: sport_profile, chat, sounds = match.finish(hype_out)` / `elif gaming_scoring: gaming_profile, chat, sounds = _gaming_scoring_inputs(...)` | `elif plugin: gaming_profile, chat, sounds = plugin.finish(prepass, hype_out)` — assigning to `gaming_profile` reuses the existing `find_clips(gaming=...)` kwarg at :446-447 with no fusion change |
| 8 | :439-450 | `find_clips(...)` kwargs | nothing to add if #7 assigns `gaming_profile`; a plugin with its OWN moment detector (the sport branch at analysis/fusion.py:273-297, :538-548, :565-580 is keyed on `sport is not None` and calls sports.core directly) would need either a `SportProfile`-compatible object passed as `sport=`, or a new generic kwarg in fusion (that IS a fusion change) |
| 9 | :465-473 | `if sport_profile is not None and getattr(sport_profile, "report_data", None): outcome["sport"] = ...` | `elif plugin and getattr(profile, "report_data", None): outcome[plugin.key] = ...` |
| 10 | :513-528 | `title_rules` / `check_titles` read via `getattr(sport_profile, ...)` | change the lookup target to "whichever profile is set" (`sport_profile or gaming_profile`) — no branch change |
| 11 | :598 | `gaming_opts = _gaming_prepare(...) if modes.is_gaming(config) else None` | `elif plugin: {plugin.key: plugin.prepare_render(...)}`; the dict flows through `_clip_opts` :600-607 and `render_opts` unchanged |
| 12 | :668-669 | `if sport_profile is not None and made and (sport_profile.option or {}).get("reels"): rendered += _sport_reels(...)` | `elif plugin: rendered += plugin.after_render(db, video.video_id, made, clip_dir, config, segments)` |
| 13 | `_render_files` :1180-1197 | flag resolution (`podcast`, `vertical_live`, `sport_name`, `gaming`) from `opts` or `config` | `plugin_opts = opts.get(plugin.key) or config["clips"].get(plugin.key)` beside them |
| 14 | :1331-1361 | `if gaming: gaming_kept = _try_gaming_render(...)` / `if gaming_kept is not None: pass` / `elif podcast:` / `else:` (tracked, with `_sport_framing` at :1365-1367) | one more `elif` in the same ladder: `elif plugin_opts: kept = _try_plugin_render(...)`; a framing-only plugin instead hooks beside `_sport_framing` :1365 |
| 15 | :1456-1482 | `render_opts_json` persistence (`podcast`, `vertical_live`, `gaming`, `watermark`, `sport`) | add `**({plugin.key: kept} if kept is not None else {})` |
| 16 | `_register_clip` :1563-1635 | DB funnel; re-run branch merges `gaming`/`speaker_turns`/highlights card (:1596-1630) | a plugin that must survive re-render merges its key here like `gaming` does (:1618-1622) |
| 17 | `_sport_reels` :685-754 | shows how a mode registers FINISHED files it made itself: synthetic `ClipCandidate` (:744-745) + `ClipMetadata` (:746) + `_register_clip` (:748) | the template for "plugin returns finished clips" |

Outside pipeline.py the same adapter needs: a `modes.py` predicate (core/modes.py:44-97 pattern), the worker's payload→config copy (server/jobs.py:191-216), API field + validation + exclusivity (server/api.py:39-62, :440-545), watch presets (server/automation.py:183-191), editor re-render option cleaning (server/api.py:549-557, :1748-1750), and `longform/process.py:86-88, :115-123, :199-204` if the mode should also work for 16:9.

## 4. Detection vs rendering, per mode

| Mode | Where candidate clips (time ranges) are produced | Where they are cut/rendered | Separated? |
|---|---|---|---|
| Standard | `find_clips` (analysis/fusion.py:54) called at core/pipeline.py:439; DB status `analyzed` :463 | `_render_files` in worker threads :634-651, cut at :1318 | Yes: stage boundary with DB commit (:463) and metadata in between (:514-518) |
| Gaming | Moments: same `find_clips`, with profile/chat/sounds evidence (analysis/fusion.py:126-160, :195-205). Layout detection (webcam) is RENDER-time: `prepare` runs after candidates exist (core/pipeline.py:598; gaming/run.py:126-143 probes the candidates' own time ranges) and `render` detects again per clip (gaming/run.py:197-218) | Host cuts intermediate (:1318) then `gaming_run.render` detects + composes in one call (gaming/run.py:183-242) | Moment detection yes; layout detection and composition are fused inside `render()` (it returns the layout dict only after writing the file :235-242) |
| Sports | Moments in `find_clips` via `sport_detect.moments` (analysis/fusion.py:276-285) fed by prepass results (`curves`, `board`) computed before transcription finished (core/pipeline.py:887-894); candidate windows re-timed by `sport_clips.choose` (sports/core/clips.py:107-196) | Host cuts (:1318); ball/action framing is render-time per clip (`_sport_framing` :1365; sports/soccer/__init__.py:15) then host `render_vertical` (:1393); reels joined post-render (:668) | Yes for moments; framing is per-render like Gaming's layout |
| Longform short_clips/clips_140 | `find_clips` (longform/process.py:116) | `_render_files` with `profile` (:183-186) | Yes |
| Longform highlights/edited_stream | `find_clips` + `select_highlights` (:116, :245) / signals only (:322) | `longform.assemble` writes ONE file (:268-273, :345-350) | Yes; and the output is a finished file registered via `_register_clip` (:295-296, :359-360) |
| Podcast | Standard `find_clips` | Host cuts; `podcast_mod.analyze` decides framing per clip at render (:1345-1349); `render_clip` (:1356-1359) | Moments yes; framing at render |
| Vertical Live | Standard `find_clips` (`measures_reaction` unaffected unless gaming_scoring; core/modes.py:165-173) | Single `cut_clip` (:1409-1414) | Yes; no framing stage at all |

**Can a pipeline return finished clips instead of time ranges today?**
- Through `find_clips`: no. Its contract is `list[ClipCandidate]` time ranges (analysis/fusion.py:70), and `_render_files` always cuts from `source` (core/pipeline.py:1318, :1409).
- Through the render hook: partly. `gaming_run.render` is handed the output path and writes the finished (pre-card) clip itself, returning only options (gaming/run.py:235-242; core/pipeline.py:1332); the host then applies card/watermark/outro (:1428-1446). So a plugin can own the encode of a clip, but not its time range or its existence.
- Through the DB funnel: yes. `_register_clip(db, video_id, candidate, final_path, meta, render_opts_json, config)` (:1563) takes any `final_path`; `_sport_reels` (:744-748) and longform `_highlights` (:277-296) register files the mode produced itself with synthetic `ClipCandidate`s. Nothing checks that `final_path` came from `_render_files`.
- Through remote rendering: the generator shape `render_all(video_id, source, items, segments, clip_dir, config, render_opts, language, workers, local=None, opts_for=None)` yielding `(candidate, meta, get_result)` with `get_result() -> (final_path, render_opts_json)` (remote_render/dispatch.py:90-93; consumed core/pipeline.py:654-657, longform/process.py:192-196) is already a renderer-replacement interface and is the cleanest existing seam for "plugin renders, host registers".

## 5. What a third-party pipeline would need from the host that is only reachable by importing internals

Taken from the actual imports in gaming/ and sports/ (grep over `from (core|video|analysis|llm|...)`), plus what core/pipeline.py hands them:

| Need | Today's internal module / symbol | Used by |
|---|---|---|
| Stage dataclasses | `core.models.{DownloadedVideo, Segment, ClipCandidate, Rejection, RenderedClip}` (core/models.py); `analysis.metadata.ClipMetadata` (analysis/metadata.py:60) | everything; sports reels build both (core/pipeline.py:744-746) |
| The config dict as a whole | `config["tracking"]["detector"|"sample_fps"]` (gaming/run.py:119, :190; core/pipeline.py:1347), `config["clips"]` (gaming/run.py:107), `config["paths"]["data_dir"]`, `config["scoring"]["profiles"]` (analysis/gaming.py:185; sports/core/profile.py:252) | all |
| Binaries | `core.binaries.ffmpeg/ffprobe/yolo_weights` (gaming/compose.py:23; sports imports list) | render + detection |
| Encoder settings | `video.encoding.video_encoder_args/audio_filter_args` (gaming/compose.py:25, :93-96), `hwaccel_input_args` (video/encoding.py:105) | own FFmpeg graphs |
| Cutting | `video.cutter.cut_clip(source, candidate, output_path, ass_path, vf_extra, normalize)` (video/cutter.py:21; gaming/run.py:105, :134) | probes, reels |
| Vertical render + tracking dict shape | `video.cropper.render_vertical(clip_path, tracking, output_path, ass_path, vf_extra, cam_position, normalize)` (video/cropper.py:33-60; video/podcast.py:239) | framing-only plugins |
| Tracker internals | `video.tracker.{_get_model,_detect,_assign,_face_box,_update_speaking,head_box,mouth_region,_infer_lock}` (video/podcast.py:50-58; sports import list), `video.framing.{is_cut,small_gray,HoldMove,stable_target}`, `video.capture.video_capture`, `video.asd` | podcast, gaming/detect, sports framing |
| GPU helpers | `core.gpu.{cuda_usable, torch_device}` | sports |
| Probe | `core.modes.probe_size/fit_filter` (gaming/run.py:188) | layout |
| Scratch cleanup | `core.paths.discard` (gaming/run.py:62) | all |
| Cancellation | `core.cancel.{check, check_active, wait, CancelledError}` (sports/soccer/__init__.py:41-42; analysis/fusion.py:598) | long prepasses |
| Progress | `core.progress.emit` — NOT used by gaming/ or sports/ today (they print); a plugin would need it to show a stage | — |
| LLM | `llm.base.LLMBackend` (`generate`, `sees_images`, `look`; llm/base.py:30-60), `llm.base.generate_json` (:71), prompt files under config/prompts (analysis/highlights.py:29-30) | scoring, `look` hooks |
| Signal helpers | `analysis.game_audio.{listen, sound_signal, GameSounds}` (core/pipeline.py:378, :855-861, :954-971), `analysis.panns.available` (:374, :901), `analysis.chat_moments.{chat_signal, ChatSignal}` (:849-854, :945-950), `analysis.gaming.knowledge()` (:852, :857, :947), `analysis.game_text.{available, read_screen, _ocr}` (sports/soccer/__init__.py:32-35; sports import list), `analysis.game_vision._jpeg` (sports import list), `analysis.hype.audience_signals` messages (:347-353) | scoring plugins |
| Fusion internals a sport-like detector relies on | `highlights.score_windows` (analysis/fusion.py:292-294), `_select_unique(priority=, distinct=)` (:570-579), `_voice_jump` (:127), `_event_windows` (:200), the `events` timeline format `[(second, "ON SCREEN: ...")]` (:278-279) | moment-detecting plugins |
| DB | `core.state.StateDB.{add_clip, set_clip, get_clip, clips_for_video, set_outcome, set_video_games, video_games, set_creator_gaming_layout, creator_gaming_layout, conn}` (core/pipeline.py:250, :290-295, :473, :741-748, :757-782) | reels, creator memory, outcome |
| Platform data | `sources.dispatch.{identify, game_info, description, download}` (core/pipeline.py:243, :362, :1004, :1029) | games list, description |
| Metadata regeneration closure | `analysis.metadata.generate_metadata_batch` passed to `check_titles` (core/pipeline.py:525-528) | title rules |
| End card / join | `video.outro.{enabled, finish, has_outro, DURATION, reset_tally, summary}` (core/pipeline.py:701-732, :202, :1167, :1446) ; `sports.core.reels.join` builds its own ffmpeg concat (sports/core/reels.py:172) | finished-file plugins |
| Post style / captions | `video.post_style` (:507-512), `video.captions.build_captions` (:1267) — host-side; plugins receive only `ass_path` | render plugins |
| Option validation | `core.modes.clean_gaming` (core/modes.py:115), `sports.clean` (sports/__init__.py:72) called from server/api.py:467-486 | API layer |
| Knowledge YAML | config/gaming.yaml (analysis/gaming.py:24), config/sports.yaml (sports/__init__.py:21) read with `yaml.safe_load` and `lru_cache` | both |

## 6. Open questions for the platform design

1. Fusion's sport branch (analysis/fusion.py:273-297, :538-548, :565-580) imports `sports.core.{clips,detect,select}` directly and is keyed on `sport is not None`; a third moment-detecting pipeline cannot reuse it without either implementing the full `SportProfile` surface or a new fusion kwarg. Which is the intended extension point: "profile duck type" (zero fusion change) or "moment provider" (one new kwarg)?
2. `check_titles` and `title_rules` are read from `sport_profile` only (core/pipeline.py:513-528) and are unguarded; should they move to the generic profile and into a guard like every other hook?
3. Gaming's `render()` both detects and composes (gaming/run.py:183-242). For a plugin SDK, should the host split "framing decision" (returns a plan/tracking dict, persisted) from "encode" (host-owned, like the sport path at core/pipeline.py:1365-1396)? The sport path shows the split already works.
4. `_render_files` runs in worker threads with "NO database access and NO LLM call" (core/pipeline.py:1123-1138). Plugin render hooks inherit that rule; is it enforced or just documented?
5. The only places a mode produces a finished file of its own (`_sport_reels` core/pipeline.py:685, longform `_highlights` longform/process.py:231) build synthetic `ClipCandidate`s with `start=0`/nudged ends to satisfy the `(video_id, start_s, end_s)` UNIQUE key (core/pipeline.py:771-782 `_free_end`; longform/process.py:25 `_NUDGE`). A plugin returning finished clips needs a first-class identity instead of that workaround.
6. Mutual-exclusion rules are hand-written per pair (server/api.py:527-543, server/automation.py:183-191). A registry would need a declarative "conflicts with" field.
7. Remote rendering (`render_all`, remote_render/dispatch.py:90) only knows `render_opts`/`opts_for`; a plugin's render hook would have to exist on the render worker too (remote_render/worker.py) — not examined here.
8. `modes.py` says Gaming is "never combined with [Vertical Live], Podcast or Longform" (core/modes.py:56-59) yet `gaming_scoring` IS combinable with VL (:68-73); a plugin manifest needs to separate "scoring profile" from "layout" as the code already does.
