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
| Python packages added for the baseline (not repo dependencies) | pytest 9.1.1, ruff 0.16.10, opencv-python-headless 5.0.0, yt-dlp (CI installs only pyyaml, ruff, pytest, requests) |
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

Rule for the night: every test that passed at this baseline must still pass after each phase.

## Phases

- [x] 0 · Setup: branch `claude/open-platform-w4eh9g`, BRIEF.md saved unchanged, baseline recorded
- [ ] 0A · Repository map → `docs/platform/repo-map.md`
- [ ] 0B · Research notes → `docs/platform/research-notes/*.md`
- [ ] 0C · `docs/platform-research.md` (summary, two matrices, borrow sections, hypotheses H1–H10, the 46 questions)
- [ ] 1 · `docs/platform-architecture.md`
- [ ] Review of both documents against BRIEF.md sections 3 and 4
- [ ] 2 · Public API boundary: reference with stability labels, API version, contract tests
- [ ] 3 · Plugin/pipeline contract (minimum SDK) with a first-party adapter
- [ ] 4 · Manifest schema and validator
- [ ] 5 · Example external pipeline
- [ ] 6 · Plugin manager
- [ ] 7 · GitHub registry (index format, build script, offline client)
- [ ] 8 · Marketplace UI
- [ ] 9 · Model management
- [ ] 13 · `docs/platform/OVERNIGHT-REPORT.md` and the draft PR

## Next action

Phase 0A readers are running (one per subsystem); Phase 0B research agents start next. Assemble `repo-map.md` from the per-area notes when the readers finish.
