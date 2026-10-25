# Connected accounts in a pipeline

Clips Kitty owns account connections. A pipeline does not register an OAuth
client, run a login flow, store Twitch tokens, or ask the creator to connect an
account again. Connections belong to the main application and can be reused by
game-specific pipelines and other integrations.

The creator connects Twitch in Settings. When the application can verify that a
pipeline's source Twitch VOD belongs to a connected broadcaster, it grants that
run access to the operations its installed manifest declares. A channel name in
Watched Channels, or a video's display name, is not proof of authorization.

Declare only the operations the pipeline needs in `clipskitty.yaml`:

```yaml
permissions:
  - video.read
  - twitch.context.read
```

Read context using the supported SDK:

```python
from clipskitty_sdk import AccountOperationError, run


def main(job):
    # Score the video using the pipeline's own detection logic.
    job.add_range(120, 150, score=90, label="highlight")
    if job.accounts.available:
        try:
            context = job.accounts.twitch.context()
            job.log(f"Channel: {context['broadcaster']['display_name']}")
        except AccountOperationError as error:
            # Clipping still works when an account is disconnected or offline.
            job.log(f"Twitch context is unavailable ({error}); continuing with the video.")


run(main)
```

`available` means a run grant is present. Individual operations can still fail
because the account was disconnected, consent was revoked, a required scope is
missing, or Twitch is unavailable. `AccountOperationError.status` is the HTTP
status: 400 a refused argument, 401 Twitch needs reconnecting, 403 not granted,
404 gone, 412 the video's Twitch timeline is not verified, 429 a limit, 502 or
503 Twitch or the connection is unavailable (0 when Clips Kitty didn't answer).
`AccountOperationError.code` is the fixed word Clips Kitty gave, which tells
apart refusals that share a status, and the error's message follows it:

| `code` | Status | Meaning |
| --- | --- | --- |
| `invalid_request` | 400 | A value passed to the operation was refused |
| `reconnect_required`, `invalid_token`, `invalid_grant` | 401 | The Twitch connection needs reconnecting in Settings |
| `not_permitted` | 403 | This run isn't granted the operation (the manifest, or the grant ended) |
| `missing_scope` | 403 | The Twitch connection lacks the Twitch permission it needs; the creator reconnects with it ticked |
| `permission_denied` | 403 | Twitch allows it only for the connected account's own channel |
| `not_found`, `account_not_found` | 404 | The VOD, clip or connection is gone |
| `timeline_unverified` | 412 | This video's Twitch timeline isn't verified (see below) |
| `run_limit` | 429 | This run's own request limit |
| `rate_limited` | 429 | Twitch's rate limit |
| `provider_unavailable` | 503 | Twitch could not be reached or is having trouble |
| `unavailable`, `provider_not_configured`, `credential_store_unavailable` | 503 | A part of Clips Kitty's Twitch support isn't running or set up |
| `failed` | 502 | Anything else |

A code is "" when Clips Kitty sent none. No response body or provider text
reaches the message. Standalone SDK samples do not grant real account access.

A plugin that uses these needs a Clips Kitty that knows the `twitch.*`
permissions. Set `requires.clips_kitty` to the first release that bundles
Connected Accounts. An older Clips Kitty refuses the manifest as naming an
unknown permission, before it compares versions, so it says nothing about
needing a newer release ([Versioning](versioning.md#connected-accounts-and-older-clips-kitty)).

| Manifest permission | SDK operation | Host restrictions |
| --- | --- | --- |
| `twitch.context.read` | `context()`, `videos(first=20)`, `timestamp_link(seconds)` | The bound broadcaster only: its VOD list (at most 100 per request) and live status, and links into this run's VOD |
| `twitch.clips.create` | `create_vod_clip(start_seconds, end_seconds, title)`, `get_clip(clip_id)` | A 5–60-second range of this run's VOD once its timeline is verified, with Twitch's channel/editor clip-management grant; only actual returned public URLs |
| `twitch.context.read` | `source_info()`, `audience_signals()` | Source identity without file paths; nominations only after a stream-to-VOD alignment, which the app cannot make yet |
| `twitch.chat.send` | `send_chat(message)` | Bound broadcaster, authorized sender, 1–500 characters |
| `twitch.events.read` | `start_events(event_types)`, `events(after=0, limit=100)` | Host-owned EventSub socket, bound broadcaster, supported event types only |
| `twitch.chat.read` | Chat events through `start_events` and `events` | Also requires `twitch.events.read`; Twitch authorization is checked separately |

The default event types are `stream.online` and `stream.offline`.
`channel.update` and `channel.chat.message` are also supported. Use the returned
`cursor` as the next `after` value. The host retains the WebSocket session; the
pipeline cannot supply a session ID or transport address.

A run works on a finished VOD, so it cannot clip the live broadcast: whatever is
on air when the plugin asks has nothing to do with the video it found. Twitch's
Create Clip From VOD operation supports an authorized archive highlight through
`create_vod_clip()`. It needs `channel:manage:clips` or `editor:manage:clips`, a
5–60-second range and a bounded title. Twitch takes a whole-second end, so the
host rounds the end to the nearest second and the length to 0.1 s; the result
gives the VOD times it used. Creation returns a processing identifier;
`get_clip()` checks readiness and the public URL Twitch actually returned.

`timestamp_link()` and `create_vod_clip()` accept source-media time. When the
run's file is the whole Twitch archive (how Watched Channels downloads one, and
checked by measuring the file against Twitch's length for the VOD), source time
is VOD time. Otherwise the host needs an explicit calibration for that source,
which no screen in the app makes yet. They refuse unknown timing, including a
trimmed file of the same VOD, with status 412. `source_info()` returns the host
fingerprint for that calibration without a local file path.
`audience_signals()` returns only aggregate nominations mapped to the source.
Those need the live stream's chat timing aligned to its VOD, which the app does
not do by itself yet: until then the answer has status `unavailable` with a
`reason` and a plain `message`. Nomination times are when chat arrived, so they
trail the moment by the viewers' delay. See
[stream workflows](../STREAM_WORKFLOWS.md). A plugin must not guess VOD offsets
from stream creation/publishing dates.

The grant fixes the connection, broadcaster and VOD. SDK methods have no account
selector, bearer-token getter, arbitrary API URL, or arbitrary Helix request.
The host rechecks the installed manifest, enabled state and block list on every
call, then the account service checks scopes and provider authorization.

A run may make at most 100 account requests, including at most 10 native VOD
clip creations and 20 chat sends. An event subscription a run starts ends with
the run unless stream workflows or another run still needs it. Requests carry unique IDs; a duplicate write with
the same ID does not create a second clip or send a second message. An ambiguous
write failure is not retried automatically. The grant expires at the run's time
limit and is revoked when its process exits, including cancellation and failure.
Disconnecting the account also revokes its active run grants.

No provider access or refresh token is written to `job.json`, inherited in the
pipeline's environment, returned by an operation, or exposed through an SDK
method. The short-lived run capability is passed separately in the process
environment and is not a desktop session credential.

As described in [permissions](permissions.md), native plugins run with the
creator's own operating-system rights. This controlled SDK hand-over is not an
OS sandbox: an untrusted program running as the same user can attempt to access
local files and processes. Install only pipelines you trust.

These connections do not change WoopSocial publishing or its supported API-key
authorization workflow. Discord integration is developed separately against the
shared application architecture.
