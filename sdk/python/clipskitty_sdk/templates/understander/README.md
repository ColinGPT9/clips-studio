# @@NAME@@

A Clips Kitty plugin that notes what happens in the moments found in a video, from the screen and the words said, so their titles can say it.

It is set up for Quarkbloom Arena (a made-up game): replace it with yours. In that game a red "quark burst" banner shows at the top of the screen after a big play, and the casters say "round one" and "overtime". Measure where your game shows something worth noting with `frame` (below), and change the settings and the marked block in `src/main.py`.

## What it does

It runs after the moments are found, by Clips Kitty or by a pipeline, when you choose it under **Rate & understand** as you add a video. For each moment it adds a note when:

- the banner (`banner_region`, `banner_colour`) starts showing during it: "The quark burst banner shows from 22.0 s";
- the commentary says one of the `cue_words` during it: "The commentary says "round one" here".

Clips Kitty gives the notes to the AI that writes each clip's title, description and hashtags. This plugin doesn't change which moments become clips, or their scores.

## What it can't see

It sees only that part of the screen and what is said. It can't read what the banner says, anything else of that colour in the same place fools it, and it can't tell whether the words are about the moment.

## Settings

| Setting | Default | |
|---|---|---|
| `banner_region` | `0.30,0.10,0.40,0.10` | Where the banner shows, as left, top, width, height (0 to 1) |
| `banner_colour` | `e0303a` | The banner's colour, as 6 hex digits (red, green, blue) |
| `cue_words` | `round one, overtime` | Words or phrases to note when they are said, separated by commas |

## Try it

You need Python @@PYTHON@@ (the Python Clips Kitty runs plugins on) and FFmpeg. In the plugin's folder, in PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
py -m clipskitty_sdk validate .
py -m clipskitty_sdk run . --sample
py -m clipskitty_sdk frame "$env:USERPROFILE\Videos\match.mp4" --at 24 --region "0.30,0.10,0.40,0.10" --out ..\frame.png
py -m pytest -q
```

Or in bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --sample
python -m clipskitty_sdk frame ~/Videos/match.mp4 --at 24 --region "0.30,0.10,0.40,0.10" --out ../frame.png
python -m pytest -q
```

`validate` checks `clipskitty.yaml` and the code the way Clips Kitty does. `run . --sample` hands the plugin 5 moments spread through the SDK's 40-second sample video, whose red banner shows from 22 to 27 s and where "round one" is said at 8 s. `frame` writes one frame of your own recording with a box around a region, and prints the region in pixels; it writes outside this folder, because Clips Kitty copies everything in the folder when it installs it. For the same reason, if you use a virtual environment, make it next to this folder, not inside it. `pytest` runs `tests/test_main.py`.

## Use it in Clips Kitty

In a Clips Kitty that runs plugins ([which release does](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md#which-release-runs-plugins)), open **Marketplace › Browse**, choose **For developers: install a pipeline you're writing** at the bottom, and pick this folder. Then choose it under **Rate & understand** when you add a video.

## Share it

Push this folder to GitHub as its own repository. Its id, `@@ID@@`, starts with `@@PUBLISHER@@`: for a Marketplace listing, that must be the GitHub name that owns the repository. `.github/workflows/clipskitty-check.yml` then checks it on Linux and Windows with Python @@PYTHON@@ on every push. How to get it listed in the Marketplace: [Marketplace publishing](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/marketplace-publishing.md).

## Files

```text
clipskitty.yaml        what Clips Kitty reads about the plugin: its inputs, outputs, permissions and settings
src/main.py            the plugin: Clips Kitty starts it with a job folder
tests/test_main.py     its tests, on the SDK's sample video
LICENSE                your licence for the plugin
TEMPLATE-LICENSE.txt   the MIT licence of the code that came from the template
```

The SDK's helpers and the job's contract: [SDK](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md). How finding, understanding and rating fit together: [Steps](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/steps.md).
