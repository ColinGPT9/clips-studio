# @@NAME@@

A Clips Kitty plugin that rates the moments found in a video by the words said in them.

It is set up for Quarkbloom Arena (a made-up game): replace it with yours. There, a caster shouts "quark burst" at a big play and talks about "waiting" when nothing happens, so those are its good and dull words. Put your game's and your community's own words in the settings.

## What it does

It runs after the moments are found, by Clips Kitty or by a pipeline, when you choose it under **Rate & understand** as you add a video. For each moment:

- When one of the `good_words` is said during it, the moment gains `bonus` points, never going above 100.
- Otherwise, when one of the `dull_words` is said, the moment drops to `dull_score`. A moment already scored lower keeps its score.
- Any other moment keeps the score it had.

Scores decide which clips are made and their order, and which are posted when a channel posts only its best few. A word matches as a whole word in any case, so `win` doesn't match `window`.

## What it can't see

It only reads what is said. It doesn't see the screen, it can't tell whether a word is about this moment, and a moment nobody talks about keeps its score. It never finds moments of its own.

## Settings

| Setting | Default | |
|---|---|---|
| `good_words` | `quark burst` | Words or phrases that make a moment better, separated by commas |
| `bonus` | 15 | Points a moment gains when one of them is said (1 to 50) |
| `dull_words` | `waiting` | Words or phrases that make a moment dull, separated by commas |
| `dull_score` | 10 | The score a dull moment drops to |

## Try it

You need Python @@PYTHON@@, the Python Clips Kitty runs plugins on. FFmpeg isn't needed: this plugin never reads the video. In the plugin's folder, in PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
py -m clipskitty_sdk validate .
py -m clipskitty_sdk run . --sample
py -m clipskitty_sdk run . --sample --set "good_words=quark burst, triple bloom"
py -m pytest -q
```

Or in bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --sample
python -m clipskitty_sdk run . --sample --set "good_words=quark burst, triple bloom"
python -m pytest -q
```

`validate` checks `clipskitty.yaml` and the code the way Clips Kitty does. `run . --sample` hands the plugin 5 moments spread through the SDK's 40-second sample, each scored 60, with what is said in them. To rate moments of your own, pass `--moments moments.json` instead (a list of `{"start": ..., "end": ..., "score": ...}`, or a pipeline's `result.json`) with `--transcript`. `pytest` runs `tests/test_main.py`. If you use a virtual environment, make it next to this folder, not inside it: Clips Kitty copies everything in the folder when it installs it.

## Use it in Clips Kitty

In a Clips Kitty that runs plugins ([which release does](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md#which-release-runs-plugins)), open **Marketplace › Browse**, choose **For developers: install a pipeline you're writing** at the bottom, and pick this folder. Then choose it under **Rate & understand** when you add a video.

## Share it

Push this folder to GitHub as its own repository. Its id, `@@ID@@`, starts with `@@PUBLISHER@@`: for a Marketplace listing, that must be the GitHub name that owns the repository. `.github/workflows/clipskitty-check.yml` then checks it on Linux and Windows with Python @@PYTHON@@ on every push. How to get it listed in the Marketplace: [Marketplace publishing](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/marketplace-publishing.md).

## Files

```text
clipskitty.yaml        what Clips Kitty reads about the plugin: its inputs, outputs, permissions and settings
src/main.py            the plugin: Clips Kitty starts it with a job folder
tests/test_main.py     its tests, on the sample video's transcript
LICENSE                your licence for the plugin
TEMPLATE-LICENSE.txt   the MIT licence of the code that came from the template
```

The SDK's helpers and the job's contract: [SDK](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md). How finding, understanding and rating fit together: [Steps](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/steps.md).
