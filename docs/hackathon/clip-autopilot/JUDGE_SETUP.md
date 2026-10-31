# Run Clip Autopilot

Optional: after completing the base setup, follow [TEXT_UPDATES.md](TEXT_UPDATES.md)
to enable app/Discord approvals. It requires the owner's Discord sign-in,
bot token, private channel or DM and a successful test before Away from desk
can be enabled. The base entry works with Clip updates off.

Use Windows and the entry branch. This feature runs inside the existing
Clips Kitty desktop app; it is not an additional hosted service.

You do not need to stream yourself. Connect a viewer account and watch a
public channel you have permission to reuse, such as the demo channel in
the brief. Five posts/day plus five upcoming clips controls volume even
when that channel has a large audience making many clips.

1. Clone https://github.com/ColinGPT9/clips-studio and check out
   `feature/twitch-clip-autopilot`. Follow the root README for Python 3.11,
   engine dependencies, FFmpeg and desktop setup. From the repository root,
   `python main.py serve` starts the loopback API. In `ui`, use `npm ci` and
   `npm run dev` for the development desktop. Use `npm.cmd` in PowerShell
   when its script execution policy blocks `npm.ps1`.
2. Configure an available AI backend in Settings. Caption transcription
   needs the configured Whisper model/provider. The app's existing model
   checks apply; it does not bundle account credentials for judges.
3. Settings → Connected accounts: enter a Twitch application **Client ID**
   registered as a public client, then connect your Twitch account through
   the device approval page. A viewer account is sufficient. No client
   secret or extra scope is needed to read public clips.
4. Settings → WoopSocial: enable it, enter your own key and connect the
   destination accounts. Keep keys in Settings. Choose Ask first or Only
   clip to inspect output before authorizing publication.
5. Watch → Add a channel → **Twitch clips (made by viewers)**. Enter a
   channel you are permitted to reuse, a view target and a lookback of
   1–90 days. Gaming layout is automatic; choose captions as desired.
6. Unticked backfill lists already-qualified clips as Earlier with a
   **Clip this** action. Tick **Also post clips already over the target
   from the lookback window** to queue them on first look. The distinction
   is displayed, and backfill never creates a live-crossing timestamp.
7. Choose the publishing mode and platforms. The defaults match five
   posts per day with five upcoming clips at most. Choose Automatic and
   spread enabled. Switch Watch channels
   on and use Check now. The app must stay running and the PC awake.
8. Open the resulting clip in Clip Studio. The candidate covers the whole
   source file; output is 9:16 with Gaming layout/captions. No webcam uses
   full-frame fit. Original clip title and viewer credit are defaults.
9. Check WatchLive's scheduling message, Clip Studio → Schedule and your
   WoopSocial dashboard. Allow at least 16 minutes when checking ahead of
   a scheduled post. Rechecking or restarting must not queue/post again.

Targets and lookbacks autosave in each watch's Publishing panel. Twitch
view counts can lag and polling is periodic: the crossing time means the
app's first qualifying observation. Raising/lowering a target does not
forget already-recorded clips. Keep watching from the tray is available;
sleeping the PC stops work.

For a busy channel, the upcoming limit includes scheduled posts. Excess
qualifying clips are reconsidered when space opens. Substantially overlapping
clips on the same verified VOD are skipped; without VOD offset metadata,
overlap cannot be verified, and the queue/posting limits still protect volume.

## Offline verification

```text
python -m ruff check .
python -m pytest
python scripts/gen_api_reference.py --check
cd ui
npm run typecheck
npm run build
node scripts/test-watch-detector.cjs
node scripts/test-viewer-channel.cjs
```

For the full Python suite on native Windows, enable UTF-8 and make Git
Bash available to the tutorial regression before invoking pytest:

```powershell
$env:PYTHONUTF8 = '1'
$env:PATH = 'C:\Program Files\Git\bin;' + $env:PATH
python -m pytest
```

See [TEST_LOG.md](TEST_LOG.md) for actual results and pending live checks.
Fixtures and synthetic media are explicitly separate from live evidence.
Never put credentials, temporary download links or `session.secret` in
screenshots or evidence. The public demo URL is tracked in SUBMISSION.md.
