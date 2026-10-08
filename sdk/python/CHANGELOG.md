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
