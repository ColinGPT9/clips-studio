# @@NAME@@

A Clips Kitty pipeline that finds the moments when a coloured banner shows on screen and the sound gets louder.

It is set up for Quarkbloom Arena (a made-up game): replace it with yours. In that game a red "quark burst" banner shows at the top of the screen after a big play, and the crowd gets loud. Your game may show a banner, a kill feed or an icon instead: measure where it shows with `frame` (below) and change the settings, and the marked block in `src/main.py`.

## What it does

1. It looks at the banner's part of the screen (`banner_region`) 4 times a second and notes when most of it is the banner's colour (`banner_colour`).
2. Each time the banner shows becomes a moment, from 6 seconds before it shows to 3 seconds after it goes. With `needs_loud`, only when the sound at that time is at least `louder_by_db` louder than the half minute around it.
3. Clips Kitty then cuts, frames and captions the clips, as it does for every video.

## What it can't see

It sees only that part of the screen and how loud the video is. It can't read what the banner says, anything else of that colour in the same place fools it, and a moment without the banner is missed. A stream whose layout moves the game (a webcam over it, or the game shown smaller) needs its own `banner_region`.

## Settings

| Setting | Default | |
|---|---|---|
| `banner_region` | `0.30,0.10,0.40,0.10` | Where the banner shows, as left, top, width, height (0 to 1) |
| `banner_colour` | `e0303a` | The banner's colour, as 6 hex digits (red, green, blue) |
| `louder_by_db` | 6 | How much louder the sound gets at the same time (dB) |
| `needs_loud` | true | Only when the sound gets louder too |

## Try it

You need Python @@PYTHON@@ (the Python Clips Kitty runs plugins on) and FFmpeg. In the plugin's folder, in PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
py -m clipskitty_sdk validate .
py -m clipskitty_sdk run . --sample
py -m clipskitty_sdk frame "$env:USERPROFILE\Videos\match.mp4" --at 24 --region "0.30,0.10,0.40,0.10" --out ..\frame.png
py -m clipskitty_sdk run . --video "$env:USERPROFILE\Videos\match.mp4" --set "banner_region=0.30,0.10,0.40,0.10"
py -m pytest -q
```

Or in bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --sample
python -m clipskitty_sdk frame ~/Videos/match.mp4 --at 24 --region "0.30,0.10,0.40,0.10" --out ../frame.png
python -m clipskitty_sdk run . --video ~/Videos/match.mp4 --set "banner_region=0.30,0.10,0.40,0.10"
python -m pytest -q
```

`validate` checks `clipskitty.yaml` and the code the way Clips Kitty does. `run . --sample` runs the plugin on the SDK's 40-second sample video, whose red banner shows from 22 to 27 s with a loud sound. `frame` writes one frame of your own recording with a box around a region, and prints the region in pixels, so you can see where your game's banner is; it writes outside this folder, because Clips Kitty copies everything in the folder when it installs it. For the same reason, if you use a virtual environment, make it next to this folder, not inside it. `pytest` runs `tests/test_main.py`.

## Use it in Clips Kitty

In a Clips Kitty that runs plugins ([which release does](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/versioning.md#which-release-runs-plugins)), open **Marketplace › Browse**, choose **For developers: install a pipeline you're writing** at the bottom, and pick this folder. Then choose it as the **Pipeline** when you add a video.

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

The SDK's helpers and the job's contract: [SDK](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md).
