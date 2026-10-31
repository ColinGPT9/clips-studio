# Clip Autopilot test log

Date: 2026-10-30, America/New_York. Branch: `feature/twitch-clip-autopilot`.
Evidence below is offline unless explicitly labeled live. Fixture view
counts test program behavior; they are never presented as actual views.

| Check | Result | Evidence |
|---|---|---|
| Rules and entry separation | PASS | RULES_CHECK.md; new branch from fetched `origin/main`. |
| Provider tests written before implementation | PASS | New Clips operation/query, cursor, ownership and rate tests initially failed, then passed with the full provider module. |
| Public-channel HTTP permission | PASS | New selected-channel test initially returned unsupported-operation, then the account/API suite passed. |
| Feed and pagination | PASS | New feed tests initially failed for missing `clips`; daily windows, paging below target, canonical IDs, no 15-row truncation and saturated-window split are covered. |
| Invalid clip channel lookup | PASS | Added a failing regression during review; Twitch account errors now become readable watch-add errors. Full feed/watcher modules passed after the fix. |
| Sources and VOD-chat guard | PASS | New clip ID/download/chat tests initially failed, then channel-feed and hype suites passed. |
| Watch state and deduplication | PASS | New tests initially failed; under-target omission, crossing once over restart/repeated polls, edited target, zero minimum, all backlog, baseline/backfill and whole-job payload pass. |
| Whole mode | PASS | API/finder tests initially failed; one measured `[0,duration]` candidate, no finder/refinement and incompatible option rejection now pass. |
| Gaming fallback | PASS | Tests initially found center cropping/standard fallback; whole clips now fit the entire source when no webcam is found. Existing layout behavior remains covered. |
| Actual FFmpeg encoding (synthetic media) | PASS | 320×180, 1.6-second generated source encoded to 1080×1920, duration within 0.15 seconds, real ASS caption path used, no outro; full-frame fit persisted. Not live footage. |
| Understand-only detector contract | PASS | Fixture whole candidate is passed to Understand with Rate, Edit and finder absent; unchanged window reaches titles. Not evidence of the external GTA plugin running live. |
| Shared once-only schedule + viewer footer | PASS | Six separate one-clip videos use distinct slots, honor 5/day and ≥1-hour gaps, credit fixture viewer and skip repeats. Offline provider fixture. |
| Automatic watch -> shared schedule -> publish ledger | PASS | Added during completion audit: five finished viewer clips pass through real Automatic watch orchestration and WoopSocial scheduling, get consecutive one-hour slots on the same day and actual viewer credit, and remain five posts/jobs across another poll and restart. Only transport/rendered input is a fixture. |
| Busy-channel limits | PASS | New cap/overlap tests initially failed; upcoming queue is bounded, scheduled posts retain their place, excess clips are rechecked, and verified overlapping VOD moments are skipped. Defaults updated to 5/day + 5 upcoming at the owner's request. |
| Deferred backfill provenance | PASS | A new regression reproduced deferred first-look clips being mislabeled as new crossings after a restart. Persisted first-look IDs now retain zero crossing timestamps and "already has" messages for those clips; an initially under-target clip still gets its real qualifying observation timestamp. Included in both complete local gates below. |
| Midnight scheduling and multi-platform budget | PASS | CI run 41122350788 reproduced a real duplicate-slot bug across midnight. Deterministic tests failed before the fix, then passed for 09:00/20:00/23:00 UTC starts, single-clip admissions and 17-clip batches, reserved off-grid times, and YouTube/TikTok fanout. One clip consumes one shared slot; each actual UTC date stays at or below five. Included in the final full suite below. |
| Local daily start through daylight saving | PASS | Two existing chosen-time tests exposed a one-hour shift across the upcoming OS offset change. The shared scheduler now converts each local day's anchor separately. A deterministic October 31–November 1 regression also passes on this Eastern-time PC; it skips on OS zones with no change on that boundary. |
| Desktop regression | PASS | Extended `test-watch-detector.cjs`: clips source, 10,000 target, 45-day lookback, backfill, whole/Gaming payload, target autosave and no write on mount. Fixture-only. |
| Viewer-channel regression | PASS | `test-viewer-channel.cjs`; no collection POST on mount/remount and selected-channel behavior retained. |
| Connected Accounts client boundary | PASS | `test-connected-accounts.cjs`. |
| TypeScript | PASS | `npm.cmd run typecheck`. |
| Desktop production build | PASS | `npm.cmd run build`. |
| Ruff | PASS | `python -m ruff check .` at repository root, after final implementation. |
| Full pytest | PASS | Latest run including the optional approval add-on, scheduler corrections and deferred-backfill provenance: 5,510 passed, 10 skipped, zero failures/errors in 837 seconds with Bash on PATH and `PYTHONUTF8=1`. Initial collection required explicit IDs for existing HTTPError fixtures; native Windows decoding required UTF-8. |
| CI platform test set | PASS | Latest expanded set from `.github/workflows/ci.yml`, including test_automation.py and test_text_updates_api.py: 1,509 passed, 8 skipped; zero failures/errors in 5m18s. JUnit report in local TEMP, not committed. |
| API reference generation | PASS | `python scripts/gen_api_reference.py` completed. Its current generator lists routes, so this additive field-only change may produce identical output. |
| Initial GitHub CI run | FAIL, test dependency corrected | Python job in run 41007548168 lacked FastAPI and the scheduler test imported the web-layer footer helper. The scheduler test now uses already-rendered footer text; the Automatic watch integration above verifies actual placeholder substitution. Desktop, plugin platform, Windows SDK, website, vendored-code and CodeQL jobs passed. For the newest commit's GitHub result, use the PR Checks tab rather than this initial run. |

Completion-audit test addition: full Python suite and the expanded CI platform
set (now including test_automation.py) passed before the verification commit's push.
The actual GitHub failure reproduces the missing FastAPI dependency; its
fixed scheduler regression passes with only lightweight CI dependencies.
An additional native-Windows venv probe ran 4,169 tests successfully with
569 skips but had three SDK/tutorial launcher failures because that venv's
Scripts directory was not on PATH. Activating that venv made all three
launcher cases plus the scheduler regression pass (4 passed). No production
SDK code was changed for this environment issue.

A separate platform rerun exposed a pre-existing folder-name collision test
crossing a wall-clock second. Its timestamp is now fixed in that test only;
the focused regression and complete expanded platform set passed.
See COMPLETION_AUDIT.md for scope and pending live requirements.

GitHub CI run 41009213725 and CodeQL run 41009213567 both passed on
`2449f34`. The subsequent backfill-provenance fix has a failing-then-passing
regression and its complete local gates passed as recorded above. Replacement
GitHub checks must be inspected on that fix's head before claiming it green.

On `bcb7822`, CodeQL run 41122350657 passed, and all CI jobs except Python
passed in run 41122350788. Python exposed the midnight scheduler bug described
above. This is a production correction, not a removed or weakened assertion;
the test now fixes its clock and covers additional boundary/platform cases.
The corrected scheduler's full and platform gates passed before push, as
recorded above. Inspect the replacement head's PR Checks tab for GitHub status.

## Live checks — owner-operated, pending

Read-only check of the running desktop API: reachable; it has Connected
Accounts routes but not `twitch_clips` watches. WoopSocial reports disabled
and no key configured. No live watch or publication was created. Use the
updated build and enter credentials in Settings before the live checklist.

1. **PENDING:** Twitch connected and WoopSocial enabled with destination accounts.
2. **PENDING:** Add a permitted channel's clip watch; record actual qualifying
   counts, lookback, captions, layout and chosen backfill setting.
3. **PENDING:** Check now, repeat check and app restart: preserve item/job counts.
4. **PENDING:** A genuinely fresh clip crosses a target naturally. Record the
   real channel, clip URL, observed counts, poll times and evidence. Never
   invent views, crossings or timestamps. Backfill is not this test.
5. **PENDING:** Actual Twitch clip renders whole, 9:16, captions, facecam/gameplay
   (or full-frame fit when no webcam is present). Compare measured durations.
6. **PENDING:** At least 16 minutes ahead: record WatchLive, Clip Studio Schedule
   and the WoopSocial dashboard showing the same scheduled post.
7. **PENDING:** Public YouTube demo under three minutes and client `/status`
   screenshot/session capture. Record URLs without exposing credentials.

Do not turn any pending live item into PASS based on unit tests or synthetic
media. Submission text must retain these limits until real evidence exists.

## Optional Clip updates add-on

Branch: `feature/clip-autopilot-text-updates`, based on fetched entry head
`6326b19`. The session ID and official protocol sources are in TEXT_UPDATES.md.
All results in this section are offline unless explicitly marked live.

| Check | Result | Evidence |
|---|---|---|
| Discord protocol check before implementation | PASS | Current official Gateway, events, components, interaction, message, user, permission, OAuth and rate-limit docs were read first. Disabled privileged intents close with 4014; a Gateway bot needs no Interactions Endpoint URL. |
| Request wording and action contracts | PASS | Core regressions initially failed for missing modules/behavior, then passed: one saved line, bounded/factual local-model output, plain fallback, exact-item intent checking, styles/custom IDs, owner checks, expiry and a non-mutating joke reply/edit. Latest owner-requested wording and the attorney popup replace the initial joke copy. |
| Gateway and safe transport | PASS | Fake sockets exercise Hello, heartbeat/ACK, missing ACK, Resume, opcode 7/9 and 4014 fallback. Button handlers acknowledge without model work; callback and bot global rate limits are separate. A read timeout is ambiguous and is not retried. No real Discord calls. |
| Guarded settings and secret handling | PASS | All new writes reject a missing desktop session header. A delivered test plus a connected bot is required for Away. Malformed bot tokens return a fixed error instead of framework errors that echo inputs; status never returns the token. |
| Watcher decisions and gates | PASS | Away holds Automatic; Away off retains automatic publishing. App, button and typed decisions use shared helpers; first answer wins and publishing still works with watching off. Deny frees a clip-watch place. Existing pending asks are baselined on enable/re-enable. |
| Once-only event updates | PASS | Real temporary-database watcher flow plus fixture transport: ready/request, scheduled, posted, run failure, publish failure and rejection, across repeats/restart. Existing Discord buttons update when the joke setting changes. Failed posts are not mislabeled Scheduled. |
| Desktop approval fixture | PASS | `node scripts/test-text-updates.cjs`: saved wording, actual attorney popup/two-step text, zero requests for the joke, existing publish/skip endpoints, Discord-answer refresh and zero writes on mount. Fixture-only preview; no credentials or live media. |
| Existing desktop regressions | PASS | Viewer-channel, Connected Accounts and watch-detector fixture scripts remain passing. |
| TypeScript / desktop build | PASS | `npm.cmd run typecheck` and `npm.cmd run build`; optional approval UI and main/preload changes included. |
| Lightweight dependencies | PASS | Core text-update tests run with FastAPI, HTTPX and WebSockets intentionally blocked; injected sockets keep this module runnable in the main CI job. |
| Actual local Gemma, synthetic input | PASS | Installed `gemma4:e4b` generated a saved notification line, and actual inference classified “post it” as approve, “nah skip” as deny, and “dmca it” as the joke. The strict “damage control” alias also passed. The first real probe exposed destination-view wording and an unknown reply; clarified prompts and factual-output regressions corrected these. Inputs were explicitly synthetic; no Twitch crossing, Discord message or publication was performed. |
| Ruff / API reference | PASS | Root Ruff and regenerated `scripts/gen_api_reference.py --check`; seven new internal routes, stable contracts retained. |
| Full pytest / exact expanded CI platform set | PASS | Final full suite: 5,510 passed, 10 skipped in 13m57s. Exact expanded platform set: 1,509 passed, 8 skipped in 5m18s. Both JUnit reports have zero failures/errors. Includes typed/deny/failure cases and real-model prompt corrections. Superseded partial runs are not reported as passes. |

Live add-on checks remain **PENDING**:

1. Owner creates the bot in the existing app, enables Message Content Intent,
   signs in, saves the token in Settings, invites it to a private destination
   and delivers Send test. Verify the installed local-model status.
2. A real Ask-first crossing displays the saved line and thumbnail in the app.
   Open the generic attorney popup, observe both steps, close it and Approve;
   verify the real schedule in Clip Studio and WoopSocial.
3. Enable Away. A real new clip waits in both places. Use the Discord joke,
   type an approval, then Deny another item in the app and inspect its Discord
   edit. Record the first-answer/repeated-click behavior.
4. Stop Ollama and repeat with real plain messages and functioning buttons.
5. Restart with a waiting request; verify reconnect, restored card and no
   duplicate messages or publication.
6. Record the optional phone/demo beat only after it actually runs. Keep
   credentials and personal Discord identifiers out of captures. Personal
   satirical captions belong to the video material outside the repository.
