# Clips Kitty SDK changelog

No version is on PyPI yet. Clips Kitty bundles the SDK from the commit it is built from, so a plugin always runs with the SDK of the Clips Kitty it runs in.

The installed app puts its own SDK on the plugin's path, and it wins over one installed with pip. A plugin that uses a module added in a later SDK sets `requires.clips_kitty` to the first release that bundles it ([Versioning](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md)).

## 1.2.0 (not released yet)

- `python -m clipskitty_sdk --version` prints the SDK's version and the plugin contract it follows: `clipskitty-sdk 1.2.0 (plugin contract 1)`.
- Installed with pip, the SDK adds a `clipskitty-sdk` command, the same as `python -m clipskitty_sdk`.
- A `test` extra installs pytest, for a plugin's own tests: `pip install "clipskitty-sdk[yaml,test]"`.
- The package ships `py.typed`, so type checkers read its type hints.
- The package's details list the Python versions it supports, and link to its issues and this changelog.
- `edits` is a planned output, for the edit step: `outputs: [edits]` is refused with "output 'edits' is planned, not supported by plugin API 1" instead of "unknown output". `contract.PLANNED_STEPS` names the planned steps, `edit` and `export`.
- `validate` and `run` show the line of `clipskitty.yaml` each problem is on, and "did you mean permissions?" under a misspelt field (`Report.hints`, `manifest.line_marks`). Without a `clipskitty.yaml`, or without PyYAML on the PC, they say so and exit with 2.
- `version: 0.1` is refused with "YAML read this as the number 0.1; write a version like 0.1.0", in the app too. The `run.python_requirements` warning says that pipelines run on Clips Kitty's own Python.
- `validate` and `run` warn about code that would fail on Clips Kitty's own Python (`clipskitty_sdk.lint`): syntax newer than Python 3.11, imports it doesn't promise (other packages, Clips Kitty's own code, modules the app leaves out or Windows lacks, a module at the plugin's root), and text files opened without `encoding=`. Files under `tests/` are skipped.
- `host.APP_PYTHON` is `(3, 11)`, the Python Clips Kitty runs plugins on. `host.plugin_env` sets `PYTHONUTF8=0`.
- `run` stops a plugin when Clips Kitty would (`host.timeout_seconds`: `run.timeout_minutes`, else 60 minutes to find and 10 to understand or rate) instead of after 600 seconds; `--timeout` still sets another limit.
- `run`: a refused run makes no job folder; `--set` values follow the setting's type, and an unknown one lists the plugin's settings; `--steps` takes several words (`--steps understand rate`), and `--steps edit` says edit is planned; `--game`, `--game-hint` and `--model` fill `video.games` and `job.models`; a warning when the plugin asks for FFmpeg and none is found; each moment's reason (`why:`), and a warning for a label that isn't in the manifest's `events`; on a terminal, progress rewrites one line.
- `run(main)` started without a job folder (`python src/main.py`) says how to try the plugin and exits with 2. `job.settings[name]` for a setting with no value raises `SettingMissing`: creators see a plain line and the log gets the developer's hint. A slip in the plugin's own code (a `KeyError`, `TypeError` and the like) gives creators "it stopped on a mistake in its own code. Ask its developer to fix it.", with the exception and its line in the log; other exceptions keep their message.
- `job.rate` and `job.add_range` show the score they refused: "(got 140)".
- `clipskitty_sdk.devrun` holds the core of `run`, so other tools can run a plugin the same way.

## 1.1.0

- Plugins can understand and rate moments that were found before them, as well as find their own ([Steps](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/steps.md)).
- `job.moments`: the moments handed over to understand or rate, as `Moment`s.
- `job.understand(moment, text)` and `job.rate(moment, score, reason)`: a run's answers.
- `job.wants(step)`: whether this run is asked to `find`, `understand` or `rate`.
- `job.text(moment)`: what is said during a moment.
- `Moment`, exported from `clipskitty_sdk`.
- `run --steps` and `--moments` try a moment run; `--duration` and `--min-score` go with them, and `--video` is needed only to find moments or for a plugin with `video.read`.

## 1.0.0

- The job contract: `job.json` in, progress lines out, `result.json` back (`clipskitty_sdk.contract`, `check_job`, `check_result`).
- `read_job()`, `run(main)` and `Job`: `progress`, `log`, `secret`, `add_range`, `finish` and `fail`.
- The manifest checks the app, the plugin manager and the registry run (`clipskitty_sdk.manifest`).
- `clipskitty_sdk.host`: the job folder, the process and the result, as the app runs a plugin.
- The `validate`, `schema` and `run` commands.
- `LocalAPI`: a small client for Clips Kitty's local API.
