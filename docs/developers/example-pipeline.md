# Example pipeline

[`examples/pipelines/scene-cut-highlights/`](../../examples/pipelines/scene-cut-highlights/) is a complete pipeline plugin written the way an outside developer would write one: its own folder laid out as a repository, a manifest, a README, a licence and one script that imports nothing from Clips Kitty but the SDK. Copy it to start your own.

Status: **built** and tested end to end (`tests/test_example_pipeline.py`).

## What it detects, honestly

Stretches of the video that are clearly louder than the rest (6 dB above the median by default), each turned into a clip that starts on the last scene cut before it. It uses two FFmpeg filters: `ebur128` for loudness every tenth of a second, and the `scene` score for cuts.

It does **not** know what is on screen or what is said. Loud is not the same as interesting, and a quiet highlight is missed. The manifest's description and the README say so, because a listing must not claim more than the plugin does.

## The files

```text
clipskitty.yaml   id clips-kitty-examples/scene-cut-highlights; permissions video.read and ffmpeg; three settings
README.md         what it does and doesn't, settings, permissions, how to try it
LICENSE           MIT
src/main.py       135 lines
```

## How `src/main.py` works

```python
from clipskitty_sdk import run


def main(job):
    video, ffmpeg = str(job.video.path), job.tools.ffmpeg      # handed over because of video.read and ffmpeg
    job.progress(0.05, "Measuring loudness")
    levels = loudness_per_second(ffmpeg, video)                 # ffmpeg ... -af ebur128=metadata=1 ...
    job.progress(0.5, "Looking for scene cuts")
    cuts = scene_cuts(ffmpeg, video, job.settings["scene_threshold"])
    for stretch in loud_stretches(levels, job.settings["louder_by_db"]):
        start, end, on_cut = to_range(stretch, cuts, ...)       # start on a cut, keep within job.limits
        job.add_range(start, end, score=..., label="loud", reason="9 dB louder than the video's usual level")
    ...


run(main)
```

Things worth copying:

- **Use the FFmpeg Clips Kitty hands over** (`job.tools.ffmpeg`) rather than one on `PATH`; ask for it with the `ffmpeg` permission.
- **Respect `job.limits`**: the user's shortest and longest clip.
- **Report progress** at each step; it moves the job's bar inside "Finding the best moments".
- **Say why** in `reason`; it is saved with the clip.
- **Say when there is nothing**: with no loud stretch, it finishes with a note ("Nothing was clearly louder than the rest of the video") instead of failing.

## How it is tested

`tests/test_example_pipeline.py` makes a 40-second video with FFmpeg: four colours with hard cuts at 10, 20 and 30 seconds, and a tone that is loud only from 22 to 27 seconds. Then:

| Check | What it proves |
|---|---|
| Run through the app's own runner (`plugins/runner.find_clips`) | One clip, starting on the cut at 20 s, covering 22-27 s, at least the job's shortest length, recorded as `plugin:clips-kitty-examples/scene-cut-highlights@1.0.0` |
| Run on the same video with no loud part | No moments, and the note saying why |
| Run with `python -m clipskitty_sdk run` from outside the repository, `PYTHONPATH` set to the SDK alone | It needs nothing from Clips Kitty but the SDK |
| Parse `src/main.py` and list its imports | Standard library and `clipskitty_sdk` only |
| `validate_folder` | Valid with no warnings; local; sends nothing |
| Unit checks of `loud_stretches` and `to_range` | The clip boundaries: on a cut, a lead-in without one, the longest and shortest clip, the end of the video |

The test video's cut and loud part are at known times and the test checks the clip lands on them, so it cannot pass for the wrong reason.

## Trying it

```text
set PYTHONPATH=C:\path\to\clips-studio\sdk\python
python -m clipskitty_sdk validate examples\pipelines\scene-cut-highlights
python -m clipskitty_sdk run examples\pipelines\scene-cut-highlights --video some-video.mp4
```
