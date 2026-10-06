# Gaming clippers: Overwolf/Outplayed, Medal, Allstar, Sizzle, SteelSeries Moments, NVIDIA Highlights, Powder, Framedrop, StreamLadder

- Track: D (specialised gaming clipping platforms). Tier 2: Overwolf/Outplayed. Tier 3: the rest.
- Date read: 2026-10-06 (every URL below was opened on that date unless marked "not confirmed").
- Brief's question: what does a specialised gaming clipping platform need to provide, and could Clips Kitty let independent developers build even more specialised game-specific pipelines on a shared platform? Plus, for Overwolf: the Game Events API, the auto-highlights game list, app approval and review, monetisation and the stated revenue share (H9), and the developer-agreement constraints.
- Verdict (two lines): Every product here is a closed product, not a platform: the only extension points are Overwolf's app SDK (gated by proposal whitelisting, DevRel QA and publisher compliance) and two "game-developer-facing" event APIs (Medal Game API, Overwolf Events SDK, the legacy NVIDIA Highlights SDK), all of which let a *game* emit events, not a third party add a detector. The common anatomy is: an event source per game (hooks/replay/log/in-game SDK, or vision+audio as the generic fallback), a per-event timing window (past/future/merge), a rolling capture buffer or VOD input, per-game user toggles, a health signal because game patches break detectors, and an output of mp4 + event metadata.
- H9 verdict: Overwolf is the nearest per-game precedent and its approval is gated; the "20–30%" figure is Overwolf's marketing wording (Our Story page). The binding Developer Terms (last updated 2025-10-09) state Overwolf's share as 15% of subscription revenue and 30% of ad revenue, paid by Overwolf to the developer (ads: within 60 days of month end, $200 minimum). See section A.5.

These are products, so template points 1–9 are kept short and marked "n/a" where there is no extension model. The effort is in the Track D questions and the matrix at the end.

---

## A. Overwolf and Outplayed (Tier 2)

**What it is.** Overwolf is a Windows client that hosts third-party "apps" (HTML/JS in CEF windows) with in-game overlays, a Game Events Provider (GEP) and a replay/auto-highlight capture service. Outplayed is Overwolf's own capture app built on these APIs. Market facts from Overwolf's homepage (marketing page, read 2026-10-06): "113M" monthly active users, "33B" yearly downloads, "178K" in-game creators, "1,500+" games, and "$240 Million" paid to creators in 2024 (cited there to a Forbes article). Our Story (marketing page): founded 2010, beta summer 2011 "supporting about a hundred games".

### A.1 Unit of extension
An "app": a package (`.opk`, a ZIP with the extension renamed) containing `manifest.json`, icons and web source files, hosted inside the Overwolf client. For the newer `ow-electron` flavour the unit is an Electron app that loads Overwolf "packages" (e.g. `gep`, `recorder`) at runtime. Outplayed itself is not extensible (n/a).

### A.2 Manifest or metadata
`manifest.json`. Fields seen in the GEP guide (quoted): `"data": { "game_events": [5426, 7764] }` (array of game class IDs the app wants events for; "there is no wildcard support"), `"game_targeting": { "type": "dedicated", "game_ids": [5426, 7764] }` (which games the overlay may draw on), and `"launch_events": [{ "event": "GameLaunch", "tracked": false, "event_data": { "game_ids": [5426, 7764], "wait_for_stable_framerate": 30 }, "start_minimized": true }]`. Store release also requires `launcher_icon` (multi-size `.ico`) and optional `window_icon`, `tray_icon`. The release page provides a manifest validator. Per-feature compatibility is expressed on the per-game event pages as a "Since GEP Ver." column (e.g. LoL `death` since 77.0, Marvel Rivals `kill` since 269.0, `kill_feed` since 269.0, `ability_cooldown_X` since 282.0).

### A.3 Distribution and install
Only through the Overwolf Appstore after approval; "Only whitelisted Overwolf developer accounts can load or install apps that are not available on the Overwolf store" (search snippet of the app-proposal page; the page itself returned 404, not confirmed). Double-clicking a valid OPK installs it. The app page for Outplayed reports "17M" downloads and that the app "requires the Overwolf Client" (10.3 MB).

### A.4 Dependencies and isolation
n/a for third parties (apps are web code inside the client). For `ow-electron`, GEP packages are downloaded at runtime from Overwolf's package server (`--owepm-packages-url=...` switches the QA environment during development).

### A.5 Versioning and updates
Developer Console has "Production vs. Testing Channels". GEP features are versioned per game ("Since GEP Ver."). Game support is deprecated explicitly: the docs sitemap lists 70 active `ow-native` per-game GEP pages and 23 under `supported-games/deprecated/` (counted from `https://dev.overwolf.com/sitemap.xml`, 2026-10-06; deprecated examples: CS:GO, Hearthstone, Heroes of the Storm, StarCraft 2, World of Tanks, World of Warships, Halo Infinite, XDefiant). The status-health page says "Use the event status for all games API to keep your app's game event status up to date" and "It is highly recommended to communicate errors and warnings to your app users" (the per-game table loads client-side; the endpoint URL was not visible in the HTML: not confirmed).

### A.6 Registry design
Central, curated store run by Overwolf. Release process (Phase 3 page, quoted):
1. Self-test with the dev tools, manifest validator, "Events recorder and simulator", "Game events simulator", OW-Logs.
2. Pre-submission checklist: game compliance, UX, FTUE, advertising compliance, app name in manifest, hotkey reminder, resolution and compatibility, "Monetization—make sure you have designed your app to best utilize monetization strategies, even if at first you are not planning to monetize your app", second-screen support.
3. Build the OPK; "Submit your app to the DevRel QA team for review by uploading your latest build and filling out the necessary details in the Developer Console."
4. Timeline: "Typically the QA team will try to finish as quickly as possible, but be patient as they are quite thorough with their testing." No number of days is published. "the QA cycle includes initial testing, feedback, and retesting as needed to meet MVP standards."
5. Gate before any of this: "Using Overwolf's APIs require your app idea to be whitelisted. This is only given to app ideas submitted and approved using the App proposal process. Apps that haven't been approved are considered non-compliant or non-approved Overwolf apps." Also: "Overwolf won't be able to test or approve apps that only have background processes. You must have at least one desktop window indicating the app is running."
6. After QA passes: Developer Console access (store listing, game stats, performance stats, revenue stats).
What is checked: "functionality, design, and compliance", game-specific compliance, UX/FTUE, advertising policy. Cost to run: Overwolf staff (hand review); nothing is published about cost.

### A.7 Trust and permissions
- Declared in the manifest and (inferred) enforced by the client: events are only delivered for games listed in `game_events`; overlays only on `game_targeting` games.
- Game compliance overview: "Apps that fail to adhere to these rules will not be published or approved for distribution". Riot page: Riot approval is required through Riot's third-party process; "apps which reach the publication phase and do not yet possess a Riot approval will be asked to provide one before proceeding"; forbidden: ultimate timers, enemy cooldown tracking, "Spike timers are not allowed to be shown during a live match" (Valorant), Brawl-mode data; private Valorant apps "no longer accepted"; mandatory disclaimer "[Your app name] isn't endorsed by Riot Games...". Marvel Rivals page: NetEase's policy "prevents giving users confidential information like damage and healing statistics, selectively ban heroes, and predict opponents' ultimate abilities".
- Developer Terms §2.4: "Overwolf may at any time, without notice and without providing reasons, cease all display or distribution of any or all Applications, or cease to provide any or all Applications with access to the Platform API." §15.2: Overwolf may terminate "at its sole discretion at any time, for any reason or no reason at all" (as summarised by the fetch; §2.4 verified in raw text).
- "Avoid logging GEP data to any of the log files." (GEP guide warning.)

### A.8 Models
n/a (no ML models exposed; GEP is Overwolf's closed detector).

### A.9 Local, remote or both
Local Windows client; detection and capture are local; ads/subscriptions/payouts remote.

### A.10 Game Events API (the Track D core)
- Concepts (GEP guide, quoted): "A feature is a category of related game events, for example 'Match Start', 'Match End', 'Match Outcome' are all events belonging to the Match feature." Each feature has two entity types: "Info Updates—game information changes that define the game's current status" and "Events—specific events that happen in the game". Example: the LoL "Death" feature has a `death` event and a `deaths` info update (session counter).
- Subscribe: declare `game_events` in the manifest, add listeners `overwolf.games.events.onNewEvents` and `overwolf.games.events.onInfoUpdates2`, then call `overwolf.games.events.setRequiredFeatures(['stats', 'match'], function(info) {...})` and finally `overwolf.games.events.getInfo()` to fetch state that changed before registration. `ow-electron` equivalent: `app.overwolf.packages.gep.setRequiredFeatures(features)`, `.on('new-game-event', (e, gameId, ...args))`, `.on('new-info-update', ...)`, `.getInfo(gameId)`.
- Event shape (per-game pages, quoted): LoL `{"events":[{"name":"death","data":"{"count":"1"}"}]}`; info update `{"info":{"game_info":{"deaths":"1"}},"feature":"death"}`; LoL `usedAbility` `{"events":[{"name":"usedAbility","data":"{ "type": "4"}"}]}`. Marvel Rivals: `{"events":[{"name":"kill","data":4}]}`, `{"events":[{"name":"match_start","data":""}]}`, and `kill_feed` with `{"attacker":"Guru","attacker_character_id":"1034","attacker_character_name":"IRON MAN","attacker_is_teammate":true,"victim":"remix",...}`. Note the `data` field is a string that often contains JSON; the type varies per game (string in LoL, number in Marvel Rivals).
- Per-game "GEP" pages: one page per game listing available features and events with "Since GEP Ver.". LoL features: gep_internal, live_client_data, matchState, match_info, death, respawn, abilities, kill, assist, gold, minions, summoner_info, gameMode, teams, level, announcer, counters, damage, heal, jungle_camps, team_frames, chat, panel_location, augments. Marvel Rivals features: gep_internal, match_info, game_info; events match_start, match_end, round_start, round_end, death, kill, assist, kill_feed; info updates roster_xx, match_id, game_type, game_mode, map, player_stats, match_outcome, banned_characters, ability_cooldown_X, objective_progress, scene (Lobby/Ingame). Games relevant to Clips Kitty's examples all have GEP pages: marvel-rivals, minecraft, minecraft-bedrock, roblox (+ roblox-rivals, roblox-grow-a-garden), world-of-warcraft, valorant.
- How events are obtained is not documented (the GEP intro does not say). Two sources are documented: Overwolf's own detectors, and the "Events SDK for game developers" where a studio "include[s] the 32/64 DLL with your game", defines events and "send[s] notifications over to Overwolf with a simple function"; the data model is "a configurable real-time database split into two main sections" (information categories and events). A search snippet of the Outplayed support article (article itself 404, not confirmed) says: "supporting real-time events for a game requires constant maintenance in addition to permissions from the game developers, so only selected games are added."

### A.11 Auto-highlights
- Supported games page (quoted list): LoL, Dota 2, Fortnite, CS2, PUBG, R6, Apex, RL, HotS, Valorant, Overwatch (11 games). "To check using the API if a particular game is supporting auto-highlights, you can use overwolf.media.replays.getHighlightsFeatures()". The page does not publish a per-game table of highlight types; the only example is LoL with `death` and `assist`. The API call in the reference shows `"requiredHighlights" : ["death","assist","victory"]`.
- Mechanism (quoted): settings live in `highlights.json` under the Overwolf install folder; per game ID an `events` object; per event a `timing` object with `past` ("Time before the event to record"), `future` ("Time after the event to record") and `pending` ("Time that we wait for another event to trigger to merge several events into one highlight"). LoL example: death `past 12000, future 3000, pending 12000`; assist `past 12000, future 8000, pending 12000`. "These settings are shared between ALL the OW apps ... only the OW team can edit this file ... every local change will be overwritten on an OW client update." Changes go through a feature request.
- API: `overwolf.media.replays.turnOn({ "settings": streamSetting, "highlights": { "enable": true, "requiredHighlights": ["death","assist","victory"] } }, callback)`; event `overwolf.media.replays.onHighlightsCaptured`. Payload fields (quoted): `game_id`, `match_id`, `match_internal_id`, `session_id`, `session_start_time`, `match_start_time`, `start_time`, `duration`, `events` (e.g. `["victory"]`), `raw_events` (e.g. `[{"type":"victory","time":10000}]`), `media_url`, `media_path` (an `.mp4` under `Videos\Overwolf\Game Summary\<game>\...`), `thumbnail_url`, `replay_video_start_time`. This is, in effect, the "video in, labelled time ranges out" contract of H2 with the labels attached.
- Outplayed (app page, quoted): "Automatically capture your best moments and biggest plays, as well as manually recording on demand"; "supports over 5000 games" for recording; clip review in a match timeline, trim, montage, share. The support articles listing which games get automatic highlights all returned 404 on 2026-10-06 (not confirmed); a search snippet lists LoL, TFT, Valorant, LoR, CS:GO, Apex, Fortnite, PUBG, Dota 2, R6, Overwatch, HotS, Hearthstone, StarCraft II, Minecraft (Java), World of Tanks, World of Warships, Splitgate, Rocket League "and many more", with a vote/request mechanism for new games (snippet only, not confirmed).

### A.12 Monetisation and revenue share (H9)
- Monetisation overview (dev docs, quoted): "Overwolf will only approve apps that integrate and use Overwolf ads, Overwolf subscriptions, or both"; payments "NET 60", "The minimum amount for each payment is $200 (net)", "Payments are made in the first week of every month", USD via Payoneer; "The revenue numbers in the Developer Dashboard are not final." Subscriptions are processed by Tebex.
- Developer Terms (legal.overwolf.com, "Last updated: October 9, 2025", §6.2, quoted from raw text): "The subscription Revenue Share for Overwolf shall mean fifteen percent (15%) share of the total Revenues actually received and generated from the Subscription/s. Overwolf shall make payment of amounts due hereunder to the Developer within 30 days of receipt". "The ad Revenue Share shall mean thirty percent (30%) of the Revenues actually received through the advertising platform. (a) Payment will be made by Overwolf in USD through a wire transfer within sixty (60) days from the end of the applicable month. (b) The minimum amount for payment is $200." "The revenue share for Other Monetization Solutions will be as mutually agreed between Overwolf and the Developer" and such solutions "require written approval from Overwolf". "Revenues" are net of deductions including "ads server licensing, anti-fraud tools licensing or any other costs related to Overwolf's advertising platform, including any fees for using Tebex Services". Direction of payment: Overwolf collects (ads platform, Tebex) and pays the developer; if the developer collects, "the Developer shall make the payments due to Overwolf hereunder within 30 days". A separate "5% Tebex fee" reported by the page summary was not found in the raw text: not confirmed.
- Monetization Terms (legal, "Last Updated: November 23, 2025", as summarised by the fetch): developers must give notice before monetising an app outside Overwolf: "Six (6) months" for apps using only regular monetisation services, "Twelve (12) months" if Overwolf provided "acceleration, marketing, or other promotional support"; "Developer may not monetize or attempt to monetize any Application outside of the Overwolf platform unless the applicable notice requirement above has been satisfied."
- Marketing wording (Our Story, quoted from raw text): "we take a rev-share from these ads. Our share is 20-30%, and we pay more for creators who build better products and meet our quality checklists." Press (GamesBeat, 2012-08-01, press source): "The developer gets 70 percent of that amount, and Overwolf gets 30 percent."
- Verdict for H9: the gated approval claim is confirmed (proposal whitelist, DevRel QA, publisher approvals). The "20–30%" is a marketing range; the contract figures are 15% (subscriptions) and 30% (ads) for Overwolf, with payouts flowing from Overwolf to the developer. Clips Kitty's "0% / $0 / no payments" is a clean contrast, but note that Overwolf's gate is as much about publisher compliance (Riot, NetEase, Valve) as about quality.

### A.13 Known incidents and stated limitations
No security incidents found in official sources (none searched for beyond the docs; not confirmed either way). Stated limitations: no wildcard game subscription; highlights timing is global and uneditable; 23 games' events deprecated; events "require constant maintenance" (snippet, not confirmed); apps with only background processes are not approved; Valorant private apps not accepted.

### A.14 Borrow / Avoid
Borrow: (1) the two-entity event model (info updates vs events) and per-feature versioning ("Since GEP Ver."); (2) the per-event timing triple `past/future/pending` as the declarative bridge from event to clip; (3) the `onHighlightsCaptured` payload shape (`events`, `raw_events[{type,time}]`, `media_path`, match/session ids) as a reference output schema for a pipeline; (4) a published per-game "supported events + status" page, generated from metadata; (5) a manifest validator and an "events recorder and simulator" so detectors can be tested from recordings without the game running. Avoid: a proposal-whitelist gate before anyone may even call the API; a global, host-owned timing file that developers cannot override per pipeline; unpublished review timelines; terms that let the host drop an app "without providing reasons".

---

## B. Medal (Tier 3)

1–9 (short): Closed Windows/mobile capture app. Extension model: a **Game API** for game and server developers (not for third-party detector authors). Unit = an "event" registered per claimed game in the Developer Portal (Beta). Metadata = event name, icon, "how many seconds to keep before and after the event" (example "15 seconds before and 15 seconds after"), an API key "shown once". Distribution: the game calls Medal; nothing is installed. Isolation: n/a. Versioning: events go "Early Access → Live" (about 3 minutes after saving), then "Publish to everyone → Get your tested events reviewed by Medal". Registry: Medal's portal; game "claim" must be approved by "a Medal admin". Trust: human approval of claims and of published events. Models: n/a. Local: detection and capture are on the player's PC; library sync is cloud.

**Auto clipping (support article "What is Automatic Event Detection/Auto Clipping?", modified "Sun, 19 Jul", year not shown; game list "updated in November 2025", quoted):** "With auto clipping enabled, you don't need to worry about pressing your clip hotkey to save that kill, Medal will capture it automatically. If you are using Full Session Recording, Medal will automatically create bookmarks instead of clips." Games: "Rocket League, League of Legends, GTA V, Dota 2, Roblox, PUBG, Counter-Strike 2, Fortnite, War Thunder, EA Sport FC Online, Rematch, REPO, Runescape: Dragonwilds, YAPYAP, and Valorant". Per-game event toggles live in Settings > Auto Clipping ("You can also enable/disable events inside the same panel!"). Windows only. Detection hints: "try disabling any Streamer Mode in your game. This mode typically obfuscates some information that Medal uses to detect the in-game events" and "most of the Auto Clipping games on Medal use the game's Replay system as a base" (so detection reads game-exposed replay/overlay information, not pure vision: inferred from these two sentences). Clip length (recording article): "you can capture the last 15, 30, 60, or even up to 20 minutes of gameplay"; in Long Recordings the hotkey (default F8) inserts a bookmark. Clip file format: not stated on the pages read (not confirmed).

**Game API (developer portal Quickstart, quoted):** "Auto clipping happens in the Medal app on the player's PC, not inside your game. Medal records the screen while they play. When a moment happens, your game sends Medal one small web request, and Medal saves the seconds around it as a clip in the player's library." Steps: install Medal, claim the game, wait for admin approval, add an event (name, icon, before/after handles), "Generate API key", switch the test PC to Early Access, fire the test event with a copied cURL command on "the PC running Medal". The nav lists Quickstart, Events & API keys, Test on Early Access, API reference, Publishing, Troubleshooting (the sub-pages could not be resolved by URL: not confirmed). The former docs URL `docs.medal.tv/gameapi.html` now redirects to the portal. A search snippet names `POST /api/v1/event/invoke` with `eventId`, `eventName`, `otherPlayers`, `contextTags`, `triggerActions`, `clipOptions` (snippet, not confirmed on page).

**Roblox integration (developer page, quoted):** a `MedalClipper` ModuleScript; `Medal:TriggerClip("evt_kill_streak", killStreak .. "-Kill Streak", { duration = 30, captureDelayMs = 5000 })`; options `duration` (default 30), `captureDelayMs`, `contextTags` ("become hashtags"). Transport: "The payload is base64-encoded and printed with a special tag that Medal's desktop app watches for in the Roblox log output" (`[_MAPIEvent][v1/event/invoke]` + JSON including `universeId`); "Players without Medal are unaffected - no errors, no prompts."

Borrow: the trigger contract `eventId + eventName + duration + captureDelayMs + contextTags` is a minimal, engine-agnostic event message; the log-line transport shows that an out-of-process event source needs nothing but a watched text channel (H1). "Bookmarks instead of clips" when a full session is recorded is a good two-stage model (mark now, cut later). Avoid: human approval of every published event; one-time-visible keys with no scoped permissions.

---

## C. Allstar (Tier 3)

1–9: Closed cloud service; no SDK or public API found (not confirmed). Homepage renders only "Get your own in-game highlights, magically out of thin air." without JavaScript. Help centre (Zendesk JSON API) facts: "What games are supported?" (updated 2025-01-30): "Allstar supports CS2, DOTA2, League of Legends, and Fortnite!"; requests via allstar.canny.io. "How does Allstar work?" (2025-01-30): "Allstar is a next-generation cloud highlights capture service ... convert your desktop gameplay to 9:16 mobile format with true zero FPS drop. Our engine smart trims, tags, titles, and uploads to the cloud, all from the ease of a hotkey." "Match History - FAQ" (updated 2026-06-17): clips from "Competitive CS2 matches" (not casual), FACEIT, LoL, Dota 2, "Fortnite Cash Cups" (not regular matches), Overwatch 2, "Valorant - Coming soon!"; missing matches happen "because Valve did not provide a demo for that specific match"; users can "upload a demo file directly into Match History"; linking is via Steam share codes or Riot account; "Match History is FREE for everyone to use!". "Creator Credits" (2025-11-21): credits are bought or come with a subscription; "Pro & Platinum subscriptions" exist; prices not published in the help centre (not confirmed).

Detection approach (inferred from the Match History FAQ): match data and demo/replay files, rendered server-side ("cloud highlights capture"), which is why support is limited to games whose demos or match data are obtainable (CS2 demos, FACEIT, Riot match history, Fortnite Cash Cup replays). This is game-specific by construction: each game needs a demo parser and a renderer. No video analysis is mentioned.

Borrow: "replay/demo file in, clips out" is a second kind of game-specific input (structured match data rather than pixels) that a Clips Kitty pipeline could accept alongside video. Avoid: depending on publisher-supplied demos (support is hostage to Valve's availability, per the FAQ).

---

## D. Sizzle (Tier 3)

1–9: Closed web service; "Sizzle.gg is a website, and it does not require any download." No API (none found). Input: linked Twitch/YouTube accounts or uploads ("The current size limit per file is 10GB"). Supported games (FAQ, 43 titles counted): Among Us, Apex Legends, BGMI, Brawl Stars, CoD Black Ops 6, CoD MW2, CoD MW3, CoD Warzone Mobile, CS2, Dark and Darker, Dead By Daylight, Destiny 2, Dota 2, Escape from Tarkov, EA SPORTS FC 24, Euro Truck Simulator 2, F1 23, F1 24, Fall Guys, Fortnite, Garena Free Fire, Genshin Impact, GTA V, Halo Infinite, LoL, Madden NFL 24, Minecraft, NBA 2K24, Overwatch 2, Palworld, PUBG, PUBG Mobile, R6 Siege, Roblox, Rocket League, Street Fighter 6, Super Smash Bros. Ultimate, TFT, TEKKEN 8, The Finals, VALORANT, World of Warcraft, XDefiant. Generic fallback: "Universal AI, allows you to generate highlights from any game and video ... still in beta". Filters are per game: "including eliminations, knockdowns, assists". Pricing (FAQ): "Sizzle.gg currently offers one plans: Sizzle.gg Starter, for $4.99 per month" with a free first month; Universal page: "After the free trial access to these features (and more!) is $4.99 / month"; Starter streams "typically ready within 4 hours". B2B: "KRAFTON are using Sizzle.gg to generate highlights for PUBG Esports in North and South America"; Garena for Free Fire tournaments; licensing by email. Blog (2023-12-12) announced Universal AI "exclusive to Sizzle.gg Starter members".

Detection approach: not documented. Because the input is a VOD or uploaded file with no client software, detection must be video/audio analysis with per-game models for the 43 titles plus a generic model (inferred). This is the closest analogue to Clips Kitty's own position (post-hoc, file in).

Borrow: a per-game model list plus an explicit "universal" fallback, both exposed to users as filters (eliminations, knockdowns, assists). Avoid: undocumented detection and a single-plan paywall for the generic model.

---

## E. SteelSeries Moments (Tier 3) — status: active (confirmed)

Status: the product page (read 2026-10-06) is live, free ("Free Download", "Windows 10 (RS5+)"), and carries current banners: "Street Fighter 6 is now supported by Moments AI auto-clipping" and "Call of Duty: Black Ops 7 is supported by Moments AI auto-clipping"; "2,5B+ Clips Captured", "3,5M+ Clippers" (marketing numbers). The page shows 38 "AUTO-CLIP" game tiles (counted in the HTML). Support articles were updated in July–August 2026 (Marvel Rivals 2026-08-28, Rocket League 2026-08-03, Genshin Impact 2026-07-31, Brawlhalla 2026-07-09). The earlier "not confirmed" status can be replaced by "active".

How it works (product page, quoted): "Specific games in Moments automatically save highlights as event markers to your timeline for easy post-game editing and sharing." Each support article opens "SteelSeries GameSense now supports auto-clipping for <game> within Moments!" and "Auto-clip will only save clips based on specific in-game events when playing with supported modes and configurations." Per-game event tables: Marvel Rivals — "Overtime (end game)", "Multi-KO: Any time you earn 3 or more eliminations in rapid succession"; Rocket League — "Goal", "Shot on Goal"; Brawlhalla — "Dominating", "Berserk", "Double KO", "Triple KO", "Game Over". Users pick events in "Settings > Auto Clip Events". Requirements listed per game: resolutions "1920x1080, 2560x1440, 3840x2160, 1600x900, 1366x768 - Ratio 16:9", language "English", and "SteelSeries GameSense currently does not support Windows 11 HDR settings". Detection approach (inferred from the resolution/language/HDR constraints and the "Moments GameSense AI" naming): on-screen recognition of HUD elements rather than game hooks. The 2021 launch blog listed LoL, CS:GO and Dota 2 as the first auto-clip games. No SDK for third-party detectors; SteelSeries' GameSense SDK is a game-integration SDK, and whether Moments consumes it is not stated (not confirmed).

Borrow: event markers on a timeline (mark, then cut) and per-game event toggles; honest per-game constraint lists (resolution, language, HDR) are exactly the kind of compatibility metadata a Clips Kitty pipeline manifest should carry. Avoid: n/a beyond the undocumented method.

---

## F. NVIDIA Highlights (Tier 3) — status: legacy (confirmed)

Developer page (quoted): "Note: This is a legacy SDK. Developers may download and continue to use, but it is no longer supported." It "enables automatic video capture of key moments, clutch kills, and match-winning plays"; integrated with "Unreal Engine 4.18 and Unity 5.6" plugins and a C/C++ SDK; named titles PUBG and Fortnite Battle Royale; runtime requires GeForce Experience. Links point to github.com/NVIDIAGameWorks/GfeSDK and GfeSDK-UE4Plugin, but the repositories were not readable (raw README 404; NVIDIA forum, 2024-05-23 to 05-28, NVIDIA staff MarkusHoHo: "The github repository is currently unavailable. It might be made publicly available at a later time, but there is no ETA."). A 2022-01 forum thread (same staffer): "There are no more NVIDIA plugins in the Unity asset store". API shape (NVIDIA blog, 2018-03-21, quoted names): `Init Highlights`, `Highlights Configure`, `Open Group`, `Set Video Highlight`, `Set Screenshot Highlight`, `Close Group`, `Open Summary`, `Poll`; a highlight definition has `Id` ("A string token that uniquely identifies the event"), `User Default Interest`, `Highlight Tags`, `Significance`, a `Name Translation Table`; a capture is marked with `Start Delta` and `End Delta` in milliseconds (negative start captures pre-event context).

Lesson: the game-integrated model (the game itself declares highlight types with ids, significance and user-default interest, then marks ranges with start/end deltas) is the cleanest formal schema found; it died with the vendor's product priorities, which argues for keeping Clips Kitty's schema vendor-neutral and file-based.

---

## G. Powder (Tier 3) — status: shut down (official note), date not confirmed

`https://powder.gg/` redirects to a Google Doc titled "To the Powder community" (text export read 2026-10-06, quoted): "After 7 incredible years together, our journey with Powder has reached its end. We've stretched our financial capacities as far as possible, but we can no longer sustain the app. Starting today, the Powder app will no longer be updated or maintained. All active subscriptions are being cancelled so they won't renew." The note carries no date; third-party blogs disagree (late 2024, early 2026, July 2026), so the date is not confirmed. `https://www.powder.gg/` still serves stale marketing ("The Powder recorder is back!"). Detection approach from that page (quoted): "Powder is a PC app exclusively available on Windows"; "We designed Powder AI to run entirely locally on your Windows PC"; "Powder automatically generates highlights for 40+ of the world's most popular games"; otherwise "Universal Game Support ... Powder will identify heightened emotions via the gameplay recording or stream audio feed", plus "community clipping, chat spike, auto-transcription, and smart keyword search"; "Powder provides post-processing software ... the gameplay recording must be finished"; inputs were recordings or Twitch/YouTube/Kick VODs; a "Powder Pro" edition targeted AMD XDNA NPUs. Feature pages were named "Game Models", "Voice Models", "Twitch Models", "Live Highlighting".

Lesson: a local-first, per-game-model clipper with a generic emotion/audio fallback is the closest architectural match to Clips Kitty, and it failed commercially, not technically ("unable to secure a sustainable business model", press wording). A free, open plugin model avoids carrying that cost on one company.

---

## H. Framedrop (Tier 3) — status: not confirmed

`framedrop.ai` could not be loaded (DNS/proxy failure on 2026-10-06, both with WebFetch and curl); `framedrop.gg` redirects to `www.sloode.com`, an unrelated video-hosting platform that does not mention Framedrop. Search snippets (not confirmed on page) describe a web-only tool: paste a Twitch VOD URL or upload a file; "supports Valorant, League of Legends, Rocket League, Apex Legends, COD: Warzone and Fortnite, as well as general funny moments and chat reactions"; "240 minutes of imported gaming content for free, with Starter and Pro plans offering 1200 and 2400 minutes"; auto captions. Detection would therefore be VOD video/audio/chat analysis with per-game models for six titles (inferred from snippets only).

---

## I. StreamLadder (Tier 3) — status: active

Web app plus iOS/Android app. Core product converts stream clips to vertical with captions. AI detection ("ClipGPT" page, quoted): "ClipGPT analyzes your entire VOD and surfaces the best moments, ranked by virality potential"; signals named: "engaging chat interactions, intense highlights, or chaotic reactions"; "Every detected moment gets a score from 0–100"; "Optionally, say 'clip that' while streaming to mark your own too"; "Transcript Search"; facecam detection; inputs: "Paste a Twitch or Kick VOD link, or Upload your YouTube stream." No game list and no game-specific logic is claimed: detection is generic (chat, audio, speech, reactions). Marketing numbers: "11 million clips made by 1 million streamers". Pricing: the pricing page is a JavaScript app with no readable content (not confirmed); the site mentions a "7-day free trial" and "Starter"/"Creator" plans. No API found.

Lesson: the generic, transcript-plus-chat approach with a 0–100 "virality" score and a voice command to bookmark live is the non-game-specific baseline; it is what Clips Kitty already does, which is why game-specific pipelines are the differentiator.

---

## Track D matrix

| Platform | Main use case | Generic vs game-specific | Automated highlights | Game-specific logic | Extensibility | What Clips Kitty should learn |
|---|---|---|---|---|---|---|
| Overwolf / Outplayed | In-game overlay apps and auto-capture on Windows (Outplayed: capture app) | Game-specific (70 active GEP games, 11 auto-highlight games); recording is generic ("over 5000 games") | Yes: `overwolf.media.replays` with `requiredHighlights`, `highlights.json` timing `past/future/pending`, `onHighlightsCaptured` payload with `events`/`raw_events`/`media_path` | Per-game GEP feature pages, versioned ("Since GEP Ver."), deprecation list, status/health API, publisher compliance rules (Riot, NetEase, Valve) | Apps only, gated: proposal whitelist, DevRel QA (no published SLA), Overwolf-only monetisation (15% subs / 30% ads to Overwolf) | Event model (info vs events), per-event timing triple, output schema, per-game status page, recorder/simulator for offline testing; avoid the gate and the global timing file |
| Medal | Consumer capture app with cloud library | Game-specific auto clipping (15 games, Nov 2025) + generic hotkey/full-session recording | Yes: event-triggered clips or bookmarks; 15/30/60 s up to 20 min buffers | Reads game replay/overlay data ("Streamer Mode ... obfuscates some information that Medal uses"); Game API lets a game/server fire `TriggerClip(eventId, eventName, {duration, captureDelayMs, contextTags})` | Game API for game devs only; claims and published events approved by Medal staff | Minimal trigger message; log-line transport as an out-of-process event source (H1); bookmarks-then-cut |
| Allstar | Cloud clip generation from match data | Game-specific (CS2, Dota 2, LoL, Fortnite; OW2 in Match History) | Yes, server-side from demos/match history and hotkey | Demo/replay parsers per game; depends on Valve supplying demos | None public (not confirmed) | Structured match data as a second input type; do not depend on publisher demo availability |
| Sizzle | Web VOD-to-highlights for streamers | Both: 43 game models + "Universal AI" fallback | Yes, post-hoc on VODs/uploads (10 GB), ~4 h turnaround | Per-game filters (eliminations, knockdowns, assists) (vision/audio, inferred) | None | Expose per-game models and a universal fallback as user filters; $4.99/month single plan shows the price ceiling |
| SteelSeries Moments | Free Windows recorder (GG suite) | Both: generic capture + 38 auto-clip games (site tiles) | Yes: event markers on the timeline, per-game event toggles | Per-game event tables (e.g. Marvel Rivals Overtime/Multi-KO; RL Goal/Shot on Goal); resolution/language/HDR constraints imply on-screen recognition (inferred) | None | Per-game compatibility constraints belong in the manifest; event markers + post-game cut |
| NVIDIA Highlights | Game-integrated capture via GeForce Experience | Game-specific by integration (PUBG, Fortnite named) | Yes, developer-triggered | Game declares highlight types (`Id`, `Significance`, `User Default Interest`, tags, translations) and marks `Start Delta`/`End Delta` | Legacy SDK, repo unavailable, unsupported | Cleanest formal highlight-definition schema; keep ours vendor-neutral |
| Powder | Local-first AI clipper (Windows) | Both: 40+ game models + "Universal Game Support" (audio emotion, chat spike, transcript) | Yes, post-processing only (recordings or VODs) | Per-game "Game Models" | None | Closest architecture to Clips Kitty; it died on business model, so free + open is the safer route |
| Framedrop | Web VOD clipper | Both (six games + funny moments/chat) — not confirmed | Yes (snippets) — not confirmed | Per-game models (snippets) | None known | Nothing verifiable; mark as not confirmed |
| StreamLadder | Web vertical-clip editor with AI clipping | Generic (chat, reactions, transcript); no game logic | Yes: ClipGPT, 0–100 virality score, "clip that" voice bookmark | None | None | The generic baseline; game-specific pipelines are the differentiator |

## Hypotheses

- H1 (out-of-process plugins over a local API): supported by Medal's Game API design ("your game sends Medal one small web request"; Roblox variant is a watched log line) and by Overwolf `ow-electron` loading GEP as a runtime package. Overwolf native apps, by contrast, run inside the client and are gated by manifest + whitelist.
- H2 (smallest contract = video in, scored/labelled ranges out): confirmed as sufficient by three independent schemas: Overwolf `HighlightsCapturedEvent` (`events`, `raw_events[{type,time}]`, `start_time`, `duration`, `media_path`), NVIDIA (`Id`, `Significance`, `Start Delta`, `End Delta`), Medal (`eventId`, `eventName`, `duration`, `captureDelayMs`, `contextTags`). Add an event label and an optional significance/score to the range.
- H4 (hand review does not scale): consistent but anecdotal: Overwolf publishes no QA turnaround ("be patient as they are quite thorough"); Medal hand-approves game claims and event publishing.
- H5 (declared vs enforced permissions): Overwolf's `game_events`/`game_targeting` are declared in the manifest and (inferred) enforced by the client, which only delivers events for listed games: an example of a host-enforced declaration.
- H9: see A.12. Gated approval confirmed; share should be restated as 15% (subscriptions) / 30% (ads) to Overwolf per the 2025-10-09 Developer Terms, with "20–30%" being marketing wording; payouts go from Overwolf to developers ($200 minimum, 60 days for ads, 30 days for subscriptions); plus 6/12-month notice before monetising elsewhere (Monetization Terms, 2025-11-23).
- H10: n/a (no registry).

## Sources (all read 2026-10-06)

Overwolf
- https://dev.overwolf.com/ow-native/guides/general-tech/using-game-events-in-your-app
- https://dev.overwolf.com/ow-native/guides/general-tech/auto-highlights-supported-games
- https://dev.overwolf.com/ow-native/getting-started/release-your-app
- https://dev.overwolf.com/ow-native/monetization/overview
- https://dev.overwolf.com/ow-native/monetization/subscriptions/overview
- https://dev.overwolf.com/ow-native/reference/media/replays
- https://dev.overwolf.com/ow-native/live-game-data-gep/live-game-data-gep-intro
- https://dev.overwolf.com/ow-native/live-game-data-gep/events-sdk-for-game-developers
- https://dev.overwolf.com/ow-native/live-game-data-gep/game-events-status-health (table loads client-side; endpoint not confirmed)
- https://dev.overwolf.com/ow-native/live-game-data-gep/supported-games/league-of-legends
- https://dev.overwolf.com/ow-native/live-game-data-gep/supported-games/marvel-rivals
- https://dev.overwolf.com/ow-electron/live-game-data-gep/live-game-data-gep-intro
- https://dev.overwolf.com/ow-native/guides/game-compliance/overview
- https://dev.overwolf.com/ow-native/guides/game-compliance/riot-games
- https://dev.overwolf.com/sitemap.xml (used to count per-game pages: 70 active + 23 deprecated ow-native; 57 ow-electron)
- https://legal.overwolf.com/docs/overwolf/developers/developer-terms (Last updated October 9, 2025)
- https://legal.overwolf.com/docs/overwolf/monetization-terms (Last Updated November 23, 2025)
- https://www.overwolf.com/our-story/ (marketing)
- https://www.overwolf.com/ (marketing numbers)
- https://www.overwolf.com/app/Overwolf-Outplayed (product page)
- https://gamesbeat.com/overwolf-opens-an-in-game-appstore-for-player-created-goods-for-online-games/ (press, 2012-08-01)
- Not confirmed (404 on 2026-10-06): https://dev.overwolf.com/ow-native/getting-started/submitting-an-app-proposal ; https://support.overwolf.com/support/solutions/articles/9000188179 ; https://support.overwolf.com/en/support/solutions/articles/9000208995-how-to-use-outplayed ; https://support.overwolf.com/en/support/solutions/articles/9000215376-highlights-with-limited-support ; https://support.overwolf.com/en/support/solutions/articles/9000268210-outplayed-quick-set-up-guide ; https://dev.overwolf.com/ow-native/reference/live-game-data-gep/supported-games/ ; https://legal.overwolf.com/ (no links rendered)

Medal
- https://support.medal.tv/support/solutions/articles/48001167701
- https://support.medal.tv/support/solutions/articles/48001157618-how-to-record-and-make-clips
- https://medal.tv/developer/auto-clipping (redirect target of https://docs.medal.tv/gameapi.html)
- https://medal.tv/developer/roblox/autoclipping
- Not confirmed (404): https://support.medal.tv/support/solutions/articles/48001252022 ; portal sub-pages api-reference / publishing / events-and-api-keys

Allstar
- https://allstar.gg/ (JavaScript-only shell)
- https://help.allstar.gg/hc/en-us/articles/12025115940119-What-games-are-supported (via Zendesk JSON API; updated 2025-01-30)
- https://help.allstar.gg/hc/en-us/articles/11903206897175-How-does-Allstar-work (2025-01-30)
- https://help.allstar.gg/hc/en-us/articles/17179323907351-Match-History-FAQ (2026-06-17)
- https://help.allstar.gg/hc/en-us/articles/17179288020759-How-does-Match-History-work (2024-10-08)
- https://help.allstar.gg/hc/en-us/articles/36499975915415-Creator-Credits (2025-11-21)

Sizzle
- https://sizzle.gg/ (redirects to https://www.sizzle.gg/home)
- https://www.sizzle.gg/faq
- https://www.sizzle.gg/universal
- https://www.origin.sizzle.gg/blog/?p=661 (company blog, 2023-12-12)

SteelSeries Moments
- https://steelseries.com/gg/moments
- https://support.steelseries.com/hc/en-us/articles/33748999805453-Auto-Clipping-for-Marvel-Rivals (updated 2026-08-28)
- https://support.steelseries.com/hc/en-us/articles/9333320555277-Auto-clip-Rocket-League-with-SteelSeries-Moments (2026-08-03)
- https://support.steelseries.com/hc/en-us/articles/4967837092109-Auto-clip-Brawlhalla-with-SteelSeries-Moments (2026-07-09)
- https://support.steelseries.com/hc/en-us/articles/6375708698125-Auto-clip-Genshin-Impact-with-SteelSeries-Moments (2026-07-31)
- https://steelseries.com/blog/how-to-clip-share-game-plays-moments-506 (company blog, 2021-04-25)

NVIDIA Highlights
- https://developer.nvidia.com/highlights
- https://developer.nvidia.com/blog/implementing-nvidia-highlights-plugin-unreal-engine-4 (2018-03-21)
- https://forums.developer.nvidia.com/t/cant-download-access-highlights-on-github/293943 (2024-05)
- https://forums.developer.nvidia.com/t/nvidia-highlights-not-available-anymore/199651 (2022-01)
- Not confirmed (not readable): https://github.com/NVIDIAGameWorks/GfeSDK ; https://github.com/NVIDIAGameWorks/GfeSDK-UE4Plugin

Powder
- https://powder.gg/ (redirects to https://docs.google.com/document/d/1p8TjN7bCRdOnhrD5DjG02pmoGi4ylsGKmdCS-YV4jrg/ "To the Powder community")
- https://www.powder.gg/ (stale marketing page)
- Shutdown date: not confirmed (third-party blogs disagree; not used)

Framedrop
- Not confirmed: https://framedrop.ai/ and https://framedrop.ai/gaming (DNS/proxy failure); https://framedrop.gg/ redirects to https://www.sloode.com/ (unrelated)

StreamLadder
- https://streamladder.com/
- https://www.streamladder.com/clipgpt
- Not confirmed: https://app.streamladder.com/upgrade (pricing page is a JavaScript app with no readable content)
