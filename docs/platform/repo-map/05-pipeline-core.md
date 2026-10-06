# Pipeline core, job system, progress, cancellation, errors, state and storage

Repository: /home/user/clips-studio (branch claude/open-platform-w4eh9g). All paths repo-relative; `path:line` cites the line a claim rests on. "(inferred)" marks a conclusion the code does not state literally. Read-only survey; nothing in the repo was changed.

---

## 1. Layering (as the code states it)

`core/queue.py:7-16` documents a one-directional layering:

```
Queue Manager (core/queue.py)
  -> Queue Item            a `jobs` row                      (core/state.py)
  -> Processing Config     jobs.payload, a JSON snapshot
  -> Video Pipeline        core.pipeline.process_video
  -> Result / Error        job status + error + data/logs/job_N.log
```

- `core/queue.py` knows only a `StateDB`: "no FastAPI, no UI" (`core/queue.py:18-21`).
- `server/jobs.py` holds the single worker thread, `class Worker(threading.Thread)` (`server/jobs.py:40`), that claims jobs and calls the pipeline. It is started by `create_app()` in `server/api.py:597,615` and is the only place `process_video` is called in the API process.
- `core/pipeline.py` orchestrates one video: `download -> transcribe -> analyze -> render` (`core/pipeline.py:1-8`).
- Stages communicate "only through these types (and files on disk), never by importing each other's internals" (`core/models.py:1-5`).
- The same `process_video` is also called directly from the CLI (`main.py:203`, command `process`) and from the RSS daemon (`core/scheduler.py:100`).

---

## 2. Shared dataclasses (core/models.py): the data passed between stages

| Dataclass | Fields (type = default) | Where |
|---|---|---|
| `DownloadedVideo` | `video_id: str`, `title: str`, `path: Path`, `duration: float` (s), `channel: str = ""`, `games: list = []` (`[{"name","start","end"}]`), `description: str = ""` | `core/models.py:11-26` |
| `Segment` | `start: float`, `end: float`, `text: str`, `words: list|None = None` (`[{"start","end","word"}]`) | `core/models.py:29-41` |
| `ClipCandidate` | `start: float`, `end: float`, `score: int`, `hook: str = ""`, `reason: str = ""`, `source: str = "transcript"` (`"transcript"` or `"signal"`), `engagement: int|None = None`, `trending: bool = False`, `subscores: dict|None = None`; property `duration`; `overlap_ratio(other)` | `core/models.py:44-75` |
| `Rejection` | `candidate: ClipCandidate`, `reason: str` (`timestamp_overlap | transcript_similarity | segment_reuse | below_min_score | over_limit`), `kept: ClipCandidate|None = None` | `core/models.py:78-84` |
| `RenderedClip` | `source_video_id: str`, `candidate: ClipCandidate`, `path: Path` | `core/models.py:87-92` |

`subscores` keys seen in the pipeline: `text`, `audio`, `visual`, `reaction`, `engagement` (`core/models.py:52-54`, printed at `core/pipeline.py:484-491`), plus `intent`, `intent_why`, `required` (`core/pipeline.py:489-490`), `reaction_measured` (`core/outcome.py:63`), `game` (`analysis/fusion.py:666`).

`ClipMetadata` (`analysis/metadata.py:60-67`): `title: str`, `description: str`, `hashtags: list[str] = []`, `headline: str = ""`, `subline: str = ""`.

---

## 3. Job lifecycle

### 3.1 Job types and payloads
From `server/jobs.py:7-11` and the dispatch at `server/jobs.py:270-294`:

| `jobs.type` | Payload | Handler |
|---|---|---|
| `process` | `{"url", "force", ...options}` (options in section 8) | `core.pipeline.process_video` (`server/jobs.py:283`), or `longform.process.process_longform` when `payload["longform"]` (`:279-281`), or both via `Worker._both_formats` when `payload["longform"]["shorts"]` (`:274-276`, `:756-775`) |
| `render` | `{"clip_id", "start"?, "end"?, "render_opts"?}` | `Worker._rerender_clip` (`server/jobs.py:285`, `:777-820`) |
| `translate` | `{"clip_ids", "languages", "stage": "translate"|"export", "folder"?, ...}` | `Worker._translate_clips` (`:287`, `:383-479`) |
| other | | `ValueError(f"Unknown job type ...")` -> job `failed` (`:289`) |

### 3.2 States
- Job statuses: `queued`, `running`, `done`, `failed`, `cancelled`. `RETRYABLE = ("failed", "cancelled")`, `LIVE = ("queued", "running")` (`core/queue.py:46-50`); terminal writes at `server/jobs.py:295-313`.
- Video statuses (`videos.status`), written by the pipeline: `queued` (upsert default, `core/state.py:864`) -> `downloaded` (`core/pipeline.py:304`) -> `transcribed` (`:420`) -> `analyzed` (`:463`) -> `done` (`:481` when no candidates, `:673` after rendering). `failed` is set by `recover_stuck_videos` (`core/state.py:571-581`) and the daemon (`core/scheduler.py:103`).
- Clip statuses: `queued` at registration ("awaiting a daily schedule slot", `core/pipeline.py:1589`) -> `scheduled` -> `uploaded`/`failed` (`core/scheduler.py:1-14`, `:154`).

### 3.3 The worker loop (who claims jobs)
`Worker.run()` (`server/jobs.py:95-321`):
1. Opens its own `StateDB` (sqlite, one connection per thread) (`:96`).
2. Startup recovery: `db.recover_interrupted_jobs()` re-queues rows left `running` with `interrupted = 1, attempts = attempts + 1` (`core/state.py:1384-1400`); `db.recover_stuck_videos()` marks videos in `downloaded|transcribed|analyzed` as `failed` (`core/state.py:571-581`); creator backfill (`server/jobs.py:97-112`).
3. Installs the progress handler `progress.set_handler(on_progress)` (`:121-134`): tags each event with the current job id (except `prefetch` events), records a per-job snapshot, and broadcasts `{"type": "progress", "job_id", ...event}`.
4. Loop: if `queue.is_paused(db)` wait 2 s (`:142-145`); else `job = db.claim_next_job()` (`:146`), which selects `status = 'queued' ORDER BY position, id LIMIT 1` and sets `running` + `started_at` (`core/state.py:1325-1340`). `Worker.notify()` wakes the loop early (`:65-69`).
5. Opens `data/logs/job_<id>.log` (`feedback.open_job_log`) and stores `jobs.log_path` (`:162-164`); broadcasts `{"type":"job","status":"running"}` and `{"type":"queue"}` (`:165-166`).
6. For `process`: `identify(payload["url"])` -> `vid`; `db.set_job(id, video_id=vid)`; `cancel.set_active(vid)`; `cancel.clear(vid)`; `self.prefetch.wait_for(vid)` (`:167-186`). Then `self.prefetch.maybe_start(db)` for the next queued video (`:187`).
7. `cfg = copy.deepcopy(self.config)`, `_check_plan(db, cfg, payload)` (signed-in AI plan gate, `:730-744`), then the payload-to-config merge (section 8) (`:197-269`).
8. Dispatch per table above.
9. Terminal: `db.finish_job(id, "done")`, `_run_follow_up(db, job, payload)` (the `then: {"action": "publish"}` intent, `:683-728`), `_announce(db, job, "done")` (`:295-298`). `CancelledError` -> `finish_job(id, "cancelled", "Cancelled by user")` (`:299-302`). Any `Exception` -> `traceback.print_exc()`, `scrub_secrets(str(e))`, `finish_job(id, "failed", message[:2000])`, `_announce(..., "failed", message[:500])` (`:303-313`).
10. `finally`: clears current job id and progress entry, `cancel.set_active(None)`, `feedback.close_job_log()`, `_prune_logs()` (keeps newest `_KEEP_LOGS = 50`) (`:314-321`, `:28`, `:78-87`).

`finish_job` writes `status, error, interrupted = 0, finished_at, updated_at` (`core/state.py:1312-1323`).

Invariants (`core/queue.py:23-32`): one video at a time; a payload is a snapshot; stopping never deletes; a failure is contained to its own video.

### 3.4 Queue API (core/queue.py)

| Function | Does | Line |
|---|---|---|
| `is_paused(db)` / `set_paused(db, paused)` | `app_state["queue_paused"]`; defaults to PAUSED (`"1"`): "Nothing in this app starts processing on its own" | `:56-72` |
| `duplicate_of(db, video_id) -> int|None` | a `queued`/`running` job for the video | `:78-86` |
| `enqueue(db, type_, payload: dict, video_id="", title="") -> int` | `db.add_job(type_, json.dumps(payload), ...)` | `:89-90` |
| `enqueue_once(db, video_id, payload, title="") -> (outcome, job_id)` | `"done" | "existing" | "full" | "queued"`; for integrations | `:93-115` |
| `active_count`, `capacity` (`MAX_ACTIVE = 5`), `waiting_ahead(db, job_id)` | | `:36-43`, `:118-141` |
| `start_if_alone(db, job_id) -> bool` | unpause only if this is the sole queued process job | `:144-158` |
| `move(db, job_id, delta)` | swap `position` with the neighbour | `:164-191` |
| `remove(db, job_id)` | delete a `queued` job only | `:197-204` |
| `retry(db, job_id, drop=()) -> int|None` | row reused: `status='queued', error='', interrupted=0, started_at='', finished_at='', position=end`, optional payload keys dropped; `attempts` kept | `:207-239` |
| `update_settings(db, job_id, payload)` | replace a `queued` job's payload | `:242-252` |
| `clear(db, what)` | `completed | failed | queued | all` | `:255-271` |
| `estimate(db) -> {queued_seconds, per_video_seconds, samples, confident}` | median of `process_seconds/duration` over the last 20 done videos; `COLD_START_SECONDS = 3600` | `:277-330` |
| `snapshot(db, history_limit=50) -> {processing, queued, completed, failed, paused, estimate, capacity, max_active}` | the read model; each item carries `url` and `settings` lifted out of payload | `:337-382` |

`add_job` ordering: `process` jobs go to `MAX(position)+1`; `render`/`translate` jobs take the position of the first queued process job and shift the rest back by one (`core/state.py:1275-1310`).

### 3.5 Retries, interruption, crash
- No automatic retry. `retry()` is explicit; `attempts` is only incremented by crash recovery (`core/state.py:1396`).
- Crash: on next start, `running` jobs return to `queued` with `interrupted = 1` (`core/state.py:1384-1400`); the log file is keyed by job id, so the second attempt appends to the same `job_N.log` (`core/queue.py:220-222`, `server/feedback.py:90`: opened with mode `"a"`).
- Resume: `process_video` re-enters from the top; the download is skipped when `data/downloads/<id>.mp4` exists (`_cached_or_download`, `core/pipeline.py:1010-1113`), and the transcript is reused when `data/transcripts/<id>.json` exists (`transcription/transcriber.py:210-219`). Analysis and rendering always re-run (inferred: no cache of candidates exists; `_register_clip` tolerates the UNIQUE clash on re-run, `core/pipeline.py:1599-1633`).
- "Already processed" guard: `db.shorts_made(video_id)` (`core/state.py:829-854`) short-circuits a Shorts run unless `force` (`core/pipeline.py:301-303`).
- Daemon mode (`core/scheduler.py:_process_queue`, `:93-103`) reprocesses videos with status `queued|downloaded|transcribed|analyzed` and marks `failed` on exception.

---

## 4. Cancellation (core/cancel.py)

- Module state: `_cancelled: set[str]`, `_active: str|None` (`core/cancel.py:13-16`).
- `set_active(video_id)`, `active_video()`, `request_cancel(video_id)`, `is_cancelled(video_id)`, `check(video_id)` (raise at stage boundaries), `check_active()` (raise if the running video is cancelled, for deep code without a video id), `clear(video_id)`, `wait(thread, timeout=None, video_id=None)` (a join that aborts on cancel), `run(cmd)` (a Popen that kills the process on cancel) (`core/cancel.py:19-122`).
- `class CancelledError(Exception)` (`:36`).
- Pipeline check points: `cancel.clear` at start (`core/pipeline.py:231`); `cancel.check` after download (`:305`) and after transcription (`:422`); `cancel.wait` on the prepass threads (`:428-431`); `cancel.check_active()` on every completed render future (`:650`) and on entry to `_render_files` (`:1147`). Inside stages: transcriber loop (`transcription/transcriber.py`, test at `tests/test_cancel.py:84-113`), LLM chunk loop (`analysis/highlights.py`, test `:124-147`), rerank batches (`analysis/fusion.py:617`), yt-dlp progress hook (`sources/ytdlp_common.py:34-36`).
- HTTP: `POST /cancel {video_id | url}` -> `cancel.request_cancel(vid)` (`server/api.py:1201-1214`).
- The worker converts `CancelledError` to job status `cancelled` (`server/jobs.py:299-302`).
- A cancel flag is sticky until `clear()`; the worker clears it at job start (`server/jobs.py:174-181`).

---

## 5. Progress

### 5.1 The hook (core/progress.py)
`set_handler(handler: Callable[[dict], None] | None)`, `emit(**event)`, `set_thread_tags(**tags)` (`core/progress.py:18-39`). A handler exception is swallowed: "a broken UI listener must never kill a render" (`:35-37`). Default (CLI) is no handler.

### 5.2 Event vocabulary (all emitted as keyword dicts)

| `stage` | Extra fields | Emitter |
|---|---|---|
| `download` | `message=url`; then `fraction, video_id, downloaded, total` | `core/pipeline.py:205`; `sources/ytdlp_common.py:40-46` |
| `downloaded` | `video_id, title, duration` | `core/pipeline.py:208` |
| `converting source to H.264` | `video_id` | `core/pipeline.py:188` |
| `transcribe` | `video_id, title`; then `fraction` | `core/pipeline.py:390`; `transcription/transcriber.py:182`; `transcription/cloud.py:78,98` |
| `signals` | | `analysis/fusion.py:96` |
| `analyze` | `video_id`; then `current, total` (chunks) | `core/pipeline.py:424`; `analysis/highlights.py:71,132,138` |
| `reactions` | `current, total` | `analysis/fusion.py:359` |
| `ranking` | `current, total` | `analysis/fusion.py:620` |
| `render` | `video_id, clip, total` | `core/pipeline.py:612-614` |
| `done` | `video_id, clips, seconds` | `core/pipeline.py:674-676` |
| `multilingual` | `message, fraction, clip, total` | `server/jobs.py:426-433` |
| `prefetch` (+ `prefetch=True`) | restamped by thread tags | `core/prefetch.py:120` |

Longform emits the same stage names (`longform/process.py:57-209`).

### 5.3 Percentage folding (server/jobs.py)
`_STAGES = {stage: (base, weight, label)}` (`server/jobs.py:30-39`): download 0.00/0.15 "Downloading video"; downloaded 0.15/0; transcribe 0.15/0.25 "Transcribing speech"; signals 0.40/0.05; analyze 0.45/0.20 "Finding the best moments"; ranking 0.65/0.05; reactions 0.70/0.08; render 0.78/0.22 "Rendering clips". Stated as "kept identical to the UI's STAGES in ui/src/renderer/src/lib/jobProgress.ts" (`:25-27`). Within-stage fraction: `event["fraction"]`, else `(clip-1)/total`, else `(current-1)/total`, else 0.5 (`:323-336`). Never moves backwards; capped at 0.99; a `span=(lo,hi)` scales both-format jobs (`:338-345`, `:746-754`). Unknown stages (`done`, `publish`) do not move the figure (`:325-326`).

`Worker.progress_snapshot(job_id) -> {stage, label, percent, eta_seconds, elapsed_seconds} | None` (`:347-366`); ETA only above 6 %.

### 5.4 Fan-out
`server/events.py:Broadcaster.publish(event)` is thread-safe (`call_soon_threadsafe`), subscribers are asyncio queues (maxsize 200, drops on full) (`server/events.py:11-41`); `GET /ws` streams them (`server/api.py:1428-1439`). Event types published by the worker: `progress`, `job` (`job_id, job_type, status, title, remaining, video_id?, clips?, error?`), `queue` (`server/jobs.py:131-134, 165-166, 368-396`). Webhook: when `payload["webhook_url"]` is set, `webhooks.deliver(url, webhooks.body_for(event), secret)` on every terminal state (`:398-413`).

Tests: `tests/test_job_progress.py` (weights, monotonic, ETA, render label).

---

## 6. Errors

- A stage failure is an exception that escapes `process_video`; the worker converts it into job `failed` with the scrubbed message (`server/jobs.py:303-313`; `core/scrub.py:scrub_secrets`).
- Per-clip render failure is caught in `_finish` (`core/pipeline.py:609-627`): `_render_failure_reason(e)` (`:34-63`) gives one line (the last meaningful FFmpeg line, or an out-of-memory hint); identical repeats are counted and printed once (`:659-660`); the job continues and the clip is simply absent.
- Mode precondition: `modes.NotVerticalError(width, height)` before any work for a Vertical Live job on a non-9:16 source (`core/pipeline.py:217-220`, `core/modes.py:36-44`); the API's retry can `drop` the option (`core/queue.py:225-231`).
- `_cached_or_download` raises `ValueError` for an unreadable half-imported local copy (`core/pipeline.py:1030-1041`).
- Background passes (signals, hype, game sounds, sport prepass, creator learning, gaming prepare, remote render) are all best-effort: each catches `Exception`, prints, and the run continues (`core/pipeline.py:315-384, 553-577, 785-830, 896-914`).
- The LLM backend is created once per run (`core/pipeline.py:431`) via `create_backend(_with_usable_model(config["llm"]))`; a missing local model is swapped for an installed one (`:141-172`).
- Run outcome (`core/outcome.py`): `summarise_run(candidates, rejections, config) -> {clips, candidates, best_score, min_score, rejected: {reason: n}, measured, nothing_detected, cause}` with `cause` in `no_candidates | no_people | duplicates | below_threshold | None` (`core/outcome.py:35-96`); `explain_no_clips(out) -> str` (`:99-118`). Stored by `db.set_outcome(video_id, outcome)` (`core/pipeline.py:465-473`) with optional `outcome["intent"]` and `outcome["sport"]`. Tests: `tests/test_outcome.py`.

---

## 7. The stage sequence inside `process_video(url, config, db, force=False) -> list[RenderedClip]` (`core/pipeline.py:192-682`)

| # | Step | Function(s) | Input -> output | Line |
|---|---|---|---|---|
| 0 | reset end-card tally | `video.outro.reset_tally()` | | `:200` |
| 1 | download or reuse | `_cached_or_download(url, data_dir, db, vertical)` -> `sources.dispatch.download(url, dir, vertical)` or ffprobe on the cached file | `url` -> `DownloadedVideo` | `:206`, `:1010-1113` |
| 1b | H.264 conversion | `convert_slow_source(video, config)` -> `video.encoding.ensure_h264_source` | in place | `:210`, `:175-189` |
| 1c | mode preconditions | `modes.is_vertical_live` / `modes.sport` + `modes.probe_size`, `modes.orientation` | may raise `NotVerticalError`; a vertical sports source gets `clips.vertical_live = True` | `:214-229` |
| 1d | bookkeeping | `cancel.clear`, `db.upsert_video(video_id, title, channel_name, duration)`, `db.set_video_source`, `db.set_video_games`, creator tagging (`creator.identity.tag_video`, `retrieval.context_for`, `learning.preferences`), branding default, gaming layout memory | | `:231-299` |
| 1e | already-done guard | `db.shorts_made(video_id) and not force` -> `return []` | | `:301-303` |
| | status | `db.set_video_status(vid, "downloaded")`; `cancel.check` | | `:304-305` |
| 2a | background prepasses (threads) | `_extract_signals` (`analysis.audio_features.extract_audio_features(path)`, `analysis.visual_features.extract_visual_features(path)`), `_fetch_hype` (`analysis.hype.audience_signals(url, vid, duration)` -> curve, messages; `sources.dispatch.game_info`), `_listen` (gaming: `analysis.game_audio.listen(path, sound_groups)`), `MatchReading(config, video)` (sports: sound + `sports.prepass`) | `signals_out`, `hype_out`, `sounds_out`, `match` | `:315-387` |
| 2b | transcribe | `transcription.transcriber.transcribe(video.path, video_id, data_dir/"transcripts", model_size=config["whisper"]["model"], device=config["whisper"]["device"], language, online=online_transcription(config), hotwords?)`; `detected_language(vid, dir)` | `Path` -> `list[Segment]`, `content_lang` | `:390-419` |
| | status | `db.set_video_status(vid, "transcribed")`; `cancel.check` | | `:420-422` |
| 3a | join prepasses | `cancel.wait(signals_thread)`, `cancel.wait(hype_thread, 60)`, `cancel.wait(sounds_thread, 900)` | | `:428-431` |
| 3b | LLM | `llm = create_backend(_with_usable_model(config["llm"]))` | `LLMBackend` | `:431` |
| 3c | mode inputs | `match.finish(hype_out)` -> `(sport_profile, chat, sounds)`; or `_gaming_scoring_inputs(...)` -> `(gaming_profile, chat, sounds)` | | `:432-437`, `:832-931` |
| 3d | direction | `clip_direction(config, llm, duration)` -> `analysis.intent.ClipIntent | None` | | `:438`, `:121-138` |
| 3e | DETECTION | `analysis.fusion.find_clips(video.path, segments, llm, config, signals=, creator_context=, weight_bias=, audience=, measure_reaction=, gaming=, chat=, sounds=, sport=, intent=) -> (list[ClipCandidate], list[Rejection])` | `Segment[]` + signals -> `ClipCandidate[]` | `:439-451`; signature `analysis/fusion.py:54-69` |
| 3f | audit | `db.log_rejection(vid, start, end, score, reason, kept_start, kept_end, subscores)` per rejection | rows in `rejections` | `:452-459` |
| | status + outcome | `db.set_video_status(vid, "analyzed")`; `summarise_run`; `db.set_outcome`; if no candidates -> `done`, `return []` | | `:463-482` |
| 4a | clip folder | `clip_dir = data_dir/"clips"/_safe_name(channel)/f"{_safe_name(title)} [{video_id}]"` | | `:501-505` |
| 4b | metadata | `analysis.metadata.generate_metadata_batch(candidates, segments, video.title, llm, creator_context=, style=, rules=) -> list[ClipMetadata]`; sports `check_titles`; `required_hashtags` appended | `ClipCandidate[]` -> `ClipMetadata[]` (zipped by index) | `:511-552` |
| 4c | creator learning (thread) | `creator.extractor.extract_and_store(kdb, creator_id, vid, segments, llm)` | | `:557-577` |
| 4d | render pool | `workers = config["video"]["parallel_renders"]`; `_share_the_cpu(workers)` if `modes.needs_framing`; `gaming_opts = _gaming_prepare(...)` if gaming | | `:584-598` |
| 4e | RENDER | `ThreadPoolExecutor.submit(_render_files, video.path, candidate, segments, clip_dir, config, _clip_opts(meta), content_lang)` per candidate; or `remote.render_all(...)` | `(ClipCandidate, Segment[], opts)` -> `(final_path, render_opts_json)` | `:635-657`; `_render_files` `:1123-1482` |
| 4f | register | `_finish` -> `_register_clip(db, vid, candidate, final_path, meta, render_opts_json, config) -> RenderedClip | None` | clip row | `:609-631`, `:1563-1635` |
| 4g | sports reels | `_sport_reels(db, vid, sport_profile, made, clip_dir, config, segments)` | more clip rows | `:668-669`, `:685-754` |
| 5 | finish | `db.set_process_seconds`, `db.set_video_status(vid, "done")`, `progress.emit(stage="done")` | `list[RenderedClip]` | `:671-682` |

Inside `find_clips` (`analysis/fusion.py`): 1. signals normalised by percentile (`:95-110`); 2. gaming/sport evidence curves (`:117-`); candidate pools (LLM chunks via `analysis.highlights`, signal peaks `_signal_peak_windows`); fusion `_fuse(c, weights, reaction, speech_ratio, game)` (`:636-679`); 4. `highlights._select_unique(candidates, segments, min_score, max_clips, max_overlap, max_text_similarity, max_segment_reuse, priority?, distinct?)` (`:571-580`); 4b. `_look_at_game` (`:587-593`); 5. `_rerank` in `rerank_pool` batches (`:605-624`); cap -> `over_limit` rejections; `return kept, rejections` (`:627-629`).

---

## 8. Mode branches (standard, gaming, sports, podcast, vertical live, longform)

Modes are flags in `config["clips"]` read through `core/modes.py` predicates: `is_vertical_live(cfg_or_opts)` (`core/modes.py:46-54`), `is_gaming` (`:57-66`), `gaming_scoring` (`:69-79`), `sport(cfg_or_opts) -> str|None` (`:82-94`), `needs_framing(config)` (`:171-174`), `measures_reaction(config)` (`:177-184`). Each predicate accepts either a job config (looks in `["clips"]`) or a clip's `render_opts` dict, which is how re-renders keep their mode.

| Mode | Set by | Detection branch (`core/pipeline.py`) | Render branch (`_render_files`) |
|---|---|---|---|
| Standard (Shorts, 9:16, tracked) | default | `find_clips` with no profile; `measure_reaction=True` | `cut_clip` -> `compute_tracking(intermediate, model_name=config["tracking"]["detector"], sample_fps=...)` -> `render_vertical(intermediate, tracking, render_path, ass_path, vf_extra, normalize)` (`:1316-1397`) |
| Gaming / Reaction | `payload.gaming` -> `clips.gaming` (+ `gaming_layout`, `gaming_remember`) (`server/jobs.py:208-215`) | `gaming_scoring = modes.gaming_scoring(config)` (`:337`); `_listen` thread for game sounds (`:364-383`); `_gaming_scoring_inputs` -> `analysis.gaming.profile_for`, `chat_signal`, `sound_signal` (`:435-437`, `:832-869`); `measure_reaction=False` (`:445`) | `gaming_opts = _gaming_prepare(...)` once per video (`:598`, `:803-814`) -> `gaming.run.prepare(source, candidates, config, clip_dir)`; per clip `_try_gaming_render` -> `gaming.run.render(intermediate, output, g, config, ass_path, vf_extra, normalize) -> dict|None`; `None` falls through to the standard tracker (`:1331-1335`, `:817-829`) |
| Gaming scoring only | `payload.gaming_scoring` -> `clips.gaming_scoring` (`server/jobs.py:201-204`) | same scoring as above | standard or vertical-live render |
| Sports | `payload.sport` -> `clips.sport` (`{name, highlights, period, teams,...}` cleaned by `sports.clean`) (`server/jobs.py:222-224`; `server/api.py:480-486`); Teams/players become `clips.focus` (`server/jobs.py:230-239`) | vertical source -> `clips.vertical_live = True` (`:223-229`); `MatchReading` prepass (`:387`, `:872-930`); hotwords `_listening_for` (`:395-400`); `match.finish` -> `_sport_inputs` -> `sports.profile_for(config, video)` (`:433-434`, `:933-992`); `find_clips(sport=...)`; `title_rules`/`check_titles` (`:513-528`); `outcome["sport"]` (`:469-471`) | `_sport_framing(intermediate, config, sport_name)` -> `sports.framing(name, clip_path, config)` replaces face tracking (`:1365-1374`, `:1550-1560`); reels `_sport_reels` (`:668-669`) |
| Podcast | `payload.podcast` -> `clips.podcast = True` (`server/jobs.py:197-199`) | no scoring change (inferred: nothing in `process_video` reads `podcast`) | `podcast = opts.podcast or clips.podcast` (`:1180`); `video.podcast.analyze(intermediate, model_name, sample_fps) -> decision`; editor crop overrides; `podcast_mod.render_clip(intermediate, render_path, decision, ass_path, vf_extra, normalize)` (`:1336-1359`) |
| Vertical Live | `payload.vertical_live` -> `clips.vertical_live = True` (`server/jobs.py:200`); own download copy `sources.vertical.SUFFIX` (`:1019-1024`; `core/prefetch.py:62-68`) | `NotVerticalError` precheck (`:217-220`); scoring unchanged; `needs_framing` false skips `_share_the_cpu` (`:585-588`) | `vertical_live` (`:1187`) takes the single-encode branch: `modes.fit_filter(*probe_size)` + `cut_clip(source, padded, render_path, ass_path, vf_extra, normalize)` (`:1217-1221`, `:1399-1413`); persisted as `render_opts.vertical_live = True` (`:1470-1474`) |
| Longform (16:9) | `payload.longform = {"mode": short_clips|clips_140|highlights|edited_stream, "shorts"?}` (`server/api.py:50`) | separate entry `longform.process.process_longform(url, config, db, options)` (`longform/process.py:28`) reusing `_cached_or_download`, `convert_slow_source`, `MatchReading`, `transcribe`, `clip_direction`, `find_clips` with profile durations (`:62-128`), `_render_files` with `render_opts={"profile": mode}` (`:150-190`), `_register_clip`; `highlights`/`edited_stream` use `longform.assemble` (`:131-133`, `:231-365`) | `landscape = bool(opts.get("profile"))` -> canvas 1920x1080, scale+pad filter, no tracking (`:1173`, `:1201-1208`, `:1399-1413`) |

Both-formats job: `Worker._both_formats` runs `process_video` then `process_longform` with progress spans 0-0.5 / 0.5-1 (`server/jobs.py:756-775`).

---

## 9. State and storage

### 9.1 Files on disk (under `paths.data_dir`, resolved by `core/paths.py:resolve_data_dir`: absolute as given; relative -> repo root in a checkout, `%LOCALAPPDATA%/Clips Studio/<raw>` when frozen; `core/paths.py:17-50`)

| Path | Written by | Notes |
|---|---|---|
| `data/state.db` | `StateDB(data_dir / "state.db")` (`server/jobs.py:46`, `main.py:201`) | SQLite, WAL, foreign keys on (`core/state.py:356-364`) |
| `data/downloads/<video_id>.mp4` (or `<video_id><SUFFIX>.mp4` for Vertical Live) | `sources.dispatch.download` / prefetch | reused by `cached_source` (`core/pipeline.py:1019-1026`); re-render source path `data/downloads/{video_id}.mp4` (`server/jobs.py:789`) |
| `data/transcripts/<video_id>.json` | `transcribe()` (`transcription/transcriber.py:244-256`) | `{"video_id", "language", "segments": [vars(Segment)]}`; read back by `detected_language`, `_rerender_clip`, `_caption_lines_for` |
| `data/clips/<channel>/<title> [<video_id>]/clip_SSSSS-EEEEE.mp4` | `_render_files` (`core/pipeline.py:501-505`, `:1150-1152`) | stem `clip_{int(start):05d}-{int(end):05d}`; scratch `*.source.mp4`, `*.edited.mp4`, `*.plain.mp4`, `*.pre-card.mp4`, `*.ass`, `*.card.png` are discarded (`:1309-1424`); longform adds `/<profile subdir>` (`longform/process.py:146-150`) |
| `data/logs/job_<id>.log` | `feedback.open_job_log` (`server/jobs.py:159-164`) | see 9.3 |
| `data/branding/assets/` | watermark images (`core/pipeline.py:1288`) | |
| `data/remote_render/pieces/` | remote rendering (`remote_render/dispatch.py:82`) | |
| `data/voices/`, `data/youtube_token.json` | multilingual, uploads (`server/jobs.py:466`, `core/scheduler.py:126`) | |

### 9.2 Schema (core/state.py `SCHEMA` + `_migrate()`)

`videos` (`core/state.py:15-22` + migrations `:385-409, 447-450`): `video_id TEXT PK`, `channel_id`, `title`, `status TEXT DEFAULT 'queued'`, `created_at`, `updated_at`, `channel_name`, `process_seconds REAL`, `creator_id INTEGER`, `duration REAL`, `source_url`, `source_platform`, `games TEXT` (JSON list), `outcome TEXT` (JSON).

`clips` (`:24-36` + `:368-372`): `id INTEGER PK`, `video_id REFERENCES videos`, `start_s REAL`, `end_s REAL`, `score INTEGER`, `hook`, `path`, `status TEXT DEFAULT 'rendered'`, `scheduled_for`, `created_at`, `title`, `description`, `hashtags` (JSON list), `scores` (JSON subscores), `render_opts` (JSON), `exported_at`; `UNIQUE (video_id, start_s, end_s)` (start/end rounded to 2 dp at insert, `:938-942`).

`rejections` (`:38-48` + `:441-443`): `id`, `video_id`, `start_s`, `end_s`, `score`, `reason`, `kept_start_s`, `kept_end_s`, `created_at`, `subscores` (JSON).

`jobs` (`:62-70` + `:410-434`): `id INTEGER PK`, `type TEXT DEFAULT 'process'`, `payload TEXT` (JSON), `status TEXT DEFAULT 'queued'`, `error TEXT`, `created_at`, `updated_at`, `position INTEGER`, `video_id TEXT`, `title TEXT`, `interrupted INTEGER`, `started_at`, `finished_at`, `attempts INTEGER`, `log_path TEXT`; index `idx_jobs_queue(status, position, id)` (`:433-435`).

`app_state (key PK, value)` (`:251-254`): `queue_paused`, `watches_imported`, plan automation flags.

Other tables (names only; `:50-60, 78-330`): `uploads`, `channels`, `creators` (+ `default_branding_id`, `gaming_layout`), `platform_accounts`, `creator_knowledge`, `creator_events`, `clip_feedback`, `branding_profiles`, `creator_terms`, `clip_translations`, `publish_jobs`, `clip_publishes`, `streams`, `watches`, `watch_items`.

Migrations are additive `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info` (`:366-560`); no schema version number exists (inferred: no `user_version` or version table in `core/state.py`).

### 9.3 What a clip is
On disk: one MP4 at `clips.path`, 1080x1920 (standard/vertical-live/gaming/podcast/sports) or 1920x1080 (longform), captions burned from a temporary ASS, optional title card, image watermark and end card concatenated (`core/pipeline.py:1426-1446`).
In the DB: the `clips` row written by `_register_clip` (`core/pipeline.py:1582-1596`): `start_s, end_s, score, hook, path, status="queued", title, description, hashtags(json), scores(json subscores), render_opts(json)`. On a re-run the existing row is pointed at the fresh file and `_register_clip` returns `None` (`:1597-1633`).

`render_opts` keys written by `_render_files` (`core/pipeline.py:1456-1481`) and read back on re-render: `captions`, `caption_style`, `caption_lines`, `crop` (`track|center|letterbox|bias_left|bias_right`), `filter`, `adjust`, `edit` (`video_editor.timeline.EditList`), `watermark`, `headline`, `subline`, `speaker_edits`, `speaker_turns`, `normalize_audio`, `podcast`, `vertical_live`, `gaming` (dict), `sport`, `profile` (longform), `reel`.

### 9.4 The run log
`server/feedback.py`: `install_log_capture()` wraps `sys.stdout`/`sys.stderr` in `_Tee` (`:75-80`), called in `create_app` (`server/api.py:575`). `_Tee.write` appends each line to a 400-line ring (`_LOG_LINES`, `:36-37`) and, when a job sink is open, writes and flushes it to the file (`:47-69`). `open_job_log(path)` opens `data/logs/job_<id>.log` in append mode (`:83-104`); `close_job_log()` closes it (`:107-113`); `recent_log(lines)` returns the ring (`:116-119`). The pipeline "logs" by `print()`. `GET /jobs/{id}/log?tail=300` reads the file (`server/api.py:1181-1199`).

---

## 10. Settings: how configuration reaches the pipeline

- `main.py:load_config(path)` (`main.py:68-121`) reads `config/settings.yaml` (or the per-user copy when frozen, `core/paths.py:user_config_path`), normalises the flat quick-setup keys: `model` -> `llm.backend = "ollama/<tag>"` or `provider/model` (`:72-77`), `channel` -> `channels[]`, `auto_upload` -> `upload.enabled`, `privacy` -> `upload.privacy`; env overrides `CLIPS_STUDIO_OLLAMA_HOST` -> `llm.ollama_host`, `CLIPS_STUDIO_DATA_DIR` -> `paths.data_dir` (`:102-108`); then `paths.data_dir = str(resolve_data_dir(config))` and `llm.data_dir = paths.data_dir` (`:110-115`).
- Keys the pipeline reads directly (from `config/settings.yaml:35-150`): `llm.{backend, ollama_host, temperature, num_ctx, translation_model, data_dir}`; `whisper.{model, device}`; `transcription.{backend, model}`; `content_language`; `clips.{min_score, max_clips_per_video, min_duration, max_duration, vertical, captions, outro}` plus job-injected `clips.{podcast, vertical_live, gaming, gaming_layout, gaming_remember, gaming_scoring, sport, focus, filter, required_hashtags, caption_style, watermark}`; `scoring.{weights, signal_peak_percentile, creator_context_max, action_bonus, audience_bonus, rerank_pool, reaction_top_k, look_at_game}`; `analysis.{max_overlap, max_text_similarity, max_segment_reuse, chunk_seconds, chunk_overlap_seconds, long_video_threshold_seconds}`; `video.{encoder, parallel_renders}`; `tracking.{detector, sample_fps}`; `paths.data_dir`.
- Per-job merge (`server/jobs.py:189-269`): `cfg = copy.deepcopy(self.config)` then payload keys mapped onto `cfg["clips"]` (and `cfg["scoring"]["rerank_pool"]`): `podcast`, `vertical_live`, `gaming_scoring`, `gaming` (+`gaming_layout`, `gaming_remember`), `captions`, `min_score`, `sport`, `focus` (+ sports direction), `long_clips` -> `min_duration=61, max_duration=180`, `filter`, `hashtags` -> `required_hashtags`, `max_clips` -> `max_clips_per_video` and `rerank_pool`, `caption_style`, `watermark_profile_id` -> `clips.watermark` (branding row). `JobIn` (`server/api.py:39-64`) and `_process_options` (`:441-`) define the HTTP side; `max_clips` is clamped to 1..10 (`:450`).
- Remote rendering ships only `CONFIG_SECTIONS = ("clips", "tracking", "video")` to a render worker (`remote_render/protocol.py:19-24`), which is the allowlist of config a render needs.

---

## 11. Model dependency resolution at run time

| Dependency | Setting | Resolved where | Fallbacks |
|---|---|---|---|
| Whisper | `whisper.model` (`auto` or a size), `whisper.device` (`auto|cuda|cpu`) | `transcription/transcriber.py:_load_model` (`:90-131`): `auto` -> `large-v3-turbo` then `small` on CUDA, `small` on CPU; weights path via `core.binaries.whisper_model(size)` (bundled dir or bare name for HF download, `core/binaries.py:115-128`) | GPU failure at first encode -> retried on CPU (`transcription/transcriber.py:233-242`); cloud STT when `transcription.backend != local` (`:221-226`, `core/pipeline.py:109-118`) |
| LLM | `llm.backend` (`ollama/<tag>` or `provider/model`), `llm.ollama_host` | `llm.registry.create_backend(llm_config)` (`llm/registry.py:14-`); `_with_usable_model` swaps a missing local tag for an installed one via `llm.manager.resolve_usable_model` (`core/pipeline.py:141-172`) | cloud providers via `llm/providers`, signed-in plans via `llm/signin` |
| YOLO pose weights | `tracking.detector` (`yolov8n-pose.pt`), `tracking.sample_fps` | `video/tracker.py:_get_model` (`:94-108`) -> `core.binaries.yolo_weights(name)` (bundled `weights/` dir, repo root, or bare name for ultralytics download, `core/binaries.py:131-164`); loaded once per process and shared across render threads (`video/tracker.py:82-83`); device from `core.gpu.torch_device()` (`:1298-1305`) | |
| PANNs sound tagger | none (optional) | `analysis.panns.available()` (`core/pipeline.py:371-373`, `:900-902`) | scored without it |
| FFmpeg/ffprobe | none | `core.binaries.ffmpeg()/ffprobe()` (bundled or PATH) | preflight blocks when absent (`core/preflight.py:71-86`) |

---

## 12. Resource handling

- `core/gpu.py:cuda_usable() -> (bool, reason)` checks that the PyTorch build has a kernel for the device's compute capability, not merely `torch.cuda.is_available()` (`core/gpu.py:72-121`); `torch_device() -> "cuda"|"cpu"` is cached per process (`:124-142`). `NO_GPU = "no CUDA GPU detected"`, `gpu_too_old(reason)` (`:30-43`).
- VRAM: only reported, in `core/preflight.py:check_gpu` (`:256-263`: `torch.cuda.get_device_properties(0).total_memory`); no VRAM-based gating in the pipeline (inferred: no other reader of `total_memory` in core/).
- Preflight (`core/preflight.py:run(config) -> Preflight`): checks `ffmpeg`, `ffprobe`, `ollama` + `model` (or `ai` for cloud), `whisper` (or `transcription` for cloud), `gpu`, `disk` (`MIN_FREE_GB = 20`); `Check(name, ok, detail, fix, blocking)`; `Preflight.ready` ignores non-blocking checks (`:30-52`, `:306-331`). Exposed at `GET /health/preflight` (`server/api.py:718`).
- CPU sharing: `_share_the_cpu(workers)` sets OpenCV and torch threads to `(cores-2)//workers`, latched once per process (`core/pipeline.py:66-106`).
- Concurrency: one worker thread; renders in a `ThreadPoolExecutor(max_workers=video.parallel_renders)` (`:584`, `:637`); SQLite writes stay on the worker thread (`:580-582`); prefetch of the next download in one background thread (`core/prefetch.py`, join timeout 20 min, `:23-25`, `:76-105`).
- Housekeeping: `core/housekeeping.py:survey(db, data_dir)`, `clean(db, data_dir)`, `orphan_clip_dirs` (`:33, :102, :129`); endpoints `GET /storage`, `POST /storage/cleanup` (`server/api.py:1242, 1324`).

---

## 13. The natural extension seam: detection vs rendering

Yes, the separation exists and is clean, and it is already crossed by two other callers (longform and remote rendering), which is the strongest evidence it is a stable boundary.

**Detection output** (what exists right before rendering), at `core/pipeline.py:439-451`:
```
candidates: list[ClipCandidate]   # kept, ordered best first (analysis/fusion.py:627-629)
rejections: list[Rejection]       # audit only
```
Each `ClipCandidate`: `start`, `end` (seconds in the source), `score` (0-100 int), `hook` (str), `reason` (str), `source` (`"transcript"|"signal"`), `engagement`, `trending`, `subscores` (dict). Then, still before any file work, `metas: list[ClipMetadata]` is produced in lockstep (`:514-519`): `title`, `description`, `hashtags`, `headline`, `subline`.

**Renderer input**, `_render_files(source: Path, candidate: ClipCandidate, segments: list[Segment], clip_dir: Path, config: dict, render_opts: dict | None = None, content_language: str = "en") -> tuple[Path, str]` (`core/pipeline.py:1123-1131`). Its contract, stated in its docstring: "Pure file work: cut, track, crop, captions, color. NO database access and NO LLM call" (`:1132-1139`). It needs:
- the source file and the candidate window;
- `segments` only for captions (words inside the window) and speaker turns;
- `config["clips"]` (`vertical, captions, caption_style, filter, watermark, podcast, vertical_live, gaming, sport, outro`), `config["tracking"]`, `config["video"]`, `config["paths"]["data_dir"]` (for branding assets). `remote_render/protocol.py:CONFIG_SECTIONS = ("clips", "tracking", "video")` confirms the allowlist (`:19-24`);
- `render_opts` (section 9.3 keys);
- `content_language` for caption fonts.
It returns the finished MP4 path and the `render_opts` JSON to persist.

**Registration**, `_register_clip(db, video_id, candidate, final_path, meta, render_opts_json, config) -> RenderedClip | None` (`:1563-1572`), is the single funnel every mode passes through ("a mode added later cannot miss it", `:1574-1581`).

**The remote-render precedent** shows what a renderer plugin would receive: `RemoteRenderer.render_all(video_id, source, items=[(candidate, meta)], segments, clip_dir, config, render_opts, language, workers, local=None, opts_for=None)` yields `(candidate, meta, get_result)` where `get_result() -> (clip_path, render_opts_json)` or raises (`remote_render/dispatch.py:5-12`, `:90-100`). It serialises a candidate with `dataclasses.asdict` minus `subscores` (`:51-55`), the segments within +-2 s of the window (`:58-65`), and `render_config(config)` (`protocol.py:22-24`). The worker side rebuilds `ClipCandidate(**c)` and `Segment(...)` and calls `_render_files` on the piece (`remote_render/worker.py:366-380`).

**Where a detection plugin would plug in**: `find_clips` is one call with one return shape (`analysis/fusion.py:54-69`); every mode (gaming, sports, intent) enters it as optional keyword profiles rather than as separate code paths, and longform calls the same function with different durations (`longform/process.py:116-121`). The three mode hooks are themselves objects with a small duck-typed surface: a sport profile exposes `weights`, `genre_track`, `sound_weights`, `sound_curves`, `curves`, `board`, `option`, `title_rules`, `check_titles`, `report_data`, `look`, `one_play_per_clip` (`core/pipeline.py:933-992`, `:513-528`; `analysis/fusion.py:77-86, 540-593`); a gaming profile exposes `weights`, `genre_track` (`core/pipeline.py:851-866`).

**What is NOT separated**: `process_video` is one 490-line function; stage boundaries are marked by `db.set_video_status` and `progress.emit` calls, not by returning intermediate objects, so a plugin cannot today replace only "download" or only "transcribe" without editing this function (`core/pipeline.py:192-682`). Rendering options are passed as an untyped `dict` whose keys are documented only in comments (`:1136-1139`, `:1456-1481`).

---

## 14. Reachability today

| Capability | Python import | HTTP | CLI | MCP (`server/mcp.py`) |
|---|---|---|---|---|
| Run the full pipeline on one URL | `core.pipeline.process_video(url, config, db, force)` | `POST /jobs` (`server/api.py:803`), `POST /jobs/batch` (`:1118`), `POST /videos/local` (`:867`) | `python main.py process <url> [--force]` (`main.py:149, 203`) | `queue_video`, `queue_local_file` (`server/mcp.py:953, 1098`) |
| Queue control | `core.queue.*` | `GET /queue`, `POST /queue/pause|resume|clear`, `POST /jobs/{id}/move|retry`, `DELETE /jobs/{id}`, `GET /jobs`, `GET /jobs/{id}` (`:977-1118`) | | `queue_status`, `job_status` (`:1150, 1139`) |
| Progress | `core.progress.set_handler` | `GET /ws` events; `GET /jobs/{id}` (snapshot, inferred from `progress_snapshot`); webhooks (`payload.webhook_url`) | stdout | |
| Cancel | `core.cancel.request_cancel(video_id)` | `POST /cancel` (`:1201`) | Ctrl+C | |
| Job log | `server.feedback.recent_log` | `GET /jobs/{id}/log` (`:1181`) | file | |
| Detection only | `analysis.fusion.find_clips(...)` | not reachable | not reachable | |
| Render one clip | `core.pipeline._render_files(...)` (private name) | `POST /clips/{id}/render` -> `render` job (`:1679`); `POST /clips/{id}/preview` (`:1705`) | `python main.py render-worker` (remote rendering, `main.py:193`) | |
| Register a clip | `core.pipeline._register_clip(...)` (private) | not reachable | | |
| Transcribe | `transcription.transcriber.transcribe(...)` | not reachable (transcript readable via `GET /clips/{id}/captions`, `/clips/{id}/words`) | | `clip_captions` |
| Preflight | `core.preflight.run(config)` | `GET /health/preflight` (`:718`) | | `engine_status` |
| Outcome | `core.outcome.summarise_run`, `StateDB.get_outcome` | inside `GET /videos/{id}/clips` (inferred) | | |
| State DB | `core.state.StateDB` | `GET /videos`, `GET /videos/{id}/clips`, `GET /media/{clip_id}` | `python main.py status` | `list_videos`, `list_clips` |
| Settings | `main.load_config` | `GET /settings` (`:2939`) | `config/settings.yaml` | |

Existing docs: `docs/API.md` (the HTTP surface, with a "Supported and internal" section at `docs/API.md:127`), `docs/EXTENDING.md` (add a language / AI model / platform / export format; it says "The whole pipeline is already reachable over HTTP", `docs/EXTENDING.md:9-11`), `ARCHITECTURE.md` sections 3, 4, 10.1-10.3, 12, 13.

---

## 15. Open questions for the platform design

1. Should the plugin contract be at `find_clips` (replace detection wholesale) or at the profile objects it already accepts (`gaming=`, `sport=`, `intent=`), which are duck-typed and undocumented (`analysis/fusion.py:63-68`)?
2. `_render_files` and `_register_clip` are underscore-private but are imported by `longform/process.py:36-38`, `server/jobs.py:783`, `remote_render/worker.py:366`. Promoting them to public names is a rename, not a redesign; is that in scope?
3. `render_opts` has no schema (dict with ~20 keys, `core/pipeline.py:1456-1481`); the API cleans only `gaming` (`server/api.py:1692`, `core/modes.py:clean_gaming`). A plugin writing render options needs a typed contract.
4. No schema version in `core/state.py`; plugins that need their own tables have no migration hook (inferred).
5. Progress stage names are a closed set folded by `server/jobs.py:_STAGES` and mirrored in the UI; a plugin stage would need a registration path or it is silently ignored (`server/jobs.py:325-326`).
6. The worker holds one config dict and deep-copies per job (`server/jobs.py:189-196`); plugin settings would ride in `jobs.payload` (per job) or `settings.yaml` (global); which?
7. Cancellation inside a plugin stage requires calling `cancel.check_active()` in its loops (`core/cancel.py:55-78`); the SDK must expose this or wrap plugin calls in `cancel.run`/`cancel.wait`.
