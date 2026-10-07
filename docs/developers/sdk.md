# SDK

The Clips Kitty plugin SDK is a small Python package, `clipskitty_sdk`, in [`sdk/python/clipskitty_sdk/`](../../sdk/python/clipskitty_sdk/). A pipeline plugin uses it to read its job, report progress and hand back moments. It imports only the Python standard library (reading a YAML manifest also needs PyYAML), so depending on it does not mean depending on Clips Kitty.

| | |
|---|---|
| SDK version | `clipskitty_sdk.__version__` = `1.0.0` |
| Plugin contract | `clipskitty_sdk.PLUGIN_API_VERSION` = `1` (see [Versioning](versioning.md)) |
| Python | 3.10 or newer |
| Licence | The repository's AGPL-3.0 for now. Whether the SDK gets a more permissive licence is the owner's decision (`docs/platform/DECISIONS.md` D5). |
| Published to PyPI | No. See "Getting the SDK" below. |

## Getting the SDK

You do not install it in the app. When Clips Kitty starts your plugin it puts its own copy of the SDK on the plugin's `PYTHONPATH`, so `import clipskitty_sdk` works without a `requirements.txt` entry.

To develop against it, point `PYTHONPATH` at a Clips Kitty checkout:

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
| `job.limits` | `max_clips` (`None` means no limit), `min_duration`, `max_duration` in seconds, from the user's settings. Advisory: `add_range` logs a warning when a range is outside them, and Clips Kitty cuts the list to `max_clips`. |
| `job.focus` | What the user asked the clips to be about, or `None` |
| `job.tools` | `ffmpeg`, `ffprobe` (paths, with the `ffmpeg` permission) and `ollama` (`{"host", "model"}`, with the `ollama` permission; `model` is empty when the user's AI runs at a cloud provider) |
| `job.models` | Models your manifest references, by name. Empty in plugin contract 1; model references are [planned](model-references.md). |
| `job.output_dir` | A folder of your own inside the job folder |
| `job.progress(fraction, message)` | How far along you are, 0 to 1. Shown on the job's "Finding the best moments" step. |
| `job.log(message)` | A line for the job's log |
| `job.add_range(start, end, *, score=None, label="", title="", reason="")` | One moment of the source video, in seconds. `score` 0-100 is optional. Raises `ContractError` for a range Clips Kitty would refuse (end before start, a score of 140). |
| `job.ranges` | What you have added so far |
| `job.finish(notes="")` | Writes `result.json`. `notes` appears in the job log. |
| `job.fail(message)` | Stops with exit code 1 and a message the user sees on the failed job |

Returning clip files instead of ranges (`job.add_clip`) is **planned**, not part of plugin contract 1.

### Checking files yourself

`check_job(data)` and `check_result(data)` return the list of problems with a `job.json` or `result.json` mapping (empty when valid). They are the same functions the app uses, so a result that passes them is one the app accepts. `clipskitty_sdk.contract` documents both files and the progress lines in its docstring.

## Running your plugin the way the app does

```text
python -m clipskitty_sdk run . --video sample.mp4 --transcript transcript.json --set min_score=70
```

This builds the same job folder the app builds (one shared function, `clipskitty_sdk.host.build_job`), including leaving out what your permissions do not cover, starts your manifest's `run.command`, shows progress, and checks `result.json` with the app's own checks. It prints the moments the app would take, best first, fitted to the video's length and the `--max-clips` limit. Exit code 0 means the app would accept the answer, 1 that the run failed or the answer would be refused, 2 that the command could not start (no manifest, an unknown setting).

| Option | |
|---|---|
| `--video FILE` | Required. Its length is read with `ffprobe` when one is on `PATH` (or given with `--ffprobe`). |
| `--transcript FILE` | `{"language", "segments"}` or a bare list of segments. Without it a plugin that asks for `transcript.read` gets an empty transcript. |
| `--set NAME=VALUE` | A setting from your manifest; the value is read as JSON when it parses (`70`, `true`), else as text. Repeatable. |
| `--secret NAME=VALUE` | A `secret` setting, passed in the environment as the app does |
| `--max-clips`, `--min-duration`, `--max-duration`, `--focus` | The job's limits (defaults: no clip limit, 10 and 60 seconds, as in the app's settings) |
| `--ollama-host`, `--ollama-model` | What a plugin with the `ollama` permission is told |
| `--python`, `--ffmpeg`, `--ffprobe` | Which programs to use |
| `--timeout SECONDS` | Default 600 |
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

`tests/test_plugin_sdk.py` (job reading, progress lines, result writing and checking, errors, the environment, the job a manifest's permissions allow, the `run` command on a fixture plugin, the API client against a stand-in server) runs with the standard library and PyYAML only, as in CI. `tests/test_plugin_runner.py` runs real plugin processes through the app's runner.
