# Progress

Overnight run started 2026-10-06 10:55 UTC. Brief: [BRIEF.md](BRIEF.md). Decisions: [DECISIONS.md](DECISIONS.md).

## Environment (recorded 2026-10-06 11:05 UTC)

| Item | Found |
|---|---|
| Operating system | Ubuntu 24.04.5 (Linux 6.18, x86_64), 4 CPUs, 15 GB RAM, ~30 GB free disk. The app ships on Windows; nothing here exercises Windows paths, symbolic links or process spawning. |
| Python | 3.13.16 (CI uses 3.11; pyproject targets 3.10) |
| Node | 22.22.0, npm 10.9.4 (CI uses Node 20). `ui/node_modules` is not installed. |
| FFmpeg | 6.1.1 (`/usr/bin/ffmpeg`, `/usr/bin/ffprobe`) |
| GPU | none (`nvidia-smi` absent, no `/dev/nvidia*`) |
| Ollama | not installed, nothing on 127.0.0.1:11434 |
| Whisper | faster-whisper not installed; no CTranslate2 weights present |
| YOLO weights | none (`*.pt` is gitignored; ultralytics not installed) |
| Python packages added for the baseline (not repo dependencies) | pytest 9.1.1, ruff 0.16.10, opencv-python-headless 5.0.0, yt-dlp, and later fastapi 0.142.2 for the live API probe in Phase 0A (CI installs only pyyaml, ruff, pytest, requests) |
| Project installs and builds | Python engine imports (`core.pipeline`) once OpenCV is present; the desktop UI was not built here (no node_modules) |

## Test baseline (before any change, 2026-10-06 12:40 UTC)

`python3 -m pytest -q --tb=no -p no:cacheprovider` → **1633 passed, 14 failed, 72 skipped** (pytest's own summary line does not print in this sandbox; the counts come from the progress dots in `scratchpad/baseline-summary.txt`). `python3 -m ruff check .` → all checks passed.

The 14 failures are environment limits, not code regressions, and every one of them fails the same way on main:

| Test | Why it fails here |
|---|---|
| `tests/test_outro.py` (7) and `tests/test_outro_join.py` (3), `tests/test_vertical_live.py::test_a_vertical_live_clip_still_ends_with_the_clips_kitty_card` | `PIL.ImageFont` "cannot open resource": the end-card font is not installed in this sandbox |
| `tests/test_haar_cascade_missing.py` (2) | `opencv-python-headless` 5.0 ships no `cv2/data/haarcascades` |
| `tests/test_clip_intent.py::test_a_time_range_gets_windows_and_points` | fails on main here too (`assert []` at line 294); noted by the Basketball thread as a sandbox-only failure, not investigated tonight |

Running the suite rewrites `docs/brand/mascot.png`, `docs/brand/mascot-head.png` and `ui/build/icon.ico` (the mascot test regenerates them and the sandbox font differs). They are reverted with `git checkout --` before every commit.

**Second baseline, with httpx (2026-10-06, before Phase 1).** The API tests use FastAPI's `TestClient`, which needs `httpx`; without it they skip. With `httpx` 0.28.1 added to the sandbox (D6): **1879 passed, 14 failed, 23 skipped**, the same 14 failures as above (list in `scratchpad/baseline-failures.txt`). This is the baseline the build phases are compared with.

Rule for the night: every test that passed at this baseline must still pass after each phase.

## Phases

- [x] 0 · Setup: branch `claude/open-platform-w4eh9g`, BRIEF.md saved unchanged, baseline recorded
- [x] 0A · Repository map → `docs/platform/repo-map.md` (5fc30c7)
- [x] 0B · Research notes → `docs/platform/research-notes/*.md` (22 notes, all read 2026-10-06)
- [x] 0C · `docs/platform-research.md` (summary, two matrices with no empty cell, borrow sections, hypotheses H1–H10, all 46 questions answered)
- [x] 1 · `docs/platform-architecture.md` (types, SDK, manifest, execution, models, permissions with enforced or declared labels, trust, versioning, registry and Marketplace, dogfooding, designed-later, two worked examples, build plan, the four success tests)
- [x] Review of both documents against BRIEF.md sections 3 and 4: an agent that had not seen the drafting checked about 45 code references and 9 external links. Verdict: one API, one engine, one queue kept; all four success tests pass with gaps. 26 findings (1 blocking: Open Shorts under-declared data leaving the PC; 12 should-fix; 13 minor). All fixed: the architecture's new "Review" section lists each finding and what changed; the research document has a "superseded by the architecture" table; two findings were fixed in code (Gaming scoring beside a pipeline, Phase 3; the pipeline choice no longer travels to a paired render PC). A CI job now runs the platform tests, which skipped in the existing Python job.
- [x] 2 · Public API boundary: `server/api_stability.py` (60 stable, 5 experimental, 111 internal), generated `docs/developers/api-reference.md`, `tests/test_api_contract.py` (20 pass, 1 skips without psutil) (dc64ebd, 96929d6). Full suite after: 1899 passed, 14 failed (the baseline 14), 24 skipped.
- [x] 3 · Plugin/pipeline contract: `sdk/python/clipskitty_sdk/` (contract, Job, host runner shared with the engine, `python -m clipskitty_sdk run`, local API client), `plugins/` (store lookup, runner), the `pipeline` job option on every entry point, one branch in `process_video`, the first-party adapter `examples/pipelines/transcript-highlights/` (run in tests, same moments as calling the scorer directly), packaging and CI lines (4cab905). `tests/test_plugin_sdk.py`, `test_plugin_runner.py`, `test_plugin_job_option.py`: 80 tests, all real plugin processes. Full suite after: 1987 passed, the baseline 14 failed, 24 skipped.
- [x] 4 · Manifest validator `sdk/python/clipskitty_sdk/manifest.py` with a generated JSON Schema, `python -m clipskitty_sdk validate`, built-in manifests for Shorts, Gaming and Sports, setting values checked when a job is added (2e5d109). `tests/test_plugin_manifest.py` over 5 valid and 41 invalid fixtures, each invalid one naming the only messages it may produce. Full suite after: 2080 passed, the baseline 14 failed, 24 skipped.
- [x] 5 · Example external pipeline `examples/pipelines/scene-cut-highlights/` (loud stretches started on the scene cut before them, FFmpeg only, stdlib and SDK only) (cdbcbfb). `tests/test_example_pipeline.py`: 12 tests, end to end through the app's runner on an FFmpeg-made video with cuts and a loud stretch at known times, a run with only the SDK on PYTHONPATH, an import check. No shared code changed; the Phase 4 suite plus these 12 pass.
- [ ] 6 · Plugin manager
- [ ] 7 · GitHub registry (index format, build script, offline client)
- [ ] 8 · Marketplace UI
- [ ] 9 · Model management
- [ ] 13 · `docs/platform/OVERNIGHT-REPORT.md` and the draft PR

## Next action

Phase 6: the plugin manager (install from a folder or a Git commit, enable, disable, update, roll back, remove, the session secret, routes).
