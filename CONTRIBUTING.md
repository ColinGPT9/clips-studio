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
cd ui && npm install
npm run dev          # starts Electron + the backend together
npm run typecheck    # must pass before a PR
```

## Pull requests

- Keep PRs focused on one issue; link it ("Fixes #123").
- `npm run typecheck` clean; try the affected flow in the running app.
- Match the style around you — comments explain *why*, not *what*.

## Triage (maintainers)

Priority = 👍 reactions + comment count on issues, `critical` /
`high-priority` labels first. Duplicates: close with a link to the
canonical issue so reactions concentrate in one place.
