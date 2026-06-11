# YouTube-Clips-Automation

Autonomous AI-powered video clipping platform — fully local. Downloads YouTube videos,
transcribes them with faster-whisper, finds viral moments with a local LLM (Gemma via
Ollama), and cuts MP4 clips with FFmpeg. No cloud AI APIs, no inference fees.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design, including the
planned face tracking (YOLOv8), captioning, metadata generation, YouTube Shorts
upload, and RSS channel monitoring stages.

## Current status

- [x] Download a video by URL (yt-dlp)
- [x] Local transcription (faster-whisper, word-level timestamps, cached per video)
- [x] LLM highlight detection with 0-100 virality scoring (Gemma via Ollama, swappable)
- [x] Duplicate prevention: timestamp overlap (>40%), transcript similarity (>70%),
      segment reuse (>40%), SQLite uniqueness — rejections logged with reasons
- [x] YOLOv8 + OpenCV subject tracking with true 9:16 (1080x1920) crop-only rendering
- [x] Word-synced burned-in captions (Opus Clip style)
- [x] LLM-generated titles, descriptions, and hashtags per clip
- [x] YouTube Shorts upload (Data API, OAuth, resumable)
- [x] SQLite state DB: crash-safe resume, no reprocessing, no duplicate clips/uploads
- [x] RSS channel monitoring daemon (zero API quota)
- [x] Daily schedule cap (6 Shorts/day), overflow queues to the next day, survives restarts

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) on PATH
- [Ollama](https://ollama.com) running locally with a model pulled:
  ```
  ollama pull gemma:7b
  ```

## Install

```
pip install -r requirements.txt
```

## Quickstart for YouTubers

Open [config/settings.yaml](config/settings.yaml) and edit the two lines at the very top:

```yaml
model: gemma:7b        # your AI model
channel: "@YourHandle" # your channel
```

Then:

```
pip install -r requirements.txt
python main.py run
```

That's the whole setup. The daemon detects your new uploads, finds the best
moments, renders captioned vertical Shorts, writes titles/hashtags, and
schedules up to 6 per day. (Prefer the command line? `python main.py channels
add @YourHandle` works too — it accepts handles, URLs, or channel IDs.)

## Enable uploads (optional, one-time)

Without this, clips and their titles/descriptions still pile up locally for
manual posting. With it, the daemon posts to YouTube automatically:

1. In [Google Cloud Console](https://console.cloud.google.com): create a project,
   enable **YouTube Data API v3**, configure the OAuth consent screen (add your
   own Google account as a test user), and create an **OAuth client ID** of type
   **Desktop app**. Download the JSON as `config/client_secret.json`.
2. Run `python main.py auth` — your browser opens once to approve; the token is
   cached locally after that.
3. Set `auto_upload: true` at the top of settings.yaml.

Every upload goes out with its AI-generated title, description, and hashtags
(plus #Shorts), exactly like Repurpose.io. Uploads cost 1,600 of your 10,000
daily API quota units — the 6/day cap matches this. Trigger a one-off batch
anytime with `python main.py upload`.

## Posting publicly

`privacy: public` is already set at the top of settings.yaml, but YouTube has
one platform rule every tool (including Repurpose.io and Opus Clip) has to deal
with: **API uploads from un-audited apps are forced to private**, regardless of
what the app requests.

To unlock public posting for your own app — free, one-time:

1. Make sure uploads work end-to-end first (they'll land as private).
2. Fill in the [YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form).
   Describe it honestly: a personal/open-source tool that uploads Shorts to
   your own channel. Personal-use exceptions are routinely granted.
3. Once approved (typically days to a couple of weeks), every upload posts
   public automatically — no code or settings change needed, since the app
   already requests `public`.

Until approval, uploads arrive as private drafts with all metadata attached —
flip them to public in YouTube Studio with one click each.

Other commands:

```
python main.py process "https://www.youtube.com/watch?v=VIDEO_ID"   # one video now
python main.py status                                               # what has it done?
python main.py channels list / remove @handle                       # manage channels
python main.py models                                               # see + switch models
python main.py auth                                                 # one-time YouTube login
python main.py upload                                               # post scheduled clips now
```

## Got a better GPU? Use a bigger Gemma

Clip selection quality scales directly with the model. Check what you're
running and what your hardware can handle:

```
python main.py models
```

| Your hardware | Recommended model | Why |
|---|---|---|
| CPU only / integrated GPU | `gemma3:4b` | fast, surprisingly capable |
| 6-8 GB VRAM | `gemma3:4b` | fully GPU-accelerated |
| 10-12 GB VRAM | `gemma3:12b` | big quality jump in scoring |
| 16-24 GB VRAM | `gemma3:27b` | best local Gemma |
| Any | `llama3.1:8b`, `qwen2.5:14b`, ... | anything Ollama serves works |

Switching is two commands — nothing else changes:

```
ollama pull gemma3:12b
python main.py models use gemma3:12b
```

Finished clips land in `data/clips/<video_id>/` as true 1080x1920 vertical MP4s
with the subject kept centered by YOLOv8 tracking. Transcripts are cached in
`data/transcripts/`; all state lives in `data/state.db`. A video already marked
done is never reprocessed (`--force` overrides).

## Configuration

Everything lives in [config/settings.yaml](config/settings.yaml):

- `channels` — channel IDs for the daemon to monitor.
- `llm.backend` — swap models by changing one line (`ollama/gemma:7b`,
  `ollama/llama3.1:8b`, any Ollama tag). New providers = one file in `llm/`.
- `clips.min_score` — raise for fewer/better clips, lower if nothing passes.
- `analysis.max_overlap` / `max_text_similarity` / `max_segment_reuse` —
  duplicate rejection thresholds.
- `upload.daily_limit` — hard cap on Shorts scheduled per day (default 6,
  matching YouTube's 10,000-unit default quota at 1,600 units per upload).
- `whisper.model` — `small` is a good speed/quality default; `medium`/`large-v3`
  for better accuracy if you have the GPU.

The scoring prompt is plain text at [config/prompts/score_clips.txt](config/prompts/score_clips.txt) —
tune the virality criteria without touching code.
