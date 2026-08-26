# Changelog

What changed, written for people who use Clips Studio rather than people who
read the commits. Dates are release dates.

This project is in **alpha**: versions move fast, and things listed as fixed
were often broken in a way that only showed up on somebody else's machine.

---

## Unreleased

### Fixed

- **A failed YouTube download now says what went wrong.** Pasting a YouTube
  link could fail with a wall of technical text ending in `HTTP Error 403:
  Forbidden`. That looks like a broken app and reads like a broken link, and it
  is neither. YouTube hands over the video's details and then refuses to send
  the actual data to your network, which is Google rate-limiting the connection
  itself. It usually clears on its own within an hour, and Twitch, Kick and
  local files keep working while it does. The app now explains that in plain
  English instead of printing the error.

  **What the app cannot do is prevent it.** Nothing in Clips Kitty can persuade
  Google to serve a connection it has decided to throttle. If it keeps
  happening, switching off a VPN or moving to a different network is what
  actually fixes it. Reported by a user through the in-app feedback hub
  ([#81](https://github.com/ColinGPT9/clips-studio/issues/81)).

### Changed

- **The YouTube downloader is six weeks newer** (yt-dlp 2026.8.19). YouTube
  changes how it serves video often enough that this is the one component worth
  keeping current, and the shipped copy had fallen behind. Older copies
  gradually lose access to formats as YouTube moves on.

---

## 1.1.3 — the app is now called Clips Kitty

> **Same app, same data, nothing to do.** Your clips, settings and creator
> profiles stay exactly where they are and open as normal. Only the name
> changed.

**Why:** the old name was too close to existing software to be listed on the
Microsoft Store. The clip editor page had the same problem and is now called
**Clip Editor**.

Everything that is a link stayed a link: the GitHub repository, this website
and the download addresses are all unchanged, so nothing anyone has bookmarked
or shared has broken.

**The version jumped from 0.1.2 to 1.1.3**, which looks odd and is deliberate.
The Store will not accept a version starting with 0, so every release used to
carry two numbers — 0.1.2 in the app and 1.1.2.0 on the Store — and somebody
had to remember the mapping. 1.1.3 is above the 1.1.2.0 already published, so
from here the app version and the Store version are the same number. This is
still alpha software; the leading 1 is a Store requirement, not a claim.

### Fixed

- **Processing no longer needs to reach GitHub.** Every video tried to download
  a 7 MB detection model, even though that file was already inside the
  installer. If the download failed, so did the job: "Download failure … Retry
  limit reached". It now uses the copy it shipped with.

  **This affected 0.1.2**, so if a video failed with a download error, this was
  why. It was unpredictable rather than universal: the app looked for the file
  in whatever folder Windows happened to start it from, so it worked or failed
  depending on where the shortcut pointed, and it always worked when run from a
  developer's own copy of the source. That is why it survived to a release.
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

- **Russian is now fully translated**, thanks to [@4nmus](https://github.com/4nmus),
  the first contribution to Clips Studio from outside. Russian was listed as a
  finished language and was not: 92 of the app's 208 phrases were quietly
  falling back to English. All 92 are translated now, and 56 of the existing
  ones were rewritten by someone who actually speaks Russian rather than by a
  machine. If you use the app in Russian and something still reads oddly,
  [#60](https://github.com/ColinGPT9/clips-studio/issues/60) is the place to say so.
- **Brazilian Portuguese is now fully translated**, thanks to
  [@espinafr](https://github.com/espinafr), the second contribution from
  outside. Portuguese was listed as finished and was not: 133 of the app's 208
  phrases were falling back to English. All of them are translated now, and 30
  of the existing ones were rewritten by someone who speaks the language. The
  most visible change is that clips are "cortes" rather than "clipes", which is
  what Brazilian editors actually call them. If something still reads oddly,
  [#59](https://github.com/ColinGPT9/clips-studio/issues/59) is the place to
  say so — two phrases are already known to need a second opinion.
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
