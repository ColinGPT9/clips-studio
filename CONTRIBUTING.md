# Contributing to Clips Studio

Thanks for helping! Clips Studio is a local-first AI clipping app — Python
FastAPI backend + Electron/React UI. Issues labeled
[`good-first-issue`](../../labels/good-first-issue) are small and
well-scoped if you want a place to start.

## Fixing a bug from a Feedback Hub report

Most bug reports arrive through the app's in-app Feedback Hub (label
[`from-app`](../../labels/from-app)). They're written by non-technical
streamers, but each one carries an auto-collected **Diagnostics** block:

- exact app commit, Windows version, CPU/GPU/VRAM/RAM
- the AI model in use (name, parameter size, quantization, Ollama version)
- FFmpeg / OpenCV / faster-whisper / yt-dlp versions
- the non-secret settings, the video's platform, and the recent log tail

That block is the reproduction recipe: match the model + settings and feed
a similar video (platform matters — Twitch/Kick VODs are H.264; YouTube
sources are H.264 by design, see `video/encoding.py`). Secrets and
usernames are redacted before reports ever leave the reporter's machine.

## Dev setup

```
pip install -r requirements.txt
pip install ruff pytest          # the checks CI runs
cd ui && npm install
npm run dev                      # starts Electron + the backend together
```

Before a PR:

```
pytest                                   # deterministic logic
ruff check .                             # lint
cd ui && npm run typecheck && npm run build
```

Want to add a language, a model, a platform or an export format? Each is a
small, well-defined change — see [docs/EXTENDING.md](docs/EXTENDING.md).

## Building the Windows installer

```
pip install -r requirements-build.txt
python scripts/build_installer.py
```

That runs the whole chain and stops at the first failure with an
explanation: fetch FFmpeg → freeze the Python engine with PyInstaller →
smoke-test the frozen engine → build the renderer → wrap it all up. The
results land in `release/`: a small **Web Setup .exe**, the **.7z payload**
it downloads, and a **.zip** for offline installs.

Publish the Web Setup and the .7z to the *same* GitHub release — the setup
fetches the payload by name, so one without the other is useless. Full
release checklist: [docs/RELEASING.md](docs/RELEASING.md).

Expect it to take a while and to need disk: the frozen engine is ~4.8 GB
unpacked, mostly CUDA PyTorch. `--skip-backend` and `--skip-ui` reuse the
previous run's output while iterating, and `--backend-only` stops after the
freeze.

Things worth knowing before you change any of it:

- **The backend is frozen as a console app on purpose.** A windowed
  PyInstaller build gives the process no stdout, and every `print()` in the
  pipeline then raises. Electron passes `windowsHide` so no console appears.
- **PyTorch ships as the CUDA build.** It isn't only for tracking — those
  wheels carry the cuBLAS/cuDNN DLLs that CTranslate2 needs for GPU
  transcription, so a CPU build silently drops Whisper to CPU as well.
- **FFmpeg comes from `vendor/`,** fetched by `scripts/fetch_ffmpeg.py` and
  gitignored. Never call `ffmpeg` by bare name — use `core.binaries.ffmpeg()`,
  or an installed copy will look for a binary the user doesn't have.
- **Code signing is off** (`signAndEditExecutable: false`). electron-builder
  otherwise downloads a bundle containing macOS symlinks, which an ordinary
  Windows account cannot extract. The trade-off and how to re-enable it are
  documented in `ui/electron-builder.yml`.
- **Don't switch the target back to plain `nsis`.** `makensis.exe` is 32-bit
  and memory-maps the payload to embed it, so it dies around 2 GB with
  `failed creating mmap`. The app is ~5 GB unpacked. That is a ceiling, not
  a setting.

## Pull requests

- Keep PRs focused on one issue; link it ("Fixes #123").
- `npm run typecheck` clean; try the affected flow in the running app.
- Match the style around you — comments explain *why*, not *what*.

CI runs on every PR: Python compiles, config and prompts parse, the desktop
app typechecks and builds, and website links resolve. It is fast and it is
narrow — a runner has no GPU, no Ollama and no footage, so **a green tick does
not mean clips still come out well.** Anything touching scoring, tracking,
captions or rendering needs testing against a real video, and the PR should
say which one.

## Triage (maintainers)

Priority = 👍 reactions + comment count on issues, `critical` /
`high-priority` labels first. Duplicates: close with a link to the
canonical issue so reactions concentrate in one place.
