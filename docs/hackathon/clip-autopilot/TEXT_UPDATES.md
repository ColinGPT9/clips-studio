# Optional Clip updates

Clip updates is off by default. Enable it in Settings → Clip updates for
approval cards across the app and private Discord updates. Existing Discord
sign-in, webhook destinations and campaign link posting are reused unchanged.

Build session, read from the running environment:
`01a18b17-1906-7f23-b4c4-9a99fd010bd9`.
Add-on branch: `feature/clip-autopilot-text-updates`, based on the fetched
`origin/feature/twitch-clip-autopilot` at `6326b19`. Its PR targets the entry
branch, not main.

## Set up

1. Sign in under Settings → Discord. That signed-in account owns approvals.
2. In the same Discord application, open Developer Portal → Bot, create the
   bot and enable Message Content Intent. Keep the token in Settings.
3. Enable Settings → Clip updates, save the bot token and use Invite bot.
   The link requests `scope=bot&permissions=68608`: View Channel, Send Messages
   and Read Message History, without Administrator.
4. Wait for Bot connected, load the channels the bot can see, and choose a
   private text channel. Alternatively choose Send to my DMs. Send test
   creates the owner's DM when needed; Discord privacy can reject it (50007).
5. Select a local model if desired. The current local AI model is reused;
   when the main AI setting is cloud-based, an installed Gemma is selected.
   This feature never sends inference to a cloud AI service. Missing models,
   errors and the bounded inference timeout fall back to plain messages and
   strict reply parsing. No model download is started here.
6. Send test. Away from desk becomes available only after a delivered test
   and a connected bot. It is also available on Watch and in the tray menu.

Leave the application's Interactions Endpoint URL empty: this client receives
interactions through the Gateway. Bot invitation and the existing user OAuth
sign-in serve separate purposes; the bot never posts as the signed-in user.

## Decisions and updates

At the desk, Automatic watches retain automatic publishing. New Ask-first
items get an app-wide card and the matching controls in Ready to publish.
Away from desk holds Automatic items too, asking in the app and on Discord.
If the bot disconnects later, the items keep waiting in the app and Settings
shows the error. Merely going silent never posts or skips an item.

The line is written once and stored. Both places use that saved wording.
Cards show the thumbnail, destination platforms, first slot and an editor
link. An unfocused app sends a desktop notification; clicking it focuses the
app and the approval card. Pending cards refresh on automation events and
every 30 seconds, with no write on mount.

- Approve and Deny use the existing watched-channel publishing/decline
  helpers. The first saved answer wins across the two places. Discord
  decisions are applied by the watcher, including when watching is off.
- An app decision updates the Discord message and disables its buttons.
  A Discord decision updates the app card, which closes after a few seconds.
- The grey **Damage Control** button is the DMCA joke. In the app it opens
  a **Contact your attorney** popup, shows Ringing, and changes its text after
  about two seconds. Discord uses an interaction reply and one delayed edit.
  The satirical wording mentions deleting the stream's VODs/clips and
  contacting an attorney over an unwanted online clip. No file, VOD, clip,
  approval or copyright request is changed. Approve and Deny remain available.
- Show the Damage Control joke button controls the app card, Watch row and existing
  Discord requests. Owner-specific video jokes are kept out of the repository.
- After 24 hours Discord requests expire and their buttons are disabled.
  The item remains Ready to publish in the app for a manual decision.
- Typed replies resolve a quoted request first, then an explicit item number,
  then a single pending item. The local model's result must name that exact
  waiting item and an allowed intent. Plain parsing accepts approve/yes/post,
  deny/no/skip, or dmca/damage control, optionally followed by the item number. Ambiguous
  replies get one clarification rather than a guessed action.

Ready, scheduled, posted and failure updates are selectable; Posted is off
initially. Turning the feature on baselines already-true events and old
requests. Restarting retains pending requests and the once-only send ledger.
A send that might have reached Discord is never automatically repeated.

## Discord protocol check made before implementation

The current official documentation supports the requested v10 endpoints and
component layout. Two operational details affect setup: requesting a disabled
privileged intent closes the Gateway with 4014, and Gateway interactions
require no configured Interactions Endpoint URL.

| Detail | Official source and implemented handling |
|---|---|
| Bot invite and permission mask | [OAuth2](https://docs.discord.com/developers/topics/oauth2), [Permissions](https://docs.discord.com/developers/topics/permissions): 1024 + 2048 + 65536 = 68608; no Administrator |
| Gateway v10 JSON, bot URL, sequence, heartbeat/ACK, Identify/Resume and opcodes 7/9 | [Gateway](https://docs.discord.com/developers/events/gateway), [Gateway events](https://docs.discord.com/developers/events/gateway-events): independent daemon and reconnect/backoff; missing ACK reconnects |
| Intents | [Gateway intents](https://docs.discord.com/developers/events/gateway#gateway-intents): 37377 with Message Content; after 4014 reconnect with 4609, retain buttons and ignore typed replies |
| Button styles and identifiers | [Components](https://docs.discord.com/developers/components/reference): one action row, styles 3/4/2, item-bound custom IDs; optional joke button |
| Interaction replies | [Receiving/responding](https://docs.discord.com/developers/interactions/receiving-and-responding): callbacks within three seconds without model work; types 7 and 4, original-response PATCH for the delayed joke; tokens last 15 minutes |
| Sending/editing and mentions | [Messages](https://docs.discord.com/developers/resources/message): channel message POST/PATCH, content below 2000 characters, mentions disabled |
| Owner DMs and privacy failure | [Users](https://docs.discord.com/developers/resources/user#create-dm), [Status codes](https://docs.discord.com/developers/topics/opcodes-and-status-codes): create only on Send test; handle 50007 as an undelivered test |
| Rate limits and uncertain sends | [Rate limits](https://docs.discord.com/developers/topics/rate-limits): honor retry_after and rate headers, keep interaction callbacks separate from the bot global limit, reserve sends before transport and retry only known-unsent failures |

The bot token uses the existing credential store and is masked in Settings.
It is never returned by status, included in a query string, fed to the model,
or printed. Incoming reply text is classified but not stored or logged. New
internal writes require X-Clips-Kitty-Session. The generated API reference
lists the seven internal /text-updates routes.

## Evidence

Core, Gateway, HTTP, watcher and UI fixtures are offline checks. See TEST_LOG.md
for their final gates. A real owner sign-in, bot invitation/test, natural
crossing, phone interaction, fallback with Ollama stopped and restart demo
remain PENDING until the owner runs them. No fixture result is live evidence.
