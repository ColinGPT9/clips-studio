# Repo map: the local HTTP API, endpoint by endpoint

Branch `claude/open-platform-w4eh9g` (== main). Read-only survey. Every claim cites a repo-relative path; `(inferred)` marks anything not seen directly.

## 0. Method and headline numbers

- The live route dump (`from server.api import create_app`) could **not** run here: `fastapi` is not installed in this environment (`ModuleNotFoundError: No module named 'fastapi'` at `server/api.py:20`). The route list below therefore comes from grepping every `@app.(get|post|put|patch|delete|websocket)(` decorator; there are no `APIRouter`s anywhere (grep for `@router.` returned nothing) — every sub-module registers directly on the one `FastAPI` instance through an `install(app, ...)` function.
- Route decorator counts: `server/api.py` 86 (85 HTTP + 1 WebSocket), `server/youtube_api.py` 20, `server/ai_api.py` 17, `server/uploadpost_api.py` 13, `server/woopsocial_api.py` 13, `server/automation.py` 13, `remote_render/service.py` 9, `server/integrations.py` 5 → **176 routes on one app** (175 HTTP + `/ws`). `docs/API.md:11` says "86 HTTP endpoints and a WebSocket", which only counts `server/api.py` and is stale.
- `remote_render/gateway.py:50` builds a **second, separate** FastAPI app (`Clips Kitty render gateway`, `docs_url=None`, HTTPS, bearer-authenticated, 10 `/v1/...` routes at `remote_render/gateway.py:72-192`). It is not part of the local API and is listed only in section 8.9.
- FastAPI's auto docs are on: `create_app` passes only `title`/`version` (`server/api.py:576`), so `/docs`, `/redoc`, `/openapi.json` are served (documented at `docs/API.md:60-67`). The UI links to `/docs` from `ui/src/renderer/src/components/AICard.tsx:683`, `RemoteRenderCard.tsx:6`, `ui/src/main/index.ts:507-508`.

## 1. How the app is created (`create_app`)

`def create_app(config: dict, settings_path: Path) -> FastAPI` — `server/api.py:572`. Called once from `main.py:277` (`uvicorn.run(create_app(config, args.config), host=args.host, port=args.port)`).

| Step | What | Where |
|---|---|---|
| Log capture | `feedback_mod.install_log_capture()` redirects pipeline `print`s into the bug-report ring + per-job log | `server/api.py:575`, `server/feedback.py:75` |
| App | `FastAPI(title="Clips Kitty API", version="0.1")` | `server/api.py:576` |
| CORS | `CORSMiddleware(allow_origins=["http://localhost:5173","http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])` — Vite dev server only | `server/api.py:577-582` |
| Host check | `TrustedHostMiddleware(allowed_hosts=["127.0.0.1","localhost"])` — any other `Host:` header → 400; the comment says this is the DNS-rebinding guard and "there is no auth" | `server/api.py:583-589` |
| Auth | **None.** No token, no origin check beyond Host. Stated in `server/api.py:583-586`, `main.py:164-167`, `docs/API.md:70-95` |
| Version | `app_version = feedback_mod._app_version().get("app","?")` read once from `ui/package.json` (or `_MEIPASS/package.json` when frozen) | `server/api.py:593`, `server/feedback.py:260-297` |
| Data dir / DB | `data_dir = Path(config["paths"]["data_dir"]).resolve()`; `db_path = data_dir/"state.db"`; `def db() -> StateDB` makes a per-request sqlite connection | `server/api.py:595-602` |
| Worker | `worker = Worker(config)` (pipeline thread, `server/jobs.py:49`) | `server/api.py:597` |
| Publish worker | `publish_worker = PublishWorker(config, db_path, data_dir)` (YouTube uploads thread) | `server/api.py:609`, `server/publisher.py` |
| Startup | `set_exception_handler(_quiet_connection_resets)`; `broadcaster.attach_loop(loop)`; `worker.start()`; `publish_worker.start()`; `stream_watcher.start()`; `channel_watcher.start()` | `server/api.py:611-618` |
| Shutdown | stops the same four | `server/api.py:620-625` |
| Sub-routers (install order) | `youtube_api.install(app, config, db, data_dir, worker, publish_worker)` → `uploadpost_api.install(...)` → `remote_render_service.install(app, config, data_dir)` → `woopsocial_api.install(...)` → `ai_api.install(app, config, db, data_dir, settings_path)` → `stream_watcher = integrations.install(app, db, worker, broadcaster)` → `channel_watcher = automation.install(app, db, worker, broadcaster, options_from=_watch_options, data_dir, interval_minutes=config["poll_interval_minutes"] or 15)` | `server/api.py:631-709` |
| `_watch_options` | validates a watch's option dict through `JobPatch(**raw)` + `_process_options` so a watch "cannot hold a setting the queue would refuse" | `server/api.py:689-697` |
| Ollama host | `ollama_host = config["llm"].get("ollama_host", "http://localhost:11434")` (closure var used by `/models*`, `/agent*`) | `server/api.py:2809` |
| Return | `return app` | `server/api.py:3019` |

Pattern worth copying for an SDK/plugin host: every optional feature is a module exposing `install(app, *, config, db, data_dir, ...)` that registers closures on the app and optionally returns a thread to start/stop (`server/youtube_api.py:148`, `server/uploadpost_api.py:63`, `server/woopsocial_api.py:39`, `server/ai_api.py` (install near line 120, inferred from test usage `tests/test_ai_api.py:55`), `server/integrations.py:210`, `server/automation.py:955`, `remote_render/service.py:148`). The docstrings state the rule "a switched-off feature 404s and `/<feature>/status` returns `{"enabled": false}`" (`server/youtube_api.py:10-12`, `server/uploadpost_api.py:6-9`, `server/woopsocial_api.py:9-11`).

## 2. Serving: host, port, process

- `python main.py serve --port 8765 --host 127.0.0.1` — `main.py:162-172`. Default host is loopback "by DEFAULT and on purpose: this API has no authentication"; a non-loopback host prints `WARNING: binding {host} — this API has no authentication.` (`main.py:275-276`).
- Electron spawns `api.exe serve --port 8765` (packaged) or `python main.py serve --port 8765` (dev) — `ui/src/main/index.ts:19,228,235`. Renderer base URL: `export const API_BASE = 'http://127.0.0.1:8765'` (`ui/src/renderer/src/lib/api.ts:49`); WebSocket `ws://127.0.0.1:8765/ws` (`ui/src/renderer/src/lib/useEvents.ts:4`).
- Docker: `CMD ["python","main.py","serve","--port","8765","--host","0.0.0.0"]` (`Dockerfile:52`), compose publishes `127.0.0.1:8765:8765` only (`docker-compose.yml:20`).
- Env overrides read by `load_config`: `CLIPS_STUDIO_OLLAMA_HOST`, `CLIPS_STUDIO_DATA_DIR` (`main.py:102-108`). The MCP client reads `CLIPS_STUDIO_API` (`server/mcp.py:52-55`).
- `python main.py mcp` runs the MCP stdio server, which talks to the running engine over HTTP, not the DB (`main.py:280-288`).
- Other CLI commands that bypass the API entirely and call the pipeline in-process: `process <url> [--force]` → `core.pipeline.process_video` (`main.py:203-209`), `run` → `core.scheduler.run_daemon` (`main.py:211`), `status`, `outro-backfill`, `auth`, `upload`, `render-worker`, `channels`, `models` (`main.py:149-196`).

## 3. API version and app version

- `API_VERSION = 1` — `server/api.py:34`, comment: "Bumped only when a supported endpoint changes shape, never for additions." Returned by `GET /health` (`server/api.py:716`). `server/integrations.py:10-11` repeats the rule ("an incompatible change bumps API_VERSION in server/api.py"). `CHANGELOG.md:659` records when `app_version`/`api_version` were added. The MCP `engine_status` tool prints it (`server/mcp.py:444`). No test asserts the value (grep over `tests/` found no `API_VERSION`).
- `app_version` comes from `ui/package.json` `version` (`server/feedback.py:260-297`).
- The FastAPI `version="0.1"` string (`server/api.py:576`) is independent of both and appears only in `/openapi.json` (inferred).

## 4. Request models in `server/api.py` (the SDK-relevant shapes)

### 4.1 `JobIn` (`server/api.py:38-66`) — body of `POST /jobs`

| Field | Type | Meaning (from the comments) |
|---|---|---|
| `url` | `str` (required) | YouTube/Twitch/Kick link; `local:<vid>` is produced internally for imports |
| `force` | `bool=False` | reprocess even if done |
| `max_clips` | `int\|None` | clamped 1..10 in `_process_options` (`server/api.py:447-448`) |
| `caption_style` | `dict\|None` | font/size/colour/position/`words_per_caption`/`second_speaker`… |
| `captions` | `bool\|None` | burn captions (default true via config) |
| `long_clips` | `bool\|None` | 61–180 s clips |
| `filter` | `str\|None` | colour preset name, validated by `video.filters.is_valid` (`server/api.py:508-512`) |
| `min_score` | `int\|None` | clamped 0..100 (`server/api.py:514`) |
| `focus` | `str\|None` | whitespace-normalised, truncated to `analysis.intent.MAX_CHARS` (`server/api.py:492-499`) |
| `sport` | `dict\|str\|None` | validated by `sports.clean` (`server/api.py:482-490`) |
| `longform` | `dict\|None` | `{"mode": short_clips\|clips_140\|highlights\|edited_stream, "shorts"?: bool}` |
| `watermark_profile_id` | `int\|None` | branding profile id |
| `podcast`, `vertical_live`, `gaming`, `gaming_scoring`, `gaming_remember` | `bool\|None` | layout/scoring modes |
| `gaming_layout` | `dict\|None` | validated by `core.modes.clean_gaming`, stamped `"by":"user"` (`server/api.py:465-472`) |
| `webhook_url`, `webhook_secret` | `str\|None` | validated by `server.webhooks.is_deliverable` (`server/api.py:519-528`) |
| `hashtags` | `list[str]\|None` | required tags on every clip |
| `then` | `dict\|None` | post-job action, e.g. `{"action":"publish","platforms":["youtube"]}` (`server/api.py:61-65`) |

`JobPatch` (`server/api.py:69-95`) = same minus `url`/`force`/`hashtags`/`then`, plus `clear: list[str]` to drop keys back to default. `BatchItemIn` (`server/api.py:98-126`) = `JobIn` minus `hashtags`/`then`; `BatchJobIn.items: list[BatchItemIn]` (`server/api.py:129-130`). `LocalVideoIn` (`server/api.py:203-241`) = `path`, `title`, `channel`, `platform` (youtube|twitch|kick), `source_url`, plus the same option set and `force`.

Other bodies: `QueueMoveIn{delta:int=0, to:"top"|"bottom"|None}` (133-135), `ClipPatch{title, description, hashtags, exported}` (138-142), `MergeIn{from_id,into_id}` (145-147), `LearningIn{enabled}` (150-151), `AccountIn{platform, channel}` (154-156), `PreviewIn{edit, caption_lines, crop, caption_style, watermark, normalize_audio, gaming, gaming_off, headline, subline, speaker_edits}` (159-178), `CreatorGamingLayoutIn{layout}` (181-182), `BrandingIn{name, config}` (185-187), `BrandingAssetIn{path}` (198-199), `CreatorBrandingIn{branding_id}` (202-203), `RenderIn{start, end, render_opts}` (244-247), `CaptionsIn{lines:[{start,end,text}]}` (250-251), `TightenIn{silence=True, fillers=True}` (254-258), `TermIn{term, rule=protect|ignore|auto}` (261-265), `TranslationPatch{lines, post}` (268-272), `CancelIn{video_id, url}` (275-277), `AiEditIn{message}` (280-281), `AgentIn{message, history:[], defaults:{}}` (284-293), `ExportIn{folder}` (296-297), `BatchExportIn{clip_ids, folder}` (300-302), `ModelIn{tag}` (305-306), `SettingsPatch{model, channel, auto_upload, privacy, content_language, translation_model, outro, min_score}` (309-317), `TranslateIn{clip_ids, languages, stage=translate|export, folder, include_video, burn, dub, subtitles, post_text, voices, style}` (320-337), `FeedbackIn{kind, title, answers, areas, severity, include_diagnostics, video_id, images:[{b64,ext}]}` (340-349).

Allowed import suffixes: `_IMAGE_SUFFIXES=(.png,.jpg,.jpeg,.webp)`, `_VIDEO_SUFFIXES=(.mp4,.mov,.mkv,.webm,.m4v,.avi,.flv,.ts,.mpg,.mpeg,.wmv,.m2ts,.mts)` (`server/api.py:193-195`), enforced by `core.paths.picked_file` before any file is opened.

### 4.2 `_process_options(body, into=None) -> dict` — the one options validator (`server/api.py:440-550`)

Shared by `POST /jobs`, `POST /jobs/batch`, `PATCH /jobs/{id}`, `POST /videos/local` and watches (`server/api.py:443-445`, `689-697`). Produces the **job payload** (section 5). Mode-exclusivity rules, each a `HTTPException(400, ...)`:

| Rule | Line |
|---|---|
| `vertical_live` with `podcast` or `longform` → 400 "Vertical Live can't be combined with Podcast or Longform" | `server/api.py:533-535` |
| `gaming` with `vertical_live`, `podcast` or `longform` → 400 | `server/api.py:538-542` |
| `gaming_scoring` with `podcast` or `longform` → 400 (allowed with standard, Vertical Live, Gaming) | `server/api.py:543-546` |
| `sport` with `gaming`, `gaming_scoring` or `podcast` → 400 "Sports can't be combined with Gaming / Reaction or Podcast" | `server/api.py:548-550` |
| `gaming_layout` invalid → 400 `gaming_layout: <reason>` (via `core.modes.clean_gaming`) | `server/api.py:465-472` |
| `sport` not in `sports.available()` → 400 `sport: <reason>` (via `sports.clean`) | `server/api.py:482-490` |
| unknown `filter` → 400 | `server/api.py:508-512` |
| non-http(s) `webhook_url` → 400 | `server/api.py:519-526` |

Not refused but notable: `sport` + `longform` and `sport` + `vertical_live` are allowed (no rule; `core/pipeline.py:224-231` even flips vertical_live on for a 9:16 match).

## 5. The job payload stored in the queue, and the worker

### 5.1 `jobs` table (`core/state.py:62-70` + migrations `core/state.py:412-437`)

Columns: `id`, `type` (`process|render|translate`, `server/jobs.py:8-11`, `297-302`), `payload` (JSON text), `status` (`queued|running|done|failed|cancelled`, `server/jobs.py:285-299`), `error`, `created_at`, `updated_at`, `position`, `video_id`, `title`, `interrupted`, `started_at`, `finished_at`, `attempts`, `log_path`. Index `idx_jobs_queue(status, position, id)` (`core/state.py:435`). Claim = `SELECT … WHERE status='queued' ORDER BY position, id LIMIT 1` then set `running` (`core/state.py:1325-1340`). Render/translate jobs are inserted **ahead** of waiting process jobs (`core/state.py:1275-1305`).

### 5.2 Payload shapes

- `process`: `{"url": str, "force": bool, ...options from _process_options...}` — keys possible: `max_clips, caption_style, captions, long_clips, podcast, vertical_live, gaming, gaming_scoring, gaming_layout, gaming_remember, longform, sport, focus, watermark_profile_id, filter, min_score, hashtags, then, webhook_url, webhook_secret` (`server/api.py:446-528`), plus `origin: "stream"` when queued by an integration (`server/integrations.py:146`) or `"watch"` (inferred from `server/jobs.py:645`, which reads `payload.get("origin") in ("watch","stream")`), plus `preset` for watches (`server/automation.py:1121`). Local imports use `url = "local:<video_id>"` (`server/api.py:970`).
- `render`: `{"clip_id": int, "start"?: float, "end"?: float, "render_opts"?: dict}` (`server/api.py:1683-1692`, `1569`, `1646-1653`; `server/youtube_api.py:433-440` for `render_first`).
- `translate`: `{"clip_ids":[≤50], "languages":[...], "stage":"translate|export", "folder", "include_video", "burn", "dub", "subtitles", "post_text", "voices":{}, "style":{}}` (`server/api.py:2108-2121`).

`GET /queue` lifts `url` out of the payload and exposes the rest as `settings` (`core/queue.py:360-371`).

### 5.3 Worker loop (`server/jobs.py:49-340`)

`Worker(threading.Thread)`; `notify()` wakes it (`server/jobs.py:65-69`); `run()` recovers interrupted jobs (`core/state.py:1384-1399`), installs the progress handler (`server/jobs.py:109-123`), then loops: if `queue.is_paused(db)` wait (`server/jobs.py:125-134`; the queue **defaults to paused**, `core/queue.py:59-66`), else `claim_next_job`, open `data/logs/job_<id>.log` (`server/jobs.py:145-150`), publish `{"type":"job","status":"running"}` + `{"type":"queue"}` (`153-154`), set `cancel.set_active(vid)` (`163`), deep-copy config and translate payload keys onto `cfg["clips"][...]` (`server/jobs.py:187-262`: `podcast, vertical_live, gaming_scoring, gaming(+gaming_layout, gaming_remember), captions, min_score, sport, focus (+sports.direction), long_clips→min/max_duration 61/180, filter, hashtags→required_hashtags, max_clips→max_clips_per_video & scoring.rerank_pool, caption_style, watermark_profile_id→clips.watermark`), then dispatch: `longform.shorts` → `_both_formats` (`server/jobs.py:272-273`, `673-695`), `longform` → `longform.process.process_longform(url, cfg, db, payload["longform"])` (`274-279`), else `core.pipeline.process_video(url, cfg, db, force=...)` (`280-281`); `render` → `_rerender_clip` (`283`, `697-820`); `translate` → `_translate_clips` (`285`, `386-540`). On success `finish_job(id,"done")`, `_run_follow_up` (the `then` action, WoopSocial only, `server/jobs.py:561-620`), `_announce(db, job, "done")` (`288-291`). `CancelledError` → `cancelled`; any other exception → `failed` with `scrub_secrets(str(e))[:2000]` (`292-310`).

Everything the pipeline needs is passed as `config` dict + `StateDB`; the pipeline's public entry is `process_video(url: str, config: dict, db: StateDB, force: bool=False) -> list[RenderedClip]` (`core/pipeline.py:192`).

## 6. Progress, events and the WebSocket

### 6.1 Path of a progress event

1. Pipeline stages call `core.progress.emit(**event)` (`core/progress.py:30-39`): a process-wide settable callback, thread-local `set_thread_tags` lets the prefetcher restamp its events (`core/progress.py:23-27`). Emit sites: `core/pipeline.py:188,205,208,390,424,612,674`; `longform/process.py:57,60,91,105,165,209,270,301,322,347,365`; `transcription/transcriber.py:182`; `transcription/cloud.py:78,98`; `analysis/highlights.py:71,132,138`; `analysis/fusion.py:96,359,620`; `sources/ytdlp_common.py:42`; `remote_render/dispatch.py:205`; `server/jobs.py:444` (translate).
2. The worker's `on_progress` (`server/jobs.py:109-123`) stamps `job_id` (None for `prefetch` events), adds `span` for both-formats jobs, records it (`_record_progress`, `server/jobs.py:312-337`) and publishes `{"type":"progress","job_id":…, **event}` to the broadcaster (`server/jobs.py:121`).
3. `server/events.py:11-41`: `Broadcaster.publish(event)` from any thread via `loop.call_soon_threadsafe`; each `/ws` client has an `asyncio.Queue(maxsize=200)`; a full queue **drops** events (`server/events.py:25-30`, pinned by `tests/test_event_delivery.py:307-331`).
4. `@app.websocket("/ws")` (`server/api.py:1428-1440`): accept, `broadcaster.subscribe()`, `send_json` forever; the server never reads from the client.

Stage weights used both by the worker's `progress_snapshot(job_id) -> {"stage","label","percent","eta_seconds","elapsed_seconds"} | None` (`server/jobs.py:339-359`, `_STAGES` at `37-46`) and by the UI (`ui/src/renderer/src/lib/jobProgress.ts:17-28`): `download 0–0.15, downloaded, transcribe 0.15–0.40, signals 0.40–0.45, analyze 0.45–0.65, ranking 0.65–0.70, reactions 0.70–0.78, render 0.78–1.0`. `tests/test_job_progress.py` checks these.

### 6.2 Event catalogue (the `type` field)

| `type` | Shape | Emitted at |
|---|---|---|
| `queue` | `{"type":"queue"}` — no data; re-read `GET /queue` | `server/api.py:851,974,1016,1027,1048,1063,1084,1102,1115,1178`; `server/jobs.py:154,383`; `server/integrations.py:154,301`; `server/automation.py:651,830` |
| `job` | running: `{"type":"job","job_id","status":"running"}`; terminal: `{"type":"job","job_id","job_type","status":done\|failed\|cancelled,"title","remaining", "video_id"?, "clips"?, "error"?}` | `server/jobs.py:153`, `_announce` `server/jobs.py:361-383` |
| `progress` | `{"type":"progress","job_id":int\|None, "stage":str, ...}` where stage-specific keys are: `download`: `message` or `fraction, video_id, downloaded, total`; `downloaded`: `video_id, title, duration`; `converting source to H.264`: `video_id`; `transcribe`: `video_id, title` then `fraction`; `signals`; `analyze`: `video_id` then `current,total`; `ranking`: `current,total`; `reactions`: `current,total`; `render`: `video_id, clip, total`; `done`: `video_id, clips, seconds`; `multilingual`: `message, fraction, clip, total`; optional `span:[lo,hi]`; `prefetch` tagged events carry `job_id: null` | `server/jobs.py:121` + emit sites above |
| `model_pull` | `{"type":"model_pull","tag","status","completed"?,"total"?}` then `status:"done"` or `status:"error","error"` | `server/api.py:2875-2887` |
| `publish` | `{"type":"publish","publish_job":int,"clip_id":int,"stage":"publish", "phase": prepare\|upload\|metadata\|done\|failed\|cancelled\|checked, "fraction"?, "message"?, "terminal"?, "youtube_id"?, "url"?, "warnings"?, "state"?}` | `server/publisher.py:330-348` (`_emit`) |
| `automation` | `{"type":"automation"}` or `{"type":"automation","activity":{...}}` / `{"doing": str}` | `server/automation.py:438,445,451,538,652,708,831,1031,1141,1174,1185,1251,1266` |

The UI's `StudioEvent` type mirrors this union (`ui/src/renderer/src/lib/types.ts:642-680`). `docs/API.md:1218-1262` documents six types. Note `examples/drive_the_api.py:131-137` claims "events carry no job id" and "there is no failure event" — both are out of date versus `server/jobs.py:121` and `_announce` (`server/jobs.py:361-383`), which do carry `job_id` and do emit `status:"failed"`.

### 6.3 Cancellation

`POST /cancel` → `core.cancel.request_cancel(vid)` (`server/api.py:1201-1214`); the pipeline checks at stage boundaries and inside long loops via `cancel.check` / `cancel.check_active` (`core/cancel.py:46-72`) and yt-dlp's hook (`sources/ytdlp_common.py:35-37`). Cooperative only.

## 7. Webhooks (`server/webhooks.py`)

- Opt-in per job via `webhook_url` (+ `webhook_secret`) in the payload; validated at submission (`server/api.py:519-528`, `is_deliverable` at `server/webhooks.py:82-93`).
- Fired exactly once from `Worker._announce` on every terminal state (`server/jobs.py:385-397`): `deliver(url, body_for(event), secret)`.
- Body (`server/webhooks.py:104-122`): `{"event":"job.done|job.failed|job.cancelled","job_id","job_type","status","video_id","title","clips","error"}`; header `X-Clips-Kitty-Signature: sha256=<hmac over the exact bytes>` (`server/webhooks.py:79,96-101`); `TIMEOUT=10.0`, `User-Agent: clips-kitty`, one attempt, never raises (`server/webhooks.py:125-145`). Tests: `tests/test_webhooks.py`.
- A webhook listener is today the **only push channel to an outside process** other than `/ws`; it carries no clip data, only counts.

## 8. Route tables

Legend — **API.md**: `S` = listed in `docs/API.md` as something to build on; `D-int` = documented in API.md but explicitly called internal/may change (`docs/API.md:106-120`, `654-668`); `I` = not in API.md ("everything else is internal", `docs/API.md:111-114`); `ref` = only mentioned in passing. **Deps**: `UI` = `ui/src/renderer/src/lib/api.ts` (all 135 distinct UI path literals live in that one file, plus `/system/stats` in `lib/modelSpeed.ts` and `/ws` in `lib/useEvents.ts`); `MCP:n` = `server/mcp.py` line; `EX` = `examples/drive_the_api.py`; `AGENT` = `POST /agent/chat` calls MCP tool handlers in-process which call the API over loopback (`server/api.py:2686-2800`, `server/mcp.py:67-78`); `WH` = webhook; `SKILL` = `skills/clips-kitty/SKILL.md` (names MCP tools only, never HTTP paths). `d` = per-request `StateDB`.

### 8.1 `server/api.py` — health, system, feedback

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/health` | `health` | 711 | liveness + versions | — | `{"ok":true,"app_version":str,"api_version":1}` | — | S | UI, MCP:441, EX:42 |
| GET | `/health/preflight` | `preflight_check` | 718 | can this install make a clip (`core.preflight.run(config).as_dict()`) | — | `{"ready":bool,"checks":[{name,ok,detail,fix,blocking}]}`; check names incl. `ffmpeg, ffprobe, ollama, model, ai, transcription, whisper, gpu, disk` (`core/preflight.py:104-275`) | — | S | UI, EX:47 |
| GET | `/system/stats` | `system_stats` | 731 | cpu/ram/disk/gpu + build stamp | — | `{cpu_percent, ram_percent, data_dir_bytes, disk_free_bytes, gpu:{name,vram_used,vram_total,gpu_percent}\|null, build_sha, started_at, uptime_seconds}` | — | S | UI, modelSpeed.ts |
| GET | `/feedback/diagnostics` | `feedback_diagnostics` | 748 | diagnostics block for a bug report | query `video_id?: str` | dict from `feedback.collect_diagnostics` | — | I | UI |
| POST | `/feedback/submit` | `feedback_submit` | 758 | build report, POST to relay (`config.feedback.relay_url`) | `FeedbackIn` | `{"ok":bool,"url"?,"markdown","error"?}` | 400 missing answers | I | UI |

### 8.2 `server/api.py` — jobs and queue

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| POST | `/jobs` | `create_job` | 803 | queue one URL (identify → dup guards → `_process_options` → `d.add_job("process", json, video_id)` → `worker.notify()` → `queue` event) | `JobIn`; stray query `status_code` (a def-arg wart, `docs/API.md:290`) | `{"job_id":int}` or `{"job_id":null,"already_processed":true,"video_id"}` or `{"job_id":null,"already_queued":true,"video_id","queued_job_id"}`; HTTP 200 | 409 queue full (`queue.MAX_ACTIVE=5`, `core/queue.py:42`); 400 option errors; 422 | S | UI, MCP:316, EX:73, AGENT |
| GET | `/videos/local/shape` | `local_video_shape` | 854 | probe a local file's size/orientation before import | query `path: str` | `{"width","height","orientation"}` | 400 not a video | I | UI |
| POST | `/videos/local` | `add_local_video` | 867 | import a local file (ffprobe check, `video.encoding.import_local_source` to `data/downloads/<vid>.mp4`, `vid="local_"+md5[:12]`), upsert video row, tag creator, queue `process` with `url="local:<vid>"` | `LocalVideoIn` | `{"job_id":int,"video_id":str}` or already_processed shape | 400 bad file/convert; 409 full | S | UI, MCP:352, EX:76 |
| GET | `/jobs` | `jobs` | 977 | job rows | — | bare array of job rows (`d.list_jobs()` → **newest 50, id DESC**, `core/state.py:1365-1376`) | — | S | UI |
| GET | `/jobs/{job_id}` | `job` | 985 | one job row | path int | job row dict (columns in 5.1, `payload` as JSON string) | 404 | S | UI, MCP:359, EX:141 |
| GET | `/queue` | `queue_snapshot` | 1001 | grouped queue view (`core.queue.snapshot`) | — | `{processing,queued,completed,failed:[item],paused,estimate:{queued_seconds,per_video_seconds,samples,confident},capacity,max_active}`; items add `display_title, channel, source_seconds, video_status, url, settings` (`core/queue.py:343-385`) | — | S | UI, MCP:372 |
| POST | `/queue/pause` | `queue_pause` | 1009 | `queue.set_paused(True)` | — | `{"paused":true}` | — | S | UI |
| POST | `/queue/resume` | `queue_resume` | 1019 | `set_paused(False)` + `worker.notify()` | — | `{"paused":false}` | — | S | UI |
| POST | `/jobs/{job_id}/move` | `move_job` | 1030 | reorder a queued job | `QueueMoveIn` | `{"moved":bool}` | — | I | UI |
| POST | `/jobs/{job_id}/retry` | `retry_job` | 1051 | re-queue a failed/cancelled job (same row) | query `standard: bool=False` drops `vertical_live` | `{"job_id":int}` | 409 not retryable | S | UI |
| PATCH | `/jobs/{job_id}` | `patch_job` | 1066 | edit a **queued** process job's settings snapshot | `JobPatch` | `{"ok":true}` | 404; 409 started / not process | ref (`docs/API.md:987`) | UI |
| DELETE | `/jobs/{job_id}` | `delete_job` | 1087 | drop a non-running job | — | `{"deleted":id}` | 404; **409 if running** | S (doc says it works on running — see §12) | UI |
| POST | `/queue/clear` | `clear_queue` | 1105 | delete finished/waiting rows | raw `dict` `{"what": completed\|failed\|queued\|all}` | `{"deleted":n}` | 400 unknown target | I | UI |
| POST | `/jobs/batch` | `create_jobs_batch` | 1118 | queue many, tolerant | `BatchJobIn` | `{"created":[{url,job_id,video_id}],"skipped":[{url,reason: unrecognized\|already_processed\|already_queued\|queue_full\|bad_option, detail?, video_id?}]}` | — (always 200) | S | UI |
| GET | `/jobs/{job_id}/log` | `job_log` | 1181 | tail of `data/logs/job_<id>.log` | query `tail:int=300` | `{"log":str,"missing":bool}` | 404; 500 unreadable | S | UI |
| POST | `/cancel` | `cancel_processing` | 1201 | cooperative cancel | `CancelIn{video_id\|url}` | `{"cancelling":vid}` | 400 neither | S | UI |

### 8.3 `server/api.py` — storage, videos, clips, media, editor

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/storage` | `storage` | 1242 | `core.housekeeping.survey` | — | survey dict minus `_groups` | — | I | UI |
| GET | `/storage/videos` | `storage_videos` | 1255 | per-video disk cost | — | `{"videos":[{video_id,title,channel,created_at,clips,source_bytes,transcript_bytes,clip_bytes,total_bytes}],"total_bytes"}` | — | I | UI |
| POST | `/storage/cleanup` | `storage_cleanup` | 1324 | `housekeeping.clean` | — | `{bytes_freed, files_removed, ...}` | — | ref (`docs/API.md:451`) | UI |
| DELETE | `/videos/{video_id}` | `delete_video` | 1340 | remove download, transcript, voice profile, clip folder, rows | path | `{"deleted":vid}` | 400 bad id; 409 processing now | I | UI |
| DELETE | `/clips/{clip_id}` | `delete_clip` | 1406 | remove one clip file + preview + rows | path | `{"deleted":id,"bytes_freed"}` | 404 | I | UI |
| WS | `/ws` | `ws` | 1428 | event stream (§6) | — | JSON events | — | S | UI (useEvents.ts), EX:107 |
| GET | `/videos` | `videos` | 1443 | all videos newest first, `outcome` decoded | — | bare array `{video_id,channel_id,title,status,created_at,updated_at,channel_name,process_seconds,creator_id,duration,source_url,source_platform,games,outcome,clip_count,creator_name}` | — | S | UI, MCP:389, EX:162 |
| GET | `/videos/{video_id}/clips` | `clips_for_video` | 1470 | clips of a video (`_clip_json`: `hashtags`,`scores`,`render_opts` decoded) | path | bare array of clip rows `{id,video_id,start_s,end_s,score,hook,path,status,scheduled_for,created_at,title,description,hashtags:[],scores:{},render_opts:{},exported_at,...}`; **200 []** for unknown id | — | S | UI, MCP:402,559, EX:167, AGENT |
| PATCH | `/clips/{clip_id}` | `patch_clip` | 1478 | edit title/description/hashtags/exported star | `ClipPatch` | clip JSON | 404 | S (exported flag, `docs/API.md:643-647`) | UI |
| GET | `/clips/{clip_id}/captions` | `get_captions` | 1545 | caption lines (override or rebuilt from transcript) | path | `{"lines":[{start,end,text}]}` | 404 | S | UI, MCP:422 |
| PUT | `/clips/{clip_id}/captions` | `put_captions` | 1556 | save lines + queue `render` job | `CaptionsIn` | `{"job_id"}` | 404 | I | UI |
| POST | `/clips/{clip_id}/tighten` | `tighten_clip` | 1577 | propose silence/filler cuts (`analysis.tighten.propose`) | `TightenIn` | keep ranges dict | 404 | I | UI |
| POST | `/clips/{clip_id}/ai-edit` | `ai_edit` | 1617 | NL edit via `analysis.clip_edit.interpret_edit` + LLM, may queue `render` | `AiEditIn` | `{"reply","job_id"\|null}` | 404 | I | UI |
| POST | `/clips/{clip_id}/render` | `rerender_clip` | 1679 | queue `render` job | `RenderIn` | `{"job_id"}` | 404; 400 bad gaming | I | UI |
| POST | `/clips/{clip_id}/preview` | `preview_clip` | 1705 | synchronous real render into `data/previews/clip_<id>.mp4` via `core.pipeline._render_files` | `PreviewIn` | `{"url":"/media/preview/{id}?v=ts"}` | 404 source missing; 500 render failed | I | UI |
| GET | `/sources/frame` | `source_frame` | 1807 | JPEG frame of an unprocessed URL/file (`sources.preview_frames`) | query `at=0.5,url,path` | image/jpeg | 400/422 | I | UI |
| GET | `/sources/people` | `source_people` | 1816 | person detector on that frame (`gaming.detect.people_on`) | same | `{"size":[w,h],"people":[{box,head,confidence}]}` | 503 no vision stack | I | UI |
| GET | `/sources/suggest` | `source_suggest` | 1830 | suggested webcam box + panels | query `url,path` | `{"cam":...\|null,"panels":[...]}` | — | I | UI |
| GET | `/sources/snap` | `source_snap` | 1873 | snap a drawn box to a border | query `box="x,y,w,h",url,path` | `{"box":[4],"bordered":bool}` | 400; 503 | I | UI |
| GET | `/clips/{clip_id}/source-frame` | `clip_source_frame` | 1911 | frame of the clip's source | query `at` | image/jpeg | 404; 500 | I | UI |
| GET | `/clips/{clip_id}/snap` | `clip_snap` | 1919 | as `/sources/snap` for a clip | query `box` | same | same | I | UI |
| GET | `/clips/{clip_id}/panels` | `clip_panels` | 1924 | panels on the clip's source | — | `{"panels":[...]}` | 404 | I | UI |
| GET | `/clips/{clip_id}/people` | `clip_people` | 1929 | people on a clip frame | query `at` | as `/sources/people` | 404/503 | I | UI |
| GET | `/clips/{clip_id}/creator-gaming-layout` | `get_creator_gaming_layout` | 1934 | remembered layout for the clip's creator | — | `{"creator_id","layout"}` | 404 | I | UI |
| PUT | `/clips/{clip_id}/creator-gaming-layout` | `put_creator_gaming_layout` | 1948 | remember/forget | `CreatorGamingLayoutIn` | same | 400; 404; 409 no creator | I | UI |
| GET | `/media/preview/{clip_id}` | `media_preview` | 1974 | serve the preview mp4 | — | video/mp4 FileResponse | 404 | I | UI (api.ts:329 builds `/media/` URLs) |
| GET | `/clips/{clip_id}/words` | `clip_words` | 1981 | clip-relative word timings | — | `{"words":[{start,end,word}]}` | 404 | S | UI |
| GET | `/media/{clip_id}` | `media` | 2001 | the rendered clip (range requests via FileResponse) | — | video/mp4 | 404 | S | UI, EX (prints URL) |

### 8.4 `server/api.py` — languages, translation, export, branding

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/languages` | `list_languages` | 2017 | `multilingual.languages.LANGUAGES` | — | `{"languages":[{code,name,native,can_dub,caption_font}],"dubbing_available"}` | — | S | UI |
| GET | `/voices` | `list_voices` | 2043 | dubbing voices for a language | query `language` | `{"voices":[...],"default"}` | — | I | UI |
| GET | `/voices/preview` | `preview_voice` | 2057 | synthesize a sample wav | query `language, voice?` | audio/wav | 400; 500 | I | UI |
| POST | `/translate` | `translate_clips` | 2091 | queue `translate` job | `TranslateIn` | `{"job_id","languages","clips"}` | 400 | S | UI |
| GET | `/clips/{clip_id}/translations` | `clip_translations` | 2128 | stored translations for review | — | `{"source":[lines],"translations":[{language,lines,post,edited,updated_at}]}` | 404 | I | UI |
| PUT | `/clips/{clip_id}/translations/{language}` | `save_clip_translation` | 2153 | save corrections | `TranslationPatch` | `{"saved","lines"}` | 400; 404 | I | UI |
| GET | `/clips/{clip_id}/glossary` | `clip_glossary` | 2185 | protected/ignored terms | — | `{"protected","ignored","mine"}` | 404 | I | UI |
| POST | `/clips/{clip_id}/glossary` | `rule_clip_term` | 2209 | rule a term | `TermIn` | `{"term","rule"}` | 400; 404 | I | UI |
| DELETE | `/clips/{clip_id}/translations/{language}` | `discard_clip_translation` | 2232 | drop a translation | — | `{"discarded"}` | — | I | UI |
| POST | `/clips/{clip_id}/export` | `export_clip` | 2243 | copy clip to a folder (`shutil.copy2`), set `exported_at`, log feedback | `ExportIn` | `{"exported":[paths]}` (200 + [] if missing) | — | S | UI, MCP:430 |
| POST | `/export/batch` | `export_batch` | 2247 | same for many | `BatchExportIn` | same | — | S | UI |
| GET | `/branding` | `list_branding` | 2274 | branding profiles | — | `[{id,name,config}]` | — | I | UI, MCP:194 |
| POST | `/branding` | `create_branding` | 2283 | add | `BrandingIn` | `{"id"}` | — | I | UI |
| PUT | `/branding/{profile_id}` | `update_branding` | 2292 | edit | `BrandingIn` | `{"id"}` | 404 | I | UI |
| DELETE | `/branding/{profile_id}` | `delete_branding` | 2303 | delete | — | `{"deleted"}` | — | I | UI |
| POST | `/branding/asset` | `upload_branding_asset` | 2312 | import a logo file (≤20 MB) into `data/branding/assets/<sha256[:16]><ext>` | `BrandingAssetIn{path}` | `{"asset":name}` | 400 | I | UI |
| GET | `/branding/asset/{name}` | `get_branding_asset` | 2343 | serve an asset | — | file | 404 | I | UI (api.ts:586) |

### 8.5 `server/api.py` — creators, assistant, models, settings

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/creators` | `creators` | 2363 | profiles + stats + merge suggestions + `watched` flag | — | `{"creators":[{creator_id,display_name,aliases,learning_enabled,videos,clips,avg_score,accounts,watched}],"suggestions"}` | — | I | UI |
| GET | `/creators/{creator_id}` | `creator_detail` | 2409 | knowledge (with `state: active\|candidate\|dormant`), events, feedback, accounts, preferences | — | dict | 404 | I | UI |
| POST | `/creators/{creator_id}/accounts` | `add_creator_account` | 2473 | attach a channel | `AccountIn` | `{"account_id","creator_id"}` | 400 | I | UI |
| POST | `/creators/merge` | `merge_creators` | 2488 | merge two profiles | `MergeIn` | `{"merged","into"}` | 404 | I | UI |
| POST | `/creators/split/{account_id}` | `split_creator_account` | 2502 | undo merge | — | `{"new_creator_id"}` | 404 | I | UI |
| DELETE | `/creators/{creator_id}/knowledge/{knowledge_id}` | `delete_knowledge` | 2516 | drop one fact | — | `{"deleted"}` | — | I | UI |
| DELETE | `/creators/{creator_id}/knowledge` | `clear_unused_knowledge` | 2530 | drop unconfirmed/dormant | — | `{"deleted":n}` | — | I | UI |
| DELETE | `/creators/{creator_id}/memory` | `wipe_creator_memory` | 2558 | wipe learned data | — | `{"creator_id","wiped"}` | — | I | UI |
| DELETE | `/creators/{creator_id}` | `delete_creator` | 2574 | delete profile (videos/clips unlinked) | — | `{"deleted","name",...counts}` | 404 | I | UI |
| POST | `/creators/{creator_id}/learning` | `set_learning` | 2594 | toggle learning | `LearningIn` | `{"creator_id","learning_enabled"}` | — | I | UI |
| POST | `/creators/{creator_id}/branding` | `set_creator_branding` | 2608 | default branding | `CreatorBrandingIn` | `{"creator_id","default_branding_id"}` | — | I | UI |
| GET | `/agent/status` | `agent_status` | 2626 | can the assistant run, which model | — | `{"ready","model","configured","reason","install"}` | — | I | UI |
| POST | `/agent/chat` | `agent_chat` | 2686 | one assistant turn; tools = `server.mcp.TOOLS` executed in-process, each tool calling the API over loopback (`server/mcp._request`) | `AgentIn` | `server.agent.run`/`run_cloud` result | 400 no model / LLMError; 500 | I | UI |
| GET | `/models` | `models` | 2811 | Ollama models + recommendations | — | `{"active","installed","recommendations","other_models","recommended"}` | 503 Ollama down | S | UI |
| POST | `/models/activate` | `activate_model` | 2844 | switch `llm.backend` via `llm.manager.switch_model(settings_path, tag)` | `ModelIn` | `{"active":spec}` | 400 not pulled | S | UI |
| POST | `/models/pull` | `pull_model` | 2858 | background `ollama /api/pull`, progress on `/ws` | `ModelIn` | `{"started":tag}` | — | S | UI |
| DELETE | `/models/{tag:path}` | `delete_model` | 2920 | `ollama /api/delete` | — | `{"deleted"}` | 400 | I | UI |
| GET | `/sports` | `list_sports` | 2929 | `sports.available()` | — | `[{id,label,highlights:[...],periods:[...]}]` or `[]` | — | S | UI |
| GET | `/settings` | `get_settings` | 2939 | quick-setup keys | — | `{model,channel,auto_upload,privacy,content_language,translation_model,outro,min_score}` | — | I | UI |
| PATCH | `/settings` | `patch_settings` | 2954 | regex-edit `settings.yaml` in place + live config | `SettingsPatch` | `{"ok":true,"note":"restart serve to apply pipeline-level changes"}` | 400 | I | UI |

### 8.6 `server/youtube_api.py` (`install(app, *, config, db, data_dir, worker, publish_worker)` at :148). All routes except `/youtube/status` 404 when YouTube publishing is disabled (`_guard`, :153-156).

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | API.md | Deps |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/youtube/status` | `youtube_status` | 168 | `service.status_payload` | — | `{enabled, backend, has_client, connected, scopes, playlists_available, channel, settings, quota}` or `{"enabled":false}` | — | S | UI, MCP:449 |
| PATCH | `/youtube/settings` | `patch_youtube_settings` | 176 | enable/defaults | `YouTubeSettingsPatch{enabled, privacy, category_id, made_for_kids, playlists_enabled, notify_subscribers, region, common_description≤1500}` | `{"settings","status"}` | 400 | D-int | UI |
| PUT | `/youtube/credentials` | `put_credentials` | 198 | store OAuth client | `CredentialsIn{client_id, client_secret}` | `{"ok","client_id_tail"}` | 400; 404 | D-int | UI |
| DELETE | `/youtube/credentials` | `delete_credentials` | 217 | wipe client + all tokens | — | `{"cleared":true}` | — | D-int | UI |
| POST | `/youtube/connect` | `start_connect` | 238 | start browser consent thread | `ConnectIn{playlists, add}` | `{"state":"waiting"}` | 400; 404 | D-int | UI |
| GET | `/youtube/connect` | `poll_connect` | 265 | poll consent | — | `{"state": idle\|waiting\|error\|done, "channel"?, "error"?, "status"?}` | — | D-int | UI |
| POST | `/youtube/disconnect` | `disconnect` | 309 | revoke one/all channels | `DisconnectIn{channel_id?}` | `{"disconnected","status"}` | — | D-int | UI |
| PATCH | `/youtube/default-account` | `set_default_account` | 343 | choose default channel | `DefaultAccountIn{channel_id}` | `{"status"}` | 404 | I | UI |
| GET | `/youtube/categories` | `categories` | 355 | assignable categories | query `region="US"` | `{"categories"}` | 400; 404 | D-int | UI |
| GET | `/youtube/playlists` | `playlists` | 368 | playlists (needs full scope) | — | `{"playlists"}` | 400; 404 | D-int | UI |
| GET | `/clips/{clip_id}/publish` | `clip_publish_status` | 383 | upload row + active publish job | — | `{"upload":{clip_id,youtube_id,privacy,actual_privacy,state,publish_at,channel_title}\|null,"job"}` | 404 | S | UI, MCP:701 |
| POST | `/clips/{clip_id}/publish` | `publish_clip` | 397 | queue a `publish_jobs` row (optionally after a `render` job via `render_first`) | `PublishIn` (title required, description, tags, category_id, privacy, publish_at, made_for_kids, contains_synthetic_media, embeddable, public_stats_viewable, license, default_language, notify_subscribers, playlist_id, thumbnail:bool, channel_id, render_first{start,end,render_opts}) | `{"publish_job_id","render_job_id"\|null}` | 400; 404; 409 already publishing | S | UI |
| POST | `/publish/{job_id}/cancel` | `cancel_publish` | 494 | cancel upload | — | `{"cancelled":true}` or `{"cancelling":true}` | 404 | D-int | UI |
| GET | `/youtube/uploads` | `uploads` | 510 | history | query `limit=50` (≤200) | `{"uploads":[rows]}` | 404 | D-int | UI, MCP:711 |
| GET | `/clips/{clip_id}/frame` | `clip_frame` | 538 | JPEG frame at `t` seconds | query `t=0.0` | image/jpeg | 404; 500 | D-int | UI (api.ts:660) |
| POST | `/publish/plan` | `publish_plan` | 560 | resolve a batch plan, create nothing | `PlanIn{clip_ids, start_at?, every_hours?, privacy}` | `{"items":[{clip_id,title,description,privacy,publish_at}],"warnings":[]}` | 400 | S-ish (`docs/API.md:838-870`) | UI, MCP:666, AGENT |
| POST | `/publish/plan/execute` | `publish_plan_execute` | 629 | one publish job per item | `PlanExecuteIn{items:[{clip_id,title?,publish_at?,privacy?}]}` | `{"started":[{clip_id,publish_job_id,render_job_id}],"skipped":[{clip_id,reason}]}` | 400 | S-ish | UI, MCP:688 |
| POST | `/clips/{clip_id}/thumbnail/generate` | `generate_thumbnails` | 682 | face-crop candidates (`video.thumbnail.generate`) | query `count=3` (1..4) | `{"generated":n}` | 404 | S-ish (`docs/API.md:872-900`) | UI |
| GET | `/clips/{clip_id}/thumbnail/generated/{index}` | `generated_thumbnail` | 715 | one candidate | — | image/jpeg | 404 | S-ish | UI (api.ts:677) |
| POST | `/clips/{clip_id}/thumbnail` | `choose_thumbnail` | 723 | choose by base64 `image`, `t`, or `generated` index | `ThumbnailIn` | `{"thumbnail":path}` | 400; 404 | D-int | UI |

### 8.7 `server/uploadpost_api.py` (`install(app, *, config, db, data_dir, publish_worker=None)` :63) — all but `/uploadpost/status`, `/uploadpost/settings`, `/uploadpost/key`, `/uploadpost/capabilities` 404 when off (`_guard` :67-69).

| Method | Path | Handler | Line | Purpose | Request | Response | API.md | Deps |
|---|---|---|---|---|---|---|---|---|
| GET | `/uploadpost/status` | `uploadpost_status` | 95 | status (`enabled`, `has_key`, key tail…) | — | dict | I | UI, MCP:467 |
| PATCH | `/uploadpost/settings` | `uploadpost_settings` | 103 | settings | `SettingsIn{enabled, profile, platforms, common_description, first_comment, affiliate_url}` | status | I | UI |
| PUT | `/uploadpost/key` | `put_key` | 115 | store + validate key | `KeyIn{api_key}` | status + `plan`, `email` | I | UI |
| DELETE | `/uploadpost/key` | `delete_key` | 144 | wipe | — | `{"removed",...status}` | I | UI |
| GET | `/uploadpost/profiles` | `profiles` | 155 | provider profiles | — | `{"profiles"}` | I | UI |
| GET | `/uploadpost/connections` | `connections` | 167 | linked platforms | — | `{"profile","connected"}` | I | UI |
| POST | `/uploadpost/connect` | `connect` | 187 | hosted link page | `ConnectIn{username}` | `{"url","expires_hours":48,"profile"}` | I | UI |
| POST | `/uploadpost/clips/{clip_id}/publish` | `publish_clip` | 226 | fan-out one clip | `PublishIn{platforms,title,description,tags,first_comment,thumbnail:bool,overrides,scheduled_date,timezone,add_to_queue}` | `{"clip_id","request_id","platforms":[rows],"done"}` | I | UI, MCP:508 |
| POST | `/uploadpost/batch` | `publish_batch` | 327 | many clips, spaced | `BatchIn{clip_ids,platforms,overrides,every_hours,start_at,timezone,add_to_queue}` | `{"started","skipped"}` | I | UI |
| POST | `/uploadpost/refresh/{request_id}` | `refresh` | 443 | poll a fan-out | — | publish payload | I | UI |
| POST | `/uploadpost/retry/{request_id}` | `retry` | 470 | retry failed platforms | — | publish payload | I | UI |
| GET | `/uploadpost/capabilities` | `capabilities` | 513 | per-platform field support (`publish.uploadpost.PLATFORM_FIELDS`) | — | `{"platforms":{name:{description,first_comment,ai_disclosure,thumbnail,requires,extra}}}` | I | UI |
| GET | `/uploadpost/clips/{clip_id}` | `clip_publishes` | 581 | per-platform state | — | `{"platforms":[rows]}` | I | UI, MCP:520 |

### 8.8 `server/woopsocial_api.py` (`install(app, *, config, db, data_dir, publish_worker=None)` :39) — same guard pattern (:43-45).

| Method | Path | Handler | Line | Purpose | Request | Response | API.md | Deps |
|---|---|---|---|---|---|---|---|---|
| GET | `/woopsocial/status` | `woopsocial_status` | 110 | status | — | dict | I | UI, MCP:533 |
| PATCH | `/woopsocial/settings` | `woopsocial_settings` | 118 | settings | `SettingsIn{enabled, platforms, common_description, affiliate_url}` | status | I | UI |
| PUT | `/woopsocial/key` | `put_key` | 131 | store + validate | `KeyIn` | status + `projects` | I | UI |
| DELETE | `/woopsocial/key` | `delete_key` | 153 | wipe | — | `{"removed",...}` | I | UI |
| GET | `/woopsocial/connections` | `connections` | 164 | connected platforms | — | `{"connected"}` | I | UI |
| POST | `/woopsocial/connect` | `connect` | 177 | per-platform connect link | `ConnectIn{platform}` | `{"url","platform"}` | I | UI |
| POST | `/woopsocial/clips/{clip_id}/publish` | `publish_clip` | 204 | one clip | `PublishIn{platforms,title,description,tags,overrides,scheduled_date}` | payload | I | UI |
| POST | `/woopsocial/batch/plan` | `plan_batch` | 257 | plan, creates nothing | `BatchIn{clip_ids,platforms,overrides,every_hours,start_at,hashtags,exclude,per_day,gap_hours}` | `{"provider","platforms","every_hours","per_day","gap_hours","hashtags","items":[{clip_id,title,publish_at}],"warnings"}` | I | MCP:576, AGENT |
| POST | `/woopsocial/batch` | `publish_batch` | 338 | execute (`service.publish_clips`) | `BatchIn` | `{"started","skipped"}` | I | UI, MCP:652 |
| POST | `/woopsocial/refresh` | `refresh_all` | 380 | refresh all in flight | — | dict | I | UI |
| GET | `/woopsocial/schedule` | `schedule` | 398 | upcoming posts | — | `{"posts"}` | I | UI |
| GET | `/woopsocial/in-flight` | `in_flight_count` | 408 | request ids in flight | — | `{"request_ids"}` | I | — (no UI/MCP hit) |
| POST | `/woopsocial/refresh/{post_id}` | `refresh` | 417 | refresh one | — | payload | I | UI |

### 8.9 `server/ai_api.py` (`install(app, config=, db=, data_dir=, settings_path=)`; tested standalone in `tests/test_ai_api.py:54-57`)

| Method | Path | Handler | Line | Purpose | Request | Response | API.md | Deps |
|---|---|---|---|---|---|---|---|---|
| GET | `/ai` | `ai_status` | 227 | active model, transcription backend, providers (never a key), sign-in plans | — | `{"active":{provider,model,local},"transcription":{backend,model},"providers":[...],"signin":[...]}` | S | UI |
| PUT | `/ai/providers/{provider_id}/key` | `put_key` | 231 | check then save key | `KeyIn{api_key}` | status + `message` | S | UI |
| POST | `/ai/providers/{provider_id}/connect` | `connect` | 252 | OAuth/PKCE sign-in URL | — | `{"url"}` | I | UI |
| GET | `/ai/providers/{provider_id}/callback/{state}` | `sign_in_callback` | 269 | browser callback (HTML) | query `code` | HTML page | I | browser |
| DELETE | `/ai/providers/{provider_id}/key` | `delete_key` | 291 | wipe | — | status + `removed` | S | UI |
| GET | `/ai/providers/{provider_id}/models` | `provider_models` | 298 | models from provider | query `refresh=false, kind=text\|stt` | `{"models":[{id,name,context,json_schema,tools,note}],"fetched_at","kind"}` | S | UI |
| POST | `/ai/providers/{provider_id}/test` | `test_provider` | 313 | key/model check | `TestIn{model}` | `{"ok","kind"?,"message"}` | S | UI |
| POST | `/ai/activate` | `activate` | 331 | choose model (`llm.manager.switch_model`) | `ActivateIn{provider, model}` | status | S | UI |
| GET | `/ai/signin/{provider_id}` | `plan_status` | 370 | signed-in plan state | — | view | I | UI |
| POST | `/ai/signin/{provider_id}/start` | `plan_start` | 376 | begin sign-in | `SignInStartIn{device}` | provider dict | I | UI |
| POST | `/ai/signin/{provider_id}/cancel` | `plan_cancel` | 386 | cancel | — | view | I | UI |
| POST | `/ai/signin/{provider_id}/sign-out` | `plan_sign_out` | 392 | sign out | — | status | I | UI |
| GET | `/ai/signin/{provider_id}/models` | `plan_models` | 403 | plan models | — | `{"models","fetched_at","kind"}` | I | UI |
| POST | `/ai/signin/{provider_id}/automation` | `plan_automation` | 414 | allow unattended use | `AutomationIn{allowed}` | view | I | UI |
| POST | `/ai/signin/{provider_id}/test` | `plan_test` | 426 | tiny real call | `TestIn` | `{"ok","message"}` | I | UI |
| POST | `/ai/transcription` | `set_transcription` | 440 | local vs cloud STT | `TranscriptionIn{backend, model}` | status | S | UI |
| POST | `/ai/providers/{provider_id}/stt-check` | `check_voice_model` | 460 | validate an STT model | `SttCheckIn{model}` | status + `ok`,`message` | I | UI |

### 8.10 `server/integrations.py` (`install(app, *, db, worker, broadcaster, finder=None, clock=time.time) -> StreamWatcher` :210) — supported (module docstring :10-12; `docs/API.md:706-800`)

| Method | Path | Handler | Line | Purpose | Request | Response | Errors | Deps |
|---|---|---|---|---|---|---|---|---|
| GET | `/integrations/presets` | `list_presets` | 230 | `PRESETS` (`standard`, `podcast`, `long_clips`, `highlights`; :34-55) | — | `[{id,name,description,options}]` | — | OBS plugin (external, not in repo) |
| POST | `/integrations/streams` | `create_stream` | 234 | hand over an ended stream; VOD found later by `StreamWatcher` (`sources.vod_finder.find_stream_vod`) | `StreamIn{session_id ^[A-Za-z0-9-]{8,64}$, source, platform twitch\|youtube\|kick, channel, started_at, ended_at, preset}` | `{"created":bool, ...view}` with `state: waiting_for_vod\|needs_link\|queued\|processing\|complete\|error\|cancelled`, `progress` (worker.progress_snapshot) when processing, `waiting_behind`, `queue_paused`, `clips` | 400 | external |
| GET | `/integrations/streams/{session_id}` | `get_stream` | 263 | live view | — | view (`view()` :95-127) | 404 | external |
| POST | `/integrations/streams/{session_id}/link` | `set_link` | 271 | supply the VOD url (`queue_vod` → `queue.enqueue_once` with `origin:"stream"`, `queue.start_if_alone`) | `LinkIn{url}` | view | 404 | external |
| DELETE | `/integrations/streams/{session_id}` | `cancel_stream` | 283 | remove/cancel | — | view | 404 | external |

### 8.11 `server/automation.py` (`install(app, *, db, worker, broadcaster, options_from, data_dir, interval_minutes, feed, publisher, clock) -> ChannelWatcher` :955) — supported (docstring :20; `docs/API.md:910-1130`)

| Method | Path | Handler | Line | Purpose | Request | Response | Deps |
|---|---|---|---|---|---|---|---|
| GET | `/automation` | `get_automation` | 1011 | status + presets | — | `{enabled, delete_sources, interval_minutes, watches, watching, presets}` | UI |
| PATCH | `/automation` | `patch_automation` | 1019 | flags | `AutomationPatch{enabled, delete_sources}` | status | UI |
| GET | `/automation/activity` | `activity` | 1034 | live panel | — | `{"now":{state,text,progress?},"watching","next_check_at","events":[{at,text,kind}]}` | UI |
| GET | `/automation/slots` | `slots` | 1077 | preview schedule | query `per_day=5, gap_hours=1, day_start="", count=5` | `{"times":[iso],"already_scheduled"}` (400 invalid) | UI |
| GET | `/automation/watches` | `list_watches` | 1101 | watches | — | `[view_watch]` | UI |
| POST | `/automation/watches` | `add_watch` | 1109 | add channel (`feed.resolve`), options via `options_from` | `WatchIn{platform, channel, publish?: PublishSettings, options?, preset?}` | `{"created":bool, ...watch}` (400) | UI |
| PATCH | `/automation/watches/{watch_id}` | `patch_watch` | 1144 | edit | `WatchPatch{enabled, name, preset, options, publish, backlog, min_minutes}` | watch (404) | UI |
| DELETE | `/automation/watches/{watch_id}` | `delete_watch` | 1177 | remove | — | `{"deleted":true}` | UI |
| POST | `/automation/watches/{watch_id}/check` | `check_now` | 1188 | poll now | — | `{"checking":true}` | UI |
| GET | `/automation/items` | `list_items` | 1199 | seen videos | query `watch_id?, limit=100` (≤500) | `[view_item]` | UI |
| POST | `/automation/items/{item_id}/queue` | `clip_this` | 1208 | clip a set-aside video | — | item (409) | UI |
| POST | `/automation/items/{item_id}/publish` | `publish_item` | 1232 | publish finished clips (watcher thread) | — | item (409 not ready) | UI |
| POST | `/automation/items/{item_id}/skip` | `skip_item` | 1254 | set aside / decline | — | item | UI |

`PublishSettings` (`server/automation.py:89-117`): `mode: off|ask|auto`, `platforms`, `max_posts 0..100`, `spread`, `per_day 1..50`, `gap_hours 0.25..24`, `day_start "HH:MM"`, `hashtags`, `ai_hashtags`, `footer ≤1000`, `overrides`.

### 8.12 `remote_render/service.py` (`install(app, config, data_dir)` :148; adds its own `startup`/`shutdown` hooks :164-170) — all internal (not in API.md; `docs/REMOTE-RENDERING.md` exists but was not read)

| Method | Path | Handler | Line | Request | Response | Deps |
|---|---|---|---|---|---|---|
| GET | `/remote-render` | `get_remote` | 174 | — | `state(data_dir)` | UI |
| PUT | `/remote-render` | `put_remote` | 178 | `RemotePatch{enabled, mode local\|auto\|worker:<id>, port 1024-65535}` | state (400) | UI |
| POST | `/remote-render/pairing-code` | `pairing_code` | 195 | — | `{"code","expires_in":600,...gateway}` (409; 503) | UI |
| DELETE | `/remote-render/workers/{worker_id}` | `remove_worker` | 205 | — | state (404) | UI |
| POST | `/remote-render/workers/{worker_id}/hold` | `hold_worker` | 214 | `Hold{held}` | state | UI |
| PUT | `/remote-render/this-pc` | `put_this_pc` | 219 | `ThisPcPatch{enabled, main, code, max_jobs 1..8, draining}` | state (400) | UI |
| POST | `/remote-render/this-pc/unpair` | `unpair_this_pc` | 236 | — | state | UI |
| POST | `/remote-render/videos/{video_id}/render-locally` | `render_locally` | 243 | — | `{"released":bool}` | UI |
| POST | `/remote-render/jobs/{job_id}/retry` | `retry_job` | 247 | `Retry{target}` | `{"ok":bool}` | — |

The **render gateway** (`remote_render/gateway.py:50`, separate app on a separate port, TLS, `Authorization: Bearer <secret>` + `X-Worker-Id`, :57-63): `POST /v1/pair`, `POST /v1/heartbeat`, `POST /v1/claim`, `GET /v1/jobs/{id}/piece`, `GET /v1/jobs/{id}/assets/{name}`, `POST /v1/jobs/{id}/progress`, `GET|PUT /v1/jobs/{id}/result`, `POST /v1/jobs/{id}/complete`, `POST /v1/jobs/{id}/fail` (:72-192). This is the one place the repo already has an **authenticated, out-of-process worker protocol** for a render stage — relevant precedent for a plugin runtime.

## 9. What depends on the API (summary)

| Consumer | How | Paths used |
|---|---|---|
| Electron renderer | `fetch(API_BASE + path)` wrapper `request<T>` (`ui/src/renderer/src/lib/api.ts:64-74`); all 135 distinct path literals in `lib/api.ts`; WS in `lib/useEvents.ts` | essentially every route except `/integrations/*`, `/woopsocial/in-flight`, `/remote-render/jobs/{id}/retry`, `/ai/providers/{id}/callback/{state}` |
| MCP server `server/mcp.py` (19 tools in `TOOLS` :722-1212; `docs/API.md:1181` says "Thirteen tools" — stale) | stdlib `urllib` to `CLIPS_STUDIO_API` or `http://127.0.0.1:8765` (`:52-78`) | `/branding`, `/jobs`, `/videos/local`, `/jobs/{id}`, `/queue`, `/videos`, `/videos/{id}/clips`, `/clips/{id}/captions`, `/clips/{id}/export`, `/health`, `/youtube/status`, `/uploadpost/status`, `/uploadpost/clips/{id}/publish`, `/uploadpost/clips/{id}`, `/woopsocial/status`, `/woopsocial/batch/plan`, `/woopsocial/batch`, `/publish/plan`, `/publish/plan/execute`, `/clips/{id}/publish`, `/youtube/uploads` |
| In-app assistant `POST /agent/chat` | runs the same MCP tool handlers in-process → loopback HTTP (`server/api.py:2686-2800`) | as MCP |
| `examples/drive_the_api.py` | `requests` + `websockets` | `/health`, `/health/preflight`, `/jobs`, `/videos/local`, `/jobs/{id}`, `/videos`, `/videos/{id}/clips`, `/media/{id}` (printed), `ws://…/ws` |
| `skills/clips-kitty/SKILL.md` | names MCP tools only (`queue_video`, `queue_local_file`, `job_status`, `list_clips`, `list_videos`, `queue_status`, `export_clip`) | — |
| Webhook listeners | pushed by `server/webhooks.py` | n/a (outbound) |
| OBS plugin (external repo) | `/integrations/*` per `docs/API.md:706-800`, `server/integrations.py:3-9` | — |

## 10. How the API is tested without heavy deps

- Sub-routers are installed on a bare `FastAPI()` and driven with `TestClient(app, base_url="http://127.0.0.1")` (the explicit base_url is for `TrustedHostMiddleware`, `tests/test_uploadpost_api.py:3-5`): `tests/test_ai_api.py:54-57` (`ai_api.install`, HTTP transport monkeypatched), `tests/test_integration_streams.py:58-64` (`integrations.install` with `FakeWorker`/`FakeBroadcaster`/fake finder), `tests/test_automation.py` (`automation.install` with `FakeFeed`), `tests/test_uploadpost_api.py:21-33`, `tests/test_woopsocial_api.py` (not read).
- The full app: `tests/test_local_upload.py:28-43` calls `create_app(load_config(BUNDLED_CONFIG) with tmp data_dir, tmp settings.yaml)` and uses `TestClient` **without** a `with` block so startup never runs (no worker thread). It `importorskip`s `fastapi`, `httpx`, `numpy`. Other tests touching `create_app`: `tests/test_both_formats.py`, `test_clip_intent.py`, `test_clip_marks.py`, `test_gaming_editor.py`, `test_input_safety.py`, `test_min_score_setting.py`, `test_post_style_pipeline.py`, `test_remote_render.py`, `test_second_speaker_render.py`, `test_sports_pipeline.py`.
- Pure-unit API pieces: `tests/test_server_connection_resets.py` (`_quiet_connection_resets`), `tests/test_job_progress.py` (`Worker._record_progress`/`progress_snapshot`, skips if worker imports fail), `tests/test_event_delivery.py` (`Broadcaster` drop semantics), `tests/test_webhooks.py` (transport monkeypatched), `tests/test_mcp.py` (`mcp._request` monkeypatched; pins tool schemas, e.g. `longform` enum `tests/test_mcp.py:242-247`).
- `tests/conftest.py:12-16` puts the repo root on `sys.path`; `db` fixture is a real `StateDB` in `tmp_path`.

## 11. Core types an SDK would need (seen)

- `core/models.py`: `DownloadedVideo{video_id, title, path, duration, channel, games, description}` (:11-25), `Segment{start, end, text, words:[{start,end,word}]|None}` (:28-40), `ClipCandidate{start, end, score, hook, reason, source, engagement, trending, subscores}` (:43-74), `Rejection` (:77-83), `RenderedClip{source_video_id, candidate, path}` (:86-92).
- Transcript file: `data/transcripts/<video_id>.json` = `{"video_id","language","segments":[Segment]}` (`transcription/transcriber.py:249-257`); read directly by the API in `_clip_captions` (`server/api.py:1527-1543`), `/clips/{id}/words` (`:1990-1999`), `/clips/{id}/tighten`, `/clips/{id}/preview`.
- Source file: `data/downloads/<video_id>.mp4` (`server/api.py:1716`, `server/jobs.py:712`). Clip folder: `data/clips/<channel>/<title> [<video_id>]/` (`server/jobs.py:752-756`). Previews: `data/previews/`. Logs: `data/logs/job_<id>.log`. Branding assets: `data/branding/assets/`. Thumbnails: `data/thumbnails/`.
- Render entry used by the API outside the worker: `core.pipeline._render_files(source, candidate, segments, clip_dir, config, render_opts, content_language) -> (Path, render_opts_json)` (`core/pipeline.py:1123-1131`), DB-free; `_register_clip(db, video_id, candidate, final_path, meta: ClipMetadata, render_opts_json, config)` (`:1563-1571`).
- Settings keys the API reads/writes: top-level `model, channel, auto_upload, privacy, content_language, channels, poll_interval_minutes, llm, whisper, transcription, clips, scoring, analysis, video, tracking, upload, feedback, paths` (`config/settings.yaml:5-149`); `PATCH /settings` rewrites `model`, `channel`, `auto_upload`, `privacy`, `content_language`, `llm.translation_model`, `clips.outro`, `clips.min_score` by regex (`server/api.py:2954-3017`).

## 12. Doc-vs-code discrepancies spotted (worth fixing before an SDK freezes them)

1. `docs/API.md:11` "86 HTTP endpoints" vs 176 route decorators across modules (§0).
2. `docs/API.md:405-407` says `GET /jobs` returns "every job, newest last"; code uses `d.list_jobs()` with default `limit=50, ORDER BY id DESC` (`core/state.py:1365-1376`) → newest **first**, capped at 50.
3. `docs/API.md:419` lists job `status` as `queued, running, done, failed`; `cancelled` also exists (`server/jobs.py:293`, `core/queue.py:50`).
4. `docs/API.md:441` "DELETE /jobs/{id} … Also works on a running one"; code returns 409 for running (`server/api.py:1094-1095`).
5. `docs/API.md:1181` "Thirteen tools" vs 19 in `server/mcp.py:722-1212`.
6. `examples/drive_the_api.py:131-137` says events carry no job id and there is no failure event; both are false today (`server/jobs.py:121`, `361-383`).
7. `docs/API.md:290` and `server/api.py:804`: `def create_job(body: JobIn, status_code=201)` makes `status_code` a query param and the route returns 200 (documented wart).
8. `docs/API.md:1215-1223` lists six event types; `automation` events can carry `activity`/`doing` payloads (`server/automation.py:438-451`) not mentioned.

## 13. Six-way classification (API area)

- **Reuse as is**: `/health`, `/health/preflight`, `/jobs` family, `/queue` family, `/videos`, `/videos/{id}/clips`, `/media/{id}`, `/clips/{id}/captions|words`, `/clips/{id}/export`, `/ws`, webhooks, `/integrations/*` (`server/api.py`, `server/integrations.py`, `server/webhooks.py`).
- **Needs abstraction**: `_process_options` + `Worker.run` payload→config mapping (`server/api.py:440-550`, `server/jobs.py:187-262`) is the de-facto "pipeline options contract" but is hard-coded per mode; `install(app, ...)` is an implicit router/plugin interface with no shared signature; `core.progress.emit` is global single-handler; `PRESETS` are a literal dict (`server/integrations.py:34-55`).
- **Needs compat layer**: `GET /jobs` 50-row cap/ordering vs doc; `/jobs/batch` `skipped.reason` enum; event `type` union and `progress.stage` strings (free-form, e.g. `"converting source to H.264"`, `core/pipeline.py:188`).
- **Needs docs**: `PATCH /jobs/{id}`, `/queue/clear`, `/jobs/{id}/move`, `/storage/*`, `DELETE /videos/{id}`, `/clips/{id}/render|preview`, `/sources/*`, `/branding/*`, `/woopsocial/*`, `/uploadpost/*`, `/remote-render/*`, the `then` and `origin` payload keys, the full `progress` event key set.
- **Remain internal**: `/feedback/*`, `/agent/*`, `/creators/*`, `/settings` (regex-edits YAML), `/ai/signin/*`, `/ai/providers/{id}/callback`, `/clips/{id}/ai-edit|tighten|snap|panels|people|creator-gaming-layout`, `/voices*`, translations/glossary editing routes.
- **Suitable for external plugins today**: anything a process on the same machine can call over loopback: queue/poll/read-clips/export; `/integrations/streams` as a model for a "source" plugin; `/branding` lookup; `/publish/plan` + `/publish/plan/execute` and `/woopsocial/batch/plan|batch` as a model for "propose then confirm" tools.

## 14. How would an outside process run a pipeline through the existing API today, and what is missing?

Today an outside process (same machine, no credentials) does: `GET /health` (check `api_version == 1`, `server/api.py:711`) → `GET /health/preflight` (refuse to start on a failing `blocking` check, `:718`) → `POST /jobs {"url": …, options}` or `POST /videos/local {"path": …}` (`:803`, `:867`; read `job_id`, which may be `null` with `already_processed`/`already_queued`) → `POST /queue/resume` because the queue **starts paused** and nothing starts on its own (`core/queue.py:59-66`; `/integrations/streams` is the only caller that auto-starts, and only when alone, `server/integrations.py:150-153`) → watch `ws://…/ws` for `{"type":"progress","job_id":N,…}` and `{"type":"job","job_id":N,"status":"done|failed|cancelled","video_id","clips"}` (`server/jobs.py:121,361-383`), or pass `webhook_url` and receive one signed `job.done` POST (`server/webhooks.py:104-145`), or poll `GET /jobs/{id}` (`:985`) → `GET /videos/{video_id}/clips` (`:1470`) for `start_s/end_s/score/scores/title/description/hashtags/path/render_opts`, `GET /media/{clip_id}` for the bytes (`:2001`), `GET /clips/{id}/captions|words` for text (`:1545`, `:1981`), then `POST /clips/{id}/export` or `/export/batch` to copy files out (`:2243`, `:2247`), optionally `POST /clips/{id}/render` with `render_opts` to re-cut (`:1679`), and `POST /publish/plan` → `/publish/plan/execute` or `/woopsocial/batch/plan` → `/woopsocial/batch` to publish. What it **cannot** do: register a new pipeline, stage, scorer, source or layout — every mode is a fixed boolean/dict in `JobIn` mapped onto `cfg["clips"]` inside `Worker.run` (`server/jobs.py:187-281`) and dispatched to exactly three hard-coded entry points (`process_video`, `process_longform`, `_rerender_clip`); it cannot receive the downloaded source path or the transcript as data (there is no `GET /videos/{id}/transcript` or `/videos/{id}/source`; the transcript is only reachable per clip and the file path only appears in clip rows as an absolute local path), cannot submit its own candidates or clips back (clips are only created by `_register_clip` inside the pipeline, `core/pipeline.py:1563`; `POST /clips/{id}/render` only re-cuts an existing clip), cannot report progress into the job's bar or log (`core.progress.emit` is in-process only, `core/progress.py:30`; `/ws` is one-way server→client, `server/api.py:1428-1440`), cannot add a `job.type` (`ValueError("Unknown job type")`, `server/jobs.py:287`) or a preset (`PRESETS` literal, `server/integrations.py:34`), cannot authenticate or be scoped (no auth at all, so any plugin has the whole API including `DELETE /videos/{id}` and `PATCH /settings`), cannot be told which `data_dir`/settings it is talking to except by reading `/system/stats` and inferring, and cannot run two jobs concurrently (one worker thread, `server/jobs.py:3-6`). The nearest existing seams for a plugin SDK are the `install(app, …)` module pattern (§1), `queue.enqueue_once` + `start_if_alone` for sources (`core/queue.py:94-154`), `_render_files` for DB-free rendering (`core/pipeline.py:1123`), the authenticated out-of-process worker protocol in `remote_render/gateway.py`, and the MCP tool list in `server/mcp.py:722` which is already a curated "safe surface" over the HTTP API.
