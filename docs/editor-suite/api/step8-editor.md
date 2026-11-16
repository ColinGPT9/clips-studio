# Step 8: the new editor (behind "New editor (beta)")

Status: **built, waiting for Colin's click-through.** Part A (the engine work the editor needs, and this contract), part B (the switch, the ways in, the Projects list, the editor's frame and the viewer) and part C (the timeline, the inspector, the media bin, the export dialog, today's editor's question before it renders over a new-editor export, and "Clear editor cache") are done. See [the contract](#the-contract) and [the UI's structure](#the-uis-structure).

With the switch off, nothing a person sees changes, and a plain auto-clipping run and today's Apply render exactly as before. The one engine change a person can meet without the switch: today's editor asks before it re-renders a clip whose file is a new-editor export ([7-D5](#a-clip-whose-file-is-a-project-export-7-d5)), and such a clip only exists once the new editor exported it.

Contents: [The contract](#the-contract) · [Projects](#projects) · [Editing](#editing) · [Media](#media) · [The viewer: stills and the draft](#the-viewer-stills-and-the-draft) · [Exporting](#exporting) · [Events](#events) · [Protection and the Origin](#protection-and-the-origin) · [Errors](#errors) · [Part A: what changed in the engine](#part-a-what-changed-in-the-engine) · [Part C: what changed in the engine](#part-c-what-changed-in-the-engine) · [Choices made](#choices-made) · [The UI's structure](#the-uis-structure) · [Measured in the sandbox (part C)](#measured-in-the-sandbox-part-c) · [Left for later](#left-for-later) · [Tests](#tests)

## The contract

Every route the editor calls, on `http://127.0.0.1:8765`. The document, its operations and their fields are step 5's ([step5-project-core.md](step5-project-core.md)); media ids, tokens and formats are step 6's ([step6-media.md](step6-media.md)); the export, draft and still are step 7's ([step7-compiler.md](step7-compiler.md)); presets are step 1's ([step1-export.md](step1-export.md)). This page says what the editor sends and what it gets back, and what part A added.

"Header" means the `X-Clips-Kitty-Session` header (`lib/api.ts` `sessionHeaders()`). "Token" means the `?t=` already inside an address the engine gave.

### Projects

| Route | Who | Body / query | Answer |
|---|---|---|---|
| `GET /projects` | header | `?limit=` (1 to 500, default 500) | **New in step 8.** `{projects: [summary]}`, the last changed first. 400 for another limit |
| `POST /clips/{clip_id}/project` | header | none, or `{start_again: true, expected_revision}` | The clip's project (`created: true` the first time, `false` after: a clip has one project, so "Edit in new editor" reopens the same one). The project shape below, plus `clip_changed_since` and `started_again` (QC fix 8-D6, [below](#a-clip-changed-in-todays-editor-after-its-project-8-d6)). 404 no such clip; 400 a clip that can't be opened as a project, or `start_again` without `expected_revision`; 409 `start_again` at another revision |
| `POST /projects` | header | `{name?, canvas?, fps?, media?, insert?}` | A new project (shape below, with `created: true`). See [a project made by hand](#a-project-made-by-hand-in-one-call) |
| `GET /projects/{id}` | header | | The project |
| `DELETE /projects/{id}` | header | | `{deleted: id}`. The clip, the media and the exported files stay |

**A project** (`GET /projects/{id}` and the answers above):

```json
{"id": "9f0c…", "name": "Friday stream", "clip_id": 42, "video_id": "vid1", "revision": 7,
 "can_undo": true, "can_redo": false, "read_only": false,
 "created_at": "2026-11-14T09:00:00", "updated_at": "2026-11-14T09:05:00", "document": {"…": "step 5"}}
```

- `clip_id` is null for a project made by hand, and for one whose clip was deleted.
- `revision` is what every change sends back as `expected_revision`.
- `read_only`: saved by a newer version. Every change answers 423.

**A summary** (`GET /projects`), no document:

| Field | |
|---|---|
| `id`, `name`, `video_id`, `revision`, `created_at`, `updated_at`, `read_only` | as the project |
| `clip_id` | the clip, or null |
| `origin` | `"clip"` (made from a clip) or `"manual"` |
| `clip_deleted` | true for a project made from a clip that is gone. It still opens, changes and exports; the list says so plainly |
| `clip_file_is_export` | the clip's file is now this project's export (Library: show that the clip was edited in the new editor) |
| `canvas` | `{width, height, fps: {num, den}}`; null when damaged |
| `duration_us` | the length of the video (V1's end) |
| `items` | how many items V1 holds |
| `damaged` | the saved project can't be read: list it, offer to delete it, don't open it (`GET` answers 500) |

#### A project made by hand, in one call

`POST /projects` takes, besides step 5's `name`, `canvas` (`"9:16"` default, `"16:9"`, `"1:1"`, `"4:5"` or `{width, height}`) and `fps`:

| Field | |
|---|---|
| `media` | `"video:<video id>"` (a video of the Library: "Edit whole video") or `"asset:<sha256>"` (a file from `POST /media/import`: "Import a video to edit") |
| `insert` | `true` (default): the whole media goes on V1, framed `{"fit": "fit", "align": "center"}` (a blurred pad fills the rest of the canvas). `false`: the media is only in the project's media (the bin) |

- Without `fps`, the project takes the video's own frame rate when it is known (else 30). Without `name`, the media's name (the video's title, the imported file's name).
- The project starts with the media in it, at revision 0: undo never takes it away.
- No AI run: the item is a manual item (framing `fit`), so the export frames it by the document.
- `video:<id>` projects pin their video (step 6): the app's clean-ups never delete its download.
- 400: `media` that is neither form, a video not in the Library, an asset never imported, a video whose length isn't known yet; the sentence says which. Nothing is made.

### Editing

| Route | Body | Answer |
|---|---|---|
| `POST /projects/{id}/ops` | `{expected_revision, ops: [op], dry_run?, label?}` | `{id, revision, changed, dry_run, document, inverse, warnings, created, can_undo, can_redo}` |
| `POST /projects/{id}/undo` | `{expected_revision}` | `{id, revision, changed, document, label, can_undo, can_redo}` |
| `POST /projects/{id}/redo` | `{expected_revision}` | the same |
| `GET /projects/{id}/revisions` | `?limit=` | the history, newest first (step 5) |

- Every change goes through `ops` (step 5's table of about 20 operations; `ops.describe()` lists them with their fields).
- **409** `{detail: {message, current_revision}}`: someone (another window, the AI, MCP) changed the project first. Reload the project (`GET /projects/{id}`) and say so in plain words; nothing was changed.
- **A drag is one undo step:** send each small change of the same property of the same item (`set_item_props`, `set_text`, `set_track_props`, `set_project_props`, `set_canvas`, `set_captions` without lines, `set_branding`), alone in its call, within a second of the last: step 5 joins them into one revision (the revision number still goes up each time). Moves and trims (`move_item`, `trim_item`) are not joined: send the drag's end only, or batch.
- `warnings` are plain sentences to show (a fade shortened, the frame grid, a fade that moved).
- `created` gives the new ids (`{kind: "item" | "media" | "track", id}`), for selecting what was just made.
- **Deleting V1's first or last item** (`delete_items`, step 8): the video's fade in or fade out moves to the item that is first or last now, unless it has a fade of its own there, with a warning. One undo step takes both back.

The operations the editor uses, with step 5's fields: `set_canvas`, `set_project_props`, `add_media`, `insert_clip` (V1: `index`; V2 and up: `start_us`), `trim_item` (`source_in_us`/`source_out_us` for video; `start_us`/`end_us` for text and pictures), `split_item` (`at_us`), `delete_items` (ripple on V1), `move_item` (V1: `index`; others: `track_id`, `start_us`), `duplicate_items`, `set_item_props` (`speed` 0.25 to 4, `volume` 0 to 2, `fades {in_us, out_us}`, `framing`, `transform {x, y, scale, rotation, opacity}`, `color {filter, adjust}`), `add_track`, `remove_track`, `set_track_props`, `move_track`, `batch`.

**Framing** (`set_item_props.framing`): `{"crop": …}` or `{"gaming": …}` is an AI item's kept framing ("AI framing"; the inspector shows no crop numbers for it). `{"fit": "fit" | "fill", "align": "center" | "top" | "bottom" | "left" | "right", "pad": "blur" | "#RRGGBB"}` is a manual framing. "Reset to AI framing" sets the item's framing back to the one the imported clip had: `document.context.imported.render_opts` holds the clip's `crop` and `gaming`; the item's own `framing` before the change is also in the inverse of the change that replaced it.

**Times** are whole microseconds (`*_us`). V1 edges are on the canvas's frame grid (`frame n` starts at `floor(n * 1e6 * den / num)`, step 5 `model.frame_us`).

### Media

The editor names media by id, never by path. For a project's own media use `project:<project id>:<media id>` (step 6), which follows the document.

| Route | Who | Answer |
|---|---|---|
| `GET /media/{id}/info` | header or token | What it is (`kind`, `duration_us`, `width`, `height`, `display_width`, `display_height`, `rotation`, `fps`, `has_audio`, `start_us`, codecs), `needs_proxy` and `proxy_reasons`, the states of `proxy`, `peaks`, `thumbs`, and `urls: {source, proxy, peaks, thumbs}` (each with its token) and `token`. 404 missing (the sentence names what is missing), 422 not readable |
| `GET`/`HEAD /media/source/{id}` | header or token | The file in byte ranges, for `<video src>` (use `urls.source`) |
| `POST /media/{id}/proxy` | header | Ask for the 540p proxy when `needs_proxy` (202 while made, 200 ready). `GET /media/{id}/proxy` (`urls.proxy`) serves it when ready |
| `POST /media/{id}/proxy/cancel` | header | Stop it |
| `GET /media/{id}/peaks` | header or token | `?per_second=64&start_us=&end_us=&format=bin&wait=&retry=` The waveform (step 6's `CKPK` format). 202 `{state, job_id, fraction}` while it is made (ask again, or `wait=` up to 60 s); 404 for media with no sound; 500 `{state: "failed", error}` until `retry=true` |
| `GET /media/{id}/thumbs` | header or token | The thumbnail index: `{interval_us, tile: {width, height}, columns, rows, count, times_us, sheets: [{index, url, first, count, width, height}]}`. 202 while made. Tile i: sheet `i // 100`, column `(i % 100) % 10`, row `(i % 100) // 10` |
| `GET /media/{id}/thumbs/{n}` | token | Sheet n, a JPEG (use the sheet's `url`) |
| `POST /media/import` | header | `{path, name?}`: a file picked on this PC (the main process's file picker gives the path). 200 the asset `{id: "asset:<sha256>", sha256, kind, name, ext, size, duration_us, width, height, fps, has_audio}`, or 202 `{job}` for a file over 64 MB (then `GET /media/import/{job_id}` or the `media_job` events; the asset is in `result` when `done`). 400/404 with a plain sentence |
| `GET /media/cache/size` | header | `{folders, files, bytes, cap_bytes}` |
| `POST /media/cache/clear` | header | The "Clear editor cache" button: `{files_removed, bytes_freed, kept_in_use}` |

- **A proxy** is needed for HEVC, ProRes, 10-bit, Matroska and anything above 1080p60 (step 6's rule). Play the source when `needs_proxy` is false, else the proxy once its state is `ready`; until then the stills show the frame.
- **A source whose first timestamp isn't 0** (`start_us` in the info): the proxy and the thumbnail times start at 0; the source's own `<video>` time is `start_us` later.
- **Part A:** a waveform, thumbnail or proxy job asked for before "Clear editor cache" now makes its folder again and works (it failed before); its error, when there is one, names no path. Deleting a video stops its waiting or running editor jobs first.

### The viewer: stills and the draft

| Route | Who | Answer |
|---|---|---|
| `GET /projects/{id}/still?t_us=&rev=` | header or the project's token | A JPEG of the program's frame at `t_us`, every layer drawn as the export draws it. Headers: `X-Clips-Kitty-Still-Time` (the frame's start, µs) and, when an AI item had no tracking kept, `X-Clips-Kitty-Still-Note: approximate`. 400 negative time or an empty V1; 404; **409** another revision than the current one (`{message, current_revision}`) or **superseded** (`{message, superseded: true}`, below); 423 |
| `POST /projects/{id}/draft` | header | `{revision?}`. `{job, revision, draft, urls}`: `draft: {revision, bytes, url}` when it is made already, else `job` (a lane task, `kind: "project_draft"`) whose progress comes as `media_job` events. Asking again while it is made gives the same job |
| `GET /projects/{id}/draft?rev=` | header or the project's token | The draft file in byte ranges (`<video src>`: the `url` above). 404 until it is made |

- **The addresses:** `POST /projects/{id}/export` and `POST /projects/{id}/draft` answer `urls: {still: "/projects/<id>/still?rev=<r>&t=<token>&t_us=", draft: "/projects/<id>/draft?rev=<r>&t=<token>"}`; append the time in µs to `still`. A `fetch()` with the header works as well, and lets the editor read the two headers.
- **Only the current revision** has a still or a draft: after every change, use the new revision's address.
- **One still at a time (part A).** A project makes one still's FFmpeg at a time. A newer request stops the one being made, and requests still waiting for their turn give up: each answers **409 `{detail: {message, superseded: true}}`**. The editor sends one still request at a time while scrubbing (aborting the older `fetch`), and ignores a superseded answer. A still already made (kept per revision, settings and frame; the newest 200 per project) comes back at once.
- **The draft** is 540 on the short side, x264 ultrafast, no end card, no export preset: "Preview" plays it.

### Exporting

| Route | Who | Body | Answer |
|---|---|---|---|
| `GET /export/presets` | none | | Step 1: `{default, original, custom, choices, limits}` |
| `GET /export/codecs` | none | | What this PC can encode: `{codecs: [{codec, …}]}` (the first call probes, under a second) |
| `POST /export/estimate` | none | `{preset, seconds}` | Step 1's size estimate for a video `seconds` long: send the project's `duration_us / 1e6` (plus the end card's length when it is on). `available` is false when this PC can't encode the preset |
| `POST /projects/{id}/export` | header | `{revision?, preset?, replace_clip_changes?, folder?}` | `{export, branch, reasons, project_export_id, revision, notes, urls}`. `export` is the lane job (fields below). `folder` (UX fix round, ED46): the person's export folder from Settings, as clip exports send it. Used only for a project with **no clip** (made by hand, or whose clip was deleted): its file goes into that folder as `<project name> - <YYYY-MM-DD>.mp4` (local date; the name made safe for Windows, a video extension at its end left out, an empty name "Project", a copy beside an existing file as `… -2.mp4`). Ignored for a clip's project (it replaces the clip's file in `data/clips/projects/`, ED28) and when it is empty or not an absolute path (the file then stays `data/clips/projects/<id>-r<revision>.mp4`). If the folder can't be made, the file goes to `data/clips/projects/`. `preset`: a step 1 preset, `"original"`/null (the clip's own encode), `"default"` (Settings → Export's), absent (the project's own `export.preset`). 400 a bad preset or a plan that can't be made (an empty V1, AV1 without a graphics card), 404 a missing source (the sentence), 409 another revision, or `{message, reason: "clip_changed", clip_id}` when the clip was changed in today's editor since (8-D6; `replace_clip_changes: true` exports anyway), 423 |
| `GET /export/jobs/{export_id}` | none | | The job, as the events give it |
| `POST /export/jobs/{export_id}/cancel` | header, and an Origin of this PC (8-D9) | | Cancel: a waiting export is dropped, a running one's FFmpeg stopped; the clip, the project and every file stay as they were |
| `GET /projects/{id}/exports` | header | | `{exports: [row]}`, newest first |
| `GET /projects/{id}/exports/{row}/file` | header or the project's token | | **New in part C.** A finished export's file in byte ranges (Range, ETag), for the dialog's Play: use the row's `url`. 404 for a row of another project, a row not `done`, a file that is gone, or a path that is not a video (`.mp4`, `.mov`, `.m4v`, `.mkv`, `.webm`) |

**The export job** (`export` above, and the `export` events): `export_id`, `kind: "project_export"`, `status` (`queued`, `running`, `done`, `failed`, `cancelled`), `cancelling`, `fraction` (0 to 1), `stage` (`cut-001`, `frame-001`, `final`, …: show progress, not the stage's name), `pass`/`passes`, `smaller` (made again under a size target), `result` (`{path, file, folder, bytes, duration_us, clip_id?}` when done; `folder` is the folder the file is in), `error` (a sentence when failed), `project_id`, `revision`, `branch`, `project_export_id`.

**An exports row** (`GET /projects/{id}/exports`): `id`, `project_id`, `head` (the revision exported), `clip_id`, `path`, `preset` (JSON, null for Original), `branch`, `status`, `error`, `duration_us`, `bytes`, `job_id`, `created_at`, `updated_at`, and, **new in part A**, `file_exists`, and, **new in part C**, `url` (the file's address with the project's token; null unless `status` is `done` and `file_exists`).

- `status` is `queued`, `running`, `done`, `failed`, `cancelled`, `interrupted` (the app closed mid-export) or, **new in part A**, `replaced`: a later export of the project replaced this export's file. Since QC fix 8-D4 a `done` row also becomes `replaced` when its path now holds another file (its size or modification time differ from those recorded when it finished, `bytes` and `file_mtime_ns`): today's Apply writes a clip's file in place, and that file is a Branch A export's. Paths are compared as the system compares them (case on Windows), not as text.
- Offer "Play" and "Show in folder" only for `status: "done"` with `file_exists: true` (the dialog keys both on `url`). "Show in folder" goes through the desktop app (`window.studio.showInFolder(path)`, part C), which takes only an existing video file and only shows it; the browser build has no such call, so the button is absent there. A `done` row whose file is gone (deleted by hand, or replaced by today's Apply) shows as gone; a `replaced` row says a later export replaced it.
- **The branch is never shown.** A project made from a clip replaces that clip's file when exported: the clip keeps its title and its posts, and the Library shows the new file. The dialog's tooltip says so in plain words.
- **`clip_file_replaced` event** `{type: "clip_file_replaced", clip_id, project_id}`: refresh the Library.
- **Captions** (burn or not, a `.srt` file) are step 9's: the export request has no such fields yet.

**The end pad (part A):** an export of a clip's project whose V1 is still the clip's one stretch (no cut, one speed) ending where the clip ends runs 0.4 s past that end, as Apply renders the clip (`compose.end_pad_us`, [choice 1](#choices-made)). The draft is the export's program, so it is that much longer too (and its AI runs are tracked once for both); the editor's timeline and the stills show the document's length, as today's clip is longer than its window.

### Events

One WebSocket, `ws://127.0.0.1:8765/ws`, every event as JSON:

| `type` | Fields | |
|---|---|---|
| `export` | `export`: the export job | each change of an export's state or progress (step 1's events; project exports are among them) |
| `media_job` | `job`: `{job_id, kind, key, name, status, cancelling, fraction, result, error, created_at, started_at, finished_at, stage?, project_id?, revision?}` | a proxy, waveform, thumbnails, import (`kind: "import"`) or draft (`kind: "project_draft"`) task |
| `clip_file_replaced` | `clip_id`, `project_id` | a project export replaced a clip's file |

### Protection and the Origin

- **Changing routes** (and reading a project) need the header and an Origin of this PC's own (`projects_api.local_origin`): no Origin, `null`, `file://`, the dev server (`http://localhost:5173`, `http://127.0.0.1:5173`) or the engine's own address. Any other `http(s)` origin is 403.
- **Read routes** used by `<video>` and `<img>` take the token in the address instead of the header.
- **What the packaged window sends (measured in part A):** Electron 31.7.7 (the version `ui/package.json` pins) running a window made as `ui/src/main/index.ts` makes it (`contextIsolation: true`, `nodeIntegration: false`, `webSecurity` as default) and loaded with `loadFile`, against a stand-in server with `server/api.py`'s own CORS settings on another port, sent:
  - **no `Origin` header at all**, for a `fetch()` with and without the session header, for a `POST`, and for an `<img>`;
  - **no CORS preflight** (`OPTIONS`) even with the custom header, and the page read every answer and its headers (the still's two headers included) whether or not they were exposed. `location.origin` was `file://`.
  So the packaged editor's requests pass the Origin check as "no Origin", and CORS doesn't apply to them. Nothing in `ui/src/main` or the Electron config was changed. The measuring scripts were scratch work, not committed; `tests/test_step8_routes.py` holds the routes to it (no Origin, `null` and `file://` let in).
- **The dev server** (`npm run dev`, origin `http://localhost:5173`) is a real cross-origin page: CORS applies, and part A adds `expose_headers` for `X-Clips-Kitty-Still-Time` and `X-Clips-Kitty-Still-Note` (`server/api.py`), so the editor can read them there too.
- The CSP (`ui/src/renderer/index.html`) already allows `connect-src`, `media-src` and `img-src` for `http://127.0.0.1:8765`. JASSUB's `wasm-unsafe-eval` and `worker-src blob:` are step 9's.

### Errors

| Status | When | `detail` |
|---|---|---|
| 400 | an operation refused | `{message, op, field}` |
| 400 | anything else refused | a sentence |
| 403 | no header (or token), a foreign Origin | a sentence |
| 404 | no such project, clip, media; a missing file | a sentence naming what is missing |
| 409 | the project moved on | `{message, current_revision}` |
| 409 | a still superseded by a newer request | `{message, superseded: true}` |
| 409 | today's editor rendering over a project export | `{message, reason: "project_export", project_id}` ([below](#a-clip-whose-file-is-a-project-export-7-d5)) |
| 409 | new files (a translation, a dub, subtitle files) from a clip whose file is a project export | `{message, reason: "project_export", separate_file: true, clip_ids, count, total, project_id}` ([8-D13](#everything-made-from-a-clips-own-window-8-d13)) |
| 409 | exporting a clip's project after the clip was changed in today's editor | `{message, reason: "clip_changed", clip_id}` (8-D6) |
| 422 | a body of the wrong shape; media FFprobe can't read | FastAPI's own; a sentence |
| 423 | a project saved by a newer version | a sentence |
| 500 | a damaged project; a still that failed | a sentence |

## Part A: what changed in the engine

### Step 6's small faults

Each bullet of step6-media.md's "Small faults from the last check", and what was done:

| Fault | Done | Test |
|---|---|---|
| A job queued during Clear fails, and its error shows the cache path | The proxy, waveform and thumbnails jobs make their folder (with its `source.json`) again when they start; an `OSError` becomes a plain sentence with no path (`media_api._plain_task`) | `test_step8_routes.py::test_a_waveform_job_queued_before_clear_still_works` |
| A junction inside the cache is followed by Clear and Free up | `cache.is_link` (a symbolic link, or any Windows reparse point) is never looked through: `_remove`, the sizes, the empty-folder sweep, the partials sweep and Storage's file list (`cache.walk_files`) remove the link itself only. A link among the cache's folders is never an orphan | `test_step8_engine.py::test_the_cache_never_reaches_through_a_link_or_junction`, `::test_storage_clean_up_leaves_a_junctions_target` (a real junction on Windows, a symbolic link elsewhere) |
| The peaks job hangs on a read error | `peaks.compute` kills FFmpeg before it waits for it | `::test_the_peaks_job_stops_ffmpeg_when_reading_fails` |
| A Range with thousands of digits answers 500 | Leading zeros are dropped and a number longer than 19 digits counts as larger than any file, without `int()` | `::test_a_range_with_thousands_of_digits_is_never_an_error` |
| If-Range by date | If-Range takes the ETag only; a date gets the whole file (RFC 9110 allows it; Chromium sends the ETag). `tests/test_media_fileio.py` changed with it | `::test_if_range_takes_the_etag_only` |
| An import of a file changed after its check | `store.add_asset(check=)` reads the copy before it is kept (`importing.checked_copy`), and records what was copied; a copy that no longer reads keeps nothing. The copy keeps the file's extension | `::test_an_import_records_what_was_copied`, `::test_an_import_whose_copy_no_longer_reads_keeps_nothing`, `::test_importing_a_real_file_reads_the_copy` |
| `video:VID1` serves `vid1`'s download on Windows | The download's name on disk must match exactly (`resolver._exact_name`, through `os.path.realpath`) | `::test_an_id_in_other_case_never_serves_another_videos_download` |
| Tampered rows: a clip row naming `state.db`, a hard link | `clip:` takes only a video by its extension (both the name given and the file it leads to), and a `clip:` or `file:` file with more than one name (a hard link) is refused | `::test_a_clip_row_naming_a_file_that_is_no_video_is_refused`, `::test_a_clip_row_naming_a_hard_link_is_refused` |
| A replaced download a project names keeps its old folder | A folder made from a file that was since replaced (the file is there, another size or time) is an orphan whether or not a project names it; an imported file's folder (named by its content) is not | `::test_a_folder_of_a_replaced_file_is_an_orphan_even_when_a_project_names_it` |
| FFmpeg holds the source while a lane job runs | `DELETE /videos/{id}` stops the video's waiting and running editor jobs first (`media_api.cancel_tasks_for`) | `test_step8_routes.py::test_deleting_a_video_stops_its_editor_tasks` |
| Make clips again puts back a pinned copy deleted by hand during the run | **Not fixed.** `DELETE /videos/{id}` already answers 409 while the video is processing, so this needs a delete outside the app during a run; the copy is put back only because a project still names the video, which is what the pin is for | |
| Thumbnails of a long source with keyframes over 30 s apart take 366 s | **Not fixed.** Only the every-frame fallback is slow, and no measured source (OBS, YouTube and Twitch downloads keep keyframes 2 to 5 s apart) reaches it; a faster fallback (seeking per tile) is a rework worth doing only when such a source shows up | |

### Step 7's small faults

| Fault | Done | Test |
|---|---|---|
| A long AI run is one command line (too long in a deep folder) and one FFmpeg of 64 inputs (2.2 GB) | An AI run is cut in pieces the way manual chunks are (at most 16 items, each command inside the 30,000-character budget: `compose._ai_pieces`), each piece's sound goes to A1, and the pieces are joined by stream copy (`join-NNN`, `concat()`'s flags) into the one stretch the frame stage tracks. A run that fits is one cut, as before | `test_step8_engine.py::test_a_long_ai_run_is_cut_in_pieces_and_joined_before_it_is_framed`, `::test_a_long_ai_run_in_a_deep_folder_stays_under_windows_limit`, `::test_a_short_ai_run_is_one_cut_as_before` |
| Two exports of one clip's project: the first row stays `done` with a missing file | `store.mark_replaced`: when an export finishes, earlier `done` rows whose file it replaced (the clip's old file, or the same name written again) become `replaced`; `GET .../exports` adds `file_exists` | `test_step8_routes.py::test_an_export_replaced_by_a_later_one_is_never_offered`, `::test_a_gone_file_is_shown_as_gone`; `test_step8_engine.py::test_an_export_a_later_one_replaced_is_marked` |
| Branch B is 0.4 s shorter than Apply | [Choice 1](#choices-made): Branch B's export carries Apply's end pad for an unchanged end | `::test_an_unchanged_clip_exports_as_long_as_apply_makes_it`, `::test_no_end_pad_where_apply_has_none`, `::test_the_end_pad_is_held_to_stop_by_and_the_videos_end`, `::test_the_end_pad_captions_nothing_new_and_music_fills_it` |
| A crash between moving the export and updating the clip leaves a file nothing names | The export's row records its file's path before the file moves there | `test_step8_routes.py::test_an_exports_row_names_its_file_before_the_file_moves` |
| Branch A's restore after a cancel fails while a player holds the file | **Not fixed.** The restore runs only when the re-render wrote the clip's file in place, so the file could be written a moment before; a program that took it without sharing in between is the only way to hit it. Moving the file aside would fail the same way (no share-delete), so there is no better write to make | |
| Stills: no limit on FFmpegs | One still's FFmpeg at a time per project; a newer request stops the running one and makes waiting ones give up (409 `superseded`) | `::test_a_newer_still_stops_the_one_being_made`, `::test_still_slots_run_one_at_a_time_and_older_waiting_ones_give_up` |
| Indentation in `core/framing.py` | Stays (the move stays literal) | |

**Plan snapshots that changed on purpose:** `tests/snapshots/compose_plans/ai_gaming_layout.json` and `landscape_canvas_ai_items.json`. Both are clips imported unchanged (100 to 110 s, no cut), so their exports now run 0.4 s longer: 300 → 312 frames at 30 fps, 480,000 → 499,200 samples, the last item's source out 110.0 → 110.4 s, its read and the stage seconds 0.4 s longer. Nothing else in them changed, and no other snapshot changed.

### The steps' other notes

- **CORS `expose_headers`** for the still's two headers (`server/api.py`): see [the Origin](#protection-and-the-origin).
- **Step 5's edge fades:** decided as [choice 3](#choices-made).
- **7-D5:** below.

#### A clip whose file is a project export (7-D5)

After a Branch B export of a clip's project, the clip's row keeps its window and render options while its file is the export. Today's editor's Apply (`POST /clips/{id}/render`) and chat edit (`POST /clips/{id}/ai-edit`) would render the old window again and replace the export with the clip as it was before the project's edits.

- **Now:** both answer **409** `{detail: {message, reason: "project_export", project_id}}` for such a clip, before anything is queued (and before the AI is asked). The message: "This clip was changed in the new editor. Applying changes here starts again from the clip as it was before, so those changes would be lost. Open it in the new editor to keep them."
- **The person can still choose it:** the same request with `"replace_project_edits": true` renders as before. Today's editor shows the message with two choices: "Open in new editor" (when the switch is on) and "Apply anyway" (sends the flag). That dialog is UI work (part B or C).
- **Every such route (QC fix 8-D1, 8-D2):** the same check (`server.api.project_edits_kept`) guards Apply (`POST /clips/{id}/render`), the chat edit (`POST /clips/{id}/ai-edit`), "Apply edits & upload" (`POST /clips/{id}/publish` with `render_first`; the flag is `render_first.replace_project_edits`) and `PUT /clips/{id}/captions` (`replace_project_edits` in its body; no screen calls it today, `api.saveCaptions` asks as Apply does). A publish without `render_first` renders nothing and is not asked.
- **How it is told:** `StateDB.project_export_of(clip_id)`: the clip's `path` equals a `project_exports` row of that clip with `branch = 'B'` and `status` `done` or `running`, **or** (8-D11) the clip's file is directly in `data/clips/projects/` (compared as the system compares paths; only Branch B writes there). Branch A exports are today's render (their render options are the project's), so they never ask.
- **A deleted project (8-D11):** deleting a project deletes its rows, but its export is still known by its file, so the routes still ask, with `project_id: null` and a message saying the project was deleted. `ProjectExportPrompt` then shows a sentence of its own and only "Apply anyway" and "Cancel". `DELETE /projects/{id}` first cancels that project's waiting and running exports, so a deleted project never replaces its clip's file afterwards. A render job asks again when it runs, under the clip's lock (one queued before the export landed, or retried after it): it fails with a plain sentence unless its payload carries `replace_project_edits: true`, which the four routes add only when the person chose "Apply anyway".
- **Unchanged:** a clip with no such export (every clip until the new editor exports one) renders exactly as before; the check is one SQL read. Make clips again already keeps a clip with a project.

#### Everything made from a clip's own window (8-D13)

The review found a fourth route of the same kind as Apply, the chat edit, the caption save and "Apply edits & upload": Export in other languages (`POST /translate`) made its burned and dubbed videos through `multilingual.burn.clean_base`, which renders the clip's **old** window and render options from the source, and took the caption timings of every language (subtitle files too) from the old window. The rule now, for the whole kind (ED45): **anything made from a clip's original window asks first when the clip's file is a new-editor export.** A result that replaces the clip's file asks with the existing question (`project_edits_kept`, "Apply anyway"); a result that is a separate file asks with its own (`project_edits_left_out`, "Make it anyway"); a run that only happens to find the clip's window again keeps the export.

Inventory (grep of `server/`, `core/`, `multilingual/`, `longform/`, `remote_render/`, `plugins/`, `campaigns/`, `stream_workflows/` and the MCP tools for `clean_base`, `cut_clip`, `_render_files`, `_rerender_clip`, `render_opts`, `start_s`/`end_s` and every `add_job`):

| What | Reads | Before this round | Now |
|---|---|---|---|
| Apply, `POST /clips/{id}/render` | window, render options; replaces the file | asks (7-D5) | unchanged |
| Chat edit, `POST /clips/{id}/ai-edit` | the same; replaces the file | asks (8-D1) | unchanged |
| Caption save, `PUT /clips/{id}/captions` | the same; replaces the file | asks (8-D2) | unchanged |
| "Apply edits & upload", `POST /clips/{id}/publish` with `render_first` | the same; replaces the file | asks (8-D2) | unchanged |
| The queue's `render` job (any of the four, queued before the export landed, or retried) | the same | asks again when it runs (8-D9) | unchanged |
| Plugin edit suggestions (used through Apply's `suggestions`) | the same, through Apply | asks (Apply's question) | unchanged |
| **Export in other languages, `POST /translate`, stage `export` with burned subtitles, a dub or subtitle files** | window, render options, caption lines; **separate files** | **not asked (8-D13)** | asks once per request: 409 `reason: "project_export"`, `separate_file: true`, `clip_ids` (the ones affected, in order), `count` of `total`, `project_id` (only when one clip is affected and its project exists). `"without_project_edits": true` ("Make it anyway") queues the job as before, with that flag in its payload. A batch is asked about once and never skips a clip |
| **The `translate` job** (queued before the export landed, or retried) | the same | **not asked** | asks again when it runs, before any AI is set up and before anything is written: fails with a plain sentence unless its payload has `without_project_edits` |
| `POST /translate` stage `translate` (text for review), and post text only | caption text; no video, no timings written | not asked | not asked: nothing is made from the window's video, and the reviewed text is used only by an export, which asks |
| `POST /translate` with only "include the video" | copies the clip's file (the export itself) | not asked | not asked: the copy is the export |
| **A forced run of the video** (Process anyway from Add videos, a retry, `queue_video` / `queue_local_file` with `force` from the MCP tools and the chat assistant, remote rendering's clips, Longform's runs): `core.pipeline._register_clip` re-points a clip whose window the run found again at its fresh render | the run's own render of that window | **replaced the export without a word** | keeps the clip on its export (`_keeps_project_export`); the fresh render stays on disk for the run (a story reel may join it) and Storage's unused clip files free it. Every other clip is re-pointed as before |
| **Story reels made again** (`_sport_reels`, a forced Sports run) | the run's clips | **replaced a reel's export** | kept, as above |
| Make clips again (`POST /videos/{id}/remake`) | a fresh run | keeps the clip (its project, or `edited_at` set by the export: 8-D11) | unchanged (a kept clip never receives the run's held-back change; with the pipeline rule none is held back for it) |
| Remote render dispatch (`remote_render/dispatch.py`) | the pipeline's new clips only | as the forced run | as the forced run |
| Highlights / Edited Stream / Longform assembly (`longform/`) | a run's own renders, through `_register_clip` | as the forced run | as the forced run |
| Tighten, `POST /clips/{id}/tighten` | the old window's words; proposes cuts, renders nothing | not a render | unchanged: its cuts reach the file only through Apply, which asks |
| Today's editor's preview, `POST /clips/{id}/preview` | the old window, into a preview file | not a result the person keeps | unchanged: Apply asks before anything replaces the export |
| Exports in a size (`POST /clips/{id}/export`, `/export/batch`), every publish (YouTube without `render_first`, WoopSocial, Upload-Post, schedules, publish plans), clipping campaigns, Discord, channel watch | the clip's file | use the export | unchanged |
| MCP tools and the chat assistant (`server/mcp.py`) | no tool re-renders a clip; `export_clip` copies the file | nothing reaches a clip's window | unchanged; any API caller of `POST /translate` gets the 409 with its reason and sends `without_project_edits` to go ahead |

Choices:
- **Ask, not burn onto the export.** Burning and dubbing onto the export itself would need the caption lines remapped through the project's timeline (cuts, speed, reorders, items from other sources); step 9 brings the project's own caption lane and its `.srt`. Until then the translated subtitle timings could only be the old window's, so the safe answer is the question. Revisit in step 9 (left for later).
- **A run that finds the window again keeps the export, without asking.** It is a new AI run, not a request about that clip: Make clips again already keeps every edited clip without asking, and a question at Process anyway would have to hold a whole queue. Keeping loses nothing; the person can still apply the run's version from today's editor (which asks).
- **When it asks:** stage `export` with `burn`, `dub` or `subtitles`. Not for the review stage or post text (no video and no timing is made), nor "include the video" alone (the copy is the export).

UI: `api.translateClips` goes through `newFilesRequest` (`lib/api.ts`), which sends the request again with `without_project_edits` only on "Make it anyway" (`sendAskingFirstForNewFiles`, `lib/projectExportPrompt.ts`). `ProjectExportPrompt` shows the separate-file question in words of its own through t(): "The new files are made from the clip as the AI made it, without the new editor's changes." (a batch: "Some of these clips were changed in the new editor", with "Clips changed in the new editor: n / total"), with "Open in new editor" (only with a project to open and the switch on), "Make it anyway" and "Cancel"; first focus is never on "Make it anyway". Cancel leaves "Nothing was made." in the panel, not an error.

#### A clip changed in today's editor after its project (8-D6)

Found by the review of round 1: "Edit in new editor" reopened a clip's project made before today's Apply changed the clip, and exporting it put the old window back and deleted Apply's file, with no question.

- **What is kept:** `projects.clip_basis`, a hash of the clip's video, window and render options (`importer.clip_basis`; the title is left out), and `projects.clip_basis_head`, the project's revision then. Written when the project is made, when it starts again from the clip, after a Branch A export (the clip is now the project at the revision that export rendered, not the head when it ended, so edits made while it waited or ran count as changes of the project's own: 8-D12) and after a Branch B export that pointed the clip at its file (the head is -1: the project holds more than the clip's settings say).
- **Opening (`POST /clips/{id}/project`):** the clip as it is now is compared with `clip_basis`. Unchanged: the project as before. Changed, and the project has no change of its own since (`head == clip_basis_head`): the project starts again from the clip by itself, one revision Undo takes back (`started_again: true`), keeping the project's own name; nothing of the person's is lost, and the editor says so as it opens ("Started again from the clip as it is now. Undo brings back your changes.", 8-D12). Changed, and the project has changes of its own: the project as it is, with `clip_changed_since: true`; the editor asks "Start again from the clip as it is now" (`{start_again: true, expected_revision}`) or "Keep the project".
- **Exporting:** a clip's project whose clip changed since is refused (409 `reason: "clip_changed"`) unless `replace_clip_changes: true`; the export dialog asks ("Export anyway"). After such an export the clip's settings as they are now become the basis, so the question is asked once.
- A project with no `clip_basis` (made before this) is never asked about; a project made by hand has none.

### Files

| File | Change |
|---|---|
| `video_editor/media/fileio.py` | Range numbers of any length; If-Range by ETag only |
| `video_editor/media/cache.py` | `is_link`, `walk_files`; nothing looked through a link or junction; a replaced source's folder is an orphan |
| `core/housekeeping.py` | Storage's list of the cache's orphan files through `walk_files` |
| `video_editor/media/peaks.py` | FFmpeg killed before the wait on an error |
| `video_editor/media/resolver.py` | exact download names; `clip:` only a video, no hard links (also `file:`) |
| `video_editor/media/importing.py`, `video_editor/project/store.py` | the copy read again before it is kept (`check=`); `mark_replaced`; `summaries` |
| `server/media_api.py` | jobs make their folder again; plain errors; `cancel_tasks_for` |
| `server/api.py` | `DELETE /videos` stops the video's editor jobs; CORS `expose_headers`; 7-D5's 409 and `replace_project_edits` on `RenderIn` and `AiEditIn` |
| `core/state.py` | `StateDB.project_export_of` |
| `video_editor/compose.py` | AI runs in pieces (`_ai_pieces`, `_join_stage`); the end pad (`end_pad_us`, `with_end_pad`; `build_ass(caption_doc=)`) |
| `server/compose_api.py` | `StillSlots`; the export row's path before the move; `mark_replaced` after an export; `file_exists` |
| `video_editor/project/ops.py` | `delete_items` moves V1's edge fades |
| `server/projects_api.py` | `GET /projects`; `POST /projects` with `media` and `insert` |
| `ui/src/renderer/src/pages/Settings.tsx` | Storage's line for `editor_cache`: "New editor files no project uses" (through `t()`; shown only once the new editor has left such files) |

## Part C: what changed in the engine

Part C is UI work; three small changes outside the renderer came with it.

| What | Where | Test |
|---|---|---|
| **A finished export's file** for the dialog's Play: `GET /projects/{id}/exports/{row}/file` (Range and ETag as step 6's sources; the session header or the project's token) and each row's `url`. Only a `done` row of that project whose file is there and is a video by its extension is served; nothing is taken from the request but the row's number | `server/compose_api.py` (`project_export_file`, `EXPORT_FILE_TYPES`) | `test_step8_routes.py::test_a_finished_export_plays_from_its_row_and_nothing_else_does`, `::test_an_export_row_naming_a_file_that_is_no_video_is_never_served`; `test_project_routes.py::test_the_routes_are_internal` lists it |
| **Matroska with H.264 and AAC plays without a proxy.** Measured in Electron 31.7.7 (Chrome 126.0.6478.234, the app's window settings, a `file://` page as the packaged app loads it): a 6 s H.264 + AAC `.mkv` played, decoded both streams and seeked to 4.5 s in 13 ms, as the same streams in `.mp4` did (14 ms). Step 6's rule ("a guess on the safe side, until step 8 tries it") is gone; 10-bit H.264, HEVC and other codecs in Matroska still get a proxy. The check ran Electron from the owner's checkout's `node_modules` (read only, its user data in scratch); the scripts are in `scratch/step8/mkv/`, not committed | `video_editor/media/probe.py` (`PLAYS`) | `test_media_probe.py::test_what_needs_a_proxy_and_why`, `test_media_routes.py` (an OBS-style `.mkv` needs no proxy) |
| **"Show in folder"** for the export dialog: an IPC call `show-in-folder` that takes a string that is an absolute path to an existing `.mp4`, `.mov`, `.m4v`, `.mkv` or `.webm` file and calls `shell.showItemInFolder` (never opens or runs it) | `ui/src/main/index.ts`, `ui/src/preload/index.ts`, `env.d.ts` | `test_ui_new_editor_part_c.py::test_show_in_folder_takes_only_an_existing_video_file`. **Not tried in a packaged app** (the sandbox is a browser) |

`docs/developers/api-reference.md` was generated again (305 routes; the new one is internal).

## Choices made

Part A, where the plan and the task were not specific:

1. **Branch B's export carries Apply's end pad for an unchanged end (ED34).** An imported clip doesn't carry its pad in its length (the document, the timeline and the stills stay the clip's window, and `projection(import(x)) == x` is untouched); instead, the export runs `END_PAD` (0.4 s, less when `stop_by` or the video's end says so) past V1's end when V1 is still one stretch of the clip's own video ending where the clip's window ends, which is exactly when Apply keeps its pad. The draft, being the export's program, runs as long, so one tracking pass serves both, and a still on the last item finds that tracking. Looped music that filled the program fills the pad; captions are mapped through the unpadded timeline, so the pad shows no new caption (Apply builds a clip's captions unpadded). Rejected: lengthening the imported item (the editor's timeline would disagree with the clip's window everywhere, and a trim would move the pad); giving the last item the tail without an end card (sound only: still 0.4 s shorter than Apply).
2. **Today's editor asks before rendering over a project export (7-D5, ED35):** a 409 with a reason and the project, and `replace_project_edits` to go ahead. Rejected: routing today's Apply through the project (two editors writing one clip's options and one project's document), and refusing outright (with the switch turned off, the person would have no way to change the clip).
3. **Deleting V1's first or last item moves the video's fade (ED36)**, in `delete_items` itself (one undo step, the same for the editor, the AI and MCP), only onto an item with no fade of its own there, with a warning. Rejected: a warning alone (the clip's fade would be lost on the first trim of its edges, which is the most common edit).
4. **A project made by hand comes from one call** (`POST /projects` with `media`): the media and, by default, the whole video on V1 framed fit and centred, made before the project is saved, so undo starts after it (ED37). Its frame rate is the video's own when known. Rejected: an empty project plus three operations from the UI (three undo steps that undo the project's reason to exist, and three round trips on open).
5. **The projects list is a summary without documents**, the last changed first, and a damaged project is listed, not hidden.
6. **If-Range takes only an ETag.** A date can't tell two files written in the same second apart.
7. **A newer still answers the older request with 409 `superseded`**, not 204 or 200 with no body, so the editor can tell "stopped for a newer one" from "made".
8. **The packaged window's Origin was measured**, not reasoned: none at all. No route check had to change.


Part C:

9. **The timeline is written from scratch** (no code taken from OpenCut or react-timeline-editor, so nothing to credit in NOTICE). Items are buttons (a keyboard reaches each and a screen reader names it: track, place, name, start and end); their pictures and waveforms are drawn on one canvas per track, for the view only. Only items within a view's width of the view are buttons (plus the selected and the dragged), placed again when the view nears that window's edge. Rows top to bottom as in CapCut: text, overlays (the higher first), the main track, then the sound tracks. A1 has no row: it is V1's own sound, drawn on V1's items, and the main track's header mutes it. Step 9's caption lane goes between the main track and the sounds (`TimelinePanel.tsx` marks the place).
10. **The main track magnet is always on**, because step 5's V1 is gapless by definition: a toggle that could not turn it off would mislead. The toolbar names it with a tooltip; "Snap" is the toggle (the playhead, every item's edges, the video's start and end; remembered per person as `new-editor-snap`). Step 5's document has no markers, so there are none to snap to.
11. **A drag sends one operation when it ends** (a move or a trim), so it is one undo step; while dragging, a ghost shows where it goes (on the main track, a line before the item it goes in front of). Esc cancels a drag. An inspector slider sends its change when it is let go, a field when it is left.
12. **Split** splits the selected items under the playhead, or, with none of them there, the main track's item under the playhead (as CapCut). **Delete** deletes the selected items (the main track closes the gap; its edge fades move, ED36).
13. **Keys:** S, Delete or Backspace, + or =, - or _, Shift+Z (fit), Alt+Left/Right (the selected item one place earlier or later on the main track, one frame on another track; with Shift, one second), Esc (clear the selection). None while typing or while the export dialog is open.
14. **AI framing only on the main track** (`compose.is_ai_item` is about V1): "AI framing" in the Framing choice, and "Reset to AI framing" (the imported clip's `crop`/`gaming`, or `{}` when it had neither, which is AI-framed on the clip's own video). An overlay of the clip's own video is framed fit, as the export frames it.
15. **The media bin adds whole media:** "+ Main track" at the end of V1, "+ Overlay" over the video at the playhead (on the lowest overlay track with room there, or a new track in the same call, one undo step), no longer than what is left of the video; or a drag onto a track. A picture lasts 3 s. "Import" brings a file into the open project (`add_media {asset}`).
16. **The export dialog** starts from the project's own preset, else Settings > Export's default: "Original", or "Custom" (size, shape and its fill, quality, format where this PC can make it, a size limit), with the size estimate (not its pixel size, which the engine works out for a 9:16 clip). How the engine makes the file (branch A or B) is never shown. A clip's project says, in the Export button's tooltip and a short line beside it, that its export replaces the clip in the Library.
17. **Today's editor asks before it renders over a new-editor export (7-D5).** Apply and the chat edit send exactly what they sent before; only a 409 with `reason: "project_export"` brings up the question ("Open in new editor" while the switch is on, "Apply anyway", "Cancel"). "Apply anyway" sends the same request with `replace_project_edits: true`.
18. **"Clear editor cache"** sits in Settings > Storage with the cache's size, shown only while the switch is on.
19. **Matroska H.264/AAC needs no proxy** (measured; above).

## The UI's structure

### Part B: the switch, the ways in, the screen and the viewer

**The switch.** `lib/newEditor.ts`: `newEditorOn()` and `setNewEditorOn()` keep "New editor (beta)" in localStorage (`new-editor-beta`, on only for `"1"`), beside the window's other preferences (appearance, the gaming preview's full screen). `useNewEditor()` follows it. Settings gets one card, `NewEditorCard`, after Appearance: a checkbox, with its explanation in the tooltip. With it off, nothing else changes anywhere (`tests/test_ui_new_editor.py`).

**The ways in** (`components/editor/LibraryEntry.tsx`, on the Clip Editor page only while the switch is on):

| Where | Component | What it calls |
|---|---|---|
| Under today's "Edit this clip" in the clip panel (`ClipEditor`'s optional `newEditorButton` slot) | `EditClipInNewEditor({clipId})` | `POST /clips/{id}/project`, then opens it |
| Beside "Make clips again…" for the open video | `EditWholeVideo({videoId})` | The shape dialog, then `POST /projects {media: "video:<id>", canvas}` |
| Under the page title | `NewEditorBar` | "Projects" (the list) and "Import a video to edit": the shape dialog, `window.studio.pickVideoFile()`, `POST /media/import` (a 202 job is followed through `GET /media/import/{job}`), then `POST /projects {media: "asset:<sha256>", canvas}` |
| The list | `ProjectsList` | `GET /projects`. A name opens its project; × deletes it after a confirm (`DELETE /projects/{id}`). Labels: "Clip deleted", "Can't be opened" (damaged: listed, never opened), "View only" (read-only) |

**Opening.** It goes through one window event (`openProject(id)`, `OPEN_PROJECT`).
- `App.tsx` listens and draws the editor as a fixed full screen **over** the app. The editor is loaded with `React.lazy`, only then.
- The page underneath stays mounted, so Close returns to exactly where the person was (the same video, the same selected clip).
- Focus moves into the editor, and back to what opened it on Close.
- The Library reads its clips again on `clip_file_replaced`.

**The screen** (`components/editor/`):

| File | What it is |
|---|---|
| `EditorScreen.tsx` | The frame: the top bar (Close, name, length, Beta, Undo, Redo), a notice line, a grid of the media bin, the viewer and the inspector, and the timeline across the bottom. It owns the selection (`selected: string[]`, item ids), the media shown on its own (`source`), the playhead store and the keys: Space, ←/→ and `,`/`.` (Shift: 10 frames), Home, End, I, O, Esc, Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y. A key typed into a field, or Space on a focused button, is left to it |
| `useProject.ts` | `useProject(id)` gives `{project, loadError, notice, clearNotice, apply(ops, label?), undo(), redo(), reload()}`. Each change waits for the one before, and sends the revision that one produced. A 409 reloads the project and says so in plain words; a 423 says the project is view-only; `warnings` become the notice |
| `useMediaInfos.ts` | `useMediaInfos(projectId, doc)`: `GET /media/project:<id>:<media>/info` for each video and picture. Read again when the document's media change or a proxy job is done (its event, and every 5 s while one is made). Asks for the proxy once when `needs_proxy`. `playable(info)` gives the address a `<video>` plays and the source's clock offset (`start_us`; 0 for a proxy) |
| `Viewer.tsx` | The viewer (below). A `forwardRef` with a `ViewerHandle`: `togglePlay`, `step(frames)`, `toStart`, `toEnd`, `seek(t_us)`, `markIn`, `markOut`. Inside it, `StillLayer` (the paused frame), `SourceScrub` (the bar under a media on its own) and `TimeReadout`, with `OverlayLayer`, are the only parts that follow the playhead each frame (the stand-in timeline redraws only at a cut); the viewer itself draws again only on play, pause and edits |
| `programPlayer.ts` | `ProgramPlayer` plays V1 on two `<video>` elements (below). Also `framingStyle(item, ai)` and `transformCss(transform)`: the export's framing and transform in CSS |
| `OverlayLayer.tsx` | V2 and up while playing: each item on screen at the playhead, framed fit or fill, moved, scaled, turned and faded as step 7 draws it. A muted track is hidden. Pictures are `<img>`; videos are muted `<video>`s kept within 0.1 s of the program |
| `MediaBin.tsx` | **Part B's version**: the project's media as a list. A video or picture opens on its own in the viewer |
| `Inspector.tsx` | **Part B's version**: the selected item's track, length, speed and framing ("AI framing" for an AI item), or the project's name, canvas, frame rate and length. It already takes `apply`, for part C |
| `TimelinePanel.tsx` | **Part B's stand-in**: V1's items in order (a click selects and jumps there; Shift or Ctrl adds), and the other tracks' item counts |
| `ProjectsList.tsx`, `LibraryEntry.tsx` | The list and the ways in (above) |

**Shared code in `lib/`:**
- `editorTypes.ts`: the shapes of the document, a project, a summary, the media info, a job and an event.
- `editorApi.ts`: every call above; `EditorError` (`status`, `detail`, `conflict`, `superseded`); `engineUrl`; `projectMediaId`.
- `editorProgram.ts`: the frame grid as step 5's `model.frame_us`; `frameAt`; V1's items and length; `itemIndexAt`; `sourceAt` and `programAt`; `continues`; `isAiItem`, which is step 7's `compose.is_ai_item`; `formatTime` (m:ss.ff) and `formatLength`.
- `editorPlayhead.ts`: the playhead as a small store outside React state, so only what shows it draws again each frame (`createPlayhead()`, `usePlayheadTime`, `usePlaying`).
- `lib/api.ts` only gained `export` on `sessionHeaders`.

**The viewer.**
- **Playing** (`ProgramPlayer`):
  - The playing element's own presented frames (`requestVideoFrameCallback`) are the program's clock.
  - The other element waits, hidden and silent, 300 ms of program time before the next cut's first frame. It starts playing early by what a start takes (measured at each cut). While it runs hidden, its speed is nudged by at most ±20% (it is silent) so it reaches the cut when the outgoing item ends.
  - It is shown and heard on the cut's first frame, and the outgoing one is paused then. If the next one is late, the outgoing one holds its last frame, so a frame past a cut is never shown.
  - Items that carry straight on in the same file at the same speed (a split never moved) play on one element, with no cut.
  - An item with nothing playable yet (a proxy being made) stops playback there with a plain line; pausing shows the engine's frame.
  - A `<video>` plays volume 0 to 1, so a volume over 1 plays at 1 (step 10's preview does the mix).
  - While playing, an AI item shows as a centre fill and colour isn't applied. Pausing shows the truth.
- **Paused:**
  - `GET /projects/{id}/still?t_us=&rev=` for the frame under the playhead, 40 ms after it stops moving. It goes through `fetch` with the header, so the two headers can be read.
  - One request at a time: a newer one aborts the older, and a 409 (superseded, or another revision) is ignored.
  - The JPEG is decoded with `createImageBitmap` and drawn on a canvas over the players. The packaged page's rules (`index.html`'s CSP) let no `blob:` or `data:` address into an `<img>`.
  - The still is hidden the moment the frame changes, so an older frame's still is never shown as this one's.
  - "Approximate frame" (with its tooltip) shows when the engine sends `X-Clips-Kitty-Still-Note: approximate`.
- **Preview:**
  - `POST /projects/{id}/draft {revision}`. When the draft is made already, it plays at once.
  - Otherwise its progress comes from `media_job` events (`kind: "project_draft"`), with a POST every 3 s as a fallback (the same job comes back).
  - The draft plays in a `<video controls>` in place of the picture. "Back to editing", or any change to the project, goes back.
- **A media on its own** (clicked in the bin):
  - One `<video>` of that media, with a scrub bar (a long video has no timeline of its own here) and the marked part shaded on it.
  - The time is in the media's own frame rate.
  - **Mark in** (I) and **Mark out** (O). The out point includes the frame under the playhead.
  - **Add range**: `insert_clip` at the end of V1 (`track_id: "V1"`, `media_id`, `source_in_us`, `source_out_us`). The new item is selected, and the viewer goes back to the timeline at its start.
- **The phone apps' screens:**
  - The picker offers `tiktok`, `reels`, `shorts` and `none`, labelled as the gaming preview labels them (`"<SAFE_ZONES label> preview"`, "No overlay") and drawn by `PlatformOverlay` over the canvas box.
  - It works only on a 9:16 canvas. On any other shape the picker is greyed out, with a tooltip saying why.
  - The choice is remembered per person (`new-editor-platform`, default "No overlay").
- **Size:** Fit (the default), or 25%, 50% or 100% of the canvas's pixels. A size larger than the window scrolls.
- **Shortcuts** are listed in the ⌨ tooltip at the bottom right, and in each button's tooltip.

**Measured in the sandbox** (headless Edge through playwright-core, the sandbox engine, step 8's seeded library; the scripts are in `scratch/step8/partB/`, not committed):
- **Opening:**
  - The 30 s clip's project, from its button: 835 ms.
  - The 2-hour video's whole-video project: 312 ms.
  - An imported 45 s file, the copy included: 211 ms.
  - A 300-item project from the list, until its 300 items were drawn: 52 to 113 ms.
- **Cuts:**
  - 20 one-second parts of the 2-hour video, played through. A normal frame stayed on screen 34.7 ms (the median); each part's last frame stayed 35 to 70 ms, so at most about one frame longer.
  - 1 source frame of 600 was never shown, and no frame was shown outside its part.
  - Three 3-second parts: no frame was shown past a part's end, on a 16:9 or a 9:16 canvas.
- **The draft** of the 30 s clip's project: 21.3 s from Preview to playing.

### Part C: the timeline, the inspector, the media bin and the export dialog

| File | What it is |
|---|---|
| `TimelinePanel.tsx` | **The timeline** (replaces part B's stand-in, same props plus `apply`, `infos`, `readOnly`; a `forwardRef` with `TimelineHandle`: `split`, `remove`, `zoom(dir)`, `fit`, `nudge(dir, big)`, which `EditorScreen`'s keys call). A toolbar (Split, Delete, Snap, the magnet's note, zoom out/in, Fit, the ⌨ tooltip from `TIMELINE_SHORTCUTS`), a ruler (click or drag to move the playhead) and the rows. Each row: a sticky header (its name; hide for an overlay, mute for a sound track, the main track's own sound for V1) and a lane holding a sticky canvas the width of the view and the items as `ItemButton`s (memoised: an item is drawn again only when its own place or state changes). The playhead line moves without a render (`translateX` from the playhead store) and turns the page while playing. Zoom: Ctrl+wheel about the pointer (wheel steps between two frames are one zoom), + and − about the playhead, Fit; each zoom is one render, its scroll set right after |
| `timelineMedia.ts` | `TimelineMedia`: per media, step 6's thumbnail index and its sheets (decoded with `createImageBitmap`, the newest 48 kept) and the waveform (64 bins a second with every coarser level, `parsePeaks` reading the CKPK bytes). A product still being made (202) is asked again 4 s later. `drawThumbs` and `drawWave` draw one item's stretch, for the part in view only |
| `Inspector.tsx` | The selected item: name, track, length; **Timing** (where it starts and ends in its video, typed as m:ss.ff or seconds, `trim_item`; "Starts at" for any track but V1, `move_item`; "Ends at" for text, pictures and looped music); **Speed and sound** (speed 0.25 to 4, volume 0 to 200%, fade in and out); **Picture** (Framing: AI framing on the main track's AI items, Fit, Fill; Position; Background blurred or a colour on the main track; Reset to AI framing); **Place** for overlays (left/right, up/down, size, rotation, opacity: step 7's transform); **Colour** (the app's filter picker and its seven adjustments, Reset colour). Nothing selected: the project's name, canvas (9:16, 16:9, 1:1, 4:5), frame rate and length. Text items say they are edited in a later version (step 9) |
| `MediaBin.tsx` | Each media with a thumbnail (its index's tile a third of the way in, on a canvas), its kind and length; "+ Main track", "+ Overlay", draggable onto a track; "Import" (desktop app only: the file picker, step 6's import, `add_media {asset}`) |
| `ExportDialog.tsx` | The dialog (`role="dialog"`; `useModalFocus.ts` keeps the keyboard in it: the editor behind is inert, Tab and Shift+Tab go round its controls, Esc closes it wherever the focus is, focus back on Export). A project with no clip sends Settings' export folder (`lib/exportFolder.ts`) and shows "Saves to <folder>" beside Export and "Saved in <folder>" on the done line; a clip's project shows "Replaces the clip in your Library". Export as Original or Custom (size, shape, its fill, quality, format, "Keep under N MB"); the estimate (`POST /export/estimate` with the project's length); Export (`POST /projects/{id}/export` with the revision and the preset), progress from `export` events with a 2 s check of `GET /export/jobs/{id}`, Cancel; done: the file's name and size, Play (in the dialog, from the row's `url`), Show in folder (desktop app only). Earlier exports: when, the file, its state ("File gone" for a done row whose file went, "Replaced by a later export"), Play and Show in folder only for a row with a `url` |
| `ProjectExportPrompt.tsx`, `lib/projectExportPrompt.ts` | Today's editor's question (7-D5). `api.rerenderClip` and `api.aiEdit` go through `renderRequest`, which sends what they sent before; a 409 with `reason: "project_export"` asks (the prompt is mounted once in `App.tsx`, and with nothing mounted the answer is Cancel) |
| `EditorCacheRow.tsx` | Settings > Storage, while the switch is on: "New editor files: N · size" and "Clear editor cache" (`GET /media/cache/size`, `POST /media/cache/clear`) |
| `lib/editorTimeline.ts` | The timeline's arithmetic: the rows' order, pixels and microseconds at a zoom, the window of items, snapping, where a dragged main-track item lands (`dropIndex`, step 5's index once the item is taken out), the trim limits (`trimEdge`: at least a frame, never past the media's ends), `parseTime`, the ruler's spacing |

`useMediaInfos` now reads the sounds' infos too (their waveforms). `LibraryEntry.importFinished` is exported for the media bin. `EditorScreen` gained the Export button, the timeline's keys and the dialog.

## Measured in the sandbox (part C)

Headless Edge through playwright-core against the sandbox engine and the seeded library (2026-11-15); scripts in `scratch/step8/partC/` (not committed), screenshots `scratch/step8/screens/c-*.png`, looked at.

- **Three ranges from the 2-hour video** (the plan's proof): Edit whole video (9:16); in the media bin's view of the video, I and O around 10:00 to 10:03, 1:00:00 to 1:00:04 and 1:45:00 to 1:45:02, Add range each time; the whole video selected and deleted; the third range dragged in front of the first. The main track became 1:45:00-1:45:02, 0:10:00-0:10:03, 1:00:00-1:00:04 (9.1 s). Exported as Original in 3.9 s: 1080x1920, 30 fps, 12.0 s (9.1 s and the 2.9 s end card); frames read 01:45:00:15 at 0.5 s, 01:45:01:27 at 1.9 s, 00:10:00:05 at 2.2 s, 00:10:02:26 at 4.9 s, 01:00:00:07 at 5.3 s, 01:00:03:25 at 8.9 s, then the end card. Play in the dialog loaded it (1080x1920, 12.0 s).
- **An own file**: "Import a video to edit" (16:9) with `own_clip_45s.mp4` (the file picker stood in for); the ruler clicked at 0:10; S split it at exactly 10.000 s; the second part's right edge dragged to about 0:35 (source out 35.107 s; V1 put the end on the frame grid, 25.1 s long); Ctrl+Z gave back the 45 s, Ctrl+Shift+Z the trim again (the same document). Exported as Custom 720p in 16.0 s: 1280x720, 38.0 s (35.1 s and the end card); frames 00:00:09:27 at 9.9 s, 00:00:10:03 at 10.1 s, 00:00:34:27 at 34.9 s.
- **An AI clip unchanged, against Apply**: clip 1 of real_150s (34.78-55.24) applied unchanged in today's editor (the render route as its Re-render sends it) gave a 23.767 s file of 16,330,933 bytes. "Edit in new editor" on the same clip, then Export as Original (38.7 s): the clip's file afterwards is **byte for byte the same file** (`cmp`; PSNR inf). The dialog's tooltip said the export replaces the clip.
- **7-D5**: with the clip's project reordered and exported (branch B), today's editor's Re-render asked the question; Cancel queued nothing; "Open in new editor" opened the project.
- **The inspector**: speed 2 typed (the item halved to 4.73 s), Ctrl+Z back; Fill, then "Reset to AI framing" back to the clip's framing; "+ Overlay" at 0:05 made V2 with the item, Size 50 set its scale to 0.5; the project renamed from the inspector.
- **Accessibility**: axe found no violations in the editor with an item, an overlay and the inspector open. Tab goes from the toolbar through each row's header and items, top to bottom.
- **Speed (T5)**, a project of one-second parts of the 2-hour video:

  | | Development build (Vite) | Production build (`npm run build`, opened from `file://`) |
  |---|---|---|
  | Opening a 300-item project, until all 300 items are drawn | 27 to 272 ms (first open of the run the slowest) | 17 to 42 ms |
  | Scrolling 500 items zoomed in, 40 px a frame for 4 s | every frame 7.0 to 7.1 ms (the display's 144 Hz), none over 17.5 ms | the same |
  | Zooming 500 items with all of them on screen, a Ctrl+wheel step every frame for 4 s | median 7.1 ms, 95th percentile 20.9 ms, 82 of 324 frames over 17.5 ms, 2 over 34 ms | median 7.0 ms, 95th percentile 20.8 ms, 28 of 401 frames (7%) over 17.5 ms, none over 34 ms |

  Scrolling holds 60 frames a second. Zooming with every one of 500 items in view drops about one frame in 14 (the layout of 500 buttons); with a zoom where only part of them are in view it is cheaper. The production build was opened from `file://` in a scratch Edge with web security off (the packaged window applies no CORS; Edge would), only for this measurement.

## QC round 1 fixes

| Defect | Fix | Tests |
|---|---|---|
| 8-D1 (serious) "Apply edits & upload" rendered the old window over a project export | `RenderFirst.replace_project_edits`; `_publish_clip` asks `project_edits_kept` before it queues the render; `api.publishClip` with `render_first` goes through `renderRequest` (the same question) | `test_step8_routes.py::test_apply_edits_and_upload_and_the_caption_save_ask_first_too`, `::test_a_clip_with_no_project_export_publishes_and_saves_captions_as_before`; `test_ui_new_editor.py::test_every_render_of_todays_editor_asks_first` |
| 8-D2 `PUT /clips/{id}/captions` did the same | `CaptionsIn.replace_project_edits` and the same check; `api.saveCaptions` asks first | as 8-D1 |
| 8-D3 state.json still said the timeline came from OpenCut | the note now says it was written from scratch | |
| 8-D4 a Branch A row whose file Apply rewrote in place stayed `done` | `bytes` and `file_mtime_ns` recorded at done; a mismatch lists the row `replaced` and its file route answers 404; `mark_replaced` compares paths as the system does | `::test_an_export_whose_file_was_written_again_in_place_is_not_offered`, `::test_replaced_exports_are_found_by_the_file_not_the_text_of_its_path` |
| 8-D5 (serious) = 8-D1 and 8-D2, from the review | as 8-D1 | the review's `test_r1_d5_publish.py` passes |
| 8-D6 (serious) a stale project put the clip back as it was | [above](#a-clip-changed-in-todays-editor-after-its-project-8-d6) | `::test_a_clip_changed_in_todays_editor_after_its_project_was_made`, `::test_keeping_the_project_over_a_changed_clip_is_the_persons_choice`, `::test_a_manual_project_never_asks_about_a_clip`; `test_ui_new_editor.py::test_a_clip_changed_since_its_project_is_asked_about`; the review's `test_r8_stale_project.py` passes |
| 8-D7 the UI's frame starts rounded down, the engine's to the nearest µs | `frameUs` is `model.frame_us`'s formula, `(2·n·1e6·den + num) // (2·num)`, in BigInt past 2^53 | `test_ui_new_editor.py::test_the_ui_frame_grid_is_the_engines_to_the_microsecond`; the review's `r2_frames.py`: 0 of 600 frames differ at 23.976, 29.97, 30 and 60 |
| 8-D8 typing back the Inspector's shown time moved the edge | a time is committed only when it is on another frame | `::test_typing_back_the_shown_time_changes_nothing` |
| 8-D9 `POST /export/jobs/{id}/cancel` | It already needed the session header (a web page can't send it, and a custom header is never a simple request), so no page could cancel; it now also checks the Origin as the project routes do. `GET /export/jobs` and `GET /export/jobs/{id}` stay open: step 1's screens read them without the header, and they only show progress | `::test_stopping_an_export_needs_the_apps_own_window` |
| 8-D11 (serious) deleting a project left its export unprotected: today's Apply re-rendered over it and deleted it | the export is known by its file too; the 409 without a project offers only "Apply anyway" and "Cancel"; deleting a project cancels its waiting and running exports; render jobs ask again when they run ([above](#a-clip-whose-file-is-a-project-export-7-d5), ED44) | `test_step8_engine.py::test_a_clip_whose_file_is_a_deleted_projects_export_is_still_known`; `test_step8_routes.py::test_a_deleted_projects_export_is_still_asked_about`, `::test_a_render_job_never_goes_over_an_export_it_was_not_asked_about`, `::test_deleting_a_project_cancels_its_waiting_export`, `::test_deleting_a_project_cancels_its_running_export`; `test_ui_new_editor_part_c.py::test_the_question_without_a_project_offers_only_apply_anyway_and_cancel`; the review's `test_u1_delete_project_then_apply.py` and `test_u2_delete_project_mid_export.py` pass |
| 8-D10 (serious) the end pad (ED34) lengthened V1 only: a picture, logo, overlay video or text laid over the whole program vanished for the export's (and Preview's) last 0.4 s | `with_end_pad` lengthens every item on the other video and text tracks that ends at the old end; an overlay video runs on in its source, then holds its last frame (`overlay_graph`'s `tpad`, only for an item longer than its source span); an item ending earlier keeps its end; captions follow Apply (unpadded) | `test_step8_engine.py::test_the_end_pad_covers_every_layer_that_ran_to_the_end`, `::test_a_caption_that_ends_at_the_end_ends_there_as_apply_draws_it`; `test_compose_routes.py::test_the_end_pad_keeps_what_was_laid_to_the_end` (real FFmpeg); the review's `test_t2_pad_overlay.py` passes |
| 8-D12 (serious) edits made during a Branch A export were dropped by a silent start-again | the export records the revision it rendered (`set_clip_basis(revision=...)`), so later edits make the person be asked; the editor shows a plain notice on `started_again`; a start-again keeps the project's name; the 7-D5 question's first focus is on Cancel when "Open in new editor" is not offered; the moved-fade notices name no item ids (ED43) | `test_step8_routes.py::test_edits_made_during_a_branch_a_export_are_never_dropped`, `::test_an_unchanged_branch_a_export_still_follows_the_clip_and_keeps_its_name`; `test_step8_engine.py::test_the_clip_basis_is_the_exported_revision_not_the_head`, `::test_a_moved_fades_notice_names_no_internal_id`; `test_ui_new_editor_part_c.py::test_without_open_in_new_editor_first_focus_is_on_cancel`, `::test_a_start_again_is_always_told`; the review's `test_v1_edit_during_branch_a.py`, `test_s8_restart_after_a.py` and `test_v3_edge_fade_undo.py` pass |

## Left for later

- **Step 9 (8-D13):** with the project's own caption lane, Export in other languages could burn and dub onto a clip's new-editor export with the captions remapped through the timeline, instead of asking.

- **Step 9:** captions and text on the viewer (JASSUB over `compose.build_ass`), editing text items, the caption lane (its place is marked in `TimelinePanel.tsx`), the export's caption choices (`captions: burn | none`, `srt`), dragging with centre lines.
- **Step 10:** the audio panel and mixer, WebAudio preview (a volume over 1 plays at 1 while previewing), whether V2's sound is heard, editing sound items beyond volume and fades, adding sounds to A2 and up.
- **Step 11:** AI framing on other canvases, an auto-track button for manual items. Steps 5 and 7 have no tracked manual item: an item is AI-framed only through an imported clip's `crop`/`gaming`, so the button needs step 11.
- **This step, open:** "Show in folder" was not tried in the desktop app (the sandbox is a browser; typecheck and a source test only). Zooming 500 items all in view drops about one frame in 14. The platform overlay is offered for 9:16 only. I and O mark only a media shown on its own, not the program.
- **Not fixed (part A):** Make clips again's pinned copy, the slow thumbnail fallback, Branch A's restore with a locked file.
- **For Colin's click-through (found by the last checks, not fixed; none loses work in normal use):**
  - Two exports of the same project that overlap (export, close the dialog, edit, export again while the first, an AI re-render, still runs): the second fails with "The clip this project was made from is gone", or says Exported while the Library keeps the first export. Serious but unusual; reproduction `scratch/step8-review/test_x2_two_exports.py`. Fix: look the clip up through the project when the export runs.
  - Typing two Inspector fields in a row (Place X, then Size) is one undo step.
  - After a "changed somewhere else" reload, a change already queued behind the refused one is still sent.
  - An overlay's left edge trimmed onto the playhead at speeds other than 1x can start one frame late.
  - A whole-video clip's project exported through Branch B gets the end card, though the clip had none.
  - Delete in the timeline leaves keyboard focus on the page; empty overlay tracks can't be removed; the overlay track reads "V2"; the ⌨ shortcut lists are mouse-only; edits aren't announced to a screen reader; Inspector values have no units; the Projects list doesn't say where a project came from.
  - Text beside controls: "Framed by the AI, as when the clip was made.", "Main track magnet", and "Replaces the clip in your Library" beside Export (his call whether it stays).
  - The size limit accepts 1 MB below the 5 MB minimum without a word, and the dialog's size guess was far above a small file's real size.
  - "Show in folder" and the whole editor in the desktop app (only the browser sandbox was used).
  - Existing, not step 8's: deleting a video while today's editor plays its clip leaves the clip file on disk (X1); `GET /export/jobs*`, `/media/{clip_id}` and `/media/preview/{clip_id}` from steps 0 and 1 take a foreign Origin.

## Tests

| File | What it checks | Runs on CI light |
|---|---|---|
| `tests/test_step8_engine.py` | Step 6's faults (Range digits, If-Range, links and junctions in the cache and Storage, the peaks kill, case and tampered rows, replaced sources, the import's second read), step 7's (AI runs in pieces, the end pad, replaced exports), the edge fades, the projects list, `project_export_of` | yes (the import with a real file skips without FFmpeg) |
| `tests/test_step8_routes.py` | `GET /projects`, a finished export's file (part C), the deleted clip's project, the packaged window's (no) Origin, CORS headers for the dev server, `POST /projects` with `media`, 7-D5's 409 and its flag, replaced and gone exports, the export row's path before the move, stills one at a time, a waveform job across Clear, a deleted video's jobs, every route of this page listed here | no (FastAPI; the Plugin platform job). The Clear test needs FFmpeg |
| `tests/test_media_fileio.py` | changed: If-Range by date now gives the whole file | yes |
| `tests/test_ui_new_editor.py` | Part B: the switch off by default and kept with the UI preferences; Settings' one new card; every Library way in behind the switch; today's clip panel unchanged without the button; App.tsx loads the editor only when a project is opened; one still at a time, a superseded one ignored, drawn with no blob: or data: address; two players on requestVideoFrameCallback; the platform overlay reused from the gaming preview and only for 9:16; the AI framing rule the same as `compose.is_ai_item`; the keys the tooltip lists; every change sends its revision | yes |
| `tests/test_ui_new_editor_part_c.py` | Part C: the timeline and the inspector change the project only through operations, a drag and a slider send once when they end; the timeline draws only what is near the view (buttons in a window, one canvas per track, no picture addresses), leaves the caption lane's place, and answers the keys its tooltip lists; items and tracks have names; AI framing only on the main track, never crop numbers; the inspector's ranges are the engine's; the export dialog never names the branch, plays only a row with a `url`, says a clip's export replaces the clip, has no caption choices; "Show in folder" takes only an existing video file; today's Apply and chat edit send what they sent before and ask only on a project export; the editor cache line only with the switch on | yes |
| `tests/test_media_probe.py`, `tests/test_media_routes.py` | changed in part C: H.264 + AAC in Matroska needs no proxy (10-bit H.264 and HEVC in Matroska still do) | probe yes; routes no (Plugin platform job) |
