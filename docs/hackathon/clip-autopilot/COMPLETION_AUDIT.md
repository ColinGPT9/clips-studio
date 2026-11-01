# Completion audit

Audited against the attached Clip Autopilot brief and subsequent owner
instructions: viewer account on another public channel, five posts/day,
five upcoming clips, and protection against a busy channel filling the queue.
Offline evidence proves software behavior; it does not prove the live demo.

## Rules, scope and artifacts

| Requirement | Evidence | State |
|---|---|---|
| Verify second-entry eligibility, new-work window, deadline/time zone and submission requirements before coding | RULES_CHECK.md and the recorded rules-page inspection | Verified; organizer discretion remains |
| Fetch main and create the named new branch | Base `8dab0ce`; branch `feature/twitch-clip-autopilot`; NEW_WORK.md | Verified |
| Preserve first-entry files, named branches and external GTA repository | Entry commits change no protected first-entry documents; no operations on protected branches or external repository | Verified for this work |
| Dedicated build thread and session ID | RULES_CHECK.md and SUBMISSION.md; environment/thread metadata agree | ID verified; client `/status` capture pending |
| Submission description, Codex use, repo/branch links | SUBMISSION.md | Written |
| Entry-only commits and existing foundations | NEW_WORK.md; reproducible base-to-branch log | Written |
| Judge instructions: Client ID, connect, WoopSocial and clips watch | JUDGE_SETUP.md | Written |
| Pull request to main | https://github.com/ColinGPT9/clips-studio/pull/202; merge `0963ff2e8fee5031f3bb9361be8b32f79b4ba8aa` | Merged; includes approval add-on #204 |
| Public YouTube URL and actual demonstration | Placeholder and recording plan in SUBMISSION.md / DEMO_SCRIPT.md | Pending owner/live evidence |

## Build requirements

| Brief item | Authoritative implementation and verification | State |
|---|---|---|
| 1. `clips` operation, fixed Helix endpoint, connected user token, public broadcaster selection, dates/first/cursor, sanitized metadata and ownership checks | connected_accounts/twitch.py; server/connected_accounts_api.py; test_twitch_connected_provider.py and test_connected_accounts_api.py | Verified offline |
| 1. Highest-view paging, stop below target, explicit daily windows and saturated-window splits, rate headers/429 backoff, lag notice | server/connected_feed.py and provider request handling; paging/window/rate tests; CONNECTED_ACCOUNTS.md | Verified offline |
| 1. Official restricted `clip_downloads` | Not implemented; viewer-account fallback retained | Optional scope cut |
| 2. Both clip URL shapes, canonical `tw_clip_` identity, existing downloader, no VOD chat lookup | sources/twitch.py, sources/dispatch.py, analysis/hype.py; viewer-clip source/download/chat tests in test_channel_feed.py | Verified offline |
| 3. Separate platform key, identity/footer mapping, additive migrations and allowed columns | core/state.py and server/automation.py; real temporary-database watch tests and existing watch regressions | Verified offline |
| 3. Target bounds/lookback, rechecking under-target clips before dedupe, crossing once across polls/restart, edited target, zero duration minimum, all-backlog default and newest-first catch-up | test_automation.py crossing/target/default tests; `_poll`, `_insert`, job_payload; test_connected_feed.py listing beyond 15 | Verified offline |
| 3. First-look baseline versus explicit backfill | Parameterized first-look test, deferred-backfill/restart regression and browser form regression; a watch-bound persistent first-look snapshot keeps delayed backfills' timestamps zero while initially under-target clips remain distinguishable | Verified offline |
| 4. `whole` in job create/patch/batch/options and worker config; incompatible finder modes rejected | server/api.py, server/jobs.py; whole-option API tests | Verified offline |
| 4. One measured `[0,duration]` candidate, finder/refinement bypass, captions/titles/Gaming/library retained | core/pipeline.py; whole-candidate, Understand-only and library-registration tests in test_plugin_job_option.py | Verified offline |
| 4. No-webcam full-frame fit, no center-crop fallback; whole duration without pad/outro | gaming/run.py and pipeline fallback; test_gaming.py unit cases and actual synthetic FFmpeg encode | Verified offline; actual Twitch footage pending |
| 4. GTA detector only as Understand, with no trimming/edit suggestions | Whole mode retains Understand and removes Rate/Edit/finder; fixture contract test. No GTA selector is offered for clip watches | Optional watch UI cut; no live GTA claim |
| 5. One clip through Automatic watch publishing to `publish_clips(once=True)` and next free slot | Existing publish path; `test_automatic_clip_watch_uses_consecutive_real_schedule_slots_once` exercises watch -> scheduler -> publish ledger, with only transport replaced | Verified offline |
| 5. Viewer title and `{clipper}` credit | Whole-pipeline registration test; default-footer test; shared-schedule and automatic-watch publishing tests | Verified offline |
| 6. New source, target/lookback/backfill, Gaming/captions and whole-clip note; autosaving target; badge/count/crossing display; API/types; no write on mount | Watch.tsx, WatchPublishSettings.tsx, WatchCard.tsx, lib/api.ts/types.ts; extended test-watch-detector.cjs and existing viewer-channel regression | Verified in fixture browser |
| 7. Additive API docs, connected-accounts clips row and regenerated reference/contract compatibility | docs/API.md, docs/CONNECTED_ACCOUNTS.md; generator/check and test_api_contract.py | Verified |
| Owner amendment: five/day and five upcoming, busy-channel protection | PublishSettings default 5; max_pending default 5; admission/scheduled-slot/overlap tests and browser payload assertions | Verified offline |
| Shared daily budget across midnight and multiple destinations | Deterministic schedule and publish-once regressions reproduce and correct a duplicate-slot CI failure and double-counted YouTube/TikTok fanout. Reservations honor actual UTC dates and minimum gaps; a local daily anchor is converted separately across daylight saving | Verified offline; final complete gates pass |
| Preserve existing YouTube, VOD and Kick watches; no credential commits; no fake live evidence | Existing full-suite regressions; scoped git diff and excluded local settings; explicit fixture/synthetic labels throughout entry docs | Verified within available evidence |

The requested test modules and UI commands are recorded individually in
TEST_LOG.md. New behavioral regressions were observed failing during the
initial build before their fixes. The additional Automatic integration test
audits the reused publishing implementation and needs no production change.
The base-entry gates including the deferred-backfill and scheduler fixes passed: 5,465 full-suite
tests (10 skips), 1,498 expanded platform tests (8 skips), and Ruff.
The platform CI workflow now includes test_automation.py, ensuring the
Automatic watch publishing regression runs in the job with FastAPI installed.
The lightweight scheduler test keeps its transport/schedule coverage without
importing the HTTP layer; the watch integration verifies template substitution.

## Actual-test follow-up verification

The local follow-up corrects stale processing recovery during watch events,
provider account-limit wording, connected-handle display and tomorrow's
first slot across daylight saving. Its final gates passed: 5,515 full-suite
tests (10 skips), 1,509 exact expanded platform tests (8 skips), Ruff,
API-reference consistency, TypeScript, desktop build and all five renderer/
hook regression scripts. Both JUnit reports have zero failures or errors.
Follow-up GitHub CI is separate from the earlier merged-base CI evidence.

## Required live evidence still missing

The initial local API was reachable, but its loaded build lacked clip watches
and WoopSocial was disabled with no key. On October 31 the owner quit that
older research-checkout app and Codex launched the checked desktop build from
this checkout. Its running API now exposes clip watches and text updates.
WoopSocial has a saved key and reports Instagram and TikTok connected.
The owner now limits the live test to Instagram only; connecting or posting
to YouTube or TikTok is not authorized for this test. The owner subsequently
connected a Twitch viewer account without extra scopes. Live watch #4 now
uses a 45-day lookback, a two-view observation target, five upcoming clips,
captions/Gaming/whole mode, and Instagram-only Ask-first publishing during
inspection. The pre-existing watch is temporarily disabled, with its prior
state saved for restoration. One actual 60-second clip was approved by the
owner; its initial schedule to an unintended profile was canceled before
sending. The replacement is scheduled to the verified Instagram @ClipsKitty2
at 20:47 Eastern. TikTok setup is deferred. A second item was denied in the app.
A fresh clip was observed crossing naturally from one view to two and was
admitted once after the destination-correction pause; its whole render completed.

| Live requirement | What proves it | State |
|---|---|---|
| Owner connects Twitch viewer and the authorized Instagram destination | Actual app account status: connected, scopes empty; app MCP status and actual WoopSocial post | Verified live |
| zackrawrr lookback covering the beta, real counts, whole/Gaming/captions, Automatic, five/day and five upcoming | Actual watch #4 has the 45-day window, real counts and five/five limits | Ask-first inspection verified; Automatic live phase pending |
| Backfill queue, repeat Check now and app restart without duplicates | 1,283 Earlier items survived the initial restart; all 1,288 items and brand schedule survived a post-job restart unchanged. A repeat actual poll retained exactly one fresh item/job 217 | Baseline, repeat poll and post-job restart verified; explicit first-look checkbox backfill pending |
| Natural crossing on the live-test channel | Real clip CuriousSilkyYamPunchTrees-x6atpLw5Edvgq4jv observed 1 view through 23:35:51 UTC, 2 at 23:36:36; admitted after account-correction pause as item 1317/job 217 | Verified natural counts, delayed admission, repeat poll and restart |
| Whole 9:16 Twitch output with captions and appropriate facecam/gameplay layout | Actual job 214 / clip 4304: source 60.015 s, output 60.000 s at 1080x1920; split/fit Gaming and captions visually reviewed; full decode passed | Verified live |
| Scheduled at least 16 minutes ahead in WatchLive, Clip Studio Schedule and WoopSocial | All three displays verified for original 19:48 schedule, subsequently canceled before sending. Replacement provider GET verifies Instagram @ClipsKitty2 only, 20:47 Eastern / November 1 00:47 UTC | Replacement scheduled; updated display captures and delivery pending |
| Record every live result without secrets | TEST_LOG.md evidence entries | Pending |
| Public demo under three minutes showing Twitch and Codex, with proper credits/backfill disclosure | Public YouTube URL and actual video | Pending |
| Owner submits Devpost | Owner's Devpost form | Owner action, not delegated to Codex |

The complete entry is **not yet proven finished**. Passing local/CI tests or
opening the PR cannot substitute for the pending live-account and video evidence.

## Optional Clip updates add-on audit

| Add-on requirement | Evidence | State |
|---|---|---|
| Branch from fetched entry head; PR into the entry branch | `feature/clip-autopilot-text-updates` based on `6326b19`; NEW_WORK.md; https://github.com/ColinGPT9/clips-studio/pull/204 | Merged into the entry branch, then main through #202 |
| Verify current Discord details before coding and record this session | Official sources and differences in TEXT_UPDATES.md; environment session ID | Verified |
| Off-by-default settings, Away safety gate, local Gemma/plain fallback | server/text_updates.py; guarded settings tests; local-only bounded inference | Verified offline |
| Shared first-answer decision helpers; watcher tick integration even with watching off | server/automation.py; button/typed/app, stale-answer, off-watch, baseline and Deny admission regressions | Verified offline |
| Persist one request line and one send per item/event; expiry and selected updates | core/state.py tables and text_updates service; repeat/restart/failure/schedule regressions | Verified offline |
| Bot token, owner identity, private channel/DM, invitation and Send test | Existing credential store and Discord identity reused; text_updates_api.py; settings API tests | Verified offline; real setup pending |
| Gateway lifecycle, intents, heartbeat/resume and prompt click replies | discord_bot.py and fake socket/transport tests; separate callback rate limits | Verified offline; real Gateway pending |
| Approval popup/card across pages, existing watch-row actions, notifications, Settings and Watch/tray switches | ApprovalCard.tsx, ClipUpdatesCard.tsx, main/preload bridge; real Electron fixture, typecheck/build | Verified offline |
| Damage Control satire with generic attorney popup, no state change or filing | Front-end-only popup; real nine-second app recording and unchanged-item/file/publish checks; Discord response/edit regressions | App verified live; Discord live demo pending |
| Personal video joke stays outside source/tests/docs | Add-on wording is generic; no personal caption text added | Verified for the add-on diff |
| Additive internal API reference, CI coverage, lint/full Python/platform gates | Generated reference; workflow platform list and entry-branch PR triggers; TEST_LOG.md | Ruff passed; full suite 5,510 passed/10 skipped; platform 1,509 passed/8 skipped |
| Owner live checks and optional 25-second phone/demo beat, total under three minutes | TEXT_UPDATES.md, TEST_LOG.md and alternate 2:55 recording plan | Pending owner/live evidence |

The add-on is optional. Its offline checks do not change any pending base-entry
live result into a pass, and its real bot/phone demonstration remains unproven.
