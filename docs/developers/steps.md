# Steps: find, understand, rate

Clips Kitty turns a video into clips in a fixed order. Plugins can take over or add to the steps that work on the video's moments: **find** picks the moments, **understand** says what happens in each one, and **rate** gives each one a new score. Clips Kitty does every step no plugin does, so a job that names no plugin runs exactly as it always has.

Status: **built** in plugin contract 1 for find, understand and rate (SDK 1.1.0, `tests/test_plugin_steps.py`). Two more steps, edit and export, are **planned**. They are not part of plugin contract 1, and `job.wants()` answers `False` for them.

## The run order

```text
download → transcribe → [find] → [understand] → [rate] → titles, cut, frame, caption, render
                           │           │            │
                 Clips Kitty, Sports,  up to 3      up to 3
                 Gaming scoring, or    plugins,     plugins,
                 one pipeline          in order     in order
```

| Step | What it does | Who does it | Where the creator chooses it |
|---|---|---|---|
| **Find** | Picks the video's moments, each with a score | Clips Kitty's own scoring, Sports, Gaming scoring, or one pipeline plugin | **Pipeline** when adding a video (`pipeline` in the API) |
| **Understand** | Says what happens in each moment. The notes go to the AI that writes each clip's title, description and hashtags. | Up to 3 plugins, one after the other. Without one, the titles come from what is said, as always. | **Rate & understand** when adding a video (`understand` in the API) |
| **Rate** | Gives each moment a new score. Scores decide which clips are made and their order. | Up to 3 plugins, one after the other. Without one, each moment keeps the score it was found with. | **Rate & understand** when adding a video (`rate` in the API) |

Every understander runs before any rater, so a rater sees every note. A plugin chosen for both steps, with the same version and settings, runs once, at its place among the raters, and is asked to do both. Each time a plugin understands or rates, it is one **moment run**: Clips Kitty starts it once, with all the moments in one job folder, and reads its answer when it exits.

Understand and rate work on the moments whoever found them: Clips Kitty, Sports, Gaming scoring or a pipeline. They can't be combined with Longform, which picks and writes its clips its own way. A job can't name its own pipeline again under Rate & understand: a pipeline that also rates or describes its moments does that in its find run (below).

## Which plugin does which step

There is no field for it. A plugin's role follows from words its manifest already has, `inputs` and `outputs` ([Plugin manifest](plugin-manifest.md)):

| Word | In | Means |
|---|---|---|
| `moments` | `inputs` | It is handed the moments found before it runs. It needs no permission. |
| `ranges` | `outputs` | It finds moments: it can be a job's Pipeline. |
| `context` | `outputs` | It says what happens in moments. |
| `ratings` | `outputs` | It scores moments. |

| Role | `inputs` | `outputs` | Chosen as |
|---|---|---|---|
| Finder | `[video, transcript]` | `[ranges]` | Pipeline |
| Finder that also understands its own ranges | `[video, transcript]` | `[ranges, context]` | Pipeline |
| Understander | `[moments, transcript]` | `[context]` | Rate & understand |
| Rater | `[moments, transcript]` | `[ratings]` | Rate & understand |
| One plugin, all three | `[video, transcript, moments]` | `[ranges, context, ratings]` | Pipeline, or Rate & understand |

`kind` stays `pipeline` and `capability` stays `highlight_detection`, so raters and understanders are listed under Pipelines in the Marketplace. Pills on each listing say what it does: **Finds moments**, **Understands moments**, **Understands what it finds** (a finder with `context` and no `moments` input) and **Rates moments**. A plugin that only rates or understands is never offered as a Pipeline, and a job that names it as one is refused with "pipeline: the pipeline {name} doesn't find moments: it rates or understands moments others found. Choose it under Rate & understand instead". A plugin named under Rate & understand that can't do that step is refused with, for example, "rate[0]: the pipeline Quarkbloom Notes can't rate moments others found: its manifest needs moments in inputs and ratings in outputs".

The validator ties the words together with three rules. It never gives two of them for one manifest, and a manifest that uses these words gets no new warning:

| Rule | Message |
|---|---|
| `ratings` in outputs without `moments` in inputs | `outputs[i]: ratings score moments found before this plugin runs: add moments to inputs (a pipeline's own ranges carry their score already)` |
| `moments` in inputs with neither `ratings` nor `context` in outputs | `inputs[i]: a plugin given moments answers about them: add ratings or context to outputs` |
| `context` in outputs with no `ranges` in outputs, no `moments` in inputs and no `ratings` in outputs | `outputs[i]: context describes moments: add ranges to outputs, or moments to inputs` |

Raters and understanders declare `requires: {clips_kitty: ">=2.0", plugin_api: 1}` like any pipeline. Rate and understand come in the same release as the Marketplace, so every Clips Kitty that can install a plugin can run them.

## `job.json` in a moment run

```json
{"plugin_api": 1, "plugin": {"id": "example-dev/quarkbloom-rater", "version": "1.0.0"},
 "steps": ["rate"],
 "moments": [
  {"id": "m1", "start": 812.0, "end": 841.5, "score": 72, "found_score": 72, "found_by": "clipskitty",
   "label": "", "signals": {"text": 61, "audio": 70, "visual": 44, "engagement": 66},
   "title": "He holds the bridge alone", "reason": "loud reaction and fast speech",
   "context": ["This happens in the final round of a Quarkbloom Arena match"]}],
 "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60, "min_score": 55},
 "settings": {}, "focus": null, "models": {}, "tools": {}, "output_dir": "…/out", "transcript": {"path": "…", "language": "en"}}
```

- **`steps`** is what this run is asked for: `["understand"]`, `["rate"]` or `["understand", "rate"]`.
- **`moments`** are in Clips Kitty's order, with ids `m1`, `m2`… At most 200 are handed over; any after the first 200 keep their scores.
- **`score`** is the moment's score now, after any plugin that rated it before yours. **`found_score`** is the score it was found with.
- **`found_by`** is `clipskitty` (Sports and Gaming scoring included) or the id of the pipeline that found it.
- **`label`** is the pipeline's or the sport's label for the moment, else `""`.
- **`signals`** are Clips Kitty's own numeric scores for the moment, each sent only when it has one: `text`, `audio`, `visual`, `engagement`, `game`, and `reaction` **only when it was measured**. Without a measurement Clips Kitty uses a neutral placeholder, which is no evidence, so it isn't sent.
- **`title`** (the line the moment was picked for), **`reason`** and **`context`** (what earlier plugins said happens in it) all come from what was said, so they are sent only with `transcript.read`.
- **`limits.max_clips`** is the creator's own clip limit, and **`limits.min_score`** the creator's minimum score. `min_score` is only in moment runs.
- `video`, `transcript`, `tools`, `models` and `settings` follow your permissions and manifest exactly as in a find run ([Pipeline development](pipeline-development.md)).

**A find run** of a plugin whose manifest uses any of `moments`, `ratings` or `context` gets the job.json a finder always got, plus `"steps": ["find"]`, or `["find", "understand"]` when its outputs have both `ranges` and `context`. A plugin that uses none of these words gets exactly the job.json it always got, with no `steps`. A job.json without `steps` is a find run.

## `result.json`

**A find run asked to understand** may give each range up to 5 notes of what happens in it:

```json
{"plugin_api": 1, "ranges": [{"start": 12, "end": 40, "score": 80, "label": "quark_burst",
  "context": ["First quark burst of the match"]}]}
```

**A moment run** answers in `moments`, at most one answer for each moment id, and leaves `ranges` empty:

```json
{"plugin_api": 1, "ranges": [],
 "moments": [{"id": "m1", "score": 85, "reason": "the caster called a big play",
              "context": ["The team that was behind is catching up here"]}],
 "notes": "optional log text"}
```

`score`, `reason` and `context` are each optional. A moment you leave out keeps what it had.

| Limit | |
|---|---|
| Answers in one result | at most 200 |
| An answer's `id` | text of 1 to 32 characters, one answer for each id |
| `score` | 0 to 100, or left out. Clips Kitty rounds it to a whole number. |
| `reason` | at most 500 characters |
| `context` | at most 5 notes from one run for one moment, each 1 to 160 characters |
| Notes kept for one moment | 8, from every plugin together (below) |

Check an answer with `check_result(data, steps=job_json.get("steps"))`, as `Job.finish()` and the app do. Without `steps` it is checked as a find run's answer, exactly as it always was, so a finder's stray `context` or `moments` key changes nothing. Any problem refuses the whole answer, and nothing of it is used.

What Clips Kitty uses is decided by the SDK's `host.read_answers`, the same function `python -m clipskitty_sdk run` uses:
- `score` and `reason` are used only when the run was asked to rate, and `context` only when it was asked to understand.
- These are left out, each with a line in the job log: `ignored: an answer for m9, which isn't one of this run's moments`; `ignored: 2 range(s): this run was asked about moments, not to find new ones`; `ignored: scores, because this run wasn't asked to rate`; `ignored: notes, because this run wasn't asked to understand`.
- Every note is put on one line (control characters removed, whitespace and newlines collapsed), links (`http://`, `https://`, `www.`) are taken out, and it is cut to 160 characters. A note left empty is dropped. A finder's own `ranges[].context` is cleaned the same way.

## Writing one with the SDK

The SDK's calls are in [SDK](sdk.md): `job.moments`, `job.text(m)`, `job.understand(m, text)`, `job.rate(m, score, reason)`, `job.steps` and `job.wants(step)`. Each moment is a `Moment`.

### A rater

`clipskitty.yaml`:

```yaml
manifest_version: 1
id: example-dev/quarkbloom-rater
name: Quarkbloom Rater
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: Rates moments of Quarkbloom Arena (a made-up game) by what the caster calls out.
license: MIT
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run: {command: ["{python}", "src/main.py"], timeout_minutes: 5}
execution: local
inputs: [moments, transcript]
outputs: [ratings]
permissions: [transcript.read]
```

`src/main.py`:

```python
# Rates moments of Quarkbloom Arena (a made-up game) by what the caster calls out.
from clipskitty_sdk import run

CALLS = {"quark burst": 15, "triple bloom": 25, "arena wipe": 40}


def main(job):
    for m in job.moments:                           # found by Clips Kitty, Sports or a pipeline
        said = job.text(m).lower()                  # what is said during the moment
        bonus = sum(points for call, points in CALLS.items() if call in said)
        if bonus:
            job.rate(m, min(100, m.score + bonus), reason="the caster called a big play")
        elif "respawn timer" in said:
            job.rate(m, 10, reason="the players are waiting to respawn")  # under the minimum: set aside


run(main)
```

A moment it doesn't rate keeps its score. Quarkbloom Arena is a made-up game; put your own niche's words in its place.

### An understander

The manifest is the rater's, with `id: example-dev/quarkbloom-notes`, `name: Quarkbloom Notes` and `outputs: [context]`.

```python
# Says what happens in moments of Quarkbloom Arena (a made-up game), for the titles.
from clipskitty_sdk import run

ROUNDS = ("round one", "round two", "final round", "sudden bloom")


def main(job):
    for m in job.moments:
        said = job.text(m).lower()
        for name in ROUNDS:
            if name in said:
                job.understand(m, f"This happens in the {name} of a Quarkbloom Arena match")
        if "comeback" in said or "back in it" in said:
            job.understand(m, "The team that was behind is catching up here")


run(main)
```

`tests/test_plugin_sdk.py` runs both of these, as written here, through `python -m clipskitty_sdk run`.

### One plugin doing find, understand and rate

The manifest has `inputs: [video, transcript, moments]`, `outputs: [ranges, context, ratings]` and `permissions: [video.read, transcript.read]`.
- Chosen as a job's **Pipeline**, it finds its own moments and grades them in one find run. Its job.json `steps` is `["find", "understand"]`.
- Chosen under **Rate & understand**, it grades the moments Clips Kitty or another pipeline found.

Both share `grade()`. `rate` gives each of the plugin's own ranges its score, in any run.

```python
def grade(job, m):
    hits = job.text(m).lower().count("quark burst")
    job.rate(m, min(100, 50 + 15 * hits), reason=f"{hits} quark burst(s) called")
    if hits:
        job.understand(m, f"The caster calls {hits} quark burst(s) here")

def main(job):
    if job.wants("find"):
        for start, end in find_bursts(job):          # your own detection
            grade(job, job.add_range(start, end, label="quark_burst"))
    for m in job.moments:                            # empty unless chosen under Rate & understand
        grade(job, m)
```

### A complete one to copy

[`examples/pipelines/keyword-rater/`](../../examples/pipelines/keyword-rater/) is a rater and understander laid out as its own repository (MIT). When the commentary during a moment says one of the creator's words, it raises the moment's score by a `bonus` setting and notes `The commentary says "{word}" here`. It imports only the standard library and the SDK, and `tests/test_example_keyword_rater.py` runs it through the app's own runner.

## How the answers combine

- **Notes stack.** Each understander's notes are added to the moment's, in run order, up to 8 for one moment. Notes past the eighth are not kept, and the job log says so: `{name}: 3 note(s) not kept: at most 8 for each moment`. A rater asked to understand too adds its notes after every understander's.
- **Ratings chain, and the last one counts.** Each rater is handed the scores as the raters before it left them, and its rating replaces the score. The first rating keeps the original as `found_score`.
- **The minimum score applies to rated moments.** Once every plugin has run, a moment a rater scored under the creator's minimum score is set aside: `2 moment(s) rated under the minimum score (55) set aside`. A must-have never is: a moment the creator asked for in `focus` ("make sure…"), or one Sports must keep.
- **Then the order and the limit.** When any rating was applied, the moments are sorted by score, must-haves first. The creator's clip limit is applied after that. With a rater and a limit, the moments are found from a shortlist of three times the limit (at most 200), so the raters choose from more than would be kept. With no rating applied, including when every rater failed, the order is left as it was.
- **Understanders never change the clips.** They don't add, drop or reorder a moment.
- **A pipeline's own scores** are still not held to the minimum score ([Pipeline development](pipeline-development.md#how-your-answer-becomes-clips)). Only scores a rater gives are.
- **What is kept with each clip** (its saved scores, `GET /videos/{id}/clips`): `found_score`, `plugin_ratings` (`{plugin, version, name, score, reason}`, in the order applied) and `plugin_notes` (`{plugin, version, name, text}`). The clip editor shows each rating, the score when found and each plugin's notes.

## Notes in the titles

Notes reach the AI that writes a new clip's title, description and hashtags (and the Highlights card lines), as background data framed in the request:

```text
CLIP 3 (notes from Marketplace plugins about this moment, background only, never invent beyond them: …):
```

- Notes are cleaned twice: when Clips Kitty keeps them (above), and again when it writes the request. Each note stays on one line, so it can't start a new `CLIP` block, and links are taken out.
- Every plugin's notes for one clip share 400 characters, in run order: understanders first, then raters that also understand. A later plugin's notes can be cut there. A note that repeats one already given is used once.
- Text in a note that looks like a placeholder, such as `{video_title}`, is never filled in.
- A clip without notes gets exactly the request it always got.
- Burned-in captions still come from what is said.

Notes don't reach a title when the AI call fails and the clip falls back to its hook, when the creator has edited the title, or on a forced re-run, because a clip made before keeps its title.

## When a plugin can't run

A plugin that can't run, or gives an answer Clips Kitty can't use, is skipped. The moments stay as they were, nothing of its answer is kept, and the job goes on. The job log says `Going on without {name}: {why}`, followed by the technical message in brackets. The video page says `Clips Kitty made these clips without {name}. {why}`, where `{why}` is one sentence:

| Cause | `{why}` |
|---|---|
| Not installed any more | It isn't installed any more. |
| Turned off | It's turned off in Marketplace › Installed. |
| Can't run on this app version | It can't run on this version of Clips Kitty. |
| On a block list | It was blocked: {reason}. |
| Can no longer do the step | It can no longer rate moments. (or understand) |
| A setting no longer fits | A setting chosen for it no longer fits: {detail}. |
| No command in its manifest | Its files are damaged. Install it again. |
| No Python | It needs Python, and none was found on this PC. |
| A model missing | A model it needs isn't on this PC. Get it in Marketplace › Installed. |
| Ran out of time | It took longer than its {n} minute limit, so Clips Kitty stopped it. |
| Stopped with an error line | It said: {your last error line}. |
| Stopped without one | It stopped before it finished. |
| Couldn't be started | Clips Kitty couldn't start it. |
| An answer it can't use | Clips Kitty couldn't use its answer. |

Report problems with `job.fail("...")` in words a creator understands: that line is what the video page shows.

**What waits for the creator.** When a plugin the job named under Rate & understand didn't run, two kinds of posting wait:
- A watched channel that posts automatically holds the video's clips for the creator instead: "Clips Kitty made these clips without {names}, so they weren't posted automatically. Check them and publish, or turn off Rate & understand in this channel's settings." A channel that asks first says "Clips Kitty made these clips without {names}. Check them before you publish."
- A job told to publish its clips when it finishes (`"then": {"action": "publish"}`) skips publishing, and its log says so: "Publish skipped: Clips Kitty made these clips without {names}, so they wait for you to publish them."

The command-line daily upload is not held: it schedules clips one by one, not by run. A job that named no plugin is never held, whatever an earlier run of the same video left behind.

When a rater scores every moment under the minimum, the video has no clips, and the reason names the rater: "Quarkbloom Rater rated every moment under your minimum score (55). Lower the minimum score, or turn off Rate & understand for this video."

Holding the posting is about Clips Kitty's own posting. A plugin is a program running as the user, like any other, so nothing stops one from posting by itself. What it declares, its labels and the block list are the protection ([Security](security.md)).

## A rater decides what is posted

Where only the best few clips go out, scores decide which ones: a watched channel's automatic posting and its **Publish** button send the best N by score, and the command-line daily upload schedules the highest-scored clips first. So a rater decides **which** clips are posted there, and the order everywhere else. That is by design, because the creator chose the rater. The job form, the watch panel, the Marketplace and these pages all say so.

## Re-runs

- A video already done is not run again without `force`.
- With `force`, the steps run again and each clip gets the new ratings and notes. A clip's score follows its ratings: a clip rated now takes the new score, and one rated before but not now (the rater was turned off, removed or failed) gets its unrated score back.
- Titles are kept, so the creator's edits win. New notes show in the clip editor but reach only new clips' titles.
- A watched video's clips are chosen once, at its first publish. Every later send (Retry failed, the re-send of rejected posts, a start tried again) sends only clips chosen then, even when a forced re-run changed the scores.

## Time limits and job folders

- A moment run is stopped after **10 minutes** unless the manifest's `run.timeout_minutes` says otherwise (up to 24 hours). That value applies to every run of the plugin, find runs included; a find run's default is 60 minutes. The Marketplace shows the limit: "Clips Kitty stops it after 10 minutes when it rates or understands a video’s moments."
- Cancelling the job stops the plugin, as in a find run.
- A moment run's folder is `<data folder>/plugins/runs/<video id>-<date>-<time>-rate` (or `-understand`, or `-understand-rate`). Only the newest **5** run folders are kept, counted across every plugin, find runs and moment runs alike, so a job's moment runs can remove the folder of its own find run. To keep a run's folder for a closer look, use `python -m clipskitty_sdk run --job-dir DIR`.

## Progress

A moment run's progress lines show as "Rating moments with {name}", or "Understanding moments with {name}" for a run asked only to understand. Those steps sit at 65 to 70 percent of the bar, which never moves back. Clips Kitty's own scoring can already have passed that point, so the bar may not move during your run; the label says your plugin is working. The progress events carry `plugin` (your plugin's name) and the stage `ranking` or `understand` ([API](../API.md#websocket-events)).

## Trying it on your own PC

```text
python -m clipskitty_sdk run <plugin> [--video v.mp4] [--transcript t.json] [--duration SECONDS]
       [--moments moments.json] [--steps find|understand,rate] [--min-score 55]
```

`run` uses the same helpers as the app, so it asks for the run the app would make:
- Without `--steps`, a plugin that finds gets a find run (with `understand` when it declares `context`). Any other plugin gets a moment run of every step it offers, understand before rate.
- `--steps understand`, `--steps rate` or `--steps understand,rate` asks for a moment run, and `--steps find` for a find run. A step the plugin doesn't offer, an unknown step, or `find` with `rate`, is refused with exit code 2.
- `--moments` takes a list of `{start, end, score?, label?, title?, reason?, context?}`, or a finder's `result.json` (its `ranges` are used), so you can chain a finder into a rater. A missing score becomes 60, and `found_by` is `clipskitty`.
- Without `--moments`, a moment run gets 5 sample moments spread through the video, each scored 60. The video's length comes from `--video` (with FFprobe), else `--duration`, else the end of `--transcript`.
- `--video` is needed only for a find run and for a plugin with `video.read`.
- `--min-score` (default 55, the app's default) is the `limits.min_score` a moment run is handed.

A moment run prints one line for each moment, its notes under it, then any `ignored:` lines:

```text
m1   812.0s-841.5s  score 72 -> 97  the caster called a big play
m2   900.0s-925.0s  score 64 -> 10  the players are waiting to respawn
m3   1000.0s-1012.0s  score 60
```

Exit code 0 means the app would use the answer, 1 that the run failed or the answer would be refused, 2 that the run couldn't start. Every option is in [SDK](sdk.md#running-your-plugin-the-way-the-app-does).
