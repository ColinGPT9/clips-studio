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
`"resolution can't be '999p'. It is one of: same, 720p, ..."`. A body field of the
wrong JSON type is FastAPI's `422`.

## The preset object

Every preset the engine returns has all of these keys. `kind` decides whether
the rest is used: `"original"` is the copy, byte for byte, and ignores the rest.

| Key | Values | Default | Notes |
|---|---|---|---|
| `kind` | `"original"`, `"custom"` | `"custom"` (`"original"` for the saved default until one is saved) | |
| `resolution` | `"same"`, `"720p"`, `"1080p"`, `"1440p"`, `"2160p"`, `"custom"` | `"same"` | Counts the **short** side: 720p is 720x1280 at 9:16, 1280x720 at 16:9, 720x720 at 1:1, 720x900 at 4:5. `"same"` keeps the clip's short side. Sizes start at 720p (the owner's call, 2026-11-06): `"240p"`, `"360p"`, `"480p"` and `"540p"`, offered until then, are refused like any unknown size, and a saved default that names one loads as 720p ([Settings → Export](#settings--export)). |
| `width`, `height` | 720-4096, rounded to even | 1080, 1920 | Used only with `resolution: "custom"`; then `aspect` is ignored. Rounded to even first, so under 719 is refused (400) and 719 becomes 720; over 4096 is clamped. |
| `aspect` | `"same"`, `"9:16"`, `"16:9"`, `"1:1"`, `"4:5"`, `"custom"` | `"same"` | `"same"` is the clip's own shape. |
| `aspect_w`, `aspect_h` | 1-32 | 9, 16 | Used only with `aspect: "custom"`. The long side stops at 4096. |
| `fill` | `"crop"`, `"blur"`, `"bars"` | `"crop"` | How a different shape is filled: cut to fit, the clip blurred behind itself, or colour bars. |
| `bar_color` | `"black"`, `"white"` or `"#RRGGBB"` | `"#000000"` | Always returned as `#RRGGBB`, upper case. Used only with `fill: "bars"`. |
| `quality` | `"smallest"`, `"balanced"`, `"high"`, `"custom"` | `"balanced"` | |
| `rate` | `"crf"`, `"bitrate"` | `"crf"` | Used only with `quality: "custom"`. |
| `crf` | 1-51 | 23 | Custom quality: CRF on the CPU, CQ on NVIDIA, ICQ on Intel's H.264 and HEVC, each on that encoder's own scale. On AMD, Apple and Intel's AV1 the app reads it on x264's scale and turns it into a bitrate from the size estimate. Lower is better and bigger. |
| `bitrate_kbps` | 100-150000 | 6000 | Custom bitrate. 2-pass on the CPU, capped VBR on a graphics card. |
| `codec` | `"h264"`, `"hevc"`, `"av1"` | `"h264"` | Offer only what `GET /export/codecs` lists. AV1 needs a graphics card that makes it. |
| `encoder` | `"auto"`, `"cpu"`, `"gpu"` | `"auto"` | Auto: the encoder forced in settings.yaml first, then the graphics card, then the CPU. GPU with no card for that codec is refused (400). |
| `fps` | `"same"`, 24, 25, 30, 50, 60 | `"same"` | Never above the clip's: 60 on a 30 fps clip stays 30. Numeric strings (`"30"`) are accepted. |
| `audio_kbps` | 64-320 | 128 | AAC at 48 kHz. |
| `max_mb` | `null` or 5-4000 | `null` | "Keep under N MB", decimal megabytes (1 MB = 1,000,000 bytes). Rounded to 0.1. |

Numbers out of range are clamped, not refused (`crf: 99` becomes 51), and sizes
are rounded to even (719 becomes 720). Unknown keys, values outside the lists,
booleans, NaN, non-numeric strings and a `width` or `height` under 719 are
refused (400), for example `"Width can't be 640. A custom size is 720 to 4096
pixels on each side."`.

No side passes 4096 (`MAX_SIDE`). At any size, a custom shape whose long side
would pass 4096 stops there, so its short side comes out under the size's
number: at 2160p from about 1.9:1 (21:9 is 4096x1756), at 1440p from about
2.8:1, at 1080p from about 3.8:1. A custom shape (`aspect_w`:`aspect_h`)
longer than 4096:720 (about 5.7:1) is still accepted, and its short side
comes out under 720 (2160p at 1:32 is 128x4096). The built-in shapes are at
most 16:9, so they never reach the cap.

### What `max_mb` does

What is promised, exactly:

- A clip **made as without the limit first** is never bigger with the limit
  than without it, whatever the estimate said: the export is that file, or
  one made again from it that came out smaller (the smallest file made is
  kept, below).
- A clip **held to the limit from the start** is given a bitrate under the
  low end (`low`) of a measured estimate of the export without the limit. It
  is smaller than that export as long as that export would come to at least
  `low`, as every measured clip of these kinds did. This one rests on the
  estimate.

Which one a clip gets:

- **It may fit anyway** (`fits_anyway: true`): even the low end of the
  estimate of the export *without* the limit (`low`) is under the limit. It is
  made as without the limit first (a 20-second clip "under 100 MB" is not made
  95 MB).
- **Its size can't be told beforehand** (`sure: false`). The estimate was
  measured only for x264 and x265 on the CPU at quality levels (tables below
  and [Where the estimate holds](#where-the-estimate-holds)), so these are
  made as without the limit first too. None is at a bitrate: an export at a
  bitrate is always `sure`.
  - a graphics card's constant-quality encode (NVIDIA CQ for H.264, HEVC and
    AV1; Intel ICQ for H.264 and HEVC): their sizes were never measured;
  - a CPU encode at a quality level (CRF) of:
    - colour bars or a blurred background filling a new shape: a real clip
      came to 0.47x and 0.82x the estimate (bars cost next to nothing; a
      blurred background costs what it covers);
    - a clip whose own bitrate is under busyness's floor (0.3 of the
      baseline's: a still screen, slides, a very still webcam): the estimate
      can't follow a clip that calm and is too high (QC's still picture: 3.7x
      its real file);
    - a clip whose bitrate can't be read.
- **It won't fit, and that is sure** (`sure: true` and `fits_anyway: false`):
  held to the limit from the start. Except where the limit leaves less than
  the floor and a file at the floor would come to `low` or more: that clip is
  made as without the limit first (see **Too small to look good**, below).
  `sure` is true when the export without
  the limit is at a bitrate (a custom bitrate, or any quality on AMD, Apple
  and Intel's AV1, which the app encodes at a bitrate: then its size is that
  bitrate), or is a CPU encode of a measured
  kind: the clip's own bitrate known and over busyness's floor, and the
  picture in its own shape or cropped to a new one (any size, any frame rate).
  Without a clip (the Settings card), the app's typical clip counts as
  measured. The video bitrate is `0.95 x 8 x bytes / seconds - audio`, never
  below the floor (next section). Since `low` is over the limit, that comes
  to less than `low` (for a CRF encode of a clip, under 0.79x the video
  bitrate of the estimate's central figure), so it is also under what the
  clip's own bitrate, scaled to the export's size, frame rate and quality,
  says the export needs: the cap QC asked for is built in, and
  `tests/test_export_presets.py` holds it.

What happens to the file:

- **Made as without the limit:** the same FFmpeg arguments as the export
  without the limit, so the same file. It is checked afterwards, and only one
  that came out over is made again: at the limit's bitrate (2-pass on the
  CPU, one capped VBR pass on a graphics card), which is below the bitrate it
  came out at, never below the floor, then with the 0.9x retry below. **That
  second encode is this rule's cost.** It happens only when the first file
  came out over the limit; the export then takes the first encode plus a
  2-pass encode, and pass 2 once more if that file came out over too. Measured on the CPU (QC's probe, the 150 s clip at High,
  first file 94 KB over): 237.8 s against 104.4 s without a limit. Since the
  QC fix round, NVIDIA's and Intel's constant-quality exports, and CPU
  exports at a quality level with bars, a blurred background or a calm clip,
  take this path too where they used to be held to the limit at once: each
  of them that comes out over takes the extra encode (on a graphics card,
  one more GPU encode; not timed, none works here).
- **Held from the start:** after the encode the file is checked and, if it
  came out over, made once more at 0.9x the bitrate, never below the floor.
- **The smallest file made is kept.** Before a file is made again it is set
  aside in the export's working folder (moved: a rename on one drive, a copy
  across drives). If what was made again is no smaller, the set-aside file
  comes back and is the export (`result.retried` is then false for a file
  made as without the limit). An encoder can overshoot its bitrate: QC saw
  x264 2-pass make 1260 kbps when asked for 1088 on a calm clip. The working
  folder is removed after every job, finished, failed or cancelled.
- **A graphics card that fails while making a file again** (out of encode
  sessions, say) hands it to the CPU, as a failed first try does: the same
  bitrate, 2-pass, and `result.fell_back_to_cpu: true`. H.264 and HEVC only;
  AV1 has no CPU encoder, so there the export fails with FFmpeg's words, as a
  failed first try does. If the CPU's file is no smaller than the card's, the
  card's comes back (above) and `fell_back_to_cpu` is false. A cancel is never
  handed to the CPU.
- **Too small to look good.** When the limit's bitrate would be under the
  floor, the file goes no lower than the floor: held at the floor from the
  start, or, made as without the limit, made again at the floor only if it
  came out at a higher bitrate than that (one already under it is kept as it
  is). (A sure clip for which a file at the floor would come to `low` or more
  could be bigger than the export without the limit, so it is made as without
  the limit too.) If it finishes over the limit, the job has
  `result.over_target: true` and `result.over_reason: "too_small"`. A miss
  over a limit that leaves enough, over even after it was made again, is
  `over_reason: "missed"`.
- A **custom bitrate** someone typed is kept as typed: the floor is only for a
  bitrate the app picks for a limit, and a limit only ever lowers a typed
  bitrate.

### Where the estimate holds

A 20 s excerpt of a real 1080x1920 60 fps clip (the 150 s clip from the
black-box check), made at High on the CPU (x264 CRF 18 medium) with no limit,
against the estimate (measured 2026-11-06):

| Export | Real file / estimate's central figure | Under the estimate's low end? |
|---|---|---|
| Its own size and shape | 0.96 | no |
| 1:1, cropped (1080x1080) | 1.00 | no |
| 1440p (enlarged) | 1.09 | no |
| 2160p (enlarged) | 1.02 | no |
| 30 fps | 1.31 | no (over the top: frame-rate changes are under-estimated) |
| 24 fps | 1.47 | no (over the top) |
| 1:1, blurred background | 0.93 | no |
| 16:9, blurred background | 0.82 | no, just (the low end is 0.80) |
| 16:9, colour bars | 0.47 | **yes** |
| 16:9, colour bars, Smallest at 720p | 0.53 | **yes** |

Bars and a blurred background are made as without the limit first; the rest
is measured. An estimate under the real file is the safe side here: such a
clip is more often made as without the limit first and checked.

### The watchable floor

`floor_kbps = K x (width x height / (1080 x 1920))^a x (fps / 60)^0.56`, rounded,
for the export's own picture and frame rate:

| Codec | K (1080x1920, 60 fps) | a | 720x1280, 30 fps | 2160x3840, 60 fps |
|---|---|---|---|---|
| H.264 | 700 kbps | 0.66 | 278 kbps | 1748 kbps |
| HEVC, AV1 | 420 kbps | 0.60 | 175 kbps | 965 kbps |

Measured 2026-11-06 on a 42 s excerpt of a real 1080x1920 60 fps clip (a stream:
talking and walking). Each rung was encoded the way a size target is (x264
medium or x265 fast, 2-pass, the export's own scaling) and compared with the
clip scaled the same way: SSIM, PSNR and VMAF per frame, signalstats for colour
and flat frames, and frames looked at side by side. 700 kbps of H.264 at
1080x1920 60 fps is the lowest rung that is rough but watchable. The other sizes
and rates were matched to its look (the bitrate with the same VMAF, 39.4);
x265 looks better than that at the lowest bitrate it can still keep to, so
HEVC's floor is that bitrate. AV1 takes HEVC's (no AV1 encoder here); the
graphics cards' encoders take their codec's (not measured: none worked here).

| Picture, fps | Codec | Floor | Measured | Nearest rung: VMAF / PSNR-Y / colour kept | Below the floor |
|---|---|---|---|---|---|
| 1080x1920, 60 | H.264 | 700 | 700 (by eye) | 700: 39.4 / 28.3 dB / 0.94 | 300: 997 of 2538 frames (39%) had no colour, 250 one flat grey; 400: VMAF 11 |
| 1080x1920, 30 | H.264 | 475 | 457 | 500: 43.1 / 28.2 dB / 0.89 | 350: VMAF 27 |
| 720x1280, 60 | H.264 | 410 | 386 | 400: 41.2 / 28.5 dB / 0.93 | 300: VMAF 26 |
| 720x1280, 30 | H.264 | 278 | 269 | 280: 41.4 / 27.9 dB / 0.91 | 120: 79 frames had no colour |
| 480x854, 30 (\*) | H.264 | 163 | 159 | 160: 39.9 / 27.4 dB / 0.90 | 100: VMAF 12 |
| 360x640, 30 (\*) | H.264 | 111 | 108 | 120: 45.2 / 27.8 dB / 0.90 | 60: VMAF 8 |
| 240x426, 30 (\*) | H.264 | 65 | 63 | 70: 45.1 / 27.0 dB / 0.91 | 40: VMAF 12 |
| 1080x1920, 60 | HEVC | 420 | 400 (lowest it kept to) | 400: 53.4 / 32.5 dB / 0.64 | asked for 200-300, x265 made about 470 kbps, VMAF 41 |
| 720x1280, 30 | HEVC | 175 | 160 (lowest it kept to) | 160: 50.0 / 30.5 dB / 0.63 | asked for 100-130, it made about 188, VMAF 35 |
| 360x640, 30 (\*) | HEVC | 76 | 67 | 80: 49.1 / 28.8 dB / 0.68 | asked for 45-55, it made about 85, VMAF 19 |
| 240x426, 30 (\*) | HEVC | 47 | 47 | 45: 37.7 / 26.3 dB / 0.51 | asked for 25-32, it made about 56, VMAF 11 |

- (\*) Sizes the app no longer offers (sizes start at 720p since 2026-11-06).
  The rows stay as measurements: they set the floor's exponents. A custom
  shape longer than about 5.7:1 can still have a short side this small
  (128x4096), but no export has fewer pixels than 720x720, more than any row
  marked (\*).
- Floor and Measured are video kbps. The floors are 0-6% above the H.264
  measurements and 0-13% above the HEVC ones, never below.
- "Colour kept" is the lowest, over every frame where the clip has clear colour
  (signalstats `SATAVG` 5 or more), of the export's average saturation over the
  clip's. A frame "had no colour" when its average colour is grey (`UAVG` and
  `VAVG` within 0.05 of 128) where the clip's frame isn't (0.5 or more away); at
  300 kbps, 732 of those 997 frames had no colour in any pixel (`UMIN` = `UMAX` =
  `VMIN` = `VMAX` = 128). A frame is flat when `YMAX` - `YMIN` is 1 or less where
  the clip's is 16 or more. No rung at or above a floor had a flat frame or a
  frame without colour, by either colour rule.
- The black-box check's "Keep under 5 MB" on the 150 s clip asked for 111 kbps
  at 1080x1920 60 fps. It is now made at 700 kbps (about 15.7 MB) and marked
  `"too_small"`. The estimate says so beforehand (`target_reachable: false`,
  `longest_watchable_s: 45.9`).
- WoopSocial's smaller copy keeps its own rules (below) and is not held to this
  floor.

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

The saved default is read with `export_presets.from_saved` (in `GET /settings`,
`GET /export/presets` and `"default"` in any request). One saved before sizes
started at 720p still loads, at 720p, so it never breaks the card or an export:
`"240p"`, `"360p"`, `"480p"` or `"540p"` loads as `"720p"`, and a custom width x
height with a side under 720 is enlarged, keeping its shape, until that side is
720 (480x854 loads as 720x1280); where its long side would then pass 4096, it
loads at the default 1080x1920 and, if the custom size was in use, as `"720p"`.
Anything else stored that can't be read loads as Original, as before.

### `GET /export/presets`  (no probing, instant)

```json
{
  "default":  {"kind": "original", "...": "the saved default"},
  "original": {"kind": "original", "...": "..."},
  "custom":   {"kind": "custom", "resolution": "same", "quality": "balanced", "...": "the starting values"},
  "choices": {
    "kind": ["original", "custom"],
    "resolution": ["same", "720p", "1080p", "1440p", "2160p", "custom"],
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
  "limits": {"width": [720, 4096], "height": [720, 4096], "aspect_w": [1, 32], "aspect_h": [1, 32],
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
  "fits_anyway": null, "sure": null, "floor_kbps": null, "longest_watchable_s": null,
  "available": true, "clip_id": null
}
```

With a size limit, for the Settings card's minute (`{"preset": {"kind": "custom", "max_mb": 5}}`):

```json
{
  "kind": "close",
  "bytes": 6272100, "low": 5833053, "high": 6585705,
  "per_minute": {"bytes": 6272100, "low": 5833053, "high": 6585705},
  "seconds": 60.0, "width": 1080, "height": 1920, "fps": 60.0,
  "codec": "h264", "encoder": "libx264", "hardware": false, "upscale": false,
  "mode": "target", "video_kbps": 700, "audio_kbps": 128, "crf": null, "passes": 2,
  "target_bytes": 5000000, "target_reachable": false,
  "fits_anyway": false, "sure": true, "floor_kbps": 700, "longest_watchable_s": 45.9,
  "available": true, "clip_id": null
}
```

- `kind`: `"range"` for a CRF, CQ or ICQ encode, which is the quality levels and
  a custom CRF except on AMD, Apple and Intel's AV1 (the size depends on the
  footage: show `low`-`high`); `"close"` for an encode at a bitrate (a custom
  bitrate, or any quality on AMD, Apple and Intel's AV1) or a size target the clip
  is held to (show `bytes`); `"copy"` for Original with a clip (its exact size).
  On AMD, Apple and Intel's AV1 the quality's bitrate is worked out from the
  clip's own bitrate with `clip_id`, and for the typical clip without one, so a
  busier clip's export comes out bigger than that figure.
  Original without a clip is a `"range"` around the app's usual 48-54 MB a minute.
- `upscale`: true when the clip's picture is enlarged by more than 1%
  (`is_upscale`), as at 1440p and 2160p in the clip's own shape, or in a crop
  that zooms in (a tall clip cropped to 16:9). Bars or a blurred background can
  leave it unenlarged (a tall clip at 1440p 16:9 on bars is 0.75x). Without
  `clip_id`, it is for a 1080x1920 clip. For the short warning marker; the
  detail goes in its tooltip.
- `available: false`: this PC can't encode the preset's codec (AV1 without a card).
- `mode`: `"copy"`, `"quality"`, `"bitrate"` or `"target"` (any size limit).
- Errors: 404 for an unknown `clip_id`, 400 for a clip with no file or a bad preset.

With a size limit (`max_mb`), the figures follow [What `max_mb` does](#what-max_mb-does),
so no figure (`low`, `bytes`, `high`, `per_minute`) is ever bigger with a limit
than without one. All five fields below are `null` without a limit.

- A clip **made as without the limit first** (`fits_anyway: true`,
  `sure: false`, or the exception under **It won't fit, and that is sure**): `kind`, `crf` and `passes` are the no-limit ones, and so are
  the figures, except that `high` is no higher than what a file that came out
  over is made again at (the limit, or the size at the floor where the limit
  leaves less than the floor), and where the no-limit `bytes` is over the
  limit, `bytes` is the size of the file made again, since a clip like that
  usually comes out over. (For `sure: false` the no-limit figures themselves
  are the unmeasured estimate: with colour bars, for example, the real file
  can be well under `low`.)
- A clip **held to the limit** (`fits_anyway: false` and `sure: true`, but not
  that exception): the limit decides the bitrate (`kind: "close"`, `video_kbps`, 2 passes on the
  CPU), or it is too small (`target_reachable`).
- `fits_anyway`: `true` when the clip may fit under the limit (even the
  no-limit `low` is under it).
- `sure`: `true` when the size without the limit is known well enough
  beforehand to hold a clip that won't fit to the limit from the start (a
  bitrate, or a measured kind of CPU encode); `false` for a graphics card's
  constant-quality encode, or a CPU encode at a quality level or custom CRF
  with colour bars or a blurred background, of a calm clip (its own bitrate
  under busyness's floor) or of one whose bitrate can't be read.
  Without a clip, the app's typical clip counts as measured.
- `target_reachable`: `true` when the limit is expected to be kept with a
  watchable picture: it leaves at least the floor, or the clip is expected to fit
  anyway (its no-limit `bytes` is under the limit). `false` when the limit is too
  small for this clip to look good: it will go no lower than the floor, and
  `bytes` is the size at the floor, over the limit (or, made as without the limit
  first, the no-limit figures, no higher than the size at the floor). With a
  clip (`clip_id`, or `seconds`), this is the warning to show before exporting.
- `floor_kbps`: the lowest video bitrate this limit is met with at this size,
  frame rate and codec ([the floor](#the-watchable-floor)).
- `longest_watchable_s`: the longest clip, in seconds, that this limit holds at
  the floor at this size, shape, frame rate, codec and sound:
  `0.95 x target_bytes / (125 x (floor_kbps + audio_kbps))`. For the Settings
  card (no clip) it says how long a clip can be before it won't look good under
  this limit. For example 45.9 for 5 MB at 1080x1920 60 fps H.264 with 128 kbps
  of sound. With a clip, `target_reachable` can only be `false` for a clip longer
  than this (a calm clip expected to fit anyway, its no-limit `bytes` under the
  limit, is fine at any length).

Checked on 6 real finished clips (CPU, 30 and 60 fps): the H.264 files came out
at 0.85-1.10x `bytes`, inside `low`-`high`. HEVC on the CPU (x265 fast) was
checked on 4 real finished clips (1080x1920 at 30 and 60 fps, 28-151 s) at
Smallest, Balanced and High, and at 720p on the longest, 15 files:

| Clip | Size | Smallest | Balanced | High |
|---|---|---|---|---|
| 1080x1920 60 fps, 30 s, calm | own | 0.88 | 0.90 | 0.90 |
| 1080x1920 30 fps, 28 s | own | 0.96 | 0.97 | 0.96 |
| 1080x1920 60 fps, 43 s, busy | own | 0.93 | 0.96 | 0.97 |
| 1080x1920 60 fps, 151 s | own | 1.06 | 1.09 | 1.09 |
| 1080x1920 60 fps, 151 s | 720p | 1.02 | 1.06 | 1.05 |

(estimate `bytes` over the real file's size; every file inside `low`-`high`.
Before this calibration the estimates were 1.20-1.52x.) The graphics cards' HEVC
estimates are not measured.

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
            "smaller": false, "attempt_fraction": null,
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
| `pass`, `passes` | Which FFmpeg pass is running, of how many (2 for a bitrate on the CPU, which includes a size limit the clip is held to). |
| `encoder` | FFmpeg's encoder for the running pass, such as `libx264` or `h264_nvenc`. |
| `smaller` | `true` from the moment the file is being made again, smaller, because it came out over the size limit (see [What `max_mb` does](#what-max_mb-does)). Once done it is `result.retried`: `false` again when the file kept is the first one, because what was made again was no smaller. `false` before and without that. The first try has used up the bar by then: `fraction` stays near its end, so show this attempt's own progress. |
| `attempt_fraction` | 0-1: how far along the current try is, from 0 again when a file is made again smaller (each pass counts its share). `null` until the export starts. |
| `folder`, `name` | Where it goes and its name without `.mp4`. |
| `preset` | The normalised preset. |
| `result` | `null` until done, then the result object below. |
| `error` | Why it failed, in FFmpeg's or the engine's words; `null` otherwise. |
| `created_at`, `started_at`, `finished_at` | Local ISO times, like the rest of the API. |

### The result object (a done job's `result`)

```json
{"bytes": 1506378, "width": 720, "height": 1280, "fps": 30.0, "codec": "h264", "encoder": "libx264",
 "hardware": false, "duration": 3.0, "mode": "target", "video_kbps": null, "crf": 23, "audio_kbps": 128,
 "upscale": false, "target_bytes": 95000000, "over_target": false, "over_reason": null, "floor_kbps": 278,
 "retried": false, "fell_back_to_cpu": false,
 "path": "C:\\Users\\me\\Downloads\\my-clip.mp4", "file": "my-clip.mp4"}
```

(A 3-second clip under 95 MB fits anyway, so it was made at CRF 23, just as
without the limit. A file made at a bitrate shows `video_kbps` and `crf: null`.)

- `over_target`: the file is over the size limit, after it was made again where
  it could be.
- `over_reason`: why it is over, or `null` when it isn't.
  - `"too_small"`: the limit is too small for this clip to look good, and the
    file went no lower than the floor: made at the floor (`video_kbps` =
    `floor_kbps`), or the file made as without the limit, kept because it was
    already at or under the floor's bitrate (or no bigger than the one made at
    the floor).
  - `"missed"`: an ordinary overshoot. The limit leaves enough, but the file came
    out over even after it was made again.
- `floor_kbps`: the floor this limit was held to, or `null` without a limit.
- `retried`: the file is one made again, smaller, because the first came out over
  the limit. `false` when the first file was kept because what was made again
  was no smaller.
- `fell_back_to_cpu`: the file is the CPU's, because the graphics card refused
  its encode (the first try, or making it again to fit).

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
over 100 MB (WoopSocial's one-piece limit) is sent as a copy aimed under 95,000,000
bytes: H.264 at the clip's own size if that still leaves enough detail, else
720p, at the clip's frame rate. With 128 kbps of sound, the copy's video is at
its 100 kbps minimum from about 53 minutes, so from about 55 minutes the copy is
over 95,000,000 bytes, and from about 61 minutes it is over 100 MiB and refused
like any clip over it. The copy is deleted after the upload. Off,
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

The copy keeps the rules it was built with, unchanged by the size-limit work
above: it is always held to its target's bitrate (the lower of that and what
High needs, never below 100 kbps), with the one 0.9x retry, whose file is kept
whatever it comes to, and it is not held to the watchable floor. Its 720p is
unchanged too. So a long clip's copy can go below the floor: at
1080x1920 60 fps, a 30-minute clip's copy is 720p at 273 kbps, under the
410 kbps floor for 720p 60 fps.

## TypeScript shapes (for `lib/types.ts`)

```ts
export type ExportResolution = 'same' | '720p' | '1080p' | '1440p' | '2160p' | 'custom'
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
  fits_anyway: boolean | null
  sure: boolean | null
  floor_kbps: number | null
  longest_watchable_s: number | null
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
  over_reason: 'too_small' | 'missed' | null
  floor_kbps: number | null
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
  smaller: boolean
  attempt_fraction: number | null
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
