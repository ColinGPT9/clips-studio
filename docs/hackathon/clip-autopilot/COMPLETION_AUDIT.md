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
| Pull request to main | https://github.com/ColinGPT9/clips-studio/pull/202 | Open draft; not merged |
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
| 3. First-look baseline versus explicit backfill | Parameterized first-look test and browser form regression; backfill timestamps remain zero | Verified offline |
| 4. `whole` in job create/patch/batch/options and worker config; incompatible finder modes rejected | server/api.py, server/jobs.py; whole-option API tests | Verified offline |
| 4. One measured `[0,duration]` candidate, finder/refinement bypass, captions/titles/Gaming/library retained | core/pipeline.py; whole-candidate, Understand-only and library-registration tests in test_plugin_job_option.py | Verified offline |
| 4. No-webcam full-frame fit, no center-crop fallback; whole duration without pad/outro | gaming/run.py and pipeline fallback; test_gaming.py unit cases and actual synthetic FFmpeg encode | Verified offline; actual Twitch footage pending |
| 4. GTA detector only as Understand, with no trimming/edit suggestions | Whole mode retains Understand and removes Rate/Edit/finder; fixture contract test. No GTA selector is offered for clip watches | Optional watch UI cut; no live GTA claim |
| 5. One clip through Automatic watch publishing to `publish_clips(once=True)` and next free slot | Existing publish path; `test_automatic_clip_watch_uses_consecutive_real_schedule_slots_once` exercises watch -> scheduler -> publish ledger, with only transport replaced | Verified offline |
| 5. Viewer title and `{clipper}` credit | Whole-pipeline registration test; default-footer test; shared-schedule and automatic-watch publishing tests | Verified offline |
| 6. New source, target/lookback/backfill, Gaming/captions and whole-clip note; autosaving target; badge/count/crossing display; API/types; no write on mount | Watch.tsx, WatchPublishSettings.tsx, WatchCard.tsx, lib/api.ts/types.ts; extended test-watch-detector.cjs and existing viewer-channel regression | Verified in fixture browser |
| 7. Additive API docs, connected-accounts clips row and regenerated reference/contract compatibility | docs/API.md, docs/CONNECTED_ACCOUNTS.md; generator/check and test_api_contract.py | Verified |
| Owner amendment: five/day and five upcoming, busy-channel protection | PublishSettings default 5; max_pending default 5; admission/scheduled-slot/overlap tests and browser payload assertions | Verified offline |
| Preserve existing YouTube, VOD and Kick watches; no credential commits; no fake live evidence | Existing full-suite regressions; scoped git diff and excluded local settings; explicit fixture/synthetic labels throughout entry docs | Verified within available evidence |

The requested test modules and UI commands are recorded individually in
TEST_LOG.md. New behavioral regressions were observed failing during the
initial build before their fixes. The additional Automatic integration test
audits the reused publishing implementation and needs no production change.
The final gates before pushing this audit addition passed: 5,450 full-suite
tests (10 skips), 1,497 expanded platform tests (8 skips), and Ruff.
The platform CI workflow now includes test_automation.py, ensuring the
Automatic watch publishing regression runs in the job with FastAPI installed.
The lightweight scheduler test keeps its transport/schedule coverage without
importing the HTTP layer; the watch integration verifies template substitution.

## Required live evidence still missing

The local API was reachable, but its loaded build lacked clip watches and
WoopSocial was disabled with no key. Read-only checks did not start a watch,
change settings, schedule a post or expose credentials.

| Live requirement | What proves it | State |
|---|---|---|
| Owner connects Twitch viewer and WoopSocial YouTube/TikTok | Updated desktop account/connection state | Pending owner setup |
| zackrawrr lookback covering the beta, real counts, whole/Gaming/captions, Automatic, five/day and five upcoming | Actual saved watch plus Twitch API results | Pending |
| Backfill queue, repeat Check now and app restart without duplicates | Actual item/job counts before and after checks/restart | Pending |
| Natural crossing on a currently live channel | Real clip URL, observed counts/times and resulting event; no fabricated views | Pending |
| Whole 9:16 Twitch output with captions and appropriate facecam/gameplay layout | Actual render, measured duration and visual review | Pending |
| Scheduled at least 16 minutes ahead in WatchLive, Clip Studio Schedule and WoopSocial | Matching actual post/time across all three displays | Pending |
| Record every live result without secrets | TEST_LOG.md evidence entries | Pending |
| Public demo under three minutes showing Twitch and Codex, with proper credits/backfill disclosure | Public YouTube URL and actual video | Pending |
| Owner submits Devpost | Owner's Devpost form | Owner action, not delegated to Codex |

The complete entry is **not yet proven finished**. Passing local/CI tests or
opening the PR cannot substitute for the pending live-account and video evidence.
