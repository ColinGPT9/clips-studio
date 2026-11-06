# Editor suite status

Now: step 0 (guardrails) done, waiting to merge. Step 1 (export presets) built and checked, waiting for Colin's click-through and his word to merge.

A readable view of [`state.json`](state.json), updated 2026-11-06. When the two disagree, `state.json` wins. Step statuses are todo, in progress, blocked and done.

## Phases

| Phase | What | Status |
|---|---|---|
| 1 | Setup: working documents, agent graph files, promo kit and baseline benchmarks | in progress |
| 2 | The essential editor that closes all seven issues (#192, #144, #190, #177, #203, #174, #155), plus a built-in voice-over. The run stops after this phase. | in progress |
| 3 | Competitive AI editing | later, on Colin's go |
| 4 | Advanced editing | later, on Colin's go |
| 5 | AI-assisted editing | later, on Colin's go |
| 6 | Motion graphics | later, on Colin's go |
| 7 | Audio and voice | later, on Colin's go |

## Phase 1 tasks

| Task | Status |
|---|---|
| Working documents in `docs/editor-suite/` | done |
| Agent graph files (local, committed only if Colin wants) | in progress: the black-box tester is written |
| Promo kit, kept outside the repository | todo, before the first promo |
| Baseline benchmarks, taken before any change | done on the CPU encoder; app startup and editor-open times move to step 1 |

## Phase 2 steps

In run order. Size S, M or L is small, medium or large. "Users see it" marks the steps that get Colin's click-through, a section in [`USER-GUIDE.md`](USER-GUIDE.md) and a promo video.

| Step | Size | What | Closes | Depends on | Users see it | Status |
|---|---|---|---|---|---|---|
| 0 | S | Guardrails | — | — | no | done |
| 1 | M | Export presets, encoder fix and editor lane | #203, #177 (sizes) | 0 | yes | in progress |
| 2 | S | Make clips again; edits follow a widened clip | #190 | 0 | yes | todo |
| 3 | M | Caption fonts and animated presets | #174 (fonts and captions) | 0 | yes | todo |
| 4 | S | Creator description; CC0 sound and music library | #174 (creator notes), #155 (library) | 0 | yes | todo |
| V1 | M | Voice service (Voicebox) | — | 0 | yes | todo |
| 5 | M | Project core | #192 (foundation) | 0, 1, 2, 3 | no | todo |
| 6 | M | Media serving | #144 (foundation) | 1, 5 | no | todo |
| 7 | L | Export compiler (engine) | #192, #144 (engine) | 1, 3, 5, 6 | no | todo |
| 8 | L | New editor UI (beta) | #192, #144 | 1, 5, 6, 7 | yes | todo |
| 9 | M | Captions and text in the editor | #174 | 3, 7, 8 | yes | todo |
| 10 | M | Audio in the editor | #155, #174 (SFX) | 4, 7, 8 | yes | todo |
| V2 | M | Voice-over tool | — | V1, 9, 10 | yes | todo |
| 11 | M | Any aspect ratio for AI framing | #177 (aspects) | 0, 1, 7 | yes | todo |
| E | M | Electron upgrade | — | — | no | todo |

Gates outside the table:
- Step E needs Colin's one-line OK first, and must land before the new editor (step 8) reaches users.
- Step 2's proof names project clips, which only exist from step 5. This is open.
- Each step lands as one pull request, merged when Colin says so, after his click-through and once that PR's CI is green.

## Results and defects

Full records are in `state.json`.

**Step 0 (guardrails), 2026-11-05: done.**
- **Snapshots:** 38 argv snapshots cover 14 light-tier cases plus 9 cropper modes and the 15-case pipeline matrix.
- **The guard works:** QC changed the x264 CRF, a caption style default, the music duck ratio and the vertical scale in turn. The snapshot tests failed every time and passed after each revert.
- **Golden renders:** 4 cases, bit-exact from run to run on the CPU encoder.
- **Import guard:** QC found that it read only `core/pipeline.py` (defect 0-D1). It now follows imports through other modules, and that is fixed.
- **Baseline (#203 evidence):** real 1080p60 clips on the CPU encoder come to 48–54 MB per minute, so a vertical clip passes 100 MB after about 2.1 minutes. NVENC wasn't available on the dev PC, because the bundled FFmpeg needs NVIDIA driver 610 or newer.

**Found in existing code while building (not changed by step 0):**
- **Ducked music ends early:** up to 0.26 s early, by a different amount each run, because `sidechaincompress` stops when either input ends. Padding both inputs and trimming the output was byte-identical in 8 of 8 trial runs.
- **Clips carry 96 kHz audio:** `loudnorm` resamples to 192 kHz. As a result, the shipped 48 kHz end cards are never reused, and each format's card is rebuilt on first use (about 20 s).

**Step 1 (export presets, encoder fix, editor lane), 2026-11-05 to 2026-11-06: checks done; Colin's click-through is next.**
- **First run:** QC passed after 4 fixes (1-D1 to 1-D4). Black-box scenarios S1-S10 passed: Original stays byte-identical, and a 150 s 1080p60 clip exported under 100 MB (95,707,128 bytes). Performance passed: the plain pipeline is within +0.9% / -1.2% of the base, with byte-identical outputs. UX passed with 8 minor issues.
- **Two real faults outside the scenarios** sent step 1 through a fix round:
  - Keep under 5 MB gave a colourless, partly grey file that was reported as a success (1-D5).
  - A generous limit made files bigger (1-D6). The HEVC estimate was also 30-50% high (1-D7).
- **Fix round:**
  - **Floor:** a measured watchable floor: 700 kbps of H.264 at 1080x1920 60 fps, scaled for size, frame rate and codec. A limit below it gives a file marked "over the size limit" with its reason, and the card warns beforehand.
  - **Never bigger:** a clip that may fit, or whose size can't be told beforehand, is made as without the limit. It's checked, and made again only if over, and the smallest file made is kept.
  - **The rest:** the HEVC estimate is measured for x265. A graphics card that fails at a remake hands it to the CPU. The 8 UX and 2 black-box minors are fixed.
- **Colin's call (2026-11-06):** export sizes start at 720p.
- **Checks after the fix round:**
  - QC: 1,857,492 no-limit argv unchanged; 646,464 limited plans swept, none estimated bigger than without the limit.
  - Black-box: R1-R7 passed; with and without Keep under 100, High gives the same sha256.
  - UX passed (4 minors, fixed).
  - A docs audit: about 30 sentences reworded (1-D20).
  - 21 defects in all, every one fixed.
- **Performance, one named exception:** a limited export whose first try comes out over is made again, 2-pass. Keep under 100 MB at Balanced on the 150 s clip took 232 s, against 122 s at step 1's first version, for the same file. That trade-off is Colin's to keep or change.
- **Open:**
  - Estimates: a changed frame rate is under-estimated (1.31x at 30 fps); bars, blur and calm clips are over-estimated; H.264 Balanced came out 9-17% over on 4 real clips.
  - Hardware encoders and AV1 were checked as arguments only.
  - The WoopSocial copy of a clip over about 55 minutes is over 95 MB.

**Found in existing code while building step 1 (not changed):** FastAPI answers 500 instead of 422 for a request holding a number that parses to inf or nan (app-wide, also in the released app). The Original export copy answers 500 for a clip id SQLite can't hold. A NUL character in an export folder name is cut at the NUL on Windows.
