# Clips Studio — open-source AI video clipping that runs on your own PC

<img src="docs/brand/mascot.png" alt="" width="150" align="right">

**Turn long streams and videos into ready-to-post Shorts, Reels, and TikToks —
entirely on your own machine.** Clips Studio is a free, open-source AI clip generator
and video editor for creators: paste a YouTube, Twitch, or Kick link and it finds the
best moments, crops them to 9:16 with the speaker kept centred, burns in word-synced
captions, and writes titles, descriptions, and hashtags.

No cloud AI. No subscription. No per-clip fees. No upload of your footage to anyone.

**Why it exists:** most creators growing a channel are doing all of it themselves —
filming, streaming, editing, posting. Clipping is how people find you and it is usually
the first thing that gets dropped: an editor is a cost most channels cannot justify yet,
clipping tools charge per video or per month, and cutting them by hand takes a day you
needed elsewhere. This runs on hardware you already own, so it costs nothing to use and
there is no cap on how many clips you make.

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
        Video editing           YOLOv8 pose tracking + TalkNet active-speaker
             │                  detection → 9:16 crop that cuts to whoever is
             │                  speaking; trims, cuts, watermark
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
  and in group footage the camera follows **whoever is actually speaking**, decided by
  TalkNet active-speaker detection from the face and the audio together rather than
  from movement. When the speaker changes the framing **cuts** to them instead of
  panning across, the way an editor would. Crop-only framing: never stretched, never
  distorted.
- **Podcast mode** — for multi-camera footage, each shot gets its own steady crop on one
  person, so cuts land on a face with no panning. Within a shot the subject is chosen by
  mouth motion, falling back to the most prominent face.
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
| **Gaming and split-screen** | Not shipped yet. Framing did not reliably find the part of the screen where the action was, and subject tracking mistook characters *inside the game* for the streamer — to a person detector, a person on screen is a person on screen. The result was clips centred on the wrong human, so it is held back rather than shipped half-working |
| **Reaction videos** | Not yet either, and for a related reason. Clips are chosen from what's *said*, and it can't see the video you're reacting to — so the moment that made the clip is invisible to it |

Clip selection is transcript-and-signal driven. When the funny thing is *visual only*
and nobody comments on it, expect to find it yourself in the editor.

## Requirements

If you use the installer, this is the whole list:

- **Windows** PC (the Python engine should run on Linux/macOS; the app is developed
  and tested on Windows). No Mac build exists and the maintainer has no Mac to test
  one on — see [#62](../../issues/62) if you have one and want to help
- **16 GB of RAM.** Not a suggestion: 8 GB will analyse a whole video and then
  render nothing, which looks like a crash rather than a memory limit
- Recommended: an **NVIDIA GPU** (see [GPU acceleration](#gpu-acceleration))
- Around **20 GB** free, plus room for the videos you clip

Running from source needs the things the installer would otherwise bundle for you:

- **Python 3.10+** and **Node.js 18+**
- **[FFmpeg](https://ffmpeg.org/download.html)** on your PATH
- **[Ollama](https://ollama.com)** with a model pulled: `ollama pull gemma:7b`

## Install and run

**Most people want the installer.** Grab the latest **Web Setup** from
[Releases](../../releases) and run it. It carries the app, the Python engine, every
library, FFmpeg, the AI runtime and all the detection and transcription weights — no
Python, no PATH, no terminal, and no second program to install.

The one thing it doesn't carry is the language model itself, because those ship under
licences the person downloading has to accept rather than something that can be
accepted on your behalf. The setup wizard starts that download by itself, picks the
size that suits your graphics card, and shows a progress bar. So: one download, one
double-click, one progress bar. After that it runs offline.

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

One axis only: how much VRAM you have. These are the same rows the app shows, from
`RECOMMENDATIONS` in [`llm/manager.py`](llm/manager.py) — the wizard and the Models
page read that one table so they cannot disagree with each other, or with this.

| Your hardware | Recommended model | |
|---|---|---|
| CPU / iGPU, no graphics card | `gemma3:4b` | tested |
| Under 6 GB VRAM, or an older PC | `gemma4:e2b` | |
| 6 GB VRAM | `gemma4:e4b` | |
| 8 GB VRAM | **`gemma:7b`** | tested · shipped default |
| 10–12 GB VRAM | `gemma3:12b` | tested |
| 16–24 GB VRAM | `gemma3:27b` | |

And the models worth picking for a reason other than VRAM:

| Why | Model |
|---|---|
| Translation / multilingual | `qwen3:8b` or `qwen3:14b` |
| Permissive licence | `mistral-nemo:12b` (Apache-2.0) · `phi4:14b` (MIT) |

**Only the three marked "tested" have been run against real streams**, and `gemma:7b`
is the one with the most hours on it — it is what `config/settings.yaml` ships with.
Everything else is listed because it is a sensible size and free to use commercially,
not because clip quality has been measured with it. They all work, since the app talks
to every model identically through Ollama, but nobody has checked whether they pick
better moments. Closing that gap is [#38](../../issues/38).

Anything Ollama serves works, and switching is one click. Translation can use a
*different* model than clipping — set `llm.translation_model`.

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

### Help wanted: the maintainer only speaks English

18 of those 19 interface translations have never been read by somebody who speaks
the language. Half of them aren't finished. Every language has its own issue,
written in English and in that language, and **reporting a bad word is a complete
contribution** — no pull request needed. [How it works](docs/TRANSLATING.md).

**Needs finishing** (about 68 strings still in English):
[বাংলা (Bengali)](../../issues/43) ·
[Italiano](../../issues/44) ·
[한국어 (Korean)](../../issues/45) ·
[ไทย (Thai)](../../issues/46) ·
[Tagalog (Filipino)](../../issues/47) ·
[Türkçe (Turkish)](../../issues/48) ·
[اردو (Urdu)](../../issues/49) ·
[Tiếng Việt (Vietnamese)](../../issues/50) ·
[中文 (Chinese)](../../issues/51)

**Needs checking** (complete, but nobody has verified it reads naturally):
[العربية (Arabic)](../../issues/52) ·
[Deutsch (German)](../../issues/53) ·
[Español (Spanish)](../../issues/54) ·
[Français (French)](../../issues/55) ·
[हिन्दी (Hindi)](../../issues/56) ·
[Bahasa Indonesia (Indonesian)](../../issues/57) ·
[日本語 (Japanese)](../../issues/58) ·
[Português (Portuguese)](../../issues/59) ·
[Русский (Russian)](../../issues/60)

Want a language that isn't here? [#61](../../issues/61).

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

**Video decoding** goes through FFmpeg with hardware acceleration rather than OpenCV,
for both the tracking pass and rendering. Reading sample frames this way costs about a
tenth of the CPU that decoding them in Python did, which matters because the tracking
pass runs over every clip.

**CPU sharing.** Detection and rendering both want every core, and left alone they
fight each other and the interface. The engine divides the cores between render workers
at startup and deliberately holds two back, so the app stays responsive while a batch
runs. Tune with `video.parallel_renders`.

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

## Changelog, known issues, roadmap

- **[CHANGELOG.md](CHANGELOG.md)** — what changed in each release, in plain terms.
- **[KNOWN-ISSUES.md](KNOWN-ISSUES.md)** — what is broken or missing right now, and
  the workarounds. Everything on it has actually been observed.
- **[ROADMAP.md](ROADMAP.md)** — what is shipped, what is next, and the long-term
  direction.

## Roadmap detail

The short version is in [ROADMAP.md](ROADMAP.md). The reasoning behind the harder
calls is below, because "why not yet" is usually more useful than "not yet".

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

**GNU AGPL-3.0** — see [LICENSE](LICENSE), and [NOTICE](NOTICE) for what that means
in practice and what the installer bundles.

**If you use the app, this changes nothing for you.** Install it, clip your streams,
post the clips, earn from them. The AGPL binds people who *distribute* the software or
run a *modified* copy as a network service, not people who use it. Your footage and
your clips are yours.

**If you modify and share it,** your changes go out under the same licence. That is the
point: improvements to a tool built for creators stay available to those creators.

Still built to be modified: swap models, tune prompts, add platforms.
