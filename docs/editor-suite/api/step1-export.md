# Step 1 API: export presets, the editor lane and WoopSocial's smaller copy

The engine side of step 1, as the desktop app's UI uses it. Every route here is
on `http://127.0.0.1:8765`. The new routes are internal (`server/api_stability.py`
lists none of them), so their shape may change with the screens that use them.
The two stable export routes only gained an optional field.

Engine code: `video/export_presets.py` (presets, estimates, the encode),
`video/encoding.py` (`encoder_for`, `export_codecs`), `server/editor_lane.py`
(the lane), `server/api.py` (routes), `publish/woopsocial.py` (the smaller copy).

## Protection

- **Host check:** every route, as before (`TrustedHostMiddleware`: `127.0.0.1` or `localhost` only).
- **Session header:** `X-Clips-Kitty-Session: <secret>`, the same check the plugin
  routes make. Without it, or with a wrong one, the answer is
  `403 {"detail": "This needs the X-Clips-Kitty-Session header. ..."}`. Needed by:
  - `POST /clips/{clip_id}/export` and `POST /export/batch` **when the preset is not Original**;
  - `POST /export/jobs/{export_id}/cancel`;
  - `PATCH /settings` **when the body has `export`** (the check runs first, so a refused call changes nothing).

  The UI gets the secret from `sessionSecret()` in `ui/src/renderer/src/lib/plugins.ts`
  (`window.studio.pluginSession()`). The browser shim (`npm run dev:web`) has none,
  so these calls answer 403 there. `request()` in `lib/api.ts` replaces the
  headers object when `init.headers` is passed, so a guarded call must send
  `Content-Type: application/json` itself.
- Everything else (reading presets, codecs, estimates, jobs; Original exports; the
  WoopSocial setting) needs no header, as before.

Bad values: `400 {"detail": "<a sentence for a person>"}`, for example
`"resolution can't be '999p'. It is one of: same, 240p, ..."`. A body field of the
wrong JSON type is FastAPI's `422`.

## The preset object

Every preset the engine returns has all of these keys. `kind` decides whether
the rest is used: `"original"` is the copy, byte for byte, and ignores the rest.

| Key | Values | Default | Notes |
|---|---|---|---|
| `kind` | `"original"`, `"custom"` | `"custom"` (`"original"` for the saved default until one is saved) | |
| `resolution` | `"same"`, `"240p"`, `"360p"`, `"480p"`, `"540p"`, `"720p"`, `"1080p"`, `"1440p"`, `"2160p"`, `"custom"` | `"same"` | Counts the **short** side: 720p is 720x1280 at 9:16, 1280x720 at 16:9, 720x720 at 1:1, 720x900 at 4:5. `"same"` keeps the clip's short side. |
| `width`, `height` | 128-4096, rounded to even | 1080, 1920 | Used only with `resolution: "custom"`; then `aspect` is ignored. |
| `aspect` | `"same"`, `"9:16"`, `"16:9"`, `"1:1"`, `"4:5"`, `"custom"` | `"same"` | `"same"` is the clip's own shape. |
| `aspect_w`, `aspect_h` | 1-32 | 9, 16 | Used only with `aspect: "custom"`. The long side stops at 4096. |
| `fill` | `"crop"`, `"blur"`, `"bars"` | `"crop"` | How a different shape is filled: cut to fit, the clip blurred behind itself, or colour bars. |
| `bar_color` | `"black"`, `"white"` or `"#RRGGBB"` | `"#000000"` | Always returned as `#RRGGBB`, upper case. Used only with `fill: "bars"`. |
| `quality` | `"smallest"`, `"balanced"`, `"high"`, `"custom"` | `"balanced"` | |
| `rate` | `"crf"`, `"bitrate"` | `"crf"` | Used only with `quality: "custom"`. |
| `crf` | 1-51 | 23 | Custom quality: CRF on the CPU, CQ on NVIDIA, ICQ on Intel. Lower is better and bigger. |
| `bitrate_kbps` | 100-150000 | 6000 | Custom bitrate. 2-pass on the CPU, capped VBR on a graphics card. |
| `codec` | `"h264"`, `"hevc"`, `"av1"` | `"h264"` | Offer only what `GET /export/codecs` lists. AV1 needs a graphics card that makes it. |
| `encoder` | `"auto"`, `"cpu"`, `"gpu"` | `"auto"` | Auto: the encoder forced in settings.yaml first, then the graphics card, then the CPU. GPU with no card for that codec is refused (400). |
| `fps` | `"same"`, 24, 25, 30, 50, 60 | `"same"` | Never above the clip's: 60 on a 30 fps clip stays 30. Numeric strings (`"30"`) are accepted. |
| `audio_kbps` | 64-320 | 128 | AAC at 48 kHz. |
| `max_mb` | `null` or 5-4000 | `null` | "Keep under N MB", decimal megabytes (1 MB = 1,000,000 bytes). Rounded to 0.1. |

Numbers out of range are clamped, not refused (`crf: 99` becomes 51), and sizes
are rounded to even. Unknown keys, values outside the lists, booleans, NaN and
non-numeric strings are refused (400).

With `max_mb`, the video bitrate is `0.95 x 8 x bytes / seconds - audio`, never
below 100 kbps, and never more than the chosen quality needs (a 20-second clip
"under 100 MB" is not made 95 MB). After the encode the file is checked and, if
it came out over, made once more at 0.9x the bitrate. If it is still over, the
job is done with `result.over_target: true`.

## Settings → Export

### `GET /settings`

The existing answer, plus:

```json
{"...": "...", "export": { "kind": "original", "resolution": "same", "...": "every key of the preset object" }}
```

### `PATCH /settings`  (session header when `export` is present)

```json
{"export": {"kind": "custom", "resolution": "720p", "max_mb": 95}}
```

`export` may be a preset object (missing keys take their defaults) or the string
`"original"`. It is stored in the database (`app_state` key `export_settings`), not
settings.yaml. With only `export` in the body, settings.yaml isn't touched and the
answer is:

```json
{"ok": true, "export": {"kind": "custom", "resolution": "720p", "width": 1080, "height": 1920, "aspect": "same",
  "aspect_w": 9, "aspect_h": 16, "fill": "crop", "bar_color": "#000000", "quality": "balanced", "rate": "crf",
  "crf": 23, "bitrate_kbps": 6000, "codec": "h264", "encoder": "auto", "fps": "same", "audio_kbps": 128,
  "max_mb": 95.0}}
```

With other fields too, the usual answer gains the same `"export"` key. 400 for a
bad value, an AV1 preset on a PC without an AV1 encoder, or `encoder: "gpu"` on a
PC without a card for that codec.

### `GET /export/presets`  (no probing, instant)

```json
{
  "default":  {"kind": "original", "...": "the saved default"},
  "original": {"kind": "original", "...": "..."},
  "custom":   {"kind": "custom", "resolution": "same", "quality": "balanced", "...": "the starting values"},
  "choices": {
    "kind": ["original", "custom"],
    "resolution": ["same", "240p", "360p", "480p", "540p", "720p", "1080p", "1440p", "2160p", "custom"],
    "aspect": ["same", "9:16", "16:9", "1:1", "4:5", "custom"],
    "fill": ["crop", "blur", "bars"],
    "bar_color": ["black", "white"],
    "quality": ["smallest", "balanced", "high", "custom"],
    "rate": ["crf", "bitrate"],
    "codec": ["h264", "hevc", "av1"],
    "encoder": ["auto", "cpu", "gpu"],
    "fps": ["same", 24, 25, 30, 50, 60],
    "audio_kbps": [64, 96, 128, 160, 192, 256, 320]
  },
  "limits": {"width": [128, 4096], "height": [128, 4096], "aspect_w": [1, 32], "aspect_h": [1, 32],
             "crf": [1, 51], "bitrate_kbps": [100, 150000], "audio_kbps": [64, 320], "max_mb": [5, 4000]}
}
```

`choices.codec` is every codec the engine knows; show only those `GET /export/codecs` lists.

### `GET /export/codecs`

```json
{"codecs": [{"codec": "h264", "cpu": "libx264", "gpu": null},
            {"codec": "hevc", "cpu": "libx265", "gpu": null}]}
```

Only codecs this PC can encode, in the order h264, hevc, av1. `cpu` and `gpu` are
FFmpeg's encoder names (for a tooltip), or `null` where there is none. AV1 appears
only with a working graphics-card encoder. The first call probes each encoder by
encoding a test frame (13 test encodes, 0.43 s on the dev PC, which has no
working hardware encoder; a hanging driver could take longer, up to 30 s per
encoder); later calls answer from memory.
A failed probe is tried again after 5 minutes (a busy card can refuse once).
This never changes the encoder clip renders use.

### `POST /export/estimate`

```json
{"preset": {"kind": "custom", "resolution": "720p"}}
{"preset": "default", "clip_id": 12}
{"preset": {"kind": "custom", "max_mb": 95}, "seconds": 180}
```

`preset` is an object, `"default"`, `"original"` or absent (Original). With
`clip_id`, the clip's own file is read (size, frame rate, length, how busy the
picture is). Without one, a 1080x1920 60 fps clip `seconds` long (default 60,
1-21600) is assumed: that is the per-minute figure for the Settings card.

```json
{
  "kind": "range",
  "bytes": 20012558, "low": 11438907, "high": 31444094,
  "per_minute": {"bytes": 20012558, "low": 11438907, "high": 31444094},
  "seconds": 60.0,
  "width": 720, "height": 1280, "fps": 60.0,
  "codec": "h264", "encoder": "libx264", "hardware": false,
  "upscale": false,
  "mode": "quality", "video_kbps": null, "audio_kbps": 128, "crf": 23, "passes": 1,
  "target_bytes": null, "target_reachable": null,
  "available": true, "clip_id": null
}
```

- `kind`: `"range"` for the quality levels and a custom CRF (the size depends on the
  footage: show `low`-`high`), `"close"` for a bitrate or a size target (show `bytes`),
  `"copy"` for Original with a clip (its exact size). Original without a clip is a
  `"range"` around the app's usual 48-54 MB a minute.
- `upscale`: true when the export is bigger than the clip (1440p and 2160p from a
  1080 clip, or a crop that zooms in). For the short warning marker; the detail
  goes in its tooltip.
- `target_reachable: false`: the size limit is too small for this length; the
  file will be made at the lowest bitrate and may still be over.
- `available: false`: this PC can't encode the preset's codec (AV1 without a card).
- `mode`: `"copy"`, `"quality"`, `"bitrate"` or `"target"`.
- Errors: 404 for an unknown `clip_id`, 400 for a clip with no file or a bad preset.

Checked on 6 real finished clips (CPU, 30 and 60 fps): the files came out at
0.85-1.10x `bytes`, inside `low`-`high`.

## Exporting

### `POST /clips/{clip_id}/export`, `POST /export/batch`

```json
{"folder": "C:/Users/me/Downloads", "preset": "default"}
{"clip_ids": [12, 13], "folder": "C:/Users/me/Downloads", "preset": {"kind": "custom", "resolution": "720p"}}
```

`preset` absent, `null`, `"original"`, `{"kind": "original", ...}`, or `"default"`
while the saved default is Original: the copy, exactly as before, no header needed:

```json
{"exported": ["C:\\Users\\me\\Downloads\\my-clip.mp4"]}
```

Anything else: session header, then one lane job per clip, answered at once:

```json
{
  "exported": [],
  "jobs": [{"export_id": 1, "kind": "export", "clip_id": 12, "status": "queued", "cancelling": false,
            "fraction": 0.0, "pass": null, "passes": null, "encoder": null,
            "folder": "C:\\Users\\me\\Downloads", "name": "my-clip", "preset": {"...": "normalised"},
            "result": null, "error": null,
            "created_at": "2026-11-05T10:18:55", "started_at": null, "finished_at": null}],
  "skipped": [13]
}
```

`skipped` lists clips that don't exist or have no file. The folder is created
if missing (400 if it can't be). The file is written beside its final name as
`<name>.exporting-<8 hex>.mp4` and renamed when done, to `<name>.mp4` or, if
that is taken, `<name>-2.mp4`, `<name>-3.mp4`. The clip is marked exported
(`exported_at`) only when its file is made.

### `GET /export/jobs`

`{"jobs": [<job>, ...]}`, newest first: those waiting, running, and the last 100
finished. Jobs are kept in memory only; after a restart the list is empty.

### `GET /export/jobs/{export_id}`

`<job>`, or 404.

### `POST /export/jobs/{export_id}/cancel`  (session header)

A waiting job is dropped. A running one has FFmpeg killed and its partial file
removed; the call waits up to 5 s for that and answers the job, normally with
`"status": "cancelled"` (`"cancelling": true` with `"status": "running"` if it
took longer; the `export` event follows). A finished job is answered unchanged.
404 for an unknown id.

### The job object

| Key | Meaning |
|---|---|
| `export_id` | The job's number, from 1 at each engine start. |
| `kind` | `"export"` (later steps add preview jobs). |
| `clip_id` | The clip exported. |
| `status` | `"queued"`, `"running"`, `"done"`, `"failed"`, `"cancelled"`. |
| `cancelling` | A cancel was asked for and FFmpeg is being stopped. |
| `fraction` | 0-1, never goes back. 1 only when done. |
| `pass`, `passes` | Which FFmpeg pass is running, of how many (2 for a CPU bitrate or size target). |
| `encoder` | FFmpeg's encoder for the running pass, such as `libx264` or `h264_nvenc`. |
| `folder`, `name` | Where it goes and its name without `.mp4`. |
| `preset` | The normalised preset. |
| `result` | `null` until done, then the result object below. |
| `error` | Why it failed, in FFmpeg's or the engine's words; `null` otherwise. |
| `created_at`, `started_at`, `finished_at` | Local ISO times, like the rest of the API. |

### The result object (a done job's `result`)

```json
{"bytes": 1506378, "width": 720, "height": 1280, "fps": 30.0, "codec": "h264", "encoder": "libx264",
 "hardware": false, "duration": 3.0, "mode": "target", "video_kbps": 3695, "crf": null, "audio_kbps": 128,
 "upscale": false, "target_bytes": 95000000, "over_target": false, "retried": false, "fell_back_to_cpu": false,
 "path": "C:\\Users\\me\\Downloads\\my-clip.mp4", "file": "my-clip.mp4"}
```

`over_target`: the file is over the size limit even after the retry.
`fell_back_to_cpu`: the graphics card refused the encode and it was made on the CPU.

## Events

On the existing WebSocket, `ws://127.0.0.1:8765/ws`, a new event type:

```json
{"type": "export", "export": <job>}
```

Sent when a job is queued, when it starts, at most every 0.5 s while it runs,
and once when it is done, failed or cancelled. Nothing else uses `"export"`,
and the processing bar (`lib/jobProgress.ts`) ignores it, since it reacts only
to `job` and `progress`. `StudioEvent` in `lib/types.ts` needs `'export'` in
its `type` union and an optional `export` field.

## WoopSocial's smaller copy

### `PATCH /woopsocial/settings`

```json
{"smaller_copy": true}
```

Off by default. The answer is the status object, which now carries
`"smaller_copy": true|false` (also in `GET /woopsocial/status`). With it on, a clip
over 100 MB (WoopSocial's one-piece limit) is sent as a copy under 95,000,000
bytes: H.264 at the clip's own size if that still leaves enough detail, else
720p, at the clip's frame rate. The copy is deleted after the upload. Off,
uploads are exactly as before, and a clip over 100 MB is refused with the same
message. If a copy can't be made, the publish fails with
`"That clip is N MB, over the 100 MB WoopSocial takes, and a smaller copy couldn't be made: <why>"`.
The copy is made in the publish request (or the background publisher), not on
the editor lane, so with the setting on, `POST /woopsocial/clips/{clip_id}/publish`
for a clip over 100 MB answers only once the copy is made and uploaded: minutes
on the CPU for a clip of a few minutes (2-pass x264). Show it as busy, without a
short timeout. In background publishing (watched channels, "publish when done"),
a post stays `sending` while its copy is made and uploaded, however long that
takes: the sweep that marks a send left at `sending` for 20 minutes as stopped
(`reconcile_sending`) leaves a send that is still under way alone.

## TypeScript shapes (for `lib/types.ts`)

```ts
export type ExportResolution = 'same' | '240p' | '360p' | '480p' | '540p' | '720p' | '1080p' | '1440p' | '2160p' | 'custom'
export type ExportAspect = 'same' | '9:16' | '16:9' | '1:1' | '4:5' | 'custom'

export interface ExportPreset {
  kind: 'original' | 'custom'
  resolution: ExportResolution
  width: number
  height: number
  aspect: ExportAspect
  aspect_w: number
  aspect_h: number
  fill: 'crop' | 'blur' | 'bars'
  bar_color: string // #RRGGBB
  quality: 'smallest' | 'balanced' | 'high' | 'custom'
  rate: 'crf' | 'bitrate'
  crf: number
  bitrate_kbps: number
  codec: 'h264' | 'hevc' | 'av1'
  encoder: 'auto' | 'cpu' | 'gpu'
  fps: 'same' | 24 | 25 | 30 | 50 | 60
  audio_kbps: number
  max_mb: number | null
}

export interface ExportEstimate {
  kind: 'range' | 'close' | 'copy'
  bytes: number
  low: number
  high: number
  per_minute: { bytes: number; low: number; high: number }
  seconds: number
  width: number
  height: number
  fps: number
  codec: string
  encoder: string | null
  hardware: boolean
  upscale: boolean
  mode: 'copy' | 'quality' | 'bitrate' | 'target'
  video_kbps: number | null
  audio_kbps: number | null
  crf: number | null
  passes: number
  target_bytes: number | null
  target_reachable: boolean | null
  available: boolean
  clip_id: number | null
}

export interface ExportResult {
  bytes: number
  width: number
  height: number
  fps: number
  codec: string
  encoder: string
  hardware: boolean
  duration: number
  mode: 'quality' | 'bitrate' | 'target'
  video_kbps: number | null
  crf: number | null
  audio_kbps: number
  upscale: boolean
  target_bytes: number | null
  over_target: boolean
  retried: boolean
  fell_back_to_cpu: boolean
  path: string
  file: string
}

export interface ExportJob {
  export_id: number
  kind: 'export'
  clip_id: number | null
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  cancelling: boolean
  fraction: number
  pass: number | null
  passes: number | null
  encoder: string | null
  folder: string
  name: string
  preset: ExportPreset
  result: ExportResult | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}
```
