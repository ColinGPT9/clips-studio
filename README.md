# Clips Studio — open-source AI video clipping that runs on your own PC

**Turn long streams and videos into ready-to-post Shorts, Reels, and TikToks —
entirely on your own machine.** Clips Studio is a free, open-source AI clip generator
and video editor for creators: paste a YouTube, Twitch, or Kick link and it finds the
best moments, crops them to 9:16 with the speaker kept centred, burns in word-synced
captions, and writes titles, descriptions, and hashtags.

No cloud AI. No subscription. No per-clip fees. No upload of your footage to anyone.

> **Local AI video processing** · **AI clip generator** · **Twitch clip generator** ·
> **Kick clip generator** · **YouTube Shorts automation** · **AI video editor** ·
> **open-source OpusClip alternative**

<!-- TODO: add a screenshot of Clip Studio here, and a short demo GIF above it.
     Recommended: 1600px wide PNG in docs/images/, referenced as
     ![Clips Studio](docs/images/clip-studio.png) -->

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Features](#features)
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
- **Speaker-aware face tracking** — YOLOv8 pose detection keeps the subject centred.
  In group footage the camera follows **whoever is talking**, not whoever is biggest.
  Crop-only framing: never stretched, never distorted.
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

## Requirements

- **Windows** PC (the Python engine should run on Linux/macOS; the app is developed
  and tested on Windows)
- **Python 3.10+** and **Node.js 18+**
- **[FFmpeg](https://ffmpeg.org/download.html)** on your PATH
- **[Ollama](https://ollama.com)** with a model pulled: `ollama pull gemma:7b`
- Recommended: an **NVIDIA GPU** (see [GPU acceleration](#gpu-acceleration))

## Install and run

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

> A one-click Windows installer and a first-run setup wizard are the next items on the
> roadmap — until then, the steps above are the supported path.

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

Clips can be translated, subtitled, and dubbed into **19 languages**:

English · Spanish · Portuguese (Brazilian) · French · German · Hindi · Indonesian ·
Japanese · Russian · Arabic · Chinese (Simplified) · Vietnamese · Filipino · Turkish ·
Urdu · Bengali · Thai · Korean · Italian

The **interface itself** is translated into 10: English, Spanish, Portuguese, French,
German, Hindi, Indonesian, Japanese, Russian, and Arabic.

Adding a language is one row in `multilingual/languages.py` — the translator and
subtitle writer are language-agnostic.

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

## Roadmap

**Before the first alpha release**

1. **Windows installer** — one-click setup for non-technical creators
2. **First-run setup wizard** — GPU detection, model recommendation and download,
   dependency checks
3. **Update wizard** — check GitHub Releases, show release notes, install updates

**Later**

4. TikTok / Instagram Reels export
5. **Android companion app** — clip from a phone (Twitch, Kick, and local files only,
   to comply with Play Store policy)
6. **Remote rendering** — hand rendering to another machine
7. **Automated posting** *(possible future plan)* — channel monitoring, scheduling,
   and YouTube Shorts auto-upload are coded in the repo but **dormant and not exposed
   in the UI**. The upload path has not been tested end-to-end against a real server,
   because it needs your own YouTube API credentials and Google's API audit to post
   publicly. If people want this, it will be finished and tested in a future release.
8. **Voice cloning for dubbing** *(possible future plan)* — dubbing today uses a preset
   local voice. Speaking translations in the creator's own voice needs a cloning model,
   and every credible local one (Chatterbox, XTTS, F5-TTS) pulls in its own PyTorch
   build: on a machine set up for clipping that would downgrade torch to a CPU-only
   build and silently strip GPU acceleration from tracking and transcription, turning a
   30-minute pipeline into hours. Not worth breaking clipping for.

   The safe route, if it's built later, is a separate Python environment under `data/`
   that the app shells out to, so the clipping environment is never touched — plus
   checking each model's licence, since several of the best-sounding ones are
   non-commercial and this app's users monetize their videos.

9. **Gaming and reaction layouts** *(possible future plan)* — a dedicated layout for
   gameplay-with-facecam and for reaction videos, composing the creator's webcam and
   what they're reacting to into one vertical frame.

   Prototyped and **removed on purpose**. Both fail on the same problem: the app can't
   reliably tell which region is which. Reaction and gameplay footage is full of
   *other* people — a speaker in the video being reacted to, a rendered game character
   — and they look exactly like a webcam to a person detector. Motion doesn't separate
   them either: creators pause the video to comment, so on real footage the thing being
   reacted to was the *least* moving region on screen (measured: chat 19.6, webcam
   12.9, the paused video 0.5), which sends "find the interesting region" heuristics
   straight to chat and UI. Marking regions by hand works, but every layout differs and
   creators switch between them mid-stream, so the setup cost lands on the user for
   every video.

   The core pipeline — talking-head, IRL, gym, podcast — is what this app is for, and
   it's deliberately kept free of that complexity. If there's real demand, this returns
   as a self-contained mode that cannot affect the standard path.

## License

MIT — see [LICENSE](LICENSE). Built to be modified: swap models, tune prompts, add
platforms.
