# Step 6: media serving

Engine only. Nothing a person can see changes in this step, and nothing in it runs unless one of the media routes below is called. The one exception is the pinning rule, which only ever keeps a file the app would otherwise have deleted.

- The new editor's player and timeline (step 8) play, scrub and draw from these routes.
- The export compiler (step 7) finds media through the same resolver.
- **Media ids:** a media id names something the app already knows (a downloaded video, a clip, an imported file, a free sound, a project's media), never a path.
- **On disk:** what is made from a source (a proxy, the waveform, the thumbnails) is kept in `data/media_cache/`, and a project keeps the videos it uses from being cleaned up.

Contents: [Files](#files) · [Media ids](#media-ids) · [Routes](#routes) · [Protection](#protection) · [The proxy rule](#the-proxy-rule) · [Peaks format](#peaks-format) · [Thumbnails format](#thumbnails-format) · [The cache](#the-cache) · [Projects pin their sources](#projects-pin-their-sources) · [The editor lane](#the-editor-lane) · [What changed in existing code](#what-changed-in-existing-code) · [Measured](#measured) · [Choices made](#choices-made) · [Left for steps 7 and 8](#left-for-steps-7-and-8) · [Tests](#tests)

## Files

| File | What it is |
|---|---|
| `video_editor/media/resolver.py` | The one place a media id becomes a file |
| `video_editor/media/fileio.py` | Opening a file so Windows can still rename or delete it; Range, ETag, If-Range, If-None-Match |
| `video_editor/media/probe.py` | What a file is (ffprobe), and whether Chromium plays it |
| `video_editor/media/proxy.py` | The proxy's FFmpeg command |
| `video_editor/media/peaks.py` | The waveform pyramid: FFmpeg to PCM, min/max bins, the binary format |
| `video_editor/media/thumbs.py` | Thumbnail sheets from keyframes, and their index |
| `video_editor/media/cache.py` | `data/media_cache/`: fingerprints, writing under temporary names, holds, the cap, orphans, Clear |
| `video_editor/media/importing.py` | Import for editing: the path checks, then the copy through step 5's store |
| `server/media_api.py` | The routes, installed by `server/api.py` |
| `server/editor_lane.py` | Tasks beside the exports (`submit_task`, `TaskContext`) |
| `core/state.py` | `project_media`, `pinned_video_ids` (plain SQL over `projects.doc`) |
| `core/housekeeping.py`, `server/automation.py`, `core/remake.py` | The pinning rule, and the cache's place in Storage |

The package is standard library only; `peaks.py` uses numpy when it is there. It reads the rest of the app only inside the function that needs it: `core.binaries`, `core.paths`, `video.audio_library`, `video.export_presets.low_priority`, and `video_editor.project.store` for an import. `core/pipeline.py` never imports it, directly or through anything it loads (`tests/test_editor_import_guard.py`). Only `server/media_api.py`, `core/housekeeping.py` and `server/api.py` import it, the last two inside a function.

## Media ids

| Id | Names | The file |
|---|---|---|
| `video:<video id>` | a video the app downloaded or was given | `data/downloads/<video id>.<ext>`, any of the containers a download can have (`core/paths.py` `cached_source`), or, when there is none, its Vertical Live download `<video id>__vertical.<ext>` (`sources/vertical.py`). Found even when the video left the library, since a project can pin it |
| `clip:<clip id>` | a clip's rendered file | the clip's row (`clips.path`), only inside the data folder |
| `asset:<sha256>` | a file imported for editing | `data/media/assets/<first 2>/<sha256><ext>` (`media_assets`, step 5) |
| `library:<id>` | a free track or sound effect | `assets/library/`, only while its sha256 matches the manifest |
| `project:<project id>:<media id>` | an entry of a project's `media` | whatever its `source` names, as above. A `{"type": "file", "path"}` source (the music file of an imported clip) only when the file is inside the data folder and is a sound or a video by its extension (`.mp3 .m4a .aac .wav .ogg .opus .flac .mp4 .m4v .mov .mkv .webm`, the name given and the file it leads to), so a project document can't reach `state.db` or a saved sign-in |

- Whatever the id says, the file found must sit inside the data folder (the library folder for a free sound) once every link is resolved.
- Anything else is 404, the same answer as a missing file.
- An id that has no shape of one is 404 too, with the list of shapes.
- A missing file is named in the answer, for example: *The download of "Friday stream" is missing (it was deleted, or freed after publishing). Download the video again to edit it.*

## Routes

All in `server/media_api.py`, all internal (`server/api_stability.py` lists none of them). `HEAD` is a route of its own beside each `GET` that has one, left out of the OpenAPI schema (one operation id per route). In the URL the id is one path segment: `/media/video:abc123/info`. Installed before `GET /media/{clip_id}`, which is unchanged.

| Route | Who | Answer |
|---|---|---|
| `GET`/`HEAD /media/source/{id}` | read | The file itself, as the request's Range, If-Range and If-None-Match ask. Below |
| `GET /media/{id}/info` | read | What it is and where its products are. Below |
| `GET`/`HEAD /media/{id}/proxy` | read | Ready: the proxy file, served like the source. Being made: 202 `{state, job_id, fraction}`. Never asked for: 404 `{state: "none", message}`. Failed: 500 `{state: "failed", error}`. `?state_only=true` always answers the state as JSON, 200 |
| `POST /media/{id}/proxy` | change | Asks for the proxy (once; asking again returns the same job). 202 with the state, or 200 `{state: "ready"}`. 400 for a picture |
| `POST /media/{id}/proxy/cancel` | change | Drops a waiting proxy, or stops the running one's FFmpeg and removes its half-made file. Answers the state |
| `GET /media/{id}/peaks` | read | `per_second` (1 to 512, default 64), `start_us`, `end_us`, `format` (`bin` or `json`), `wait` (seconds, at most 60), `retry`. The [peaks](#peaks-format), made the first time they are asked for. Until then 202 with the state. 404 for media with no sound, 400 for a bad parameter |
| `GET /media/{id}/thumbs` | read | `wait`, `retry`. The [thumbnail index](#thumbnails-format), each sheet with its `url`, made the first time it is asked for. 404 for a sound |
| `GET`/`HEAD /media/{id}/thumbs/{n}` | read | Sheet `n` (JPEG), served like the source. 404 before the index exists |
| `POST /media/import` | change | `{path, name}`: [import for editing](#import-for-editing). 200 with the asset, or 202 `{job}` for a file over 64 MB |
| `GET /media/import/{job_id}` | header | A large import's job: `status`, `fraction`, and the asset in `result` when done |
| `GET /media/cache/size` | header | `{folders, files, bytes, cap_bytes}` for the whole cache |
| `POST /media/cache/clear` | change | The "Clear editor cache" button: `{files_removed, bytes_freed, kept_in_use}`. Folders a job or a stream is using stay |

"read", "change" and "header" are explained under [Protection](#protection).

**A failed product.** When making the peaks or the thumbnails fails, the route answers 500 with `error`, and it doesn't try again by itself (a broken file would otherwise start FFmpeg on every request). `?retry=true` tries again. `POST /media/{id}/proxy` always tries again.

### The source route

| Request | Answer |
|---|---|
| no Range | 200, the whole file |
| `Range: bytes=a-b` | 206, `Content-Range: bytes a-b/size`; an end past the end is the last byte |
| `Range: bytes=a-` | 206, from a to the end |
| `Range: bytes=-n` | 206, the last n bytes (all of them when n is more than the size) |
| a first byte at or past the end, or `bytes=-0` | 416, `Content-Range: bytes */size` |
| several ranges, another unit, or a header that doesn't parse | 200, the whole file (RFC 9110 lets a server ignore Range) |
| `If-None-Match` that matches (weak comparison, or `*`) | 304, no body |
| `If-Range` with the current ETag (strong comparison) or the exact `Last-Modified` | the range |
| `If-Range` with anything else | 200, the whole file |
| `HEAD` | the same headers, no body |

- Every answer carries `Accept-Ranges: bytes`, a strong `ETag` (`"<size hex>-<mtime ns hex>"`), `Last-Modified`, `Cache-Control: no-cache` and the type from the extension (`video/mp4`, `video/webm`, `video/x-matroska`, `audio/mpeg`, `image/jpeg`...).
- The size, time and bytes all come from the one open handle, so they agree even when the name is replaced meanwhile.
- **Windows:** the file is opened with `CreateFileW(GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)` through ctypes, then `msvcrt.open_osfhandle`. While it streams, the file can be renamed or deleted, and a new file can then be written under its name; the stream goes on sending the bytes it opened. Python's own `open()` leaves out `FILE_SHARE_DELETE`, which is what locked such files before.
- **One Windows limit stays:** `os.replace` of another file onto the very name being streamed is still refused (`MoveFileExW` can't replace a name that has an open handle). Moving or deleting the old file first works. `tests/test_media_fileio.py` checks both.
- **In-place writes.** The handle shares write access, so a program that overwrites the file in place while it streams has its new bytes sent under the old `ETag` and `Content-Range` (the same on Linux). No app path writes a download, clip or asset in place; dropping `FILE_SHARE_WRITE` would block such a writer instead, and was not chosen.
- **A file that gets shorter while it streams:** the answer can't reach the `Content-Length` it announced, so it ends with an error (`SourceShrank`) and the server drops the connection; the `<video>` element asks again. It never ends the body short as if it were whole.
- **`Range` digits are ASCII only.** Header bytes are read as Latin-1, and `str.isdigit()` takes superscripts; such a header is ignored (200, the whole file), never a 500.
- Elsewhere the file is a plain `os.open` (with `O_NONBLOCK`, so a pipe never hangs it).
- **Releasing the file.** The answer is sent in 256 KiB chunks. A watcher waits for the client's `http.disconnect`, and the file is closed the moment the answer ends or the client goes away, without reading the rest. `tests/test_media_routes.py` drives the ASGI answer by hand to check this.

### The info route

```json
{
  "id": "video:abc123", "ref": "video:abc123", "name": "Friday stream",
  "kind": "video", "container": "mov,mp4,m4a,3gp,3g2,mj2", "size": 320071641,
  "duration_us": 7200000000, "start_us": 0,
  "width": 1280, "height": 720, "display_width": 1280, "display_height": 720, "rotation": 0,
  "fps": {"num": 30, "den": 1}, "fps_value": 30.0,
  "video_codec": "h264", "pix_fmt": "yuv420p", "audio_codec": "aac", "audio_channels": 2, "sample_rate": 48000,
  "has_video": true, "has_audio": true,
  "fingerprint": "5c501f46…",
  "needs_proxy": false, "proxy_reasons": [],
  "proxy": {"state": "none"}, "peaks": {"state": "ready"}, "thumbs": {"state": "running", "job_id": 4, "fraction": 0.42},
  "urls": {"source": "/media/source/video:abc123?t=…", "proxy": "/media/video:abc123/proxy?t=…",
           "peaks": "/media/video:abc123/peaks?t=…", "thumbs": "/media/video:abc123/thumbs?t=…"},
  "token": "…"
}
```

- **Fields:**
  - `kind` is video, audio or image.
  - `rotation` is the degrees clockwise the picture is turned to be shown upright.
  - `display_width` and `display_height` are the size with that turn applied.
  - `start_us` is the source's first timestamp.
  - A state is `ready`, `queued`, `running`, `failed` (with `error`), `cancelled` or `none`.
- **Read once:** what ffprobe says is kept in the cache (`info.json`), so the first call for a 2-hour file took 71 ms and later calls 11 ms. A changed file has a new fingerprint and is read again.
- 422 when the file can't be read as media.

### Import for editing

`POST /media/import {"path": "D:/Videos/holiday.mp4", "name": "Holiday"}`: the one media route that takes a path. It copies a file the person picked on this PC into the project media. No AI run starts.

**What is refused, with a plain sentence:**

| Path | Answer |
|---|---|
| empty, or holding a NUL, or longer than 4096 characters | 400 |
| not absolute (on Windows, no drive) | 400, "Give the whole path of the file, starting with its drive." |
| a network path `\\server\share\…` or `//server/…` (device paths `\\.\`, `\\?\` too) | 400, "That file is on a network path. Copy it to this PC first, then import it." |
| a device name (`CON`, `NUL`, …) | 400 |
| a drive that doesn't answer within 5 s (a network drive that is offline) | 400, "The drive that file is on didn't answer…" |
| no such file | 404 |
| a folder | 400, "That is a folder. Pick a file inside it." |
| a device or a pipe | 400 |
| an empty file | 400 |
| a file FFprobe can't read, or with no length | 400, "This isn't a video, a sound or a picture Clips Kitty can read." |
| a list of other files: an HLS or DASH playlist, a concat list, an SDP (FFprobe formats `hls`, `concat`, `dash`, `sdp`, `rtp`, `rtsp`, `tee`, `ffmetadata`), whatever its name | 400, "This file is a list of other files (a playlist), not a video, a sound or a picture of its own." |
| a picture Chromium can't show (TIFF…) | 400, "Save it as PNG or JPEG" |

**What it does:**
- FFprobe and every FFmpeg of this step read local files only (`-protocol_whitelist file`, `probe.LOCAL_ONLY`), and a playlist is refused by any route that reads media (`probe.describe`), not only by import.
- The file is only ever opened for reading. It is hashed and copied in one pass of 1 MB blocks, never held in memory whole.
- It is copied to `data/media/assets/.incoming-<hex>.part`, then renamed to its sha256 name. When the same file is already there, the copy is dropped.
- A file over 64 MB is copied on the editor lane, with progress (`media_job` events, `GET /media/import/{job_id}`).
- The answer is the asset: `{id: "asset:<sha256>", sha256, kind, name, ext, size, duration_us, width, height, fps, has_audio}`. Width and height are as shown (rotation applied).
- The same file imported twice is one asset.

## Protection

**Routes that change something** (`POST` proxy, cancel, import, clear) are checked exactly as step 5's project routes are, through the same helper (`projects_api.session_guard`):
- they need the `X-Clips-Kitty-Session` header;
- they need an Origin of this PC's own (`projects_api.local_origin`): none, `file://`, `null`, the dev server, or the engine's own address. Every other `http(s)` origin gets 403.
- A token in the address never stands in for the header.

**Routes that read** are fetched by `<video>` and `<img>` elements, which can't send a header. They take either:
- the session header, as a `fetch()` from the app sends it; or
- the media's token in the address, `?t=<32 hex>`: an HMAC-SHA256 of the media id under the session secret, cut to 128 bits. `GET /media/{id}/info` gives it in its `urls`, and the thumbnail index puts it in each sheet's `url`.

They also refuse any request whose Origin header is not this PC's own (the same rule).

**What a page on another origin can and can't reach:**
- It can't send the header: the CORS allow-list refuses the preflight a custom header needs.
- It can't make a token: the secret is new at every start of the app (`plugins/session.py`) and never leaves this PC.
- A `fetch()` from it carries its Origin and is refused. An `<img>` or `<video>` on it carries no Origin and, without a token, gets 403 like everything else.
- So such a page can't play, show or measure any media, and can't start any work.
- **Its one remaining possibility:** if a token leaked (an address copied out of the app's developer tools into a page), that page could play that one media until the app restarts.
- Unchanged from before this step, and true of every other route: software already running as the person can read the secret file.

Nothing existing is weakened: `GET /media/{clip_id}` and `GET /media/preview/{clip_id}` are untouched, and every new route is guarded.

## The proxy rule

`needs_proxy` is true when any of these hold (`proxy_reasons` says which, in words):

1. **A codec or container Chromium doesn't play.** The editor runs in Electron 31 (Chromium 126), which Electron builds with H.264, AAC and MP3. The list is from Chromium's format support (`media/base/supported_types.cc`, `mime_util_internal.cc`), read, not measured in the app:

   | Container (ffprobe's format) | Video | Sound |
   |---|---|---|
   | MP4, MOV, M4A | H.264 (8-bit 4:2:0 only), VP9, AV1 | AAC, MP3, Opus, FLAC |
   | WebM, Matroska | VP8, VP9, AV1; H.264 in Matroska since step 8 | Opus, Vorbis; AAC in Matroska since step 8 |
   | MP3, ADTS AAC, Ogg, WAV, FLAC (sound only) | none | MP3; AAC; Opus, Vorbis, FLAC; PCM; FLAC |

   - Anything else gets a proxy: HEVC, ProRes, MPEG-4 Part 2, AVI, AC-3, 10-bit or 4:2:2 H.264.
   - **Matroska with H.264 or AAC** (a common OBS recording) got one too, as a guess on the safe side. **Step 8 tried it:** Electron 31.7.7 (Chrome 126.0.6478.234) plays and seeks H.264 + AAC in Matroska as it does the same streams in MP4, so that rule is gone ([step8-editor.md](step8-editor.md#part-c-what-changed-in-the-engine)). 10-bit H.264 and other codecs in Matroska still get a proxy.
2. **Bigger than 1080p60:** the shorter side over 1080 or the longer over 1920 (after rotation), or more than 60 frames a second.
3. **Asked for:** once a proxy exists or is on its way, `proxy_reasons` says "a proxy was asked for".

The plan's "on a weak PC" for rule 2 is not measured; step 8 can choose to play a 1440p source itself on a strong PC.

**The proxy** (`video_editor/media/proxy.py`):
- **Picture:** the shorter side 540 (never larger than the source), H.264 High, `yuv420p`, libx264 veryfast CRF 26. A keyframe every half second (`-force_key_frames expr:gte(t,n_forced*0.5)` plus `-g` of half a second, scene cuts off). At most 60 frames a second.
- **Sound:** AAC 128k stereo.
- **File:** MP4 with `+faststart` (the app's rule for files only it reads, `video/encoding.py`). FFmpeg turns the picture upright, so the proxy carries no rotation.
- **A sound with no picture:** an AAC `.m4a`.
- **Where:** one FFmpeg on the editor lane, below normal priority, cancellable, on the CPU.

## Peaks format

FFmpeg decodes the first sound track to mono 16-bit PCM at 8192 samples a second, read from a pipe in 1 MiB chunks:
- Every 16 samples become one bin: the lowest and the highest sample, shifted right by 8 to a signed byte (−128 to 127).
- That is 512 bins a second. Each coarser level is the min of two mins and the max of two maxes: 512, 256, 128, …, 2, 1 bins a second (10 levels).
- A level's last bin may cover less than its share.
- The peaks are of the sound at 8192 a second, so a click shorter than about 0.1 ms can read a little lower than in the original.
- **numpy:** it does the same sums when it is there. The standard-library path gives the same bytes (tested).

`GET /media/{id}/peaks?per_second=N` sends the level at N bins a second (rounded down to a power of two) and every coarser one. `start_us`/`end_us` cut every level to that stretch. So the UI fetches the whole video at 64 a second once, and a zoomed-in window at up to 512.

**Binary (default, `application/octet-stream`, little-endian):**

| Offset | Size | What |
|---|---|---|
| 0 | 4 | `CKPK` |
| 4 | 2 | version, 1 |
| 6 | 2 | number of levels L |
| 8 | 4 | the PCM rate the peaks were read at, 8192 |
| 12 | 8 | duration in microseconds (the samples read) |
| 20 | 12 × L | per level, finest first: bins a second (u32), count (u32), index of its first bin (u32; not 0 when cut with `start_us`) |
| 20 + 12L | 2 × count, per level | the bins: min, max (signed bytes), level after level |

Example: 2 seconds of a tone at half volume and then silence, asked at `per_second=1`:

```
43 4b 50 4b  01 00  01 00  00 20 00 00  80 84 1e 00 00 00 00 00
01 00 00 00  02 00 00 00  00 00 00 00
c0 40  fe 00
```

- **Header:** `CKPK`, version 1, one level, 8192, 2,000,000 µs.
- **The level:** 1 bin a second, 2 bins, from bin 0.
- **The bins:** (−64, 64) and (−2, 0). The resampler rings for a moment as the tone stops, so the silent second's lowest is −2.
- Read from this step's code on a generated file.

**Size and reading it in JavaScript:**
- **Size:** 2 hours at the default 64 a second, with every coarser level, is 1,828,904 bytes (measured). The stored file with all 10 levels is 14.7 MB.
- **Reading it:** a `DataView` for the header and table, then `new Int8Array(buffer, offset, 2 * count)` per level.

**JSON (`format=json`)**, for scripts:

```json
{"version": 1, "sample_rate": 8192, "duration_us": 2000000,
 "levels": [{"per_second": 1, "first": 0, "min": [-64, -2], "max": [64, 0]}]}
```

## Thumbnails format

One FFmpeg pass over the source:
- **Keyframes only:** with `-skip_frame nokey` the decoder decodes keyframes only. `select='isnan(prev_selected_t)+gt(floor(t/I),floor(prev_selected_t/I))'` keeps the first keyframe of each interval I, so a tile is a real frame and its time is its own (read from `showinfo`). The frames are scaled and padded to the tile and tiled 10 × 10 into JPEG sheets.
- **The spacing I** is the smallest of 1, 2, 5, 10, 15, 30, 60, 120, 300 and 600 seconds that keeps a source to 600 tiles: 15 s for 2 hours.
- **The tile** is 90 px high and as wide as the picture's shape (32 to 320, even): 160 × 90 for 16:9, 50 × 90 for 9:16.
- **Fallback for sparse keyframes:** when fewer than half the expected tiles come out, the pass is run again decoding every frame, which is slower but keeps the spacing. This happens to a source with a keyframe every 10 s and 1 s spacing. `keyframes_only` in the index says which pass made it.
- **A picture** is one tile.

`GET /media/{id}/thumbs`:

```json
{"version": 1, "interval_us": 15000000, "keyframes_only": true,
 "tile": {"width": 160, "height": 90}, "columns": 10, "rows": 10, "count": 480,
 "times_us": [0, 16000000, 30000000, 46000000, "…", 7186000000],
 "sheets": [{"index": 0, "file": "sheet-001.jpg", "width": 1600, "height": 900, "first": 0, "count": 100,
             "url": "/media/video:abc123/thumbs/0?t=…"}, "…"]}
```

- **Where a tile is:** tile i is on sheet `i // 100`, column `(i % 100) % 10`, row `(i % 100) // 10`. Its top-left corner is at (`column × tile.width`, `row × tile.height`).
- **Its time:** `times_us[i]`, counted from the source's first timestamp. With 2-second keyframes and 15 s spacing the times are 0, 16, 30, 46 s…: the first keyframe at or after each 15 s.
- **The last sheet** is padded with black past its `count`.

## The cache

```
data/media_cache/
  <fingerprint>/             64 hex
    source.json              {ref, path, size, mtime_ns, made_at}
    info.json                {version: 1, info: <what the info route returns from ffprobe>}
    proxy.mp4 | proxy.m4a
    peaks.bin
    thumbs/                  index.json, sheet-001.jpg, sheet-002.jpg, …
```

**The fingerprint:**
- For a file, the sha256 of its size, modification time, first megabyte and last megabyte, read without locking it.
- For an imported file, its own sha256.
- A file replaced under the same name gets a new folder, so nothing made from the old file is served for the new one.
- Fingerprints are remembered in memory per (path, size, modification time), so a request reads the 2 MB once.
- **Accepted limit:** a different file with the same size, the same modification time to the nanosecond and the same first and last megabyte, changed only in the middle, gets the old folder. That takes a tool that sets the time back on purpose; hashing the middle too would cost a read of the whole file per new source.

**Never half written:**
- Every product is written under `<name>.part-<8 hex>` (a folder for the thumbnails) and renamed when complete. Only the final names are ever served.
- A temporary name left by a closed app is removed the first time the cache is used after a start, never in a folder that is in use.
- `source.json` and `info.json` say the same whoever writes them. When several requests for a new media race and Windows refuses a rename because another request has the file open (WinError 5), the file already there is taken as written (`cache.write_json(..., once=True)`); any other write tries the rename five times before it fails.

**In use:**
- A folder is held (`cache.hold`) while a job writes into it, a stream reads from it, or it is being made (`folder_for`, until its `source.json` is written). The cap, the orphan sweep, the empty-folder sweep and Clear skip a held folder.
- The hold is per process, and the engine is one process.

**Housekeeping** (`core/housekeeping.py`):
- **What the Storage page shows:** `GET /storage` gains `editor_cache: {folders, files, bytes}` beside `sources`, and `reclaimable.editor_cache`, the orphan folders' files.
- **Orphans:** a folder is an orphan when its source is gone (deleted, or replaced: another size or time) and no project names it (`core/state.py` `project_media`).
- **Free up** (`POST /storage/cleanup`) removes the orphan folders (skipping one a job or a stream took since the survey), then empty folders (`cache.remove_empty`: never a held folder, never a temporary name such as a thumbnails job's `thumbs.part-xxxx` work folder, which stays empty until FFmpeg writes its first sheet, and never one that changed in the last minute), then holds the cache to its cap.
- **The cap:** 5 GB by default (`cache.DEFAULT_CAP_BYTES`), or `editor.media_cache_gb` in the settings when that is a number above 0. The default lives in code, since installed builds never get new settings.yaml keys. The least recently used products go first: the proxy, the peaks or the thumbnails, each on its own.
- **When the cap runs:** "Used" is the product's access time, set explicitly at most once a minute when it is served, so it works on drives that don't keep access times. The cap is also applied after every product the lane makes.
- **The other groups never look inside `data/media_cache/`, and the cache holds nothing of `downloads/` or `clips/`** (`tests/test_media_cache.py`).
- **The Storage page:** it lists `reclaimable` groups from data. With no UI change, `editor_cache` appears there under its raw key whenever orphans exist. No screen asks for media yet, so a person has no cache, and it doesn't show; step 8 adds its label and the "Clear editor cache" button (`POST /media/cache/clear`).

## Projects pin their sources

A video one of the new editor's projects names is never deleted by the app's own clean-up.

**What names a video:**
- a `{"type": "video", "video_id"}` source in any project's `media`;
- a project made from a clip, through `projects.video_id`.

**The one helper:** `core/state.py` `pinned_video_ids(conn)`:
- plain SQL with SQLite's `json_each` over `projects.doc`;
- a Python reading when SQLite has no JSON functions, or when one document is damaged (which makes the SQL fail). A damaged document names nothing, but its `video_id` still pins;
- nothing that deletes files loads the project package.

**Every place the app deletes a source video or a clip file by itself, and what it does now:**

| Where | Deletes | With the rule |
|---|---|---|
| `server/automation.py` `_free_disk` | a watched video's download after its clips are published | skipped while pinned, and not marked freed, so it is freed once no project names it |
| `core/housekeeping.py` `survey`/`clean` (Storage → Free up) | "Downloads with no video" | a pinned download is no orphan, and counts as a kept source. A Vertical Live download `<id>__vertical.<ext>` counts as the video `<id>`'s, both for the pin and for "a video in the library": before this, Free up took every Vertical Live download for an orphan, even while its video was in the library (an older fault, fixed here) |
| `core/remake.py` `Run._restore_source` (Make clips again) | the second name of a video's own copy, when the video left the library during the run | a pinned video gets its copy back, as a video still in the library does |
| `core/remake.py` `remove_clips` / `_clear_files`, `server/jobs.py` re-render | clip files | unchanged: a clip with a project is already kept by Make clips again (step 5), and a re-render replaces the file of a clip whose project follows the new id |
| `core/housekeeping.py` orphan clips, render leftovers, previews | files no clip row points at | unchanged: no project names those |
| `core/pipeline.py` `_cached_or_download` | a local copy FFprobe can't read; a slow AV1/VP9 copy after a fresh H.264 download | unchanged on purpose: plain auto-clipping must stay identical, and both are replaced by a readable copy of the same video, which the project then plays |
| `DELETE /videos/{id}`, `DELETE /clips/{id}` | by hand | unchanged: a person can still delete; the project stays and its media is missing (step 5) |

## The editor lane

The lane (step 1) now also runs **tasks**: `submit_task(kind, key, work, name=, on_done=)`.
- `work(ctx)` gets a `TaskContext` with these:
  - `ctx.run(argv, duration=)`: one FFmpeg, below normal priority, `-progress` as progress, killed by a cancel;
  - `ctx.started(proc)`: a process the task started itself becomes cancellable the same way;
  - `ctx.progress(f)` reports progress;
  - `ctx.check()` raises once a cancel was asked for.
- **Made once:** a task with the key of one waiting or running is not queued again. The keys are `<product>:<fingerprint>`.
- **Order:** tasks and exports share the one worker, one at a time, first come first served.
- **Events:** `{"type": "media_job", "job": {job_id, kind, key, name, status, cancelling, fraction, result, error, created_at, started_at, finished_at}}`.
- **Exports see only exports:** `jobs()`, `get()` and `cancel()` answer for exports only, unless asked otherwise, so `GET /export/jobs`, Make clips again's in-use check and the export cancel route are as they were.

## What changed in existing code

- `core/state.py`: `project_media`, `pinned_video_ids`, `StateDB.pinned_video_ids`, `StateDB.video_pinned`.
- `core/housekeeping.py`: pinned downloads kept; the `editor_cache` group; `clean(..., cache_cap=)` removes orphan cache folders and applies the cap.
- `core/remake.py`: `_restore_source` keeps a pinned video's copy.
- `server/automation.py`: `_free_disk` skips a pinned video.
- `server/editor_lane.py`: tasks (above). Exports are unchanged; `tests/test_editor_lane.py` passes as before.
- `server/projects_api.py`: the guard is a function, `session_guard`, which the media routes reuse. The project routes behave as before.
- `video_editor/project/store.py`: `add_asset` hashes and copies in one pass and takes `progress`. It still returns the row, and still keeps one file per sha256.
- `server/api.py`: installs `server/media_api.py`, and passes the cache cap to Storage's clean-up.
- `.github/workflows/ci.yml`: `tests/test_media_routes.py` joins the Plugin platform job. The other step 6 tests run on the CI light install (FFmpeg ones skip without FFmpeg).
- `core/pipeline.py`, `server/jobs.py`: unchanged.

## Measured

On this PC (12 threads), CPU only, on a generated 2-hour source (1280 × 720, 30 fps, H.264 at 250 kbps with a keyframe every 2 s, AAC 96 kbps, 320 MB):

| What | Target | Measured |
|---|---|---|
| Peaks, numpy | ≤ 90 s | 2.9 s |
| Peaks, standard library | ≤ 90 s | 6.7 s |
| Thumbnails (480 tiles, 5 sheets, keyframes only) | ≤ 3 min | 7.3 s |
| A 64 KiB Range at any of 5 offsets, whole answer through TestClient (median of 5) | a few ms | 4.5 to 4.7 ms |
| Reading 256 KiB at any offset (`SharedFile.read_at`) | | 0.04 to 0.12 ms |
| Peaks sent at 64 a second, every level below | small | 1.83 MB |

- A real 1080p60 VOD decodes more slowly than this generated picture: the keyframe pass decodes one frame in 60, and the sound is the same.
- The fallback pass (every frame decoded) was not measured on 2 hours. It only runs for sources with keyframes further apart than the spacing.

## Choices made

Where the plan was not specific:

1. **Ids are `kind:value`** in one path segment, and `project:<project>:<media>` reaches a project's own media. A route never takes a path, except import.
2. **The read routes take a per-media token** in the address as well as the header (ED24). The plan said only that changing routes need the header and the Origin check.
3. **A project's `{"type": "file"}` source is served only inside the data folder** (ED25). An imported clip's own music file elsewhere on the PC is imported for editing first.
4. **Import refuses `\\server\share` paths**, as the app's other picked-file routes do (`core/paths.py` `picked_file`). A mapped network drive works when it answers within 5 s (ED25).
5. **Several ranges in one request are answered with the whole file.** Chromium's media stack asks for one range at a time.
6. **Peaks are signed bytes at 512, 256, … 1 bins a second, from 8192 Hz mono.** A level is chosen by rounding `per_second` down to a power of two, so every level is exactly half the one below.
7. **Peaks and thumbnails are made when first asked for**, on the lane, with 202 until then. Asking is a GET, because making them changes nothing but the cache. They are not tried again after a failure without `retry=true`.
8. **The proxy is made on the CPU** (libx264), not the graphics card: the same everywhere, and the card stays free for exports.
9. **Matroska with H.264/AAC gets a proxy** until step 8 shows Chromium 126 plays it. (Step 8 showed it: the rule is gone.)
10. **The cap's "least recently used" counts each product on its own**; `source.json` and `info.json` stay until their folder goes (orphan, or Clear).
11. **Housekeeping's cap runs on Free up and after each product**, not on a timer, so nothing runs that wasn't asked for.
12. **Lane tasks and exports share one queue, first come first served.** A waveform can wait behind an export, and the other way round. Step 8 can put small tasks first if it shows.
13. **A Vertical Live download is its video's** (ED26): `video:<id>` falls back to `<id>__vertical.<ext>`, and Storage counts it as the video `<id>`'s. The plain download wins when both exist.
14. **A list of other files is not media** (ED26): playlists, concat lists and session descriptions are refused wherever media is read, and FFprobe and FFmpeg read local files only.
15. **No cache of resolved paths.** A download is found by trying its few possible names (constant time); the database read stays, so a project's media change is seen by the next request.

## Left for steps 7 and 8

- **Step 7:**
  - the export compiler reads every media through `resolver.resolve` (and `cache` for nothing: the final render always uses originals, ED7);
  - tracking results can be cached in `media_cache/<fingerprint>/` beside the others (add the name to `cache.PRODUCTS` so the cap and Clear count it);
  - still frames and draft renders become tasks on the lane, the same way.
- **Step 8:**
  - **Data:** take `urls` from `GET /media/{id}/info` (they carry the token) for `<video src>`, `<img src>` and the sheet addresses; listen for `media_job` events; check whether Chromium 126 plays H.264/AAC in Matroska (if it does, drop that rule).
  - **Screens:** the "Clear editor cache" button and the Storage label for `editor_cache`; how a project shows a source that is missing.
- **Small faults from the last check, not fixed (none serious; fix them early in step 8):** step 8 fixed each one or gave its reason for leaving it; see [step8-editor.md, "Step 6's small faults"](step8-editor.md#step-6s-small-faults).
  - **Clear while a job waits:** a peaks, thumbnails or proxy job that is queued (not yet running) when Clear or the orphan sweep runs fails, because its cache folder is gone. `GET` peaks and thumbs then answer 500 until the caller adds `retry=true`, and the error text shows the cache path. Fix: make the folder inside the job before writing, and keep the path out of the message.
  - **Junctions in the cache:** `cache._remove` and the survey treat a Windows directory junction as a folder, so a junction placed inside `data/media_cache` by hand or by other software would have its target's files deleted by Clear or Free up. The app never makes one. Fix: skip reparse points before recursing.
  - **Peaks job on a read error:** if `Builder.feed` raises while FFmpeg is still writing, `compute()` waits for FFmpeg without killing it, and the lane can hang. Fix: kill the process before waiting.
  - **A Range with thousands of digits** (over Python's 4,300-digit limit) answers 500 instead of being ignored. Fix: cap the digit count.
  - **If-Range by date** is honoured when the file changed within the same second. Chromium sends the ETag, so the editor does not hit it. Fix: accept a date only when the file is at least a second old, or accept only ETags.
  - **Import, file changed after the check:** a large import is probed when asked and copied later on the lane; a file replaced or still being written in between is stored with the earlier facts. Fix: probe the copied `.part` file before it is renamed and recorded.
  - **Upper and lower case:** on Windows `video:VID1` serves the download of `vid1`, while the pin compares exactly. Fix: check the exact file name, as `cached_source` does.
  - **Tampered rows:** a clip row edited to point at `state.db` inside the data folder, or a hard link named `.mp4`, is served. Both need someone who can already write the database or the disk. Fix: give `clip:` the media-extension rule and refuse files with more than one link.
  - **A replaced download a project names** leaves its old `source.json` and `info.json` until Clear (the cap removes the products). Fix: an orphan is any folder that isn't the current fingerprint of a named source.
  - **FFmpeg holds the source on Windows** while a proxy, peaks or thumbnails job runs (read from FFmpeg's source, not run), so a delete in that time may leave the file. Fix: cancel a video's lane tasks when it is deleted.
  - **Make clips again** puts back a pinned video's copy even when the person deleted the video by hand during the run (the 409 on `DELETE /videos` makes this rare).
  - **Thumbnails on a long source with far-apart keyframes:** the every-frame fallback on a 2-hour 1080p60 source took 366 s, over the 3-minute target. It runs only when keyframes are more than 30 s apart on a 2-hour source; no measured source did that.
- **Open:**
  - `os.replace` onto a name being streamed on Windows is refused (above): no app path does that to a download today. Any future code that replaces a source moves the old file aside first.
  - A Vertical Live video that also has a plain download: `video:<id>` is the plain one. A project can't yet say which of the two it means; no app path makes both for one video today, except clipping the same link both ways.
  - The read routes resolve the id on every request (a database read, and up to seven `stat` calls for a download, whatever the size of the library). Measured at about 4.5 ms per Range request before the change; no cache of resolved paths was added, so a project's media change is seen at once.
  - A source whose first timestamp isn't 0 (`start_us` in the info): the proxy and the thumbnail times start at 0, and step 8 lines the source and the proxy up by `start_us`.
  - The proxy rule's "on a weak PC" is not measured.

## Tests

| File | What it checks | Runs on CI light |
|---|---|---|
| `tests/test_media_fileio.py` | Every Range form (ASCII digits only), 416, ignored ranges, ETag, If-None-Match, If-Range, content types; reading at any offset; on Windows, renaming, deleting and replacing a file while it is open | yes (Windows parts skip elsewhere) |
| `tests/test_media_resolver.py` | Every media shape (a Vertical Live download too), missing files named, a `file` source that isn't a sound or a video, ids that try to leave (`..`, `\`, `/`, a symbolic link out), a clip file outside the data folder | yes |
| `tests/test_media_probe.py` | The proxy rule on ffprobe's JSON (codecs, containers, 10-bit, Matroska, 1440p, 120 fps, rotation, cover art); lists of other files refused; every command reads local files only; real files, and a real playlist refused | yes (FFmpeg parts skip without it); also in the Plugin platform job |
| `tests/test_media_peaks.py` | Bins in any pieces, extremes and silence, each level halves the one below, numpy gives the same bytes, the format round trip and cut, 2 hours is small; a tone then silence through FFmpeg on both paths | yes (numpy and FFmpeg parts skip); also in the Plugin platform job, which has both |
| `tests/test_media_thumbs_proxy.py` | Spacing, tile size, commands; tiles from keyframes at their real times, two sheets, the sparse-keyframe fallback, a picture; a proxy probed at 540p H.264 with keyframes at most 0.5 s apart and AAC; a turned 120 fps phone video | yes (FFmpeg parts skip); also in the Plugin platform job |
| `tests/test_media_cache.py` | The fingerprint follows the file, a replaced file gets a new folder, products appear only whole, leftovers swept, the cap removes the oldest first and never what is in use, using a product makes it recent, the cap setting, orphans, Storage's groups and Free up, Clear; Free up leaves a running job's empty work folder; empty folders go only when free and not new; a new folder is held while it is made; a racing write of `info.json` | yes; also in the Plugin platform job |
| `tests/test_media_pinning.py` | What projects name (SQL and the Python reading, a damaged document), Storage never frees a pinned download, Make clips again gives a pinned copy back, a Vertical Live download counts as its video's | yes |
| `tests/test_automation.py` (one test added) | The delete after publishing keeps a pinned download, and frees it once no project names it | no (FastAPI; Plugin platform job) |
| `tests/test_editor_lane_tasks.py` | Tasks in order, made once, apart from exports in `jobs()`/`get()`/`cancel()`, their events, a failure, cancelling kills FFmpeg | yes |
| `tests/test_media_routes.py` | Every Range form through the route, HEAD, 304, If-Range, 416, 404s, tokens, foreign origins, changing routes need the header, the old clip route unchanged, a stopped stream releases the file, on Windows the source renamed and deleted mid-stream; info, proxy, cancel, peaks, thumbnails, a failed product, a replaced file, import (good, twice, refusals, large on the lane, a playlist), Clear, Storage; non-ASCII Range digits, a file that shrinks mid-stream, no duplicate OpenAPI ids, a `file` source never serves `state.db`, a Vertical Live source | no (FastAPI; Plugin platform job) |
| `tests/test_editor_import_guard.py` | The pipeline never reaches the media code, the package's own rules, who imports it | yes |
