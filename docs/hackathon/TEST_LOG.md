# Twitch/GTA/WoopSocial verification

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
| Live Twitch/GTA | 2026-10-26 | not-run | Requires own test stream and consenting viewers | Do not infer success from these regressions. |
| W.1-W.5 | 2026-10-26 | not-run (live) | Requires designated WoopSocial connection and reviewed own-stream clip | No new subscription or external publication attempted. |

Normal checkout settings are untouched; work is isolated in
`D:/ck-hackathon/host`, branch `feature/twitch-vod-binding`. The GTA plugin
remains unchanged while the original Fanum/Eklipse baseline is pending.
