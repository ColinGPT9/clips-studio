# Contributing to Awesome Clips Kitty

Awesome Clips Kitty is a folder of small YAML files, reviewed by pull request, from which two files are built: `README.md` for people and `index.json` for the Clips Kitty Marketplace. There is no server and no account to create. Listing, updating and installing are free, and Clips Kitty takes no share of anything a developer earns; link to your own pricing, sponsors or support page if you have one.

Status: the format, the build, the app's reader and the Marketplace screens are **built**. **Not yet:** a public repository of its own and a published index address. Until then the catalog lives in `awesome-clips-kitty/` in the Clips Kitty repository, and the only index an installed app has is the copy bundled with it, so a new entry reaches users with the next app release.

```text
awesome-clips-kitty/
  README.md                                  generated between the markers; the rest is written by hand
  CONTRIBUTING.md                            this file
  LICENSE                                    CC0-1.0, for the list and its data
  registry/
    sections.yaml                            each kind's sections, in order, and the niches wanted
    catalog.yaml                             catalog-wide settings (the install counter's address)
    apps/<name>.yaml                         one file per app
    models/<name>.yaml                       one file per model
    workflows/<name>.yaml                    one file per workflow
    integrations/<name>.yaml                 one file per integration
    tools/<name>.yaml                        one file per tool
    pipelines/<publisher>/<name>.yaml        one listing per installable pipeline
    plugins/<publisher>/<name>.yaml          one listing per installable plugin of another kind (when those exist)
    blocklist.yaml                           versions that must not be installed or run
  stats/
    metrics.json                             written by a scheduled job: stars, downloads, installs
    compatibility.json                       written by the compatibility check
  index.json                                 generated: what the app reads
```

One file per entry, rather than one long file per kind, so that two pull requests rarely touch the same file.

## What gets in

A curated list is useful because not everything gets in. An entry must be:

- **Relevant**: it helps someone make, find, edit or publish clips, or build on Clips Kitty.
- **Useful**: it works and does something real, not a placeholder or a tutorial exercise.
- **Maintained, where that matters**: commits in the last year or so, unless it is a finished, stable tool. Archived projects are left out unless nothing replaces them; the README marks old ones with ⚠.
- **Documented**: a README that says what it does and how to run it.
- **Licensed**: its licence can be identified (an SPDX identifier such as `MIT`, `Apache-2.0`, `GPL-3.0-or-later`). A project with no licence is not open source and is left out.
- **Legitimate**: the official source, not a re-upload or a fork, unless the original is gone.
- **Of reasonable quality**: no malware, no hidden data collection, no tools whose main use breaks a platform's terms.
- **Honest about data**: if it sends videos, transcripts or accounts anywhere, its entry says so.

An entry whose author added it but that no maintainer has checked against these yet goes under "Not yet checked" (no `checked` date). A maintainer adds `checked: <date>` once it passes.

Game, league and brand names are only ever used to say what a project works with. Nothing here is made or endorsed by those games' or leagues' makers, and the Marketplace says so on every listing that names a game.

## How a project relates to Clips Kitty

Every entry says one of three things, and they never blur:

| `relationship` | Means | Where |
|---|---|---|
| `built-for` | Runs inside Clips Kitty: a pipeline or plugin installed from the Marketplace. | Listings in `pipelines/` and `plugins/`; set automatically. |
| `built-with` | A separate app or tool that uses Clips Kitty's local API or SDK. Say which with `uses: api`, `sdk` or `both`. | Any other kind. |
| `related` | Relevant, not connected to Clips Kitty yet. | Any other kind. |

An app and a pipeline are different things: an **app** stands on its own; a **pipeline** runs inside Clips Kitty. The same project can appear as both, for example an open-source clipping app (`apps/`) and the pipeline that runs it through Clips Kitty (`pipelines/`). Link them with the app's `adapter:` field; an app with an adapter belongs in the "Work with Clips Kitty" section.

## Adding an app, model, workflow, integration or tool

Add `registry/<kind>s/<name>.yaml`, where `<name>` is lowercase words joined by hyphens:

```yaml
name: Example Clipper                    # required, up to 80 characters
description: Turns long videos into vertical clips with subtitles.   # required, one or two sentences
section: video-clipping                  # required: a section of this kind in sections.yaml
relationship: related                    # required: built-with or related
license: MIT                             # required: the project's licence, as an SPDX expression
license_note: The cloud/ folder has a separate commercial licence.   # optional: exceptions a user must know
source:                                  # required: at least one of github, huggingface, url
  github: https://github.com/example-org/example-clipper
  path: tools/clipper                    # optional: a folder in that repository
  huggingface: example-org/example-model # a model's home
  url: https://example.org               # a home page, or the source when it isn't on GitHub
models:                                  # optional: Hugging Face models it uses
  - huggingface: example-org/example-model
platforms: [windows, macos, linux]       # optional: windows, macos, linux, web, android, ios
runs: local                              # optional: local, cloud or both
tags: [subtitles, face tracking]         # optional: up to 10 short words people search for
games: [example-game]                    # optional: games it covers
sports: [soccer]                         # optional: sports it covers
uses: api                                # built-with only: api, sdk or both
adapter: example-dev/example-clipper     # optional: the listed pipeline that runs it in Clips Kitty
warning: Downloads videos from sites whose terms may not allow it.   # optional: shown with a ⚠
added: 2026-10-07                        # required
checked: 2026-10-07                      # set by a maintainer once it meets the criteria
```

GitHub is the home of code: source, issues, discussions and releases. Hugging Face is the home of model weights: a model entry's `source` is its Hugging Face repository, and an app or pipeline names the models it uses with `models:`. Hugging Face is never the place an app or plugin is listed from.

## Listing a pipeline or plugin

A listing is what makes something installable from the Marketplace.

1. Put your plugin in a public GitHub repository with `clipskitty.yaml` at its root (or in the folder named by `path`). `python -m clipskitty_sdk validate .` must pass. The SDK is MIT-licensed, so using it puts no licence on your plugin.
2. Tag a release and note its full commit hash.
3. Open a pull request adding `registry/pipelines/<publisher>/<name>.yaml`:

```yaml
id: example-dev/example-plugin
repository: https://github.com/example-dev/example-plugin
path: .                        # optional: the folder holding clipskitty.yaml
section: gaming/generic        # required: a pipeline section in sections.yaml
aliases: [example game]        # optional: words people may search for
added: 2026-10-07
versions:
  - version: 1.0.0
    commit: 0123456789abcdef0123456789abcdef01234567   # the full hash
    tag: v1.0.0                                          # optional
    tested_with: [{game: example-game, version: "Season 1"}]   # optional
```

Add a version by adding an entry; never change or remove one. The name, description, licence, permissions, models and everything else the Marketplace shows come from your manifest at that commit, so they cannot drift from the code.

### What the build checks

`scripts/build_registry_index.py` (in the Clips Kitty repository) reads each listing, fetches the manifest at each listed commit as plain text from GitHub (it never clones, installs or runs plugin code), and refuses the listing unless:

- the file path is `<folder>/<id>.yaml`, and the repository is `https://github.com/<owner>/<repo>`;
- the publisher (the id before `/`) is the repository's GitHub owner, and is not `clipskitty` (the Clips Kitty project's own repositories may list its examples under their own names);
- every commit is a full 40-character hash, and every version is listed once;
- the manifest passes the same validator the app uses, with the listing's id and version, and its kind matches the folder;
- the commit is reachable on GitHub without signing in (the fetch proves it).

That is all a listing means: **the automated checks passed. Nobody has read the code.** The Marketplace shows such a listing as "Community · not reviewed by a person".

## Licences

- **This catalog** (the list and its data) is [CC0-1.0](LICENSE). By contributing you agree your contribution is CC0 too.
- **Each project keeps its own licence.** Being listed here changes nothing about it. Its `license` field is shown on every Marketplace card; for a listing it comes from the plugin's manifest.
- **Clips Kitty** is AGPL-3.0-or-later; **its SDK** is MIT. A plugin may use any licence its author chooses.
- **Building on someone else's project**: an adapter that runs another project as a separate program, or that includes its code, must respect that project's licence (keep its notices, and use a compatible licence if you include its code). Say what it builds on in the manifest's `based_on`, and the Marketplace shows it. Never copy code whose licence doesn't allow it.

## Labels

| Label | Means | Who sets it |
|---|---|---|
| ✓ Official | Made and maintained by the Clips Kitty project. | The build: the source repository belongs to the project. |
| ✓ Compatible | The listed version passed the automated compatibility checks: the manifest is valid, it installs, its requirements are met, and it runs on a sample video and gives an answer Clips Kitty accepts. **A technical label, not a trust or security guarantee.** | `scripts/check_compatibility.py`, which writes `stats/compatibility.json`. It belongs to one version at one commit, checked on one Clips Kitty version; a new version needs its own check. A plugin that needs a graphics card, an Ollama model or more than 2 GB of models can't be checked on the check machine yet, so it can't get this label. |
| ★ Featured | A notable project, picked by hand. | A maintainer, with `featured: {reason: ..., date: ...}` in the entry. |
| Community | Everything not official. The default. | The build. |

There is no "Verified" label: nobody verifies identities or audits code, so nothing claims to.

## Numbers

Each number is its own figure, from its own source, never added to another:

| Number | Source | Shown as |
|---|---|---|
| Clips Kitty installs | The install counter (below) | "12,482 Clips Kitty installs" |
| GitHub stars, last commit, archived, discussions | GitHub, for the entry's repository | "★ 1.2k on GitHub" |
| Hugging Face downloads and likes | Hugging Face, for each model an entry uses | on the model, not the plugin |

`scripts/update_registry_metrics.py` reads them on a schedule and writes `stats/metrics.json`; the build copies them into the index with the date they were read. The app never asks GitHub or Hugging Face for them itself, so browsing the Marketplace tells nobody what you looked at. Comments and upvotes live in each project's GitHub Discussions, which the Marketplace links to.

### The install counter

Clips Kitty counts installs from this catalog without a server of its own: `registry/catalog.yaml` names an address with `{asset}` in it, and after the first install of a listing (not an update or a version switch) the app requests that one small file, named after the listing. The plan is a GitHub release in this catalog's repository with one such file per listing, so the count is GitHub's public download count for it. The request carries no account, no identifier and nothing about your videos; users can switch counting off at the bottom of the Marketplace, or for a whole PC with `plugins.count_installs: false` in `settings.yaml`. The address is empty until the catalog has its own public repository, so nothing is counted yet.

## Blocking

A harmful or broken version goes in `registry/blocklist.yaml`:

```yaml
- id: example-dev/example-plugin
  versions: ["1.0.0"]          # or "*"
  severity: blocked            # blocked: refused at install and at run; delisted: still runs, shown as no longer listed
  reason: Uploads videos to a server it does not declare
  date: 2026-10-07
  advisory: https://github.com/example-org/example-advisory   # optional
```

Blocked and delisted versions leave the index's list; the block list itself is in the index, so the app can flag copies already installed. Clips Kitty never deletes a user's files on its own: a blocked plugin is refused and shown in red with a Remove button.

## Building

From a Clips Kitty checkout:

```text
python scripts/build_registry_index.py           # write index.json and README.md
python scripts/build_registry_index.py --check   # fail if either is out of date or an entry is refused
python scripts/update_registry_metrics.py        # refresh stats/metrics.json (needs a GitHub token for stars)
python scripts/check_compatibility.py <id>       # run the compatibility checks for one listing
```

The build needs the network only to fetch manifests, and no secrets. The compatibility check installs and runs a plugin, so run it only on a throwaway machine holding no secrets. No workflow runs it yet: a plugin under test could tamper with the results of the job that checks it, so an automatic check needs one isolated job per plugin, which is designed but not built. Until then a maintainer runs it by hand.
