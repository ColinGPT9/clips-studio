# Troubleshooting

What goes wrong with plugins most often, what the message means, and what to do. The messages are quoted as Clips Kitty shows them.

## Where to look

- **The job's log.** Everything your plugin prints (progress messages, log lines and standard error) goes into the job's log, prefixed with your plugin's id. In the app: the queue, the job, its log. Through the API: `GET /jobs/{id}/log`.
- **The job folders.** The last five runs are kept in `plugins/runs/` inside Clips Kitty's data folder (`%LOCALAPPDATA%\Clips Studio\data` in the installed app, `data/` in a checkout), each with the `job.json` and `transcript.json` your plugin received and whatever it wrote, `result.json` included.
- **Outside the app.** `python -m clipskitty_sdk run <your folder> --video <file>` builds the same job folder, runs your plugin, and prints the moments the app would take, without the app. `python -m clipskitty_sdk validate <your folder>` checks the manifest. See [Getting started](getting-started.md).

## Installing

| Message | Why | Fix |
|---|---|---|
| "a Git source needs the full 40-character commit hash" | A branch or tag can change after you looked at it. | Use the commit (on GitHub, the long hash on the commit page). Pasting a GitHub link ending in `/tree/<commit>` fills it in. |
| "Couldn't get the plugin's files: the repository has no commit …" | The commit hasn't been pushed, or the repository is private. | Push it; listed plugins must be public at that commit. |
| "…: symbolic links are not allowed in a plugin", "…: the plugin uses a Git submodule, which Clips Kitty does not fetch" | Links and submodules can point outside the plugin. | Commit the files themselves. |
| "the commit has no folder …" | The listing or link names a subfolder that isn't there at that commit. | Check `path` in your listing, or the folder box. |
| "… is stored with Git LFS, which Clips Kitty does not fetch" (a warning) | Large files in Git LFS are not fetched; the plugin gets the small pointer file. | Reference big files as models instead ([Model references](model-references.md)). |
| "Can't install: … needs Clips Kitty …" | `requires.clips_kitty` excludes this version. | Update Clips Kitty, or widen the range if your plugin works ([Versioning](versioning.md)). |
| "the listing says version …, the files say …" | A registry listing and the manifest at its commit disagree. | Fix the listing or tag a new commit. |
| "This install plan has expired or was already used" | Plans last an hour and are used once. | Look at the plugin again. |
| "This needs the X-Clips-Kitty-Session header" | Installing and changing plugins needs the app's session secret. | Use the desktop app, or send the header from a script ([`docs/API.md` › Plugins](../API.md#plugins)). |
| "installing from this address needs Git, which isn't installed on this PC" | Without Git, Clips Kitty can only fetch GitHub's archive of a commit. | Install Git, or host on GitHub. |

## Adding a video with a pipeline

| Message | Fix |
|---|---|
| "pipeline: the pipeline … isn't installed", "… is turned off" | Install it, or turn it on in Marketplace › Installed. |
| "setting 'mode': 'explode' is not one of …", "no setting called …", "'api_key' is a secret" | Only settings your manifest declares, with values of their type; secrets are entered once in Marketplace › Installed, never in a job. |
| "… can't be combined with Sports, Gaming scoring or Longform" | A pipeline picks the moments itself. Layouts (Vertical Live, Gaming / Reaction's split, Podcast) still work with it. |

## Running

| Message in the job | Why | Fix |
|---|---|---|
| "… needs Python 3.10 or newer, and none was found on this PC" | Only in a source checkout: the installed app runs plugins on its own Python. | Install Python, or set `plugins.python` in `settings.yaml` to one. |
| "This pipeline needs X, which this version of Clips Kitty doesn't include" | Your plugin imports a package the app's own Python doesn't have. | Use the standard library, ship pure-Python code in your plugin's folder, or call your own executable as `run.command`. |
| "This pipeline tried to use Clips Kitty's own code (…)" | Plugins can't import the engine's packages (`core`, `plugins`, `video`…); they change with every release. | Use the SDK and the job folder instead. |
| `UnicodeDecodeError` or garbled text reading a file | The app's Python never runs in UTF-8 mode. | Pass `encoding="utf-8"` to `open()`, `read_text()` and `write_text()`. |
| "… failed: …" | Your plugin exited with an error; the text is its last error line (`job.fail(...)`) or its last output. | Read the job log; reproduce with `python -m clipskitty_sdk run`. |
| "… gave an answer Clips Kitty can't use: …" | `result.json` broke the contract: a range outside the video, end before start, a score outside 0-100, too many ranges. | The message names the field. `Job.add_range` and `Job.finish` check the same rules as you go, except the video's length, which Clips Kitty checks when it reads the result. |
| "the plugin took longer than its … minute limit" | `run.timeout_minutes` (default 60, at most 24 hours). | Raise it in your manifest if your pipeline is slow on long videos. |
| "its model '…' isn't on this PC: …" | A model in your manifest hasn't been downloaded (or pulled, for Ollama). | Download it in Marketplace › Installed, or pull it on the Models page. |
| "the pipeline … can't run here: it needs Clips Kitty …" | An app update left the plugin's version range behind. | Install a newer version of the plugin. |
| "the pipeline … is blocked: …" | The version is on a registry block list. | Remove it in the Marketplace; install a version that isn't blocked. |
| No moments found | Your plugin returned no ranges. | Normal for a video without what it looks for; `job.finish(notes=...)` lets you say why in the log. |

A plugin's own Python packages are not installed by Clips Kitty yet (planned). Until then, use the standard library, the SDK, FFmpeg through `job.tools`, Ollama, or ship your plugin as an executable.

## Models

| Message | Fix |
|---|---|
| "… is in a pickle format, which can run code when it is loaded. Confirm to download it." | Tick the box in the download dialog if you trust it; better, use a safetensors or ONNX file. |
| "this model is gated on Hugging Face …" | Clips Kitty doesn't sign in to Hugging Face yet. Choose an ungated model. |
| "…: its SHA-256 doesn't match …", "got … bytes, expected …" | The file changed or the download broke. Try again; if it persists, the manifest's checksum is wrong. |
| Marketplace says the model is a "copy" | Windows refused a link, so the file was copied. It works; it takes the space twice. Turning on Windows Developer Mode allows links. |

## The Marketplace

- **"No pipelines are listed yet."** Neither the list that came with this version nor Clips Kitty's online list has a pipeline yet (or the online list hasn't been fetched: press **Check for new pipelines**, which the desktop app shows at the bottom of Browse). Install from a folder or a link meanwhile.
- **A listing is missing.** A new listing, or a new version, reaches the Marketplace once its pull request is merged, the next time the Marketplace checks Clips Kitty's online list (when it opens, at most once a day, unless that is switched off at the bottom of Browse), or at once with **Check for new pipelines**. Until the next release bundles it, it shows as Community. A change to an existing listing's text, and anything in the Clips Kitty project's own repositories, waits for the next release. A blocked version is never shown. If the footer says the check didn't work, it says why: no connection, nothing at the address yet, or a list the app can't read.
- **Links don't open.** Each opens only after a dialog showing the address; outside the desktop app the address is shown as text to copy.
