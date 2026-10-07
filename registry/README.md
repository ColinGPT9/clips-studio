# Clips Kitty plugin registry

The list of plugins the Marketplace shows. It is a folder of small files reviewed by pull request and one generated `index.json`; there is no server.

Status: the format, the build script and the app's client are **built**. **Not yet:** a public home. Until the owner moves this folder to a repository of its own and publishes the index's address, the only index an installed app has is the copy bundled with it, so a new listing or block reaches users only with an app release.

```text
registry/
  plugins/<publisher>/<name>.yaml   one listing per plugin
  blocklist.yaml                    versions that must not be installed or run
  index.json                        generated: python scripts/build_registry_index.py
```

## Listing a plugin

1. Put your plugin in a public GitHub repository with `clipskitty.yaml` at its root (or in the folder named by `path`). `python -m clipskitty_sdk validate .` must pass.
2. Tag a release and note its full commit hash.
3. Open a pull request adding `registry/plugins/<publisher>/<name>.yaml`:

```yaml
id: example-dev/example-plugin
repository: https://github.com/example-dev/example-plugin
path: .                        # optional: the folder holding clipskitty.yaml
aliases: [example game]        # optional: words people may search for
versions:
  - version: 1.0.0
    commit: 0123456789abcdef0123456789abcdef01234567   # the full hash
    tag: v1.0.0                                          # optional
    tested_with: [{game: example-game, version: "Season 1"}]   # optional
```

Add a version by adding an entry; never change or remove one.

## What the build checks

`scripts/build_registry_index.py` reads each listing, fetches the manifest at each listed commit as plain text from GitHub (it never clones, installs or runs plugin code), and refuses the listing unless:

- the listing's file path is its id, and the repository is `https://github.com/<owner>/<repo>`;
- the publisher (the id before `/`) is the repository's GitHub owner, and is not `clipskitty`;
- every commit is a full 40-character hash, and every version is listed once;
- the manifest passes the same validator the app uses, with the listing's id and version;
- the commit is reachable on GitHub without signing in (the fetch proves it).

That is all "Listed" means: **the automated checks passed. Nobody has read the code.** The Marketplace says "Listed · not reviewed by a person". There is no "Verified" label.

## Blocking

A harmful or broken version goes in `blocklist.yaml`:

```yaml
- id: example-dev/example-plugin
  versions: ["1.0.0"]          # or "*"
  severity: blocked            # blocked: refused at install and at run; delisted: still runs, shown as no longer listed
  reason: Uploads videos to a server it does not declare
  date: 2026-10-07
  advisory: https://github.com/example-org/example-advisory   # optional
```

Blocked and delisted versions leave the index's plugin list; the block list itself is in the index, so the app can flag copies already installed. Clips Kitty never deletes a user's files on its own: a blocked plugin is refused and shown in red with a Remove button.

## Building

```text
python scripts/build_registry_index.py           # write index.json
python scripts/build_registry_index.py --check   # fail if index.json is out of date
```

The build needs the network only to fetch manifests; with no listings it needs none. It needs no secrets.
