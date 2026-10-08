# Plugin manifest

Every plugin has one manifest, `clipskitty.yaml`, at the root of its folder (or of the subfolder its registry listing names). It is data: Clips Kitty reads it and never evaluates anything in it. JSON with the same fields works too.

Status: **built**, manifest version 1 (`sdk/python/clipskitty_sdk/manifest.py`). Check yours with:

```text
python -m clipskitty_sdk validate .
```

It reports every problem at once, each with the path of the field (`settings.min_kills.default: 9 is above the maximum, 6`), and warnings that do not stop an install (an unknown field, a pickle-format model file). The app, the plugin manager and the registry's index build run the same checks. Editors can use the JSON Schema in [`sdk/python/clipskitty_sdk/schema/clipskitty.schema.json`](../../sdk/python/clipskitty_sdk/schema/clipskitty.schema.json), which is generated from the validator; the validator checks more than a schema can (an input needs its permission, a plugin given moments must answer about them, a remote pipeline must say what it sends).

## A complete example

```yaml
manifest_version: 1
id: example-dev/quarkbloom-arena-highlights   # publisher/name: lower case, digits, hyphens
name: Quarkbloom Arena Highlights
version: 1.0.0                                 # SemVer
kind: pipeline
capability: highlight_detection
description: Finds team wipes, multi-kills and quark bursts in Quarkbloom Arena recordings by reading the kill feed.
author: {name: Example Developer, url: https://github.com/example-dev}
repository: https://github.com/example-dev/clips-kitty-quarkbloom-arena
license: MIT
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run:
  command: ["{python}", "src/main.py"]
  timeout_minutes: 60
execution: local
inputs: [video, transcript]
outputs: [ranges]
events: [team_wipe, multi_kill, quark_burst]
games: [quarkbloom-arena]
settings:
  min_kills: {type: integer, default: 3, minimum: 2, maximum: 6, title: Smallest multi-kill}
models:
  - name: killfeed
    source: huggingface
    id: example-dev/quarkbloom-arena-killfeed
    revision: 0123456789abcdef0123456789abcdef01234567
    files: [killfeed.onnx]
    format: onnx
    license: apache-2.0
permissions: [video.read, transcript.read, ffmpeg, gpu]
network: []
sends: []
requirements: {gpu: recommended, vram_gb: 6, ram_gb: 8, disk_gb: 2, os: [windows]}
category: gaming
tags: [quarkbloom-arena, highlights, kill-feed]
links:
  docs: https://github.com/example-dev/clips-kitty-quarkbloom-arena#readme
```

(Quarkbloom Arena is a made-up game, and all names, ids and commits are placeholders.) More examples, valid and invalid, are in [`tests/fixtures/plugins/manifests/`](../../tests/fixtures/plugins/manifests/): each invalid one names the message it produces.

## Fields

Required fields are in bold.

| Field | What it is | Rules |
|---|---|---|
| **`manifest_version`** | The manifest format | `1` |
| **`id`** | `publisher/name` | lower case letters, digits and hyphens; publisher up to 39 characters, name up to 64. The publisher `clipskitty` is reserved for the modes that ship with the app. For a listed plugin the publisher must be the repository's GitHub owner (checked by the catalog's index build; only the Clips Kitty project's own repositories may list examples under another name, such as `clips-kitty-examples`). |
| **`name`** | What users see | up to 60 characters |
| **`version`** | This release | SemVer, `1.2.0` or `1.2.0-beta.1` |
| **`kind`** | What sort of plugin | `pipeline`. `caption-style`, `publisher`, `source`, `integration`, `provider` and `component` are planned and refused with a message saying so. |
| **`capability`** | What the pipeline does | `highlight_detection` |
| **`description`** | One or two sentences | up to 1000 characters; say honestly what it detects |
| **`license`** | The plugin's licence, your choice | an SPDX identifier: `MIT`, `Apache-2.0`, `GPL-3.0-only`, `MIT OR Apache-2.0`. Shown on every Marketplace card. Using the SDK (MIT) puts no licence on your plugin. |
| **`requires.clips_kitty`** | App versions it works with | a range: `>=2.0`, `>=2.0, <3`, `~=2.1` |
| **`requires.plugin_api`** | The plugin contract it was written for | `1` |
| **`run.command`** | What to start | a list: `["{python}", "src/main.py"]`, or a program in the plugin's folder by a path with a slash, `["bin/detect.exe"]` or `["./detect"]`. A bare name such as `bash` or `node` is refused, because it would be looked up on `PATH` rather than be the plugin's own program. `{python}` may only come first. |
| `run.timeout_minutes` | How long a run may take | 1 to 1440; default 60 |
| `run.python_requirements` | A requirements file in the plugin | accepted with a warning: per-plugin Python packages are **planned** ([Pipeline development](pipeline-development.md)) |
| **`execution`** | Where the work happens | `local`, `remote` or `hybrid` |
| **`inputs`** | What it needs handed over | `video`, `transcript`, `moments`. `video` and `transcript` each need their permission; `moments` (the moments found before it runs) needs none. |
| **`outputs`** | What it returns | `ranges` (moments it finds), `context` (what happens in moments), `ratings` (scores for moments others found). Together with `inputs` they say which steps the plugin does ([below](#inputs-and-outputs-which-steps-a-plugin-does)). `clips` (finished files) and `edits` (suggested cuts and framing, for the edit step) are planned, and refused with a message saying so. |
| **`permissions`** | What it asks for | see [Permissions](permissions.md) |
| `network` | Hosts it connects to | host names, optionally with a port; required with the `network` permission, and for `remote` and `hybrid` |
| `sends` | What leaves the computer | `video`, `video_link`, `audio`, `frames`, `transcript`, or `{data, to, when}` when it depends on a setting. Required for `remote` and `hybrid`; a `local` pipeline sends nothing. Shown to the user as a ⚠ warning. |
| `events` | Labels its moments may carry | lower case, digits, `_` and `-` |
| `games` | Games it covers, for search | slugs such as `quarkbloom-arena` |
| `settings` | Options the user can set | below |
| `models` | Models it uses | below; found on the PC, downloaded into one shared folder and handed to the plugin ([Model references](model-references.md)) |
| `requirements` | What the PC needs | `gpu` (`none`, `optional`, `recommended`, `required`), `vram_gb`, `ram_gb`, `disk_gb`, `os` (`windows`, `macos`, `linux`), `software` (names, such as `docker`) |
| `category` | One of ten | `gaming`, `sports`, `creators`, `streaming`, `podcasting`, `captions`, `detection`, `analytics`, `audio`, `utilities` |
| `tags` | Search words | at most 10; lower case, digits and hyphens |
| `author` | `{name, url}` | `url` must be `https` |
| `repository` | Where the source is | `https` |
| `links` | `docs`, `funding` (a list) | `https`; shown as links, never opened without asking |
| `service` | A paid or hosted service the plugin can use | `{name, url, pricing, required}`; `required` (true or false) says whether the plugin works without it |
| `examples` | `[{title, url}]` | links to the developer's own images or clips |
| `based_on` | Projects the plugin builds on | up to 10 of `{name, url, license, how}`; shown in the Marketplace as "Built on" ([below](#based-on-other-projects)) |

Any other field is ignored with a warning, so a typo shows up without breaking an install.

## Inputs and outputs: which steps a plugin does

There is no field for a plugin's role. It follows from `inputs` and `outputs`, and decides where a creator can choose the plugin ([Steps](steps.md)):

| Role | `inputs` | `outputs` | Chosen as |
|---|---|---|---|
| Finder | `[video, transcript]` | `[ranges]` | Pipeline |
| Finder that also understands its own ranges | `[video, transcript]` | `[ranges, context]` | Pipeline |
| Understander | `[moments, transcript]` | `[context]` | Rate & understand |
| Rater | `[moments, transcript]` | `[ratings]` | Rate & understand |
| One plugin, all three | `[video, transcript, moments]` | `[ranges, context, ratings]` | Pipeline, or Rate & understand |

`kind` stays `pipeline` and `capability` stays `highlight_detection` for each of them. A plugin given moments reads what is said in them, so it usually takes `transcript` and asks for `transcript.read` too.

Three rules tie the words together. The validator never gives two of them for one manifest, and a manifest that uses these words gets no new warning:

| Rule | Message |
|---|---|
| `ratings` in outputs without `moments` in inputs | `outputs[i]: ratings score moments found before this plugin runs: add moments to inputs (a pipeline's own ranges carry their score already)` |
| `moments` in inputs with neither `ratings` nor `context` in outputs | `inputs[i]: a plugin given moments answers about them: add ratings or context to outputs` |
| `context` in outputs with no `ranges` in outputs, no `moments` in inputs and no `ratings` in outputs | `outputs[i]: context describes moments: add ranges to outputs, or moments to inputs` |

`clipskitty_sdk.manifest` answers the same questions in code: `steps_of(m)` (what the plugin does), `offers(m)` (what a job may name it for), `find_steps(m)` (what its find run is asked for) and `step_problem(m, step)` (why a job can't name it for a step).

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

A Hugging Face model must be pinned to a full commit hash, because a branch or tag can change under the user; a `url` model needs its SHA-256. Pickle-format files (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`) can run code when loaded and produce a warning; the app will ask before downloading them. Clips Kitty finds each one on the PC, downloads Hugging Face and `url` models into one folder every plugin shares when the user presses Download, and stops a run whose models aren't there yet ([Model references](model-references.md)).

## Based on other projects

```yaml
based_on:
  - {name: Example Clipper, url: "https://github.com/example-org/example-clipper", license: MIT, how: runs}
  - {name: Example Scorer, url: "https://github.com/example-org/example-scorer", license: GPL-3.0-or-later, how: port}
```

`based_on` names the projects your plugin builds on, so the Marketplace can credit them and show their licences beside yours. The listing, the installed plugin and the install screen show them under "Built on": each name as a link, its licence, and how your plugin uses it, with a note that each keeps its own licence. Up to 10 projects.

| Field | Rules |
|---|---|
| `name` | text, up to 100 characters |
| `url` | an `https` link to the project |
| `license` | the other project's licence, as an SPDX identifier like `license` above |
| `how` | `runs` (your plugin starts it as a separate program), `includes-code` (your plugin contains its code) or `port` (your plugin is a rewrite of it) |

All four are required in each entry; any other key is ignored with a warning. Naming a project here does not settle what its licence asks of you: keep its notices, and if you include its code, use a licence compatible with it ([Licences](../../awesome-clips-kitty/CONTRIBUTING.md#licences)). Examples: `valid/based-on.yaml` and `invalid/bad-based-on.yaml` in [`tests/fixtures/plugins/manifests/`](../../tests/fixtures/plugins/manifests/).

## The official modes

Clips Kitty's own modes have manifests too, in [`plugins/builtin/`](../../plugins/builtin/): `clipskitty/shorts`, `clipskitty/gaming` and `clipskitty/sports`, with `run: builtin`. They exist so the Marketplace can list the official modes beside community pipelines and so the schema is tested on real pipelines. Only manifests shipped inside the app may use the `clipskitty` publisher or `run: builtin`.
