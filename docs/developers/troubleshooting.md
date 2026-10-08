# Troubleshooting

What goes wrong with plugins most often, what the message means, and what to do. The messages are quoted as Clips Kitty shows them.

## Where to look

- **The job's log.** Everything your plugin prints (progress messages, log lines and standard error) goes into the job's log, prefixed with your plugin's id. In the app: the queue, the job, its log. Through the API: `GET /jobs/{id}/log`.
- **The job folders.** The last five runs, of every plugin, finding, rating, understanding and suggesting edits alike, are kept in `plugins/runs/` inside Clips Kitty's data folder (`%LOCALAPPDATA%\Clips Studio\data` in the installed app, `data/` in a checkout), each with the `job.json` and `transcript.json` your plugin received and whatever it wrote, `result.json` included.
- **Outside the app.** `python -m clipskitty_sdk run <your folder> --sample` (or `--video <file>`) builds the same job folder, runs your plugin, and prints the moments the app would take, without the app. `python -m clipskitty_sdk validate <your folder>` checks the manifest and the code. In PowerShell, run them as `py -m clipskitty_sdk`. See [Getting started](getting-started.md).

## While developing

What the SDK's commands and a plugin's first runs say most often. The commands are written `python -m clipskitty_sdk`; in PowerShell they are `py -m clipskitty_sdk`.

| Message | Why | Fix |
|---|---|---|
| "No module named clipskitty_sdk" | The SDK isn't installed for the Python that ran the command. | Install it with the `pip install` line in [`sdk/python/README.md`](../../sdk/python/README.md), or point `PYTHONPATH` at a checkout's `sdk/python` ([SDK](sdk.md#getting-the-sdk)). |
| "No module named clipskitty_sdk", after `py -m pip install` worked | A later command used `python`, which on Windows can be a different Python from the one `py` runs. | Run every command with `py`: `py -m clipskitty_sdk`, `py -m pytest`. |
| "error: reading clipskitty.yaml needs PyYAML, which isn't installed with this Python: …" | PyYAML is missing on your PC. It is no fault of your plugin's. | Install the SDK with the `yaml` extra: `"clipskitty-sdk[yaml,test] @ git+…"`. |
| An error about `&&` in PowerShell | Windows PowerShell 5.1 doesn't run two commands joined with `&&`. | Put each command on its own line. These pages never join them. |
| "unrecognized arguments" after a `--set` value with commas, in PowerShell | PowerShell passes an unquoted value with commas, such as `banner_region=0.30,0.10,0.40,0.10`, as separate arguments. | Quote it: `--set "banner_region=0.30,0.10,0.40,0.10"`. `--region` and `--steps` take the split form too, but quoting is never wrong. |
| "This is a Clips Kitty plugin: Clips Kitty starts it with a job folder." | You ran `src/main.py` directly. A plugin needs the job folder Clips Kitty makes. | Run it with `python -m clipskitty_sdk run <the plugin's folder> --sample`, as the message says. |
| "warning: this plugin asks for ffmpeg, but FFmpeg isn't on PATH. …", "error: --sample needs FFmpeg to make the test video. …" | FFmpeg isn't installed, or isn't on `PATH`. | Install FFmpeg, or pass `--ffmpeg` and `--ffprobe`. An installed Clips Kitty has both, in `resources\backend\_internal\ffmpeg` inside the folder it was installed to. A plugin that reads only the transcript runs on `--sample` without FFmpeg. |
| "error: --set: this pipeline has no setting called '…'; its settings are …" | The manifest declares no setting by that name. | Use one of the names it lists, or declare the setting under `settings` in `clipskitty.yaml`. |
| "the setting … has no value: choose one in the pipeline's settings, or ask its developer" | `job.settings[name]` for a setting with no default that nobody chose. The job's log has the developer's hint. | Give the setting a `default`, or use `job.settings.get(name, <default>)`. A `secret` setting is never in `job.settings` and can't have a default: read it with `job.secret(name)`, which gives `None` when it isn't set. When the creator did set the secret, the creator sees "it stopped on a mistake in its own code" and the log says to use `job.secret(name)`. |
| "This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's developer which version it needs." | The plugin imports a part of the SDK that the Clips Kitty running it doesn't have: a module added in a later SDK than the one that Clips Kitty bundles. The import error is in the job's log. | Creators: update Clips Kitty. Developers: set `requires.clips_kitty` to the first release that bundles the SDK you use ([Versioning](versioning.md#which-release-runs-plugins)). |
| "cannot import name … from 'clipskitty_sdk'", with a path from inside Clips Kitty | An SDK import inside `main()`, run by an SDK older than 1.2.0, which doesn't turn it into the sentence above. | Import the SDK and its modules at the top of the file, as the templates do. |
| "error: .venv is inside the plugin's folder, and Clips Kitty would copy it into every install. …" (or `venv`, `node_modules`) | `install` refuses a virtual environment inside the plugin's folder: Clips Kitty copies everything in the folder. | Move it next to the plugin's folder, then run `install` again. |
| "warning: src/main.py line 12: open() without encoding=. …" (or `read_text()`, `write_text()`) | Clips Kitty's Python doesn't use UTF-8 mode, so on Windows a text file would be read in the PC's own code page. | Pass `encoding="utf-8"`. |
| "warning: src/main.py line 3: imports numpy. Pipelines run on Clips Kitty's own Python, …" | Clips Kitty's Python promises only the standard library and `clipskitty_sdk`, and can't install other packages for a plugin yet. | Use the standard library and the SDK's helpers, or ship pure-Python code in `src/`. |
| "warning: src/main.py line 2: imports helpers, but helpers.py is at the plugin's root, …: move it into src/" | Only the script's own folder is on the path when Clips Kitty runs it. | Move `helpers.py` into `src/`, beside `main.py`. |
| "warning: src/main.py line 7: this needs a newer Python than Clips Kitty's (3.11): …" | The code uses syntax from a later Python. Library calls added after 3.11 aren't caught here: test on 3.11, as the generated GitHub workflow does. | Write it for Python 3.11. |
| "error: This Clips Kitty (2.0.0) can't install plugins: it came out before plugin support. …" | No Clips Kitty release runs plugins yet. Plugins made with SDK 1.2.0 need the first release that includes it; until that is out, run Clips Kitty from source. | Run Clips Kitty from source: [From source](../../README.md#from-source). |
| "error: Clips Kitty isn't running: nothing answered at http://127.0.0.1:8765. …" | `install` talks to the Clips Kitty running on this PC. | Open Clips Kitty, then run `install` again. |
| "error: couldn't find Clips Kitty's session file. …" | `install` needs `plugins/session.secret` from Clips Kitty's data folder and couldn't find it. | Pass `--data-dir` with Clips Kitty's data folder. |

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
| "This needs the X-Clips-Kitty-Session header" | Installing and changing plugins needs the app's session secret. | Use the desktop app or `python -m clipskitty_sdk install` ([SDK](sdk.md#installing-it-into-clips-kitty)), or send the header from a script ([`docs/API.md` › Plugins](../API.md#plugins)). |
| "…: downloading it from this address needs Git, a free program that isn't installed on this PC. …" | Without Git, Clips Kitty can only fetch GitHub's archive of a commit. | Install Git, or host on GitHub. |

## Adding a video with a pipeline

| Message | Fix |
|---|---|
| "pipeline: the pipeline … isn't installed", "… is turned off" | Install it, or turn it on in Marketplace › Installed. |
| "setting 'mode': 'explode' is not one of …", "no setting called …", "'api_key' is a secret" | Only settings your manifest declares, with values of their type; secrets are entered once in Marketplace › Installed, never in a job. |
| "… can't be combined with Sports, Gaming scoring or Longform" | A pipeline picks the moments itself. Layouts (Vertical Live, Gaming / Reaction's split, Podcast) still work with it. |
| "rate[0]: the pipeline … can't rate moments others found: its manifest needs moments in inputs and ratings in outputs" (or understand, and context) | The plugin doesn't do that step. Declare `moments` in `inputs` and `ratings` (or `context`) in `outputs` ([Steps](steps.md)). |
| "pipeline: the pipeline … doesn't find moments: it rates or understands moments others found. Choose it under Rate & understand instead" | A rater or understander can't be a job's Pipeline. Name it under `rate` or `understand`. |
| "edit[0]: the pipeline … can't suggest edits for clips: its manifest needs moments in inputs and edits in outputs" | The plugin doesn't suggest edits. Declare `moments` in `inputs` and `edits` in `outputs` ([Steps](steps.md#suggest-edits-the-edit-step)). |
| "pipeline: the pipeline … doesn't find moments: it suggests edits for the clips Clips Kitty makes. Choose it under Suggest edits instead" | A plugin that only suggests edits can't be a job's Pipeline. Name it under `edit`. |
| "Rate & understand can't be combined with Longform: …", "Suggest edits can't be combined with Longform: …", "rate: … is this job's pipeline, so it already scores and describes the moments it finds" | Longform picks and writes its clips its own way. A pipeline that rates or describes its own moments does so in its find run. A job's own pipeline may be named under Suggest edits: it then has an edit run of its own after the clips are chosen. |

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
| "… took longer than its … minute limit, so Clips Kitty stopped it." | `run.timeout_minutes` (default 60 for a run that finds moments, 10 for one that rates or understands them or suggests edits; at most 24 hours). | Raise it in your manifest if your plugin is slow on long videos. |
| "… can't run. Its AI model '…' isn't downloaded yet. …" | A model in your manifest hasn't been downloaded (in Ollama's case, on the Models page). | Download it in Marketplace › Installed, or on the Models page for an Ollama model. Give `size_bytes` and the message shows the size. |
| "… can't run. Its AI model '…' is shared only with people its makers give access to on Hugging Face, so Clips Kitty can't download it for you yet." | The model is gated (`gated: true`), and Clips Kitty doesn't sign in to Hugging Face yet. | Choose an ungated model. |
| "the pipeline … can't run here: it needs Clips Kitty …" | An app update left the plugin's version range behind. | Install a newer version of the plugin. |
| "the pipeline … is blocked: …" | The version is on a registry block list. | Remove it in the Marketplace; install a version that isn't blocked. |
| No moments found | Your plugin returned no ranges. | Normal for a video without what it looks for; `job.finish(notes=...)` lets you say why in the log. |

## Rate & understand

| What you see | Why | Fix |
|---|---|---|
| "ignored: scores, because this run wasn't asked to rate", or your scores change nothing | The run wasn't asked to rate: your manifest doesn't declare `ratings` (with `moments` in `inputs`), or the creator chose the plugin only to understand. | Declare `ratings` and `moments`, and choose the plugin in a Rate row under **Rate & understand**. A pipeline's own scores go in its ranges' `score`. |
| "unrecognized arguments: --moments", or "unknown input 'moments'" | The SDK you run is older than 1.1.0. `--sample`, `new`, `sample`, `frame`, `install` and `listing` need 1.2.0. | Install it again (the `pip install` line in [`sdk/python/README.md`](../../sdk/python/README.md)), or point `PYTHONPATH` at a current checkout's `sdk/python`; `python -m clipskitty_sdk --version` says which you have. A plugin that vendors its own copy needs 1.1.0 too, and the 1.2.0 helpers need 1.2.0. |
| "Clips Kitty made these clips without …" on the video page, "Going on without …" in the job log | The plugin couldn't run, or gave an answer Clips Kitty couldn't use, so it was skipped. The sentence after the name says why; the technical message is on the next log line. | Fix what it says and process the video again with `force`. Meanwhile a watched channel that posts automatically holds that video's clips for the creator, and a job told to publish when it finishes skips publishing and leaves them for the creator too. The command-line daily upload isn't held. |
| "… rated every moment under your minimum score", "… moment(s) rated under the minimum score (55) set aside" | A rated moment under the job's `min_score` is set aside, unless it is a must-have. | Check your scale: 55 is the app's default minimum. Rate a moment you want kept at or above it, and leave a moment you have no opinion on unrated. |
| Your notes aren't in an older clip's title | A forced re-run keeps the titles of clips made before, so the creator's edits win. New notes show in the clip editor and reach new clips' titles. | Nothing to fix in your plugin. The creator can edit the title; clips made from now on get the notes. |
| "… took longer than its 10 minute limit, so Clips Kitty stopped it." | A run that rates or understands has a 10-minute default. | Set `run.timeout_minutes` in your manifest. |
| "… note(s) not kept: at most 8 for each moment" | Every plugin's notes for one moment together are kept up to 8. | Give fewer, more useful notes; at most 5 from one run. |

A plugin's own Python packages are not installed by Clips Kitty yet (planned). Until then, use the standard library, the SDK, FFmpeg through `job.tools`, Ollama, or ship your plugin as an executable.

## Suggest edits

What an edit run's log says, and what Clips Kitty does with each line. `python -m clipskitty_sdk run --steps edit` prints the same lines (from `host.read_edits`, the function the app uses). `m1` is the clip's moment id; times are seconds of the video. A suggestion is never put into a clip by the run: it waits for the creator in the editor ([Steps](steps.md#suggest-edits-the-edit-step)).

| What you see | Why | Fix |
|---|---|---|
| "changed: m1's fade_out 0.7 s to 0.5 s, the nearest the editor offers" (or `fade_in`) | The editor offers fades of 0, 0.3, 0.5 and 1 s, so Clips Kitty keeps the nearest. | Suggest one of those. `suggest_edit` logs "m1: fade_out 0.7 s will be 0.5 s, the nearest the editor offers" as you call it. |
| "changed: m1's speed 1.4 to 1.5, the nearest the editor offers" | The editor offers 0.75, 1, 1.25, 1.5 and 2. Halfway between two, Clips Kitty keeps the one nearer 1. | Suggest one of those. |
| "changed: m1's title_overlay seconds 6 s to 5 s, the nearest the editor offers" | The editor shows a hook title for 2, 3, 5 or 8 seconds. | Suggest one of those; 3 is the default. |
| "ignored: m1's cut 950.0-955.0 s: it is outside the clip (900.0-925.0 s)" (or a mute) | A cut or mute wholly outside the clip is dropped; one partly outside is cut to the clip. Times are seconds of the video, not of the clip. | Use the clip's `m.start` and `m.end`. `suggest_edit` logs "m1: cut 950.0-955.0 s is outside the clip (900.0-925.0 s), so it isn't kept" as you call it. |
| "ignored: m1's cuts: they would leave 4.0 s, under this job's 10 s shortest clip" | All of a clip's cuts are ignored together when what is left would be shorter than the job's shortest clip (at least 1 s). A piece left under 0.25 s doesn't count: the render drops it. | Cut less. `job.limits.min_duration` is the shortest clip, and `s.length` reads back what your cuts leave; `job.finish()` logs "m1: cuts leave 4.0 s, under this job's 10 s shortest clip: Clips Kitty will ignore the cuts". |
| "ignored: m1's speed: at 2x the clip would be 6.0 s, under this job's 10 s shortest clip", "… over this job's 60 s longest clip" | Speed changes the clip's length, after any cuts. | Suggest a speed that keeps the clip between `job.limits.min_duration` and `job.limits.max_duration`. |
| "ignored: m1's title_overlay: no text is left once web addresses and line breaks are taken out" | A hook title is one line without web addresses (those starting http://, https:// or www.). | Write the hook title as words. |
| "ignored: m1's crop \"track\": this job's clips don't use a layout" | `job.limits.crops` is empty: the job's clips are Podcast, Sports, Gaming / Reaction or Vertical Live, or not vertical, and none of them takes a layout from the editor ([Steps](steps.md#layouts)). | Suggest a crop only when it is in `job.limits.crops`. |
| "ignored: m1's crop \"bias_left\": this job's clips don't use \"bias_left\"" | A crop other than `track`, `center` or `letterbox` is ignored, not refused. | Use one of `job.limits.crops`. `suggest_edit` logs "m1: crop 'bias_left' will be ignored: this job's clips use track, center or letterbox" as you call it. |
| "ignored: m1's keep: write the spans to take out as cuts", "ignored: m1's hook: write the hook title as title_overlay" | `keep` and `hook` are the editor's own words, which Clips Kitty doesn't take from a plugin. | Write `cuts` (or call `trim` or `keep_only`) and `title_overlay`. |
| "ignored: muted_words in m1's edit: Clips Kitty doesn't take them from a plugin" | An edit holds only `cuts`, `mutes`, `volume`, `fade_in`, `fade_out`, `speed`, `title_overlay`, `crop` and `reason`. | Mute the word's time with `mute(start, end)`: the editor hides captions under a mute. |
| "ignored: m1's edit changes nothing" | What was left changes nothing: volume 1, fades 0, speed 1, or only lines ignored above. | Leave the clip without a suggestion: `job.suggest_edit(m)` that is never given anything writes nothing. |
| "ignored: edits, because this run wasn't asked to suggest edits", "suggest_edit ignored: this job didn't ask for edits" | The run rates or understands, or finds. A plugin is asked to suggest edits only in an edit run of its own. | Check `job.wants("edit")` before suggesting. |
| "suggest_edit ignored on r1: Clips Kitty asks for edits on the clips it hands over (job.moments)" | `suggest_edit` was given a range of your own. | Suggest edits only for `job.moments`. |
| "ignored: 2 range(s): this run was asked about moments, not to find new ones", "ignored: scores, because this run wasn't asked to rate", "ignored: notes, because this run wasn't asked to understand" | An edit run only suggests edits. | Find, rate and understand in their own runs, behind `job.wants`. |
| "suggest_edit: volume must be a number from 0 to 2 (got 3)" (a `ContractError`) | A value outside what the render takes stops the plugin at the call: volume 0 to 2, fades 0 to 3 s, speed 0.5 to 3, a hook title 1 to 10 s, at most 20 cuts and 20 mutes for one clip. | Keep the value in range. |
| "output 'edits' is planned, not supported by plugin API 1", or "unknown output 'edits'; expected one of: …" | The SDK, or the Clips Kitty, that checked the manifest is older than SDK 1.3.0 and doesn't know the edit step. | Install the SDK again (`python -m clipskitty_sdk --version` says 1.3.0), and run Clips Kitty from source ([Versioning](versioning.md#the-edit-step-and-older-clips-kitty)). A plugin that vendors its own copy of the SDK needs 1.3.0. |
| "AttributeError: 'Job' object has no attribute 'suggest_edit'" in the job's log, and "No edit suggestions from …" on the video page | The plugin ships its own copy of `clipskitty_sdk`, older than 1.3.0, beside its script. The script's folder comes first on the path, so that copy is used instead of the SDK Clips Kitty hands over. | Don't ship the SDK in your plugin: Clips Kitty puts its own on every plugin's path ([Versioning](versioning.md)). |
| "No edit suggestions from …" on the video page | The plugin couldn't run, or gave an answer Clips Kitty couldn't use. The sentence after the name says why ("It can no longer suggest edits." after an update that took `edits` out of its manifest); the job's log has the technical message. The clips were made all the same. | Fix what it says. The creator gets suggestions again by processing the video again with `force`. |
| "… looked at the clips and suggested nothing." on the video page | The run worked and suggested no edit for any clip. | Normal when nothing in the clips calls for one; `job.finish(notes=...)` lets you say why in the log. |
| "(used, but the clip was made again without it)" beside a suggestion in the clip editor | The clip's file was made again without the creator's saved edit: the edit was saved while a run of the same video was making that clip (for example, the command line processing the video while the app re-rendered the clip). | Nothing to fix in your plugin. The creator presses **Make it again with my edits** on the suggestion's card in the timeline editor, which makes the clip again with its saved edits. |

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
