# Clips Studio — local-first AI video clipping

Turn long YouTube videos into ready-to-post vertical Shorts — **entirely on your own
PC**. A desktop app powered by open-weight AI models: no cloud AI, no subscriptions,
no per-clip fees, and your videos never leave your machine.

Paste a video link — a YouTube video, a Twitch VOD, or a Kick VOD — and Clips Studio finds the
best moments, crops them to 9:16 with the speaker kept centered, burns in word-synced
captions, and writes titles, descriptions, and hashtags — then lets you review, edit,
and export everything from a clean desktop interface.

## Features

- **Multimodal clip detection** — moments are scored 0–100 by fusing five signals:
  what's *said* (transcript analysis), audio excitement (laughter, shouting, hype),
  visual activity (scene cuts, motion), on-screen reactions, and hook/payoff strength.
  Every clip that clears the quality bar is kept — no arbitrary limit.
- **Speaker-aware face tracking** — YOLOv8 + OpenCV keep the subject centered in the
  9:16 crop. In group videos the camera follows **whoever is talking** (detected by
  mouth movement), not just the biggest person. Gameplay + facecam layouts get the
  classic stacked webcam-over-gameplay format automatically. Crop-only framing —
  never stretched or distorted.
- **Editable burned-in captions** — word-synced captions in your style: colour, size,
  position, words-per-line, casing, or off entirely. Fix any transcription mistake
  line-by-line before exporting. Set your style once and every clip uses it.
- **AI edit chat** — tell the clip what's wrong in plain language: *"make it 5 seconds
  longer"*, *"center the crop"*, *"the caption says gost, it should say ghost"* — and
  it re-edits from the original video.
- **AI titles, descriptions & hashtags** for every clip, all editable before export.
- **Model manager** — swap the AI brain from inside the app. Bigger GPU, bigger
  Gemma, better clips. Download/remove/switch models with progress bars, no terminal.
- **GPU accelerated** — CUDA for detection and transcription, NVENC hardware video
  encoding, and the LLM on GPU via Ollama.
- **Organized output** — clips land in `channel name / video title /` folders with
  clean slug filenames like `cra-tax-rules-explained.mp4`.
- **Accessible UI** — WCAG-minded: keyboard focus, reduced-motion support, adjustable
  font, text size, and colour.

## The AI stack (all local, all open)

| Job | Tool |
|---|---|
| Download | yt-dlp |
| Transcription | faster-whisper (word-level timestamps) |
| Clip selection, titles, chat editing | Gemma via [Ollama](https://ollama.com) — swappable for any local model |
| Person/face detection | YOLOv8 + OpenCV |
| Rendering | FFmpeg with hardware encoding (NVIDIA NVENC, AMD AMF, or Intel QSV — auto-detected) |

## Requirements

- Windows PC (Linux/macOS should work for the Python engine; the app is developed on Windows)
- Python 3.10+, Node.js 18+
- [FFmpeg](https://ffmpeg.org/download.html) on PATH
- [Ollama](https://ollama.com) with a model pulled: `ollama pull gemma:7b`
- Optional but recommended: an NVIDIA GPU (see [GPU acceleration](#gpu-acceleration))

## Install & run

```bash
git clone https://github.com/ColinGPT9/clips-studio
cd clips-studio
pip install -r requirements.txt

cd ui
npm install
npm run dev        # opens the Clips Studio desktop app
```

The app starts its own Python engine automatically. Paste a YouTube URL in
**Clip Studio**, hit *Generate clips*, and watch the progress live. A one-click
Windows installer for non-technical users is the next item on the roadmap.

### Pick your model

Open the **Models** page (or the sidebar dropdown) to see what's installed and what
your GPU can handle:

| Your hardware | Recommended model |
|---|---|
| CPU only / integrated GPU | `gemma3:4b` |
| 6–8 GB VRAM | `gemma3:4b` |
| 10–12 GB VRAM | `gemma3:12b` |
| 16–24 GB VRAM | `gemma3:27b` |

Anything Ollama serves works — Llama, Qwen, future models — switching is one click.

## GPU acceleration

**Video encoding** is hardware-accelerated automatically on all three GPU vendors —
NVIDIA (NVENC), AMD (AMF), and Intel (QSV). The engine test-encodes a frame with
each encoder at startup and uses the first one that actually works, falling back
to CPU if none do. Force a choice with `video.encoder` in `config/settings.yaml`.

**Detection & transcription** run fastest with CUDA on NVIDIA GPUs. Out of the box,
`pip install torch` gives you the **CPU-only** build — for NVIDIA:

```bash
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

(AMD GPU owners: tracking/transcription run on CPU on Windows — still fully
functional, just slower. Your GPU is still used for video encoding via AMF and
for the LLM via Ollama, which supports AMD on its own.)

## Headless / CLI use

Everything also works without the app:

```bash
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID"   # one video
python main.py models                                               # list/switch models
python main.py status                                               # processing state
python main.py serve                                                # just the API engine
```

Advanced settings (scoring weights, tracking, quality bar) live in
[config/settings.yaml](config/settings.yaml). The clip-scoring prompts are plain text
in [config/prompts/](config/prompts/) — tune them without touching code.

## Roadmap

1. **Windows installer** — one-click setup for non-technical creators
2. ~~**Twitch & Kick VODs**~~ — done! Paste a `twitch.tv/videos/…` or
   `kick.com/video/…` link. (VODs only — live streams are deliberately not
   supported.)
3. **Automated posting** *(possible future plan)* — channel monitoring,
   scheduling, and YouTube Shorts auto-upload are coded in the repo but
   **dormant and not exposed in the UI**. The upload path has not been
   tested end-to-end against a real server, because it needs your own
   YouTube API credentials and Google's API audit to post publicly — access
   we don't have during development. If people want this, it will be
   finished and tested in a future release.
4. TikTok / Instagram Reels export
5. **Gaming & reaction layouts** *(possible future plan)* — a dedicated
   layout for gameplay-with-facecam streams and for reaction videos, where
   the creator's webcam and what they're reacting to are composed into one
   vertical frame instead of cropping to a single subject.

   Prototyped and **removed on purpose**. Both fail on the same problem:
   the app can't reliably tell which region is which. Reaction and gameplay
   footage is full of *other* people — a speaker in the video being reacted
   to, or a rendered game character — and they look exactly like a webcam
   to a person detector. Motion doesn't separate them either: creators
   pause the video to comment, so on real footage the thing being reacted
   to was the *least* moving region on screen (measured: chat 19.6, webcam
   12.9, the paused video 0.5), which sends "find the interesting region"
   heuristics straight to chat and UI. Marking the regions by hand works,
   but every layout differs — YouTube, Twitch, a tweet, a news article —
   and creators switch between them mid-stream, so the setup cost lands on
   the user for every video.

   The core pipeline (talking-head, IRL, gym, podcast) is what this app is
   for, and it is deliberately kept free of that complexity. If there is
   real demand, this comes back as a self-contained mode that cannot affect
   the standard path.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the engine design and
[DESIGN-V2.md](DESIGN-V2.md) for the multimodal scoring system and desktop app design.

## License

Open source — see [LICENSE](LICENSE). Built to be modified: swap models, tune
prompts, add platforms.
