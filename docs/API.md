# The local API

Clips Kitty is a desktop app on top of a **local HTTP service**. The desktop
window is one client of it. Anything else can be another: a Discord bot that
clips a stream on command, a batch runner, a web front end, an OBS integration,
a script that queues last night's VOD every morning.

Nothing needs to be added to the app for that to work. The service is already
running whenever Clips Kitty is open, on `127.0.0.1:8765`.

The service has 71 HTTP endpoints and a WebSocket. This document covers the
subset meant to be built against — most of the rest are the desktop UI talking
to itself, and are listed as internal below.

> **Every example here was run against a live instance**, and the responses are
> real (with the video titles swapped for made-up ones). If something in this
> file does not work, that is a bug worth reporting.

---

## Contents

- [Start it](#start-it)
- [Security: read this before binding anything](#security-read-this-before-binding-anything)
- [Which data directory you are talking to](#which-data-directory-you-are-talking-to)
- [Supported and internal](#supported-and-internal)
- [Conventions](#conventions)
- [Readiness](#readiness)
- [Submitting work](#submitting-work)
- [Watching progress](#watching-progress)
- [The queue](#the-queue)
- [Results](#results)
- [Models](#models)
- [Languages and export](#languages-and-export)
- [WebSocket events](#websocket-events)
- [A complete example](#a-complete-example)
- [Gotchas](#gotchas)

---

## Start it

If the desktop app is open, the API is already up — it is the same process
tree, and the app is just a window onto it.

Headless, which is usually what an integrator wants:

```bash
python main.py serve --port 8765
```

Confirm it is alive:

```bash
curl http://127.0.0.1:8765/health
# {"ok":true}
```

**FastAPI's interactive docs are served too**, and they are generated from the
running code rather than written by hand, so they never drift:

| | |
|---|---|
| `http://127.0.0.1:8765/docs` | Swagger UI — every endpoint, try them in the browser |
| `http://127.0.0.1:8765/redoc` | the same thing, easier to read |
| `http://127.0.0.1:8765/openapi.json` | the schema, for generating a client |

Use those for the full list. Use this document for which ones to build on and
what actually happens when you call them.

## Security: read this before binding anything

**There is no authentication. None.** Not a token, not a password, not an
origin check.

That is a deliberate trade for a single-user desktop app on loopback, and it
means anything that can reach the port can:

- read every video title, transcript and clip on the machine
- stream the video files themselves out of `/media/{clip_id}`
- queue downloads, delete clips, delete videos, change settings

`main.py` binds `127.0.0.1` by default and prints a warning if you change it:

```
WARNING: binding 0.0.0.0 — this API has no authentication.
```

**Exposing this to a network is not a supported configuration.** If you need
remote access, put your own authenticated service in front of it and keep the
API itself on loopback. `--host 0.0.0.0` exists for containers, where
"localhost" means the container and nothing on the host can reach in anyway.

## Which data directory you are talking to

Easy to lose an hour to, so it is near the top:

| How it is running | Where its database and videos live |
|---|---|
| Installed build | `%LOCALAPPDATA%\Clips Studio\data` |
| A source checkout | `<repo>/data` |

These are **separate libraries with separate databases**. A checkout resolves
the relative `paths.data_dir` against the repo, not the working directory, so a
dev instance and an installed instance disagree about which videos exist even
though they serve identical routes on the same machine — and will happily fight
over the same port.

Two consequences worth knowing before you debug something confusing:

- A video that is `done` in the installed app is unknown to a source run, so
  the "already processed" guard does not fire and it downloads again.
- If you run a second instance for development, give it `--port 8766`. It still
  runs its own worker thread and will claim jobs from *its* queue.

## Supported and internal

The endpoints in this document are the ones intended to be built on. They will
not change shape without a note in [CHANGELOG.md](../CHANGELOG.md).

**Everything else is internal** — branding assets, creator memory, caption
editing, the AI edit endpoints, feedback submission. They exist to serve one
specific screen and they change when that screen changes. They are visible in
`/docs`, they work, and depending on them is at your own risk.

That line is drawn now because it cannot be drawn retroactively. If something
internal is genuinely useful to you, open an issue asking for it to be
promoted, rather than pinning yourself to a version.

This is alpha software. The line above is a commitment to *tell you*, not a
guarantee of never changing.

## Conventions

- **JSON in, JSON out.** `Content-Type: application/json` on anything with a body.
- **Job IDs are integers.** `149`. Passing a non-integer gets a 422, not a 404.
- **Video IDs are strings with a platform prefix.** YouTube uses its own
  11-character ID (`aB3dEfGhIjK`), Twitch prefixes `tw_`, Kick prefixes `kick_`,
  an imported file gets `local_`. They cannot collide, which is the point.
- **Clip IDs are integers**, unique across all videos.
- **Errors** are `{"detail": "..."}` with 400 (bad request), 404 (no such
  thing), or 409 (queue full). Validation failures are FastAPI's standard 422
  with a list of field errors.
- **No pagination anywhere.** `GET /videos` returns every video, `GET /jobs`
  every job. Fine for a personal library; know it before you point this at
  10,000 rows.
- **Times are seconds as floats.** Timestamps are naive local ISO-8601
  (`2026-08-15T13:56:15`), not UTC, with no timezone marker.

---

## Readiness

### `GET /health`

```json
{"ok": true}
```

The liveness check. Cheap enough to poll.

### `GET /health/preflight`

Whether it can actually *do* anything, which is a different question.

```json
{
  "ready": true,
  "checks": [
    {"name": "ffmpeg",  "ok": true, "detail": "8.1.2-essentials_build", "fix": "", "blocking": true},
    {"name": "ollama",  "ok": true, "detail": "running, 2 model(s) installed", "fix": "", "blocking": true},
    {"name": "model",   "ok": true, "detail": "gemma:7b installed", "fix": "", "blocking": true},
    {"name": "whisper", "ok": true, "detail": "large-v3-turbo, small bundled", "fix": "", "blocking": false},
    {"name": "gpu",     "ok": true, "detail": "NVIDIA GeForce RTX 3060 (13 GB)", "fix": "", "blocking": false},
    {"name": "disk",    "ok": true, "detail": "67 GB free", "fix": "", "blocking": false}
  ]
}
```

Seven checks: `ffmpeg`, `ffprobe`, `ollama`, `model`, `whisper`, `gpu`, `disk`.
**Call this before submitting work.** A failing `blocking` check means the job
will be accepted and then die — no model installed is the common one. `fix`
carries a human-readable remedy when a check fails.

`gpu` and `disk` are non-blocking: it runs on the CPU, slowly.

### `GET /system/stats`

```json
{
  "cpu_percent": 4.2, "ram_percent": 61.0,
  "data_dir_bytes": 48216342528, "disk_free_bytes": 72341598208,
  "gpu": {"name": "NVIDIA GeForce RTX 3060", "vram_used": 1024, "vram_total": 12288, "gpu_percent": 3},
  "build_sha": "", "started_at": 1755261234.5, "uptime_seconds": 8134.2
}
```

`gpu` is `null` on a machine without one.

## Submitting work

### `POST /jobs`

The main entry point. Only `url` is required.

```bash
curl -X POST http://127.0.0.1:8765/jobs \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=aB3dEfGhIjK"}'
```

```json
{"job_id": 149}
```

Takes YouTube, Twitch and Kick links. Optional fields, all defaulting to the
values in `config/settings.yaml`:

| Field | Type | What it does |
|---|---|---|
| `force` | bool | process again even if this video is already done |
| `max_clips` | int | cap clips from this video |
| `min_score` | int | quality bar, 0–100 |
| `captions` | bool | burn captions in (default true) |
| `caption_style` | object | font, size, colour, position, `words_per_caption` |
| `long_clips` | bool | 61–180s clips, for TikTok monetisation |
| `podcast` | bool | multi-camera: letterbox, no subject tracking |
| `longform` | object | `{"mode": ...}` — `short_clips`, `clips_140`, `highlights` or `edited_stream` |
| `filter` | string | colour preset from `video/filters.py` |
| `watermark_profile_id` | int | branding profile applied to every clip |

**Two responses that are not failures and not `job_id`:**

```json
{"job_id": null, "already_processed": true, "video_id": "tw_2833826919"}
{"job_id": null, "already_queued": true, "video_id": "aB3dEfGhIjK", "queued_job_id": 148}
```

Re-submitting a finished video is silently a no-op without `force`, because
processing costs an hour and produces duplicate clips. **Check for `job_id`
being `null`** rather than assuming a job was created. Retry with
`{"force": true}` if you meant it.

Other outcomes:

- `409` — the queue is full (the cap is a real limit, not a greyed-out button)
- `422` — no `url` field

> Two known warts, both harmless: this returns **200**, not 201, and it accepts
> a stray `status_code` query parameter that does nothing. Do not send it.

### `POST /jobs/batch`

Several links at once, each with its own settings.

```bash
curl -X POST http://127.0.0.1:8765/jobs/batch \
  -H "Content-Type: application/json" \
  -d '{"items": [{"url": "https://www.twitch.tv/videos/123456789"}, {"url": "not a link"}]}'
```

```json
{
  "created": [{"url": "https://www.twitch.tv/videos/123456789", "job_id": 150, "video_id": "tw_123456789"}],
  "skipped": [{"url": "not a link", "reason": "unrecognized"}]
}
```

**Deliberately tolerant**: one bad link in a list of twelve reports itself and
the other eleven still queue. `reason` is one of `unrecognized`,
`already_processed`, `already_queued`, `queue_full`, `bad_option`. Always read
`skipped` — the request succeeds with a 200 even when nothing was queued.

### `POST /videos/local`

A file already on this computer. No download, same pipeline.

```json
{"path": "D:/streams/friday.mp4", "title": "Friday stream", "channel": "examplechannel"}
```

`title` defaults to the filename. **Set `channel`**: creator profiles key off
it, so an empty one means catchphrase learning and preference history quietly
do not run for that video. Accepts the same option fields as `POST /jobs`.

A path that is not a readable video gives `400`:

```json
{"detail": "not a video file this app can open: C:/does/not/exist.mp4"}
```

## Watching progress

### `GET /jobs`

Every job, newest last. Note this is a **bare array**, not an object:

```json
[
  {
    "id": 149, "type": "process", "status": "done", "error": "",
    "video_id": "aB3dEfGhIjK", "title": "Friday stream",
    "position": 3, "attempts": 0, "interrupted": 0,
    "created_at": "2026-08-15T13:56:15", "started_at": "2026-08-15T13:56:15",
    "updated_at": "2026-08-15T14:41:02", "finished_at": "2026-08-15T14:41:02",
    "payload": "{\"url\": \"...\", \"force\": false}"
  }
]
```

`status` is `queued`, `running`, `done` or `failed`.

### `GET /jobs/{id}`

One job, same shape. `404 {"detail": "no such job"}` if it is gone.

### `GET /jobs/{id}/log`

```json
{"log": "…", "missing": false}
```

The run's own log file, last 300 lines. `?tail=2000` for more. `missing` is
true when the file has been cleaned up — the in-memory event stream only holds
minutes, which is no use for a batch that failed at 3am. **This is the endpoint
to surface when a job fails**; `error` on the job row is one line, and the log
is why.

### `POST /jobs/{id}/retry`

Re-queue a failed job with its original settings.

### `DELETE /jobs/{id}`

```json
{"deleted": 149}
```

Removes a queued job. Also works on a running one — see `POST /cancel` first.

### `POST /cancel`

```bash
curl -X POST http://127.0.0.1:8765/cancel \
  -H "Content-Type: application/json" -d '{"video_id": "aB3dEfGhIjK"}'
```

```json
{"cancelling": "aB3dEfGhIjK"}
```

**Cooperative, not immediate.** The pipeline stops at its next stage boundary
(or aborts the download). A job cancelled during transcription keeps running
until transcription finishes. Accepts `video_id` or `url`; `400` with neither.

A cancelled download can leave a `.part` file in `data/downloads/` — see
`POST /storage/cleanup`.

## The queue

### `GET /queue`

The grouped view the UI renders, and the one endpoint to poll if you only poll
one:

```json
{
  "processing": [], "queued": [], "completed": [], "failed": [],
  "paused": false,
  "estimate": {"queued_seconds": 0, "per_video_seconds": 2400, "samples": 6, "confident": true},
  "capacity": 5, "max_active": 5
}
```

`max_active` is the cap on videos waiting or running at once (**5**);
`capacity` is how many more you may add right now. When `capacity` hits zero,
`POST /jobs` returns `409` and `POST /jobs/batch` skips with
`"reason": "queue_full"`.

Job objects here are **richer than in `GET /jobs`** — they add `log_path`,
`display_title`, `channel`, `url`, `source_seconds`, `video_status` and the
`settings` snapshot.

`estimate.confident` is false below three samples, because a 3-hour stream and
a 20-minute upload do not cost the same and two data points cannot tell you
which you have. Do not show a countdown when it is false.

### `POST /queue/pause` · `POST /queue/resume`

```json
{"paused": true}
```

Stops claiming new work. Whatever is running keeps running. **The pause state
lives in the database**, so it survives a restart — and a paused queue that
nobody un-paused looks exactly like a broken app. Check `paused` before
reporting that nothing is happening.

## Results

### `GET /videos`

Every processed video, newest first. A bare array:

```json
[
  {
    "video_id": "aB3dEfGhIjK", "title": "Friday stream",
    "channel_id": "", "channel_name": "examplechannel",
    "status": "done", "duration": 10842.0, "clip_count": 38,
    "creator_id": 4, "creator_name": "examplechannel",
    "process_seconds": 2831.4,
    "created_at": "2026-08-12T21:49:50", "updated_at": "2026-08-12T22:09:28"
  }
]
```

### `GET /videos/{video_id}/clips`

```json
[
  {
    "id": 80, "video_id": "aB3dEfGhIjK",
    "start_s": 3574.12, "end_s": 3590.64, "score": 98,
    "title": "The one about the sandwich",
    "description": "A short summary written by the model.",
    "hashtags": ["#clip", "#stream", "#funny"],
    "hook": "the transcript line the clip was chosen for",
    "path": "C:\\Users\\you\\AppData\\Local\\Clips Studio\\data\\clips\\…\\clip_03574-03590.mp4",
    "status": "queued", "scheduled_for": null,
    "created_at": "2026-08-12T22:00:50",
    "scores": {"text": 88, "audio": 54, "visual": 72, "reaction": 66,
               "engagement": 85, "action": 10, "trending": true,
               "source": "transcript", "rerank_position": 2},
    "render_opts": {"caption_style": {"font": "Arial", "font_size": 84,
                    "words_per_caption": 3, "uppercase": true}, "podcast": true}
  }
]
```

`score` is the final 0–100 ranking; `scores` is the breakdown that produced it,
which is the interesting part if you are building your own selection on top.

> **An unknown video ID returns `200 []`, not 404.** A typo in a video ID is
> indistinguishable from a video with no clips. Check `GET /videos` first.

### `GET /media/{clip_id}`

The rendered MP4.

```
curl -r 0-63 http://127.0.0.1:8765/media/80
→ 206 Partial Content, video/mp4, Content-Range: bytes 0-63/8922466
```

Supports range requests, so it can be the `src` of a `<video>` element and seek
properly. `404` if the clip or its file is gone. **`HEAD` returns 405** — use a
one-byte range request if you only want the size.

### `GET /clips/{clip_id}/captions`

```json
{"lines": [{"start": 0.0, "end": 0.98, "text": "the first caption line"}]}
```

Clip-relative seconds. `GET /clips/{clip_id}/words` is the same data at word
granularity as `{"words": [{"start", "end", "word"}]}`, and returns
`{"words": []}` when the transcript file has been cleaned up.

## Models

### `GET /models`

```json
{
  "active": "ollama/gemma:7b",
  "installed": [{"name": "gemma:7b", "size_gb": 5.01}],
  "recommended": {"model": "gemma3:12b", "reason": "Sized for 13 GB of VRAM — …"},
  "recommendations": [{"hardware": "8 GB VRAM", "model": "gemma:7b", "note": "…"}],
  "other_models": [{"purpose": "Translation / multilingual", "model": "qwen3:8b / qwen3:14b", "note": "…"}]
}
```

`recommended` is the single answer for *this* machine, measured from actual
VRAM. `recommendations` is the whole table.

### `POST /models/activate`

```json
{"tag": "gemma3:12b"}
```

`400 {"detail": "'x' is not pulled yet"}` if it is not installed. Anything
Ollama serves works — the app has no allow-list.

### `POST /models/pull`

Downloads a model. Returns immediately; **progress arrives on the WebSocket**
as `{"type": "model_pull", "tag": …, "status": "done"}` (or `"error"`). A 12 GB model
on a slow connection is not an HTTP request you want to hold open.

## Languages and export

### `GET /languages`

```json
{
  "languages": [{"code": "es", "name": "Spanish", "native": "Español",
                 "can_dub": true, "caption_font": null}],
  "dubbing_available": true
}
```

19 languages. `caption_font` is non-null where burned captions need a specific
font for the script — Chinese gets Microsoft YaHei, Hindi gets Nirmala UI —
because the default Latin fonts render those as empty boxes, permanently, in
the video.

**`dubbing_available` reflects whether the speech engine is importable.** It
is true in installed builds from 1.1.3, which bundle it, and false in a source
checkout without the optional dependency. `can_dub` describes the language, not
your installation — check both.

### `POST /translate`

```json
{"clip_ids": [80, 81], "languages": ["es", "pt"], "stage": "translate"}
```

`stage` is `translate` (produce text for review) or `export` (write files).
With `export`, `folder` is where they land, and `burn`, `dub`, `subtitles` and
`post_text` choose what gets written. `400 {"detail": "no clips selected"}` on
an empty list.

### `POST /clips/{clip_id}/export`

```json
{"folder": "D:/clips/friday"}
```

Copies the clip out with its final filename. Returns `{"exported": [...]}`, and
**returns 200 with an empty list rather than 404 when the clip does not
exist** — check the array, not the status.

`POST /export/batch` takes `{"clip_ids": [...], "folder": "..."}`.

## WebSocket events

```
ws://127.0.0.1:8765/ws
```

Connect and listen. The server never expects a message from you. Four event
types:

```json
{"type": "queue"}
{"type": "job", "job_id": 149, "job_type": "process", "status": "running", "title": "Friday stream", "remaining": 2}
{"type": "progress", "job_id": 149, "stage": "transcribe", "video_id": "aB3dEfGhIjK"}
{"type": "model_pull", "tag": "gemma3:12b", "status": "done"}
```

- **`queue`** carries no data. It means "something changed, re-fetch
  `GET /queue`". You will see it often; it is the cheapest way for the server
  to stay honest without duplicating the queue in the event stream.
- **`job`** fires on status transitions. `error` is present when it failed.
- **`progress`** is the pipeline talking. `stage` moves through `download`,
  `downloaded`, `converting source to H.264`, `transcribe`, `analyze`,
  `render`, `done`. Render events carry `clip` and `total`.
  **`job_id` is `null` for prefetch downloads**, which belong to a future job,
  not the running one — never attribute them to the current job.
- **`model_pull`** is download progress for `POST /models/pull`.

Events are dropped rather than queued for a slow client (a 200-event buffer per
connection). Treat the WebSocket as a hint to re-read state, not as the state
itself.

## A complete example

**[`examples/drive_the_api.py`](../examples/drive_the_api.py) is a working
program** that walks the whole path — preflight, submit, follow the WebSocket,
read the clips — and it is kept running as part of the repo:

```bash
python main.py serve                                  # in another terminal
python examples/drive_the_api.py                      # preflight only
python examples/drive_the_api.py --url https://twitch.tv/videos/123456789
python examples/drive_the_api.py --file "D:/footage/stream.mp4"
```

The shorter version below is the same idea in one file with polling instead of
a WebSocket. It was run end to end against a live instance.

```python
"""Queue a video and wait for its clips. Requires: pip install requests"""

import time
import requests

API = "http://127.0.0.1:8765"
URL = "https://www.youtube.com/watch?v=aB3dEfGhIjK"

# 1. Can it actually work right now?
pre = requests.get(f"{API}/health/preflight", timeout=10).json()
blocking = [c for c in pre["checks"] if c["blocking"] and not c["ok"]]
if blocking:
    raise SystemExit("not ready: " + "; ".join(f"{c['name']}: {c['fix']}" for c in blocking))

# 2. Submit. A null job_id is an answer, not a failure.
r = requests.post(f"{API}/jobs", json={"url": URL}, timeout=30).json()
if r.get("already_processed"):
    video_id = r["video_id"]
    print(f"already done: {video_id}")
else:
    job_id = r["job_id"]
    print(f"queued job {job_id}")

    # 3. Poll. The WebSocket is nicer; this keeps the example to one file.
    while True:
        job = requests.get(f"{API}/jobs/{job_id}", timeout=10).json()
        print(f"  {job['status']}")
        if job["status"] in ("done", "failed"):
            break
        time.sleep(15)

    if job["status"] == "failed":
        log = requests.get(f"{API}/jobs/{job_id}/log", timeout=10).json()
        raise SystemExit(job["error"] + "\n" + log["log"][-2000:])
    video_id = job["video_id"]

# 4. Read the clips.
clips = requests.get(f"{API}/videos/{video_id}/clips", timeout=30).json()
for c in sorted(clips, key=lambda c: -c["score"]):
    print(f"{c['score']:3}  {c['start_s']:8.1f}s  {c['title']}")
    print(f"     {API}/media/{c['id']}")
```

The same thing in one line, for a video that is already processed:

```bash
curl -s http://127.0.0.1:8765/videos/aB3dEfGhIjK/clips \
  | python -c "import json,sys; [print(c['score'], c['title']) for c in json.load(sys.stdin)]"
```

## Gotchas

Collected because each one has cost somebody time:

1. **`POST /jobs` can return `{"job_id": null}`.** Already processed or already
   queued. Check the field, not the status code.
2. **`GET /videos/{unknown}/clips` returns `200 []`.** A typo looks like a video
   with no clips.
3. **`POST /clips/{id}/export` returns 200 for a clip that does not exist.**
   Check `exported`.
4. **`HEAD /media/{id}` is a 405.** Use a range request.
5. **A paused queue is invisible** unless you read `paused`. It persists across
   restarts.
6. **Cancelling is cooperative.** The job keeps running to the next stage
   boundary.
7. **A source checkout and an installed build have different libraries** on the
   same machine, and both want port 8765.
8. **`dubbing_available` and `can_dub` are different questions** — one is
   your installation, the other is whether the language has a voice at all.
9. **One worker, one video at a time.** GPU contention makes parallel jobs
   pointless on consumer hardware, so a queued job waits — that is not a hang.
10. **No pagination.** `GET /videos` returns everything.

---

## Building something?

Open an issue and say what you are building — partly so it can be linked from
the README, and partly because the fastest way to get an internal endpoint
promoted to supported is for somebody to need it.

For changing the app itself rather than building beside it, see
[EXTENDING.md](EXTENDING.md) — adding a language, a platform, an AI model or an
export format. [ARCHITECTURE.md](../ARCHITECTURE.md) explains how the pipeline
fits together.
