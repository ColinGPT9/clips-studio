# Step 2 API: Make clips again, and edits that follow a moved clip

The engine side of step 2 (#190), as the desktop app's UI uses it. Every route
here is on `http://127.0.0.1:8765`. Both new routes are internal
(`server/api_stability.py` lists neither), so their shape may change with the
dialog that uses them.

Engine code: `core/remake.py` (options, source, which clips are kept, the
old clips' files while the run works, the video's own copy, the
replacement, the journal and the start-up recovery), `core/in_use.py` (the
clips a request is working with right now, and the files a run holds),
`core/housekeeping.py` (Storage's clean-up leaves held files, and a file
whose other Make clips again name a clip or a video has), `core/cancel.py`
(the count of a video's Cancels), `server/api.py` (routes, `DELETE
/clips/{clip_id}` leaving a file another clip uses or a run holds, clip
JSON's `media_version`, and the clip routes that hold their clip),
`server/jobs.py` (the worker finishes an interrupted run before its first
job, and calls `core/remake.py` only for a job with `replace_untouched`;
`_rerender_clip` carries a clip's edit and gives its new row no
fingerprint; the translate job marks a clip it exports in other
languages), `video_editor/timeline.py` (`shift_edit`), `core/state.py` (the
`edited_at`, `as_made` and `first_exported_at` columns, `mark_edited` and
`mark_exported`, and where edit marks start).

## What it does

**Make clips again** runs a video again with new settings, from its download
or its link, so nobody has to add the video again. It is an ordinary process
job, forced as Process again is, plus `"replace_untouched": true`. When that
run has finished and made at least one 9:16 clip, the video's old 9:16 clips
that nobody used and that the run didn't make again are deleted. Everything
else stays: a clip anyone used (the list below), every Longform output and
story reel, and every clip of every other video.

- **Old clips** are the video's clips that existed when the run started.
- **Not while one of its clips is in use.** A Make clips again isn't queued,
  and a queued one doesn't start, while one of the video's clips is held by
  a request, exported on the editor lane, or has a publish job waiting or
  under way (`core/remake.py busy_clips`; below).
- **Nothing from before changes while the run works.** As the job starts,
  each old clip's file is put out of the run's way (below), and what the
  run would write on an old clip's row (a moment found again, a story reel
  joined again) is held back. So a render can never write over a kept
  clip's file, whatever its name. The files the run holds back, and those
  of the clips from before, are held (`core/in_use.py`) until the run is
  over, so Storage's clean-up and `DELETE /clips/{clip_id}` leave them.
- **Made again:** the run found the same window (start and end, to the
  hundredth) and rendered it. An unused clip then takes the run's new file,
  scores, render options, score and hook, and keeps its title, description
  and hashtags, if that file is still there when the run ends; if it isn't
  (deleted by hand, say), the clip keeps its own file and everything it
  had, and the job log says so. A kept clip (any reason below) stays
  exactly as it was: its row, its file and its render options. The run's
  own render of its moment (made with its saved edits if it has some, D33)
  is dropped.
- **Clips with no fingerprint** (`older`, below: made before this update,
  or by another version of Clips Kitty) are kept, unless the run makes their
  window again: then each is made again in place as an unused clip is, and
  keeps its title, description and hashtags. It still counts as kept, as
  `GET`'s `counts` said, in the job log and the job's `result` (where `again`
  counts it too: What the job says it did).
- **Nothing is deleted or changed** when the run fails, is cancelled, stops
  halfway because the app closed (the next start puts every old clip's file
  back first, The app closed in the middle; the job runs again from the
  queue, and that run's "old clips" are the ones there then), makes no 9:16
  clips, or couldn't make every clip it set out to make: every old clip
  keeps its row and its own file. The clips the run made stay, as in any run
  (in the last case, the new windows it made are added beside the old
  clips). A Cancel counts up to the moment the first clip from before
  changes (below), and the job then ends `cancelled`.
- **A render failed:** `core/pipeline.py` records how many clips it is
  about to render (its outcome's `clips`, through the run's database)
  before it renders, and registers each one it made; a render that failed
  registers nothing. When the run registered fewer, nothing is replaced and
  nothing is made again in place (`Run.renders_failed`, the job log line
  below). The safer reading of "only after the new run succeeds".
- **Process again** (Add videos, after `already_processed`) is unchanged: a
  forced job without the flag, which keeps every clip from before.

## A Cancel

`POST /cancel` sets the video's flag (`core/cancel.py`), which the run looks
for at its checks. `core/pipeline.py` clears that flag itself once its
download step is done (a fresh start), so a Cancel pressed before then (the
model check, finding or converting the source, downloading an old AV1 copy
again, where `_cached_or_download` also carries on with the cached copy)
used to be forgotten: the run finished and the old clips were replaced. Now:

- `core/cancel.py` counts every Cancel of a video (`times_requested`), and
  clearing the flag leaves the count. The worker reads it before its own
  fresh start, and a Make clips again job counts every Cancel after that
  (`Run.cancelled`).
- The run's database asks again for a Cancel whose flag was cleared,
  whenever the run reads or writes through it, so the run stops at its next
  check (right after the download step) rather than going on.
- The worker checks once before the run starts (a Cancel during the job's
  checks) and once after it (one pressed after the last clip, while the
  creator's learning or the story reels finish). Either way the job ends
  `cancelled` and nothing is replaced.
- `Run.finish()` looks once more, inside the write lock and
  `in_use.settled()`, right before it changes its first clip from before
  (deleted or made again in place), and at its end when it changed none.
  A Cancel since the job started then stops it: nothing is changed,
  `finish()` raises `CancelledError`, and the job ends `cancelled`.

So **a Cancel counts when it is pressed after the job started and before
`finish()` changes its first clip from before**: while the job waits for its
clips to be free, during its checks, while the video is made, and while
`finish()` decides. After the first change, the rest of the replacing goes
on and the job ends `done`: half the old clips replaced and half not would
be neither the old set nor the new. That moment is the end of a job that may
have run for an hour, and lasts as long as deleting a few rows and files.

Plain runs, Process again included, are unchanged: only a job with
`replace_untouched` reads the count. (The pipeline's own clear, and
`_cached_or_download` carrying on after a Cancel, are left as they are for
them.)

## Protection

The same as `POST /jobs`, `PATCH /jobs/{id}` and `DELETE /clips/{clip_id}`:

- **Host check** on every route (`TrustedHostMiddleware`: `127.0.0.1` or `localhost` only).
- **A JSON body is required** on `POST`: a page elsewhere can send a form or
  plain text to 127.0.0.1 without asking the browser, and FastAPI answers
  that with `422`, so it can't start one.
- No session header, so it works in the browser shim (`npm run dev:web`) too,
  where nobody can pick a file to add it again.

## `GET /videos/{video_id}/remake`

What the dialog starts from. Reads only; nothing is queued.

```json
{
  "video_id": "aB3dEfGhIjK",
  "options": {"long_clips": true, "captions": true, "caption_style": {"font": "Arial", "font_size": 84}},
  "from_job": 148,
  "source": {"kind": "link", "downloaded": true, "link": "https://www.youtube.com/watch?v=aB3dEfGhIjK", "usable": true},
  "counts": {"total": 9, "replaced": 5, "kept": 4, "kept_because": {"longform": 1, "edited": 2, "published": 1}},
  "clips": [
    {"id": 80, "start_s": 3574.12, "end_s": 3650.0, "replace": true, "reasons": []},
    {"id": 81, "start_s": 3700.0, "end_s": 3790.5, "replace": false, "reasons": ["edited", "published"]}
  ],
  "queued_job": null,
  "blocked": null
}
```

- **`options`**: the options of the video's last 9:16 run (a finished one
  first, else the last that failed or was cancelled; never one still waiting
  or running), cleaned to the fields `POST` takes (below). Absent keys mean
  the setting's own value, exactly as for a job. `{}` with `from_job: null`
  when no such job is left: **Clear completed** on the Queue page deletes
  them. Then the dialog may start from the Generate bar's current choices
  instead.
- **`from_job`**: that job's id, or `null`.
- **`source.kind`**: `"file"` for a video added from this PC (its id starts
  `local_`), `"link"` for one added by link.
- **`source.downloaded`**: its download is in `data/downloads` (for a link,
  either the usual copy or Vertical Live's own).
- **`source.link`**: where it can be downloaded again: the link its runs
  recorded, else one a process job or a watched channel used. Only a link to
  this very video counts (a channel or live page would fetch another one).
  Always `""` for a file: a file's "original link" is another video.
- **`source.usable`**: a job can be queued. A link is enough (the run uses
  the download when it's there and downloads it again when not; a watched
  channel's **Delete downloads once published** removes them). A file needs
  its copy in `data/downloads`. A linked video with no link left but its
  download here is made from the download (queued as `local:<id>`).
- **`counts`**: `replaced` is the 9:16 clips nobody used (the run makes each
  one again with the new settings, or deletes it). `kept` is every other
  clip, Longform outputs and story reels included. `kept_because` counts each
  kept clip once, under its first reason, in the order of the table below, so
  it adds up to `kept`. These are counted now: what the run finds again, and
  anything done to a clip before the run ends, can change them. A clip with
  no fingerprint counts as kept (`older`) even though the run will make it
  again in place if it finds its window again.
- **`clips`**: every clip of the video, ordered by start, with all its
  reasons in table order. `replace` is `true` when `reasons` is empty.
- **`queued_job`**: `{"job_id", "status": "queued" | "running"}` when this
  video is waiting or being processed (any process job for it), else `null`.
- **`blocked`**: why the action can't be used right now, for its tooltip, or
  `null`. In this order: `"running"` (being processed), `"queued"` (waiting
  in the queue), `"source_gone"` (`source.usable` is false), `"busy"` (one
  of its clips is held by a request, exported on the editor lane, or has a
  publish job waiting or under way: `core/remake.py busy_clips`),
  `"queue_full"` (5 videos waiting or running). The app reads it again when
  the video changes, on every `queue` event, when a publish (`publish`
  event, `phase: "prepare"` or with `terminal`) or an export (`export` event
  not `running`) starts or ends, when either Publish dialog closes and when
  **Export all** ends. Most holds send no event when they end (a WoopSocial
  or Upload-Post publish, a watched channel's, an Original export, a
  preview), so while it is `"busy"` and that video is open the app also
  reads it every 4 seconds, and stops when it changes or another video is
  opened.

`404 {"detail": "no such video"}` for a video the library doesn't have.

## `POST /videos/{video_id}/remake`

Queue it. The body is the options, all of them, not a patch:

```json
{"long_clips": false, "captions": true, "caption_style": {"font": "Arial", "font_size": 84}}
```

Fields, each optional, as `POST /jobs` (docs/API.md) and `JobOptions` in
`lib/types.ts`: `whole`, `max_clips`, `caption_style`, `captions`,
`long_clips`, `filter`, `min_score`, `focus`, `sport`,
`watermark_profile_id`, `podcast`, `vertical_live`, `gaming`,
`gaming_scoring`, `gaming_layout`, `gaming_remember`, `hashtags`,
`pipeline`, `rate`, `understand`, `edit`. That list is
`core/remake.py OPTIONS`, and a test keeps it equal to `JobIn` less `url`,
`force`, `webhook_url`, `webhook_secret`, `then` and `longform`. Unknown
fields are ignored.

- Not carried, not taken: the link and `force` (the video's own link or file
  is used, and the run is forced), the original job's `webhook_url`,
  `webhook_secret` and `then` (what whoever queued it asked for, such as a
  publish once it finished), and `longform`: this makes the 9:16 clips again.
  `longform` in the body is `400` (below); hide the Longform switch in the
  dialog.
- **Send back what you didn't change**, including keys the dialog doesn't
  show (`hashtags`, `gaming_layout`, `gaming_remember`, `pipeline`, `rate`,
  `understand`, `edit`): a key left out is the setting's own value. With
  `QueueItemSettings`, pass a `save` that merges its patch over `options`:
  set each field the patch has, then delete each key in its `clear` (that is
  how it switches a toggle off), and post the result. GET's `options` posted
  back unchanged queue exactly the last run's options (tested).
- **Show what changes what the run picks.** `whole` (the whole video as one
  clip, with no moments picked: a watched channel's Twitch clips are made
  so) is shown, never carried unseen: the dialog passes `offerWhole`, and the
  panel then has a **Whole video (one clip)** switch, on, when `options`
  has `whole`, to keep or turn off (off, the patch's `clear` names it). The
  engine refuses `whole` with `longform`, `long_clips`, `sport`, `pipeline`,
  `rate` or `edit` (400, "Whole clips can't be combined with ..."), so on
  the panel keeping Whole video on turns those off, and turning one of them
  on turns Whole video off. Hidden, a no-speech video made again with
  "ordinary" settings made its one whole clip again instead of none
  (black-box S3).

Checked exactly as `POST /jobs` checks its options (`_process_options`): the
same clamps (`max_clips` 1-10, `min_score` 0-100), the same filter check and
the same refusals of options that can't be combined, with the same messages.

The queued job's payload, as `GET /queue` shows its `settings`:

```json
{"force": true, "replace_untouched": true, "long_clips": false, "captions": true, "caption_style": {"...": "..."}}
```

with `url` the video's link, or `local:<video_id>`. Answer:

```json
{"job_id": 312, "video_id": "aB3dEfGhIjK"}
```

The route only queues, as `POST /jobs` does. Add videos starts a stopped queue
itself afterwards (`POST /queue/resume` when it isn't running); the dialog can
do the same.

Errors, with a sentence for a person in `detail`:

| Status | When | `detail` |
|---|---|---|
| 400 | `longform` in the body | "Make clips again makes the 9:16 clips again, so Longform can't be on here. Turn Longform off, or add the video again for 16:9 clips." |
| 400 | a file whose copy is gone | "This video's file isn't in Clips Kitty any more, so its clips can't be made again. Add the file again." |
| 400 | a linked video with neither a link nor its download | "Clips Kitty no longer has this video's download or a link to it, so its clips can't be made again. Add the video again." |
| 400 | an option `POST /jobs` would refuse | the same message, for example `unknown filter 'x'` |
| 404 | no such video | "no such video" |
| 409 | the video is waiting or being processed | "This video is already waiting or being made. Let that finish first, or remove it from the queue." |
| 409 | one of its clips is in use (`busy_clips`, as `blocked: "busy"`) | "Clips Kitty is working with one of this video's clips right now (publishing, exporting or editing it). Make its clips again once that's done." |
| 409 | the queue is full | "the queue is full (5 videos) — let one finish or remove one first" (as `POST /jobs`) |
| 422 | no JSON body, or a field of the wrong type | FastAPI's own |

The checks run in that order: longform, the video, its source, the options,
then the queue (already waiting or being made), its clips in use, and a
full queue.

The job checks its clips again as it starts, since it may wait in the queue
for hours (The files of the clips from before).

## Kept because

A clip is kept when any of these holds. They are read when the run ends (and
when `GET` asks), so something done while the run works counts too, and read
once more for each clip in the moment it would be deleted or made again in
place (below). When in doubt, the clip is kept.

| Reason | What is checked (`core/remake.py`) |
|---|---|
| `longform` | `render_opts.profile` is set: a 16:9 Longform output. Never replaced. |
| `story_reel` | `render_opts.reel` is set: a match's story reel. Never replaced. |
| `edited` | `core.pipeline._edited(render_opts)` (D33): it holds `edit`, `crop`, `caption_lines`, `speaker_edits`, `adjust`, `captions` or `normalize_audio`, or a `gaming` split turned off (`null`) or drawn for this clip alone (`by: "clip"`). |
| `edited` | `clips.edited_at` is set: a person changed its title, description or hashtags (`PATCH /clips/{id}` with a different value), or asked for a render of it. Every route that queues a render job, which only a person starts, sets it as it queues the job (`StateDB.mark_edited`): Apply and a new start or end (`POST /clips/{id}/render`), a caption fix (`PUT /clips/{id}/captions`), the AI chat (`POST /clips/{id}/ai-edit`, when it queues a render) and a render before a YouTube publish (`POST /clips/{id}/publish` with `render_first`). So a render that fails or is cancelled leaves the clip marked: its job holds the person's edit (the typed caption lines live nowhere else until the render works), and Retry on the Queue page needs the clip. A render that works marks its new row too (`server/jobs.py _rerender_clip`). New in step 2: a change made before it, or in a version without it, left no mark here, which is what the fingerprint below and `older` are for. |
| `edited` | a `render` job that names it (`payload.clip_id`), in any state: waiting, running, done, failed or cancelled. Until the Queue page's history is cleared; `edited_at` above lasts. |
| `edited` | its fingerprint no longer matches it: `clips.as_made` holds a hash of the title, description, hashtags (as the list they hold), start and end its run gave it (`core/state.py as_made`, saved by `StateDB.add_clip` as the row is inserted), and the row's now differ. Whichever version of Clips Kitty changed them: the released app's `PATCH /clips/{id}` wrote only the new words. The same words saved again change nothing. |
| `edited` | an edit a plugin suggested for it was used or hidden: `scores.plugin_edits[].state` is not `"new"` (a run writes them all as `"new"`). A plain **Process again** that finds the clip's window again keeps only `used` decisions, so a hidden one stops counting after it (Known limits). |
| `edited` | a `clip_feedback` row for it other than `exported`: `captions_edited`, `timestamps_adjusted`, `rerendered`, or any other action. |
| `edited` | a person chose its thumbnail: `thumbnails/clip_<id>_chosen.jpg` is in the data folder (`POST /clips/{id}/thumbnail`: their own image, a frame or a generated one; both publish dialogs send that file). The frames and candidates the picker only offers (`clip_<id>_<ms>.jpg`, `clip_<id>_gen<n>.jpg`) don't count. Clip ids are never reused (`AUTOINCREMENT`), so the file is this clip's. |
| `published` | an `uploads`, `clip_publishes` or `publish_jobs` row, in any state (sent, waiting, failed or skipped), found by the clip's id or by its window (`video_id`, `start_s`, `end_s`, which a post keeps when a re-render moves the id). |
| `published` | `clips.status` is `uploaded`, or `failed` (an upload was tried). |
| `scheduled` | `clips.status` is `scheduled`, or `clips.scheduled_for` is set (a schedule slot). |
| `exported` | `clips.exported_at` is set (exported, or starred by hand), or a `clip_feedback` row `exported`. |
| `exported` | `clips.first_exported_at` is set: when the clip was first really exported (`StateDB.mark_exported`, beside `exported_at`): an Original copy (`POST /clips/{id}/export`, `POST /export/batch`, which **Export all** and the MCP tool `export_clip` use) or an export on the editor lane, when it has made the file. The star (`PATCH /clips/{id}` `exported`) doesn't touch it, and erasing a creator's learning doesn't reach it, so a clip exported and then un-starred is kept. A re-render carries it to the new row. The migration that adds it copies it from `clip_feedback` `exported` rows (the earliest), once. Clip JSON leaves it out. |
| `exported` | it was exported in other languages: an `app_state` key `clip_language_export:<clip_id>` (`core/remake.py note_language_export`), which the translate job's export stage (`POST /translate`, `stage: "export"`, its default) writes before it writes the clip's subtitled, burned or dubbed videos into the person's folder. That stage records nothing else, and discarding a translation's text (`DELETE /clips/{id}/translations/{language}`) doesn't remove it. For the exports made before this mark, the migration that adds `first_exported_at` writes it once from every translate job of the export stage that has run (the same jobs as the next row), so clearing the Queue page's history since doesn't lose them. The key's value is `{"clip_id", "video_id", "languages", "at"}`; the clip's id in the key is what counts, so a value that can't be read still keeps the clip. Nothing else reads it: the star, **Export all** and creator learning go by `exported_at` and `clip_feedback`, which it leaves alone. A re-render gives the clip a new id and marks it `edited`. |
| `exported` | a `translate` job that names it in its `clip_ids`, with `stage` `export` (or none, its default), that has run: `done`, `failed` or `cancelled` (one that stopped partway may have written some files). The only mark another version of Clips Kitty leaves of a translated export, until **Clear completed** on the Queue page deletes the job. |
| `translated` | a `clip_translations` row. |
| `submitted` | a `campaign_submissions` row (Discord or a website), by the clip's id or its window. |
| `submitted` | a publish that will send its link to Discord: its intent, saved in `app_state` under `discord_post_link:<destination>:<provider>:<clip_id>` (`campaigns/post_links.py` `prepare`) before the upload starts, and there until the post is live and its `campaign_submissions` row exists. Found by the intent's `clip_id`, or by its window (`video_id`, `start_s`, `end_s`). |
| `chosen` | a `watch_items.chosen_clips` entry of this video holds its window: the clips a watched channel's first publish chose. |
| `busy` | a `render` or `translate` job that names it is waiting or running (a render is `edited` too). |
| `busy` | an export of it is waiting or running on the editor lane (the lane's jobs, in the app's memory: `Worker.clips_in_use`). |
| `busy` | a request is working with it right now (`core/in_use.py`, in the app's memory). Every route that reads a clip and then writes for it holds the clip from before it reads it until what it did is recorded: a WoopSocial or Upload-Post publish, one clip or a batch (a batch holds all of its clips from before its first upload, so the ones still to be sent are kept too; the same loop serves a watched channel's and a finished job's publishing); a watched channel's publish (every clip of the video, from before it reads them until its posts are sent); a YouTube publish (until its `publish_jobs` row is saved); a YouTube publish plan carried out (`POST /publish/plan/execute`: the assistant's Confirm button and the MCP tool `publish_plan_execute`), which holds every clip of the plan from before it reads the first until the last one's `publish_jobs` row is saved; an Original export (until the copy is marked exported); an export queued on the editor lane (until the lane has it); a thumbnail choice (until the file is written); thumbnail candidates (while they are made from its file); the AI chat (until its render job is queued); a new title, description, hashtags, star or suggestion decision (`PATCH /clips/{id}`, until it is saved); a caption fix (`PUT /clips/{id}/captions`) and a render (`POST /clips/{id}/render`), until the job is queued; a preview (`POST /clips/{id}/preview`, while it is made); a saved translation (`PUT /clips/{id}/translations/{language}`); queueing a translation or a translated export (`POST /translate`, until the job is queued); a glossary ruling and a remembered Gaming layout (which read the clip for its creator); sending a post's link to Discord (`POST /discord/clips/{id}/send`) and attaching clips to a campaign (`POST /campaigns/submissions`). |
| `older` | it has no fingerprint (`clips.as_made` is `""`): made before this update (the column is added blank), or by another version of Clips Kitty (its `add_clip` names its own columns, so it writes none), or the new row of a re-render (`_rerender_clip` blanks it: that row is inserted with the clip's words as they were, which may be a person's; it is `edited` too). A title, description or hashtags a person changed on such a clip, or a start or end changed by hand whose learning was wiped since, may have left no mark this version reads, so it can't be told from one nobody used. Also `older`: its id is at most `app_state` `clips_unmarked_through` (`core/state.py UNMARKED_CLIPS`, below). When the run makes its window again it is made again in place like an unused clip (its title, description and hashtags stay, and it keeps no fingerprint), so it counts as replaced; otherwise it is kept. |
| `unsure` | its `render_opts` can't be read, or `clips.status` is one Clips Kitty doesn't know (anything but `queued`, `rendered`, `scheduled`, `uploaded`, `failed`). |

A run gives every clip it makes `status: "queued"`, a blank `scheduled_for`,
`exported_at`, `first_exported_at` and `edited_at`, the fingerprint of the words and window it
gave it, and none of the rows above, so a clip made by a run and left alone
has no reason. Its `render_opts` hold only what the job decided
(`job_captions`, `caption_style`, `podcast`, `gaming` found by the run,
...), which `_edited` doesn't count. Every clip a run makes goes through
`core/pipeline.py _register_clip`, which inserts it with the words
generated for it (`tests/test_remake_files.py` runs the real
`process_video` to show it).

`clips_unmarked_through` is written once, by the migration that adds
`edited_at`. If the column is there without it (the app stopped between the
two), it is written at the next start with the highest clip id then: every
clip so far counts as `older`. With the fingerprint it is no longer needed:
the clips it counts were inserted before `as_made` existed, so they have
none and are `older` on that alone (written again as above, it counts more,
which can only keep a clip). It is left as it was.

### What can erase a mark while the clip stays

A mark of a use only keeps a clip while it is there. Every mark above was
checked against every action of this version that deletes or changes it
while the clip itself stays: **Erase everything learned** (`DELETE
/creators/{id}/memory`) and **Delete creator** (`DELETE /creators/{id}`),
which delete the creator's `clip_feedback` rows; the Queue page's **Remove**
(`DELETE /jobs/{id}`) and **Clear** buttons (`POST /queue/clear`); Storage's
**Clean up** (`core/housekeeping.py`); deleting a campaign, a watch or a
Discord destination; disconnecting YouTube, WoopSocial, Upload-Post, Discord
or a connected account; merging creators; the star; Show again on a hidden
suggestion; discarding a translation; a plain **Process again**. Where a
normal action can erase the only mark of a real use by a person, a mark that
nothing erases is written as the use happens (round 6, 2-D29 to 2-D31). One
exception, accepted by Colin: a hidden suggestion that Process again drops
gets no lasting mark (Known limits).

`clip_feedback` actions this version writes (`server/api.py _log_feedback`):

| Action | Written by | Erased while the clip stays by | Lasting mark |
|---|---|---|---|
| `captions_edited` | `PUT /clips/{id}/captions` | Erase everything learned, Delete creator | `edited_at`, set as the render is queued |
| `timestamps_adjusted` | `POST /clips/{id}/render` or `POST /clips/{id}/ai-edit` with a new start or end | the same | `edited_at`, the same |
| `rerendered` | the same routes without a new start or end | the same | `edited_at`, the same |
| `exported` | an Original copy (`POST /clips/{id}/export`, `POST /export/batch`), an editor-lane export once its file is made | the same | `first_exported_at` (copied once from these rows by the migration that adds it) |

The schema's comment also names `deleted`; nothing writes it (deleting a
clip deletes its rows). A re-render moves a clip's rows to its new id, and
merging creators moves them to the other creator: neither erases one.

Every other mark `core/remake.py` reads:

| Mark | Erased while the clip stays by | Then |
|---|---|---|
| `clips.edited_at` | nothing | lasting |
| `clips.as_made` against the row's words and window | nothing (a person's change is in `edited_at` too) | lasting |
| `render_opts` keys only a person sets (D33) | only a render, which sets `edited_at` | lasting |
| `scores.plugin_edits[].state` | `hidden` back to `new` by the person's own **Show again** (`PATCH /clips/{id}` `suggestion`), and dropped by a plain **Process again** that finds the clip's window again (`plugins/edit_marks.py carry`, reached from `core/pipeline.py _register_clip`, keeps only `used` decisions); `used` only by a render, which sets `edited_at` | no mark: Show again puts the clip back as it was made; after Process again the clip counts as one nobody used (Known limits) |
| `thumbnails/clip_<id>_chosen.jpg` | nothing: Clean up doesn't look in `thumbnails/`, and making candidates replaces only `clip_<id>_gen<n>.jpg` | lasting |
| `clips.status` `uploaded`, `failed`, `scheduled`, and `scheduled_for` | nothing resets them | lasting |
| `clips.exported_at` | the star (`PATCH /clips/{id}` `exported: false`) | a real export also wrote `first_exported_at`, which nothing clears; a star set by hand and cleared again exported nothing, so no mark |
| `clips.first_exported_at` | nothing | lasting |
| `uploads` | only with the clip (`DELETE /clips/{id}`, deleting the video); a re-render moves them; disconnecting YouTube changes only its accounts | lasting |
| `clip_publishes` | nothing deletes one; a re-render moves it; removing the WoopSocial or Upload-Post key removes only the key | lasting |
| `publish_jobs` | nothing deletes one (the Queue page's buttons work on `jobs`) | lasting |
| `campaign_submissions` | nothing deletes one: deleting a campaign that has any archives it and cancels the rows not sent yet; moving one cancels it and adds it to the other campaign; removing a Discord destination marks its rows to be done by hand | lasting |
| `discord_post_link:*` intents | `campaigns/post_links.py recover`, once every platform of the post has settled (a live post has its `campaign_submissions` row, a failed or skipped one its `clip_publishes` row), or when its Discord destination is removed | the post's own rows keep the clip; a publish refused outright sent nothing and left no post, so no mark |
| `watch_items.chosen_clips` | deleting the watch (`DELETE /automation/watches/{id}`) | the choice is the watch's, not a person's; every clip it sent anything for has its own `clip_publishes` rows (written as `sending` before the upload), which stay, so no mark |
| `clip_translations` | the person's own **Discard** (`DELETE /clips/{id}/translations/{language}`), and deleting the clip | no mark: a discard is the person taking that translation back, and nothing of it is left in the app; an export of it has its own lasting key |
| `clip_language_export:<id>` | nothing (deleting the clip leaves it; ids are never reused) | lasting |
| `render` jobs, any state | Remove and Clear on the Queue page | `edited_at`, set as the route queues the job (above) |
| `translate` jobs of the export stage that have run | Clear on the Queue page | `clip_language_export:<id>`, written before the job writes any file, and by the migration for exports from before it; one that stopped before it wrote anything exported nothing, and clearing it also removes its Retry, so no mark |
| waiting `render` and `translate` jobs (`busy`) | Remove and Clear waiting | a withdrawn request; a render's clip is `edited` already |
| `app_state clips_unmarked_through` | nothing (only the migration writes it) | lasting |
| `core/in_use.py` holds, editor-lane exports (memory) | they end when the work does | each use records its own lasting mark when it is done (above); an editor-lane export that failed made nothing |

## How a clip is deleted or made again

`finish()` first judges every old clip (`classify`) to plan what changes:
an unused clip whose window the run made again (or one whose only reason is
`older`) is made again in place; an unused clip it didn't make again is
deleted. Then each one is judged again in the moment it would change, one at
a time:

1. `BEGIN IMMEDIATE` on the worker's connection, so no other connection can
   write until this clip is decided;
2. inside `core/in_use.py` `settled()`, so no request can start holding a
   clip: one that starts now waits, and then finds the clip gone, or made
   again, rather than one about to change;
3. before the first change only: a Cancel since the job started stops it
   all (A Cancel);
4. every check above for this clip, read now, with the clips the lane and
   the requests hold;
5. still no reason (or, to make it again, only `older`): the change, whose
   commit ends the transaction before any waiting request goes on. A reason
   now: rolled back, and the clip is kept as it is, its file too (the log
   counts it under that reason).

A clip to make again whose new file is gone by then is kept as it is too
(the log says `whose new video was gone`).

Deleting is as `DELETE /clips/{clip_id}`: `StateDB.delete_clip` (the row,
with its `uploads`, `clip_translations` and `clip_feedback` rows, which an
unused clip doesn't have), the editor's draft `previews/clip_<id>.mp4`, and
the clip's file when it is inside the data folder. Two additions:

- A file another clip still points to stays, and so does one another run
  holds (`core/remake.py file_in_use`, which `DELETE /clips/{clip_id}` now
  uses too, below).
- A file Windows holds open (a player showing it) stays on disk, as with
  `DELETE`; **Storage → Clean up** reclaims it later. The row goes either way.

The deleted clip's file is the one it was put aside under (below).

Made again in place, a clip's row takes the run's new file and scores (what
`core/pipeline.py _register_clip` would have written, held back until now)
and the score, hook and render options the run made that file with (its
title, description and hashtags stay), so the editor and any later render
of it use the new settings. Its old file, put aside, is deleted when
settled, unless something may be reading it then. After a run that failed,
was cancelled, or in which a render failed, none of this happens.

## The files of the clips from before

A run writes each clip's file under a name made from its whole seconds
(`clip_00010-00070.mp4`, `core/pipeline.py _render_files`), and a story reel's
under what it is (`reel_recap.mp4`), in the video's clip folder. So a moment
found again, or a new one with the same whole seconds (10.4-70.6 s), would be
written over a clip from before, a kept one too, while the run works and
before anyone knows whether it will finish. `core/remake.py Run.set_aside`
stops that, as the job starts (before the job's checks):

1. **First, none of the video's clips may be in use**: held by a request
   (`core/in_use.py`), exported on the editor lane or about to be, or with a
   `publish_jobs` row `queued` or `running` (the publish worker reads its
   path as it starts), by its id or its window (`busy_clips`). The check and
   every step below run inside one `in_use.settled()`, so no request can
   start holding one of them in between: one that starts then waits, and
   finds the file where its row now says. While one is in use, the job waits
   outside that lock (its log says `Make clips again: waiting for this
   video's clips to finish being published, exported or edited`), looking
   every half second for up to `START_WAIT` (120 s); a Cancel stops it.
   Then it fails before its run, with nothing changed: "Clips Kitty was
   still working with one of this video's clips (publishing, exporting or
   editing it), so nothing was made again. Make its clips again once that's
   done." **Retry** on the Queue page starts it again. So nothing can be
   reading a file whose first name the run may write over.
2. Each old clip's file inside the data folder is renamed beside itself, with
   `.clip-<id>` before its extension (`clip_00010-00070.clip-17.mp4`), and its
   rows point there. No run writes such a name. Clips that share one file (two
   windows with the same whole seconds) move together, named after the lowest
   id.
3. A file Windows won't rename (a player showing it holds it open) is copied
   there instead, and left where it was; only that player can be reading it
   (1), and it records nothing. (Built without the worker's wait, as tests
   do, `Run` copies a file something may be reading too.)
4. A file outside the data folder stays as it is: no run writes there. A
   missing file has its rows pointed at the new name all the same, so the
   run's render of their moment can't become theirs. A file already under a
   `.clip-<id>` name (a kept clip whose first name a new clip took) stays
   where it is. A row on a `.clip-<id>` name with no file there, while the
   file has its first name and no other clip plays that, is pointed back at
   it (saved with the plan, below) and the file put aside as any is: such a
   row is what an app closed as a file went back used to leave.
5. A file that can neither be renamed nor copied (a full disk) fails the job
   before its run starts, with "A clip from before couldn't be kept safe
   while its video is made again (...), so nothing was made again." What was
   put aside goes back first.

**Held files.** From then until the run is over, the run holds in
`core/in_use.py` (`hold_files`) each old clip's file under both its names,
and every render it holds back: the new file of a moment found again, and a
story reel joined again, which no row points at until `finish()`. Nothing
deletes a held file: **Storage → Clean up** (`core/housekeeping.py`
`survey`, so `GET /storage` doesn't count it either and `POST
/storage/cleanup` leaves it), `DELETE /clips/{clip_id}` (a new clip with the
same whole seconds shares the held render), and Make clips again's own
deleting. Outside a run nothing is held, and the clean-up is as before.

Once the run is over (after `finish()`, and in `stopped()`), each one is
settled, inside `in_use.settled()`, and then the run holds nothing:

- A clip still on its file put aside (kept, or every clip after a run that
  stopped) gets it back under its first name, over whatever the run left
  there, and its row its path as it was written. Its rows are pointed back
  first, in one commit with that step in the journal (below), and then the
  file follows; a file that can't follow (held open) has its rows pointed
  at it where it is again. It stays under the new name instead when a clip
  of this run now has the first name (the same whole seconds), when
  something may be reading one of its clips (held by a request, on the lane,
  being published), or when the file is held open.
- A file put aside that no clip plays any more (its clips were deleted, or
  took the run's new file) is deleted.
- A file under a first name that no clip plays is deleted only when this
  run wrote it (a file it handed one of its clips: its render of a kept
  clip's moment). Anything else there may be a clip's own file (an app
  closed halfway, or one moved by hand), and stays.
- Neither of those is deleted while something may be reading one of the
  clips it belonged to (as above).

So after a run, a kept clip's `path` is what it was, or its `.clip-<id>` name
when a new clip took its first one, or while something was reading it. Its
file never changes.

### The app closed in the middle

Closing Clips Kitty ends the engine (taskkill /F), and so do a power cut and
Windows shutting down: nothing after that moment runs. So the run keeps a
journal: `app_state` key `remake_journal:<video_id>` (`core/remake.py
JOURNAL`), JSON `{"video_id", "entries": [{"own", "aside", "ids", "was",
"state"}], "source": {"copy", "second"} | null}`, one entry per file of the
clips from before:

- It is saved before the first file moves, with every entry `"planned"`
  (or `"aside"` for a file already aside), in the commit that points back
  the rows step 4 repairs.
- Each file's next step is saved in the same commit as the row change it
  goes with, before or after the file moves, so the journal always says
  where each row points: `"aside"` in the commit that points the rows aside
  (after the file moved), `"back"` in the commit that points them back at
  the first name (before the file follows).
- The video's own copy (below) is saved before its second name is made.
- It is deleted once every file is settled and the copy is back; one a
  file couldn't be settled in stays for the next start.

`recover()` finishes a journal the way `stopped()` settles a run: the worker
calls it as the engine starts, before it takes a job (`server/jobs.py
Worker.run`), and a Make clips again calls it for its own video before it
starts. For each entry, from where its rows point and which of its files
exist:

- rows on the first name, file aside: the file follows its rows (`"back"`:
  over what the run left there; `"planned"`: when nothing is there);
- `"planned"`, both there: the first name is the file its rows play, and
  the put-aside one (a copy the run made, or one from before) goes;
- rows aside: settled as after a run (back under the first name, or left
  aside when a clip of the run took the first name); a recovered run wrote
  no file it knows of, so it deletes nothing under a first name;
- a file that can't move (held open by another program) has its rows
  pointed at it where it is;
- the copy's second name goes back under the copy's name when the copy is
  gone, and is dropped otherwise.

What `finish()` had committed stays: clips it deleted stay deleted (their
files go if they are still there), and clips it made again in place keep
the run's file. A journal that can't be read moves nothing and is dropped:
the next Make clips again of the video finds each file where it is (step
4). The engine's log says `Make clips again: Clips Kitty was closed while
<video id>'s clips were being made again. Every clip from before has its
own video back`, or `(Make clips again: the record of an interrupted run of
<video id> couldn't be read; its clips' files stay where they are)`.

Until the engine has started, and if anything is ever left over, **Storage →
Clean up** (`core/housekeeping.py survey`) leaves a clip file whose other
name a clip row points at: the first name of a row's `.clip-<id>` name, and
a `.clip-<id>` file whose first name a row points at. It also leaves
`<video id>.remaking.<ext>` of a video in the library. A file under a
put-aside name that settling left because something was reading it, while
its clip plays its first name, is therefore never taken by the clean-up; the
video's next Make clips again puts that clip's file aside over it.

`tests/test_remake_crash.py` kills a real process after every rename, copy,
hard link, delete and commit of a run in turn (and of the recovery itself),
then checks that every clip from before plays its own video after the
clean-up, the start-up recovery, the next Make clips again and the clean-up
again.

## The video's own copy

A Make clips again of a file from this PC, or of a linked video whose link
is lost, runs from the video's copy in `data/downloads` (queued as
`local:<id>`). The run's first step (`core/pipeline.py _cached_or_download`,
unchanged) deletes such a copy when FFprobe can't read it within a minute,
and nothing could bring it back. So as the job starts (`Run._keep_source`):

- The copy gets a second name beside it, `<id>.remaking.<ext>`, a hard link
  (no more disk space), held so nothing deletes it.
- Once the run is over: if the copy is gone and the video is still in the
  library with no copy, the second name goes back under the copy's name (the
  job log says `this video's own copy was put back, as the run had
  deleted it`); otherwise the second name goes. The job itself still ends as
  the run did (failed, with the pipeline's message).
- A drive with no hard links (exFAT, FAT32): the copy is read first, the way
  that step reads it (`video/encoding.py readable_video`). One that can't be
  read fails the job before its run, the copy left as it is: "Clips Kitty
  couldn't read this video's own copy just now, so nothing was made again.
  The copy is kept: try again, or add the video again."
- Both names are saved in the run's journal before the second one is made,
  so when the app is closed from then on, the start-up recovery puts the
  copy back if it is gone, and drops the second name otherwise (The app
  closed in the middle). **Storage → Clean up** leaves the second name of a
  video in the library. A second name with no journal (left by an earlier
  build) goes back the same way as the next such job starts.

A job of a linked video (its link) keeps no second name: a copy deleted
there was replaced by a new download first.

## `DELETE /clips/{clip_id}`

Unchanged, except that the clip's file stays when another clip's row still
points to the same file, or a Make clips again run holds it (Held files)
(`bytes_freed` is then `0`). Two clips share a file when a forced run
(Process again) finds a window with the same whole-second start and end as
an older clip: it writes the new clip over the older one's file
(`core/pipeline.py`, unchanged). Deleting either one used to delete the file
the other still played.

## Known limits

- **A hidden suggestion doesn't survive Process again** (accepted by Colin,
  2026-11-08). Hiding an edit a plugin suggested keeps the clip as `edited`.
  But a plain Process again that finds the clip's window again carries only
  `used` decisions (`plugins/edit_marks.py carry`, from `core/pipeline.py`,
  the plain path), so the hidden one is gone. The next Make clips again then
  counts the clip as one nobody used and replaces it. Nothing the person made
  is lost: hiding a suggestion left the clip exactly as its run made it. It
  needs a plugin that suggests edits.
- **File names are whole seconds** (existing forced-run behaviour, not
  changed here). A Make clips again puts the old files out of the run's way
  (above), but Process again, a plain forced run, still writes a new window
  with the same whole-second start and end over an older clip's file, which
  then shows the new clip; `DELETE /clips/{clip_id}` no longer deletes such a
  shared file. A person's re-render (`_rerender_clip`) names its file the same
  way, so re-rendering a kept clip whose first name a new clip took writes
  over the new clip's file. Fixing either means naming files differently in
  `core/pipeline.py`, the plain path, so it is left for Colin's call.
- **A kept clip's moment is still rendered.** The run renders every window
  it finds (with a person's saved edits, D33), and then drops the render of a
  kept clip's: time spent for nothing. Skipping it means changing which
  windows `core/pipeline.py` renders.
- **Story reels from before stay as they are.** A Sports run joins a reel
  of a kind it already has (the recap, a team's or a player's) again, under
  the same file name, and writes its new chapters on the old reel's row
  (`core/pipeline.py _sport_reels`). Both are held back and dropped, so the
  old reel keeps its file and words, and no new reel of that kind is made.
  A person who wants a new one deletes the old reel first.
- **The app closed in the middle** (taskkill as the app closes, Windows
  shutting down, a power cut) is finished at the next start, before the
  queue takes a job: every clip from before plays its own file again (The
  app closed in the middle; tested by killing a process at every step).
  What it can't do: a file another program holds open as the engine starts
  stays under the name it has, its rows pointed there; a journal that can't
  be read moves nothing (the next Make clips again finds each file where it
  is); and if another version of Clips Kitty is opened before this one
  starts again, that version doesn't know the journal: until this one has
  started once, a clip may show without its video there, and that version's
  **Storage → Clean up** could take a file whose row points at its other
  name.
- **A power cut can keep a step without the one before it** (a file's move
  on disk without the commit before it, or the other way round), which a
  killed process never does. Reasoned, not tested: the recovery still points
  a kept clip's rows at whichever of its two names has its file, as it
  looks at the files there; the one case it can't is the run's plan lost
  with the first file already moved, when that clip shows without its video
  until the video's next Make clips again finds it (step 4), and Storage's
  clean-up leaves it meanwhile. A clip nobody used that was being deleted in
  that instant may come back with no video.
- **Clips with no fingerprint** (`older`: made before this update, or by
  another version) are kept unless the run makes their window again, so a
  video processed before it keeps most of its old clips beside the new
  ones; a person deletes the ones they don't want. The dialog's count shows
  them under their own reason.
- **What another version does that this one can't see.** A title,
  description or hashtags it changes keep the clip (the fingerprint), and so
  does everything it records in a table this version reads (posts, uploads,
  schedules, the star and exports, translations, campaigns, Discord, a
  watch's choices, plugin decisions, learning rows) or a chosen thumbnail's
  file. Not seen: a translated export it made, once **Clear completed** on
  the Queue page has deleted the job (the job's row is its only mark), and
  a clip it re-rendered with nothing but a new start or end, whose learning
  rows were wiped since: that one has no fingerprint, so it is kept as
  `older`, but a run that makes exactly its window again makes it again in
  place (its words stay, its video and render options are the run's).
- **What isn't saved isn't seen.** Edits not applied yet in an open editor
  leave no mark: such a clip is replaced, and the editor closes (Events). A
  preview being made holds its clip, so that one is kept.
- **A job waits at most two minutes for its clips** (`START_WAIT`). A
  publish or an export that takes longer makes it fail with nothing
  changed, and the person presses **Retry** once it's done. While it waits,
  the queue waits behind it.
- **A Cancel after the first change is too late** (A Cancel): the rest of
  the replacing goes on, and the job ends `done`.
- **A render that failed stops the replacing**, even when the other renders
  made most of the clips: the old clips all stay, and the new ones are added
  beside them, so the person may see both until they delete the ones they
  don't want, or make the clips again.
- **Storage → Clean up while the run works** leaves every file the run holds,
  and still takes everything else it would (editor drafts included, as
  before). After the run, a file put aside or rendered that settling kept
  because something was reading it is the clean-up's, unless a clip row
  points at its other name (The app closed in the middle): then it stays
  until the video's next Make clips again puts that clip's file aside over
  it, or the clip is deleted.
- **The video's own copy is kept by a hard link.** On a drive without hard
  links, the copy is only read first, the same way the run's first step
  reads it, which leaves a sliver of time in which a copy that read fine
  could fail that step's read and be deleted. A copy the run converts to
  H.264 (a slow-to-decode file from the PC, converted once, in place) keeps
  its old version under the second name until the run is over, so the drive
  needs room for both meanwhile.
- **The two plain-pipeline behaviours** a Make clips again works around are
  unchanged for every other run: `core/pipeline.py` clears a Cancel's flag as
  its run starts (the run's database asks for it again), and
  `_cached_or_download` carries on with the cached copy when a re-download
  of an AV1 copy is cancelled or fails.

## Events

No new event types. On `ws://127.0.0.1:8765/ws`:

- `POST` sends `{"type": "queue"}`, as `POST /jobs` does.
- The run sends the usual `progress` events (`video_id`, stages, `render`
  with `clip` and `total`, then `done`).
- The old clips are deleted **before** the job's own end, so the job's
  `{"type": "job", "status": "done", "video_id", "clips": <count after>,
  "result": {...}}` and `{"type": "queue"}` come after the deletions.
  `result` is what the job says it did (below); a `done` event of any other
  job has none, as before. The Library already reads its
  clip list again on a job's `done` (`pages/ClipStudio.tsx`), so an open
  Library drops the deleted clips then, and an editor open on one of them
  closes: `editingClip` is looked up in the new list and is gone. A clip the
  run made again keeps its id, so an editor open on it stays open, and so
  does one on a kept clip.
- A clip made again in place keeps its id and the name of its file, but its
  file is the run's: its `media_version` (below) changes, so its card, its
  details and an editor open on it load the new video when the list is read
  again, with no reload.
- `failed` and `cancelled` end jobs as before; nothing was deleted.
- While the run works, the old clips' `path` in clip JSON is their
  `.clip-<id>` name (The files of the clips from before). The app reads clips
  by id (`/media/<id>` and the clip routes), and the rename keeps a file's
  time and size, so its `media_version` is the same and nothing in the UI
  changes.

## The job log

`GET /jobs/{job_id}/log` (the job's own file) ends with one of these lines,
after the run's usual ones:

```
      Make clips again: this run made 6 clip(s): 4 new, 2 at the same start and end as a clip from before. Replaced 5 clip(s) from before that nobody had used (4 removed, 1 made again with the new settings). Kept 4: 1 16:9 Longform, 2 edited, 1 published or sent to publish.
      Make clips again: this run made no clips, so the 9 clip(s) from before are all kept as they were
      Make clips again: this run failed, so nothing was replaced and the 9 clip(s) from before are all kept
      Make clips again: this run was cancelled, so nothing was replaced and the 9 clip(s) from before are all kept
      Make clips again: 1 of the 7 clip(s) this run set out to make couldn't be made, so nothing was replaced: the 9 clip(s) from before are all kept, and the 4 new clip(s) it made are added beside them
      Make clips again: the clips from before couldn't be replaced (<why>), so they're all kept
      Make clips again: 2 clip(s) from before that nobody had used were removed, then the rest couldn't be (<why>), so they're kept
      Make clips again: 3 clip(s) from before that nobody had used were replaced (2 removed, 1 made again), then the rest couldn't be (<why>), so they're kept
```

In the first line, "new" is the run's clips that are new rows, which is what
the pipeline's own `Done in ... (N clips)` line just before counts; the
others are at a window a clip from before already had (each one made again
in place, or dropped when that clip is kept as it is).

"failed" covers a job that stopped at a check before its run started (no
AI model, for example) as well as a run that failed partway. "was
cancelled" covers a Cancel pressed after the job started and before the
first clip from before changed, the ones the run's own fresh start cleared
included (A Cancel). The last two lines are a failure partway through the
replacing: what went before it went whole (row, file and draft).

These lines can come too:

```
      Make clips again: waiting for this video's clips to finish being published, exported or edited
      Make clips again: 1 of the kept clip(s), made before this update, were found again at the same start and end, so their video was made again with the new settings; their title, description and hashtags stay
      Make clips again: the new video of 1 clip(s) was gone by the end of the run, so they keep their own
      Make clips again: this video's own copy was put back, as the run had deleted it
```

The first as the job starts, while one of the video's clips is in use (The
files of the clips from before, 1); the second and third after the first
line above: for clips with no fingerprint the run made again in place
(counted kept, `made before this update or by another version`, as `GET`
counted them), and for a clip to make again whose new file was gone (it is
counted `whose new video was gone` under Kept); the last once a run of the
video's own copy is over (The video's own copy).

A file that couldn't be settled once the run was over (The files of the
clips from before) adds one line, and stays under its `.clip-<id>` name,
where its clip's row points:

```
      (Make clips again: clip_00010-00070.clip-17.mp4 stays where it is: <why>)
```

A job that couldn't put a file aside fails before its run with "A clip from
before couldn't be kept safe while its video is made again (<why>), so
nothing was made again", and its log has no "Make clips again:" line. So
does one whose clips stayed in use ("Clips Kitty was still working with one
of this video's clips ...", after the waiting line), and one that couldn't
read the video's own copy ("Clips Kitty couldn't read this video's own copy
just now ..."). A Cancel while the job waits for its clips ends it
`cancelled` with the "was cancelled" line.

In the first line, "Kept" counts each kept clip under its first reason, in
the plain words: `16:9 Longform`, `story reel`, `edited`, `published or sent
to publish`, `scheduled`, `exported or starred`, `translated`, `in a
campaign`, `chosen by a watched channel`, `being edited, exported or
published`, `made before this update or by another version`, `couldn't tell
if it was used`, and `whose new video was gone` (the log only: never in
`GET`'s counts).

## What the job says it did

A Make clips again that finished keeps what it did on its job row, in the
new `jobs.result` column (JSON text, `''` for every other job), from
`core/remake.py summary()`:

```json
{"made": 15, "new": 15, "again": 0, "removed": 7, "replaced": 7, "kept": 2, "unchanged": 2}
```

- `made`: the 9:16 clips the run made; `new`: those that are new clips.
- `again`: clips from before made again in place with the new settings (one
  nobody used, or one with no fingerprint whose window was found again);
  `removed`: clips from before deleted.
- `replaced` and `kept`: as `GET`'s `counts` counted them (a clip with no
  fingerprint made again in place is kept, and in `again`).
- `unchanged`: clips from before left exactly as they were. Absent, with
  `"error": true`, when the replacing stopped partway (the last two log lines
  in the list above).
- `renders_failed`: present when a clip the run set out to make couldn't be
  made; then nothing was replaced, and `again` and `removed` are 0.

A job that failed or was cancelled has none (nothing changed). It reaches the
app three ways: `GET /queue` gives a finished Make clips again's row a parsed
`result` (no such key on any other row), its `done` event carries it (Events),
and `GET /jobs` and `GET /jobs/{job_id}` show the column as it is stored
(JSON text, `''` for the others).

The app says it in one line, `lib/remake.ts remakeResultText`: under the
job's "Finished in" on the Queue page, and in the Library, under the video's
actions, when the open video's run ends (until another video is opened or it
is made again):

- "Make clips again: No new clips this time, so your clips are kept as they
  were." when the run changed nothing (no new clip, none made again, none
  removed; "No new clips this time." when the video had no clips left).
  With its clips from before still there, it would otherwise look as if
  nothing had run (black-box S3).
- Otherwise the counts that aren't 0, for example "Make clips again: 15 new ·
  7 removed · 2 kept as they were" or "1 made again with the new settings",
  led by "Some clips couldn't be made, so nothing was replaced" after a
  failed render, and ending "the rest kept, as they couldn't be replaced"
  after `error`.

## The queued job

It is a normal process job, so the Queue page shows it, its progress and its
settings (`settings.replace_untouched: true`, `settings.force: true`).
`PATCH /jobs/{id}` keeps the flag while it waits (the Queue page's settings
editor can still change its options); `{"clear": ["replace_untouched"]}`
turns it into Process again. **Retry** after a failure or a cancel keeps it.
A watched channel's own retries never touch it: it isn't a watch item's job.

Only `POST /videos/{video_id}/remake` queues a job with the flag. `POST
/jobs`, `POST /jobs/batch`, `POST /videos/local` and `PATCH /jobs/{id}` have
no such field, so one sent to them is ignored (`PATCH` can't turn it on; a
waiting Make clips again keeps its own unless `clear` names it, above). A
job queued without a person pasting it, a watched channel's (whatever its
saved options hold) or a streaming tool's, has it dropped (`core/queue.py
enqueue_once`).

## Clips gain `edited_at` and `media_version`

Clip JSON (`GET /videos/{video_id}/clips`, `PATCH /clips/{id}`) carries
`edited_at`: an ISO time, or `""`. Set by `PATCH /clips/{id}` when the title,
description or hashtags change (saving the same words again doesn't), by
every route that queues a render as it queues it (`StateDB.mark_edited`, so
a render that fails leaves it set), and on the new row of every re-render.
Nothing that makes or renders clips reads it, and no screen shows it.

It also carries `media_version`: a short string made from the clip file's
modification time and size (`server/api.py _media_version`), `""` when the
clip has no file. It changes whenever the file does. The app puts it on the
clip's video URL (`lib/api.ts mediaUrl(clip.id, clip.media_version)`:
`/media/<id>?v=<media_version>`), which the engine ignores, so a player
loads a file written again in place: a clip Make clips again made again in
place, or an end card added to a file later. `GET /media/{clip_id}` already
answers `Cache-Control: no-store`. Both fields are additions to a stable
route's JSON; nothing was removed.

The `clips` table also gains `as_made` and `first_exported_at` (Kept
because), which clip JSON leaves out (`server/api.py _clip_json`): they are
Make clips again's own. The star stays `exported_at`.

## Edits that follow a moved clip

`render_opts.edit` (keep, mutes, muted_words) counts from the clip's start. A
render job that moves the start or end (`POST /clips/{id}/render` with
`start`/`end`, the AI chat's new start or end, a render before publishing)
now carries it with `video_editor/timeline.py shift_edit`, so each cut and
mute stays on the same moment of the video:

- what the clip gains plays: a start moved earlier adds the new head, an end
  moved later the new tail;
- what it loses is cut away; mutes and muted words outside it go;
- with no kept part left inside it, keep goes and the whole clip plays (the
  job log says so);
- volume, mute all, fades, speed, the hook and music are unchanged.

An `edit` sent **with** a new `start`/`end` is read as the clip as saved (the
editor works on the saved clip) and carried the same way. An `edit` sent
without one (the timeline editor's Apply) is rendered as sent. Nothing
changes for the UI: `ClipEditor.tsx` keeps sending a range alone.

The other clip-relative times in `render_opts`, and what a moved clip does
with each:

| Key | Carried by |
|---|---|
| `edit.keep`, `edit.mutes`, `edit.muted_words` | `shift_edit` (new) |
| `caption_lines` | `video/captions.py refit_caption_lines` (#120): moved, and the gained seconds captioned |
| `speaker_edits` | `video/captions.py shift_edits`: moved, never cut, so a fix comes back when the clip grows again |
| `sport_moment.t` | worked out again from the clip's scores, else `core/pipeline.py _shift_sport_moment`; dropped while the play is outside the clip |
| `stop_by` | worked out again from the clip's scores |
| `speaker_turns` | heard again at every render; the render's own are saved |
| `edit.hook.seconds`, `edit.fade_in`, `edit.fade_out` | left alone on purpose: lengths from the clip's start and end, not moments |
| `scores.plugin_edits` (`window`, `edit.cuts`, `applied`) | left alone: seconds of the video, not of the clip. `plugins/edit_marks.py` reads the saved edit at the clip's window, so a used suggestion's cut is still found after the move (it used to be marked hidden) |

Everything else in `render_opts` (`crop`, `captions`, `job_captions`,
`caption_style`, `filter`, `adjust`, `watermark`, `gaming`, `framing`,
`normalize_audio`, `podcast`, `vertical_live`, `sport`, `sport_screen`,
`profile`, `whole`, `reel`) holds no time.

Outside `render_opts`, a clip's saved translations (`clip_translations.lines`)
count from its start too. They are not carried when the start moves, as
before this step: a translation of a clip whose start moved is timed for
the old start until it is translated again.

## TypeScript shapes (for `lib/types.ts`)

```ts
export type RemakeReason =
  | 'longform' | 'story_reel' | 'edited' | 'published' | 'scheduled' | 'exported'
  | 'translated' | 'submitted' | 'chosen' | 'busy' | 'older' | 'unsure'

export type RemakeOptions = Omit<JobOptions, 'force' | 'longform'> & { hashtags?: string[] }

export interface RemakePreview {
  video_id: string
  options: RemakeOptions
  from_job: number | null
  source: { kind: 'file' | 'link'; downloaded: boolean; link: string; usable: boolean }
  counts: { total: number; replaced: number; kept: number; kept_because: Partial<Record<RemakeReason, number>> }
  clips: { id: number; start_s: number; end_s: number; replace: boolean; reasons: RemakeReason[] }[]
  queued_job: { job_id: number; status: 'queued' | 'running' } | null
  blocked: 'running' | 'queued' | 'source_gone' | 'busy' | 'queue_full' | null
}

// What the job says it did (core/remake.py summary). QueueJob and
// StudioEvent gain `result?: RemakeResult`.
export interface RemakeResult {
  made?: number; new?: number; again?: number; removed: number
  replaced?: number; kept?: number; unchanged?: number
  renders_failed?: number; error?: boolean
}

// lib/api.ts
//   remakePreview: (videoId: string) => request<RemakePreview>(`/videos/${encodeURIComponent(videoId)}/remake`)
//   remakeVideo: (videoId: string, options: RemakeOptions) =>
//     request<{ job_id: number; video_id: string }>(`/videos/${encodeURIComponent(videoId)}/remake`,
//       { method: 'POST', body: JSON.stringify(options) })
//   mediaUrl: (clipId: number, version?: string) =>
//     version ? `${API_BASE}/media/${clipId}?v=${encodeURIComponent(version)}` : `${API_BASE}/media/${clipId}`

// Clip gains:
//   edited_at: string        // '' or an ISO time
//   media_version?: string   // changes with its file; '' with none
```

## Not in this step

- Project clips (the new editor's, from step 5) don't exist yet; how a
  remake treats them is decided with them.
- How clips are picked is unchanged, and so is Add videos (its Process again
  and its words).
