# Step K2: Clips Kitty Editor inside Clips Kitty (the contract)

Clips Kitty Editor is the full editor behind the Editor page: a separate program, based on Concat (AGPL-3.0-or-later), that the desktop app shows inside its own window. This file is the contract between the three parts:

- **the editor** (the fork, repository `clips-kitty-editor`): its command line and its `host.*` API are written down in that repository's `PATCHES.md`;
- **the engine** (this file's first half): where the editor is, its projects, taking exports back in, and the rules that keep a person's editor work from being lost;
- **the app** (Electron main process and the Editor page): the second half, written by the UI work of this step.

Nothing here is a public API: every route is internal and may change with the app.

## Engine

### What the engine does and does not do

- It finds the editor, builds a project for it with no window (the editor's own automation), and takes finished exports back in.
- It never starts the editor's window, never holds the window's token and never talks to the window: that is the main process's job.
- It takes no path from a client except a file the person picked (checked like `POST /videos/local`) and their export folder. An export is named by its file name only.
- A plain clipping run never loads any of this (`tests/test_editor_import_guard.py`).

### Files

| File | What |
|---|---|
| `core/binaries.py` | `editor_dir()`, `editor_exe()`: where the editor is |
| `kitty_editor/registry.py` | the projects' records (one folder and one manifest each) |
| `kitty_editor/automation.py` | the headless editor: `command(exe)`, `version_of(exe)`, `Session` |
| `kitty_editor/build.py` | `render_base`, `plan_clip`, `apply_clip_plan`, `build_whole`, `probe` |
| `kitty_editor/intake.py` | `take_in`, `recover` |
| `server/kitty_editor_api.py` | the routes |
| `core/state.py` | `PROJECT_EXPORTS_FOLDER`, `in_project_exports`, `StateDB.project_export_of`, `project_export_clips`, `data_dir` |
| `server/jobs.py` | `clip_lock`, the render job's and the translate job's second question, a re-rendered clip's projects follow its new id |
| `server/api.py`, `server/youtube_api.py` | `project_edits_kept`, `project_edits_left_out`, the `editor_export` key |
| `core/pipeline.py` | `_keeps_project_export` (a forced run and story reels keep an export) |
| `core/housekeeping.py` | never sweeps `data/clips/projects/` or `data/editor/`; keeps a download a project uses |

`kitty_editor/` is standard library only when imported; what it asks of the app it imports inside a function. `core/state.py`, `core/housekeeping.py` and `core/pipeline.py` never import it.

### Where things are kept

```
data/editor/projects/<id>/            <id> = 32 hex characters
    kitty.json                        the manifest; written whole (a temp file, then os.replace)
    media/<clip title>.mp4            a clip project's video: the whole window, framed, no captions
                                      (media/base.mp4 in a project made before K-D20); manifest `video_file`
    media/<video title>.<ext>         a whole video's project: a hard link to the Library's download
                                      (no copy); the download itself where the drive can't link
    media/music.<ext>, watermark.<ext>   a copy of the clip's music file and watermark picture
    p/<name>/                         the editor's own project folder (holds concat.lock while open)
    out/                              where the editor's Export writes; emptied as exports are taken in
    build.log                         what the headless editor logged while the project was built
data/clips/projects/<id>-<n>.<ext>    a clip project's export once taken in (n = 1, 2, ...)
```

No database table was added or changed. The manifests are the registry: listing is reading the folder.

The manifest, `kitty.json`:

```json
{"schema": 1, "id": "<32 hex>", "name": "My clip", "kind": "clip|video|file",
 "state": "building|ready|failed", "progress": 0.0, "message": "",
 "created_at": "2026-11-18T09:00:00.123Z", "last_opened_at": "2026-11-18T09:00:00.123Z",
 "canvas": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}, "shape": "9:16",
 "project_dir": "<abs>/p/My clip", "export_dir": "<abs>/out", "export_name": "My clip",
 "clip": {"id": 5, "video_id": "abc", "start_s": 10.0, "end_s": 55.0,
          "file": "<abs path of the clip's file the project stands on>", "file_stamp": [12345, 1700000000000000000]},
 "video_id": "abc or null", "source_file": "<abs, kind file only>",
 "media": ["<abs paths the project uses outside its own folder>"],
 "notes": ["framing_baked"],
 "exports": [{"file": "My clip.mp4", "at": "<UTC>", "result": "replaced_clip|kept_beside|saved_to_folder",
              "path": "<abs>", "seconds": 36.9, "clip_id": 5}]}
```

- `clip` is null for a whole video and a file. `file_stamp` is `[size in bytes, mtime_ns]`.
- `clip.file` and `clip.file_stamp` are set when the project's video is made from the clip, and move to each export that replaced the clip. `clip.id` follows the clip when a render gives it a new id.
- `canvas` is null and `shape` may be empty until the build has ended. A clip project's `shape` is one of the four names when its size is exactly that shape, else `"<width>x<height>"`.
- Times are UTC with milliseconds (they also say which of two projects is newer).

### Finding the editor (E1)

`core.binaries.editor_dir()`: the folder named by `CLIPS_KITTY_EDITOR_DIR` when that variable is set (and nowhere else then); otherwise, in the installed app, `resources/editor` (`Path(sys.executable).parent.parent / "editor"`); it must hold `clips-kitty-editor.exe` (no `.exe` off Windows). No PATH lookup, no vendor folder: a checkout without the variable has no editor. Bundling the folder is step K3.

`GET /editor/status` answers `{"found", "version", "message", "reason"}`:

| `reason` | `message` | When |
|---|---|---|
| `""` | `""` | found: `version` is what `--version` printed after `clips-kitty-editor ` |
| `missing` | The editor is not installed with this copy of Clips Kitty. | no such file |
| `not_ours` | The editor found on this PC is not Clips Kitty Editor. | `--version` does not start with `clips-kitty-editor `, exits with an error, or does not run in 5 s |
| `windows_only` | The editor runs on Windows only. | any other system |

The `--version` run happens once per program file per engine start, with no console window.

`GET /editor/launch`: `{"exe", "cwd"}` (the program and its folder), or 503 `{"detail": {"message", "reason"}}`. Only the main process calls it.

### Routes (E3)

All need the `X-Clips-Kitty-Session` header and an Origin that is the app's own (`server.projects_api.local_origin`); 403 otherwise.

| Route | Body | Answer | Errors |
|---|---|---|---|
| `GET /editor/status` | | above | |
| `GET /editor/launch` | | above | 503 |
| `POST /editor/projects/open` | `{"kind":"clip","clip_id":5,"fresh":false}`, `{"kind":"video","video_id":"abc","shape":"16:9"}` or `{"kind":"file","path":"<picked>","shape":"16:9"}` | `{"project": Project, "created": bool}` | 400, 404, 422, 503 (below) |
| `GET /editor/projects` | | `{"projects": [Project]}`, last opened first | |
| `GET /editor/projects/{id}` | | `Project` | 404 |
| `POST /editor/projects/{id}/open` | `{}` or none | `Project` with `last_opened_at` now, plus `recovered` (a list of take-in answers, usually empty) | 404; 409 `reason: "building"`; 503 when a failed project must be made again and the editor is missing |
| `DELETE /editor/projects/{id}` | | `{"deleted": true}` | 404; 409 `reason: "building"`; 409 `reason: "open_in_editor"` |
| `POST /editor/projects/{id}/exports` | `{"file": "My clip.mp4", "export_folder": "<abs>" or null}` | a take-in answer (below) | 400, 404, 409 `reason: "busy"`, 500 |

- `id` must be 32 lowercase hex characters; anything else is 404 before a file is touched.
- 409, 503 and the 500 of a failed take-in carry `{"detail": {"message", "reason"}}` (`message` only for the 500); the others carry a plain sentence in `detail`.
- An unknown key in a body is refused by FastAPI itself with **422**, as everywhere in the app; a key that does not belong to the `kind` (a `shape` for a clip) is 400.
- `shape` is `16:9` (the default), `9:16`, `1:1` or `4:5`: 1920x1080, 1080x1920, 1080x1080, 1080x1350.

`Project` is the manifest plus:

| Key | Meaning |
|---|---|
| `clip_changed` | a clip project whose clip is there, on another file (or the same file written again) than the one the project stands on |
| `clip_gone` | a clip project whose clip is no longer in the Library. It still opens and exports |
| `media_missing` | a file in `media`, or a ready clip project's `media/base.mp4`, is not there |
| `state` | a project left `building` by an engine that stopped is shown as `failed` with "This project was not finished. Open it again to make it." |

A project whose manifest can't be read is listed with `state: "failed"` (and `damaged: true`), never hidden; it can be deleted.

**Opening, by kind**

- `clip`: 404 for no such clip. When a project of this clip exists and `fresh` is not true, the newest is returned as it is (`created: false`), with `clip_changed` for the UI's question. Otherwise a new project is made (`fresh` leaves the older ones in the list). 503 when a build is needed and the editor is not found.
- `video`: 404 for no such video, and when its file is no longer in `data/downloads/`. The same video in the same shape returns the existing project; another shape is another project. The file is used in place and is listed in `media`.
- `file`: 400 `not a video file this app can open` when `core.paths.picked_file(path, _VIDEO_SUFFIXES)` refuses it (a folder, a network-share path, another kind of file, a file that is not there, a NUL in the name), and 400 `that file doesn't look like a video` for a file with no picture. No AI run and no Library entry. The same file in the same shape returns the existing project (choice 7).

**Building.** A new project answers at once with `state: "building"`. One background thread builds one project at a time, in the order asked. Progress is in the manifest (`GET /editor/projects/{id}`) and on the event stream as `{"type": "editor_project", "id", "state", "progress"}`, at most twice a second while building and once when it ends. A clip is held as in use while its project is built (`core.in_use.holding`). A build that fails leaves `state: "failed"` and a plain `message`; opening it again (either route) builds it again in the same folder. A project being built answers 409 `building` to `POST .../open` and `DELETE`.

### A clip's project (E2)

**The video** is `media/base.mp4`: the clip's whole window rendered by `core.pipeline._render_files` with the clip's own options, less `captions`, less `caption_lines`, less its `edit` block (cuts, speed, sound, hook, music), with `watermark: None`, and with the end card off (`kitty_editor.build.render_base`). So it carries the AI's framing, layout, colour filter and sound levelling, and nothing else; it runs from the clip's start to its end plus the render's own end pad. It is `multilingual.burn.clean_base`'s call, less the edits and the watermark that one keeps. It is made once and kept for the life of the project.

**Made the fast way when it can be (K-D24, ED28).** For a clip framed by the tracker's moving crop (the 9:16 crop filling the screen, or the face-safe 4:5 crop under the apps' top bar over a blurred copy of itself) the render is not run: `kitty_editor/fastbase.py` reads the crop back from the clip's own file (small grey copies of the clip's and the source's frames matched along the width at the tracker's sample frames, and at every frame between two samples where it jumps), then one FFmpeg pass crops the source there, lays it out as `video/cropper._render_tracked` does, with the clip's colour filter and loudness, untagged BT.601 colours as the standard render's piped frames give, and encodes it with x264 `ultrafast` CRF 16 on the CPU (a hardware encoder Clips Kitty's FFmpeg has is used as it is). The captions, a watermark or a logo in the clip's file take no part in the match (8 bands across the frame, the 5 that agree best). Any other clip, any frame that does not match (plain correlation under 0.5, or the fine detail under 0.3 unless the picture matches 0.95), or any error, falls back to the full render above, with the pipeline's own encoder (a faster encode of its cut was measured to change the tracker's layout choice) and the tracking models loaded while it cuts. Measured against the old base: the same crop to a tenth of a pixel, SSIM 0.98 to 0.99 at 5 points, one frame shorter at most, loudness within 0.1 LU.

**Reused (K-D26).** The manifest keeps `base_key`, a fingerprint of the clip's window, its framing options (not its captions' or watermark's), its file and the source (by path, size and time), and the tracking settings (`build.base_key`). A build that failed after the video was made, or a new project of the same clip (`fresh`), uses a video with the same key instead of making it again (another project's is copied, never linked: the editor may have that one open). A change to the window, the framing or either file makes it again.

- **Cost, measured** on this PC (RTX 3060, the 45 s window 30 to 75 s of the 1080p60 test video, the first render after the engine started, so with the tracking models loading): **82 s** from the request to `ready`, nearly all of it this render; the editor's part (building the project and reading it back) took 0.6 s. A whole-video or file project took **0.5 s**.
- **After K-D24** (2026-11-19, the 24.8 s clip of real_150s, 1080x1920 60 fps, face-safe layout, in the desktop app): "Open in editor" to the editor showing the project **15.1 s** (median of 3; 63.1 s before), of it the clip's video 12.6 s, the editor's project 0.4 s and the editor's start 2.0 s. A plain 9:16 clip's video takes about 7.7 s. The full render, when the fast way can't be used, 59 s (64 s before; the same file byte for byte).
- **Progress (K-D25).** The video's own progress is the project's from 5% to 80%: the fast way's frames read and encoded, the full render's time against an estimate from the window's length and frame rate (`build.time_share`: in step to 90% of it, then ever more slowly). It never goes back, and the page shows at most 99% until the editor has the project on screen; the page asks for the project at once when the engine says its build ended.
- The render runs on the build thread, beside the job worker: a build during a clipping run shares the graphics card with it.

**The canvas** is the base file's own width, height and frame rate.

**What goes on the timeline** (`plan_clip` works it out, `apply_clip_plan` makes the calls; tool names as in the editor's `docs/MCP.md`):

| Clip's setting | In the editor | How |
|---|---|---|
| the base video | `project_create(location=<id>/p, name, video)`, `media_import`, `clip_add` on the main lane at 0 | name = the clip's title made safe for a Windows folder (60 characters at most), else its hook, else "Clip <id>" |
| kept ranges (`edit.keep`), else the whole base | one piece per range, back to back | the base is cut at every edge (`clips_split`) and what is not kept is removed with the lane closing up (`clips_remove(ripple=True)`): a cut is taken back by dragging a piece's edge (choice 1) |
| `edit.mutes` | a muted stretch is its own piece with `muted: true` | cut at the same time as the kept ranges |
| `edit.mute_all` | every piece `muted: true` | |
| `edit.volume` | `clip_update(patch={"volume"})` on every piece | |
| `edit.speed` | `clip_speed(ripple=True)` on every piece | |
| `edit.fade_in`, `edit.fade_out` | `fadeIn` on the first piece, `fadeOut` on the last | |
| every piece | `clip_frame(framing="fill")` | |
| caption lines (`_clip_captions(row)`, counted from the clip's start) | `captions_add` at their program times | through the kept ranges and the speed (`video_editor.captions.remap_lines`): a line cut away is dropped, a line across a cut is held to what is kept. None when the clip has captions off |
| the other speaker's lines (second speaker on) | a second `captions_add` in that colour | marked as the render marks them (`paint_turns`, `tag_lines`) |
| caption style | `captions_add(style, offsetY)` | `color`; `uppercase` to `textCase: "upper"`; `font_size` (px at 1080x1920) to `fontSize = font_size / 1920`; today's black outline to `strokeWidth = 7 / 1920`; `font` to `fontFamily` when it is a font Windows has; `position` bottom / middle / top to `offsetY` 0.35 / 0 / -0.35, moved clear of a safe zone on a portrait clip |
| `edit.hook` | `title_add` at the top (`offsetY` -0.35) from 0 for its seconds | |
| `edit.music` (a file, or a free track's id) | the file copied to `media/`, a lane under every other (`track_add(bottom=True)`), `clip_add` from 0, repeated to the program's end and trimmed there, `volume` | |
| watermark text | `title_add` for the whole length at its corner, with its size, colour and opacity; `clip_transform(offsetX)` | the corner is approximate: 0.30 of the width and 0.44 of the height from the centre |
| watermark picture | copied to `media/`, a lane on top, the whole length, `clip_transform` (scale and corner offsets from its `scale` and `padding`), `opacity` | |
| then | `project_save`, `project_close(save=True)` | the folder's lock is let go: the window can open it |

**What is not carried**, as `notes` codes (the UI shows a sentence for each; an unknown code is to be ignored):

| Code | Meaning |
|---|---|
| `framing_baked` | always for a clip: the AI's framing is part of the video and can't be re-aimed in the editor |
| `captions_plain` | the clip lights up words or animates captions: in the editor they are plain text. Only when the clip has captions and one of the two is on |
| `font_default` | the caption font is not one Windows has, so the editor's own is used |
| `music_duck` | the music no longer dips under speech; its volume is the one set |
| `source_missing` | the original video is gone: the clip's finished file is the video, with its captions in the picture and its cuts fixed |
| `from_export` | the clip's file is already an editor export: that finished file is the video (as above), so the editor's earlier changes are kept |
| `watermark_still` | **added in this step:** a moving watermark is placed at one spot (the right edge's middle) |
| `music_missing` | **added in this step:** the clip's music file is gone, so there is no music in the project |

`source_missing` and `from_export` projects hold one piece and no caption, hook, music or watermark items. Never added: an end card.

### A whole video and a file

One piece, the whole source, shown whole in the chosen shape (`clip_frame(framing="fit")`: bars where the shapes differ), at the file's own frame rate (30 when it can't be read). The file is never copied. `DELETE /videos/{id}` works as today; the project then shows `media_missing`.

### The editor's automation, as the engine uses it

- Started as `[exe, "--mcp", "headless"]` (`kitty_editor.automation.command`, the one place that says so; tests put a stand-in there), with no console window, standard input and output as pipes, its log appended to the project's `build.log`.
- `CONCAT_MCP_ROOTS` = the project's own folder and the folders of the media it imports, and nothing else.
- MCP over standard input and output: `initialize` (protocol `2025-06-18`), `notifications/initialized`, then `tools/call`. Each call may take 120 s; on a timeout, a refusal that can't be read, or the editor ending, the process is killed and waited for. The session ends by closing its input (killed after 30 s).
- The engine uses none of the `host.*` API and none of the window's command-line options.

### Exports come back (E4)

`POST /editor/projects/{id}/exports`, `{"file", "export_folder"}`.

- `file` is any name the editor's export sheet can write (its `file_name()`): none of `/ \ : * ? " < > |` or a control character, no space at either end, not only dots, at most 255 characters, ending `.mp4`, `.mov`, `.mkv` or `.webm`; dots are fine anywhere (`Wait... what happened.mp4`, `.intro.mp4`). The editor's own unfinished work files (`.<name>.concat-video|audio|muxed.<ext>`) are refused. Else 400 (K-D6).
- The file must be a playable video: ffprobe finds a picture and a length above zero (`registry.probe_seconds`). An empty, broken or sound-only file answers 500 "The export isn't a video Clips Kitty can play." and stays in `out/`; nothing changes (K-D7). `out/<file>` must be a regular file directly in this project's `out/` (not a link); else 404. Nothing outside `out/` can be named, another project's file included.
- A name already taken in (recorded in `exports`, no longer in `out/`) answers 200 with the recorded answer and `again: true`.
- One take-in per project at a time; a report while one runs answers 409 `busy`.
- The answer: `{"result", "path", "folder", "file", "seconds", "again"}`, plus `clip_id` when a clip was involved and `reason: "clip_changed"` for `kept_beside`. `seconds` is the file's real length (ffprobe), kept in the manifest.

| Case | What happens | `result` | Events |
|---|---|---|---|
| a clip's project; the clip's row is on `clip.file` with the same `file_stamp` | under the clip's lock (`server.jobs.clip_lock`, which a re-render holds too): the export moves to `data/clips/projects/<id>-<n>.<ext>`; the row's `path` and `edited_at` are set; only then the old file is removed (when it is inside the data folder) with the clip's Quick edit preview; the manifest moves `clip.file` and `clip.file_stamp` to the export | `replaced_clip` | `{"type": "clip_file_replaced", "clip_id", "project_id"}`, `{"type": "queue"}` |
| a clip's project; the clip changed since | the export moves to `data/clips/projects/` and nothing else happens | `kept_beside` | `{"type": "editor_export", "project_id", "result", "path"}` |
| no clip to re-point (a whole video, a file, a deleted clip) | the export moves to `export_folder`, else the Downloads folder the app's own Export buttons use (Windows' Downloads known folder, wherever the person moved it; K-D22), else `data/clips/projects/`, named `<project name> - <YYYY-MM-DD>.<ext>` (copies get `-2`, `-3`). Across drives the copy's size is checked before the source goes | `saved_to_folder` | `editor_export`, as above |

- `export_folder` is used only in the last case. A network-share path, a relative path or a folder that can't be made is 400, with nothing moved.
- The clip's `start_s`, `end_s` and `render_opts` are **not** changed: they still describe the AI's window (what E5 guards). `GET /videos/{video_id}/clips` gives such a clip one more key, `"editor_export": {"seconds", "project_id"}` (`project_id` is `""` when the project was deleted); its `media_version` changes with the file as for every clip. A clip the editor never exported has no such key.
- Exporting twice: each export is one report; the second replaces the first as the clip's file, and the first is then the old file that is removed.
- A failure before the row is changed puts the export back in `out/` and answers 500 `{"detail": {"message"}}`; nothing is deleted and the clip is as it was.
- The app closed between an export's end and its report: `POST /editor/projects/{id}/open` takes in every export left in `out/`, oldest first, and returns the answers in `recovered`. Its body may carry `{"export_folder"}` (the app sends the one from Settings), used as above for an export with no clip to re-point (K-D10). The editor's unfinished work files are never taken, and a file changed in the last 3 seconds is left for the next time. A file that can't be taken in (not a video, a bad folder) stays in `out/`.
- A cancelled or failed export is never reported, so nothing changes. No end card is added to an export.

**Measured with the real editor** (headless, build 44025696954): a 35 s program at 1080x1920, 60 fps, exported in 14 s. While it rendered, `out/` held only `.<name>.concat-video.mp4` and `.<name>.concat-audio.m4a`; the final name appeared when it was complete (sampled every 0.2 s). Whether the window's export does the same is the fork's to confirm.

### Nothing of the person's is lost silently (E5)

**The rule:** a clip is an editor export when its file is directly in `data/clips/projects/` (`core.state.in_project_exports`: compared as the system compares paths, and by where links lead). It holds with no project left. `StateDB.project_export_of(clip_id)` is `""` for such a clip and `None` otherwise; the routes add which project it was (`kitty_editor.registry.project_of_export`: the `<id>-` at the start of the file's name, when that manifest is there).

| What | Rule |
|---|---|
| Apply `POST /clips/{id}/render`; the chat edit `POST /clips/{id}/ai-edit`; the caption save `PUT /clips/{id}/captions`; "Apply edits & upload" `POST /clips/{id}/publish` with `render_first` | 409 `{"detail": {"message", "reason": "project_export", "project_id": "<id>" or null}}` before anything is queued or any AI is asked, unless the body has `"replace_project_edits": true` (`render_first.replace_project_edits` for publish). `server.api.project_edits_kept` |
| the queue's `render` job | asks again when it runs, under the clip's lock: it fails with a plain sentence unless its payload has `replace_project_edits` (which the four routes add only when the person chose so) |
| Export in other languages, `POST /translate`, stage `export` with burned subtitles, a dub or subtitle files | 409 `{"detail": {"message", "reason": "project_export", "separate_file": true, "clip_ids", "count", "total", "project_id"}}` unless `"without_project_edits": true`. A batch is asked about once; `project_id` is set only when one clip is affected and its project is there. `server.api.project_edits_left_out` |
| the `translate` job | asks again when it runs, before any AI is set up and before anything is written |
| `POST /translate` for text only (the review stage, the post text), or only "include the video" | not asked: nothing is made from the clip's window |
| a forced run of the video; story reels made again (`core.pipeline._register_clip`, `_sport_reels`) | the clip stays on its export (`_keeps_project_export`); the fresh render stays on disk for the run |
| Make clips again | the clip counts as edited (`edited_at` is set at take-in) and is kept |
| Storage clean-up | never sweeps `data/clips/projects/` or `data/editor/`; a download a manifest's `media` names is not freed as an orphan |
| everything that only reads the clip's file (exports in a size, every publish without `render_first`, campaigns, Discord, watch) | unchanged: they use the export |

The messages (the UI shows its own strings and reads only `reason`): "This clip was changed in the editor. Applying changes here starts again from the clip as it was before, so those changes would be lost." and, for separate files, "This clip was changed in the editor. The new files are made from the clip as the AI made it, without the editor's changes."

A clip the editor never exported has no file in `data/clips/projects/`: each rule is one path comparison that says no, and every render, argv snapshot and golden file is as before.

**Ported from the hand-built editor (commit b43d905)**, with the smallest changes that keep the behaviour:

| From | Taken | Changed |
|---|---|---|
| `core/state.py` | `PROJECT_EXPORTS_FOLDER`, `in_project_exports`, `project_export_of`, `project_export_clips`, `data_dir` | `project_export_of` is the file test alone (no `project_exports` table) |
| `server/api.py` | the four request fields, `project_edits_kept`, `project_edits_left_out`, their calls in the caption save, the chat edit, Apply and `/translate` | new sentences; the project is looked up from the file's name |
| `server/youtube_api.py` | `RenderFirst.replace_project_edits` and its check | none |
| `server/jobs.py` | `clip_lock`, `PROJECT_EXPORT_KEPT`, `_rerender_clip` taking the lock and asking again, the translate job's question | without the export compiler's hooks (`before_register`, `while_waiting`, `render_opts_replace`) |
| `core/pipeline.py` | `_keeps_project_export` and its two calls | none |
| `core/housekeeping.py` | the exports folder is never an orphan clip | manifests read as plain JSON in place of the `projects` tables |
| `tests/test_step8_routes.py`, `tests/test_step8_engine.py` | the tests under "A clip whose file is a project export" and "Everything made from a clip's own window" | `tests/test_kitty_editor_guards.py`, rewritten for the file rule |

### Choices made where the contract was open

1. **Pieces are cut out of the whole base** (`clips_split`, then `clips_remove` with the lane closing up), not added and trimmed one by one. The result is the same (each piece keeps its handles, so a cut is taken back by dragging), with fewer calls and no id to look up per piece. Checked against the real editor: the removal closes the lane in one call.
2. **An export keeps its own extension** (`.mp4`, `.mov`, `.mkv`) when it is moved (`registry.unique_target`); `server.editor_lane.unique_target` only knows `.mp4`.
3. **`install()` takes one more argument,** `clip_captions`, the app's own `_clip_captions`, so the engine reads a clip's caption lines the way the Captions tab does.
4. **A re-rendered clip's projects follow its new id** (`registry.follow_clip_id`, called from `server/jobs.py` inside the function, next to the row's own `follow_clip_id`). Without it every project of a clip changed in Quick edit would say "deleted" instead of "changed".
5. **`from_export` whenever the clip's file is already an editor export,** also for `fresh: true`: re-making the base from the AI's window would drop the editor's earlier changes without a word.
6. **`captions_plain` only when something is really not carried** (the lit-up word or an animation is on, and the clip has captions).
7. **The same file from the PC in the same shape reopens its project,** as a Library video does.
8. **A failed project is made again by either open route;** a project left `building` by a stopped engine is shown as failed.
9. **Two more note codes,** `watermark_still` and `music_missing`.
10. **Short music is repeated** to the program's end (today's render loops it); ducking is not carried (`music_duck`).
11. **Recovery never takes a file whose name starts with a dot or that changed in the last 3 seconds:** those are the editor's own files of an export still running.
12. **`kept_beside` also sends an `editor_export` event,** so a window that missed the answer can still tell the person.
13. **With no export folder given,** what the app's own Export buttons use when Settings has none (`lib/exportFolder.ts`: Electron's `app.getPath('downloads')`, Windows' Downloads known folder): the main process sends that folder when the page gives none or one that is not a full path, and the engine, given none, asks Windows for the same folder (`intake.downloads_folder`, SHGetKnownFolderPath), else `~/Downloads`, else `data/clips/projects/` (K-D22).
14. **Caption fonts by name work for fonts Windows has:** checked with the real editor's `preview_frame` (Arial, Impact and Courier New each draw differently; a name nobody has draws as Arial does).
15. **The safe-zone row:** `0.5 - (the zone's edge + 20 px + the font size) / 1920` from the centre, never further out than the plain row.

### Tests

| File | What | CI light install |
|---|---|---|
| `tests/test_kitty_editor_engine.py` | the resolver; the records; `plan_clip` (kept ranges, mutes, speed, program-time captions, style, notes); projects built through the stand-in; the automation's failures; take-in for every case, twice, told twice, bad names, a crash; Storage | runs |
| `tests/test_kitty_editor_routes.py` | every route's guard; the status words; opening a clip, a video, a file; path refusals; list, reopen, delete; the exports route; the base render's options | skips (needs FastAPI) |
| `tests/test_kitty_editor_guards.py` | E5 | skips (needs FastAPI) |
| `tests/test_kitty_editor_real.py` | a clip project and a whole-video project built with the real editor and read back with `timeline_read` | skips unless Windows, `CLIPS_KITTY_EDITOR_DIR` and `CLIPS_KITTY_EDITOR_TEST_VIDEO` are there |
| `tests/test_editor_import_guard.py` | extended: nothing the pipeline loads imports `kitty_editor`; the package is standard library only; the database and the worker never import it at their top | runs |
| `tests/fakes/fake_kitty_editor.py` | the stand-in: the editor's MCP tools the builder uses, with the same arithmetic for a cut, a removal, a speed change and a trim | |

### Left for later

- **K3:** bundling the editor to `resources/editor`, its size, signing, and the data folder inside the Store package.
- The builder was developed against the unbranded build 44025696954 (its program is `concat.exe`, so the resolver does not find it: `GET /editor/status` says `missing`). Checked again with the branded build 44399491727 (`clips-kitty-editor 0.2.11`): `GET /editor/status` says `found: true`, `tests/test_kitty_editor_real.py` passes (2 tests), and a clip project built through the routes from the 45 s window of the test video was `ready` in 89.6 s (the base render, with the tracking models loading) and read back with `timeline_read` as four pieces (10 s, 5 s, the 1 s muted stretch, 19 s; 35 s in all), the hook, the watermark text and four caption lines at their program times, with the folder's lock let go.
- Progress while the base renders is two steps (5 %, then 80 %): the render gives no figure of its own.
- A build runs beside a clipping run and shares the graphics card; it is not queued behind the job worker.
- The watermark's corner and size are approximations; a moving watermark stands still.
- Caption lines keep their text and times; the lit-up word, animations and Clips Kitty's own bundled caption fonts are not carried.
- A `.mov` or `.mkv` export taken in as a clip's video keeps its extension; whether the Library's player shows it was not tried.
- An export kept beside a changed clip stays in `data/clips/projects/` until the person removes it: Storage never sweeps that folder.
- K4 to K8 as planned.

## The app

### What the app does and does not do

- The main process (`ui/src/main/editor.ts`) is the only part that starts, talks to and stops the editor. It asks the engine where the program is (`GET /editor/launch`) and where a project lives (`GET /editor/projects/{id}`); the page only ever names a project by its id.
- The page (`ui/src/renderer/src/pages/Editor.tsx`) shows recent projects and the two ways to start one, or, with a project open, a 36 px strip of its own and the area the editor covers.
- Nothing of the editor is shown anywhere while `GET /editor/status` says `found: false` (read once at start, `lib/editorStatus.ts`), or when the window has no editor bridge (the browser shim, an older preload). The app then looks exactly as before.

### Files

| File | What |
|---|---|
| `ui/src/main/editor.ts` | start, switch, close; `host.state` polling; exports handed to the engine; IPC |
| `ui/src/main/index.ts` | `setupEditor(...)`, `watchEditorWindow(win)`; the quit path closes the editor first |
| `ui/src/main/updater.ts` | "Restart and install" closes the editor first (at most 3 s) |
| `ui/src/preload/index.ts` | `window.studio.editor` |
| `ui/src/renderer/src/pages/Editor.tsx` | the page, the strip, the area and its rectangle |
| `ui/src/renderer/src/lib/editor.ts` | the page's state outside the page; `openInEditor`, `openEditorProject`; notices; notes |
| `ui/src/renderer/src/lib/editorStatus.ts` | found or not, read once |
| `ui/src/renderer/src/lib/editorCover.ts` | the cover gate (`useEditorCover`) |
| `ui/src/renderer/src/lib/projectExportPrompt.ts`, `components/editor/ProjectExportPrompt.tsx` | Quick edit's question before it makes an editor export again (ported from b43d905, new words) |
| `ui/src/renderer/src/lib/api.ts` | `editorApi` (the routes above), `renderRequest` / `newFilesRequest` (E5) |
| `tests/test_ui_editor_page.py`, `tests/test_ui_editor_main.py` | source tests |

### Starting the editor

`show(projectId, {exportFolder, destination})` (IPC `editor:show`): the same project only shows it; another project is opened with `host.open_project` (refused while an export runs: the page says "The editor is exporting. Open another project when it has finished."); if that fails the editor is closed (`host.close`, up to 10 s) and started again. With no editor running:

```
clips-kitty-editor.exe --embed <hwnd> --bounds x,y,w,h --api 127.0.0.1:<free port>
    --project <project_dir> --export-dir <export_dir> --export-name <export_name>
    [--export-note <destination>] --hidden
spawn options: detached: true, stdio: 'ignore', windowsHide: true, cwd: the Videos folder,
    env: the app's own plus CONCAT_EMBED_TOKEN (64 hex characters, new at each start),
    without ELECTRON_RUN_AS_NODE and without the engine's session secret
```

`destination` is the page's own line for the editor's Export sheet, shown read-only under the name (UX round; fork 211c751d): "Replaces this clip in the Library", "Saved next to this clip, which was changed since", "Export folder: <folder>" or "Goes to your Downloads folder". The main process keeps it per project (control characters out, at most 400 characters) and passes it as `--export-note` at start and as `destination` with `host.open_project`. An editor build older than 211c751d does not know `--export-note` and exits with code 2 ("The editor could not start.").

The sheet's name starts as `export_name`: a clip's name for a clip project, "<name> - <date>" for a whole video, a file, or a clip project whose clip was deleted (computed when the project is read, so the date is the day it is opened). What the person types there is the file's name in the export folder (copies -2, -3); a name equal to the project's plain name, as older builds started the sheet with, gets " - <date>" added.

Then `host.state` every 100 ms until it answers (at most 20 s). No answer, or `hostApi < 2`: "The editor could not start." `graphics.software`: it stays hidden and the page says "The editor cannot use this PC's graphics card, so it cannot be shown here." Otherwise `host.set_bounds`, and `host.set_visible` when the page wants it shown.

While it runs, `host.state` every 500 ms (every 2 s while hidden and not exporting). Each new `exports` entry (`seq` above the last seen): `done` goes to the engine as `POST /editor/projects/{id}/exports {file: <its file name only>, export_folder}`, matched to its project by the folder the editor reports (normalised: slashes and case); `failed` / `cancelled` call no route. Hidden for 10 minutes, not exporting, nothing unsaved: closed (`ended: "idle"`); coming back to the page starts it again on the same project.

Closing is always `host.close` (it saves first), then up to 10 s for it to leave. It is never killed: one whose window is gone saves and leaves by itself.

| Quit route | What happens |
|---|---|
| closing the window, the tray's Quit, File > Exit | `before-quit`: the model download question as before; then, when the editor is exporting, "The editor is still exporting. Quit and stop the export?" ("Keep exporting", the default, or "Quit"); then `host.close` and up to 10 s, before the engine and Ollama are stopped |
| `window-all-closed` | `host.close`, at most 3 s, then as before |
| Restart and install (updater) | `host.close`, at most 3 s, then the installer |
| Windows shutting down, an app crash | the editor's own watcher sees the window gone, saves and leaves |

A change of screen scale (`display-metrics-changed` with `scaleFactor`, or the window moved to a screen with another scale): the editor is closed and started again on the same project with new bounds (a child window gets no DPI message). Never while it exports (closing would stop the export): it is only re-placed until the export has ended, then restarted (K-D8).

### IPC (`window.studio.editor`)

Every call checks it comes from the main window's own page (as `plugin-session` does).

| Call | Does |
|---|---|
| `show(projectId, {exportFolder})` | start, switch or only show; `{ok, error}` with `error` `exporting`, `missing`, `no_project`, `cannot_start` |
| `setBounds({x, y, width, height})` | physical pixels of the window's client area; remembered for the next start |
| `setVisible(visible)` | `host.set_visible` |
| `focus()` / `blur()` | `host.focus` / `host.blur` (blur only when the editor has the keys, asked fresh) |
| `close()` | `host.close`, back to the lists |
| `reveal(projectId, index)` | Explorer on that project's recorded export (`-1` = the latest); the path comes from the engine |
| `ask({message, detail, buttons, defaultId, cancelId})` | a native question, drawn above the editor; the button's index |
| `getState()`, `onState(cb)` | `{running, projectId, visible, focused, dirty, exporting, software, error, ended, code}` |
| `onExport(cb)` | the engine's take-in answer (plus `project_id`), or `{result: "failed" or "cancelled", error}` |
| `onMeasure(cb)` | measure again (resize, maximise, restore, full screen, scale change) |

### The page

- Sidebar entry "Editor", right after "Clip Editor" (that label is unchanged), only while the editor is found.
- No project shown: "Recent projects" (kind as an icon with a tooltip, last opened, delete with a native confirm, a bar while a project is being made), "Edit a whole video" (a Library video and a shape), "Edit a file from your PC" (a shape, then the file picker).
- A project shown: the strip ("← Projects", the name, one-line notes, export notices, "Clip approvals (n)", and "Saved" / "Saving…" / "Exporting 42%" at the right) and the area. The area's rectangle goes to the main process from a `ResizeObserver` (on the area and the page), `window` resize, a `matchMedia` resolution change and `onMeasure`, at most once per frame, in physical pixels (x and y floored, width and height rounded, at least 1).
- "← Projects" calls `close()` (the editor saves and leaves); while it exports it first asks natively "The editor is still exporting. Close the project and stop the export?" ("Keep exporting", the default, or "Close").
- Leaving the page hides the editor (`setVisible(false)`); the editor and its work stay as they were. The shown project lives in `lib/editor.ts`, not in the page, so coming back needs no reload.

### The editor is always drawn over the web page (U3)

The editor is a native child window above the page. One gate, `lib/editorCover.ts`: `useEditorCover(open)` is a counted hold; the page asks for the editor to be shown only while a project is shown and no hold is taken. While covered, the area shows the page's plain background.

| Thing (drawn from `App.tsx` or the sidebar) | Rule |
|---|---|
| `SetupWizard` (full window) | holds the cover while open |
| `FeedbackHub`'s dialog (sidebar) | holds the cover while open |
| `ProjectExportPrompt` (mounted in `App.tsx`) | holds the cover while open |
| `ApprovalCard` (`ApprovalCards`, fixed at the bottom right; its "Contact your attorney" `<dialog>` lives inside a card) | while a project is shown the cards are not drawn; the strip shows "Clip approvals (n)", which shows them with a Close button and holds the cover until they are closed. A clip-approval notification clicked meanwhile opens them the same way |
| `UpdateBanner` | in the page's flow above the page: outside the area, and the rectangle follows it |
| `ModelSwitcher` | in the sidebar; its list is a native select popup, a window of its own |
| the page's own questions (delete a project, "This clip was changed in Quick edit…") | native message boxes (`window.confirm`, `editor.ask`) |
| export notices, project notes | in the strip |
| `title` tooltips, the Alt menu, native dialogs, notifications | windows of their own, above the editor |
| dialogs of other pages (`RemakeDialog`, `PublishAllDialog`, the "Edit whole video" popover, ...) | cannot overlap: the editor is hidden on other pages |

`tests/test_ui_editor_page.py` reads every component `App.tsx` imports and fails on one that draws a fixed overlay, a portal or a `<dialog>` without a rule here.

### Ways in (U4)

| Where | Control | Request |
|---|---|---|
| a Library clip card (Clip Editor page) | "Open in editor" (on hover, beside Publish), tooltip "Open this clip in the editor" | `{kind: "clip", clip_id}` |
| Quick edit's panel (`ClipEditor.tsx`) | "Open in editor" beside "Edit this clip" | the same |
| Quick edit's full page (`EditorModal.tsx`) | "Open in editor" in its header | the same |
| a Library video (Clip Editor page's toolbar) | "Edit whole video", then a shape and "Open" in a small popover | `{kind: "video", video_id, shape}` |
| the Editor page | a recent row; "Edit a whole video"; "Edit a file from your PC" | by id; video; file |

The new ways in say "Open in editor" (UX round: a plain "Edit" beside "Edit this clip", and in the header next to "Editing — <clip>", read as the same thing). Today's clip editor is unchanged; renaming its button "Quick edit" waits for the owner's OK, so the app's own words call it by the page it is on, "the Clip Editor" ("This clip was changed in the Clip Editor …"); "Quick edit" stays only in these docs and in code comments.

Every way in switches to the Editor page at once; a project being made shows its progress there (the page's list, or the strip when another project is open), and it opens when it is ready. A reopened clip project whose clip was changed in Quick edit since asks natively: "This clip was changed in the Clip Editor after this project was made." with "Open the project" (default) and "Start a new project from the clip" (`fresh: true`).

### When it cannot be shown

| Case | Words |
|---|---|
| not found | nothing is shown at all |
| cannot start (no answer in 20 s, `hostApi < 2`, or it ended within 20 s) | "The editor could not start." + "Try again" (tooltip "If it keeps happening, restart Clips Kitty.") |
| ended later by itself | "The editor closed unexpectedly. Your work is saved as you go." + "Open again" |
| drawing in software | "The editor cannot use this PC's graphics card, so it cannot be shown here." + "Try again" (tooltip "Updating the graphics driver usually fixes this.") |
| exporting, another project asked for | "The editor is exporting. Open another project when it has finished." |
| a project's notes | "Some things from the clip are not carried over." in the strip, the sentences in its tooltip, once per open |

After an export: `replaced_clip` "Saved as this clip's video."; `kept_beside` "This clip was changed in the Clip Editor meanwhile, so the export was saved next to it." + "Show in folder"; `saved_to_folder` "Saved to {folder}" + "Show in folder"; `failed` "The export did not finish." (the editor's sentence in the tooltip); `cancelled` nothing. The notice is said to a screen reader: the page keeps a `role="status"` region (and a `role="alert"` one for a failure) that is always there, and puts the notice's words in it. "Exporting n%" is shown at every poll but announced only at its start and each quarter (a separate polite region); the percent shown never goes back (the editor starts its fraction again at 0 for an export's last step). The Clip Editor page reads its clips again on `clip_file_replaced`, and a card of an editor export shows `editor_export.seconds` as its length.

### Keyboard and focus (U5)

- A click in the editor gives it the keys (the editor does that). A `pointerdown` or `focusin` anywhere in the app's own page calls `blur()` (one listener in `App.tsx`, only while an editor runs).
- The area is in the Tab order (`tabIndex=0`, `role="application"`, `aria-label` "Editor. Press Enter to use it, Ctrl+F6 to come back."): Enter or Space gives the editor the keys. Ctrl+F6 in the editor gives them back, and focus goes to "← Projects".
- When the editor takes the keys, no page button stays focused (Space would press it).
- The strip's state text is `aria-live="polite"`.

### Choices made where the contract was open (UI)

1. **The page switches at once** and shows a project's making there, rather than waiting where the request was made: a clip's project takes about 90 s to make (the base render), and the page is where its progress and any refusal are said.
2. **The page's state lives in a module** (`lib/editor.ts`), not in `App.tsx`'s React state: the same effect (it survives page switches), and a project being made keeps being made while the person is elsewhere.
3. **"Edit whole video" starts at 16:9.** The Library has no picture size for a video, so the "9:16 when taller than wide" default could not be read; the shape is chosen in the popover.
4. **`focused` is part of `onState`**, so the page can move focus to "Projects" after Ctrl+F6 (told apart from a click in the app by the click itself).
5. **`blur()` asks the editor fresh** whether it has the keys (the last poll can be half a second old), so a quick click in the app after one in the editor still gives the keys back.
6. **The engine's session secret is removed from the editor's environment** (the editor never needs it).
7. **"Show in folder" shows the project's latest recorded export** (`reveal(id, -1)`).
8. **The quit question is in English,** as `confirmQuit`'s is (the main process has no translations).
9. **A failed project is made again once** when it is opened again (the engine's choice 8); if that fails too, the page says why instead of trying again.
10. **Quick edit's "Nothing was made."** shows as a plain line, not as an error (`NothingMade` has an empty name).

### Tests (UI)

| File | What | CI light install |
|---|---|---|
| `tests/test_ui_editor_page.py` | t() on the page; the nav entry, the page and every way in behind the status; the cover list kept honest; hidden while covered and when left; bounds; keyboard and screen reader; native questions; Quick edit's question | runs |
| `tests/test_ui_editor_main.py` | the spawn options; the token only in the environment; never killed; every quit route closes it first; IPC from the app's window only, projects by id; switching; exports by name only; the preload | runs |

### Checked in the real app (2026-11-18, build 44439018733, the desktop sandbox)

| Check | Result |
|---|---|
| "Edit" on a Library clip card | the Editor page, the editor inside the window with the clip's video and its caption items; a new project was ready and shown 69 s after the click |
| playing | Space in the editor plays and pauses (00:00:07:16 while playing) |
| a cut, a caption change, Export | the clip's file became `data/clips/projects/<id>-1.mp4`; the strip said "Saved as this clip's video."; the Library card showed 25 s and its player the new picture (24.83 s, the changed caption) |
| Quick edit's Re-render on that clip | the question, first focus on "Open in editor"; Cancel left "Nothing was made." and queued nothing |
| "Apply anyway", then "Edit" again | the clip was made again (new id); "Edit" asked natively "This clip was changed in Quick edit after this project was made." above the editor; "Open the project" opened it as it was |
| "Edit whole video" on the 2-hour test video, 16:9 | shown 2.0 s after "Open" (switched with `host.open_project`), 1920x1080 at 30 fps |
| a file from the PC, 1:1 | shown 2.9 s after it was picked; its export went to the export folder as `own_clip_45s - 2026-11-18.mp4`, "Saved to <folder>" with "Show in folder" |
| resize to 1200x800, maximise, restore, minimise and restore, back to 1440x900 | the editor's rectangle equalled the area's to the pixel each time |
| page switches | hidden on Settings and Clip Editor (`visible: false`), shown again on return with the work as it was |
| Feedback dialog over the editor | the editor hidden while it was open, shown again after |
| a sidebar tooltip beside the editor | drawn above the editor |
| keyboard | Tab from "Projects" reaches the area; Enter gives the editor the keys; Space plays; Ctrl+F6 gives them back and focus lands on "← Projects"; a click in the editor takes the keys and a click in the app gives them back |
| closing the app right after a cut | the editor saved at quit (its project file was written at the moment of closing) and left in 2.7 s; reopened after a restart, the cut was there |
| no Clips Kitty Editor (the unbranded build only) | `found: false, reason: missing`; no "Editor" in the sidebar, no "Edit" on the cards, no "Edit whole video" |

The screenshots are kept outside the repository.

### QC fix round 1 (2026-11-18)

One line each; the records are in `state.json` (K1 and K2 `defects`).

- K-D1 (open, environment): the walk through the real desktop app was not done because drive C: was full; QC reruns it once C: has room, with the newest editor build.
- K-D2 (fixed): the quit-while-exporting question is in the app's language: the page sends its words through t() (`editor:words`, again on a language change); English until then.
- K-D3 (not changed): the export folder is the one from Settings, which only the page keeps (localStorage `export-folder`); the engine has no copy, and every existing export passes it the same way. The engine checks it.
- K-D4 (fixed): CI's compileall lists every engine package (campaigns, gaming, kitty_editor and remote_render added; not the vendored third_party).
- K-D5 (not changed, fork): the launch screen's two guide links open the upstream guides on a click only; the launch screen is never shown embedded.
- K-D6 (fixed): exports named with dots (a title such as "Wait... what happened?") and `.webm` exports are taken in; `safe_name` keeps a run of dots as one.
- K-D7 (fixed): an empty, broken or sound-only export never replaces a clip's video; it stays in `out/`.
- K-D8 (fixed): a screen-scale change never closes the editor while it exports: the restart waits for the export to end and asks again just before closing.
- K-D9 (fixed): clip approvals can be opened from the Editor page's strip again (App renders ClipUpdatesProvider around `Shell`).
- K-D10 (fixed): a close asks the editor's state once more and takes in exports that ended since the last poll; a reopen sends the export folder for recovered exports.
- K-D11 (fixed, fork daafd34f, build 44460212388; its brand test passed in the build, the line itself not yet seen on screen): Settings' "Concat's mirror" download source and the "Concat sounds" line keep the upstream name; branding them claimed they were this build's.
- K-D12 (fixed): a manifest write on Windows is retried while another thread reads the manifest (os.replace was refused, access denied).

### QC fix round 2 (2026-11-19)

- K-D13 (fixed, tests only): `test_project_routes.py::test_the_routes_are_internal` leaves the `/editor/` routes out of its "project" filter; `test_kitty_editor_routes.py` checks the served `/editor/` routes are exactly the editor's eight and all internal.
- K-D14 (fixed, fork 34fa4320, build 44475550509: its i18n and brand tests passed in the build, both lines are in the exe; the open Downloads list not yet seen on screen; supersedes K-D11): the Downloads source reads "Project mirror (GitHub)" and the sound shelf's offline notice "Could not reach the sound library. Check the connection and try again."; both are the brand layer's own lines (`brand::OVERRIDES`), in English for every language, the locales untouched.

### Fix round 3 (2026-11-19, before the owner's click-through)

- K-D15 (not changed in Clips Kitty): typing into the embedded editor. Real key presses arrive whole; only injected VK_PACKET characters are lost when embedded, and a press on the caption box's border takes the keyboard from the field (both in the fork). Nothing in the main process, the page or a key hook is involved: no accelerator, global shortcut or before-input-event handler exists, and the focus stays on the editor's child window for every key.
- K-D16 (fixed): taking an export in as a clip's video never deletes a file another clip row still plays (two AI windows with the same whole-second start and end share `clip_<start>-<end>.mp4`) or a Make clips again run holds: `intake._discard_old` asks `core.remake.file_in_use` with the clip's lock and the database held, after the re-point, as DELETE /clips/{id} does. recover() takes exports in through the same path; deleting a project removes only its own folder (its media are its own copies or hard links).
- K-D17 (fixed): an export reported while a reopen's recover() takes the same project's exports in is never reported as failed: the route waits up to `intake.LOCK_WAIT` (120 s) for the project's lock and then answers what was recorded (`again: true`); the main process asks again (6 times, 2 s apart) on a 409 or an engine it can't reach. A file is taken in once however often it is asked about.
- K-D18 (fixed): `tests/test_kitty_editor_routes.py` and `tests/test_kitty_editor_guards.py` run in CI's Plugin platform job.
- K-D19 (fixed, test only): the long export name in `test_an_export_named_with_dots_comes_back` is as long as the tmp folder lets `out/<name>` stay under 260 characters (up to 120), still with dots.
- K-D20 (fixed): the editor shows people's names, never ids: a clip project's video is `media/<clip title>.mp4`, a whole video's is a hard link `media/<video title>.<ext>` to the Library's download (the download itself where the drive can't link: the editor's automation has no display name for a media item, and a 2-hour file is never copied). Both names are `registry.safe_name`'s, the one the project and its export are already named with (no character Windows refuses, no device name such as CON, at most 60 characters; `core.pipeline._safe_name` keeps device names). A title "Music" or "Watermark" gets " video" added, so it never meets the project's music or logo copy. The ids stay in the manifest (`media`, `video_file`). Deleting a project removes the link only; the Library's download stays. While a project links a download, deleting that download from the Library frees its space only when the project is deleted too.
- K-D21 (fixed): "Saved as this clip's video." has no tooltip (it was the internal `data/clips/projects` folder, which a screen reader read too); "Saved to <folder>" and the kept-beside notice keep the file's path.
- K-D22 (fixed): with no export folder in Settings, a whole video's or a file's export goes where the app's own Export buttons put theirs, Windows' Downloads folder as Windows knows it (on the test PC that folder was on another drive than `~/Downloads`, the engine's guess before).
- K-D23 (fixed): Make clips again keeps a clip that has a project in Clips Kitty Editor, exported or not (counted as edited), read from the projects' manifests as plain JSON (`core.remake._editor_project_clips`); nothing else in Make clips again changed.

### First open (2026-11-19)

- K-D24 (fixed): the first "Open in editor" on a clip took 63 s, 62.5 s of it the full render of the caption-free video. A clip framed by the tracker's moving crop now has its video made from the clip's own framing (above): 15.1 s to the editor showing the project for the 24.8 s 60 fps test clip, of it 12.6 s the video. The floor on this PC: decoding the clip and the source once each to read the crop (about 3 s, CPU-bound), and one x264 encode of every 1080x1920 frame (6 to 7 s; this PC's driver gives Clips Kitty's FFmpeg no NVENC), plus the editor's own start (about 2 s).
- K-D25 (fixed): the Editor page's bar sat at 5% for the whole render and jumped to 80%; it now moves with the render, never goes back and stops at 99% until the editor shows the project.
- K-D26 (new): a clip's video is reused by a rebuild or a new project of the same clip while its window, framing and files are unchanged (`base_key`).

### Black-box fix round (2026-11-20)

- K-D27 (not changed, by choice): the Export sheet's "~31 MB" before a 30 s wide export that came to 2.9 MB. The figure is Concat's own estimate (unchanged in the fork): the bitrate the file is encoded at (8 Mbps for 1080p at 30 fps) times the length, plus the sound. x264 keeps to that average only when the picture needs it; the test videos where the gap showed are synthetic, almost still pictures (0.15 Mbps as made), so the encoder spends far less. On real footage the estimate held (S8: "~16 MB" for 16.7 MB), and the sheet says the written file's real size once the export is done. A better estimate would need each source's bitrate added to the editor's media probe and project model, a fork build and a new rule, for a figure marked "~" that only errs high on near-still video. No work is lost and no file is written wrong.

### Left for later (UI)

- The editor's own Export sheet, if left open when another project is opened, keeps showing the previous export's "Exported" line with the new project's size (the fork's; reported to it).
- Not caused for real, only read in the code: "The editor could not start.", "The editor closed unexpectedly.", software drawing, a screen scale change, the 10-minute idle close, quitting while the editor exports.
- The Alt menu bar was not seen: with the keys in the editor, Alt went to the editor.
- Typing quickly into the editor's caption text box from the test driver dropped characters (the change still landed); a person typing was not tried. GUESS: the driver's speed.
- The page's new strings are in English in every language until the translators add them (t() falls back to English).
- K3 bundles the editor; until then it is found only through `CLIPS_KITTY_EDITOR_DIR`.
