# Connected Accounts

Connected Accounts belongs to the **Clips Kitty desktop engine in clips-studio**.
Twitch is the first provider. Game pipelines and other integrations reuse the
same connections; they do not implement login or receive OAuth credentials.
There is no required Clips Kitty cloud account. Existing WoopSocial publishing
continues to use its own supported API-key workflow, independently of this feature.
Discord authorization and bot installation are a separate integration effort.

## Connect Twitch

1. Register an application in the [Twitch developer console](https://dev.twitch.tv/console/apps).
   Twitch asks for two-factor authentication on the account first. The form
   needs a name, at least one **OAuth Redirect URL** (`http://localhost` will
   do: device sign-in never uses it), a **Category**, and **Client Type: Public**.
2. In **Settings → Connected accounts**, save that application's Client ID
   (letters and numbers only). Do not enter a client secret; the desktop app
   neither needs nor stores one. Replacing a saved Client ID asks first, since
   every connected account then needs reconnecting.
3. Tick the permissions to authorize (none is ticked to start with, and
   watching your own channel needs none) and click **Connect Twitch**. Open
   Twitch's official activation page in the system browser and enter the
   displayed code.
4. Complete consent in the browser. Clips Kitty polls at Twitch's interval and
   displays the account once the token's client and user identity are verified.
   If Twitch or the engine doesn't answer one poll, the card keeps waiting until
   the code expires rather than ending the sign-in.
5. Use **Test connection**, **Watch my channel**, or **Disconnect** in the card.
   An account that shows **Reconnect required** has a **Reconnect** button,
   which asks for the same permissions it had. **Watch my channel** adds the
   channel with the Watch page's Add a channel choices (from Settings: the
   Generate bar's settings, and clips wait for you before publishing). When
   Twitch doesn't confirm a disconnect removed its authorization, the card says
   so, and the app can be removed under Connections in Twitch's settings.

On Windows, Clips Kitty stores tokens in its own credential store (core/secrets,
Windows DPAPI), one entry per account. Elsewhere it uses the macOS Keychain or a
Linux Secret Service through `keyring`, which is installed only there. An
unavailable or locked OS credential store is an error; this system does **not**
fall back to plaintext token files. A stored credential that can no longer be
decrypted (a data folder restored onto another PC or Windows account) shows the
account as needing reconnection, and reconnecting replaces it. Headless Linux environments
without Secret Service can develop and test with injected test stores but
cannot connect real accounts until an OS credential store is available.

The public client ID and sanitized account roster live in the data directory.
Access and refresh tokens are stored separately. Neither API responses nor
plugin jobs contain them. Device codes remain in host memory until completion,
cancellation, expiration, or shutdown.

## Permissions and Twitch behavior

| Feature | OAuth scopes | Additional constraints |
| --- | --- | --- |
| Stream and VOD metadata | User token; no additional scope | Metadata access does not grant private-video or download rights |
| Native live clip | `clips:edit` | Desktop application route only (no pipeline, and nothing in the app asks for one); connected broadcaster only; Twitch must permit clips and be live |
| Native VOD clip | `channel:manage:clips` or `editor:manage:clips` | Verified owned archive, authorized subject, calibrated source timing; 5–60 seconds |
| VOD timestamp link | User token; no additional scope | Twitch verifies the VOD belongs to the connected broadcaster |
| Receive chat | `user:read:chat` | Host-owned `channel.chat.message` EventSub subscription |
| Send chat | `user:write:chat` | Sender is the connected user; destination is the connected broadcaster |
| Stream online/offline and channel updates | User token; no additional scope | Host-owned EventSub WebSocket and connected broadcaster |

Every scope is requested only when selected; a plain connection asks for none.
A watched channel is not an authorization grant. Existing watches for other
channels retain their original feed; the connected streamer's own watch uses
authorized Helix broadcast metadata and the existing queue/readiness checks.
When that connection needs reconnecting or Twitch does not answer, the watch
keeps reading the channel's public video list, as it did before Twitch was
connected, and says so once in the log. The video IDs are the same, so nothing
is found twice.

Twitch has separate native clip operations for a live broadcast and a VOD.
The current **Create Clip From VOD** endpoint is `POST /helix/videos/clips`;
it requires separate channel/editor authorization, an owned archive, a title,
and an integer **end** offset with a 5–60-second duration. A request returns a
processing identifier, never an invented share URL or Twitch's privileged edit
URL. `get_clip` uses Twitch's returned public URL once the clip is available.
Creation can fail when Twitch disables or refuses clipping. Use a verified VOD
timestamp link when creation is unavailable and the timing is known.

Source-file timestamps are not assumed to equal VOD offsets. Host SDK link and
VOD-clip operations require a verified mapping tied to the actual local source
identity, or a source the host measured as the whole Twitch archive (its length
matches Twitch's for that VOD), whose time is the VOD's time. See [Stream workflows](STREAM_WORKFLOWS.md) for nomination monitoring,
source calibration, and reusable catch-up approval/export. Missing calibration
is reported rather than silently linking to the wrong moment.

## Reusable core

`connected_accounts.models.Provider` defines provider authorization, identity,
validation, refresh, revocation, and named operations. `ConnectedAccounts`
serializes refreshes, saves rotated refresh credentials before reuse, validates
restored sessions and at least hourly while running, and changes unusable
connections to `reconnect_required`. It never holds its account-list lock
across a Twitch request. A damaged account list is set aside as
`connected-accounts.json.corrupt` and the app starts without connections
rather than not starting at all. Disconnecting revokes the token with the
client ID that issued it, even after the Client ID setting changed. Transient network failures do not silently
erase account credentials. Disconnect stops event listeners and plugin grants,
attempts provider revocation, and removes local credentials. If remote revocation
was unreachable, the response says `revoked: false`; users can also revoke the
application in Twitch's connections settings. A local credential deletion
failure is reported rather than claiming disconnection succeeded.

Twitch uses **device authorization**, which has no redirect callback or OAuth
`state` parameter. Its advertised capability is `pkce: false`; Clips Kitty does
not pretend it supports authorization-code PKCE.

Provider requests use fixed HTTPS endpoints, bounded timeouts, TLS verification,
and disabled redirects. Provider errors are mapped to safe messages. EventSub
owns its WebSocket session in the host, validates reconnect destinations, checks
keepalives, deduplicates bounded sanitized events, and recreates subscriptions
after a dropped connection. Plugins cannot choose a transport URL or session ID.

## Local application API

All `/connected-accounts` endpoints require `X-Clips-Kitty-Session`, including
metadata reads. These experimental routes are described in the generated
[API reference](developers/api-reference.md#connected-accounts).

Examples of trusted application operations:

```http
POST /connected-accounts/twitch:123/twitch
X-Clips-Kitty-Session: <desktop session>
Content-Type: application/json

{"operation":"stream_context"}
```

```json
{"operation":"vod_link","params":{"video_id":"456","seconds":90}}
```

Named operations are `stream_context`, `videos`, `create_clip`, `create_vod_clip`,
`get_clip`, `vod_link`, and `send_chat` (`create_clip`, a live clip, is for this
desktop route only; plugin runs work on finished VODs and cannot ask for it).
Trusted application requests use explicit VOD-relative offsets; SDK source-media
offsets are mapped by the host. These
operations enforce connected account identity and OAuth scopes; they do
not accept arbitrary endpoints or tokens. Event reception uses
`POST /connected-accounts/{account_id}/events` with `event_types`, followed by
`GET` on that path with an `after` cursor; `DELETE` stops it and clears events.

Plugins must instead use the supported
[Connected Accounts SDK](developers/connected-accounts-sdk.md). The host grants
only the installed permissions and binds each run to a Twitch-verified source
VOD, account, and broadcaster. It revokes capabilities when the process ends or
the account disconnects. Future GTA, Minecraft, and Fortnite pipelines use this
same interface; no plugin-specific authentication is required.

**Trust limit:** the existing plugin platform runs native programs as the user;
it is not an OS sandbox. The SDK handoff and host operations enforce their
permissions, but malicious same-user software can read the desktop session file
or access user-protected resources outside that interface. These changes do not
claim to isolate hostile native plugins. Install only native plugins you trust.

## Validation and live acceptance

Offline tests exercise real account lifecycle and HTTP/SDK boundaries with
fake provider responses; they make no consent requests or real clips/posts.

```bash
python -m pytest tests/test_connected_accounts_*.py tests/test_twitch_*.py \
  tests/test_connected_feed.py tests/test_plugin_account_gateway.py \
  tests/test_plugin_sdk_accounts.py
cd ui
node scripts/test-connected-accounts.cjs
npm run typecheck
npm run build
```

Live acceptance requires a public Twitch application, an unlocked OS credential
store, browser consent, and network access to `id.twitch.tv`, `api.twitch.tv`,
and `eventsub.wss.twitch.tv`. The activation page is on `www.twitch.tv`.
Verify consent/denial/cancellation, account identity, scope selection, restart,
token refresh, revoked permissions, own-channel watch discovery, live clip
creation, VOD links, chat/EventSub reconnect, and disconnect against that account.
None of this has yet been tried against a real Twitch account: live
authorization, clips, chat and EventSub are checked only with fake provider
responses. Windows DPAPI storage itself (core/secrets) is the app's existing
credential store; the Connected Accounts tests inject the OS boundary.

Official provider references:

- [Device authorization](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)
- [Refresh](https://dev.twitch.tv/docs/authentication/refresh-tokens/)
- [Validation](https://dev.twitch.tv/docs/authentication/validate-tokens/)
- [Revocation](https://dev.twitch.tv/docs/authentication/revoke-tokens/)
- [EventSub WebSocket](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)
- [Create Clip](https://dev.twitch.tv/docs/api/reference/#create-clip)
