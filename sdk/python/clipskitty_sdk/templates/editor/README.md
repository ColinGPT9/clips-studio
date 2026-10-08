# @@NAME@@

A Clips Kitty plugin that suggests edits for the clips Clips Kitty makes, by the words said in them.

It is set up for Quarkbloom Arena (a made-up game): replace it with yours. There, the players wait for the "respawn timer" after a knockout, and a caster shouts "quark burst" at a big play, so it cuts the wait after the first and adds a hook title where the second is said. Put your game's own words in the marked block in `src/main.py`, and the words you want muted in the settings.

## What it does

It runs after Clips Kitty has chosen the clips and before it makes them, when you choose it under **Suggest edits** as you add a video. For each clip it suggests:

- a mute over each of the `mute_words` said in it, from a tenth of a second before the word to a tenth after, so the whole word is silent and its caption is hidden too;
- a cut of the wait after "respawn timer": from when it is said to 4 seconds after, when all of that is inside the clip;
- the hook title "Quark burst!" for the clip's first 3 seconds, when "quark burst" is said in it.

Each suggestion comes with a reason, and a clip with none of these gets no suggestion. Suggestions wait for you in the timeline editor, where you can use or hide each one. Clips Kitty doesn't put a suggestion into a clip until you use it in the editor and apply your edits (Apply edits, or Apply edits & upload).

Clips Kitty fits each suggestion to its clip as it reads it. Cuts that would leave the clip shorter than the video's shortest clip are left out, for example, and the job's log says so.

## What it can't see

It only reads what is said. It doesn't see the screen, it can't tell whether a word is about this clip, and a clip where nobody says its words gets no suggestion. It never finds moments of its own, and it doesn't change which clips are made.

## Settings

| Setting | Default | |
|---|---|---|
| `mute_words` | none | Words or phrases to mute, separated by commas |

## Try it

You need Python @@PYTHON@@, the Python Clips Kitty runs plugins on. FFmpeg isn't needed: this plugin never reads the video. In the plugin's folder, in PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
py -m clipskitty_sdk validate .
py -m clipskitty_sdk run . --sample
py -m clipskitty_sdk run . --sample --set "mute_words=round one"
py -m pytest -q
```

Or in bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --sample
python -m clipskitty_sdk run . --sample --set "mute_words=round one"
python -m pytest -q
```

`validate` checks `clipskitty.yaml` and the code the way Clips Kitty does. `run . --sample` hands the plugin 5 clips spread through the SDK's 40-second sample, with what is said in them, and shows each clip's suggestion as Clips Kitty would keep it. "quark burst" is said in the third clip, so it gets the hook title, and `--set "mute_words=round one"` mutes "round one" in the first. The sample's clips are too short for a cut, so the tests try one on a longer clip of their own. To suggest edits for clips of your own, pass `--moments clips.json` instead (a list of `{"start": ..., "end": ...}`, in seconds of the video) with `--transcript`. `pytest` runs `tests/test_main.py`. If you use a virtual environment, make it next to this folder, not inside it: Clips Kitty copies everything in the folder when it installs it.

## Use it in Clips Kitty

In a Clips Kitty that runs plugins ([which release does](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md#which-release-runs-plugins)), open **Marketplace › Browse**, choose **For developers: install a pipeline you're writing** at the bottom, and pick this folder. Then choose it under **Suggest edits** when you add a video, and open a clip in the editor to see what it suggests.

## Share it

Push this folder to GitHub as its own repository. Its id, `@@ID@@`, starts with `@@PUBLISHER@@`: for a Marketplace listing, that must be the GitHub name that owns the repository. `.github/workflows/clipskitty-check.yml` then checks it on Linux and Windows with Python @@PYTHON@@ on every push. How to get it listed in the Marketplace: [Marketplace publishing](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/marketplace-publishing.md).

## Files

```text
clipskitty.yaml        what Clips Kitty reads about the plugin: its inputs, outputs, permissions and settings
src/main.py            the plugin: Clips Kitty starts it with a job folder
tests/test_main.py     its tests, on the sample's transcript and on a clip of their own
LICENSE                your licence for the plugin
TEMPLATE-LICENSE.txt   the MIT licence of the code that came from the template
```

The SDK's helpers and the job's contract: [SDK](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md). How finding, understanding, rating and suggesting edits fit together: [Steps](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/steps.md).
