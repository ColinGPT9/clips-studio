# Marketplace publishing

How a plugin gets into the Marketplace, how people find it, and what a listing and its labels do and do not mean.

The Marketplace reads **Awesome Clips Kitty**, a curated directory of apps, pipelines, plugins, models, workflows, integrations and tools, kept as small YAML files in [`awesome-clips-kitty/`](../../awesome-clips-kitty/). The same files build the directory's [README](../../awesome-clips-kitty/README.md) for people and `index.json` for the app. The format, the inclusion criteria and what the build checks are in [Contributing to Awesome Clips Kitty](../../awesome-clips-kitty/CONTRIBUTING.md); this page is the developer's view of it.

Status: the listing and entry formats, the build (`scripts/build_registry_index.py`, which writes `index.json` and the README's generated part), the app's client and search (`plugins/registry.py`, `plugins/catalog.py`), installing from a listing, the compatibility check (`scripts/check_compatibility.py`), the weekly numbers job (`.github/workflows/catalog-numbers.yml`) and the Marketplace screen are **built**; the screen is type-checked and not yet looked at on a real PC. **Not yet:** a public repository of the catalog's own (until then the catalog lives in this repository, and the app reads its index from here: see [More than one index](#more-than-one-index)), and an install counter address, so nothing is counted.

Listing, updating and installing are free. Developers pay nothing and Clips Kitty takes no share of what they earn; link to your own pricing, sponsors or support page with `links.funding` and `service`.

## Getting listed

1. Publish your plugin in a public GitHub repository with `clipskitty.yaml` at its root, or in a folder of it. `python -m clipskitty_sdk validate .` must pass.
2. Tag a release and note its full commit hash.
3. Open a pull request adding one file, `awesome-clips-kitty/registry/pipelines/<publisher>/<name>.yaml`: your `id`, `repository`, an optional `path`, a `section`, optional `aliases`, and `versions`, each a `version` and its full `commit`. The format is in [Listing a pipeline or plugin](../../awesome-clips-kitty/CONTRIBUTING.md#listing-a-pipeline-or-plugin).

The publisher in your id must be your GitHub user or organisation, the owner of the repository; `clipskitty` is reserved for the modes that ship with the app. A new version is a new entry in your listing's `versions`; a listed version is never changed or removed. Everything the Marketplace shows about the plugin (name, description, licence, permissions, models) comes from your manifest at the listed commit, so it can't drift from the code. A maintainer adds `checked: <date>` once the listing meets the [inclusion criteria](../../awesome-clips-kitty/CONTRIBUTING.md#what-gets-in); until then the README shows it under "Not yet checked".

Only pipelines can be listed today. Other plugin kinds will go in `registry/plugins/<publisher>/<name>.yaml` once they exist (planned). An app, model, workflow, integration or tool that does not run inside Clips Kitty is a directory entry, `registry/<kind>s/<name>.yaml`, not a listing; its `relationship` says whether it is built with Clips Kitty's API or SDK or only related.

GitHub is the home of code: the plugin, its manifest, docs, releases and discussions. Hugging Face holds model weights and model cards; your manifest points at them ([Hugging Face](hugging-face.md)), and Hugging Face is never where a plugin is listed from.

## Sections

Each listing names one `section` from `awesome-clips-kitty/registry/sections.yaml`. For pipelines these are `general`, `gaming` with one section per game (`gaming/valorant`, `gaming/rocket-league`, …), `sports` with one per sport (`sports/soccer`, `sports/hockey`, …), `creators` and `podcasts`. The README groups listings by section, search matches a section's words, and `GET /marketplace?section=gaming` returns that section and the ones inside it. (The Marketplace screen groups the directory's other kinds by section; its pipeline list doesn't group or filter by section yet.) `sections.yaml` also lists niches nobody has filled yet ("Wanted"), shown as ideas for developers, never as projects that exist.

## What the checks prove, and what they don't

The build fetches your manifest at each listed commit as plain text and runs the validator the app uses; it never clones, installs or runs your code. Passing proves: the manifest is valid and matches the listing (id, version and kind), the commit is public, and the publisher owns the repository. The app then installs exactly that commit.

It does **not** prove the code is safe or does what it says. Nobody reads plugin code. The Marketplace shows a community listing as "Community · not reviewed by a person", and there is no "Verified" label. See [Security](security.md).

## Labels

| Label | Means | Set by |
|---|---|---|
| ✓ Official | The listing's repository belongs to the Clips Kitty project (GitHub owner `ColinGPT9`), and every listed commit is on one of that repository's own branches or tags (GitHub serves a fork's commits under the parent's address too, so the build checks). It installs with the tier "✓ Official · made by the Clips Kitty project". | The build, from the repository and its history. The app works it out again from the repository, and only for the index bundled with it. |
| ✓ Compatible | The latest listed version, at its commit, passed the automated compatibility check on one Clips Kitty version: the manifest is valid, it installs, its requirements are met, and it runs on a sample video and gives an answer Clips Kitty accepts. **A technical label, not a trust or security guarantee.** | `scripts/check_compatibility.py`, which writes `stats/compatibility.json`; the build turns a passed record for that exact version and commit into the label. |
| ★ Featured | A notable project, picked by hand. | A maintainer, with `featured: {reason: ..., date: ...}` in the listing. |
| Community | Everything not official. The default. | The build. Everything in an index other than the bundled one is Community, whatever it claims: Official, Compatible and Featured come only from the list this project builds and ships with the app. |

The official example pipeline, [`clips-kitty-examples/scene-cut-highlights`](../../awesome-clips-kitty/registry/pipelines/clips-kitty-examples/scene-cut-highlights.yaml), is listed with ✓ Official and ✓ Compatible.

### The compatibility check

```text
python scripts/check_compatibility.py example-dev/example-plugin --version 1.2.0 --write
```

It runs five checks in order and stops at the first that fails: `manifest_valid`, `installs` (through the plugin manager, as the app does), `requirements_met`, `starts` (it runs on a generated 40-second sample video and finishes without an error) and `valid_result` (its answer passes the result contract, even when it finds no moments). Each version carries its own record, for one commit on one Clips Kitty version; a new version needs its own check.

What it can't check yet, so such a plugin can't get the label: one that needs a graphics card, an Ollama or bundled model, more than 2 GB of models in all, or a model whose size isn't known. It installs and runs the plugin's code, so it must run on a throwaway machine with no secrets; no workflow in the repository runs it yet. A plugin can pass and still do something its listing doesn't say.

## Licences

- **Your plugin keeps your licence.** The manifest's `license` (an SPDX identifier) is shown on every Marketplace card. Clips Kitty is AGPL-3.0-or-later; the [SDK](sdk.md) is MIT, so using it puts no licence on your plugin. The example pipeline is MIT too.
- **The catalog's data** (the list and its YAML files) is CC0-1.0. By contributing a listing you agree that the listing file is CC0; your plugin is unaffected.
- **Building on someone else's project**: an adapter that runs another project, includes its code or ports it must respect that project's licence. Say so in the manifest's [`based_on`](plugin-manifest.md#based-on-other-projects); the Marketplace shows it as "Built on", with each project's licence and how your plugin uses it.

## Numbers

Each number is its own figure, from its own source, and is never added to another:

| Number | Source |
|---|---|
| Clips Kitty installs | The install counter (below); listings only |
| GitHub stars, last push, archived, number of discussions | GitHub, for the listing's repository |
| Hugging Face downloads (last 30 days) and likes | Hugging Face, for each model your manifest lists; shown on the model, not added to the plugin |

`scripts/update_registry_metrics.py` reads them every Monday (`.github/workflows/catalog-numbers.yml`) and writes `stats/metrics.json`; the build copies them into the index with the date they were read. The app never asks GitHub or Hugging Face for them itself, so browsing the Marketplace tells nobody what you looked at. No numbers have been written yet. A repository that is archived or has had no commits for over a year is shown with ⚠. There are no ratings; comments and upvotes live in your repository's GitHub Discussions, which the Marketplace links to when they are on.

### The install counter

`plugins/counter.py`. After a **first** install from a listing in the bundled index, the app requests one small file named after the listing at that index's `counter.install` address, with `{asset}` replaced by the id with `/` turned into `__` plus `.count` (`example-dev__example-plugin.count`). An update, a rollback or a version switch is not counted; installing again after removing is. The address must be a release download in one of the project's own GitHub repositories (`https://github.com/ColinGPT9/<repo>/releases/download/<tag>/{asset}`), so the count is GitHub's public download count for that file, which `update_registry_metrics.py` reads back. Other indexes can't count installs.

The request carries no account, no identifier, no cookie, no app version and nothing about anyone's videos; GitHub sees the address it comes from, as it does for any download. Users switch it off in the Marketplace ("Count my installs", stored in `<data_dir>/plugins/counting.json`), and `plugins.count_installs: false` in `settings.yaml` switches it off for everyone on that PC.

**Nothing is counted yet.** `awesome-clips-kitty/registry/catalog.yaml` has `counter.install: null`, so no request is made. `tests/test_catalog.py` fails if the address is set while the website's privacy policy and the Microsoft Store answers still say "No telemetry".

## Being found

Search runs in the app, over the index, with no server. It folds case and punctuation, and every word of a query must match somewhere: the name counts most, then your listing's `aliases` and your manifest's `games`, then `tags`, `events`, `category` and your listing's `section`, and the description least. A small alias table maps common short names (`wow` to `world of warcraft`, `football` and `soccer` to each other, `lol`, `cs2` and a few more); more are added by pull request.

So, to be found by people looking for exactly what you made:

- **Name it for what it is**: "WoW Arena PvP Highlights" is found by "WoW", "World of Warcraft PvP" and "arena"; "Gaming Highlights" is found by everything and first for nothing.
- **List the games** as slugs (`world-of-warcraft`, `rocket-league`, `nhl`) and the **events** you detect (`team_wipe`, `goal`).
- **Tag the niche**, not the category: `mythic-plus`, `arena`, `chat-spikes`. Up to ten tags.
- Pick the **one category** that fits: gaming, sports, creators, streaming, podcasting, captions, detection, analytics, audio or utilities; and the narrowest **section** that fits (`gaming/world-of-warcraft` rather than `gaming`).
- Add `aliases` in the listing for names people use that you don't: a season name, an abbreviation.

`tests/test_registry.py` checks that each of the searches in the platform brief ("Marvel Rivals", "WoW", "World of Warcraft PvP", "Minecraft", "Rocket League", "soccer", "Soccer goals", "NHL", "podcast", "Podcast shorts", "Twitch", "captions", "highlights") puts the specialised listing first in a test catalogue that also has broad ones.

## What a listing shows

From your manifest at the latest listed version: name, description, publisher, version, licence, repository, category, tags, games, events, local, remote or hybrid with every ⚠ data line, requirements (GPU, video memory, memory, disk, systems, software), permissions with whether each is enforced, models and their sources, settings, a paid service and its pricing, funding and docs links, examples, and "Built on" from `based_on`. From the catalog: the section, the labels, each version's compatibility record, the numbers above and a link to your Discussions. Plus the install tier, the checks, and whether the user has it installed and an update is available.

A listing about a game, league or product you don't own shows **"Unofficial · not made or endorsed by the makers of …"**, built from your `games`. Whether a rights holder's own plugin can drop it is the owner's call.

Links are shown with their full address; opening one in the browser goes through a dialog that shows the address and says it comes from you, not from Clips Kitty.

## Being blocked or delisted

A version that is harmful goes on the block list, `awesome-clips-kitty/registry/blocklist.yaml` (format: [Blocking](../../awesome-clips-kitty/CONTRIBUTING.md#blocking)), with `severity: blocked`: it leaves the index, is refused at install and at run, and copies already installed are flagged in red with a Remove button. A version that is abandoned, broken by a game patch or has a licence problem is **delisted**: it leaves the index but keeps running where installed, shown as "No longer listed" with the reason. Clips Kitty never deletes a user's files on its own.

## More than one index

The app reads three kinds of index, keeps the last good copy of each fetched one, and works offline:

1. **The list bundled with it**, the only one that labels: ✓ Official, ✓ Compatible, ★ Featured and install numbers come from it alone.
2. **Clips Kitty's online list**: this same `index.json` on the project's main branch (`https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/awesome-clips-kitty/index.json`). Once your listing's pull request is merged, it reaches creators the next time their Marketplace checks (when it opens, at most once a day, unless that is switched off) or at once when they press **Check for new pipelines**. Until a release bundles it, it shows as Community, without install numbers, whatever it would otherwise carry. A new version of a listing already in the bundled list arrives the same way: it is added to that listing as Community, and installing it isn't counted. A version the bundled list has always comes from the bundled list, with its commit, its compatibility record and its counting. The online list brings new listings and entries from other owners, new versions, new sections and blocks; a change to an existing entry or listing, a removal, and anything in the Clips Kitty project's own repositories arrive with the next release.
3. **Any index addresses in settings** (`plugins.registry_urls` in `settings.yaml`, `https://` only; none by default): lists other people keep, always Community.

When two indexes list the same id, the first wins (bundled, online, then settings order), except that the online list adds the versions the bundled one lacks (never to the project's own listings, and never from a listing whose code is in another repository or folder). Block lists from every index the app has ever cached keep applying, and a new block reaches an installed copy the next time Clips Kitty opens with a pipeline installed, or its Marketplace opens (at most once a day, while the automatic checks at the bottom of Browse are on), at once with **Check for new pipelines**, or with the next app release.
