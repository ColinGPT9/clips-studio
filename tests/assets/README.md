# Test assets

Two things a contributor needs before they can run anything: a transcript
and a video. Neither can be committed as-is — real footage belongs to the
creator who recorded it, and video files do not belong in git — so one is
hand-written and the other is generated.

## `sample_transcript.json`

A written-out transcript in exactly the shape the transcriber caches
(`{video_id, language, segments: [{start, end, text}]}`), so anything that
reads a cached transcript will accept it unchanged.

Two minutes of plausible talking-head stream: a follower milestone, a story
with a punchline around 0:36, some setup advice, a surprise donation at
1:14, and an honest aside about burnout. It is written to give scoring
something to actually rank — a few real moments, a lot of filler, which is
the ratio a stream really has.

It also carries two deliberate traps:

- **"let's get it"** appears at 1:31 and again at 1:56. Repeated, so a
  catchphrase detector *should* pick it up.
- **"I have never been so humbled in my entire life"** appears once. Vivid,
  memorable, and exactly the kind of line that used to get mistaken for a
  catchphrase. It should not be treated as one.

That pair is why `MIN_PHRASE_REPEATS` exists. If a change to creator
knowledge makes the second line register as a catchphrase, that is the
regression.

In tests:

```python
def test_something(sample_transcript):
    assert sample_transcript[0].text.startswith("yo what is good")
```

The `sample_transcript` fixture (in `conftest.py`) returns `Segment`
objects, already parsed.

## `sample_video.mp4` — generated, not committed

```
python tests/assets/make_sample_video.py
```

Two minutes of 1080p30 H.264 (~87 MB) with a burnt-in timecode, plus a tone
whose loudness peaks on a **known schedule** — 0:20, 0:36, 1:12 and 1:38,
with 0:36 the loudest. Those four are not aspirational; they were checked
against `analysis/audio_features.py`, which finds them at exactly those
times. So audio-signal detection has peaks whose right answers are known in
advance, and the burnt-in timecode means you can open a rendered clip and
read off whether it was cut where you asked for.

Gitignored. Regenerate it whenever; it is deterministic.

**What it cannot tell you:** whether clips are any good. There are no faces,
so tracking has nothing to track; there is no speech, so transcribing it
returns nothing useful. It exercises the plumbing — decode, cut, render,
burn captions, export — not the judgment. Judgment needs a real video and a
person watching the output, every time. Same limitation as CI; see
[../README.md](../README.md).
