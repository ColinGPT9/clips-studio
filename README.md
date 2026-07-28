<img src="docs/brand/mascot.png" alt="" width="150" align="right">

# Clips Studio — open-source AI video clipping that runs on your own PC

**Turn long streams and videos into ready-to-post Shorts, Reels, and TikToks —
entirely on your own machine.** Clips Studio is a free, open-source AI clip generator
and video editor for creators: paste a YouTube, Twitch, or Kick link and it finds the
best moments, crops them to 9:16 with the speaker kept centred, burns in word-synced
captions, and writes titles, descriptions, and hashtags.

No cloud AI. No subscription. No per-clip fees. No upload of your footage to anyone.

**Why it exists:** clipping is how people find you, and it is the part most creators
cannot afford — an editor costs money you do not have yet, and doing it yourself costs
a day you needed elsewhere. The tools built to solve that are subscriptions, so the
creators who need the help most are the ones priced out. This runs on your own machine
instead, so promoting your channel costs you nothing.

> **Local AI video processing** · **AI clip generator** · **Twitch clip generator** ·
> **Kick clip generator** · **YouTube Shorts automation** · **AI video editor** ·
> **open-source OpusClip alternative**

<!-- TODO: a screenshot of Clip Studio mid-run, and a short demo GIF, both ~1600px
     wide in docs/images/. Nothing sells a video tool like seeing it work. -->

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Features](#features)
- [What it's built for](#what-its-built-for)
- [Requirements](#requirements)
- [Install and run](#install-and-run)
- [Pick your AI model](#pick-your-ai-model)
- [Tested hardware and performance](#tested-hardware-and-performance)
- [Supported platforms](#supported-platforms)
- [Supported languages](#supported-languages)
- [GPU acceleration](#gpu-acceleration)
- [Command line use](#command-line-use)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)

---

## What it does

You give Clips Studio a long video. It gives you back a folder of finished vertical
clips you can post, plus a studio to review and fix them before you do.

It is built for the content most clipping tools handle worst: **live streams, IRL and
talking-head footage, podcasts, and gym/fitness content** — long, loosely structured
videos where the good moments are buried and a transcript alone won't find them.

## How it works

```
        Video input  (YouTube · Twitch VOD · Kick VOD · local file)
             │
             ▼
        Transcription           faster-whisper, word-level timestamps, local
             │
             ▼
        AI video analysis       audio · visual · reaction signals, computed
             │                  over the whole video in 1-second bins
             ▼
        Clip detection          local LLM scores the transcript in context of
        and scoring             those signals; signal peaks become candidates
             │                  too, so laughs and hype aren't missed
             ▼
        Video editing           YOLOv8 pose tracking → 9:16 crop that follows
             │                  whoever is speaking; trims, cuts, watermark
             ▼
        Captions/subtitles      word-synced, burned in, fully editable
             │
             ▼
        Multilingual            translate, subtitle, or dub into 19 languages
             │
             ▼
        Export                  organized folders, clean filenames, metadata
```

**Every stage above runs on your computer.** The only thing that touches the network
in a normal run is downloading the source video.

### The stages in detail

**Transcription — faster-whisper.** Word-level timestamps, running on CUDA where
available and falling back to CPU. Word timing is what makes captions land on the
right syllable and clips start on a real sentence boundary.

**AI clip scoring.** The transcript is one signal among five. Audio gives loudness
spikes, burst density (what laughter and applause look like), and silence→explosion
payoff shapes. Visual gives scene cuts, motion, and face-area changes. A reaction
signal fuses those. Each is normalized *within that video*, so a quiet podcast and a
screaming stream both produce meaningful peaks. Where real audience data exists —
Twitch chat replay measured by unique chatters, YouTube's most-replayed heatmap — it
adds a small capped bonus.

The local LLM sees the transcript **plus a timeline of those events**, so it can weigh
"the words are mild but the room exploded" correctly. Because small models score
everything in a narrow band, the finalists then get compared head-to-head in one
rerank call — relative judgment is much easier for a 7B model than absolute scoring.

**Creator Profiles.** The app builds a knowledge base per creator across their videos:
recurring topics, series, collaborators, running jokes, and ongoing storylines. That
context helps it write accurate titles and spot callbacks. It is deliberately
conservative — a catchphrase has to actually repeat before it counts, knowledge that
stops being mentioned goes dormant, and every score contribution from it is additive,
capped, and can be switched off.

**Video editor.** Non-destructive: trims, internal cuts, mutes, muted words, volume,
fades, speed, hook text, music, and watermark are stored as operations and applied at
render time. There's an AI edit chat too — *"make it 5 seconds longer"*, *"the caption
says gost, it should say ghost"* — where the model proposes and validated code applies.

**Long-form processing.** An opt-in 16:9 path using the same analysis: horizontal
clips, X/Twitter-length cuts, a best-of highlight reel, or the full stream with dead
air removed.

**Multilingual pipeline and AI dubbing.** Captions are translated and shown for review
*before* anything is written or burned, so a bad line gets fixed while it's still
fixable. A glossary protects channel names, sponsors, and in-jokes from being
translated. Dubbing uses local TTS with auditionable voices.

**Local GPU acceleration.** CUDA for detection and transcription, hardware video
encoding via NVENC/AMF/QSV (auto-detected, with CPU fallback), and the LLM on GPU
through Ollama.

## Features

- **Multimodal clip detection** — moments scored 0–100 by fusing what's said, audio
  excitement, visual activity, on-screen reactions, and hook/payoff strength. Every
  clip clearing the quality bar is kept, with no arbitrary cap.
- **Speaker-aware face tracking** — YOLOv8 pose detection keeps the subject centred,
  and in group footage the camera follows **whoever is talking**, detected from mouth
  movement. Crop-only framing: never stretched, never distorted.
- **Podcast mode** — for multi-camera footage, each shot gets its own steady crop
  centred on the person talking, so cuts land on a face with no panning.
- **Editable burned-in captions** — word-synced, in your style: colour, size, position,
  words per line, casing, or off. Fix a transcription mistake line by line before export.
- **AI edit chat** — describe what's wrong in plain language and it re-edits.
- **AI titles, descriptions, and hashtags**, all editable before export.
- **Creator Profiles** — the app learns each creator over time to pick and title clips
  better. Everything stays on your computer, and you can inspect, correct, or wipe it.
- **Multilingual publishing** — 19 languages, with review before anything is burned.
- **AI dubbing** — local text-to-speech with voice auditioning per language.
- **Long-form export** — 16:9 clips, highlight reels, or a de-duplicated stream edit.
- **Watermark and branding profiles** — per-creator defaults applied automatically.
- **Model manager** — swap the AI brain from inside the app; download, remove, and
  switch models with progress bars and no terminal.
- **In-app feedback** — bug reports with auto-collected diagnostics, no account needed.
- **Accessible UI** — keyboard focus, reduced-motion support, adjustable font and size.

## What it's built for

Everything runs on any video you give it. These are the cases where it's doing more
or less thinking than the feature list suggests, so nothing comes as a surprise:

| Content | How it does |
|---|---|
| **IRL, just chatting, podcasts, vlogs, interviews** | What it's tuned for and what gets tested on real streams before release |
| **Gaming with a facecam** | Works — the webcam and the gameplay are detected and stacked automatically. Newer than the rest, so good rather than polished |
| **Gaming with no facecam** | Weaker framing. Subject tracking follows a person; with nobody on screen it centres the shot instead. Cutting, captions and the editor are unaffected |
| **Reaction videos** | Not really its thing. Clips are chosen from what's *said*, and it can't see the video you're reacting to — so the moment that made the clip is invisible to it |

Clip selection is transcript-and-signal driven. When the funny thing is *visual only*
and nobody comments on it, expect to find it yourself in the editor.

## Requirements

- **Windows** PC (the Python engine should run on Linux/macOS; the app is developed
  and tested on Windows)
- **Python 3.10+** and **Node.js 18+**
- **[FFmpeg](https://ffmpeg.org/download.html)** on your PATH
- **[Ollama](https://ollama.com)** with a model pulled: `ollama pull gemma:7b`
- Recommended: an **NVIDIA GPU** (see [GPU acceleration](#gpu-acceleration))

## Install and run

**Most people want the installer.** Grab the latest **Web Setup** from
[Releases](../../releases) and run it. It carries the app, the Python engine, every
library, FFmpeg and the detection weights — no Python, no PATH, no terminal. A setup
wizard then checks your machine and downloads an AI model sized to your graphics card.

The one thing it doesn't bundle is [Ollama](https://ollama.com), which runs the AI
locally and manages your GPU itself. The wizard detects whether you have it and links
you to it if not.

> Windows will warn that the app is unsigned the first time you run it — click
> *More info → Run anyway*. A signing certificate is on the list.

### From source

```bash
git clone https://github.com/ColinGPT9/clips-studio
cd clips-studio
pip install -r requirements.txt

cd ui
npm install
npm run dev        # opens the Clips Studio desktop app
```

The app starts its own Python engine automatically. Paste a link in **Clip Studio**,
press *Generate clips*, and watch the progress live.

Building the installer yourself is one command — see
[CONTRIBUTING.md](CONTRIBUTING.md#building-the-windows-installer).

### With Docker — nothing else to install

For contributors. You need [Docker Desktop](https://docs.docker.com/get-started/get-docker/)
and nothing else — no Python, no Node, no FFmpeg, no PyTorch.

```bash
git clone https://github.com/ColinGPT9/clips-studio
cd clips-studio
docker compose up
```

Three services come up together:

| | |
|---|---|
| **<http://localhost:5173>** | the interface, in your browser |
| **<http://localhost:8765>** | the engine and its API |
| Ollama | the local AI, on :11434 |

Then pull a model — `docker compose exec ollama ollama pull gemma3:4b` — and
you have a working checkout.

Run the checks the same way:

```bash
docker compose run --rm engine pytest
docker compose run --rm ui npm run typecheck
```

> **Skip the ten-minute first build:** `docker compose pull` fetches a prebuilt
> engine image instead of compiling PyTorch and OpenCV locally.

The desktop shell itself still runs on your host, since Electron needs a display —
everything else is containerised. Details, and what this can't tell you, in
[docs/DOCKER.md](docs/DOCKER.md).

## Pick your AI model

Open the **Models** page to see what's installed and what your GPU can handle:

| Your hardware | Recommended model |
|---|---|
| CPU only / integrated GPU | `gemma3:4b` |
| 6–8 GB VRAM | `gemma3:4b` |
| 10–12 GB VRAM | `gemma:7b` or `gemma3:12b` |
| 16–24 GB VRAM | `gemma3:27b` |

Anything Ollama serves works — Llama, Qwen, Mistral, future models — and switching is
one click. Translation can use a *different* model than clipping (`qwen2.5:7b` is
noticeably stronger at multilingual work than Gemma).

## Tested hardware and performance

Clips Studio is developed and tested daily on this machine, so it's a useful reference
point if you're comparing your own results:

| | |
|---|---|
| **GPU** | NVIDIA GeForce RTX 3060, 12 GB VRAM |
| **CPU** | AMD Ryzen 5 5600X (6 cores / 12 threads) |
| **RAM** | 16 GB |
| **OS** | Windows 10 |
| **Clipping model** | `gemma:7b` via Ollama |
| **Transcription** | faster-whisper `large-v3-turbo` on CUDA |

Everything in this repo — the default settings, `parallel_renders: 3`, the 5-minute
analysis chunk size — is tuned for roughly this class of machine. On a bigger GPU,
raise `llm.num_ctx` and `analysis.chunk_seconds` together and move up a model size;
on a smaller one, drop to `gemma3:4b`.

If you benchmark Clips Studio on different hardware, please post it in
[Discussions](../../discussions) — real numbers from real machines help everyone size
their setup.

## Supported platforms

| Source | Support |
|---|---|
| YouTube videos | Full — H.264 is selected deliberately (AV1 roughly doubles processing time) |
| Twitch VODs | Full, including chat replay as an audience signal |
| Kick VODs | Full (Kick discards chat after a stream ends, so there's no chat signal) |
| Local video files | Full — clip your own footage before you publish it |
| Live streams | Not supported by design — VODs only |

## Supported languages

Clips can be translated, subtitled, and dubbed into **19 languages** — and the
**interface itself is translated into all 19 too**, so the app is usable in the same
languages it publishes in:

English · Spanish · Portuguese (Brazilian) · French · German · Hindi · Indonesian ·
Japanese · Russian · Arabic · Chinese (Simplified) · Vietnamese · Filipino · Turkish ·
Urdu · Bengali · Thai · Korean · Italian

Adding a language is one row in `multilingual/languages.py` plus a locale file in
`ui/src/renderer/src/locales/` — the translator and subtitle writer are
language-agnostic.

## GPU acceleration

**Video encoding** is hardware-accelerated automatically on all three vendors — NVIDIA
(NVENC), AMD (AMF), and Intel (QSV). The engine test-encodes a frame with each at
startup and uses the first that actually works, falling back to CPU. Force a choice
with `video.encoder` in `config/settings.yaml`.

**Detection and transcription** are fastest with CUDA. Out of the box `pip install
torch` gives you the **CPU-only** build — for NVIDIA:

```bash
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

AMD GPU owners: tracking and transcription run on CPU on Windows — still fully
functional, just slower. Your GPU is still used for video encoding via AMF and for the
LLM through Ollama, which supports AMD itself.

## Command line use

Everything works without the desktop app:

```bash
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID"   # one video
python main.py models                                               # list/switch models
python main.py status                                               # processing state
python main.py serve                                                # just the API engine
```

Settings live in [config/settings.yaml](config/settings.yaml) — the top of the file is
a short quick-setup block, everything advanced is below it. Every LLM prompt is a plain
text file in [config/prompts/](config/prompts/), so you can tune how clips are scored
without touching Python.

## Architecture

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full design: the pipeline, the
scoring engine, tracking, creator intelligence, the API surface, and the design rules
that keep it modular.

## Contributing

Contributions are welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for dev setup
and pull request expectations, and **[SECURITY.md](SECURITY.md)** to report a
vulnerability. Issues labelled [`good-first-issue`](../../labels/good-first-issue) are
small and well-scoped if you want somewhere to start.

The website lives in [site/](site/) and is published to GitHub Pages and a Hugging
Face Space. See [docs/MIRRORS.md](docs/MIRRORS.md) — **GitHub is where work
happens**, and the website mirror is force-pushed one way, so a commit made on it
will be overwritten.

## Roadmap

1. **Android companion app** — clip from a phone. Twitch, Kick and local video files
   only, to comply with Play Store policy.
2. **Remote rendering** — hand the rendering work to another machine, so a long stream
   doesn't tie up the computer you're using.
3. **Automated posting** *(possible future plan)* — channel monitoring, scheduling and
   auto-upload are coded in the repo but **dormant and not exposed in the UI**. The
   upload path hasn't been tested end-to-end against a real server, because it needs
   your own API credentials and Google's API audit to post publicly. Posting to TikTok
   and Instagram belongs here too — export alone adds little, since the work is in the
   posting, and that needs a server this app deliberately doesn't have yet.
4. **Gaming and reaction layouts** *(possible future plan)* — a dedicated layout for
   gameplay-with-facecam and for reaction videos, composing the creator's webcam and
   what they're reacting to into one vertical frame.

   Prototyped and **set aside on purpose**. Automatically telling which region is the
   webcam, which is the game, and which is chat is not reliable enough on real footage
   to ship: every creator's layout is different and many change it mid-stream. Marking
   the regions by hand works, but that cost lands on the user for every single video.

   The core pipeline — talking-head, IRL, gym, podcast — is what this app is for, and
   it's kept free of that complexity. If there's real demand, this returns as a
   self-contained mode that cannot affect the standard path.
5. **Voice cloning for dubbing** *(last on this list on purpose)* — dubbing today uses
   a preset local voice. Speaking translations in the creator's **own** voice needs a
   cloning model, and every credible local one pulls in its own PyTorch build: on a
   machine set up for clipping that downgrades torch to a CPU-only build and silently
   strips GPU acceleration from tracking and transcription. Breaking clipping to add
   dubbing is a bad trade.

   Realistically this waits for consumer hardware to catch up, or ships as a separate
   optional install with its own Python environment so the clipping one is never
   touched. Model licences need checking too — several of the best-sounding ones are
   non-commercial, and this app's users monetize their videos.

## License

MIT — see [LICENSE](LICENSE). Built to be modified: swap models, tune prompts, add
platforms.
