# SDK

The Clips Kitty plugin SDK is a small Python package, `clipskitty_sdk`, in [`sdk/python/clipskitty_sdk/`](../../sdk/python/clipskitty_sdk/). A plugin uses it to read its job, report progress and hand back moments, to rate and describe the moments it is handed, or to suggest edits for the clips Clips Kitty is about to make ([Steps](steps.md)). It imports only the Python standard library (reading a YAML manifest also needs PyYAML), so depending on it does not mean depending on Clips Kitty.

| | |
|---|---|
| SDK version | `clipskitty_sdk.__version__` = `1.3.0` (`python -m clipskitty_sdk --version`; [changelog](../../sdk/python/CHANGELOG.md)) |
| Plugin contract | `clipskitty_sdk.PLUGIN_API_VERSION` = `1` (see [Versioning](versioning.md)) |
| Python | 3.10 or newer. Clips Kitty runs plugins on its own Python 3.11 (`clipskitty_sdk.host.APP_PYTHON`), so test on 3.11. |
| Licence | MIT ([`sdk/python/LICENSE`](../../sdk/python/LICENSE)). Clips Kitty itself is AGPL-3.0-or-later; the SDK is MIT so that a plugin, app or tool built on it can use any licence its author chooses, open or closed. Using the SDK does not put your code under the AGPL. |
| Package | `clipskitty-sdk` ([`sdk/python/pyproject.toml`](../../sdk/python/pyproject.toml)); not published to PyPI. See "Getting the SDK" below. |

## Getting the SDK

You do not install it in the app. When Clips Kitty starts your plugin it puts its own copy of the SDK on the plugin's `PYTHONPATH`, so `import clipskitty_sdk` works without a `requirements.txt` entry.

To develop against it, install it from the repository with pip. The `yaml` extra adds PyYAML, for reading manifests, and `test` adds pytest, for your plugin's tests. PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source ([Versioning](versioning.md#which-release-runs-plugins)).

Or point `PYTHONPATH` at a Clips Kitty checkout. PowerShell:

```powershell
git clone https://github.com/ColinGPT9/clips-studio
$env:PYTHONPATH = "C:\path\to\clips-studio\sdk\python"
```

bash:

```bash
git clone https://github.com/ColinGPT9/clips-studio
export PYTHONPATH=/path/to/clips-studio/sdk/python
```

You never need the rest of the checkout. A plugin that imports anything from Clips Kitty other than `clipskitty_sdk` will not run in an installed copy of the app.

The commands below are written `python -m clipskitty_sdk`, as in bash; in PowerShell on Windows, run them as `py -m clipskitty_sdk`. pip also installs a `clipskitty-sdk` command that does the same, but pip's command folder is often not on `PATH` on Windows.

| Command | What it does |
|---|---|
| `new FOLDER --template NAME` | Starts a plugin from a template ([below](#starting-a-plugin-new)) |
| `validate FOLDER` | Checks the manifest as Clips Kitty does, and the code for what would fail on Clips Kitty's Python |
| `run FOLDER` | Runs the plugin the way Clips Kitty does ([below](#running-your-plugin-the-way-the-app-does)) |
| `sample OUT.mp4` | Writes the 40-second sample video and its transcript ([below](#the-sample-video-and-frame)) |
| `frame VIDEO --at SECONDS` | Writes one frame as a PNG, with a region drawn on it |
| `install FOLDER` | Installs the plugin into the Clips Kitty running on this PC ([below](#installing-it-into-clips-kitty)) |
| `listing FOLDER --section SECTION` | Writes the file that lists the plugin in the Marketplace's catalog ([below](#listing-it-listing)) |
| `schema` | Prints the manifest's JSON Schema, for editors |
| `--version` | Prints the SDK's version and the plugin contract it follows: `clipskitty-sdk 1.3.0 (plugin contract 1)` |

[Your first game pipeline](first-game-pipeline.md) uses most of them, in order.

## Starting a plugin: `new`

```bash
python -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name
python -m clipskitty_sdk new --list
```

`new` writes a new plugin folder from a template: `clipskitty.yaml`, `src/main.py`, `README.md`, `tests/test_main.py` (using `clipskitty_sdk.testing`), `CHANGELOG.md`, `.gitignore`, `LICENSE`, `TEMPLATE-LICENSE.txt` and a GitHub workflow, `.github/workflows/clipskitty-check.yml`, that checks the plugin and runs its tests on Linux and Windows with Python 3.11. Every template is set up for Quarkbloom Arena, a made-up game, and runs as it is with `run --sample`.

| Template | What it does |
|---|---|
| `blank` | finds nothing yet: a start for your own checks |
| `transcript` | finds moments where your words are said |
| `game-events` | finds moments when a coloured banner shows and the sound gets louder |
| `rater` | rates moments others found, by the words said in them |
| `understander` | notes what happens in moments others found, from the screen and the words |
| `editor` | suggests cuts, mutes and a hook title for clips others found: it mutes the words the creator lists, cuts the wait after "respawn timer" and adds a hook title where "quark burst" is said ([Steps](steps.md#suggest-edits-the-edit-step)) |

| Option | |
|---|---|
| `--publisher NAME` | Your GitHub name in lower case; it starts the plugin's id. Default `your-github-name`. On a terminal, `new` asks for it when it isn't given. `clipskitty` is reserved. |
| `--name "Display name"` | The name people see. Default: from the folder's name; on a terminal, `new` asks. |
| `--game SLUG` | The game, for the `game-events` template's `games`. Default `quarkbloom-arena`. |
| `--author NAME` | Who holds the copyright in `LICENSE`. Default: the publisher. |
| `--license SPDX` | The plugin's licence. Default `MIT`, with its text in `LICENSE`; for another, `new` leaves `LICENSE` out and says to add one. |

It writes only into a new or empty folder, and it checks the `clipskitty.yaml` it makes before writing anything. `TEMPLATE-LICENSE.txt` holds the SDK's MIT notice, for the code that came from the template; your `LICENSE` covers your own work.

## Reading the job and answering

```python
from clipskitty_sdk import run


def main(job):
    job.progress(0.0, "Reading the video")
    for start, end, score in find_moments(job.video.path, job.tools.ffmpeg):
        job.add_range(start, end, score=score, label="loud", reason="the volume jumps")
    job.progress(1.0, "Done")


run(main)
```

`run(main)` reads the job, calls `main(job)`, writes `result.json`, and turns any exception into an error the user sees (the traceback goes to the job log). If you would rather drive it yourself:

```python
from clipskitty_sdk import read_job

job = read_job()            # the folder from argv[1], else the CLIPSKITTY_JOB variable
...
job.finish()                # writes result.json; then exit with status 0
```

### `Job`

| Attribute or method | What it is |
|---|---|
| `job.plugin_id`, `job.plugin_version` | Your plugin, as installed |
| `job.video` | `path`, `id`, `title`, `duration` (seconds or `None`), `games`: what Clips Kitty already knows about the games the video shows, as a list in one of two shapes. A source that names the game gives `{"name": "…", "start": 0, "end": 812.5}` for each game and the seconds it is played (Twitch and Kick name it); a source that only says the video is about gaming gives `{"name": "", "start": 0, "end": 812.5, "hint": "the video's tags"}` (YouTube). Often empty. `None` unless your manifest asks for `video.read`. |
| `job.transcript` | `path`, `language`, and `segments()` → `[{"start", "end", "text", "words"}]` in seconds. `None` unless your manifest asks for `transcript.read`. |
| `job.settings` | Your manifest's settings: its defaults, then what the user chose for this job. Never contains `secret` settings. |
| `job.secret(name)` | A `secret` setting (an API key, a licence key), read from the environment. Clips Kitty never writes secrets into the job folder. |
| `job.limits` | `max_clips` (`None` means no limit), `min_duration`, `max_duration` in seconds, from the user's settings. Advisory: `add_range` logs a warning when a range is outside them, and Clips Kitty cuts the list to `max_clips`. `min_score` is the creator's minimum score in a run that rates or understands moments, otherwise `None`. `crops` is the layouts the clips can use in a run that suggests edits (`track`, `center` and `letterbox`, or none), otherwise `()`. |
| `job.focus` | What the user asked the clips to be about, or `None` |
| `job.tools` | `ffmpeg`, `ffprobe` (paths, with the `ffmpeg` permission) and `ollama` (`{"host", "model"}`, with the `ollama` permission; `model` is empty when the user's AI runs at a cloud provider) |
| `job.models` | Models your manifest references, by name: each with `path`, `files` (each listed file's full path), `revision`, `source` and `id` ([Model references](model-references.md)). A run doesn't start until every one is on the PC, so each is there when your code runs; `path` is `None` only for an Ollama model when Ollama wasn't answering. Empty when you run with `python -m clipskitty_sdk run`. |
| `job.output_dir` | A folder of your own inside the job folder |
| `job.progress(fraction, message)` | How far along you are, 0 to 1. In a find run it shows on the job's "Finding the best moments" step. In a run that rates or understands moments the job shows "Rating moments with {name}" or "Understanding moments with {name}" (your plugin's name), and in a run that suggests edits "Suggesting edits with {name}". There the bar may not move: it never goes back, and Clips Kitty's own scoring may already have passed that part of it. |
| `job.log(message)` | A line for the job's log |
| `job.add_range(start, end, *, score=None, label="", title="", reason="")` | One moment of the source video, in seconds. `score` 0-100 is optional. Raises `ContractError` for a range Clips Kitty would refuse (end before start, a score of 140). Returns the range as a `Moment` (ids `r1`, `r2`… in the order added), for `rate` and `understand`. |
| `job.ranges` | What you have added so far |
| `job.steps` | What this run is asked for, from job.json's `steps`: `("find",)` when job.json has none. A step name this SDK doesn't know is kept here and never wanted. |
| `job.wants(step)` | `True` only for `find`, `understand`, `rate` or `edit` when this run is asked for it, so one `main()` can serve every way the plugin is used. `job.wants("export")` answers `False`: posting isn't a plugin step. |
| `job.moments` | The moments handed over in a run that rates or understands, or the clips that will be made in a run that suggests edits, as `Moment`s in Clips Kitty's order. Empty in a find run. Clips Kitty never starts such a run with none. |
| `job.text(m)` | What is said during moment `m`: the transcript's segments that overlap it, joined with spaces. The transcript is read once. Without one it raises `ContractError("text", ["this job has no transcript: add transcript to inputs and transcript.read to permissions"])`. |
| `job.understand(m, text)` | Adds one note of this run saying what happens in `m`, for its title. Control characters are removed, whitespace is collapsed and the note is cut to 160 characters; an empty note is ignored. At most 5 for one moment: a sixth raises `ContractError("understand", ["at most 5 notes for one moment"])`. In a run not asked to understand it logs `understand ignored: this job didn't ask for notes` and keeps nothing. |
| `job.rate(m, score, reason="")` | Gives `m` a score from 0 to 100, with `reason` cut to 500 characters. Rating again replaces the earlier rating, and `m.score` follows. A handed moment needs a run asked to rate; otherwise it logs `rate ignored: this job didn't ask for ratings` and keeps nothing. On a range of your own (from `add_range`) it sets that range's `score` and `reason` exactly as `add_range(score=, reason=)` would, in any run. |
| `job.suggest_edit(m)` | The edit this run suggests for clip `m`: an `EditSuggestion`, the same one on every call for `m`, whose methods chain (`cut`, `trim`, `keep_only`, `mute`, `volume`, `fade`, `speed`, `title_overlay`, `crop`, `reason`; `cuts`, `mutes` and `length` read it back). Times are seconds of the video. A value outside the render's limits raises `ContractError` at the call. In a run not asked to edit, or on a range of your own, it logs that it is ignored and keeps nothing. New in 1.3.0; [Steps](steps.md#writing-an-editor-with-the-sdk) has every call. |
| `job.finish(notes="")` | Writes `result.json`. `notes` appears in the job log. |
| `job.fail(message)` | Stops with exit code 1 and a message the user sees on the failed job |

Returning clip files instead of ranges (`job.add_clip`) is **planned**, not part of plugin contract 1.

### `Moment`

A moment of the video, exported from `clipskitty_sdk`: one handed over in `job.moments`, or one of your own ranges from `job.add_range()`. It is built from job.json with only the keys this SDK knows, so a later 1.x can add moment keys without breaking a plugin that vendors 1.1.0.

| Field | A handed moment (`job.moments`) | Your own range (`add_range`) |
|---|---|---|
| `id` | `m1`, `m2`… in Clips Kitty's order | `r1`, `r2`… in the order added |
| `start`, `end` | seconds | as given |
| `score` | its score now, after any plugin that rated it before yours; never `None` | the score given to `add_range` or `rate`, else `None` |
| `found_score` | the score it was found with | the score at `add_range` time (may be `None`) |
| `found_by` | `clipskitty` (Sports and Gaming scoring included) or the id of the pipeline that found it | your plugin's id |
| `label`, `title`, `reason` | `title` and `reason` are `""` without `transcript.read` | as given |
| `context` | what earlier plugins said happens in it (with `transcript.read`), as a tuple; never changed | `()` |
| `signals` | Clips Kitty's own numeric scores for it: `text`, `audio`, `visual`, `engagement`, `game`, and `reaction` only when it was measured | `{}` |
| `notes` | this run's own notes, from `job.understand` (read-only) | the same |
| `suggested` | in a run that suggests edits, what the edit plugins before yours suggested for the clip: a tuple of `Suggested(by, name, edit, reason)`, read-only; without `transcript.read` their reasons and hook titles are left out | `()` |
| `duration` | `end - start` | the same |

A rater that adds to `m.score` must handle `None` on its own ranges; handed moments always have a score.

`rate` on a `Moment` this `Job` didn't hand out (from `job.moments` or `add_range`) raises `ContractError("rate", ["m9 is not a moment of this job"])`, printed as `rate: m9 is not a moment of this job`; `understand` raises the same with `understand`. A score outside 0-100 raises `ContractError("rate", ["score must be a number from 0 to 100 (got 140)"])`, naming the score it refused.

`job.finish()` writes your ranges (each with its own notes, in a run asked to understand), `moments` (one answer for each handed moment you rated, noted or suggested an edit for: `id`, plus `score` and `reason` when rated, plus `context` when noted, plus `edit` in a run asked to edit), and `notes`. A plain finder's `result.json` is exactly what it always was. [Steps](steps.md) has examples of a rater, an understander, a plugin that does all three, and an editor.

### Checking files yourself

`check_job(data)` and `check_result(data, steps=None)` return the list of problems with a `job.json` or `result.json` mapping (empty when valid). They are the same functions the app uses, so a result that passes them is one the app accepts. Check an answer with `check_result(data, steps=job_json.get("steps"))`, as `Job.finish()` and the app do: `steps=None` means "as a find run is checked", exactly as before 1.1.0, and a run asked to rate, understand or suggest edits also has its `moments` answers checked. `clipskitty_sdk.contract` documents both files and the progress lines in its docstring.

## Running your plugin the way the app does

```bash
python -m clipskitty_sdk run my-plugin --sample
python -m clipskitty_sdk run my-plugin --video match.mp4 --transcript transcript.json
python -m clipskitty_sdk run my-plugin --transcript transcript.json --moments moments.json --min-score 70
```

This builds the same job folder the app builds (one shared function, `clipskitty_sdk.host.build_job`), including leaving out what your permissions do not cover, starts your manifest's `run.command`, shows progress, and checks `result.json` with the app's own checks. Exit code 0 means the app would accept the answer, 1 that the run failed or the answer would be refused, 2 that the command could not start (no manifest, an unknown setting, a step the plugin doesn't offer).

It asks for the run the app would make, using the same helpers (`manifest.offers`, `manifest.find_steps`, `manifest.step_problem`). A plugin that finds moments gets a find run: `run` prints the moments the app would take, best first, fitted to the video's length and the `--max-clips` limit, each followed by its notes (`       note: ...`) when the plugin describes its own ranges. Any other plugin gets a run that understands and rates, as far as it does each: `run` prints one line for each moment it was handed, such as `m1   812.0s-841.5s  score 72 -> 85  the caster called a big play`, that moment's notes under it, then any `ignored:` lines (from `host.read_answers`, the function the app uses). A plugin that only suggests edits gets an edit run: `run` prints one line for each clip, such as `m1   812.0s-841.5s  29.5 s -> 24.5 s  cut 815.6-820.6, hook title "Triple bloom!" (3 s)`, its reason under it, then any `changed:` and `ignored:` lines (from `host.read_edits`). A plugin that also finds, rates or understands gets the run it would get without that, and a note that it can run again with `--steps edit`.

| Option | |
|---|---|
| `--video FILE` | Needed for a plugin with `video.read`; any other plugin, a finder included, can leave it out. Its length is read with `ffprobe` when one is on `PATH` (or given with `--ffprobe`). |
| `--sample` | Instead of `--video`, `--transcript` and `--duration`: a 40-second test video, made in the job folder with FFmpeg (a red banner at the top and a loud sound from 22 to 27 s, scene cuts at 10, 20 and 30 s), and a transcript that says "quark burst" from 21 to 26 s. A plugin without `video.read` or `ffmpeg` needs no FFmpeg for it: it gets the transcript and a 40-second length. |
| `--duration SECONDS` | The video's length, when there is no `--video` or FFprobe can't read it. Without a video, a find run's moments are fitted to it, else to the end of `--transcript`. |
| `--steps find\|understand,rate\|edit` | What to ask for: `find`, or `understand`, `rate` or both, as separate words or with commas, or `edit`, which is a run of its own (default: the run the app would make). An unknown step, a step the plugin doesn't offer, `find` with `rate`, or `edit` with another step is refused, with the app's own message where there is one: `error: --steps: the pipeline Quarkbloom Notes can't rate moments others found: its manifest needs moments in inputs and ratings in outputs`. |
| `--moments FILE` | The moments to hand over: a JSON list of `{start, end, score?, label?, title?, reason?, context?}`, or a finder's `result.json` (its `ranges` are used), so you can chain a finder into a rater. Ids `m1`… are filled in, a missing score becomes 60, and `found_by` is `clipskitty`. In an edit run they are the clips, and each may carry `suggested` (`[{by, name, edit, reason?}]`), to try a plugin after another edit plugin. Without it, a run that rates, understands or suggests edits gets 5 sample moments, each scored 60, starting at 1/6, 2/6 … 5/6 of the video and each lasting the shorter of 20 seconds and a sixth of the video. The length comes from `--video`, else `--duration`, else the end of `--transcript`; with none of them, `run` stops with `error: no video length for sample moments: pass --duration, or --moments`. |
| `--min-score N` | The creator's minimum score handed to a run that rates or understands (`limits.min_score`); default 55, the app's default |
| `--layout KIND` | The kind of clip an edit run is for, which fills `limits.crops` as the app would: `standard` (the default) gives `track`, `center` and `letterbox`; `podcast`, `sports`, `gaming`, `vertical-live` and `whole-frame` give none ([Steps](steps.md#layouts)) |
| `--transcript FILE` | `{"language", "segments"}` or a bare list of segments. Without it a plugin that asks for `transcript.read` gets an empty transcript. |
| `--set NAME=VALUE` | A setting from your manifest, read as its type: text for a `string`, JSON for `integer`, `number` and `boolean` (`70`, `true`), and for a `choice` the option as written. A name the manifest doesn't declare is refused with the names it does. Repeatable. |
| `--secret NAME=VALUE` | A `secret` setting, passed in the environment as the app does |
| `--model NAME=PATH` | Where a model your manifest lists is on this PC: `job.models[NAME]`, with its `files` (the ones the manifest lists for it) inside `PATH`. A name the manifest doesn't list is refused with the names it does. Repeatable. |
| `--game NAME`, `--game-hint TAGS` | What `job.video.games` holds: `--game` a game shown for the whole video, as Twitch and Kick name it; `--game-hint` the video's tags, as YouTube hands them over when it only says Gaming. Repeatable. |
| `--max-clips`, `--min-duration`, `--max-duration`, `--focus` | The job's limits (defaults: no clip limit, 10 and 60 seconds, as in the app's settings) |
| `--ollama-host`, `--ollama-model` | What a plugin with the `ollama` permission is told |
| `--python`, `--ffmpeg`, `--ffprobe` | Which programs to use |
| `--timeout SECONDS` | Stop the plugin after this long. Default: what Clips Kitty allows, `run.timeout_minutes`, else 60 minutes for a find run and 10 for a run that understands, rates or suggests edits |
| `--job-dir DIR` | Build the job folder here instead of a new temporary folder, to look at it afterwards |

Values holding commas go in quotes: `--set "banner_region=0.30,0.10,0.40,0.10"`. PowerShell passes an unquoted `a,b,c` to a program as separate arguments. `--steps` takes the words as one argument or several, so `--steps understand rate` works in every shell.

The run prints `job folder: …` (a new temporary folder, kept so you can look inside it), the plugin's progress (on a terminal, one line rewritten in place), then the moments with each one's reason (`       why: ...`) and the plugin's notes. It warns when the plugin asks for `ffmpeg` and FFmpeg isn't on `PATH`, and when a moment's label isn't in the manifest's `events` (when it declares any). A run it refuses (no manifest, an unknown setting, a step the plugin doesn't offer) makes no job folder. A plugin started without a job folder (`python src/main.py`) says how to try it, and exits with 2.

`python -m clipskitty_sdk validate .` runs the manifest checks the app and the registry run ([Plugin manifest](plugin-manifest.md)), with the line of `clipskitty.yaml` each problem is on; `run` runs them first and refuses a plugin that fails them. Both also warn about code that would fail on Clips Kitty's own Python (`clipskitty_sdk.lint`): syntax newer than Python 3.11, an import of anything but the standard library and `clipskitty_sdk` (Clips Kitty's own code, a module its Python leaves out or Windows lacks, a module at the plugin's root that should be in `src/`), and text files opened without `encoding=`. Files under `tests/` are skipped: they run only on your PC. `python -m clipskitty_sdk schema` prints the manifest's JSON Schema for editors.

`clipskitty_sdk.manifest` offers the same in code: `load(folder)`, `validate(data)` (a report with `errors` and `warnings`), `validate_folder(folder)` (also checks the files the manifest names exist and that the folder has no symbolic links), `setting_value_problem(spec, value)` and `version_satisfies("2.0.0", ">=2.0, <3")`.

## The sample video and `frame`

```bash
python -m clipskitty_sdk sample sample.mp4
python -m clipskitty_sdk frame sample.mp4 --at 24 --region "0.30,0.10,0.40,0.10" --out frame.png
```

`sample OUT.mp4` writes the 40-second sample video that `run --sample` uses, and its transcript beside it as `OUT.transcript.json`. It is made only with FFmpeg's own generators, at 640x360: a red banner at the top (the region `0.30,0.10,0.40,0.10`) and a white square in the top right corner from 22 to 27 s, a loud sound from 22 to 27 s, and scene cuts at 10, 20 and 30 s, between dark and light backgrounds. The transcript says "round one, here we go" from 8 to 12 s, "what a quark burst" from 21 to 26 s and "just waiting for the respawn timer" from 34.5 to 39 s. It needs FFmpeg on `PATH`, or `--ffmpeg`.

`frame VIDEO --at SECONDS` writes the frame at that second as a PNG (`--out`, default `frame-{SECONDS}s.png` in the current folder). With `--region` it draws a box just outside that part of the screen and prints it in pixels, quoted so it pastes into PowerShell as it is:

```text
wrote frame.png: the frame at 24 s of this 640x360 video
region "0.30,0.10,0.40,0.10" is x=192 y=36 w=256 h=36 on this 640x360 video
```

A region is four numbers from 0 to 1, with commas, spaces or both between them: its left edge, its top edge, its width and its height, as parts of the frame. The same region fits a video of any size. `--region` takes the numbers as one argument or several, so PowerShell's split of an unquoted value still works.

Neither command writes inside a plugin's folder (a folder with `clipskitty.yaml` at or above it), because Clips Kitty copies everything there when it installs the plugin: `error: that is inside a plugin's folder, and Clips Kitty copies everything there on install. Write the sample somewhere else, for example: python -m clipskitty_sdk sample ../sample.mp4`.

## Helpers: `media`, `signals`, `text`, `local_model`

Modules for the work most game pipelines share, new in SDK 1.2.0. Like the rest of the SDK they use only the standard library, plus the FFmpeg Clips Kitty hands over. Import them at the top of `src/main.py` (`from clipskitty_sdk import media, signals, text`), as the templates do. From SDK 1.2.0 on, Clips Kitty stops a plugin that imports a module its SDK doesn't have, as soon as it starts, with "This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's developer which version it needs." The [Signals cookbook](signals-cookbook.md) has a recipe for each, with what it costs on the sample.

### `media`: the video, with Clips Kitty's FFmpeg

Each function takes the job. It needs `ffmpeg` in the manifest's permissions, and `video.read` for the video.

| Name | What it is |
|---|---|
| `Region(x, y, w, h)` | A part of the screen as fractions of the frame, 0 to 1: left, top, width, height. `Region.parse("0.30,0.10,0.40,0.10")` reads one from text (commas, spaces or both), and `region.pixels(width, height)` gives `(x, y, w, h)` in whole pixels. A region outside the frame raises `ValueError` with a sentence. |
| `probe(job)` | `VideoInfo(width, height, fps, duration)`, from FFprobe. A video the file says to show turned (as phones record) is measured turned, the way its frames are read. |
| `frames(job, fps=4, region=None, size=(32, 8))` | The video's frames, `fps` a second from the start, one `Frame` at a time: `t` (seconds), `width`, `height`, `rgb` (3 bytes a pixel, row by row) and `pixel(x, y)`. `region` keeps only that part of each frame, and `size` shrinks what is kept, which makes each frame quick to check; `size=None` keeps the full size. FFmpeg reads the video once, as the frames are used; stopping early stops it. |
| `jpeg(job, t, max_side=896)` | The frame at `t` seconds as JPEG bytes, shrunk so neither side is over `max_side` (never enlarged), for `local_model.ask(images=...)` |
| `loudness(job)` | How loud each second is, in LUFS (FFmpeg's `ebur128`): item n is second n, and a silent second is `-70` (`media.SILENCE`). Empty for a video with no sound. |
| `scene_cuts(job, threshold=0.3)` | The times the picture changes by more than `threshold` (0 to 1, FFmpeg's scene score) |

Errors are `MediaError`, with a plain sentence a creator can read: `This pipeline needs FFmpeg: add ffmpeg to permissions in clipskitty.yaml`, `This pipeline needs the video: add video.read to permissions in clipskitty.yaml`, or what FFmpeg said when it couldn't read the video. `loudness()` and `scene_cuts()` are adapted from the [example pipeline](example-pipeline.md), which is MIT too.

### `signals`: numbers and frames into moments

It needs nothing of its own: it works on what `media` (or your own code) gives it, and on the job's limits.

| Name | What it is |
|---|---|
| `colour_share(frame, colour="e0303a", tolerance=60)` | How much of the frame, 0 to 1, is `colour` (6 hex digits, or `(red, green, blue)`): each of red, green and blue within `tolerance` (0 to 255) of it |
| `brightness(frame)` | How bright the frame is, 0 (black) to 255 (white) |
| `difference(a, b)` | How different two frames of the same size are, 0 (the same) to 255 |
| `spikes(values, louder_by=6.0, window=30)` | The positions in `values` at least `louder_by` above the median of the `window` values around them. For `loudness()`, the positions are seconds and `louder_by` is in dB. |
| `stretches(times, gap=1.0, min_length=0.5)` | Times joined into `(first, last)` stretches where they are no more than `gap` apart, kept when at least `min_length` long |
| `merge(ranges, gap=2.0)` | `(start, end)` ranges joined where they overlap or are no more than `gap` apart |
| `around(job, start, end, lead=6.0, tail=3.0)` | A moment from `lead` seconds before `start` to `tail` after `end`, inside the video, fitted to the job's limits: cut to its longest clip (keeping the start) or made as long as its shortest. Seconds, rounded to 0.01. |

### `text`: words in what is said

| Name | What it is |
|---|---|
| `words_of(setting)` | The words or phrases in a setting such as `"quark burst, triple bloom"`: split on commas, each kept once |
| `find_words(text, words)` | Which of `words` are in `text`, as whole words in any case or spacing: "win" doesn't match "window", and "Quark  Burst" matches "quark burst" |
| `said(job, words)` | Each time one of `words` is said in the transcript: `(start, end, word)`, by the words' own times when the transcript has them (Clips Kitty's usually do), else the segment's |
| `hits(job, moment, words)` | Which of `words` are said during `moment` |
| `normalise(text)` | `text` as these compare it: Unicode's compatibility form, case folded, one space between words |

`said()` and `hits()` need `transcript` in `inputs` and `transcript.read` in the permissions; without them they raise `ContractError`, as `job.text()` does.

### `local_model`: the creator's local model, on this PC

| Name | What it is |
|---|---|
| `model(job)` | The name of the creator's local model, as Clips Kitty hands it over |
| `ask(job, prompt, *, images=(), json=False, timeout=120)` | Asks the model `prompt` (Ollama's `/api/generate`) and returns its answer. `images` are pictures to show it (JPEG or PNG bytes, such as `media.jpeg()` gives, or files). With `json`, it asks for JSON only, and a model that can think first is told not to. `timeout` is in seconds. |
| `can_see(job)` | Whether the model can look at pictures: Ollama lists `vision` in what it can do (`/api/show`) |

It needs `ollama` in the permissions, and the creator needs a local model in Clips Kitty. It talks only to a model on this PC (127.0.0.1, localhost or ::1): it never falls back to another address and never calls a cloud service, and its requests ignore proxy settings on purpose, so the prompt and the frames never leave the PC. The model is always the one the job names. Errors are `LocalModelError`: `This pipeline doesn't ask for the local model: add ollama to permissions in clipskitty.yaml`; `No local model is set in Clips Kitty (its AI may run at a cloud provider), and this helper only uses a model on this PC`; `Clips Kitty's model address {host} isn't on this PC, and this helper only talks to a model on this PC`; and plain sentences when the model doesn't answer or can't. What an older Ollama that doesn't list its capabilities answers to `can_see` hasn't been checked: it reads as a model that can't see.

## Testing your plugin

`clipskitty_sdk.testing` runs your plugin from your own tests, as `run` does. It is for your PC, not for your plugin's code inside Clips Kitty. Your tests need pytest, which the `test` extra installs.

```python
from pathlib import Path

from clipskitty_sdk import testing

PLUGIN = Path(__file__).resolve().parent.parent   # the folder holding clipskitty.yaml


def test_it_finds_the_quark_burst(tmp_path):
    run = testing.run_plugin(PLUGIN, transcript=testing.sample_transcript(),
                             duration=testing.SAMPLE_VIDEO_SECONDS, tmp_path=tmp_path)
    assert run.ok, run.error
    assert [m.label for m in run.moments] == ["words_said"]
```

| Name | What it does |
|---|---|
| `run_plugin(plugin, *, video=None, transcript=None, duration=None, settings=None, steps=None, moments=None, tmp_path=None, timeout=None, games=(), ollama_host=None, ollama_model=None, models=None, secrets=None, layout=None)` | Checks the manifest, builds the job folder with `host.build_job`, starts `run.command` (`{python}` is the Python running your tests), and reads the answer with `host.read_result`, `host.read_answers` or, in a run that suggests edits, `host.read_edits`. `layout` is the kind of clip an edit run is for, as `run`'s `--layout`. Without `steps` it asks for the run the app would make. Returns a `PluginRun`. The job folder is a new folder inside `tmp_path` (else the system's temporary folder), and is kept. `timeout` is in seconds; the default is the app's limit. `secrets` are your secret settings by name, handed over in the environment as Clips Kitty hands them over, for `job.secret()`. |
| `PluginRun` | `ok`; `error` (the plugin's own last error line, else why the run stopped or why Clips Kitty can't use its answer); `moments`, as `Moment`s: in a find run its ranges as Clips Kitty takes them (fitted to the video's length, scored ones best first, cut to the clip limit, numbered `m1`, `m2`…, `score` `None` when it gave none), in a run that understands or rates the moments it was handed, in their order, with the scores this run gave them and this run's `notes`; `answers` (by moment id, from `host.read_answers`); `edits` (in a run that suggests edits, each clip's suggestion by moment id as Clips Kitty keeps it, `{edit, reason}`, from `host.read_edits`); `notes` (result.json's `notes`); `events` (every progress, log and error line); `log` (its log lines, standard error included, and any `ignored:` line); `steps`; `folder` |
| `make_job(folder, plugin, *, video=None, transcript=None, duration=None, settings=None, steps=None, moments=None, games=(), ollama_host=None, ollama_model=None, models=None, layout=None)` | Writes the same job folder at `folder` without running anything, and returns `folder`, so a test can call your own functions on `read_job(folder)`. Secret settings are never in the job folder: set `CLIPSKITTY_SECRET_<NAME>` with pytest's `monkeypatch.setenv` before calling `job.secret()`. |
| `sample_video(folder)` | Makes the 40-second sample video (as `sample` does) in `folder`, with its transcript beside it, and returns its path. It needs FFmpeg on `PATH`: without it a pytest test that calls it is skipped ("needs FFmpeg"), and anywhere else it raises `SampleUnavailable`. It refuses a folder inside a plugin's folder. |
| `sample_transcript()`, `SAMPLE_VIDEO_SECONDS` | What is said in the sample video, and its length (40.0). They need nothing, so a test of a plugin that reads only the transcript runs without FFmpeg. |

`transcript` is a `{language, segments}` mapping, a list of segments or a transcript.json path. `moments` are `Moment`s from an earlier run (so a finder's moments can be handed to your rater) or `{start, end, score?, label?, title?, reason?, context?}` mappings; without them a run that understands, rates or suggests edits gets 5 sample moments, as with `run`. `games` are names for `video.games`. `ollama_model` is the creator's local model, for a plugin with the `ollama` permission, and `ollama_host` its address (default `http://localhost:11434`), as `run`'s `--ollama-model` and `--ollama-host`: without `ollama_model` the job names no local model, as when Clips Kitty's AI runs at a cloud provider, and `local_model` refuses. `models` maps a model your manifest lists to where it is on this PC, as `--model NAME=PATH`. A plugin with `video.read` needs `video`; any other plugin, a finder included, can leave it out. As in the app, only what the manifest's permissions cover reaches the plugin.

A run that can't start raises `ContractError` before any folder is made: a manifest Clips Kitty would refuse (its `errors` are the manifest's errors), a step the plugin doesn't offer, a setting it doesn't declare, a model the manifest doesn't list, or no video for a plugin with `video.read`.

## Installing it into Clips Kitty

PowerShell:

```powershell
py -m clipskitty_sdk install my-plugin
py -m clipskitty_sdk install my-plugin --watch
```

bash:

```bash
python -m clipskitty_sdk install my-plugin
python -m clipskitty_sdk install my-plugin --watch
```

No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source ([From source](../../README.md#from-source)).

`install` puts the plugin into the Clips Kitty running on this PC, as **For developers** in Marketplace › Browse does. It checks the plugin as `validate` does, asks Clips Kitty what installing it would do, prints Clips Kitty's own install screen as text, and asks `Install it? [y/N]`. Exit code 0 means installed, 1 not installed (Clips Kitty refused it, the answer was no, or `--yes` or `--watch` found something new), 2 that it stopped before asking for Clips Kitty's plan (a folder or manifest it refuses, Clips Kitty not running or too old, no session file).

- It refuses a folder holding `.venv`, `venv` or `node_modules`, which Clips Kitty would copy into every install (keep a virtual environment next to the plugin's folder, not inside it), and a folder with a symbolic link, which Clips Kitty refuses. It warns about `.clipskitty` and videos over 50 MB, which Clips Kitty copies too.
- A Clips Kitty from before plugin support, such as 2.0.0, can't install plugins, and `install` says so before it looks for the session secret.
- `--yes` skips the question only when nothing is new: the same plugin is already installed, and the update adds no permission, network host, data sent off the PC, change to where it runs, or step. Otherwise it stops and says what is new.
- `--watch` installs it as above, then reinstalls it after each save (it looks for changes every second), with the same rule as `--yes`: a save that adds something stops it. Ctrl+C stops it too. Whether a job already running the plugin is affected when its files are replaced hasn't been checked.

It needs Clips Kitty's session secret, from `plugins/session.secret` in Clips Kitty's data folder: the folder `--data-dir` names, else the file Clips Kitty names when asked without the secret, else `%LOCALAPPDATA%\Clips Studio\data`. Whether the Microsoft Store build keeps its data folder there hasn't been checked; if `install` can't find the file, pass `--data-dir`. The secret keeps out web pages and scripts that don't know it, not programs running as you: any program running as the user can read its file, plugins included. `install` reads it for each request, sends it only to Clips Kitty on this PC (`--api` takes only 127.0.0.1, localhost or ::1), and never prints it. `install` and `clipskitty_sdk.local_model` ignore proxy settings on purpose, so the secret and a creator's frames stay on this PC.

## Listing it: `listing`

```bash
python -m clipskitty_sdk listing my-plugin --section gaming/generic
python -m clipskitty_sdk listing my-plugin --to my-plugin.yaml
```

`listing` writes the file that lists a finished plugin in Awesome Clips Kitty, the catalog the Marketplace reads, from the plugin's git repository: its `id`, `repository`, the `path` of the plugin inside it, the `section`, any `--alias` words (up to 10), the date, and this version at its full commit, with its tag when `v<version>` points at that commit. Run it from the folder that holds the plugin's folder; the file goes to `<name>.yaml` there, or to `--out`. `--to` adds this version to a listing you already have, and refuses a version already listed. Exit code 0 means written, 2 refused.

Before it writes anything it checks that the plugin passes `validate`, that everything in the folder is committed, that the commit is on a branch you pushed, that the repository is the one `clipskitty.yaml` names, and that your id's publisher owns it. It refuses a plugin that still has a template's placeholders (`your-github-name`, `quarkbloom-arena`, `quark_burst`, "Quarkbloom Arena (a made-up game)" and a few more): a listing is for your real plugin. It runs only git commands that read; it never pushes, opens a pull request or fetches, so "pushed" means as far as your last push or fetch shows. It then says what to do next: add the file to the catalog, rebuild the catalog's index, and open one pull request with both ([Marketplace publishing](marketplace-publishing.md#getting-listed)).

## Calling Clips Kitty's API

```python
from clipskitty_sdk.client import LocalAPI, APIError

api = LocalAPI()                      # http://127.0.0.1:8765
api.health()                          # raises APIError if the app speaks an API version this SDK does not know
api.add_job("https://www.youtube.com/watch?v=...", max_clips=3)
```

`LocalAPI` wraps a few [stable routes](api-reference.md) (`health`, `add_job`, `jobs`, `job`, `queue`, `videos`, `clips`) with `urllib`; `get`, `post`, `patch` and `delete` reach any route. A refused call raises `APIError` with the HTTP status and the API's own message; `status` is 0 when Clips Kitty is not running. A pipeline plugin does not need the API to do its job.

At 127.0.0.1, localhost or ::1 (the default), `LocalAPI` ignores proxy settings, so a proxy set in the environment never sees a request to this PC. At any other address it uses them, as `urllib` does. It never sends the session secret that installing plugins needs.

## Where the SDK is tested

`tests/test_plugin_sdk.py` (job reading, progress lines, result writing and checking, moments, ratings and notes, errors, the environment, the job a manifest's permissions allow, the `run` command on fixture plugins, finding, rating and understanding, the API client against a stand-in server) and the other `tests/test_plugin_sdk_*.py` files (the package, messages and the lint, the sample tools, `testing`, the helpers, `new` and the templates, `install` and `listing`, and suggested edits) run with the standard library, PyYAML and pytest only, as in CI, on Linux and on Windows; the tests that need FFmpeg are skipped where it isn't installed. `tests/test_plugin_runner.py` runs real plugin processes through the app's runner, `tests/test_plugin_templates.py` runs every template through it, and `tests/test_plugin_docs.py` runs [Your first game pipeline](first-game-pipeline.md), the [Signals cookbook](signals-cookbook.md) and the editor in [Steps](steps.md#an-editor) as written.
