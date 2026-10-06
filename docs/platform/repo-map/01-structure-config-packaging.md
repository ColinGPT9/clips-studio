# Repo map: structure, configuration, logging, packaging, distribution, updates

Area notes for `docs/platform/repo-map.md`. Repository `/home/user/clips-studio`,
branch `claude/open-platform-w4eh9g` at `1d13723` (684 tracked files, 50 commits, no
git tags in the checkout). Every claim cites a repo-relative path; "(inferred)" marks
anything not seen directly. Nothing in the repository was modified; the only commands
run against the code were read-only Python imports of `core.paths`, `core.binaries` and
`main.load_config` (which write nothing).

---

## 1. Identity in one paragraph

Clips Kitty (called Clips Studio until 1.1.3; `README.md:31`) is a local-first AI
clipping app: an Electron + React + TypeScript desktop shell (`ui/`) that is only a
client of a local FastAPI engine (`server/api.py:1-8`, `ARCHITECTURE.md:851-853`).
Licence AGPL-3.0 (`LICENSE`, `NOTICE`, `ui/package.json` `"license": "AGPL-3.0-only"`).
**The app ships for Windows 10/11 only** (`README.md` Requirements table, "Windows
only for the app"; `ARCHITECTURE.md:947` "Windows packaging"); the engine "should run on
Linux/macOS" but no Mac build exists (`README.md`, issue #62 reference). A Docker image
exists for contributors, not users (`Dockerfile:1-12`, `docker-compose.yml:1-7`).
Three distribution channels, all built from one `ui/package.json` version: a Web Setup
NSIS installer (GitHub release + Hugging Face payload), a Microsoft Store MSIX, and a
winget manifest pointing at the installer (`docs/RELEASING.md` "The other two
channels"). Current app version **2.0.0** (`ui/package.json:3`), supported-API version
**1** (`server/api.py:34`).

---

## 2. Repository structure

Sizes are `wc -l` over `*.py` in each package (measured; tests excluded from engine
totals). Purpose column cites the file that says so.

| Path | Purpose | Size | Cited |
|---|---|---|---|
| `main.py` | CLI entry; subcommands `process`, `run`, `status`, `outro-backfill`, `auth`, `upload`, `serve`, `mcp`, `channels`, `models`, `render-worker`; `load_config()`; forces UTF-8 I/O | 16.7 KB | `main.py` |
| `config/` | `settings.yaml` (the one config file), `prompts/*.txt` (12 LLM prompts), `gaming.yaml`, `sports.yaml`, `audioset_labels.txt` | — | `ARCHITECTURE.md:148-152`, `ls config` |
| `core/` | pipeline orchestration, SQLite state, models, progress, cancel, modes, preflight, binaries, queue, prefetch, housekeeping, scheduler, paths, secrets, scrub, outcome | 18 files / 5,884 lines | `ARCHITECTURE.md:153-166` |
| `server/` | FastAPI app (`api.py`, 86 routes + ws), jobs worker, events, feedback, ai_api, automation, agent, mcp, integrations, webhooks, publisher + per-provider `*_api.py`/`*_service.py` | 18 / 11,595 | `ARCHITECTURE.md:238-250` |
| `analysis/` | the clip engine: audio/visual features, hype, gaming, fusion, highlights, metadata, clip_edit, tighten, voice_turns, panns, intent | 17 / 5,583 | `ARCHITECTURE.md:184-201` |
| `video/` | tracking (YOLOv8), ASD (TalkNet adapter), framing, cropper, captions, cutter, filters, encoding, outro | 15 / 6,771 | `ARCHITECTURE.md:208-217` |
| `video_editor/` | non-destructive edit model and export | 8 / 715 | `ARCHITECTURE.md:218-225` |
| `sources/` | one module per platform + `dispatch.py` (URL → module) | 11 / 1,556 | `ARCHITECTURE.md:167-174` |
| `transcription/` | faster-whisper + cloud STT; `assets/probe.mp3` | 3 / 619 | `ARCHITECTURE.md:175-177` |
| `llm/` | `base.py` interface, `ollama_backend.py`, `registry.py`, `manager.py`, `spec.py`, `providers/` (cloud, catalog, adapters, keys, oauth), `signin/` (ChatGPT plan) | 27 / 2,756 | `ARCHITECTURE.md:178-183` |
| `creator/` | creator profiles / learning | 6 / 974 | `ARCHITECTURE.md:202-207` |
| `gaming/` | Gaming / Reaction layouts; `layouts.json` data | 7 / 1,840 | `ARCHITECTURE.md:226-232` |
| `sports/` | registry in `__init__.py`; `core/`; `soccer/`; `basketball/` | 25 / 6,327 | `ARCHITECTURE.md:233-237`, `sports/__init__.py:26` |
| `longform/` | 16:9 profiles registry and assembly | 7 / 776 | `ARCHITECTURE.md:252-257` |
| `multilingual/` | translate, subtitles, burn, dub (Piper), voices | 10 / 1,405 | `ARCHITECTURE.md:258-267` |
| `publish/` | `base.py` Publisher ABC, YouTube, WoopSocial, Upload-Post, metadata, quota, schedule, oauth | 12 / 2,943 | `ARCHITECTURE.md:251` |
| `remote_render/` | second-PC rendering: settings, protocol, tls, gateway, queue, worker, service | 10 / 2,051 | `ARCHITECTURE.md:252` |
| `third_party/` | vendored TalkNet-ASD (MIT) + icon notices; must never be edited | 9 / 522 | `third_party/talknet/README.md`, `.github/CODEOWNERS` |
| `models/` | **absent in a checkout**; created by `scripts/fetch_asd_model.py`, `fetch_panns.py`, `fetch_voice_model.py` | — | `ls models` fails; `ARCHITECTURE.md:254` |
| `vendor/` | **absent in a checkout, gitignored**; `ffmpeg/`, `ollama/`, `whisper/` written by `scripts/fetch_*.py` | — | `.gitignore` `/vendor/`; `scripts/fetch_ffmpeg.py:36` |
| `scripts/` | build/fetch/sign/sitemap scripts (not packaged) | 14 / 2,019 | `clips-studio.spec` comment "scripts/, which is not packaged at all" |
| `tests/` | pytest, deterministic only | 105 / 26,427 | `tests/README.md` |
| `examples/` | `drive_the_api.py`, `score_a_transcript.py`, `fake_backend.py` | 3 / 374 | `examples/README.md` |
| `ui/` | Electron app: `src/main` (3 files), `src/preload`, `src/renderer` (90 TS/TSX files, 21,706 lines), `electron-builder.yml`, `build/` (icon, afterPack, appx tiles, release-notes.md) | — | `ui/` listing |
| `packaging/winget/` | three winget manifests + README | — | `packaging/winget/README.md` |
| `docs/` | API.md, EXTENDING.md, RELEASING.md, MSSTORE.md, DOCKER.md, AI-BACKENDS.md, SPORTS.md, GAMING.md, REMOTE-RENDERING.md, …; `docs/platform/BRIEF.md` (the open-platform brief) | — | `ls docs` |
| `skills/clips-kitty/SKILL.md` | an agent skill for MCP clients | — | `README.md:738-739` |
| `site/` | static website (GitHub Pages + HF Space mirror) | — | `.github/workflows/mirror.yml` |
| `web/` | "Clips Kitty Web": a browser-only Next.js taster, no server, OpenRouter + ffmpeg.wasm | — | `web/README.md` |
| `whop-app/` | marketing pages only; never talks to the engine | — | `whop-app/README.md` |
| `feedback-relay/`, `twitch-proxy/` | Cloudflare Workers (bug-report relay; CORS proxy for the web version) | — | their `README.md` |
| `assets/outro/` | 4 prebuilt end-card MP4s (build inputs, bundled) | — | `.gitignore` `!assets/outro/*.mp4`, `clips-studio.spec` |
| `data/` | runtime artefacts, gitignored | — | `.gitignore` `data/` |

Vendored and generated code is excluded from lint and scanning: `pyproject.toml`
`[tool.ruff] exclude = ["ui","vendor","third_party","build","dist","release","data","site"]`;
`.github/codeql/codeql-config.yml` `paths-ignore: third_party, vendor, ui/node_modules,
build, dist, release`; `.gitattributes` marks `third_party/**` and `vendor/**`
`linguist-vendored`; CI job `vendored` fails a PR that touches `third_party/`
(`.github/workflows/ci.yml` "Refuse silent edits to third_party/").

---

## 3. Processes, ports and entry points

| Process | Started by | Listens on | Cited |
|---|---|---|---|
| Python engine (`python main.py serve` / `api.exe serve --port 8765`) | Electron main `startBackend()`; or by hand | `127.0.0.1:8765` by default; `--host 0.0.0.0` only for containers | `main.py` `p_serve` args; `ui/src/main/index.ts` `API_PORT = 8765` |
| Bundled Ollama (`_internal/ollama/ollama.exe serve`) | Electron `startOllama()` **only when packaged** | `127.0.0.1:11435` (`OLLAMA_HOST`), models in `%LOCALAPPDATA%\Clips Studio\data\models` (or legacy `Clips Kitty\data\models`) | `ui/src/main/index.ts` `OLLAMA_PORT = 11435`, `startOllama` |
| Remote-render gateway | `remote_render/service.py` when enabled | port `8766` default, TLS, paired workers | `remote_render/settings.py` `DEFAULTS["port"] = 8766` |
| Render worker child | `api.exe render-worker --run-job <dir>` / `python main.py render-worker` | — | `remote_render/worker.py:82-87`, `remote_render/service.py:56-62` |
| MCP server (`api.exe mcp`) | an MCP client | stdio; talks to engine at `CLIPS_STUDIO_API` or `http://127.0.0.1:8765` | `server/mcp.py:52-55` |
| Vite dev server (`npm run dev:web`) | contributors / Docker `ui` service | `5173` | `ui/vite.config.mts`, `docker-compose.yml` |

Electron detail that matters to anything spawning the engine: in packaged mode the
backend is spawned with `stdio: 'ignore'`, `windowsHide: true`, env
`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `CLIPS_STUDIO_OLLAMA_HOST=http://127.0.0.1:11435`;
in dev it runs `python main.py serve --port 8765` with `cwd` = repo root and
`stdio: 'inherit'`, unless `BACKEND_EXTERNAL=1` (`ui/src/main/index.ts` `startBackend`).
Electron takes down the whole process tree with `taskkill /T /F` on quit (`killTree`).
Single-instance lock (`app.requestSingleInstanceLock()`); AppUserModelId
`com.clipsstudio.app` must match `appId` (`ui/src/main/index.ts`, `ui/electron-builder.yml`).
The renderer is sandboxed (`contextIsolation: true`, `nodeIntegration: false`) and
reaches native features only through `window.studio` from `ui/src/preload/index.ts`
(file pickers, clipboard-key read, notify, allow-listed `openExternal`, tray, update
IPC). Page CSP: `default-src 'self'; connect-src 'self' http://127.0.0.1:8765
ws://127.0.0.1:8765; media-src http://127.0.0.1:8765; img-src 'self' data:
http://127.0.0.1:8765; style-src 'self' 'unsafe-inline'`
(`ui/src/renderer/index.html:7-8`). The renderer hardcodes `API_BASE =
'http://127.0.0.1:8765'` (`ui/src/renderer/src/lib/api.ts:49`) and `WS_URL =
'ws://127.0.0.1:8765/ws'` (`ui/src/renderer/src/lib/useEvents.ts:4`).

---

## 4. Versions: where each number lives

| Number | Value now | Defined in | Read by |
|---|---|---|---|
| App version | `2.0.0` | `ui/package.json:3` — "Version lives in ui/package.json only" (`docs/RELEASING.md` "Version bump") | engine: `server/feedback.py:260-290` `_app_version()` reads `ui/package.json` in a checkout, `sys._MEIPASS/package.json` when frozen (bundled by `clips-studio.spec` datas `(ROOT/"ui"/"package.json", ".")`); exposed at `GET /health` as `app_version` (`server/api.py:716`); MCP `serverInfo` (`server/mcp.py:58-64`); remote render handshake (`remote_render/protocol.py` `app_version()`); electron-builder artefact names `${version}` |
| Supported API version | `API_VERSION = 1` | `server/api.py:34` "Bumped only when a supported endpoint changes shape, never for additions" | `GET /health` → `api_version` (`server/api.py:716`); `docs/API.md:165-176`; `server/integrations.py:11`; MCP prints it (`server/mcp.py:444`) |
| FastAPI app version string | `"0.1"` | `server/api.py:576` `FastAPI(title="Clips Kitty API", version="0.1")` (stale, informational only) | `/openapi.json` |
| MCP protocol | `PROTOCOL_VERSION = "2025-06-18"`, known `2025-03-26`, `2024-11-05` | `server/mcp.py:38-39` | MCP clients |
| Remote-render protocol | `PROTOCOL = 1` | `remote_render/protocol.py:16` | job ids, worker compatibility |
| Store package version | app version + `.0` (`2.0.0.0`); major may never be 0 again | `scripts/build_msix.py` `store_version()`; `docs/MSSTORE.md` "About the version number" | electron-builder via `-c.extraMetadata.version` |
| winget | `PackageVersion: 2.0.0`, `PackageIdentifier: ColinGPT9.ClipsKitty` | `packaging/winget/*.yaml` | winget-pkgs PR |
| Pinned Ollama runtime | `OLLAMA_VERSION = "v0.32.6"` (`VERSION.txt` stamp in `vendor/ollama/`) | `scripts/fetch_ollama.py` | build only |
| Electron / builder | `electron ^31.0.0`, `electron-builder ^24.13.3`, `electron-updater ^6.8.9`, `overrides.tar ^7.5.22` | `ui/package.json` | `docs/DEPENDENCY-SECURITY.md` explains the pins |
| Schema version | **none**: SQLite migrations are additive `ALTER TABLE … ADD COLUMN` guarded by `PRAGMA table_info` | `core/state.py:366-392` `_migrate()` | — |
| Changelog | `## Unreleased`, `## 2.0.0`, `1.2.0`, `1.1.4`, `1.1.3`, `0.1.2 (2026-08-10)`, `0.1.1`, `0.1.0`; written for creators; Added/Changed/Fixed subsections; `(#NNN)` issue refs | `CHANGELOG.md:1-30,109,574,710,788,856-907` | release notes in-app (`ui/build/release-notes.md`) |

The website download buttons and `whop-app/lib/content.ts` / `web/lib/content.ts`
`VERSION` carry the version too and must be bumped by hand (`docs/RELEASING.md` "The
website's download buttons name the version").

---

## 5. Configuration: how settings are loaded, where they live, how they are written

### 5.1 The config file and its resolution

* Bundled defaults: `config/settings.yaml` (`main.py` `BUNDLED_CONFIG`). Frozen build
  bundles it as `_internal/config/settings.yaml` (`clips-studio.spec` datas).
* The file an install **reads and writes**: `main.py` `CONFIG_PATH =
  user_config_path(BUNDLED_CONFIG)`. `core/paths.py:83-124` `user_config_path()`:
  checkout → the repo file; frozen → `%LOCALAPPDATA%\Clips Studio\settings.yaml`,
  seeded by copying the bundled file on first use (because a per-machine install or an
  MSIX package makes the bundle read-only, `core/paths.py:95-100`). Falls back to the
  bundled file if the copy cannot be written.
* `--config <path>` overrides it on every CLI subcommand (`main.py` parser).
* `main.load_config(path)` (`main.py`): `yaml.safe_load`, then **normalisation** of the
  quick-setup keys onto the full structure:
  * `model: X` → `llm.backend = "ollama/X"` unless `X` contains `/` (then used as-is,
    e.g. `openrouter/<model>`);
  * `channel` → prepended to `channels`;
  * `auto_upload` → `upload.enabled`; `privacy` → `upload.privacy`;
  * env `CLIPS_STUDIO_OLLAMA_HOST` → `llm.ollama_host`;
  * env `CLIPS_STUDIO_DATA_DIR` → `paths.data_dir`;
  * `paths.data_dir` ← `str(resolve_data_dir(config))` (always absolute afterwards);
  * `llm.data_dir` ← same path ("where a cloud backend finds the user's own API key").
  Verified by import on this box: top-level keys after load are `analysis, auto_upload,
  channel, channels, clips, content_language, feedback, llm, model, paths,
  poll_interval_minutes, privacy, scoring, tracking, transcription, upload, video,
  whisper`; `llm` becomes `{translation_model, temperature, num_ctx, ollama_host,
  backend: 'ollama/gemma:7b', data_dir}`.
* The server gets `create_app(config, settings_path)` (`server/api.py:572`) and keeps
  the dict in memory for the process lifetime; the `/settings` PATCH says "restart serve
  to apply pipeline-level changes" (`server/api.py:3020`).

### 5.2 `config/settings.yaml` top-level sections (whole file read)

| Section / key | Default | Note for an SDK |
|---|---|---|
| `model` | `gemma:7b` | Ollama tag, or `provider/model` for cloud (`ARCHITECTURE.md:909-910`) |
| `channel` | `""` | watched channel handle |
| `content_language` | `auto` | ISO code or auto |
| `auto_upload`, `privacy` | `false`, `public` | CLI daemon only |
| `channels` | `[]` | extra watched channels |
| `poll_interval_minutes` | `15` | used by `server/automation.install(..., interval_minutes=…)` |
| `llm` | `translation_model: ""`, `temperature: 0.4`, `num_ctx: 8192`, `ollama_host: http://localhost:11434` | plus derived `backend`, `data_dir` |
| `whisper` | `model: auto`, `device: auto` | auto = large-v3-turbo on GPU / small on CPU |
| `transcription` | `backend: local`, `model: ""` | cloud STT on own key; rewritten by `server/ai_api.py:92-101` `write_transcription()` |
| `clips` | `min_score: 55`, `max_clips_per_video: 0`, `min_duration: 10`, `max_duration: 60`, `vertical: true`, `captions: true`, `outro: true` | per-job overrides arrive in `JobIn` (`server/api.py:38-60`) |
| `scoring` | `weights {text .30, visual .20, reaction .20, audio .20, engagement .10}`, `signal_peak_percentile: 85`, `creator_context_max: 6`, `action_bonus: 10`, `audience_bonus: 8`, `rerank_pool: 8`, `reaction_top_k: 8` | fusion knobs (`docs/EXTENDING.md` "Tune before you code") |
| `analysis` | `max_overlap .4`, `max_text_similarity .7`, `max_segment_reuse .4`, `chunk_seconds 300`, `chunk_overlap_seconds 45`, `long_video_threshold_seconds 420` | |
| `video` | `encoder: auto`, `parallel_renders: 3` | `nvenc/amf/qsv/cpu` |
| `tracking` | `detector: yolov8n-pose.pt`, `sample_fps: 8` | resolved via `core.binaries.yolo_weights()` |
| `upload` | `daily_limit: 6`, `client_secret: config/client_secret.json` | CLI daemon; path resolved by `core.paths.resolve_config_file()` |
| `feedback` | `relay_url: https://clips-studio-feedback.clipsstudio.workers.dev` | empty = save reports to file |
| `paths` | `data_dir: data` | relative → see §6 |

**Not in settings.yaml at all** (so an installed build never sees new YAML keys,
`remote_render/settings.py:4-6`): remote rendering, AI provider keys, publishing
provider keys/settings, YouTube credentials. Those live in the credential store (§5.4).
`config/gaming.yaml` and `config/sports.yaml` are data read at runtime
(`sports/__init__.py:19-31` `knowledge()`), and `config/prompts/*.txt` are the LLM
prompts ("Prompts are data, not code", `ARCHITECTURE.md:940-944`).

### 5.3 How settings are written

There is no generic "save config" function. Every writer does a **regex rewrite of
the YAML text** to preserve the user's comments:
* `llm/manager.py:192-201` `switch_model(settings_path, tag)` rewrites the `model:` line.
* `server/api.py:2955-3020` `PATCH /settings` rewrites `model`, `channel`, `auto_upload`,
  `privacy`, `content_language` (top-level, anchored at column 0) and the nested
  `llm.translation_model`, `clips.outro`, `clips.min_score` (inserting a line under
  `clips:` if missing). `GET /settings` (`server/api.py:2940`) returns only those eight.
* `server/ai_api.py:92-101` `write_transcription()` replaces or appends the whole
  `transcription:` block.
* `core/queue.py:238` `update_settings(db, job_id, payload)` is per-job options in
  SQLite, not the YAML.

### 5.4 Other stores an SDK needs to know about

| Store | Location | Contents | Cited |
|---|---|---|---|
| SQLite `state.db` | `<data_dir>/state.db` | videos, clips, jobs, uploads, creators, …, `app_state` key/value (`get_flag`/`set_flag`), `streams` | `core/state.py:251,366,1402-1406`; `ARCHITECTURE.md:882-900` |
| Credential store | `<data_dir>/credentials/<name>.bin` (Windows DPAPI, entropy `clips-kitty/publish/v1`) or `<name>.secret.json` 0600 elsewhere | names seen: `youtube_token`, `youtube_client` (`server/youtube_*`), `ai_key_<provider>` (`llm/providers/keys.py:15` `PREFIX = "ai_key_"`), `remote_render` (`remote_render/settings.py` `NAME`), Upload-Post key via `server/uploadpost_service.py:82-95` (name not read), ChatGPT sign-in (`llm/signin/chatgpt.py`) | `core/secrets.py` `_DIR = "credentials"`, `backend_name()`, `save/load/wipe/has/migrate_plaintext` |
| Electron userData | `%APPDATA%\clips-studio\` (derived from package.json `name`) | renderer localStorage (caption style, toggles, UI language), `update-prefs.json` (`channel`, `skipped`), `tray.json` (`keepInTray`) | `ui/electron-builder.yml:10-20` comment; `ui/src/main/updater.ts` `PREFS_FILE`; `ui/src/main/index.ts` `trayPrefsPath` |
| Per-job options | `jobs.payload` JSON in SQLite | `JobIn`/`JobPatch` fields | `server/api.py:38-60`, `core/queue.py` |
| Ollama models | `%LOCALAPPDATA%\Clips Studio\data\models` (packaged) via `OLLAMA_MODELS` env | GGUF weights | `ui/src/main/index.ts` `startOllama` |
| Piper voices | `data/voices/` downloaded on first dub | | `requirements.txt` piper comment; `docs/MSSTORE.md` "Known gaps" |

### 5.5 Environment variables the engine honours (complete list from grep)

`CLIPS_STUDIO_DATA_DIR` (`main.py`, `Dockerfile`, `docker-compose.yml`),
`CLIPS_STUDIO_OLLAMA_HOST` (`main.py`; set by Electron and compose),
`CLIPS_STUDIO_FFMPEG` / `CLIPS_STUDIO_FFPROBE` / `CLIPS_STUDIO_OLLAMA` (binary overrides,
`core/binaries.py:59-62` `_resolve` reads `CLIPS_STUDIO_{NAME}`),
`CLIPS_STUDIO_API` (MCP client base URL, `server/mcp.py:52-55`),
`LOCALAPPDATA` (`core/paths.py`), `CUDA_PATH`, `PATH`, `WINDIR` (build/diagnostics),
`PYTHONIOENCODING`/`PYTHONUTF8` set by Electron, `BACKEND_EXTERNAL=1` and
`ELECTRON_RENDERER_URL` read by Electron main (`ui/src/main/index.ts`).

---

## 6. `data_dir` and path safety

`core/paths.py:19-50` `resolve_data_dir(config)`: absolute `paths.data_dir` is honoured
as-is; a relative one resolves against `%LOCALAPPDATA%\Clips Studio\<raw>` when
`sys.frozen`, else `<repo root>/<raw>` (never the CWD, because Electron sets none).
The folder keeps the pre-rename name "Clips Studio" on purpose (`core/paths.py:33-40`).
Verified on this box (not frozen): default → `/home/user/clips-studio/data`;
`/mnt/big` → `/mnt/big`. `docs/API.md:105-125` documents that an installed build and a
checkout are therefore **separate libraries** that can fight over port 8765.

Known subfolders of `data_dir` (from code, not a spec): `downloads/`, `transcripts/`,
`voice_profiles/`, `clips/<creator>/<title [id]>/…` with `Longform/*` subdirs, `previews/`,
`logs/job_N.log`, `credentials/`, `models/` (Ollama), `voices/` (Piper), `state.db`,
legacy `youtube_token.json` (`core/housekeeping.py` `survey()`; `server/jobs.py:54`;
`longform/profiles.py` `subdir`; `core/secrets.py`; `main.py` `auth`).
Scratch suffixes swept by housekeeping: `.source.mp4`, `.edited.mp4`, `.plain.mp4`,
`.cropped.mp4` (`core/housekeeping.py:21`).

Path-safety primitives any plugin surface should reuse rather than re-implement
(`.github/codeql/codeql-config.yml` says so explicitly): `safe_name(name)` for stored
filenames, `picked_file(raw, allowed_suffixes)` for dialog-chosen files (rejects UNC,
non-regular files, wrong suffix), `within(base, candidate)`, `cached_source()`,
`discard()` (`core/paths.py:127-300`).

---

## 7. Binaries, weights and bundled data resolution

`core/binaries.py` is the single door for bundled tools. `_search_roots(folder)`
(`core/binaries.py:33-55`) returns, when frozen: `<exe dir>`, `<exe dir>/<folder>`,
`<exe dir>/_internal/<folder>`, `sys._MEIPASS`, `sys._MEIPASS/<folder>`; always also
`<repo>/vendor/<folder>`. `_resolve(name, folder)` (`:58-70`, `@cache`): env override
`CLIPS_STUDIO_<NAME>` → those roots → `shutil.which` → bare name.

| Function | Folder / file | Fallback |
|---|---|---|
| `ffmpeg()`, `ffprobe()` | `ffmpeg/ffmpeg.exe`, `ffmpeg/ffprobe.exe` | PATH, then bare name; `missing()` reports absence for preflight |
| `ollama()`, `has_bundled_ollama()` | `ollama/ollama.exe` | PATH (dev) |
| `whisper_model(size)` | `whisper/<size>/model.bin` | bare size → faster-whisper downloads from HF (checkout only) |
| `yolo_weights(name)` | `weights/<name>` roots + repo root | bare name → ultralytics downloads |
| `haar_cascade(filename)` | `cascades/<xml>` then `cv2.data.haarcascades` | `None` → skip face refinement |
| `bundled_whisper_sizes()` | lists `whisper/*` with `model.bin` | preflight |

Other bundled data found by their own module, all via `sys._MEIPASS` when frozen,
`<repo>/models/` otherwise: TalkNet weights `pretrain_TalkSet.model`
(`video/asd.py:80-85`), PANNs `panns_mobilenetv1.pth` (`analysis/panns.py:46-50`),
voice models `pyannote_segmentation_3.onnx` / `wespeaker_resnet34_lm.onnx`
(`analysis/voice_turns.py:103-107`), end cards `assets/outro` (`video/outro.py:972-982`),
cuBLAS 12 `nvidia/cublas/bin` (`transcription/transcriber.py:32-42`), the sport
knowledge `config/sports.yaml` resolved relative to `sports/__init__.py`
(`sports/__init__.py:22`), `gaming/layouts.json` beside `gaming/framing.py`
(`clips-studio.spec` comment). Each has an `available()` gate so a missing file degrades
the feature instead of crashing (`ARCHITECTURE.md:968-972`).
`core/preflight.py` turns this into the seven checks served at `GET /health/preflight`
(`ffmpeg`, `ffprobe`, `ollama`, `model`, `whisper`, `gpu`, `disk`;
`docs/API.md:178-201`; `Check(name, ok, detail, fix, blocking)` dataclass
`core/preflight.py:29-35`).

---

## 8. Logging convention

* **No `logging` module anywhere in the engine** (grep for `import logging` /
  `getLogger` over `*.py` excluding `third_party` returned nothing). The pipeline
  `print()`s: ~300 `print(` calls across packages (core 87, analysis 46, server 35,
  video 33, multilingual 23, sports 21, longform 21, remote_render 16, transcription 15).
  Style: stage banners like `print(f"[1/4] Downloading: {url}")`
  (`core/pipeline.py:204`) and indented parenthetical notes like
  `print(f"      (could not remove {path.name}: {e})")` (`core/paths.py`).
  `pyproject.toml` ruff ignores note "Prompts and pipelines print a lot".
* **Capture**: `server/feedback.py:31-82` installs a `_Tee` over `sys.stdout`/`stderr`
  (`install_log_capture()`, called from `create_app`, `server/api.py:575`) that keeps a
  400-line ring (`_LOG_LINES = 400`, lines truncated to 500 chars) and, while a job runs,
  appends to that job's file. `server/jobs.py:150-152`: `data/logs/job_<id>.log`, path
  stored on the job row (`core/state.py:425` `log_path` column), served by
  `GET /jobs/{id}/log` (`server/api.py:1192`, `docs/API.md:389`), pruned to the newest
  50 (`server/jobs.py:31` `_KEEP_LOGS = 50`).
* In a **packaged** build the engine's stdout is discarded by Electron (`stdio:
  'ignore'`), so the per-job files and the ring are the only persistent engine logs;
  in dev it is inherited to the terminal (`ui/src/main/index.ts` `startBackend`). The
  frozen exe is a console app precisely so `print()` has somewhere to go
  (`clips-studio.spec` header; `CONTRIBUTING.md` "frozen as a console app on purpose").
  `main.py:_force_utf8_io()` replaces a `None` stdout with `os.devnull` and reconfigures
  to UTF-8 (`errors="replace"`).
* **Rule for the MCP subcommand**: nothing may print; stdout carries the protocol
  (`main.py` `mcp` branch; `server/mcp.py`).
* **Redaction**: bug reports and provider errors pass through `core/scrub.py`
  `SECRET_PATTERNS` / `scrub_secrets()` and `server/feedback.redact()`; diagnostics are
  built from an allowlist (`server/feedback.py:293-312` `_SETTINGS_ALLOWLIST = ("clips",
  "scoring", "video", "tracking", "analysis")`). `core/secrets.py` never logs a value.
* Electron main uses `console.error/warn` only; `autoUpdater.logger = null`
  (`ui/src/main/updater.ts`). KNOWN-ISSUES: "A failed update disappears silently…no
  log is kept" (`KNOWN-ISSUES.md` "A failed update disappears silently").
* **Structured progress is separate from logging**: `core/progress.py` `emit(**event)`
  with a single process-wide handler (`set_handler`) and per-thread tags; the server
  broadcasts them (`server/events.py` `Broadcaster`, 200-event queue per client,
  drops when full) over `ws://127.0.0.1:8765/ws`. Six event `type`s: `queue`, `job`,
  `progress`, `model_pull`, `publish`, `automation` (`docs/API.md:1230-1270`).
  `progress.stage` values: `download`, `downloaded`, `converting source to H.264`,
  `transcribe`, `analyze`, `render`, `done` (+ `prefetch` restamp). Stage weights for
  the job bar in `server/jobs.py:40-47` (`transcribe .15/.25`, `signals .40`,
  `analyze .45`, `ranking .65`, `reactions .70`, `render .78/.22`).
* **Webhooks**: `POST /jobs` with `webhook_url` (+ `webhook_secret`) → one POST with
  `{"event": "job.done"|"job.failed"|"job.cancelled", job_id, job_type, status,
  video_id, title, clips, error}` signed `X-Clips-Kitty-Signature: sha256=<hmac>`;
  one attempt, 10 s timeout, never raises (`server/webhooks.py:1-49`).

---

## 9. The frozen build: how it is laid out at runtime

Built by `python scripts/build_installer.py` (Windows build machine; `docs/RELEASING.md`
"Build"), which runs: 1 check tools (PyInstaller, **CUDA** torch with `sm_120`, npm) →
2 `ensure_vendored()` runs each `scripts/fetch_*.py` if its marker is missing →
3 `python -m PyInstaller clips-studio.spec --distpath build/dist --workpath build/work` →
3b smoke test `build/dist/backend/api.exe status` → 4 `npm run build` in `ui/` →
5 `npx electron-builder --win --config electron-builder.yml` → `release/`
(`scripts/build_installer.py` `check_tools`, `ensure_vendored`, `freeze_backend`,
`smoke_test_backend`, `build_ui`, `package_installer`). Takes ~2.5 h and ~30 GB
(`docs/RELEASING.md`). **Not built in CI**: a GitHub runner has 14 GB disk
(`.github/workflows/msix.yml` header; `docs/MSSTORE.md` "Known gaps").

### 9.1 PyInstaller spec (`clips-studio.spec`)

* One-**dir** (`COLLECT(... name="backend")`), console app (`EXE(name="api",
  console=True, upx=False)`), entry `main.py`, `pathex=[ROOT]`.
* `collect_all` for packages that resolve things at runtime: `yt_dlp`, `ultralytics`,
  `faster_whisper`, `ctranslate2`, `curl_cffi`, `piper`, `onnxruntime`,
  `rapidocr_onnxruntime`, `shapely`, `pyclipper`, `nvidia.cublas`.
* `collect_submodules("uvicorn")`, `collect_submodules("sports")` ("the registry imports
  each sport's package by name"), plus explicit hidden imports for lazy imports:
  `torch`, `torchvision`, `cv2`, `psutil`, `pynvml`, Google API libs, every `gaming.*`,
  `analysis.gaming/chat_moments/game_audio/panns/game_text/game_vision/voice_turns`,
  every `remote_render.*`, `cryptography.x509`, `cryptography.hazmat...ec`,
  `sources.preview_frames`.
* `datas`: `config/settings.yaml`, `config/prompts/`, `config/gaming.yaml`,
  `config/sports.yaml`, `config/audioset_labels.txt`, `transcription/assets`,
  `ui/package.json` (→ `.`), `assets/outro`, `gaming/layouts.json`, two Haar cascades
  (→ `cascades/`), `yolov8n-pose.pt` + `yolov8n.pt` (→ `.`), `models/pretrain_TalkSet.model`,
  `models/panns_mobilenetv1.pth`, the two `.onnx` voice models (→ `.`), `vendor/ffmpeg/*`
  (→ `ffmpeg/`), `vendor/ollama/**` (→ `ollama/...` recursively, layout preserved),
  `vendor/whisper/**` (→ `whisper/...`).
* `excludes`: `polars`, `tkinter`, `PyQt5`, `PySide2`, `IPython`, `jupyter`, `notebook`,
  `pandas`, `sklearn`, `scikeras`, `tensorflow`, `keras`. **`matplotlib` must not be
  excluded** (ultralytics imports it) — guarded by `tests/test_packaging.py`, which also
  asserts the vendor folders, `nvidia.cublas`, `transcription/assets`, sports
  submodules, voice models and that `analysis/voice_turns.py` imports no `scipy`,
  `sklearn`, `torchaudio`, `librosa`, `torch`.
* `scripts/` is not packaged at all (`clips-studio.spec` comment on `assets/outro`).

### 9.2 Installed layout (NSIS and MSIX share it; `docs/MSSTORE.md` "What the Store build does differently")

```
<install dir>\                       per-user by default (nsis.perMachine: false)
  Clips Kitty.exe                    Electron (productName), icon via build/afterPack.cjs + rcedit
  resources\app.asar                 renderer/main/preload JS (files: out/**, package.json)   (inferred: electron-builder default)
  resources\backend\                 extraResources "from ../build/dist/backend to backend", not in asar
    api.exe                          the frozen engine (console app)
    _internal\                       PyInstaller 6 one-dir payload = sys._MEIPASS (inferred from PyInstaller 6; code checks both exe dir and _internal, core/binaries.py:47-52)
      config\settings.yaml, config\prompts\*.txt, config\gaming.yaml, config\sports.yaml, config\audioset_labels.txt
      package.json                   app version for bug reports
      assets\outro\*.mp4, gaming\layouts.json, transcription\assets\probe.mp3
      cascades\haarcascade_*.xml
      yolov8n-pose.pt, yolov8n.pt, pretrain_TalkSet.model, panns_mobilenetv1.pth, *.onnx
      ffmpeg\ffmpeg.exe, ffprobe.exe, README-FFMPEG.txt
      ollama\ollama.exe, ollama\lib\..., README-OLLAMA.txt, VERSION.txt   (Electron spawns backend\_internal\ollama\ollama.exe, ui/src/main/index.ts)
      whisper\small\model.bin…, whisper\large-v3-turbo\model.bin…, README-WHISPER.txt
      nvidia\cublas\bin\cublas64_12.dll
      torch\lib\ (CUDA build), cv2, ctranslate2, onnxruntime, piper (espeak-ng data), rapidocr models, yt_dlp extractors, …
%LOCALAPPDATA%\Clips Studio\settings.yaml      the editable config (seeded from the bundle)
%LOCALAPPDATA%\Clips Studio\data\              library: state.db, downloads, clips, logs, credentials, models (Ollama), voices
%APPDATA%\clips-studio\                        Electron userData: localStorage, update-prefs.json, tray.json
```

Sizes: unpacked ~10 GB (4 GB Ollama + Whisper, rest CUDA PyTorch); payload
`clips-studio-<v>-x64.nsis.7z` 5.9 GiB; `ClipsKitty-<v>-x64.zip` 6.9 GiB; Web Setup
~813 KB (`docs/RELEASING.md` "What comes out"; `ui/electron-builder.yml` publish comment;
winget README says 6.7 GB at 2.0.0). Uninstall leaves `data/` (`nsis.deleteAppDataOnUninstall:
false`, `docs/RELEASING.md`).

### 9.3 electron-builder (`ui/electron-builder.yml`)

`appId: com.clipsstudio.app`, `productName: Clips Kitty`, output `../release`,
`files: out/**/*, package.json`, `extraResources: ../build/dist/backend → backend`
(asar would corrupt native DLLs), `win.target: nsis-web + zip (x64)`,
`artifactName: ClipsKitty-${version}-${arch}.${ext}`, `nsisWeb.artifactName:
ClipsKitty-Web-Setup-${version}.${ext}`, `signAndEditExecutable: false` (unsigned;
`afterPack: build/afterPack.cjs` embeds the icon with rcedit from the electron-builder
cache), `nsis: oneClick false, perMachine false, allowToChangeInstallationDirectory`,
`compression: maximum`, `publish: provider generic, url
https://huggingface.co/ColinGPT9/clips-studio-releases/resolve/main, channel latest`.
An `appx:` block (identity `ClipsStudio.ClipsStudio`, publisher
`CN=82A1C822-…`, `applicationId: ClipsStudio`, `runFullTrust` only) is used only when
`scripts/build_msix.py` passes `--win appx` explicitly. Plain `nsis` is impossible:
32-bit `makensis` dies ~2 GB (`CONTRIBUTING.md`, `ARCHITECTURE.md:957-966`).

### 9.4 Fetch scripts (build inputs, never committed)

| Script | Fetches | Puts it in | Licence note written |
|---|---|---|---|
| `scripts/fetch_ffmpeg.py` | gyan.dev `ffmpeg-release-essentials.zip` (GPL build, libx264) → `ffmpeg.exe`, `ffprobe.exe` | `vendor/ffmpeg/` | `README-FFMPEG.txt` (source offer) |
| `scripts/fetch_ollama.py` | `ollama-windows-amd64.zip` pinned `OLLAMA_VERSION = "v0.32.6"`, full tree incl. `lib/` GPU runners, zip-slip guarded | `vendor/ollama/` + `VERSION.txt` | `README-OLLAMA.txt` (MIT) |
| `scripts/fetch_whisper.py` | `faster_whisper.utils.download_model` for `small` and `large-v3-turbo` | `vendor/whisper/<size>/` | `README-WHISPER.txt` (MIT) |
| `scripts/fetch_asd_model.py` | TalkNet `pretrain_TalkSet.model` from two HF mirrors, min 50 MB | `models/` | (MIT, `NOTICE`) |
| `scripts/fetch_panns.py` | PANNs MobileNetV1 from Zenodo 3987831, MD5-checked | `models/panns_mobilenetv1.pth` | CC BY 4.0 (`NOTICE`) |
| `scripts/fetch_voice_model.py` | WeSpeaker ResNet34-LM + pyannote segmentation-3.0 ONNX, commit-pinned, SHA-256-checked | `models/*.onnx` | MIT / CC BY 4.0 (`NOTICE`) |

Also: `scripts/build_msix.py` (Store appx; refuses placeholders, Developer Mode,
stale-backend timestamp check), `scripts/sign_msix.py` (self-signed test install),
`scripts/build_appx_assets.py` (Store tiles into `ui/build/appx/`), `scripts/test-install.wsb`
/ `test-app.wsb` (Windows Sandbox), `scripts/build_sitemap.py`, `scripts/ping_indexnow.py`,
`scripts/capture_*.ps1`, `scripts/gaming_detect_bench.py`, `scripts/make_mascot.py`.

### 9.5 Build-machine requirements

Windows (`scripts/build_msix.py` exits on non-win32; NSIS/rcedit/`.exe` assumptions in
`build_installer.py`), Python with `requirements.txt` + `requirements-build.txt`
(`pyinstaller>=6.21.0`) + **CUDA torch from `--index-url …/whl/cu130`** (not in
requirements; the build refuses a CPU torch, `scripts/build_installer.py` `check_tools`),
Node 18+ / npm (CI uses Node 20, `.github/workflows/ci.yml`). Code signing: none
(`KNOWN-ISSUES.md` "Windows warns that the app is unsigned"; `ROADMAP.md` Outstanding).

---

## 10. Distribution channels and the update mechanism

| Channel | Artefact | Where it is hosted | How a user updates | Cited |
|---|---|---|---|---|
| Standalone installer (primary) | `ClipsKitty-Web-Setup-<v>.exe` (≈1 MB) downloads `clips-studio-<v>-x64.nsis.7z`; `ClipsKitty-<v>-x64.zip` offline | Setup .exe + notes on the GitHub release (2 GiB asset cap); payload, zip and feed files on Hugging Face model repo `ColinGPT9/clips-studio-releases` via `hf upload` | `electron-updater` generic provider reads `latest.yml` (stable), `beta.yml` or `alpha.yml` (fallback to `latest.yml`), SHA512-verified; **`autoDownload = false`, `autoInstallOnAppQuit = false`**, `allowDowngrade = false`; one silent check 8 s after launch; user presses Download then "Restart & install" → `quitAndInstall(false, true)`; a skipped version is remembered in `update-prefs.json` | `docs/RELEASING.md` "Publishing", "Updates come from the feed file"; `ui/src/main/updater.ts`; `ui/electron-builder.yml` publish |
| Microsoft Store | `Clips Kitty-<v>-x64.appx` (~6 GB, under the 25 GB cap), unsigned (Microsoft re-signs) | Partner Center, Store ID `9NB6XT7DSQZZ`; "Start update" per release | the Store updates it; electron-updater is never wired (`process.windowsStore` check); donate opens system browser | `docs/MSSTORE.md`; `ui/src/main/distribution.ts`; `ui/src/main/updater.ts` `isMicrosoftStore()` branch |
| winget | manifests for `ColinGPT9.ClipsKitty` pointing at the GitHub Web Setup URL + SHA256; `InstallerType: nullsoft`, `Scope: user`, `/S` silent | PR to `microsoft/winget-pkgs` (accepted at 2.0.0, PR #425676) | winget runs the same installer | `packaging/winget/*.yaml`, `packaging/winget/README.md` |
| Docker (contributors) | `ghcr.io/colingpt9/clips-studio-engine:latest`, `python:3.11-slim`, CPU torch, `CMD serve --host 0.0.0.0` | GHCR, published manually or on `v*` tags | `docker compose pull` | `Dockerfile`, `docker-compose.yml`, `.github/workflows/docker-image.yml` |
| Website / web taster | `site/` (Pages + HF Space), `web/` (Vercel, Next static) | | force-pushed mirror | `.github/workflows/mirror.yml`, `docs/MIRRORS.md`, `web/README.md` |

Release checklist specifics: tag `v<version>` must match `ui/package.json`; upload the
payload first and the feed file **last** (an update exists the moment `latest.yml`
lands); bump the nine site download links and the two `VERSION` constants
(`docs/RELEASING.md` checklist). Update UX gaps: no progress for the multi-GB package
download beyond a 1-second size poll (`ui/src/main/updater.ts` `watchPackageDownload`;
`KNOWN-ISSUES.md` "Updating shows no progress"); a failed update vanishes silently.

### 10.1 What this implies for shipping third-party code separately

* Every release is a **wholesale replacement** of one monolithic payload (NSIS install
  over the top; MSIX package swap). There is no partial update, no component feed, and
  no in-app download of code (`ui/src/main/updater.ts` only knows one artefact; the
  only runtime downloads are Ollama models via Ollama's pull API and Piper voices).
* The install directory is per-user-writable for NSIS but **read-only for MSIX**
  (`core/paths.py:95-100`); the app already moved its own writable state to
  `%LOCALAPPDATA%\Clips Studio\`. Third-party code would therefore have to live under
  the data dir (or similar per-user path), never beside `api.exe`.
* The engine is a **PyInstaller freeze**: no `pip`, no `site-packages`, and `api.exe`
  cannot run `-m module` (`requirements.txt` Piper comment: "sys.executable -m piper …
  is api.exe in a frozen build and cannot run -m module"). A Python plugin can only
  import what the bundle already contains, and anything imported lazily needs a
  `hiddenimports` entry at build time (`clips-studio.spec`). Adding even a small
  dependency "would also mean a new hidden import in clips-studio.spec and a re-frozen
  backend" (`server/mcp.py:10-16`, which is why the MCP server is stdlib-only).
* Precedents already in the tree for out-of-process work: the render worker is a
  separate `api.exe render-worker --run-job` child with a JSON job and an allow-listed
  config slice (`remote_render/worker.py:82-87`, `remote_render/protocol.py:16-27`
  `CONFIG_SECTIONS = ("clips","tracking","video")`); the MCP server and the OBS plugin
  drive the engine purely over HTTP (`server/mcp.py`, `server/integrations.py`).
* Release cadence is slow and manual (2.5 h builds, no CI build, bundled `yt-dlp`
  fixed at build time, `KNOWN-ISSUES.md` "A download can break when a site changes,
  until the next release") — a reason the brief's registry would need its own update
  path independent of app releases (inferred).
* Hosting constraints: GitHub release assets ≤ 2 GiB; Hugging Face is the
  large-file CDN the project already uses (`docs/RELEASING.md`).

---

## 11. Python version and dependency policy

* Target: `pyproject.toml` `[tool.ruff] target-version = "py310"`; `README.md`
  "Python 3.10+"; CI and Docker run **3.11** (`.github/workflows/ci.yml`
  `python-version: '3.11'`, `Dockerfile` `FROM python:3.11-slim`); code uses PEP 604
  unions (`str | None`, `core/paths.py:52`) and `Path.is_relative_to`
  (`scripts/fetch_ollama.py`). The frozen build uses whatever interpreter runs
  `scripts/build_installer.py` (`sys.executable -m PyInstaller`); **the release
  interpreter version is not pinned or recorded anywhere I read** (bug reports do carry
  `platform.python_version()`, `server/feedback.py:247`). This sandbox has 3.13.16.
* `requirements.txt`: floors **and** major-version ceilings on everything except
  `yt-dlp` (no ceiling); `nvidia-cublas-cu12` only on `sys_platform == "win32"`;
  `piper-tts` bundled since 1.1.3; comments give the reason for every line. `torch`
  is deliberately absent (CPU vs CUDA chosen by index URL). `requirements-build.txt`:
  `pyinstaller>=6.21.0`. `requirements-chatgpt.txt`: optional `openai-codex` (not in
  the installer). `pyproject.toml` has **no build system** ("Clips Kitty ships as a
  frozen application, not a pip package").
* Dependabot: weekly, grouped, **no major bumps** for pip, npm or actions
  (`.github/dependabot.yml`); `npm audit` posture in `docs/DEPENDENCY-SECURITY.md`
  (electron 31 → 39+ upgrade is "a real piece of work" tied to electron-builder 26).
* Licence rule: anything bundled must be AGPL-3.0-compatible and listed in `NOTICE`
  (`CONTRIBUTING.md` "Adding a dependency?"; `NOTICE` third-party table; CODEOWNERS on
  `NOTICE`/`LICENSE`).

### 11.1 How a new Python dependency reaches the frozen build (the actual steps)

1. Add it to `requirements.txt` with a floor and a major ceiling and a comment
   (`requirements.txt` header).
2. Check its licence; add it to `NOTICE` (`CONTRIBUTING.md`).
3. If it loads modules/data at runtime by name, add it to the `collect_all` loop or
   `hiddenimports`, and any data files to `datas`, in `clips-studio.spec`; if it is
   imported lazily inside a function, it **must** be in `hiddenimports`
   (`clips-studio.spec` "Imported lazily inside functions").
4. If it must *not* be bundled, add to `excludes` — and add a guard in
   `tests/test_packaging.py` if its absence is load-bearing.
5. Rebuild on the Windows build machine (`python scripts/build_installer.py`), which
   smoke-tests `api.exe status`; then install on a clean machine (Windows Sandbox,
   `docs/RELEASING.md` "Testing it the way a stranger meets it"). CI cannot verify any
   of this (`.github/workflows/ci.yml` installs only `pyyaml ruff pytest requests`).
6. For the Store, rebuild the appx too (`scripts/build_msix.py`); Docker image is
   rebuilt from `requirements.txt` on demand.

---

## 12. Existing notions of "extension", "plugin", "registry", "provider", "skill"

Grep (`plugin|extension|marketplace|registry|provider`, case-insensitive) over
`*.py|*.ts|*.tsx|*.md|*.yaml|*.json`, excluding `node_modules`:

* **No plugin loader, no entry points, no dynamic discovery, no sandbox** exists.
  "Plugin architecture and community extensions" is listed under **Later** in
  `ROADMAP.md:63`. The only prior art in writing is `docs/platform/BRIEF.md` (the
  brief for this project) and `ARCHITECTURE.md:1004` "`sources/` and `publish/` are
  plugin folders. Adding a platform means adding one file".
* "Plugin" in code means the external **OBS plugin** (a separate repo) that uses the
  HTTP integrations API (`server/integrations.py:3`, `server/api.py:680,713`,
  `server/mcp.py:6`, `PROJECTS.md:13`, `README.md:744`).
* "Extension" otherwise refers to file extensions and TypeScript config (noise).

The **code-level extension points that already behave like registries** (all
in-process, all compiled into the freeze):

| Mechanism | Registry / contract | Adding one means | Cited |
|---|---|---|---|
| Sports | `SPORTS = {"soccer": "sports.soccer", "basketball": "sports.basketball"}`; package exposes `profile(config, opt, video)` (required), optional `framing(clip_path, config)`, `prepass(video_path, duration)`, `prepass_wait(duration)`, `hotwords(opt, video)`, `READS_DESCRIPTION`; data in `config/sports.yaml`; validated by `clean()`, listed by `GET /sports` | an entry in `config/sports.yaml`, a `sports/<name>/` package, a line in `SPORTS`, and (frozen) `collect_submodules("sports")` already covers it | `sports/__init__.py:1-27,46-200`; `docs/SPORTS.md` "Adding a sport"; `tests/test_packaging.py` |
| LLM backends | `llm/registry.py` `create_backend(llm_config) -> LLMBackend` dispatches `provider/model`: `ollama` → `OllamaBackend`; sign-in plans → `llm/signin/catalog`; cloud → `llm/providers/catalog.PROVIDERS[id]` (`ProviderSpec` dataclass: `id, label, adapter, base_url, key_label, key_url, pricing_url, privacy, auth, extra_headers, body_extras, models_path, model_filter, key_check_path, key_check_ok, stt, stt_filter, tier, tagline, regions, plan_note, plan_note_url, oauth, preferred`) + `CloudBackend` | "one ProviderSpec in `_ORDER`" for OpenAI-compatible APIs; a new `adapters/` module otherwise; keys stored as `ai_key_<id>` | `llm/registry.py`; `llm/providers/base.py:81-140`; `llm/providers/catalog.py:1-13,144`; `docs/EXTENDING.md` "Add an AI model" |
| `LLMBackend` ABC | `generate(prompt, *, json_mode=False) -> str`, `name`, `supports_schema`, `sees_images()`, `look(prompt, images)`, `chat(messages, tools) -> ChatTurn`; `ToolCall(id,name,arguments)`; `generate_json()` helper | `analysis/` depends on this only | `llm/base.py` |
| Sources | `sources/dispatch.py` `identify(url) -> (source, video_id)`, `download(url, output_dir, vertical) -> DownloadedVideo`, `metadata`, `description`, `game_info`; ids prefixed `tw_`, `kick_`, `local_` | one module + a branch in `dispatch.py` | `sources/dispatch.py`; `docs/EXTENDING.md` "Add a platform" |
| Publishers | `publish/base.py` `Publisher` ABC: `name`, `publish(request: PublishRequest, on_progress, should_cancel) -> PublishResult`, legacy `upload()`; `PublishRequest`/`PublishResult` dataclasses | implementations `publish/youtube_shorts.py`, `woopsocial.py`, `uploadpost.py`; each with its own `server/<x>_api.py` + `<x>_service.py` | `publish/base.py`; `ls publish server` |
| Server feature modules | the `install(app, *, config, db, data_dir, …)` convention: `server/youtube_api.py` (20 routes), `server/uploadpost_api.py` (13), `server/woopsocial_api.py` (13), `server/ai_api.py` (17), `server/integrations.py` (5), `server/automation.py` (13), `remote_render/service.py` (9) — each "optional, self-contained and removable" | copy the pattern | `server/api.py:616-705` |
| Longform profiles | `longform/profiles.py` `PROFILES = {short_clips, clips_140, highlights, edited_stream}` with `label, subdir, min/max_duration, ready` | a dict entry | `longform/profiles.py`; `docs/EXTENDING.md` "Add an export format" |
| Modes | `core/modes.py` toggles (`is_vertical_live`, `is_gaming`, …) read from job config or render opts | | `core/modes.py` |
| Colour presets | `video/filters.py` "Preset names are validated against this registry everywhere" | | `video/filters.py:8` |
| Integration presets | `server/integrations.py` `PRESETS = {"standard": …}`; `PLATFORMS = ("twitch","youtube","kick")` | | `server/integrations.py:27-40` |
| Signals ("bring your own model") | a numpy array over the timeline appended to `peak_signals` in `analysis/fusion.py`; `hype.audience_curve(url, video_id, duration) -> np.ndarray | None` is the shape to copy | | `docs/EXTENDING.md` "Bring your own model" |
| Prompts / knowledge as data | `config/prompts/*.txt`, `config/gaming.yaml`, `config/sports.yaml`, `gaming/layouts.json` | | `ARCHITECTURE.md:940-944` |
| Agent skill | `skills/clips-kitty/SKILL.md` (YAML front matter `name`, `description`) describing the MCP tools `queue_video`, `queue_local_file`, `job_status`, `list_clips`, `export_clip`, `list_videos`, `queue_status` | | `skills/clips-kitty/SKILL.md` |
| External integrations | OBS plugin over `POST /integrations/streams`; webhooks; MCP over stdio; `examples/drive_the_api.py` | | `docs/API.md:806-975`; `server/webhooks.py`; `server/mcp.py` |
| Electron allow-list | `EXTERNAL_ALLOWED` host regexes: "A provider added there needs its hosts added here, or its 'Get a key' link does nothing" | a UI-visible link from any new provider/plugin needs an Electron change | `ui/src/main/index.ts` `EXTERNAL_ALLOWED` |

---

## 13. Security boundary facts a plugin design inherits

* The API has **no authentication**; binds `127.0.0.1`; `TrustedHostMiddleware`
  allows only `127.0.0.1`/`localhost` Host headers (DNS-rebinding guard); CORS only for
  the Vite origin `localhost:5173` (`server/api.py:576-588`; `docs/API.md:75-103`;
  `SECURITY.md` "What is in scope"). Anything on the machine can read footage, delete
  clips, change settings, and — if YouTube is connected — publish (`SECURITY.md`).
* Supported-vs-internal line: `docs/API.md:127-142`; shapes change only with a
  CHANGELOG note; `api_version` bumps on incompatible change.
* Secrets never leave the credential store; keys never returned by any route
  (`server/ai_api.py:9-12`); outputs scrubbed (`core/scrub.py`).
* Electron: renderer sandboxed; `openExternal` allow-listed; clipboard read gated to
  key-shaped text; thumbnails passed as bytes not paths (`ui/src/main/index.ts`).
* CodeQL dismissals are documented per line (`.github/codeql/codeql-config.yml`);
  CI secret check greps tracked files for `client_secret|token.*\.json`
  (`.github/workflows/ci.yml`).
* The job payload a remote worker receives is an allow-listed config slice with no
  keys (`remote_render/protocol.py:19-27`).

---

## 14. Capabilities a plugin could use today

| Capability | What it gives | Where | Reachable today |
|---|---|---|---|
| Queue a video / local file, with every processing option | `POST /jobs`, `POST /jobs/batch`, `POST /videos/local`, `PATCH /jobs/{id}`, `GET /jobs/{id}`, `POST /cancel`, queue control | `server/api.py` (`JobIn` fields `url, force, max_clips, caption_style, captions, long_clips, filter, min_score, focus, sport, longform, watermark_profile_id, podcast, vertical_live, gaming*, webhook_url, webhook_secret, hashtags`) | HTTP (supported) |
| Progress events | `ws://127.0.0.1:8765/ws` six event types; `core.progress.emit()` in-process | `server/events.py`, `core/progress.py`, `docs/API.md:1230` | HTTP/WS; python import |
| Completion callback | webhook POST signed with HMAC | `server/webhooks.py` | HTTP |
| Results | `GET /videos`, `GET /videos/{id}/clips` (scores breakdown), `GET /media/{clip_id}`, captions/words endpoints, `POST /clips/{id}/export`, `POST /export/batch` | `server/api.py` | HTTP |
| Readiness / environment | `GET /health` (`app_version`, `api_version`), `GET /health/preflight`, `GET /system/stats` (GPU, disk) | `server/api.py:711-740`, `core/preflight.py` | HTTP |
| Job logs | `GET /jobs/{id}/log`, files `data/logs/job_N.log` | `server/api.py:1192`, `server/jobs.py:150` | HTTP / file |
| FFmpeg / ffprobe | bundled binaries path | `core/binaries.py` `ffmpeg()`, `ffprobe()` | python import; `CLIPS_STUDIO_FFMPEG` env for overrides; **not** exposed over HTTP |
| Ollama runtime | bundled `ollama.exe` on `127.0.0.1:11435` (packaged) / `11434` (dev); model list/pull/activate/delete over the API | `ui/src/main/index.ts`; `GET /models`, `POST /models/pull`, `POST /models/activate`, `DELETE /models/{tag}` | HTTP (model mgmt); Ollama's own HTTP API on the port (inferred: it is a plain Ollama server) |
| LLM (local or cloud, user's key) | `create_backend(config["llm"])` → `generate/chat/look`; `/ai` routes for provider/key/model management | `llm/registry.py`, `llm/base.py`, `server/ai_api.py` | python import; HTTP for management only (no "run a prompt" endpoint seen; `POST /agent/chat` is the assistant, `server/agent.py`) |
| Whisper transcription | `transcription.transcriber.transcribe`; bundled weights via `core.binaries.whisper_model(size)`; cloud STT via providers | `transcription/transcriber.py`, `transcription/cloud.py` | python import; no standalone HTTP endpoint seen (transcripts written to `data/transcripts/<id>.json`) |
| YOLO / TalkNet / Haar / PANNs / voice models | weights resolved by module; `available()` gates | `core/binaries.py`, `video/asd.py`, `analysis/panns.py`, `analysis/voice_turns.py` | python import |
| GPU info / encoder choice | `video/encoding.py` auto-detect; `GET /system/stats` gpu | `video/encoding.py`, `server/api.py` | python import; HTTP (read-only) |
| Data dir / library DB | `resolve_data_dir(config)`, `StateDB(data_dir/"state.db")` | `core/paths.py`, `core/state.py` | python import (internal; `PROJECTS.md` says do not read the DB/data folder directly) |
| Secrets storage | `core.secrets.save/load/wipe(data_dir, name, dict)` DPAPI-backed | `core/secrets.py` | python import |
| Settings | `GET/PATCH /settings` (8 keys); YAML regex writers | `server/api.py:2940-3020` | HTTP (subset) |
| Config knowledge data | prompts, `gaming.yaml`, `sports.yaml`, `layouts.json` | `config/`, `gaming/` | file (bundled, read-only when frozen) |
| Publishing | YouTube / Upload-Post / WoopSocial routes; `Publisher` ABC | `server/youtube_api.py`, `server/uploadpost_api.py`, `server/woopsocial_api.py`, `publish/base.py` | HTTP; python import |
| Streams hand-over | `POST /integrations/streams` + presets | `server/integrations.py` | HTTP (supported) |
| Watched channels | `/automation*` | `server/automation.py` | HTTP (supported) |
| Remote render worker | pair + run as a separate process; `PROTOCOL = 1` | `remote_render/` | CLI (`api.exe render-worker`) |
| MCP tools | stdio server over the HTTP API | `server/mcp.py`, `skills/clips-kitty/SKILL.md` | CLI |
| Housekeeping | `GET /storage`, `POST /storage/cleanup` | `core/housekeeping.py`, `server/api.py` | HTTP |

---

## 15. Six-way classification (rows are "claim (path)")

**Reuse as is**
* `GET /health` with `app_version` + `api_version` is the compatibility handshake a plugin host and registry can key on (`server/api.py:34,711-716`; `docs/API.md:165-176`).
* The supported HTTP API, webhooks and WebSocket events are the documented, CHANGELOG-guarded contract and already serve an external plugin (OBS) (`docs/API.md:127-142`; `server/integrations.py:11`; `server/webhooks.py`).
* `core/binaries.py` resolution (env override → bundled → PATH) and `core/paths.resolve_data_dir` are the per-user, frozen-aware location rules any plugin directory should copy (`core/binaries.py:33-70`; `core/paths.py:19-50`).
* `core/secrets.py` DPAPI store and `core/scrub.py` are the credential and redaction layers to reuse for plugin keys (`core/secrets.py`; `core/scrub.py`).
* `core/progress.emit` → `server/events.Broadcaster` is the progress channel (`core/progress.py`; `server/events.py`).
* The job-log convention (`data/logs/job_N.log`, `GET /jobs/{id}/log`, 50 kept) is the logging sink a plugin's output should feed (`server/jobs.py:31,150-152`; `server/feedback.py:83-104`).
* The `install(app, *, config, db, data_dir, …)` module pattern is how optional server features already plug in (`server/api.py:616-705`).
* The remote-render worker is a working out-of-process execution precedent with an allow-listed config slice and a stable `PROTOCOL` (`remote_render/protocol.py:16-27`; `remote_render/worker.py:82-87`).

**Needs abstraction**
* Settings persistence is five regex rewriters of YAML text with no schema or generic read/write; a plugin settings surface needs one (`server/api.py:2955-3020`; `llm/manager.py:192-201`; `server/ai_api.py:92-101`).
* "Which Whisper/FFmpeg/Ollama/weights do I have" is only reachable by Python import (`core/binaries.py`) and `GET /health/preflight`; an SDK capability API must wrap it (`core/binaries.py`; `core/preflight.py`).
* The in-process registries (`SPORTS`, `PROVIDERS`, `PROFILES`, `dispatch.py` branches) are hard-coded dicts with no discovery step; a plugin registration layer must sit beside them without replacing them (`sports/__init__.py:26`; `llm/providers/catalog.py:144`; `longform/profiles.py`; `sources/dispatch.py`).
* Transcription and LLM calls exist only as Python functions, not as HTTP capabilities a separate-process plugin could call (`transcription/transcriber.py`; `llm/registry.py`).

**Needs compat layer**
* The frozen engine cannot `pip install` or run `-m`; a plugin needing extra Python deps needs its own interpreter/process and a bridge (`requirements.txt` Piper comment; `server/mcp.py:10-16`; `clips-studio.spec`).
* Electron's `EXTERNAL_ALLOWED` host allow-list must be extended for any plugin UI link, so a declared-hosts mechanism is needed (`ui/src/main/index.ts` `EXTERNAL_ALLOWED`).
* The renderer's CSP and hard-coded `API_BASE` pin everything to `127.0.0.1:8765`; plugin UI assets would need serving from that origin (`ui/src/renderer/index.html:7-8`; `ui/src/renderer/src/lib/api.ts:49`).
* `app_version` is read from `ui/package.json` / `_MEIPASS/package.json` and there is no SDK/plugin-API version beside `API_VERSION = 1`; compatibility checking needs an added field (`server/feedback.py:260-290`; `server/api.py:34`).

**Suitable external** (lives outside the app repo / process)
* The Hugging Face model repo already used for payloads is the proven large-file host; GitHub releases cap at 2 GiB (`docs/RELEASING.md`; `ui/electron-builder.yml` publish).
* The OBS plugin, MCP clients and the agent skill show third-party code driving the engine over HTTP/stdio without being inside it (`PROJECTS.md`; `server/mcp.py`; `skills/clips-kitty/SKILL.md`).
* Cloudflare Workers (`feedback-relay/`, `twitch-proxy/`) are the project's pattern for small hosted services with no app coupling (`feedback-relay/README.md`; `twitch-proxy/README.md`).
* `web/` and `whop-app/` are separate deployables with their own `VERSION` constants (`docs/RELEASING.md`; `web/README.md`; `whop-app/README.md`).

**Needs docs**
* The frozen-build layout (`resources/backend/api.exe` + `_internal/...`) is only described across spec comments, `electron-builder.yml` and `ui/src/main/index.ts` (`clips-studio.spec`; `ui/electron-builder.yml`; `ui/src/main/index.ts` `startOllama`).
* The full list of `CLIPS_STUDIO_*` env overrides is undocumented outside code comments (`core/binaries.py`; `main.py`; `server/mcp.py:52-55`).
* The three writable locations (`%LOCALAPPDATA%\Clips Studio\settings.yaml`, `…\data`, `%APPDATA%\clips-studio`) and which store holds what (`core/paths.py`; `ui/electron-builder.yml:10-20`; `ui/src/main/updater.ts`).
* The print-based logging convention and the `_Tee` capture are undocumented for contributors (`server/feedback.py:31-82`).
* The dependency-to-frozen-build procedure (requirements → NOTICE → spec → rebuild → sandbox) is spread across four files (`requirements.txt`; `CONTRIBUTING.md`; `clips-studio.spec`; `docs/RELEASING.md`).

**Remain internal**
* `clips-studio.spec`, `scripts/build_*.py`, `scripts/fetch_*.py`, `ui/electron-builder.yml`, `ui/build/afterPack.cjs` and the winget/Store manifests are release tooling, not platform surface (`scripts/`; `packaging/`).
* `core/state.py` schema and the data folder layout are explicitly not for third parties ("not by reading the database or the data folder directly, which change without notice", `PROJECTS.md`).
* `third_party/talknet/` is verbatim vendored code that must never be modified or exposed (`third_party/talknet/README.md`; `.github/CODEOWNERS`).
* `server/feedback.py` diagnostics, `server/agent.py`, branding/creator/caption-editing routes are "internal" per the API doc (`docs/API.md:131-134`).
* `vendor/`, `models/`, `build/`, `release/`, `data/` are gitignored build inputs/outputs (`.gitignore`).

---

## 16. Open questions

1. Which Python interpreter version froze the 2.0.0 release? Nothing pins it; CI/Docker use 3.11 and ruff targets 3.10 (`scripts/build_installer.py`; `pyproject.toml`; `.github/workflows/ci.yml`).
2. Is `sys._MEIPASS` exactly `resources\backend\_internal` in the shipped build? Code tolerates both `exe_dir` and `_internal`; Electron hard-codes `_internal/ollama` (`core/binaries.py:47-52`; `ui/src/main/index.ts` `startOllama`). (inferred yes)
3. Can an MSIX (Store) install execute code from `%LOCALAPPDATA%`, and does `runFullTrust` cover spawning a plugin interpreter? Only `runFullTrust` is declared (`ui/electron-builder.yml` appx comment).
4. Is there any endpoint to run transcription or an LLM prompt on its own over HTTP? None seen in the 86 + installed routes (`server/api.py` route list; `server/ai_api.py`); `POST /agent/chat` is the assistant, not a raw completion.
5. The credential-store name used by `server/uploadpost_service.py:82-95` was not read; and `server/woopsocial_api.py` key storage was not opened.
6. Which `hiddenimports`/`collect_all` entries are still necessary vs. historical (e.g. `polars` exclusion must be re-checked after an ultralytics upgrade, `clips-studio.spec` comment).
7. `docs/DOCKER.md`, `docs/AI-BACKENDS.md`, `docs/GAMING.md`, `docs/SPORTS.md` were not read in this pass (outside the area); they may hold more extension-point detail.
8. `FastAPI(version="0.1")` in `server/api.py:576` disagrees with every other version number; decide whether `/openapi.json` should carry `API_VERSION`.
9. `docs/brand/mascot.png`, `docs/brand/mascot-head.png` and `ui/build/icon.ico` show up as modified after
   a full test run: the test suite regenerates them (`scripts/make_mascot.py` is exercised by the tests) and
   the bytes differ run to run. Anyone committing after `pytest` must `git checkout --` those three files first
   (observed in this session's baseline run; recorded in `docs/platform/PROGRESS.md`).
