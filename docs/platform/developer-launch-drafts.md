**DRAFT. Nothing here is posted, published or created. Each item needs Colin's approval (see the SDK plan's approval list).**

# Developer launch drafts

Drafts for telling developers about the Clips Kitty SDK. Each one waits for Colin. None of them has been posted, uploaded or set up anywhere, though this file itself can be read on the public branch (below).

The channels' rules quoted below were read on 2026-10-08, from the pages linked beside them. Every claim about Clips Kitty was checked against this repository's code and docs on the same day.

## What each item waits for

| Item | What it waits for |
|---|---|
| [Show HN](#show-hn) | Colin's go-ahead, and the text written by Colin himself (HN's rule, below) |
| [DEV](#dev) | Colin's go-ahead |
| [PyCoder's Weekly](#pycoders-weekly) | Colin's go-ahead |
| [Lobsters](#lobsters) | Colin's go-ahead, from his own Lobsters account |
| [GitHub Discussions categories](#github-discussions-categories) | Colin's go-ahead to add the four categories |
| [The template repository](#the-template-repository) | Colin's go-ahead to create the repository, and his choice of licence for template output (MIT-0 or CC0-1.0) |
| [The "Built for Clips Kitty" badge](#the-built-for-clips-kitty-badge) | Colin's go-ahead for a new public label |
| [The CONTRIBUTING licence sentence](#the-contributing-licence-sentence) | Colin's confirmation of the terms contributions are made under |
| [A PyPI release workflow](#a-pypi-release-workflow) | Colin's go-ahead to publish `clipskitty-sdk` to PyPI |

## Before any of it

- **Already readable; merging waits.** This branch is public and has been pushed, as Colin's brief asks after every commit (`docs/platform/BRIEF.md`), so this file, the source of the developers page (`site/developers.html`), the root README's SDK section and the developer docs can already be read on it. Nothing is posted anywhere, and the site isn't deployed. Merging it deploys `site/` to GitHub Pages (`.github/workflows/pages.yml`), and the Hugging Face Space mirror too when its secrets are set (`.github/workflows/mirror.yml`). Merging waits for Colin.
- **What works only after the merge.** `main` doesn't have `sdk/python/pyproject.toml` yet. Until this branch is merged, the `pip install … @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python` line fails, the `blob/main` links below go to pages `main` doesn't have, and a template's own GitHub workflow can't install the SDK. Post nothing before the merge.
- **Which release runs plugins.** No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source. A developer can try the SDK on its own (`new`, `run --sample`) as soon as the merge is in, but putting a plugin into Clips Kitty needs a source run until then. Show HN asks for work that people can try (below), so the best time to post is once a release runs plugins.
- **Windows.** Every PowerShell command here was written to the docs' rules (`py -m`, commas quoted, no `&&`) but none has been run on Windows. Run the three Quickstart commands on a Windows PC first.
- **WoopSocial.** The drafts name [WoopSocial](https://woopsocial.com/?via=clipskitty) where they say how clips get posted. <sub>Affiliate link - Clips Kitty may earn a commission if you sign up through it, at no extra cost to you.</sub> The drafted posts name it in plain text: before publishing one, add the link and that note, where the channel allows affiliate links.
- **The made-up game.** Search the web for "Quarkbloom Arena" again before posting, to check it is still nobody's real game.
- **Who writes it.** Hacker News asks for text written by the person posting (quoted below). The Show HN part is a list of facts to write from, not text to paste. The DEV draft is a whole post, and it says at the end that AI helped write it, as DEV's guidelines ask.

## Show HN

**Rules** (https://news.ycombinator.com/showhn.html):
- "Show HN is for something you've made that other people can play with."
- "If your work isn't ready for users to try out, please don't do a Show HN. Once it's ready, come back and do it then."
- "Don't post landing pages or fundraisers." So the link is the SDK's README in the repository, not the website's developers page.
- "To post, submit a story whose title begins with "Show HN"."
- "The project must be something you've worked on personally and which you're around to discuss."
- "Please don't ask friends to upvote or comment."

And from the site's guidelines (https://news.ycombinator.com/newsguidelines.html): "Please don't put generated text in HN posts. Write your text yourself—HN is for sharing between humans."

**Title:** Show HN: Clips Kitty SDK – Python plugins that find, rate and describe moments in a local video clipper

HN's submit form needs an account, so how long a title it takes wasn't checked. If that one is cut short, a shorter one: Show HN: Clips Kitty SDK – Python plugins for a local video clipper

**Link:** https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/README.md

**What the text could say, in Colin's own words.** Each point is checked against the code:
- **What it is.** Clips Kitty is a free, open-source app that turns long videos and streams into short clips on the creator's own PC. The SDK is a small Python package for writing plugins that teach it what a good moment looks like in a particular game or kind of show.
- **In and out.** Clips Kitty starts a plugin with a job folder. Its `job.json` points to the video and the transcript, and holds the creator's settings and clip lengths. A finder writes `result.json` with moments: each has a start and an end in seconds, and can have a score from 0 to 100, a label and a reason.
- **Which steps.** Find, understand, rate and edit are built. One plugin can find a video's moments, replacing Clips Kitty's own finding. Up to 3 can understand (their notes go to the AI that writes each clip's title, description and hashtags), and up to 3 can rate (their scores decide which clips are made). Edit plugins suggest edits that wait for the creator in the editor: up to 3 can suggest cuts, mutes, a hook title and more for each clip, and Clips Kitty doesn't put a suggestion into a clip until the creator uses it there and applies their edits. Posting isn't a plugin step: Clips Kitty posts clips itself, and through WoopSocial it can post to many sites at once on the creator's own account. `kind: publisher` is refused: plugins don't post.
- **Trying it.** `new` makes a plugin from one of six templates, and `run --sample` runs it the way Clips Kitty would, on a 40-second test video the SDK makes with FFmpeg. The `game-events` template finds a red banner and a loud sound in that video. It is set up for Quarkbloom Arena, a made-up game.
- **What it runs on.** Clips Kitty runs plugins on its own Python 3.11, with the standard library (minus a few modules the app leaves out) and the SDK. It can't install other packages yet.
- **Licences.** The SDK is MIT, so a plugin can use any licence. Clips Kitty itself is AGPL-3.0-or-later.
- **Not sandboxed.** Every plugin runs on the creator's PC with their rights, like any program; Clips Kitty doesn't sandbox it. The install screen says what it declares.
- **Which release.** No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.

## DEV

**Rules:**
- The #showdev tag (https://dev.to/t/showdev): "For showing off projects and launching products." "Please make posts community-driven and not overly corporate or salesy."
- DEV's terms (https://dev.to/terms): content must not be "designed primarily for the purposes of promotion or creating backlinks", and "Posts must contain substantial content — they may not merely reference an external link that contains the full post."
- DEV's AI guidelines (https://dev.to/guidelines-for-ai-assisted-articles-on-dev) allow AI-assisted posts that "Disclose the fact that they were generated or assisted by AI in the post". The draft's last line does that; keep it unless Colin rewrites the post himself.

This is a project launch: what the SDK is, how to try it and its limits. It has no tutorial walkthrough; it links the tutorial. **Tag:** `showdev` (any others are Colin's choice).

````markdown
# Clips Kitty SDK: plugins for a free local clipper

Clips Kitty is a free, open-source app that turns long videos and streams into short clips on your own PC. You give it a link or a video file, and it finds the best moments, crops them to 9:16, burns in captions and writes the titles. I made it for small YouTubers and streamers who can't afford the other options or an editor.

What it can't know is your game: that a red banner at the top of the screen means a big play, say. So Clips Kitty now has an SDK, a small MIT-licensed Python package for writing plugins that teach it what a good moment looks like.

## Where a plugin fits

```text
Clips Kitty SDK

Input                      a link or a video file
  ↓
Video                      Clips Kitty downloads it and writes down what is said
  ↓
Your plugin                every step is optional
  ├── find                 picks the moments                  built
  ├── understand           says what happens in each one      built
  ├── rate                 scores each moment                 built
  └── edit                 suggests edits for the creator     built
  ↓
Clips Kitty                does every step no plugin does, then cuts, frames and captions the clips
  ↓
Creator / Social Platform  posts when the creator clicks Publish, or on a schedule or automatic posting the creator switched on
```

- One plugin can find moments for a video; it replaces Clips Kitty's own finding.
- Up to 3 plugins can understand and up to 3 can rate, after any finder.
- A plugin's role comes from `inputs` and `outputs` in its manifest.
- Up to 3 plugins can suggest edits for the clips that will be made.
- Edit plugins suggest edits that wait for the creator in the editor.
- Posting isn't a plugin step: Clips Kitty posts clips itself, and through WoopSocial it can post to many sites at once on the creator's own account ([Publish to every platform at once](https://github.com/ColinGPT9/clips-studio#publish-to-every-platform-at-once)). `kind: publisher` is refused: plugins don't post.

Clips Kitty starts a plugin with a job folder. Its `job.json` points to the video and the transcript, and holds the creator's settings and clip lengths. A finder answers in `result.json` with moments: each has a start and an end, and can have a score from 0 to 100, a label and a reason. An understander adds a note on what happens in each moment, and the notes go to the AI that writes each clip's title, description and hashtags. A rater gives each moment a new score, and the scores decide which clips are made. An editor suggests edits for each clip Clips Kitty makes, such as a cut, a mute or a hook title, and each one waits for the creator in the editor until they use it.

## Try it

You need Python 3.11, FFmpeg and Git. These three commands install the SDK, make a plugin from the `game-events` template, set up for Quarkbloom Arena (a made-up game), and run it on a 40-second test video the SDK makes with FFmpeg.

PowerShell (Windows):

```powershell
py -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
py -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name
py -m clipskitty_sdk run quarkbloom-bursts --sample
```

bash (macOS, Linux):

```bash
python -m pip install "clipskitty-sdk[yaml,test] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name
python -m clipskitty_sdk run quarkbloom-bursts --sample
```

The last one ends with:

```text
1 moment(s), as Clips Kitty would take them:
      16.0s      29.8s  score   -  quark_burst
       why: the banner shows from 22 to 26.75 s, and the sound gets 25 dB louder
```

The test video has a red banner from 22 to 27 seconds and a loud sound at the same time, and the plugin found that moment from the screen and the sound alone.

## What's in it

- `new`: six templates (blank, transcript, game-events, rater, understander and editor), each with its own tests and a GitHub workflow that checks it on Linux and Windows.
- `run --sample`: runs your plugin the way Clips Kitty does, on the test video, without Clips Kitty.
- `frame`: one frame of your own recording with a box drawn on it, to measure where your game's banner or icon is.
- Helpers: `media` (frames, loudness and scene cuts, through FFmpeg), `signals`, `text` (the words said, with their times), `local_model` (asks the creator's local model, only on their PC) and `testing`.
- `install --watch`: puts the plugin into the Clips Kitty running on your PC, and installs it again each time you save, stopping if a save asks for anything new.
- `listing`: writes the catalog file that lists your plugin in Clips Kitty's Marketplace, ready for a pull request.

## Its limits

- No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source.
- Plugins run on Clips Kitty's own Python 3.11, with the standard library (minus a few modules the app leaves out) and the SDK. Clips Kitty can't install other packages yet. A few happen to be inside the app, numpy and OpenCV among them, and can be imported, but they aren't promised and may change with an app update.
- Every plugin runs on the creator's PC with their rights, like any program; Clips Kitty doesn't sandbox it. The install screen says what it declares.
- Plugins don't post: Clips Kitty posts clips itself. Edit plugins suggest edits that wait for the creator in the editor; Clips Kitty doesn't put a suggestion into a clip until the creator uses it there and applies their edits.
- The SDK isn't on PyPI yet: it installs from GitHub.

## Licence

The SDK is MIT, so your plugin can use any licence, open or closed. Clips Kitty itself is AGPL-3.0-or-later. Getting listed in the Marketplace costs nothing, and Clips Kitty takes no share.

Start with [the SDK's README](https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/README.md), then [Your first game pipeline](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/first-game-pipeline.md). Questions and ideas are welcome in [Discussions](https://github.com/ColinGPT9/clips-studio/discussions).

*I wrote this post with help from an AI assistant, and checked what it says against the code.*
````

## PyCoder's Weekly

**Rules** (https://pycoders.com/submissions): "We want to hear from you about projects you are working on, conferences you are running, and articles you want to share." "we cannot guarantee to feature every submitted link in the newsletter". Links go in through the button on that page.

**Link:** https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/README.md

**One line:** Clips Kitty SDK: an MIT-licensed Python package for writing plugins that find, rate and describe the moments in a video, for Clips Kitty, a free clipper that runs on your own PC.

## Lobsters

Only from Colin's own Lobsters account, if he already has one: this is not a reason to ask anyone for an invitation.

**Rules** (https://lobste.rs/about):
- "As a rule of thumb, self-promo should be less than a quarter of one's stories and comments."
- "Users are considered "new" for their first 70 days", and new users can't use the `show` tag, among others.

**Title:** Clips Kitty SDK: Python plugins for a local video clipper

**Link:** https://github.com/ColinGPT9/clips-studio/blob/main/sdk/python/README.md

**Tags:** `show` and `python`, both on https://lobste.rs/tags.

## GitHub Discussions categories

Discussions is already on for the repository. GitHub's page on categories (https://docs.github.com/en/discussions/managing-discussions-for-your-community/managing-categories-for-discussions) says "Each repository or organization can have up to 25 categories." and gives the steps: **Discussions**, the pencil next to "Categories", **New category**, then the emoji, title, description and format, then **Create**. In the Announcement format, "only people with maintain or admin permissions can create new discussions, but anyone can comment and reply."

| Category | Format | Description |
|---|---|---|
| Plugin help | Question and Answer | Questions about writing a Clips Kitty plugin with the SDK. |
| Show your plugin | Open-ended discussion | Plugins you made: what they find, and a link. |
| Wanted pipelines | Open-ended discussion | Ideas for plugins: a game, a sport or a kind of show Clips Kitty should understand better. |
| SDK releases | Announcement | New versions of the SDK and what changed. |

**Show your plugin:** GitHub's default categories include "Show and tell" ("Creations, experiments, or tests relevant to the project", open-ended). If the repository still has it, use it instead of a new category.

**Wanted pipelines:** link the Wanted list from the description or a pinned post: https://github.com/ColinGPT9/clips-studio/blob/main/awesome-clips-kitty/README.md#wanted

## The template repository

A repository that developers start from with GitHub's **Use this template** button, holding what `new --template game-events` writes. GitHub's page (https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository): in the repository's **Settings**, "Select **Template repository**."

How to make it, once approved:
1. In an empty folder: `python -m clipskitty_sdk new quarkbloom-bursts --template game-events --publisher your-github-name`.
2. Put the note below in its `README.md`, right under the title, then commit the folder's contents (`.gitignore` and `.github/` included) as a new repository, under a name Colin chooses.
3. In the repository's **Settings**, select **Template repository**.

Until Colin chooses MIT-0 or CC0-1.0 for template output, the repository carries `TEMPLATE-LICENSE.txt` with the SDK's MIT notice, as `new` writes it. Its `LICENSE` says `your-github-name`, which each developer changes to their own name.

The note, for under the README's title:

```markdown
> **This is a template.** Choose **Use this template** to start your own Clips Kitty plugin from it. Then put your GitHub name in place of `your-github-name`, and your game in place of Quarkbloom Arena (a made-up game) and its `quark_burst` event, in every file: `listing` won't list a plugin whose `clipskitty.yaml` or README still has them. `python -m clipskitty_sdk new` makes the same files on your PC: see [Your first game pipeline](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/first-game-pipeline.md).
```

Apart from that note, the README is what `new` writes, word for word:

````markdown
# Quarkbloom Bursts

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

You need Python 3.11 (the Python Clips Kitty runs plugins on) and FFmpeg. In the plugin's folder, in PowerShell:

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

Push this folder to GitHub as its own repository. Its id, `your-github-name/quarkbloom-bursts`, starts with `your-github-name`: for a Marketplace listing, that must be the GitHub name that owns the repository. `.github/workflows/clipskitty-check.yml` then checks it on Linux and Windows with Python 3.11 on every push. How to get it listed in the Marketplace: [Marketplace publishing](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/marketplace-publishing.md).

## Files

```text
clipskitty.yaml        what Clips Kitty reads about the plugin: its inputs, outputs, permissions and settings
src/main.py            the plugin: Clips Kitty starts it with a job folder
tests/test_main.py     its tests, on the SDK's sample video
LICENSE                your licence for the plugin
TEMPLATE-LICENSE.txt   the MIT licence of the code that came from the template
```

The SDK's helpers and the job's contract: [SDK](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/sdk.md).
````

## The "Built for Clips Kitty" badge

A badge a developer could put on their plugin's README. It is a new public label, so it appears in no README, template or page until Colin approves it. Awesome Clips Kitty already says "Built for Clips Kitty" about plugins that run inside Clips Kitty. The badge would mean only that: the plugin is written for Clips Kitty with its SDK. It says nothing about review, safety or compatibility, it isn't ✓ Official or ✓ Compatible, and anyone can add it to their own README.

```markdown
[![Built for Clips Kitty](https://img.shields.io/badge/built_for-Clips_Kitty-0ea5e9)](https://github.com/ColinGPT9/clips-studio/blob/main/docs/developers/README.md)
```

It is a Shields.io static badge (https://shields.io/badges/static-badge), where `_` stands for a space. `0ea5e9` is the website's `--accent-strong` colour (`site/styles.css`). If it is approved, `new` would add it to the templates' READMEs, and `test_no_template_carries_a_badge_or_a_real_game` in `tests/test_plugin_sdk_new.py` would change with it.

## The CONTRIBUTING licence sentence

It applies D20 (in `docs/platform/DECISIONS.md`) to contributions. [`CONTRIBUTING.md`'s Licence section](../../CONTRIBUTING.md#licence) stays as it is until Colin confirms it, because it sets the terms contributions are made under.

The sentence:

> Contributions to `sdk/python/` and the MIT examples are under MIT, and to `awesome-clips-kitty/` under CC0-1.0; everything else is AGPL-3.0-or-later.

- The MIT examples are `examples/pipelines/scene-cut-highlights` and `examples/pipelines/keyword-rater`. `examples/pipelines/transcript-highlights` is AGPL-3.0-or-later.
- "or-later" matches [`NOTICE`](../../NOTICE), which says "either version 3 of the License, or (at your option) any later version", and the root [`CHANGELOG.md`](../../CHANGELOG.md), which says the app stays AGPL-3.0-or-later. The Licence section today says only "AGPL-3.0".
- Where it would go: in place of the section's first paragraph, "Clips Kitty is **AGPL-3.0**, and contributions are accepted under the same terms: opening a PR means you are licensing your change that way."

## A PyPI release workflow

For publishing `clipskitty-sdk` to PyPI with Trusted Publishing (https://docs.pypi.org/trusted-publishers/), whose tokens are "only valid for 15 minutes from time of creation". It follows the PyPA guide (https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/). It is not in `.github/workflows/`, so nothing runs it.

What it does: a tag like `sdk-v1.2.0` starts it, and nothing else does. The build job checks that the tag names the version in `sdk/python`, then builds the wheel and the source archive. The publish job, in a GitHub environment called `pypi`, uploads them. The app's release tags (`v*`, which `.github/workflows/docker-image.yml` builds on) don't start it.

**Action versions.** `actions/checkout` and `actions/setup-python` are pinned to the same commits as in this repository's workflows that can write (`.github/workflows/catalog-numbers.yml`). This repository uses no `upload-artifact`, `download-artifact` or `gh-action-pypi-publish` yet, so their versions are the ones the PyPA guide shows. Because the publish job can write to PyPI, those are pinned by commit too, the way this repository's workflows with write access pin theirs. Each `<commit of …>` is looked up in that action's own repository when Colin approves; none is filled in here.

**Once, before the first tag:**
1. On PyPI, add a pending publisher (https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) for the project `clipskitty-sdk`: owner `ColinGPT9`, repository `clips-studio`, workflow `sdk-release.yml`, environment `pypi`. That page warns: "A "pending" publisher does not create a project or reserve a project's name until it is actually used to publish." PyPI's JSON API had no project called `clipskitty-sdk` on 2026-10-08, which doesn't prove the name will still be free.
2. On GitHub, add an environment called `pypi` in the repository's settings.
3. Save the block below as `.github/workflows/sdk-release.yml`, with the commits filled in.
4. In `sdk/python/CHANGELOG.md`, change `## 1.2.0 (not released yet)` to the release, and the line under the title, "No version is on PyPI yet." (`tests/test_plugin_sdk_package.py` checks that line word for word, and the `pyproject.toml` note below). Then change everything else that says the SDK isn't on PyPI or installs it from GitHub: search `sdk/python`, `docs/developers`, `site/`, `README.md` and this file for "PyPI", "git+https" and "installs from GitHub". On 2026-10-08 that found:
   - the note at the top of `sdk/python/pyproject.toml`;
   - the Package row in `docs/developers/sdk.md`;
   - in `docs/developers/first-game-pipeline.md`, the note at the top ("Git (for step 1's install)") and the Git line in its "You need" list, and the Git line under the Quickstart on `site/developers.html`;
   - the DEV draft's last limit, above;
   - the git-based `pip install` commands in `README.md`, `sdk/python/README.md`, the developer docs, each template's README, `site/developers.html` and this file;
   - the workflow that `new` puts in every plugin (`sdk/python/clipskitty_sdk/templates/_shared/github/workflows/clipskitty-check.yml`): its comment says it installs from GitHub until the SDK is on PyPI, and its git install becomes an install of the released version from PyPI.
5. Tag a commit whose CI passed: `git tag sdk-v1.2.0`, then `git push origin sdk-v1.2.0`.

The PyPA guide also gives the `pypi` environment a `url`, the project's page on PyPI. That page doesn't exist until the first upload, so the draft leaves it out.

```yaml
# Publishes the Clips Kitty SDK (sdk/python) to PyPI when a tag like
# sdk-v1.2.0 is pushed. PyPI trusts this workflow through Trusted Publishing,
# so no PyPI password or token is stored in the repository.

name: Publish the SDK to PyPI

on:
  push:
    tags: ['sdk-v*']

permissions:
  contents: read

jobs:
  build:
    name: Build the SDK
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          persist-credentials: false

      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.11'

      - name: Check the tag names the SDK's version
        run: |
          version=$(PYTHONPATH=sdk/python python -c "import clipskitty_sdk; print(clipskitty_sdk.__version__)")
          if [ "$GITHUB_REF_NAME" != "sdk-v$version" ]; then
            echo "The tag is $GITHUB_REF_NAME, but sdk/python is version $version: tag sdk-v$version instead."
            exit 1
          fi

      - name: Build the wheel and the source archive
        run: |
          python -m pip install build
          python -m build sdk/python --outdir dist

      - name: Keep the built files for the publish job
        uses: actions/upload-artifact@<commit of v5> # v5 (as the PyPA guide shows on 2026-10-08)
        with:
          name: python-package-distributions
          path: dist/

  publish:
    name: Publish to PyPI
    needs: [build]
    runs-on: ubuntu-latest
    environment:
      name: pypi
    permissions:
      id-token: write   # Trusted Publishing: PyPI accepts this job's short-lived token
    steps:
      - name: Fetch the built files
        uses: actions/download-artifact@<commit of v6> # v6 (as the PyPA guide shows on 2026-10-08)
        with:
          name: python-package-distributions
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@<commit of release/v1> # release/v1 (as the PyPA guide shows on 2026-10-08)
```

`python -m pip install build` takes the newest `build`, as the guide does; pin it too if Colin wants every tool in the publish path fixed.
