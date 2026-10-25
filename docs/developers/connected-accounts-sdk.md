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

Read context or request a timestamp link using the supported SDK:

```python
from clipskitty_sdk import AccountOperationError, run


def main(job):
    # Score the video using the pipeline's own detection logic.
    job.add_range(120, 150, score=90, label="highlight")
    if job.accounts.available:
        try:
            context = job.accounts.twitch.context()
            link = job.accounts.twitch.timestamp_link(120)
            job.log(f"Highlight link: {link['url']}")
        except AccountOperationError:
            # Clipping still works when an account is disconnected or offline.
            job.log("Twitch context is unavailable; continuing with the video.")


run(main)
```

`available` means a run grant is present. Individual operations can still fail
because the account was disconnected, consent was revoked, a required scope is
missing, or Twitch is unavailable. Standalone SDK samples do not grant real
account access.

| Manifest permission | SDK operation | Host restrictions |
| --- | --- | --- |
| `twitch.context.read` | `context()`, `videos(first=20)`, `timestamp_link(seconds)` | Only the bound broadcaster and source VOD; at most 100 VODs per request |
| `twitch.clips.create` | `create_clip(has_delay=False)` | Native Twitch clip of the current live broadcast, subject to authorization and `clips:edit` |
| `twitch.clips.create` | `create_vod_clip(start_seconds, end_seconds, title)`, `get_clip(clip_id)` | Source-bound calibrated times, separate Twitch channel/editor clip-management grant; only actual returned public URLs |
| `twitch.context.read` | `source_info()`, `audience_signals()` | Source identity without file paths; minimal nominations only after verified source/VOD calibration |
| `twitch.chat.send` | `send_chat(message)` | Bound broadcaster, authorized sender, 1–500 characters |
| `twitch.events.read` | `start_events(event_types)`, `events(after=0, limit=100)` | Host-owned EventSub socket, bound broadcaster, supported event types only |
| `twitch.chat.read` | Chat events through `start_events` and `events` | Also requires `twitch.events.read`; Twitch authorization is checked separately |

The default event types are `stream.online` and `stream.offline`.
`channel.update` and `channel.chat.message` are also supported. Use the returned
`cursor` as the next `after` value. The host retains the WebSocket session; the
pipeline cannot supply a session ID or transport address.

`create_clip()` asks for the broadcaster's current live moment. Twitch's separate
Create Clip From VOD operation supports an authorized archive highlight through
`create_vod_clip()`. It needs `channel:manage:clips` or `editor:manage:clips`, a
5–60-second range and a bounded title. Creation returns a processing identifier;
`get_clip()` checks readiness and the public URL Twitch actually returned.

`timestamp_link()` and `create_vod_clip()` accept source-media time, which the
host converts through explicit calibration for that source. They refuse unknown
timing, including a different trimmed file of the same VOD. `source_info()`
returns the host fingerprint for that calibration without a local file path.
`audience_signals()` returns only aggregate nominations mapped to the source.
See [stream workflows](../STREAM_WORKFLOWS.md). A plugin must not guess VOD
offsets from stream creation/publishing dates.

The grant fixes the connection, broadcaster and VOD. SDK methods have no account
selector, bearer-token getter, arbitrary API URL, or arbitrary Helix request.
The host rechecks the installed manifest, enabled state and block list on every
call, then the account service checks scopes and provider authorization.

A run may make at most 100 account requests, including at most 10 native-clip
creations and 20 chat sends. Requests carry unique IDs; a duplicate write with
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
