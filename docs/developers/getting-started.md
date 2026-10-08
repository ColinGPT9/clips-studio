# Getting started

This page takes you from nothing to a pipeline plugin Clips Kitty can run, in short steps. [Your first game pipeline](first-game-pipeline.md) does the same with every command's output, for one made-up game. You need Python 3.11, the version Clips Kitty's own Python is (any 3.10 or newer works for the SDK, but test on 3.11), FFmpeg, and Git. You never change Clips Kitty's code.

The commands are shown for PowerShell on Windows (`py -m`) and for bash on macOS or Linux (`python -m`).

## 1. Install the SDK

PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.

How: [From source](../../README.md#from-source). If you use a virtual environment, keep it next to your plugin's folder, not inside it.

## 2. Start from a template

PowerShell:

```powershell
py -m clipskitty_sdk new my-plugin --template blank --publisher your-github-name
```

bash:

```bash
python -m clipskitty_sdk new my-plugin --template blank --publisher your-github-name
```

Use your own GitHub name, in lower case. `new --list` shows the five templates: `blank`, `transcript`, `game-events`, `rater` and `understander`. In the new folder's `clipskitty.yaml`:

- `id` is `<your GitHub name>/<plugin name>`, lower case;
- set `name`, `description` (what it detects, honestly), `license` and `repository`;
- list the `permissions` you need: `video.read` for the file, `transcript.read` for what was said, `ffmpeg` for Clips Kitty's FFmpeg, `ollama` for the user's local AI model;
- declare your `settings`.

[Plugin manifest](plugin-manifest.md) has every field.

## 3. Write the detector

In `src/main.py`, read what you need from the job and add a range for each moment:

```python
from clipskitty_sdk import run


def main(job):
    for start, end, score in my_detector(job.video.path, job.settings):
        job.add_range(start, end, score=score, label="big_play", reason="5 eliminations in 6 s")


if __name__ == "__main__":
    run(main)
```

Import only the standard library and `clipskitty_sdk`, at the top of the file: Clips Kitty runs plugins on its own Python, which has nothing else. The SDK's `media`, `signals` and `text` modules read frames, loudness, scene cuts and words for you ([Signals cookbook](signals-cookbook.md)). [SDK](sdk.md) lists everything on `job`; [Pipeline development](pipeline-development.md) explains the contract and what Clips Kitty does with your moments.

## 4. Check it and run it

PowerShell:

```powershell
py -m clipskitty_sdk validate my-plugin
py -m clipskitty_sdk run my-plugin --sample
```

bash:

```bash
python -m clipskitty_sdk validate my-plugin
python -m clipskitty_sdk run my-plugin --sample
```

`validate` runs the checks Clips Kitty and the registry run, and warns about code that would fail on Clips Kitty's Python. `run --sample` builds the job folder exactly as the app does, on a 40-second test video, runs your plugin, and prints the moments the app would take. When both pass, the app would accept your plugin and its answer. Then try it on your own recordings with `--video`.

## 5. Install it in Clips Kitty

In a Clips Kitty that runs plugins (see step 1), with the app running:

PowerShell:

```powershell
py -m clipskitty_sdk install my-plugin --watch
```

bash:

```bash
python -m clipskitty_sdk install my-plugin --watch
```

It shows Clips Kitty's own install screen as text and asks first, then reinstalls the plugin each time you save ([SDK › Installing it into Clips Kitty](sdk.md#installing-it-into-clips-kitty)). In the desktop app the same is **For developers: install a pipeline you're writing**, a link at the bottom of **Marketplace → Browse** ([Plugin development](plugin-development.md)). Then choose it with the **Pipeline** switch on a video in the Generate bar.

## 6. Publish it

Push the folder to a public GitHub repository and tag a release, in the plugin's folder:

```text
git tag v0.1.0
git push origin v0.1.0
```

Then make its catalog listing, from the folder that holds the plugin's folder, with `python -m clipskitty_sdk listing my-plugin --section gaming/generic` (in PowerShell, `py -m clipskitty_sdk`). How users install it, and how to get it listed in the Marketplace, is in [Marketplace publishing](marketplace-publishing.md).

## Where to go next

- [Your first game pipeline](first-game-pipeline.md): the same steps for one game, with every command's output
- [Signals cookbook](signals-cookbook.md): a recipe for each thing a game pipeline reads
- [Example pipeline](example-pipeline.md): a complete one, laid out as its own repository, and how it is tested
- [Permissions](permissions.md) and [Security](security.md): what users see before they install
- [Troubleshooting](troubleshooting.md)
