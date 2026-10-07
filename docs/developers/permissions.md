# Permissions

A plugin lists what it needs in its manifest's `permissions`, `network` and `sends`. The user sees each one, in plain words, before installing. This page says, for each, whether Clips Kitty **enforces** it (checks it and refuses otherwise) or whether it is **declared** (the developer states it and nothing stops the plugin doing otherwise). The install screen shows enforced as "Clips Kitty hands this over" and declared as "the developer says so", and explains them: "“Clips Kitty hands this over”: Clips Kitty decides what the pipeline is given. “The developer says so”: a promise only. Nothing stops a pipeline doing more, because it can do anything you can do on this PC." (`plugins/permissions.py`).

**The rule behind the table.** A plugin is a program that runs on the user's PC with the user's own rights, as any program they install does. Clips Kitty decides what it *hands over* to a plugin, so a permission can be enforced for what is handed over. Clips Kitty does not sandbox the plugin's process, so it cannot stop a plugin from opening files, starting programs or using the network on its own. Where this page says "declared", that is why.

| Permission | Shown to the user as | Enforced or declared | Status |
|---|---|---|---|
| `video.read` | Reads the video you process | **Enforced** for the hand-over: without it the job has no video path. Declared beyond that. | built |
| `transcript.read` | Reads its transcript | **Enforced** for the hand-over: without it no transcript is written. Declared beyond that. | built |
| `ffmpeg` | Uses Clips Kitty's FFmpeg | **Enforced** for the hand-over: without it the job has no FFmpeg path. Declared beyond that. | built |
| `ollama` | Uses your local AI model | **Enforced** for the hand-over: without it the job has no Ollama address. A cloud AI provider's name and key are never handed over. Declared beyond that: Ollama has no password, so any program on the PC can use it. | built |
| `gpu` | Uses your graphics card | Declared (a requirement, compared with this PC) | built (shown with the plugin manager) |
| `network` with the `network` hosts | Connects to: api.example.com, … | Declared. Clips Kitty does not filter a plugin's connections. | built (validated; shown with the plugin manager) |
| `sends` | ⚠ Sends your video / audio / frames / transcript to … | Declared, and shown as a warning wherever the plugin is listed or installed | built (validated; shown with the plugin manager and Marketplace) |
| `filesystem.read`, `filesystem.write` | Reads / writes files outside its own folder | Declared | built (validated) |
| `project.read`, `project.write` | Reads / changes your clip library | Declared: the local API has no authentication, so any program on the PC can call it | built (validated) |
| `models` (the manifest's `models:` list) | Uses these models: … | Enforced for downloads Clips Kitty makes (only the listed files, checked against their size and SHA-256, and only when the user presses Download); declared beyond, since the plugin's own code can fetch anything | built ([Model references](model-references.md)) |
| `clips.write` | Returns finished clips | Refused in plugin contract 1 | planned |

The manifest validator also ties them together: an `inputs` entry needs its permission (`video` needs `video.read`), hosts in `network` need the `network` permission and the other way round, and a `remote` or `hybrid` pipeline must list both `network` and `sends`. A `local` pipeline may not list `sends`.

## Also enforced, whatever the permissions

- **The environment.** A plugin's process does not inherit Clips Kitty's own variables (`CLIPS_*`, `CLIPSKITTY_*`) or any variable whose name contains KEY, TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL, COOKIE or AUTH. This stops Clips Kitty handing over credentials by accident; it is not a wall, since the plugin can read the user's files.
- **Secrets.** A plugin's `secret` settings reach only its own process, only in its environment, never in a file. They are not isolated from other software: the store is encrypted for the user's account (Windows) or readable only by the user (elsewhere), so another plugin running as the user can read it.
- **Settings.** Only settings the manifest declares, with values that fit their type.
- **Time.** A run stops at `run.timeout_minutes`, and a cancelled job stops the plugin and everything it started.
- **Installing runs nothing** from the plugin (built with the plugin manager).

## Not enforced, and said so

- No sandbox: file, process and network access are the user's.
- No resource limits on memory or child processes. Windows Job Object limits are designed and will be called enforced only once built and tested on Windows.
- No per-plugin API credential: the local API is open to every program on the PC, plugins included. A scoped credential is designed, not built.

## Tiers and labels grant nothing

A plugin's install tier ("✓ Official · made by the Clips Kitty project", "Community · not reviewed by a person", "Not listed · Clips Kitty has not checked this") and its Marketplace labels (✓ Official, ✓ Compatible, ★ Featured) change nothing in the tables above: every plugin is handed what its permissions allow, and the same things stay declared. ✓ Compatible means one version's manifest is valid, it installs, its requirements are met and it runs on a sample video with an answer Clips Kitty accepts; it does not check that the plugin keeps to its declared `network`, `sends` or file permissions.

Clips Kitty's own install counter is not a plugin permission and sends nothing about the user or their videos; [Security](security.md#what-clips-kitty-sends-when-you-install) says what it sends and how to switch it off.

See [Security](security.md) for what this means for people installing plugins.
