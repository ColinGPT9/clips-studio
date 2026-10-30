# New work: Clip Autopilot

Base: `8dab0ce` from `origin/main`, fetched before creating
`feature/twitch-clip-autopilot` on 2026-10-30.

## This entry

- Read-only Twitch Clips API operation with bounded parameters, sanitized
  metadata, selected public channels, cursor pagination and rate backoff.
- Clip feed with daily/adaptive windows, target-aware paging and canonical
  `tw_clip_` identities; finished clip downloads use the existing downloader.
- Separate `twitch_clips` watches, persisted view targets/lookbacks and viewer
  metadata, rechecking below-target clips, explicit first-look backfill and
  existing exactly-once queue/publish orchestration.
- Whole-file candidate mode that bypasses finding/refinement while retaining
  captions, viewer titles, Gaming rendering and library registration. Full
  frame fit with no webcam; no end padding or outro.
- Viewer credit footer, desktop target controls and qualification displays.
- Busy-channel controls requested during the build: five posts/day and
  five upcoming clips by default; scheduled posts hold a place, excess clips
  wait for space, and verified overlapping VOD moments are skipped.
- Provider, API, source, feed, watch, layout and shared-schedule tests;
  browser regressions for clip-watch creation and autosaving targets.
- Entry-specific documentation under this directory and additive API docs.
- Two existing HTTP-error fixture parametrizations now use explicit type
  labels because pytest 9.1.1's automatic IDs raised during collection on
  Python 3.11. Their tested behavior and assertions are unchanged.
- The platform CI job now includes the watched-channel orchestration suite,
  so the Automatic scheduling integration runs where FastAPI is installed.
  The existing job-folder collision test freezes its timestamp to remove a
  wall-clock boundary failure observed during verification.

## Entry commits

- `0ce7a9c` — Add Twitch viewer clip autopilot with whole-clip rendering and bounded scheduling.
- `028ed0c` — Return readable errors when Twitch clip channel lookup fails.
- `a6af8e3` — Document Clip Autopilot entry and verification.
- Verification commit titled `Test automatic clip scheduling and keep lightweight CI independent` — adds the Automatic watch/scheduler integration and completion audit, includes watch tests in platform CI, removes the scheduler-only test's dependency on FastAPI, and stabilizes the folder-collision fixture.

Exact self-inclusive history: `git log --oneline 8dab0ce..feature/twitch-clip-autopilot`.
Do not count earlier main-branch work.

## Already existed; not this entry's new work

Clips Kitty editor and pipeline; Watched Channels and tray polling; SQLite
dedupe, queue recovery and retries; WoopSocial integration, daily scheduling,
publish-once ledger, WatchLive and Clip Studio Schedule; Connected Accounts,
public desktop Twitch OAuth and service-owned tokens; Gaming layouts, webcam
detection and caption rendering; plugin framework, Rate/Understand steps and
the separate GTA detector plugin; all work and artifacts of the first entry,
including stream workflows and VOD binding.

The protected first-entry documentation, named branches and external GTA
plugin repository are excluded. The pre-existing local
`config/settings.yaml` edit is excluded from this entry's commits.

## Optional scope

Official restricted clip-download URLs are not implemented. Whole mode
accepts existing Understand steps, while clip-watch UI does not offer a GTA
finder or claim live GTA detector evidence. No live Twitch, social post or
demo result is claimed from unit fixtures.
