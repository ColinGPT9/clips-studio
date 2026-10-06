# Clips Kitty open platform: architecture

Phase 1 of the overnight brief (`docs/platform/BRIEF.md` §8). It builds on [`docs/platform-research.md`](platform-research.md) (the decisions and their evidence) and [`docs/platform/repo-map.md`](platform/repo-map.md) (the code as it is, with file paths). Written 2026-10-06 against `main` at `1d13723`.

**The short version.** Nothing is replaced. The engine (`main.py serve`, `server/api.py create_app`), its HTTP API, its single job worker (`server/jobs.py`) and its one pipeline function (`core/pipeline.py process_video`) stay as they are. The platform adds four small things around them:

1. A **plugin contract**: a manifest file (`clipskitty.yaml`) in the developer's repository, and a job hand-over in plain files (`job.json` in, `result.json` and progress lines out) between the engine and a plugin process.
2. A **detection hook**: when a job names a pipeline plugin, `process_video` asks the plugin for clip moments instead of calling `find_clips`, then renders and registers them with the code it already has. With no plugin named, the pipeline runs exactly as today.
3. A **plugin manager and registry client** in a new engine package, `plugins/`, mounted on the existing app through the existing `install(app, …)` pattern.
4. A **public SDK**, `sdk/python/clipskitty_sdk/`, standard library only, that developers use from their own repositories without importing anything from Clips Kitty.

Plus documentation (`docs/developers/`), a static registry (`registry/`), a Marketplace page in the existing UI, and a model-reference layer over the model stores that already exist.

## What is reused and what is created

| Need | Reused as is | Created |
|---|---|---|
| Accepting a job | `POST /jobs`, `JobIn`, `_process_options` (`server/api.py:39-66`, `:440-550`), `Worker.run` payload copy (`server/jobs.py:187-262`) | one optional job field, `pipeline: {id, version, settings}`, validated in `_process_options` and copied by the worker like every other option |
| Running a job | the queue and the single worker (`core/queue.py`, `server/jobs.py`), `process_video` (`core/pipeline.py:192`) | one branch at the detection call (`core/pipeline.py:438-450`, `clip_direction` and `find_clips`): the plugin runner instead, when `config["clips"]["pipeline"]` is set |
| Download and transcription | `_cached_or_download`, `transcribe` (`core/pipeline.py:205`, `:402`) | nothing |
| Rendering and registration | `_render_files`, `_register_clip`, metadata generation (`core/pipeline.py:1123`, `:1563`, `:514`) | nothing; plugin moments become `ClipCandidate`s (`core/models.py:43`) |
| Running another process | the render-worker child pattern (`remote_render/worker.py:82-87`), `core/cancel.py` | `plugins/runner.py` (job folder, child process, progress lines, timeout, cancel) |
| Progress | `core.progress.emit` → `Broadcaster` → `/ws` (`core/progress.py`, `server/events.py`), the `fraction` field the worker already folds into its percentage (`server/jobs.py:312-318`) | nothing new in the protocol: plugin progress is emitted as the existing `analyze` stage with a `fraction`, so `_STAGES` and the UI mirror stay untouched |
| Mounting routes | `install(app, …)` (`server/api.py:629-706`) | `plugins/api.py install(app, …)` with the plugin, Marketplace and model routes |
| Data location | `core.paths.resolve_data_dir` (`core/paths.py:19-50`), the writable per-user data folder | `<data_dir>/plugins/` for installed plugins, state, snapshots and the registry cache |
| API versioning | `API_VERSION = 1` (`server/api.py:34`), `docs/API.md:127-142` | a stability label per route (`server/api_stability.py`), a generated reference, contract tests; `PLUGIN_API_VERSION = 1` for the contract |
| Models | Ollama listing and pulls (`server/api.py:2830-2887`), the per-module resolvers (`core/binaries.py`), the Hugging Face cache faster-whisper already uses (`core/binaries.py:115-128`) | `plugins/models.py`: model references and an index over those stores |
| UI | the page conventions and the Models page as the catalogue precedent (`ui/src/renderer/src/pages/Models.tsx`), `GET /sports` → toggle pattern | a Marketplace page and a Pipeline choice in the job form (`ui/src/renderer/src/components/queue/AddVideos.tsx`) |
| Packaging | `clips-studio.spec` hidden imports and datas, guarded by `tests/test_packaging.py` | spec lines for `plugins` and the SDK folder, with a packaging test |

No second API, no second engine, no second queue, no second runtime. The one genuinely new runtime piece, the child process a plugin runs in, already has a precedent in the remote-render worker.

## The two diagrams, redrawn to the real code

```text
Clips Kitty engine            existing local API              public SDK                     plugin / pipeline contract         Marketplace
main.py serve           →     server/api.py create_app   →    sdk/python/clipskitty_sdk  →   clipskitty.yaml (manifest)    →    registry/index.json
core/pipeline.py              127.0.0.1:8765, API v1          (stdlib only: read_job,        job.json → result.json             → plugins/registry.py
server/jobs.py Worker         stability labels per route      progress, add_range, finish,   + progress lines on stdout         → GET /marketplace
                              docs/developers/api-reference   validate, local API client)    plugins/runner.py runs it          → Marketplace page (ui)
```

```text
                          Marketplace page (ui/src/renderer/src/pages/Marketplace.tsx)
                                 │   GET /marketplace, GET /plugins  (plugins/api.py)
           ┌─────────────────────┼──────────────────────────┐
   Pipelines                  Plugins                     Models
   kind: pipeline (built)     other kinds (designed)      plugins/models.py references over
   built-in: Shorts, Gaming,  caption style, publisher,   HF cache · Ollama store · vendor/
   Sports (manifests only)    source, provider            (no new store)
           └─────────────────────┼──────────────────────────┘
          Public SDK/API: HTTP API v1 (server/api.py, labelled) + plugin contract v1 (clipskitty_sdk)
                                 │
          Clips Kitty Core: server/jobs.py Worker → core/pipeline.process_video
                            download → transcribe → [find_clips | plugins/runner.py] → _render_files → _register_clip
           ┌─────────────────────┼──────────────────────────┐
   FFmpeg                     Whisper                     Ollama / Gemma
   core/binaries.ffmpeg()     transcription/              llm/ollama_backend.py,
   video/encoding.py          transcriber.py              llm/registry.create_backend
           └─────────────────────┼──────────────────────────┘
                         GPU: core/gpu.py
```

The target is the brief's: one API with a stable SDK layer over it, serving official, community and remote pipelines alike.

## 8.1 Extension types

Five candidate types, kept as few as the code allows. Three collapse into others.

| Type | Decision | Maps onto | First release |
|---|---|---|---|
| **Pipeline** | A plugin of kind `pipeline` that implements a capability (first: `highlight_detection`) and returns scored, labelled moments or finished clips. | The detection step of `process_video` (`core/pipeline.py:439`), with rendering and registration unchanged; returned files go through `_register_clip` (`core/pipeline.py:1563`). | **Built.** |
| **Plugin** | The umbrella: anything installed from a repository with a `clipskitty.yaml`. A pipeline is one kind of plugin. Later kinds each map onto a seam that already exists: `caption-style` (the caption preset tables, `video/captions.py:36-55`), `publisher` (`publish/base.py:103`), `source` (`sources/dispatch.py:14-25`), `integration` (the streamer hand-over, `server/integrations.py`). | The manifest's `kind` field. | **Built for `kind: pipeline` only.** Other kinds are rejected by the validator with "planned" in the message. |
| **Model** | Not executable and not installed on its own: a **reference** a plugin lists, resolved by the model manager. | `plugins/models.py` over the Hugging Face cache, Ollama's store and `vendor/` (§8.6). | **Built** (references, detection, reuse; downloads behind an injectable fetcher). |
| **Workflow** | **Collapses into pipeline + presets.** Clipping has a fixed flow in which only detection varies (`core/pipeline.py:192-682`), and the compositions the brief lists ("Gaming → Captions → Vertical") are already job options and presets (`JobIn`, automation watches, streamer presets). A pipeline's settings become part of those presets. | `server/api.py:39-66`; `server/automation.py`; `server/integrations.py:37-58`. | Not a separate type. Pipeline composition is designed in §8.12. |
| **Provider** | **Collapses in two.** An LLM provider is already a data entry (`ProviderSpec` in `llm/providers/catalog.py:144`) and stays first-party. A remote inference service a *pipeline* uses is part of that pipeline (execution `remote` or `hybrid`, §8.5). | `llm/providers/catalog.py`; §8.5. | Not a separate type. A `provider` kind (an OpenAI-compatible endpoint contributed as a plugin) is designed, not built. |

## 8.2 The SDK

The architecture decides the shape. Clips Kitty's engine is Python and already talks to other processes in two ways: HTTP on `127.0.0.1:8765` (the UI, MCP server, OBS plugin) and a job folder handed to a child process (the render worker, `remote_render/worker.py:82-87`). The SDK uses exactly those two, and nothing else: no gRPC, no new WebSocket protocol, no containers.

**Location and dependencies.** `sdk/python/clipskitty_sdk/`, a package that imports only the Python standard library, so a plugin can vendor it or have it put on its path by the engine. The engine imports the same files (one copy, no drift); `plugins/_sdk.py` adds `sdk/python` to `sys.path` in a checkout, and the frozen build ships the folder as data (`clips-studio.spec`).

**What it gives a developer**, mapped to the brief's list:

| Need | SDK |
|---|---|
| define a plugin, define a pipeline, declare inputs, outputs, dependencies, capabilities | the manifest (`clipskitty.yaml`, §8.4) and `clipskitty_sdk.manifest.validate()` |
| receive a job | `clipskitty_sdk.read_job()` → a `Job` with `video`, `transcript`, `settings`, `limits`, `models`, `tools`, `output_dir` |
| report progress, log | `job.progress(fraction, message)`, `job.log(message)`: JSON lines on standard output |
| return outputs | `job.add_range(start, end, score=None, label=…, title=…, reason=…)`, `job.add_clip(path, …)`, `job.finish()` writes `result.json` |
| handle errors | `job.fail(message)` (exit code 1 with a message the user sees); any uncaught exception is reported the same way by the engine |
| reference models | manifest `models:`; at run time `job.models["detector"].path` |
| use the Clips Kitty API | `clipskitty_sdk.client.LocalAPI` for the stable routes (health, jobs, library), standard library `urllib` |
| test locally | `python -m clipskitty_sdk validate <plugin folder>` and `python -m clipskitty_sdk run <plugin folder> --video clip.mp4`, which builds the same job folder the engine builds and validates the result |

The `clips-kitty plugin …` commands in the brief are conceptual; the two `python -m clipskitty_sdk` commands cover `validate` and `dev` for the first release, and install, update and remove live in the app (§8.10). A developer never reads Clips Kitty's source tree.

## 8.3 The developer experience

A developer's repository, for example `clips-kitty-marvel-rivals`:

```text
clipskitty.yaml        the manifest (§8.4)
README.md              what it detects, honestly, and what it needs
LICENSE
src/main.py            reads the job, writes moments
examples/              a short sample clip or screenshots, linked from the manifest
```

Models are listed in the manifest's `models:` section rather than a separate `models.yaml`, so there is one file to validate. The loop:

```text
python -m clipskitty_sdk validate .                     same checks the registry runs
python -m clipskitty_sdk run . --video sample.mp4       same job folder the engine builds
git tag v1.0.0 && git push --tags                       the developer's own repository
pull request adding registry/plugins/<publisher>/<name>.yaml
CI validates the listing and builds registry/index.json  (reads metadata only)
user opens Marketplace → sees the listing → Install → picks it on the Generate bar
```

## 8.4 The manifest

`clipskitty.yaml` at the repository root (or at the subfolder the listing names). YAML because the brief's example is YAML and the engine already depends on PyYAML (`requirements.txt`); the validator takes the parsed mapping, so JSON works too. `manifest_version` lets the schema grow. The JSON Schema for editors is published beside the validator (`sdk/python/clipskitty_sdk/schema/clipskitty.schema.json`); the Python validator is the source of truth and a test keeps the two in step.

```yaml
manifest_version: 1
id: example-dev/marvel-rivals-highlights   # publisher/name; lower case, digits, hyphens
name: Marvel Rivals Highlights
version: 1.0.0                             # SemVer
kind: pipeline                             # only "pipeline" in plugin API 1
capability: highlight_detection
description: >
  Finds team wipes, multi-kills and ultimates in Marvel Rivals VODs by reading the kill feed.
author: {name: Example Developer, url: https://github.com/example-dev}
repository: https://github.com/example-dev/clips-kitty-marvel-rivals
license: MIT                               # SPDX identifier
requires:
  clips_kitty: ">=2.0"                     # app versions it works with (the plugin_api check is what stops older apps)
  plugin_api: 1                            # contract major version
run:
  command: ["{python}", "src/main.py"]     # argv; {python} is the Python Clips Kitty finds
  python_requirements: requirements.txt    # optional: wheels only, hash-pinned, own environment, installed after you agree
  timeout_minutes: 60
execution: local                           # local | remote | hybrid
inputs: [video, transcript]                # what the engine hands over
outputs: [ranges]                          # ranges and/or clips
events: [team_wipe, multi_kill, ultimate]  # labels its moments may carry
games: [marvel-rivals]                     # optional, for discovery
settings:                                  # rendered as a form; values arrive in job.settings
  min_kills: {type: integer, default: 3, minimum: 2, maximum: 6, title: Smallest multi-kill}
models:
  - name: killfeed
    source: huggingface
    id: example-dev/marvel-rivals-killfeed
    revision: 0123456789abcdef0123456789abcdef01234567
    files: [killfeed.onnx]
    format: onnx
    license: apache-2.0
permissions: [video.read, transcript.read, ffmpeg, gpu]
network: []                                # hosts contacted; required for remote and hybrid
sends: []                                  # what leaves the machine: video | video_link | audio | frames | transcript,
                                           #   or {data, to, when} when it depends on a setting
requirements: {gpu: recommended, vram_gb: 6, ram_gb: 8, disk_gb: 2, os: [windows], software: []}
category: gaming
tags: [marvel-rivals, highlights, kill-feed]
links:
  docs: https://github.com/example-dev/clips-kitty-marvel-rivals#readme
  funding: [https://github.com/sponsors/example-dev]
service: null                              # or {name, url, pricing, required: true|false}
examples:
  - {title: A team wipe, url: https://github.com/example-dev/clips-kitty-marvel-rivals/raw/v1.0.0/examples/wipe.jpg}
```

(All names and URLs above are placeholders.)

**Checks the validator makes** (clear message per failure, path to the field, all failures at once): required fields; id grammar and that the publisher matches the repository owner for listed plugins; SemVer; known `kind`, `capability`, `execution`, `inputs`, `outputs`, permissions, category; `inputs` consistent with permissions (`video` needs `video.read`, `transcript` needs `transcript.read`, `outputs: [clips]` needs `clips.write`); `execution: remote|hybrid` needs non-empty `network` and `sends`; `sends` non-empty needs `execution` other than `local`; `run.command` is a non-empty list of strings with no shell syntax, and `run.python_requirements` names a file inside the plugin; model references well formed (§8.6: 40-hex revision for Hugging Face); `requires.plugin_api` supported by this engine; tags lower case, at most 10; URLs `https`. A manifest is inert data: nothing in it is evaluated.

**Setting types** are `string`, `integer`, `number`, `boolean`, `choice` and `secret`. A `secret` is for the developer's own service (an API key or licence key). Clips Kitty stores it with its existing secrets store (`core/secrets.py:97-159`, DPAPI on Windows) under the plugin's id, never writes it into `job.json` or a log, and hands it to that plugin's process only, as an environment variable the SDK reads with `job.secret(name)`.

## 8.5 Execution models

| Model | What it means here | First release |
|---|---|---|
| **Native / local in-process** | plugin code imported into the engine | **Not offered.** Every in-process ecosystem studied gives plugins the host's full access (research §7.4), and the frozen build cannot import external Python anyway (`server/mcp.py:10-16`). Clips Kitty's own modes are the only in-process pipelines. |
| **Isolated local worker** | the engine starts the plugin's `run.command` as a child process with a job folder | **Built.** This is the default for every plugin. |
| **Remote** | the plugin's process sends work to its developer's API and returns the same `result.json` | **Built as a declaration** (`execution: remote`, `network`, `sends`) with the warnings shown; the mechanics are the developer's code inside the same child process. A no-code remote kind where the engine itself calls a URL is designed, not built. |
| **Hybrid** | local Clips Kitty services plus remote inference | **Built the same way** as remote: the job already carries the local transcript and the bundled FFmpeg path, so `Local video → Local Whisper → Developer-hosted model API → Local FFmpeg → Local clips` is: Clips Kitty downloads and transcribes, the plugin sends audio or frames to its API, Clips Kitty renders. |

Why the isolated worker first: it is the only model that keeps plugin code out of the engine, works for any language, and already has a precedent in the repository; remote and hybrid are the same mechanism with a different declaration.

### The run, step by step (`plugins/runner.py`)

1. `process_video` reaches detection with `config["clips"]["pipeline"] = {"id": …, "version": …, "settings": {…}}` (copied by the worker from the job payload like every other option, `server/jobs.py:187-262`).
2. The runner looks the plugin up in the plugin store, checks it is enabled, not blocked, compatible with this engine (§8.9) and that its declared requirements pass, and refuses with a message otherwise.
3. It creates `<data_dir>/plugins/runs/<video_id>-<n>/` and writes `job.json`:

```json
{
  "plugin_api": 1,
  "plugin": {"id": "example-dev/marvel-rivals-highlights", "version": "1.0.0"},
  "video": {"path": "…/downloads/abc123.mp4", "id": "abc123", "title": "…", "duration": 5321.4, "games": []},
  "transcript": {"path": "…/transcripts/abc123.json", "language": "en"},
  "settings": {"min_kills": 3},
  "limits": {"max_clips": 5, "min_duration": 15, "max_duration": 60},
  "models": {"killfeed": {"path": "…/killfeed.onnx", "revision": "0123…"}},
  "tools": {"ffmpeg": "…/ffmpeg.exe", "ffprobe": "…/ffprobe.exe"},
  "output_dir": "…/runs/abc123-1/out"
}
```

   `video` appears only with `video.read`, `transcript` only with `transcript.read`, `tools.ffmpeg` only with `ffmpeg`, models only those the manifest lists. Settings are validated against the manifest's `settings` schema first.
4. It starts `run.command` (with `{python}` replaced by the plugin's own environment when it has one, otherwise by the Python Clips Kitty finds: the `plugins.python` setting, then the engine's own interpreter in a source checkout, then `python` or `py` on `PATH`) with the plugin's install folder as working directory, the job folder path as the only argument and in `CLIPSKITTY_JOB`, the SDK folder on `PYTHONPATH`, and nothing from the engine's own environment beyond what a child process needs (`PATH`, `SYSTEMROOT`, `TEMP`, locale). Secrets from Clips Kitty's settings are never passed.
5. It reads standard output line by line: `{"type":"progress","fraction":0.4,"message":"…"}` becomes `progress.emit(stage="analyze", fraction=…, message=…)`, which the worker already folds into the job's percentage (`server/jobs.py:312-318`); other lines go to the job log. It honours the job's cancel flag through `core/cancel.py` and the manifest's timeout, killing the process tree on either.
6. On exit 0 it reads `result.json`, validates it with the same SDK validator, clamps ranges to the video and the job's limits, and returns `ClipCandidate`s: `start`, `end`, `score` (or a rank-based score when the plugin gives none), `hook` from the title, `reason`, `source = "plugin:<id>@<version>"`, and `subscores = {"plugin": id, "version": …, "label": …}`. On any other exit it raises with the plugin's last error line, and the job fails with that message.
7. `process_video` continues unchanged: metadata, `_render_files`, `_register_clip`. Clips returned as files (`clips.write`) are copied into the clip folder and registered through `_register_clip` with a candidate for their range, like `_sport_reels` does today (`core/pipeline.py:685-754`).

## 8.6 Models

**A model is a reference.** `plugins/models.py` parses each entry of a manifest's `models:` into a `ModelRef`:

| `source` | `id` | `revision` | stored in | installed when |
|---|---|---|---|---|
| `huggingface` | `owner/name` | 40-hex commit (required) | the Hugging Face cache (`HF_HUB_CACHE`, default under `<data_dir>/models/huggingface`), documented layout `models--owner--name/snapshots/<commit>/<file>` | every listed file exists under that snapshot |
| `ollama` | `name:tag` | digest (recommended) | Ollama's own store, via its API at `llm.ollama_host` | `/api/tags` lists it (and the digest matches, when given) |
| `url` | the URL | `sha256` (required) | `<data_dir>/models/files/<sha256>/<name>` | the file exists and its SHA-256 matches |
| `bundled` | a name the app ships (`whisper:small`, `yolov8n-pose`, `panns`) | n/a | `vendor/` or the frozen bundle, via the existing resolvers in `core/binaries.py` | the resolver finds it |

Developer-hosted inference, OpenRouter and Replicate are not model *files*: they belong to a remote or hybrid pipeline's own code and appear in its `network` and `sends` declarations.

**Shared model manager.** No new store. The manager is an index keyed by `(source, id, revision, file)` over the stores above, so:

- Two plugins listing the same Hugging Face file at the same commit get the same path; nothing downloads twice. Files unchanged between two commits are stored once by the Hugging Face cache itself, except on Windows without symlink permission, where the library documents duplicates per revision ([cache guide](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)). The manager reports which case applies.
- A plugin that needs Whisper or Gemma lists `bundled` or `ollama` references and gets the app's existing installation; it never downloads its own copy through the manager.
- A file whose SHA-256 is already in the index under another reference is reported as a duplicate, so the UI can say "already on this PC".

**Download** goes through one injectable fetcher. In the app it uses `huggingface_hub.hf_hub_download(repo_id, filename, revision=<commit>)` when that library is present (faster-whisper already depends on it), and plain HTTPS with a SHA-256 check for `url` references. Pickle-format files (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`) are refused unless the user confirms on the install screen. Tests use a fake fetcher; nothing large is downloaded tonight.

**Shown before install**: per model, its licence (manifest value, checked against the Hub's `cardData.license` when online), size, format, gated status and "already installed". Hardware needs come from the manifest's `requirements`.

**Loading a model is the plugin's job**, in its own process: loaders can execute code ([CVE-2026-4372](https://advisories.gitlab.com/pypi/transformers/CVE-2026-4372/)), so the engine hands over paths and never loads a plugin's model itself.

## 8.7 Capabilities and permissions

**What a pipeline can use**, from the capability list in `repo-map.md` §14, and how it reaches it:

| Clips Kitty capability | How a plugin reaches it | Status |
|---|---|---|
| source video (`video.read`) | path in `job.json` | built |
| transcript with word timings (`transcript.read`) | path in `job.json` (`{"segments": [{start, end, text, words}]}`, `transcription/transcriber.py:249-257`) | built |
| FFmpeg (`ffmpeg`) | the bundled binary's path in `job.tools` (`core/binaries.ffmpeg()`) | built |
| models (`models:`) | resolved paths in `job.models` | built |
| rendering, captions, title card, library, publishing | by returning moments; Clips Kitty does all of it | built |
| progress, log, cancel | standard output lines; the process is killed on cancel | built |
| Ollama / Gemma (`ollama`) | the Ollama host and the user's chosen model in `job.tools.ollama` | built (hand-over only) |
| the stable HTTP API | `clipskitty_sdk.client.LocalAPI` | built (no per-plugin credential yet) |
| per-plugin API credential with scopes | a token in `job.json`, checked by the plugin routes | designed, not built |
| OCR, YOLO tracker, PANNs, frame grabbing as host services | host endpoints ("run OCR on these frames") | designed, not built (today they are private Python, `repo-map.md` §13) |

**Permissions.** No plugin gets anything by default. Each permission below is shown before install with its label. "Enforced by Clips Kitty" means the engine checks it and refuses otherwise. "Declared" means the developer states it and nothing stops the plugin doing otherwise, because a plugin process runs with the user's own rights (`docs/platform-research.md` §7.6 q26).

| Permission | Shown to the user as | Enforced or declared |
|---|---|---|
| `video.read` | Reads the video you process | Enforced for the hand-over: without it the job has no video path. Declared beyond that (the process could open files itself). |
| `transcript.read` | Reads its transcript | Same as above. |
| `clips.write` | Returns finished clips | Enforced: returned files are ignored without it. |
| `ffmpeg` | Uses Clips Kitty's FFmpeg | Enforced for the hand-over; declared beyond. |
| `ollama` | Uses your local AI model | Enforced for the hand-over; declared beyond (Ollama has no authentication). |
| `models` (the `models:` list) | Downloads these models: … | Enforced for downloads the model manager makes; declared beyond. |
| `gpu` | Uses your graphics card | Declared (a requirement). |
| `network` + host list | Connects to: … | Declared. |
| `sends` | ⚠ Sends your video / audio / frames / transcript to … | Declared, and shown as a warning on the listing, at install and on each job. |
| `filesystem.read`, `filesystem.write` | Reads / writes files outside its own folder | Declared. |
| `project.read`, `project.write` (library through the API) | Reads / changes your clip library | Declared today, because the local API has no authentication; becomes enforced with the per-plugin credential (designed). |

The brief's `video.write` collapses into `clips.write` (the only way a pipeline writes video into Clips Kitty), and `model.download` / `model.cache` into the `models:` list.

**Security, in one place.** A plugin is code from the internet that runs as the user. The install screen says so in those words, names publisher, repository, version and commit, lists the permissions with their labels and any data-leaving warning, and installs nothing until the user confirms. Plugin-manager routes that change what is installed or enabled require a header with the desktop app's per-session secret (`X-Clips-Kitty-Session`), so a web page cannot trigger them through the open loopback API; the CORS allow-list already stops cross-origin reads (`server/api.py:577-582`). Installing never runs anything from the plugin: no `setup.py`, no install script, no hook; the installer copies files and validates the manifest. Python packages a plugin lists in `run.python_requirements` are a separate line on the install screen that the user agrees to; they go into the plugin's own environment under `<data_dir>/plugins/envs/` as wheels only, with hashes (`pip install --only-binary=:all: --require-hashes`), so no package build script runs at install and nothing from them runs until a job starts the plugin. Windows Job Object limits for plugin processes are designed and will be called enforced only once built and tested on Windows.

## 8.8 Trust

**Tiers say what actually happened**, never more (research §7.4 findings 6 and 7, Q29, Q30):

| Tier | Means | Who decides | Shown as |
|---|---|---|---|
| **Official** | Ships inside Clips Kitty and is maintained by the project. Today's modes (Shorts, Gaming, Sports) are described by built-in manifests (§8.11). | The app bundle: only `plugins/builtin/` can carry the reserved `clipskitty/` publisher; the registry refuses it from anyone else. | "Official" |
| **Listed** | Has an entry in a registry index the user has enabled, and the index's automated checks passed (§8.10). Nobody has read the code. | The registry's pull-request checks plus a maintainer's merge. | "Listed · not reviewed by a person" |
| **Installed from a link** | Installed from a Git URL or a folder on this PC that no enabled index lists. | The user, on the install screen. | "Not listed · Clips Kitty has not checked this" |
| **Blocked** | Matches the block list. It does not run. | A registry maintainer (removal path below). | "Blocked: <reason>" in red |

**"Verified" is defined and unused.** If the owner sets up a real review, the label would be **Reviewed**: a named maintainer read one specific commit against a written checklist, and the label applies to that commit only. Even then it is not a security audit, and the UI would say so. Until such a process exists, no listing carries it (`DECISIONS.md` D5).

**Separate attributes**, each shown only when it is a fact:

| Attribute | First release |
|---|---|
| Source visible | Every listed plugin: the index build checks that the pinned commit is fetchable from a public repository. Link installs show the URL or folder they came from. |
| Publisher matches repository owner | Checked by the index build (the `publisher` part of the id equals the GitHub owner). This is all "publisher" means; there is no publisher verification. |
| Pinned commit | Every install records the 40-character commit it installed, and the installer refuses a fetch whose commit differs from the listing. This proves the files are the ones listed, not who wrote them. |
| Signed | Not built. Commit pinning covers integrity; signing would add authorship and is designed for later. |
| Local / remote / hybrid | From `execution`, with the ⚠ data warning from `sends` (§8.7). Remote API is an attribute, not a tier: a remote pipeline can be Official, Listed or from a link. |
| Unofficial | Any listing whose `games`, tags or name refer to a game, league, team or product shows "Unofficial · not made or endorsed by the makers of <name>". **Owner's note:** this label is probably needed unless the rights holder published the plugin; deciding when it can be dropped is the owner's call (`OVERNIGHT-REPORT.md` questions). |

**Removal path.**

1. A report arrives as an issue on the registry repository, or privately through GitHub's private vulnerability reporting on that repository for anything harmful.
2. A maintainer adds an entry to `registry/blocklist.yaml`: `id`, `versions` (`"*"` or a list), `severity` (`blocked` or `delisted`), `reason`, `date` and an optional advisory link. CI rebuilds `index.json`; blocked and delisted versions disappear from the Marketplace.
3. The client refreshes the index and block list whenever the Marketplace opens, and every release of the app ships the list as of its build, so an offline PC still knows about old entries. A daily refresh while plugins are installed is designed, not built.
4. Copies already installed are flagged from the cached list. `blocked`: the runner refuses to start it, the plugin page shows the reason in red and offers Remove, and nothing else changes on its own. `delisted` (abandoned, licence problem, broken): it still runs and shows "No longer listed: <reason>". Clips Kitty never deletes a user's files by itself; removal is the user's click.

## 8.9 Versioning

| Version | Where it lives | Rule |
|---|---|---|
| App | `ui/package.json` (`2.0.0` today), reported by `GET /health` | `requires.clips_kitty` is a SemVer range checked at install and at run. |
| Public API | `API_VERSION = 1` (`server/api.py:34`), also in `/health` | Phase 2 labels each route stable, experimental or internal. A stable route changes only additively within API version 1; removing or changing one means API version 2, with the old behaviour kept for at least one app release (policy, written into the API page). |
| Plugin API (job contract) | `PLUGIN_API_VERSION = 1` in `clipskitty_sdk` and the engine | The engine lists the plugin API versions it supports. A plugin asking for one it does not support is refused at install with "needs a newer (or older) Clips Kitty". Within a version only optional fields are added: plugins ignore job fields they do not know, the engine ignores result fields it does not know. |
| SDK | `clipskitty_sdk.__version__`, major = plugin API | The SDK is a convenience over the files; a plugin that vendors an older 1.x copy still works because the contract is `job.json` and `result.json`, not the SDK's functions. |
| Manifest | `manifest_version: 1` | The validator knows which versions it can read and says so. |
| Plugin and pipeline | `version` (SemVer), one pipeline per plugin in API 1 | Each index entry maps a version to a tag and a commit; versions are immutable and never deleted, only blocked or delisted. Clips record `plugin` and `plugin_version` in their saved scores (`candidate.subscores` → `clips.scores`, `core/pipeline.py:1592`), next to the existing `sport_label` and `game_why` keys. |
| Model | `revision` (Hugging Face commit, Ollama digest, file SHA-256) | Pinned in the manifest. A new model revision is a new plugin version. |

**Pinning, updates, rollback** (research Q13-Q15). The index is refreshed when the Marketplace opens; an update shows the new version, its changelog link and any change in permissions, network hosts or data sent, and installs only on a click. The new version installs beside the old one and becomes active only when it validates; the previous version stays on disk and **Roll back** makes it active again. Before each change the manager writes a snapshot of the plugin set (app version, each plugin's version and commit, enabled state) to `<data_dir>/plugins/snapshots/`. **Pin** stops update offers for a plugin. No automatic updates; a security problem is handled by the block list, not by a forced update. Because Colin's PC is short of disk, only one previous version is kept by default.

## 8.10 Marketplace and registry

**Hybrid registry** (research Q12): one official static index built by CI from one listing file per plugin in a public Git repository, reviewed by pull request; installs from any GitHub URL, labelled "not listed"; and a setting to add other index URLs. The app never needs a Clips Kitty server.

**In this repository first** (`DECISIONS.md` D5): `registry/` holds the format and tooling until the owner picks a public home.

```text
registry/
  plugins/<publisher>/<name>.yaml   one listing file per plugin, submitted by pull request
  blocklist.yaml
  index.json                        built by CI; empty plugin list today
  README.md                         how to submit
scripts/build_registry_index.py     validate listings, fetch each manifest at its commit, write index.json
```

A listing file is small because the manifest is the source of truth:

```yaml
id: example-dev/marvel-rivals-highlights
repository: https://github.com/example-dev/clips-kitty-marvel-rivals
path: .                         # subfolder holding clipskitty.yaml, for monorepos
versions:
  - {version: 1.0.0, tag: v1.0.0, commit: 0123456789abcdef0123456789abcdef01234567}
```

**The build script** validates each listing, fetches the manifest at the pinned commit as raw text (it never clones with hooks, installs or runs plugin code), runs the manifest validator, checks that the manifest's `id` and `version` match the listing, that the publisher matches the repository owner, that a licence is declared, that the `clipskitty` publisher is not claimed, and writes `index.json` with the manifest fields the Marketplace shows plus the check results. GitHub stars and release download counts may be captured at build time and labelled as such. CI holds no secrets beyond the read-only default token and never checks out a contributor's branch with write permissions ([CVE-2025-6705](https://blogs.eclipse.org/post/mikaël-barbero/eclipse-open-vsx-registry-security-advisory), research §7.4 H3). In tests the fetch is a fixture directory; nothing touches the network.

**The client** (`plugins/registry.py`) reads index URLs from settings (none is invented: the default is the bundled copy until the owner publishes one), caches the last good copy under `<data_dir>/plugins/cache/`, works offline from that cache, and searches it locally.

**Browse and search.**

| Need | Design |
|---|---|
| Types | Pipelines (built); Plugins, Models, Workflows, Providers, Integrations appear as tabs that say "planned" until a kind exists. Models listed by installed plugins appear under Models. |
| Categories | A closed list checked by CI: gaming, sports, creators, streaming, podcasting, captions, detection, analytics, audio, utilities (the brief's ten, research Q32). |
| Tags | Lower case, at most 10; game and sport slugs (`marvel-rivals`, `world-of-warcraft`, `rocket-league`, `soccer`, `nhl`). |
| Search | Local and forgiving: case and punctuation folded; matches name, description, tags, games, events and capability; an alias table grown by pull request (`wow` → `world-of-warcraft`, `football` → `soccer`); every word must match somewhere, ranked by where (name over tag over description). So "WoW", "World of Warcraft PvP", "Soccer goals", "Podcast shorts" each find the one specialised listing instead of a broad category. |
| At a glance | Name, one-line description, publisher, tier, local/remote/hybrid with the ⚠ data line, requirements against this PC, permissions, models and their sources, version, licence, repository link, last updated. |
| Detail | Everything above plus events detected, settings, example images or clips (links to the developer's own files), funding links, `service` label (Free · Optional paid service · Paid service required), GitHub stars "at index build time", check results. |
| Hardware fit | From `requirements` and the existing hardware probe used by the Models page (`ui/src/renderer/src/pages/Models.tsx`; `GET /system`): "Runs on this PC", "CPU only", "Needs a GPU with 6 GB", "Cloud service required". |

**Ratings, counts, examples, cost** (research Q34-Q36, Q43): no in-app ratings (they need accounts and are gamed); GitHub stars shown as GitHub stars; no telemetry and no install counts, only "release downloads" if a plugin ships release assets; examples are the developer's own links. The Marketplace stays free because it runs nothing: a Git repository, a CI job, a JSON file. Clips Kitty takes no cut and processes no payments; `links.funding` and `service` are outbound links only.

## 8.11 First-party dogfooding

The existing modes stay exactly where they are. Two things make them part of the platform:

1. **Built-in manifests.** `plugins/builtin/shorts.yaml`, `gaming.yaml` and `sports.yaml` describe the official modes in the same schema (`id: clipskitty/shorts`, `kind: pipeline`, `capability: highlight_detection`, `run: builtin`), validated by the same validator, so the Marketplace lists them as Official beside community pipelines and the manifest schema is tested against real pipelines. `run: builtin` is accepted only for the reserved publisher and only from the app bundle; selecting one leaves the job exactly as it is today.
2. **A first-party adapter that satisfies the contract.** `examples/pipelines/transcript-highlights/` is a contract-only pipeline whose `main.py` reads a `job.json`, loads the transcript and calls Clips Kitty's own transcript scorer (`analysis/highlights.find_highlights`), as `examples/score_a_transcript.py` already does with the offline `examples/fake_backend.py`, and writes `result.json`. It shows that an existing first-party detector fits the contract unchanged. Because it imports Clips Kitty internals it is labelled a first-party adapter, not an example of what outside developers may do; the community example (Phase 5) imports only the SDK.

## 8.12 Designed now, built later

**Capability market.** Every pipeline names a `capability` (first: `highlight_detection`). Several implementations coexist under one capability: Official Shorts, Official Gaming, Open Shorts, a community model, a soccer model, a game model, a remote API. The job picks one by id (`pipeline: {id, version}`); the Marketplace can group by capability. Nothing routes between them in the first release. What keeps the door open: typed `capability`, the shared result shape, `events` labels, and `execution` with `sends`. Later, OpenRouter-style preferences (research §7.3) can be added as job fields (`pipeline: {capability: highlight_detection, prefer: [...], fallback: true}`) without changing a plugin.

**Pipeline composition.** A later `kind: component` with typed inputs and outputs (`frames → events`, `events → ranges`, `ranges → ranked ranges`) lets a pipeline list other plugins it calls through the engine, so a Marvel Rivals pipeline can reuse a community kill-feed reader or a highlight ranker. What keeps the door open now: plugin ids are namespaced and versioned, `requires` is a mapping that can gain a `plugins:` entry, the result already carries labels and reasons a ranker can consume, and the model manager already shares models between plugins.

| Designed, not built | Why not tonight | What it changes later |
|---|---|---|
| Per-plugin API credential with scopes | needs the plugin-facing routes to exist first | `project.*` permissions become enforced for calls made with it |
| Windows Job Object limits (memory, child processes) | must be tested on Windows | "Enforced" labels for resource limits |
| Host services (OCR, tracker, frame grabbing) as endpoints | they are private Python today (`repo-map.md` §13) | plugins stop shipping their own OCR |
| Multi-segment ranges | renderer cuts one span today | `segments: [{start, end}]` per range |
| Other plugin kinds (caption-style, publisher, source, integration, provider, component) | one kind proves the contract | new `kind` values |
| A bundled Python for plugins in the frozen build | packaging work | `{python}` resolves without a system Python |
| Opt-in usage counts | owner's call | a counter, never on by default |
## 8.13 Worked examples

Both are on paper. Publisher names, repositories, model ids and commits are placeholders.

### Open Shorts for Clips Kitty

An independent developer wraps [Open Shorts](https://github.com/mutonby/openshorts) (MIT core) so its moment picker can feed Clips Kitty. The adapter copies no Open Shorts code and Open Shorts is not changed; it talks to a running Open Shorts instance over that project's own REST API, as sketched in the [research note](platform/research-notes/clipping-pipelines.md) (§B, "Adapter sketch").

```yaml
manifest_version: 1
id: example-dev/open-shorts
name: Open Shorts for Clips Kitty
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: Lets Open Shorts pick the moments, then Clips Kitty titles, crops, captions and files them.
author: {name: Example Developer, url: https://github.com/example-dev}
repository: https://github.com/example-dev/clips-kitty-open-shorts
license: MIT
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run: {command: ["{python}", "src/main.py"], timeout_minutes: 120}
execution: hybrid
inputs: [video]
outputs: [ranges]
settings:
  instance: {type: choice, options: [this-pc, hosted], default: this-pc, title: Where Open Shorts runs}
  url: {type: string, default: "http://127.0.0.1:8000", title: Open Shorts address on this PC}
  api_key: {type: secret, title: Open Shorts API key (hosted only)}
permissions: [video.read, network]
network: ["127.0.0.1:8000", api.openshorts.app]
sends:
  - {data: video_link, to: api.openshorts.app, when: "instance = hosted"}
  - {data: transcript, to: "Google Gemini, through Open Shorts", when: "Open Shorts is not set to a local model"}
requirements: {gpu: optional, ram_gb: 8, disk_gb: 10, os: [windows, macos, linux], software: [docker]}
category: creators
tags: [open-shorts, podcast, highlights]
service: {name: Open Shorts hosted, url: "https://github.com/mutonby/openshorts#readme", required: false}
```

**What runs.** Clips Kitty downloads the video as usual. At the detection step the engine writes `job.json` (video path, limits; no transcript, because Open Shorts transcribes for itself) and starts `src/main.py`, which:

| Step | Call (Open Shorts' own API, from the research note) |
|---|---|
| upload (this PC) | `POST /api/uploads`, `PUT /api/uploads/{upload_id}` with the file |
| or link (hosted) | the video's original link; per the note, hosted mode takes links, so a local file is refused with a message |
| start | `POST /api/process` with `acknowledged: true`, `upload_id` or `url`, `target_clips` and clip lengths from `job.limits`, `captions: false`, `auto_hook: false`, `output_format: vertical` |
| progress | `GET /api/status/{job_id}` every 10 s; each poll becomes `job.progress(...)`, its `logs[]` lines `job.log(...)` (Open Shorts' webhooks refuse localhost receivers, so polling is the only way) |
| result | each `result.clips[i]` → `job.add_range(start, end, title=…, label="open-shorts", reason=…)`; no score unless Open Shorts returns one, so Clips Kitty ranks by order |

Clips Kitty then does everything after detection, unchanged: Gemma titles, the vertical crop or Vertical Live, captions, the title card, the library and publishing. The user's own API calls are the normal ones: `POST /plugins/plan` and `POST /plugins/install` from the Marketplace, then `POST /jobs` with `pipeline: {id: "example-dev/open-shorts", version: "1.0.0"}`, progress on `/ws`, clips from `GET /clips`.

**What does not fit, said plainly.** Open Shorts brings its own Whisper, YOLO and other models inside its container, so they are not shared with Clips Kitty's. Open Shorts has no documented way to accept a transcript, so "use Clips Kitty's Whisper" is not possible without a change in Open Shorts. Its moment picker can use Clips Kitty's Ollama if the user sets `LLM_BASE_URL` in Open Shorts' own configuration (research note §B, Models); the adapter can show the exact line but cannot set it. Even on this PC, Open Shorts' own moment picker sends the transcript (never the video) to Google Gemini unless it is pointed at a local model (research note §B, "Local, remote or both"), so the manifest declares that too. It needs Docker (WSL 2 on Windows) or a hosted account.

**What the user sees at install:**

```text
Install Open Shorts for Clips Kitty 1.0.0?
example-dev · github.com/example-dev/clips-kitty-open-shorts · commit 0123456 · MIT
Listed · not reviewed by a person · Unofficial: not made by the Open Shorts project

This plugin is code from the internet. It runs on this PC with your rights.

It will
  Read the video you process ............................ enforced for the hand-over
  Connect to 127.0.0.1:8000 and api.openshorts.app ..... declared by the developer
⚠ If you choose Hosted: sends the video's link to api.openshorts.app,
  which downloads and processes the video on its servers.
⚠ Open Shorts itself sends the transcript to Google Gemini unless you set it
  to a local model in Open Shorts' own settings.
Needs   Docker Desktop running Open Shorts, or an Open Shorts account
Service Open Shorts hosted · optional paid service · pricing in its README
Models  none from Clips Kitty (Open Shorts brings its own)

                                                      [Cancel]  [Install]
```

### Marvel Rivals Highlights

A developer who knows the game trains a kill-feed detector, publishes it on Hugging Face, and ships a pipeline that uses it. The manifest is the one in §8.4, plus `run.python_requirements: requirements.txt` listing `onnxruntime` as a hash-pinned wheel.

**What runs.** Clips Kitty downloads and transcribes as usual. The model manager has already placed `killfeed.onnx` at the pinned commit in the shared Hugging Face cache at install. The engine writes `job.json` with the video, the transcript, `tools.ffmpeg`, `models.killfeed.path` and the user's `min_kills` setting, and starts the plugin in its own environment. The plugin:

1. runs the bundled FFmpeg from `job.tools.ffmpeg` to sample the kill-feed corner at a few frames a second;
2. runs `killfeed.onnx` with onnxruntime on those frames (its own process, its own GPU use);
3. groups eliminations into multi-kills and team wipes, and raises the score where the transcript shows the caster reacting;
4. reports `job.progress(…)` as it goes and finishes with ranges such as `add_range(751.0, 778.5, score=92, label="team_wipe", title="Team wipe", reason="5 eliminations in 6 s")`.

Clips Kitty renders them with its Gaming layouts or Vertical Live, captions, title card and library; each clip's saved scores carry `plugin`, `plugin_version`, `plugin_label` and `plugin_why`, so the editor can say which pipeline chose it and why. API calls are the same as for Open Shorts: plan, install, `POST /jobs` with `pipeline`, `/ws`, `GET /clips`. The plugin itself makes no network call at run time.

**What the user sees at install:**

```text
Install Marvel Rivals Highlights 1.0.0?
example-dev · github.com/example-dev/clips-kitty-marvel-rivals · commit 0123456 · MIT
Listed · not reviewed by a person · Unofficial: not made or endorsed by the makers of Marvel Rivals

This plugin is code from the internet. It runs on this PC with your rights.
Runs on this PC. The developer declares that nothing leaves your computer.

It will
  Read the video you process and its transcript ........ enforced for the hand-over
  Use Clips Kitty's FFmpeg .............................. enforced for the hand-over
  Use your graphics card ................................ declared by the developer
Downloads
  killfeed.onnx · <size from the Hub> · Apache-2.0 · huggingface.co/example-dev/marvel-rivals-killfeed @ 0123456
Python packages (installed into its own folder, wheels only)
  onnxruntime <version from requirements.txt>
Needs   GPU recommended, 6 GB video memory · this PC: <GPU and VRAM from the hardware probe>
        2 GB disk · free: <from the disk check>

                                                      [Cancel]  [Install]
```
## 8.14 Build plan

Each phase is its own commits (code, then docs), ends with `ruff check .` and the full `pytest` compared with the baseline's 14 known failures, and is pushed before the next starts. Shared code that Sports and Soccer use (`core/pipeline.py`, `server/api.py`, `server/jobs.py`) gets only an added branch that is never taken unless a job names a plugin, and each such change is listed in the morning report in plain words.

| Phase | Reused | Created | Tested by | Main risk |
|---|---|---|---|---|
| **2. Public API boundary** | every route in `server/api.py` and the modules it installs; `API_VERSION` (`server/api.py:34`); `GET /health`; FastAPI's own `app.openapi()` | `server/api_stability.py` (a label per method and path, plus `stable_routes()`); `scripts/gen_api_reference.py`; `docs/developers/api-reference.md` (generated) and `docs/developers/api.md` | `tests/test_api_contract.py`: every registered route has a label (a new route without one fails with a message saying where to add it); the stable routes' methods, paths, parameters and request fields match a pinned snapshot (`tests/fixtures/api/stable_contract.json`); behaviour of the stable calls pinned through `TestClient` (health fields, job validation errors, list shapes) | Labelling about 150 paths by hand. Mitigation: start with a small stable set (health, version, jobs, queue, clips, library reads) and label the rest experimental or internal, which promises nothing. |
| **3. Plugin and pipeline contract** | `core/progress.emit` and the worker's `fraction` folding (`server/jobs.py:312-318`); `core/cancel.py`; `ClipCandidate`; `_process_options` and `JobIn` (`server/api.py:39-66`, `:440-550`); the worker's option copy (`server/jobs.py:187-262`); the detection point (`core/pipeline.py:438-450`); `analysis.highlights.find_highlights` with `examples/fake_backend.py` | `sdk/python/clipskitty_sdk/` (`job.py`, `client.py`, `__main__.py`, `contract.py`); `plugins/__init__.py`, `plugins/_sdk.py`, `plugins/runner.py`, `plugins/store.py` (lookup only); the `pipeline` job option; `examples/pipelines/transcript-highlights/` (first-party adapter); `docs/developers/sdk.md`, `pipeline-development.md` | `tests/test_plugin_sdk.py` (job reading, progress lines, result writing, errors); `tests/test_plugin_runner.py` with tiny fixture plugins (success, error exit, bad result, timeout, cancel, progress mapped to the `analyze` stage, `job.json` withholding what a permission does not grant, secrets kept out of files); `tests/test_plugin_job_option.py` (validation and mutual exclusion; an unchanged payload when no pipeline is named; `process_video` takes the branch only with `pipeline`); the first-party adapter run on `tests/assets/sample_transcript.json` | The branch in `process_video`, which every mode passes through. Mitigation: one `if` before `find_clips`, and a test that a job without `pipeline` reaches `find_clips` with the same arguments as before. |
| **4. Manifest** | PyYAML (already a dependency) | `sdk/python/clipskitty_sdk/manifest.py`; `sdk/python/clipskitty_sdk/schema/clipskitty.schema.json`; `plugins/builtin/{shorts,gaming,sports}.yaml`; `docs/developers/plugin-manifest.md`, `permissions.md` | `tests/test_plugin_manifest.py` over `tests/fixtures/plugins/manifests/valid/*.yaml` and `invalid/*.yaml`, each invalid fixture naming the message it must produce; the built-in manifests validate; the JSON Schema and the validator agree on fields and enums | Too strict or too loose a schema. Mitigation: the fixtures are the spec; every rule has a fixture. |
| **5. Example external pipeline** | the bundled FFmpeg via `job.tools` (`core/binaries.ffmpeg()`); the renderer and library through the runner | `examples/pipelines/scene-cut-highlights/` laid out as its own repository (`clipskitty.yaml`, `README.md`, `LICENSE`, `src/main.py`): FFmpeg scene-change and loudness detection, described as a demo that finds cuts and loud moments and nothing more; `docs/developers/example-pipeline.md`, `getting-started.md` | `tests/test_example_pipeline.py`: end to end on a tiny FFmpeg-made video with hard cuts and a loud stretch, through `plugins.runner` and into ranges; an AST check that it imports only the standard library and `clipskitty_sdk`; a run with `PYTHONPATH` set to `sdk/python` alone; `python -m clipskitty_sdk validate` passes on it | A detection test that passes for the wrong reason. Mitigation: the test video's cuts and loud stretch are at known times and the test checks the ranges land on them. |
| **6. Plugin manager** | `<data_dir>` from config; `core/secrets.py`; the `install(app, …)` pattern (`server/api.py:629-706`); `git` when present | `plugins/manager.py` (install from a folder or a Git URL at a 40-character commit, validate before activating, enable, disable, update, remove, roll back, pin, snapshots); `plugins/sources.py` (folder copy; `git` clone and checkout of one commit with hooks disabled; GitHub archive download behind an injectable fetcher; safe extraction); `plugins/permissions.py`; `plugins/api.py` (routes labelled experimental; write routes need `X-Clips-Kitty-Session`); the session secret passed from `ui/src/main/index.ts` and exposed by `ui/src/preload/index.ts`; Python package environments behind an injectable runner if time allows, else planned; `docs/developers/plugin-development.md`, `security.md`, `versioning.md` | `tests/test_plugin_manager.py` against a fixture Git repository made in a temporary folder (`file://`), no network: install, the plan showing permissions and the ⚠ data warning before install, a marker proving nothing from the plugin ran at install, invalid plugin refused, wrong commit refused, enable, disable, update beside the old version, roll back, remove, pin, blocked plugin refused; `tests/test_plugin_api.py` for the routes and the session header | Files locked on Windows during update or remove, which cannot be tested here. Mitigation: versions live in separate folders, and the active one is a pointer in `installed.json`, so nothing is overwritten in place. |
| **7. GitHub registry** | the manifest validator; the plugin manager's install | `registry/README.md`, `registry/plugins/` (empty), `registry/blocklist.yaml`, `registry/index.json` (empty list); `scripts/build_registry_index.py` (with `--check`); `plugins/registry.py` (index URLs from settings, offline cache, search with aliases); `docs/developers/marketplace-publishing.md` | `tests/test_registry.py` with `tests/fixtures/registry/` (listings plus manifests at fake commits): the build accepts good listings and rejects bad ones with clear messages; the client reads, caches and works offline; search finds "WoW", "World of Warcraft PvP", "Soccer goals" and the brief's other queries in a fixture index; install from an index entry through the manager; a blocked entry is hidden and its installed copy flagged | The index CI being a supply-chain target ([CVE-2025-6705](https://blogs.eclipse.org/post/mikaël-barbero/eclipse-open-vsx-registry-security-advisory)). Mitigation: the build reads manifests as text, never runs plugin code, needs no secrets; the workflow itself is designed, and only the script and its `--check` are committed tonight. |
| **8. Marketplace UI** | the Models page's catalogue layout and hardware probe (`ui/src/renderer/src/pages/Models.tsx`); the sidebar in `ui/src/renderer/src/App.tsx`; `lib/api.ts`; the job form `components/queue/AddVideos.tsx`; Tailwind tokens in `theme.css` | `ui/src/renderer/src/pages/Marketplace.tsx`; `ui/src/renderer/src/lib/marketplace.ts` (search, filters and labels as pure functions); `lib/plugins.ts` (API calls); a Pipeline choice in the job form; a sidebar entry | `tests/test_ui_marketplace.py` runs `lib/marketplace.ts` under Node, as `tests/test_ui_speaker_turns_sync.py` does, and checks it agrees with `plugins/registry.py` on the same fixture index; `npm run typecheck` and `npm run build` in `ui/` | The desktop app cannot be opened here, so nothing is seen. The report says so. |
| **9. Model management** | the Hugging Face cache layout; Ollama's `/api/tags` through the existing Ollama host setting; `vendor/` resolvers in `core/binaries.py` | `plugins/models.py` (references, resolution, installed detection, duplicate detection by reference and SHA-256, licence, size and gated status, pickle refusal, an injectable fetcher); model routes in `plugins/api.py`; `docs/developers/model-references.md`, `hugging-face.md`, `remote-apis.md`, `local-apis.md` | `tests/test_plugin_models.py`: reference parsing (good and bad), a fake Hugging Face cache tree for installed detection and reuse across two plugins, duplicate detection, a mocked Ollama `/api/tags`, a fake fetcher that records it was called once for two plugins, pickle refusal, licence and size surfaced from mocked metadata | Windows without symlink permission duplicates files per revision. It cannot be tested here; the manager reports it and the docs say so. |

**Cross-cutting.** `clips-studio.spec` gains `collect_submodules("plugins")` and the data folders (`sdk/python`, `plugins/builtin`, `registry/index.json`, `registry/blocklist.yaml`), with a packaging test like the existing sports one (`tests/test_packaging.py:66`). CI's `compileall` list gains `plugins` and `sdk`. The remaining developer pages (Platform Overview, Remote APIs, Troubleshooting) are written with the phase that makes them true, and anything not built is labelled "planned".

## How the four success tests pass

**1. The niche developer.** They write `clipskitty.yaml` and `src/main.py` against the SDK (§8.2-8.4) and never open Clips Kitty's source; reference their model on Hugging Face by commit (§8.6); test with `python -m clipskitty_sdk validate` and `run`; push to their own GitHub repository; open a pull request adding one listing file to the registry (§8.10). Users find it by searching the game's name, see what it needs and sends, install it (§8.7, §8.13), and pick it in the job form. Clips Kitty does the download, transcript, FFmpeg, crop, captions, titles, library and publishing. Nothing is forked. Components: Phases 3-8; the model reference is Phase 9.

**2. Open Shorts.** The adapter in §8.13 is a pipeline like any other; Open Shorts stays the Open Shorts project's, runs in its own Docker container or hosted service, and the user keeps Clips Kitty for everything after detection. Its data use is declared and shown. Not built tonight (research §7.7 question 8); the contract it needs is.

**3. Marvel Rivals Highlights.** §8.13: the developer provides the model, the frame logic and the event labels; Clips Kitty provides the video, transcript, FFmpeg, the shared model cache, the local runtime, rendering, the library, installation and the Marketplace. The GPU is the user's, used by the plugin's own process.

**4. The existing gaming market.** Every game pipeline implements the same `highlight_detection` capability with game tags, so the Marketplace can hold Marvel Rivals, World of Warcraft PvP, Minecraft, Valorant and Rocket League pipelines side by side, searchable by name, each from a different developer, next to the Official Gaming mode. Research §7.2 found per-game tuning everywhere and a third-party ecosystem nowhere (Track D answer); this design is what supplies the second.
