# Troubleshooting

What goes wrong with plugins most often, what the message means, and what to do. The messages are quoted as Clips Kitty shows them.

## Where to look

- **The job's log.** Everything your plugin prints (progress messages, log lines and standard error) goes into the job's log, prefixed with your plugin's id. In the app: the queue, the job, its log. Through the API: `GET /jobs/{id}/log`.
- **The job folders.** The last five runs, of every plugin, finding and rating or understanding alike, are kept in `plugins/runs/` inside Clips Kitty's data folder (`%LOCALAPPDATA%\Clips Studio\data` in the installed app, `data/` in a checkout), each with the `job.json` and `transcript.json` your plugin received and whatever it wrote, `result.json` included.
- **Outside the app.** `python -m clipskitty_sdk run <your folder> --video <file>` builds the same job folder, runs your plugin, and prints the moments the app would take, without the app. `python -m clipskitty_sdk validate <your folder>` checks the manifest. See [Getting started](getting-started.md).

## Installing

| Message | Why | Fix |
|---|---|---|
| "Couldn't install this pipeline: Clips Kitty needs the full 40-character commit, not a branch or tag name …" | A branch or tag can change after you looked at it. | Use the commit (on GitHub, the long hash on the commit page). Pasting a GitHub link ending in `/tree/<commit>` fills it in. |
| "Couldn't download this pipeline. Check your internet connection and try again." | No connection, a timeout, or a connection that broke off. What Git or the download said is in Clips Kitty's log. | Check the connection and try again. |
| "Couldn't download this pipeline: its files aren't where its listing or link says any more. …" | The repository doesn't exist at that address, is private, or hasn't got that commit (not pushed yet, or gone after a force-push). Git's or GitHub's own words are in Clips Kitty's log. | Check the address and the commit, and that the repository is public; listed plugins must be public at that commit. |
| "Couldn't save this pipeline: this PC's disk is full. …", "Couldn't download this pipeline: the download arrived damaged. …", "Couldn't download this pipeline. Try again; if it happens again, send a bug report …" | A full disk; GitHub's archive that couldn't be unpacked or is of another commit; anything else Git or the download reported. The details are in Clips Kitty's log. | Free up space, or try again. |
| "The folder you chose (…) has no clipskitty.yaml in it." | The folder isn't the plugin's own. A folder whose one subfolder has `clipskitty.yaml` (what Windows makes of GitHub's "Download ZIP") is installed from that subfolder. | Choose the folder that holds `clipskitty.yaml`. |
| "Couldn't install this pipeline: it contains a shortcut (…), which Clips Kitty doesn't install. …", "…: it includes …, a folder linked in from another project (a submodule), which Clips Kitty doesn't download. …" | Symbolic links (and Windows junctions) and submodules can point outside the plugin. | Commit the files themselves. |
| "Couldn't install this pipeline: its listing or link points to a folder (…) that isn't in its files. …" | The listing or link names a subfolder that isn't there at that commit. | Check `path` in your listing, or the folder box. |
| "Couldn't install this pipeline: Clips Kitty couldn't read … in that folder. …" | Installing from a folder: another program (an editor, antivirus, a cloud-sync app) had that file locked, or a cloud copy didn't download. | Close the program, or make the file available offline, and try again. |
| "Some of this pipeline's files couldn't be downloaded, so it may not work." (a warning; Technical details names each file: "… is stored with Git LFS, which Clips Kitty does not fetch") | Large files in Git LFS are not fetched; the plugin gets the small pointer file. | Reference big files as models instead ([Model references](model-references.md)). |
| "Can't install: … needs Clips Kitty …" | `requires.clips_kitty` excludes this version. | Update Clips Kitty, or widen the range if your plugin works ([Versioning](versioning.md)). |
| "the listing says version …, the files say …" | A registry listing and the manifest at its commit disagree. | Fix the listing or tag a new commit. |
| "This install plan has expired or was already used" | Plans last an hour and are used once. | Look at the pipeline again. |
| "This needs the X-Clips-Kitty-Session header" | Installing and changing plugins needs the app's session secret. | Use the desktop app, or send the header from a script ([`docs/API.md` › Plugins](../API.md#plugins)). |
| "…: downloading it from this address needs Git, a free program that isn't installed on this PC. …" | Without Git, Clips Kitty can only fetch GitHub's archive of a commit. | Install Git, or host on GitHub. |

## Adding a video with a pipeline

| Message | Fix |
|---|---|
| "pipeline: the pipeline … isn't installed", "… is turned off" | Install it, or turn it on in Marketplace › Installed. |
| "setting 'mode': 'explode' is not one of …", "no setting called …", "'api_key' is a secret" | Only settings your manifest declares, with values of their type; secrets are entered once in Marketplace › Installed, never in a job. |
| "… can't be combined with Sports, Gaming scoring or Longform" | A pipeline picks the moments itself. Layouts (Vertical Live, Gaming / Reaction's split, Podcast) still work with it. |
| "rate[0]: the pipeline … can't rate moments others found: its manifest needs moments in inputs and ratings in outputs" (or understand, and context) | The plugin doesn't do that step. Declare `moments` in `inputs` and `ratings` (or `context`) in `outputs` ([Steps](steps.md)). |
| "pipeline: the pipeline … doesn't find moments: it rates or understands moments others found. Choose it under Rate & understand instead" | A rater or understander can't be a job's Pipeline. Name it under `rate` or `understand`. |
| "Rate & understand can't be combined with Longform: …", "rate: … is this job's pipeline, so it already scores and describes the moments it finds" | Longform picks and writes its clips its own way. A pipeline that rates or describes its own moments does so in its find run. |

## Running

| Message in the job | Why | Fix |
|---|---|---|
| "… needs Python 3.10 or newer, and none was found on this PC" | Only in a source checkout: the installed app runs plugins on its own Python. | Install Python, or set `plugins.python` in `settings.yaml` to one. |
| "This pipeline needs X, which this version of Clips Kitty doesn't include" | Your plugin imports a package the app's own Python doesn't have. | Use the standard library, ship pure-Python code in your plugin's folder, or call your own executable as `run.command`. |
| "This pipeline tried to use Clips Kitty's own code (…)" | Plugins can't import the engine's packages (`core`, `plugins`, `video`…); they change with every release. | Use the SDK and the job folder instead. |
| `UnicodeDecodeError` or garbled text reading a file | The app's Python never runs in UTF-8 mode. | Pass `encoding="utf-8"` to `open()`, `read_text()` and `write_text()`. |
| "… failed: …" | Your plugin exited with an error; the text is its last error line (`job.fail(...)`). | Read the job log; reproduce with `python -m clipskitty_sdk run`. |
| "… stopped before it finished. Try again; if it happens again, send a bug report from Feedback (it includes the details)." | Your plugin exited with a code other than 0 and no error line. Its output and the exit code are in the job log. | Report errors with `job.fail(...)`, or a `{"type": "error"}` line, in words the user understands. |
| "Clips Kitty couldn't start …" | `run.command` couldn't be started: a program that isn't there or can't run on this PC. The reason is in the log. | Check `run.command` and that the program is in your plugin's folder. |
| "… gave an answer Clips Kitty can't use: …" | `result.json` broke the contract: a range outside the video, end before start, a score outside 0-100, too many ranges. | The message names the field. `Job.add_range` and `Job.finish` check the same rules as you go, except the video's length, which Clips Kitty checks when it reads the result. |
| "… took longer than its … minute limit, so Clips Kitty stopped it." | `run.timeout_minutes` (default 60 for a run that finds moments, 10 for one that rates or understands them; at most 24 hours). | Raise it in your manifest if your plugin is slow on long videos. |
| "… can't run. Its AI model '…' isn't downloaded yet. …" | A model in your manifest hasn't been downloaded (in Ollama's case, on the Models page). | Download it in Marketplace › Installed, or on the Models page for an Ollama model. Give `size_bytes` and the message shows the size. |
| "… can't run. Its AI model '…' is shared only with people its makers give access to on Hugging Face, so Clips Kitty can't download it for you yet." | The model is gated (`gated: true`), and Clips Kitty doesn't sign in to Hugging Face yet. | Choose an ungated model. |
| "the pipeline … can't run here: it needs Clips Kitty …" | An app update left the plugin's version range behind. | Install a newer version of the plugin. |
| "the pipeline … is blocked: …" | The version is on a registry block list. | Remove it in the Marketplace; install a version that isn't blocked. |
| No moments found | Your plugin returned no ranges. | Normal for a video without what it looks for; `job.finish(notes=...)` lets you say why in the log. |

## Rate & understand

| What you see | Why | Fix |
|---|---|---|
| "ignored: scores, because this run wasn't asked to rate", or your scores change nothing | The run wasn't asked to rate: your manifest doesn't declare `ratings` (with `moments` in `inputs`), or the creator chose the plugin only to understand. | Declare `ratings` and `moments`, and choose the plugin in a Rate row under **Rate & understand**. A pipeline's own scores go in its ranges' `score`. |
| "unrecognized arguments: --moments", or "unknown input 'moments'" | The SDK you run is older than 1.1.0. | Install it again (the `pip install` line in [`sdk/python/README.md`](../../sdk/python/README.md)), or point `PYTHONPATH` at a current checkout's `sdk/python`. A plugin that vendors its own copy needs 1.1.0 too. |
| "Clips Kitty made these clips without …" on the video page, "Going on without …" in the job log | The plugin couldn't run, or gave an answer Clips Kitty couldn't use, so it was skipped. The sentence after the name says why; the technical message is on the next log line. | Fix what it says and process the video again with `force`. Meanwhile a watched channel that posts automatically holds that video's clips for the creator, and a job told to publish when it finishes skips publishing and leaves them for the creator too. The command-line daily upload isn't held. |
| "… rated every moment under your minimum score", "… moment(s) rated under the minimum score (55) set aside" | A rated moment under the job's `min_score` is set aside, unless it is a must-have. | Check your scale: 55 is the app's default minimum. Rate a moment you want kept at or above it, and leave a moment you have no opinion on unrated. |
| Your notes aren't in an older clip's title | A forced re-run keeps the titles of clips made before, so the creator's edits win. New notes show in the clip editor and reach new clips' titles. | Nothing to fix in your plugin. The creator can edit the title; clips made from now on get the notes. |
| "… took longer than its 10 minute limit, so Clips Kitty stopped it." | A run that rates or understands has a 10-minute default. | Set `run.timeout_minutes` in your manifest. |
| "… note(s) not kept: at most 8 for each moment" | Every plugin's notes for one moment together are kept up to 8. | Give fewer, more useful notes; at most 5 from one run. |

A plugin's own Python packages are not installed by Clips Kitty yet (planned). Until then, use the standard library, the SDK, FFmpeg through `job.tools`, Ollama, or ship your plugin as an executable.

## Models

| Message | Fix |
|---|---|
| "… is in a pickle format, which can run code when it is loaded. Confirm to download it." | Tick the box in the download dialog if you trust it; better, use a safetensors or ONNX file. |
| "Its makers share this model only with people who sign in to Hugging Face and are given access. …" | The model is gated, and Clips Kitty doesn't sign in to Hugging Face yet. Choose an ungated model. |
| "Couldn't download … Check your internet connection and try again." | No connection, a timeout, or a connection that broke off; what went wrong is in Clips Kitty's log. Try again. |
| "Couldn't download …: it isn't at its address any more. Ask the pipeline's developer." | The address answered 401, 403, 404 or 410: the file was moved, removed or made private. Fix the address (or the commit) in your manifest. |
| "Couldn't save …: this PC's disk is full …", "Couldn't save … in Clips Kitty's model folder. …" | The download arrived but couldn't be written to Clips Kitty's shared model folder. Free up space, or send a bug report from Feedback. |
| "…: its SHA-256 doesn't match …", "got … bytes, expected …" | The file changed or the download broke. Try again; if it persists, the manifest's checksum is wrong. |
| Marketplace says the model is "stored twice" | Windows refused a link, so the file was copied. It works; it takes the space twice. Turning on Windows Developer Mode allows links. |

## The Marketplace

- **"No pipelines are listed yet."** Neither the list that came with this version nor Clips Kitty's online list has a pipeline yet (or the online list hasn't been fetched: press **Check for new pipelines**, which the desktop app shows at the bottom of Browse). To try your own pipeline meanwhile, install it from **For developers: install a pipeline you're writing** at the bottom of Browse.
- **A listing is missing.** A new listing, or a new version, reaches the Marketplace once its pull request is merged, the next time the Marketplace checks Clips Kitty's online list (when it opens, at most once a day, unless that is switched off at the bottom of Browse), or at once with **Check for new pipelines**. Until the next release bundles it, it shows as Community. A change to an existing listing's text, and anything in the Clips Kitty project's own repositories, waits for the next release. A blocked version is never shown. If the footer says the check didn't work, it says why: no connection, nothing at the address yet, or a list the app can't read.
- **Links don't open.** Each opens only after a dialog showing the address; outside the desktop app the address is shown as text to copy.
