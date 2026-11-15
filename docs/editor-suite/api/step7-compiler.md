# Step 7: the export compiler (engine only)

Status: **built, in progress until its checks.** Part A (the moves the compiler builds on, and Branch A), part B (Branch B's video path, the plan, the runner and the export route) and part C (captions, text and branding in one ASS file with the caption remap, the audio mix, the tail pad, the draft and the still, and their routes) are done.

Contents: [Part A: what moved](#part-a-what-moved) · [Helpers](#helpers) · [Branch A](#branch-a) · [Part B: the plan](#part-b-the-plan) · [The stages](#the-stages) · [V1: AI and manual items](#v1-ai-and-manual-items) · [The final encode](#the-final-encode) · [Command lines](#command-lines) · [Running a plan](#running-a-plan) · [The export route](#the-export-route) · [project_exports](#project_exports) · [Measured](#measured) · [Part C: captions, text and branding](#part-c-captions-text-and-branding) · [The caption remap rule](#the-caption-remap-rule) · [Part C: the audio graph](#part-c-the-audio-graph) · [The bleep](#the-bleep) · [The tail pad](#the-tail-pad-plan-problem-4) · [Part C: the draft](#part-c-the-draft) · [Part C: the still](#part-c-the-still) · [Part C: the routes](#part-c-the-routes) · [Choices made](#choices-made) · [Left for steps 8 to 11](#left-for-steps-8-to-11) · [Tests](#tests)

## Part A: what moved

Nothing a person or a plain auto-clipping run can see changed. The proof:

- the step-0 argv snapshots (`tests/test_render_argv_snapshots.py`) pass unchanged, without `--update-snapshots`;
- the golden renders (`scripts/golden_renders.py --check`) are byte-identical;
- the import guard (`tests/test_editor_import_guard.py`) now also covers `core/framing.py` and `video_editor.compose`.

| Moved | From | To |
|---|---|---|
| The framing block: the intermediate cut, the editor's edits, the gaming layout, podcast framing, face / sport tracking and `cropper.render_vertical` with every crop mode, and the one-encode path (landscape, Vertical Live, "vertical" off) | `core/pipeline.py` `_render_files`, the body of its `try` | `core/framing.py` `frame_intermediate()`, line for line |
| `_try_gaming_render`, `_sport_framing` | `core/pipeline.py` | `core/framing.py`, unchanged; `core/pipeline.py` imports them back under the same names |
| The image watermark's overlay graph | `video_editor/watermark.py` `apply_image` | `video_editor/watermark.py` `logo_graph()`, which `apply_image` calls |

Inside the moved block only three names changed: `cut_clip(` is `cut(`, `_try_gaming_render(` is `gaming_render(` and `_sport_framing(` is `framing_of(`. `core/pipeline.py` passes its own `cut_clip`, `_try_gaming_render` and `_sport_framing` for them, so the tests that stand in for those names on `core.pipeline` still reach the render.

## Helpers

```python
# core/framing.py
frame_intermediate(
    source: Path, padded: ClipCandidate, render_path: Path, clip_dir: Path, stem: str, *,
    config: dict, opts: dict, edit: EditList | None = None, ass_path: Path | None = None,
    vf_extra: str = "", normalize: bool = True, landscape: bool = False, vertical_live: bool = False,
    podcast: bool = False, gaming: bool = False, sport_name: str | None = None,
    sport_screen: str | None = None, scratch: list | None = None,
    cut=None, gaming_render=None, framing_of=None,
) -> tuple[dict | None, dict | None]     # (gaming_kept, framing_quality)
```

- Cuts `padded` (its end already padded) out of `source` and writes `render_path`. Staging files are named `<stem>.source.mp4`, `<stem>.edited.mp4`, `<stem>.plain.mp4` in `clip_dir`, and each is appended to `scratch`; the caller deletes them in its own `finally`.
- `config` needs `clips` (and `tracking` on the tracked path); `opts` is the clip's render options (`crop`, `gaming`, `sport_moment`).
- It runs FFmpeg, and on the tracked path YOLO, so the compiler calls it from its runner, never while compiling a plan.

```python
# video/cutter.py
cut_clip(source, candidate, output_path, ass_path=None, vf_extra="", normalize=False, final=False,
         encoder: list[str] | None = None) -> Path

# video_editor/export.py
apply_edits(input_path, edit, output_path, ass_path=None, normalize=False, final=False,
            encoder: list[str] | None = None) -> Path
```

- `encoder` is the `-c:v ...` block, in place of `video_encoder_args()`. `None` is today's command exactly.
- `cut_clip` retries a refused graphics-card encode once on the CPU only when `encoder` is `None`. A block the caller chose is used as given and never retried.
- Audio stays `-c:a aac -b:a 128k` in both.

```python
# video_editor/watermark.py
logo_graph(cfg: dict, canvas: tuple[int, int], base: str = "0:v", logo: str = "1:v",
           tag: str = "wm", out: str | None = None) -> str
```

- With the defaults it is the graph `apply_image` has always run: `[1:v]scale=…[wm];[0:v][wm]overlay=x:y`, its output unlabelled.
- `base`, `logo` and `tag` name the pads; `out`, when given, labels the result (`…overlay=x:y[out]`).
- It never names the logo file. A caller finds the file the way `apply_image` does: by safe name, in the branding assets folder.

```python
# video/captions.py
refit_caption_lines(lines, segments, old_start, new_start, new_end, words_per_caption=3,
                    head_until: float | None = None) -> list[dict]
```

- `head_until` is new, for Branch A. `None` is the rule as before.

## Branch A

`server/jobs.py` `_rerender_clip` reads a new payload key, `render_opts_replace` (bool). The export of step 7's part B sends it with projection's answer:

```json
{"clip_id": 42, "start": 95.0, "end": 130.0, "render_opts": {"edit": {...}, "caption_lines": [...]},
 "render_opts_replace": true}
```

With `render_opts_replace` true:

| What | A job without the key (today) | With `render_opts_replace` |
|---|---|---|
| The clip's saved options | merged with the sent ones (`caption_style` merged key by key) | **replaced** by the sent ones |
| `edit` when the window moved | shifted (`shift_edit`) | **as sent** |
| `caption_lines` | shifted from the row's start, then the gained seconds captioned (`refit_caption_lines`) | **not shifted**; dropped where outside the clip, and only the gained seconds captioned (see below) |
| `speaker_edits` | shifted (`shift_edits`) | **as sent** |
| `start`, `end` | as sent | rounded to the hundredth of a second (see below) |
| Everything else (`sport_moment`, `stop_by`, the reel refusal, `_drop_post_style`, what the render decided, the row's re-registration, deleting the old file only after the new one is registered) | as today | as today |

The public routes never send the key: each of them builds its own payload (`server/api.py`). The export route (below) sends it, through a job on the editor lane that calls `Worker._rerender_clip` with two additions that change nothing for the job queue: `before_register(final_path)`, called once the file is made and before any row is touched (a cancel asked for during the render stops the job there), and the clip's new id as the return value.

## Part B: the plan

```python
# video_editor/compose.py
plan = compile(document, revision, preset, purpose="export", *, inputs=Inputs(...))   # pure
result = run_plan(plan, task, config=config)                                          # on the lane
```

`compile()` runs no FFmpeg, no ffprobe and no tracking. Everything it needs from the PC is in `Inputs`, which the caller fills:

| `Inputs` field | What it is |
|---|---|
| `work` | The plan's own folder. The export job uses `data/editor_lane/job-<id>/`, which the lane removes at its next start if the app stopped mid-export |
| `media` | `{media id: MediaFile(path, kind, width, height, has_audio, cache)}`: each media the items use, resolved through step 6's `resolver.resolve_entry` to its **original** file (never a proxy, ED7). `cache` is its `data/media_cache/<fingerprint>/` folder, given only for media an AI item tracks |
| `ffmpeg`, `version` | The FFmpeg the commands start and its `(major, minor)` (`ffmpeg_version()`), which picks the filter-script flag |
| `encoder` | A preset's `video.encoding.Encoder`, from `export_presets.pick_encoder` (the job probes; compile never does). None: the CPU's |
| `render_args` | For Original: the renders' `-c:v` block (`encoding.video_encoder_args()`). None: `CPU_ARGS` |
| `card_seconds` | The end card's length when the app's end card is on (`outro.DURATION`), None without one |

A **RenderPlan** is plain data (`plan.to_dict()` is JSON):

```json
{"project_id": "0123456789abcdef", "revision": 7, "purpose": "export",
 "preset": {"kind": "original", "...": "export_presets.normalize()'d"},
 "canvas": {"width": 1080, "height": 1920, "fps": {"num": 30, "den": 1}},
 "output": {"width": 1080, "height": 1920, "fps": 30.0, "codec": "h264", "encoder": "libx264",
            "audio_kbps": 128, "max_bytes": null, "passes": 1},
 "program_us": 4000000, "card_us": 2900000,
 "work": "<WORK>", "result": "<WORK>/export.mp4",
 "video_post": [], "notes": [],
 "stages": [
   {"id": "cut-001", "kind": "ffmpeg", "commands": [["ffmpeg", "..."]],
    "reads": ["<M:m1>"], "writes": ["<WORK>/v-001.mp4", "<WORK>/a-001.wav"],
    "temps": {"<WORK>/v-001.mp4": "<WORK>/v-001.part.mp4", "<WORK>/a-001.wav": "<WORK>/a-001.part.wav"},
    "scripts": {"<WORK>/cut-001.graph.txt": "[0:v]setpts=..."},
    "seconds": 4.0, "weight": 4.0, "params": {}},
   {"id": "card", "kind": "card", "writes": ["<WORK>/card.mp4"], "params": {"w": 1080, "h": 1920, "...": "..."}},
   {"id": "final", "kind": "ffmpeg", "commands": [["ffmpeg", "..."]], "cwd": "<WORK>", "...": "..."}
 ]}
```

(`tests/snapshots/compose_plans/end_card.json`, with `<WORK>` and `<M:id>` for the paths.)

| Stage field | What it is |
|---|---|
| `id` | `cut-NNN`, `frame-NNN`, `conform-NNN`, `join`, `layer-NN`, `card`, `final`, in the order they run |
| `kind` | `ffmpeg` (run `commands` in order: two for a 2-pass encode), `join` (its one command, the stream-copy join of the listing in `scripts`, run on the task like `ffmpeg`), `frame` (`core.framing.frame_intermediate` with `params`), `card` (`outro.ensure_outro` with `params`, copied into `writes`) |
| `reads`, `writes` | The files it reads and the files it leaves |
| `temps` | Each written file's temporary name (`v-001.part.mp4`): the commands write those, and the runner renames them when the stage is done |
| `scripts` | Text files written before the stage runs: filter graphs, concat listings (part C adds the ASS file here) |
| `seconds`, `weight` | The video it makes (FFmpeg's `-progress` is a fraction of it) and its share of the progress bar |
| `fallback` | The commands again on the CPU, when the first ones use a graphics card |
| `retry` | The size target's one retry: `{"commands", "video_kbps", "max_bytes"}` |
| `cwd` | Where the commands run (the final stage: its 2-pass log is relative, as step 1's is) |

`notes` says in plain words what a plan leaves out: AI framing fitted on a canvas other than 9:16, the sound of videos on V2 and up, a last word cut without an end card, an approximate still, an enlarged export.

Part C adds stage kinds of no new sort: `sounds-NN` (ffmpeg: the sounds on A2 and up, one WAV per bus), and for a still a single `still` stage. A final or still stage whose ASS file uses a bundled font has `params.fonts` (the families the runner stages into `<WORK>/fonts`). `purpose` is `export`, `draft` or `still`.

## The stages

**`cut-NNN`** (one FFmpeg per chunk of V1; see [V1](#v1-ai-and-manual-items)):

```
ffmpeg -hide_banner -y -v error -nostdin -progress pipe:1 -nostats
  -ss 9.75 -t 8.45 -i <M:m1>
  -/filter_complex <WORK>/cut-001.graph.txt
  -map [vout] -c:v libx264 -preset veryfast -crf 12 -bf 0 -g 60 -pix_fmt yuv420p -video_track_timescale 30000 -an <WORK>/v-001.part.mp4
  -map [aout] -c:a pcm_s16le -ar 48000 -ac 2 -vn <WORK>/a-001.part.wav
```
```
[0:v]setpts=(PTS-0.25/TB)/1,fps=30/1:start_time=0[s0];
[s0]split=2[i0bg][i0fg];
[i0bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=136:240,gblur=sigma=12,scale=1080:1920:flags=bilinear,setsar=1[i0wash];
[i0fg]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1[i0pic];
[i0wash][i0pic]overlay=(W-w)/2:(H-h)/2[f0];
[f0]format=yuv420p,tpad=stop=-1:stop_mode=clone,trim=end_frame=240,setpts=PTS-STARTPTS[v0];
[0:a]asetpts=PTS-STARTPTS,aresample=48000,atrim=start_sample=12000,asetpts=N/SR/TB,aformat=sample_fmts=s16:channel_layouts=stereo,apad,atrim=end_sample=384000,asetpts=N/SR/TB[a0];
[v0]concat=n=1:v=1:a=0[vout];
[a0]concat=n=1:v=0:a=1[aout]
```

- Every item is its own input, with its own `-ss` before `-i` (accurate input seeking) and `-t` its source stretch plus 0.2 s, so `trim`, not the file's end, ends it.
- **Picture timing (fix 7-D9):** each video item is read from `PRE_ROLL_US` (0.25 s, less where the source has less) before its in point (`-ss <in - pre>`), and its picture keeps each source frame's own offset: `setpts=(PTS-<pre>/TB)/speed,fps=<canvas>:start_time=0` (for an overlay, `+<start>/TB` and `start_time=<start>`). `fps` then gives each canvas frame the source frame on screen at that moment, including the one that began before an in point that falls between two source frames. `PTS-STARTPTS` (the first version) took the first frame after the in point as time 0, so the picture ran up to one source frame / speed early against the sound (4 frames at 0.25x). The item's own sound read from the same input drops the pre-roll (`atrim=start_sample=<pre>`), so it still starts exactly at the in point. A source with gaps longer than 0.25 s between frames (under 4 fps) can still show a later frame at an item's start. A picture item is unchanged.
- Speed: `setpts=(PTS-<pre>/TB)/speed` for the picture, chained `atempo` (each 0.5 to 2: `atempo=2,atempo=2` for 4x, `atempo=0.5,atempo=0.5` for 0.25x) for the sound.
- **Exact lengths.** Each item comes out at exactly the frames the timeline gives it (`trim=end_frame=`, after `tpad` in case the source ends early) and exactly its samples at 48 kHz (`atrim=end_sample=`, after `apad`). A frame count is `frames_at(end) - frames_at(start)` and a sample count `samples_at(end) - samples_at(start)` on the program's time, so the chunks add up to the program with no rounding drift, at 29.97 too (`tests/test_compose_plan.py`).
- The picture is cut to the **mezzanine** (choice 5) and the sound to a 16-bit PCM WAV: the A1 track of those items.

**`frame-NNN`** (an AI run only): `core.framing.frame_intermediate` on the run's cut (`src-NNN.mp4`, at the source's size, its speed already applied), exactly as a plain run frames a clip: the crop mode, the Gaming layout, podcast and sport framing, the colour as `vf_extra`. Its `cut` is a copy of that file, and its `track` is `cached_tracker` (below). Writes `framed-NNN.mp4` (1080x1920).

**`conform-NNN`** (an AI run only): `framed-NNN.mp4` to the canvas at the mezzanine, exactly the run's frames:
```
ffmpeg ... -i <WORK>/framed-001.mp4 -map 0:v:0 -vf setpts=PTS-STARTPTS,fps=30/1,scale=1080:1920:flags=lanczos,setsar=1,format=yuv420p,tpad=stop=-1:stop_mode=clone,trim=end_frame=300,setpts=PTS-STARTPTS <mezzanine> -an <WORK>/v-001.part.mp4
```

**`join`** (two chunks or more): the chunks' pictures joined by stream copy with `video/encoding.py` `concat()`'s own flags; the runner runs this command itself on the lane (fix 7-D7), so the plan shows what runs (`-f concat -safe 0 -i <WORK>/join.txt -copyts -c copy -movflags +faststart+negative_cts_offsets`). Every chunk is encoded with the same parameters (codec, size, frame rate, timescale, no B-frames), so the copy is valid.

**`layer-NN`** (only when one command can't hold every overlay): overlays drawn onto the joined picture at the mezzanine, in the order they stack, so the final command draws the rest.

**`card`** (when the app's end card is on): `outro.ensure_outro` for the export's size and frame rate (the shipped card for 1080x1920 and 1920x1080 at 30 and 60 fps, else drawn once and kept, as today), copied to `<WORK>/card.mp4`.

**`final`**: [below](#the-final-encode).

## V1: AI and manual items

**Which is which** (`compose.is_ai_item`): an item framed `{crop}` or `{gaming}` (as an imported clip keeps them, verbatim), or with no framing at all on the clip's own video in a project made from a clip (a clip saved without `crop` is tracked: its default). Every other item is framed by the document. A picture on V1 is always a manual item.

**Chunks.** V1 is cut in chunks, in order:
- an **AI run**: consecutive AI items of one video, with the same framing, colour and speed, in the video's order (each starts at or after the last one's end), at most 64. One run is one tracking pass, as one clip is today.
- a **manual chunk**: consecutive manual items, at most 16 (each is a decoder in one command), and fewer when the command would pass the [length budget](#command-lines).

**An AI item on a canvas other than 9:16** (or in a Vertical Live project) is framed as a manual `fit` with a blurred pad: the cropper and the Gaming layouts make 9:16 pictures until step 11. The plan's `notes` says so.

**Manual framing** (`framing_graph`), on the canvas's size:

| `framing` | The picture |
|---|---|
| `fit` (the default) | The whole picture, as large as fits. The rest of the canvas is the picture again, blurred and washed out the way the cropper's fit_blur and an export's blur pad are (`pad: "blur"`, the default), or a colour (`pad: "#RRGGBB"`) |
| `fill` | Covers the canvas; the rest is cut off |
| `align` | `center` (default), `top`, `bottom`, `left`, `right`: where a fitted picture sits, or which part a fill keeps |

`pad` is new on `framing` (`ops._framing`: `"blur"` or `#RRGGBB`, stored in capitals); a framing with it is Branch B.

**Transform** (`{x, y, scale, rotation, opacity}`, defaults 0, 0, 1, 0, 1): the framed picture is scaled about its centre, turned by `rotation` degrees (the corners clear), made `opacity` see-through, and drawn on black with its centre moved by `x` canvas widths and `y` canvas heights. An item with the defaults has no transform filters at all.

**Colour**: `color.filter` (a `filters.is_valid` name) and `color.adjust`, through `video/filters.py` `combined_chain`, exactly as today's render builds `vf_extra`: in a manual item's own chain after its framing, in an AI run as `vf_extra` to `frame_intermediate` (where today's render puts it), in an overlay's chain.

**Tracking, cached.** `cached_tracker` stands in for `video.tracker.compute_tracking` (through the new `track=` keyword of `frame_intermediate`; the pipeline never passes it). The result is kept in `data/media_cache/<fingerprint>/tracking/<key>.json`, `<key>` a hash of what was tracked (the media, its stretches, speed, frame rate and crop mode) and how (the detector, sample rate, shot-aware settings), with whether letterbox was forced. `tracking` is a new entry of step 6's `cache.PRODUCTS`, with an `index.json` as its "used" mark, so the cap and Clear count it. A Gaming layout's detection and a sport's framing are not cached: they run each export.

## The final encode

One FFmpeg, its graph in a script file:

```
ffmpeg ... -i <WORK>/v-001.mp4 -f concat -safe 0 -i <WORK>/a1.txt
  -loop 1 -framerate 30/1 -t 2 -i <M:m2> -ss 50 -t 4.2 -i <M:m1>
  -/filter_complex <WORK>/final.graph.txt
  -map [vout] -map [aout] -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p
  -c:a aac -b:a 128k -ar 48000 -movflags +faststart+negative_cts_offsets -f mp4 <WORK>/export.part.mp4
```
```
[0:v]setpts=PTS-STARTPTS[b0];
[2:v]setpts=PTS-STARTPTS+1/TB[os1];
[os1]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1[of1];
[of1]format=yuva420p[ot1];
[b0][ot1]overlay=x=(W-w)/2:y=(H-h)/2:enable='between(t,1,2.999999)':eof_action=pass[b1];
[3:v]setpts=(PTS-0.25/TB)/2+4/TB,fps=30/1:start_time=4[os2];
...
[b2]trim=end_frame=240[pre];
[pre]format=yuv420p[pv];
[1:a]asetpts=PTS-STARTPTS,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=end_sample=384000,asetpts=N/SR/TB[pa];
[pv]null[vout];
[pa]anull[aout]
```
(`tests/snapshots/compose_plans/overlays_picture_and_video.json`.)

- **Inputs**: the joined picture (or the last layer), the A1 sound (the chunks' WAVs through the concat demuxer, sample-exact), each overlay left, the end card.
- **Overlays** (V2 and up, the lower track first, each in start order; a muted video track is hidden): moved to their start on the timeline (`+start/TB`), at their speed, framed `fit` (no pad: the rest stays clear) or `fill`, coloured, transformed, and drawn with `enable='between(t,start,end-1µs)'`. A picture is `-loop 1 -framerate <fps> -t <length>`.
- **The program's sound:** A1 and the sounds' buses, mixed and brought to the app's loudness: see [the audio graph](#part-c-the-audio-graph).
- **Captions, text, the hook and the watermarks:** burned after the overlays, before the preset: see [part C](#part-c-captions-text-and-branding).
- **The export preset** (step 1, `video/export_presets.py`): the program at the canvas's size is the "clip" a preset is planned for (`make_plan` on `Source(canvas, fps, program + card)`). Its `vf` (size with the upscale note, aspect by crop, blur pad or colour pad, fps) goes after the overlays; its `-c:v` block is `export_presets._video_args` (CRF/CQ, a bitrate, 2-pass x264 on the CPU, VBV caps on a graphics card); audio is AAC at its bitrate. **Original** (no preset) is the renders' own encode: `video_encoder_args()` and AAC 128k at the canvas's size, as a clip is made today.
- **The size target**: the plan is make_plan's (made as without the limit, or held to it). The stage carries the one retry: a file made as without the limit that comes out over is made again at the limit's bitrate (held: 2-pass on the CPU); a file held to the limit, at 0.9x its bitrate. The smaller file is kept. The retry runs only when its bitrate is under what the file came to.
- **A graphics card that refuses** the encode: the stage's `fallback`, the same graph on the CPU (step 1's rule).
- **The end card** is joined as a concat **filter** input (`[pv][pa][cv][ca]concat=n=2:v=1:a=1`), scaled and resampled to the export's picture and sound, so it works with any codec, size and frame rate. The file is the program plus the card.
- The filter-script flag is chosen by FFmpeg's version: `-filter_complex_script` before 7.1 (CI's 6.x), `-/filter_complex` from 7.1 (the bundled 8.1).

## Command lines

Windows refuses a command line over 32,767 characters. A plan holds every command to 30,000 (`ARGV_BUDGET`, checked with `subprocess.list2cmdline`) and refuses itself (`CompileError`) if one is still over the limit:

- V1 is cut in chunks of at most 16 items, fewer when the cut command would pass the budget;
- overlays go in groups of at most 16 (inputs at most half the budget); every group but the last is a `layer` stage, the last is drawn by the final command;
- filter graphs are always in script files, so a graph's length never counts.

`tests/test_compose_plan.py` compiles a project of 300 V1 items (a third at double speed, a fifth filled), 50 overlays on two tracks (pictures and video) and 10 sounds, in a data folder with a 200-character path: every command is under the limit (19 cut stages, 3 layer stages, the join, the card, the final).

## Running a plan

`run_plan(plan, task, config=)` runs the stages in order on `task`: the lane's `TaskContext` (each FFmpeg at below-normal priority, its `-progress` as progress, killed by a cancel) or `compose.LocalTask` (tests and scripts).

- Each stage's scripts are written, its commands run, and its `temps` renamed to its `writes` only when it finished with each one there and not empty. A failure or a cancel removes the stage's temporary files: no stage's file is ever half there.
- Progress is the stages' weights (seconds of video times a cost per kind), reported as `{"stage": id}` with the fraction; the export job's `stage` field shows it.
- `TaskContext.run` gains `cwd` and `on_progress` (a task of many commands scales each command's fraction to its share), and `progress(fraction, step)`.
- A cancel kills the running FFmpeg, the `join`'s too (its command is the plan's, run on the task, fix 7-D7). A `frame` stage (tracking in Python, FFmpeg started by the cropper) checks for a cancel before it starts and again once the tracking is known (fix 7-D3), so the cropper's render never starts after a cancel; the tracking itself and a render that has started run to their end, and the cropper's FFmpeg is not at the lane's below-normal priority (step 11 reworks the cropper).

## The export route

`POST /projects/{id}/export`, guarded like the project routes (`projects_api.session_guard`: the session header and an Origin of this PC's own).

| Body field | |
|---|---|
| `revision` | The project's current revision (its head). Absent: the current one. Any other number: 409 `{message, current_revision}` (an export is always of the project as it is; undo to an older state first) |
| `preset` | An export preset (step 1's fields), `"original"` or null for the clip's own encode, `"default"` for Settings → Export's. Absent: the project's own (`document.export.preset`). A preset this PC can't encode at all: 400 |

| Answer | When |
|---|---|
| 200 `{export, branch, reasons, project_export_id, revision, notes}` | Queued. `export` is the lane job as `GET /export/jobs` lists it (`export_id`, `kind: "project_export"`, `status`, `fraction`, `stage`, `clip_id`, `project_id`, `revision`, `branch`, `project_export_id`, ...); `reasons` say why Branch B |
| 400 | A bad preset; a plan that can't be made (an empty V1, AV1 without a graphics card) |
| 403 | No session header, or a foreign Origin |
| 404 | No such project; a media the project uses is missing (the resolver's sentence) |
| 409 | `revision` is not the current one |
| 423 | The project was saved by a newer version |

**The branch.** Branch A when `projection.to_clip(document)` answers A, the project's clip still exists, and the preset is Original. Otherwise Branch B.

**The job** is a task on the editor lane listed with the exports: `GET /export/jobs`, `GET /export/jobs/{id}`, `POST /export/jobs/{id}/cancel` and the `"export"` events cover it (`EditorLane.submit_project_export`). Make clips again keeps its clip while it runs (the lane's clips in use).

- **Branch A**: `Worker._rerender_clip` with `{clip_id, start, end, render_opts, render_opts_replace: true}`, on the lane's thread with its own database connection. The clip's file is copied aside first: today's re-render can write the clip's own file in place (the same window gives the same name), so a failure or a cancel puts it back, and a file rendered under another name is removed. The clip gets a new id as Apply gives it; the project follows it (`follow_clip_id`).
- **Branch B**: the media resolved again (a source gone since the request fails the job with the resolver's sentence), the plan compiled into `data/editor_lane/job-<id>/` and run, then:
  0. for a clip's project, the clip's lock (`server/jobs.py` `clip_lock`, the one Apply and Branch A hold) is taken, checking for a cancel every half second while it waits, and held to the end of step 3 (fix 7-D14). Under it the clip's row is read again: if the clip is gone (an Apply replaced it with a new id) or its file is no longer the one the export started from (path, size and modification time read when the job began), the newer render wins: steps 2 and 3 are skipped and the export stays a project export in the folder below, recorded in `project_exports` without touching the clip;
  1. the file is moved to `data/clips/projects/<project id>-r<revision>.mp4` (`-2`, `-3` when that name is taken). Storage's clean-up never takes a file in this folder for an orphan clip (`core/housekeeping.PROJECT_EXPORTS`, fix 7-D11): a manual project's export has no clip row, and a deleted project's none at all; only whole exports land here;
  2. for a project whose clip exists, the clip's row points at it (`path`, `edited_at`): the clip keeps its id, title, posts and render options;
  3. then the clip's old file is deleted (only inside the data folder), and its editor preview, and a `clip_file_replaced` event `{clip_id, project_id}` goes out.
  The work folder is removed whatever happened.

A failed or cancelled export, either branch, leaves the clip's row and file, the project and every source as they were, with no partial file (`tests/test_compose_routes.py`).

`GET /projects/{id}/exports` → `{exports: [project_exports rows, newest first]}`, `preset` as JSON. A row still `queued` or `running` whose lane job the lane no longer knows (the app was closed mid-export) is set to `interrupted`.

## project_exports

Step 5's table gains four columns (made on a new database; added by `_migrate()` to one made before):

| Column | |
|---|---|
| `head` | The revision exported |
| `preset` | The preset as canonical JSON; empty for Original |
| `branch` | `A` or `B` |
| `status` | `queued`, `running`, `done`, `failed`, `cancelled`, `interrupted` |
| `path` | The finished file (Branch A: the clip's new file) |
| `duration_us`, `bytes` | Its length (ffprobe) and size |
| `clip_id` | The clip it made or replaced; null for a project with no clip |
| `job_id` | The lane job |
| `error` | Why it failed |
| `created_at`, `updated_at` | When |

`store.add_export(..., branch=, job_id=)` and `store.set_export` (now also `branch`, `duration_us`, `bytes`, `job_id`).

## Measured

On this PC (Ryzen 5 5600X, RTX 3060, driver 591.44), the bundled FFmpeg 8.1.2, CPU only, a generated 1920x1080 30 fps source (a moving test picture and a tone; real footage encodes more slowly), a 60 s project of six 10 s cuts on a 1080x1920 canvas, fit with the blurred pad, with the end card:

| Export | Total | cut (mezzanine) | final | Target |
|---|---|---|---|---|
| Original (x264 veryfast CRF 20) | 18.5 s (0.31x realtime) | 10.9 s | 7.4 s | ≤ 90 s |
| 1080p Balanced H.264 (x264 medium CRF 23) | 23.4 s (0.39x) | 11.1 s | 12.2 s | ≤ 90 s |
| 720p 1:1 blur, under 5 MB | 21.8 s | 10.9 s | 6.4 s (card drawn: 4.6 s, once) | |

- The same 60 s project as one AI run with face tracking (crop "track", YOLO on the RTX 3060, nothing cached), Original: 50.3 s (0.84x realtime): cut 7.1 s, frame (tracking and the cropper's encode) 30.4 s, conform 6.1 s, final 6.7 s.
- An AI run with face tracking (a 5 s generated test, 360x640 canvas): 8.9 s the first time, 1.8 s with the tracking cached; the same bytes both times.
- **Part C, everything on** (`scratch/step7/perfc.py`, the same source and six cuts, plus captions with the word highlight from a transcript, a hook, a text watermark and an image logo, looped music ducked under A1, three sound effects, a bleep and the end card), CPU, Original: **19.2 s for the 60 s export (0.32x realtime; target 90 s)**: compile 0.04 s, cut 11.3 s, sounds 0.09 s, card 0.14 s, final 7.6 s. Its loudness: -14.0 LUFS integrated, true peak -6.6 dBFS (ebur128 over the program). The draft of it: 7.4 s (540x960). **Stills of it at 0.5, 2.0, 31.2 and 59.9 s: 0.17 to 0.20 s each** at 1080x1920 (compile 7 ms; target 600 ms), plan and FFmpeg only: the route adds its database read and the media resolve.
- A still against the export's frame at the same time (a 1080x1920 smoke test with every layer): SSIM 0.996.
- **NVENC still doesn't work here**: the bundled FFmpeg needs NVENC API 13.1 and driver 591.44 gives 13.0 ("The minimum required Nvidia driver for nvenc is 610.00 or newer", checked 2026-11-14). The 0.5x-realtime and SSIM targets on the graphics card can't be measured on this PC until the driver is updated.

## Part C: captions, text and branding

One ASS file per plan (`compose.build_ass`), written as a script of the final stage (`<WORK>/export.ass`) and burned at the canvas's size, after the overlays and before the export preset, by `subtitles=export.ass` (plus `:fontsdir=fonts` only when a bundled font is used: the runner copies those files into `<WORK>/fonts` first, with step 3's `fonts.stage`). Its `PlayResX/Y` are the project's canvas, so a draft (smaller) burns the same layout scaled.

| Layer | From | Drawn as |
|---|---|---|
| Captions (layer 0) | `captions.lines` (the project's own, in the source's time), else made from the transcript over what V1 plays (`captions.build_caption_lines`, the rules a clip's captions follow) | `video/captions.caption_ass` (new: the text `build_captions` writes, which now calls it): step 3's style, fonts, animations, safe zones, word highlight and karaoke, unchanged |
| Text items and the hook (layer 1) | T tracks that aren't muted | An item with no look of its own: the hook's style exactly (`video_editor/overlay._hook_style`, the font `caption_font_for(language)` or Arial Black). An item with `font`, `size` (at a 1920-tall canvas), `color` or `position` (top, middle, bottom): a style of its own on the hook's geometry. A line break becomes `\N` |
| Text watermark (layer 2) | `branding.watermark`; absent: the settings' (`clips.watermark`) | `watermark._text_style` / `_text_events`, as a clip's render draws it, for the program's length |
| Image watermark | the same watermark, `type` image or both | `watermark.logo_graph(cfg, canvas, base, logo, out)` on the picture after the burn (above the captions, as `apply_image` puts it on a finished clip). The file is found by the caller as `apply_image` finds it: `safe_name`, then a listing of `data/branding/assets/` (`compose_api.branding_facts`), never a path from the document |

**When captions burn** (`compose.captions_on`, `core.pipeline.captions_on`'s rule): the settings' switch (`clips.captions`) off: never. Else `captions.enabled` true or false; null: the job's choice (`context.job.job_captions`, default on) for a project made from a clip, and **off** for a project made by hand.

**The style:** `captions.style`, else the settings' caption style (`clips.caption_style`). The second speaker's colour reads the turns the clip was rendered with (`context.job.speaker_turns`, moved into the source's time) with the project's `speaker_edits` laid over them (`paint_turns`, then `tag_lines`); the voice analysis is not run again.

### The caption remap rule

Captions stay in the source's time in the document (step 5). The compiler maps them through V1 (`compose.remap_captions`):

1. **Spans.** The V1 items of the captions' media (`captions.media_id`), consecutive items that go on with the same video where the last stopped (same speed) merged into one span: source `[src_in, src_out)` plays at program `[start, end)`.
2. **Each word gets a source time:** the transcript's own when the line's words line up with it (`captions._word_times`, as the highlight already reads them), else the line's time shared out by word length.
3. **A word is kept where its middle plays** (its end counted at most `LONGEST_WORD_SECONDS` after its start, as captions count it). A word in a cut-out stretch is dropped. A line split by a cut becomes a caption on each side, each holding its own words. A stretch played twice is captioned twice.
4. **Times are mapped through the span:** `program = start + (t - src_in) / speed`, held inside the span, so speed scales them. The caption runs from its words' span start (or the line's start, if later) to its end, mapped the same way.
5. **The words' own times go along,** mapped the same way, as the "spoken" words `caption_ass` reads for the word highlight and karaoke. That fixes plan problem 5 in Branch B: today's edits time the highlight from words that were never remapped (`karaoke_untimed`). Plain runs and Branch A are unchanged.

Example (`tests/test_compose_plan.py::test_captions_follow_the_timeline_through_cuts_and_speed`): source 10 to 11 s at 1x, then 12 to 14 s at 2x. The line "one two three four" (10.2 to 12.6 s) becomes "one two" at 0.2 to 1.0 s and "four" at 1.0 to 1.3 s. "three" (11.0 to 11.4 s) was cut out. "four" (12.1 to 12.6 s, its middle at 12.35 s) plays at 1.05 to 1.3 s. A line wholly in the cut is gone.

## Part C: the audio graph

```
V1 items' own sound (each cut stage, sample-exact, into a-NNN.wav):
  [j:a] asetpts, aresample 48k, atempo chain (speed), [slowed: lead trimmed],
        mutes and bleeps (volume=0 over them; a 1 kHz sine mixed in over a bleep),
        volume (item x A1), afade in/out (ss/ns, sample-exact), held to its samples
  + the tails under the next item's start (adelay in samples, amix normalize=0)

A2 and up (sounds-NN stages, one 32-bit float WAV per bus, each exactly the program's samples):
  music:  -stream_loop -1 (the whole file looped, as today's render loops a clip's music)
          or aloop over a part of the file; then atrim from loop_offset_us
  sfx, voice: -ss/-t of their stretch
  then atempo, volume (item x track), apad, atrim to its samples, afade, adelay=<start in samples>S
  buses: plain | duck_own | duck_free | voice

final:
  [A1] (the chunks' WAVs, concat demuxer)
  ducked buses: sidechaincompress under [A1 + voice bus]   (duck_own: _DUCK_OWN, duck_free: _DUCK_FREE, step 4's)
  amix=inputs=N:duration=first:normalize=0   (A1, voice, ducked, plain: nothing turned down)
  volume=<master>, loudnorm=I=-14:TP=-1.5:LRA=11 (encoding.LOUDNORM, when audio.normalize), aresample 48k,
  held to the program's samples
```

- **Tracks:** a muted audio track is not heard; when any audio track is soloed, only soloed tracks are (A1 too: an unsoloed A1 is silent). Track volume multiplies its items'.
- **Which bus:** a `voice` item is heard and is part of what the music ducks under. An item with `duck` ducks under A1 and the voices, with step 4's setting: the app's free tracks (`source.type` library) `threshold=0.05:ratio=2:attack=15:release=400`, anything else `threshold=0.02:ratio=12:attack=15:release=400` (what a person's own music file has always had). Everything else is `plain`.
- **Looped music** plays its source stretch again and again and starts `loop_offset_us` into it (step 5's note), so the right half of a split goes on where the left half stopped. When the stretch is the whole file (`source_in_us` 0 and `source_out_us` at or past the file's length, or the length unknown, as an imported clip's free track is), FFmpeg loops the file (`-stream_loop -1`); otherwise `aloop` loops exactly that stretch.
- **A sound effect** is placed with `adelay=<samples>S`: it starts on its sample (`test_a_click_lands_within_an_audio_frame`).
- **Fades** are each item's `fades.in_us` / `out_us`, sample-exact (`afade` with `ss`/`ns`), on V1's items and on sounds alike. They are sound fades; the picture doesn't fade (as an imported clip's `fade_in`/`fade_out` never faded it).
- **Loudness:** the whole mix, after the master volume, is brought to the app's target by `encoding.LOUDNORM` (today's single-pass loudnorm, -14 LUFS, true peak -1.5 dBTP) when `audio.normalize` is on (the default; an imported clip keeps its `normalize_audio`). Today's render normalises the voice before the music is mixed in; the compiler normalises the mix, as the plan says, so music or effects can't push the export over the target.
- **A video on V2 and up is not heard** (its picture only); the plan's `notes` say so. Step 10 decides.
- **A project with no sound at all** still gets a stereo AAC track of silence of the program's length (each V1 item's sound is `anullsrc` when its media has none), as today's clips always have one. When nothing can be heard (no V1 item is a video with sound at a volume above 0 on an A1 that plays, and there are no sounds on A2 and up), loudnorm is left out: it can't measure silence. Under 3 s, loudnorm on digital silence (a video whose track is silent) puts out NaN, which the AAC encoder refuses; so a program shorter than `LOUDNORM_MIN_S` (3 s) gets `NAN_GUARD` after loudnorm, an `aeval` that turns NaN and infinity into 0 and leaves every other sample as it is (fix 7-D10).
- **Slowed items (atempo's lead):** `atempo` starts a sound early: about 0.019 s / f per stage of factor f, compounding through a chain (measured with the bundled FFmpeg 8.1: 38 ms at 0.5x, 23 ms at 0.75x, about 0.1 s at 0.25x; 7 to 20 ms above 1x). For an item slower than 1x, the sound is read `LEAD_READ_S` (0.05 s) plus the lead earlier than the picture (a second input of the same file) and that much is trimmed off after atempo, so it starts where the picture does (`audio_lead`); with nothing before the item in the source, the rest is a silence at its start. At 1x and above (a lead under a frame) nothing is changed.

### The bleep

`set_audio_mutes` mode `bleep`: the stretch's own sound is muted (as `mute` does) and a **1 kHz sine** plays in its place, at FFmpeg's `sine` source's own level (**1/8 of full scale, -18 dBFS**), times A1's volume, before the mix's loudness. Mutes and bleeps are in the captions' media time and apply to the V1 items of that media, at the item's speed. Their edges are where the `volume` filter's `enable` switches, so to the audio frame (about 21 ms at 48 kHz), as a clip's word mutes are today.

### The tail pad (plan problem 4)

**What today's code does:** a clip's render cuts `END_PAD` (0.4 s) past its end (`core/pipeline.py`, before `frame_intermediate`), because Whisper's word ends run early. With any cut (`edit.keep`), `apply_edits` keeps only the keep ranges, which `EditList.from_dict` clamps to the clip's unpadded length, so **the pad is dropped**: the clip ends exactly on the last kept range's end, and each cut inside it ends exactly on its range's end too. Confirmed by reading `video_editor/timeline.py` (`_clean_ranges` clamps to `duration`) and `video_editor/export.py`. Plain runs and Branch A are not changed.

**What Branch B does** (`compose.tails`): where a run of V1 ends (a cut, or the program's end) in the middle of its last word (the last word that started before the cut, with its transcript end plus 0.4 s past the cut), the sound goes on past the cut up to that point. It never reaches the next word's start, the video's end, or a mute or bleep. The tail fades out over its whole length and is heard **under the next item's start** (an L-cut). The picture and the timeline stay as the document has them, so no caption, overlay or sound moves. A tail at the end of a chunk is played by the next chunk's stage, which reads it from the source. The program's last tail is heard **under the end card**. Without a card there is nothing to let it finish under, so it is cut where the timeline ends, and the plan's `notes` say so. Tails need the transcript; a project without one gets none.

## Part C: the draft

`compile(doc, rev, preset, "draft")`: the export's plan with:

- **540 on the canvas's short side**, its shape kept (`draft_size`: 1080x1920 → 540x960, 1080x1350 → 540x676; a canvas already that small is kept), every graph built at that size (framing, overlays, transforms, the logo); the ASS file stays at the canvas's own size and libass scales it;
- chunks at x264 ultrafast CRF 18 (`DRAFT_MEZZ`), the final encode x264 ultrafast CRF 28 (`DRAFT_ARGS`), one pass, AAC 128k;
- no export preset (the one given is ignored) and no end card: the draft is the program;
- AI items tracked as the export tracks them (the same cache key: a draft's tracking is the export's, and the other way round).

## Part C: the still

`compile(doc, rev, None, "still", at_us=t)`: one JPEG of the program at `t` at the canvas's size (`still_plan`).

- **The frame** is the one the export shows at `t` (`still_frame`: the last frame n whose start `model.frame_us(n)` is not after `t`, so a time from `frame_us` is that frame; held inside the program). Each input is seeked on its own (`-ss` before `-i`) to the source frame the export's `fps` filter shows there (`source_frame_time`: the latest source frame whose time from the item's in point, divided by the speed and rounded to the nearest output frame with a tie going up, is not after it; at 0.5x every source frame is shown twice and the still takes the earlier one, and an in point between source frames starts on the frame before it, as the export does since 7-D9), half a source frame early, then `-frames:v 1`.
- **Every layer** an export draws: V1's framing, colour and transform; the overlays on screen at `t`; the captions, text and watermarks (the same ASS file; the picture is given the program's time with `setpts=...+t/TB`, so the burn and a moving logo are where the export has them). The sound is not made.
- **JPEG:** `scale=out_range=full,format=yuvj420p`, `-q:v 2`.
- **An AI item** uses the tracking an export or a draft kept (`still_run` gives the run's tracking key, `read_cached_tracking` reads it, `Inputs.tracking` carries it). A followed subject: the cropper's own 9:16 window at that moment (full height, centred on the path, held inside; `video/cropper.py _render_tracked`), then its colour. Fit with a blurred backdrop: the cropper's own graph. **Anything else**: the middle 9:16 of the frame, and the plan's `notes` say the still is approximate. That covers no tracking kept yet, a Gaming layout, a sport's or a podcast's framing, zoom, bands, and the face-safe 4:5. Crop `center` without tracking is exactly that window, so it isn't marked.

## Part C: the routes

All internal, in `server/compose_api.py`. A changing route takes `projects_api.session_guard` (the session header and an Origin of this PC's own). A **read route** takes an Origin of this PC's own, and either the session header or the project's token in the address (`?t=`: `media_api.media_token(secret, "project:<id>")`, the HMAC of step 6's media tokens; another project's token is refused).

**`POST /projects/{id}/export`** (part B) now also answers `urls`: `{"still": "/projects/<id>/still?rev=<r>&t=<token>&t_us=", "draft": "/projects/<id>/draft?rev=<r>&t=<token>"}`. A Branch B export's inputs now carry the transcript and language (`caption_facts`: `data/transcripts/<video id>.json` of the captions' media, when that is a library video), the settings' caption style, captions switch and watermark, and the logo's file (`branding_facts`). The media read now include every audio track's.

**`POST /projects/{id}/draft`** `{revision?}`

| Answer | When |
|---|---|
| 200 `{job: null, revision, draft: {revision, bytes, url}, urls}` | The draft of this revision is made already |
| 200 `{job, revision, draft: null, urls}` | Queued: `job` is the lane task (`kind: "project_draft"`, `job_id`, `key: "project_draft:<id>:r<rev>:r<rev>-<facts key>"`, `status`, `fraction`, `stage` while running, `project_id`, `revision`, `result: {project_id, revision, bytes, url}` when done). Its progress comes as `media_job` events, like step 6's tasks. Asked again while it waits or runs: the same job |
| 400 | A plan that can't be made (an empty V1) |
| 403, 404, 409, 423 | As the export: protection; no such project or a missing media; another revision than the current one; saved by a newer version |

The **facts key** (`compose_api.facts_key`, fix 7-D6) is a 12-character hash of what a draft or still is made from besides the document: the settings' caption style, captions switch and watermark, the logo file and the transcript file (each by its modification time and size, so nothing large is read). A change that doesn't raise the revision (Settings, a new logo, a transcript made again) makes a new draft or still instead of serving the old one.

The draft is kept in **`data/media_cache/project-<id>/draft/r<rev>-<facts key>.mp4`** (never in `clips/` or `downloads/`). A newer revision's draft removes the older ones. The folder is a cache folder: "Clear editor cache" removes it, the cap counts it (`draft` and `stills` are new `cache.PRODUCTS`, each with an `index.json` "used" mark), and Storage's orphan clean-up removes it (it has no `source.json`); a draft is made again when asked for. The current editor's "Update preview" is unchanged.

**`GET /projects/{id}/draft?rev=`** (read route): the file, with Range and ETag (`media_api.serve_file`). 404 until it is made; 409 for another revision.

**`GET /projects/{id}/still?t_us=&rev=`** (read route): `image/jpeg`, with `X-Clips-Kitty-Still-Time: <the frame's time, µs>`, and `X-Clips-Kitty-Still-Note: approximate` when an AI item had no tracking kept. It is made in the request: one short FFmpeg, not queued on the lane behind exports. It is kept as `data/media_cache/project-<id>/stills/r<rev>-<facts key>-<frame µs>.jpg`, so any time inside the same frame is the same still; the newest 200 are kept per project. An approximate still isn't kept, so it is made again once tracking exists. 400 for a negative time or an empty V1; 404, 409, 423 as above.

Only the current revision has a draft or a still: a revision number is a head (it only goes up), and an older head's document can't be told from an undone one (choice 11).

## Choices made

1. **The window is rounded to the hundredth.** The clip's row holds `start_s` and `end_s` to a hundredth of a second (`StateDB.add_clip`), and projection's `start` and `end` can be finer after a trim. The job renders the rounded window, so the file and its row agree. The options' times are kept as sent, so they can be up to 5 ms off the rounded start, which is under one frame at 60 fps. Shifting them by that amount would be the second shift step 5 warned about.
2. **Speaker fixes that end before a later start are kept, and the caption render ignores them.** Today's re-render keeps a fix that lies outside the clip ("back when the clip grows again"), and the project keeps its fixes in source time. A test proves that lines built fresh, and saved lines tagged, are the same with and without fixes at negative times.
3. **Gained seconds are captioned as Apply captions them.** Projection's caption lines cover what the project's lines cover. A clip that grew gets captions from the transcript for its new seconds, before the first line and after the last, as `refit_caption_lines` does for Apply. The head is filled only up to the earlier of the row's old start and the first sent line, so lines the project already holds for the head are not captioned twice, and an unmoved window gets nothing new.
4. **`encoder=` is the video block only.** Audio in `cut_clip` and `apply_edits` stays AAC 128k. If the compiler wants a lossless mezzanine audio track, it adds its own keyword the same way (default: today's).

Part B:

5. **The mezzanine is x264 veryfast CRF 12, no B-frames, a keyframe every 2 s, the timescale from the canvas's rate** (`compose.mezz_args`). CRF 12 is visually lossless on 1080x1920, so the final encode is the only generation a viewer can see; no B-frames makes every chunk's timestamps plain for the stream-copy join; one timescale for every chunk makes the copy valid. Measured: the 60 s cut takes 11 s. A lossless mezzanine (`-qp 0`) was rejected: about ten times the disk for no visible gain.
6. **V1's sound is cut beside its picture, as 16-bit PCM WAV, sample-exact**, and joined by the concat demuxer in the final command. Not AAC in the mezzanine: each AAC part carries its own priming and padding, which a join of 300 parts would add up to an audible drift. `encoder=` (part A) is therefore not used for the cut: the compiler's cut is one command per chunk (one input per item), not `cut_clip` per item, which keeps 300 items to 19 commands instead of 300.
7. **An AI run is cut by the compiler first and framed second.** `frame_intermediate` gets the run already cut (at mezzanine quality, at its speed) as its source, with no `edit`, so `apply_edits` (which encodes with the renders' encoder) never runs on it. The cropper's own encode (today's quality: x264 CRF 20 or NVENC) is kept: AI items pass through one more visible-quality encode than manual ones until step 11 teaches the cropper the canvas and an encoder. A Gaming layout's detection and a sport's framing are not cached.
8. **AI framing on another canvas is fit with a blurred pad** until step 11 (the plan's `notes` says so).
9. **A manual item's pad is a new framing key**, `pad` (`"blur"`, the default, or `#RRGGBB`). The plan named "fit with blur or colour pad" but step 5's framing had no field for it.
10. **An overlay video's own sound is not heard** in part B (V1's items make A1); part C decides whether V2's sound joins the mix.
11. **An export is always of the current revision** (409 for another): the document of an older head can't be told apart from an undone one by its number (step 5's `head` only goes up).
12. **A clip's project export replaces the clip's file and keeps the clip** (same id, title, posts, render options), the file at `data/clips/projects/<project id>-r<revision>.mp4` (ED28). Branch A replaces it as Apply does (a new id). A project with no clip gets only the file and its row.
13. **Branch A runs on the editor lane**, calling `Worker._rerender_clip` (today's job, unchanged) in the lane's thread, not in the processing queue, so an export never waits behind an hour of processing. Tracking takes the tracker's own inference lock. Every re-render of a clip holds that clip's lock (`server/jobs.py` `clip_lock`, fix 7-D8), so Branch A and a queued Apply of the same clip run one after the other; Branch A checks for a cancel every half second while it waits. A processing run (Make clips again) is not held by it: the lane's clips-in-use already keeps its clip. A render that is running can't be stopped (as Apply can't): its cancel takes effect when the render ends, before any row changes, and the clip's file comes back from the copy.
14. **The size target's retry runs at a bitrate fixed when compiling** (the limit's, or 0.9x the held one) and only when that is under what the file came to; export_presets.encode works the bitrate out from the file. A retry after a graphics card fell back to the CPU uses the card's plan.


Part C:

15. **Captions are mapped word by word, by where each word's middle plays** ([the rule](#the-caption-remap-rule)), not line by line: a cut through a line keeps each side's own words, and the highlight and karaoke use the mapped word times. A line whose words don't line up with the transcript is shared out by word length, as the highlight already does. Branch B only (ED29).
16. **A project made by hand has no captions until they are switched on** (`captions.enabled` null means the job's choice, and such a project had no job).
17. **The tail pad is an L-cut:** the last word's end is heard under the next item's start (or the end card), with the picture and the timeline unchanged, rather than lengthening the item, which would move everything after it and make the export disagree with the editor's timeline (ED29). Without an end card the program's last tail is not kept.
18. **Loudness is set on the whole mix** (after the master volume), as the plan says, not on the voice before the music as today's render does: the export holds -14 LUFS whatever music and sounds are on it. It stays today's single-pass `LOUDNORM`; a fade is evened out a little by it, as a two-pass (measured) loudnorm would not (left for step 10) (ED30).
19. **A bleep is a 1 kHz sine at -18 dBFS times A1's volume** in place of the muted sound (ED30).
20. **Sounds on A2 and up are premixed by bus** in `sounds-NN` stages (at most 16 to a command), so the final command has one input per bus and any number of sounds fits Windows' limit; the ducking is applied to the bus.
21. **Ducking follows step 4:** a ducked item dips under A1 and the voice items, with the free tracks' setting for a library sound and the person's-own-file setting for anything else (`video_editor/export.py _DUCK_FREE`, `_DUCK_OWN`).
22. **Slowed items' sound is read early to take atempo's lead out** (`audio_lead`): measured 38 ms at 0.5x, about 0.1 s at 0.25x, which is past one frame. Above 1x the lead is under 20 ms and is left (no second input per item).
23. **The draft and the stills live in the editor cache** (`data/media_cache/project-<id>/`), one draft per project and the newest 200 stills, never in `clips/` or `downloads/`; Clear, the cap and the orphan clean-up remove them, and they are made again (ED31).
24. **A still is made in the request, not on the lane:** one FFmpeg of a single frame (0.17 to 0.20 s here), which a queue behind an export would hold back for minutes. A draft (seconds to minutes) is a lane task (ED31).
25. **An AI item's still uses the tracking an export or a draft kept** and draws the cropper's window from it. With none kept it shows the middle 9:16 and says it is approximate, rather than tracking in a request.
26. **Only the current revision** has a draft or a still (as an export: choice 11).
27. **Text items take the hook's look** unless they have their own font, size, colour or place. Text the document holds is already without ASS override characters (step 5's ops); the compiler strips them again and writes a line break as `\N`.

## Tests

| File | What it checks | Runs on CI light |
|---|---|---|
| `tests/test_step7_helpers.py` | `encoder=` changes only the encoder block, and isn't retried; `logo_graph()` equals the step-0 snapshot's graph, and names its pads; `frame_intermediate()` cuts as the pipeline did and uses the stand-ins it is given | yes |
| `tests/test_branch_a_replace.py` | replace, not merge; no second shift; gained seconds captioned once; window rounding; a job without the key unchanged; speaker fixes before the start ignored; Branch A of an unchanged project gives the render what Apply gives it | the speaker and refit tests; the job tests need OpenCV (Plugin platform job) |
| `tests/test_editor_import_guard.py` | `core/pipeline.py` and `core/framing.py` reach none of `video_editor.project`, `.media` or `.compose`; only `server/compose_api.py` imports the compiler; the compiler imports the rest of the app only inside functions and loads nothing heavy | yes |
| `tests/test_compose_plan.py` | Plan snapshots (`tests/snapshots/compose_plans/`, 32 cases: one item, cuts, speed 0.5 and 2, manual fit, fill, align, pad and transform on a 16:9 source into 9:16, an AI crop, a Gaming layout, picture and video overlays and their looks, colour, the end card, every preset family: size, aspect by blur, bars and crop, a frame rate, HEVC, a size target on the CPU and the graphics card, Original on a graphics card, FFmpeg 6's flag, AI items on a 16:9 canvas, 29.97; part C: text and the hook, captions with the word highlight after a cut and 0.5x and 2x, karaoke on saved lines with a bundled font, music looped from its offset and ducked, sound effects and a voice, mutes and bleeps with fades, the text and image watermark, the tail pad, the draft, a still with overlays and captions, an AI item's still from its tracking); compiling is pure; exact frames and samples; atempo chains; the flag by version; AI runs; the tracking key; the 300-item project's commands all under 32,767 characters (its 10 sounds in one sounds stage); the caption remap rule (words in a cut dropped, a split line, speed, a stretch played twice); when captions burn; the tail rule (never into the next word, never over a mute, none without a transcript); the buses and the loop offset; the bleep and fades; the draft's size, encoder and no card; the still's frame and seek; an AI still without tracking is approximate; the still finds the export's tracking key | yes |
| `tests/test_compose_render.py` | With real FFmpeg: the export lasts the program (and the program plus the end card) within a frame; two CPU exports have the same framemd5; a preset's size; a failed stage and a cancel leave no stage's file; part C: a white flash and its beep on the same frame (within one) after cuts at 1x, 2x and 0.5x, and with in points between source frames at 0.25x, 0.5x and 1x (the still white and dark on the same frames as the export, 7-D9); a 1 s picture, a 2 s video with a silent track and a 2 s item at volume 0 export and draft with a silent track (7-D10); captions on screen at their remapped times after a cut and 2x, and off between; the mix at -14 ±1 LUFS with a true peak at most -1 dBTP (ebur128, with ducked looped music); a click placed at 1.234567 s starts within an audio frame; the still at three times against the export's frame there, SSIM ≥ 0.97, with every layer; the draft is 540x960 and lasts the program | skips without FFmpeg; Plugin platform job |
| `tests/test_compose_routes.py` | The routes' protection; 404, 409, 400, a missing source refused before queuing; the branch rule; cancelling a queued export; an interrupted one; a manual project's export in the projects folder with its row; a clip's file replaced only once the new one is made; a failed export (the source deleted mid-export) and a cancelled one (its FFmpeg killed) leave the clip, the project and every file as they were; Branch A's file equals Apply's; part C: the draft and still routes' protection (header or the project's own token, never a foreign Origin), their 404, 409 and 400; a draft made on the lane once (the same job when asked again), kept per revision in the editor cache, served with Range, and replaced by a newer revision's; a still is a JPEG of its frame, kept and served again for any time in that frame, and made again when the transcript changes (7-D6); a manual project's export survives Storage's clean-up, also once its project is deleted (7-D11); the cancel test's final stage is a stand-in FFmpeg that runs until it is cancelled (7-D2); an Apply registering while a Branch B export of the same clip finishes keeps its new file, and a clip whose file changed during the export is left alone (7-D14) | needs FastAPI (Plugin platform job); the renders need FFmpeg, Branch A's OpenCV |
| `tests/test_compose_lane.py` | A project export is listed and announced with the exports; cancelling a waiting one tells it; the tracking cache (kept, used again, keyed by settings, counted by the cap and Clear); a cancel made while tracking stops the frame stage before the cropper's render (7-D3); the new project_exports columns, on an older table too; the `pad` framing | yes |

## Left for steps 8 to 11

- **Step 8:** the export dialog over `POST /projects/{id}/export`; the viewer over `GET .../still` and the draft (`POST`/`GET .../draft`, its `media_job` events); exposing `X-Clips-Kitty-Still-Time` and `X-Clips-Kitty-Still-Note` to the page (CORS `expose_headers`) if it reads them with `fetch()`; what a project with a deleted clip shows; showing `clip_file_replaced`; what the old editor shows for a clip whose file a project export replaced (its render options still describe the clip as it was, so Apply from the old editor would render that again).
- **Step 9:** the `captions.ass` route can serve `compose.build_ass(doc, inputs, canvas)` as it is (the same file the export burns), and JASSUB draws it with the fonts of `fonts.stage`.
- **Step 10:** the audio panel over the bus model above; a two-pass loudnorm (a measure stage) so fades aren't evened out; whether a video on V2 and up is heard; the item's picture fades, if wanted (fades are sound only now).
- **Step 11:** the cropper and the Gaming layouts on any canvas (AI items on 16:9, 1:1, 4:5), and an encoder for the cropper so an AI item gets no extra visible encode.
- **Small faults from the last check, not fixed (none serious; fix them early in step 8):**
  - **Long AI runs on one command line:** only manual chunks are held under the length budget. An AI run is cut in one command of up to 64 items (two inputs each when slowed), so in a very deep data folder (about 200 characters) a 64-item run at 0.5x is refused with "command is too long for Windows" instead of being split. A usual data folder compiles (longest command 11,594 characters). Fix: split AI runs by the same length check as manual chunks.
  - **Memory of a long AI run's cut:** every item is its own decoder in one FFmpeg, so 64 items on a 1080p source peaked at 2.2 GB (16 items: 0.9 GB). Fix: cut at most 16 inputs per command and join the sub-cuts before framing.
  - **Two exports of one clip's project:** the second deletes the first export's file (by then the clip's file), but the first `project_exports` row stays `done` with a path that no longer exists, so step 8's list would offer a missing file. Fix: mark that row replaced, or have the route say whether the file exists.
  - **Branch B is 0.4 s shorter than Branch A for the same clip:** Branch A keeps today's 0.4 s pad after the clip's end; Branch B ends at the item's out point and keeps the last word's tail only as sound under the end card. With the end card off, a last word that runs to the out point is cut. Decide in step 8 whether an imported clip carries its pad in its length, or whether the program's last item gets the tail without an end card.
  - **A crash between moving the export and updating the clip's row** would leave a file in `clips/projects/` that nothing names, and Storage never sweeps that folder (reasoned from the code, not reproduced).
  - **Branch A's restore after a cancel** copies the clip's file back in place, which would fail if a player held the file open on Windows (not reproduced).
  - **Stills are made in the request** with no limit on how many FFmpeg processes run at once; step 8's scrubbing should send one at a time or cancel the older one.
  - **Indentation:** the continuation lines of the three renamed calls in `core/framing.py` kept their old indentation. Cosmetic; behaviour is unchanged.
- **Open:** a running Branch A render can't be stopped mid-way, nor the tracking or the cropper's render inside a `frame` stage (the cancel lands when the one running ends; 7-D3); a Gaming layout's detection is not cached.
- **For step 8 (7-D5):** after a Branch B export of a clip's project, the clip's row keeps its `start_s`, `end_s` and `render_opts` while its file is the export (`clips.path` equals a `project_exports` row's `path` with that `clip_id`: that is how to tell). Today's editor would show the window's length, and its Apply would re-render the window and replace the export with a render without the project's edits. Step 8 routes Apply for such a clip through its project (or warns).
- **For step 9 (7-D13):** braces and backslashes typed into text are removed by step 5's `clean_text`, not escaped, so `{x}` shows as `x`. Kept for now: the hook goes to today's renderers through Branch A, which don't escape them, and an escape (`\{`) is libass-only. Step 9, which draws text on the viewer, decides an escape that works in both branches and in JASSUB.
- **Loudness after AAC (7-D12):** on a peaky synthetic source (pink noise with a 3 Hz swell) the true peak after the AAC encode reached -0.6 to -0.9 dBTP (the plan's proof asks at most -1), and one project with a bleep measured -15.5 LUFS. A true-peak ceiling of -2 instead of -1.5 was tried and did not fix it (-0.8 dBTP on the same source), so nothing was changed; step 10's two-pass loudnorm and a limiter after it are the place. The test's speech-like source stays inside both limits.
- **CI's FFmpeg (7-D1):** `ATEMPO_LEAD_S` was measured with the bundled FFmpeg 8.1 only. If CI's FFmpeg 6.x leads differently, the 0.25x and 0.5x sync tests show it on the first CI run; then measure the lead per version.
