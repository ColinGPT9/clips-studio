# Editor suite research

The research behind the editor-suite plan, condensed. Nine read-only researchers wrote reports on 2026-11-05: two on Clips Kitty's own code, and seven on outside projects, licences, assets, models and AI providers. An architect review then checked the design against the code, and key claims were spot-checked. The decisions taken from this research are in [DECISIONS.md](DECISIONS.md).

How to read it:
- Figures were checked on 2026-11-05 unless another date is given. The outside projects change fast.
- "(inferred)" marks a conclusion the research drew, not a fact it found.
- Licence notes are readings for planning, not legal advice.
- File references point into this repository, as found on 2026-11-05.

## Clips Kitty today

- **Editor.** It works on one rendered 9:16 clip at a time and simulates new cuts on that file. Nothing opens a whole source video, and no route streams one. "Update preview" is a full-resolution render inside the request handler (`server/api.py:1889-1990`).
- **Edit model.** An `EditList` (keep ranges, mutes, volume 0–2, fades, speed 0.5–3, hook, music) in seconds relative to the clip, stored under `edit` in the `clips.render_opts` JSON column (`video_editor/timeline.py`, `core/state.py:557-561`). Undo is 30 steps in the browser, and there is no redo (`lib/editSuggestions.ts:618-626`). There are no tracks, no reordering, no waveform, no thumbnails and no timeline snapping.
- **Re-renders.** A re-render deletes and re-inserts the clip row, so the clip gets a new id (`server/jobs.py:943-950`), and the incoming `render_opts` are merged over the saved ones (`server/jobs.py:823-831`). `render_opts.edit` isn't shifted when the start moves, so a widened clip's cuts, mutes and hook land at the wrong times. Adding the same video again can re-run it with the cached download and transcript, but the Library has no re-run button, and a forced run adds clips beside the old ones (#190).
- **Rendering.** The canvas is 1080x1920 or 1920x1080, in H.264 only, through 2–4 lossy encodes per clip; audio is AAC 128k, normalised to −14 LUFS. Export is a plain file copy (`server/api.py:2462-2481`). 9:16 is hard-coded in `video/cropper.py` (42 places), `gaming/layouts.json`, `core/modes.py`, `video/outro.py` and the UI. YouTube downloads are capped at 1080 high (`sources/youtube.py:207-208`).

  | Encoder | Settings (`video/encoding.py:26-36`) |
  |---|---|
  | CPU | libx264, veryfast, CRF 20 |
  | NVENC | h264_nvenc p5, VBR, CQ 23, `-b:v 0` (no bitrate cap) |
  | AMF, VideoToolbox | 8 Mbps, maximum 12 Mbps |
  | QSV | global_quality 23 |

  Only `video.encoder` and `parallel_renders` can be configured, and `video.encoder` reaches only 2 of 15 call sites (`video/encoding.py:183-189`).
- **Captions.** ASS files burned by libass, with Windows stock fonts only (`video/captions.py:36-52`), animated only by highlighting one word at a time. The editor copies the caption constants into TypeScript (`CaptionStyleControls.tsx:5-30`, `LiveTextOverlay.tsx:50-67`).
- **Audio.** One music file per clip, picked by path, looped and ducked under the voice. It is never heard in the live preview, and a clip with music always renders on this PC (`remote_render/dispatch.py:75-81`). There are no sound effects and no library.
- **Sources.** Kept in `data/downloads/` and never deleted automatically, except that a watched channel can be set to delete a download once its clips are published (`server/automation.py:1181`). Storage clean-up deletes unknown mp4 files under `data/clips` (`core/housekeeping.py:74-84`), so a cache can't live there. Clip rows keep `video_id`, `start_s` and `end_s`.
- **Creators.** There is no free-text description or AI context field; the AI summary is built only from learned facts (`creator/retrieval.py:168-180`).
- **Engine surfaces.** The engine API checks Host but not Origin, and has no authentication (`server/api.py:692,698`). MCP knows protocol versions up to 2025-06-18 and has no editing tool (`server/mcp.py:37-38`). The AI edit chat asks for JSON without a schema (`analysis/clip_edit.py:55`) and queues a render at once.
- **Desktop shell.** Electron 31.7.7 (Chromium 126, Node 20.18), which is end-of-life. The renderer's Content Security Policy blocks WebAssembly, workers and engine fonts (`ui/src/renderer/index.html:7`).

### Problems found in the existing code

| # | Problem | In Phase 2? |
|---|---|---|
| 1 | Widening a clip leaves its cuts, mutes and hook at the wrong times | Fixed in step 2 |
| 2 | Forcing an encoder flips the global choice for every later render, and `video.encoder` reaches only 2 of 15 call sites | Fixed in step 1 |
| 3 | The engine API has no Origin check and no authentication | Fixed in steps 5–6, for new routes |
| 4 | Any cut drops the 0.4 s tail pad that protects the last word (inferred) | Fixed in step 7, in the compiler |
| 5 | Word highlight after cuts or speed changes uses times that were never remapped | Fixed in step 7 |
| 6 | "Update preview" renders at full resolution inside the request handler | Fixed in step 7, by the draft route |
| 7 | The editor's CSP blocks WebAssembly, workers and engine fonts | Fixed in step 9 |
| 8 | The English dubbing voice (Piper `en_US-lessac-medium`) was trained on research-licence data, so its use in monetised videos is unclear | No |
| 9 | Electron 31.7.7 is end-of-life | Step E, with Colin's OK |
| 10 | The MCP server supports protocol versions only up to 2025-06-18 | No (Phase 5) |
| 11 | AI-edit rules have drifted: maximum clip length 180 s in code vs 120 s in the prompt, and font size limits differ | No |
| 12 | The `build_msix.py --skip-backend` stale check misses packages | No |
| 13 | Nothing checks that translations are complete; the Creators page and the current editor are untranslated | No |
| 14 | ChatGPT sign-in uses the older app-server login | No (Phase 5) |

Rows marked "No" stay untouched unless Colin says otherwise.

## Licences at a glance

| Project | Licence, as found | Verdict | How Clips Kitty uses it |
|---|---|---|---|
| Concat | AGPL-3.0-or-later, plus a paid commercial licence and a CLA | Real beta, one maintainer | Design reference only (ED1) |
| FilmCraft, EffectCraft | MIT OR Apache-2.0 | Alphas that change daily | Not embedded; Phase 4 OTIO handoff, Phase 6 optional EffectCraft plugin |
| Ghost Editor | MIT | Young, Mac-only tool set | Phase 3 ports, credited |
| HyperFrames | Apache-2.0 | Real, pre-1.0, fast-moving | Phase 6: its format and seek method, in Electron's Chromium (ED10) |
| Remotion | Proprietary, source-available | Mature | Imported renders only (ED11) |
| GSAP | GSAP Standard License, not open source | Bans visual animation builders | Never shipped; WAAPI or anime.js instead |
| Motion Canvas | MIT | Dormant | Not adopted |
| Revideo | MIT | One committer, telemetry on | Not adopted |
| Motionity | MIT | Abandoned in 2022 | UI ideas only |
| MotionForge | MIT | Hobby project | Not adopted |
| Theatre.js | Core Apache-2.0, studio AGPL-3.0-only | Stalled | Not adopted |
| Lottie (ThorVG, dotlottie-web) | MIT; thorvg-python LGPL-2.1 | Healthy through ThorVG | Phase 6, designer-made assets |
| OpenCut classic | MIT | Archived 2026-05-17 | Step 8's timeline is seeded from its code |
| react-timeline-editor | MIT | Small but usable | Phase 2 building block |
| wavesurfer.js | BSD-3-Clause | Mature | Phase 2 building block |
| peaks.js | LGPL-3.0 | Mature | Not in the plan's building blocks |
| JASSUB | Bundled parts LGPL-2.1+, FreeType licence or GPL-2.0+, ISC, MIT | Active | Step 9 caption preview (ED8) |
| mediabunny | MPL-2.0 | Mature | Not in Phase 2 |
| MLT | LGPL-2.1, some GPL modules | Mature | Not adopted: one renderer |
| OpenTimelineIO | Apache-2.0 | Mature | Phase 4 interchange |
| LosslessCut | GPL-2.0-only | Mature | Ideas only, no code |
| Twick | Sustainable Use License v1.0 | Not open source | Rejected |
| OpenVideo | OpenVideo License, tiered by company size | Not open source | Rejected |
| Diffusion Studio | Labelled MPL-2.0; no usable source | No usable source | Rejected |
| Voicebox | MIT | Real; no release since 2026-04-25 | The shipped voice engine (ED12) |
| VoiceStudio | AGPL-3.0-only, plus a CLA and a paid licence | Real; changes daily | Not shipped |
| Soundbox | MIT for the app code only | Three weeks old | Not adopted |
| audio.cpp | Apache-2.0 | Young, already depended on | Phase 7, sound effects and music (ED14) |

## Outside projects

### Concat

- **What it is.** A desktop video editor in Rust (17 crates, about 146k lines): a Slint window, a wgpu compositor, FFmpeg 7 or later linked dynamically, and whisper.cpp and Chatterbox for speech. A project is a folder holding `concat.json` and `concat.lock`. Automation is JSON-RPC 2.0 over stdin, TCP (127.0.0.1:7420) or Unix sockets, with about 55 edit commands described by a JSON Schema (`concat --schema`), plus an MCP server (`concat --mcp`) and a headless mode in the release binary.
- **Licence.** AGPL-3.0-or-later, and the owner also sells a commercial licence. CLA 3.1 assigns contributors' copyright to the owner. Modified builds must use a different name (`TRADEMARK.md`). Its own dependencies: FFmpeg under the LGPL (x264 and x265 removed on 2026-11-03), Slint under GPL-3.0-only in the window crate only, fonts under OFL or Apache-2.0.
- **Maturity.** Created 2026-08-25; 1,364 commits on main, 1,195 of them by one author; about 48 contributors; 14.2k stars. 91 of the last 100 commits carry an AI co-author trailer. 8 releases, v0.2.1 (2026-09-04) to v0.2.8 (2026-10-20); the 0.2.8 Windows setup.exe is 86.6 MB, with 77k downloads. 194 open issues (labels: crash 20, windows 19, gpu 13). CI on main was red: of the 13 runs after the last green one (2026-11-04 18:54), 6 failed and 7 were cancelled. API version "0.2"; bus factor 1.
- **Facts that shaped the plan.**
  - Document times are f64 seconds; exact rational time is used only when rendering.
  - `DOCUMENT_VERSION = 2`, with migrations that are only ever appended. A newer document is refused, and unknown fields survive a save through `extra`. Media is referenced by absolute path.
  - The editing UI and the real-time preview exist only in the Slint window. The API returns still frames only (`preview.frame`), and the window's server keeps its own sessions, so a running window can't be driven.
  - One export runs per process, and the export spec has no bitrate (#471).
  - Proxies are made only for sources above 1080p. The compositor is GPU-only (DX12 on Windows, with WARP as the fallback). There is no WASM build (#386, wontfix) and no OTIO, EDL or FCPXML.
  - No relation to FilmCraft or EffectCraft was found.
- **Verdict.** A real, working beta, not vaporware, but pre-1.0, single-maintainer and fast-changing. The research's first-ranked option was a headless sidecar under a React timeline, at 4–6 months (inferred).
- **How Clips Kitty uses it.** As a design reference only: the project shape, edit-command ideas, `extra` for unknown fields, append-only migrations, and a batch as one undo step. No Concat code or binary ships, and its ~55 commands become about 20 validated operations (ED1, ED2).
- **Sources:** https://github.com/jub0t/Concat, https://concat.standalones.org/docs, https://concat.standalones.org/docs/api/methods, https://concat.standalones.org/guides/system-requirements, https://github.com/jub0t/Concat/issues/337, https://github.com/jub0t/Concat/pull/601, https://github.com/jub0t/Concat/blob/main/docs/MCP.md

### FilmCraft and EffectCraft

- **What they are.** Two Rust apps from the ArtCraft organisation, written largely by AI agents: FilmCraft is a clean-room Premiere Pro clone and EffectCraft an After Effects clone. Both use an egui UI on wgpu and their own codecs, with headless CLIs and MCP servers. FilmCraft's time is integer ticks, 254,016,000,000 per second. FilmCraft imports and exports FCP7 XML, FCPXML 1.9–1.11, OTIO, CMX 3600 EDL, AAF and OMF. EffectCraft renders a template headless with alpha (`render --template … --channels rgba --format prores --prores 4444`).
- **Licence.** MIT OR Apache-2.0 (the workspace `Cargo.toml`; GitHub's label shows only Apache-2.0). The ArtCraft name and marks may not appear in another product's name, icon, store listing or marketing; the plain line "based on EffectCraft by the ArtCraft team" is allowed. Supply-chain warning: the `filmcraft-*` and `effectcraft-*` crates on crates.io belong to an unrelated account with no repository link, so only git revisions from storytold are genuine.
- **Maturity.** FilmCraft: created 2026-09-30, 3,516 commits, v0.17.1 on 2026-11-05, and its project format already at schema v17 after five weeks. EffectCraft: created 2026-10-01, 4,232 commits, v0.21.1. Both call themselves alpha (FilmCraft is about 56% "ready for real work", EffectCraft about 45%), and macOS is their most-tested platform. Open issues include Windows start-up crashes and a missing preview on Windows 10. Forks run at 41–53% of stars (17,549 and 8,454 stars), so stars are no maturity signal; downloads look real (the v0.17.0 MSI has 68,395).
- **Verdict.** Real, impressive alphas that change too fast to embed or link. FilmCraft has no AMF or Quick Sync and EffectCraft no hardware encoding, so neither beats the FFmpeg path Clips Kitty already has.
- **How Clips Kitty uses them.** Not in Phase 2. Phase 4 may hand projects to FilmCraft through OTIO or FCP7 XML, and Phase 6 may add an optional EffectCraft template plugin that renders ProRes 4444 with alpha.
- **Sources:** https://github.com/storytold/filmcraft (ROADMAP.md, docs/architecture.md, docs/project-files.md, docs/gaps.md, docs/performance.md, crates/platform/README.md), https://github.com/storytold/effectcraft (ROADMAP.md, docs/architecture.md, docs/agents.md, docs/brand/LICENSE-brand.txt), https://crates.io/crates/filmcraft-h264, https://www.phoronix.com/news/Photocraft-Filmcraft-AI (headline only; the page returned 403)

### Ghost Editor

- **What it is.** A Claude Code skill that drives about 100 bash, Python and Node scripts to cut talking-head reels: take selection, natural joins, captions, B-roll and QA. It has no server, GUI or MCP. Rendering builds one HTML page with a paused GSAP timeline, which HyperFrames captures frame by frame, then FFmpeg re-encodes.
- **Licence.** MIT; the take-selection logic and 7 recipes also carry other authors' MIT notices. The demo clips are "watch, don't reuse". Traps in the repository: Mixkit sounds (no redistribution inside a tool, and its bulk-download script goes against Mixkit's terms), GSAP (not open source), Google Images through Apify (rights unknown), and an example people-rules file that is a brand risk.
- **Maturity.** Created 2026-09-24, last push 2026-10-30. 12 commits, which are squashed exports of a private repository; tags v0.1.0 to v0.3.0; 216 stars. 3 tests, none for take selection, and no CI. Officially Apple silicon only; Windows support is an open pull request (#2, 2026-10-28). Its speeds are self-reported, with no test protocol.
- **Facts worth porting.**
  - Takes are found by Smith-Waterman alignment of normalised words: exact match 1, a shared 4-letter prefix 0.8, a difflib ratio of 0.72 or more; −0.8 for a missing word, −0.6 for an extra one. The best alignment is repeated with masking, so every full or partial attempt is found.
  - Whisper decodes island by island (silero, minimum pause 0.3 s), because decoding a whole file merges restarts.
  - Pauses over 0.26 s inside a take shrink to about 0.18 s; pieces get a 0.10 s lead and a 0.18 s tail.
  - Picture and voice snap to one frame grid, which fixed a measured 43–54 ms lip-sync drift. Joins use 35 ms equal-power crossfades, per-take level matching within ±3 dB, room tone and two-pass loudnorm to −16 LUFS.
  - Captions sit below the chin, else above the head, inside each platform's UI margins (`safezone.mjs`).
  - A one-pass FFmpeg QA report (loudness −16 to −13.5 LUFS, true peak ≤ −1 dBTP, black frames ≥ 0.1 s, frozen picture ≥ 0.5 s, silences over 0.8 s), with an auto-fix loop of at most 3 rounds.
- **Privacy.** With a Gemini key, the documented workflow sends Google frame sheets, whole raw takes at 540x960, rough cuts and the full final render; its INSTALL mentions only frames.
- **Verdict.** Valuable as a set of measured, deterministic tools, not as a product to embed.
- **How Clips Kitty uses it.** Phase 3 ports, with MIT credit: the take matcher, smooth joins, caption safe zones and the QA report, plus its synthetic flash/beep sync test (Phase 2's A/V checks use the same idea). Not taken: GSAP, Mixkit sounds, cloud uploads, Apify, CrisperWhisper weights.
- **Also checked for the same jobs:** auto-editor (Unlicense, v31.8.0), autocut (Apache-2.0, idle since 2024-10), video-use (MIT; uses a cloud speech service), WhisperX (BSD-2), stable-ts (MIT, archived 2026-05-30), whisper-timestamped (AGPL-3.0; marks disfluencies), CrisperWhisper (code MIT, weights non-commercial).
- **Sources:** https://github.com/kurbaitaev/ghost-editor (at commit ce17272: scripts/takes.py, scripts/islands_tx.py, scripts/plan_cut.py, scripts/voice_smooth.py, scripts/build.mjs, references/reel-json.md, docs/THIRD_PARTY/matte.md), https://github.com/kurbaitaev/ghost-editor/pull/2, https://github.com/WyattBlue/auto-editor, https://github.com/browser-use/video-use, https://github.com/m-bain/whisperX, https://github.com/jianfch/stable-ts, https://github.com/linto-ai/whisper-timestamped, https://github.com/nyrahealth/CrisperWhisper

### HyperFrames

- **What it is.** HeyGen's HTML-to-video framework. A composition is plain HTML with no build step: `class="clip"`, `data-start`, `data-duration`, `data-track-index`, and a paused timeline registered on `window.__timelines`, with typed template variables in `data-composition-variables`. A local server opens the page in a pinned chrome-headless-shell through Puppeteer, calls `window.__hf.seek(t)` for each frame, captures it, and FFmpeg encodes. It has a CLI (`init`, `preview`, `lint`, `inspect`, `render`, `add`), agent skills and an editing SDK.
- **Licence.** Apache-2.0, which is compatible with AGPL-3.0. Its npm manifests omit the `license` field, so record it in NOTICE. CLI telemetry goes to PostHog and is on by default; `HYPERFRAMES_NO_TELEMETRY` or `DO_NOT_TRACK` turns it off.
- **Maturity.** Created 2026-03-10; 79.1k stars, 7.3k forks, 6,795 commits. v0.9.11 to v0.9.15 shipped within about 16 hours, the last on 2026-11-05; 1.54M npm downloads a week. It has a Windows render CI job, and open Windows bugs #4058 (FFmpeg EBUSY on spawn), #4060 and #4845. Its `FrameAdapter` is an "experimental v0 API". Real and well funded, but pre-1.0.
- **Facts that shaped the plan.**
  - On macOS and Windows it falls back to `Page.captureScreenshot`; BeginFrame and `--deterministic-mode` run only on Linux. Alpha comes from a transparent background override plus PNG capture.
  - The engine always launches its own browser (there is no `puppeteer.connect`), so Electron's Chromium can't be plugged in without porting the capture loop.
  - Extra download: the headless shell zip is 119.5 MB and the CLI 24 MB unpacked, and it needs Node 22 or later. Electron 31 embeds Node 20.18; the first Electron with Node 22 is 35.
  - In the editing SDK, `setText`, `setStyle`, `setTiming`, `moveElement` and `setVariableValue` work with any animation runtime, but every tween and keyframe operation is GSAP-only. About 640 of about 760 registry HTML files contain `gsap.timeline`.
  - Published speed: 1080p, 300 frames in 25.0 s or 9.8 s depending on one CSS property, about 12–31 fps, hardware not stated. Transparent WebM is "CPU-heavy".
  - Electron exposes the Chrome DevTools Protocol in-process (`webContents.debugger.sendCommand`), so the same seek-then-capture loop can run in Electron's own Chromium.
- **Verdict.** Use its HTML format and seek protocol as the one motion-graphics engine, hosted in Electron's own Chromium, without GSAP. The research put an MVP at 4–6 developer-weeks, at medium risk (inferred).
- **How Clips Kitty uses it.** Phase 6 (ED10): Clips Kitty's own JSON layers and keyframes compile to HyperFrames-compatible HTML, render in a hidden sandboxed Electron window, and are captured as PNG alpha and composited by FFmpeg. Headless and Docker renders may run the HyperFrames producer as shipped, on the same files.
- **Sources:** https://github.com/heygen-com/hyperframes, https://www.heygen.com/research/html-to-video, https://github.com/heygen-com/hyperframes/blob/main/docs/sdk/reference/edit-operations.mdx, https://github.com/heygen-com/hyperframes/blob/main/docs/guides/performance.mdx, https://github.com/heygen-com/hyperframes/blob/main/docs/guides/captions-and-recuts.mdx, https://www.electronjs.org/docs/latest/api/debugger, https://www.gnu.org/licenses/license-list.html#apache2

### Remotion

- **What it is.** Video made from React components that are functions of the frame number. It renders by screenshots in Chrome Headless Shell (158.0.8127.2 in 4.0.540), then a Rust compositor that redistributes FFmpeg. Changing a video after it is written means editing code.
- **Licence.** Proprietary and source-available: "Remotion is not open-source software according to the Open Source Initiative (OSI) definition." From its terms (updated 2026-11-04):
  - Free for "an individual…; an organization or team of individuals with up to 3 people", where headcount means all personnel.
  - In a native desktop app (Electron or Tauri), only behind "an abstraction layer… the end-user must not have direct access to edit or upload Remotion code", and "the regular licensing requirements still apply, including reporting renders".
  - If the app "is no longer actively developed or maintained", the distributor must hold an active Company License for at least a year after its last release.
  - "Organizations building video editors… will fall under Remotion for Automators": $0.01 per render, with a minimum spend of $100 a month. Remotion 5.0 will make telemetry mandatory for that tier (pull request #3750).
  - The in-browser `renderMediaOnWeb` handles only a "subset of HTML elements" and "always sends a telemetry event for every render".
- **Maturity.** Since 2020; 70.8k stars, v4.0.540, 3.6M npm downloads a week. Mature. Its Electron guide bundles the headless shell (a 128.5 MB zip) and warns "This will increase the final app size significantly".
- **Verdict.** It can't ship in an AGPL-3.0 app. In the research's reading, its terms are "further restrictions" that AGPL §10 forbids, and the no-code-access rule clashes with every user's right to modify the app. An AGPL §7 exception would need every copyright holder to agree, and `git shortlog` shows outside contributors. Companies of 4 or more people running Remotion locally are plausibly users who need their own licence.
- **How Clips Kitty uses it.** Only through imported renders (ED11): someone who licenses Remotion themselves brings in ProRes 4444, WebM with alpha or a PNG sequence with alpha. Remotion documents ProRes 4444 for exactly this kind of hand-off to editors.
- **Sources:** https://www.remotion.dev/docs/terms, https://www.remotion.dev/docs/license/faq, https://www.remotion.dev/docs/electron, https://www.remotion.dev/docs/client-side-rendering, https://www.remotion.dev/docs/transparent-videos, https://www.remotion.dev/docs/performance, https://github.com/remotion-dev/remotion/pull/3750

### GSAP

- **What it is.** A JavaScript animation library, under Webflow's licence. HyperFrames' template registry, HyperFrames Studio and Ghost Editor all depend on it.
- **Licence.** The GSAP Standard License, which is not open source. "'Prohibited Uses' means any implementation and/or use of GSAP Products in tools that allow users to build visual animations without code that encourages, induces, or materially assists in creating a solution that competes with Webflow's visual animation building capabilities." Such use needs "prior written consent", and Webflow may terminate or amend the licence. Its FAQ says "AI-generated code is not a 'Prohibited Use'".
- **Verdict.** A CapCut-style keyframe editor falls squarely within the prohibited use (the research's reading), and the licence isn't free software, so GSAP can't ship in the app.
- **How Clips Kitty uses it.** It doesn't. Animation uses WAAPI, CSS or anime.js (MIT, 4.5.0).
- **Sources:** https://gsap.com/standard-license, https://gsap.com/community/standard-license/

### Motion Canvas, Revideo, Motionity, MotionForge, Theatre.js and Lottie

| Project | What it is | Licence | Maturity (2026-11-05) | Verdict and use |
|---|---|---|---|---|
| Motion Canvas | Animations written as code, rendered from its editor | MIT | Last code commit 2025-02-16 (v3.18.0-alpha.0); a 2026-07-02 push only changed the docs domain. Rendering needs a button press in its editor | Dormant, one maintainer; not adopted |
| Revideo | A video-from-code library; the engine behind a small vendor, Midrender | MIT | Moved to `midrender/revideo`; v0.11.0 on 2026-07-10, last push 2026-07-15, essentially one committer; PostHog telemetry on by default | Not adopted |
| Motionity | A web animation tool built on Fabric.js, anime.js, ffmpeg.wasm and webm-writer | MIT | Last push 2022-09-12 | Abandoned; UI ideas only |
| MotionForge | A Remotion-API clone on Next.js and React Three Fiber | MIT | 98 stars, no releases, "Add files via upload" commits; looks AI-generated (inferred) | Ignored |
| Theatre.js | An animation library with a visual studio | Core Apache-2.0, studio AGPL-3.0-only | Last public commit 2024-04-11 ("1.0 notice"); development moved to a private repository | Stalled; not adopted |
| Lottie | Keyframe-JSON vector animation. lottie-web plays it; ThorVG is a native rasteriser (CPU, GL or WebGPU) that returns RGBA, and dotlottie-web is ThorVG compiled to WASM | MIT; thorvg-python LGPL-2.1 | lottie-web last pushed 2025-09-01, with 861 open issues. ThorVG 1.0 shipped in February 2026 and is active; dotlottie-web gets 2.29M downloads a week. Sizes: thorvg-python Windows wheel 0.9 MB, dotlottie-web 7.3 MB | Healthy through ThorVG. Phase 6: Lottie through ThorVG for designer-made template assets. Frames are indexed, so output is deterministic, and ThorVG 1.0 adds slot overrides for swapping text, colours and images |

- LottieFiles' free assets come under the Lottie Simple License, which forbids compiling them into a competing service, so templates must be Clips Kitty's own or commissioned.
- In a Canva case study published by LottieFiles, generating the frames for a 1-minute Lottie video took 2.34 s with ThorVG against 10.2 s with rlottie (a vendor figure; resolution not stated).
- Also checked: Rive (MIT runtimes, but a proprietary editor), Manim (MIT, a code-only explainer tool), PixiJS (MIT, a renderer with no timeline) and anime.js (MIT, 4.5.0).
- **Sources:** https://bestofjs.org/projects/revideo, https://github.com/midrender/revideo, https://www.thorvg.org/post/thorvg-v1-0-a-new-generation-released, https://lottiefiles.com/case-studies/canva, https://help.lottiefiles.com/animation-licensing-basics-

### Editor building blocks

OpenCut classic, react-timeline-editor, wavesurfer, peaks, JASSUB, mediabunny, MLT, OTIO, LosslessCut, Twick, OpenVideo and Diffusion Studio.

| Project | What it is | Licence | Maturity (2026-11-05) | Verdict and use |
|---|---|---|---|---|
| OpenCut classic | A Next.js web editor: a CapCut-style main track plus overlay and audio tracks, media kept in browser storage (OPFS and IndexedDB), mediabunny for decoding, and versioned project JSON with tested migrations from v0 to v30 | MIT | Archived 2026-05-17. OpenCut itself (97k stars, 101 contributors) is being rewritten with a Rust core, is pre-alpha, and takes no outside contributions yet | Not adopted whole. Step 8's timeline is seeded from its MIT track types, snapping and ripple code, with credit |
| react-timeline-editor (`@xzdarcy`) | A React timeline component on interactjs and react-virtualized (`gridSnap`, `dragLine`, `onActionResizing`) | MIT | 5 contributors, 79 commits, v1.0.0 (2026-01-25), about 47k downloads a month. No `aria`, `role`, `tabIndex` or `onKeyDown`; grid and line snapping, no ripple | Phase 2 building block; it would need a keyboard and ARIA layer |
| wavesurfer.js | A waveform player with Regions and Envelope plugins (fades, volume) | BSD-3-Clause | v8.0.5, about 5.8M downloads a month | Mature. Phase 2 building block, fed with engine-made peaks: it can't decode 2 hours of audio in the browser |
| peaks.js (BBC) | A waveform UI with segments, points and keyboard interaction, drawn from server-side waveform data | LGPL-3.0 | v4.0.0 (2025-08) | Mature; not in the plan's building blocks |
| JASSUB | libass, the subtitle renderer FFmpeg uses, compiled to WebAssembly for the browser | Bundled parts LGPL-2.1+, FreeType licence or GPL-2.0+, ISC, MIT | Active, about 131k downloads a month; 4.9 MB unpacked | Step 9: caption preview from the same ASS file and fonts as the burn (ED8) |
| mediabunny | A WebCodecs library for reading and writing media | MPL-2.0, without the "Incompatible With Secondary Licenses" notice | 49 contributors, 1,531 commits, v1.65.2, about 18M downloads a month; used by OpenCut classic and Diffusion Studio; 11.4 MB unpacked | Mature; not in Phase 2. Only if real-time multi-layer preview is needed later (the research put that at 6–10 weeks and medium-high risk, inferred) |
| MLT | The multimedia framework under Shotcut and Kdenlive: producers, playlists and multi-track tractors, with in and out points in frames; MLT XML and `melt` | LGPL-2.1, plus GPL modules (plusgpl, qt, rubberband, vid.stab) | 147 contributors, 6,840 commits, v7.42.0 (2026-10-03); available as MSYS2's `mingw-w64-mlt` or in Shotcut's 197 MB zip | Mature; not adopted, because Clips Kitty keeps one renderer (ED1) |
| OpenTimelineIO (OTIO) | A timeline interchange format with a Python library | Apache-2.0 | v0.18.1, with a Windows wheel for Python 3.11. Adapters: Kdenlive 0.0.3 (2026-01); FCPXML, FCP7 and CMX 3600 EDL at 1.0.0; AAF 2.0.0. The MLT adapter is stale (2021) | Phase 4: OTIO in and out, and the FilmCraft handoff |
| LosslessCut | An Electron and FFmpeg cutter | GPL-2.0-only | 184 contributors, 4,174 commits, v3.70.2, active | Its code can't be mixed into an AGPL-3.0 app, so ideas only, re-implemented: FFmpeg-assisted playback, smart cut, and an HTTP API that rejects any request carrying an `Origin` header |
| Twick | A React video editor SDK | Sustainable Use License v1.0: "non-transferable", forbids rebranding | 7 contributors, last commit 2026-05-07; the README is SEO marketing | Not open source; rejected |
| OpenVideo | designcombo's react-video-editor, moved to openvideodev | "OpenVideo License": free only for individuals and companies with up to 3 employees | — | Not open source; rejected |
| Diffusion Studio | A browser video engine (`core`) | Labelled MPL-2.0 | The repository holds only 133 files (docs and a playground); the npm build is minified and contains license-key and watermark code | No usable source; rejected |

- **LosslessCut's playback technique.** Files Chromium can decode play straight in `<video>`. For others, the main process runs `ffmpeg -ss <t>`, scales down, and encodes `libx264 ultrafast zerolatency crf 10 -g 1` to fragmented MP4 on stdout, which the renderer plays through MediaSource, restarting the process on every seek (`src/main/ffmpeg.ts:561-713`). Smart cut re-encodes only the partial group of frames at the start and stream-copies the rest; its docs call it experimental.
- **Twick, OpenVideo, Remotion and Diffusion Studio** would each add restrictions that the AGPL forbids (inferred).
- **Preview and proxy facts.** Electron 22 and later play HEVC through Windows hardware decoding, which needs roughly a GTX 645, Intel HD 4400 or RX 460 or newer; without one, HEVC won't play (inferred). Shotcut's proxy default is 540p and Kdenlive's 640 px wide, and both render the final file from the originals. At about 2 Mbps, a proxy costs about 1.8 GB per 2-hour VOD (inferred). OBS MKV recordings usually need only a lossless remux.
- **Encoders.** The bundled gyan.dev FFmpeg includes NVENC, AMF, Intel QSV, x264, x265, libass, harfbuzz and libaom; SVT-AV1 is only in the "full" build. RTX 30 cards can't encode AV1, so the export dialog should build its codec list by probing the PC.
- **Also checked:** omniclip (MIT, one contributor, last commit on main 2025-08-07), Olive (GPL-3.0, last real commit 2024-12-05, dormant), WebAV (MIT, last commit 2026-01-10), Etro (GPL-3.0, video from code).
- **Sources:** https://github.com/OpenCut-app/OpenCut, https://github.com/OpenCut-app/opencut-classic, https://github.com/xzdarcy/react-timeline-editor, https://github.com/katspaugh/wavesurfer.js, https://github.com/bbc/peaks.js, https://github.com/ThaUnknown/jassub, https://github.com/Vanilagy/mediabunny, https://mediabunny.dev/guide/reading-media-files, https://www.mltframework.org/docs/mltxml/, https://packages.msys2.org/base/mingw-w64-mlt, https://github.com/AcademySoftwareFoundation/OpenTimelineIO, https://github.com/mifi/lossless-cut, https://github.com/ncounterspecialist/twick/blob/main/LICENSE.md, https://github.com/openvideodev/react-video-editor, https://github.com/diffusionstudio/core, https://github.com/omni-media/omniclip, https://github.com/olive-editor/olive, https://github.com/StaZhu/enable-chromium-hevc-hardware-decoding, https://docs.kdenlive.org/en/project_and_asset_management/project_settings/proxy_settings.html, https://forum.shotcut.org/t/proxy-editing/18517, https://www.gyan.dev/ffmpeg/builds/, https://www.phoronix.com/news/NVIDIA-RTX-30-AV1-Decode

### Voicebox, VoiceStudio, Soundbox and audio.cpp

| | Voicebox | VoiceStudio | Soundbox | audio.cpp |
|---|---|---|---|---|
| What it is | A Tauri app plus a FastAPI voice server (PyInstaller one-folder build) on 127.0.0.1:17493 | An Electron app plus a FastAPI backend on :3900 that bootstraps a uv-managed Python 3.11 and PyTorch on first launch | A Tauri app plus one Fastify (Node) process on :17494 that runs audio.cpp per job | A ggml C++ runtime for 143 audio model families, with a CLI, an OpenAI-style server and a C API |
| Licence | MIT | AGPL-3.0-only plus a CLA; a company sells a paid Pro and commercial licence | MIT for the app code only, plus a note that several models prohibit commercial use (GitHub shows NOASSERTION) | Apache-2.0 |
| Created, last push | 2026-01-25, 2026-10-07 | 2026-04-09, 2026-11-05 | 2026-10-13, 2026-11-05 | 2026-06-23, 2026-11-05 |
| Stars | 59,434 | 101,207 | 143 | 4,009 |
| Commits | 813 | 6,466, about 30 a day by two people | 71 | 687 by its top author |
| Latest release | v0.5.0, 2026-04-25, nothing since; MSI 496,491 downloads | v0.5.7, 2026-10-26; Windows exe 119,873 downloads | v0.4.1, 2026-11-01; 214 downloads | v0.9.4, 2026-11-04 |
| Windows size | 543 MB MSI; the CUDA add-on downloads separately as 1,035 MB plus 2,112 MB | 222 MB installer, then about 10 GB free disk (about 5 GiB on CPU-only PCs) | 31 MB installer, unsigned | 32 MB (CPU), 84 MB (Vulkan), 279 MB plus 575 MB cudart (CUDA 13.3) |
| Automation | `/generate`, `/speak`, `/profiles`, `/models/load` and unload, OpenAPI at `/docs`, server-sent events, MCP (4 tools); no documented authentication (loopback only) | OpenAI-compatible `/v1/audio/speech`, transcription and dubbing routes, WebSocket ASR, MCP; a bearer key for remote clients | `/api/generate`, server-sent events, MCP (24 tools); "There is no authentication" | `/v1/audio/speech`, `/v1/tasks/run`, `/v1/models/load` and unload; `lazy_load`, `max_loaded_models`, `idle_unload_ms` |
| Sound effects, music | No, no | No, no | Yes, yes | Yes, yes |
| Verdict and use | Shipped as Clips Kitty's voice engine (Colin's choice, ED12) | Not shipped | Not adopted; its SHA-256-checked download pattern and licence-flagged model catalogue are worth copying | Phase 7: the optional engine for sound effects and music (ED14) |

- **Voicebox.** The server takes `--host --port --data-dir --parent-pid`; the last is a watchdog that exits when the parent dies. The CPU server ships inside the installer and isn't published separately. It already fights version pins: chatterbox wants torch==2.6 and hume-tada torch<2.8, so both are installed with `--no-deps`. Its README says "Built with Capy" (an AI agent runs the project); main holds unreleased OmniVoice, VoiceDesign and MP3 export, and many issues get no reply. VRAM from its own table: Kokoro about 150 MB, Chatterbox Turbo about 1.5 GB, Qwen3-TTS 0.6B about 2 GB and 1.7B about 6 GB, TADA 3B about 8 GB. It watermarks only through Chatterbox's built-in PerTh.
- **VoiceStudio.** Its default voice model, OmniVoice, changed from Apache-2.0 to CC-BY-NC on 2026-07-03. Engines with conflicting pins (IndexTTS, CosyVoice, MOSS) run as sidecars in their own environments, and each "can reach several GB". It watermarks with AudioSeal. Its stars-to-watchers ratio (about 290:1) suggests stars boosted by trending (inferred).
- **Soundbox.** Three weeks old, one developer; an open pull request renames it "Konchi" (2026-11-04). It pulls the Stable Audio 3 GGUFs from an ungated mirror, which skips Stability's click-through.
- **audio.cpp.** Unsloth Studio installs it as its audio engine, and VoiceStudio uses it for two models. Its request handling got stricter in 0.9.x, and many model families are unverified.
- **No torch clashes.** None of these may load into Clips Kitty's own torch 2.13+cu130: Voicebox and VoiceStudio use cu128 builds, and audio.cpp has no torch at all. On the minimum-spec RTX 2060 (6 GB), one model at a time fits, serialised with YOLO, Whisper and Ollama (inferred).
- **The research's ranking.** audio.cpp first, for voice and sound in one runtime (about 2–3 weeks, inferred); Voicebox second, as a voice sidecar (about 1–2 weeks). Colin chose Voicebox for the shipped voice (ED12); audio.cpp stays for sound effects and music in Phase 7 (ED14).
- **Sources:** https://github.com/jamiepine/voicebox (the research recorded no URL for Voicebox; this repository was confirmed through the GitHub API on 2026-11-05: MIT, created 2026-01-25, last push 2026-10-07), https://raw.githubusercontent.com/debpalash/VoiceStudio/HEAD/LICENSE-NOTICE.md, https://raw.githubusercontent.com/debpalash/VoiceStudio/HEAD/docs/install/windows.md, https://raw.githubusercontent.com/HamSanders/soundbox/HEAD/docs/AUDIOCPP.md, https://raw.githubusercontent.com/0xShug0/audio.cpp/HEAD/app/server/README.md, https://raw.githubusercontent.com/0xShug0/audio.cpp/HEAD/docs/model_licenses.md, https://raw.githubusercontent.com/unslothai/unsloth/HEAD/README.md

## Microsoft Store policy points

From the Microsoft Store Policies (version 7.20):
- **10.2.2:** no dynamic code that changes the described functionality, so describe any optional engine in the listing. The plan reads a first-launch download of Python and PyTorch as code downloaded at runtime under this rule.
- **10.2.3:** an app must not "offer to install secondary software" not developed by its publisher. A headless engine that enhances Clips Kitty is safer than offering whole third-party apps (inferred).
- **10.2.4:** disclose any dependency at the start of the description.
- **11.16:** disclose live generative AI, and provide a way to report content.

Microsoft removed its 2022 restriction on free and open-source apps, and Shotcut (GPL) is sold in the Store. Already documented for Clips Kitty: `runFullTrust` only, justified by three bundled child processes (`docs/msstore-submission-sheet.md`), and downloads described as model weights (data), not executable code (`docs/MSSTORE.md`).

**Sources:** https://learn.microsoft.com/en-us/windows/apps/publish/store-policies, https://www.windowscentral.com/microsoft/microsoft-removes-restrictions-on-generally-free-and-open-source-apps-in-microsoft-store, https://apps.microsoft.com/detail/9plnffl3p6lr

## Assets

### Fonts (step 3)

The research's starter set is about 2.5 MB. Licences are as the research listed them: unmarked fonts are OFL, and two are Apache-2.0.

| Font | Licence | Size | Note |
|---|---|---|---|
| Montserrat, static ExtraBold and Black | OFL | — | Static weights, because libass has an open variable-font issue (#386) |
| Anton | OFL | 171 KB | |
| Bebas Neue | OFL | 61 KB | |
| Bangers | OFL | 93 KB | |
| Luckiest Guy | Apache-2.0 | 73 KB | |
| Archivo Black | OFL | 91 KB | |
| Poppins Black | OFL | about 155 KB | |
| Lilita One | OFL | 28 KB | |
| Titan One | OFL | 56 KB | |
| Bowlby One | OFL | 60 KB | |
| Oswald | OFL | 172 KB | |
| Permanent Marker | Apache-2.0 | 75 KB | |
| Noto Sans Arabic, Noto Sans Devanagari | OFL (inferred; the research gave no licence) | 845 KB, 642 KB | Bundled, for script coverage |
| Noto Sans JP, Noto Sans SC | OFL (inferred) | 9.6 MB, 17.8 MB | Downloaded on demand |

- The OFL allows bundling fonts "with any software, provided that each copy contains the above copyright notice and this license". A font can't be sold on its own, and a modified copy can't use a Reserved Font Name.
- FFmpeg's subtitles filter needs `fontsdir=` to see bundled fonts (`video_editor/export.py:89`).
- The plan calls these 12 OFL fonts; with two Apache-2.0 fonts, the set is 10 OFL plus 2 Apache-2.0, all fine to bundle.
- **Sources:** https://openfontlicense.org/ofl-faq/, https://github.com/libass/libass/issues/386

### Sound effects (steps 4 and 10)

| Source | Licence | Decision | Note |
|---|---|---|---|
| Kenney packs: Interface Sounds (100 files), Impact Sounds (130 files), UI, Digital, Sci-fi | CC0: "Attribution is not required" | Bundle | https://kenney.nl/assets/impact-sounds |
| "512 Sound Effects (8-bit style)" on OpenGameArt | CC0 | Candidate | |
| Hand-picked Freesound sounds | CC0 | Candidate | Download through the website: the Freesound API is "free only for non-commercial purposes". Re-check each page |
| The 27 Freesound effects bundled in Ghost Editor | CC0 | Candidate, after re-checking each Freesound page | |
| Sonniss GDC bundles | Forbids supplying the sounds "as sound effects to any other person", asset packs included, and forbids AI training | Exclude | |
| Mixkit | May not be redistributed "inside a tool" | Exclude | |
| Pixabay | "cannot… distribute Content… on a Standalone basis" | Exclude | https://pixabay.com/service/license-summary/ |

### Music (steps 4 and 10)

| Source | Licence | Decision | Note |
|---|---|---|---|
| HoliznaCC0, on the Free Music Archive | CC0, stated per track | Bundle | |
| Kenney Music Jingles | CC0 (inferred: the research lists Kenney's packs as CC0) | Candidate | |
| Musopen recordings | Public domain; check each track | Candidate | |
| CC-BY music | CC-BY | Only with automatic credit lines | |
| Pixabay | No standalone redistribution | Exclude | |
| YouTube Audio Library | No blanket redistribution found | Exclude | |
| Mixkit | No redistribution inside a tool | Exclude | |

Attribution duties: ship the OFL and Apache licence texts; show "Powered by Stability AI" and the Stability notice if Stable Audio is used; give per-video credits for CC-BY music and models; show MiniMax's name in the UI if MiniMax Music is used. CC0 needs nothing, but a provenance manifest helps in Content ID disputes (inferred).

## Models

### Voice, sound effects and music

"Hidden" is the plan's own list. "Not offered" applies the same rule to other non-commercial models (inferred).

| Model | Task | Weights licence | Commercial use | VRAM, size | In Clips Kitty |
|---|---|---|---|---|---|
| Kokoro-82M | Text to speech | Apache-2.0 | Yes | About 150 MB, CPU is fine; 0.36 GB | Default voice (`af_heart`), through Voicebox |
| Chatterbox ML, Turbo | Speech and cloning | MIT, with the PerTh watermark; the repository is now an unsupported archive | Yes | About 3 GB and 1.5 GB; 3.2 GB and 1.5 GB | Offered through Voicebox |
| Qwen3-TTS 0.6B, 1.7B | Speech, cloning, voice design | Apache-2.0 | Yes | About 2 GB and 6 GB; 1.2 GB and 3.5 GB | Offered through Voicebox |
| ACE-Step 1.5 | Songs with lyrics | MIT | Yes: "You can strictly use the generated music for commercial purposes" | Under 4 GB (its claim), XL 12 GB or more; 6.19 GB at Q8 | Phase 7 music, through audio.cpp |
| Stable Audio 3 Small SFX, Music (gated) | Sound effects, instrumentals | Stability AI Community | Conditional: under US$1M revenue, after registering, with "Powered by Stability AI" shown; outputs belong to the user | About 2.5 GB (estimate); 1.68 GB at Q8 | Phase 7, behind a licence click-through |
| Stable Audio Open 1.0, Small | Sound effects, music | Stability AI Community | Conditional | —; 15.7 GB and 5.0 GB repositories | Not chosen |
| MiDashengLM-Gen | Sound effects, ambience | Apache-2.0, plus use restrictions | Yes | About 3.5 GB (estimate); 3.38 GB at Q8 | The research's no-strings alternative for sound effects |
| HeartMuLa 3B, Magenta RT 2 | Music | Apache-2.0, CC-BY-4.0 | Yes (Magenta needs attribution) | —; about 15.7 GB and 15.6 GB repositories | Not chosen |
| MiniMax Music 3 | Songs | Community licence | Conditional: its name shown in the UI, AI disclosure, authorisation above US$20M | About 10 GB | Not chosen |
| VoxCPM2, CosyVoice3, VibeVoice-1.5B | Speech and cloning | Apache-2.0, Apache-2.0, MIT | Yes | —; 5.0, 9.8 and 5.4 GB repositories | Not chosen |
| OpenVoice V2 | Tone-colour voice conversion | MIT | Yes | Small; 0.13 GB | Not chosen |
| IndexTTS-2, 2.5 | Cloning with emotion | bilibili Model Use License | Conditional: above 100M monthly users or RMB 1bn revenue; may not be used to improve other AI models | —; 5.9 GB | Not chosen |
| TADA 1B, 3B | Text to speech | Llama 3.2 Community | Conditional | About 4 GB and 8 GB; 3.9 GB and 8.9 GB | Not chosen |
| MMAudio | Video to sound effects | CC-BY-NC 4.0 | No | About 6 GB | Hidden |
| MusicGen, AudioGen | Music, sound effects | CC-BY-NC 4.0 | No | — | Hidden |
| F5-TTS | Cloning | CC-BY-NC-4.0 | No | —; 6.7 GB | Hidden |
| XTTS-v2 | Cloning | CPML: "only non-commercial use of a machine learning model and its outputs" | No | —; 2.1 GB | Hidden |
| OmniVoice | Speech and cloning (VoiceStudio's default) | CC-BY-NC since 2026-07-03 (was Apache-2.0), plus a separate Boson licence for one of its components | No | —; 3.3 GB | Hidden |
| AudioLDM2 | Sound effects, music | CC-BY-NC-SA 4.0 | No | — | Not offered |
| TangoFlux | Sound effects | "non-commercial, research-only" | No | — | Not offered |
| ControlFoley, YuE2 | Foley, songs | CC-BY-NC-4.0 | No | — | Not offered |
| Fish S2 Pro, Higgs TTS 3, Voxtral TTS | Text to speech | Research or non-commercial | No | — | Not offered |
| Piper `en_US-lessac-medium` | Text to speech: Clips Kitty's current English dubbing voice (`multilingual/dub.py:36`) | Trained on a dataset under a "RESEARCH LICENCE AGREEMENT" | Unclear | — | Unchanged in Phase 2 (problem 8) |

- **Stability's terms fall on each creator.** The licence exempts end users only when they receive the weights "from a Licensee as part of an integrated end user product". If Clips Kitty doesn't redistribute the weights, each creator is the licensee (inferred).
- **Sources:** https://raw.githubusercontent.com/0xShug0/audio.cpp/HEAD/docs/model_licenses.md, https://stability.ai/community-license-agreement, https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html, and the models' Hugging Face cards

### Other models met in the research

| Model | Task | Licence | Commercial use | Note |
|---|---|---|---|---|
| Whisper (openai-whisper, mlx-whisper) | Speech to text | MIT | Yes | |
| silero-vad | Voice activity | MIT | Yes | 100–300× real time (Ghost Editor's own figure) |
| YuNet | Face detection | MIT | Yes | |
| SFace | Face recognition | Apache-2.0 | Yes | |
| MediaPipe Face Landmarker | Face landmarks, gaze, blinks | Apache-2.0 | Yes | 3.7 MB, runs on CPU |
| BiRefNet | Person cut-out | MIT | Yes | 2.3–3 s per frame (Ghost Editor's own figure) |
| DeepFilterNet3 | Speech clean-up | MIT or Apache-2.0 | Yes | |
| MossFormer2 | Speech clean-up | Apache-2.0 | Yes | |
| MobileSAM | Segmentation | Apache-2.0 | Yes | |
| Robust Video Matting | Matting | GPL-3.0 | Yes, under copyleft; compatible with AGPL-3.0 | |
| CrisperWhisper 2.0 | Verbatim speech to text, with fillers | Code MIT; weights non-commercial; Pro weights commercial-only | No, for shipping | The best verbatim fillers, but it can't ship in the app |
| madmom models | Beat detection | CC BY-NC-SA | No | Avoid; use librosa (ISC) or beat_this (MIT on PyPI; its weights' licence not verified) |

**Source:** https://github.com/CPJKU/madmom (and the project pages listed under Ghost Editor)

## AI providers

All pages were fetched on 2026-11-05; the page's own date is given where it shows one. What Clips Kitty may and may not do with each:

| Provider | Allowed | Not allowed | Sources and dates |
|---|---|---|---|
| Ollama, on this PC (the default) | Tool calling (`tools` returns `tool_calls`, including parallel calls). Structured output with a JSON schema in `format`, a temperature of 0 and the schema repeated in the prompt. `think` accepts true, false, null or a level | Sending `format` and `tools` in the same request: since 0.34.4, models can't call tools when `format` is json (#18944, opened 2026-10-11; fix #18975 still open), and the schema isn't enforced when a thinking model answers without reasoning (#18774, opened 2026-10-03). For JSON, use `think:false`, a schema and spare output budget, and validate in the app. Ollama's cloud doesn't support structured output | https://docs.ollama.com/capabilities/tool-calling, https://docs.ollama.com/capabilities/structured-outputs, https://docs.ollama.com/capabilities/thinking (all undated); https://github.com/ollama/ollama/releases (latest v0.40.14, 2026-11-03) |
| Anthropic (Claude) | The Claude API with the user's own API key (Clips Kitty's existing provider). Structured output is stable (`output_config.format`, strict tool use), but numeric minimum and maximum and string-length limits are unsupported, so the app checks ranges itself. A user can add Clips Kitty's local MCP server to their own Claude Desktop or Claude Code (`claude mcp add --transport stdio <name> -- <cmd>`); a `.mcpb` file packages a local stdio server for Claude Desktop | Offering Claude.ai sign-in inside the app, or collecting, storing or passing on Claude.ai credentials: "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications". Running Claude Code for users: "preinstalling or running Claude Code in your products or services … requires agreeing to our Commercial Terms of Service". Automated access is allowed only "via an Anthropic API Key" or where permitted. claude.ai on the web and on mobile can't reach a server on 127.0.0.1 (inferred: remote connectors go through Anthropic's infrastructure). The connector directory no longer accepts MCPB submissions; a local server is distributed inside a plugin | https://code.claude.com/docs/en/legal-and-compliance (undated), https://code.claude.com/docs/en/agent-sdk/overview (undated), https://www.anthropic.com/legal/consumer-terms (effective 2025-10-08), https://claude.com/docs/connectors/custom/add-unlisted (undated), https://claude.com/docs/connectors/mcp-tunnels/overview, https://code.claude.com/docs/en/mcp, https://claude.com/docs/connectors/building/mcpb (undated), https://github.com/modelcontextprotocol/mcpb, https://claude.com/docs/plugins/overview, https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| OpenAI (ChatGPT, Codex, API) | The OpenAI API with the user's own key: "API keys are still the recommended default for automation"; strict mode is recommended, with fewer than about 20 functions. "Sign in with ChatGPT": at launch for "open-source projects, personal projects that run locally, and selected private apps", with no client secret or API key needed. Calls go to `POST https://api.openai.com/v1/responses` with `store:false` and `stream:true`; function and custom tools work; system-role items are rejected; `temperature`, the output-length limit and `previous_response_id` must be left out; hosted MCP and audio or video input are unsupported; structured output isn't mentioned, so test it. Clips Kitty is free, with no paid tier, so it appears to qualify (inferred). A user's own Codex (the CLI, the IDE extension or the ChatGPT desktop app) can drive Clips Kitty through MCP (`codex mcp add <name> … -- <command>`); its "writes" mode prompts for tools not marked read-only. The Codex SDK (`openai-codex`) controls the local Codex app-server over JSON-RPC, and `codex mcp-server` was removed | App-server sign-in for commercial or hosted services: "App-server authentication has never been permitted for commercial or hosted services". A local open-source app may keep using it, but OpenAI recommends moving to Sign in with ChatGPT, which Phase 5 does for Clips Kitty's experimental ChatGPT sign-in (problem 14). Pointing requests at ChatGPT's `backend-api` endpoints. ChatGPT on the web doesn't read local Codex configuration | https://learn.chatgpt.com/docs/app-server (undated), https://developers.openai.com/siwc/quickstart (undated), https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt (undated), https://learn.chatgpt.com/docs/extend/mcp, https://learn.chatgpt.com/docs/codex-sdk (undated), https://learn.chatgpt.com/docs/auth, https://developers.openai.com/api/docs/guides/function-calling |
| OpenRouter | The user's own key, or OAuth PKCE sign-in: "Localhost callbacks are supported on any port", and requests are billed to the user's own OpenRouter account. Already built (`llm/providers/oauth.py`, `llm/providers/openrouter.py`). Tool calling is standardised across providers; Auto Exacto orders providers by tool-call error rate and is on by default for requests with tools (override with `:floor` or a provider `order`). Structured output: `json_schema` with `strict: true`; support is per endpoint, so set `require_parameters: true`. Response Healing applies only to non-streaming requests | Nothing found | https://openrouter.ai/docs/guides/features/tool-calling (undated), https://openrouter.ai/blog/announcements/auto-exacto/ (posted 2026-03-12, updated 2026-06-24), https://openrouter.ai/docs/guides/features/structured-outputs (undated), https://openrouter.ai/docs/guides/features/plugins/response-healing (undated), https://openrouter.ai/docs/guides/overview/auth/oauth (undated) |
| MCP, the protocol | Specification 2026-07-28 is current, with stdio and Streamable HTTP as the standard transports; use stdio for local servers. The `initialize` handshake was removed, and clients that support both protocol eras probe `server/discover` and fall back to `initialize`. A local HTTP server "MUST validate the `Origin` header", "SHOULD bind only to localhost (127.0.0.1)" and "SHOULD implement proper authentication". Servers MUST validate all tool inputs, rate-limit and sanitise outputs, and return validation failures as `isError:true` so the model can correct itself | HTTP+SSE, which is deprecated: "New implementations SHOULD NOT adopt it". Treating tool annotations as trusted. Leaving out the human: "there SHOULD always be a human in the loop with the ability to deny tool invocations". Clips Kitty's `server/mcp.py` knows versions only up to 2025-06-18 and answers -32601 for unknown methods, so probing clients fall back to the old handshake, but a client that speaks only the new version would fail (Phase 5) | https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http, https://modelcontextprotocol.io/specification/2026-07-28/server/tools |

Design rules the research drew for Clips Kitty:
- Present the options local first: Ollama on this PC; then the user's own AI app through MCP; then a cloud provider on the user's own key; then the experimental ChatGPT sign-in. The manual editor must work fully without AI, including on CPU-only PCs.
- Never read another AI app's stored sign-in files, never route requests through a user's Claude.ai account, and never use Claude or OpenAI logos in a way that implies endorsement (inferred).
- One versioned set of edit operations for the manual UI, the AI chat and MCP; allow-lists for fonts, codecs and resolutions; never let a model write raw FFmpeg arguments; clamp values on the engine side, as `analysis/clip_edit.py:187` already does; an `expected_revision` so stale edits are rejected (inferred). The flow: propose, validate, preview, approve, apply as one undo step, and render only when asked (ED2).
- OWASP LLM05:2025: the classic failure is "LLM output is entered directly into a system shell or similar function such as exec or eval"; take a "zero-trust approach". OWASP LLM06:2025: avoid open-ended tools, require approval for high-impact actions, and "Implement authorization in downstream systems rather than relying on an LLM."
- Anthropic, "Writing effective tools for agents" (2025-09-11): "More tools don't always lead to better outcomes"; use namespaces, truncation and actionable error messages.
- Concat's MCP has typed tools such as `clip_trim`, `clips_split` and `export_start`, an `edit_apply` that takes "any edit command, or a batch as one undo step", schemas "generated from the Concat API's own types", and edits that stay in memory until `project_save`; no approval step is documented. The counterexample is BlenderMCP's `execute_blender_code`, which "allows running arbitrary Python code in Blender, which can be powerful but potentially dangerous."
- **Sources:** https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/, https://genai.owasp.org/llmrisk/llm062025-excessive-agency/, https://www.anthropic.com/engineering/writing-tools-for-agents, https://github.com/jub0t/Concat/blob/main/docs/MCP.md, https://github.com/ahujasid/blender-mcp

Local models for tool use (vendor scores unless marked; fit inferred by the research):
- Gemma 4 on Ollama: e2b 4.6–7.5 GB, e4b 6.6–9.5 GB, 12b 7.7–8.0 GB, 26b 16–19 GB, 31b 19–20 GB. Tau2: E2B 24.5%, E4B 42.2%, 12B 69.0%, 26B 68.2%, 31B 76.9%.
- Qwen3.5 (February 2026, thinking on by default): 2b 2.7 GB, 4b 3.3–4.0 GB, 9b 6.6–7.6 GB. BFCL-V4: 9B 66.1, 4B 50.3. Qwen3.6 comes only as 27b and 35b (18–23 GB).
- BFCL V4 (updated 2026-04-12): Llama-3.2-3B 21.95%, Llama-3.1-8B 25.83%, Llama-4-Scout 28.13%, against Qwen3-8B 42.57%.
- Fit: a 6 GB RTX 2060 runs qwen3.5:4b; a 12 GB RTX 3060 runs qwen3.5:9b or gemma4:12b; a CPU-only PC runs qwen3.5:4b or 2b, slowly. Avoid gemma4 e2b and e4b and small Llama models for multi-step tool use.
- **Sources:** https://ollama.com/library/gemma4, https://huggingface.co/google/gemma-4-12b-it, https://huggingface.co/Qwen/Qwen3.5-9B, https://ollama.com/library/qwen3.6, https://gorilla.cs.berkeley.edu/leaderboard.html

## Editor UX patterns (steps 8 to 10)

Most shortcut and export details come from third-party guides to CapCut; check them against a current CapCut build.

- **Layout:** a tabbed asset panel at top left (Media, Audio, Text, Stickers, Effects, Transitions, Captions, Filters, Adjustment), the player in the centre, an inspector on the right showing "settings for whatever is selected", and the timeline at the bottom.
- **Shortcuts:** split Ctrl+B; split all tracks Ctrl+Shift+B; delete left or right of the playhead Q or W; select or split mode A or B; main-track magnet P; snapping N; audio-video linkage ~; preview axis S; zoom Ctrl+ and Ctrl−; fit the timeline Shift+Z; mark in and out I and O; keyframe panel Alt+K; speed panel Ctrl+R.
- **Main-track magnet:** deleting a clip closes the gap, and linked overlays and audio move with the main clip.
- **Trimming:** drag clip edges, or set in and out points in the source viewer before dropping a clip in; marked in and out points can export just that range.
- **Text and captions:** text is its own clip on the timeline and can be saved as a preset; one styled caption can be applied to all; auto captions can highlight keywords. Deleting transcript words cuts the matching video, which Clips Kitty already tracks as `muted_words`.
- **Audio:** music on a lane under the video, fades, loudness normalising, voice enhancement, and automatic and manual beat markers.
- **Export:** a file name and location, resolution up to 4K, a bitrate set to "Recommended" or custom (CBR or VBR, in kbps), H.264 or HEVC, MP4 or MOV, 24–60 fps. What Clips Kitty can add: custom width × height, a size estimate, automatic encoder detection, and an allow-listed "Advanced FFmpeg" section behind a disclosure (Phase 4).
- **Approachability:** one-click tools, presets and a layout that stays the same across projects. Icon-only toolbars confuse first-time users, so add text labels.
- **WCAG 2.2:**
  - Every action works by keyboard (2.1.1), with no keyboard traps (2.1.2); single-key shortcuts can be remapped or turned off (2.1.4).
  - Focus order runs media → player → inspector → timeline (2.4.3), and focus is visible and not hidden behind other UI (2.4.7, 2.4.11).
  - Trims and moves have a non-drag alternative, such as numeric in and out fields (2.5.7), and targets such as trim handles are at least 24×24 px or spaced apart (2.5.8).
  - Contrast is 3:1 for clip edges and the playhead (1.4.11), and 4.5:1 for text (1.4.3).
  - Playhead, volume and speed controls follow the ARIA slider pattern, with timecodes in `aria-valuetext`; the current scrubber already does this (`TimelineEditor.tsx:1493-1499`).
  - Splits and deletes are announced through a live region (4.1.3).
- **Sources:** https://primalvideo.com/guides/capcut-for-pc-mac-tutorial/, https://fastshortcuts.com/shortcuts/capcut/, https://www.capcut.com/help/export-clip-of-video, https://www.capcut.com/resource/how-to-add-keyframes-in-capcut, https://www.capcut.com/resource/video-caption-generator, https://www.capcut.com/help/video-quality-change-after-exporting, https://editlogic.io/best-export-settings-for-capcut-pc/, https://notjustok.com/article/how-to-add-music-and-sync-it-with-cuts-in-capcut-desktop/, https://techpp.com/2025/05/27/capcut-desktop-icons-symbols-meaning/, https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/, https://www.w3.org/TR/WCAG22/, https://www.w3.org/WAI/ARIA/apg/patterns/slider/
