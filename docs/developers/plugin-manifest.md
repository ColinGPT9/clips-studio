# Plugin manifest

Every plugin has one manifest, `clipskitty.yaml`, at the root of its folder (or of the subfolder its registry listing names). It is data: Clips Kitty reads it and never evaluates anything in it. JSON with the same fields works too.

Status: **built**, manifest version 1 (`sdk/python/clipskitty_sdk/manifest.py`). Check yours with:

```text
python -m clipskitty_sdk validate .
```

It reports every problem at once, each with the path of the field (`settings.min_kills.default: 9 is above the maximum, 6`), and warnings that do not stop an install (an unknown field, a pickle-format model file). The app, the plugin manager and the registry's index build run the same checks. Editors can use the JSON Schema in [`sdk/python/clipskitty_sdk/schema/clipskitty.schema.json`](../../sdk/python/clipskitty_sdk/schema/clipskitty.schema.json), which is generated from the validator; the validator checks more than a schema can (an input needs its permission, a remote pipeline must say what it sends).

## A complete example

```yaml
manifest_version: 1
id: example-dev/marvel-rivals-highlights   # publisher/name: lower case, digits, hyphens
name: Marvel Rivals Highlights
version: 1.0.0                             # SemVer
kind: pipeline
capability: highlight_detection
description: Finds team wipes, multi-kills and ultimates in Marvel Rivals VODs by reading the kill feed.
author: {name: Example Developer, url: https://github.com/example-dev}
repository: https://github.com/example-dev/clips-kitty-marvel-rivals
license: MIT
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run:
  command: ["{python}", "src/main.py"]
  timeout_minutes: 60
execution: local
inputs: [video, transcript]
outputs: [ranges]
events: [team_wipe, multi_kill, ultimate]
games: [marvel-rivals]
settings:
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
network: []
sends: []
requirements: {gpu: recommended, vram_gb: 6, ram_gb: 8, disk_gb: 2, os: [windows]}
category: gaming
tags: [marvel-rivals, highlights, kill-feed]
links:
  docs: https://github.com/example-dev/clips-kitty-marvel-rivals#readme
```

(All names, ids and commits are placeholders.) More examples, valid and invalid, are in [`tests/fixtures/plugins/manifests/`](../../tests/fixtures/plugins/manifests/): each invalid one names the message it produces.

## Fields

Required fields are in bold.

| Field | What it is | Rules |
|---|---|---|
| **`manifest_version`** | The manifest format | `1` |
| **`id`** | `publisher/name` | lower case letters, digits and hyphens; publisher up to 39 characters, name up to 64. The publisher `clipskitty` is reserved for the modes that ship with the app. For a listed plugin the publisher must be the repository's GitHub owner (checked by the registry, planned). |
| **`name`** | What users see | up to 60 characters |
| **`version`** | This release | SemVer, `1.2.0` or `1.2.0-beta.1` |
| **`kind`** | What sort of plugin | `pipeline`. `caption-style`, `publisher`, `source`, `integration`, `provider` and `component` are planned and refused with a message saying so. |
| **`capability`** | What the pipeline does | `highlight_detection` |
| **`description`** | One or two sentences | up to 1000 characters; say honestly what it detects |
| **`license`** | The plugin's licence | an SPDX identifier: `MIT`, `Apache-2.0`, `GPL-3.0-only`, `MIT OR Apache-2.0` |
| **`requires.clips_kitty`** | App versions it works with | a range: `>=2.0`, `>=2.0, <3`, `~=2.1` |
| **`requires.plugin_api`** | The plugin contract it was written for | `1` |
| **`run.command`** | What to start | a list: `["{python}", "src/main.py"]`, or a program in the plugin's folder by a path with a slash, `["bin/detect.exe"]` or `["./detect"]`. A bare name such as `bash` or `node` is refused, because it would be looked up on `PATH` rather than be the plugin's own program. `{python}` may only come first. |
| `run.timeout_minutes` | How long a run may take | 1 to 1440; default 60 |
| `run.python_requirements` | A requirements file in the plugin | accepted with a warning: per-plugin Python packages are **planned** ([Pipeline development](pipeline-development.md)) |
| **`execution`** | Where the work happens | `local`, `remote` or `hybrid` |
| **`inputs`** | What it needs handed over | `video`, `transcript`; each needs its permission |
| **`outputs`** | What it returns | `ranges`. `clips` (finished files) is planned. |
| **`permissions`** | What it asks for | see [Permissions](permissions.md) |
| `network` | Hosts it connects to | host names, optionally with a port; required with the `network` permission, and for `remote` and `hybrid` |
| `sends` | What leaves the computer | `video`, `video_link`, `audio`, `frames`, `transcript`, or `{data, to, when}` when it depends on a setting. Required for `remote` and `hybrid`; a `local` pipeline sends nothing. Shown to the user as a ⚠ warning. |
| `events` | Labels its moments may carry | lower case, digits, `_` and `-` |
| `games` | Games it covers, for search | slugs such as `marvel-rivals` |
| `settings` | Options the user can set | below |
| `models` | Models it uses | below; resolution and download are **planned** ([Model references](model-references.md)) |
| `requirements` | What the PC needs | `gpu` (`none`, `optional`, `recommended`, `required`), `vram_gb`, `ram_gb`, `disk_gb`, `os` (`windows`, `macos`, `linux`), `software` (names, such as `docker`) |
| `category` | One of ten | `gaming`, `sports`, `creators`, `streaming`, `podcasting`, `captions`, `detection`, `analytics`, `audio`, `utilities` |
| `tags` | Search words | at most 10; lower case, digits and hyphens |
| `author` | `{name, url}` | `url` must be `https` |
| `repository` | Where the source is | `https` |
| `links` | `docs`, `funding` (a list) | `https`; shown as links, never opened without asking |
| `service` | A paid or hosted service the plugin can use | `{name, url, pricing, required}`; `required` (true or false) says whether the plugin works without it |
| `examples` | `[{title, url}]` | links to the developer's own images or clips |

Any other field is ignored with a warning, so a typo shows up without breaking an install.

## Settings

```yaml
settings:
  min_kills: {type: integer, default: 3, minimum: 2, maximum: 6, title: Smallest multi-kill}
  speed:     {type: choice, options: [fast, careful], default: careful}
  api_key:   {type: secret, title: Example Cloud key}
```

| `type` | Extra fields | Value |
|---|---|---|
| `string` | `max_length` | text |
| `integer` | `minimum`, `maximum` | a whole number |
| `number` | `minimum`, `maximum` | a number |
| `boolean` | | true or false |
| `choice` | `options` (required) | one of the options |
| `secret` | | entered by the user and stored by Clips Kitty; never has a default and never travels in a job (see [Pipeline development](pipeline-development.md)) |

Every type takes `title` and `description`; all but `secret` take `default`, which must fit the type. A setting's name is lower case letters, digits and `_`, starting with a letter. A job's settings are checked against these when the job is added: an unknown setting, a `secret`, or a value that does not fit is refused with 400 and a message naming the setting.

## Models

```yaml
models:
  - {name: killfeed, source: huggingface, id: owner/name, revision: <40-character commit>, files: [killfeed.onnx]}
  - {name: weights, source: url, id: https://example.com/weights.onnx, sha256: <64 hex>}
  - {name: chat, source: ollama, id: example-model:latest}
  - {name: speech, source: bundled, id: example-bundled-model}
```

A Hugging Face model must be pinned to a full commit hash, because a branch or tag can change under the user; a `url` model needs its SHA-256. Pickle-format files (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`) can run code when loaded and produce a warning; the app will ask before downloading them. The validator checks these shapes today; finding, sharing and downloading the files is **planned** ([Model references](model-references.md)).

## The official modes

Clips Kitty's own modes have manifests too, in [`plugins/builtin/`](../../plugins/builtin/): `clipskitty/shorts`, `clipskitty/gaming` and `clipskitty/sports`, with `run: builtin`. They exist so the Marketplace can list the official modes beside community pipelines and so the schema is tested on real pipelines. Only manifests shipped inside the app may use the `clipskitty` publisher or `run: builtin`.
