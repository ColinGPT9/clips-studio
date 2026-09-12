---
license: agpl-3.0
tags:
  # Both names on purpose. The app is Clips Kitty from 1.1.3; the old tag
  # stays so anything already pointing at it keeps resolving.
  - clips-kitty
  - clips-studio
  # The rest are here to be searched for. `installer` and `windows` describe
  # what the files are; nobody browses Hugging Face looking for those.
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

# Clips Kitty: free AI video clipping that runs on your own PC

[Clips Kitty](https://github.com/ColinGPT9/clips-studio) turns long streams and
videos into ready-to-post vertical clips, entirely on your own Windows machine.
Paste a Twitch, Kick or YouTube link and it finds the best moments, crops them
to 9:16 with the speaker kept in frame, burns in word-synced captions and writes
the titles. It is an open-source alternative to cloud clipping tools: nothing is
uploaded, so there is no subscription, no watermark and no cap on how many clips
you make. [Full comparison with Opus Clip](https://colingpt9.github.io/clips-studio/opus-clip-alternative.html).

**This repository is not a model.** It holds the installer payload.

It lives on Hugging Face for one dull reason: a GitHub release asset is capped
at 2 GiB and the payload is roughly twice that, because the installer bundles
everything the app needs rather than sending you off to install it yourself.

## Do not download these files directly

They will not do anything on their own. **Get the installer instead:**

### → [Download Clips Kitty](https://github.com/ColinGPT9/clips-studio/releases/latest)

The Web Setup is about 1 MB. Run it, and it fetches the payload from here by
itself, checks it against the SHA512 in `latest.yml`, and installs the app.

| File | What it is |
|---|---|
| `clips-studio-<version>-x64.nsis.7z` | What the Web Setup downloads. Not usable by hand. |
| `latest.yml` | Version and SHA512. The in-app updater reads this. |
| `ClipsStudio-<version>-x64.zip` | Offline alternative: unzip it and run `Clips Kitty.exe`. No installer, no shortcuts. |

## What Clips Kitty does

Paste a Twitch, Kick or YouTube link and it finds the good moments, crops them
to a phone screen with subject tracking, writes captions and titles, and gives
you an editor to fix anything it got wrong. Nothing is uploaded: the video, the
transcription and the language model all stay on your machine.

The installer carries the app, the Python engine, FFmpeg, the Ollama runtime
and the tracking and transcription weights. The only thing it fetches on first
launch is the language model, which is licensed to whoever downloads it rather
than something that can be handed over in a box.

## For developers

Source, issues and contributing guide: **<https://github.com/ColinGPT9/clips-studio>**

- [Architecture](https://github.com/ColinGPT9/clips-studio/blob/main/ARCHITECTURE.md): how the pipeline fits together
- [Contributing](https://github.com/ColinGPT9/clips-studio/blob/main/CONTRIBUTING.md)
- [Engine container image](https://github.com/ColinGPT9/clips-studio/blob/main/docs/DOCKER.md): work on the Python engine without installing PyTorch locally
- Website: <https://colingpt9.github.io/clips-studio/> · Space: <https://huggingface.co/spaces/ColinGPT9/Clips-Studio>

Licensed AGPL-3.0. The bundled components keep their own licences; see
[NOTICE](https://github.com/ColinGPT9/clips-studio/blob/main/NOTICE).
