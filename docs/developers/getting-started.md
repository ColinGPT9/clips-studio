# Getting started

This page takes you from nothing to a pipeline plugin Clips Kitty can run. You need Python 3.10 or newer, FFmpeg, and a copy of Clips Kitty's repository for its SDK. You never change Clips Kitty's code.

## 1. Get the SDK on your path

```text
git clone https://github.com/ColinGPT9/clips-studio
set PYTHONPATH=C:\path\to\clips-studio\sdk\python          (Windows, cmd)
export PYTHONPATH=/path/to/clips-studio/sdk/python          (macOS, Linux)
pip install pyyaml                                          (to read manifests)
```

## 2. Start from the example

Copy [`examples/pipelines/scene-cut-highlights/`](../../examples/pipelines/scene-cut-highlights/) to a folder of your own; it will become your plugin's repository. Then, in `clipskitty.yaml`:

- set `id` to `<your GitHub name>/<plugin name>`, lower case;
- set `name`, `description` (what it detects, honestly), `version: 0.1.0`, `license`, `repository`;
- list the `permissions` you need: `video.read` for the file, `transcript.read` for what was said, `ffmpeg` for Clips Kitty's FFmpeg, `ollama` for the user's local AI model;
- declare your `settings`.

[Plugin manifest](plugin-manifest.md) has every field.

## 3. Write the detector

In `src/main.py`, read what you need from the job and add a range for each moment:

```python
from clipskitty_sdk import run


def main(job):
    for start, end, score in my_detector(job.video.path, job.settings):
        job.add_range(start, end, score=score, label="team_wipe", reason="5 eliminations in 6 s")


run(main)
```

[SDK](sdk.md) lists everything on `job`; [Pipeline development](pipeline-development.md) explains the contract and what Clips Kitty does with your moments.

## 4. Check it and run it

```text
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --video sample.mp4 --set min_kills=4
```

`validate` runs the checks Clips Kitty and the registry run. `run` builds the job folder exactly as the app does, runs your plugin, and prints the moments the app would take. When both pass, the app would accept your plugin and its answer.

## 5. Install it in Clips Kitty

With the app running, plan an install from your folder, read what it would do, then install it; the [Plugin development](plugin-development.md) page has a short script for this, and [`docs/API.md` › Plugins](../API.md#plugins) has the calls. Pick it for a job with `"pipeline": "<your id>"`. Change your code, plan and install again; the same version is replaced. In the desktop app the same is **For developers: install a pipeline you're writing**, a link at the bottom of **Marketplace → Browse**, and then the **Pipeline** switch on a video in the Generate bar.

## 6. Publish it

Push the folder to a public GitHub repository and tag a release (`git tag v0.1.0 && git push --tags`). How users install it, and how to get it listed in the Marketplace, is in [Marketplace publishing](marketplace-publishing.md).

## Where to go next

- [Example pipeline](example-pipeline.md): the example, line by line, and how it is tested
- [Permissions](permissions.md) and [Security](security.md): what users see before they install
- [Troubleshooting](troubleshooting.md)
