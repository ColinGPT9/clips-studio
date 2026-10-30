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
| Desktop regression | PASS | Extended `test-watch-detector.cjs`: clips source, 10,000 target, 45-day lookback, backfill, whole/Gaming payload, target autosave and no write on mount. Fixture-only. |
| Viewer-channel regression | PASS | `test-viewer-channel.cjs`; no collection POST on mount/remount and selected-channel behavior retained. |
| Connected Accounts client boundary | PASS | `test-connected-accounts.cjs`. |
| TypeScript | PASS | `npm.cmd run typecheck`. |
| Desktop production build | PASS | `npm.cmd run build`. |
| Ruff | PASS | `python -m ruff check .` at repository root, after final implementation. |
| Full pytest | PASS | Latest run including deferred-backfill provenance: 5,451 passed, 10 skipped, zero failures/errors in 14m36s with Bash on PATH and `PYTHONUTF8=1`, including the lookup-error and Automatic watch/scheduler regressions. Initial collection required explicit IDs for existing HTTPError fixtures; native Windows decoding required UTF-8. |
| CI platform test set | PASS | Latest expanded set from `.github/workflows/ci.yml`, including test_automation.py: 1,498 passed, 8 skipped; zero failures/errors in 5m22s. JUnit report in local TEMP, not committed. |
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
