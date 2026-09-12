---
title: Clips Kitty
emoji: 🐱
colorFrom: blue
colorTo: indigo
sdk: static
app_file: index.html
pinned: true
license: agpl-3.0
short_description: Free open-source Opus Clip alternative that runs on your PC
tags:
  - gemma
  - video
  - video-editing
  - ai-video
  - clips
  - shorts
  - whisper
  - ollama
  - local-ai
  - open-source
  - twitch
  - youtube
  - kick
---

# Clips Kitty

Free, open-source AI video clipping that runs on your own computer. Paste a
Twitch, Kick or YouTube link and get vertical clips with captions and titles,
no upload, no subscription, no per-clip fee.

**This Space is the project website.** Clips Kitty is a Windows desktop
application; it cannot run inside a Space, because it needs a local GPU, FFmpeg
and your own files.

## What it does

Transcribes with faster-whisper at word level, scores candidate moments with a
local language model reading the transcript alongside loudness, laughter-shaped
bursts, scene cuts and on-screen reactions, reframes to 9:16 with pose tracking
and active-speaker detection so whoever is talking stays in shot, then burns in
word-synced captions and writes the titles. It also translates, subtitles and
dubs into 19 languages.

Good at: IRL, just chatting, podcasts, vlogs and interviews. Not yet good at:
gaming, split-screen and reaction footage.

## An open-source Opus Clip alternative

Nothing is uploaded, so nothing is metered. There is no watermark, no clip cap
and no export expiry, and the AI model is your choice rather than whichever one
a service picked.

The trade is honest: it needs Windows, 16 GB of RAM and ideally an NVIDIA
graphics card. If you clip occasionally, or from a phone or a Mac, a browser
tool is the better answer.

**[Full comparison, with current prices](https://colingpt9.github.io/clips-studio/opus-clip-alternative.html)**

## Links

- **Download and source:** <https://github.com/ColinGPT9/clips-studio>
- **Website:** <https://colingpt9.github.io/clips-studio/>

---

The frontmatter above is what makes this folder a Hugging Face **static
Space** (`sdk: static`, serving `index.html`). The same folder is published to
GitHub Pages, so every path in the HTML is **relative**. An absolute `/styles.css`
would work on a Space and break on a project Pages site, which is served from a
subdirectory.

The `tags:` list is not decoration. It is the primary filter on the Spaces index
and a direct input to Hugging Face search, and this Space was invisible without
it. The card body is the part Google indexes, because every mirrored page below
it carries a canonical pointing back at the GitHub Pages copy.
