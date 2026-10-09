# Security

What Clips Kitty does and does not protect when someone installs a plugin. Written for the people installing plugins as much as for the people writing them. Where something is planned rather than built, it says so.

## The one sentence

**A plugin is code from the internet that runs on your PC with your rights.** The install screen says so before anything is installed: "This pipeline is a program from the internet. It can do anything you can do on this PC." Clips Kitty does not sandbox plugins: a plugin can do what any program you install can do.

## What Clips Kitty does

| Protection | What it covers | Status |
|---|---|---|
| **Nothing runs at install** | Installing copies files and checks the manifest. No `setup.py`, no install script, no Git hook or filter. Your code first runs when a job uses it. | built, tested |
| **You see it first** | Publisher, source and commit, licence, tier and labels, what it is built on, each permission with whether it is enforced or declared (shown as "Clips Kitty hands this over" or "the developer says so"), ⚠ a line for each kind of data that leaves the PC, requirements, a paid service, and that keys are readable by other programs. An update lists new permissions, hosts and data warnings. | built: the install dialog in the Marketplace, which also asks you to tick what you accept (an unlisted or unreviewed source, data leaving the PC, a required outside account) before Install is enabled |
| **Exactly the files you looked at** | A Git install takes one full commit hash and Git checks every file against it; the files planned are the files installed, checked again before they move into place. | built, tested |
| **Files stay in their folder** | Symbolic links, submodules, paths that leave the folder, `.git` entries and archive tricks refuse the plugin. | built, tested; untested on Windows |
| **Only what was asked for is handed over** | The video, transcript, FFmpeg and the local AI model are put in the job only with their permission ([Permissions](permissions.md)). | built, tested |
| **No credentials by accident** | A plugin's process does not inherit Clips Kitty's settings, the session secret, or any variable whose name looks like a key, token, password or cookie. | built, tested |
| **It stops when told** | Cancel and the time limit stop the plugin and every process it started. | built; the Windows path is untested |
| **Suggested edits wait for you** | A plugin chosen under Suggest edits answers with suggestions, which Clips Kitty keeps beside the clip. Clips Kitty doesn’t put a suggestion into a clip until you use it in the editor and apply your edits (Apply edits, or Apply edits & upload). | built: the timeline editor's suggestions, with Use, Hide and Take it back |
| **Turn off, roll back, remove** | One click each; removal deletes the plugin's files and stored keys. | built (routes and the Marketplace's Installed tab) |
| **Block list** | A maintainer can block a version for everyone; a blocked plugin is refused at install and at run, and flagged where it is installed. | built: the catalog's `registry/blocklist.yaml` and every cached index's list, applied at install, at run and in the plugin list; a new block merged into the catalog reaches an installed copy through Clips Kitty's online list (DECISIONS D29): the next time Clips Kitty opens with a pipeline installed, or its Marketplace opens (at most once a day, while the automatic checks at the bottom of Browse are on), at once with **Check for new pipelines**, or with the next app release |

## What it does not do, and says so

- **No sandbox.** A plugin can read and write your files, start programs and use the network. `filesystem`, `network`, `project`, `gpu` and `sends` are declarations the developer makes; nothing enforces them.
- **Suggest edits is about what Clips Kitty does, not a wall.** The sentence above describes what Clips Kitty does with a suggestion. A plugin runs with your rights, so nothing stops its own code from changing your clip files or posting by itself, as with any program you install.
- **No code review.** Nobody reads plugin code before it is listed. A listing means automated checks passed, nothing more, and ✓ Compatible means one version installed and ran on a sample video on a throwaway machine. Neither is a security review. There is no "Verified" badge.
- **No signing.** A commit hash proves the files are the ones listed, not who wrote them.
- **Keys are not isolated between plugins.** A plugin's keys are stored for your account (encrypted with Windows DPAPI on Windows) and handed only to that plugin, but any program running as you, another plugin included, can read the store.
- **The local API has no password.** Any program on your PC can call it, plugins included.
- **Resource limits** (memory, processes) are designed, not built. They will be called enforced only once built and tested on Windows.

## Tiers and labels

Every installed plugin has a tier, which says where it came from:

| Tier | Shown as | Means |
|---|---|---|
| `official` | Official | Ships inside Clips Kitty (Shorts, Gaming, Sports). |
| `listed-official` | ✓ Official · made by the Clips Kitty project | Installed from a listing whose repository belongs to the Clips Kitty project. |
| `listed` | Community · not reviewed by a person | Installed from a listing; the catalog's automated checks passed. |
| `link` | Not listed · Clips Kitty has not checked this | Installed from a folder or a Git address no index lists. |

Every tier but `official` gets the line "This pipeline is a program from the internet. It can do anything you can do on this PC." The Marketplace also shows labels on listings: ✓ Official, ✓ Compatible, ★ Featured or Community ([Marketplace publishing](marketplace-publishing.md#labels)). **✓ Compatible is a technical label, not a trust or security guarantee:** the version's manifest is valid, it installs, its requirements are met, and it runs on a sample video and gives an answer Clips Kitty accepts. Nobody reads the code, and a plugin can pass and still do something its listing doesn't say. None of these changes what a plugin is allowed to do ([Permissions](permissions.md)).

## What Clips Kitty sends when you install

Installing from a listing fetches the plugin from GitHub at its commit, and any models you choose to download from where the manifest says. The Marketplace's numbers (stars, downloads, installs) come inside the index; the app never asks GitHub or Hugging Face for them.

**The install counter** (`plugins/counter.py`): after a first install from a listing in the index bundled with the app, Clips Kitty requests one small file named after the listing from that index's address, which must be a release download in one of the project's own GitHub repositories, so the catalog can count installs. Other indexes can't count installs. The request carries no account, no identifier, no cookie, no app version and nothing about your videos; GitHub sees the address it comes from, as it does for any download. Updates, rollbacks and version switches are not counted. Switch it off with "Count my installs" in the Marketplace, or with `plugins.count_installs: false` in `settings.yaml`. The installed app keeps `settings.yaml` in each Windows account's own app-data folder, so that setting switches counting off for one account, not for everyone on the PC. **Not on yet:** the catalog has no counter address (the catalog's `registry/catalog.yaml`), so nothing is sent.

## The session secret

The routes that fetch, install, change or remove plugins need an `X-Clips-Kitty-Session` header. The desktop app makes a random secret at each start, gives it to the engine and to its own window, and to nothing else; the engine also writes it to `<data folder>/plugins/session.secret` for scripts.

**What it stops:** web pages. A page you visit can send some requests to `127.0.0.1` without being able to read the answer, but it cannot add a custom header without the browser asking the engine first, and the engine refuses pages other than the app's own (`tests/test_plugin_api.py` checks this). It also stops scripts that call the API without knowing the secret.

**What it does not stop:** software already running as you, which can read that file or the engine's environment. It is not a boundary against an installed plugin.

## For developers

- Ask for the permissions you use and no more; users see the list.
- Declare every host you connect to in `network`, and everything that leaves the PC in `sends`, with who receives it and when. A `local` pipeline must send nothing.
- Pin models by commit or SHA-256 ([Model references](model-references.md)). Prefer formats that cannot run code when loaded (ONNX, safetensors) over pickle files.
- Never ask users to paste credentials into settings that are not `secret`.

## Reporting a harmful plugin

Report it in [Awesome Clips Kitty's repository](https://github.com/ColinGPT9/awesome-clips-kitty): as an issue, or privately through GitHub's private vulnerability reporting there. A maintainer blocks or delists the version in its `registry/blocklist.yaml`.
