# Handoff: post Watched Channel clip links to Discord

Delete this file before merging.

## The one thing to build

When Watched Channels automation publishes a clip, post its **title and public link** to a Discord channel once the post is live, through a webhook the user chose. Nothing else: no campaign screens, no message templates, no docs or changelog, no refactors.

1. **Setting.** `PublishSettings` (`server/automation.py:89`) gets one optional field: the id of a Discord destination (`discord_destinations` table), validated to exist. It is a JSON field, so no migration is needed. `WatchPatch` (`:140`) accepts it.
2. **Posting.** For a watch with a destination set, each post that automation actually published (read from `clip_publishes`, not from the publish call's return value) is sent once it is live, as `"{title}\n{url}"`. Never send twice, across retries, restarts and re-renders.
   - Smallest wiring with the existing engine: one internal campaign per destination, created on first use with `StateDB.insert_campaign` (`core/state.py:1897`), method `discord_webhook`, no rules.
   - Attach with `campaigns/queue.py::attach_for_watch` (`:507`). It is already built and tested, but nothing calls it yet. Call it from a durable step in `ChannelWatcher.tick()` (`server/automation.py:532`), driven by the DB.
   - `PublishWorker` already runs `queue.advance` (`campaigns/queue.py:822`), which waits for the live link and sends the webhook safely.
   - To reopen a row, use `queue.reopen`, never `statemachine.apply("reopen")`.
3. **UI.** On the Watch page (`ui/src/renderer/src/pages/Watch.tsx`), add one "Send links to Discord" dropdown in a channel's settings, None by default, listing `discord.destinations()` from `ui/src/renderer/src/lib/campaigns.ts:125`. Show it only when a destination exists. Strings go through `t()`.

The user adds the webhook in Settings → Discord, a card that already exists.

## What is already on this branch (reviewed and tested)

- **Publishing fixes:** posts are followed until they are live with a link (`server/posts.py`).
- **The `campaigns/` package:** tables, the submission state machine, URL resolution, the webhook sender `campaigns/discord_webhook.py`, and the submission engine in `server/publisher.py`.
- **Discord:** sign-in and webhook destinations (`campaigns/service.py`, `server/discord_api.py`), and the Settings Discord card (`ui/src/renderer/src/components/DiscordCard.tsx`).
- **Do not apply** the local `stash@{0}` (abandoned campaign UI). It isn't on GitHub anyway.

## Tests

Keep them minimal and synthetic. Never call real Discord: see `tests/discord_fake.py` and `tests/campaign_fake.py`, and copy the patterns of `tests/test_automation.py` and `tests/test_campaign_worker.py`.

1. Publish → live → exactly one webhook message.
2. A retry or restart → no second message.
3. No destination set → the watch behaves exactly as before.

CI's main Python job has only pyyaml, ruff, pytest and requests, so tests needing fastapi or httpx must `pytest.importorskip` them. CodeQL flags side effects inside `assert`, unused imports, and one module imported both as `import x` and `from x import y`.

Before committing, check:
- `python -m ruff check .`
- `npm --prefix ui run typecheck`
- `python scripts/gen_api_reference.py --check`
