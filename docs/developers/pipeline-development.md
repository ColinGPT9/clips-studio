# Pipeline development

A **pipeline plugin** decides which moments of a video become clips. Clips Kitty does everything around that decision exactly as it does for its own modes: downloading, transcribing, writing titles and hashtags with the user's AI model, cropping to vertical, captions, rendering with FFmpeg, the library, publishing and the automation that watches channels. You write the part that knows your niche (a game's kill feed, a sport's scoreboard, a podcast's best exchanges) and nothing else.

Status: **built** in plugin contract 1: the contract, the SDK, the runner in the engine, the job option, the manifest validator and the plugin manager (install from a folder or a Git commit, through experimental API routes). **Planned**: the Marketplace screen and returning finished clip files.

## How a run works

```text
Clips Kitty                                   your plugin
-----------                                   -----------
download, transcribe, audio and visual signals
make a job folder: job.json, transcript.json
start run.command with the folder ────────►   read_job()
show progress on "Finding the best moments" ◄─ {"type": "progress", ...} lines
                                              add_range(...) for each moment
read result.json when the process exits 0 ◄── finish() writes result.json
titles, crop, captions, render, library
```

Concretely (`plugins/runner.py`):

1. The job's `pipeline` option names your plugin. Clips Kitty checks it is installed and turned on.
2. It makes a job folder under `<data folder>/plugins/runs/` and writes `job.json` and, with `transcript.read`, `transcript.json`. The last five job folders are kept so a failed run can be looked at.
3. It starts your manifest's `run.command` with the job folder as the last argument, in your plugin's own folder, with a cleaned environment (below).
4. Progress lines move the job's progress bar inside the "Finding the best moments" step. Other lines on standard output and everything on standard error go to the job log.
5. When the process exits with 0, it reads `result.json` and checks it with the same checks the SDK has. Exit codes other than 0, an `{"type": "error"}` line, a missing or invalid `result.json`, or running past the time limit fail the job with a message naming your plugin.
6. Your ranges become the job's clips, and the job carries on as any other.

Cancelling a job stops your process and everything it started. Stopping the whole process tree is tested on Linux; on Windows it uses `taskkill /T /F` and has not been tested.

## The smallest plugin

```text
my-pipeline/
  clipskitty.yaml
  src/main.py
```

`clipskitty.yaml`:

```yaml
manifest_version: 1
id: example-dev/loud-moments          # publisher/name: lower case, digits and hyphens
name: Loud moments
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: Picks the loudest stretches of a video.
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run:
  command: ["{python}", "src/main.py"]
  timeout_minutes: 30
execution: local
inputs: [video]
outputs: [ranges]
settings:
  threshold: {type: number, default: 0.8, minimum: 0, maximum: 1, title: How loud}
permissions: [video.read, ffmpeg]
sends: []
```

`src/main.py`:

```python
from clipskitty_sdk import run


def main(job):
    for start, end, score in loud_stretches(job.video.path, job.tools.ffmpeg, job.settings["threshold"]):
        job.add_range(start, end, score=score, label="loud", reason="the loudest part of the video")


run(main)
```

The full list of manifest fields is in [Plugin manifest](plugin-manifest.md). Of them, the runner uses today: `id`, `name`, `version`, `run.command`, `run.timeout_minutes`, `permissions` and `settings`.

`{python}` in `run.command` is replaced by: the `plugins.python` setting in `settings.yaml` if set, else the Python the engine runs on (in a source checkout), else `python`, `py` or `python3` from `PATH`. **The installed app does not ship a Python for plugins**, so a Python plugin needs the user to have Python installed; the runner says so in words when none is found. A plugin's own Python packages, installed into an environment of its own, are **planned**: for now a plugin can use the standard library, the SDK, and programs it calls (FFmpeg through `job.tools`, or its own executable as `run.command`).

A plugin does not have to be Python: `run.command` can start a program shipped in the plugin's folder, named by a path with a slash (`["bin/detect.exe"]`, `["./detect"]`); Clips Kitty starts it by its full path, never from `PATH`. It then reads `job.json` and writes `result.json` itself, following the contract below.

## The contract (plugin contract 1)

`clipskitty_sdk/contract.py` is the reference; this is a summary.

**`job.json`**, written by Clips Kitty:

```json
{"plugin_api": 1,
 "plugin": {"id": "example-dev/loud-moments", "version": "1.0.0"},
 "video": {"path": "...", "id": "...", "title": "...", "duration": 812.5, "games": []},
 "transcript": {"path": ".../transcript.json", "language": "en"},
 "settings": {"threshold": 0.8},
 "limits": {"max_clips": null, "min_duration": 10, "max_duration": 60},
 "focus": null,
 "models": {},
 "tools": {"ffmpeg": "...", "ffprobe": "..."},
 "output_dir": "..."}
```

**`transcript.json`**: `{"language": "en", "segments": [{"start": 0.0, "end": 2.4, "text": "...", "words": [{"start", "end", "word"}] or null}]}`.

**`result.json`**, written by your plugin:

```json
{"plugin_api": 1,
 "ranges": [{"start": 12.0, "end": 41.5, "score": 87, "label": "goal", "title": "...", "reason": "..."}],
 "notes": "optional, shown in the job log"}
```

At most 200 ranges; `0 <= start < end`; `score` 0-100 or left out; `label` up to 64 characters, `title` 200, `reason` 500, `notes` 4000.

**Progress**, one JSON object per line on standard output: `{"type": "progress", "fraction": 0.4, "message": "..."}`, `{"type": "log", "message": "..."}`, `{"type": "error", "message": "..."}`. The last error line is the message the user sees if the run fails.

## What your plugin receives, and what that does and doesn't protect

| Permission | What goes into the job | Enforced? |
|---|---|---|
| `video.read` | `video` (the downloaded file's path and details) | Yes: without it the job has no `video` |
| `transcript.read` | `transcript` and `transcript.json` | Yes: without it neither is written |
| `ffmpeg` | `tools.ffmpeg`, `tools.ffprobe` (the app's own FFmpeg) | Yes: without them the paths are left out |
| `ollama` | `tools.ollama`: the local Ollama address and, when the user's AI runs locally, its model name | Yes for what is handed over: a cloud provider's name and key never are |

"Enforced" means only that: the job folder does not contain what was not granted. **Your plugin runs as the user, with the user's rights**, like any program they install. Nothing stops it from opening other files, starting programs or using the network. The [Permissions](permissions.md) page lists which declarations are checked by the app and which are only shown to the user, and [Security](security.md) explains what that means for people installing plugins.

**The environment.** Your process inherits the user's environment minus Clips Kitty's own settings (`CLIPS_*`, `CLIPSKITTY_*`) and any variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `COOKIE` or `AUTH`. It gets `CLIPSKITTY_JOB` (the job folder), `PYTHONPATH` (the SDK), UTF-8 settings for Python, and each of your `secret` settings as `CLIPSKITTY_SECRET_<NAME>`. This keeps Clips Kitty from handing credentials over by accident; it is not a wall, since a process running as the user can read the user's files.

**Secrets.** A setting of `type: secret` is stored in Clips Kitty's secrets store (`core/secrets.py`: encrypted with Windows DPAPI under the user's account; on other systems a file only the user can read) and reaches only your process, only in its environment, never in a file. Secrets are not isolated between plugins: any program running as the user, including another plugin, can read the same store.

## How your answer becomes clips

- Ranges are clamped to the video's length, and any left shorter than one second are dropped.
- Scored ranges are kept best first; ranges without a score follow, in your order. The list is cut to the user's clip limit.
- A clip's score is yours, or, without one, comes from its place: 90 for the first, 5 less for each after, never under 50.
- `title` (or `label`) becomes the clip's working headline; Clips Kitty still writes the final title, description and hashtags with the user's AI model.
- Each clip records where it came from: `source` is `plugin:<id>@<version>`, and its scores keep `plugin`, `plugin_version`, `plugin_label` and `plugin_why` (your `reason`).
- The job's minimum score (`min_score`) is **not** applied to plugin results: your plugin returns the moments it stands behind.

## Choosing a pipeline for a job

The job option is `pipeline`, on `POST /jobs`, each item of `POST /jobs/batch`, `POST /videos/local`, `PATCH /jobs/{id}` (and `{"clear": ["pipeline"]}` to drop it) and a watched channel's options:

```json
{"url": "https://www.youtube.com/watch?v=...", "pipeline": {"id": "example-dev/loud-moments", "settings": {"threshold": 0.9}}}
```

`"pipeline": "example-dev/loud-moments"` is the same as `{"id": ...}`. `version` picks an installed version other than the active one. A pipeline that is not installed or is turned off, a setting the manifest does not declare, a `secret` setting, or a value that does not fit the setting's type is refused with 400 when the job is added.

How it combines with the job's other options:

| Option | With a pipeline |
|---|---|
| Sports, Gaming scoring, Longform | Refused (400): each picks the moments its own way. A watched channel that has both keeps the other mode and drops the pipeline. |
| Gaming layout (`gaming`), Vertical Live, Podcast | Allowed: they change how clips are framed, not which moments are picked. With a pipeline, Gaming's own stream scoring is skipped. |
| `max_clips` | Becomes `limits.max_clips`, and the list is cut to it |
| `focus` | Handed to the plugin as `focus` |
| `min_score` | Not applied (above) |
| Captions, caption style, hashtags, watermark, `long_clips`, publishing | Applied after the plugin, as for any job |

Clips Kitty still runs its own audio and visual signal pass before the plugin starts; skipping it for plugin jobs is a possible later speed-up.

## Testing

- `python -m clipskitty_sdk validate .` checks your manifest the way the app and the registry do ([Plugin manifest](plugin-manifest.md)).
- `python -m clipskitty_sdk run . --video sample.mp4` runs your plugin the way the app does ([SDK](sdk.md)); it refuses a plugin whose manifest the app would refuse.
- Use the SDK's `check_result` in your own tests.
- Install it into the app from your folder (plan, then install) to try it on real jobs; see [Plugin development](plugin-development.md).

## The first-party adapter

[`examples/pipelines/transcript-highlights/`](../../examples/pipelines/transcript-highlights/) wraps Clips Kitty's own transcript scorer (`analysis/highlights.find_highlights`) in this contract. It shows that a pipeline already in the app fits the contract without changes, and `tests/test_plugin_runner.py` checks the plugin returns the same moments as calling the scorer directly. It is **not** a model to copy: it imports Clips Kitty's internals, so it only runs next to a source checkout. The [Example pipeline](example-pipeline.md) is the one that imports nothing but the SDK.
