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

## ED24 · The editor's media addresses carry a token, so <video> and <img> can use them

**Date:** 2026-11-14 · **Steps:** 6, 8 · taken while building step 6

**Context:** The plan asks for the session header and an Origin check on every media route that changes something. The routes that read (the source, the proxy, the thumbnail sheets) are loaded by `<video>` and `<img>` elements, which can't send a header. Such an element on any web page sends no Origin either, so the Origin rule alone would let any page play, show and measure a person's videos through the engine.

**Decision:**
- A read route takes the session header (a `fetch()` from the app) or the media's own token in the address: `?t=` with an HMAC-SHA256 of the media id under the session secret, 128 bits.
- `GET /media/{id}/info` gives the addresses with their tokens.
- Every read route also refuses an Origin that is not this PC's own.
- Changing routes take only the header, with the same Origin check as the project routes, through the same helper.

**Why:**
- A page on another origin can't make a token: the secret is new at every start and never leaves this PC. Without a token it gets 403, whatever element it uses.
- A token names one media, so a leaked address opens that one file until the app restarts.

**Rejected:**
- The Origin rule alone, which lets any page play the files.
- Putting the session secret itself in addresses, where one leak opens everything.
- A cookie, which the packaged window (a page loaded from a file) can't set for 127.0.0.1.
- Short-lived tokens, which would break a long editing session's `<video>` mid-play.

**Undo:** `read_guard` in `server/media_api.py` is the whole rule; the token is only checked there.

## ED25 · Only the app's own folders are served, and import takes only a file on this PC

**Date:** 2026-11-14 · **Steps:** 6, 7, 8 · taken while building step 6

**Context:** Step 5 kept an imported clip's own music file in its project as `{"type": "file", "path"}`, a path anywhere on the PC. Step 6 says nothing outside the app's data folders may be reached whatever an id says. Import for editing is the one route that takes a path, and the app's other picked-file routes refuse network paths (`core/paths.py` `picked_file`). Asking Windows to open `\\server\share` can send the person's sign-in to that server.

**Decision:**
- The resolver serves a file only when, every link resolved, it is inside the data folder (the library folder for a free sound).
- A project's `file` source elsewhere answers 404 with a plain sentence. Step 8 offers to import it for editing, which copies it in by sha256.
- Import refuses `\\server\share` and device paths, as the other routes do. A network drive with a letter is accepted when it answers within 5 seconds, and refused in words when it doesn't. The file is only ever opened for reading.

**Why:** One rule for every id, which a test can check by trying to leave. The import route keeps the app's existing stance on network paths.

**Trade-offs accepted:** A clip made with music from elsewhere on the PC plays that music in the new editor only after it is imported. A file on a network share must be copied to the PC, or reached through a drive letter.

**Undo:** `_checked` and the `file` branch in `video_editor/media/resolver.py`; `check_path` in `video_editor/media/importing.py`.

## ED26 · Vertical Live downloads belong to their video, and a playlist is not media

**Date:** 2026-11-14 · **Steps:** 6, 7, 8 · taken in step 6's first fix round (6-D1, 6-D9, 6-D14)

**Context:** The review found three gaps in what the resolver and Storage take a file to be:
- A Vertical Live download is `<id>__vertical.<ext>` (`sources/vertical.py`). The resolver didn't find it, and Storage's survey took it for a download with no video, so Free up deleted it even while its video was in the library, and even when a project used it.
- A project's `{"type": "file"}` source could name any file in the data folder, the database included.
- A playlist (`.m3u8`) passed FFprobe, was stored as an asset of a few bytes, and made FFmpeg read whatever it named.

**Decision:**
- `video:<id>` is the plain download when there is one, else the Vertical Live one. Storage counts `<id>__vertical.<ext>` as the video `<id>`'s download, both for "in the library" and for the pin.
- A `file` source is served only when its name, and the file it leads to, end in a sound or video extension.
- FFprobe formats that are only a list of other files (`hls`, `concat`, `dash`, `sdp`, `rtp`, `rtsp`, `tee`, `ffmetadata`) are refused by `probe.describe`, so by every route that reads media, and FFprobe and this step's FFmpeg read local files only (`-protocol_whitelist file`).

**Why:** The resolver finds a video's file the way the pipeline and Make clips again do. The other two keep "only media in the app's own folders" true for what a client can write.

**Trade-offs accepted:**
- Fixing the Vertical Live survey changes what Free up does for everyone, not only for projects: such downloads now count as kept sources. That is an older fault, and the change only ever keeps a file (CHANGELOG, Fixed).
- A project can't yet say it means the Vertical Live copy of a video that has both.

**Undo:** `_download`/`VERTICAL` in `video_editor/media/resolver.py`, `_video_of` in `core/housekeeping.py`, `FILE_SOURCE_EXTS` in the resolver, `INDIRECT_FORMATS`/`LOCAL_ONLY` in `video_editor/media/probe.py`.

## ED27 · Branch A replaces a clip's options, never shifts them again, and renders the row's hundredths

**Date:** 2026-11-14 · **Steps:** 5, 7 · taken in step 7, part A

**Context:** Step 5's projection gives Branch A a `start`, an `end` and the clip's complete render options, with the edit, the caption lines and the speaker fixes already counted from that `start`. Today's re-render job (`server/jobs.py` `_rerender_clip`) merges the options it is sent into the saved ones and, when the window moved, shifts those three keys from the row's old start. Doing both to Branch A's options would put every cut, mute and caption in the wrong place. Also, the clip row keeps its window to a hundredth of a second, while projection's times are in microseconds.

**Decision:**
- A new payload key, `render_opts_replace`, makes the job use the sent options as they are: no merge, no shift of `edit`, `caption_lines` or `speaker_edits`. Everything else the job does stays as it is.
- The window is rounded to the hundredth, so the file is made from the window its row holds. The options' times are kept as sent: at most 5 ms from the rounded start.
- Seconds a clip gained are captioned from the transcript as Apply captions them. The head is filled only before the first sent line and before the row's old start, so nothing is captioned twice.
- Speaker fixes that end before a later start are kept, as today's job keeps one past the end. A test proves the caption render ignores them.

**Why:** The projection already did the arithmetic once, so the job must not do it again. Rounding the window rather than the options keeps every option exactly what projection worked out, and keeps the row and the file in agreement.

**Rejected:** rounding inside the projection (a single kept part would then gain a few-millisecond cut at its start and render differently); shifting the options by the rounding (the "second shift" step 5 warned of); dropping speaker fixes outside the clip (today's job keeps them on purpose).

**Undo:** the `replace` branches in `_rerender_clip`, and `head_until` in `video/captions.py` `refit_caption_lines`.

## ED28 · A project's export: Branch B replaces the clip's file and keeps the clip; V1 goes through a mezzanine

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, part B

**Context:** Step 7 makes a project into a finished video. A project made from a clip has to land somewhere the person finds it, without losing the clip's place in the library, its posts or its title, and without ever losing the file it had. The compiler also has to join hundreds of cut pieces exactly, in sound and picture, and run on Windows' command-line limit.

**Decision:**
- Branch A (the project still projects to today's render options, the preset is the clip's own) is today's re-render, run on the editor lane: Apply's result, a new clip id, the project following it.
- Branch B writes `data/clips/projects/<project id>-r<revision>.mp4`. For a project whose clip exists, the clip's row then points at that file (same id, title, posts and render options) and only then is its old file deleted. A project with no clip gets the file and its `project_exports` row only.
- An export is always of the project's current revision (409 for another).
- V1 is cut in chunks to a mezzanine (x264 veryfast CRF 12, no B-frames, one timescale) and joined by stream copy; its sound goes beside it as sample-exact PCM WAV; the final encode is the only visible generation. An AI-framed run goes through `core.framing.frame_intermediate` (the cropper's own encode stays until step 11), with its tracking kept in the media cache.
- A fitted manual picture's pad is a new `framing.pad` key: `"blur"` (default) or `#RRGGBB`.

**Why:** Replacing the file keeps everything the library and publishing hang off the clip's id, as Apply does; writing the new file first and deleting the old one last means a failure at any point loses nothing. WAV beside the mezzanine avoids the AAC priming that would add up over hundreds of joins.

**Rejected:** adding a new clip per export (the library fills with near-copies, and posts stay on the old one); a lossless mezzanine (ten times the disk); AAC in the mezzanine (drift across joins); exporting an older revision by number (an undone state can't be told from it).

**Trade-offs accepted:**
- After a Branch B export the clip's saved render options still describe the clip as it was: Apply from the old editor would render that again (step 8 decides what the old editor shows).
- AI items get one more visible-quality encode (the cropper's) than manual ones until step 11.

**Undo:** `_register_b` in `server/compose_api.py` (the file and the row), `mezz_args` and `_cut_stage` in `video_editor/compose.py`, `pad` in `video_editor/project/ops.py` `_framing`.

## ED29 · Captions are mapped word by word through the timeline; the tail pad is an L-cut

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, part C

**Context:** Captions stay in the source's time in a project (step 5), so the export compiler has to place them on the program's timeline through cuts, repeats and speed changes. Today's edits retime whole lines and leave the word highlight's times in the old timeline (plan problem 5). Separately, any cut drops the 0.4 s pad a clip's render adds after its last word (plan problem 4), because the keep ranges are clamped to the unpadded clip.

**Decision:**
- Every word of a caption line gets its source time (the transcript's when the line lines up with it, else the line's time shared out by word length). A word is kept where its middle plays, so a word in a cut is dropped and a line split by a cut becomes a caption on each side. Times are mapped through the span the word plays in, speed included, and the words' own times go along to the word highlight and karaoke.
- Where a run of V1 ends in the middle of its last word, the word's sound goes on past the cut, up to its transcript end plus 0.4 s, never into the next word, the video's end or a mute. It fades out under the next item's start, or under the end card for the program's last word. The picture and the timeline don't change.
- Branch B only. A plain run and Branch A render exactly as before.

**Why:** Word-level mapping is the only rule that keeps the highlight on the word being said after a cut or a speed change, and it needs nothing new in the document. An L-cut keeps the word without moving anything after it, so the export still matches the editor's timeline and every caption, overlay and sound stays where the person put it.

**Rejected:** mapping lines by their start and end (a line across a cut would show cut-out words); lengthening the run's last item by the pad (the program would no longer be what the editor shows; every later item would move); a tail without a transcript (nothing to tell a word's end from).

**Trade-offs accepted:** without an end card, the program's last word is still cut where the timeline ends (the plan's notes say so). A tail needs the captions' media to have a transcript.

**Undo:** `remap_captions`, `tails`, `tail_audio` and their callers in `video_editor/compose.py`.

## ED30 · The export's sound: loudness on the whole mix; a bleep is a 1 kHz tone

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, part C

**Context:** Today's render normalises a clip's own voice to -14 LUFS and then mixes the music in, so music can push a clip over the target. A project can hold any number of sounds, and the plan's proof is -14 ±1 LUFS with a true peak of at most -1 dBTP for the finished export. `set_audio_mutes` has a "bleep" mode that no render drew yet.

**Decision:**
- The export's mix (V1's own sound, the voices, the ducked music and the other sounds, summed with `amix normalize=0`) goes through the master volume and then today's `encoding.LOUDNORM` (single-pass, -14 LUFS, true peak -1.5 dBTP), when the project's `normalize` is on.
- Ducking stays step 4's: the music ducks under V1's own sound and the voice items, with the free tracks' setting for a library sound and the person's-own-file setting for anything else.
- A bleep mutes its stretch and plays a 1 kHz sine at -18 dBFS (FFmpeg's sine source's own level) times A1's volume in its place.
- An item slower than 1x has its sound read early and trimmed by atempo's measured lead (38 ms at 0.5x), so its sound starts with its picture.

**Why:** Measuring the whole mix is what holds every export at the target whatever is on it (measured: -14.0 LUFS on a 60 s project with music, effects and a bleep). A plain tone at a fixed level is the common bleep and is unmistakable, and -18 dBFS keeps it under speech before the loudness pass.

**Rejected:** normalising the voice only, as today (the mix can exceed the target); a two-pass loudnorm now (a measure stage per export; step 10, with the audio panel, decides); a beep at full scale (painful after loudness raises quiet clips).

**Trade-offs accepted:** single-pass loudnorm evens out a long fade a little. Bleep and mute edges fall on audio frames (about 21 ms), as a clip's word mutes do today.

**Undo:** `_final_audio`, `item_audio`, `audio_lead` and `BLEEP_HZ` in `video_editor/compose.py`.

## ED31 · The draft and the still: kept in the editor cache; a still is made in the request

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, part C

**Context:** The new editor (step 8) needs a quick render of the whole program (the draft) and a true picture of any moment (the still, "pause shows the truth"). Today's "Update preview" renders at full size inside the request (plan problem 6). The plan asks for a still in at most 600 ms, cached per revision and time, and for nothing of the editor's in `clips/` or `downloads/`.

**Decision:**
- The draft is the export's plan at 540 on the short side, ultrafast, with no preset and no end card. It runs as a task on the editor lane (`media_job` events, one per revision at a time) and is kept as `data/media_cache/project-<id>/draft/r<rev>-<facts key>.mp4` (the facts key added in fix round 1, 7-D6: a hash of the settings, logo and transcript it was made from); a newer draft replaces it.
- The still is one FFmpeg of one frame with every layer, made inside the request (not queued behind exports), kept as `data/media_cache/project-<id>/stills/r<rev>-<facts key>-<frame µs>.jpg` (the newest 200 per project).
- Both are read with the session header or a per-project token in the address (step 6's token, of `project:<id>`), and only for the current revision.
- An AI item's still uses the tracking an export or draft kept; without it, the middle of the frame, marked approximate and not kept.

**Why:** A still is a fraction of a second of work (0.17 to 0.20 s measured at 1080x1920), and a queue could put minutes of export in front of it. The editor cache already has the cap, Clear and orphan clean-up these need, and a draft or still can always be made again.

**Rejected:** stills on the lane (they would wait behind exports); tracking inside a still request (seconds of GPU for one frame); keeping drafts beside the clips (the clean-ups sweep there, and a draft is not a clip).

**Trade-offs accepted:** Storage's orphan clean-up removes a project's draft and stills (their folder has no source to match), so they are made again after it.

**Undo:** the draft and still routes in `server/compose_api.py`; `draft`, `stills` in `video_editor/media/cache.py` `PRODUCTS`.

## ED32 · A project's exports are never Storage's orphan clips

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, fix round 1 (7-D11)

**Context:** Storage's clean-up (`core/housekeeping.py`) deletes every `.mp4` under `data/clips/` that no clip row points at. A manual project's export (`data/clips/projects/<id>-r<rev>.mp4`) is recorded only in `project_exports`, so the clean-up deleted the person's finished video while its row still said "done". A deleted project's export has no row at all.

**Decision:** `data/clips/projects/` is never swept for orphan clips (`housekeeping.PROJECT_EXPORTS`). Only whole exports are ever moved there (each is made in the editor lane's work folder), so there is nothing half-made in it to free.

**Why:** an export is the person's finished work, like a clip; counting `project_exports` rows alone would still free a deleted project's export, which the person may have published or still want.

**Rejected:** counting only the paths in `project_exports` (the deleted-project case); deleting a project's exports with the project (deleting a project should not delete videos made from it without asking).

**Trade-offs accepted:** exports of deleted projects stay on disk until the person removes them; step 8 can offer that on the project's exports list.

**Undo:** the `PROJECT_EXPORTS` skip in `core/housekeeping.survey`.

## ED33 · A Branch B export never replaces a clip's newer file

**Date:** 2026-11-14 · **Steps:** 7 · taken in step 7, fix round 2 (7-D14)

**Context:** Branch B's register step repointed a clip's row at the export and deleted its old file without the clip's lock. If today's Apply had re-rendered the clip's file in place and not yet registered it, the export deleted Apply's fresh file, and Apply's new clip pointed at a missing file.

**Decision:** the register step holds `jobs.clip_lock` (as Apply and Branch A do; a cancel is checked while it waits, before the file moves). Under it the row is read again; the clip is repointed and its old file deleted only if the row still names the file the export started from, with the same size and modification time. Otherwise the newer render wins: the clip is left alone and the export stays a project export in `data/clips/projects/`, recorded in `project_exports`.

**Why:** the person's latest action on the clip (Apply) should not be undone silently by an export queued earlier; the export is still kept and listed.

**Rejected:** following the clip to Apply's new id and replacing that file (it would throw away the newer render).

**Trade-offs accepted:** in that race the export does not become the clip's file; the person sees it in the project's exports.

**Undo:** the lock and the `start_file` check in `server/compose_api.py` `_register_b`.

## ED34 · An unchanged clip's export runs Apply's 0.4 s past its end

**Date:** 2026-11-14 · **Steps:** 8 (part A) · step 7's open fault "Branch B is 0.4 s shorter than Branch A"

**Context:** Apply renders a clip 0.4 s past its window's end (`core/pipeline.py` `END_PAD`: Whisper's word ends run early), and drops the pad as soon as the clip has a cut. Branch B ended at V1's last out point, so an unchanged AI clip exported through the compiler was 0.4 s shorter than Apply's file, and its last word could be cut with the end card off.

**Decision:** the export carries the pad (`compose.end_pad_us`, `with_end_pad`), and so does the draft, which is the export's program (one tracking pass serves both; a still on the last item finds it): for a project made from a clip whose V1 is still one stretch of the clip's own video (no cut, one speed) ending where the clip's window ends, the export's last item runs `END_PAD` further (less where `stop_by` or the video's end says so; none for a whole-video clip). Looped music that filled the program fills it; captions are mapped through the unpadded timeline. The document, the editor's timeline and the stills keep the clip's window.

**Why:** that is exactly when Apply keeps its pad, so the two branches make files of the same length; the document stays the clip as it was saved (`projection(import(x)) == x` holds).

**Rejected:** carrying the pad in the imported item's length (the timeline would disagree with the clip's window, and every trim would move it); a sound-only tail without the end card (still 0.4 s shorter).

**Trade-offs accepted:** the exported file is 0.4 s longer than the timeline shows, as today's clips are longer than their window. Two compose-plan snapshots changed for it (`ai_gaming_layout`, `landscape_canvas_ai_items`).

**Undo:** `end_pad_us`/`with_end_pad` and their call in `compose.compile`; `build_ass(caption_doc=)`.

**Amended 2026-11-15 (8-D10, the round-3 review):** the pad covers the whole program, not V1 alone. `with_end_pad` also lengthens every item on the other video tracks and the text tracks that ends at the old end (on the same frame): overlays, pictures, stickers, logos, text and the hook end at the new end, as looped music already did. An overlay video runs on in its source as far as the source has frames, then holds its last frame (`overlay_graph` adds `tpad=stop_mode=clone` only when an item is longer on the timeline than its source span, which a plain item never is). An item that ends before the end keeps its end. Captions are unchanged: they follow Apply, which builds a clip's captions from the unpadded window, so a line that ends at the clip's end ends there. The draft and the export share the compile, so both changed. Without this, anything laid over the whole program vanished for the export's last 0.4 s. Rejected: dropping the pad whenever another track reaches the end (an unchanged clip with a logo laid over it would be 0.4 s shorter than Apply again, and could cut the last word). No snapshot changed for it (no snapshot case is a clip's project with items on other tracks); the argv snapshots and the golden check are identical.

## ED35 · Today's editor asks before rendering over a new-editor export

**Date:** 2026-11-14 · **Steps:** 8 (part A) · 7-D5

**Context:** after a Branch B export of a clip's project, the clip's file is the export while its row keeps the old window and render options. Today's Apply and chat edit would render that window again and silently replace the export with the clip as it was before the project's edits.

**Decision:** `POST /clips/{id}/render` and `POST /clips/{id}/ai-edit` answer 409 `{message, reason: "project_export", project_id}` for such a clip (`StateDB.project_export_of`: the clip's path is a Branch B `project_exports` row's), unless the request says `replace_project_edits: true`. Today's editor shows the message with "Open in new editor" and "Apply anyway".

**Why:** never quietly lose the person's edits, and never lock them out: with the switch off they can still choose to apply.

**Rejected:** routing today's Apply through the project (two editors writing one clip); refusing with no way through.

**Trade-offs accepted:** an MCP or script caller that re-renders such a clip gets the 409 and must send the flag.

**Undo:** `_project_edits_kept` in `server/api.py` and the `replace_project_edits` fields.

## ED36 · Deleting V1's first or last item moves the video's fade

**Date:** 2026-11-14 · **Steps:** 8 (part A) · step 5's edge-fade note

**Context:** an imported clip's fade in and fade out sit on V1's first and last items. Deleting that item dropped the fade with no word.

**Decision:** `delete_items` moves the deleted first item's fade in to the new first item, and the deleted last item's fade out to the new last item, only where that item has no fade of its own, shortened to fit, with a warning. It is part of the operation, so it is one undo step and the same for the editor, the AI and MCP.

**Why:** the fade belongs to the video, not to one piece of it; trimming a clip's edges is the most common edit.

**Rejected:** a warning only (the fade is lost); doing it in the UI (the AI and MCP would still lose it).

**Undo:** `_carry_edge_fades` in `video_editor/project/ops.py`.

## ED37 · A project made by hand starts in one call, with the whole video on V1

**Date:** 2026-11-14 · **Steps:** 8 (part A) · #144

**Context:** "Edit whole video" and "Import a video to edit" make a project from one media. As three operations after an empty project, the first undo steps would take the video back out.

**Decision:** `POST /projects` takes `media` (`video:<id>` or `asset:<sha256>`) and `insert` (default true): the project is made with the media and, by default, the whole of it on V1 framed fit and centred, at the video's own frame rate when known, before it is saved, so its history starts there. `GET /projects` lists every project as a summary (no document), the last changed first, a deleted clip's project labelled.

**Why:** one round trip on open, and undo never empties the project it was made for.

**Undo:** `_with_media` and `project_list` in `server/projects_api.py`; `store.summaries`.

## ED38 · The new editor opens over the app, its switch is a window preference, and the viewer pre-rolls cuts

**Date:** 2026-11-15 · **Steps:** 8 (part B) · #192, #144

**Context:** the editor needs a switch, a way to be reached from the Library, and a viewer that plays a program of cuts from the media themselves.

**Decision:**
- "New editor (beta)" is kept in localStorage like the window's other preferences (appearance), off unless it is `"1"`. It decides only what this window shows.
- The editor is drawn over the app as a full screen, loaded only when a project is opened (`React.lazy`), with the page underneath left mounted: Close returns to the same video and clip without any state to restore.
- "Edit in new editor" sits in today's clip panel under "Edit this clip" (an optional slot that is absent with the switch off), not on the clip cards, whose four corners are taken.
- Paused frames come from the engine (`still`) and are drawn on a canvas from `createImageBitmap`: the packaged CSP lets no `blob:` or `data:` address into an `<img>`, and the CSP stays as it is until step 9.
- At a cut, the waiting player starts early and hidden, by a measured start time, and its speed is nudged while it is silent so it reaches the cut when the outgoing item ends; the outgoing one holds its last frame if it is late. Measured: a part's last frame stays on screen at most about one frame longer than a normal frame.
- The phone app overlay is offered for 9:16 only (greyed out with a tooltip on other shapes): its drawing and the safe zones are measured on a 1080x1920 phone screen.

**Why:** nothing changes for anyone with the switch off; Close is exact; the paused frame is the export's; cuts play without a black frame.

**Undo:** `lib/newEditor.ts`, `App.tsx`'s `editorProject`, `components/editor/programPlayer.ts`.


## ED39 · The timeline is written from scratch: buttons for the items near the view, one canvas per track

**Date:** 2026-11-15 · **Steps:** 8 (part C) · #192, #144

**Context:** the timeline must hold 500 items at 60 frames a second, open a 300-item project within a second, and let a keyboard and a screen reader reach every item. The plan allowed adapting OpenCut classic or react-timeline-editor (both MIT).

**Decision:** written from scratch (nothing to credit). Each item near the view (a view's width each side, plus the selected and the dragged) is a memoised `<button>` with a spoken name; the pictures and waveforms are drawn on one canvas per track the width of the view, for the view only, from step 6's thumbnail sheets and peaks. A zoom is one render with its scroll set right after; wheel steps between two frames are one zoom. The main track has no magnet toggle (step 5's V1 is always gapless); "Snap" is the toggle. A drag, a split or a slider sends one operation when it ends, so it is one undo step.

**Why:** buttons give keyboard and screen-reader access for free; a canvas keeps the pictures cheap at any zoom. Measured: a 300-item project opens in 17 to 42 ms (production build); scrolling 500 items never drops a frame; zooming with all 500 in view drops about one frame in 14.

**Rejected:** adapting react-timeline-editor (its rows are divs per item with no keyboard model, and it brings its own drag library); drawing everything on one canvas (no keyboard or screen reader without a second, hidden model).

**Undo:** `components/editor/TimelinePanel.tsx`, `timelineMedia.ts`, `lib/editorTimeline.ts`.

## ED40 · Matroska with H.264 and AAC needs no proxy

**Date:** 2026-11-15 · **Steps:** 8 (part C), 6 · #192

**Context:** step 6 gave Matroska with H.264 or AAC (OBS's default recording) a proxy as a guess, for step 8 to try.

**Decision:** tried in Electron 31.7.7 (Chrome 126.0.6478.234, the app's window settings, a `file://` page): H.264 + AAC in `.mkv` plays and seeks as the same streams in `.mp4` do (seek to 4.5 s in 13 ms against 14 ms). `probe.PLAYS["matroska"]` now takes H.264 and AAC; 10-bit H.264, HEVC and other codecs in Matroska still get a proxy.

**Why:** a proxy costs a full encode of a long recording before the editor plays it smoothly.

**Undo:** `video_editor/media/probe.py` `PLAYS`; `tests/test_media_probe.py`, `tests/test_media_routes.py`.

## ED41 · The export dialog plays an export through its row, and shows it in its folder through the desktop app

**Date:** 2026-11-15 · **Steps:** 8 (part C) · #192

**Context:** the dialog must play a finished export or show it in its folder, and must never offer a file that is gone. The engine had no way to serve an export's file, and the page can't open a folder.

**Decision:** `GET /projects/{id}/exports/{row}/file` serves a `done` row's file of that project when it is there and is a video by its extension (the project's token, as the draft); each row carries its `url` only then. "Show in folder" is an IPC call (`show-in-folder`) that takes only an absolute path to an existing video file and calls `shell.showItemInFolder`; the browser build has no such call and shows no button.

**Why:** nothing from the request names a path; a row whose file went never gets an address.

**Undo:** `server/compose_api.py` `project_export_file`; `ui/src/main/index.ts` `show-in-folder`.

## ED42 · Today's editor asks in the API layer, so every way it renders asks the same

**Date:** 2026-11-15 · **Steps:** 8 (part C) · 7-D5

**Context:** part A made `POST /clips/{id}/render` and `/ai-edit` answer 409 `reason: "project_export"` for a clip whose file is a new-editor export. Today's editor calls the render route from five places (Re-render, captions, colour, the timeline editor twice) and the chat edit from one.

**Decision:** `api.rerenderClip` and `api.aiEdit` go through `renderRequest`, which sends the same body as before and, only on that 409, asks (`ProjectExportPrompt`, mounted once in `App.tsx`): "Open in new editor" (while the switch is on), "Apply anyway" (the same request with `replace_project_edits: true`) or "Cancel". The caller gets a plain sentence ("Nothing was applied.") when nothing was sent.

**Why:** one place for all six callers, and a clip the new editor never exported sends and gets exactly what it did.

**Undo:** `lib/projectExportPrompt.ts`, `lib/api.ts` `renderRequest`, `components/editor/ProjectExportPrompt.tsx`.

## ED43 · A clip's project remembers what the clip was, and asks when today's editor changed it since

**Date:** 2026-11-15 · **Steps:** 8 (QC fix round 1) · 8-D6

**Context:** the review found that "Edit in new editor" reopened a clip's project made before today's Apply changed the clip, and that exporting it put the old window back and deleted Apply's file, with no question. 7-D5 (ED35, ED42) guards the other direction.

**Decision:** `projects.clip_basis` (a hash of the clip's video, window and render options; not its title) and `projects.clip_basis_head` (the project's revision then). Opening a clip's project whose clip changed since: a project with no change of its own starts again from the clip by itself, as one revision Undo takes back; one with changes of its own opens as it is, and the editor asks "Start again from the clip as it is now" or "Keep the project". Exporting such a project answers 409 `reason: "clip_changed"` unless `replace_clip_changes`; the export dialog asks "Export anyway". After an export over the clip, the clip as it is then is the new basis, so the person is asked once.

**Why:** nothing of the person's is lost without a question, in either editor, and an untouched project simply follows its clip (the review's case) with no dialog. Rejected: a timestamp comparison (edited_at is also written by the project's own Branch A export), and deleting or detaching the old project (it would lose its exports' records, or be labelled as if its clip were deleted).

**Amended (2026-11-15, 8-D12):** `clip_basis_head` after a Branch A export is the revision that export rendered (the job carries it; `set_clip_basis(revision=...)`), not the head when the export ended: a cut made while the export waited or ran counted as "no change of its own", and the next Apply in today's editor made the project start again by itself, without a word. Now such edits make the person be asked (`clip_changed_since`); Branch B still records -1. No route starts a project again from its clip without the person being asked or told: whenever the engine does (`started_again: true`), the editor opens with "Started again from the clip as it is now. Undo brings back your changes.", and the project's own name (not part of the clip) stays across a start-again.

**Undo:** `core/state.py` (`PROJECT_COLUMNS`), `video_editor/project/store.py` (`clip_basis`, `set_clip_basis`, `start_again`), `video_editor/project/importer.py` (`clip_basis`), `server/projects_api.py` (`POST /clips/{id}/project`), `server/compose_api.py` (`clip_changed`, the export's 409), `components/editor/LibraryEntry.tsx`, `ExportDialog.tsx`.

## ED44 · A new-editor export is known by its file, and deleting a project cancels its exports

**Date:** 2026-11-15 · **Steps:** 8 (review fix) · 8-D11, 7-D5

**Context:** the review found that deleting a project (which deletes its `project_exports` rows) left the clip whose file is its Branch B export unprotected: today's Apply re-rendered the old window over it and deleted the exported file, against the delete dialog's "The clip, your videos and the files you exported stay." The same when the project was deleted while its export waited or ran.

**Decision:**
- `StateDB.project_export_of` also knows an export by its file: a file directly in `data/clips/projects/` (the data folder is the database file's folder), compared as the system compares paths. Only Branch B writes there. The project is the one the file is named after when it still exists; otherwise `""`, and the 409 carries `project_id: null`. `ProjectExportPrompt` then says the project was deleted and offers only "Apply anyway" (as before) and "Cancel".
- Deleting a project while its export waits or runs: **cancel** it as part of the delete (option a), not let it finish (option b). With (b) the clip's file would be replaced by the edits of a project the person just deleted, with no project or exports list left to see them in; with (a) the clip stays exactly as it was and no file the person had is touched, so the dialog's promise holds without new words. One that finishes regardless (the cancel came during its last step) is protected by the file rule.
- The same question when the render runs: a render job (queued before the export landed, or retried after) checks again under the clip's lock and fails with a plain sentence unless its payload says `replace_project_edits` (added by the four routes only when the person chose "Apply anyway"). Branch A and other direct callers of `Worker._rerender_clip` are unchanged.
- Make clips again needs no change (the export sets `edited_at`, which nothing clears, so the clip is kept), nor Storage (it never sweeps `clips/projects/`).

**Why:** the guard must not depend on records a person can delete; the file itself is the fact. Rejected: keeping rows or a tombstone after a delete (another record that can drift), and refusing to delete a project (a person may want it gone).

**Undo:** `core/state.py` (`in_project_exports`, `project_export_of`, `data_dir`), `server/api.py` (`project_edits_kept`, the `replace_project_edits` payload key), `server/youtube_api.py`, `server/jobs.py` (`keep_project_export`, `PROJECT_EXPORT_KEPT`), `server/projects_api.py` (`DELETE /projects/{id}` cancels, `install(lane=)`), `components/editor/ProjectExportPrompt.tsx`.


## ED45 · Anything made from a clip's original window asks first when the clip's file is a new-editor export

**Date:** 2026-11-15 · **Steps:** 8 (review fix) · 8-D13, 7-D5, ED44

**Context:** the review found Export in other languages (`POST /translate`) making its burned and dubbed videos from the clip's old window (`multilingual.burn.clean_base`) and its subtitle timings from the old window's caption lines, for a clip whose file is a new-editor (Branch B) export: the translated videos silently lost the project's edits. It was the fourth defect of one kind after Apply, the chat edit, the caption save and "Apply edits & upload", each caught alone.

**Decision:** one rule for the kind, checked against an inventory of every route, job, MCP tool and hook (step8-editor.md, "Everything made from a clip's own window"):
- A result that **replaces** the clip's file asks with the existing question (`project_edits_kept`, 409 `reason: "project_export"`, "Apply anyway" = `replace_project_edits`).
- A result that is a **separate file** (translations, dubs, subtitle files) asks with its own question (`project_edits_left_out`, 409 `reason: "project_export"`, `separate_file: true`, with `clip_ids`, `count`, `total`): the new files are made from the clip as the AI made it, without the new editor's changes; "Make it anyway" = `without_project_edits`. A batch asks once, naming every clip it affects, and never skips one. Non-UI callers get the same 409 and send the flag.
- The job asks again when it runs (as the render job does, ED44), before anything is written.
- A **new AI run** that finds a clip's window again (a forced run, a retry, a story reel made again) keeps the clip on its export, without asking, as Make clips again keeps every edited clip.
- An export is known by `StateDB.project_export_of` (rows, or the file in `data/clips/projects/`, ED44), so a deleted project's export is still asked about.

**Why:** the clip's file is the person's latest work; nothing made "from the clip" may silently be made from an older version of it. Rejected for now: burning and dubbing onto the export itself, which needs the captions remapped through the project's timeline (step 9's caption lane), so the subtitle timings would still be the old window's; and asking at Process anyway, which would hold a whole queue for a clip the run only happens to find again.

**Unchanged:** with the switch off, and for every clip that is not a new-editor export, every route, job payload, argv and file is as before (the checks are one SQL read per clip).

## ED46 · A project with no clip exports to the person's export folder, named after the project and the day

**Date:** 2026-11-15 · **Steps:** 8 (UX fix round) · ED28, ED44

**Context:** the UX check exported an imported 45 s project and the file landed in `data/clips/projects/<project id>-r0.mp4`: not the export folder set in Settings, named by an id, the folder only in a tooltip, and no Library entry (the project has no clip). Nothing said where it would go before Export.

**Decision:** `POST /projects/{id}/export` takes an optional `folder` (Settings' export folder, which the UI keeps, as clip exports send it). Only a project with **no clip** (made by hand, or whose clip was deleted) uses it: the file goes there as `<project name> - <YYYY-MM-DD>.mp4` (the local date; the name made safe for Windows, a trailing video extension left out, reserved names such as CON get " project", an empty name "Project", copies as `-2`, `-3`). A clip's project still replaces the clip's file in `data/clips/projects/` (ED28) and its dialog keeps "Replaces the clip in your Library". No folder, an empty or relative one, or one that can't be made: the old place and name. The dialog says "Saves to <folder>" before and "Saved in <folder>" after; the job's `result` gains `folder`.

**Why:** it must always be clear what is exported and where it went; a file the Library never lists has to be where the person looks for exports. Rejected: moving a clip's project's export too (the clip would then point outside the app's folder, and ED28's replace, the 7-D5/8-D11 guard by path in `data/clips/projects/`, and Storage's survey all rely on it being there); keeping the id name with only a visible folder line (the file would still be unrecognisable in the folder).

**Unchanged:** the 7-D5/8-D11 path guard (a clip's file in `data/clips/projects/`) is untouched: no clip ever points at a file in the export folder. Deleting a project never deletes its exports' files, so a file in the person's folder stays theirs.

