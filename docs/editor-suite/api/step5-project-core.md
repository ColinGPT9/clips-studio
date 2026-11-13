# Step 5: the project core

Engine only. Nothing a person can see changes in this step, and nothing in it runs unless one of the project routes below is called. It is what the new editor (steps 6 to 10) is built on.

- A **project** is one JSON document: a canvas, media, tracks of items, captions, branding and an export preset.
- It is changed only through **operations**: about 20 checked ones, the same set for the manual editor, the AI and MCP.
- Every change is kept as a **revision** with its exact undo, so undo and redo live on the server.
- An AI clip opens as a project and goes back to today's render options without loss.

Contents: [Files](#files) · [The document](#the-document) · [Operations](#operations) · [Storage](#storage) · [A clip as a project, and back](#a-clip-as-a-project-and-back) · [Routes](#routes) · [What changed in existing code](#what-changed-in-existing-code) · [Choices made](#choices-made) · [Left for later steps](#left-for-later-steps) · [Tests](#tests)

## Files

| File | What it is |
|---|---|
| `video_editor/project/model.py` | The document: its shape, its checks (`validate`), its one way of being written (`canonical`), time and the frame grid |
| `video_editor/project/patch.py` | The difference between two documents, and applying it |
| `video_editor/project/ops.py` | The operations: `apply(document, ops, env)`, `SPECS`, `describe()` |
| `video_editor/project/env.py` | What the operations ask the rest of the app (fonts, filters, Gaming layouts, export presets, which media exist) |
| `video_editor/project/migrate.py` | A document saved by another version: brought up to date, or opened read-only |
| `video_editor/project/importer.py` | An AI clip as a project |
| `video_editor/project/projection.py` | A project as an AI clip again, or the answer that it needs the export compiler |
| `video_editor/project/store.py` | Projects, revisions, media assets and exports in SQLite |
| `core/state.py` | The four tables (`PROJECT_TABLES`, made by `_migrate()`); `follow_clip_id`, `delete_clip` and `delete_video` know about projects |
| `core/remake.py` | Make clips again keeps a clip that has a project |
| `server/projects_api.py` | The routes, installed by `server/api.py` |

The package is standard library only: no numpy, FastAPI or pydantic. `env.py` and `importer.py` import other parts of the app (`video.captions`, `video.filters`, `core.modes`, `video.export_presets`, `video.encoding`, `video.audio_library`, `video_editor.timeline`) only inside the function that needs them, so loading the package loads none of them. `core/pipeline.py`, `core/state.py`, `core/remake.py` and `server/jobs.py` never import the package. `tests/test_editor_import_guard.py` checks all of this.

## The document

```json
{
  "schema": 1,
  "name": "A clip",
  "seq": 7,
  "canvas": {"width": 1080, "height": 1920, "fps": {"num": 30, "den": 1}},
  "media": {
    "m1": {"id": "m1", "kind": "video", "source": {"type": "video", "video_id": "vid1"},
           "duration_us": 3600000000, "width": 1920, "height": 1080, "fps": {"num": 30, "den": 1},
           "has_audio": true, "name": "", "extra": {}},
    "m5": {"id": "m5", "kind": "audio", "source": {"type": "library", "id": "music-doodles"},
           "duration_us": 0, "width": 0, "height": 0, "fps": null, "has_audio": true, "name": "", "extra": {}}
  },
  "tracks": [
    {"id": "V1", "kind": "video", "name": "V1", "mute": false, "extra": {}, "items": [
      {"id": "i2", "media_id": "m1", "source_in_us": 120000000, "source_out_us": 130000000,
       "start_us": 0, "duration_us": 10000000, "speed": 1.0, "volume": 1.0,
       "fades": {"in_us": 0, "out_us": 0}, "framing": {"crop": "center"}, "transform": {}, "color": {},
       "text": null, "link": null, "extra": {}},
      {"id": "i3", "media_id": "m1", "source_in_us": 135000000, "source_out_us": 150000000,
       "start_us": 10000000, "duration_us": 15000000, "speed": 1.0, "volume": 1.0,
       "fades": {"in_us": 0, "out_us": 0}, "framing": {"crop": "center"}, "transform": {}, "color": {},
       "text": null, "link": null, "extra": {}}]},
    {"id": "T1", "kind": "text", "name": "Text", "mute": false, "extra": {}, "items": [
      {"id": "i4", "media_id": null, "source_in_us": 0, "source_out_us": 3000000,
       "start_us": 0, "duration_us": 3000000, "speed": 1.0, "volume": 1.0,
       "fades": {"in_us": 0, "out_us": 0}, "framing": {}, "transform": {}, "color": {},
       "text": {"content": "Watch this", "role": "hook"}, "link": null, "extra": {}}]},
    {"id": "A1", "kind": "audio", "name": "A1", "mute": false, "volume": 1.0, "solo": false, "extra": {}, "items": []},
    {"id": "A2", "kind": "audio", "name": "Music", "mute": false, "volume": 1.0, "solo": false, "extra": {}, "items": [
      {"id": "i6", "media_id": "m5", "source_in_us": 0, "source_out_us": 25000000,
       "start_us": 0, "duration_us": 25000000, "speed": 1.0, "volume": 0.3,
       "fades": {"in_us": 0, "out_us": 0}, "framing": {}, "transform": {}, "color": {},
       "text": null, "link": null, "extra": {}, "role": "music", "loop": true, "duck": true}]}
  ],
  "audio": {"master_volume": 1.0, "normalize": true},
  "captions": {"media_id": "m1", "enabled": null, "style": null, "lines": null,
               "speaker_edits": [], "mutes": [], "muted_words": []},
  "branding": {},
  "export": {"preset": null},
  "context": {"origin": "clip", "video_id": "vid1", "media_id": "m1",
              "clip": {"start_us": 120000000, "end_us": 150000000},
              "job": {"job_captions": true},
              "imported": {"start": 120.0, "end": 150.0, "render_opts": {"...": "the clip as it was saved"}}},
  "extra": {}
}
```

This is the project of a clip that plays 120 to 150 seconds of its video with 5 seconds cut out, a hook and a free music track.

**Top level**

| Key | What it holds |
|---|---|
| `schema` | The version of the document's shape. 1 now |
| `name` | At most 120 characters |
| `seq` | The next id number. Ids are `i7` (item), `m7` (media), `c7` (caption line), so the same operations always give the same ids |
| `canvas` | `width`, `height` (even, 16 to 4096) and `fps` as a fraction `{num, den}`, 1 to 240. 29.97 is `{30000, 1001}` |
| `media` | By media id: `kind` (video, audio, image), `source`, `duration_us`, `width`, `height`, `fps`, `has_audio`, `name`. `source` is `{"type": "video", "video_id"}` (a library video), `{"type": "asset", "sha256"}` (an imported file), `{"type": "library", "id"}` (a free track or sound effect), or `{"type": "file", "path"}` (only from an imported clip's own music file; no operation can add one) |
| `tracks` | In stacking order: a later video or text track is on top |
| `audio` | `master_volume` (0 to 2) and `normalize` (even out the loudness of the finished video) |
| `captions` | `media_id` (whose time they are in), `enabled` (true, false, or null for the job's own choice), `style`, `lines` (null for the transcript's own), `speaker_edits`, `mutes`, `muted_words` |
| `branding` | `watermark`: a watermark, null for none, or absent for the job's own |
| `export` | `preset`: an export preset of `video/export_presets.py`, or null for Original |
| `context` | What the project came from, never edited: the clip, the keys the job decided, and the clip as it was saved |
| `extra` | Anything else. `extra.legacy` holds a clip's render options this version doesn't know |

**Tracks**

- **V1** is the main video track. It is gapless: its first item starts at 0 and each item starts where the one before ends. Every edge is on the canvas's frame grid.
- **V2 and up** are overlays, and **T tracks** hold text. Their items sit anywhere, in start order, and never overlap inside one track.
- **A1** is the sound of V1's own items. It holds no items of its own: each V1 item carries its volume and fades, and A1 carries the name, volume, mute and solo for all of them.
- **A2 and up** hold music, sound effects and voice.
- Every track has `id`, `kind`, `name`, `mute`, `items`, `extra`. An audio track also has `volume` (0 to 2) and `solo`.

The request for step 10 (several audio tracks and a mixer, imported sounds) fits with no change to this shape: any number of audio tracks (`add_track`), each with a name, volume, mute and solo (`set_track_props`), a master volume (`set_project_props`), and items that point at an imported file (`{"type": "asset"}`) or a library id (`{"type": "library"}`). `tests/test_project_ops.py` builds that document.

**Items**

Every item has all of: `id`, `media_id`, `source_in_us`, `source_out_us`, `start_us`, `duration_us`, `speed` (0.25 to 4), `volume` (0 to 2), `fades` (`in_us`, `out_us`, together no longer than the item), `framing`, `transform`, `color`, `text`, `link`, `extra`. An item on an audio track also has `role` (music, sfx, voice), `loop` and `duck`.

- A clip or a sound plays `source_in_us` to `source_out_us` of its media at `speed`. Its length is that stretch divided by the speed (on V1, rounded to whole frames).
- Text and pictures have no source to play: their source times are 0 and their length, at speed 1.
- Looped music plays its source stretch again and again for `duration_us`. `loop_offset_us` is how far into that stretch it starts; the key is only there when it isn't 0. Splitting looped music, or moving its start with `trim_item`, sets it, so every moment of the video goes on playing the same moment of the music.
- `framing` is `{crop}` (track, center, letterbox, bias_left, bias_right), `{gaming}` (a Gaming layout, or null for off) or `{fit, align}`. `color` is `{filter, adjust}`. `transform` is `{x, y, scale, rotation, opacity}`.
- `text` is `{content, role, font, size, color, position}`; `role` is text or hook.
- `link` is the id of an item this one belongs with, or null.

**Time**

- Every time is a whole number of microseconds, in a key ending `_us`. That is exact in JSON and in JavaScript (always below 2^53) and doesn't depend on the frame rate. There are no floats for time in a stored document.
- `model.frame_us(n, fps)` is where frame n starts, worked out from the frame number each time, never by adding frame lengths, so it can't drift. `model.frames_at(t, fps)` is the frame nearest a time.
- V1's edges are put on the frame grid whenever an operation is checked. `tests/test_project_ops.py` makes 10,000 edits at each of 23.976, 25, 29.97, 30, 50, 59.94 and 60 frames a second and checks every edge against a plain count of frames.
- Captions, speaker fixes and mutes are in the **source's** time (`captions.media_id`), so they stay on the same words when items are trimmed or moved.

**Checks and writing**

- `model.validate(doc)` returns what is wrong, one plain sentence each, or `[]`. It never changes the document.
- `model.canonical(doc)` writes the document the one way it is ever written: keys sorted, no spaces, text as itself, no NaN. The same document always gives the same bytes.
- Keys this version doesn't know are kept where they are.
- What the document carries as it was given (a Gaming layout, a colour adjustment, a caption style, an export preset, a caption line's own keys, anything under `extra`) is not looked into: a key ending `_us` inside one of those is not read as a time of the document's.

## Operations

```python
result = ops.apply(document, [op, ...], env)
```

- `apply` is pure: it never changes `document`.
- It returns `result.document` (the new document), `result.inverse` (operations that turn it back into the old one, byte for byte), `result.forward` (operations that redo it), `result.warnings`, `result.created` (`[{"kind": "item" | "media" | "track", "id"}]`), `result.changed` and `result.text` (the new document as written).
- Several operations in one call are all or nothing.
- An operation that can't be applied raises `ops.OpError`. Its text is one plain sentence naming the operation and the field, such as `set_item_props: "speed" must be a number from 0.25 to 4.` It also has `.op`, `.field` and `.message`. Nothing has changed.
- An operation is `{"op": name, ...fields}`. A field the operation doesn't have is refused. `ops.describe()` gives every operation and field as JSON; `ops.OPS_VERSION` (1) goes up when one changes meaning.
- One call takes at most 500 operations, a project at most 5,000 items and 64 tracks.
- What can't be saved is refused before anything is applied, by its operation and field: a number that is NaN or Infinity, text holding half of a character (a lone surrogate, which is what cutting a string in the middle of an emoji leaves), anything that is not JSON. The operations are kept in the history as they were sent, so this covers values an operation would have cleaned too.
- The document holds one zero. Every number an operation checks (a volume, a speed, a transform, a colour adjustment) is stored as a float and never as `-0.0`, so going between `0.0` and `-0.0` is no change. An object kept as given (an item's `extra`, a watermark, a Gaming layout) may hold either; the inverse tells them apart, so undo still gives back the bytes.

**The inverse.** The inverse of every operation, and of a whole call, is one `restore` operation holding the difference between the two documents (`patch.py`). It is exact whatever the operation did, and small: only what changed is in it. `restore` is internal: only the store's undo and redo may apply one. Sent by a caller, it is refused.

**What the operations ask the app.** Which fonts exist, which filters, which media, what this PC can encode: the operations ask `env` (`ops.Env`; `env.py`'s `AppEnv` is the real one, and tests bring their own). Nothing in `ops.py` opens a file.

| Operation | Fields (required in bold) | What it checks and does |
|---|---|---|
| `set_canvas` | `preset`, `width`, `height`, `fps` | A preset (9:16, 16:9, 1:1, 4:5) or an even width and height from 16 to 4096, not both. A new frame rate moves V1 onto the new grid: each clip's new length comes from its source and speed, so another rate and back gives the lengths it had. Warns when it moved items |
| `set_project_props` | `name`, `master_volume`, `normalize_audio` | Name at most 120 characters; master volume 0 to 2 |
| `add_media` | `video_id`, `asset`, `library` | Exactly one of the three. The server finds the media and its facts (`env.media`); a path is never accepted from the caller. Media already in the project is not added twice (a warning, and its id in `created`) |
| `insert_clip` | **`media_id`**, **`source_in_us`**, **`source_out_us`**, `track_id`, `index`, `start_us`, `speed` | A video or a picture on a video track. On V1 it goes at `index` (the end by default) and the rest move along. On an overlay track it needs `start_us` and must not overlap. The stretch must be inside the media and at least one frame long. Speed 0.25 to 4, never on a picture |
| `trim_item` | **`item_id`**, `source_in_us`, `source_out_us`, `start_us`, `end_us` | A clip or a sound is trimmed by its source times; text, a picture or looped music by `start_us` and `end_us`. A result shorter than one frame (V1) or with no length is refused. On V1 the items after it move. Off V1, moving the left edge keeps the right edge where it was. Fades are shortened to fit, with a warning. Looped music whose start moves keeps playing in time (`loop_offset_us`) |
| `split_item` | **`item_id`**, **`at_us`** | Cuts an item in two at a time in the video. On V1 the cut lands on the nearest frame and must be at least one frame from each end; nothing else moves and the video's length stays the same. The fade in stays on the left half, the fade out on the right. The right half of looped music goes on from where the left half stops (`loop_offset_us`) |
| `delete_items` | **`item_ids`** | On V1 the items after close the gap (ripple). Elsewhere nothing moves. An item that belonged with a deleted one (`link`) belongs with none |
| `move_item` | **`item_id`**, `track_id`, `index`, `start_us` | To another place on its track or to another track of the same kind. V1 takes `index`; any other track takes `start_us` and refuses an overlap. Never onto A1. A clip that leaves V1 for an overlay track is no longer a whole number of frames long: its length becomes its source stretch at its speed, to the microsecond (a picture keeps its length) |
| `duplicate_items` | **`item_ids`** | Each copy comes right after its original. Off V1 it is refused when there is no room |
| `set_item_props` | **`item_id`**, `speed`, `volume`, `fades`, `framing`, `transform`, `color`, `link`, `loop`, `duck`, `extra` | Speed 0.25 to 4 (not text, pictures or looped music; on V1 the length changes and the rest move). Volume 0 to 2. Fades fit inside the item. A filter goes through `filters.is_valid`, a Gaming layout through `modes.clean_gaming`. Framing and colour only on a video track; `loop` only on music. Music that stops looping plays its stretch once from its start, and is refused when that would overlap the next item |
| `add_track` | **`kind`**, `name` | video, text or audio. The new track gets the next id of its kind (V2, T2, A3) and goes after the last of its kind. At most 64 tracks |
| `remove_track` | **`track_id`**, `force` | Never V1 or A1. A track with items needs `force` |
| `set_track_props` | **`track_id`**, `name`, `mute`, `volume`, `solo` | `volume` (0 to 2) and `solo` only on an audio track |
| `move_track` | **`track_id`**, **`index`** | Puts a track at another place among the tracks of its kind (`index` counts from 0 within that kind). The order is the stacking order: a later video or text track is on top. Never V1 or A1, which stay the first of their kind, so a video or audio track takes 1 and up. Its items and settings go with it |
| `add_audio` | **`media_id`**, **`track_id`**, **`start_us`**, **`role`**, `source_in_us`, `source_out_us`, `duration_us`, `volume`, `loop`, `duck`, `fades` | Music, a sound effect or a voice on A2 and up. Only music can loop, and looped music needs `duration_us`. `duck` dips it under A1. Refuses an overlap on its track |
| `set_audio_mutes` | **`mutes`**, `muted_words` | Replaces the list of silenced or bleeped stretches (`mode` mute or bleep), in the source's time |
| `add_text` | **`content`**, **`start_us`**, **`duration_us`**, `track_id`, `role`, `font`, `size`, `color`, `position` | 1 to 500 characters once cleaned: `{`, `}` and `\` (ASS control characters) and other control characters are taken out. The font must be one of the caption fonts (`video/captions.py` `FONTS`, which includes `video/fonts.py`'s bundled ones) and is stored in the registry's own spelling |
| `set_text` | **`item_id`**, `content`, `role`, `font`, `size`, `color`, `position` | The same checks. null puts a look back to its default |
| `set_captions` | `enabled`, `style`, `lines`, `media_id` | A style takes only the keys the captions read, each checked. Lines are `[{in_us, out_us, text}]` in the source's time. A line keeps the `id` it came with (the first time that id appears); any other line gets a new one, never one the caller gave to another line |
| `edit_caption_words` | **`edits`** | `[{line_id, text, in_us, out_us}]`. Empty text removes the line |
| `set_speaker` | `in_us`, `out_us`, `speaker`, `clear` | Who is talking in a stretch (0 the main speaker, 1 the other), or forget every fix |
| `set_branding` | `watermark`, `clear` | A watermark, null for none, or `clear` for the job's own. It takes only the keys the render reads (`ops.WATERMARK_KEYS`: `type`, `text`, `image_asset`, `font`, `font_size`, `color`, `position`, `x`, `y`, `scale`, `opacity`, `padding`, `rotation`, `shadow`), each checked; any other key is refused, so a path can't be put in the document through it. An image is named by its asset name, never a path |
| `set_export_preset` | **`preset`** | Through `export_presets.normalize`. The codec must be one this PC can encode (`encoding.encoder_for`), else it is refused. "original" or null is Original |
| `batch` | **`ops`** | Several operations as one: all or none, one undo step. At most 4 deep |

A dry run (`store.apply_ops(..., dry_run=True)`, or `"dry_run": true` on the route) checks everything and returns what would happen (the document, the created ids, the warnings) and saves nothing.

## Storage

Four tables in the app's database, made by `_migrate()` in `core/state.py` on every database, and empty until a project route is called.

| Table | Columns |
|---|---|
| `projects` | `id` (a random hex id), `name`, `clip_id` (the clip it was made from; **no foreign key**), `video_id`, `doc_schema`, `head`, `cursor`, `tip`, `base`, `doc` (the document as it is now), `created_at`, `updated_at`. A clip has at most one project (a unique index on `clip_id` where it isn't null) |
| `project_revisions` | `project_id`, `seq`, `head`, `label`, `ops` (as they were sent), `forward` and `inverse` (the exact redo and undo), `snapshot` (the whole document, on every 25th revision and on the base), `coalesce`, `at`, `created_at` |
| `media_assets` | `sha256` (the key), `kind`, `name`, `ext`, `size`, `duration_us`, `width`, `height`, `fps_num`, `fps_den`, `has_audio`, `created_at`. The file is `data/media/assets/<first two characters>/<sha256><ext>`, so the same file imported twice is kept once |
| `project_exports` | `id`, `project_id`, `head` (the revision it was made from), `clip_id`, `path`, `preset`, `status`, `error`, `created_at`, `updated_at`. Step 7 writes these; nothing in this step renders |

**The three numbers on a project**

- `head` goes up by one with **every** change: an edit, an undo, a redo. A change says which head it was made on (`expected_revision`). When the project has moved on, the change is refused (`store.Conflict`, the route's 409) and nothing changes. A head is never reused, so an old number can never pass for the current one. This is the "revision" every route answers with.
- `cursor` is the revision the document is at. An edit adds revision `cursor + 1`. Undo applies that revision's inverse and steps the cursor back; redo applies the next one's forward operations. An edit made after an undo drops the revisions past the cursor (the redo tail).
- `base` is the revision undo stops at: where the project started, or where its oldest revisions were folded away. `tip` is the last revision.

Every change runs inside one `BEGIN IMMEDIATE` transaction, so two changes can never both see the same head, and the row is also updated only `WHERE head = ?`.

**Snapshots and the cap.** Every 25th revision holds the whole document, so any revision can be rebuilt from the nearest snapshot before it (`store.document_at`). A project keeps at most 500 revision rows. Past that, the oldest are folded into the next snapshot, which becomes the new base.

**Coalescing.** A small tweak of the same property of the same item (`set_item_props`, `set_text`, `set_track_props`, `set_project_props`, `set_canvas`, `set_captions` without lines, `set_branding`, alone in its call), made within one second of the tweak before it, joins that revision: the revision then goes from before the first tweak to after the last, and one undo takes the whole drag back. The head still goes up with each tweak. When the tweaks end where they began (a slider dragged out and back, a switch flipped twice), the revision is removed: there is no step left that undo would take back to the same thing. A tweak never joins across an undo, a redo or any other operation. The time is the caller's `now` (seconds), so tests don't depend on the clock.

**Another version's document** (`migrate.py`)

- An older `schema` is brought up to date when it is loaded, by pure migrations, one version at a time (`MIGRATIONS[n]` turns version n into n + 1; there is none yet). Reading saves nothing. The next change saves the document in the new shape and starts its history again from there: undo doesn't cross a change of format.
- A newer `schema` opens read-only: the document comes back as it is, and every change is refused with "This project was saved by a newer version of Clips Kitty, so it opens read-only here. Update Clips Kitty to change it." It is never rewritten.
- A document that can't be read at all is `store.Damaged`. Saved JSON is read strictly: a row holding NaN or Infinity (hand-edited or damaged) is damaged too, since it could never be written back.

**A project and its clip**

- A re-render gives a clip a new id. `StateDB.follow_clip_id`, which `server/jobs.py`'s re-render already called for the clip's posts, now moves `projects.clip_id` and `project_exports.clip_id` too. It does nothing when the clip has no project. If the new id already had a project (never expected: a re-render's row is new), that one stays and the other is left with no clip, never with the id of a clip that is gone.
- Deleting a clip or a video (`StateDB.delete_clip`, `delete_video`) leaves the project, with `clip_id` null. It still opens, changes and undoes. Its export records let go of the clip the same way.
- Make clips again (`core/remake.py`) counts a clip that has a project as edited, so it is kept.
- A re-render removes the old clip's row before the new one exists (`server/jobs.py`). If it stops in between, or registers no new row, the project's row still names the old id. A clip's id is never used again (`AUTOINCREMENT`), so nothing else is ever reached through it, and `store.get` reports `clip_id` as null whenever the clip's row is gone. The project opens by its own id either way.

## A clip as a project, and back

**`importer.import_clip(clip, media)`** lays a clip (`{video_id, start, end, render_opts, title}`) out as a project:

| In the clip's render options | In the project |
|---|---|
| the window, `edit.keep`, `edit.speed` | V1 items (source times of the video) and their speed |
| `edit.volume`, `edit.mute_all` | A1's volume and mute |
| `edit.fade_in`, `edit.fade_out` | the first item's fade in, the last item's fade out |
| `edit.mutes`, `edit.muted_words` | `captions.mutes`, `captions.muted_words`, in source time |
| `edit.music` (a file, or a free track by its library id) | media and one looped item on A2 |
| `edit.hook` | a text item with role "hook" on T1 |
| `crop`, `gaming` | each V1 item's `framing`, verbatim |
| `filter`, `adjust` | each V1 item's `color`, verbatim |
| `captions`, `caption_style`, `caption_lines`, `speaker_edits` | `captions.enabled`, `.style`, `.lines`, `.speaker_edits` (in source time) |
| `watermark` | `branding.watermark`, verbatim |
| `normalize_audio` | `audio.normalize` |
| what the job decided (`profile`, `podcast`, `vertical_live`, `sport`, `speaker_turns`, `job_captions`, `stop_by`, `sport_screen`, `sport_moment`, `framing`, `whole`, `reel`, `of`) | `context.job`, verbatim |
| anything else | `extra.legacy`, verbatim |

The edit is read the way the render reads it (`EditList.from_dict`: ranges sorted, merged and clamped), so the project shows what the clip really plays. The clip as it was saved is kept whole in `context.imported`.

**`projection.to_clip(document)`** answers one of:

- `{"branch": "A", "start", "end", "render_opts"}`: the project still says nothing today's render can't do, so today's `render` job can make it (the export compiler's Branch A).
- `{"branch": "B", "reasons": [...]}`: it needs the export compiler of step 7, and why, in plain sentences.

For a project nobody changed, Branch A's answer is exactly the clip it came from: `to_clip(import_clip(x))` gives `x`'s start, end and render options back as they were saved, whatever form they were saved in. `tests/test_project_projection.py` checks this on 56 clip shapes, each with four sets of facts about the source video (none, 29.97 frames a second, 60, and unreadable): the render options each of the 15 step-0 pipeline snapshot cases saved, the edits the editor saves, what a re-render adds, and the raw forms a clip's row can hold.

For a changed project, the project is read as pieces (window, cuts, speed, sound, hook, music, look, captions, mutes, watermark) and so is the clip it came from, imported again. A piece that reads the same in both keeps the value the clip was saved with. Only a piece that differs is written new. When the first item's start or the last item's end moved, the window moves and every time counted from its start is written again.

**What needs the compiler (Branch B):** an overlay; text that isn't one plain hook at the start; a sound besides one looped music item from the start; a bleep; a soloed track; a changed master volume; a different canvas size or frame rate; a transform; an item with its own volume; items at different speeds, looks or framings; a speed outside 0.5 to 3; a fade between two items or longer than 3 seconds; parts out of the video's order or repeated; a part shorter than a quarter of a second; a `fit`/`align` framing; a project not made from a clip. The export preset never decides: an export applies it to the finished clip.

Nothing in this step calls the render with Branch A's answer. `_rerender_clip` is unchanged (no `render_opts_replace`); step 7 adds that.

## Routes

All in `server/projects_api.py`, all internal (`server/api_stability.py`). Every one of them, reading included, needs:

1. the `X-Clips-Kitty-Session` header (the same secret the plugin, export and campaign routes take). Without it: 403.
2. an `Origin` the app itself sends (`projects_api.local_origin`). Anything else is refused with 403, even with the right secret.

| Origin | Answer | Who sends it |
|---|---|---|
| none | let through | a script, the app's main process, a plugin |
| `file://` or `null` | let through | the packaged app's window (see below) |
| `http://localhost:5173`, `http://127.0.0.1:5173` | let through | the dev server: the same two origins `server/api.py`'s CORS lets in |
| `http://` + the request's own `Host` (`http://127.0.0.1:8765`) | let through | the engine's own address. `Host` is already held to `127.0.0.1` or `localhost` by `TrustedHostMiddleware` |
| any other `http://` or `https://` origin, another port of this PC included | 403 | a web page |
| anything else (`file:///C:/page.html`, `chrome-extension://…`, text that is no origin) | 403 | |

**What the packaged window sends.** `ui/src/main/index.ts` (`createWindow`) loads the dev server's URL when `ELECTRON_RENDERER_URL` is set (`win.loadURL`), and otherwise the built page from disk (`win.loadFile(join(__dirname, '../renderer/index.html'))`), with `contextIsolation: true`, `nodeIntegration: false` and no change to `webSecurity`. The renderer calls the engine itself (`ui/src/renderer/src/lib/api.ts`, `fetch` to `http://127.0.0.1:8765`). So in development the Origin is `http://localhost:5173`, and in the packaged app it is that of a page loaded from a file: Chromium writes such an origin as `file://` or as `null`, depending on its file-access settings. Which of the two the packaged app sends was read from the code, not captured from the running app, so both are let through. `null` is also what a sandboxed frame on a website sends; such a page is kept out by the session secret, which only the app's own window is given, not by this check. The Origin check is there for the other case: a page that somehow held the secret still carries its own `http(s)` origin.

| Route | Body | Answer |
|---|---|---|
| `POST /clips/{clip_id}/project` | none | The clip's project, made from the clip the first time (`"created": true`) and the same project after (`false`). The clip is not changed. 404 for no such clip |
| `POST /projects` | `{name, canvas, fps}`: `canvas` is "9:16" (the default), "16:9", "1:1", "4:5" or `{width, height}` | An empty project: V1, T1, A1 and A2 |
| `GET /projects/{id}` | | `{id, name, clip_id, video_id, revision, can_undo, can_redo, read_only, created_at, updated_at, document}` |
| `POST /projects/{id}/ops` | `{expected_revision, ops, dry_run, label}` | `{id, revision, changed, dry_run, document, inverse, warnings, created, can_undo, can_redo}` |
| `POST /projects/{id}/undo` | `{expected_revision}` | `{id, revision, changed, document, label, can_undo, can_redo}` |
| `POST /projects/{id}/redo` | `{expected_revision}` | the same |
| `GET /projects/{id}/revisions` | `?limit=` (100; at most 500) | `{revision, cursor, base, tip, revisions: [{seq, label, ops, created_at, current, undone, start, snapshot}]}`, newest first |
| `DELETE /projects/{id}` | | `{"deleted": id}`. The clip and the media assets stay |

**Errors**

| Status | When | `detail` |
|---|---|---|
| 400 | An operation was refused | `{message, op, field}` |
| 400 | Nothing to undo or redo; a bad canvas, frame rate or name | a sentence |
| 403 | No session header, or a foreign origin | a sentence |
| 404 | No such project or clip | a sentence |
| 409 | The project is no longer at `expected_revision` | `{message, current_revision}`. Nothing changed |
| 422 | The body isn't the shape above. `expected_revision` must be a whole number as JSON has it: `true`, `"1"` and `1.0` are refused | FastAPI's own |
| 423 | The project was saved by a newer version (read-only) | a sentence |
| 500 | The saved document can't be read | a sentence |

## What changed in existing code

- `core/state.py`: `PROJECT_TABLES` and their creation in `_migrate()`; `follow_clip_id` also moves `projects.clip_id`; `delete_clip` and `delete_video` set a project's `clip_id` to null before the clip rows go.
- `core/remake.py`: `_evidence` reads which of the video's clips have a project, and `_reasons` counts such a clip as "edited".
- `server/api.py`: installs `server/projects_api.py`.
- `server/jobs.py` and `core/pipeline.py`: unchanged. The re-render's existing `db.follow_clip_id(...)` call is what moves a project.
- `.github/workflows/ci.yml`: `tests/test_project_routes.py` joins the Plugin platform job (it needs FastAPI). The other project tests run on the CI light install.

A plain auto-clipping run never touches the new tables (`tests/test_project_store.py`), and the step-0 snapshots and golden renders are unchanged.

## Choices made

Where the plan was not specific:

1. **The inverse is a patch, not a mirrored operation.** Every operation's undo is the difference between the two documents, applied by the internal `restore`. It is exact for every operation, including ones with no natural opposite (a ripple delete, a batch), and it is what makes "byte for byte" hold.
2. **`head` is separate from the place in the undo chain.** With one number, an undo followed by a new edit would reuse a revision number for a different document, and a caller holding the old number would pass the check. `head` only goes up.
3. **The head document is stored on the project's row**, not rebuilt from snapshots on each read. Snapshots are for rebuilding older revisions and for the fold.
4. **A1 holds no items.** The sound of V1's items isn't a second copy of them to keep in step; A1 is their mixer strip.
5. **A clip has one project.** Opening a clip a second time gives the same project.
6. **A deleted clip leaves its project** (with `clip_id` null) rather than deleting a person's work. Step 8 decides how such a project is shown.
7. **Reading a project needs the session header too**, because a document can name a file on this PC (music a person picked for a clip).
8. **Undo doesn't cross a change of the document's format.** History starts again at the first change after an update.
9. **Coalescing window: one second, sliding** (each tweak within a second of the one before), so a slow drag is still one step.
10. **V1 lengths are whole frames; source times are not rounded.** A V1 item's length may differ from its source stretch by up to one frame. A split shares that difference between the two halves, so it never adds up.
11. **Speed limits are the plan's (0.25 to 4)**, wider than today's render (0.5 to 3). A project outside today's range is Branch B.
12. **Projection keeps the saved value of every piece that didn't change**, rather than writing the whole render options again from the project, so an edit to one thing never rewrites another and unknown keys ride along.
13. **The Origin check lets through no Origin, `file://`, `null`, the dev server and the engine's own address, and refuses every other `http(s)` origin** (the owner's answer, 2026-11-12). A page on another port of this PC is refused too: only the two dev-server origins that `server/api.py`'s CORS already names are let in. Steps 1 to 4 check only the session header; this check is new with these routes.
14. **The answer is written as JSON once** (`Response`), not passed through FastAPI's encoder, which took about 11 ms of an operation's time at 300 items.

15. **Looped music keeps its place in the music when it is split or its start is trimmed** (`loop_offset_us`, an optional key on a looped item, absent when 0). Without it the right half of a split started the loop again. A project with an offset is Branch B: today's render always starts music at its beginning.
16. **A project's export records follow its clip** through a re-render and let go of a deleted clip, like the project itself.
17. **Laying V1 out again keeps every item in step, not only the edited one.** Where a frame is not a whole number of microseconds (30 and 29.97 frames a second among them), the same number of frames is a microsecond longer or shorter at another place on the track. So when an edit moves the items after it, a picture's source times follow its length, and fades that filled an item are shortened by that microsecond, without a warning (nobody can see it). Fades shortened because the edit itself made the item shorter still warn.
18. **One zero, and a strict difference.** Checked numbers are never stored as `-0.0`; where an object is kept as given, the difference between two documents compares floats as they are written, not by `==` (which calls `0.0` and `-0.0` equal), so the inverse is exact there too.
19. **What can't be saved is refused up front** (NaN, Infinity, a lone surrogate), by operation and field, rather than cleaned: the operations are stored as sent, and a caller that sends half a character has a bug worth hearing about. A revision's `label` is the exception: half a character in it is left out.
20. **A watermark set through `set_branding` takes only the keys the render reads.** A watermark that came with an imported clip is kept as it was (the round trip is lossless), so the export compiler of step 7 must read a watermark's image only through `image_asset`, by safe name, and never a path from any other key.
21. **A drag that ends where it began leaves no undo step**, and switches coalesce like sliders: two quick flips of the same switch are no step, three are one.
22. **`move_track` keeps a track within its kind and V1 and A1 first.** It was added after the second review, which found no way to change the stacking order (5-D27); `OPS_VERSION` stays 1 because no released caller exists yet.

## Left for later steps

- **Step 6:** serving media, importing files into `media_assets` (the store function exists; no route yet), and pinning a project's sources.
- **Step 7:** the export compiler (Branch B), calling the render with Branch A's answer (`render_opts_replace`), writing `project_exports`, playing looped music from its `loop_offset_us`, and what a bleep sounds like.
- **Step 8:** a list of projects, and what a project whose clip was deleted looks like.
- **Step 7, no second shift:** when the window moved, Branch A's `render_opts` (`edit`, `caption_lines`, `speaker_edits`) are already counted from the returned `start`. `_rerender_clip` shifts those same keys itself when the window differs from the clip's row, so `render_opts_replace` must skip that shifting, with a test.
- **Step 7, speaker fixes before the start:** with the start moved later, `speaker_edits` can hold negative times, and the job's `speaker_turns` is carried as it was. Step 7 drops fixes that end before the new start, or tests that the caption render ignores them.
- **Step 8, edge fades:** a clip's fade-in and fade-out sit on the first and last V1 items. Deleting that item drops the fade without a warning; step 8 decides whether the editor carries it over or warns.
- **Speed:** at 300 items a saved edit takes 20-30 ms (median) and a drag 37 ms (95th percentile 40 ms); about 1 call in 100 takes longer, up to about 125 ms, cause not found. At 1,000 items a saved edit is 45-97 ms. Step 8 looks at this if dragging feels slow.
- One operation carrying a 5 MB string is refused only after about a second; a body-size limit is optional.
- A clip's window is kept to a hundredth of a second on its row, while Branch A's `start` and `end` can be finer after a trim. Step 7 decides the rounding.
- A project made from a clip with its own music file holds that file's path in the document (as the clip's render options do today).

## Tests

| File | What it checks | Runs on CI light |
|---|---|---|
| `tests/test_project_model.py` | The document's checks, stable bytes, whole-number times, patches | yes |
| `tests/test_project_ops.py` | Every operation: checks, inverse, edges; the frame grid over 10,000 edits at 7 rates | yes |
| `tests/test_project_property.py` | Seeded random operations: inverses byte-identical, a refused operation changes nothing, a batch is all or nothing, the store's undo and redo | yes |
| `tests/test_project_store.py` | Compare-and-swap, snapshots, the cap, coalescing, `follow_clip_id`, deleted clips, Make clips again, other versions' documents, the tables on an older database | yes |
| `tests/test_project_projection.py` | `to_clip(import_clip(x)) == x` on 56 shapes; Branch A after edits; Branch B's reasons | yes |
| `tests/test_project_env.py` | The real fonts, filters, Gaming layouts, presets and media lookups | yes |
| `tests/test_project_routes.py` | The routes through TestClient: session header, Origin, 409, dry run, refused operations | no (needs FastAPI) |
| `tests/test_editor_import_guard.py` | The package is standard library only and loads nothing heavy; the pipeline, the worker and the database never import it | yes |
