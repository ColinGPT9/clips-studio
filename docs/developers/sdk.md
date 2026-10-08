# SDK

The Clips Kitty plugin SDK is a small Python package, `clipskitty_sdk`, in [`sdk/python/clipskitty_sdk/`](../../sdk/python/clipskitty_sdk/). A plugin uses it to read its job, report progress and hand back moments, or to rate and describe the moments it is handed ([Steps](steps.md)). It imports only the Python standard library (reading a YAML manifest also needs PyYAML), so depending on it does not mean depending on Clips Kitty.

| | |
|---|---|
| SDK version | `clipskitty_sdk.__version__` = `1.2.0` (`python -m clipskitty_sdk --version`; [changelog](../../sdk/python/CHANGELOG.md)) |
| Plugin contract | `clipskitty_sdk.PLUGIN_API_VERSION` = `1` (see [Versioning](versioning.md)) |
| Python | 3.10 or newer |
| Licence | MIT ([`sdk/python/LICENSE`](../../sdk/python/LICENSE)). Clips Kitty itself is AGPL-3.0-or-later; the SDK is MIT so that a plugin, app or tool built on it can use any licence its author chooses, open or closed. Using the SDK does not put your code under the AGPL. |
| Package | `clipskitty-sdk` ([`sdk/python/pyproject.toml`](../../sdk/python/pyproject.toml)); not published to PyPI. See "Getting the SDK" below. |

## Getting the SDK

You do not install it in the app. When Clips Kitty starts your plugin it puts its own copy of the SDK on the plugin's `PYTHONPATH`, so `import clipskitty_sdk` works without a `requirements.txt` entry.

To develop against it, install it from the repository with pip (the `yaml` extra adds PyYAML, for reading manifests):

```text
pip install "clipskitty-sdk[yaml] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

or point `PYTHONPATH` at a Clips Kitty checkout:

```text
git clone https://github.com/ColinGPT9/clips-studio
set PYTHONPATH=C:\path\to\clips-studio\sdk\python        (Windows, cmd)
export PYTHONPATH=/path/to/clips-studio/sdk/python        (macOS, Linux)
```

You never need the rest of the checkout. A plugin that imports anything from Clips Kitty other than `clipskitty_sdk` will not run in an installed copy of the app.

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
| `job.video` | `path`, `id`, `title`, `duration` (seconds or `None`), `games` (names Clips Kitty already knows the video shows). `None` unless your manifest asks for `video.read`. |
| `job.transcript` | `path`, `language`, and `segments()` → `[{"start", "end", "text", "words"}]` in seconds. `None` unless your manifest asks for `transcript.read`. |
| `job.settings` | Your manifest's settings: its defaults, then what the user chose for this job. Never contains `secret` settings. |
| `job.secret(name)` | A `secret` setting (an API key, a licence key), read from the environment. Clips Kitty never writes secrets into the job folder. |
| `job.limits` | `max_clips` (`None` means no limit), `min_duration`, `max_duration` in seconds, from the user's settings. Advisory: `add_range` logs a warning when a range is outside them, and Clips Kitty cuts the list to `max_clips`. `min_score` is the creator's minimum score in a run that rates or understands moments, otherwise `None`. |
| `job.focus` | What the user asked the clips to be about, or `None` |
| `job.tools` | `ffmpeg`, `ffprobe` (paths, with the `ffmpeg` permission) and `ollama` (`{"host", "model"}`, with the `ollama` permission; `model` is empty when the user's AI runs at a cloud provider) |
| `job.models` | Models your manifest references, by name: each with `path`, `files` (each listed file's full path), `revision`, `source` and `id` ([Model references](model-references.md)). A run doesn't start until every one is on the PC, so each is there when your code runs; `path` is `None` only for an Ollama model when Ollama wasn't answering. Empty when you run with `python -m clipskitty_sdk run`. |
| `job.output_dir` | A folder of your own inside the job folder |
| `job.progress(fraction, message)` | How far along you are, 0 to 1. In a find run it shows on the job's "Finding the best moments" step. In a run that rates or understands moments the job shows "Rating moments with {name}" or "Understanding moments with {name}" (your plugin's name). There the bar may not move: it never goes back, and Clips Kitty's own scoring may already have passed that part of it. |
| `job.log(message)` | A line for the job's log |
| `job.add_range(start, end, *, score=None, label="", title="", reason="")` | One moment of the source video, in seconds. `score` 0-100 is optional. Raises `ContractError` for a range Clips Kitty would refuse (end before start, a score of 140). Returns the range as a `Moment` (ids `r1`, `r2`… in the order added), for `rate` and `understand`. |
| `job.ranges` | What you have added so far |
| `job.steps` | What this run is asked for, from job.json's `steps`: `("find",)` when job.json has none. A step name this SDK doesn't know is kept here and never wanted. |
| `job.wants(step)` | `True` only for `find`, `understand` or `rate` when this run is asked for it, so one `main()` can serve every way the plugin is used |
| `job.moments` | The moments handed over in a run that rates or understands, as `Moment`s in Clips Kitty's order. Empty in a find run. Clips Kitty never starts such a run with none. |
| `job.text(m)` | What is said during moment `m`: the transcript's segments that overlap it, joined with spaces. The transcript is read once. Without one it raises `ContractError("text", ["this job has no transcript: add transcript to inputs and transcript.read to permissions"])`. |
| `job.understand(m, text)` | Adds one note of this run saying what happens in `m`, for its title. Control characters are removed, whitespace is collapsed and the note is cut to 160 characters; an empty note is ignored. At most 5 for one moment: a sixth raises `ContractError("understand", ["at most 5 notes for one moment"])`. In a run not asked to understand it logs `understand ignored: this job didn't ask for notes` and keeps nothing. |
| `job.rate(m, score, reason="")` | Gives `m` a score from 0 to 100, with `reason` cut to 500 characters. Rating again replaces the earlier rating, and `m.score` follows. A handed moment needs a run asked to rate; otherwise it logs `rate ignored: this job didn't ask for ratings` and keeps nothing. On a range of your own (from `add_range`) it sets that range's `score` and `reason` exactly as `add_range(score=, reason=)` would, in any run. |
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
| `duration` | `end - start` | the same |

A rater that adds to `m.score` must handle `None` on its own ranges; handed moments always have a score.

`rate` on a `Moment` this `Job` didn't hand out (from `job.moments` or `add_range`) raises `ContractError("rate", ["m9 is not a moment of this job"])`, printed as `rate: m9 is not a moment of this job`; `understand` raises the same with `understand`. A score outside 0-100 raises `ContractError("rate", ["score must be a number from 0 to 100 (got 140)"])`, naming the score it refused.

`job.finish()` writes your ranges (each with its own notes, in a run asked to understand), `moments` (one answer for each handed moment you rated or noted: `id`, plus `score` and `reason` when rated, plus `context` when noted), and `notes`. A plain finder's `result.json` is exactly what it always was. [Steps](steps.md) has examples of a rater, an understander and a plugin that does all three.

### Checking files yourself

`check_job(data)` and `check_result(data, steps=None)` return the list of problems with a `job.json` or `result.json` mapping (empty when valid). They are the same functions the app uses, so a result that passes them is one the app accepts. Check an answer with `check_result(data, steps=job_json.get("steps"))`, as `Job.finish()` and the app do: `steps=None` means "as a find run is checked", exactly as before 1.1.0, and a run asked to rate or understand also has its `moments` answers checked. `clipskitty_sdk.contract` documents both files and the progress lines in its docstring.

## Running your plugin the way the app does

```text
python -m clipskitty_sdk run . --video sample.mp4 --transcript transcript.json --set min_score=70
python -m clipskitty_sdk run . --transcript transcript.json --moments moments.json
```

This builds the same job folder the app builds (one shared function, `clipskitty_sdk.host.build_job`), including leaving out what your permissions do not cover, starts your manifest's `run.command`, shows progress, and checks `result.json` with the app's own checks. Exit code 0 means the app would accept the answer, 1 that the run failed or the answer would be refused, 2 that the command could not start (no manifest, an unknown setting, a step the plugin doesn't offer).

It asks for the run the app would make, using the same helpers (`manifest.offers`, `manifest.find_steps`, `manifest.step_problem`). A plugin that finds moments gets a find run: `run` prints the moments the app would take, best first, fitted to the video's length and the `--max-clips` limit, each followed by its notes (`       note: ...`) when the plugin describes its own ranges. Any other plugin gets a run that understands and rates, as far as it does each: `run` prints one line for each moment it was handed, such as `m1   812.0s-841.5s  score 72 -> 85  the caster called a big play`, that moment's notes under it, then any `ignored:` lines (from `host.read_answers`, the function the app uses).

| Option | |
|---|---|
| `--video FILE` | Needed for a find run and for a plugin with `video.read`; a plugin that only rates or understands, without `video.read`, can leave it out. Its length is read with `ffprobe` when one is on `PATH` (or given with `--ffprobe`). |
| `--duration SECONDS` | The video's length, when there is no `--video` or FFprobe can't read it |
| `--steps find\|understand,rate` | What to ask for: `find`, or `understand`, `rate` or both, as separate words or with commas (default: the run the app would make). An unknown step, a step the plugin doesn't offer, or `find` with `rate` is refused, with the app's own message where there is one: `error: --steps: the pipeline Quarkbloom Notes can't rate moments others found: its manifest needs moments in inputs and ratings in outputs`. |
| `--moments FILE` | The moments to hand over: a JSON list of `{start, end, score?, label?, title?, reason?, context?}`, or a finder's `result.json` (its `ranges` are used), so you can chain a finder into a rater. Ids `m1`… are filled in, a missing score becomes 60, and `found_by` is `clipskitty`. Without it, a run that rates or understands gets 5 sample moments, each scored 60, starting at 1/6, 2/6 … 5/6 of the video and each lasting the shorter of 20 seconds and a sixth of the video. The length comes from `--video`, else `--duration`, else the end of `--transcript`; with none of them, `run` stops with `error: no video length for sample moments: pass --duration, or --moments`. |
| `--min-score N` | The creator's minimum score handed to a run that rates or understands (`limits.min_score`); default 55, the app's default |
| `--transcript FILE` | `{"language", "segments"}` or a bare list of segments. Without it a plugin that asks for `transcript.read` gets an empty transcript. |
| `--set NAME=VALUE` | A setting from your manifest, read as its type: text for a `string`, JSON for `integer`, `number` and `boolean` (`70`, `true`), and for a `choice` the option as written. A name the manifest doesn't declare is refused with the names it does. Repeatable. |
| `--secret NAME=VALUE` | A `secret` setting, passed in the environment as the app does |
| `--max-clips`, `--min-duration`, `--max-duration`, `--focus` | The job's limits (defaults: no clip limit, 10 and 60 seconds, as in the app's settings) |
| `--ollama-host`, `--ollama-model` | What a plugin with the `ollama` permission is told |
| `--python`, `--ffmpeg`, `--ffprobe` | Which programs to use |
| `--timeout SECONDS` | Stop the plugin after this long. Default: what Clips Kitty allows, `run.timeout_minutes`, else 60 minutes for a find run and 10 for a run that understands or rates |
| `--job-dir DIR` | Build the job folder here instead of a new temporary folder, to look at it afterwards |

`python -m clipskitty_sdk validate .` runs the manifest checks the app and the registry run ([Plugin manifest](plugin-manifest.md)); `run` runs them first and refuses a plugin that fails them. `python -m clipskitty_sdk schema` prints the manifest's JSON Schema for editors.

`clipskitty_sdk.manifest` offers the same in code: `load(folder)`, `validate(data)` (a report with `errors` and `warnings`), `validate_folder(folder)` (also checks the files the manifest names exist and that the folder has no symbolic links), `setting_value_problem(spec, value)` and `version_satisfies("2.0.0", ">=2.0, <3")`.

## Calling Clips Kitty's API

```python
from clipskitty_sdk.client import LocalAPI, APIError

api = LocalAPI()                      # http://127.0.0.1:8765
api.health()                          # raises APIError if the app speaks an API version this SDK does not know
api.add_job("https://www.youtube.com/watch?v=...", max_clips=3)
```

`LocalAPI` wraps a few [stable routes](api-reference.md) (`health`, `add_job`, `jobs`, `job`, `queue`, `videos`, `clips`) with `urllib`; `get`, `post`, `patch` and `delete` reach any route. A refused call raises `APIError` with the HTTP status and the API's own message; `status` is 0 when Clips Kitty is not running. A pipeline plugin does not need the API to do its job.

## Where the SDK is tested

`tests/test_plugin_sdk.py` (job reading, progress lines, result writing and checking, moments, ratings and notes, errors, the environment, the job a manifest's permissions allow, the `run` command on fixture plugins, finding, rating and understanding, the API client against a stand-in server) runs with the standard library and PyYAML only, as in CI. `tests/test_plugin_runner.py` runs real plugin processes through the app's runner.
