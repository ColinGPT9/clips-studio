# Live API probe — Clips Kitty local API (sandbox, 2026-10-06)

Repo: /home/user/clips-studio, branch claude/open-platform-w4eh9g. Sandbox: Linux, no GPU, no Ollama,
FFmpeg 6.1.1 (`ffmpeg -version` → "ffmpeg version 6.1.1-3ubuntu5"), Python 3.13.16 (`python3 --version`).
Everything written by this probe lives under
`/tmp/claude-0/-home-user-clips-studio/8828d716-03cc-5de9-81b9-53d443093a47/scratchpad/repo-map/probe/`.

## 1. How the config path and data_dir are chosen

- `main.py:146` — `parser.add_argument("--config", type=Path, default=CONFIG_PATH)`; so `--config` exists and is a
  GLOBAL option (before the subcommand). `CONFIG_PATH = user_config_path(BUNDLED_CONFIG)` (`main.py:65`), and
  `BUNDLED_CONFIG` is `<repo>/config/settings.yaml` (`main.py:60`).
- `core/paths.py` `user_config_path()`: in a non-frozen checkout it returns the bundled path unchanged
  (`if not getattr(sys, "frozen", False): return bundled`).
- `main.py:68-114` `load_config()`: reads YAML, normalises the "quick setup" keys, honours env overrides
  `CLIPS_STUDIO_OLLAMA_HOST` (`main.py:102`) and `CLIPS_STUDIO_DATA_DIR` (`main.py:106-108`), then
  `config["paths"]["data_dir"] = str(resolve_data_dir(config))` (`main.py:110`).
- `core/paths.py` `resolve_data_dir()`: key is `paths.data_dir`; an absolute path is honoured as-is; a relative
  path resolves against `_REPO_ROOT` in a checkout (`return _REPO_ROOT / raw`) or `%LOCALAPPDATA%/Clips Studio/<raw>`
  when frozen.
- `config/settings.yaml:149-150` — `paths:` / `  data_dir: data` (relative → `<repo>/data` in a checkout).
- `main.py:199-200` — the DB is opened at `<data_dir>/state.db` before the subcommand dispatch; `serve` then does
  `from server.api import create_app` (`main.py:272`, lazy) and `uvicorn.run(create_app(config, args.config),
  host=args.host, port=args.port)` (`main.py:277`). Default host 127.0.0.1 (`main.py:168-172`), default port 8765
  (`main.py:163`).

## 2. Probe config

`probe/settings.yaml` is a copy of `config/settings.yaml` with line 150 rewritten to
`  data_dir: /tmp/claude-0/-home-user-clips-studio/8828d716-03cc-5de9-81b9-53d443093a47/scratchpad/repo-map/probe/data`
(command: `cp config/settings.yaml $P/settings.yaml && sed -i "s|^  data_dir: data$|  data_dir: $P/data|" ...`;
verified with `grep -n -A1 "^paths:"`).

## 3. Server start attempt — FAILED (missing `fastapi`)

Command run from `/home/user/clips-studio`:
`(timeout 240 python3 main.py --config $P/settings.yaml serve --port 8799 > $P/server.log 2>&1 &); sleep 8;
curl -s -m 5 -w "\nHTTP %{http_code}\n" http://127.0.0.1:8799/health`

Result: curl printed `HTTP 000`, exit 7 (connection refused). `pgrep -af 'serve --port 8799'` showed no server
process. `probe/server.log` verbatim:

```
Traceback (most recent call last):
  File "/home/user/clips-studio/main.py", line 402, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/user/clips-studio/main.py", line 272, in main
    from server.api import create_app
  File "/home/user/clips-studio/server/api.py", line 20, in <module>
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
ModuleNotFoundError: No module named 'fastapi'
```

Is the failing import lazy?
- In `main.py` yes: `from server.api import create_app` sits inside `if args.command == "serve":` (`main.py:269-272`).
- In `server/api.py` no: `from fastapi import ...` is a module-level import (`server/api.py:20`), so `server.api`
  cannot be imported at all without fastapi.
- `core/pipeline.py` is NOT the blocker: `python3 -c "import core.pipeline"` printed `core.pipeline OK`. Its
  heavy ML imports are lazy — `transcription/transcriber.py:93` does `from faster_whisper import WhisperModel`
  inside a function, `transcription/transcriber.py:49` imports torch inside a function. `core.state`, `core.queue`,
  `server.jobs`, `server.events` all import OK (`python3 -c "import core.state, core.queue, server.jobs,
  server.events"` → `OK`). Note `main.py:54` imports `core.pipeline` at module level, which is fine here.

What is installed vs missing (`python3 -m pip list`): present — starlette 1.6.0, uvicorn 0.53.0, pydantic 2.13.5,
sse-starlette 3.4.11, PyYAML 6.0.1, requests 2.34.2, numpy 2.5.3, opencv-python-headless 5.0.0.93, yt-dlp 2026.8.19.
Missing — fastapi (`python3 -m pip show fastapi` → "Package(s) not found: fastapi"), faster_whisper, ultralytics,
torch (each `ModuleNotFoundError`).

Minimum needed to get the HTTP server up: install `fastapi>=0.141.1,<1` (`requirements.txt:45`); uvicorn
(`requirements.txt:46`) is already present. Nothing was installed (rules).

Consequence: steps 4 and 6 (live curls, openapi.json) could not be done. The sections below are a STATIC
substitute built from `server/api.py` route decorators and `docs/API.md`, each cited.

## 4. What the API exposes — STATIC substitute for openapi.json (server not started)

`probe/openapi.json` was NOT saved: the server never bound the port (section 3). The following is derived from
the `@app.<method>("...")` decorators in the modules `create_app()` wires together, so it is the set of routes the
live `/openapi.json` would enumerate, minus FastAPI's own `/docs`, `/redoc`, `/openapi.json` (enabled by default:
`app = FastAPI(title="Clips Kitty API", version="0.1")` at `server/api.py:576`; documented at `docs/API.md:62-70`).

Wiring (`server/api.py`): `youtube_api.install(` :631, `uploadpost_api.install(` :646,
`remote_render_service.install(app, config, data_dir)` :659, `woopsocial_api.install(` :666,
`ai_api.install(` :678, `integrations.install(` :684, `automation.install(` :699.
`remote_render/gateway.py` (`/v1/pair`, `/v1/claim`, `/v1/jobs/{id}/progress`, ... lines 72-192) is "a separate,
authenticated gateway for render workers" (`server/api.py:655-656`) and is NOT on this app, so it is excluded.

Counts (command: `grep -c -E '@app\.(get|post|put|delete|patch|websocket)\(' <file>`):
86 server/api.py (85 HTTP + the `/ws` websocket at `server/api.py:1447`), 17 server/ai_api.py,
20 server/youtube_api.py, 5 server/integrations.py, 13 server/automation.py, 13 server/uploadpost_api.py,
13 server/woopsocial_api.py, 9 remote_render/service.py — **176 route decorators, 153 distinct paths**
(script output: "# 176 route decorators across 8 modules; 153 distinct paths"). `docs/API.md:11` says
"86 HTTP endpoints and a WebSocket"; that matches `server/api.py` alone, not the whole app.

Tags: NO route decorator passes `tags=`. A `grep -n "tags="` over the eight modules hits only unrelated function
kwargs (`server/uploadpost_api.py:302` `tags=body.tags`, `:429` `tags=tags`, `server/automation.py:763-764`
`lead_hashtags=`/`ai_hashtags=`, `server/woopsocial_api.py:373` `hashtags=`), so every path in a live openapi.json
would sit under the single implicit "default" tag.

Request schema names (pydantic `BaseModel` subclasses, `grep -n -E "^class \w+\(.*BaseModel\)"`):
server/api.py: JobIn:39, JobPatch:67, BatchItemIn:98, BatchJobIn:128, QueueMoveIn:132, ClipPatch:137, MergeIn:144,
LearningIn:149, AccountIn:153, PreviewIn:158, CreatorGamingLayoutIn:178, BrandingIn:182, BrandingAssetIn:195,
CreatorBrandingIn:199, LocalVideoIn:203, RenderIn:238, CaptionsIn:244, TightenIn:248, TermIn:255,
TranslationPatch:262, CancelIn:269, AiEditIn:274, AgentIn:278, ExportIn:289, BatchExportIn:293, ModelIn:298,
SettingsPatch:302, TranslateIn:313, FeedbackIn:332.
server/ai_api.py: KeyIn:59, TestIn:63, ActivateIn:67, SttCheckIn:72, TranscriptionIn:76, SignInStartIn:81, AutomationIn:85.
server/youtube_api.py: PlanIn:40, PlanItemIn:51, PlanExecuteIn:58, YouTubeSettingsPatch:64, CredentialsIn:75,
ConnectIn:80, DisconnectIn:87, DefaultAccountIn:91, RenderFirst:95, PublishIn:101, ThumbnailIn:125.
server/integrations.py: StreamIn:76, LinkIn:86. server/automation.py: PublishSettings:89, AutomationPatch:120,
WatchIn:127, WatchPatch:140. server/uploadpost_api.py: KeyIn:27, SettingsIn:31, PublishIn:40, BatchIn:58, ConnectIn:70.
server/woopsocial_api.py: KeyIn:27, SettingsIn:31, ConnectIn:38, BatchIn:42, PublishIn:64.
(Several names repeat across modules — KeyIn, ConnectIn, PublishIn, BatchIn, SettingsIn — so the live openapi
component names for those would be disambiguated by FastAPI; no response models are declared via `response_model`
in the decorators I listed, so response schemas would be untyped.)

Full path list (path, then METHOD (module:line) for each method), from `probe/routes-static.txt`:

    /agent/chat  POST (server/api.py:2705)
    /agent/status  GET (server/api.py:2645)
    /ai  GET (server/ai_api.py:227)
    /ai/activate  POST (server/ai_api.py:331)
    /ai/providers/{provider_id}/callback/{state}  GET (server/ai_api.py:269)
    /ai/providers/{provider_id}/connect  POST (server/ai_api.py:252)
    /ai/providers/{provider_id}/key  PUT (server/ai_api.py:231)  DELETE (server/ai_api.py:291)
    /ai/providers/{provider_id}/models  GET (server/ai_api.py:298)
    /ai/providers/{provider_id}/stt-check  POST (server/ai_api.py:460)
    /ai/providers/{provider_id}/test  POST (server/ai_api.py:313)
    /ai/signin/{provider_id}  GET (server/ai_api.py:370)
    /ai/signin/{provider_id}/automation  POST (server/ai_api.py:414)
    /ai/signin/{provider_id}/cancel  POST (server/ai_api.py:386)
    /ai/signin/{provider_id}/models  GET (server/ai_api.py:403)
    /ai/signin/{provider_id}/sign-out  POST (server/ai_api.py:392)
    /ai/signin/{provider_id}/start  POST (server/ai_api.py:376)
    /ai/signin/{provider_id}/test  POST (server/ai_api.py:426)
    /ai/transcription  POST (server/ai_api.py:440)
    /automation  GET (server/automation.py:1021)  PATCH (server/automation.py:1029)
    /automation/activity  GET (server/automation.py:1044)
    /automation/items  GET (server/automation.py:1209)
    /automation/items/{item_id}/publish  POST (server/automation.py:1242)
    /automation/items/{item_id}/queue  POST (server/automation.py:1218)
    /automation/items/{item_id}/skip  POST (server/automation.py:1264)
    /automation/slots  GET (server/automation.py:1087)
    /automation/watches  GET (server/automation.py:1111)  POST (server/automation.py:1119)
    /automation/watches/{watch_id}  PATCH (server/automation.py:1154)  DELETE (server/automation.py:1187)
    /automation/watches/{watch_id}/check  POST (server/automation.py:1198)
    /branding  GET (server/api.py:2293)  POST (server/api.py:2302)
    /branding/asset  POST (server/api.py:2331)
    /branding/asset/{name}  GET (server/api.py:2362)
    /branding/{profile_id}  PUT (server/api.py:2311)  DELETE (server/api.py:2322)
    /cancel  POST (server/api.py:1220)
    /clips/{clip_id}  DELETE (server/api.py:1425)  PATCH (server/api.py:1497)
    /clips/{clip_id}/ai-edit  POST (server/api.py:1636)
    /clips/{clip_id}/captions  GET (server/api.py:1564)  PUT (server/api.py:1575)
    /clips/{clip_id}/creator-gaming-layout  GET (server/api.py:1953)  PUT (server/api.py:1967)
    /clips/{clip_id}/export  POST (server/api.py:2262)
    /clips/{clip_id}/frame  GET (server/youtube_api.py:538)
    /clips/{clip_id}/glossary  GET (server/api.py:2204)  POST (server/api.py:2228)
    /clips/{clip_id}/panels  GET (server/api.py:1943)
    /clips/{clip_id}/people  GET (server/api.py:1948)
    /clips/{clip_id}/preview  POST (server/api.py:1724)
    /clips/{clip_id}/publish  GET (server/youtube_api.py:383)  POST (server/youtube_api.py:397)
    /clips/{clip_id}/render  POST (server/api.py:1698)
    /clips/{clip_id}/snap  GET (server/api.py:1938)
    /clips/{clip_id}/source-frame  GET (server/api.py:1930)
    /clips/{clip_id}/thumbnail  POST (server/youtube_api.py:723)
    /clips/{clip_id}/thumbnail/generate  POST (server/youtube_api.py:682)
    /clips/{clip_id}/thumbnail/generated/{index}  GET (server/youtube_api.py:715)
    /clips/{clip_id}/tighten  POST (server/api.py:1596)
    /clips/{clip_id}/translations  GET (server/api.py:2147)
    /clips/{clip_id}/translations/{language}  PUT (server/api.py:2172)  DELETE (server/api.py:2251)
    /clips/{clip_id}/words  GET (server/api.py:2000)
    /creators  GET (server/api.py:2382)
    /creators/merge  POST (server/api.py:2507)
    /creators/split/{account_id}  POST (server/api.py:2521)
    /creators/{creator_id}  GET (server/api.py:2428)  DELETE (server/api.py:2593)
    /creators/{creator_id}/accounts  POST (server/api.py:2492)
    /creators/{creator_id}/branding  POST (server/api.py:2627)
    /creators/{creator_id}/knowledge  DELETE (server/api.py:2549)
    /creators/{creator_id}/knowledge/{knowledge_id}  DELETE (server/api.py:2535)
    /creators/{creator_id}/learning  POST (server/api.py:2613)
    /creators/{creator_id}/memory  DELETE (server/api.py:2577)
    /export/batch  POST (server/api.py:2266)
    /feedback/diagnostics  GET (server/api.py:748)
    /feedback/submit  POST (server/api.py:758)
    /health  GET (server/api.py:711)
    /health/preflight  GET (server/api.py:718)
    /integrations/presets  GET (server/integrations.py:230)
    /integrations/streams  POST (server/integrations.py:234)
    /integrations/streams/{session_id}  GET (server/integrations.py:263)  DELETE (server/integrations.py:283)
    /integrations/streams/{session_id}/link  POST (server/integrations.py:271)
    /jobs  POST (server/api.py:822)  GET (server/api.py:996)
    /jobs/batch  POST (server/api.py:1137)
    /jobs/{job_id}  GET (server/api.py:1004)  PATCH (server/api.py:1085)  DELETE (server/api.py:1106)
    /jobs/{job_id}/log  GET (server/api.py:1200)
    /jobs/{job_id}/move  POST (server/api.py:1049)
    /jobs/{job_id}/retry  POST (server/api.py:1070)
    /languages  GET (server/api.py:2036)
    /media/preview/{clip_id}  GET (server/api.py:1993)
    /media/{clip_id}  GET (server/api.py:2020)
    /models  GET (server/api.py:2830)
    /models/activate  POST (server/api.py:2863)
    /models/pull  POST (server/api.py:2877)
    /models/{tag:path}  DELETE (server/api.py:2939)
    /publish/plan  POST (server/youtube_api.py:560)
    /publish/plan/execute  POST (server/youtube_api.py:629)
    /publish/{job_id}/cancel  POST (server/youtube_api.py:494)
    /queue  GET (server/api.py:1020)
    /queue/clear  POST (server/api.py:1124)
    /queue/pause  POST (server/api.py:1028)
    /queue/resume  POST (server/api.py:1038)
    /remote-render  GET (remote_render/service.py:174)  PUT (remote_render/service.py:178)
    /remote-render/jobs/{job_id}/retry  POST (remote_render/service.py:247)
    /remote-render/pairing-code  POST (remote_render/service.py:195)
    /remote-render/this-pc  PUT (remote_render/service.py:219)
    /remote-render/this-pc/unpair  POST (remote_render/service.py:236)
    /remote-render/videos/{video_id}/render-locally  POST (remote_render/service.py:243)
    /remote-render/workers/{worker_id}  DELETE (remote_render/service.py:205)
    /remote-render/workers/{worker_id}/hold  POST (remote_render/service.py:214)
    /settings  GET (server/api.py:2958)  PATCH (server/api.py:2973)
    /sources/frame  GET (server/api.py:1826)
    /sources/people  GET (server/api.py:1835)
    /sources/snap  GET (server/api.py:1892)
    /sources/suggest  GET (server/api.py:1849)
    /sports  GET (server/api.py:2948)
    /storage  GET (server/api.py:1261)
    /storage/cleanup  POST (server/api.py:1343)
    /storage/videos  GET (server/api.py:1274)
    /system/stats  GET (server/api.py:731)
    /translate  POST (server/api.py:2110)
    /uploadpost/batch  POST (server/uploadpost_api.py:338)
    /uploadpost/capabilities  GET (server/uploadpost_api.py:528)
    /uploadpost/clips/{clip_id}  GET (server/uploadpost_api.py:577)
    /uploadpost/clips/{clip_id}/publish  POST (server/uploadpost_api.py:227)
    /uploadpost/connect  POST (server/uploadpost_api.py:188)
    /uploadpost/connections  GET (server/uploadpost_api.py:168)
    /uploadpost/key  PUT (server/uploadpost_api.py:116)  DELETE (server/uploadpost_api.py:145)
    /uploadpost/profiles  GET (server/uploadpost_api.py:156)
    /uploadpost/refresh/{request_id}  POST (server/uploadpost_api.py:464)
    /uploadpost/retry/{request_id}  POST (server/uploadpost_api.py:492)
    /uploadpost/settings  PATCH (server/uploadpost_api.py:104)
    /uploadpost/status  GET (server/uploadpost_api.py:96)
    /videos  GET (server/api.py:1462)
    /videos/local  POST (server/api.py:886)
    /videos/local/shape  GET (server/api.py:873)
    /videos/{video_id}  DELETE (server/api.py:1359)
    /videos/{video_id}/clips  GET (server/api.py:1489)
    /voices  GET (server/api.py:2062)
    /voices/preview  GET (server/api.py:2076)
    /woopsocial/batch  POST (server/woopsocial_api.py:344)
    /woopsocial/batch/plan  POST (server/woopsocial_api.py:263)
    /woopsocial/clips/{clip_id}/publish  POST (server/woopsocial_api.py:205)
    /woopsocial/connect  POST (server/woopsocial_api.py:178)
    /woopsocial/connections  GET (server/woopsocial_api.py:165)
    /woopsocial/in-flight  GET (server/woopsocial_api.py:416)
    /woopsocial/key  PUT (server/woopsocial_api.py:132)  DELETE (server/woopsocial_api.py:154)
    /woopsocial/refresh  POST (server/woopsocial_api.py:386)
    /woopsocial/refresh/{post_id}  POST (server/woopsocial_api.py:425)
    /woopsocial/schedule  GET (server/woopsocial_api.py:406)
    /woopsocial/settings  PATCH (server/woopsocial_api.py:119)
    /woopsocial/status  GET (server/woopsocial_api.py:111)
    /ws  WEBSOCKET (server/api.py:1447)
    /youtube/categories  GET (server/youtube_api.py:355)
    /youtube/connect  POST (server/youtube_api.py:238)  GET (server/youtube_api.py:265)
    /youtube/credentials  PUT (server/youtube_api.py:198)  DELETE (server/youtube_api.py:217)
    /youtube/default-account  PATCH (server/youtube_api.py:343)
    /youtube/disconnect  POST (server/youtube_api.py:309)
    /youtube/playlists  GET (server/youtube_api.py:368)
    /youtube/settings  PATCH (server/youtube_api.py:176)
    /youtube/status  GET (server/youtube_api.py:168)
    /youtube/uploads  GET (server/youtube_api.py:510)

## 5. The three negative tests — could not be run live; expected outcome from the code

(a) `-H 'Host: evil.example'` → **400, body `Invalid host header`**. `server/api.py:587-589` adds
`TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"]`; starlette's implementation returns
`PlainTextResponse("Invalid host header", status_code=400)`
(`/usr/local/lib/python3.13/dist-packages/starlette/middleware/trustedhost.py:59`). Matches `docs/API.md:94-96`.
Note: the 400 body is plain text, not the `{"detail": ...}` JSON convention of `docs/API.md:152`.

(b) `POST /jobs {"url": "not a link"}` → **accepted, not rejected**: expected `200 {"job_id": <n>}` and a job that
fails later in the worker. `JobIn.url` is a bare `str` (`server/api.py:40`), nothing validates it as a URL.
`create_job` (`server/api.py:822-870`) calls `identify(body.url)`; run offline:
`python3 -c "from sources.dispatch import identify; print(identify('not a link'))"` → `('youtube', None)`
(`sources/dispatch.py:14-25`; `sources/youtube.py:34-38` regex finds no 11-char id). With `vid` None the handler
reaches `d.add_job("process", json.dumps(payload), video_id=vid or "")` (`server/api.py:864`); the comment at
`server/api.py:859-863` says "the pipeline is what should report a bad URL". Only 409 (queue full,
`server/api.py:853-857`) and 422 (no `url`) reject at submit (`docs/API.md:260-262`); a non-http `webhook_url`
is the other 400 (`docs/API.md:304-305`). This is why the test was not run: it would have queued a real job
whose worker calls yt-dlp.

(c) `POST /jobs` with `{"url": "...", "sport": {"name": "fakeball", ...}}` → **400
`{"detail": "sport: unknown sport 'fakeball'; available: soccer, basketball"}`**. `_process_options`
(`server/api.py:480-488`) does `sports.clean(body.sport)` and converts `ValueError` to `HTTPException(400,
f"sport: {e}")`. Run offline: `python3 -c "import sports; sports.clean({'name':'fakeball','highlights':'goals',
'period':'full'})"` → `ValueError -> unknown sport 'fakeball'; available: soccer, basketball`
(`sports/__init__.py:72` `clean()`). `GET /sports` (`server/api.py:2948-2956`) returns `sports.available()`;
offline `[s['id'] for s in sports.available()]` → `['soccer', 'basketball']`. Note `sport` also accepts a bare
string (`sport: dict | str | None`, `server/api.py:49`); `sports.clean('fakeball')` gives the same error.

## 6. Shutdown and repo state

- `pgrep -af 'python3 [m]ain.py'` → "no main.py process running"; `pkill -f 'python3 [m]ain.py.*serve --port 8799'`
  → exit 1 (nothing matched). (A first `pkill -f 'serve --port 8799'` killed its own shell, exit 144, because the
  pattern matched the shell's command line; the bracket pattern avoids that.)
- Isolation held: `probe/data/state.db` (159744 bytes) was created by `StateDB(...)` at `main.py:200` before the
  import failed; `ls /home/user/clips-studio/data` → "No such file or directory".
- `git -C /home/user/clips-studio status --short`, verbatim at the end of the probe:

```
?? docs/platform/research-notes/
```

  Earlier in the probe the same command printed `?? docs/platform/` (first call) and then nothing (second call).
  Explanation, from `git log --oneline -5 -- docs/platform` and `git ls-files docs/platform`: HEAD is now
  `c3bd2c1 Platform: save the overnight brief, the progress log and the decisions log`, which tracks
  `docs/platform/BRIEF.md`, `DECISIONS.md`, `PROGRESS.md` (mtimes 12:47:42), and `docs/platform/research-notes/comfyui.md`
  appeared at 12:51:31. None of this was done by this probe: it ran no git command that changes state and wrote only
  under the scratchpad. No tracked file is modified (`git status --short` shows no ` M` lines; `git diff --cached
  --stat` empty). `__pycache__/` directories are ignored (`.gitignore:42`, `git check-ignore -v`) and pre-date the
  probe (mtimes 10:56-11:01 vs. probe start 12:46).

## 7. How would an outside process run a pipeline through the existing API today, and what is missing?

**What works today (once `fastapi` is installed so the server starts).** An outside process is a plain HTTP
client on loopback: (1) `GET /health` (`server/api.py:711`) then `GET /health/preflight` (`server/api.py:718`,
`docs/API.md:178`) and refuse to continue on any `blocking && !ok` check; (2) `POST /jobs {"url": ...}`
(`server/api.py:822`, `docs/API.md:218`) for a YouTube/Twitch/Kick link, or `POST /videos/local {"path": ...}`
(`server/api.py:886`, `docs/API.md:329`) for a file already on this machine, optionally with `webhook_url` /
`webhook_secret` (`docs/API.md:272-306`) or a `then` action (`JobIn.then`, `server/api.py:39-64`); treat
`{"job_id": null, "already_processed"|"already_queued"}` as an answer (`docs/API.md:254-262`); (3) wait by
polling `GET /jobs/{id}` (`server/api.py:1004`) or `GET /queue` (`server/api.py:1020`), or by listening on
`ws://127.0.0.1:<port>/ws` for `job` / `progress` / `queue` events (`server/api.py:1447`, `docs/API.md:1230-1271`),
or by receiving the one-shot webhook (`server/webhooks.py:8-11`); on failure read `GET /jobs/{id}/log`
(`server/api.py:1200`); (4) read results with `GET /videos` (`server/api.py:1462`) and
`GET /videos/{video_id}/clips` (`server/api.py:1489`, `docs/API.md:490-526`), fetch pixels with
`GET /media/{clip_id}` (`server/api.py:2020`, range requests, `docs/API.md:528-539`) and words with
`GET /clips/{id}/captions` / `GET /clips/{id}/words` (`server/api.py:1564`, `:2000`), and copy files out with
`POST /clips/{id}/export` or `POST /export/batch` (`server/api.py:2262`, `:2266`). `examples/drive_the_api.py:42-167`
is exactly this sequence, and `main.py mcp` wraps the same calls as 13 MCP tools (`docs/API.md:1192-1228`).
The caller chooses the engine's options (`sport`, `focus`, `longform`, `caption_style`, ... `server/api.py:39-64`)
but the engine owns every stage.

**What is missing for an outside process that wants to BE (part of) the pipeline.**
- *Register a new pipeline, stage, source or signal:* no route. Nothing under `/plugin*`, `/pipeline*`, `/register*`,
  `/extensions*` or `/hooks*` exists in the 153 static paths (section 4; `grep -i -E "plugin|register|/pipeline"
  server/api.py` hits only comments at `server/api.py:680` and `:713`). `docs/EXTENDING.md:65-70` says a new platform
  is a file in `sources/` plus an edit to `sources/dispatch.py`; "Bring your own model" (`docs/EXTENDING.md:102-130`)
  is an in-tree edit of `analysis/fusion.py`'s `peak_signals` list. Both are code changes, not API calls. The only
  out-of-process compute hook is remote rendering (`remote_render/service.py:174-247`, `remote_render/gateway.py`),
  which farms out rendering of already-chosen clips to a paired worker, not analysis or selection.
- *Read the transcript:* no route. The transcript is `<data_dir>/transcripts/<video_id>.json`
  (`server/api.py:1309-1310`, `:1542-1546`) and is read only server-side (captions, tighten); `grep -i transcript
  docs/API.md` lists no transcript endpoint; the nearest are the clip-relative `/clips/{id}/captions` and
  `/clips/{id}/words` (`docs/API.md:541-549`), which exist only after the engine has chosen and rendered a clip.
- *Get the downloaded source file (or its path):* no route. `GET /videos` rows carry no path
  (`docs/API.md:477-488`); the download sits at `<data_dir>/downloads/<video_id>.<ext>` (`server/api.py:942`,
  `:1719`, `core/paths.py cached_source()`) and the only pixel routes are per-clip `/media/{clip_id}`,
  `/media/preview/{clip_id}` (`server/api.py:1993`) and single frames `/sources/frame`, `/sources/snap`
  (`server/api.py:1826`, `:1892`, internal). A clip row does expose an absolute local `path` of the rendered clip
  (`docs/API.md:501`), which is a filesystem hint, not a stream.
- *Return its own clips / candidates / scores:* no route creates a clip row. `POST /videos/local` only feeds a file
  into the built-in pipeline (`server/api.py:886-995`); `PATCH /clips/{id}` edits an existing clip
  (`server/api.py:1497`); `POST /clips/{id}/render`, `/preview`, `/tighten`, `/ai-edit` all re-run the engine's own
  cutter on an existing clip (`server/api.py:1596-1724`). `scores` in the clip JSON is read-only output
  (`docs/API.md:513-516`).
- *Report progress into the engine:* no inbound route. Progress is emitted in-process by `core.progress.emit`
  (`core/progress.py:30`) → `broadcaster.publish({"type": "progress", ...})` (`server/jobs.py:121`) → WebSocket;
  the only `POST .../progress` is the render-worker gateway `POST /v1/jobs/{job_id}/progress`
  (`remote_render/gateway.py:122`), on a separate authenticated app for paired render workers.
- *Live progress of a running job over plain HTTP:* `GET /jobs/{id}` returns the DB row only (`server/api.py:1004-1013`;
  fields at `docs/API.md:370-381`, no stage/percent). The worker's `progress_snapshot()` (`server/jobs.py:333-352`:
  stage, label, percent, eta_seconds) is surfaced only via `/integrations/streams/{session_id}`
  (`server/integrations.py:122`) and `/automation/items` (`server/automation.py:254`, `:1067`); otherwise only WS
  `progress` events (`docs/API.md:1241`, `:1252-1256`), which are dropped for slow clients (`docs/API.md:1268-1271`).
- *Operational limits an outside process inherits:* no authentication at all (`docs/API.md:77-83`); loopback only
  with a strict `Host` allowlist (`server/api.py:589`); one worker, one video at a time (`docs/API.md:1356-1358`);
  queue capped at 5 (`docs/API.md:443-449`); no pagination (`docs/API.md:155-157`); webhook is one attempt with a
  10 s timeout (`docs/API.md:296-301`); submit accepts any string as `url` (section 5b), so a bad link is a failed
  job later, not a 4xx now.

In short: today an outside process can *drive* the pipeline end to end (preflight → submit → wait → read clips →
export/publish) and nothing else; it cannot *extend* it. Every seam that would let it contribute — a transcript
to read, a source file to analyse, a candidate list to hand back, a progress channel, a registration call — is
either absent from the route table or reachable only by editing the repo (`docs/EXTENDING.md`).
