# Architecture — YouTube Clips Automation

Fully local, open-source pipeline that finds viral moments with a local LLM and
multimodal signal analysis, and renders face-tracked, captioned vertical Shorts.
No paid inference. The only external API is YouTube's (uploads only).

> **Status (2026-06-12):** the core pipeline below is built and working, and the
> clip engine has been upgraded to multimodal scoring (transcript + audio + visual
> + reaction signals — see DESIGN-V2.md). Current focus is clip quality and the
> desktop app. The automation layer (RSS monitoring, scheduling, auto-upload) is
> built and tested but **deliberately dormant**: it returns to the roadmap after
> Twitch and Kick VOD support lands (see §7).

---

## 1. System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR (daemon)                      │
│   schedule loop · per-video job state machine · retry · logging     │
└───────┬─────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────┐     RSS feed poll (free, no quota)
│  SOURCE LAYER    │◄──── https://youtube.com/feeds/videos.xml?channel_id=...
│  sources/youtube │
└───────┬──────────┘
        │ new video ID not in state DB
        ▼
┌──────────────────┐
│   DOWNLOADER     │  yt-dlp → data/downloads/{video_id}.mp4
└───────┬──────────┘
        ▼
┌──────────────────┐
│  TRANSCRIBER     │  faster-whisper (local, GPU/CPU)
│                  │  → word + segment timestamps (JSON)
└───────┬──────────┘
        ▼
┌──────────────────────────────────────────────┐
│  HIGHLIGHT ANALYZER                          │
│  chunk transcript → LLM scores candidates    │
│  (0–100 virality) → dedup → top-N clips      │
└───────┬──────────────────────────────────────┘
        │ uses                                 ┌─────────────────────┐
        ├──────────────────────────────────────┤  LLM ABSTRACTION    │
        │                                      │  llm/base.py        │
        ▼                                      │  ├ OllamaBackend    │
┌──────────────────────────────────────────────┐  (Gemma 7B default)│
│  CLIP RENDERER (per clip)                    │  ├ (LlamaBackend =  │
│  1. tracker.py  YOLOv8+OpenCV → crop path    │  │  Ollama w/ other │
│  2. cropper.py  FFmpeg 9:16 render           │  │  model tag)      │
│  3. captions.py burn-in word-level captions  │  └ future: cloud    │
└───────┬──────────────────────────────────────┘─────────────────────┘
        ▼
┌──────────────────┐
│  METADATA GEN    │  LLM → title, description, hashtags (JSON)
└───────┬──────────┘
        ▼
┌──────────────────┐
│  PUBLISHER       │  YouTube Data API v3 (OAuth2, resumable upload)
│ publish/youtube  │  → logs upload ID
└───────┬──────────┘
        ▼
┌──────────────────┐
│   STATE DB       │  SQLite: videos seen, clips made, uploads, errors
└──────────────────┘  → prevents duplicates / enables resume
```

External dependencies at runtime: **YouTube RSS feed** (watch), **yt-dlp** (download),
**YouTube Data API** (upload). Everything else — Whisper, Gemma, YOLOv8, FFmpeg — runs
on the local machine.

---

## 2. Repository / Module Structure

```
youtube-clips-automation/
├── main.py                     # CLI: `run` (daemon), `process <url>` (one-shot), `auth`
├── config/
│   ├── settings.example.yaml   # copied to settings.yaml by the user
│   └── prompts/                # LLM prompt templates as plain text files
│       ├── classify.txt        #   video-type classification
│       ├── score_clips.txt     #   virality scoring (the rating system)
│       └── metadata.txt        #   title/description/hashtags
├── core/
│   ├── pipeline.py             # stage orchestration for one video
│   ├── scheduler.py            # poll loop, queue, retries
│   ├── state.py                # SQLite wrapper (videos, clips, uploads)
│   └── models.py               # dataclasses: Video, Segment, ClipCandidate,
│                               #   RenderedClip, ClipMetadata
├── sources/                    # ── platform-agnostic ingestion ──
│   ├── base.py                 # VideoSource interface: poll(), download()
│   └── youtube.py              # RSS poller + yt-dlp downloader
├── transcription/
│   └── transcriber.py          # faster-whisper wrapper → Segment list
├── llm/                        # ── swappable model layer ──
│   ├── base.py                 # LLMBackend interface
│   ├── ollama_backend.py       # Gemma/Llama/anything Ollama serves
│   └── registry.py             # config string → backend instance
├── analysis/
│   ├── highlights.py           # chunking, scoring calls, dedup, top-N
│   └── metadata.py             # title/desc/hashtag generation + validation
├── video/                      # ── rendering, independent of analysis ──
│   ├── tracker.py              # YOLOv8 + OpenCV → smoothed crop path
│   ├── cropper.py              # FFmpeg vertical 9:16 render from crop path
│   └── captions.py             # ASS subtitle generation + burn-in
├── publish/
│   ├── base.py                 # Publisher interface: upload(clip, metadata)
│   └── youtube_shorts.py       # Data API v3 OAuth + resumable upload
├── data/                       # runtime artifacts — gitignored
│   ├── downloads/  transcripts/  clips/  logs/
│   └── state.db
├── requirements.txt
├── README.md
└── ARCHITECTURE.md             # this file
```

Design rules that keep it modular:

- **Stages communicate only through the dataclasses in `core/models.py`** (and files on
  disk). The analyzer never touches video; the tracker never reads transcripts.
- **`analysis/` depends on `llm/base.py` only** — never on a concrete backend.
- **`sources/` and `publish/` are plugin folders.** Adding Twitch means adding one file
  that implements `VideoSource`; nothing downstream changes.
- **Prompts are data, not code.** Stored as text files so users can tune the rating
  system without touching Python, and so a model swap can ship its own prompt variants.

---

## 3. Data Flow Between Components

| Step | Producer → Consumer | Artifact |
|------|--------------------|----------|
| 1 | RSS poller → state DB | new `video_id` (status: `queued`) |
| 2 | yt-dlp → disk | `downloads/{id}.mp4` (status: `downloaded`) |
| 3 | faster-whisper → disk | `transcripts/{id}.json` — segments with `start`, `end`, `text`, word timestamps |
| 4 | highlights.py ↔ LLM | `ClipCandidate[]`: `{start, end, score, hook, reason}` (status: `analyzed`) |
| 5 | tracker.py → cropper.py | crop path: `[(t, center_x), ...]` per clip |
| 6 | cropper.py + captions.py → disk | `clips/{id}/clip_{n}.mp4`, 1080×1920 ≤ 60 s (status: `rendered`) |
| 7 | metadata.py ↔ LLM | `ClipMetadata`: `{title, description, hashtags}` |
| 8 | youtube_shorts.py → YouTube | upload ID logged to state DB (status: `uploaded`) |

Every status transition is written to SQLite **before** the next stage starts, so a crash
mid-pipeline resumes at the failed stage instead of redoing (or worse, re-uploading) work.

---

## 4. Clip Scoring Design (adapted from SamurAIGPT/AI-Youtube-Shorts-Generator)

The reference repo's rating system, kept intact, with adaptations for local models and
unattended operation.

### 4.1 Two-pass LLM analysis

**Pass 1 — classify.** One short LLM call labels the video (podcast / commentary /
gameplay / tutorial / vlog) and its content density. The label selects scoring-prompt
emphasis (e.g., gameplay weights reaction peaks; podcasts weight opinion bombs).

**Pass 2 — score.** The transcript is sent chunk-by-chunk with the virality framework.
The model returns strict JSON, one entry per candidate:

```json
{
  "clips": [
    {
      "start": 124.5,
      "end": 162.0,
      "score": 87,
      "hook": "He just admitted the entire run was luck",
      "reason": "Unexpected confession creates a curiosity gap"
    }
  ]
}
```

**Virality framework (same criteria as the reference repo):** hook moments and strong
opening lines · emotional peaks · opinion-driven statements ("opinion bombs") ·
revelations/disclosures · conflict or tension · quotable lines · story-structure peaks ·
practical/actionable value. Score is **0–100 predicted viral potential**.

### 4.2 Chunking (same constants as the reference repo)

- Videos longer than **1800 s** are split into **1200 s** chunks with **60 s overlap**,
  scored independently, results merged.
- Overlap means boundary moments are never lost between chunks.

### 4.3 Post-processing (deterministic Python, not LLM)

1. **Validate** timestamps against the transcript range; drop hallucinated ranges.
2. **Snap** start/end to the nearest segment boundary so clips begin on a sentence.
3. **Enforce duration** 15–60 s (Shorts limit); extend or trim symmetrically.
4. **Deduplicate**: if two candidates overlap > 50 %, keep the higher score (reference
   repo's "collapse by score").
5. **Threshold + top-N**: discard below `min_score` (default 70), keep `max_clips_per_video`
   (default 3).

### 4.4 Local-model robustness (the adaptation that matters)

Gemma 7B is less reliable at structured output than frontier models, so the analyzer —
not the backend — owns resilience:

- JSON extracted with a tolerant parser (strip code fences, find first `{…}` block).
- On parse failure: one retry with a "return only valid JSON" reminder; on second
  failure the chunk is skipped and logged, never crashing the pipeline.
- Smaller chunk option in config for models with short context windows.

This logic lives in `analysis/highlights.py`, so swapping to a stronger model later
requires zero changes — the guards just stop triggering.

### 4.5 LLM abstraction

```
LLMBackend (llm/base.py)
  generate(prompt: str, *, json_mode: bool = False) -> str
  name -> str                      # for logging, e.g. "ollama/gemma:7b"
```

`registry.py` maps a config string to a backend: `ollama/gemma:7b` today,
`ollama/llama3.1:8b` tomorrow, `gemini/...` or `anthropic/...` later — each cloud
provider is one new ~50-line file implementing `generate()`. Nothing in `analysis/`
or anywhere else imports a concrete backend. Model swap = edit one line of YAML:

```yaml
llm:
  backend: ollama/gemma:7b      # any Ollama tag works; swap freely
  temperature: 0.4
```

---

## 5. Face Tracking Design (YOLOv8 + OpenCV)

A standalone module: input = source video + clip window, output = a crop path. It knows
nothing about transcripts, scores, or uploads.

**Pipeline inside `video/tracker.py`:**

1. **Sample** frames at ~5 fps over the clip window (full fps is wasted — subjects don't
   teleport between 200 ms samples).
2. **Detect** with `yolov8n.pt` (person class) — small, fast, runs on CPU if needed.
   Optionally a YOLOv8-face model for tighter framing when faces are large in frame.
3. **Select primary subject** per frame: score = detection confidence × box area ×
   persistence (IoU with previous frame's chosen box). This keeps tracking locked on
   the streamer when guests or game characters appear.
4. **Smooth** the chosen center-x over time (exponential moving average + dead-zone:
   ignore movements under ~5 % of frame width). This kills jitter and the "drunk camera"
   effect.
5. **Emit crop path**: `[(t, center_x)]` keyframes, interpolated linearly.
6. **Fallback**: zero detections (gameplay-only sections) → static center crop.

**Rendering in `video/cropper.py`:** FFmpeg extracts the clip window, then a crop filter
applies the path (piecewise `crop` expressions for v1 — simple and debuggable). Output:
1080×1920 H.264 + AAC. Captions (`video/captions.py`) are generated as ASS subtitles
from word-level Whisper timestamps (3–4 word groups, current word highlighted) and
burned in during the same FFmpeg pass — one encode, not two.

Tracking is intentionally decoupled so it can later be upgraded (ByteTrack IDs, speaker
diarization to follow whoever is talking) without touching anything else.

---

## 6. Publishing & State

**YouTube upload** (`publish/youtube_shorts.py`): OAuth2 installed-app flow (one-time
browser consent, refresh token stored locally), resumable upload via Data API v3,
`#Shorts` appended to description, configurable `privacyStatus` (default **unlisted**
until you trust the output).

Two real-world constraints the design plans around:

- **Quota**: an upload costs 1,600 units of the default 10,000/day quota → **max 6
  uploads/day** out of the box. The scheduler enforces a configurable daily upload cap
  and queues the rest. Watching uses RSS, costing zero quota.
- **API audit**: videos uploaded by unverified API projects are locked private until
  Google audits the project. Day one this means uploads land as private/unlisted —
  expected behavior, documented in the README, not a bug.

**State DB** (SQLite, `core/state.py`): tables `videos` (id, channel, status, timestamps),
`clips` (video_id, start, end, score, render path), `uploads` (clip_id, youtube_id,
uploaded_at). Guarantees: a video is never processed twice, a clip is never uploaded
twice, and any crash resumes from the last completed stage.

---

## 7. Extension Plan

### Phase 1 — core pipeline (DONE)
Single video → Gemma via Ollama → 9:16 face-tracked captioned clips with titles,
descriptions, and hashtags. One-shot CLI mode. (The daemon/upload code also exists
but is dormant — see Phase 4.)

### Phase 2 — clip quality + desktop studio (CURRENT)
- Multimodal scoring engine: transcript + audio + visual + reaction fusion (DONE —
  see DESIGN-V2.md for the full design)
- Face tracking v2: multi-person, gameplay+webcam layouts, anti-jitter
- Desktop app: Electron + React studio over a local FastAPI service
- Windows installer for non-technical users

### Phase 3 — more sources (before any upload automation)
| Platform | How |
|----------|-----|
| Twitch VODs | `sources/twitch.py` implements `VideoSource`. Polls the channel's VOD list; yt-dlp already downloads Twitch VODs. Everything downstream is untouched. |
| Kick VODs | `sources/kick.py`, same pattern (yt-dlp supports Kick). |
| Multiple channels | Config accepts a list; scheduler round-robins. |

### Phase 4 — automation returns (after Twitch/Kick)
The already-built RSS monitoring, daily scheduling, and YouTube Shorts upload
reactivate here, now covering all supported sources. Requires the user's YouTube
API credentials + Google's API audit for public posting.

### Phase 5 — more destinations
| Platform | Notes |
|----------|-------|
| TikTok / Instagram Reels | New `Publisher` implementations. Their official APIs are restrictive (TikTok requires app review; Instagram requires a Business account + Graph API). Interim option: a `LocalExportPublisher` that drops finished clips + metadata JSON into a folder for manual/semi-automated posting. |

### Ongoing — quality upgrades (each is one module swap)
- Stronger local models (bigger Gemma 3 sizes, Llama 3.x) — config change only.
- Optional cloud backends for users who want them — new file in `llm/`.
- Speaker diarization → track whoever is speaking in multi-person podcasts.
- Dedicated facial-expression and laughter-classifier models slotting into the
  reaction signal (interface already in place).

### Scaling path (when one PC isn't enough)
The state DB + file-artifact handoff between stages means stages can later become
queue workers (e.g., Redis + workers, transcription on the GPU box, rendering
elsewhere) without redesign — the contracts between stages don't change.

---

## 8. Suggested Config Surface (`config/settings.yaml`)

```yaml
channels:
  - id: "UCxxxxxxxx"            # channel to monitor
poll_interval_minutes: 15

llm:
  backend: ollama/gemma:7b       # swappable: any ollama tag, future cloud backends
  temperature: 0.4

whisper:
  model: small                   # tiny/base/small/medium/large-v3
  device: auto                   # cuda if available, else cpu

clips:
  min_score: 70
  max_clips_per_video: 3
  min_duration: 15
  max_duration: 59

tracking:
  detector: yolov8n              # person model; yolov8n-face optional
  sample_fps: 5

upload:
  privacy: unlisted              # private/unlisted/public
  daily_limit: 6                 # YouTube quota: 1600 units per upload
```
