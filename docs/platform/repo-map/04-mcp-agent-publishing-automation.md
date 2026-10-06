# Clips Kitty — the other API surfaces (MCP, agent, AI settings, automation, integrations, publishing, feedback)

Repo: /home/user/clips-studio, branch claude/open-platform-w4eh9g. All paths repo-relative. "(inferred)" marks inference.
Status: v2 (complete).

## 0. The one pattern everything here follows: `install(app, ...)` side-modules mounted by `create_app()`

`server/api.py:573 create_app(config, settings_path)` builds the FastAPI app and mounts each optional surface with one call. Each module owns its routes, its settings blob and (where relevant) its secret. This is the closest thing to a plugin host that exists today.

| Module | Mount call (server/api.py) | Signature |
|---|---|---|
| YouTube publishing | `api.py:629-638` | `youtube_api.install(app, *, config, db, data_dir, worker, publish_worker)` (`server/youtube_api.py:148`) |
| Upload-Post publishing | `api.py:644-651` | `uploadpost_api.install(app, *, config, db, data_dir, publish_worker=None)` (`server/uploadpost_api.py:75`) |
| Remote rendering | `api.py:657-659` | `remote_render.service.install(app, config, data_dir)` |
| WoopSocial publishing | `api.py:664-672` | `woopsocial_api.install(app, *, config, db, data_dir, publish_worker=None)` (`server/woopsocial_api.py:72`) |
| Settings → AI (cloud LLM providers) | `api.py:676-678` | `ai_api.install(app, *, config, db, data_dir, settings_path)` (`server/ai_api.py:122`) |
| Streamer integrations (OBS) | `api.py:682-684` | `integrations.install(app, *, db, worker, broadcaster, ...)` → returns `StreamWatcher` thread (`server/integrations.py:211`) |
| Automation (watched channels) | `api.py:688-706` | `automation.install(app, *, db, worker, broadcaster, options_from, data_dir=None, interval_minutes=15, feed=None, publisher=None, clock=time.time)` → returns `ChannelWatcher` thread (`server/automation.py:954-966`) |
| Feedback | not an install; `feedback_mod.install_log_capture()` at `api.py:575` and routes inline at `api.py:748,758` | — |

Threads returned by install are started/stopped in the app's startup/shutdown hooks (`server/api.py:609-623`): `worker`, `publish_worker`, `stream_watcher`, `channel_watcher`.

Common conventions across side modules (stated in their docstrings):
- "A switched-off feature 404s and `/<x>/status` returns `{"enabled": false}` and nothing else" (`server/youtube_api.py:10-12`, `server/uploadpost_api.py:6-9`, `server/woopsocial_api.py:10-12`).
- Secrets never leave the backend; UI gets `has_key` + 4-char `key_tail` (`server/uploadpost_service.py:98-106`, `server/woopsocial_service.py:84`). Errors pass through `server.feedback.redact()` (`server/feedback.py:135`).
- Settings live as a JSON blob in the `app_state` key/value table via `db.get_flag/set_flag` under a `SETTINGS_KEY` (`server/youtube_service.py:20-21,38-50`, `server/uploadpost_service.py:23-24,61-71`, `server/woopsocial_service.py:18-19`), NOT in settings.yaml.
- API keys go through `core/secrets.py` (`secrets.save/load/wipe/backend_name`), DPAPI-backed on Windows (`server/uploadpost_service.py:10-13,79-93`).
- No auth on the API; it is bound to localhost and guarded by `TrustedHostMiddleware(allowed_hosts=["127.0.0.1","localhost"])` plus CORS for the Vite dev server (`server/api.py:577-591`). `/health` returns `{ok, app_version, api_version}` (`server/api.py:710-716`); `API_VERSION` lives in server/api.py and "moves only when a supported endpoint changes shape".

## 1. MCP server — `server/mcp.py` (1326 lines)

What it is: a stdio JSON-RPC 2.0 MCP server, **stdlib only** (no SDK; `server/mcp.py:10-16`), that is **a translation layer over the local HTTP API** — "No second engine. Every tool asks the running engine over 127.0.0.1:8765 rather than importing the pipeline" (`server/mcp.py:16-20`).
- Run: `python main.py mcp` or `api.exe mcp` (`server/mcp.py:21-25`; subcommand wired at `main.py:174-177,280-288` → `server.mcp.serve()`).
- Engine address: `api_base()` = `$CLIPS_STUDIO_API` or `http://127.0.0.1:8765` (`server/mcp.py:52-55`).
- HTTP client: `_request(method, path, body=None, timeout=60.0)` using `urllib` (`server/mcp.py:67-79`).
- Protocol: `PROTOCOL_VERSION="2025-06-18"`, `KNOWN_PROTOCOLS=("2025-06-18","2025-03-26","2024-11-05")` (`server/mcp.py:36-37`). Methods handled: `initialize`, `ping`, `tools/list`, `tools/call` (`server/mcp.py:1256-1299`). Capabilities: `{"tools": {"listChanged": False}}`; serverInfo name `clips-kitty` (`server/mcp.py:1273-1281`). No resources, no prompts, no notifications.
- Tool registry: module-level list `TOOLS` of dicts `{name, description, inputSchema, handler}` (`server/mcp.py:720-1223`); `handler` is stripped on `tools/list` (`server/mcp.py:1292-1294`). `_call_tool` maps `KeyError`→"Missing argument", `HTTPError`→"The engine refused that (code)", `URLError`→NOT_RUNNING (`server/mcp.py:1237-1253`).
- `server.feedback._app_version()` is imported for serverInfo.version (`server/mcp.py:58-64`); `server.woopsocial_service.DEFAULT_PER_DAY` is imported lazily by `_spacing` (`server/mcp.py:627`) — the only two non-stdlib imports, both optional.
- Copies kept in step by hand (no import of render path): `CAPTION_FONTS`, `CAPTION_POSITIONS`, `COLOUR_NAMES` (`server/mcp.py:88-108`) mirror `video/captions.py FONTS` and `CaptionStyleControls.tsx`.

### MCP tools (name → required inputs → HTTP call it makes → returns text)

| Tool (`server/mcp.py` def line) | Inputs (required in bold) | Engine call | Output |
|---|---|---|---|
| `queue_video` (291) | **url**; force, min_score, max_clips, podcast, vertical_live, gaming, long_clips, captions, hashtags, publish_when_done, watermark (name→id via GET /branding, `_branding_id` 187), longform{mode: short_clips/clips_140/highlights/edited_stream}, caption_style{font,size,color,position,words_per_line,style,title_position,karaoke,highlight_color}, focus (FOCUS_PARAM 213), sport (SPORT_PARAM 225) | `POST /jobs` | job id / "Not queued" when already processed |
| `queue_local_file` (333) | **path**; title, channel, focus, …, platform | `POST /videos/local` | job id |
| `job_status` (358) | **job_id** | `GET /jobs/{id}` | status text |
| `queue_status` (371) | — | `GET /queue` | paused/running summary |
| `list_videos` (388) | — | `GET /videos` | newest-first list |
| `list_clips` (400) | **video_id** | `GET /videos/{id}/clips` | id, score, title per clip |
| `clip_captions` (421) | **clip_id** | `GET /clips/{id}/captions` | caption lines |
| `export_clip` (429) | **clip_id**, **folder** | `POST /clips/{id}/export {folder}` | path |
| `engine_status` (440) | — | `GET /health` | version + base url |
| `youtube_status` (448) | — | `GET /youtube/status` | enabled/connected |
| `uploadpost_status` (466) | — | `GET /uploadpost/status` | enabled/has_key |
| `uploadpost_publish` (487) | **clip_id**, **platforms**; title, description, scheduled_date, timezone, queue | `POST /uploadpost/clips/{id}/publish` | request id |
| `uploadpost_result` (516) | **clip_id** | `GET /uploadpost/clips/{id}` | per-platform outcome |
| `woopsocial_status` (532) | — | `GET /woopsocial/status` | enabled/has_key |
| `schedule_clips_plan` (546) | clip_ids or video_id; platforms, per_day, gap_hours, gap, start_at, hashtags | `GET /videos/{id}/clips` then `POST /woopsocial/batch/plan` | plan text |
| `schedule_clips_execute` (640) | **clip_ids**; same spacing args | `POST /woopsocial/batch` | queued posts |
| `publish_plan` (661) | **clip_ids**; start_at, gap_hours, privacy | `POST /publish/plan` | YouTube plan text |
| `publish_plan_execute` (684) | **items**[{clip_id,…}] | `POST /publish/plan/execute` | queued uploads |
| `publish_status` (698) | clip_id (optional) | `GET /clips/{id}/publish` or `GET /youtube/uploads` | status |

Key-management is deliberately NOT exposed: `uploadpost_status`/`woopsocial_status` say the user "adds theirs in Settings; it cannot be supplied from here" (`server/mcp.py:472-477,537`).

## 2. The in-app assistant — `server/agent.py` + `/agent/*` routes in `server/api.py`

- The assistant reuses **exactly the MCP tool list**: "The tools are exactly the ones server/mcp.py exposes to outside agents: one list, one set of descriptions" (`server/agent.py:4-7`); `from server.mcp import TOOLS` at `server/api.py:2691`.
- `tool_specs(tools)` converts MCP `{name, description, inputSchema}` to OpenAI/Ollama function-calling shape (`server/agent.py:81-95`). `HUMAN_ONLY: set[str] = set()` — the publish gate was removed on 2026-09-22 (`server/agent.py:36-48`).
- `run(message, history, tools, call_tool, host, model, num_ctx=16384) -> {reply, steps, plan, model}` loops ≤ `MAX_TURNS=8` against Ollama `POST {host}/api/chat` (`server/agent.py:159-251`). `run_cloud(message, history, tools, call_tool, backend)` does the same through `backend.chat(messages, specs) -> ChatTurn` (`server/agent.py:254-312`; `llm/base.py:60 LLMBackend.chat`, `ChatTurn`/`ToolCall` dataclasses at `llm/base.py:12-27`).
- `can_call_tools(host, model)` asks Ollama `/api/show` for the "tools" capability (`server/agent.py:97-111`); `usable_model(host, preferred)` (114); `install_offer(vram_gb)` → gemma4:e4b / e2b (146-157).
- Routes: `GET /agent/status` → `{ready, model, configured, reason, install}` (`server/api.py:2626-2684`); `POST /agent/chat` body `AgentIn{message, history, defaults}` → run/run_cloud result (`server/api.py:2686-2814`). The route's `call_tool` closure invokes `tool["handler"](arguments)` directly — i.e. the engine process makes HTTP calls to **itself** via `server.mcp._request` (`server/api.py:2727-2778`). It also re-calls `/woopsocial/batch/plan` and `/publish/plan` to get structured plan items for the window's Confirm button (`server/api.py:2741-2778`).
- Cloud path: `llm.registry.create_backend(config["llm"])` (`server/api.py:2783`); provider errors surface as `LLMError.message` → HTTP 400 (`server/api.py:2792-2797`; `llm/providers/base.py:8-30` defines `LLMError(kind, message)` with `KINDS`).

## 3. Settings → AI — `server/ai_api.py` (cloud LLM providers, BYO key; a real provider registry)

Docstring: local Ollama default; cloud providers are bring-your-own-key; "no Clips Kitty key, account or proxy" (`server/ai_api.py:1-12`).
Routes (all inside `install(app, *, config, db, data_dir, settings_path)` at `server/ai_api.py:122`):

| Route | Line | Body model |
|---|---|---|
| `GET /ai` | 227 | — (LOCAL_ENTRY first, then PROVIDERS; `has_key`, `key_tail`) |
| `PUT /ai/providers/{provider_id}/key` | 231 | `KeyIn{api_key}` (59) |
| `POST /ai/providers/{provider_id}/connect` | 252 | OAuth start (llm/providers/oauth) |
| `GET /ai/providers/{provider_id}/callback/{state}` | 269 | OAuth callback → HTMLResponse (`_sign_in_page` 108) |
| `DELETE /ai/providers/{provider_id}/key` | 291 | — |
| `GET /ai/providers/{provider_id}/models` | 298 | cached 6h (`MODELS_CACHE_SECONDS` 57) |
| `POST /ai/providers/{provider_id}/test` | 313 | `TestIn` (63) |
| `POST /ai/activate` | 331 | `ActivateIn` (67) → `llm.manager.resolve_usable_model, switch_model` |
| `GET /ai/signin/{provider_id}`, `POST .../start`, `.../cancel`, `.../sign-out`, `GET .../models`, `POST .../automation`, `POST .../test` | 370-426 | `SignInStartIn` (81), `AutomationIn` (85); plan sign-ins (ChatGPT) via `llm/signin/catalog.py` |
| `POST /ai/transcription` | 440 | `TranscriptionIn` (76) → `write_transcription(settings_path, backend, model)` (92) |
| `POST /ai/providers/{provider_id}/stt-check` | 460 | `SttCheckIn` (72) → `transcription.cloud.check_model` |

**Provider registry pattern (the most plugin-like thing in the repo):**
- `llm/providers/catalog.py:144 PROVIDERS: dict[str, ProviderSpec] = {spec.id: spec for spec in _ORDER}`; `get(provider_id)` (147). Docstring: "To add a provider that speaks OpenAI-compatible chat completions, add one ProviderSpec to _ORDER… The API, the settings card and the pipeline all read PROVIDERS; nothing else needs to change. A provider with its own wire format adds one file in adapters/ as well." (`llm/providers/catalog.py:5-10`).
- `ProviderSpec` frozen dataclass + `ModelInfo` in `llm/providers/base.py` (ModelInfo fields: id, name, context, json_schema, tools, note, vendor, price, pricing, free, verified, page_url; `as_dict()` 33-62). ProviderSpec fields: see §3a below.
- `llm/registry.py:14 create_backend(llm_config) -> LLMBackend` dispatches on `"<provider>/<model>"`: `ollama` → `OllamaBackend`; sign-in plan → `llm/signin/catalog.get(provider, data_dir).backend(model, llm_config)`; catalog provider → `CloudBackend(spec, model, data_dir)` (`llm/providers/cloud_backend.py`).
- Sign-in plans: `llm/signin/catalog.py:21 _FACTORIES = {"chatgpt": _chatgpt}`; `SignInProvider` ABC in `llm/signin/base.py`; `AUTOMATION_FLAG = "signin_automation_"` per-provider flag (27) controlling unattended use.
- Backend ABC: `llm/base.py:30 LLMBackend` — `generate(prompt, *, json_mode=False) -> str`, `name` property, `sees_images()`, `look(prompt, images)`, `chat(messages, tools) -> ChatTurn`, `supports_schema`; helper `generate_json(llm, prompt, schema)` (70). `examples/fake_backend.py:52 FakeBackend(LLMBackend)` shows the minimal subclass (generate + name).

## 4. Automation (watched channels) — `server/automation.py` (1269 lines)

What it is: "orchestration only. Detection is sources/channel_feed.py, processing is the ordinary queue and worker, and publishing is woopsocial_service" (`server/automation.py:3-6`). "Supported API, documented in docs/API.md" (`:18`; docs at `docs/API.md:977-1190`). Off by default: flags `automation_enabled`, `automation_delete_sources` in app_state (`:37-38`).
- Reaches the engine by **direct python import**: `core.queue`, `core.state.StateDB` (`:34-35`), `sources.channel_feed` (`:974`), `server.integrations.PRESETS` (`:178,976`), `server.woopsocial_service.publish_clips` (`:912-918`), `creator.identity` (`:320,663`).
- Background thread `ChannelWatcher(threading.Thread)` (`:380`): `wake()`, `stop()`, `tick()` (`:453-492`), polls each watch (`_poll` 492), readiness rechecks (`_check_readiness` 571), queues via the worker (`_queue_waiting` 614), decides/does publishing (`_decide_publishing` 672, `publish` 710), retries (`_retry_failed_runs` 807, `_retry_rejected_posts` 833), frees disk (`_free_disk` 882). Retry policy constants `PROCESS_RETRY_DELAYS=(30*60, 3*60*60)`, `PUBLISH_RETRY_SECONDS=15*60`, `PUBLISH_START_ATTEMPTS=6` (`:66-71`).
- **Injectable collaborators** (a seam a plugin system could reuse): `install(..., feed=None, publisher=None, clock=time.time)` — `feed` defaults to `sources.channel_feed`, `publisher` defaults to `woopsocial_publisher(data_dir)` which returns a `Callable[..., dict]` `publish(d: StateDB, **kwargs) -> dict` (`:907-920`, `:954-984`). Only WoopSocial is wired as the automation publisher today.
- `job_payload(watch, url, origin="watch") -> dict` builds the queue payload as `{"url", **PRESETS[preset]["options"], **watch.options, "origin": origin}` and strips incompatible mode flags (`:172-193`). `origin` ∈ {"watch", "manual"} here, "stream" in integrations (`server/integrations.py:139`).

Pydantic models: `PublishSettings{mode: off|ask|auto, platforms, max_posts(0-100), spread, per_day(1-50), gap_hours(0.25-24), day_start "HH:MM", hashtags, ai_hashtags, footer(≤1000), overrides: dict}` (`:89-118`); `AutomationPatch{enabled, delete_sources}` (`:120`); `WatchIn{platform: youtube|twitch|kick, channel(1-300), publish: PublishSettings|None, options: dict|None, preset: str|None}` (`:127`); `WatchPatch{enabled, name(≤100), preset, options, publish, backlog: all|newest|day|none, min_minutes(0-600)}` (`:140`).

Routes (all in `install`, `server/automation.py`):

| Route | Line | Notes |
|---|---|---|
| `GET /automation` | 1011 | `{enabled, delete_sources, interval_minutes, watches, watching, presets}` (status 1000-1009) |
| `PATCH /automation` | 1019 | AutomationPatch; broadcasts `{"type":"automation"}` |
| `GET /automation/activity` | 1034 | `{now:{state,text,progress?}, watching, next_check_at, events}` |
| `GET /automation/slots?per_day&gap_hours&day_start&count` | 1077 | preview of WoopSocial posting times; reserves nothing |
| `GET /automation/watches` | 1101 | `view_watch` rows (267) |
| `POST /automation/watches` | 1109 | WatchIn; options validated through `options_from` = api.py's `_watch_options` → `JobPatch` + `_process_options` (`server/api.py:690-697`) |
| `PATCH /automation/watches/{id}` | 1144 | WatchPatch |
| `DELETE /automation/watches/{id}` | 1177 | |
| `POST /automation/watches/{id}/check` | 1188 | sets next_poll_at=0, wakes watcher |
| `GET /automation/items?watch_id&limit` | 1199 | `view_item` rows (229) |
| `POST /automation/items/{id}/queue` | 1208 | "Clip this" for baseline/skipped/error items; uses `feed.readiness` |
| `POST /automation/items/{id}/publish` | 1232 | the "ask" answer / retry; runs on watcher thread |
| `POST /automation/items/{id}/skip` | 1254 | |

Detection layer `sources/channel_feed.py`: `PLATFORMS=("youtube","twitch","kick")` (`:29`), dataclasses `Channel` (57), `NewSourceVideo` (64), `Readiness` (79); functions `resolve(...)` (95), `latest(...)` (157), `readiness(...)` (256). "Every function that touches the network takes it as an argument, so tests never do" (`:21-22`). CLI counterpart: `main.py channels add|list|remove` (`main.py:178-184`) — an older RSS-monitor path (`main.py:153 "run"`), relation to /automation not verified (inferred: separate legacy path).

## 5. Streamer integrations (OBS plugin) — `server/integrations.py` (304 lines)

What it is: "a streamer tool hands a finished livestream to Clips Kitty… find the VOD that Twitch or YouTube publishes after the stream, queue it once, and report progress in terms any dock can show" (`:1-9`). "Supported API… an incompatible change bumps API_VERSION in server/api.py" (`:10-11`; `API_VERSION = 1` at `server/api.py:34`, exposed by `GET /health` `server/api.py:710-716`).
- Reaches the engine by **direct python import**: `core.cancel`, `core.queue`, `core.state.StateDB` (`:22-23`), `sources.dispatch.identify` (`:130`), `sources.vod_finder.find_stream_vod, clean_handle` (`:215`). Queues through `queue.enqueue_once(d, vid, {"url", **preset["options"], "origin": "stream"}, title=...)` (`:137-141`; `core/queue.py:94`), then `queue.start_if_alone`, `worker.notify()`, `broadcaster.publish({"type":"queue"})` (`:152-155`).
- `PRESETS` dict (`:37-58`): `standard`, `podcast`, `long_clips`, `highlights` — each `{name, description, options}`; "Named bundles of options the pipeline already has… a dock shows these names instead of raw option flags". Shared with automation (`server/automation.py:178,976`).
- `StreamWatcher(threading.Thread)` (`:157`): `__init__(db, finder, on_found, clock)`, `wake/stop/run/tick`; constants `GIVE_UP_AFTER_SECONDS=2h`, `CHECK_EVERY_SECONDS=5min`, `WATCH_TICK_SECONDS=30` (`:31-33`).
- Models: `StreamIn{session_id ^[A-Za-z0-9-]{8,64}$, source(≤32), platform, channel(≤64), started_at: float, ended_at: float, preset="standard"}` (`:76-83`); `LinkIn{url}` (`:86`).
- `view(d, row, worker)` → `{session_id, source, platform, channel, started_at, ended_at, preset, state, vod_url, video_id, job_id, waiting_behind, error, [details], [queue_paused], [progress], [clips]}`; states map `_JOB_STATES = {queued, running→processing, done→complete, failed→error, cancelled}` plus `waiting_for_vod`, `needs_link` (`:67-73, 96-125`).

| Route | Line |
|---|---|
| `GET /integrations/presets` | 230 |
| `POST /integrations/streams` (StreamIn) → `{created, **view}` | 234 |
| `GET /integrations/streams/{session_id}` | 263 |
| `POST /integrations/streams/{session_id}/link` (LinkIn) | 271 |
| `DELETE /integrations/streams/{session_id}` | 283 |

Webhooks (sibling surface, `server/webhooks.py`): `JobIn.webhook_url` / `webhook_secret` (`server/api.py:58-59`); one POST on terminal state with body `{event: job.done|job.failed|job.cancelled, job_id, job_type, status, video_id, title, clips, error}` signed `X-Clips-Kitty-Signature: sha256=<hmac>` (`server/webhooks.py:1-40`); functions `is_deliverable(url)` (41), `signature(secret, raw)` (55), `body_for(event)` (63), `deliver(url, body, secret="")` (84). Fire-and-forget, one attempt, 10s timeout.

## 6. Publishing — three providers, one worker, one half-shared interface

### 6a. The `Publisher` ABC — `publish/base.py`
- `ProgressFn = Callable[[str, int, int], None]` (`:25`); `TITLE_MAX=100`, `DESCRIPTION_MAX=5000`, `TAGS_BUDGET=500` (`:28-30`).
- `@dataclass PublishRequest{video_path: Path, title, description="", tags=[], category_id="22", privacy="private", publish_at: str|None, made_for_kids, contains_synthetic_media, embeddable, public_stats_viewable, license="youtube", default_language, recording_date, localizations, thumbnail: Path|None, playlist_id, notify_subscribers}` (`:33-62`) — "Fields map 1:1 onto videos.insert" (YouTube-shaped).
- `@dataclass PublishResult{video_id, url, requested_privacy, actual_privacy, publish_at, channel_id, channel_title, thumbnail_set, playlist_added, warnings}` + `locked_private`, `studio_url` properties (`:65-100`).
- `class Publisher(ABC)`: abstract `name` property, abstract `publish(request, on_progress=None, should_cancel=None) -> PublishResult`, `default_privacy` property, legacy `upload(video_path, title, description, tags) -> str` shim (`:103-150`).
- **Only YouTube implements it**: `class YouTubeShortsPublisher(Publisher)` (`publish/youtube_shorts.py:85`). `UploadPostPublisher` (`publish/uploadpost.py:666`) and `WoopSocialPublisher` (`publish/woopsocial.py:463`) are plain classes with a different shape: `start(...)`, `check(request_id) -> FanOutResult`, `retry`/`find_by_media`; results are `FanOutResult{PlatformOutcome...}` (`publish/uploadpost.py:201-243`). Errors: `publish/errors.py` — `PublishError` and subclasses `NotConnected, AuthRequired, QuotaExceeded, RateLimited, UploadLimitExceeded, PublishCancelled, VideoRejected` (`:15-60`); provider-specific `UploadPostError`, `WoopSocialError` subclass it.
- `publish/` is "deliberately stdlib-only" (`publish/base.py:6-9`, `publish/uploadpost.py:9-11`).

### 6b. The upload worker — `server/publisher.py`
- `PublishWorker(threading.Thread)(config, db_path, data_dir)`: `notify()`, `stop()`, `cancel(publish_job_id)`, `run()` claims `db.claim_next_publish_job()` (`:37-82`). Own table with opposite crash recovery ("interrupted", never re-run) (`:16-20`).
- `_publish` builds `PublishRequest` and always uses `youtube_service.make_publisher(config, data_dir, channel_id=...)` (`:135-171`) — the worker is **YouTube-only**; WoopSocial/Upload-Post posts are started synchronously inside their routes and only *refreshed* by the worker (`_refresh_provider_posts` 90-133, every `PROVIDER_CHECK_SECONDS=180`, WoopSocial only).
- Emits progress via `server.events.broadcaster` (`_emit` 334).

### 6c. Provider route modules (`server/<provider>_api.py` + `server/<provider>_service.py`)
Each provider is a pair: `*_service.py` = settings blob + secret + client factory + `status_payload`; `*_api.py` = `install(app, ...)` with `_guard` (404 when disabled), `_fail` (redacted 400), `_client()`.

**YouTube** (`server/youtube_api.py`, `server/youtube_service.py`, `publish/youtube_shorts.py`, `publish/oauth.py`):
- OAuth: user pastes their own Google Desktop-app client id/secret (`PUT /youtube/credentials` `:198`, stored under secret name `CLIENT_SECRET="youtube_client"`); `POST /youtube/connect` starts `publish.oauth.ConnectFlow(config, scopes)` thread running `InstalledAppFlow.run_local_server()` (loopback redirect) (`server/youtube_api.py:238-262`; `publish/oauth.py:50-95`, `CONSENT_TIMEOUT_SECONDS=300`); `GET /youtube/connect` polls `{state: idle|waiting|error|done, channel, status}` and files the token under `token_name_for(channel_id)` via `core.secrets` (`:265-307`). Multi-account: `add_account/remove_account/set_default_account/default_channel_id` (`server/youtube_service.py:118-166`). Scopes: `SCOPE_UPLOAD`, `SCOPE_READONLY`, `SCOPE_FULL`, `CONNECT_SCOPES`, `PLAYLIST_SCOPES` (`publish/youtube_shorts.py:42-59`).
- Routes: `GET /youtube/status` 168, `PATCH /youtube/settings` 176, `PUT|DELETE /youtube/credentials` 198/217, `POST|GET /youtube/connect` 238/265, `POST /youtube/disconnect` 309, `PATCH /youtube/default-account` 343, `GET /youtube/categories` 355, `GET /youtube/playlists` 368, `GET /clips/{id}/publish` 383, `POST /clips/{id}/publish` (PublishIn, 101-123; `render_first` sub-range re-render) 397, `POST /publish/{job_id}/cancel` 494, `GET /youtube/uploads` 510, `GET /clips/{id}/frame?t=` 538, `POST /publish/plan` (PlanIn{clip_ids, start_at, every_hours, privacy}) 560, `POST /publish/plan/execute` (PlanExecuteIn{items:[PlanItemIn{clip_id,title,publish_at,privacy}]}) 629, `POST /clips/{id}/thumbnail/generate` 682, `GET /clips/{id}/thumbnail/generated/{i}` 715, `POST /clips/{id}/thumbnail` (ThumbnailIn{image b64|t|generated}) 723.
- Settings DEFAULTS `{enabled:false, privacy, category_id, made_for_kids, playlists_enabled, notify_subscribers, region, common_description}` under app_state key `youtube`; quota ledger under `youtube_quota` (`server/youtube_service.py:20-36,59-85`).

**Upload-Post** (`server/uploadpost_api.py`, `server/uploadpost_service.py`, `publish/uploadpost.py`): API-key (`PUT /uploadpost/key` validates with `client.validate_key()` then keeps or wipes, `:115-142`), key secret `uploadpost_key`, settings key `uploadpost`, `DEFAULT_PROFILE="clips-kitty"`, `AFFILIATE_URL=""` (`server/uploadpost_service.py:23-58`). Social-account connection is **delegated to the provider's hosted page**: `POST /uploadpost/connect` → `client.connect_url(username)` (`:187`; `publish/uploadpost.py:518`). Routes: `GET /uploadpost/status` 95, `PATCH /uploadpost/settings` 103, `PUT|DELETE /uploadpost/key` 115/144, `GET /uploadpost/profiles` 155, `GET /uploadpost/connections` 167, `POST /uploadpost/connect` 187, `POST /uploadpost/clips/{id}/publish` (PublishIn{platforms,title,description,tags,first_comment,thumbnail:bool,overrides,scheduled_date,timezone,add_to_queue}) 226, `POST /uploadpost/batch` (BatchIn) 327, `POST /uploadpost/refresh/{request_id}` 443, `POST /uploadpost/retry/{request_id}` 470, `GET /uploadpost/capabilities` (PLATFORM_FIELDS) 513, `GET /uploadpost/clips/{id}` 581. Idempotency key `clips-kitty-{clip_id}-{created_at}` (`:316`).

**WoopSocial** (`server/woopsocial_api.py`, `server/woopsocial_service.py`, `publish/woopsocial.py`): same shape ("Mirrors server/uploadpost_api.py… same `clip_publishes` table underneath, so the renderer talks to whichever provider is switched on without caring which it is" `:3-6`). Key secret `woopsocial_key`, settings key `woopsocial` with `project_id`, `AFFILIATE_URL="https://woopsocial.com/?via=clipskitty"` (`server/woopsocial_service.py:18-43`). Routes: `GET /woopsocial/status` 110, `PATCH /woopsocial/settings` 118, `PUT|DELETE /woopsocial/key` 131/153, `GET /woopsocial/connections` 164, `POST /woopsocial/connect` (ConnectIn{platform}) 177, `POST /woopsocial/clips/{id}/publish` 204, `POST /woopsocial/batch/plan` (BatchIn{clip_ids, platforms, overrides, every_hours, start_at, hashtags, exclude, per_day, gap_hours}) 257, `POST /woopsocial/batch` 338, `POST /woopsocial/refresh` 380, `GET /woopsocial/schedule` 398, `GET /woopsocial/in-flight` 408, `POST /woopsocial/refresh/{post_id}` 417. Service-level reusable functions: `publish_clips(db, data_dir, once, remember, **kwargs)` (`server/woopsocial_service.py:284`), `schedule_times` (223), `committed_times` (163), `already_sent` (269), `reconcile_sending` (507), `refresh_in_flight` (574), `upcoming` (621), `DEFAULT_PER_DAY=5` (204).

**Plan-then-execute pattern** (used by MCP, the assistant and the UI): `POST /publish/plan` → `{items:[{clip_id,title,publish_at,...}], warnings}` then `POST /publish/plan/execute {items}` (YouTube); `POST /woopsocial/batch/plan` → `{items, platforms, every_hours|per_day, gap_hours, hashtags, warnings}` then `POST /woopsocial/batch` (`server/youtube_api.py:560-680`, `server/woopsocial_api.py:257-378`, `docs/API.md:907`).

**Deferred publish** on a processing job: `JobIn.then = {"action": "publish", "platforms": [...]}` (`server/api.py:61-65`), surfaced to agents as `queue_video.publish_when_done` (`server/mcp.py` queue_video schema; `server/agent.py:62-66`).

## 7. Feedback — `server/feedback.py` + two routes in `server/api.py`

- Routes: `GET /feedback/diagnostics?video_id=` → `collect_diagnostics(config, db, video_id)` (`server/api.py:748-756`; `server/feedback.py:302`); `POST /feedback/submit` (FeedbackIn{kind: bug|feature|improvement, title, answers, areas, severity, include_diagnostics, video_id, images[{b64,ext}]}, `server/api.py:332-341`) → builds Markdown, sends to relay, returns `{ok, url?, markdown, error?}` (`:758-800`).
- Relay: `config["feedback"]["relay_url"]` (`config/settings.yaml:147`, a Cloudflare Worker in `feedback-relay/`), client `submit_to_relay(relay_url, kind, title, markdown, areas, severity, images)` with `GET /challenge` proof-of-work + `POST /submit` + header `X-Clips-Studio: 1` (`server/feedback.py:517-544`, `_solve_pow` 497).
- Reusable utilities other surfaces already import: `redact(text)` (135), `_app_version()` (260, used by MCP serverInfo and `/health`), `install_log_capture()` / `open_job_log` / `close_job_log` / `recent_log(lines)` ring buffer (75-133). docs/API.md calls feedback submission **internal** (`docs/API.md:131-135`).

## 8. Agent skill, examples, docs

- `skills/clips-kitty/SKILL.md`: a Claude/agent **skill file** (frontmatter `name: clips-kitty`, `description`), i.e. prose instructions for an MCP client — not code. It tells the agent to connect with `claude mcp add clips-kitty -- python main.py mcp` (or `api.exe mcp`), the workflow (queue → job_status → list_clips → export_clip), and the traps. It is **stale vs. the server**: it says "no posting to social platforms except YouTube" and names only the processing tools, while `server/mcp.py` exposes 19 tools including Upload-Post/WoopSocial (`skills/clips-kitty/SKILL.md:52-55` vs `server/mcp.py:720-1223`). `docs/API.md:1208-1211` likewise says "Thirteen tools" (actual: 19, verified by importing `server.mcp.TOOLS`).
- `examples/drive_the_api.py` — end-to-end HTTP driver, `requests` + `websockets` only, `API="http://127.0.0.1:8765"` (`:34`): (1) `GET /health` then `GET /health/preflight` and prints `checks[{name, ok, blocking, detail, fix}]`, `ready` (`:37-60`); (2) submit `POST /jobs {url, max_clips}` or `POST /videos/local {path, title, channel}`; handles `already_processed` (needs `force=True`) and reads `job_id`, `video_id` (`:63-91`); (3) follows `ws://127.0.0.1:8765/ws` printing `stage`/`fraction`/`current/total` events (`:97-122`) while polling `GET /jobs/{id}` until `status in {done, failed}` — documents that WS events carry **no job id** and there is **no failure event** (`:125-144`); (4) for URL jobs finds the video as `GET /videos[0].video_id` because the jobs table "has no column for one" (`:158-165`), then `GET /videos/{id}/clips` and prints `score`, `hook`/`title` (`:167-175`). Ctrl-C detaches without cancelling (`POST /cancel` is separate) (`:197-202`).
- `examples/score_a_transcript.py` — **direct python import** path: `analysis.highlights.find_highlights(segments, llm, max_clips, min_score) -> (clips, rejected)` with `core.models.Segment` and `llm.registry.create_backend(settings["llm"])` or `examples.fake_backend.FakeBackend` (`:28-29, 59-78`).
- `examples/fake_backend.py` — `FakeBackend(LLMBackend)` implementing `name` + `generate(prompt, *, json_mode=False)` (`:52-70`): the minimal LLM backend contract.
- `examples/README.md` — states the public python entry points: `analysis.highlights.find_highlights`, `transcription.transcriber.transcribe`, `core.state.StateDB`, `llm.registry.create_backend`, and two rules: use `core.binaries.ffmpeg()` and `core.paths.resolve_data_dir(config)`.
- `docs/API.md:127-141` draws the public boundary: documented endpoints are "supported" (changes noted in CHANGELOG.md); "Everything else is internal: branding assets, creator memory, caption editing, the AI edit endpoints, feedback submission". `docs/API.md:1365-1375`: third-party projects get listed in PROJECTS.md; internal endpoints get promoted on request.
- `docs/EXTENDING.md:43-100`: documented extension points — a new LLM backend is "one new file in llm/ implementing LLMBackend.generate(), plus a line in registry.py"; "`sources/` is a plugin folder" (new `sources/<platform>.py` + URL pattern in `sources/dispatch.py`); export formats in `longform/profiles.py`.
- `docs/UPLOAD-POST.md`: user-facing guide comparing WoopSocial vs Upload-Post (BYO key, affiliate disclosure, what leaves the PC, plan/execute, where the key is kept, "The assistant" section). `docs/GITHUB-SETUP.md`: repo-admin checklist (Dependabot, secret scanning, Discussions, labels sync via `.github/labels.json`, branch rulesets requiring checks Python/Desktop app/Website) — relevant to a GitHub-hosted registry only as the existing CI/labels conventions.
- AI edit endpoint (internal per docs): `POST /clips/{clip_id}/ai-edit` (AiEditIn, `server/api.py:274,1617`) → `analysis.clip_edit.interpret_edit` + re-render; UI calls it via `ui/src/renderer/src/lib/api.ts:310`. UI also calls `/agent/status`, `/agent/chat` (`api.ts:280-302`) and `/ai/*` (`api.ts:462-486`).
- Tests covering these surfaces (all CI, no network): `tests/test_mcp.py` (monkeypatches `mcp._request`), `test_agent.py`, `test_agent_cloud.py`, `test_ai_api.py`, `test_automation.py`, `test_integration_streams.py`, `test_llm_providers.py`, `test_signin.py`, `test_uploadpost*.py`, `test_woopsocial*.py`, `test_youtube_auth.py`, `test_webhooks.py`, `test_publish_*.py`.
- Packaging: `clips-studio.spec` has no explicit `server.mcp` hidden import (grep found none); `main.py mcp` is reached through the normal `main.py` entry (inferred: picked up by PyInstaller's import analysis since `main.py:283` imports it).

## 9. Existing provider/plugin-like abstractions (base class + registry + config entry)

| Abstraction | Base / contract | Registry | Config / storage entry | Where |
|---|---|---|---|---|
| Cloud LLM provider | `ProviderSpec` frozen dataclass (id, label, adapter, base_url, key_label, key_url, pricing_url, privacy, auth, extra_headers, body_extras, models_path, model_filter, key_check_path, key_check_ok, stt, stt_filter, tier, tagline, regions, plan_note, oauth{auth_url, exchange_path}, preferred) | `PROVIDERS: dict[str, ProviderSpec]` built from `_ORDER` | `llm.backend = "<provider>/<model>"` in settings.yaml; key via `llm/providers/keys` + `core/secrets` | `llm/providers/base.py:80-140`, `llm/providers/catalog.py:144` |
| Wire-format adapter | module with `generate/chat/list_models/check_key` | `adapter_for(spec)` = `import_module("llm.providers.adapters."+spec.adapter)` | `ProviderSpec.adapter` | `llm/providers/adapters/__init__.py` |
| LLM backend | `LLMBackend` ABC (`generate`, `name`, `chat`, `look`, `sees_images`, `supports_schema`) | `create_backend(llm_config)` if/elif on provider prefix | `llm.backend` | `llm/base.py:30`, `llm/registry.py:14` |
| Plan sign-in | `SignInProvider` ABC (`available, start, cancel, status, limits, models, sign_out, backend, check_job, public`) | `_FACTORIES = {"chatgpt": ...}` | `signin_automation_<id>` flag | `llm/signin/base.py:20`, `llm/signin/catalog.py:21` |
| Upload destination | `Publisher` ABC (`name`, `publish(PublishRequest)->PublishResult`) — YouTube only; Upload-Post/WoopSocial do not implement it | none: each provider mounted by hand in `create_app` | app_state JSON blob per provider (`youtube`, `uploadpost`, `woopsocial`) + secret names | `publish/base.py:103`, `server/api.py:629-672` |
| Optional server feature | `install(app, *, config, db, data_dir, ...)` closure-based router; `_guard` 404 when disabled; `status_payload` | hand-written call list in `create_app` | `enabled` flag in the provider's app_state blob | `server/api.py:573-706` |
| Video source platform | per-file module with "the same download entry point" + URL pattern in `sources/dispatch.py` | `sources/dispatch.identify(url)` | — | `docs/EXTENDING.md:65-80`, `sources/` |
| Processing preset | dict `{name, description, options}` | `PRESETS` dict | watch/stream `preset` field | `server/integrations.py:37-58` |
| Agent tool | dict `{name, description, inputSchema, handler(args)->str}` | `TOOLS` list | — | `server/mcp.py:720-1223` |
| Automation collaborators | `feed` module (`latest/readiness/resolve`), `publisher: Callable[[StateDB, **kw], dict]` | constructor injection only | — | `server/automation.py:954-984` |
| Job completion hook | webhook POST with HMAC header | per-job `webhook_url`/`webhook_secret` | — | `server/webhooks.py`, `server/api.py:58-59` |

## 10. Capabilities a plugin could use today (name | what | file | reachable today)

| Capability | What | File | Reachable today |
|---|---|---|---|
| Queue a URL / local file job with full options | JobIn incl. focus, sport, longform, caption_style, hashtags, webhook, `then` | `server/api.py:39-65`; MCP `queue_video`/`queue_local_file` `server/mcp.py:291,333` | HTTP `POST /jobs`, `POST /videos/local`; MCP tool |
| Job/queue/video/clip reads | status, queue, videos, clips, captions | `server/mcp.py:358-428` | HTTP + MCP tools; `GET /ws` events (no job id) |
| Export a clip | copy to folder | `server/mcp.py:429` | HTTP `POST /clips/{id}/export`; MCP `export_clip` |
| Readiness/preflight | `/health`, `/health/preflight` | `server/api.py:710-731` | HTTP |
| Job terminal-state callback | signed webhook | `server/webhooks.py` | HTTP (per job) |
| Hand over a finished stream | VOD finding + queue | `server/integrations.py:230-301` | HTTP `/integrations/*` |
| Watch a channel | detection + auto queue + auto publish | `server/automation.py:1011-1267` | HTTP `/automation/*` |
| Publish to YouTube (plan/execute) | | `server/youtube_api.py:560-680` | HTTP; MCP `publish_plan*` |
| Publish via WoopSocial / Upload-Post | fan-out, schedule, batch | `server/woopsocial_api.py`, `server/uploadpost_api.py` | HTTP; MCP `schedule_clips_*`, `uploadpost_*` |
| Add a cloud LLM provider | ProviderSpec | `llm/providers/catalog.py` | python import only (edit `_ORDER`); not reachable from outside |
| Add a plan sign-in | SignInProvider | `llm/signin/catalog.py:21` | python import only |
| Swap the LLM backend | LLMBackend / FakeBackend | `llm/base.py`, `examples/fake_backend.py` | python import (scripts); not from the running engine |
| Score a transcript without video | `find_highlights` | `analysis/highlights.py` via `examples/score_a_transcript.py` | python import / CLI example |
| Add a video source platform | sources module + dispatch | `sources/dispatch.py` | python import only |
| Natural-language assistant | `/agent/chat` over MCP TOOLS | `server/api.py:2686` | HTTP (internal per docs) |
| AI edit of one clip | `/clips/{id}/ai-edit` | `server/api.py:1617` | HTTP (internal per docs) |
| Diagnostics / redact / version | `collect_diagnostics`, `redact`, `_app_version` | `server/feedback.py` | HTTP `GET /feedback/diagnostics`; python import |
| Secrets store | `core.secrets.save/load/wipe/has/backend_name` | `core/secrets.py:97-159` | python import only |
| Per-feature settings blob | `db.get_flag/set_flag` JSON | `server/*_service.py` | python import only |

## 11. Buckets

- **Suitable for external developers (already public, documented, versioned):** the local HTTP API in `docs/API.md` incl. `/integrations/*`, `/automation/*`, `/youtube/*`, `/publish/plan*`, webhooks (`server/webhooks.py`), and the MCP server (`server/mcp.py`) — all HTTP-only, no engine import, with `API_VERSION` (`server/api.py:34`) and CHANGELOG discipline (`docs/API.md:127-141`).
- **Should remain internal:** `/agent/*`, `/clips/{id}/ai-edit`, `/feedback/*`, `/ai/*` key management and the `*_api.py` key routes (`PUT /youtube/credentials`, `PUT /uploadpost/key`, `PUT /woopsocial/key`) — secrets and screen-specific shapes (`docs/API.md:131-135`, `server/mcp.py:472-477,537`).
- **Needs an abstraction:** publishing providers — `Publisher` ABC is YouTube-shaped and unimplemented by Upload-Post/WoopSocial (`publish/base.py:33-62,103`; `publish/uploadpost.py:666`; `publish/woopsocial.py:463`); `PublishWorker._publish` hard-codes `youtube_service.make_publisher` (`server/publisher.py:166-168`); automation hard-codes WoopSocial (`server/automation.py:907-920`); the `install(app, ...)` mount list is hand-written (`server/api.py:629-706`); MCP `TOOLS` is a static list with duplicated enums (`server/mcp.py:88-108`).
- **Needs documentation:** MCP tool count/list is stale in `docs/API.md:1208-1211` and `skills/clips-kitty/SKILL.md:52-55` (19 tools incl. WoopSocial/Upload-Post; the skill says YouTube-only); `/automation` and `/integrations` are documented but the `origin` job field and `PRESETS` contract are not called out as API; `ProviderSpec` field semantics only exist as inline comments (`llm/providers/base.py:80-140`).
- **Reusable as-is:** `install(app, *, config, db, data_dir, ...)` + `_guard`/`status_payload` pattern (`server/uploadpost_api.py:75-93`); `ProviderSpec`/`PROVIDERS`/`adapter_for` registry (`llm/providers/catalog.py`, `llm/providers/adapters/__init__.py`); `LLMBackend` + `create_backend` (`llm/base.py`, `llm/registry.py`); `core.secrets` (`core/secrets.py`); app_state JSON settings blob (`server/youtube_service.py:38-50`); `PRESETS` (`server/integrations.py:37`); `redact()` (`server/feedback.py:135`); the plan→execute two-step and the MCP tool dict shape `{name, description, inputSchema, handler}` (`server/mcp.py:1237-1245`); `examples/drive_the_api.py` as the canonical SDK walk-through.
- **Needs a compatibility layer:** `Publisher.upload()` legacy shim kept for `core/scheduler.py` (`publish/base.py:131-150`); `server/agent.py` depends on the exact MCP `TOOLS` dict shape and on `tool["handler"]` making loopback HTTP calls from inside the engine process (`server/api.py:2727-2731`) — a plugin-provided tool must keep that contract; MCP `CAPTION_FONTS`/`COLOUR_NAMES` copies must track `video/captions.py` (`server/mcp.py:88-96`).

## 12. Open questions

1. Should plugin-contributed MCP tools be registered into `server/mcp.py TOOLS` (and therefore also offered to the in-app assistant via `server/agent.py tool_specs`), or kept in a separate list? Today one list feeds both (`server/agent.py:4-7`).
2. Which `Publisher` contract becomes canonical: the YouTube `PublishRequest/PublishResult` ABC (`publish/base.py`) or the fan-out `start/check/FanOutResult` shape (`publish/uploadpost.py:666-745`)? Both are "stdlib-only" by rule — a plugin SDK would need to say whether that rule applies to plugins.
3. `main.py channels` / `main.py run` (RSS monitor, `main.py:153,178-184`) vs `/automation` — is the CLI path legacy? (inferred, not verified.)
4. The API has no auth and binds to localhost (`server/api.py:583-591`; `examples/drive_the_api.py:16-19`): a marketplace plugin running in-process vs out-of-process (HTTP client like the MCP server) has very different trust; nothing in the repo currently distinguishes them.
5. `clips-studio.spec` shows no explicit hidden import for `server.mcp` or for any plugin discovery — frozen builds would need an import hook or entry-point mechanism for plugins (inferred from `grep` on the spec).
