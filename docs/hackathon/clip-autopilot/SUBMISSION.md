# Clip Autopilot

**Tagline:** Your viewers choose the moments. Your PC turns them into vertical posts.

## Description

Viewers make and share Twitch clips. Clip Autopilot checks a public channel's
viewer clips through the Twitch Clips API using a connected user account.
When a clip first qualifies for the streamer's view target, it enters Clips
Kitty's persistent queue once. Clips below the target are checked again on
future polls. Restart recovery and the existing publishing ledger prevent
repeat work and repeat automatic posts.

The entire viewer-selected clip becomes one vertical video, with optional
captions and the existing Gaming facecam/gameplay layout. Without a webcam,
full-frame fit preserves the gameplay. No automatic trimming or moment
selection occurs. The original viewer title is the default title, and the
default footer credits the viewer who made the clip.

Automatic mode reuses WoopSocial's multi-platform publishing and the shared
daily schedule. The desktop Watch page edits the view target and lookback,
offers explicit backfill, and displays qualification counts and timestamps.
Backfilled clips are identified as already qualified, not live crossings.
Busy-channel defaults match five posts per day and five upcoming clips.
Verified overlapping clips of the same VOD moment are skipped, while excess
qualifying clips wait for space instead of filling the processing queue.

This is free, open-source automation running on the creator's PC inside a
full editor. External services, accounts and AI providers retain their own
terms and usage limits. We do not claim this is the first or only clip
view-filtering project.

## Codex use

Codex implemented the additive Twitch Clips operation, authenticated feed,
watch migrations and target logic, whole-clip mode, desktop controls and
regressions, and entry documentation. New behavior tests were exercised
before implementation; existing scheduling and publishing behavior was
verified through its real orchestration with offline provider fixtures.
Synthetic FFmpeg media verifies rendering, and is not represented as Twitch
footage or a live crossing. See [TEST_LOG.md](TEST_LOG.md).

- Dedicated build session: `01a18b17-1906-7f23-b4c4-9a99fd010bd9`.
- Session identity is verified from the running Codex environment and thread
  metadata. Client `/status` capture remains pending; the agent cannot invoke
  that client command.
- Repository: https://github.com/ColinGPT9/clips-studio
- Entry branch: https://github.com/ColinGPT9/clips-studio/tree/feature/twitch-clip-autopilot
- Public YouTube demonstration: **PENDING — record actual operation and add URL.**

## Submission readiness

An optional, off-by-default Clip updates add-on provides Approve / Deny /
Damage Control (DMCA joke) controls in the app and on Discord. Away from desk
holds posts for approval, while the app's local model (including Gemma)
writes the shared line and interprets owner replies with a plain-text/strict
fallback. The attorney popup is satire and performs no deletion or filing.
Live bot operation remains pending. See [TEXT_UPDATES.md](TEXT_UPDATES.md).

Read [RULES_CHECK.md](RULES_CHECK.md), [NEW_WORK.md](NEW_WORK.md) and
[JUDGE_SETUP.md](JUDGE_SETUP.md). [COMPLETION_AUDIT.md](COMPLETION_AUDIT.md)
maps the brief to evidence and distinguishes incomplete requirements.
Live Twitch crossing, user-account scheduling
evidence and the public demo are pending unless marked passed in the test
log. Do not submit this draft as a claim that those steps ran. The owner
submits the Devpost form.
