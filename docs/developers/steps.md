# Steps: find, understand, rate, edit

Clips Kitty turns a video into clips in a fixed order. Plugins can take over or add to the steps that work on the video's moments and clips: **find** picks the moments, **understand** says what happens in each one, **rate** gives each one a new score, and **edit** suggests an edit for each clip Clips Kitty is about to make. Clips Kitty does every step no plugin does, so a job that names no plugin runs exactly as it always has.

Status: **built** in plugin contract 1 for find, understand and rate (SDK 1.1.0, `tests/test_plugin_steps.py`) and for edit (SDK 1.3.0, `tests/test_plugin_steps.py` and `tests/test_plugin_edit_*.py`). An edit plugin suggests edits that wait for the creator in the editor; it changes no clip by itself ([Suggest edits](#suggest-edits-the-edit-step)). One more step, export, is **planned**. It is not part of plugin contract 1, and `job.wants("export")` answers `False`.

In a manifest, `kind: publisher` is refused as planned.

## The run order

```text
download → transcribe → [find] → [understand] → [rate] → [edit] → titles, cut, frame, caption, render
                           │           │            │        │
                 Clips Kitty, Sports,  up to 3      up to 3  up to 3 plugins, in order,
                 Gaming scoring, or    plugins,     plugins, on the clips that will be made;
                 one pipeline          in order     in order their suggestions wait in the editor
```

| Step | What it does | Who does it | Where the creator chooses it |
|---|---|---|---|
| **Find** | Picks the video's moments, each with a score | Clips Kitty's own scoring, Sports, Gaming scoring, or one pipeline plugin | **Pipeline** when adding a video (`pipeline` in the API) |
| **Understand** | Says what happens in each moment. The notes go to the AI that writes each clip's title, description and hashtags. | Up to 3 plugins, one after the other. Without one, the titles come from what is said, as always. | **Rate & understand** when adding a video (`understand` in the API) |
| **Rate** | Gives each moment a new score. Scores decide which clips are made and their order. | Up to 3 plugins, one after the other. Without one, each moment keeps the score it was found with. | **Rate & understand** when adding a video (`rate` in the API) |
| **Edit** | Suggests an edit for each clip that will be made: cuts, mutes, volume, fades, speed, a hook title or the layout. Each suggestion waits for the creator in the editor. | Up to 3 plugins, one after the other. Without one, nothing is suggested and no clip is edited, as always. | **Suggest edits** when adding a video (`edit` in the API) |

Every understander runs before any rater, so a rater sees every note. A plugin chosen for both steps, with the same version and settings, runs once, at its place among the raters, and is asked to do both. Each time a plugin understands or rates, it is one **moment run**: Clips Kitty starts it once, with all the moments in one job folder, and reads its answer when it exits.

Understand and rate work on the moments whoever found them: Clips Kitty, Sports, Gaming scoring or a pipeline. They can't be combined with Longform, which picks and writes its clips its own way. A job can't name its own pipeline again under Rate & understand: a pipeline that also rates or describes its moments does that in its find run (below).

Edit comes last, after rating, the minimum score and the clip limit, and before the titles are written, so an edit plugin is handed exactly the clips that will be made. Each time a plugin suggests edits, it is one **edit run**, never combined with another step. It changes nothing about which clips are made, their scores, their order or their titles. Suggest edits can't be combined with Longform either. A job's own pipeline may be named under Suggest edits, because a find run is never asked to edit, and a plugin chosen under both Rate & understand and Suggest edits runs twice: it rates every moment, then suggests edits for the clips that are kept.

## Which plugin does which step

There is no field for it. A plugin's role follows from words its manifest already has, `inputs` and `outputs` ([Plugin manifest](plugin-manifest.md)):

| Word | In | Means |
|---|---|---|
| `moments` | `inputs` | It is handed the moments found before it runs. It needs no permission. |
| `ranges` | `outputs` | It finds moments: it can be a job's Pipeline. |
| `context` | `outputs` | It says what happens in moments. |
| `ratings` | `outputs` | It scores moments. |
| `edits` | `outputs` | It suggests edits for the clips Clips Kitty makes. It needs `moments` in `inputs`, because the clips are handed over the way moments are. |

| Role | `inputs` | `outputs` | Chosen as |
|---|---|---|---|
| Finder | `[video, transcript]` | `[ranges]` | Pipeline |
| Finder that also understands its own ranges | `[video, transcript]` | `[ranges, context]` | Pipeline |
| Understander | `[moments, transcript]` | `[context]` | Rate & understand |
| Rater | `[moments, transcript]` | `[ratings]` | Rate & understand |
| One plugin, all three | `[video, transcript, moments]` | `[ranges, context, ratings]` | Pipeline, or Rate & understand |
| Editor | `[moments, transcript]` | `[edits]` | Suggest edits |

`kind` stays `pipeline` and `capability` stays `highlight_detection`, so raters, understanders and editors are listed under Pipelines in the Marketplace. Pills on each listing say what it does: **Finds moments**, **Understands moments**, **Understands what it finds** (a finder with `context` and no `moments` input), **Rates moments** and **Suggests edits**. A plugin that only rates or understands is never offered as a Pipeline, and a job that names it as one is refused with "pipeline: the pipeline {name} doesn't find moments: it rates or understands moments others found. Choose it under Rate & understand instead"; a plugin that only suggests edits gets "… doesn't find moments: it suggests edits for the clips Clips Kitty makes. Choose it under Suggest edits instead". A plugin named under Rate & understand that can't do that step is refused with, for example, "rate[0]: the pipeline Quarkbloom Notes can't rate moments others found: its manifest needs moments in inputs and ratings in outputs", and one named under Suggest edits with "edit[0]: the pipeline Quarkbloom Notes can't suggest edits for clips: its manifest needs moments in inputs and edits in outputs".

The validator ties the words together with four rules. It never gives two of them for one manifest, and a manifest that uses these words gets no new warning:

| Rule | Message |
|---|---|
| `ratings` in outputs without `moments` in inputs | `outputs[i]: ratings score moments found before this plugin runs: add moments to inputs (a pipeline's own ranges carry their score already)` |
| `moments` in inputs with none of `ratings`, `context` or `edits` in outputs | `inputs[i]: a plugin given moments answers about them: add ratings, context or edits to outputs` |
| `edits` in outputs without `moments` in inputs (and without `ratings` in outputs, which the first rule covers) | `outputs[i]: edits are suggested for the clips Clips Kitty makes from moments: add moments to inputs` |
| `context` in outputs with no `ranges` in outputs, no `moments` in inputs and neither `ratings` nor `edits` in outputs | `outputs[i]: context describes moments: add ranges to outputs, or moments to inputs` |

Raters and understanders declare `requires: {clips_kitty: ">=2.0", plugin_api: 1}` like any pipeline. Rate and understand come in the same release as the Marketplace, so every Clips Kitty that can install a plugin can run them. Editors declare `">=2.0"` too, for now: a Clips Kitty without the edit step refuses to install a plugin with `outputs: [edits]`, so it never runs an editor wrongly ([Versioning](versioning.md#the-edit-step-and-older-clips-kitty)).

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

The SDK's calls are in [SDK](sdk.md): `job.moments`, `job.text(m)`, `job.understand(m, text)`, `job.rate(m, score, reason)`, `job.steps` and `job.wants(step)`. Each moment is a `Moment`. An editor uses `job.suggest_edit(m)` instead ([below](#writing-an-editor-with-the-sdk)).

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
| Can no longer do the step | It can no longer rate moments. (or understand moments, or suggest edits) |
| A setting no longer fits | A setting chosen for it no longer fits: {detail}. |
| No command in its manifest | Its files are damaged. Install it again. |
| No Python | It needs Python, and none was found on this PC. |
| A model missing | A model it needs isn't on this PC. Get it in Marketplace › Installed. |
| Ran out of time | It took longer than its {n} minute limit, so Clips Kitty stopped it. |
| Stopped with an error line | It said: {your last error line that isn't blank}. |
| Stopped without one, or with only blank ones | It stopped before it finished. |
| Couldn't be started | Clips Kitty couldn't start it. |
| An answer it can't use, or a `result.json` that can't be read (a folder, nested too deep, a number too large) | Clips Kitty couldn't use its answer. |

Report problems with `job.fail("...")` in words a creator understands: that line is what the video page shows.

For an edit plugin the video page says `No edit suggestions from {name}. {why}` instead: the clips are made exactly as they would be without it.

**What waits for the creator.** When a plugin the job named under Rate & understand didn't run, two kinds of posting wait:
- A watched channel that posts automatically holds the video's clips for the creator instead: "Clips Kitty made these clips without {names}, so they weren't posted automatically. Check them and publish, or turn off Rate & understand in this channel's settings." A channel that asks first says "Clips Kitty made these clips without {names}. Check them before you publish."
- A job told to publish its clips when it finishes (`"then": {"action": "publish"}`) skips publishing, and its log says so: "Publish skipped: Clips Kitty made these clips without {names}, so they wait for you to publish them."

The command-line daily upload is not held: it schedules clips one by one, not by run. A job that named no plugin is never held, whatever an earlier run of the same video left behind. A plugin named under Suggest edits that didn't run holds nothing either: its suggestions change no clip, so there is nothing to wait for.

When a rater scores every moment under the minimum, the video has no clips, and the reason names the rater: "Quarkbloom Rater rated every moment under your minimum score (55). Lower the minimum score, or turn off Rate & understand for this video."

Holding the posting is about Clips Kitty's own posting. A plugin is a program running as the user, like any other, so nothing stops one from posting by itself. What it declares, its labels and the block list are the protection ([Security](security.md)).

## A rater decides what is posted

Where only the best few clips go out, scores decide which ones: a watched channel's automatic posting and its **Publish** button send the best N by score, and the command-line daily upload schedules the highest-scored clips first. So a rater decides **which** clips are posted there, and the order everywhere else. That is by design, because the creator chose the rater. The job form, the watch panel, the Marketplace and these pages all say so.

## Re-runs

- A video already done is not run again without `force`.
- With `force`, the steps run again and each clip gets the new ratings and notes. A clip's score follows its ratings: a clip rated now takes the new score, and one rated before but not now (the rater was turned off, removed or failed) gets its unrated score back.
- Titles are kept, so the creator's edits win. New notes show in the clip editor but reach only new clips' titles.
- A watched video's clips are chosen once, at its first publish. Every later send (Retry failed, the re-send of rejected posts, a start tried again) sends only clips chosen then, even when a forced re-run changed the scores.
- What a re-run does with suggested edits is [below](#re-runs-and-the-creators-own-edits).

## Suggest edits: the edit step

An edit plugin never changes a clip. It **suggests** an edit for each clip, and the suggestion waits on that clip for the creator. In the timeline editor the creator can **Use** it, **Hide** it, or later **Take it back**. The Suggest edits switch, the Marketplace and these pages say it in the same sentence:

> Clips Kitty doesn’t put a suggestion into a clip until you use it in the editor and apply your edits (Apply edits, or Apply edits & upload).

So a video's first run renders every clip with exactly the options it would have without the plugin (a test pins that), and Clips Kitty's own automatic posting posts what it would post without it. That describes what Clips Kitty does with your answer; it is not a sandbox. A plugin runs as the user, like any program, so nothing stops one from changing clip files or posting by itself ([Security](security.md)).

### `job.json` in an edit run

```json
{"plugin_api": 1, "plugin": {"id": "example-dev/quarkbloom-trimmer", "version": "1.0.0"},
 "steps": ["edit"],
 "moments": [
  {"id": "m1", "start": 812.0, "end": 841.5, "score": 85, "found_score": 72, "found_by": "clipskitty",
   "label": "", "signals": {"text": 61, "audio": 70, "visual": 44, "engagement": 66},
   "title": "He holds the bridge alone", "reason": "loud reaction and fast speech",
   "context": ["This happens in the final round of a Quarkbloom Arena match"],
   "suggested": [{"by": "example-dev/quarkbloom-framer", "name": "Quarkbloom Framer",
                  "edit": {"crop": "center", "fade_in": 0.3}, "reason": "Keeps both players in frame"}]}],
 "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60,
            "crops": ["track", "center", "letterbox"]},
 "settings": {"mute_words": ""}, "focus": null, "models": {}, "tools": {}, "output_dir": "…/out",
 "transcript": {"path": "…", "language": "en"}}
```

- **`steps`** is always exactly `["edit"]`.
- **`moments`** are the clips that will be made, in Clips Kitty's order, handed over as a moment run hands over moments (ids `m1`, `m2`…, at most 200). `start` and `end` are each clip's window, in seconds of the video. Nothing between this run and the render moves a window.
- **`suggested`** is what the edit plugins before yours in the same job suggested for that clip, as Clips Kitty keeps it ([below](#how-clips-kitty-fits-a-suggestion)): `by` (that plugin's id), `name`, `edit` and `reason`. It is read-only, and absent when there is nothing. Another plugin's `reason` and hook title may come from what was said, so without `transcript.read` they are left out, as `title`, `reason` and `context` are.
- **`limits.crops`** lists the layouts this job's clips can use ([Layouts](#layouts)). It is only in edit runs.
- **`limits.min_score`** is not sent: the clips are already chosen.

### `result.json` in an edit run

```json
{"plugin_api": 1, "ranges": [],
 "moments": [{"id": "m1", "edit": {
     "cuts": [[815.0, 821.5]],
     "mutes": [[830.2, 830.9]],
     "fade_out": 0.5,
     "title_overlay": {"text": "Triple bloom!", "seconds": 3},
     "crop": "center",
     "reason": "Cuts the wait for the respawn timer"}}],
 "notes": "optional log text"}
```

At most one answer for each clip's id. A clip you leave out gets no suggestion from your plugin, and every field inside `edit` is optional. All times are seconds of the video, like every other time in plugin contract 1.

### What a plugin may suggest

Each field is one the timeline editor already has. A value outside its limit refuses the whole answer, as in a moment run, and nothing of it is used. A value inside it is never refused: Clips Kitty fits it to the clip and to the editor's own controls ([below](#how-clips-kitty-fits-a-suggestion)).

| Field | What it is | Limit |
|---|---|---|
| `cuts` | Spans to take out, `[start, end]` | at most 20 pairs of numbers, `0 <= start < end` |
| `mutes` | Spans to silence; the picture stays | at most 20 pairs, the same rule |
| `volume` | The whole clip's loudness: 1 as it is, 0 silent | 0 to 2 |
| `fade_in`, `fade_out` | Seconds of fade at the start and the end | 0 to 3 |
| `speed` | The whole clip's speed | 0.5 to 3 |
| `title_overlay` | The editor's **Hook title**: big text at the top for the clip's first seconds | `{"text", "seconds"}`: text of 1 to 120 characters, seconds from 1 to 10 (3 when left out) |
| `crop` | The layout: `track` (the editor's **Auto (AI)**), `center` or `letterbox` | text of at most 32 characters |
| `reason` | Why, in one line the creator sees | at most 160 characters |

Clips Kitty never takes anything else from a plugin: no music, watermark, caption change, look, or new start or end for the clip. Any other field is ignored with a line in the job log, never refused, so a later contract can add fields. `keep` and `hook` get a line saying to write `cuts` and `title_overlay` instead, and `muted_words` is left out because Clips Kitty works out from the transcript which words a mute hides.

### How Clips Kitty fits a suggestion

The SDK's `host.read_edits` reads the answer, the same function `python -m clipskitty_sdk run` uses:
- **Cuts and mutes** are clamped to the clip and joined where they overlap. A span wholly outside the clip is left out.
- **Cuts that leave too little** are left out: less than the job's shortest clip (`limits.min_duration`, and at least 1 second), counting only kept pieces of at least 0.25 seconds, as the render does. The mutes and the other fields stay.
- **A speed** that would take the clip under that length, or over the job's longest clip, is left out.
- **Fades, speed and the hook title's seconds** are set to the nearest of the editor's own choices: fades 0, 0.3, 0.5 or 1 s; speed 0.75, 1, 1.25, 1.5 or 2; hook title 2, 3, 5 or 8 s. A tie goes toward no change: the smaller fade or seconds, and the speed nearer 1. So each control in the editor shows what the clip will get. The lists are `contract.FADE_CHOICES`, `SPEED_CHOICES` and `HOOK_SECONDS_CHOICES`, and a test checks they are the editor's.
- **The volume** is rounded to a whole percent, as the editor's slider moves.
- **The hook title** is put on one line, web addresses that start with `http://`, `https://` or `www.` are taken out, and it is cut to 120 characters. A bare domain or an @handle stays, so read what your plugin writes there: a hook title the creator uses is burned into the clip.
- **A layout** this job's clips don't use is left out.
- **The reason** is cleaned like a note.
- Values that change nothing (volume 1, fades 0, speed 1) are left out, and a suggestion left with nothing is not kept.

Each thing changed or left out is one line in the job log, prefixed with your plugin's id, and `run` prints the same lines:
- `changed: m1's fade_out 0.7 s to 0.5 s, the nearest the editor offers` (and the same for `fade_in`, `speed` and `title_overlay` seconds)
- `ignored: m4's cut 950.0-955.0 s: it is outside the clip (900.0-925.0 s)` (or `mute`)
- `ignored: m3's cuts: they would leave 4.0 s, under this job's 10 s shortest clip`
- `ignored: m2's speed: at 2x the clip would be 8.0 s, under this job's 10 s shortest clip` (or over its longest clip)
- `ignored: m5's title_overlay: no text is left once web addresses and line breaks are taken out`
- `ignored: m1's crop "center": this job's clips don't use a layout` (or `… don't use "bias_left"`)
- `ignored: music, watermark in m2's edit: Clips Kitty doesn't take them from a plugin`
- `ignored: m2's keep: write the spans to take out as cuts` (and `hook`: write the hook title as `title_overlay`)
- `ignored: m6's edit changes nothing`
- the lines a moment run gives too: an answer for an id that isn't one of the run's clips, ranges, scores and notes.

A run that rates or understands and answers with `edit` gets `ignored: edits, because this run wasn't asked to suggest edits`; the rest of its answer is used as before. [Troubleshooting](troubleshooting.md#suggest-edits) says what to do about each line.

**What Clips Kitty keeps.** Each suggestion becomes one entry in the clip's saved scores, `plugin_edits`, beside `plugin_ratings` and `plugin_notes`: `{id, plugin, version, name, window, min_length, edit, reason, state}`. `window` is the clip's window the suggestion was made for, rounded to 2 decimals as the clip's own start and end are, and `min_length` the shortest length the cuts were held to. `id` is worked out from your plugin's id and the fitted edit, so the same suggestion gets the same id in every version of your plugin and every run of the video. `state` is `new`, `used` or `hidden` ([API](../API.md#get-videosvideo_idclips)).

### Writing an editor with the SDK

| Call | What it does |
|---|---|
| `job.wants("edit")` | `True` in an edit run |
| `job.moments` | The clips Clips Kitty will make, as `Moment`s |
| `job.limits.crops` | The layouts the clips can use, a tuple; `()` outside an edit run |
| `m.suggested` | What the edit plugins before yours suggested for the clip: a tuple of `Suggested(by, name, edit, reason)`, read-only |
| `s = job.suggest_edit(m)` | This clip's suggestion: the same one on every call for `m`, so calls add up |
| `s.cut(start, end)` | Take out `[start, end]`. Cuts add up, and overlapping ones join. |
| `s.trim(start=None, end=None)` | Keep the clip from `start` to `end`: a cut at each end |
| `s.keep_only(*spans)` | Keep only these `(start, end)` spans: the rest is cut |
| `s.mute(start, end)` | Silence `[start, end]`. Mutes add up. |
| `s.volume(level)` | 0 to 2 |
| `s.fade(fade_in=None, fade_out=None)` | 0 to 3 seconds each |
| `s.speed(factor)` | 0.5 to 3 |
| `s.title_overlay(text, seconds=3)` | The editor's Hook title |
| `s.crop(mode)` | A layout from `job.limits.crops` |
| `s.reason(text)` | One line the creator sees |
| `s.cuts`, `s.mutes` | What is set so far, as tuples |
| `s.length` | The seconds left after the cuts and the speed, as Clips Kitty measures them |

Each method returns the suggestion, so calls chain: `job.suggest_edit(m).cut(815.0, 821.5).fade(fade_out=0.5).reason("Cuts the wait")`. Times are seconds of the video, like `m.start`, `m.end` and what `text.said()` gives.
- **Checked at the call.** A value the contract refuses raises `ContractError`, naming what was given: `suggest_edit: speed must be a number from 0.5 to 3 (got 4)`, `suggest_edit: cut needs start < end, in seconds of the video (got 830.0 to 820.0)`, `suggest_edit: at most 20 cuts for one clip`.
- **Logged once, not raised,** for what depends on the job or the editor: `m4: cut 950.0-955.0 s is outside the clip (900.0-925.0 s), so it isn't kept`; `m1: crop 'bias_left' will be ignored: this job's clips use track, center or letterbox` (the crop is kept, and Clips Kitty leaves it out); `m1: fade_out 0.7 s will be 0.5 s, the nearest the editor offers`.
- **In a run not asked to edit,** the call logs `suggest_edit ignored: this job didn't ask for edits` and nothing is kept; on one of your own ranges it logs `suggest_edit ignored on r1: Clips Kitty asks for edits on the clips it hands over (job.moments)`. So one shared function can serve every run.
- **At `job.finish()`**, a clip whose cuts or speed would leave less than the job's shortest clip gets a line such as `m1: cuts leave 4.0 s, under this job's 10 s shortest clip: Clips Kitty will ignore the cuts`. The suggestion is written as you set it; Clips Kitty fits it when it reads the answer, so a `result.json` you write by hand is fitted the same way.

`job.suggest_edit` is new in SDK 1.3.0. Don't ship your own copy of the SDK in your plugin's folder: an older copy there comes first on the path, lacks `suggest_edit`, and the run fails.

### An editor

`clipskitty.yaml`:

```yaml
manifest_version: 1
id: example-dev/quarkbloom-trimmer
name: Quarkbloom Trimmer
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: Suggests cuts, mutes and a hook title for clips of Quarkbloom Arena (a made-up game).
license: MIT
requires: {clips_kitty: ">=2.0", plugin_api: 1}
run: {command: ["{python}", "src/main.py"], timeout_minutes: 5}
execution: local
inputs: [moments, transcript]
outputs: [edits]
permissions: [transcript.read]
settings:
  mute_words: {type: string, title: Words to mute, default: ""}
```

`src/main.py`:

```python
# Suggests edits for clips of Quarkbloom Arena (a made-up game): mutes the words
# the creator lists, cuts the wait after "respawn timer", and adds a hook title
# where a triple bloom is called.
from clipskitty_sdk import run
from clipskitty_sdk.text import said, words_of


def main(job):
    muted = said(job, words_of(job.settings["mute_words"]))  # (start, end, word), in seconds of the video
    waits = said(job, ["respawn timer"])
    blooms = said(job, ["triple bloom"])
    for m in job.moments:                                   # the clips Clips Kitty will make
        s = job.suggest_edit(m)
        for start, end, _ in muted:
            if m.start <= start < m.end:
                s.mute(max(0.0, start - 0.1), end + 0.1)    # the whole word, so its caption is hidden too
        for start, end, _ in waits:
            if m.start < start and end + 4 < m.end:
                s.cut(start, end + 4)
        if any(m.start <= start < m.end for start, _, _ in blooms):
            s.title_overlay("Triple bloom!", seconds=3)
        why = (["mutes the words you listed"] if s.mutes else []) + (["cuts the respawn wait"] if s.cuts else [])
        if why:
            s.reason(" and ".join(why).capitalize())


run(main)
```

It mutes the words the creator lists, a teammate's name say, because that is what a creator would use it for. A mute a little wider than the word hides the word in the burned captions too, once the creator uses it. Given the rater's three moments as the clips, a transcript with word times that says "respawn timer" at 815.6 s, "Sam, a triple bloom" at 830.5 s and "triple bloom" again at 1003 s, and `--set mute_words=Sam`, `run --steps edit` prints:

```text
Suggest edits: 2 of 3 clip(s) given a suggestion, as Clips Kitty would keep them:
m1   812.0s-841.5s  29.5 s -> 24.5 s  cut 815.6-820.6 · mute 830.4-830.9 · hook title "Triple bloom!" (3 s)
     Mutes the words you listed and cuts the respawn wait
m2   900.0s-925.0s  no suggestion
m3   1000.0s-1012.0s  12.0 s  hook title "Triple bloom!" (3 s)
```

`tests/test_plugin_docs.py` runs it, as written here, through `python -m clipskitty_sdk run --steps edit`. `python -m clipskitty_sdk new <folder> --template editor` starts one like it, with its own tests ([SDK](sdk.md#starting-a-plugin-new)).

### Several edit plugins

- A job may name up to 3, each once, and they run one after another in the creator's order.
- Each sees the same clips, plus `suggested`: every earlier plugin's suggestion for each clip. Yours can build on them, for example by leaving alone a clip another plugin already trims, or ignore them.
- One suggestion is kept for each plugin and clip, each apart, shown as its own card in run order. Clips Kitty never merges them, so every card says exactly what one plugin said, and the creator settles any conflict.

### What the creator sees

- **Clip Studio** marks a clip with a suggestion not looked at yet: **Suggested edit**. The video page says "{name} suggested edits for {n} of {given} clips. Open a clip in the editor to see them.", or "{name} looked at the clips and suggested nothing."
- **The timeline editor** shows a card for each suggestion above the timeline: "Suggested by {name} {version}", what it does in the editor's own words ("Cuts 2 parts · 6.5 s shorter", "Mutes 1 part · hides 1 word in the captions", "Fades out 0.5 s", "Hook title: “Triple bloom!” for 3 s", "Layout: Center"…), your reason in quotes, and any value it would replace ("Replaces your fade out (0.3 s → 0.5 s)").
- **Use** lays the suggestion over the editor's current edit, as one Undo step. Cuts are added to the creator's own, so a suggestion never brings back what the creator cut. Mutes are added beside the creator's, and every word inside a suggested mute is hidden in the burned captions, as a hand mute does. Volume, fades, speed, the hook title and the layout replace the current value. Nothing renders until the creator applies their edits; Use is off when the creator's cuts and the suggestion's would leave almost nothing, and the card warns when they would leave less than the job's shortest clip.
- **Hide** puts the card away ("1 hidden suggestion · Show"). **Take it back** takes out only what the suggestion put in and is still as it left it; the creator's own changes stay.
- The clip editor panel has one line per suggestion: "Edit suggested by {name} {version}: {what} · {reason}", ending "(used)", "(hidden)" or "(used, but the clip was made again without it)".

Your reason and hook title are shown as you wrote them, never translated, and labelled with your plugin's name. A hook title uses the font picked for the video's language, so a title in another script may miss characters; the creator sees that in the preview before applying.

### Posting

Clips Kitty's three ways of posting without a click (a watched channel that posts automatically, a job told to publish when it finishes, and the command-line daily upload) post the clip's file as the first run made it, with exactly the options it would have without your plugin. Edit plugins don't touch scores either, so which clips go out is unchanged too. Once the creator uses a suggestion and applies their edits, it is their own edit and goes out as any of their edits does: with Apply edits & upload, Publish, or a later re-send of that clip. Posts that already went out stay as they were. A plugin that fails holds no posting: its suggestions change no clip.

Each edit plugin makes the clips wait for its answer, up to its time limit, before they are made. The watch panel tells the creator so, and the Marketplace shows each plugin's limit.

### Re-runs and the creator's own edits

- **No run changes a clip's saved edit, layout or any other choice the creator made.**
- **Decisions carry over.** On a forced re-run, a suggestion with the same id keeps the creator's decision: a hidden one stays hidden and a used one stays used. A suggestion that changed, even slightly, has a new id and is new again. A used suggestion the run no longer makes is kept, so the creator can still take it back; new and hidden ones it no longer makes are dropped, as old ratings are.
- **A forced re-run makes the clip's file without its saved edit**, as it always has. Its used suggestions then carry `remade`, and each card says "This clip was made again without your saved edits, so its file doesn’t have them." with **Make it again with my edits**, which renders the clip again with its saved edit.
- **A clip the creator trimmed since.** Times stay in seconds of the video and are turned into the clip's own seconds when the creator presses Use, so a suggestion still lands in the right place. When the clip's start or end moved since the suggestion was made, the card says "Made before you changed this clip’s start or end. Check the cuts before you apply."
- **Titles are never touched.** Suggestions don't reach the title writer; only notes do.

### Layouts

A layout (`crop`) is offered only for clips that use one the same way the editor's Layout buttons do:

| Clip | `limits.crops` |
|---|---|
| Standard vertical | `track`, `center`, `letterbox` |
| Sports | none: Sports gives each layout its own meaning, and its framing stays as it is |
| Podcast | none: Podcast frames with its own analysis |
| Gaming / Reaction | none: the clip keeps its split |
| Vertical Live, including a Sports match filmed 9:16 | none: the clip keeps the stream's own 9:16 picture |
| A job with vertical clips turned off | none: the whole frame is used |

Cuts, mutes, volume, fades, speed and the hook title work on every kind. A hook title on a Highlights clip moves its title card to the lower third, and the card says so before Use. With `run`, `--layout` picks the kind of clip: `standard` (the default) gives the three layouts, and `podcast`, `sports`, `gaming`, `vertical-live` and `whole-frame` give none.

## Time limits and job folders

- A moment run or an edit run is stopped after **10 minutes** unless the manifest's `run.timeout_minutes` says otherwise (up to 24 hours). That value applies to every run of the plugin, find runs included; a find run's default is 60 minutes. The Marketplace shows the limit: "Clips Kitty stops it after 10 minutes when it rates or understands a video’s moments.", "… when it suggests edits for a video’s clips." or "… when it rates or understands a video’s moments or suggests edits for its clips."
- Cancelling the job stops the plugin, as in a find run.
- A moment run's folder is `<data folder>/plugins/runs/<video id>-<date>-<time>-rate` (or `-understand`, or `-understand-rate`), and an edit run's ends in `-edit`. Only the newest **5** run folders are kept, counted across every plugin, find runs, moment runs and edit runs alike, so a job's later runs can remove the folder of its own find run. To keep a run's folder for a closer look, use `python -m clipskitty_sdk run --job-dir DIR`.

## Progress

A moment run's progress lines show as "Rating moments with {name}", or "Understanding moments with {name}" for a run asked only to understand. Those steps sit at 65 to 70 percent of the bar, which never moves back. Clips Kitty's own scoring can already have passed that point, so the bar may not move during your run; the label says your plugin is working. The progress events carry `plugin` (your plugin's name) and the stage `ranking` or `understand` ([API](../API.md#websocket-events)). An edit run shows as "Suggesting edits with {name}", at 70 to 78 percent of the bar, with the stage `edit`.

## Trying it on your own PC

```text
python -m clipskitty_sdk run <plugin> [--video v.mp4] [--transcript t.json] [--duration SECONDS]
       [--moments moments.json] [--steps find|understand,rate|edit] [--min-score 55]
       [--layout standard|podcast|sports|gaming|vertical-live|whole-frame]
```

`run` uses the same helpers as the app, so it asks for the run the app would make:
- Without `--steps`, a plugin that finds gets a find run (with `understand` when it declares `context`). Any other plugin gets a moment run of every step it offers, understand before rate, and a plugin that only suggests edits gets an edit run. A plugin that also suggests edits is told it can run again with `--steps edit`.
- `--steps understand`, `--steps rate` or `--steps understand,rate` asks for a moment run, `--steps find` for a find run and `--steps edit` for an edit run. A step the plugin doesn't offer, an unknown step, `find` with `rate`, or `edit` with any other step, is refused with exit code 2.
- `--moments` takes a list of `{start, end, score?, label?, title?, reason?, context?}`, or a finder's `result.json` (its `ranges` are used), so you can chain a finder into a rater. A missing score becomes 60, and `found_by` is `clipskitty`. In an edit run they are the clips, and each may carry `suggested` (`[{by, name, edit, reason?}]`), to try your plugin after another edit plugin.
- Without `--moments`, a moment run or an edit run gets 5 sample moments spread through the video, each scored 60. The video's length comes from `--video` (with FFprobe), else `--duration`, else the end of `--transcript`.
- `--video` is needed only for a find run and for a plugin with `video.read`.
- `--min-score` (default 55, the app's default) is the `limits.min_score` a moment run is handed.
- `--layout` is the kind of clip an edit run is for, and fills `limits.crops` as the app would ([Layouts](#layouts)).

A moment run prints one line for each moment, its notes under it, then any `ignored:` lines:

```text
m1   812.0s-841.5s  score 72 -> 97  the caster called a big play
m2   900.0s-925.0s  score 64 -> 10  the players are waiting to respawn
m3   1000.0s-1012.0s  score 60
```

An edit run prints one line for each clip, with its length before and after the suggestion and what is suggested, the reason under it, then any `changed:` and `ignored:` lines, as in [An editor](#an-editor).

Exit code 0 means the app would use the answer, 1 that the run failed or the answer would be refused, 2 that the run couldn't start. Every option is in [SDK](sdk.md#running-your-plugin-the-way-the-app-does).
