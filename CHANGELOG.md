# Changelog

What changed, written for people who use Clips Studio rather than people who
read the commits. Dates are release dates.

This project is in **alpha**: versions move fast, and things listed as fixed
were often broken in a way that only showed up on somebody else's machine.

---

## 0.1.2 — 2026-08-10

### Fixed

- **Your clip settings are remembered again.** Turning captions off, closing
  the app and reopening it brought captions back on. Five settings behaved this
  way — captions, 60s+ clips, podcast mode, longform and its mode. They were
  read from storage on startup but never actually saved.
- **The Watermark tickbox works.** It silently refused to stay ticked when no
  branding profile existed yet. It now explains that a profile has to be
  created first, rather than looking broken.
- **A failed render says what went wrong.** A memory failure used to print
  pages of encoder output for every affected clip. It now says so in one
  sentence, once, however many clips were hit.

### Changed

- **The Models page makes sense.** It had one heading — "Your hardware" — over
  rows like "Multilingual" and "Newer Gemma", which are not hardware, and a
  "Why" column carrying licences and warnings at the same time. There are now
  two tables: what your machine can run, and what to pick for a particular job.
  `gemma3:4b` also appeared twice; it is one row now.
- **More models to choose from**, all free to run locally and all usable on
  clips you earn from. `gemma4:e2b` and `gemma4:e4b` are built for ordinary
  local machines and are recommended alongside the Gemma 3 line — `e2b` for a
  low-power or older PC, `e4b` anywhere `gemma3:4b` fits. Qwen3 for translation
  and multilingual work, and Mistral Nemo or Phi-4 for anyone who wants a
  plainly permissive licence.

---

## 0.1.1 — 2026-08-09

The release that made 0.1.0 usable. Both bugs were packaging mistakes, and both
were invisible on a development machine — which is exactly how they reached a
release.

### Fixed

- **No clip could be produced, from any source.** Every job died partway with
  `No module named matplotlib`. The bundle excluded a library that the tracking
  model needs in order to load at all.
- **YouTube downloads failed** with `ffmpeg is not installed`. FFmpeg ships
  inside the app, but the downloader looked for it on the system instead of
  being told where it lived, so it could not join YouTube's separate video and
  audio streams. Twitch and Kick were unaffected, because their recordings
  arrive as a single stream — which is what made it look like a YouTube
  problem rather than a packaging one.

---

## 0.1.0 — 2026-08-08

First public alpha.

- Paste a Twitch VOD, Kick VOD or YouTube link and get vertical clips with
  word-synced captions and written titles.
- Everything runs on your own computer. No uploads, no subscription, no cap on
  how many clips you make.
- One installer. It carries the app, the engine, FFmpeg, the AI runtime and the
  tracking and transcription models — the only thing fetched afterwards is the
  language model, sized to your graphics card on first launch.
- Editor for fixing anything the AI got wrong, multilingual captions, creator
  profiles that learn from your corrections, and a queue that runs unattended.
