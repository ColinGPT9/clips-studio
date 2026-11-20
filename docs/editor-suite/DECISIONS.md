# Editor suite decisions

Numbered decision records for the editor suite. Each one gives the date, the context, the decision, why, what was rejected, and how to undo it. The records below were taken on 2026-11-05, with the plan Colin approved; newer records go at the bottom. Facts come from [RESEARCH.md](RESEARCH.md) unless a file is named, and "(inferred)" marks a conclusion rather than a finding. Step numbers refer to [`state.json`](state.json).

## ED1 · One editor inside Clips Kitty and one renderer: Concat's design is borrowed, not shipped

**Date:** 2026-11-05 · **Steps:** 5, 7, 8

**Context:** Issues #192 and #144 ask for a real editor and for whole-video editing. Today's editor works on one rendered 9:16 clip and simulates cuts on that file. The strongest outside candidate was Concat (AGPL-3.0-or-later): a working Rust editor with a headless engine, about 55 edit commands, JSON-RPC and MCP. But its editing UI and real-time preview exist only in its own Slint window, its API returns still frames only, its export has no bitrate setting (#471), it would bring a second FFmpeg, and one maintainer wrote 1,195 of its 1,364 commits; its main-branch CI was red on 2026-11-05. The Concat research ranked a headless Concat sidecar under a React timeline first, and estimated it at 4–6 months (inferred). Every ready-made React editor SDK found was licence-incompatible or unmaintained.

**Decision:** Build the editor inside Clips Kitty, with one renderer: a React timeline seeded from OpenCut classic's MIT code, a `<video>` viewer fed by the engine, and the existing Python-to-FFmpeg path as the only export path. Borrow Concat's design, not its code or binary: the project shape (project → canvas, fps, media, tracks; items with source in/out and program start/duration), unknown fields kept in `extra`, append-only schema migrations, and a batch as one undo step. Its 55 window commands are trimmed to about 20 validated operations (ED2). Export has two branches. Branch A sends a project that still fits today's `render_opts` to the existing render job, so its output is identical to now. Branch B (`video_editor/compose.py`) compiles everything else into one RenderPlan for the same FFmpeg.

**Why:** One renderer means one FFmpeg, one encoder choice (NVENC, AMF, QSV, x264), one set of tests and one place to fix a bug. The preview stays in Electron with the rest of the UI. Both projects are AGPL, so taking design ideas raises no licence question, and shipping no Concat binary avoids its CLA, its rule that modified builds be renamed, and pinning a pre-1.0, single-maintainer engine.

**Rejected:**
- A headless Concat sidecar under our own timeline (the Concat research's first choice): its API has no real-time preview, runs one export per process and brings a second FFmpeg, and upstream changes daily.
- An "Open in Concat" handoff: a separate, unsigned app with no live sync.
- `concat.json` as Clips Kitty's project file: f64 seconds, absolute media paths and a single-maintainer schema.
- FilmCraft or EffectCraft as the engine or as an embedded UI: too unstable (FilmCraft's project format reached schema v17 in five weeks), their native egui windows can't live inside Electron, and FilmCraft has no AMF or Quick Sync.
- MLT/melt as the renderer: packaging, frame-based time, and a second renderer to keep matched with the preview.
- Adopting OpenCut classic (archived) or omniclip (one developer) whole; Twick, OpenVideo, Remotion and Diffusion Studio (their licences).

**Undo:** The new editor ships behind a "New editor (beta)" toggle, and the current editor stays. Branch B emits a RenderPlan that tests snapshot, so another renderer could later sit behind the same plan without changing projects (inferred).

## ED2 · About 20 validated operations, shared by the editor, the AI and MCP

**Date:** 2026-11-05 · **Steps:** 5, and Phase 5

**Context:** Concat has about 55 edit commands. The MCP tools specification (2026-07-28) says servers MUST validate all tool inputs, and that there SHOULD always be a human able to deny tool calls. OWASP LLM05 and LLM06 (2025) warn against feeding model output into shells and against open-ended tools. Anthropic's guide to writing tools says "More tools don't always lead to better outcomes". In Clips Kitty today, the AI edit chat asks for JSON without a schema (`analysis/clip_edit.py:55`) and its route queues a render at once, and the engine API checks Host but not Origin and has no authentication (`server/api.py:692,698`).

**Decision:** One versioned schema of operations, used alike by the manual editor, the AI chat and MCP: `POST /projects/{id}/ops {expected_revision, ops, dry_run, label}`, checked in plain Python, which returns the inverse ops and any warnings. The ops:
- `set_canvas`, `set_project_props`
- `add_media`: the server resolves paths; never a client path.
- `insert_clip`, `trim_item`, `split_item`, `delete_items` (ripple), `move_item`, `duplicate_items`, `set_item_props` (speed 0.25–4, volume 0–2, filters through `filters.is_valid`, gaming through `modes.clean_gaming`)
- `add_track`, `remove_track`, `set_track_props`
- `add_audio` (loop for music only, duck under A1), `set_audio_mutes` (mute or bleep)
- `add_text` and `set_text`: at most 500 characters, ASS control characters stripped, font from the registry.
- `set_captions`, `edit_caption_words`, `set_speaker`, `set_branding`
- `set_export_preset`: only a codec this PC can actually encode.
- `batch`: atomic, one undo step.

Every route that changes something needs the `X-Clips-Kitty-Session` header plus an Origin check.

**Why:** A small, typed set is one an AI can use reliably, and a single validator protects every caller. Fonts and codecs come from allow-lists, so a model never writes FFmpeg arguments.

**Rejected:** All of Concat's commands (too many to use reliably); a free-form "run this code" tool (BlenderMCP's `execute_blender_code` is the counterexample the research found); file paths sent by the client; schema-less JSON from the model.

**Undo:** The routes start with the INTERNAL stability label (`server/api_stability.py`), so the schema can still change; the ops are versioned with the project document.

## ED3 · The project model: integer microseconds in SQLite, with undo and redo on the server

**Date:** 2026-11-05 · **Step:** 5

**Context:** Today an edit is an `EditList` in seconds relative to one clip, stored in `clips.render_opts['edit']`, with 30 undo steps in the browser and no redo. A re-render gives the clip a new id (`server/jobs.py:943-950`). Concat keeps f64 seconds in its document and exact rational time only when rendering; FilmCraft counts integer ticks at 254,016,000,000 per second; MLT counts frames.

**Decision:** A project model in `video_editor/project/`, standard library only.
- Shape, borrowed from Concat: project → canvas, fps, media, tracks. V1 is the gapless main track, V2 and up are overlays, and there is a text track; A2 and up hold music, sound effects and voice. Each item has `media_id`, source in/out, program start/duration, speed, volume, fades, `framing`, `transform`, `color`, `text`, `link` and `extra`. Captions stay in source time.
- Time: integer microseconds. V1 edges snap to the frame grid when an operation is checked.
- Storage: SQLite through `_migrate()` in `core/state.py`. `projects` holds `clip_id` with no foreign key, because re-renders change clip ids, and follows it with `follow_clip_id`. `project_revisions` stores ops and inverse ops, with a snapshot every 25. `media_assets` is addressed by sha256, with files in `data/media/assets/`. Then `project_exports`.
- Undo and redo on the server: each change is a compare-and-swap on `head`, and a stale edit gets 409. Undo applies the inverse ops, rapid tweaks coalesce, and at most 500 revisions are kept.
- Versioning: `doc.schema`, with pure migrations applied on load. Unknown fields are kept, and a document with a newer schema opens read-only.
- AI clips convert to projects losslessly (`importer.py`) and back (`projection.py`).

**Why:** Integer microseconds are exact in JSON and in JavaScript, and don't depend on the frame rate. JavaScript holds integers exactly up to 2^53 − 1, which in microseconds is about 285 years; FilmCraft-style ticks would pass that after about 9.8 hours (computed), so JavaScript could not hold a long source's times exactly. With undo on the server, the editor, the AI and MCP share one history, and a stale window can't overwrite newer work. Captions kept in source time survive re-trims.

**Rejected:**
- f64 seconds, as in Concat: decimal fractions such as 0.1 s aren't exact, so equality checks and round trips can drift (inferred).
- Frame counts, as in MLT: they tie a project to one frame rate.
- Rational ticks, as in FilmCraft: not exact in JavaScript for long sources.
- Keeping edits in `render_opts`: one clip only, with no tracks.
- Undo in the browser only, as today.

**Undo:** The tables are additive, created by `_migrate()`. `core/pipeline.py` never imports the project package (ED4's import guard), so removing it leaves auto-clipping as it is, and projection writes AI clips back as `render_opts` that the current editor understands.

## ED4 · Plain auto-clipping stays identical, checked from step 0

**Date:** 2026-11-05 · **Steps:** 0, and every later step

**Context:** Phase 2 changes `video/encoding.py`, `server/jobs.py`, `video_editor/` and `core/pipeline.py` (there, only to extract `frame_intermediate`). Plain auto-clipping is what most users run. Nothing tests the concat, mute, master or ducking filter graphs today. NVENC output can't be bit-exact across driver versions.

**Decision:** Before any refactor, step 0 adds three guards and an i18n check:
- Argv snapshots: subprocess, probing and tracking are mocked, and argv, ASS text and `render_opts` JSON are recorded across 14 cases (plain, captions, highlight, cuts, speed, music, hook, watermarks, filters, letterbox, center, landscape, vertical live, gaming). Snapshots change only with `--update-snapshots` and a review.
- Golden renders: a CC0 10-second fixture rendered with the CPU encoder and CPU tracking; sha256, framemd5 and ffprobe output are compared against main.
- An import guard: `core/pipeline.py` never imports `video_editor.project`.
- An i18n check: new editor code has no bare JSX text.

The guarantee is the same argv and files for the same inputs, and bit-exact output on the same machine with the CPU encoder. Against the baseline, the plain pipeline may not regress by more than 10% in render time, RAM or VRAM.

**Why:** A changed FFmpeg command shows up in an argv snapshot before anything renders, a golden render catches what argv can't, and the import guard keeps the editor optional.

**Rejected:** Unit tests alone; comparing NVENC renders (not bit-exact across drivers); judging sample clips by eye.

**Undo:** The guards are tests. A deliberate change of output is accepted with `--update-snapshots` and a review.

## ED5 · Export presets for everyone, with no plugin

**Date:** 2026-11-05 · **Step:** 1 (sizes); AI framing in other aspects comes in step 11

**Context:** #177 asks for resolutions other than 1080p, and #203 for FFmpeg settings, because clips go over WoopSocial's 100 MB limit. Output is always 1080p H.264: 1080x1920, or 1920x1080 in Longform. NVENC runs at CQ 23 with `-b:v 0`, so there is no bitrate cap (`video/encoding.py:31`). Export is a plain file copy (`server/api.py:2462-2481`). `video.encoder` reaches only 2 of 15 call sites, and forcing an encoder flips the choice for every later render (`video/encoding.py:183-189`).

**Decision:** `video/export_presets.py`, in the core app:
- Sizes from 720p to 2160p, with a warning on the Settings card when the choices would enlarge a tall 1080x1920 clip's picture by more than 1%; a custom width and height are 720 to 4096 each. (240p to 540p were offered until 2026-11-06, when the owner removed them: they look bad, and he exports at 1080p. A saved default that names one loads as 720p.)
- Aspect 9:16, 16:9, 1:1, 4:5 or custom, filled by crop, blur pad or colour pad.
- Quality Smallest, Balanced, High or Custom.
- Codec H.264 or HEVC; AV1 only where the GPU can encode it (not an RTX 2060 or 3060).
- CRF/CQ or a bitrate, plus fps and audio bitrate.
- A size target such as "under 100 MB" (revised in step 1's fix round, 2026-11-06; it was first a bitrate for every clip, from the formula below, with one retry at 0.9×, which could make a short clip bigger and a long one unwatchable):
  - A clip is made exactly as without the limit, checked, and made again at the limit's bitrate only if it came out over, when it may fit anyway (even the low end of its estimate is under the limit) or its size can't be told beforehand: a graphics card's constant-quality encode (NVIDIA CQ, Intel ICQ in H.264 and HEVC; never measured), or a CPU encode at a quality level with colour bars or a blurred background (0.47× and 0.82× the estimate on a real clip), of a clip whose own bitrate is under the estimate's busyness floor, or of one whose bitrate can't be read. (AMD, Apple and Intel's AV1 are encoded at a bitrate, so their size is known.) Such a clip is never bigger than without the limit.
  - Only a clip whose size is known (its export without the limit is at a bitrate, or is a CPU encode of a measured kind) and whose whole estimate is over the limit is held to it from the start: bitrate = 0.95 × 8 × bytes ÷ duration − audio, which is under that estimate's low end. Hardware encoders get VBV caps and the CPU uses 2-pass. (Where the limit leaves less than the floor and a file at the floor would come to the low end of the estimate without the limit or more, the clip is made as without the limit and checked instead.)
  - Never below a measured watchable floor (700 kbps of H.264 at 1080x1920 60 fps, scaled for size, frame rate and codec): a limit that leaves less goes no lower, and the export says it is too small.
  - A file over the limit is made again (at the limit's bitrate, or 0.9× a held one), and the smallest file made is the export. A graphics card that fails at making it again hands it to the CPU (H.264 and HEVC), as it does a failed first try.
  - WoopSocial's smaller copy keeps the first rule: always held to its target, one 0.9× retry, at least 100 kbps.
- A size estimate, shown before export.
- For existing clips the preset is a transcode after the end card, and "Original" stays a plain copy; in Branch B (ED1) it is part of the final encode.
- WoopSocial publishing gets an opt-in "make a copy under 100 MB".
- The encoder fix: `encoding.configure(config)` runs once at startup, and a separate `encoder_for(codec, pref)` serves presets, so a preset never changes the global encoder.

**Why:** It closes #203 and the size part of #177 for every user, while the 1080p masters, the cropper and the H.264 end-card join stay as they are. The 0.95 factor leaves room for container overhead (inferred).

**Rejected:**
- Export settings as a plugin or optional add-on: they are basic needs of every user.
- Raw FFmpeg arguments from users or a model. An allow-listed "Advanced FFmpeg" section waits for Phase 4.
- Rendering natively above 1080 now: that needs the canvas passed through the renderer, the cropper, the gaming layouts and the end card. Native 1440p and 2160p masters wait for Phase 4; until then, sizes above 1080 are made from the 1080 master and look no sharper.
- AV1 everywhere: RTX 30 cards can't encode it.

**Undo:** "Original" stays a plain copy, so not picking a preset gives today's file. The presets run as a final transcode, which can be removed without touching rendering.

## ED6 · Exports and previews run on their own editor lane

**Date:** 2026-11-05 · **Steps:** 1, 6, 7

**Context:** Render jobs share one queue with processing jobs (`server/jobs.py:301`), so an export transcode queued there would wait behind a one-hour processing job. "Update preview" renders at full resolution inside the request handler (`server/api.py:1889-1990`).

**Decision:** A new `server/editor_lane.py`: a second worker at below-normal priority that runs one FFmpeg at a time and can be cancelled. Exports use it from step 1; proxies, peaks and thumbnails from step 6; the 540x960 draft render and still frames from step 7.

**Why:** Editing stays responsive while a long processing job runs, and no preview blocks a web request.

**Rejected:** The existing job queue (an export would wait behind processing); rendering inside the request handler (today's "Update preview"); several FFmpeg processes at once on the lane (they would compete for the GPU and the disk, inferred).

**Undo:** Send the editor's jobs back to the main queue; the lane is one module.

## ED7 · Proxies only when needed

**Date:** 2026-11-05 · **Step:** 6

**Context:** #144 means opening whole sources, often 2-hour VODs. Chromium plays H.264 directly; HEVC plays through Windows hardware decoding, so a PC without a hardware decoder can't play it (inferred). A 540p proxy at about 2 Mbps costs about 1.8 GB per 2-hour VOD (inferred). Concat makes proxies only for sources above 1080p, and Shotcut and Kdenlive always render the final file from the originals. Storage clean-up deletes mp4 files under `data/clips` that the database doesn't know (`core/housekeeping.py:74-84`), and a watched channel can be set to delete a source once its clips are published (`server/automation.py:1181`).

**Decision:**
- Sources stream from `GET /media/source/{id}`, with Range and ETag. On Windows the file is opened with share-delete, so streaming never locks it.
- A 540p, short-GOP proxy is made only when needed: for an unplayable codec, for a source above 1080p60 on a weak PC, or on request.
- Proxies, peaks and thumbnails live in `data/media_cache/<fingerprint>/`, with their own housekeeping group, orphan clean-up, a size cap and a "Clear editor cache" button; never in `downloads/` or `clips/`.
- A video that a project uses is never deleted after publishing.
- The final render always uses the originals.

**Why:** Most sources play as they are, so most users never wait for a proxy or store one.

**Rejected:** Always making proxies (disk space and minutes for every source); FFmpeg-assisted streaming playback as the default (LosslessCut restarts FFmpeg on every seek); proxies stored next to downloads (clean-up would sweep them).

**Undo:** The cache holds only files made from the sources, so clearing it loses nothing. Making proxies for every source is a change to the rule above.

## ED8 · Captions preview with JASSUB

**Date:** 2026-11-05 · **Step:** 9

**Context:** Captions are burned as ASS through libass (`video_editor/export.py:89`), but the editor previews them as HTML overlays that copy the caption maths into TypeScript (`LiveTextOverlay.tsx:50-67`, `CaptionStyleControls.tsx:5-30`), so the preview can drift from the export. The editor's Content Security Policy blocks WebAssembly, workers and the engine's fonts (`ui/src/renderer/index.html:7`).

**Decision:** The engine writes one ASS file per project revision (`captions.ass?rev=`), and JASSUB, which is libass compiled to WebAssembly, draws it over the viewer with the same font files as the burn. The CSP gains `'wasm-unsafe-eval'`, `worker-src blob:` and `font-src` for 127.0.0.1:8765. The proof is SSIM ≥ 0.98 on the caption area between a JASSUB frame and the burned frame.

**Why:** The same renderer draws both, and the TypeScript side stops copying caption maths.

**Rejected:** Keeping the HTML overlays (they drift); a server-rendered still for every change (too slow for playback; the still route exists so that "pause shows the truth"); a draft video for every caption change.

**Undo:** Revert the CSP change and go back to the overlays; the ASS route only reads.

## ED9 · Bundled assets are CC0 or OFL

**Date:** 2026-11-05 · **Steps:** 3, 4

**Context:** #174 and #155 ask for fonts, sound effects and music. Captions use Windows stock fonts only (`video/captions.py:36-52`), music is a file path the user picks, and there are no sound effects. Several popular libraries forbid redistribution inside a tool.

**Decision:** Bundle about 12 caption fonts (about 2.5 MB), Kenney CC0 sound effects and HoliznaCC0 music (CC0), in a library under 25 MB with a sha256 manifest, and record every licence in NOTICE and the manifests. The plan calls the fonts OFL; the research's starter set has 10 OFL fonts and 2 Apache-2.0 fonts, which are equally fine to bundle. Montserrat ships as static weights, because libass has an open variable-font issue. Exclude Sonniss, Pixabay, Mixkit and the YouTube Audio Library. CC-BY music only with automatic credit lines. Freesound CC0 sounds are downloaded through the website, since its API is "free only for non-commercial purposes".

**Why:** CC0 needs no attribution, and the OFL allows bundling with software as long as each copy carries the notice and the licence. A provenance manifest helps in Content ID disputes (inferred).

**Rejected:** Sonniss GDC bundles (they forbid supplying the sounds "as sound effects to any other person", asset packs included, and forbid AI training); Pixabay ("cannot… distribute Content… on a Standalone basis"); Mixkit (no redistribution inside a tool); the YouTube Audio Library (no blanket redistribution found); variable Montserrat (libass issue #386).

**Undo:** Assets are files plus manifest entries. Removing one is a manifest change, checked by the licence-manifest test.

## ED10 · Motion graphics use the HyperFrames format in Electron's own Chromium, without GSAP

**Date:** 2026-11-05 · **Phase:** 6 (nothing in Phase 2)

**Context:** HyperFrames (Apache-2.0) turns plain HTML compositions into video by seeking each frame and capturing it. It is real and widely used, but pre-1.0 and releases several versions a day. Its engine always launches its own pinned headless Chrome (a 119.5 MB download) and needs Node 22 or later; on Windows it captures with `Page.captureScreenshot` anyway. All of its keyframe edit operations are GSAP-only, and about 640 of its about 760 registry templates use GSAP. GSAP's licence prohibits use "in tools that allow users to build visual animations without code" that compete with Webflow, and a CapCut-style editor is such a tool (the research's reading). Electron offers the same capture protocol in-process (`webContents.debugger`).

**Decision:** In Phase 6, motion graphics are Clips Kitty's own JSON layers and keyframes, compiled to HyperFrames-compatible HTML and animated with WAAPI, CSS or anime.js. They render in a hidden, sandboxed Electron window (no Node, network blocked, a strict CSP): seek with `__hf.seek(t)`, wait for fonts and two animation frames, capture a PNG with a transparent background, and composite it with the bundled FFmpeg. Lottie through ThorVG covers designer-made assets. GSAP never ships.

**Why:** No second browser to download, a licence-clean stack, a format an AI can write, and properties that stay editable one by one through Clips Kitty's own keyframes. Headless and Docker renders can still run the HyperFrames producer as shipped, on the same files.

**Rejected:** HyperFrames as shipped (a second Chromium, Node 22, telemetry on by default, a GSAP-heavy template registry, near-daily releases), kept at most as an opt-in plugin; Remotion (ED11); Motion Canvas, Revideo, Theatre.js, Motionity and MotionForge (dormant, or not editable property by property); Rive (proprietary editor); Manim (code only); EffectCraft as the engine (too unstable; at most an optional template plugin).

**Undo:** Nothing ships before Phase 6. The HTML is a compile target, so the renderer behind it can change without changing projects.

## ED11 · Remotion only through imported renders

**Date:** 2026-11-05 · **Phase:** 6

**Context:** Remotion is proprietary and source-available: "not open-source software according to the Open Source Initiative (OSI) definition". Its terms (updated 2026-11-04) make it free for individuals and for organisations of up to 3 people. A desktop app may use it only behind an abstraction layer, with no end-user access to edit or upload Remotion code, and render reporting still applies. A distributor must hold a Company License for a year after the app stops being maintained. Organisations building video editors fall under "Remotion for Automators" ($0.01 per render, minimum $100 a month), whose telemetry Remotion 5.0 makes mandatory.

**Decision:** Clips Kitty never bundles or calls Remotion. Someone who licenses Remotion themselves can import their renders as ProRes 4444, WebM with alpha, or a PNG sequence with alpha.

**Why:** In the research's reading, those terms are "further restrictions", which AGPL §10 forbids, and the no-code-access rule clashes with every AGPL user's right to modify the app. An AGPL §7 exception would need every copyright holder to agree, and the project has outside contributors. This is a reading for planning, not legal advice.

**Rejected:** Bundling Remotion; its in-browser `renderMediaOnWeb` (only a subset of HTML, and it "always sends a telemetry event for every render"); buying a licence (it doesn't remove the AGPL conflict).

**Undo:** The import is ordinary media import; there is nothing to remove.

## ED12 · Voicebox ships as the voice engine

> **Replaced on 2026-11-12 by ED23 (at the end of this file).** Voicebox is not shipped. What follows is kept as the record of the first choice and of what was known then.

**Date:** 2026-11-05 · **Steps:** V1, V2 · Colin's choice

**Context:** The voice-over tool (V2) and the promo videos (ED17) need a local voice that is safe for monetised videos. Colin asked which of Voicebox and VoiceStudio is better to ship in the Microsoft Store build. The audio research had ranked audio.cpp first, for both voice and sound, and Voicebox second, as a voice sidecar. Facts found on 2026-11-05:
- Voicebox is MIT. Its voice server is a fixed build that ships inside its installer and takes `--host --port --data-dir --parent-pid`; the last makes it exit when its parent dies. There has been no release since v0.5.0 (2026-04-25). Its GPU build is a separate 3.1 GB download of code.
- VoiceStudio is AGPL-3.0-only plus a CLA and a company's paid licence. Its first launch downloads Python and PyTorch, and it needs about 10 GB of disk (about 5 GiB on CPU-only machines). It gets about 30 commits a day. Its default voice model, OmniVoice, became non-commercial on 2026-07-03.
- Microsoft Store policy 10.2.2 rules out dynamic code that changes an app's described functionality.

**Decision:** Ship Voicebox's pinned CPU voice server inside Clips Kitty (step V1). The server isn't published on its own, so it is taken from the pinned release installer, SHA-256-checked, into `vendor/voicebox/`. Clips Kitty starts and stops it like Ollama, on loopback, with `--parent-pid` and its own data directory, and Clips Kitty's own PyTorch is untouched. The default voice is Kokoro `af_heart`. Qwen3-TTS and Chatterbox voices, which are clear for commercial use, are offered too, and non-commercial models are hidden. The Store build is CPU-only.

**Why:**
- Licence: MIT is simple, where VoiceStudio is AGPL plus a CLA and a paid licence.
- Packaging: Voicebox is a fixed background server that can be packaged and certified. VoiceStudio downloads Python and PyTorch at first launch, which is code downloaded at runtime (Store policy 10.2.2), and needs 5–10 GB.
- Commercial use: VoiceStudio's default voice became non-commercial in July.
- Stability: VoiceStudio changes about 30 times a day, which makes it hard to pin and certify.

**Trade-offs accepted:** No Voicebox release since April, so one version is pinned and tested here. About 0.5 GB more. A 4th background process, which goes into the Store submission notes. No GPU add-on, since it is a 3.1 GB download of code; Kokoro is fast on CPU, and cloning voices are slower (inferred). The Store listing discloses the AI voice (policy 11.16).

**Rejected:** VoiceStudio (above); audio.cpp as the voice engine now (the research's first pick, which stays the plan for sound effects and music, ED14); calling a voice app the user already runs (licence warnings would live in that app); Soundbox (three weeks old, being renamed, no authentication); a Python environment per model.

**Undo:** Voicebox is its own process in its own folder. Removing the fetch script, the start and stop code and the Settings → Voice card removes it, and nothing in the core environment changes, which V1's torch/CUDA check proves.

## ED13 · Only models cleared for commercial use are offered by default

**Date:** 2026-11-05 · **Steps:** V1, and Phase 7

**Context:** Creators monetise their videos. Many audio models have non-commercial weights (CC-BY-NC and similar), and a licence can change: OmniVoice went from Apache-2.0 to CC-BY-NC on 2026-07-03. Clips Kitty's current English dubbing voice, Piper `en_US-lessac-medium`, was trained on a dataset under a research licence, so its use in monetised videos is unclear.

**Decision:** Offer Kokoro, Chatterbox Turbo and Qwen3-TTS for voice, and in Phase 7 ACE-Step 1.5 for music. Stable Audio 3 comes only behind a click-through that shows its licence (for organisations under US$1M in revenue, after registering, with "Powered by Stability AI" shown) and records acceptance. MMAudio, MusicGen, F5-TTS, XTTS and OmniVoice are hidden as non-commercial. The Piper voice is left as it is in Phase 2, unless Colin says otherwise.

**Why:** A creator should never find out after posting that a voice or a sound can't be monetised.

**Rejected:** Listing every model with a warning; leaving licence checks to each creator.

**Undo:** Showing a hidden model again is a change to the model list; nothing else depends on it.

## ED14 · audio.cpp later, only for sound effects and music

**Date:** 2026-11-05 · **Phase:** 7 (nothing in Phase 2)

**Context:** Voicebox makes speech only, with no sound effects or music. audio.cpp (Apache-2.0) is a C++ ggml runtime for 143 audio model families, with an OpenAI-style server and memory controls (`lazy_load`, `max_loaded_models`, `idle_unload_ms`), and no Python or PyTorch. Its Windows builds are 32 MB (CPU), 84 MB (Vulkan) or 279 MB plus 575 MB (CUDA). It is young (created 2026-06-23), but Unsloth Studio and VoiceStudio already depend on it.

**Decision:** In Phase 7, audio.cpp becomes the optional engine for AI sound effects and music only, installed through the optional-components framework: sizes, licences, SHA-checked downloads, its own process and environment, and an uninstall that never touches projects. Models: ACE-Step 1.5 for music, Stable Audio 3 behind its click-through (ED13), and non-commercial models hidden.

**Why:** It adds sound generation without a second PyTorch, in its own process, like Voicebox.

**Rejected:** audio.cpp for voice as well (the research's first pick; Voicebox covers voice, ED12); Soundbox as the front end (three weeks old, no authentication, and it pulls Stable Audio from an ungated mirror that skips Stability's click-through); a Python environment per model.

**Undo:** Nothing ships in Phase 2, and in Phase 7 it is an optional component that uninstalls without touching projects.

## ED15 · Electron is upgraded before the new editor reaches users

**Date:** 2026-11-05 · **Step:** E

**Context:** Clips Kitty runs Electron 31.7.7 (Chromium 126, Node 20.18), which is end-of-life and was last patched on 2025-01-14; the current release is 45.2.3, on Chromium 156. Running the HyperFrames tools through `ELECTRON_RUN_AS_NODE` needs Electron 35 or later (Node 22).

**Decision:** Step E upgrades Electron to the current release, with electron-builder, and re-tests the MSIX package, before the new editor (step 8) reaches users. It needs Colin's one-line OK first. Its proof is a full UI click-through.

**Why:** The new editor brings WebAssembly, workers and heavy media code into the renderer, and running that on an unpatched Chromium is avoidable risk (inferred).

**Rejected:** Staying on Electron 31; folding the upgrade into step 8, which would mix a platform change and a new feature in one pull request.

**Undo:** Revert `ui/package.json` and `ui/electron-builder.yml`.

## ED16 · How each step is built, checked and landed

**Date:** 2026-11-05 · **Steps:** all

**Context:** Phase 2 is 15 steps over many weeks. A builder that checks its own work misses things, and the checks share one engine port (8765), one GPU and the clock.

**Decision:** Nine agents: Research (read-only, only when a step has an open question), Architect, Engineering, Black-box tester, QC and regression, UX and accessibility, AI editing and motion graphics (Phases 5 and 6), and Colin's two additions, the Performance engineer and the Promo producer.
- Inside a step: Engineering, then QC, the tester, Performance and UX, one after another; then Colin's click-through; then the Promo producer. Colin's own Clips Kitty is closed during check runs, and Performance always runs alone.
- Failures go back to the right agent: missing information to Research, a design conflict to the Architect, a broken feature to Engineering, a slowdown to Performance and Engineering, poor usability to UX. After at most 2 fix loops, Colin decides.
- A builder never signs off its own work, and results are reported as measured.
- The black-box tester gets only the built app, USER-GUIDE.md, scenarios and media. It runs from outside the repository against `dev:web` plus an engine on scratch data, never Colin's profile, and is told never to read the source; that is enforced by instruction, not by a sandbox.
- Each step becomes one pull request, merged when Colin says so after his click-through and once its CI is green. Packaged builds happen only on his go, and one packaged black-box pass marks the end of Phase 2.

**Why:** Independent checks catch what a builder misses, serial runs keep timings clean, and UI work isn't verified until someone has used it.

**Rejected:** One builder and one reviewer (no black-box, performance or UX checks); agents running in parallel (clashes over the port, the GPU and timings).

**Undo:** The graph can be slimmed for any step; `state.json` records what each step needed.

## ED17 · A promo video for each shipped feature

**Date:** 2026-11-05 · **Steps:** every user-visible step · Colin's addition

**Context:** Colin wants each feature shown as it ships, and wants creators to be able to make voice-overs themselves.

**Decision:** Both.
1. After Colin's click-through of each user-visible step, the Promo producer makes a narrated promo of the feature, in 9:16 and 16:9, from his one-line instructions; without them, it drafts from USER-GUIDE.md and he can veto. The script runs 20–45 seconds, hook first. Footage is recorded from the dev build with Playwright's `recordVideo` and a scripted cursor. The voice is Kokoro `af_heart` through the shipped Voicebox, or another Voicebox voice he picks; until step V1 lands, a small promo kit kept outside the repository does the narration. Each promo is finished with word-timed captions in the app's own caption styles and a CC0 music bed. From step 8 on, each promo is assembled as a Clips Kitty project, so the app makes its own promos. The output (`promo_9x16.mp4`, `promo_16x9.mp4`, `script.txt`, `captions.srt`, `post.txt`) is kept outside the repository. Checks: −16 LUFS, true peak ≤ −1 dBTP, a Whisper check that the voice says the script, and captions within ±1 frame.
2. A Voice-over tool in the editor, for every user (step V2).

Rules: footage is only Colin's own or CC0 sample video, never other creators' streams; no other companies are named; TikTok copy carries no "Clip from" text or links; nothing is posted or uploaded without Colin's go.

**Why:** Every feature arrives with a demo, and making the promos exercises the voice service and, from step 8, the editor itself.

**Rejected:** One promo at the end of Phase 2; stock footage or other creators' clips.

**Undo:** The promos live outside the repository, and dropping the Promo producer from the graph stops them.

## ED18 · Stop after Phase 2

**Date:** 2026-11-05 · Colin's decision

**Context:** The whole brief, Phases 2 to 7, is many months of work. Phase 2 alone closes all seven issues, and is estimated at about 9–13 weeks, set mostly by click-through rounds.

**Decision:** The run stops after Phase 2. Phase 3 and later start only on Colin's go. Whether the new editor becomes the default instead of beta is his call after Phase 2.

**Why:** It ships the editor users asked for, then lets Colin judge it before more is built.

**Rejected:** Running straight on into Phases 3 to 7.

**Undo:** Colin's go starts Phase 3.

## ED19 · Undo is the exact difference between two documents, and a project's revision number only goes up

**Date:** 2026-11-12 · **Steps:** 5, 8 · taken while building step 5

**Context:** The plan asks for inverse operations that undo a change exactly, a compare-and-swap on the project's head with 409 for a stale edit, and undo and redo on the server. Two things were open: what an inverse is for an operation with no natural opposite (a ripple delete, a batch, a frame-rate change), and what number a caller compares.

**Decision:**
1. The inverse of any call is one internal `restore` operation that holds the difference between the document after and the document before (`video_editor/project/patch.py`). Redo is the same the other way. Only the store's undo and redo may apply a `restore`; sent by a caller, it is refused.
2. A project has two numbers. `head` goes up by one with every change, undo and redo included, and is what `expected_revision` is compared with. `cursor` is the place in the undo chain. The head document is kept on the project's row; revisions keep the differences, with the whole document on every 25th.
3. Undo doesn't cross a change of the document's format: after an app update that changes the format, a project's history starts again at its first change.

**Why:** A difference is exact for every operation by construction, which is what "byte for byte" needs, and it stays small. With one number, an undo followed by a new edit would give a different document the number an earlier one had, and a caller holding the old number would pass the check.

**Rejected:** A hand-written opposite for each operation (about 20 chances to be subtly wrong, and none for a batch); one revision number for both jobs; rebuilding the head document from snapshots on every read (slower, and the 40 ms target is per operation).

**Undo:** The inverse format is inside `ops.py` and `store.py` only. A later step can add hand-written inverses for display without changing what is stored.

## ED20 · A project outlives its clip, and a clip with a project is never made again

**Date:** 2026-11-12 · **Steps:** 5, 8 · taken while building step 5; Colin can overrule at step 8's click-through

**Context:** `projects.clip_id` is not a foreign key, because a re-render gives a clip a new id. That leaves open what happens to a project when its clip is deleted, and whether opening a clip twice makes two projects.

**Decision:** A clip has at most one project; opening it again gives the same one. A re-render moves the project to the clip's new id (`StateDB.follow_clip_id`). Deleting the clip or its video leaves the project, with no clip. Make clips again counts a clip that has a project as edited and keeps it, even when nothing in the project was changed yet.

**Why:** A project is a person's work. Losing it because the clip was culled, or having Make clips again replace a clip someone has open in the editor, would both be data loss.

**Rejected:** Deleting the project with its clip (simplest, but silent loss); a foreign key (breaks every re-render); keeping only clips whose project has a change (a clip open in the editor would be deleted under it).

**Undo:** One line each in `StateDB.delete_clip` and `delete_video`, and one in `core/remake.py`.

## ED21 · Going back to today's render keeps what didn't change, and says when it can't

**Date:** 2026-11-12 · **Steps:** 5, 7 · taken while building step 5

**Context:** Branch A (ED1) sends a project that still fits today's render options to the existing render job. The plan asks that a project made from a clip and turned back gives the clip exactly.

**Decision:** `projection.to_clip` reads the project as pieces (window, cuts, speed, sound, hook, music, look, captions, mutes, watermark) and reads the clip it came from, imported again, the same way. A piece that reads the same in both keeps the value the clip was saved with; only a piece that differs is written new. Anything today's render has no words for (an overlay, a second sound, a bleep, a transform, items at different speeds, a different canvas) makes the answer "needs the compiler", with the reasons in plain sentences. The export preset never decides the branch.

**Why:** Writing the whole set of render options again from the project would change clips nobody edited (rounding, key order, unknown keys), and Branch A's promise is output identical to now.

**Rejected:** Rebuilding all render options from the project; a flag set by operations ("touched"), which an undo would leave set.

**Undo:** Step 7 can replace `projection.py` without touching the document or the store.

## ED22 · What an operation may put in a project

**Date:** 2026-11-12 · **Steps:** 5, 7, Phase 5 · taken while fixing step 5's review (5-D15, 5-D16, 5-D22, 5-D26)

**Context:** The operations are one set for the editor's window, the AI and MCP. The window's JavaScript can't send `-0.0`, NaN or Infinity; a script or a model can. Some objects are kept as they are given (an item's `extra`, a watermark, a Gaming layout), and the review showed three things getting in through them: a second zero that broke exact undo, values that can't be saved (a 500), and a file path in a watermark key the render doesn't read.

**Decision:** (1) The document holds one zero: every number an operation checks is stored as `0.0`, never `-0.0`; where an object is kept as given, the difference between two documents compares floats as they are written, so undo is exact there too. (2) What can't be saved (NaN, Infinity, half of a character, anything that is not JSON) is refused before anything is applied, by operation and field; it is not cleaned. (3) A watermark set by `set_branding` takes only the keys `video_editor/watermark.py` reads. A watermark that came with an imported clip is kept as it was. **The export compiler of step 7 must read a watermark's image only through `image_asset`, by safe name, and no path from any other key of any object kept as given.**

**Why:** Undo that gives back the very bytes is the contract every later step builds on (ED19), and the AI and MCP are the callers most likely to send the odd values. Refusing rather than cleaning keeps the history (which stores the operations as sent) writable and tells a caller with a bug that it has one. The plan's rule is that a path never comes from the client.

**Rejected:** Turning `-0.0` into `0.0` everywhere, including inside objects kept as given (an imported clip must come back exactly as it was saved, ED21); dropping the unknown watermark keys silently (a caller would think they were saved); checking every string in every kept object for something that looks like a path (guesswork).

**Undo:** `ops._savable`, `ops.WATERMARK_KEYS` and the `+ 0.0` in `ops._in_range` and `ops._numbers` are each a few lines; `patch._same_types` is one line.

## ED23 · One voice layer: Kokoro built in, Voicebox and VoiceStudio optional

**Date:** 2026-11-12 · **Steps:** V1, V2 · Colin's choice, replacing ED12's engine

**Context:** ED12 chose to ship Voicebox's voice server inside Clips Kitty. Before building it, a spike ran that server on Colin's PC. It works, but shipping it costs far more than planned:
- 490 MiB on disk, and it unpacks 1.24 GiB into the temp folder at every start, which takes 16 to 43 seconds before it answers;
- 1.4 to 1.9 GB of memory;
- the program is not signed, and its switch for closing with the app (`--parent-pid`) does nothing in v0.5.0;
- it gives no word timings.

The Kokoro voice that Voicebox uses by default was already in the repo as an optional script (kokoro-onnx, MIT; the model is Apache-2.0), and runs on the ONNX Runtime the engine already ships.

**Decision:** In Colin's words: "make it work with the app on all functions and make it so people can use voicebox or voicestudio as they please. but ship with the lighterweight kokoro as base and people can choose to upgrade for optional features".
- One voice layer (`voice/`) that everything that speaks goes through: dubbing, the narration script, and `POST /voice/speak` for the voice-over tool (V2) and the promo kit.
- **Built in:** Kokoro, inside the engine, on the CPU, default voice `af_heart`, for the languages measured to come out right through kokoro-onnx (English, Spanish, French, Hindi, Italian, Portuguese). Piper stays, unchanged, for the other dubbing languages. Kokoro's two model files are fetched at build time, checked by SHA-256 and bundled; nothing is downloaded while the app runs.
- **Optional, the person's own copy:** Voicebox (`127.0.0.1:17493`), VoiceStudio (the address in its `backend.json`, else `127.0.0.1:3900`) or any other local voice app with OpenAI's speech route, picked in Settings → Voice. Clips Kitty never ships, downloads or starts them, uses only voices that already exist there, and only ever contacts this PC.
- When the chosen voice app is closed, the job stops and says why. The built-in voice stands in only if the person allowed it.
- Word timings come from the app's own Whisper, run on the finished speech.

**Why:**
- Size and speed: about 356 MiB added and 2.6 s to the first sound, against 490 MiB plus 1.24 GiB of temp and 16 to 43 s. The speaking speed is the same (3.5 times faster than realtime on Colin's PC).
- No second program to start, stop, sign or explain to the Store.
- Licence: the built-in voice is cleared for videos that earn money (Apache-2.0 model, MIT runtime). ED13 holds: a voice that is not cleared carries a "not for monetised videos" tag, and nothing non-commercial is bundled.
- People who want more voices or cloning get them from the app they chose, and its licences stay in that app.

**Trade-offs accepted:**
- The built-in voice speaks six languages. Japanese and Chinese need dictionaries that are a large new dependency, so Chinese stays on Piper and Japanese has no built-in voice.
- No voice cloning inside Clips Kitty.
- VoiceStudio changes about 170 times a day, so only its standard speech route is used, and it was tested against a stand-in server only.
- For a local voice app Clips Kitty can't identify, no licence tag is shown, and the tooltip says the licence is the person's to check.
- With no voice picked, a dub in the six languages now uses the built-in voice instead of Piper's default. A dub with a Piper voice picked is exactly as before (snapshot test).
- The text front end (phonemizer and eSpeak NG) is GPL-3.0, as Piper's already is; that fits an AGPL-3.0 app and is named in NOTICE.

**Rejected:** Shipping Voicebox (ED12; the costs above); shipping VoiceStudio (ED12's reasons stand); starting the person's Voicebox for them (its lifetime is theirs); calling the voice apps from the app's window (both refuse a browser's origin, so the engine calls them, with no Origin header); adding the Japanese and Chinese dictionaries now (248 MiB and 30 MiB in Voicebox's bundle).

**Undo:** The layer is its own package, and dubbing's Piper path is untouched behind it. Removing `voice/`, `server/voice_api.py`, the Kokoro entries in `clips-studio.spec` and `requirements.txt`, and the Settings → Voice card puts dubbing back on Piper alone.

## ED24 · The editor is Clips Kitty Editor, built from Concat, inside the Clips Kitty window

**Date:** 2026-11-16 · **Steps:** K1 to K8 · Colin's choice, replacing ED1's "borrowed, not shipped" and the hand-built editor of steps 8 and 9

**Context:** Steps 8 and 9 built an editor by hand inside the app (PRs #243 and #246, unmerged). At his click-through Colin asked for "a full editor like Concat, Premiere and CapCut… meant to edit full videos too", and decided: "take concat its opensource as the plan… dont make a copy", "i want my branding overtop concat with my app. it can't just be an api endpoint it needs to ship with the whole app for windows", "i want it in my app not a 2nd window that opens".

**Decision:**
- The editor is a fork of Concat (Rust, AGPL-3.0-or-later, the same licence family as Clips Kitty), named **Clips Kitty Editor**, in its own repository. Concat's trademark rules ask a modified build to carry another name; Concat is named only in the credit "Based on Concat" and in the notices.
- It runs **inside the Clips Kitty window** as a child window (the fork's `--embed` option), as the app's Editor page. It ships with the Windows app (bundling is step K3).
- Clips Kitty **builds the project** for a clip, a whole video or a file through the editor's own automation, with no window, and the editor's **exports come back** into Clips Kitty.
- Today's clip editor stays, as "Quick edit" where a name is needed.
- The hand-built editor's code is not continued. Its rule that nothing of a person's is lost silently is kept (ED25).

**Why:** a finished multi-track editor (timeline, captions, audio, export) in place of one built by hand; the licences fit; the person never leaves the app.

**Trade-offs accepted:** a second program of a few hundred MB (to be measured in step K3) and its own graphics use; the editor is Windows only; a child window is always drawn over the web page, so the app hides the editor while something of its own must show there.

**Rejected:** a second window; an editor in the web page (porting Concat's interface would be the hand-made copy he did not want); shipping Concat unmodified under its own name.

**To undo:** the Editor page and every way in exist only while `GET /editor/status` says the editor was found. Without the editor's folder the app looks and works as before.

## ED25 · A clip's editor project stands on a framed, caption-free render, and an editor export is known by its folder

**Date:** 2026-11-18 · **Step:** K2 · Supersedes ED20 and ED21 for the new editor

**Context:** The editor knows nothing of the AI's framing, and Clips Kitty's render knows nothing of the editor's timeline. Two questions follow: what video does a clip's project hold, and what happens to the clip when the editor exports it.

**Decision:**
- **The project's video** is the clip's whole window rendered once with the AI's framing, layout, colour and sound levelling and nothing else: no captions, no cuts, no hook, no music, no watermark, no end card. The clip's cuts, speed, sound levels, captions, hook, music and watermark are put on the editor's timeline as items, so each can be changed or taken back there. What the editor has no equivalent for is said plainly when the project opens (the lit-up word, caption animations, music dipping under speech, re-aiming the framing).
- **Projects are folders,** `data/editor/projects/<id>/` with a manifest; no database table.
- **An export of a clip's project becomes that clip's video** only when the clip is still on the file the project was made from. A clip changed in Quick edit meanwhile keeps its file, and the export is kept beside it. An export of a whole video or a file goes to the export folder from Settings as "<name> - <date>".
- **An editor export is known by where its file is** (`data/clips/projects/`), with or without its project. The clip's window and options stay the AI's, so everything that would re-make the clip from them asks first (Quick edit's Apply, the chat edit, the caption save, "Apply edits & upload", Export in other languages) or keeps the export (a forced run, story reels, Make clips again, Storage clean-up).
- No end card on anything the editor exports.

**Why:** the framing is the part of a clip only Clips Kitty can make, so it is made once and kept; everything else is better edited in the editor than baked in. The folder rule needs no table and still holds after a project is deleted.

**Trade-offs accepted:** one render of the clip's window before the editor opens the first time (82 s for a 45 s clip, measured cold on Colin's PC); the framing can't be re-aimed in the editor (step K7); captions arrive as plain text.

**Rejected:** `multilingual.burn.clean_base` as it is (it keeps the cuts, hook, music and watermark in the picture); a table of exports (a migration, and a deleted project would un-protect its export); changing the clip's window and options to describe the editor's result (nothing can re-make it from them).

**To undo:** the guards are one path comparison that says no for every clip the editor never exported; without the editor nothing is ever written to `data/clips/projects/`.

## ED26 · The app shows the editor as a child window it never kills, hidden whenever the app draws over it

**Date:** 2026-11-18 · **Step:** K2 (the app's half)

**Context:** Clips Kitty Editor is a native window laid over the Editor page. A native window is always drawn above the web page, holds its own unsaved work, and has its own API with a token.

**Decision:**
- The main process alone starts and stops it: detached, no pipes, no console window, the token only in its environment, a loopback port chosen at each start. The page names a project by its id; every path comes from the engine.
- It is closed only with `host.close` (it saves first), on every quit route (before-quit after the existing question, window-all-closed, the updater's install), and never killed. One whose window is gone (an app crash) saves and leaves by itself.
- Anything of the app's own drawn into the editor's area holds a cover while open (`useEditorCover`), and the editor is hidden for that time; questions over the editor are native dialogs; notices go in the page's 36 px strip; clip approval cards wait behind a "Clip approvals" button while a project is shown.
- Every way in switches to the Editor page at once and shows a project's making there.

**Why:** nothing of the person's is lost when the app quits or crashes, the token never reaches a web page or a log, and nothing of the app is ever hidden under the editor.

**Trade-offs accepted:** a scale change restarts the editor (about 2 s); the editor is polled every 500 ms while shown; the quit question is in English, as the other main-process questions are.

**Rejected:** an in-page modal over the editor (it would be drawn under it); killing the editor on quit (unsaved work); reading exports from the editor's stdout (lost when the app is gone).

**To undo:** `GET /editor/status` saying "not found" hides all of it; the main process starts nothing until a project is opened.

## ED27 · UX round on K1 and K2: a name of its own, panels that fit, and the export's destination said before it is made

**Date:** 2026-11-19 · **Step:** K1 and K2 (UX fix round)

**Context:** the UX pass found that every project opened with the timeline on empty lanes, that a plain "Edit" beside "Edit this clip" opened a different editor, that the editor's panels were cut off in the app's 1056-pixel area, that the Export sheet did not say where the file would go (and the file was not called what it showed), and that export notices never reached a screen reader.

**Decision:**
- The fork, embedded only: each time a project opens, the timeline shows the lanes from the top one with something on it, until the person scrolls; the panels start as library, picture and inspector across the top with the timeline the whole width under them, the library and the inspector at least 300 px where the area allows, and the arrangement is not remembered between embedded starts; `--export-note` / `host.open_project {destination}` put a read-only line under the name in the Export sheet.
- The app: the new ways in say "Open in editor"; the old editor keeps its words, and the app's sentences call it "the Clip Editor" until the owner says whether its button becomes "Quick edit". The destination line is the page's (translated). A whole video's or a file's export is called what the sheet says, which starts as "<name> - <date>". Notices are in live regions that are always there; an export is announced by quarters; the percent shown never goes back.

**Why:** the first look at a project must show what was carried over; two buttons with one word must not open two things; nothing of the editor may be cut off at the app's default size; what will be exported and where it goes is clear before the export.

**Trade-offs accepted:** an embedded editor forgets panel sizes a person dragged when it restarts; the app's sidebar is not narrowed (the panels fit without it at 1280 x 860); an editor build older than 211c751d cannot be started by this app (it does not know `--export-note`).

**Rejected:** renaming "Edit this clip" to "Quick edit" (needs the owner's OK); collapsing the inspector under 1300 px (captions are edited there).

**To undo:** the fork's changes are behind `embed::active()`; the app's are the labels, `showOptions` in lib/editor.ts and the live regions in pages/Editor.tsx.

## ED28 · A clip's base video is made from the clip's own framing, and kept

**Date:** 2026-11-19 · **Step:** K2 (first-open speed) · Changes how ED25's base is made, not what it is

**Context:** the first "Open in editor" on a clip took 63 s on the owner's PC (24.8 s clip, 1080x1920 60 fps), 62.5 s of it the engine re-running the whole render for the caption-free base: the cut, YOLO and TalkNet tracking (about 31 s), and a frame-by-frame crop through Python with a CPU encode (about 24 s; this PC's driver gives Clips Kitty's FFmpeg no NVENC). All the tracking finds again is where the crop sits in each frame, which the clip in the Library already shows.

**Decision:**
- For a clip framed by the tracker's moving crop (the 9:16 crop filling the screen, or the face-safe 4:5 crop under the apps' top bar), the crop is read back from the clip's own file (`kitty_editor/fastbase.py`) and the base is one FFmpeg pass over the source: crop, layout, colour filter, loudness, untagged BT.601 colours as the standard render's piped frames give, encoded with x264 `ultrafast` CRF 16 (as good as the renders' veryfast CRF 20 by SSIM against the unencoded picture, twice as fast, about four times the size; the editor encodes again on export).
- Anything else (letterbox, split screen, podcast, gaming, sport, Vertical Live, Longform, a clip with its own edit) and any frame that does not match is made the full way, as before, with the pipeline's own encoder and the tracking models loaded while it cuts.
- The base is kept with a key (window, framing options, the clip's and the source's files, the tracking settings) and reused by a rebuild or a new project of the same clip until one of them changes.
- The page's bar follows the base's own progress, never goes back, and says at most 99% until the editor shows the project.

**Why:** 15 s instead of 63 s to the editor showing the clip, with the same crop to a tenth of a pixel (SSIM 0.98 to 0.99 against the old base at 5 points, the same loudness); and the base now follows the clip as it is in the Library rather than a fresh guess: re-tracking can choose differently (a faster encode of the full render's cut turned this very clip's face-safe layout into the plain crop).

**Trade-offs accepted:** the 10 s target is not met on this PC: reading the crop decodes the clip and the source once each (about 3 s, CPU-bound) and the base is one CPU encode of every frame (6 to 7 s), plus the editor's own start (about 2 s); a 9:16 clip is faster (its video in about 7.7 s). A base is about four times the size it was (58 to 83 MB for the three 21 to 25 s test clips, against 15 to 22 MB). A clip with a strong vignette or sharpen in its colour may not match and is made the full way.

**Rejected:** a faster encoder setting for the full render (it changed the tracker's framing); re-running tracking with fewer samples or without the shot check (a different framing from the clip's); the editor's own FFmpeg with NVENC (the owner's rule); making the base ahead of time for every clip (work and disk for clips never opened).

**To undo:** `fastbase.make` returning None sends every clip to the full render, which is the code before this change with only the models' loading moved earlier; `video.encoding.cpu_encoder_args` changes nothing outside its block.
