# Stream workflows

The desktop host collects viewer nominations and prepares catch-up digests for
connected Twitch broadcasters. Game pipelines use the narrow Connected Accounts
SDK; they do not import the host workflow package, open EventSub sockets, or
receive provider credentials. The workflow service is reusable by game pipelines
and optional delivery integrations.

## Monitor viewer nominations

Monitoring requires the connected broadcaster's own Twitch account and
`user:read:chat` consent. The host subscribes to `stream.online`,
`stream.offline`, and `channel.chat.message` through its existing EventSub
manager. It recognizes the exact chat commands `!clip`, `!chaos`, and `!funny`.
Other chat is discarded by the workflow collector. Stopping collection ends the
chat subscription and clears buffered chat, unless a running plugin that was
granted chat still needs it.

Nominations are supporting evidence for a possible highlight, not a command to
publish a clip. The collector applies a 60-second per-viewer cooldown across all
three commands within a stream and deduplicates EventSub events. A viewer's
identifier is hashed using a session-only secret for the in-memory cooldown;
neither viewer identifiers nor their hashes are saved. Raw chat is never written
to the workflow database.

SQLite retains a bounded set of minimal stream metadata, command counts and
timestamps. Its files are created the first time stream workflows are used, not
at app start; a damaged one is set aside as `<name>.corrupt` and a new one
started, so it never stops the app from starting. Persisted event hashes prevent a replay after restart from counting
the same event again. Event hashes identify events, not viewers. Retention and
row bounds limit this history; this is not a chat archive.

The aggregate defaults are 30-second buckets and 30-day retention, with at most
200 streams, 20,000 aggregate rows, and 50,000 event hashes. The in-memory
cooldown cache holds at most 10,000 viewer hashes. An SDK response exposes at
most 100 nomination buckets. Monitoring is off after restart; retained stream
metadata, aggregates, and verified mappings survive. Active stream associations
are cleared on restart and when monitoring stops. Starting monitoring obtains a
verified own-channel current stream, retrying for a few minutes while Twitch
cannot answer, and looks again after every fresh EventSub subscription: Twitch
does not replay a `stream.online` sent while the socket was reconnecting. Delayed lifecycle events cannot replace
a newer stream or reopen an ended stream.

Connected accounts now shows per-command totals and a bounded 30-second
nomination timeline. The desktop event-feed stop action releases only its own
consumer; it does not stop nomination monitoring. Explicit non-live online
events (such as reruns or playlists) do not establish a live nomination timeline.

After a recorded stream ends, the host looks for an own-channel archive with
the same `stream_id`. Lookup backoff starts at 15 seconds and is capped at 15
minutes, with a six-hour discovery deadline. The deadline, attempts and next
lookup survive restart. A confirmed offline lookup after the app missed the end
event also starts discovery; its end timestamp is the offline observation time,
not a measured broadcast end. No workflow files are created for an unused feature.

`GET /stream-workflows/{account_id}/streams` adds `commands`, `timeline`
(latest 200 nonempty buckets), `timeline_truncated`, `bucket_seconds`, `mapping`
and `recording`. A recording is `pending`, `ready` or `expired`; readiness means
its archive identity matched, not that its clock alignment is verified.
`POST /stream-workflows/{account_id}/recordings/{stream_id}/retry`, protected by
the desktop session, explicitly restarts discovery for an ended stream. The
Connected accounts card refreshes active monitoring/discovery every 15 seconds.

## Bind a recording to its live timeline

In Settings > Connected accounts, choose **Bind recording** for an ended
stream. The matching archive URL is prefilled when discovery succeeds; a URL
may also be pasted. Open the nomination-time link, compare with the stream
clock, enter the verified offset, and explicitly confirm before saving. The
server still rechecks the archive owner and stream identity.

This screen binds the live clock to the VOD. After download, host media
provenance may attach the file identity and zero local-source offset only when
the file is verified as the whole archive. It never replaces a nonzero
cropped-source mapping. A cropped file still requires the explicit source
alignment described below. Jobs that finished before binding need to be rerun
to use the nominations.

An EventSub `stream.online` event supplies the Twitch stream ID and stream start
time. Twitch Get Videos exposes `stream_id`, which can correlate an archive with
that live stream. A VOD's `created_at` is **not** the stream start time and is not
used as a substitute.

A trusted user or service must explicitly verify the recording alignment using
the account ID, broadcaster ID, VOD ID, stream ID, stream start, and offset in
seconds. A separate `source_offset_seconds` identifies where the pipeline's
local recording begins in the Twitch VOD. A host-generated `source_id` binds
that offset to the specific local media file; another trim of the same VOD
requires its own verified alignment. The host also checks the actual
provider VOD's owner and `stream_id`. Unknown stream start, a different owner, a
different stream, an unverified alignment, a missing source offset, or a
mismatched source identity prevents
nominations from being exposed for the local recording.

The plugin gateway supplies only the verified binding for the pipeline's own
source. The workflow service returns bounded nominations relative to that
recording. It returns an empty result with a reason when the binding cannot be
verified. A plugin cannot select a different account or source to read another
broadcaster's nominations.

For a chat event, the mapping uses:

```text
VOD seconds = event time - stream start + offset_seconds
local source seconds = VOD seconds - source_offset_seconds
```

If the Twitch VOD begins 12 seconds after the stream, its verified
`offset_seconds` is `-12`. If the pipeline's downloaded or edited source begins
90 seconds into that VOD, `source_offset_seconds` is `90`. Even a complete VOD
requires explicit `source_offset_seconds: 0`; its creation timestamp does not
prove alignment. Nominations outside the known VOD or source duration are
excluded. Aggregate rows that straddle a source boundary are omitted because
the collector cannot reconstruct an exact count for the partial bucket. Returned evidence distinguishes local `offset_seconds` from
`vod_offset_seconds`. A trusted application can save an explicit alignment with:

```http
PUT /stream-workflows/twitch:123/vod-mapping
X-Clips-Kitty-Session: <desktop session>
Content-Type: application/json

{"broadcaster_id":"123","vod_id":"456","stream_id":"789","stream_start":"2026-10-24T12:00:00Z","offset_seconds":-12.0,"source_offset_seconds":0.0,"source_id":"sha256:replace-with-host-source-info-id","verified":true}
```

The account, recorded stream, and actual Twitch archive must match those
identifiers. The example values do not authorize a mapping by themselves.
Use the host `source_info` result for `source_id`; plugins never provide a
filesystem path or invent the identity. A mapping without both a matching
source identity and an explicit offset remains unavailable for local source
evidence and timestamp conversion.

## Prepare and approve a catch-up digest

A digest selects the top 3, 5, or 10 candidates and presents factual titles.
Links come from host-verified Twitch recording timestamps or actual Twitch clip
records. Provider credentials, clip edit URLs, and caller-supplied arbitrary
URLs are not digest content.

Manual approval is the default. Automatic approval requires explicit opt-in.
Preparing a digest and approving it are idempotent, and the host saves the digest
and its approval state so the workflow can resume after restart.

Preparation accepts 1–100 candidates and `top_k` of `3`, `5`, or `10` (default
`5`). A candidate contains a finite `score`, an optional title of at most 160
characters, and either `vod_id` plus `offset_seconds` in the **VOD timeline**, or
`clip_id`. Local pipeline times must be converted using the verified source
offset before preparing this trusted application request. The host resolves
only the selected candidates, ordered by descending score with ties retaining
their input order. A missing VOD title becomes `Highlight at HH:MM:SS`. The
following preparation waits for manual approval:

```json
{
  "request_id": "catchup-stream-789-v1",
  "top_k": 3,
  "auto_publish": false,
  "destination_id": 1,
  "candidates": [
    {"vod_id": "456", "offset_seconds": 120, "score": 92, "title": "Escape through the tunnel"},
    {"clip_id": "VerifiedPublicClipId", "score": 85}
  ]
}
```

`request_id` is unique within an account. Reusing it returns the saved digest
instead of replacing its reviewed content. JSON responses include the digest
`id`, `status`, selected `items`, and creation/update timestamps. State and
portable JSON snapshots live in `stream-digests.sqlite3` and `stream-digests/`
inside the application's data directory. Export adds `schema_version: 1`.

The desktop host delivers through an existing saved Discord webhook destination
when preparation explicitly includes its positive integer `destination_id`.
Choose that ID from Settings → Discord destinations. The destination and content
are frozen by `request_id`; reusing it cannot redirect reviewed content. No
destination is selected automatically. An approved digest with no destination,
no stored webhook secret, or a removed or unavailable destination remains
`unsent` and can be exported. Other hosts may inject their own transport adapter;
approval without an adapter also remains `unsent`.

The Discord adapter uses the same saved webhook secrets and message sender as
the rest of the app. Mentions are disabled. All selected links fit in one
message; long titles are shortened to fit Discord's content limit. Forum
destinations use their saved thread, or create a thread titled `Stream catch-up`.
Approval rechecks the current connected Twitch account, destination, and secret.
It honors the saved destination send spacing and persists Discord's destination
and global rate-limit delays. A known refusal or a request that never connected
remains `unsent`; another explicit approval may attempt delivery after the
problem or delay is resolved. There is no background delivery retry.

The approval states are `pending_approval`, `unsent`, `sending`, `sent`, and
`delivery_unknown`. The host saves `sending` before calling a transport and
supplies a stable digest idempotency key. An ambiguous transport failure or a
restart during sending becomes `delivery_unknown`; approval does not retry that
write. Discord webhooks offer no server-side idempotency key, so the host's
durable state prevents a second attempt after an uncertain write, including a
timeout or 5xx response. Transport receipts and exception details are not
persisted or exposed.

## Local application API

Every route below requires the desktop `X-Clips-Kitty-Session` header. These are
trusted application routes; pipeline plugins use their run-scoped SDK grant.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/stream-workflows/{account_id}/monitor` | Check chat consent and start the host monitor |
| `DELETE` | `/stream-workflows/{account_id}/monitor` | Stop monitoring |
| `GET` | `/stream-workflows/{account_id}/streams` | List retained stream summaries |
| `PUT` | `/stream-workflows/{account_id}/vod-mapping` | Verify and save explicit VOD alignment |
| `GET` | `/stream-workflows/{account_id}/digests` | List saved digests |
| `POST` | `/stream-workflows/{account_id}/digests` | Prepare an idempotent digest |
| `POST` | `/stream-workflows/{account_id}/digests/{digest_id}/approve` | Approve and attempt configured delivery |
| `GET` | `/stream-workflows/{account_id}/digests/{digest_id}/export` | Export a saved digest |

Account ownership and current connection status are checked by the host. These
routes do not accept Twitch bearer tokens, EventSub transport URLs, or arbitrary
provider request URLs.
