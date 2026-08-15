# Changelog

What changed, written for people who use Clips Studio rather than people who
read the commits. Dates are release dates.

This project is in **alpha**: versions move fast, and things listed as fixed
were often broken in a way that only showed up on somebody else's machine.

---

## Unreleased

### Fixed

- **Processing no longer needs to reach GitHub.** The first video on a fresh
  install downloaded a 7 MB detection model even though that file was already
  inside the installer. Behind a firewall, on a locked-down machine, or just
  offline, the job failed outright with "Download failure … Retry limit
  reached". It now uses the copy it shipped with. **This affected 0.1.2**, so
  if your first video failed with a download error, this was why.
- **A video whose details were lost keeps its name.** Reprocessing a video
  after the database had been reset or moved showed the raw ID instead of the
  title, and no channel at all. The empty channel was the worse half: creator
  profiles are matched on it, so catchphrase learning and preference history
  quietly did not run for that video. It now re-fetches the title and channel
  without re-downloading the video, and still works offline.
- **The Models page headings no longer run together.** "Recommended" was wider
  than its column and collided with the next heading, reading as
  "RECOMMENDEDWHY". The column is now labelled "Model", which is what it holds.

### Added

- **Clips Studio is coming to the Microsoft Store.** Same application, same
  local processing; the Store version is updated by the Store rather than by
  the in-app updater, and its donate button opens your browser. The standalone
  installer is unchanged and stays the main way to get it.

---

## 0.1.2 (2026-08-10)

### Fixed

- **Your clip settings are remembered again.** Turning captions off, closing
  the app and reopening it brought captions back on. Five settings behaved this
  way: captions, 60s+ clips, podcast mode, longform and its mode. They were
  read from storage on startup but never actually saved.
- **The Watermark tickbox works.** It silently refused to stay ticked when no
  branding profile existed yet. It now explains that a profile has to be
  created first, rather than looking broken.
- **A failed render says what went wrong.** A memory failure used to print
  pages of encoder output for every affected clip. It now says so in one
  sentence, once, however many clips were hit.

### Changed

- **The Models page makes sense.** It had one heading, "Your hardware", over
  rows like "Multilingual" and "Newer Gemma", which are not hardware, and a
  "Why" column carrying licences and warnings at the same time. There are now
  two tables: what your machine can run, and what to pick for a particular job.
  `gemma3:4b` also appeared twice; it is one row now.
- **More models to choose from**, all free to run locally and all usable on
  clips you earn from. `gemma4:e2b` and `gemma4:e4b` are built for ordinary
  local machines and are recommended alongside the Gemma 3 line. `e2b` suits a
  low-power or older PC, `e4b` anywhere `gemma3:4b` fits. Qwen3 for translation
  and multilingual work, and Mistral Nemo or Phi-4 for anyone who wants a
  plainly permissive licence.

---

## 0.1.1 (2026-08-09)

The release that made 0.1.0 usable. Both bugs were packaging mistakes, and both
were invisible on a development machine, which is exactly how they reached a
release.

### Fixed

- **No clip could be produced, from any source.** Every job died partway with
  `No module named matplotlib`. The bundle excluded a library that the tracking
  model needs in order to load at all.
- **YouTube downloads failed** with `ffmpeg is not installed`. FFmpeg ships
  inside the app, but the downloader looked for it on the system instead of
  being told where it lived, so it could not join YouTube's separate video and
  audio streams. Twitch and Kick were unaffected, because their recordings
  arrive as a single stream, which is what made it look like a YouTube
  problem rather than a packaging one.

---

## 0.1.0 (2026-08-08)

First public alpha.

- Paste a Twitch VOD, Kick VOD or YouTube link and get vertical clips with
  word-synced captions and written titles.
- Everything runs on your own computer. No uploads, no subscription, no cap on
  how many clips you make.
- One installer. It carries the app, the engine, FFmpeg, the AI runtime and the
  tracking and transcription models. The only thing fetched afterwards is the
  language model, sized to your graphics card on first launch.
- Editor for fixing anything the AI got wrong, multilingual captions, creator
  profiles that learn from your corrections, and a queue that runs unattended.
