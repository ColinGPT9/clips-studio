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

## October 31: merged code and actual app preflight

- PR #204 merged into the entry branch as `6eb02395`; #202 then merged into
  main as `0963ff2e`. The separately authorized first-entry documentation PR
  #199 merged as `f88bea2c`; this follow-up changes no first-entry files.
- Post-merge main commit `f88bea2c40d33b945619c14597e1d25db074cc91` passed
  [CI](https://github.com/ColinGPT9/clips-studio/actions/runs/41173928365)
  and [CodeQL](https://github.com/ColinGPT9/clips-studio/actions/runs/41173928295).
  Python, plugin platform, Windows SDK, desktop and website jobs passed;
  the vendored-code job was skipped by its condition. Both CodeQL analyses passed.
- After the owner confirmed quitting the older research-checkout app, Codex
  launched this checkout's checked desktop build. Actual `/health` reports
  app version 2.1.0; its OpenAPI schema includes clip-watch targets and the
  text-updates routes. No fixture server was used for these prerequisite checks.
- At this check, Twitch has no saved Client ID or connected account. The
  owner requested setup help. No authorization or live Twitch API result
  is claimed.
- Actual WoopSocial status reports enabled with a saved key. Its connections
  endpoint reports Instagram and TikTok; YouTube remains to be connected.
  This verifies existing connections, not a new scheduled post.
- Watching is off, no jobs are active, and optional Clip updates is off
  without a connected Discord owner/bot. No watch, job or publication was
  created by this preflight. The existing local settings edit is preserved.
- Prepared a 22.021-second, 1920x1080/30-fps H.264 editorial insert outside
  the repository (`codex-evidence-and-close-22s.mp4`). It shows the actual
  Clips API source, verified build-session ID, saved JUnit totals and repo
  end card. Re-reading the reports confirms 5,510 passed/10 skipped and
  1,509 passed/8 skipped, with zero failures/errors. Its evidence manifest
  records source/report hashes. Card screenshots were visually reviewed;
  the final export's dimensions/duration and full FFmpeg decode passed.
  This silent insert is not the client `/status` capture, a live demo,
  or the completed public video. No pending live check changes to PASS.

## Owner amendment: Instagram-only live test

The owner explicitly limits the remaining test to Instagram. Do not connect,
schedule or post to YouTube or TikTok for it. Keep five posts/day and five
upcoming clips. This narrows the test destination, not the product's existing
multi-platform support.

An actual stdio MCP session (`python main.py mcp`) initialized successfully,
listed the app's 19 tools and called `engine_status`, `queue_status` and
`woopsocial_status`. The engine is running, nothing is processing or waiting,
and WoopSocial reports connected with Instagram last used. The local API
also confirms Instagram is the selected destination. No publishing tool was
called and no connection request was started. The current MCP tool list
does not expose Twitch application registration or account sign-in; Twitch
still has no saved Client ID or connected account.

## Live checks — October 31, partial completion

The earlier stale-app preflight is historical. The updated app is now running
against its actual database and the owner's connected services. Evidence
files remain outside the repo; they contain no OAuth tokens or media URLs.

1. **PASS:** Actual account status confirms a connected Twitch viewer with no
   extra scopes. WoopSocial is enabled with Instagram selected and connected.
2. **PARTIAL:** Actual watch #4 uses the permitted channel, a 45-day lookback
   covering the requested beta, target 2, Gaming/whole/captions, five/day and
   five upcoming. Backfill is off for the first-look observation. Publishing
   is Ask first during inspection; the Automatic live phase remains pending.
   The existing non-test watch is temporarily disabled so it cannot send to
   another destination. Its original state is saved outside the repo.
3. **PARTIAL:** First look recorded 1,283 Earlier clips and queued none. The
   app was gracefully quit and restarted: all 1,283 remained, with no new
   jobs. Two recent clips at one view remain absent below the two-view target.
   Later qualifying older clips produced jobs 214/215 exactly once across
   repeated checks. After jobs 217/218 completed, another graceful restart
   preserved all 1,288 items and the brand schedule exactly, with an idle
   queue. A real repeat poll then retained fresh item 1317 and its single
   job 217; no second job or post was created for that clip. Explicit first-look
   backfill with the checkbox remains pending live verification.
4. **PASS (natural count crossing and delayed admission):** Twitch clip
   `CuriousSilkyYamPunchTrees-x6atpLw5Edvgq4jv`, made at 22:50:32 UTC,
   was observed at one view from 23:04:19 through 23:35:51 UTC, then at two
   views at 23:36:36 UTC. The target was already two; no counts were changed
   or views stimulated. The watch was paused during destination correction.
   After resuming, it admitted the clip once at November 1 00:10:37 UTC as
   item 1317/job 217. Admission was delayed, not claimed at the first count
   crossing. This is a fresh observed crossing, not a lookback backfill.
   Job 217 completed and produced one whole clip (#4307): source and output
   both 13.683333 seconds, output 1080x1920, captions visible, full decode
   passed. The existing fit/blur Gaming fallback preserves the full source.
   This extra clip is held for review and has not been approved or scheduled.
   The source channel was live during setup; it had ended by the later
   verification at 00:11 UTC. No continuously live claim is made for admission.
5. **PASS:** Actual job 214 produced exactly one whole clip (#4304), starting
   at zero. FFprobe: source 1920x1080, 60.015 s; output 1080x1920, 60.000 s.
   The difference is under one video frame. Stored render options retain whole
   and split/fit Gaming; actual frames at 8/35 seconds show captions and the
   facecam/gameplay layout. A full FFmpeg decode passed.
6. **PARTIAL (destination corrected):** The owner approved #4304 and used
   the app's Approve action. Its original October 31 19:48 Eastern schedule
   appeared in WatchLive, Clip Studio and WoopSocial at least 21 minutes ahead.
   The owner then identified the connected Instagram profile as the wrong
   destination. That pending post was deleted at 23:38 UTC, before delivery;
   an actual provider GET confirmed its absence. The three screenshots are
   historical evidence of this canceled schedule, not delivery evidence.
   After the owner authorized replacing the old connections and connected
   Instagram @ClipsKitty2, the same approved clip was scheduled again through
   the app's actual MCP for October 31 20:47 Eastern / November 1 00:47 UTC.
   Provider GET confirms exactly one destination, Instagram @ClipsKitty2,
   with the viewer credit retained and delivery NOT_STARTED. No second
   WoopSocial approval was needed. New dashboard capture and delivery remain
   pending. No YouTube or TikTok post was created for this test.
7. **PENDING:** Public YouTube demo under three minutes and client `/status`
   screenshot/session capture. Record URLs without exposing credentials.

Do not turn any pending live item into PASS based on unit tests or synthetic
media. Submission text must retain these limits until real evidence exists.

### Live app approvals and popup

The owner also denied the second item in the app; its clip remains in the
library and has no publication. The live Damage Control popup was recorded
for nine seconds: Ringing, then the generic attorney text. Before/after
checks prove item #1314 unchanged, zero new publish rows and the rendered
file retained. The 1920x1080 recording's full decode passed. The app's saved
line, Approve and Deny are live evidence; Discord/phone operation is not.

### Progress recovery discovered during the live run

The UI retained a processing banner after the queue became idle. Continuous
watch events reset the shared quiet timer, starving the authoritative job
check after a terminal event was missed. A regression executes the actual
hook with continuing events and an idle backend; it failed before the fix.
The hook now verifies periodically during activity as well as quiet periods.
Its regression passes for completed, running/queued, unreachable and idle
cases and is included in desktop CI. The final follow-up gates passed below.

### Account identity, error wording and daylight saving follow-up

The actual account-link error was HTTP 402 with `social account limit reached`.
It was incorrectly shown as monthly credits exhausted. The classifier now
recognizes connected-account limits, reads the provider's `error_message`,
and avoids inventing monthly credit exhaustion for other 402 responses.
The connections API returns only platform, account ID and username; approval,
watch-publishing and Settings controls show the connected handles. Regression
checks verify the identity is visible before approving and tokens are omitted.
The owner deferred TikTok setup; only Instagram @ClipsKitty2 is authorized for
this post. The wrong destination's canceled post is not a public delivery.

The first follow-up full/platform gates exposed a separate first-slot bug on
the eve of daylight saving: tomorrow's chosen local clock inherited today's
fixed UTC offset. The scheduler now selects the local calendar date before
UTC conversion. Fall and spring boundary regressions and the failed preview
check pass after the correction. The complete final gates passed before push;
the superseded daylight-saving failures are not reported as final passes.

The updated desktop was built and restarted successfully after completed
jobs. The live connections endpoint now reports the actual connected handles.
The exact expanded platform suite passed with 1,509 passed, eight skipped,
zero failures/errors. Ruff, API-reference consistency, TypeScript, desktop
build, viewer-channel, Connected Accounts, watch-detector, text-updates and
job-watch recovery checks passed. The final full suite passed with 5,515
passed, ten skipped, zero failures/errors in 14m14s. The exact expanded
platform run took 5m37s. Saved JUnit reports confirm both totals. GitHub
follow-up checks must be inspected on the pushed commit before claiming CI
passed for these fixes; the earlier main CI links apply to the merged base.

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
