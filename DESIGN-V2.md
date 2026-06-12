# V2 Design — Multimodal Clip Engine + Desktop Studio

Direction change (2026-06-12): RSS automation, upload automation, and scheduling are
**paused, not removed** — they're built, tested, and dormant in the codebase until
YouTube API credentials exist. All effort moves to (1) clip quality via multimodal
scoring and (2) a professional desktop app. ARCHITECTURE.md still describes the
pipeline core; this document describes what gets built on top of it.

---

## 1. Development priority order

| # | Phase | Why this order |
|---|-------|----------------|
| 1 | **Multimodal scoring engine** (audio + visual features, fusion scorer, enriched Gemma prompts) | Biggest clip-quality lever. Pure Python, no UI dependency, testable on existing videos today. |
| 2 | **Face tracking v2** (multi-person, gameplay+webcam layouts, anti-jitter) | Second-biggest visible quality factor. Also pure Python. |
| 3 | **Metadata v2** (slug filenames, social caption field) | Small, finishes the per-clip asset bundle. |
| 4 | **Python backend service** (FastAPI wrapping the pipeline) | The contract the UI builds against. Must exist before any Electron work. |
| 5 | **Electron Clip Studio MVP** (paste URL → generate → preview → edit → export) | The core user loop. Ship this before the dashboard. |
| 6 | **Dashboard + model manager UI** | Wraps existing CLI capabilities in UI. |
| 7 | **Windows installer / packaging** | Last build phase — packaging anything earlier means packaging it twice. |
| 8 | **Twitch + Kick VOD sources** | New `VideoSource` implementations (yt-dlp downloads both already). |
| 9 | **Upload automation + RSS + scheduling** | Deliberately last: reactivated only after Twitch/Kick support lands. The code is already built and dormant. |

Rule of thumb baked into this order: **quality before chrome** — phases 1–3 improve
every clip ever produced; phases 4–7 improve how people interact with them.

## 2. What to build next (immediately)

`analysis/audio_features.py` first. Audio is the cheapest, highest-signal channel that
the system currently ignores completely: laughter, shouting, hype moments, and crowd
reactions all live in the loudness envelope, and extracting it takes seconds per video
(FFmpeg → PCM → numpy). Then `analysis/visual_features.py`, then `analysis/fusion.py`
to combine everything, then the enriched Gemma prompt.

## 3. New modules

```
analysis/
├── audio_features.py    # loudness envelope, spikes, burst density, excitement proxy
├── visual_features.py   # scene cuts, motion intensity, face metrics over time
└── fusion.py            # signal normalization, candidate generation, weighted scoring

video/
└── tracker.py           # upgraded in place (multi-person, layouts, hysteresis)

server/                  # NEW — local API for the desktop app
├── api.py               # FastAPI app: jobs, clips, models, system stats, media
├── jobs.py              # job queue + worker thread + progress events
└── events.py            # WebSocket progress broadcasting

ui/                      # NEW — Electron + React + TypeScript app
├── electron/            # main process: window, python lifecycle, updates
└── src/                 # renderer: Dashboard, ClipStudio, Models, Settings
```

Existing modules keep their roles. Nothing in `sources/`, `publish/`, or
`core/scheduler.py` changes — dormant but intact.

---

## 4. Multimodal scoring system

### 4.1 Principle

Every signal is computed over the **whole video in 1-second bins**, then normalized to
0–1 by percentile rank within that video ("how unusual is this second *for this video*"
— a quiet podcast and a screaming stream both get meaningful peaks). The transcript
becomes one input among several, exactly as requested.

### 4.2 Signal extractors (all local, all open-source)

**AUDIO** (`audio_features.py` — FFmpeg decode → numpy):
- RMS loudness envelope + **spike score** (loudness vs. rolling 30s median — catches
  shouts, cheers, hype moments)
- Burst density (rapid onset clusters — laughter, applause, rapid-fire excitement)
- High-band energy ratio + zero-crossing rate (laughter/cheering proxy: broadband,
  noisy, unlike speech)
- Silence→explosion transitions (classic payoff shape)

**VISUAL** (`visual_features.py` — OpenCV at 2–4 fps samples):
- Scene cuts (HSV histogram distance between samples)
- Motion intensity (mean absolute frame difference — cheap, robust optical-flow proxy)
- Brightness/flash events (gameplay kills, explosions, scene flashes)
- Face metrics reusing YOLOv8: face count, max face area, **face-area delta**
  (sudden zoom-ins / lean-ins = editor or streamer emphasizing a moment)

**REACTION** (fusion of the above, honestly labeled):
- v1 reaction score = face present × (face-area delta + motion near face) × audio
  excitement at the same instant. This is a *proxy*, and the design says so: true
  facial-expression recognition needs a dedicated FER model, which is not in the
  approved tool list. The extractor interface is built so an open-weights expression
  or laughter-classifier model can slot in later as a drop-in upgrade without touching
  fusion — same pattern as the swappable LLM.

**TEXT + ENGAGEMENT** (Gemma, as today, but with multimodal context — see 4.4).

### 4.3 Fusion (`analysis/fusion.py`)

Candidate generation gets a second source:

1. **Transcript candidates** — Gemma's picks (existing path).
2. **Signal-peak candidates** — windows where the combined non-text signal exceeds the
   90th percentile, snapped to transcript sentence boundaries, duration-enforced.
   *This is what catches the laugh, the rage moment, the clutch play that the
   transcript alone describes blandly or not at all.*

Both pools are scored identically:

```
final = 0.30*text + 0.20*visual + 0.20*reaction + 0.20*audio + 0.10*engagement
```

- `text` and `engagement` come from Gemma (engagement = its hook/payoff/quotability
  judgment, asked for explicitly in the prompt).
- `visual`, `reaction`, `audio` are the mean of each normalized signal over the
  candidate window, with a small bonus when the peak sits in the first 3 seconds
  (hook position matters).
- Weights live in `settings.yaml` under `scoring:` — tunable per content type
  (gameplay channels will want audio/visual higher; talking-head finance content
  will want text higher).

Deduplication, snapping, and duration enforcement stay exactly as built (they already
work). The 0–100 final score keeps the same scale users already see.

### 4.4 Gemma sees events, not just words

Each analysis chunk's prompt gains a compact EVENTS timeline derived from the signals:

```
TRANSCRIPT:
[244.1 - 247.9] dude no way he actually hit that
...
EVENTS:
[244s] AUDIO spike (99th pct, burst cluster - laughter/cheering likely)
[245s] SCENE CUT + high motion
[251s] face zoom-in
```

This is the practical local version of "multimodal LLM analysis": Gemma can't watch
pixels, but it can absolutely reason over a fused text description of what the audio
and video are doing — and that's how it weighs "transcript says something mild but the
room exploded" correctly. The same events feed the engagement score.

---

## 5. Face tracking v2

Current state: single primary subject, EMA + dead-zone smoothing, center fallback.
Known weakness: lag on fast close-ups; no layout awareness.

Upgrades, in order:

1. **Tracked IDs instead of per-frame picks** — use ultralytics' built-in
   `model.track()` (ByteTrack) so subjects keep identities across frames. Target
   switching only happens when a *different* ID dominates for ≥1.5s (hysteresis) —
   eliminates ping-ponging between two people mid-conversation.
2. **Multi-person framing** — if two tracked subjects both persist and fit, frame
   their midpoint; otherwise follow the dominant (largest × most-persistent) one.
3. **Gameplay + webcam layout detection** — if the chosen face stays inside one small
   static region for most of the clip while the rest of the frame has high motion,
   it's a facecam stream. Switch to the classic gaming-Shorts layout: webcam crop
   stacked on top (~35% height), gameplay center crop below (~65%). Both crops are
   fixed-position (no tracking jitter possible in this mode by construction).
4. **Anti-jitter, hardened** — keep EMA + dead-zone, add a max-pan-speed clamp
   (the camera may never move faster than ~15% of frame width per second) and raise
   detection sampling to 8–10 fps for fast content.
5. **Face-model option** — `tracking.detector` already accepts any weights file; a
   YOLOv8-face model improves close-up framing where person boxes are useless.

Output contract is unchanged and non-negotiable: crop windows are exactly 9:16,
only reposition (never reshape), uniform scale to 1080×1920 — distortion stays
impossible by construction.

---

## 6. Python backend service (`server/`)

FastAPI + uvicorn on `127.0.0.1:8765`, bound to localhost only. The pipeline code is
already modular; the server wraps it without forking logic.

```
POST   /jobs                {url | local_path, options}     -> job id
GET    /jobs/{id}                                            -> status, stage, progress
WS     /ws                                                   -> live progress events
GET    /videos                                               -> processed videos
GET    /videos/{id}/clips                                    -> clips + metadata + scores
PATCH  /clips/{id}          {start?, end?, title?, caption?, description?, hashtags?}
POST   /clips/{id}/render                                    -> re-render (new timestamps/captions)
POST   /clips/{id}/export   {folder}                         -> copy with final filename
POST   /export/batch        {clip_ids, folder}
GET    /media/{path}                                         -> serve clip files for <video> preview
GET    /models                                               -> installed + active + VRAM estimates
POST   /models/pull         {tag}                            -> streamed download progress
DELETE /models/{tag}
POST   /models/activate     {tag}
GET    /system/stats                                         -> CPU, RAM, GPU, VRAM, disk
GET/PATCH /settings                                          -> settings.yaml as JSON
```

Integration mechanics:
- **One worker thread** processes jobs sequentially (GPU/CPU contention makes parallel
  video jobs pointless on consumer hardware); queue in SQLite so it survives restarts.
- Pipeline stages emit progress callbacks → broadcast over the WebSocket → UI shows
  "Transcribing 41%… Analyzing chunk 3/7… Rendering clip 2/3".
- `GET /system/stats` via `psutil` + `pynvml` (both pip-installable, local).
- Electron spawns the backend as a child process at launch, health-checks
  `GET /health`, and kills it on exit. In dev they run separately.

---

## 7. Electron + React + TypeScript application

Stack: **Electron + Vite + React + TypeScript + Tailwind** (fast iteration, no CSS
framework lock-in), Zustand for state, the local API for everything — the renderer
never touches Python or the filesystem directly (clean security model: context
isolation on, no node integration in the renderer).

```
ui/src/
├── pages/
│   ├── Dashboard.tsx        # section 1
│   ├── ClipStudio.tsx       # section 2 — the heart of the app
│   ├── Models.tsx           # model manager
│   └── Settings.tsx
├── components/
│   ├── ClipCard.tsx         # vertical thumbnail, score badge, title
│   ├── ClipEditor.tsx       # preview + trim + captions + metadata
│   ├── CaptionEditor.tsx    # per-line text/timing editing
│   ├── JobProgress.tsx      # live stage progress from WebSocket
│   └── SystemStats.tsx      # CPU/GPU/RAM/disk widgets
└── lib/api.ts               # typed client for the FastAPI backend
```

### Visual theme — navy + sky blue

Dark-first interface (video apps live in dark mode) built on a navy base with sky
blue as the single accent. Defined as Tailwind design tokens so every component
inherits it:

| Token | Value | Used for |
|---|---|---|
| `bg-base` | `#0A1628` (deep navy) | app background |
| `bg-surface` | `#13243D` (navy) | cards, panels, sidebar |
| `bg-raised` | `#1C3354` | hover states, inputs, modals |
| `accent` | `#38BDF8` (sky) | primary buttons, active nav, progress bars, links |
| `accent-strong` | `#0EA5E9` | button hover, selection highlights |
| `text-primary` | `#F1F5F9` | headings, body |
| `text-muted` | `#94A3B8` (blue-grey) | labels, secondary text |
| `success` / `warn` / `error` | `#34D399` / `#FBBF24` / `#F87171` | status chips, score badges |

Score badges tint from sky (high) through blue-grey (low) so the results grid reads
at a glance. Light mode ships later by swapping token values only.

### Dashboard (Section 1)
Top row: system widgets (CPU %, GPU %, VRAM, storage used by `data/`), active model
chip (click → Models page). Main area: processed-videos table (thumbnail, title, date,
clip count, status) and a live log panel fed by the WebSocket. Upload queue/history
panels are stubbed behind a "coming soon" flag — the DB schema for them already exists.

### Clip Studio (Section 2) — the core creator loop
1. **Input bar**: paste a YouTube URL or drop a local video file. Options popover:
   number of clips, length range, style preset (which maps to scoring weights +
   caption style), prompt override (editable scoring prompt), model picker.
2. **Processing view**: stage-by-stage progress with per-stage timing.
3. **Results grid**: vertical 9:16 cards, each showing the burned preview frame,
   score badge, title, duration. Sort by score. "Regenerate" re-runs analysis with
   different temperature/seed; "More clips" lowers the threshold.
4. **Clip editor** (click a card):
   - `<video>` preview (served from `/media/`)
   - Timeline strip with draggable start/end handles snapped to sentence boundaries;
     "re-render" applies new timestamps
   - **Caption editor**: caption lines as editable rows (text + timing); preview
     overlays captions live on the video; re-render burns the edited version
   - **Metadata panel**: filename slug, title, caption, description, hashtags — all
     editable text fields, "regenerate with AI" per field
   - Export: download to Downloads, export to chosen folder, or add to batch
5. **Batch export bar**: select multiple cards → export all to a folder.

### Model management (Section: Models)
Backed entirely by Ollama's local HTTP API (`/api/tags`, `/api/pull` streaming,
`/api/delete`) — the UI is a thin skin over it, no terminal ever:
- Installed list: name, size on disk, **active** badge
- Curated catalog: Gemma 3 4B/12B/27B, Llama 3.1 8B, etc., each with estimated VRAM
  requirement and a one-line "what you get" description; entries the user's detected
  VRAM can't fit are shown greyed with a warning, not hidden
- Download button with streamed progress bar; Remove button; "Use this model" button
  (calls the existing `switch_model`, which edits one line of settings.yaml)
- The catalog is a JSON file in the repo — adding future models is a data change

### Metadata / filenames (applies engine-side, edited UI-side)
`analysis/metadata.py` gains two fields, matching the requested example:
- **filename slug**: lowercase-hyphenated from the title (`cra-tax-rules-explained.mp4`),
  deduplicated with `-2`, `-3` suffixes; used at export time (internal storage keeps
  timestamp names so the dedup DB key stays stable)
- **caption**: 1–2 sentence social-post text, distinct from the description
All five assets (filename, title, caption, description, hashtags) are generated per
clip, stored in the clips table, and editable in the UI before export.

---

## 8. Windows packaging strategy

The recommendation, having weighed the options:

**electron-builder (NSIS installer) + PyInstaller one-dir backend + bundled FFmpeg,
with Ollama and models handled by a first-run setup wizard.**

- **Backend**: freeze the Python service with PyInstaller in *one-dir* mode (one-file
  mode and PyTorch don't mix — extraction at every launch is slow and fragile). Ship
  it in the installer as an `extraResource`; Electron spawns `backend/api.exe`.
  Ship **CPU PyTorch** by default (installer ~1.5–2.5 GB; CUDA wheels would triple it);
  the setup wizard offers an optional "enable NVIDIA GPU acceleration" step that swaps
  in CUDA wheels for users who want them.
- **FFmpeg/ffprobe**: bundle the static .exe binaries (license-compatible, ~100 MB).
- **Ollama**: do *not* bundle. First-run wizard detects it (`/api/tags` probe); if
  missing, it downloads and runs the official Ollama installer with one click, then
  pulls the default model through the Models UI with a progress bar.
- **Whisper model**: auto-downloads on first transcription already; the wizard
  pre-fetches it so the first real video isn't slow.
- **First-run wizard** therefore: ① check/install Ollama → ② pick + download an LLM
  sized to detected VRAM → ③ optional GPU acceleration → ④ done. No terminal, no
  Python, no YAML — every requirement in the packaging goals is covered by these
  four screens plus the Models page.
- Auto-updates later via electron-updater + GitHub Releases (the repo is already
  public — releases are free CDN).

## 9. How clip quality competes with cloud clipping services while staying local

Honest assessment of the gap and the levers, in order of impact:

1. **Candidate recall (the multimodal engine, phase 1).** Cloud clipping services'
   biggest real advantage is finding moments that transcripts miss. Signal-peak
   candidates close most of that gap: laughter, hype, and visual chaos become
   first-class clip sources.
2. **Ranking discrimination (the rerank pass).** Local Gemma scores cluster (we
   measured: everything ~88). Fix: after fusion produces the top ~10 candidates,
   one extra Gemma call ranks them *against each other* in a single prompt
   ("order these by viral potential, best first"). Relative judgment is much easier
   for small models than absolute scoring, and it costs one LLM call per video.
3. **Model headroom.** The swap layer means quality scales with the user's GPU —
   Gemma 3 27B reasons about hooks dramatically better than Gemma 7B. Frontier-model
   users (who don't mind the API) can be served later by one new backend file.
4. **Human-in-the-loop Studio.** Creators review clips before posting no matter what
   tool they use. A fast review/edit/export loop means the model only has to get clips
   *into the top 10*, not perfect — the human picks the winners. This is the cheapest
   quality multiplier that exists.
5. **Polish parity**: word-synced captions (done), clean tracking (phase 2), smart
   metadata (phase 3).

Honest ceiling: a fully-local 7B model will not match a frontier model's editorial
taste in absolute terms. The combination of multimodal recall + relative reranking +
bigger local models + human review is how a local tool competes anyway — on control,
privacy, cost ($0/clip vs. subscription), and unlimited volume.

## 10. What comes before RSS / scheduling / upload

Everything above, in the phase order of section 1 — including **Twitch and Kick VOD
support, which deliberately ships before upload automation comes back**. The order is
quality engine → desktop studio → packaging → multi-platform sources → automated
posting.

The dormant automation systems lose nothing by waiting — they're committed, tested,
and behind flags (`auto_upload: false`, no channels configured = daemon idles). When
their turn comes, reactivation is: add channel, `python main.py auth`, flip
`auto_upload: true` — or, by then, a few clicks in the Dashboard's upload panel.
