# Your first game pipeline

This page builds a pipeline plugin for one game, from nothing to a plugin Clips Kitty runs. The game is **Quarkbloom Arena, a made-up game**: when a player makes a big play, a red "quark burst" banner shows at the top of the screen and the crowd gets loud. The plugin finds those moments in a recording, and Clips Kitty turns them into clips. Your game will show something else; the steps are the same.

> **Which Clips Kitty runs it.** No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source. Steps 1 to 6 need only Python and FFmpeg. Step 7 needs a Clips Kitty that runs plugins: until a release does, run it from source ([From source](../../README.md#from-source)).

You need:
- **Python 3.11**, the Python Clips Kitty runs plugins on. The SDK works on 3.10 or newer, but test on 3.11: newer Python code fails there.
- **FFmpeg** on `PATH`, for the sample video and for reading your recordings.
- **Git**, to install the SDK until it is on PyPI, and to share your plugin.

Every command is shown twice: for PowerShell on Windows (`py -m clipskitty_sdk`), then for bash on macOS or Linux (`python -m clipskitty_sdk`). Run them all from one folder of your own, the folder that will **hold** your plugin's folder.

## 1. Install the SDK

PowerShell:

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

bash:

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
```

`yaml` adds PyYAML, which reads `clipskitty.yaml`; `test` adds pytest, for your plugin's tests. Check it with `py -m clipskitty_sdk --version` (bash: `python -m clipskitty_sdk --version`), which prints `clipskitty-sdk 1.2.0 (plugin contract 1)`.

If you use a virtual environment, make it in the folder that will hold your plugin's folder, never inside the plugin's folder: Clips Kitty copies everything in the plugin's folder when it installs it, and `install` (step 7) refuses a folder with `.venv`, `venv` or `node_modules` in it.

## 2. Make the plugin

PowerShell:

```powershell
py -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name
```

bash:

```bash
python -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name
```

```text
Made quarkbloom-bursts from the game-events template.
Next: python -m clipskitty_sdk run quarkbloom-bursts --sample
Then change clipskitty.yaml, src/main.py and README.md for your game:
https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/first-game-pipeline.md
```

Put your own GitHub name, in lower case, in place of `your-github-name`: it becomes the publisher in the plugin's id, `your-github-name/quarkbloom-bursts`. On Windows the Next line may say `py -m clipskitty_sdk`: it names the Python that ran `new`. `new --list` shows the other templates: `blank`, `transcript`, `rater` and `understander`.

`new` wrote these files:

```text
quarkbloom-bursts/
  clipskitty.yaml                         what Clips Kitty reads about the plugin: its id, inputs, outputs, permissions and settings
  src/main.py                             the plugin: Clips Kitty starts it with a job folder
  tests/test_main.py                      its tests, on the SDK's sample video
  README.md                               what it does and can't do, for the people who install it
  CHANGELOG.md
  LICENSE                                 MIT, your licence for the plugin (change it if you like)
  TEMPLATE-LICENSE.txt                    the MIT licence of the code that came from the template
  .gitignore
  .github/workflows/clipskitty-check.yml  checks the plugin on GitHub, on Linux and Windows, with Python 3.11
```

## 3. Run it on the sample

PowerShell:

```powershell
py -m clipskitty_sdk run quarkbloom-bursts --sample
```

bash:

```bash
python -m clipskitty_sdk run quarkbloom-bursts --sample
```

```text
note: --sample: a 40-second test video with a red banner at the top and a loud sound from 22 to 27 s, and a transcript that says "quark burst" there
job folder: …
   10%  Looking for the banner
   70%  Listening for the sound getting louder
1 moment(s), as Clips Kitty would take them:
      16.0s      29.8s  score   -  quark_burst
       why: the banner shows from 22 to 26.75 s, and the sound gets 25 dB louder
notes: The banner shows 1 time(s), 1 of them as the sound gets louder.
```

`--sample` makes a 40-second test video in a new job folder, kept so you can look inside it, and runs the plugin on it the way Clips Kitty would: the same `job.json`, the same checks of the answer. The video has the red banner from 22 to 27 seconds, with a loud sound at the same time. The plugin found one moment, from 6 seconds before the banner to 3 seconds after it (16.0 to 29.75 s, shown rounded), labelled `quark_burst`, with its reason. On a terminal the progress lines are rewritten in place.

How it works, in `src/main.py`:
1. `media.frames` reads the banner's part of the screen 4 times a second, each frame shrunk to 32x8 pixels.
2. `signals.colour_share` says how much of each frame is the banner's red; `signals.stretches` joins the frames where most of it is into the times the banner shows.
3. `media.loudness` measures each second, and `signals.spikes` finds the seconds that are louder than the half minute around them.
4. `signals.around` puts 6 seconds before and 3 after each stretch, and fits it to the clip lengths the creator chose.

The [Signals cookbook](signals-cookbook.md) has these building blocks one at a time, and the [SDK](sdk.md) has every call.

## 4. Measure your screen with `frame`

Your game's banner, kill feed or icon is somewhere else. `frame` writes one frame of a video as a PNG, with a box drawn around a region, and prints the region in pixels. Run it from the folder that holds the plugin: `frame` won't write inside a plugin's folder, because Clips Kitty copies everything there when it installs it.

First, on the sample (`sample` writes the test video and its transcript where you say):

PowerShell:

```powershell
py -m clipskitty_sdk sample sample.mp4
py -m clipskitty_sdk frame sample.mp4 --at 24 --region "0.30,0.10,0.40,0.10" --out frame.png
```

bash:

```bash
python -m clipskitty_sdk sample sample.mp4
python -m clipskitty_sdk frame sample.mp4 --at 24 --region "0.30,0.10,0.40,0.10" --out frame.png
```

```text
wrote sample.mp4 and sample.transcript.json: a 40-second test video with a red banner at the top and a loud sound from 22 to 27 s, and a transcript that says "quark burst" there
wrote frame.png: the frame at 24 s of this 640x360 video
region "0.30,0.10,0.40,0.10" is x=192 y=36 w=256 h=36 on this 640x360 video
```

A region is four numbers from 0 to 1: its left edge, its top edge, its width and its height, as parts of the frame's width and height. So `0.30,0.10,0.40,0.10` starts 30% of the way across and 10% of the way down, and is 40% of the frame wide and 10% high. The same region fits a recording of any size. Quote it in PowerShell, which otherwise splits it at the commas.

Then on a recording of your own game, at a second where your banner shows. Open `frame.png`, look at where the box is, change the four numbers and run it again until the box sits around the banner:

PowerShell:

```powershell
py -m clipskitty_sdk frame "$env:USERPROFILE\Videos\match.mp4" --at 95 --region "0.30,0.10,0.40,0.10" --out frame.png
```

<!-- not run by tests/test_plugin_docs.py: it needs your own recording -->

bash:

```bash
python -m clipskitty_sdk frame ~/Videos/match.mp4 --at 95 --region "0.30,0.10,0.40,0.10" --out frame.png
```

Read the banner's colour from `frame.png` with any colour picker, as 6 hex digits (red, green, blue), such as `e0303a`.

## 5. Change it for your game

The plugin's settings are in `clipskitty.yaml`. Their defaults are what a job gets unless the creator chooses otherwise:

```yaml
settings:
  banner_region:
    type: string
    default: "0.30,0.10,0.40,0.10"
    max_length: 100
    title: Where the banner shows, as left, top, width, height (0 to 1)
  banner_colour:
    type: string
    default: "e0303a"
    max_length: 7
    title: The banner's colour, as 6 hex digits (red, green, blue)
  louder_by_db:
    type: number
    default: 6
    minimum: 1
    maximum: 30
    title: How much louder the sound gets at the same time (dB)
  needs_loud:
    type: boolean
    default: true
    title: Only when the sound gets louder too
```

Try a value with `--set` before you change a default. A value holding commas goes in quotes. Here `louder_by_db=30` asks for a sound far louder than the sample's, so the banner alone doesn't count:

PowerShell:

```powershell
py -m clipskitty_sdk run quarkbloom-bursts --sample --set "banner_region=0.30,0.10,0.40,0.10" --set louder_by_db=30
```

bash:

```bash
python -m clipskitty_sdk run quarkbloom-bursts --sample --set "banner_region=0.30,0.10,0.40,0.10" --set louder_by_db=30
```

```text
note: --sample: a 40-second test video with a red banner at the top and a loud sound from 22 to 27 s, and a transcript that says "quark burst" there
job folder: …
   10%  Looking for the banner
   70%  Listening for the sound getting louder
0 moment(s), as Clips Kitty would take them:
notes: The banner shows 1 time(s), 0 of them as the sound gets louder.
```

Then put your region and colour in as the defaults of `banner_region` and `banner_colour`. In `src/main.py`, change the marked block:

```python
# ---- your game: change these ------------------------------------------------------------
LABEL = "quark_burst"     # the kind of moment; list it under events in clipskitty.yaml
SHARE = 0.5               # how much of the banner's part of the screen must be its colour (0 to 1)
FRAMES_PER_SECOND = 4     # how often to look
LEAD_SECONDS = 6.0        # seconds kept before the banner shows
TAIL_SECONDS = 3.0        # seconds kept after it goes
# ------------------------------------------------------------------------------------------
```

When you change `LABEL`, change `events` in `clipskitty.yaml` to match: the Marketplace lists only the labels there, and `run` warns about any other. Change `name`, `description` and `games` there too, and say honestly what the plugin finds and what it misses. Last, rewrite `README.md` for your game, without the "Quarkbloom Arena (a made-up game)" line, and change `tests/test_main.py` as you change the plugin.

The plugin runs on Clips Kitty's own Python, which has the standard library and `clipskitty_sdk` and nothing else, so import only those, at the top of the file. `validate` and `run` warn about anything else, and about text files opened without `encoding="utf-8"`.

## 6. Run it on your own recording

PowerShell:

```powershell
py -m clipskitty_sdk run quarkbloom-bursts --video "$env:USERPROFILE\Videos\match.mp4"
```

<!-- not run by tests/test_plugin_docs.py: it needs your own recording -->

bash:

```bash
python -m clipskitty_sdk run quarkbloom-bursts --video ~/Videos/match.mp4
```

It prints the moments Clips Kitty would take, best first, each with its reason. Watch those seconds in the recording: is each one a moment worth a clip, and is a big play missing? Change the region, the colour, `louder_by_db` or the code, and run it again. Exit code 0 means Clips Kitty would accept the answer, 1 that the plugin failed or Clips Kitty would refuse its answer, 2 that the run couldn't start.

## 7. Put it in Clips Kitty

This needs a Clips Kitty that runs plugins: no release does yet (see the note at the top), so until one does, run Clips Kitty from source. With Clips Kitty open:

PowerShell:

```powershell
py -m clipskitty_sdk install quarkbloom-bursts --watch
```

<!-- not run by tests/test_plugin_docs.py: it needs Clips Kitty running -->

bash:

```bash
python -m clipskitty_sdk install quarkbloom-bursts --watch
```

`install` checks the plugin, prints Clips Kitty's own install screen as text (what the plugin may do, and whether anything leaves the PC), and asks `Install it? [y/N]`. With `--watch` it then reinstalls the plugin each time you save a file, until a save adds something the install screen would show, such as a new permission; Ctrl+C stops it. It talks only to the Clips Kitty on this PC. [SDK › Installing it into Clips Kitty](sdk.md#installing-it-into-clips-kitty) has the details.

Or, in Clips Kitty, open **Marketplace › Browse** and choose **For developers: install a pipeline you're writing** at the bottom, then pick the plugin's folder.

Then add a video and choose **Quarkbloom Bursts** as its **Pipeline**. Clips Kitty cuts, frames and captions the moments it finds, as it does for every video.

## 8. Share it

This part is for your real plugin, once it finds your game's moments. A catalog listing for the template as it is would be refused: `listing` checks for the template's placeholders, such as `your-github-name`, `quarkbloom-arena` and `quark_burst`.

Run the plugin's tests, in its folder:

PowerShell:

```powershell
cd quarkbloom-bursts
py -m pytest -q
```

bash:

```bash
cd quarkbloom-bursts
python -m pytest -q
```

```text
...                                                                      [100%]
3 passed in …
```

Make an empty repository on GitHub with the plugin's name, then push the folder to it, and tag the version. In the plugin's folder:

PowerShell:

```powershell
git init -b main
git add .
git commit -m "Quarkbloom Bursts 0.1.0"
git remote add origin https://github.com/your-github-name/quarkbloom-bursts.git
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

<!-- not run by tests/test_plugin_docs.py: it pushes to GitHub -->

bash:

```bash
git init -b main
git add .
git commit -m "Quarkbloom Bursts 0.1.0"
git remote add origin https://github.com/your-github-name/quarkbloom-bursts.git
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

The workflow `new` wrote, `.github/workflows/clipskitty-check.yml`, then checks the plugin and runs its tests on every push, on Linux and Windows, with Python 3.11.

To list it in the Marketplace, make its listing file from the folder that holds the plugin:

PowerShell:

```powershell
cd ..
py -m clipskitty_sdk listing quarkbloom-bursts --section gaming/generic
```

<!-- not run by tests/test_plugin_docs.py: it needs the plugin pushed to GitHub -->

bash:

```bash
cd ..
python -m clipskitty_sdk listing quarkbloom-bursts --section gaming/generic
```

`listing` checks that everything is committed and pushed and that your GitHub name owns the repository, writes the listing file, and says what to do next: add it to Awesome Clips Kitty, the catalog the Marketplace reads, with the catalog's index rebuilt, in one pull request. Use your game's own section if `awesome-clips-kitty/registry/sections.yaml` has one. [Marketplace publishing](marketplace-publishing.md) has the rest.

## What it doesn't do

- It sees only the banner's part of the screen and how loud the video is. It can't read the banner's words; anything else of that colour in that place fools it; a big play without the banner is missed.
- A stream that moves the game (a webcam over it, or the game shown smaller) puts something else in the region. Such a stream needs its own `banner_region`.
- Loud isn't the same as exciting: commentary or music at the same volume all along hides the crowd.
- It finds moments only. Saying what happens in them (understand) and scoring moments others found (rate) are other plugins' steps, or other templates ([Steps](steps.md)). Edit and export plugins are coming later.
- It runs on the creator's PC with the creator's rights, like any program; Clips Kitty doesn't sandbox it ([Security](security.md)).

## Where to go next

- [Signals cookbook](signals-cookbook.md): a region's frames, colours, brightness, an icon, loudness, scene cuts, words said, and asking the creator's local model about a frame.
- [SDK](sdk.md): every call and command.
- [Steps](steps.md): find, understand and rate, and how their answers combine.
- [Troubleshooting](troubleshooting.md): the messages, and what to do.
