# Signals cookbook

Small recipes for reading a game's video: what is on one part of the screen, how bright or loud it is, where the scenes cut, what is said, and what the creator's local model sees. Each recipe is a whole `src/main.py` that runs as written, and each says what it found on the SDK's sample video, how long that took, and how it fails.

> No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.

Every recipe here **needs SDK 1.2.0**, which added `media`, `signals`, `text` and `local_model` ([changelog](../../sdk/python/CHANGELOG.md)).

**To try one:** make a plugin with `py -m clipskitty_sdk new my-recipe --template blank` (bash: `python -m clipskitty_sdk new my-recipe --template blank`), put the recipe in its `src/main.py`, set the `inputs`, `outputs` and `permissions` lines in its `clipskitty.yaml` to the recipe's, and run it with `run my-recipe --sample`. [Your first game pipeline](first-game-pipeline.md) walks through a whole plugin.

**The sample video** is 40 seconds of 640x360: a red banner at the top (the region `0.30,0.10,0.40,0.10`) and a white square in the top right corner from 22 to 27 s, a loud sound from 22 to 27 s, scene cuts at 10, 20 and 30 s, and a transcript that says "quark burst" from 21 to 26 s. `python -m clipskitty_sdk sample sample.mp4` writes it.

**The costs** were measured on the sample, on the Linux PC these pages were checked on, with FFmpeg 6.1.1. They grow with the length of the video.

**The rules every recipe keeps.** It imports only the standard library and `clipskitty_sdk`, at the top of the file. Clips Kitty runs plugins on its own Python, which has nothing else. From SDK 1.2.0 on, a plugin that imports a module the SDK of the Clips Kitty running it doesn't have stops as soon as it starts, with "This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's developer which version it needs." Its error lines are plain sentences, because creators see them.

## A region's frames

`media.frames` reads one part of the screen, a few times a second, each frame shrunk to a few pixels, which keeps it quick. A region is four numbers from 0 to 1: left, top, width and height, as parts of the frame, so it fits a recording of any size. `python -m clipskitty_sdk frame VIDEO --at SECONDS --region "..."` draws one on a frame, to measure your own.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run

BANNER = media.Region.parse("0.30,0.10,0.40,0.10")  # left, top, width, height, as fractions of the frame


def main(job):
    red = 0
    for frame in media.frames(job, fps=4, region=BANNER):  # 4 a second, each shrunk to 32x8 pixels
        r, g, b = frame.pixel(16, 4)  # the middle of the region
        if r > 180 and g < 100 and b < 100:
            red += 1
    job.finish(notes=f"The middle of the banner's part of the screen is red in {red} frames.")


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** "The middle of the banner's part of the screen is red in 20 frames." (22.0 to 26.75 s, 4 a second.)
- **Cost:** 0.35 s.
- **How it fails:** a stream that moves the game (a webcam over it, the game shown smaller) puts something else in the region. Without `ffmpeg` or `video.read` in the permissions, the run stops with "This pipeline needs FFmpeg: add ffmpeg to permissions in clipskitty.yaml" or "This pipeline needs the video: add video.read to permissions in clipskitty.yaml". A video FFmpeg can't read stops it with FFmpeg's last line.
- Each `Frame` has `t` (seconds), `width`, `height`, `rgb` (3 bytes a pixel, row by row) and `pixel(x, y)`. `size=None` keeps the full size, which is much slower.

## A colour share

`signals.colour_share` says how much of a frame, from 0 to 1, is one colour. `signals.stretches` joins the times it shows into (first, last) stretches, and `signals.around` puts a lead-in and a tail around each one and fits it to the clip lengths the creator chose.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run, signals

BANNER = media.Region.parse("0.30,0.10,0.40,0.10")


def main(job):
    shows = [frame.t for frame in media.frames(job, fps=4, region=BANNER)
             if signals.colour_share(frame, "e0303a") > 0.5]  # most of the region is the banner's red
    for start, end in signals.stretches(shows):  # (first, last) time of each stretch it shows
        job.add_range(*signals.around(job, start, end), label="banner",
                      reason=f"the banner shows from {start:g} to {end:g} s")


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** one moment, 16.0 to 29.75 s, "the banner shows from 22 to 26.75 s".
- **Cost:** 0.35 s.
- **How it fails:** anything else of that colour in the region counts as the banner. A colour is a match when each of red, green and blue is within `tolerance` (60 by default, out of 255) of it, so a wide tolerance also matches nearby colours. A banner that fades in is seen late.

## A brightness change

`signals.brightness` is how bright a frame is, from 0 (black) to 255 (white). A big jump from one frame to the next is a flash, a cut to a brighter or darker scene, or a menu opening.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run, signals


def main(job):
    before = None
    for frame in media.frames(job, fps=2, size=(16, 9)):  # the whole screen, shrunk
        now = signals.brightness(frame)  # 0 (black) to 255 (white)
        if before is not None and abs(now - before) > 60:
            job.add_range(*signals.around(job, frame.t, frame.t), label="flash",
                          reason=f"the screen's brightness jumps from {before:.0f} to {now:.0f}")
        before = now


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** three moments, 4.0 to 14.0, 14.0 to 24.0 and 24.0 to 34.0 s, where the background changes at 10, 20 and 30 s ("the screen's brightness jumps from 53 to 208", then 208 to 90, then 90 to 220). Each is as long as the shortest clip the job allows, 10 seconds.
- **Cost:** 0.35 s.
- **How it fails:** it can't tell a flash from a cut, a fade to black or a menu: any change of brightness looks the same. `signals.difference(a, b)` compares two frames colour by colour (0 to 255), for changes that keep the brightness.

## An icon by colour at a fixed spot

A HUD icon that lights up, at the same place every time, is a small region and a colour. Shrink it less (`size=(8, 8)`) and ask for nearly all of it.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run, signals

ICON = media.Region.parse("0.92,0.05,0.05,0.09")  # the top right corner


def main(job):
    shown = [frame.t for frame in media.frames(job, fps=2, region=ICON, size=(8, 8))
             if signals.colour_share(frame, "ffffff", tolerance=30) > 0.8]  # nearly all of it white
    for start, end in signals.stretches(shown, gap=0.5):
        job.add_range(*signals.around(job, start, end), label="icon",
                      reason=f"the white icon shows from {start:g} to {end:g} s")


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** one moment, 16.0 to 29.5 s, "the white icon shows from 22 to 26.5 s" (2 frames a second).
- **Cost:** 0.33 s.
- **How it fails:** a background close to the icon's colour counts as the icon. With the default tolerance of 60, the sample's light grey (`d0d0d0`, from 10 to 20 s) would count as white, which is why this recipe uses 30. The spot moves when a game's HUD is scaled, or the stream's layout changes.

## A loudness spike

`media.loudness` gives one number a second, the video's loudness in LUFS (-70 for silence). `signals.spikes` finds the seconds at least `louder_by` dB above the half minute around them, so a video that is loud all along is compared with itself.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run, signals


def main(job):
    loudness = media.loudness(job)  # one number a second
    loud = signals.spikes(loudness, louder_by=6.0)  # the seconds at least 6 dB above the half minute around them
    for start, end in signals.merge(signals.stretches(loud, min_length=0)):
        job.add_range(*signals.around(job, start, end), label="loud",
                      reason=f"loud from {start:g} to {end:g} s")


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** one moment, 16.0 to 30.0 s, "loud from 22 to 27 s".
- **Cost:** 0.24 s.
- **How it fails:** loud isn't the same as exciting, and a quiet highlight is missed. Commentary or music at the same volume all along hides the crowd. A loud stretch longer than about a quarter of a minute raises its own surroundings, so its middle stops counting. A video with no sound gives no numbers, and so no moments.

## Scene cuts

`media.scene_cuts` gives the times the picture changes by more than `threshold` (0 to 1, FFmpeg's scene score; 0.3 by default). Starting a moment on a cut keeps a clip from opening in the middle of a shot.

```yaml
inputs: [video]
outputs: [ranges]
permissions: [video.read, ffmpeg]
```

```python
from clipskitty_sdk import media, run, signals


def main(job):
    cuts = media.scene_cuts(job)  # the times the picture changes
    loud = signals.stretches(signals.spikes(media.loudness(job)), min_length=0)
    for start, end in loud:
        before = [t for t in cuts if t <= start]
        first = before[-1] if before else start  # start on the last cut before it
        job.add_range(*signals.around(job, first, end, lead=0), label="loud",
                      reason=f"loud from {start:g} s; the scene starts at {first:g} s")


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `video` in `inputs`, and `video.read` and `ffmpeg` in `permissions`.
- **On the sample:** cuts at 10, 20 and 30 s, and one moment, 20.0 to 30.0 s, "loud from 22 s; the scene starts at 20 s".
- **Cost:** 0.54 s, with the loudness.
- **How it fails:** a game with one camera that never cuts gives no cuts, and fast action gives a cut every second; raise or lower `threshold` to suit. A cut long before the loud part makes a long moment, which `around` cuts to the longest clip the job allows.

## Words said

`text.said` says when each word or phrase is said in the transcript Clips Kitty made, as whole words in any case or spacing. It needs no FFmpeg.

```yaml
inputs: [transcript]
outputs: [ranges]
permissions: [transcript.read]
```

```python
from clipskitty_sdk import run, signals, text

WORDS = text.words_of("quark burst, triple bloom")  # or a setting: text.words_of(job.settings["words"])


def main(job):
    for start, end, word in text.said(job, WORDS):
        job.add_range(*signals.around(job, start, end, lead=4, tail=6), label="words_said",
                      reason=f'the commentary says "{word}"')


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `transcript` in `inputs` and `transcript.read` in `permissions`. No FFmpeg.
- **On the sample:** one moment, 19.5 to 32.0 s, 'the commentary says "quark burst"' ("quark burst" is said from 23.5 to 26.0 s).
- **Cost:** under 0.01 s.
- **How it fails:** the transcript is what the speech recogniser heard, so a misheard or unusual word is missed. It matches only the words you list: a word said another way, or in another language, needs its own entry. A plugin without `transcript` in its inputs and `transcript.read` in its permissions gets no transcript, and the run stops with "text: this job has no transcript: add transcript to inputs and transcript.read to permissions". `text.hits(job, moment, words)` says which words are said during one moment, for a rater.

## Asking the local model about a frame

`local_model.ask` asks the creator's local model, the one they chose in Clips Kitty, with pictures when `local_model.can_see` says it can look at them. This recipe understands moments others found: it says what happens on screen in each one, and the notes reach the AI that writes the clips' titles ([Steps](steps.md)).

```yaml
inputs: [moments, video]
outputs: [context]
permissions: [video.read, ffmpeg, ollama]
```

```python
from clipskitty_sdk import local_model, media, run

QUESTION = "In one short sentence, what happens on screen?"


def main(job):
    if not local_model.can_see(job):
        job.finish(notes="The creator's local model can't look at pictures, so this adds no notes.")
        return
    for m in job.moments:
        picture = media.jpeg(job, (m.start + m.end) / 2)  # the middle frame, at most 896 pixels a side
        job.understand(m, local_model.ask(job, QUESTION, images=[picture]))


if __name__ == "__main__":
    run(main)
```

- **Needs:** SDK 1.2.0; `moments` and `video` in `inputs`, `context` in `outputs`, and `video.read`, `ffmpeg` and `ollama` in `permissions`; and a local model in the creator's Clips Kitty.
- **It needs the creator to use a local model in Clips Kitty.** When Clips Kitty's AI runs at a cloud provider, the job names no local model and this refuses: "No local model is set in Clips Kitty (its AI may run at a cloud provider), and this helper only uses a model on this PC". It talks only to a model on this PC (127.0.0.1, localhost or ::1), refuses an address anywhere else, never falls back to another address and never calls a cloud service. Its requests ignore proxy settings on purpose, so the frames never leave the PC. The model is always the one Clips Kitty names; a plugin can't choose another here.
- **On the sample,** with a stand-in for the model that answers every question with one sentence: each of the 5 sample moments gets that sentence as its note. No real model was run for this page.
- **Cost:** 0.45 s for the 5 frames and the stand-in's answers. A real model's own time comes on top, for each moment, and depends on the model and the PC; it wasn't measured.
- **How it fails:** without `ollama` in the permissions it stops with "This pipeline doesn't ask for the local model: add ollama to permissions in clipskitty.yaml"; when Ollama isn't running, with "The local model didn't answer at …: is Ollama running?", naming the address. A model that can't look at pictures gets no question here. A model's answer can be wrong, and a note is cut to 160 characters. With `json=True`, `ask` asks for JSON only (read it with `json.loads`).

There is no recipe for reading text on screen (OCR): it needs packages Clips Kitty can't install for a plugin yet ([Pipeline development](pipeline-development.md)).
