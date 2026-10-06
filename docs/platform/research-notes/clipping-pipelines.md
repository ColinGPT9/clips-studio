# Clipping pipelines (Track D): OpusClip, Open Shorts, and four open-source clippers

- Tier: OpusClip (Tier 2), Open Shorts (Tier 2), AI-Youtube-Shorts-Generator / ClipsAI / FunClip / ShortGPT (Tier 3)
- Date read: 2026-10-06 (all URLs below were opened on this date unless marked "not confirmed")
- Brief's question: what contract do existing clipping pipelines expose (video in, what out?), how is OpusClip's remote API shaped as a model for a remote clipping service, and what would an "Open Shorts for Clips Kitty" adapter (success test 2) have to do?
- Verdict (two lines): every pipeline studied already works as "video in -> list of {start, end, title/score} in source seconds out", with rendering a separable second step, so H2 holds. OpusClip's REST API (submit URL, completion webhook, `timeRanges` per clip) is the cleanest model for a remote adapter; Open Shorts can be wrapped out of process over its unauthenticated local REST/MCP surface without forking it, but it duplicates Clips Kitty's own models and runs in Docker, not natively on Windows.

The 13-point template applies loosely to the products; for the open-source pipelines the points that apply are filled (unit, metadata, install, dependencies, versioning, models, local/remote, license, incidents) and the rest kept short.

---

## A. OpusClip public API (Tier 2)

Source of record: https://help.opus.pro/api-reference/overview and the pages linked from https://help.opus.pro/llms.txt (read 2026-10-06). Mintlify serves most `.md` schema pages as empty stubs (e.g. `schemas/project-representation.md`, `schemas/clip-representation.md`, `schemas/curation-preferences.md` returned only a header on 2026-10-06), so field lists below come from the endpoint pages, not the schema pages. Where a schema page was the only source, the item is marked "not confirmed".

### 1. Unit of extension
There is none: OpusClip is a hosted service. The unit of work is a **clip project** (`POST /api/clip-projects`), which yields **clips** (`GET /api/exportable-clips`). Agents reach it through a hosted MCP server and an agent "skill", both wrapping the same API.

### 2. Request and response fields (the "metadata")
Create project: `POST https://api.opus.pro/api/clip-projects`, header `Authorization: Bearer <API_KEY>` (https://help.opus.pro/api-reference/endpoints/create-project). Request body fields as documented:

| Field | Notes |
|---|---|
| `videoUrl` (required) | YouTube, Google Drive, Vimeo, Zoom, Twitch, Facebook, LinkedIn, X, Dropbox, S3 MP4 "and 10+ other platforms" |
| `brandTemplateId` | e.g. `"preset-fancy-Karaoke"`; account default if omitted |
| `uploadedVideoAttr.title` | project name |
| `curationPref.model` | `"ClipBasic"` (default, talking-head) or `"ClipAnything"` (multimodal) |
| `curationPref.clipDurations` | array of `[min, max]` seconds pairs |
| `curationPref.genre` | `"Auto"` or a genre string |
| `curationPref.topicKeywords` | ClipBasic only |
| `curationPref.customPrompt` | ClipAnything only (natural-language prompt) |
| `curationPref.range` | `{startSec, endSec}` window of the source |
| `curationPref.enableAutoHook` | AI headline, off by default |
| `curationPref.skipCurate` | skip clipping entirely |
| `renderPref.layoutAspectRatio` | `"portrait"`, `"landscape"`, `"square"` |
| `renderPref.quickstartConfig.enableRemoveFillerWords` | boolean |
| `importPreference.sourceLang` | e.g. `"de"`; auto-detect by default |
| `conclusionActions[]` | `{type: "WEBHOOK" \| "EMAIL", url \| email, notifyFailure}` |

Response: the page only links to the "Project representation" schema, whose `.md` page served a stub; project `id`/`status` field names are therefore **not confirmed**.

Get clips: `GET https://api.opus.pro/api/exportable-clips?q=findByProjectId&projectId=...` (or `q=findByCollectionId`), headers `Authorization` and `x-opus-org-id` (required for multi-org users), `pageNum`/`pageSize` (https://help.opus.pro/api-reference/endpoints/get-clips). Documented clip fields: `id` (`{project_id}.{curation_id}`), `projectId`, `runId`, `curationId`, `orgId`, `userId`, `uriForPreview`, `uriForExport` (GCS URL of the rendered file), `storageUsed`, `durationMs`, `timeRanges` (described as "Original time ranges of the clip (in seconds)", but the example is `[[192170, 211630]]`, which reads as milliseconds; the unit is **not confirmed**), `keywords`, `promptName`, `title`, `description`, `hashtags`, `text`, `genre`, `subgenre`, `createdAt`, `updatedAt`, `productTier`, `renderPref`. **No virality-score field appears in the documented clip object** (the product UI shows one; whether the API exposes it is not confirmed).

Transcript: `GET https://api.opus.pro/api/transcripts?q=findByProjectId&projectId=...` returns paragraphs `{id, start, end, text, speaker?, words[{word, start, end, isFillerWord}]}` in seconds (https://help.opus.pro/api-reference/endpoints/transcripts/get-transcript.md). For API callers on a range-limited project, paragraphs are filtered to the billed range.

Other documented endpoints (from llms.txt): upload-to-GCS-and-create-project, share project, brand templates, censor jobs (`POST /api/censor-jobs`, `GET /api/censor-jobs/{jobId}`), generative thumbnail jobs (`POST /api/generative-jobs` returns a `jobId` to poll; 7 credits on success, refunded on failure), collections and export, social accounts, social-copy jobs, `POST /api/post-tasks` (publish), `POST /api/publish-schedules` (schedule).

### 3. Job model: submit, complete, fetch
- Submit a URL (or upload to GCS first, then create) and get a project back.
- Completion is signalled through `conclusionActions` (`WEBHOOK` and/or `EMAIL`, with `notifyFailure`). **No "get project status" polling endpoint is listed in llms.txt**; the documented flow is webhook (or email) then `GET /api/exportable-clips`. A status-poll endpoint is not confirmed.
- Webhook security (https://help.opus.pro/api-reference/webhook): each POST carries `X-Opus-Signature` = HMAC-SHA256(secretKey, body + salt), `X-Opus-Salt` (8 random bytes, hex, per request) and `X-Opus-Timestamp`; the secret is the organisation's API secret key (`sk-…`, the first key created). Payload schema, event names beyond the project-creation example, and retry policy are not documented on that page (not confirmed).
- Other async jobs (thumbnail, censor, social copy) are poll-by-`jobId` with `status`, `progress`, `result`.

### 6/7. Access, plans, limits (the "registry/trust" equivalent)
From the overview and https://help.opus.pro/api-reference/limitation: API on Pro (Beta), Max and Business plans; API key from the dashboard (https://clip.opus.pro/dashboard); 1 credit = 1 minute of video; API projects have a 10-credit minimum; Pro/Max cap at 15 hours (900 credits) of API usage per calendar month (403 when hit, resets on the first of the month UTC; Business per contract); 30 requests/minute per key (429); concurrency 4 projects (Pro Beta/Max) or 50 (Business), exceeded -> 429 with `X-Cap-Reason: concurrent`; max video 10 hours / 30 GB; projects expire after 30 days by default; "over 20 languages"; a post to X costs 3 credits. Plan prices in USD: not confirmed here (not on the API pages).

### 8. Models
Only two model names are exposed: `curationPref.model` = `ClipBasic` (talking-head) or `ClipAnything` (multimodal, takes `customPrompt`). Everything else is opaque. `genre` is a tunable input, which matches the H9 observation that products tune per genre.

### 9. Local or remote
Remote only. Agent surfaces: hosted MCP server at `https://mcp.opus.pro/mcp` with OAuth sign-in on first use, "29 tools total: 27 workflow tools plus two deprecated compatibility signposts", including `opusclip_edit_clip` (captions, filler/pause removal, trim/split/reorder, dubbing, undo) (https://help.opus.pro/api-reference/agent-setup, read 2026-10-06); an agent skill installed from `https://github.com/opus-pro/opus-skills.git`, authenticated with the `OPUSCLIP_API_KEY` environment variable, whose page says "Do not paste API keys into chat with the agent" (https://help.opus.pro/api-reference/skill.md).

### 10. Stated limitations
See 6/7. Also: "Pro-tier API access is currently in Beta" (skill page).

### 11. Borrow
- The remote-adapter shape: submit URL -> completion webhook (signed, salted, timestamped) -> fetch clips with `timeRanges` in source time plus a rendered-file URL. Clips Kitty's "remote pipeline" capability can standardise on exactly this: a `conclusionActions`-style callback plus a clips query.
- `curationPref` as the typed input of a clipping capability: `clipDurations` pairs, `range`, `genre`, `topicKeywords`/`customPrompt`. These map one-to-one onto a Clips Kitty pipeline manifest's declared inputs.
- `durationMs` + `timeRanges` as separate fields: a clip can be assembled from several source ranges (OpusClip and Open Shorts both allow multi-segment clips), so the Clips Kitty contract should allow a list of ranges per clip, not a single pair.
- A per-organisation header (`x-opus-org-id`) and per-key rate limits are the right place to put multi-tenant concerns; Clips Kitty's local API has neither and does not need them, but a remote pipeline manifest should declare that the remote side does.

### 12. Avoid
- Docs whose schema pages are empty stubs: Clips Kitty's SDK docs should ship the JSON schema of every I/O type next to the endpoint, generated from code.
- An inconsistent unit (`timeRanges` "in seconds" with a millisecond-looking example). Clips Kitty should state the unit in the field name (`start_sec`/`end_sec` or `start_ms`).
- Signing webhooks with the account's primary API secret ("the first key created"): rotating that key breaks signature verification. Use a per-webhook secret, as Open Shorts does (`webhook_secret`).

---

## B. Open Shorts (mutonby/openshorts) (Tier 2)

Repository: https://github.com/mutonby/openshorts. Read 2026-10-06 from a shallow clone (HEAD `7579e83e39656c7fc82fb3ac55f4965989dd79c7`, 2026-10-06 12:06 +0200, "docs: Parakeet script drift repair"; the commits page showed a later "fix(reframe): keep the YOLO fallback in the presenter-window detector" the same day). GitHub page: 6.2k stars, 1.4k forks, 503 commits on main, topics include `mcp-server`, `agent-skills`, `opus-clip-alternative`. Releases/tags: not confirmed (the GitHub API was not reachable from this session and the page showed none); the only tagged artefact found is the CLI (`cli-vX.Y.Z` tags publish `openshorts` to PyPI, `cli/pyproject.toml` version `0.1.0`).

### 1. Unit
A self-contained application, not a plugin host: FastAPI backend (`app.py`, ~317 KB), a per-job child process (`main.py`), a React/Vite dashboard, an optional Remotion render service for the "AI Shorts" product, all under `docker compose`. There is no extension mechanism of its own; it is itself the thing to wrap.

### 2. Metadata / contract (what an adapter talks to)
Documented in `skills/openshorts/reference.md` and `docs/architecture/agent-access.md` (clone, 2026-10-06):

- `POST /api/process` (JSON or multipart with a `file` part). Fields read by the handler (`app.py` lines 3027-3081): `url`, `acknowledged` (rights attestation, required), `output_format` (`auto|vertical|horizontal|square`), `layouts` (`auto`, `split`, `screencast`, `speaker_cut`, `punch_in`, `none`), `force_low_quality`, `webhook_url`, `webhook_secret`, `target_clips` (1-15), `clip_min_seconds` (5-175), `clip_max_seconds` (10-180, >= min+5), `auto_hook`, `auto_hook_style`, `thumbnail_session_id`, `captions`, `upload_id`, `max_minutes`. Returns `{"job_id", "status": "queued"}`; a quality gate can return HTTP 200 `{"needs_confirmation": true, "quality_check": {...}}`; 402 `quota_exceeded`, 429 too many jobs, 400 duration unknown.
- Local file in: `POST /api/uploads` then `PUT /api/uploads/{upload_id}` (bytes), then `/api/process` with `upload_id`; or a multipart `file` part. 2 GB upload limit (README).
- `GET /api/status/{job_id}` -> `{"status": queued|processing|completed|failed, "logs": [...], "result": {...}}`; `result.clips[]` carries `title`, `video_title_for_youtube_short`, `video_description_for_tiktok`, `video_description_for_instagram`, `start`, `end` (seconds in the source) and `video_url`.
- `GET /api/clip/{job_id}/{clip_index}/edl` -> the clip's recipe: `segments` (source-second ranges), `canonical_range`, `framing`, `duration`, `words[{w,s,e}]`, `current_file`, `has_captions` (`app.py` ~line 4139).
- `POST /api/clip/rerender` with `segments: [{start, end}]` in source seconds, `snap_to_words`, `reapply_captions`, `framing` (`auto|full|track`).
- Webhook: one POST at the terminal state, `{"event": "job.completed"|"job.failed", "job_id", "status", "clips": [{"index","title","video_url","download_url"}], "error"?}`, signed `X-OpenShorts-Signature: sha256=<hmac-sha256(raw body, webhook_secret)>`. The URL "must be public HTTPS; it is validated at submit time and re-resolved at delivery time" (`security_utils.assert_public_url`), so a `127.0.0.1` receiver is refused.
- MCP: `POST /mcp`, stateless streamable-HTTP JSON-RPC with no SDK dependency, 8 tools (`process_video`, `create_upload`, `get_job_status`, `list_clips`, `get_quota`, `add_subtitles`, `recut_clip`, `publish_clip`), each calling back into the same app in-process over `httpx.ASGITransport` "so it can never drift from the REST behaviour"; `mcp_stdio.py` gives the same tools over stdio. `list_clips` returns `{index, title, duration_seconds, video_url, youtube_title, tiktok_description, instagram_description}` (`mcp_server.py` `_clip_summaries`).
- CLI: `pip install openshorts` / `uvx openshorts`, zero dependencies, pure `urllib` against the REST API, `OPENSHORTS_API_URL` / `OPENSHORTS_API_KEY`.
- Agent skill: `skills/openshorts/SKILL.md` (front matter `name: openshorts`, `version: 1.1.0`, `metadata.openclaw.primaryEnv: OPENSHORTS_API_KEY`), following the Agent Skills format; n8n workflows in `examples/n8n/`.
- LLM output schema (`gemini_worker.py`): scoring pass `ScoredWindowModel{id, start, end, score:int, reason}`; detail pass `DetailClipModel{start, end, source_window_id, predicted_score:int, video_description_for_tiktok, video_description_for_instagram, video_title_for_youtube_short, viral_hook_text, why}`; silent-video pass `VisualClipModel` (same minus window id). `predicted_score` is used to rank (`clip_selection.trim_to_best`); whether `/api/status` exposes it per clip is not confirmed (reference.md does not list it).

### 3. Distribution and install
`git clone` + `docker compose up --build` (README). The Dockerfile builds one venv from exact pins, installs the cloud-mode requirements "always so one image serves both modes", clones `Brainicism/bgutil-ytdlp-pot-provider` at build time, installs nightly `yt-dlp[default]`, and pre-downloads `yolov8n.pt` (`RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"`). GPU image via build arg `GPU=1` adds cuBLAS/cuDNN, `onnx-asr`, `onnxruntime-gpu` (~2 GB). Windows: "use Docker Desktop with the WSL2 backend and the Windows NVIDIA driver" (README line 237); no native Windows run is documented. `docker-compose.yml` publishes `8000:8000`, which on Docker's default binds all host interfaces (inferred from the compose file; the file does not bind to 127.0.0.1).

### 4. Dependencies and isolation
One environment per deployment (the container). `requirements.txt` is fully pinned: `torch==2.11.0`, `torchvision==0.26.0`, `ultralytics==8.4.46`, `faster-whisper==1.2.1`, `mediapipe==0.10.14`, `transnetv2-pytorch==1.0.5`, `scenedetect==0.7`, `google-genai==1.75.0`, `fastapi==0.136.1`, etc.; only `yt-dlp` is unpinned (deliberately nightly). CI (`.github/workflows/ci.yml`) runs `pytest tests/` without the heavy ML stack ("the pipeline modules import those lazily behind their feature gates"), which is a pattern worth noting: tests that do not need torch to run.

### 5. Versioning and updates
Rolling: every push to `main` redeploys the hosted service (CLAUDE.md), with a documented handover/drain between containers (`docs/architecture/jobs-and-deploys.md`). No semantic versions for the app itself were found (not confirmed); the CLI is versioned and published to PyPI by tag with Trusted Publishing (`publish-cli.yml`). `alembic/` migrations exist for cloud mode.

### 7. Trust, permissions, auth
Self-host: **no authentication** ("self-host stays BYOK-open"); the MCP endpoint is open too. Rate limiting per IP (`RATE_LIMIT_AUTH` 30/600, `RATE_LIMIT_PROCESS` 60/3600, `RATE_LIMIT_MCP` 240/60; `RATE_LIMIT_ENABLED=0` to disable) and an `FORWARDED_ALLOW_IPS` warning ("NEVER \"*\"") in `.env.example`. Hosted: `osk_` API keys (sha256-stored), `Bearer osk_...` or `X-API-Key`; "API-key auth can never manage keys or delete the account"; OAuth 2.1 with dynamic client registration for claude.ai/ChatGPT connectors mints an ordinary `osk_` key as the access token. `/videos` and `/thumbnails` are authenticated endpoints with time-limited tokens (`MEDIA_TOKEN_TTL_SECONDS`, `MEDIA_URL_TTL_SECONDS`). Containers run as non-root `appuser`. Client API keys (Gemini, fal, ElevenLabs, Upload-Post) are "encrypted client-side, never stored server-side" and sent per request (`X-Gemini-Key`).

### 8. Models
| Stage | Model | Reference / pin | Cache |
|---|---|---|---|
| Transcription (default) | faster-whisper, `WHISPER_MODEL` default `"small"`, `WHISPER_DEVICE` `cpu`, `WHISPER_COMPUTE` `int8` (`subtitles.get_whisper_config`); prod GPU `large-v3-turbo` fp16 | by size name only, no Hugging Face revision pin | `/app/.cache/huggingface` created in the image for a volume |
| Transcription (GPU option) | NVIDIA Parakeet `PARAKEET_MODEL_ID = "nemo-parakeet-tdt-0.6b-v3"` via `onnx_asr.load_model(...)` + Silero VAD (`onnx_asr.load_vad("silero")`) | by name, no revision pin | same |
| Scene detection | TransNetV2 (`transnetv2-pytorch==1.0.5`, env `SCENE_ENGINE`, `TRANSNETV2_THRESHOLD` 0.5) with PySceneDetect `ContentDetector` fallback | package pin; weights source not confirmed | n/a |
| Face / person | MediaPipe face detection; YOLO `YOLO(os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"))` fallback | `yolov8n.pt` fetched by ultralytics at image build | `/tmp/Ultralytics` |
| Moment picking | Gemini `gemini-3.1-flash-lite` (`GEMINI_MODEL` override) or any OpenAI-compatible chat endpoint (`LLM_BASE_URL`, `LLM_MODEL` default `llama3.1:8b`, `LLM_API_KEY`, `LLM_SCORE_BATCH`, `LLM_TIMEOUT`) | remote or local; Ollama/LM Studio/vLLM/llama.cpp/LocalAI/OpenRouter listed | n/a |
| Frame-based stages (layout picker, screencast detector, silent-video clips, hook grounding) | Gemini only; "a text-only `LLM_BASE_URL` server cannot see footage" | | |
| Dubbing / UGC | ElevenLabs, fal.ai (Flux, Hailuo, VEED, Kling) | remote, BYOK | |

`llm_backend.generate_json` asks for `json_schema`, then `json_object`, then no `response_format`, and validates every provider's answer "with the same pydantic model Gemini's `response_schema` uses, so `main.py` sees one shape regardless of provider". README: Ollama's 4096 default context truncates silently, so `OLLAMA_CONTEXT_LENGTH=16384` is required; "7-8B models return valid JSON reliably, 3B ones do not".

### 9. Local, remote or both
Both, same code: self-hosted (Docker, BYOK, no key) and hosted (`https://api.openshorts.app`, `https://mcp.openshorts.app/mcp`, `osk_` keys; free plan 20 min/month with watermark, paid from $12/month per README). Hybrid is explicit: the only cloud call in the clip pipeline is the moment picker, which "sends the transcript (never the video)" to Gemini unless `LLM_BASE_URL` points at a local server.

### 10. Known incidents and limitations (from the repository's own notes; no external advisory found)
- A web search for Open Shorts security advisories or CVEs on 2026-10-06 found nothing relevant (not confirmed that none exist).
- Dockerfile comment: on 2026-08-25 an open range download kept a drained container alive for the whole 900 s stop grace period with its socket closed, so "Traefik sent half of all requests to a dead port (alternating 502/200) for 15 minutes"; fixed with `--timeout-graceful-shutdown 15` and a readiness probe.
- `docs/architecture/jobs-and-deploys.md`: Parakeet v3 "picks the language per VAD segment" and can write English phonetically in Cyrillic (5-oct-2026: 9 of 18 English jobs on disk); repaired by re-transcribing those segments with Whisper.
- `.env.example`: "Privacy, retention and access control (GDPR remediation, 4-sep-2026)": media endpoints moved from static mounts to authenticated, retention added for thumbnails and actor uploads.
- `docs/architecture/clip-selection.md`: the silent-video path uploads the whole video to Gemini and "nothing guards the length" (an hour is ~1.08M tokens).
- `clip_selection.py` docstrings record measured product data: "408 of 429 jobs (95%) delivered 3 clips or fewer" before the scoring pass was changed to rank every window globally (22-sep-2026).
- Jobs auto-clean after 1 hour (README); `MAX_CONCURRENT_JOBS` default 5 must be sized by VRAM.

### 11. Borrow
- The three-surface rule: REST is the only implementation; MCP tools and the CLI call the REST API ("nothing here can drift from what the app actually does"). Clips Kitty's SDK should likewise be a thin client over the existing local API, never a second code path.
- One validated schema for every LLM provider (`llm_backend` + pydantic): the Clips Kitty scoring stage can accept community LLM backends only if they return the same typed shape.
- Source-second `segments[]` as the editable recipe (`/edl`, `/rerender` with `snap_to_words`): the clip contract should carry a list of source ranges and let the renderer snap to words.
- `acknowledged: true` rights attestation on submit, and refusing to retry a quality-gated job automatically.
- Signed webhooks with a per-job secret; `webhook_url` validated against private addresses at submit and at delivery.
- Tests that import pipeline modules behind feature gates so CI needs no torch.
- Exact pins in one container per pipeline (fits H6's "one env per pipeline costs disk but avoids conflicts").

### 12. Avoid
- An unauthenticated service published on all interfaces by the default compose file; Clips Kitty already binds 127.0.0.1 and should require community pipelines that spawn servers to do the same.
- A 317 KB `app.py` with product, billing, SEO and pipeline concerns in one process; keep the pipeline contract small.
- Webhook delivery that refuses localhost: for a local adapter, polling or a local IPC path must stay available.
- Silent dependence on a remote multimodal model for "automatic" features (layout picker, silent video); a Clips Kitty manifest should declare which stages need a remote model and degrade visibly.

### Adapter sketch: "Open Shorts for Clips Kitty" (success test 2)
What it is: an out-of-process pipeline that treats a running Open Shorts instance (local Docker or hosted) as its engine and speaks Clips Kitty's local API on the other side. It copies no Open Shorts code.

| Direction | Mapping |
|---|---|
| Clips Kitty video path in | if the instance is local: `POST /api/uploads` + `PUT /api/uploads/{upload_id}` (or a multipart `file` part on `/api/process`); if hosted: the file must be reachable by URL, so the adapter either uploads to the user's own storage or declines local-only inputs (hosted mode is URL-in only). Then `POST /api/process` with `acknowledged: true`, `upload_id` or `url`, `target_clips`, `clip_min_seconds`, `clip_max_seconds`, `captions: false` (Clips Kitty burns its own captions), `auto_hook: false`, `layouts` empty or `["auto"]`, `output_format: "vertical"` |
| Progress | poll `GET /api/status/{job_id}` every 10-30 s (the CLI polls every 10 s); the `logs[]` lines can be forwarded as Clips Kitty job log; `queue: {position, ahead, eta_seconds}` while queued. Webhooks are not usable for a localhost receiver (public-HTTPS check) |
| Clips Kitty scored ranges out | for each `result.clips[i]`: `start`/`end` (source seconds) -> one range; label from `video_title_for_youtube_short`; hook from `viral_hook_text`; score from `predicted_score` if present in the result (not confirmed), else rank order; `GET /api/clip/{job}/{i}/edl` gives `segments[]` for multi-segment clips and `words[]` |
| Rendered files out | `video_url` (relative to the API base; hosted `download_url` presigned 24 h) can be attached as a pre-rendered 9:16 variant, or ignored so Clips Kitty renders from its own ranges |
| Models | Open Shorts brings its own faster-whisper (`small`/int8 CPU by default), YOLOv8n, MediaPipe, TransNetV2, Parakeet; they live inside the container and duplicate Clips Kitty's. The moment picker can be pointed at Clips Kitty's Ollama (`LLM_BASE_URL=http://host.docker.internal:11434/v1`, `LLM_MODEL=<ollama model>`) for an all-local run; frame-based stages still need a Gemini key or fall back to a plain face-tracking crop |
| Remote option | same adapter, base URL `https://api.openshorts.app` + `Authorization: Bearer osk_...`; `GET /api/me` for minutes; 402 `quota_exceeded`, 429 running-job cap; hosted free tier is watermarked |
| Install | the adapter's manifest must declare a hard dependency on Docker (WSL2 on Windows) or a reachable instance; it cannot ship Open Shorts inside the PyInstaller build |
| License | adapter can be MIT; it uses only the MIT core (`LICENSE`, `NOTICE`); the `cloud/` directory is under the "OpenShorts Commercial License" (`cloud/LICENSE`: self-host for personal/internal use allowed, no hosted/paid offering to third parties) and is never needed because "the application runs fully without the Commercial Software when the `BILLING_ENABLED` flag is not set" |
| Permissions to declare | network to the instance (localhost or internet), read of the job's video, write of clip files; no Clips Kitty-side secrets; the instance itself is unauthenticated when local |

---

## C. Four other open-source clippers (Tier 3)

### C1. AI-Youtube-Shorts-Generator
- Repo: https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator (GitHub page 2026-10-06: 5.3k stars, 960 forks, 27 commits, last commit 2026-09-29). The README's clone command still points at `SamurAIGPT/AI-Youtube-Shorts-Generator`; which of the two is canonical is not confirmed.
- License: MIT ("Copyright (c) 2026 Anil Chandra Naidu Matcha", `LICENSE` via raw.githubusercontent.com, 2026-10-06).
- Stack and pipeline (README, `shorts_generator/highlights.py`): Python 3.10+, CLI `main.py` and library `generate_shorts(...)`. Two modes: `--mode api` (default) routes download, `/openai-whisper` transcription, `gpt-5-mini` ranking and `/autocrop` through the vendor's MuAPI; `--mode local` uses `yt-dlp`, `faster-whisper`, OpenAI `gpt-4o-mini` or Gemini `gemini-2.5-flash` for ranking, and ffmpeg + OpenCV face tracking. Transcript -> content-type classification -> 20-min chunks with 60 s overlap for videos over 30 min -> LLM "virality framework" -> dedupe (>50% overlap keeps the higher score) -> top N -> crop.
- Output shape (README, `highlights.py` line 61): `{"highlights":[{"title","start_time":float,"end_time":float,"score":int(0-100),"hook_sentence","virality_reason"}]}`; `--output-json` dumps `transcript`, all `highlights`, and `shorts[]` with `clip_url`.
- Dependencies: floors only (`faster-whisper>=1.0.0`, `openai>=1.0.0`, `google-genai>=1.0.0`), no lock file; Whisper size by name (`LOCAL_WHISPER_MODEL=base`), no revision pin.
- Incidents: none found (not confirmed). Note the README is largely vendor marketing for the hosted API.
- Mapping: the local mode is already "video in -> scored ranges out"; a Clips Kitty wrapper would call `generate_shorts(path, mode="local")` in a subprocess with its own venv and read `highlights[]`.

### C2. ClipsAI
- Repo: https://github.com/ClipsAI/clipsai (GitHub page 2026-10-06: 544 stars, 100 forks, 68 commits; last commit 2024-01-17 "Fix resize report references"). Docs: https://clipsai.com/ (references: `/references/clip`, `/references/resize`, `/references/transcribe`). PyPI `clipsai` 0.2.1 (`setup.py`).
- License: MIT ("Copyright (c) 2023 Clips AI, Inc.").
- Pipeline (source read via raw.githubusercontent.com, 2026-10-06): `Transcriber` = WhisperX, default `large-v2` on CUDA or `tiny` on CPU (`transcriber.py`); `ClipFinder` = TextTiling over sentence embeddings (`sentence-transformers`, `TextEmbedder`), `cutoff_policy` `high|average|low` (avg ± stdev of depth scores), `min_clip_duration=15`, `max_clip_duration=900` (`clipfinder.py`, `texttiler.py`); `Clip{start_time, end_time, start_char, end_char}` with **no score** (`clip.py`); `resize()` = pyannote.audio diarization (needs a Hugging Face token for the gated model) + `facenet-pytorch`/`mediapipe` to crop to the active speaker, returning `crops.segments`.
- Install: `pip install clipsai` plus `pip install whisperx@git+https://github.com/m-bain/whisperx.git` (unpinned git), libmagic, ffmpeg.
- Versioning/incidents: dormant since January 2024; dependency rot (git-installed WhisperX, gated pyannote) is the practical risk (inferred).
- Mapping: unscored topical segments; a Clips Kitty wrapper would take `clips[]` as candidate ranges and let Clips Kitty's LLM stage score them. Audio-centric only ("podcasts, interviews, speeches, and sermons").

### C3. FunClip (Alibaba / modelscope)
- Repo: https://github.com/modelscope/FunClip (GitHub page 2026-10-06: 6.4k stars, 755 forks, 288 commits; release v2.2.1 2026-09-01; last commit 2026-09-16). Releases ship source archives with `SHA256SUMS`; "Model weights are downloaded separately when FunClip starts".
- License: MIT ("Copyright (c) 2023 Alibaba"); README states model weights are governed by their model pages (Paraformer-Large, SeACo-Paraformer, CAM++ "currently list Apache License 2.0").
- Stack and pipeline (README, `requirements.txt`, 2026-10-06): Python 3.12 venv, `funasr>=1.4.9`, `transformers>=4.32.0,<5.0`, `gradio>=4.31.3,<5.0`, `moviepy==1.0.3`; Gradio UI on :7860 and CLI `funclip/videoclipper.py --stage 1` (recognise -> SRT + state) / `--stage 2 --dest_text '...' --start_ost --end_ost` (cut by chosen text). ASR: Paraformer-Large (Chinese), SeACo hotwords, CAM++ speaker labels, optional Fun-ASR-Nano, SenseVoice, Whisper for English, and the third-party MOSS-Transcribe-Diarize via vLLM, which FunClip "pins ... at revision `e8681d68e7042738ffca8ac8212bc8fcb1131ab8`" of `OpenMOSS-Team/MOSS-Transcribe-Diarize`. LLM clipping: transcript + prompt to any OpenAI-compatible endpoint (`openai`, `g4f`, `dashscope`, prefixes `orcarouter/`, `minimax/`, `atlascloud/` in `funclip/llm/openai_api.py`); the model answers lines like `1. [00:00:00,500-00:00:05,850] text`, parsed by regex in `utils/trans_utils.extract_timestamps`; TwelveLabs Pegasus optional for video-aware selection in the same format.
- Mapping: "video in -> [start-end] text lines out", no numeric score; selection is text- or speaker-driven. Chinese-first; English via Whisper or Fun-ASR-Nano. A wrapper would call stage 1, run its own selection or FunClip's LLM prompt, and map timestamps to ranges.
- Relevant precedent for H7: an explicit Hugging Face commit-hash pin and checksummed release archives.

### C4. ShortGPT
- Repo: https://github.com/RayVentura/ShortGPT (GitHub page 2026-10-06: 8k stars, 1.2k forks, 298 commits on `stable`; last commit 2025-02-10 "add Gemini API key support as first priority API key"). PyPI `shortgpt` 0.1.31 (`setup.py`: `openai==1.37.2`, `moviepy==2.1.2`, `whisper-timestamped`). Docs https://docs.shortgpt.ai/ (title not readable from this session; not confirmed).
- License: MIT ("Copyright (c) 2024 Ray Ventura").
- Shape: a **generator**, not a clipper: `ContentShortEngine` (script -> TTS via ElevenLabs/EdgeTTS -> Pexels/Bing assets -> captions -> MoviePy render), `ContentVideoEngine`, `ContentTranslationEngine` (dub an existing video), an "Editing Markup Language" JSON for LLM-driven edits. Docker + Gradio on :31415, or Colab.
- Mapping: does not fit "video in -> ranges out"; only its caption timing (`whisper-timestamped`) and JSON edit description are adjacent. Not a candidate for a Clips Kitty clipping pipeline; possibly a "publish/variant" idea source.

---

## D. Track D matrix rows

| Platform | Main use case | Generic vs game-specific | Automated highlights | Game-specific logic | Extensibility | What Clips Kitty should learn |
|---|---|---|---|---|---|---|
| OpusClip (API) | Hosted long-form -> shorts for talking-head/podcast; API on Pro(Beta)/Max/Business | Generic, with `curationPref.genre` and `ClipBasic`/`ClipAnything` model switch | Yes: project -> clips with `timeRanges`, `title`, `genre`/`subgenre`, rendered `uriForExport`; no score field documented | None beyond `genre`/`customPrompt` | REST + signed webhooks, hosted MCP (29 tools, OAuth), agent skill repo; no plugins | The remote-adapter shape (submit URL, signed callback, clips query), `clipDurations`/`range` as typed inputs, multi-range clips with `durationMs` |
| Open Shorts (mutonby/openshorts) | Self-hosted or hosted clipper + UGC generator + YouTube tools; Docker | Generic (podcasts, webinars, streams); layouts picked per video | Yes: transcript windows scored by Gemini or any OpenAI-compatible LLM; `{start,end,predicted_score,title,hook}`; silent-video path via Gemini video | None (a public `/gta-5-clips` page documents silent-footage thresholds only) | REST (`/api/process`, `/api/status`, `/edl`, `/rerender`), MCP (8 tools, HTTP + stdio), zero-dep CLI, Agent Skill, n8n; no plugin system | One implementation behind REST with MCP/CLI as thin clients; one pydantic schema for all LLM providers; source-second `segments[]` + word snapping; `acknowledged` attestation; per-job webhook secret; MIT core with a clearly fenced commercial dir |
| AI-Youtube-Shorts-Generator | YouTube URL -> ranked 9:16 shorts; CLI/library | Generic; content-type classification tunes the prompt | Yes: LLM virality framework, `score` 0-100, `hook_sentence`, `virality_reason`, chunking + dedupe | None | Python function `generate_shorts()`, `--output-json`; prompt constants editable | The output JSON is exactly H2's contract; chunk-with-overlap and overlap-dedupe rules |
| ClipsAI | Library: topic-segment podcasts/interviews and reframe to 9:16 | Generic, audio-centric | Partial: unscored TextTiling segments | None | Python classes (`ClipFinder`, `Transcriber`, `resize`) | Candidate ranges without scores are still useful input to a scorer; dormant deps (git WhisperX, gated pyannote) show why pins and model declarations matter |
| FunClip | Local Gradio/CLI clipper on FunASR (Chinese-first), text/speaker/LLM selection | Generic | Partial: LLM returns `[start-end] text` lines; no score; speaker-based cutting | None | CLI stages, OpenAI-compatible LLM gateway prefixes, optional video model | HF commit-hash pin + SHA256SUMS on releases; two-stage CLI (recognise, then cut) as a clean separation of analysis and render |
| ShortGPT | Generate faceless shorts from a script; dub videos | Generic | No (generates, does not clip) | None | Python engines, Editing Markup Language JSON | Not a clipping precedent; only its JSON edit description is adjacent |

---

## E. Hypotheses this track speaks to

- **H1 (out-of-process over the local API is lowest risk): supported.** Open Shorts runs every job as a child process (`main.py`), and its MCP server, CLI and n8n examples all go through the REST API rather than importing the pipeline; the CLI is deliberately zero-dependency. A Clips Kitty adapter for it would be out of process by construction.
- **H2 ("video in, scored and labelled time ranges out" is the smallest useful contract): supported with one amendment.** Open Shorts `{start, end, predicted_score, title, hook}`, AI-Youtube-Shorts-Generator `{start_time, end_time, score, title, hook_sentence}`, ClipsAI `{start_time, end_time}`, FunClip `[start-end] text`, OpusClip `timeRanges` + `title`/`genre`. Rendering is separable in all of them (Open Shorts `/rerender`, OpusClip `uriForExport`, ClipsAI `resize()`). Amendment: allow a list of source ranges per clip (OpusClip `timeRanges[]`, Open Shorts `segments[]`), and make the score optional (ClipsAI and FunClip have none).
- **H5 (declared is not enforced): adjacent evidence.** Open Shorts self-host has no auth at all and the MCP endpoint is open; the only enforcement is per-IP rate limiting. Any pipeline that embeds a local server inherits this and Clips Kitty's manifest should say so.
- **H6 (dependency handling): supported.** Open Shorts pins every package exactly inside one container; AI-Youtube-Shorts-Generator uses floors with no lock; ClipsAI depends on an unpinned git install of WhisperX and has not moved since 2024-01. One environment per pipeline (a container or a venv) is what works in practice here; shared environments are not used by any of them.
- **H7 (Hugging Face revision pins): partly supported.** FunClip pins `OpenMOSS-Team/MOSS-Transcribe-Diarize` at commit `e8681d68e7042738ffca8ac8212bc8fcb1131ab8` and checksums its release archives; Open Shorts, ClipsAI and AI-Youtube-Shorts-Generator reference models by name only (`small`, `large-v2`, `yolov8n.pt`, `nemo-parakeet-tdt-0.6b-v3`). Windows symlink behaviour of the HF cache was not examined here (Open Shorts runs in Linux containers).
- **H8 (typed I/O lets implementations coexist): supported.** Open Shorts validates Gemini and any OpenAI-compatible server against one pydantic schema so the pipeline "sees one shape regardless of provider"; FunClip accepts several LLM gateways behind a model-name prefix (`orcarouter/`, `minimax/`, `atlascloud/`) with one output format.
- **H9 (per-genre tuning, no SDK): consistent.** OpusClip exposes `curationPref.genre` and two curation models; Open Shorts classifies layout per video; neither offers a plugin system.
- **H10 (telemetry opt-in): consistent.** Open Shorts' product analytics (OpenPanel) is "off by default", set at build time, and loads "no third-party script" when unset (`.env.example`).

---

## F. Sources (all read 2026-10-06)

OpusClip
- https://help.opus.pro/api-reference/overview (rate limits, credits, plans, concurrency, limits)
- https://help.opus.pro/llms.txt (page index; curl)
- https://help.opus.pro/api-reference/quickstart
- https://help.opus.pro/api-reference/limitation
- https://help.opus.pro/api-reference/endpoints/create-project (request fields, `conclusionActions`; raw `.md` also via curl)
- https://help.opus.pro/api-reference/endpoints/get-clips (clip fields; raw `.md` via curl shows `timeRanges` example `[[192170, ...]]`)
- https://help.opus.pro/api-reference/webhook (signature headers; payload/retries not documented)
- https://help.opus.pro/api-reference/endpoints/transcripts/get-transcript.md
- https://help.opus.pro/api-reference/agent-setup (hosted MCP server)
- https://help.opus.pro/api-reference/skill.md (agent skill, `OPUSCLIP_API_KEY`)
- https://help.opus.pro/api-reference/schemas/project-representation.md, .../clip-representation.md, .../curation-preferences.md, .../render-preferences.md: loaded but served only a Mintlify stub (not confirmed)
- https://github.com/opus-pro/opus-skills: referenced by the skill page; not opened (not confirmed)

Open Shorts
- https://github.com/mutonby/openshorts (repository page: stars, forks, commits, topics) and https://github.com/mutonby/openshorts/commits/main
- Shallow clone at HEAD `7579e83e…` read: `README.md`, `LICENSE`, `NOTICE`, `cloud/LICENSE`, `CLAUDE.md`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `docker-compose.cloud.yml`, `requirements.txt`, `requirements-billing.txt`, `app.py` (routes, `/api/process`, `/edl`), `mcp_server.py`, `llm_backend.py`, `gemini_worker.py` (schemas), `clip_selection.py`, `transcribe_backends.py`, `subtitles.py` (`get_whisper_config`), `scene_detection.py`, `main.py` (model loading, Gemini default), `cli/openshorts_cli.py`, `cli/pyproject.toml`, `skills/openshorts/SKILL.md`, `skills/openshorts/reference.md`, `examples/n8n/README.md`, `docs/architecture/agent-access.md`, `docs/architecture/clip-selection.md`, `docs/architecture/jobs-and-deploys.md`, `docs/architecture/layouts-and-reframing.md`, `.github/workflows/ci.yml`, `.github/workflows/publish-cli.yml`
- GitHub REST API for this repository: not reachable from this session (releases/tags not confirmed)
- Web search for Open Shorts security advisories: no relevant result (press/blog search, 2026-10-06)

Tier 3
- https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator (page + commits page); raw `README.md`, `LICENSE`, `requirements.txt`, `requirements-local.txt`, `shorts_generator/highlights.py`
- https://github.com/ClipsAI/clipsai (page + commits page); raw `README.md`, `LICENSE`, `setup.py`, `clipsai/clip/clipfinder.py`, `clipsai/clip/texttiler.py`, `clipsai/clip/clip.py`, `clipsai/transcribe/transcriber.py`; https://clipsai.com/ ; https://pypi.org/project/clipsai (from search results, not opened)
- https://github.com/modelscope/FunClip (page + commits page); raw `README.md`, `LICENSE`, `requirements.txt`, `funclip/llm/openai_api.py`, `funclip/utils/trans_utils.py`; release page https://github.com/modelscope/FunClip/releases/tag/v2.2.1 referenced from README (not opened)
- https://github.com/RayVentura/ShortGPT (page + commits page on `stable`); raw `README.md`, `LICENSE`, `setup.py`, `shortGPT/engine/abstract_content_engine.py`; https://docs.shortgpt.ai/ (loaded, title not parsed; not confirmed)
