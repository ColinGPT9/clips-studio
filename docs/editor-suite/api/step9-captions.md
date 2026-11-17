# Step 9: captions and text as a layer you can see, drag and edit

Status: **parts A (the engine and this contract), B (the viewer layer) and C (the caption lane, the panels, "+ Text" and the export choices) done; checks next.** Everything here is used only by the new editor (switch "New editor (beta)" on). With the switch off, nothing a person sees changes, and a plain auto-clipping run, today's Apply, today's caption editing and translations render exactly as before: a caption style or text item without the new fields gives byte-identical ASS (below, [What stays the same](#what-stays-the-same)).

Contents: [The contract](#the-contract) · [Placing captions: x and y](#placing-captions-x-and-y) · [Placing a text](#placing-a-text) · [The viewer's ASS](#the-viewers-ass) · [The caption lane](#the-caption-lane) · [Exporting: burn and .srt](#exporting-burn-and-srt) · [Errors](#errors) · [What the UI sends for each action](#what-the-ui-sends-for-each-action) · [What stays the same](#what-stays-the-same) · [Choices made](#choices-made) · [The viewer layer (part B)](#the-viewer-layer-part-b) · [The lane, the panels and the export (part C)](#the-lane-the-panels-and-the-export-part-c) · [Left for later](#left-for-later) · [Tests](#tests)

## The contract

Everything of step 8 ([step8-editor.md](step8-editor.md)) holds: "header" is `X-Clips-Kitty-Session`, "token" the `?t=` inside an address the engine gave, every change goes through `POST /projects/{id}/ops` with `expected_revision`, a 409 means the project moved on.

### New fields

| Where | Field | |
|---|---|---|
| Caption style (`set_captions.style`, `document.captions.style`) | `x`, `y` | **New.** Optional numbers 0 to 100: the caption's centre, in % of the frame's width (`x`) and height (`y`). Both or neither. `null` for either (or leaving both out) means "not dragged": the caption is placed by `position` as before. Not in `DEFAULT_STYLE` and never added by the engine |
| Text item (`add_text`, `set_text`) | `position` | Still `"top"`, `"middle"`, `"bottom"` or `null`; **new:** also `{x, y}`, each a number 0 to 100, the text's centre in % of the frame. Exactly those two keys |
| Text item | `size` | Unchanged (8 to 400, at a 1920-tall frame): the resize handle sets it |
| Caption style | `font_size` | Unchanged (20 to 200 accepted, drawn 40 to 140 at a 1920-tall frame): the caption layer's resize handle sets it |
| `POST /projects/{id}/export` | `captions` | **New.** `"burn"` (default) or `"none"` |
| `POST /projects/{id}/export` | `srt` | **New.** `true` or `false` (default) |
| Export job `result` | `srt`, `srt_file`, `srt_error` | **New**, only when `srt` was asked: the `.srt`'s full path and file name, or `srt: null` and `srt_error` (a plain sentence) |
| Export job | `captions`, `srt` | **New:** the choices the export was made with |
| `urls` (the export's, the draft's and the caption lines' answers) | `captions_ass` | **New.** `/projects/<id>/captions.ass?rev=<r>&t=<token>` |

No new operation names: the lane uses `set_captions` and `edit_caption_words`; text uses `add_text` and `set_text` (step 5).

### New routes

All three are internal (`server/api_stability.py` lists none of them) and protected as step 7 and 8's project routes are: an Origin of this PC's own (no Origin, `null`, `file://`, the dev server; any other web origin is 403), and the header, or for the two `GET`s the project's token.

| Route | Who | Query / body | Answer |
|---|---|---|---|
| `GET /projects/{id}/captions.ass` | header or token | `?rev=` (absent: the current revision) | The ASS file the export burns, as text (`text/x-ssa; charset=utf-8`): [below](#the-viewers-ass). Headers `X-Clips-Kitty-Revision` and `X-Clips-Kitty-Ass-Fonts`. 404, 409 another revision, 423, 400 the captions couldn't be made (a sentence) |
| `GET /projects/{id}/captions` | header or token | `?rev=` | The caption lines in program time, for the lane and the transcript list: [below](#the-caption-lane). 404, 409, 423, 400 |
| `POST /projects/{id}/captions/source_times` | header | `{revision?, ranges: [{start_us, end_us}]}` (1 to 2000 ranges; whole microseconds, 0 or more) | `{revision, ranges: [{in_us, out_us} \| null]}`: each block placed on the lane as the source times to save. Changes nothing. 404, 409, 422 a body of another shape |

The signed address for JASSUB: `urls.captions_ass` in the answer of `GET /projects/{id}/captions` (and of the export and draft). JASSUB's worker can fetch it with no header. After every change, ask again with the new revision (the answer of each change has it).

## Placing captions: x and y

`video/captions.py` (`place_of`, `_layout`, `_placed_at`, `_at_point`), used by every caption render, but only the new editor ever writes `x` and `y`.

- **One place for all captions.** The style's `x`, `y` move every caption together, as CapCut's auto captions do. Per-line positions are not in this step.
- **What is written:** each caption's events start with `{\an5\pos(px,py)}` (centred on the point), the style's alignment is 5 with no vertical margin. `px = round(x% × width)`, `py = round(y% × height)`, at the canvas's own size (a 16:9 project's 50/50 is 960,540).
- **Kept inside the frame:** a caption is moved in only as far as its own size needs, so it never runs off the frame (its letters inside the 60 px side margins, its outline and shadow inside the top and bottom edges). The size is measured with the same measuring the safe zones use (`caption_fit`, the caption's own text, font, size and wrap); a caption that can't be measured (a font this PC doesn't have, Hindi, Arabic, an emoji) is guessed from its length. So near an edge a long caption may sit a little further in than a short one; in the middle of the frame every caption is exactly at x, y.
- **With a safe zone** (`safe_zone` tiktok, reels or shorts, a portrait clip): kept between the app's top bar and bottom bar, 20 px clear of each (40 px more at the bottom with slide), and left of the app's column of buttons. Never moved to the other side for you.
- **Line width:** the margins only decide how wide a line may be: 960 px on a 1080-wide frame (60 px each side), less the app's buttons on the right with a safe zone. Lines break as before.
- **Animations:** pop, bounce, fade and karaoke are drawn at the point; slide rises 40 px into it (`\move` from 40 px below to the point; an event that moves carries `\an5` only, never `\pos` too). The word highlight, karaoke's fill, the second speaker's colour and a safe zone's shrink (`\fscx`) are written exactly as before, after the point.
- **Where the viewer draws the box:** centre at x%, y% of the canvas, unless the caption was moved in; the truth is the `\pos` of the event on screen in the ASS ([below](#the-viewers-ass)), and the paused still.

## Placing a text

`video_editor/compose.py` (`text_place`, `_text_style`, `build_ass`).

- A text item whose `position` is `{x, y}` gets its own style with alignment 5 and each of its events starts with `{\an5\pos(px,py)}`, the point held on the frame (0 to width, 0 to height). A text whose centre is near an edge can run past it, as in CapCut; the UI can stop a drag at the edge if it wants.
- `size`, `font`, `color` and `role` are as before. A text without `{x, y}` is drawn exactly as before.
- A hook with a `position` is "styled" for `projection`, as any styled hook already was: its export goes through the compiler (Branch B).
- `transform` on a text item is still ignored by the export (step 7); the viewer must not draw it.

## The viewer's ASS

`GET /projects/{id}/captions.ass?rev=` is `compose.build_ass` exactly as the export runs it: the export's timeline with its end pad (`with_end_pad`, ED34), the captions mapped through the unpadded timeline, at the canvas's full size, with the settings' caption style and watermark when the project has none of its own. A test holds it equal, byte for byte, to the `export.ass` of an export of the same revision.

- **Layers:** captions are `Dialogue: 0`, text and the hook `1`, the text watermark `2` (step 7). Time 0 is the program's start.
- **Nothing to burn** (captions off, no text): a script with a header and no events, so the viewer draws nothing; never a 404.
- **Captions turned off** (`captions.enabled: false`, or the settings' switch): no `Dialogue: 0` lines; text still drawn. What the viewer shows is what a burned export shows.
- **Fonts:** `X-Clips-Kitty-Ass-Fonts` names the app's own fonts the script uses, comma separated (may be empty). `GET /fonts` lists every bundled font with the addresses of its files (`/fonts/<file>`, served to any page on this PC). Its `system` list (fix round 1, 9-D9) names the Windows caption fonts this PC has (Arial, Arial Black, Impact…: `video/captions.py` `WINDOWS_FONTS`), each with its regular and bold files, served by `GET /fonts/system/<file>` from this PC's Windows font folders (only the names in `server/fonts_api.py` `SYSTEM_FACES`; anything else 404; never from the internet). Those files and the `system` list are for the app on this PC only (9-D15): the project media routes' Origin check (`projects_api.local_origin`: no Origin, `null`, `file://`, the dev server) with the request's own Origin named back in `Access-Control-Allow-Origin`, never `*`; any other web page gets 403 and an empty `system`. The viewer hands JASSUB the ones a script names, so a caption in Arial is drawn live with the very file the burn uses; a PC without the font falls back to JASSUB's default, and the still stays the truth.
- **The still** (`GET /projects/{id}/still`) is unchanged and burns the same file.

## The caption lane

`GET /projects/{id}/captions?rev=` (`compose.caption_lane`):

```json
{"revision": 12, "saved": false, "burned": true, "enabled": null, "media_id": "m1",
 "style": {"font": "Arial", "font_size": 84, "...": "the style as drawn (DEFAULT_STYLE + the project's or settings')"},
 "program_us": 30000000,
 "spans": [{"src_in_us": 34780000, "src_out_us": 44000000, "start_us": 0, "end_us": 9220000, "speed": 1.0}],
 "lines": [{"id": "tx0", "in_us": 35000000, "out_us": 35900000, "text": "we did it", "speaker": 0,
            "pieces": [{"start_us": 220000, "end_us": 1120000, "text": "we did it"}], "hidden": false}],
 "urls": {"still": "…", "draft": "…", "captions_ass": "/projects/<id>/captions.ass?rev=12&t=…"}}
```

| Field | |
|---|---|
| `saved` | `true` when the project has its own lines (`captions.lines`); `false` when they are made from the transcript at each render (a clip's project starts so) |
| `burned` | whether an export burns captions now (the project's switch, else the settings' and the clip's job, step 7's `captions_on`) |
| `enabled` | the project's own switch: `true`, `false` or `null` (the job's own choice). The lane's Hide sets it |
| `style` | the caption style as drawn, including `x`/`y` when dragged |
| `spans` | the stretches of the captions' video on the main track, in program time, with their source times and speed: what the captions are mapped through. A block can only sit inside one |
| `lines` | **every** caption line in the source's time, in source order (saved lines in the order they were saved). `id`: the saved line's id, or `"tx<n>"` for a line made from the transcript (not saved yet; see below). `in_us`/`out_us`/`text`: the line as saved (source time). `speaker`: 1 for the other speaker's line when the style colours it. `pieces`: where it plays, in program time, with the words that play there (a line cut through by an edit has a piece on each side; a stretch played twice has two). `hidden`: no piece (its words are all cut out): the lane shows no block for it, the transcript list may show it greyed |

- **The same remap as the burn:** `pieces`, sorted by time, are exactly the export's caption events' lines (`compose.program_caption_lines`, which `build_ass` and the `.srt` use too). A test holds lane = burn after cuts, speed changes, reorders and a stretch played twice.
- **Times:** a piece is shown from `start_us` to `end_us`; the burn writes centiseconds (ASS), so it may start up to 5 ms off a frame boundary, as every caption always did.
- **Transcript lines (`saved: false`):** they are made again at every render from the transcript over what the main track plays now (as a clip's captions always were), so after a cut they are grouped afresh: none is ever `hidden`, and their ids `tx0`, `tx1`… name lines **of that revision only**. The first lane edit saves them all with those ids in the same call: `set_captions {lines: [{id, in_us, out_us, text}, …]}` (every line, from `lines`), batched with the edit (`batch`, one undo step). From then on the ids are kept, a cut hides a line instead of regrouping it, and the lane's selection survives.

### Program time to source time

`POST /projects/{id}/captions/source_times {revision, ranges: [{start_us, end_us}]}` (`compose.caption_source_range`):
- The range is placed in the span that plays its `start_us`; its end is held to that span's end (a block never reaches across a cut: the lane holds it there too).
- `in_us = src_in + ⌈(start_us − span start) × speed⌉`, `out_us = src_in + ⌊(end_us − span start) × speed⌋` (exact fractions). Mapped back, the start is never before the frame the block was put on and the end never after it, within 1/speed µs: **never a frame off**, at any speed and frame rate (tested at 0.25x to 4x, 23.976 to 60 fps).
- `null` for a range whose start is in no span (past the end, a picture, another video). `in_us >= out_us` after holding gives `null` too.
- The UI may do the same arithmetic itself with `spans` for the ghost while dragging; it sends the drop's range here and saves what comes back.

## Exporting: burn and .srt

`POST /projects/{id}/export {…, captions: "burn" | "none", srt: true | false}`:

- **`captions: "burn"`** (default): as every export so far.
- **`captions: "none"`:** no captions in the picture. Text, the hook and the watermarks still burn. Branch B: the compiler leaves the captions out of the ASS (`Inputs.burn_captions`). Branch A: today's re-render with `render_opts.captions = false` (as `projection` writes captions turned off); **the clip's saved render options then say captions off too**, as any Apply with captions off does.
- **Which branch, for captions** (fix round 2): a project made from a clip goes through the compiler (Branch B) when its caption style has `x` or `y` (`projection` reason "The captions are placed by hand.", 9-D11), and when the captions show (burned, or `srt: true`) and a caption line reaches over a cut or past the ends of what plays by more than 0.05 s (`compose.caption_line_cut`; reason "A caption line is cut through by an edit.", 9-D12). Today's re-render keeps such a line whole, every word over its whole span (`video_editor/captions.remap_lines`), while the lane, the live viewer and the `.srt` split it word by word (`compose.remap_captions`); the compiler burns what they show. Otherwise the branch rule is unchanged.
- **The `.srt` goes with its video** (fix round 1, 9-D1): when a later export replaces a clip's file (Branch B over the clip, or Branch A's re-render), or the clip is deleted, the `.srt` beside the old file is deleted too. Storage's "orphan clips" lists an export's `.srt` whose video is gone (beside a file a `project_exports` row names, or in the projects' exports folder under an export's name; never a translation's `<clip>.<language>.srt`, never the person's own export folder), and Free up deletes it.
- **`srt: true`:** `"<the export's file name>.srt"` is written beside the exported file (Branch B: `data/clips/projects/<id>-r<rev>.srt`, or `<project> - <day>.srt` in the person's export folder; Branch A: beside the clip's new file), whether or not the captions are burned. Lines and times are `compose.program_caption_lines`, the same as the burn's (through the unpadded timeline, so nothing in the end pad), each time to the millisecond, the text as the line has it (not upper-cased: the `.srt` is read by the platform's own player), written by `multilingual/subtitles.write_srt`. Times are within one frame of the burned captions (tested). The job's `result` gains `srt` (path) and `srt_file` (name).
- **No captions to save**, or the file can't be written: the export still finishes; `result.srt` is `null` and `result.srt_error` says why ("There were no captions to save as a .srt file.").
- **422** for any other value (`captions: "maybe"`, `srt: "yes"`).
- The job (`export` events, `GET /export/jobs/{id}`) shows `captions` and `srt` as asked.

## Errors

| Status | When | `detail` |
|---|---|---|
| 400 | `set_captions.style` with `x` without `y` (or the reverse), a value outside 0 to 100, not a number (true/false, text, NaN, infinite) | `{message, op: "set_captions", field: "style"}`, the sentence names the rule ("x and y go together…", "…a number from 0 to 100 (% of the frame)") |
| 400 | `add_text`/`set_text` `position` neither a name nor `{x, y}` in range | `{message, op, field: "position"}` |
| 400 | the captions or the ASS couldn't be made (a damaged document) | a sentence |
| 403 | no header or token, a foreign Origin | a sentence |
| 404 | no such project | a sentence |
| 409 | another revision than the current one | `{message, current_revision}` |
| 422 | a body of another shape (source times, the export's caption fields) | FastAPI's own |
| 423 | a project saved by a newer version | a sentence |

## What the UI sends for each action

| Action | Call | Undo |
|---|---|---|
| Drag the caption layer | `set_captions {style: {...the whole current style, x, y}}` while dragging or at the end | one step: within a second, calls that change the same style keys are joined (step 5 coalescing; the key is the style keys whose value changed, 9-D7) |
| Resize the caption layer | `set_captions {style: {..., font_size}}` (a whole number 20 to 200) | one step, as above; a resize right after a drag is its own step |
| Reset position | `set_captions {style: {...the style without x and y}}` | one step |
| Drag / resize a text | `set_text {item_id, position: {x, y}}` / `{item_id, size}` | one step per property (coalesced) |
| Edit a caption in place | `edit_caption_words {edits: [{line_id, text}]}` (saved lines; else batched with the save above) | one step |
| Edit a text in place | `set_text {item_id, content}` (a new line as `\n`) | one step |
| Retime / move a block | `source_times` for the drop, then `edit_caption_words {edits: [{line_id, in_us, out_us}]}` (a line an edit cut off: both ends, the other held to the stretch it plays in, and `text`, the words that play: `trimmedLine`, 9-D6) | one step |
| Split, merge, add a line | `set_captions {lines: [...]}`: the whole list with the change, ids kept (a new line without an id gets one); a line an edit cut off is split as the part that plays; Merge only when no cut lies between the two blocks (9-D5; greyed with a tooltip otherwise) | one step (never joined with another) |
| Delete a line | `edit_caption_words {edits: [{line_id, text: ""}]}` | one step |
| Hide the captions | `set_captions {enabled: false}` (`true` shows them) | one step |
| Add a text | `add_text {content, start_us, duration_us: 3000000, position: {x: 50, y: 50}}` (the default font: no `font`) | one step; `created` names the new item |
| Export | `POST /projects/{id}/export {…, captions, srt}` | |

## What stays the same

- **Byte for byte:** a caption style without `x`/`y` and a text without `{x, y}` give exactly the ASS they gave before. Checked three ways: the step-0 argv snapshots and the step-7 plan snapshots (unchanged, no `--update-snapshots`); the golden renders; and a scratch comparison of `caption_ass` from the step-8 commit's files and from this step's over 15,552 combinations (6 animations × highlight × 4 safe zones × 3 positions × 3 canvases × transcript or saved lines × second speaker × 3 fonts × 3 languages): all identical.
- `projection`: a style with `x`/`y` makes its project Branch B (fix round 2, 9-D11), so a dragged place is never written into a clip's render options, where today's editor would keep it and its top/middle/bottom choice would do nothing. A text with `{x, y}` makes its project Branch B, as any styled text did.
- Today's editor, auto clips and translations never write `x`/`y`.

## Choices made

1. **Clamping by the caption's own size, per caption** (ED47). Each caption keeps its centre at x, y and is moved in only as far as its measured size needs. Rejected: narrowing the line width around the point (a caption at x = 10% would wrap into a thin column), and holding the point to the middle of the margins (a caption dragged to the side could never be near it).
2. **`\pos` per event, alignment 5.** libass keeps the margins for the line width, so wrapping, the safe-zone shrink and pop/bounce's line measuring work unchanged.
3. **Program to source time through the engine** (`source_times`), with ceiling and floor so a block never lands a frame off; the lane gets `spans` to draw the ghost. Rejected: a new operation taking program times (the plan keeps step 5's operations), and leaving the mapping to the UI alone (floating point at 0.25x put a start one frame early in a test).
4. **Transcript lines get ids `tx<n>`** in the lane's answer, and the first lane edit saves them all with those ids in one call: the lane needs stable ids, and saving earlier would turn every clip's captions into saved lines just by opening the editor.
5. **The `.srt` is written beside the export, even with the captions not burned**, from the same remap; an `.srt` that can't be made never fails the export.
6. **The viewer's ASS route returns the export's own script** (with the end pad), not a separate "preview" build, so what is drawn live and what burns can't drift.

## The viewer layer (part B)

Captions and text on the viewer: seen live, selected, dragged, resized, typed into, moved with the keys (ED48).

### What a person gets

- **Live, as it will burn.** While the video plays or the playhead is dragged, JASSUB (libass in WebAssembly) draws the engine's own `captions.ass` over the picture at the program's time, with the app's bundled fonts. Paused, the engine's still lies over it as before ("pause shows the truth"). If JASSUB can't start, the bar under the viewer says "Live captions could not start here. Pause to see them as they will look." and the still keeps working.
- **Select.** Click a caption: the caption layer is selected (a box with four corner handles round the caption on screen; with no caption on screen, round the nearest one, faintly). Click a text: that text is selected. A text selected on the timeline is selected on the viewer too. Click elsewhere on the picture: nothing is selected. Each box can also be reached with Tab; focusing it selects it.
- **Drag.** The caption layer moves ALL captions together (the style's `x`, `y`); a text moves on its own (`position: {x, y}`). The words follow the pointer. From the start of a drag the two centre lines show (dashed); the box snaps to them, to the five named rows (labelled while snapped), the frame's edges, the chosen app's safe lines and the edges of what it draws, and the other layers on screen (edges and middles). Alt held turns snapping off.
- **The app's screen while dragging (9:16 only).** With the picker on TikTok, Reels or Shorts, that app's screen is drawn as always. With "No overlay", the app last chosen in the picker (TikTok before any) is drawn faintly during the drag only. The apps' screens are 9:16, so on other canvases none is drawn (the picker already says why in its tooltip).
- **Amber outline.** A caption or text whose box runs under the app's buttons, its text at the bottom or its top bar gets an amber outline, with a tooltip naming the app and the part ("Under TikTok's buttons"). Shown when paused and while dragging, for the picker's app (for the faint one during a drag). Nothing is moved for you.
- **Resize.** A corner handle sets the size, growing from the middle: the captions' `font_size` (held to 40 to 140, the sizes the engine draws) or the text's `size` (8 to 400). A caption placed by name (no `x`, `y`) grows from where it stands and keeps no point.
- **Keyboard.** With a box focused: the arrow keys move it 1% of the frame (Shift: 5%), sent as one change when the keys rest; the new place is announced ("Moved to 52% across, 70% down"); Enter opens the box to type. Elsewhere the arrow keys step frames as in step 8.
- **Type in place.** Double-click (or Enter on a focused box): a box over it, in its font and size, with the words. Enter keeps, Esc cancels, a click elsewhere keeps; Shift+Enter is a new line in a text (a caption is one line). The caption's own words are hidden while typing. The fix lands on that line (`edit_caption_words`, batched after saving the transcript's lines with their `tx<n>` ids when not saved yet) or that text (`set_text content`). The playhead stays put. With no caption on screen, the nearest one's line is the one edited. Emptying a caption removes its line (the contract's rule).
- **One change per drag, resize, key burst or edit** (one undo step); a 409 is handled as everywhere in the editor (reloaded, said in plain words).

### How it is built (for part C)

| Piece | What it is | Part C uses it for |
|---|---|---|
| `components/editor/useCaptionLayer.ts` | `useCaptionLayer(projectId, revision)` → `{lane, ass, error}`: `GET /projects/{id}/captions` then its `urls.captions_ass`, at each revision (60 ms settle; an older answer never replaces a newer; a 409 is ignored). Called once in `EditorScreen`, passed to the viewer as `layer` | the lane's blocks and the transcript list: pass `layer.lane` on; `lane.revision === project.revision` means fresh |
| `components/editor/CaptionTextLayer.tsx` | the interactive layer; exports `type LayerTarget = {kind: 'captions'} \| {kind: 'text', id}` | the selection type |
| `EditorScreen` state | `captionLayer` (boolean) + `selected` (a text item id); `selectLayer(target)`, `selectItems(ids)` (selecting items lets go of the caption layer); `layerSelection` is derived; Escape clears both | a lane block's click: `selectLayer({kind: 'captions'})` (add a line id to `LayerTarget` if the lane needs one selected); the Inspector's caption panel shows when `captionLayer` |
| `components/editor/LiveCaptions.tsx` | JASSUB on its own canvas, canvas mode, drawn at `playhead` on every move; props `ass`, `playhead`, `width`, `height`, `hidden`, `onState` | nothing new: every op the lane sends changes the revision, and the new script follows |
| `lib/captionLayer.ts` | pure, no imports, tested under Node: `parseAss`, `eventAt`, `captionNear`, `eventAnchor`, `eventBox`, `textEventItems`, `placeEvents`, `withoutEvents`, `SNAP_ROWS`, `layerTargets`, `rowAt`, `appAreas`, `underApp`, `centrePercent`, `resized` | `SNAP_ROWS` if the Inspector offers the rows; `parseAss`/`eventAt` for anything about what is drawn |
| `lib/editorApi.ts` | `captionLines(id, rev)`, `captionsAss(url)`, `captionSourceTimes(id, rev, ranges)` | `captionSourceTimes` for a block's drop |
| `lib/editorTypes.ts` | `CaptionLane`, `CaptionLine`, `CaptionPiece`, `CaptionSpan` | the lane |
| `lib/newEditor.ts` | `lastPlatformApp()`: the app last chosen in the picker (saved by `savePlatform`) | nothing |
| the base style | a drag or resize sends `set_captions {style: {...the project's own style (doc.captions.style), else the lane's drawn style, x, y}}` | P1's controls must send the whole style the same way (keep `x`, `y` unless "Reset position") |

The viewer's picture is now `role="group"` (it holds the boxes, which are buttons); the still is hidden while a layer is dragged or typed into, or while a change from it is on its way (`active={showStill && !layerBusy}`).

### Choices made (part B)

1. **JASSUB 2.5.23**, the newest version at least two weeks old with no fix release within days after it (2.5.24 came 15 days later); exact pin, a development dependency (bundled by Vite into the interface; electron-builder ships no package of it). Its dependencies abslink (Apache-2.0), lfa-ponyfill (MIT) and throughput (MIT) are bundled; **rvfc-polyfill (GPL-3.0) is aliased to an empty file** in both Vite configs (`lib/rvfcNone.ts`), so no GPL-3.0 code is in the app. The package's licence expression also names LGPL-2.1+ (FriBidi) and FTL/GPL-2.0+ (FreeType) for its WebAssembly; all in NOTICE.
2. **The CSP gains `'wasm-unsafe-eval'` in `script-src` only.** The worker and the WebAssembly load from the app itself (`'self'`), so no `worker-src` and no `blob:`. Tested both ways in the built app loaded from `file://` (Electron 31.7.7): without it, "Refused to compile or instantiate WebAssembly module"; with it, the test caption draws.
3. **Canvas mode, by program time** (not bound to a `<video>`): the program plays through two alternating players.
4. **Development server:** JASSUB is served as it is (`optimizeDeps.exclude`), so its worker and WebAssembly sit beside it, with its one CommonJS import (`throughput`) pre-bundled; the worker is an ES module (`worker.format: 'es'`).
5. **Fonts:** the bundled ones by family from `GET /fonts`; `queryFonts: 'local'` (this PC's fonts when the app may read them; JASSUB never asks for the permission itself); never Google Fonts. Otherwise JASSUB's Liberation Sans.
6. **The live move is a rewrite of the engine's script** (`placeEvents`): `\an5\pos` at the box's middle, `\move` kept as a 40 px rise into it, `\fs` scaled for a resize. A caption moved this way is written exactly as the engine writes a dragged one (tested against `video/captions.py` for none, slide, pop and fade). Near an edge each caption is held inside the frame (and the safe zone) by its own size, as the engine does (`captionBounds` + `heldCentre`, the engine's `_place_bounds` and `_placed_at`; fix round 1, 9-D2), so the words don't jump on the drop; the size is the page's measured estimate, the rule is tested equal to the engine's.
7. **Boxes are estimates** from the script (anchor from `\pos`/`\move`/style; the words measured in the font at 0.87 of the size, wrapped at the margins in lines of equal width; outline and shadow added). Good enough to click and to warn; the still is exact.
8. **Snap rows** (box middle, % of the height): top 15, higher middle 33, middle 50, lower middle 63, bottom 75 (where a bottom caption sits today). Snapping distance 6 screen px.
9. **App areas** from `SAFE_ZONES` and `PLATFORM_UI`: the top bar (`top`), the button column (`right` wide, as tall as the shapes drawn in it), the text at the bottom (`bottom` tall, left of the column). A box counts as under one when it overlaps it by more than 4 px (at 1920 tall). The top bar is included as well as the buttons and the bottom text: a caption there is hidden too.
10. **Arrow keys only on a focused box**, so frame stepping keeps working everywhere else; 1% / 5% steps; sent 400 ms after the last key.
11. **Not on 9:16:** no app screen, no app snap lines, no amber outline.

### Known gaps (part B)

- **Windows fonts live** (fixed in fix round 1, 9-D9): before the fix, Arial and Arial Black were drawn live in JASSUB's Liberation Sans (regular weight; for Arial Black also about 8-10% bigger than the burn). Now the engine serves this PC's own files (`GET /fonts` `system`) and the viewer adds the ones a script names. In the sandbox (headless Edge) JASSUB no longer logs its "Using default font family … LiberationSans" fallback and the default caption draws bold. Not yet seen in the desktop app itself.
- **Letters are not pixel-identical to the burn** (9-D8, accepted): JASSUB's libass/FreeType build spaces and smooths glyphs slightly differently from the bundled FFmpeg's (under a pixel per glyph). Place and timing agree (centres within 1.5 px, the same frames); caption-area SSIM between the live layer and the burn is about 0.88-0.95 on 9:16 and 0.97-0.998 on 16:9 with a bundled font. The review bar for the live layer is therefore about 0.90 (bundled fonts), not 0.98; the paused still (the engine's own FFmpeg) stays the exact check.
- **First draw** (fixed, 9-D10): JASSUB loads a font on the first draw that needs it and doesn't draw again by itself, so the first frame of a new script could be empty until the playhead moved. Every new script is now drawn again at 250 ms and 1 s.

## The lane, the panels and the export (part C)

The caption lane, the transcript list, the caption and text panels, "+ Text" and the export dialog's two checkboxes (ED49). Everything goes through the routes and operations above; nothing in the engine changed in part C.

### What a person gets

- **The caption lane** (`CaptionLane.tsx`): one row under the main track, headed **Captions**, with **Hide** (◉/◌: `set_captions {enabled: !burned}`) and **Lock** (🔓/🔒). One block per piece of each line that plays (`lane.lines[].pieces`; a line cut out has none, a line cut through has one per part), in program time, on the timeline's own zoom and scroll; only blocks near the view are buttons, as step 8's items.
  - **Click** selects the line (and the caption layer: the viewer's box, the inspector's caption panel). Clicking an empty part of any track lets go of it.
  - **Drag an edge** retimes, **drag the block** moves it. While dragging, a dashed ghost; it snaps (with the timeline's Snap) to the cuts, the playhead, every item's edges and the other blocks; it is held between the blocks either side (never over another caption) and inside the stretch of video it plays in (never across a cut, as the engine holds it), its edges on the program's frames, at least a frame long, a moved block keeping its length. Esc cancels. At the drop: `source_times` for the block, then one `edit_caption_words` (a left edge sends only `in_us`, a right edge only `out_us`, a move both).
  - **From the keyboard** (UX fix round): with a caption line selected, **Alt+← →** moves it one frame (Shift: one second), held between its neighbours and inside its stretch as a drag is, before any item is moved (`CaptionLaneHandle.nudge`; with no line selected the items move as in step 8). One edge moves through the caption panel's **Starts at** / **Ends at** (below). Both go through the same `place` as a drop: `source_times`, then one `edit_caption_words`. Listed in the timeline's shortcut tooltip.
  - **Only a line in one piece** is retimed or moved; a line cut through by an edit has no edge handles and says why in its tooltip (its words can still be changed).
  - **Split** (S, or the toolbar's Split, with a caption line selected and the playhead on it): `source_times` for the playhead's frame, then `set_captions {lines}` with the line in two; the first part keeps the id, the words are shared by their length (`splitWords`), a one-word line is not split. With no caption line selected, S splits the timeline's items as before.
  - **Merge** (toolbar): the selected line and the next block's line, when that one also comes later in the source (a line put before it by a reorder is never merged into it): one line from the first's start to the second's end, both lines' words, the first's id.
  - **Delete** (Delete key or the toolbar's Delete, with a caption line selected): `edit_caption_words {text: ""}`.
  - **+ Caption** (toolbar): a line at the playhead's frame, 2 s or up to the next caption or the end of its stretch, words "New caption", typed over at once on the lane. Greyed where a caption already is or no captioned video plays (its tooltip says so). **A project whose captions name no video** (made by hand: `captions.media_id` is null, so it can have no captions at all) gets its first caption from the main track's video under the playhead: one `set_captions {media_id, enabled: true (only when the project had no choice and nothing burns), lines: [the line]}`, its source times worked out as the engine does (`firstLineAt`, tested equal to `compose.caption_source_range` at 0.25x to 4x and 23.976 to 60 fps).
  - **Double-click a block** (or Enter on the selected one) types its words on the lane: Enter keeps, Esc cancels, leaving keeps.
  - **Lock** stops every change on the lane and the captions' drag, resize, arrow keys and typing on the viewer (texts are not locked); it is the editor's while it is open, not saved in the project.
- **The transcript list** (`TranscriptPanel.tsx`), a **Captions** tab beside **Media** at the top of the left column (Premiere's Captions panel; the media bin's place, so the inspector keeps the style): every line that plays, in program order, with the time it first plays (`listLines`, from the same answer as the lane). Click a time: the playhead goes there and the line is selected. Type in a line's words: Enter or leaving keeps (`edit_caption_words`), Esc puts it back. **Search captions** keeps only the lines with those words (case and accents aside). The line playing now is marked, and kept in view while playing.
- **The caption panel** (inspector, when the caption layer or a caption line is selected and no item): with a caption line selected, **Timing** first: **Starts at** and **Ends at** in the video's time (step 8's time fields, m:ss.ff or seconds); a typed time moves only that edge, on the frames and within the line's room, through the lane's `retime` (one change); greyed, with the lane's tooltip, for a line an edit cuts in parts. Then step 3's `CaptionStyleControls` as they are (presets, font, colour, size, position by name, animation, safe zone, upper case, highlight, second speaker; **Words per caption** only while the lines aren't saved, since saved lines keep their grouping), then **Position**: **Across** and **Down** as numbers (%; without a dragged place, the place by name shown greyed: across 50, down 15/50/75) and **Reset position**. Every change sends the whole style (`set_captions {style}`), the project's own else the one drawn (`lane.style`), as the viewer's drag does; a burst (a slider, a colour being picked) is sent once, 350 ms after it rests; a preset keeps a dragged place; a **Position by name** takes it off (else that choice would do nothing).
- **The text panel** (inspector, a text item selected), replacing the "edited in a later version" line, under step 8's Timing: **Words** (Enter keeps, Shift+Enter a new line, Esc puts back), **Kind** (Text or Hook), **Font** (Default = Arial Black, or a caption font), **Size** (8 to 400, sent when let go), **Text colour** (sent 350 ms after the picker rests), **Position** Across/Down and **Reset position** (`position: null`: back to the top). Each is one `set_text`.
- **+ Text** (toolbar): `add_text {content: "New text", start_us, duration_us: 3 s, position: {x: 50, y: 50}}` (with an `add_track {kind: "text"}` in the same call when the project has no text track), selected, its words ready to type in the inspector (once: `focusText` is cleared when the Words box has taken focus and on any selection on the viewer or the lane, so selecting that text again later keeps the keys where they are, WCAG 3.2.1). It starts on the hundredth of a second at or before the playhead (the burn's times are hundredths), so it shows on the frame it was added at; it is held so it ends by the video's end.
- **The export dialog**: **Burn captions into the video** (on) and **Also save captions as a .srt file** (off), two compact checkboxes with their explanation in the tooltip, sent as `captions: "burn" | "none"` and `srt`. Done: "Captions saved as <file>.srt" under the video's line (the path in its tooltip), or the engine's sentence when there was nothing to save. Both start as default each time the dialog opens. With the captions hidden (the lane's Hide: `lane.burned` false), **Burn captions** opens unticked and greyed, its tooltip saying why (with the Hide button's own sentence), and `captions: "none"` is sent; the .srt choice stays.

### How it is built

| Piece | What it is |
|---|---|
| `lib/captionLane.ts` | Pure, no imports, tested under Node: `blocksOf`, `blocksIn`, `blockAt`, `savedLines`, `withSave` (lines made from the transcript saved first with their `tx<n>` ids, in the same call), `spanAt`, `room`, `dragBlock`, `retimeEdit`, `splitWords`, `splitLine`, `nextLine`, `mergeLines`, `newBlockAt`, `firstLineAt`, `addLine`, `listLines` |
| `components/editor/CaptionLane.tsx` | The lane's row (rendered by `TimelinePanel` once, right after the main track's row, with the timeline's zoom, width, window and Snap). A `forwardRef` with `CaptionLaneHandle`: `split()`/`remove()` (answer whether a caption line took the key), `merge()`, `add()`. Blocks are memoised `BlockButton`s, their names worked out once per answer |
| `components/editor/TranscriptPanel.tsx` | The list |
| `components/editor/Inspector.tsx` | `CaptionSettings`, `TextSettings`, `PositionFields`; new props `captionLayer`, `lane`, `focusText` |
| `components/editor/TimelinePanel.tsx` | The lane's row; "+ Text", "+ Caption" (`AddCaptionButton`, the only part that follows the playhead), "Merge"; S and Delete ask the lane first; new props for the lane |
| `components/editor/EditorScreen.tsx` | `captionLine` (the selected line; selecting one selects the caption layer and no items), `captionsLocked`, `focusText`, the left column's tabs; `layer.lane` to the timeline, the list and the inspector |
| `components/editor/CaptionTextLayer.tsx`, `Viewer.tsx` | `captionsLocked` |
| `components/editor/ExportDialog.tsx`, `lib/editorApi.ts`, `lib/editorTypes.ts` | The two choices; `exportProject(..., {captions, srt})`; the job result's `srt`, `srt_file`, `srt_error` |

### Choices made (part C)

1. **The lane holds a block between its neighbours and inside its stretch**, as CapCut's caption track does; overlapping captions are never made from the lane. Rejected: letting blocks overlap (two captions drawn at once, one over the other).
2. **Retime and move only for a line in one piece.** Dragging one part of a line cut through by an edit has no single meaning in source time (the other part would move too, or not). Its words can still be changed; its times follow the main track.
3. **Split shares the words by their length**, since the transcript's word times are not in the lane's answer; the words can be fixed right after (double-click). Rejected: a new route for word times (an engine change for a guess the person checks anyway).
4. **Merge only with a line that also comes later in the source**, so a reorder never makes one line cover the stretch between.
5. **The transcript list takes the media bin's place as a tab** ("Media" | "Captions"), as Premiere's Captions panel sits beside the project panel; the inspector stays the style's place.
6. **Words per caption is hidden once lines are saved**: saved lines keep their grouping, so it would do nothing (the step-3 rule for translated subtitles). Rejected: regrouping saved lines (it would undo the person's fixes).
7. **A Position by name takes a dragged place off**; a preset keeps it (part B's rule: the drag is the person's).
8. **Lock is the editor's**, not the project's: step 5's document has no lock, and a lock that came back on in another window or after the AI's edits would be a surprise.
9. **The first caption of a project made by hand names its video** in the same change, since such a project's captions name none and could otherwise never have captions.
10. **"+ Text" starts on the hundredth of a second** at or before the playhead: the engine writes ASS times in hundredths, rounded, so a text added exactly on a frame boundary that isn't one would first show a frame later.
11. **Every lane action is one change** (one undo step); a transcript's lines are saved in that same change.

### Measured in the sandbox (part C)

Headless Edge through playwright-core against the sandbox engine (development build); scripts in `scratch/step9/c/` (not committed), screenshots `scratch/step9/screens/c*.png`, looked at.

- **speech_vertical_20s's clip (23 lines from the transcript):** a right edge dragged 40 px: the line's end 1.940 → 2.233 s, saved with every transcript line's id in the same change; a block moved 0.2 s into a gap: both ends moved by 0.193 s (on the frame grid), one Ctrl+Z put it back exactly; a left edge dragged; a split at the playhead (23 → 24 lines, "words" | "should appear", the cut at the playhead's frame); Merge put it back; Delete (23 → 22); **+ Caption** in the gap, words typed ("brand new words", 8.133–8.740 s); words typed on the lane and in the list; search "fixed" showed 1 line; the preset "Bold pop" and Down 63 in the inspector (style font Poppins ExtraBold, x 50, y 63); **+ Text** at 2.27 s, its words typed (focus was in Words), Impact, size 140, Across 50 / Down 30; Hide and Show (`enabled` false, then true); Lock (a drag changed nothing, Merge greyed). Each action was one revision, labelled as expected.
- **Exports** (Original): burn on + .srt, and burn off + .srt, from the same revision. The burned caption's ink sits at 62.7 %–62.9 % down and 50 % across (asked: 63 / 50) at 3.45, 10 and 17 s; the unburned export has no caption pixels there, and both show "HELLO THERE" at 30 %. The two `.srt` files are byte-identical; each cue starts within 3 ms of a burned event (centiseconds) and within 0.3 ms of the lane's pieces.
- **A project made by hand** (the 2-hour video, 16:9): **+ Caption** at 5 s named `m1`, switched captions on and made the line 5.0–7.0 s; drawn live.
- **300 lines** (the 2-hour video, one line every 3 s over 15 minutes), development build: open until all 300 blocks were drawn 727 ms (first open), 247 and 289 ms after; scrolling zoomed in (121 blocks as buttons), 40 px a frame for 4 s: every frame 7.0 ms (the display's 144 Hz); zooming with all 300 in view, a Ctrl+wheel step every frame for 4 s: median 13.9 ms, 95th percentile 20.9 ms, 42 of 275 frames over 17.5 ms, none over 34 ms. The same project with no lines zooms at 7.0 ms every frame, so the lane's 300 buttons cost about one frame in two at 144 Hz while zooming; step 8's 500 items measured 82 of 324 frames over 17.5 ms the same way.

### Known gaps (part C)

- **The caption panel's Example** (step 3's mock) shows the caption at its place by name, not at a dragged place; the viewer and the numbers show the real place.
- **Zooming with hundreds of blocks in view** drops about one frame in seven at 60 Hz (above); scrolling doesn't.
- **The lane's words split by length**, not by when they're said.
- **Captions added on the lane start on the frame the engine maps them to**; like every caption, the burn writes hundredths of a second, so one can start a few ms after its frame (part A's note).
- Not tried in the desktop app (the sandbox is a browser), nor with a production build.

## Fix round 1 (QC, review and render findings)

- **9-D1** (fixed): a replaced or deleted export's `.srt` stayed behind and Storage didn't list it. It now goes with its video (Branch B replace, Branch A re-render, clip delete) and Storage lists a `.srt` whose video is gone (`core/housekeeping.py` `orphan_export_captions`).
- **9-D2** (fixed): while dragging near an edge the live words sat at the pointer and jumped in on the drop. The live picture now holds each caption by its own size as the engine does (`captionBounds`, `heldCentre`). Sandbox: pointer at 97% across, box 60.92% mid-drag and 60.83% after the drop.
- **9-D3** (no change): JASSUB's package licence expression includes LGPL-2.1+ (FriBidi) and FTL/GPL-2.0+ (FreeType) for its WebAssembly, beside MIT for its own code; all recorded in NOTICE, the app is AGPL-3.0, and the GPL-3.0 rvfc-polyfill is not in the build. For the owner to note, not a code change.
- **9-D4** (no change): a Branch A export with "Burn captions into the video" off leaves the clip's captions off in today's editor (it is in the contract). The checkbox's tooltip already says so for a project made from a clip ("Your clip in the library then has its captions off too.").
- **9-D5** (fixed): Merge across a cut dropped words. Merge is offered only when no cut lies between the two blocks (greyed with a tooltip otherwise), and the engine spreads a saved line's words only over the parts that play (`compose._line_word_times`), so an older merged line keeps every word too.
- **9-D6** (fixed): retime, move, split or a word fix of a line an edit cut off sent one end and left the other in the cut. The lane, the viewer's typing and the transcript list now save such a line as the part that plays (`trimmedLine`: both ends held to its stretch, the words that play), and the engine never times words into a cut. The review's random replay (80 seeds) passes.
- **9-D7** (fixed): a drag and a resize within a second undid as one step. A `set_captions` style's coalesce key is now the style keys whose value changed.
- **9-D8** (accepted): live letters differ from the burn's by under a pixel (two libass builds); bar for the live layer about 0.90 SSIM on bundled fonts (Known gaps, part B).
- **9-D9** (fixed): Arial and Arial Black were drawn live in Liberation Sans. The engine serves this PC's Windows caption fonts (`GET /fonts` `system`, `/fonts/system/<file>`) and the viewer adds them.
- **9-D10** (fixed): the first live draw of a new script could be empty while its font loaded; it is drawn again at 250 ms and 1 s.

## Fix round 2 (QC and review findings)

- **9-D11** (fixed): a caption dragged in the new editor went out through Branch A with `x`/`y` in the clip's `render_opts.caption_style`; today's editor then showed it by its top/middle/bottom name and its choice did nothing in the render. A style with `x` or `y` now makes the project Branch B (`projection._read`: "The captions are placed by hand."), so today's editor asks first, as for other new-editor-only changes.
- **9-D12** (fixed): a Branch A export after a cut through a caption line burned the line whole, cut word included, while the `.srt`, the lane and the live viewer showed two pieces without it. The export now takes Branch B when the captions show (burned or `.srt`) and `compose.caption_line_cut` finds a line reaching over a cut or past what plays; a cut between lines, or captions neither burned nor saved, keep Branch A.

## Fix round 3 (review finding)

- **9-D13** (fixed): fixing a caption's words and then pressing Merge (or Split, + Caption, or Delete while the lines still came from the transcript) silently put the old words back: the second change sent the whole caption list built from the lane before the fix, at the fix's newer revision. `useProject.apply` now takes the revision a change was built from, and `captionLane.laneRevisionFor` pins every change that sends a whole caption list (`set_captions` with `lines`) to the lane's revision (the lane, the Captions list and typing on the viewer). If the project moved on first, the change is not made and the editor says "The captions changed just before this, so it was not made: please do it again."; a change to one line is not pinned.

## Fix round 4 (review findings)

- **9-D14** (fixed): fixing the words of a line an edit cut through (two pieces, a word cut out between them) brought the cut word back into the burn, the live view and the `.srt` whenever the word count changed: the edit boxes offered the whole line, and the engine shared every typed word over the parts that play. Now the viewer's box, the lane's box and the Captions list offer only the words that play (`captionLane.wordsToEdit`: the pieces' words in the source's order, a stretch played twice counted once), and `compose._line_word_times` keeps a typed word that lines up with one of the line's own cut-out transcript words at that word's time, so it stays cut out whatever is typed; the other words are shared out over the parts that play as before.
- **9-D15** (fixed): `GET /fonts/system/<file>` served this PC's Windows font files to any web page (`Access-Control-Allow-Origin: *`) and `GET /fonts` named which ones the PC has. Both are now for the app on this PC only (the project media routes' Origin check); the request's own Origin is named back instead of `*`. Checked: JASSUB in dev:web loads Arial (200, origin `http://localhost:5173`), a worker on a `file://` page (Origin `null`, as the packaged app) loads it, a page on another port cannot. The bundled `/fonts/<file>` stay public.
- **9-D16** (fixed): a clip saved with the removed Highlights post style's caption keys (`post_style`, `card_position`) gave a project whose captions could not be dragged or restyled (400). The importer and `set_captions` now drop them, as today's render does (`importer.OLD_STYLE_KEYS`, the same names as `core.pipeline._OLD_STYLE_KEYS`).

## After fix round 4 (fixed by the orchestrator)

- **9-D17:** lit-up words (highlight or karaoke) on a clip that is cut or sped up export through the compiler (Branch B), reason "Words light up on a clip that is cut or sped up.", because today's re-render times those words by length and the burn fell up to 0.42 s from the live view. `compose.word_timing_retimed`; captions not burned keep today's re-render.

## Left for later

- Per-line positions, rotation, animated text presets; translating onto a new-editor export (8-D13) is unchanged.
- ASS times are centiseconds (as always): a caption can start up to 5 ms off a frame boundary in the burn.
- **After the UX round (fixed by the orchestrator):** the captions list keeps the keyboard on Enter and Esc (each field is keyed by its line and is read-only, not disabled, while a fix saves), and the toolbar's Delete removes a selected caption line.
- **For Colin's click-through (found by the last checks, not fixed; none loses work in normal use):**
  - The live captions look a little softer than the burned ones with the bundled fonts (Anton scores 0.88-0.96 on the caption area against the 0.98 aimed for); place, size, timing, text and colour match. Look at a few frames side by side.
  - A word typed exactly where an edit cut a word out (for example "we got it" where "did" was cut from "we did it") doesn't show: the engine keeps cut words out, and takes the new word for the cut one.
  - With a caption dragged by hand, the step-3 Position menu still says Bottom, and the panel has two Position headings.
  - "+ Text" when a text already covers the playhead is refused with a message naming internal ids.
  - The caption selection box can be narrower than a large caption or a two-line one; snapping and the edge stop use the box.
  - With the platform picker on "No overlay", a caption left under where an app's buttons would be shows no amber outline after the drop.
  - Exporting a clip's project with "Burn captions" off saves captions off on the clip itself, so today's editor then shows it without captions.
  - Today's Apply and deleting a clip now also delete a "<clip>.srt" beside the clip's video.
  - The Windows font route accepts Origin "null" (a sandboxed frame on a web page could list and read those fonts).
  - Karaoke's first frame and a fade's first frame differ slightly between live and burn (one frame).
  - New strings are English only for now (as step 8's).
  - The JASSUB package's licence also covers FriBidi (LGPL-2.1+) and FreeType (FTL or GPL-2.0+) inside its WebAssembly; both are recorded in NOTICE and fit the app's AGPL-3.0. rvfc-polyfill (GPL-3.0) is not bundled.
  - Keyboard: the shortcut lists are mouse-only tooltips; edits on the lane and in the list are not announced; words fields and time buttons all have the same names.

## Tests

| File | What it checks | Runs on CI light |
|---|---|---|
| `tests/test_step9_captions.py` | x/y: no point without both numbers; the centre at x, y on 9:16, 16:9 and 1:1; kept inside the frame and the safe zone by its size; slide rises into the point; highlight, karaoke and the second speaker unchanged apart from the point; every animation; the checks on style and text place; text `{x, y}` drawn centred and a named text as before; the lane equals the burn after cuts, speed, reorders and a stretch played twice; hidden and split lines; saving with lane ids; program to source never a frame off (0.25x to 4x, 23.976 to 60 fps); a block held inside its stretch; captions not burned keep text; the `.srt` within a frame of the burn | yes |
| `tests/test_step9_routes.py` | the three routes' protection (header, token, Origin), an old revision refused, the viewer's ASS equal to the export's `export.ass`, a dragged caption and its reset in it, an empty script, bad styles refused, the lane after a cut, a retimed block moving the burn by the same amount, bad bodies refused, exports with captions burned (as before), `none` + `srt` on Branch B and Branch A, an `.srt` with no captions, bad export fields, a cut through a caption line exported by the compiler and a cut between lines kept on Branch A (9-D12), this page lists every route and field | no (FastAPI; the Plugin platform job) |
| `tests/test_project_routes.py` | changed: the three new routes are internal | no (Plugin platform job) |
| `tests/test_fonts_routes.py` | changed: the Windows caption fonts this PC has are listed and served, only by their known names (9-D9); the CSP's one addition | no (FastAPI) |
| `tests/test_project_projection.py` | changed: a caption style with `x` or `y` projects to Branch B, a named place still to Branch A (9-D11) | yes |
| `tests/test_project_ops.py` | changed: a caption-style drag and resize a moment apart are two undo steps, two moves of one drag one (9-D7) | yes |
| `tests/test_ui_caption_lane.py` | part C: `lib/captionLane.ts` under Node (blocks are the pieces that play; an edit of transcript lines saves them first with their ids; a dragged block held between its neighbours and inside its stretch, snapped, at least a frame; a retime sends only the edge dragged, or for a line an edit cut off both ends held to its stretch with the words that play (9-D6); split, merge, add and the free place for a new caption; no merge across a reorder or a cut (9-D5); the list in program order, filtered; the first caption of a project made by hand equal to `compose.caption_source_range` at 0.25x to 4x and 23.976 to 60 fps); the sources (every lane action one labelled change through `apply`, a drop through `source_times`, S and Delete on a selected line first, the lane, list and inspector on the same answer, the caption panel is step 3's controls sending the whole style, the text panel, "+ Text", the lock reaching the viewer) | yes (the Node parts skip without Node 22.6+) |
| `tests/test_ui_new_editor_part_c.py` | changed: the caption lane is one row under the main track (was: room left for it); the export dialog offers the two caption choices, burn on and `.srt` off (was: none); "+ Text"'s operations go through `apply` | yes |
| `tests/test_ui_caption_layer.py` | part B: `lib/captionLayer.ts` under Node (a caption moved live is written as the engine writes a dragged one, for four animations; a resize; only the chosen events move; text events matched to items in `build_ass`'s order; the five rows; the apps' areas, snap lines and "under the buttons"; ranges; a live drag held inside the frame and safe zone exactly as `_placed_at` holds it, 9-D2); the viewer's source (JASSUB by program time with local fonts only, this PC's Windows caption fonts added and a new script drawn again, 9-D9/9-D10, the CSP's one addition, the exact pin, the polyfill left out, NOTICE, the app's screen while dragging, the amber outline, one change per drag, the arrow keys) | yes (the Node parts skip without Node 22.6+) |
