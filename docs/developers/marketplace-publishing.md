# Marketplace publishing

How a plugin gets into the Marketplace, how people find it, and what "listed" does and does not mean.

Status: the listing format, the index build (`scripts/build_registry_index.py`), the app's client and search (`plugins/registry.py`) and installing from a listing are **built**. **Not yet:** a public registry repository and index address (until then the only index an installed app has is the copy bundled with it, so a listing reaches users with the next app release), and the Marketplace screen (Phase 8).

## Getting listed

1. Publish your plugin in a public GitHub repository with `clipskitty.yaml` at its root, or in a folder of it. `python -m clipskitty_sdk validate .` must pass.
2. Tag a release and note its full commit hash.
3. Open a pull request adding one file, `registry/plugins/<publisher>/<name>.yaml`. The format and the checks are in [`registry/README.md`](../../registry/README.md).

The publisher in your id must be your GitHub user or organisation, the owner of the repository. A new version is a new entry in your listing's `versions`; a listed version is never changed or removed.

## What the checks prove, and what they don't

The build fetches your manifest at each listed commit as plain text and runs the validator the app uses. Passing proves: the manifest is valid and matches the listing, the commit is public, and the publisher owns the repository. The app then installs exactly that commit.

It does **not** prove the code is safe or does what it says. Nobody reads plugin code. The Marketplace shows "Listed · not reviewed by a person", and there is no "Verified" label. See [Security](security.md).

## Being found

Search runs in the app, over the index, with no server. It folds case and punctuation, and every word of a query must match somewhere: the name counts most, then your listing's `aliases` and your manifest's `games`, then `tags`, `events`, `category`, and the description least. A small alias table maps common short names (`wow` to `world of warcraft`, `football` and `soccer` to each other, `lol`, `cs2` and a few more); more are added by pull request.

So, to be found by people looking for exactly what you made:

- **Name it for what it is**: "WoW Arena PvP Highlights" is found by "WoW", "World of Warcraft PvP" and "arena"; "Gaming Highlights" is found by everything and first for nothing.
- **List the games** as slugs (`world-of-warcraft`, `rocket-league`, `nhl`) and the **events** you detect (`team_wipe`, `goal`).
- **Tag the niche**, not the category: `mythic-plus`, `arena`, `chat-spikes`. Up to ten tags.
- Pick the **one category** that fits: gaming, sports, creators, streaming, podcasting, captions, detection, analytics, audio or utilities.
- Add `aliases` in the listing for names people use that you don't: a season name, an abbreviation.

`tests/test_registry.py` checks that each of the searches in the platform brief ("Marvel Rivals", "WoW", "World of Warcraft PvP", "Minecraft", "Rocket League", "soccer", "Soccer goals", "NHL", "podcast", "Podcast shorts", "Twitch", "captions", "highlights") puts the specialised listing first in a test catalogue that also has broad ones.

## What a listing shows

From your manifest at the latest listed version: name, description, publisher, version, licence, repository, category, tags, games, events, local, remote or hybrid with every ⚠ data line, requirements (GPU, video memory, memory, disk, systems, software), permissions with whether each is enforced, models and their sources, settings, a paid service and its pricing, funding and docs links, examples. Plus the tier, the checks, and whether the user has it installed and an update is available.

A listing about a game, league or product you don't own shows **"Unofficial · not made or endorsed by the makers of …"**, built from your `games`. Whether a rights holder's own plugin can drop it is the owner's call.

Links are shown as text; opening one in the browser goes through a confirmation (planned with the Marketplace screen). There are no ratings, install counts or telemetry.

## Being blocked or delisted

A version that is harmful goes on the block list (`severity: blocked`): it leaves the index, is refused at install and at run, and copies already installed are flagged in red with a Remove button. A version that is abandoned, broken by a game patch or has a licence problem is **delisted**: it leaves the index but keeps running where installed, shown as "No longer listed" with the reason. Clips Kitty never deletes a user's files on its own.

## More than one index

The app reads the index bundled with it, and any index addresses in its settings (`plugins.registry_urls` in `settings.yaml`, `https://` only; none is set by default because none has been published). It keeps the last good copy of each, so it works offline. When two indexes list the same id, the first one (the bundled index, then settings order) wins. Block lists from every index the app has ever cached keep applying.
