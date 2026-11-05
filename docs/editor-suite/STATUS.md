# Editor suite status

Now: step 0 (guardrails) done, waiting to merge. Next: step 1 (export presets).

A readable view of [`state.json`](state.json), updated 2026-11-05. When the two disagree, `state.json` wins. Step statuses are todo, in progress, blocked and done.

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
| 1 | M | Export presets, encoder fix and editor lane | #203, #177 (sizes) | 0 | yes | todo |
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
