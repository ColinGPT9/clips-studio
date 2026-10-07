# Loud moments, cut on scene changes

An example [Clips Kitty](https://github.com/ColinGPT9/clips-studio) pipeline plugin, laid out the way a plugin's own repository would be. Copy this folder to start your own.

## What it does

It finds stretches of a video that are clearly louder than the rest (by default 6 dB above the video's median loudness), and makes each one a clip that starts on the last scene cut before it. It uses FFmpeg's `ebur128` loudness filter and its scene-change score, and nothing else.

**What it does not do.** It does not know what is on screen or what anyone says. Loud is not the same as interesting (music, an advert, someone shouting at a pet), and a quiet highlight is missed. Treat it as a demonstration of the plugin contract, not as a highlight detector.

Clips Kitty does the rest: downloading, transcribing, titles and hashtags with your AI model, the vertical crop, captions, rendering and the library.

## Files

```text
clipskitty.yaml   the manifest: what it needs, what it returns, its settings
src/main.py       reads the job, runs FFmpeg, returns moments
LICENSE           MIT
```

`src/main.py` imports only the Python standard library and `clipskitty_sdk`, which Clips Kitty puts on the plugin's path when it runs it.

## Settings

| Setting | Default | |
|---|---|---|
| `louder_by_db` | 6 | How much louder than usual a stretch must be |
| `scene_threshold` | 0.3 | How big a picture change counts as a cut (FFmpeg's scene score, 0 to 1) |
| `lead_in_seconds` | 4 | Seconds kept before a loud stretch when no cut is near |

## Permissions

`video.read` (the video file) and `ffmpeg` (Clips Kitty's FFmpeg). It runs on your PC and sends nothing anywhere.

## Trying it

With a Clips Kitty checkout's `sdk/python` on `PYTHONPATH`:

```text
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --video some-video.mp4
```

## Requirements

Python 3.10 or newer on the PC (Clips Kitty does not ship one for plugins yet), and FFmpeg, which Clips Kitty provides.
