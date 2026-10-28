# Twitch/GTA/WoopSocial verification

## Current Gaming manual facecam verification, 2026-10-27

Colin clarified **Draw it**. The prepared 28 clips had been copied from the
horizontal timing comparison without Gaming render options. Actual source
frames contained his webcam; the existing manual layout path retained a drawn
source box correctly. This was a profile/render configuration correction,
with no host or detector implementation change.

- Actual source-frame, manual picker, preview and visible gallery checks passed.
- Ordinary app render jobs 2-29 re-rendered all 28 existing source intervals as
  1080x1920 Gaming splits, facecam above gameplay. Titles, source intervals and
  media durations are unchanged; original horizontal files are backed up.
- All 28 new files have audio and fully decode with 0 errors. The persisted manual
  facecam is present in every result; all 28 reported face-clear top/bottom checks.
- Actual api.exe MCP exported the corrected impound example. Queue is idle;
  saved Twitch and protected normal settings remain preserved.
- Private proof: `D:/ck-hackathon/gaming-facecam-repair/verification.json`,
  `ui-check/result.json`, `ui-check/manual-picker.png`,
  `corrected-main-gallery.png` and `all-28-facecam-review.png`.

The app replaces clip rows during re-renders, so final matching uses unchanged
source intervals rather than old clip IDs. No extra source analysis, detector
tuning, public post or new consent was performed. The 2:17 recording is test
evidence, not an approved final submission demo; later real-footage workflow
drafts remain private until source use is confirmed.

Scope: Twitch nominations, verified recording alignment, GTA integration and
WoopSocial. Discord setup, features and dedicated tests are excluded by Colin's
latest instruction. Offline results below do not prove live-service success.

| ID | Date | Result | Evidence | Notes |
|---|---|---|---|---|
| 1.3 | 2026-10-26 | pass (offline) | `pytest tests/test_woopsocial.py tests/test_woopsocial_api.py tests/test_woopsocial_once.py tests/test_publish_followup.py tests/test_clip_posts.py`: 166 passed on base `4803dad` | Existing client/API, once-only and post-state coverage; no real publish attempted. |
| 4.10-regression | 2026-10-26 | pass (offline) | `test_stopping_desktop_events_leaves_nomination_consumer_running`; failed before fix | Desktop DELETE releases its own consumer instead of stopping every listener. Live recovery remains unverified. |
| 4.7-non-live | 2026-10-26 | pass (offline) | `test_non_live_online_event_does_not_start_nomination_timeline`: four parameters failed before fix | Explicit non-live broadcasts are ignored; legacy envelopes without type remain compatible. |
| 4.11/4.12-regression | 2026-10-26 | pass (offline) | `tests/test_stream_workflows_recordings.py` | Own-owner/stream matching, bounded persisted backoff, restart, missed offline transition, expiry and explicit retry; identity discovery does not verify clock alignment. Filename follows the existing CI collection pattern. |
| 4.14/4.15-regression | 2026-10-26 | pass (offline) | `test_verified_stream_mapping_automatically_attaches_downloaded_whole_archive`; failed before fix | Human clock mapping can precede download; host attaches only proven whole-archive sources and preserves explicit nonzero crop mappings. |
| 4.7-counts | 2026-10-26 | pass (offline) | `test_exact_commands_cooldown_dedupe_and_no_viewer_persistence`; failed before fields added | Counts and bounded timeline expose aggregates without viewer/chat persistence. |
| Twitch/API subsystem | 2026-10-26 | pass (offline) | API contract plus eight related suites: 246 passed after the startup fix | Nomination store/API/SDK bridge, connected feed/accounts, EventSub and Twitch provider; generated API reference matches. |
| UI client/build | 2026-10-26 | pass | `npm.cmd run typecheck`, `node scripts/test-connected-accounts.cjs`, `npm.cmd run build` | Source build and authenticated request/URL tests. |
| Recording UI smoke | 2026-10-26 | pass (local fixture) | `D:/ck-hackathon/artifacts/recording-ui-smoke.json`, `recording-binding-form.png`, `recording-binding-saved.png` | Actual React component in a hidden Electron window: candidate prefill, unchecked-save gate, nomination timestamp, explicit check/save, correct authenticated PUT and confirmation. Screenshots visually inspected. All requests intercepted by a private fixture; no Twitch/API network call or live timing claim. |
| Startup lifecycle | 2026-10-26 | pass locally; CI revalidation pending | Initial CI run `40401666066`, Plugin platform job `121848810318`; new constructor regression failed before fix; 246 related tests pass after fix | Initial branch started a background archive worker on app construction; Linux CI exposed a race during optional-storage recovery/output capture. Resume now belongs to actual app startup, with synchronous storage recovery and worker join on shutdown. |
| Full platform local collection | 2026-10-26 | blocked by existing environment issue | `D:/ck-hackathon/artifacts/host-platform-validation.log`, `base-plugin-collection.log` | Two unchanged plugin suites fail pytest collection on this Windows Python environment in `tempfile.__getattr__` while generating HTTPError parameter IDs. The same error reproduces on untouched main; the focused Twitch/API checks pass. Remote Linux CI remains authoritative for its complete platform job. |
| CI / CodeQL | 2026-10-26 | pass at `e1a14ab` | CI `40402914230`; CodeQL `40402914398` | All six CI jobs passed, including the full Linux platform job. The lifecycle fix above is remotely verified. Later UI changes require their own checks. |
| 2.1/2.2 lifecycle | 2026-10-26 | pass (private source runtime; partial UI coverage) | `D:/ck-hackathon/artifacts/plugin-lifecycle.json`, `runtime-plugin-installed.json` | Actual plan/install/pin, disable/enable, uninstall, restart while absent, then reinstall0.4.1. Disabled/absent detector jobs return clear400 errors. This does not prove every Add Videos/Watch interaction. |
| 2.2 Watch detector | 2026-10-26 | pass (local UI fixture after fix) | `ui/scripts/test-watch-detector.cjs`; private `watch-detector-before.json` / `watch-detector-after.json` | Actual Watch form and picker: selected available detector survives refresh; uninstall refresh previously left it selected as unavailable. It now visibly selects All games and submits without the removed pipeline. Sibling cards/network are fixtures; no watch or publication created. |
| 2.4 synthetic negative | 2026-10-26 | pass (source runtime) | Actual MCP `queue_local_file`, `job_status`, `list_clips`; private `runtime-mcp.jsonl` | Job1 completed with GTA0.4.1 explicitly running and zero clips. The40s media and three-segment transcript are authored synthetic fixtures; this is not ASR or real GTA quality evidence. |
| 2.6 distributed binary | 2026-10-26 | failed compatibility check | Private `installed-plugin-plan.json` | The existing distributed api.exe rejects `twitch.context.read`; no plugin job ran in that old binary. Do not relabel this as passing. |
| 2.6 rebuilt binary | 2026-10-26 | pass (private frozen-backend runtime) | Private `backend-rebuild.log`, `rebuilt-mcp-queue.json`, `rebuilt-mcp-result.json`; binary SHA256 `fb095de739db4a0afb898cf90b74ad14c910854906972de7c29e2a703d1fe1df` | Rebuilt current backend using existing dependencies; startup and bundled-Python/process-pool smoke passed. Actual api.exe MCP queued job1, which ran GTA0.4.1 and completed with zero clips. No installer/Store package or normal installation was changed. |
| CI / CodeQL current branch | 2026-10-26 | pass at `8462379` | CI `40420570755`; CodeQL `40420570756` | All six CI jobs and CodeQL passed at this commit, including the Watch detector fallback. |
| 2.3 mode separation | 2026-10-26 | pass (authored synthetic runtime) | Private `runtime-mode-results.json`; actual MCP jobs 2/3/4 | Identical media/transcript: regular Gaming and Sports each rendered one clip; GTA returned zero. Stored render options preserve mode separation; rendered outputs fully decode. This does not validate semantic quality on sports or GTA footage. |
| 2.5 bounded failures | 2026-10-26 | pass (controlled private runtime) | Private `runtime-failure-results.json` | Unreachable optional AI retained the source-supported synthetic selection; a one-minute fixture deadline failed clearly after 63s; missing required model failed immediately without pulling weights; absent transcript transcribed tone-only media to zero segments and completed with zero clips. Missing/corrupt inputs returned 400. The normal model service was left running. |
| 2.6 positive export | 2026-10-26 | pass (authored synthetic frozen runtime) | Private `rebuilt-positive-mcp.json`, `rebuilt-export-verification.json` | Actual api.exe MCP selected source 11-32s and exported H264/AAC 1080x1920, 24.333333s including the configured end card. Full decode returned zero errors; caption frame inspected. Does not prove real gameplay or ASR quality. |
| W.1 private connection | 2026-10-26 | pass (real connection/read-only status) | Private `private-woopsocial-ready.json`, `live-service-readiness-latest.json` | Existing key/project validated in isolated data with Windows DPAPI; existing Instagram/TikTok connections listed; real MCP says connected. Default publish targets remain empty. No new project, social connection or post was created. W.2-W.5 remain unverified. |
| Matched Fanum source | 2026-10-27 | baseline jobs complete; quality review pending | Private `D:/ck-hackathon/fanum-baselines/cases.json`, both `results.json` files, `summary.json` | Eklipse GTA V stream143536633 has 17 originals. Identical full source and 1473 real ASR segments in independent app data: Gaming job1 returned 50 clips in975.7s; pinned GTA0.4.1 job1 returned7. Shared count ceiling50, allowed10-180s, horizontal without captions/end card/publishing. Gaming reached its configured ceiling. Native selection median durations22.95s and19.84s respectively; Eklipse UI median77s. These are timing measurements, not blind annotations or semantic quality scores. |
| Matched Fanum rendering | 2026-10-27 | pass (real media decode/audio) | Both private `media-verification.json` files | All57 app outputs contain audio and fully decode with0errors. Output tails exceed selected ranges by about0.4s as explicitly configured by host END_PAD, with frame quantization up to0.43s. This does not certify hook/context quality or audio audition. |
| Full local tests with corrected Windows environment | 2026-10-27 | pass at functional source8462379 | Private `host-full-tests-utf8.log`:5379passed,16skipped,810.31s | Display-ID workaround only for Python3.11.0 HTTPError collection; existing Git Bash on PATH and PYTHONUTF8=1. The previous full run failed25tests because Bash was missing and Node output used the Windows text encoding. All four affected files passed with that environment before the full rerun. No assertions/product code were changed to obtain this result. Later host changes require their own validation. |
| Live Twitch/GTA | 2026-10-27 | not-run | Viewer OAuth renewal pending; Twitch on zackrawrr, GTA on Fanum | Colin does not stream. Organic nominations only; no automated chat messages. Separate-source evidence must be labeled. |
| W.2-W.5 | 2026-10-26 | not-run (live publishing) | Requires exact approved clip and selected destination | W.1 connection validation above does not prove successful delivery. No new subscription or external publication attempted. |

Normal checkout settings are untouched; work is isolated in
`D:/ck-hackathon/host`, branch `feature/twitch-vod-binding`. The GTA plugin
0.4.1 baseline remains unchanged. Colin separately authorized one fresh-context
coding agent for the new GTA candidate, using a behavior-only specification and
existing source/fictional tests rather than raw external observations. Baselines
are complete above; candidate evaluation and live delivery remain outstanding.

## Viewer-channel continuation, 2026-10-27

Colin has viewer access only and selected zackrawrr for non-GTA Twitch testing.
Fanum remains the GTA quality source. Own-stream/OBS preparation is optional
historical material; Discord remains excluded. No chat or public clips/posts
were sent by these tests.

| Test | Result | Evidence and limits |
|---|---|---|
| Initial Twitch application | Type corrected after renewal failure | The original Clips Kitty Hackathon - Colin registration was intended to be Public, but console inspection found it was Confidential. It was replaced for this workflow by the verified Public Clips Kitty registration described below. Browser sign-in alone is not an app OAuth connection. |
| zackrawrr Watch discovery | Pass, real read-only service | Private zackrawrr-watch-test.json: seven previous recordings baselined, no backlog jobs, global automation restored off. No GTA pipeline on this non-GTA source. |
| Selected-channel API/runtime/source gates | Pass, offline | 290 focused provider/EventSub/API/workflow/SDK/feed/contract checks; four new capability tests failed before implementation. Public VOD source binding requires a verified host mapping. Existing own-channel behavior and remote-write gates retained. |
| Recovery after channel change during an outage | Pass, offline | 27 workflow API tests after the final recovery fix, including blocked old lookup versus new-channel retry. Old bootstrap cannot overwrite a new lifecycle. |
| Actual ConnectedAccountsCard | Pass, hidden Electron fixture | ui/scripts/test-viewer-channel.cjs and private viewer-channel-ui.json: login/target distinction, active channel lock, remount/resume, malformed URL refusal; zero renderer errors. This is not live Twitch evidence. |
| UI typecheck, client and production build | Pass | TypeScript, test-connected-accounts.cjs and electron-vite build; private fixture has no real account or provider writes. |
| GTA0.5.2 actual app tuning | Complete, quality approval pending | Frozen 6ab55bd on matched host3ef5530: 28 rendered clips, 10?22.96s, median14.08s, 8.9min; all28 audio/decode checks pass. 14/53 fully validated dialogue windows plus7 partial; no blind precision, recall or holdout claim. |
| Private before/after timing video | Pass, media integrity | Fanum-GTA-Timing-Review.mp4, 134.421333s, full decode passed, SHA25664c8f547544ea77fc65ce479a12d2cbaaa421283c465d9973e63db1a5689b6f0. Actual app exports plus labeled source context; Eklipse inventory text only, not provider exports. Human boundary review pending. |
| Refreshed frozen host | Build/smoke pass, current runtime not-run | a81792 backend build exit0, startup and bundled Python/process-pool checks pass. Does not contain the subsequent selected-public-channel changes; those need their own build/runtime verification. |
| Viewer OAuth / real EventSub | Pending | Earlier grant expired before exchange; fresh user:read:chat + clips:edit grant and bounded background poll active. No durable account or live chat pass claimed. |

## Completed verification at functional d766dc9

- Full current local suite: **5397 passed,16 skipped,4270 warnings in738.29s**; private host-viewer-full-tests.log. Existing UTF8/Git Bash and display-ID-only HTTPError collection workaround; no assertions changed.
- CI40571245311 and CodeQL40571245235 both completed successfully. UI typecheck, renderer client checks, production build and hidden Electron selected-channel fixture passed.
- Rebuilt backend exited0 in358s; startup and bundled Python/process-pool smoke passed. Binary SHA256 `80df549a4f2111951a89b89845e696700d62ad689f262969e8cd3d1995056cb6`; both YOLO weights included.
- Actual api.exe MCP queued/completed job1 with pinned GTA0.5.2 and read captions/exported one clip. Export15.416667s,1080x1920,H264/AAC; full FFmpeg decode0errors, caption frame visually inspected. Evidence: private viewer-build-runtime/case.json, results.json, media-verification.json, build.json. This is authored synthetic media, not real gameplay or ASR quality evidence. Temporary private backend39192 stopped after verification.
- Source8766 retains the pending viewer grant on the previous loaded modules. Do not restart until OAuth is saved or terminal. Real viewer identity/chat/EventSub remain pending. No Twitch clip, chat message or social post created.

## Real viewer connection and selected-channel checks, 2026-10-27

- Completed the already-approved Twitch application consent through the saved browser (TinyFish run79187c02-c86a-45fc-989b-76ea0c299afa). The actual app connected as colin96tv with exactly clips:edit and user:read:chat. Windows DPAPI storage and actual backend restart preserved the account; the connection test passed afterward. No token is included in this log.
- Real metadata resolved zackrawrr to broadcaster552120296, with seven archives. The channel was offline. Actual EventSub subscriptions for channel.chat.message, stream.online and stream.offline connected; two incoming chat messages were observed as aggregate delivery counts, without saving raw text or viewer identities.
- The desktop consumer joined/released while the workflow collector remained connected with chat. Explicitly stopping collection then closed the listener. Account remains connected; global automation remains off.
- The real desktop timestamp check initially failed400 because vod_link omitted broadcaster_id in its allowed-parameter list. A regression reproduced that refusal before the one-parameter repair in99ab168. Afterward212affected tests, Ruff and diff checks passed. The actual selected-channel request returned the verified archive2911450591/stream317745332471 (27240s), linking at60s.
- Private evidence: zackrawrr-live-context.json and zackrawrr-viewer-eventsub.json. These real-service observations do not establish a live stream transition, nomination timeline, verified chat/VOD clock alignment, native Twitch clip or social delivery. No chat message, public clip or social post was sent.
- Current99ab168 frozen rebuild and CI are in progress; prior full-suite/frozen-render evidence above remains associated with d766dc9.

## Submission cut frozen after user timing approval

Colin accepted the shorter jail, street-question and impound timing and prioritized a fast hackathon submission. Freeze GTA0.5.2; extra detector tuning, the untouched holdout and broader comparison are follow-up work, not blockers for the demonstrated feature branch. No unrun test is relabeled as passing.

- Functional host99ab168: CI40576303751 and CodeQL40576304492 both succeeded. The rebuilt backend exited0 in286s and passed startup/bundled Python/process-pool smoke. Binary SHA256 `13a426e17aa0883a6dec16cb1fb2e9a297d8060707215fd4a81db2346d00553b`; both YOLO weights are included.
- The actual frozen backend loaded the existing DPAPI-protected approved viewer grant in isolated data, tested the account, resolved zackrawrr, returned seven archives and verified the selected-channel VOD timestamp link. Actual api.exe MCP engine_status also passed. Private evidence: viewer-build-runtime/connected-live-verification.json and connected-build.json. This temporary frozen test backend was stopped afterward.
- The actual production renderer was captured against a real isolated backend, without API mocks; it started/stopped the selected-channel collector, previewed a completed clip, opened the editor and exported an MP4. The actual desktop main/preload also opened successfully and authenticated its API request (HTTP200, colin96tv connected). Normal app settings retain the protected SHA256.
- A137.354667s1920x1080H264/AAC submission demo was rendered and fully decoded with0errors. SHA256 `ba2b2ca876ffe54c52f64385fe0b59332057b9920e435689ecd78fd25a57d53e`. It includes actual UI captures, verified Twitch results, real Codex session notes/source diff and original synthetic footage; no Fanum/Eklipse media. Codex session ID `01a179b9-a6c3-7760-bbdf-dabc10d0b1d3` was read from matching current runtime environment fields and its actual local session record.
- Submission text, judge setup, clean source archives and a one-click local launcher are prepared. No public YouTube upload, Devpost submission, social post, native Twitch clip, catalog release or merge was performed.


## Final Public registration repair and live GTA check, 2026-10-28 UTC

- A real expired-token check exposed that the original registration was Confidential: renewal required a client secret. Twitch locks that type after creation. Console inspection found the separately created Public registration; it was renamed **Clips Kitty**, with public Client ID `0ihqn762rozi77qg12ofr7s7azjgj2` and `https://localhost` redirect. No secret is used.
- The actual running frozen backend was configured through its normal authenticated API. Colin96tv authorized exactly `clips:edit` and `user:read:chat`. Public refresh returned HTTP200 without a client secret; the rotated grant was immediately stored with Windows DPAPI, and the actual backend connection test passed afterward. No token is present in this repository.
- xQc was confirmed live in Grand Theft Auto V through the real Twitch API. The actual host collector registered broadcaster71092938/stream322149964635, received78 `channel.chat.message` events during a bounded33-second check, discovered20 archives and returned a timestamp link for archive2912487657. Collection and the EventSub listener stopped cleanly afterward. No chat or public Twitch clip was sent.
- No exact nomination command arrived during that check: nomination count0. Live nomination-to-VOD clock alignment remains unverified. The imported Fanum recording is separate from this Twitch feature check.
- A fresh actual frozen-backend MCP export of corrected Fanum clip29 produced1080x1920H264/AAC,17.466667seconds, with audio and zero full-decode errors. SHA256 `d526863886053420c6b87c0f41060f7b139db382e5d644649f247c3f4a887ff0`. The prior all28-facecam verification remains valid.
- The normal WoopSocial connections route returned Instagram and TikTok. Its original disabled setting was restored. No post was sent and terminal delivery is unverified. The normal workspace settings hash remains unchanged.
- These are current real-service/media checks, not a claim that all of the Twitch nomination, recording alignment and publishing steps passed as one live end-to-end run. Functional code and the frozen GTA0.5.2 detector remain unchanged.


## Visible live activity and free host narration, 2026-10-27 local time

- Added the live listening state and chat-message count to the Clips Studio host Settings card. It reads the existing session-protected events route, never starts a listener by loading, excludes other broadcasters, ignores replayed event sequences, and stops polling when unmounted. No GTA plugin changes.
- TypeScript checks, Connected Accounts client checks, production UI build and the actual-component fixture passed. The fixture additionally verifies selected-channel activity, replay protection and removal after stopping; private report twitch-activity-ui.json.
- A fresh actual main/preload/renderer recording started collection through the UI on xQc's live GTA V stream, visibly reached162chat messages, then stopped through the UI. The actual EventSub listener was stopped afterward. No API mocks, sent chat, public clip or social post. Exact nomination count remained0; command-to-VOD alignment is still unverified.
- Added optional scripts/narrate_local.py and requirements-narration.txt in the host. It uses the MIT kokoro-onnx runtime and pinned Apache-2.0 Kokoro assets, on CPU, without a key, subscription, cloud request or game-plugin code. Optional dependencies were installed in a separate environment. Both model-release SHA256 digests matched. Actual generation produced11non-silent scene WAVs, used in the revised demo. The helper also works with --help without optional packages installed.
- The revised demo remains under3minutes and shows actual Twitch start/activity/stop, the separate imported Fanum GTA V highlights with facecam, Marketplace categories, SDK, WoopSocial controls and actual Codex session/source/test evidence. Captured Codex records are displayed in a clearly labeled read-only viewer; they are not presented as the Codex product interface.


## Consistent xQc broadcast example in the final demo

- The user requested the same creator for the Twitch integration and highlight demonstration. The app read xQc broadcaster71092938/stream322149964635/archive2912487657. A continuous480.010-second excerpt from source16200-16680seconds was imported through the normal local-video API. No restricted clip-download endpoint was used.
- Actual frozen-backend job30 used GTA0.5.2,local Whisper transcription and the manually selected source facecam. It completed in approximately1.3minutes with123transcript segments and one supported output. The host's optional image-model preflight skipped an unsuitable image response; measured visual/audio signals and source-grounded dialogue processing remained available. Mapped audience nominations were unavailable. No detector tuning or plugin changes.
- Clip57, source316.93-335.73seconds within the excerpt, exported as19.2seconds at1080x1920with audio. Full decode passed. SHA256 `7239a8edc4e3bd4c8812fd234710d30d239736a570e1449d6e399e61b64c9db5`. Actual MCP and GUI exports had identical hashes. Actual main/preload/renderer captures show the xQc gallery and Draw it facecam picker; no mocked responses or new render jobs during capture.
- This is a retrospectively selected demonstration excerpt from the same broadcast as the earlier live xQc check, not a blind evaluation, full nine-hour VOD run or live-nomination-to-highlight timing proof. Fanum remains earlier comparison evidence; the final demo uses xQc throughout its gameplay shots.
