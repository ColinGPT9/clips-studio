# Editor suite status

Now: step 0 (guardrails) done, waiting to merge. Steps 1 (export presets) and 2 (make clips again) built and checked, waiting for Colin's click-through and his word to merge. Step 3 (caption fonts, animations and safe zones) is built and checked (2026-11-11), waiting for Colin's click-through and his word to merge. Step 4 (creator notes, free music and sound effects) is built and checked (2026-11-12), waiting for Colin's click-through and his word to merge.

A readable view of [`state.json`](state.json), updated 2026-11-11. When the two disagree, `state.json` wins. Step statuses are todo, in progress, blocked and done.

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
| 2 | S | Make clips again; edits follow a widened clip | #190 | 0 | yes | checks done; click-through next |
| 3 | M | Caption fonts and animated presets | #174 (fonts and captions) | 0 | yes | in progress |
| 4 | S | Creator description; CC0 sound and music library | #174 (creator notes), #155 (library) | 0 | yes | in progress |
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
- **Performance, one named exception:** a limited export whose first try comes out over is made again, 2-pass. Keep under 100 MB at Balanced on the 150 s clip took 232 s, against 122 s at step 1's first version, for the same file.
- **Colin's call on that (2026-11-06):** keep the rule; the engine doesn't change. In Settings → Export the limit becomes a tick box, "Keep files under a size limit", off by default. Ticked, it fills in the last limit used (or 100) and shows "⚠ Slower" beside it. No WoopSocial caveats in user-facing text: the smaller copy's tooltip, the guide and the changelog no longer mention clips over about 55 minutes.
- **Open:**
  - Estimates: a changed frame rate is under-estimated (1.31x at 30 fps); bars, blur and calm clips are over-estimated; H.264 Balanced came out 9-17% over on 4 real clips.
  - Hardware encoders and AV1 were checked as arguments only.
  - The WoopSocial copy of a clip over about 55 minutes is over 95 MB.

**Step 2 (make clips again; cuts follow a moved start or end), 2026-11-06 to 2026-11-09: checks done; Colin's click-through is next.**
- **Built:** "Make clips again…" in the Library, prefilled from the video's last run. Clips anyone used are kept; the rest are replaced only after the new run succeeds and made clips. Cuts, mutes and muted words stay on the same moment of the video when a clip's start or end moves (`shift_edit`). `core/pipeline.py` is unchanged.
- **QC and an adversarial data-safety review took 6 fix rounds** (rounds 3, 5 and 6 on Colin's go). They closed every route found to delete a clip someone used:
  - clean-up during a run;
  - exports in other languages;
  - races and the publish plan;
  - the app being killed partway (a journal plus start-up recovery);
  - edits made in another version (a fingerprint of the generated words);
  - marks that erasing a creator's learning removed.
  The defects are 2-D1 to 2-D32.
- **Accepted known limit (Colin, 2026-11-08):** a hidden plugin suggestion stops keeping its clip after a plain Process again.
- **Black-box:** S1, S2, S2b, S4, S5 and S6 passed. In S2, the edited and exported clips were kept byte for byte, 7 untouched long clips were replaced and 15 new clips were added; S2b's older clips were all kept. S3 failed on a hidden whole-video setting and passed after a fix: a visible "Whole video" switch, an honest log line and an on-screen result (2-D32).
- **Performance:** passed. Plain renders are byte-identical to the golden record and no slower.
- **UX:** two keyboard majors (focus return and focus trap) were fixed. Six minors are left for the click-through.
- **For Colin's click-through:**
  - clips made before this update are kept, so the dialog mostly says "0 will be replaced";
  - "Make clips again" means different things in Add videos and the Library;
  - the queue chip says "Reprocess";
  - the "(one clip)" hint beside the Whole video switch.
- **Open (seen by the tester, not changed):**
  - After a remake, the files of removed clips and the `.clip-<id>` copies made while a player held a file can stay on disk (about 650 MB across two remakes in the sandbox), and Storage clean-up doesn't reclaim the copies.
  - The clip list can keep a stuck "Finding the best moments" bar after a remake while the clip editor is open, until a reload.
  - Clip cards show the window, not the length after cuts.

**Step 3 (caption fonts, animations and safe zones): built and checked 2026-11-11.**
- **Checks (2026-11-11):** black-box S1 to S7 all passed with no faults filed; performance passed (plain clips byte-identical, nothing slower than 4.9%, caption styles within noise, fonts 1.08 MiB); UX and accessibility passed with minors only (the example now honours reduced motion).
- **Last fixes (3-D17 to 3-D20, by the orchestrator on the owner's call, not re-reviewed by an independent agent):** the style's size and margin round as before on every frame height; karaoke is burned plain for right-to-left languages; karaoke on a cut or sped-up clip spreads the fill by word length; a staged font's temporary copy has a short name so long clip folders keep their font. Tests in `tests/test_caption_late_fixes.py`.
- **For the click-through:** Apply can be clicked twice while a render runs (the second job fails with "No clip with id"); translated exports use the Subtitles tab's own font, not the clip's; Space on a preset chip in the editor plays the video (Enter picks it); the bundled fonts draw smaller than Arial at the same size; the opened font list should show each bundled font in its own face.
- **Built (engine):** 12 OFL fonts in `assets/fonts/` with a sha256 manifest, served to the preview (`GET /fonts`); animations (pop, fade, bounce, karaoke, slide) and safe zones (TikTok, Reels, Shorts) in the ASS captions; six caption looks (`config/caption_presets.json`, `GET /caption-presets`). A bundled font is copied beside the captions and burned with `fontsdir=fonts`; with a Windows font every argument and the ASS text are as before (the step 0 snapshots pass unchanged). The contract is [`api/step3-captions.md`](api/step3-captions.md).
- **Built (UI):** the caption controls everywhere they appear: Presets chips (Classic first), the font list in two groups (Windows fonts, Fonts that come with Clips Kitty) with each name in its own face, Animation and Safe zone, the highlight switch shown as included while Karaoke is on, and an example that plays the animation, shades what the chosen app covers and puts the caption where it will burn. The Clip Editor's caption preview uses the bundled fonts, karaoke and safe-zone margins. The preview loads the fonts from `GET /fonts` under the new `font-src`.
- **QC fix round 1 (3-D1 to 3-D8, all fixed):** a bundled font is kept only for a language whose every letter its file has (read from the font's character map; the manifest's scripts were rebuilt from the same maps, and a test checks them); an animation carries on across the word highlight's events, so a short first word never cuts it off; pop and bounce keep the lines libass makes at 100% (breaks written out when the size change would move them) and bounce overshoots less when 110% would reach the margins; captions on screen together get no pop, bounce or slide, so they still stack; a sliding bottom caption in a safe zone stands 40 px higher and the side margins hold the outline and shadow; the render worker protocol is 2, so a worker on an older app is told to update.
- **QC fix round 2 (3-D9 to 3-D11, all fixed):** the line breaks pop and bounce write out now follow libass's own evening-out step by step (lines measured without their trailing spaces); burning 18,720 captions in 22 fonts with the bundled FFmpeg found none that differ, and a close call that could go either way (49 of them) is left to libass as before. With a safe zone, a caption with one word too wide for the safe area (CONGRATULATIONS at the default size on TikTok) is drawn smaller so the word stays clear of the buttons; pop and bounce grow to that size. A font this copy of the app doesn't have (for example an engine built without its fonts folder) still burns in Arial, and now the job log says so.
- **QC fix round 3 (3-D12 to 3-D14, all fixed):** a caption with a hyphen, a dollar or a percent sign (TWENTY-FIVE, $500, 50%) is now measured like any other, so pop and bounce keep its lines and bounce stays inside the safe zone (the bundled libass breaks lines only at spaces; a second burned sweep found one more thing to fix, Lilita One's "y", whose outline reaches past the box in its own header). A caption that still can't be measured (an emoji, Thai, Hindi, Arabic) is no longer scaled by pop or bounce: it fades in at full size. Fade on a caption shorter than a third of a second uses shorter fades, so a quick single word reaches full colour instead of flashing. With a safe zone, Chinese, Japanese and Thai captions (no spaces, so one long word) are measured and drawn smaller like a long English word; Hindi and Arabic are not measured yet, and the user guide says so.
- **QC fix round 4 (3-D15 and 3-D16, both fixed):** with an animation or a safe zone on, a caption is now written with single ordinary spaces, so what is measured is what is burned. Words joined by a no-break space (pasted text, or French captions before ? and !) no longer run under the app's buttons, and a doubled space typed in the caption editor no longer makes a bouncing caption flash to two lines. A caption with no animation and no safe zone is left exactly as typed.
- **Open:** Colin's click-through.

**Step 4 (creator notes; free music and sound effects): engine and UI built 2026-11-11; the checks are next.**
- **About this creator:** one text field per creator (600 characters, tidied when saved, never refused), read and saved through the creator routes (`POST /creators/{id}/description`). Filled in, it is one labelled line of background in the prompts that find, score and order clips and in the one that writes titles, descriptions and hashtags, also for a creator nothing was learned about and with learning off. It adds no points. Wiping learned memory keeps it; deleting the creator removes it.
- **The guarantee:** with nothing written, every prompt is byte for byte what the app sent before. The 15 prompts of three runs (no creator, a creator with nothing learned, a creator with learned context) were recorded with the app before this step (commit 14a2f8c) and with this one: identical. A test keeps comparing with that recording.
- **Found while building:** before this step the creator context reached only the title prompt and the capped bonus; the prompts that pick clips carried nothing about the creator. The note is now in those too when it is filled in.
- **The library:** `assets/library/`, 9.2 MB, all CC0 1.0: 8 music tracks by HoliznaCC0 (Free Music Archive; each cut to 75 seconds with a fade out, set to -18 LUFS, OGG Vorbis) and 56 unmodified sound effects from four of Kenney's packs, a licence file beside each, and a manifest with every file's sha256. Served to the UI by `GET /assets/library` and `GET /assets/library/{file}`. FreePD is offline, so nothing came from there.
- **Music on a clip:** `edit.music` may name a free track by its id. The render plays the bundled file; an unknown id, a sound effect's id or a damaged file gives a clip without music and a line in the job log, never a failed render. A clip with the person's own file, or with no music, renders with the same arguments as before (no existing snapshot changed).
- **Remote rendering:** a clip with a free track goes to the render PC; a clip with the person's own file still renders here. The protocol number is 3, so a render PC on an older app is told to update. A render PC that cannot play the track (missing, damaged, not listed) hands the clip back and it is made on this PC, with the reason in this PC's job log.
- **Checks by the builder:** snapshot tests without `--update-snapshots`, the golden check (4 cases identical), the CI Plugin platform list (1995 passed, 8 skipped), the CI light full run (the 27 known Windows-only failures, 5590 passed), ruff.
- **The UI:** on the Creators page, an "About this creator" box at the top of a creator's profile: it saves when you click away (and when you leave the page), shows the text as the engine stored it, stops at 600 characters and shows a counter from 500. Its tooltip gives an example and says the note stays on this PC unless cloud AI with your own key is on. In the Clip Editor's Effects tab, a "Free music" button beside Browse opens the 8 tracks with mood and length; ▶ plays one (one at a time, stopped when the list, the tab or the editor closes), clicking a name puts it under the clip and clicking it again takes it off. A free track and your own file clear each other. Sound effects are not listed. Checked in headless Edge against the sandbox (28 checks: saving, the limit, the engine asked for the file with a byte range, one sound at a time, Apply sending `{"library": id}`); typecheck and build pass.
- **QC fix round 1 (4-D1 to 4-D7, all fixed):** a render PC that cannot play a clip's free track hands the clip back instead of returning it without music, and this PC makes it; curly brackets in a creator note can no longer be filled in by a prompt template; a sound effect's id is not accepted as a clip's music; the eight tracks were encoded again at one loudness (-18 LUFS, peaks under -1 dBTP; they differed by 6 LU and six peaked at full scale); the picker's four mood words are literal translation keys; the changelog and guide no longer name the sounds' authors; the guide's example note no longer ends in an instruction.
- **Black-box fix round 1 (4-D8, fixed):** S3 found a free track picked with the app's own settings (Vol 25, Duck under voice on) could not be heard on street footage. The clip's sound never goes quiet there, so the ducking held the music about 20 dB down for the whole clip: 36 dB under the voice, measured on the mix before encoding. A free track now has its own, gentler ducking (about 7 dB under a normal voice) and starts at Vol 40, because the tracks are stored quieter than a typical song. Rendered again through the app, the same clip has the music about 20 dB under the voice, and what was added matches the track (0.40 to 0.83; it was 0.06 to 0.23). The person's own music file keeps the old ducking, so those clips render exactly as before (no existing snapshot changed).
- **Open:** the old ducking has the same weakness for the person's own music file on a clip that is loud throughout; changing it changes existing renders, so it is Colin's call. Nobody has listened to the tracks yet, so their moods and the sound effects' names come from the sources' own names and genres; the author names of the bundled sounds are now only in NOTICE, `assets/library/` and these build docs (Colin's call whether the changelog and guide should thank them by name); a render PC without a track retries the clip three times before this PC makes it; QC, black-box, performance and UX checks; Colin's click-through.

**Found in existing code while building step 1 (not changed):** FastAPI answers 500 instead of 422 for a request holding a number that parses to inf or nan (app-wide, also in the released app). The Original export copy answers 500 for a clip id SQLite can't hold. A NUL character in an export folder name is cut at the NUL on Windows.
