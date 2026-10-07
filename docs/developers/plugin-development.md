# Plugin development

How a plugin gets from your folder or repository onto a user's PC, and what happens to it there. To write one, start with [Getting started](getting-started.md); the contract is in [Pipeline development](pipeline-development.md).

Status: the plugin manager is **built** in the engine (`plugins/manager.py`, `plugins/sources.py`) with experimental API routes. Installing from a registry listing is **built** (Phase 7), and so is the Marketplace screen in the desktop app (Phase 8), which does all of the below with buttons. **Planned**: per-plugin Python packages.

## The life of a plugin

| Step | What happens | Where |
|---|---|---|
| **Plan** | Clips Kitty fetches the files into a staging folder, validates the manifest, checks it is for this Clips Kitty, and says what installing would do: the permissions with their labels, any ⚠ data warnings, requirements, and for an update what changes. Nothing is installed. | `POST /plugins/plan` |
| **Install** | The staged files are checked again and moved into place. The plugin is on. | `POST /plugins/install` |
| **Use** | A job names it with `pipeline: {"id": "publisher/name"}`; the engine runs it at the detection step ([Pipeline development](pipeline-development.md)). | `POST /jobs` |
| **Update** | A new version is planned and installed the same way, beside the old one, and becomes active once it validates. The old version is kept. | plan, install |
| **Roll back** | The previous version becomes active again (and the newer one previous, so you can go forward again). The Marketplace's button is **Go back to …**. | `POST /plugins/{publisher}/{name}/rollback` |
| **Turn off / on** | A plugin that is off can't be chosen for a job. | `.../disable`, `.../enable` |
| **Pin** | The Marketplace stops offering this plugin's updates. Installing a version by hand still works. Its buttons are **Keep this version (no update offers)** and **Offer updates again**. | `.../pin`, `.../unpin` |
| **Keys** | `secret` settings are stored in Clips Kitty's secrets store and handed only to this plugin's process. | `PUT .../secrets` |
| **Remove** | Every version Clips Kitty installed and the plugin's stored keys are deleted. | `DELETE /plugins/{publisher}/{name}` |

Only two versions are kept: the active one and the one before it. Installing a third deletes the oldest.

## Sources

```json
{"kind": "folder", "path": "C:\\Users\\you\\code\\my-plugin"}
{"kind": "git", "url": "https://github.com/example-dev/example-plugin", "commit": "<full 40-character commit hash>"}
```

- **A folder** is copied; `.git` and `__pycache__` are left out. This is the developer's loop: change your code, plan and install again. Installing the same version again replaces its files.
- **A Git commit** must be the full hash: a branch or tag can change after the user looked at it, a commit can't. With Git installed, Clips Kitty fetches that one commit into an empty repository of its own and writes the files out of Git's object store, so every file is the one the commit names. Without Git, a `github.com` repository is downloaded as GitHub's archive of that commit (GitHub's word for what the commit holds, not checked against the hash), and any other address says Git is needed.

In the desktop app both are under **For developers: install a pipeline you're writing**, a link at the bottom of Marketplace › Browse. It isn't a tab: creators find pipelines in Browse.

## What installing does not do

Installing **runs nothing** from your plugin: no `setup.py`, no install script, no Git hook, no Git filter (Git LFS included: an LFS file arrives as its small pointer, and the plan warns about it). Your code first runs when a job uses it. `tests/test_plugin_manager.py` checks this with a plugin whose `setup.py` and code would leave a mark if run, and Git hooks and a filter in the user's own Git settings that would leave one if Clips Kitty checked the files out.

A plugin is refused if it contains a symbolic link, a Git submodule, a path that would land outside its folder, a `.git` entry, a name Windows can't create, two names that differ only in case, more than 5000 files or more than 1 GB. Ship models separately ([Model references](model-references.md)).

## For this Clips Kitty

`requires.clips_kitty` is checked when the plugin is planned, installed or rolled back to, and again when a job is added and when it runs. A plugin an app update leaves behind stays installed, shows why it can't run, and is refused with that reason. `requires.plugin_api` must be one this Clips Kitty supports. See [Versioning](versioning.md).

## On disk

```text
<data folder>/plugins/
    installed.json                                  what is installed, active, on, pinned
    installed/<publisher>/<name>/<version>/         one folder per version
    staging/                                        a plan's files until installed (cleared after an hour)
    runs/                                           the job folders of the last five runs, for a look when something went wrong
    session.secret                                  the session secret, for scripts
```

The data folder is `%LOCALAPPDATA%\Clips Studio\data` in the installed app, `data/` in a source checkout. Versions live in separate folders and the active one is a pointer in `installed.json`, so nothing is overwritten in place; a file Windows keeps open during an update is removed on a later install. Untested on Windows.

## Doing it from a script

The routes that fetch, install, change or remove plugins need the session secret in an `X-Clips-Kitty-Session` header. The desktop app makes one at each start; the engine writes it to `session.secret`. Examples are in [`docs/API.md` › Plugins](../API.md#plugins), and why the header exists is in [Security](security.md).

```python
from pathlib import Path

import requests

data = Path("data")  # your data folder
headers = {"X-Clips-Kitty-Session": (data / "plugins" / "session.secret").read_text().strip()}
api = "http://127.0.0.1:8765"

plan = requests.post(f"{api}/plugins/plan", headers=headers,
                     json={"source": {"kind": "folder", "path": "C:/Users/you/code/my-plugin"}}).json()
for line in plan["errors"]:
    print("✗", line)
if plan["ok"]:
    print(requests.post(f"{api}/plugins/install", headers=headers, json={"plan_id": plan["plan_id"]}).json())
```

`GET /plugins` lists what is installed (no header): each plugin's versions, whether it is on and pinned, its source, the install screen's `details`, the names of keys that are set (never their values) a `problem` when it can't run, and `problems_here` for what this PC lacks (a Python to run it with); and the modes that ship with the app, marked Official.

## Tiers

| Tier | Means |
|---|---|
| Official | Ships inside Clips Kitty (Shorts, Gaming, Sports, described by `plugins/builtin/`) |
| ✓ Official · made by the Clips Kitty project | Installed from a listing in the index that comes with Clips Kitty, in one of the project's own repositories (the build checked each commit is on that repository's branches) |
| Community · not reviewed by a person | Installed from any other listing, in Awesome Clips Kitty or another index the user set up, whose automated checks passed; nobody has read its code |
| Not listed · Clips Kitty has not checked this | Installed from a folder or a Git address |

Listings can also carry ✓ Compatible (a version passed the automated compatibility checks: a technical label, not a review) and ★ Featured (picked by a maintainer); see [Marketplace publishing](marketplace-publishing.md). There is no "Verified" tier: nobody reviews plugin code, and Clips Kitty does not say otherwise. See [Security](security.md).
