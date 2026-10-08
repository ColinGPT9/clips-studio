# Eklipse.gg — research notes (Track D, Tier 1)

Platform: Eklipse (eklipse.gg, app.eklipse.gg). Tier 1, Track D (market reference, not a template). Date read: 2026-10-06 (all URLs below were opened on that date unless marked "not confirmed").

**Brief's question.** Study Eklipse's gaming clip/highlight workflow (connect Twitch/YouTube/Kick, upload VODs, AI highlights, edit, vertical, post), supported games and use cases ("3,000+ games"; what differs between per-game pages), automation (auto-clipping, scheduling), game-specific functionality (what "Gameplay Intelligence" detects; genres named; any per-game detection), how it separates generic from game-specific processing, how users select gaming functionality, pricing (Free vs Premium; Plus→Premium; current prices/limits), which parts could be specialised Clips Kitty pipelines, and whether it publishes any API/SDK/plugin system (H9).

**Verdict (two lines).** Eklipse is a closed, cloud-only SaaS with three detection layers: per-game "detection models" with a per-game event taxonomy (Valorant: KILL/ACE/CLUTCH/TEAM ACE/VICTORY; Fortnite: DOUBLE/TRIPLE/MEGA/EPIC ELIMINATION…), per-genre tuning in the June-2026 "Gameplay Intelligence" engine (five buckets: FPS, battle royale, MOBA, tactical shooter, strategy/non-action), and a generic audio/reaction fallback for the rest of the "3,000+" games; the game is chosen automatically from the stream's category metadata (one game per VOD), with a manual "Resubmit → select game" override.
No API, SDK, plugin system or developer program is published (home, features, terms, help-centre search): H9 confirmed, with the nuance that detection is per game *and* per genre, not per genre only. Useful as a feature checklist for a specialised gaming pipeline, not as an extensibility model.

**Update (2026-10-07).** Read again for Colin's message of 10:35 UTC: Eklipse has been paid-only and cloud-only since June 2026, its pages give 300+, 1,000+ and 3,000+ as the number of games, and Powder (a local Windows clipper) shut down in July 2026 according to Eklipse. What the wider gaming market covers, and which genres no product detects game events for, is in [ecosystem-projects.md](ecosystem-projects.md#gaming-what-the-market-covers-and-where-the-gaps-are).

---

## Template points 1–9 (mostly n/a: Eklipse is a product, not an extension ecosystem)

1. **Unit of extension.** n/a. The nearest internal analogue is a per-game "AI detection model" ("locks in the specific AI detection model for that game (reading its unique UI and events)", help, modified 2026-03-10) plus a per-genre configuration in Gameplay Intelligence. Only Eklipse adds games; users "submit requests for new games ... on our official Discord server" (help, 2026-03-31).
2. **Manifest/metadata.** n/a. The only game-level metadata visible to users is a directory entry (game name, genre tag on /use-case/, Free vs "Premium Exclusive Title" tier, and an image listing the event types the model emits). No file format, no field names.
3. **Distribution/install.** n/a. "No special software is required" (help, 2022/2023). Everything "runs in the cloud" ("Detection, reframing, captioning, and rendering all run in the cloud", /features/ai-highlights/). OBS is used only to push a private 1440p RTMP stream to an Eklipse stream key (Ultra Highlights). Mobile apps (iOS/Android) host the Content Agent.
4. **Dependencies/isolation.** n/a (server side). Marketing claim: "Zero CPU or GPU used" on the user's PC (/compare/eklipse-vs-medal-tv/, marketing).
5. **Versioning/updates.** The whole detection engine was swapped on 2026-06-01: Gameplay Intelligence "replaces the previous AI layer on Premium plans"; "The per-genre models are updated as game releases and play patterns change. Updates apply automatically, no action required." (help, 2026-05-26). Users cannot pin an engine version; they can re-run a VOD with a different game model ("Resubmit").
6. **Registry.** n/a. A "supported games directory" exists at eklipse.gg/games → app.eklipse.gg/games (a Next.js app; the list is loaded client-side and was not readable without JavaScript, so the 3,000+ count is "not confirmed" from data). 78 marketing use-case URLs ("These 72 games have a dedicated guide. Eklipse supports 3,000+ games in total.", /use-case/). Game requests go through Discord. Cost to run: unknown (cloud SaaS).
7. **Trust/permissions.** n/a. OAuth links to Twitch/YouTube/Kick/Facebook (import) and TikTok/Instagram/YouTube/Facebook (posting). Manual upload requires ticking "I confirm that this is my video and authorize Eklipse.gg to process it". Content Agent: "Nothing publishes until you approve the batch" (/features/content-agent/).
8. **Models.** Internal only. "our AI models are trained on data from over 1,000 supported games" (help, 2026-03-04); "AI-powered video analysis with Object Detection and Optical Character Recognition (OCR)" (help, 2025-08-13); Gameplay Intelligence "separates your stream audio into: your voice, in-game sound, background sound" (help, 2026-05-26). Nothing is downloadable or user-visible. The esports B2B page offers "Custom model on request" (/esport/).
9. **Local/remote.** Remote only. Inputs are platform VODs, pasted links, Google Drive, or local MP4 upload (Premium only, "Each file can be as hefty as 10 GB", "Each import requires 1 credit", "up to 10 offline recordings/VODs each month" — help, modified 2026-09-27).

---

## A. Workflow (connect → import → detect → library → edit → post)

Sources: /features/, /features/ai-highlights/, /help/how-does-eklipse-work/ (2025-08-13), /help/how-can-i-automatically-generate-clips-from-my-gaming-streams/ (2025-11-13), /help/how-to-import-streams-from-local-storage/ (mod 2026-09-27), /help/how-many-twitch-link-imports-trial/ (2026-03-17).

| Step | What Eklipse does | Facts |
|---|---|---|
| Connect | OAuth to Twitch, YouTube, Kick or Facebook ("Bring in Twitch, Kick or YouTube VODs without downloading a single file") | Kick marked as a Premium feature on /features/. Voice-command article also lists Rumble. |
| Import | Automatic after each stream ends ("our AI will automatically fetch and process your streams as soon as you go offline"); or paste a Twitch link (3 imports per user, same on Free and Premium); or Google Drive; or local MP4 (Premium, 10 GB, 1 credit) | Manual upload form has a "Select game" field and a title. Max VOD: "Twitch & Kick: up to 6 hours per stream" (help, 2025-08-18). |
| Detect | "Game-aware AI detects clutches, multi-kills, reactions and audience spikes"; "Dozens of clips from a single session"; "Up to ~100 from one stream" | Turnaround "60m from stream end to clips" (use-case pages); Content Agent help says allow 15–30 min. |
| Library | Ranked list of clips, each tagged with an event type (see C/E) | Free accounts see some clips as "Restricted" (paywalled) for certain games. |
| Edit | Eklipse Studio: "facecam-over-gameplay stacked layouts", reframing that "follows the action as it moves across the frame" keeping "your kill feed, your minimap, your facecam"; auto captions "editable word by word"; memes/stickers; "9:16 out of the box" | AI-Edit "Instantly adds memes to gaming clips". Pro Edits = human editing, Premium. |
| Post | Content Publisher: TikTok, YouTube Shorts, Instagram Reels, Facebook; share now / schedule / draft; "runs server side, so your PC does not need to be on" | "scheduling runs on a paid plan". |

Also: Voice Command ("clip it", "clip this", "clip that" only; "Eklipse listens to the audio of the stream it is already connected to"; "Each voice command triggers an automatic clip of up to 180 seconds, capturing 90 seconds before and after your command"; Premium; "Game-agnostic feature"). Ultra Highlights: private 1440p RTMP stream to an Eklipse stream key from OBS. Console Streamer: "Nothing installed on the console"; it just reads the Twitch/YouTube stream.

## B. Supported games and the per-game use-case pages

- Counts quoted on the site today: "3,000+ games supported" (home, /about-us/, /features/ai-highlights/). Older copy still says "1000+" (/use-case/marvel-rivals/, help 2026-03-04 "over 1,000 supported games", help 2026-03-31 "over 1,000+ supported titles"). The directory itself (app.eklipse.gg/games) is JavaScript-rendered; the number was not verifiable from data: "not confirmed".
- /use-case/ index: 78 distinct /use-case/ URLs (some games twice, e.g. fortnite-clipper and fortnite-clips), filter chips with counts: All 72, FPS 17, MOBA 3, Horror 4, Sports 11, RPG 9, Action 4, Battle Royale 4, MMO 4, Hero Shooter 3, Fighting 2, Sandbox 2, Simulation 2, Strategy 2, IRL 1, Party 1, Racing 1, Roleplay 1, Tabletop 1. These are marketing genre tags for SEO pages; they are not the engine's five buckets.
- What differs between per-game pages (read: Valorant, Fortnite, League of Legends, Minecraft, Marvel Rivals): the *named moment types* and copy; the workflow, pricing and feature blocks are identical templates.
  - Valorant: "aces, 1vX clutches, and flick headshots", "Operator picks", "Agent ult pop-offs"; "the same engine behind every game Eklipse supports".
  - Fortnite: "build fights, Victory Royales, snipes, and trickshots".
  - League of Legends: "pentakills, 1v5 outplays, and baron steals", "Teamfight ults, Turret dives".
  - Minecraft (no kill feed): "Eklipse watches for chat spikes, voice reactions, and on-screen events like a TNT explosion or a player death"; FAQ: "detection is based on streamer and chat reaction, so a big megabuild or redstone reveal that gets a chat spike gets clipped the same as a Bedwars kill."
  - Marvel Rivals: "MVP Multikills", "We scan the kill feed", "Team-Up Synergy", "Final Stand Clutches" (older copy: "supports 1000+ games", "Free plan includes auto-clipping").
- Per-game help pages carry a real per-game *event taxonomy* as an image (help, 2026-03-15/16). Valorant: KILL, ACE, CLUTCH, TEAM ACE, VICTORY (game events) + VOICE COMMAND, BEST K MINUTES, HIT MOMENT, HIGH VIEWER, HIGH COMMENT, WON, STREAM MONTAGE, EVENTS COMPILATION (generic). Fortnite: KILL/DOWN, MULTI KILL, DOUBLE/TRIPLE ELIMINATION, IMPOSSIBLE SHOT, MEGA/EPIC ELIMINATION, VICTORY + VOICE COMMAND, HIT MOMENT, BEST ROUND, ROUNDS, ALL-ROUNDS, WEAPON, HIGH VIEWER, HIGH COMMENT, DANCE, SINGLE/MULTI KNOCKED DOWN, FIRST STRIKE, FUNNY MOMENTS, AI NOMINATION, STREAM MONTAGE, EVENTS COMPILATION. Both pages: "you need to be an Eklipse Premium subscriber to access its dedicated AI detection model" ("Premium Exclusive Title").
- Game tiers: "certain high-demand, newly released, or resource-heavy games are 'paywalled'"; on Free, "the AI will only actively process and generate clips for those specific games if you are currently subscribed" (help, 2026-04-01). Mixed streams: "the Free plan will only generate highlights from the Free game portion of your VOD."

## C. Automation

- Auto-processing: connecting a channel makes Eklipse "automatically fetch and process your streams as soon as you go offline" (help, 2026-03-17); "Auto-Clip Streams — Keep clipping every connected stream without starting the workflow by hand" (/features/).
- Content Agent (Premium, activated from the mobile app's robot icon; help 2026-05-21): "I Hunt → I Edit → I Post", "you approve with a tap before anything goes live". Each clip gets a "Score", a "Platform Fit" rating and a "Why This Clip" tab showing "content type (e.g., 'Single Knocked-Down'), duration, recency". "Each pick comes with a plain-English reason: how strong the hook is, which platform the format suits, why it beat the moment next to it" (/features/content-agent/). "runs after every stream until you switch it off". Help notes it "appears in VIP Pass and Annual Premium plans".
- Content Planner: calendar queue across TikTok, Shorts, Reels, Facebook; "The queue does not need you present."
- Voice Command as a manual marker inside the automatic flow (see A).

## D. Gameplay Intelligence (what it detects)

Source: /help/how-gameplay-intelligence-picks-moments/ (published 2026-05-26; the HTML URL returns HTTP 500 with the article embedded in a WordPress error page; read cleanly through the site's WP REST endpoint /wp-json/wp/v2/docs/23995). Corroborated by blog.eklipse.gg/streaming-tips/how-gameplay-intelligence-works-2.html and .../gameplay-intelligence.html (company blog, modified 2026-05-26) and /help/plus-plan-moves-to-premium/.

- Three layers: "Advanced Moment Detection identifies candidate moments using game events, audio, action density, and continuity. Scene-aware context reads what is on screen to decide where each clip starts and ends. Per-genre tuning routes your VOD through detection logic built for its genre (FPS, BR, MOBA, tactical, strategy)."
- Game-state events read "when they are available": "Kill feed entries; Eliminations, defeats, knockdowns; Objective captures, round wins, victory states; Score changes and match transitions".
- Audio: voice / in-game / background separated; "A peak in your voice while game audio is loud and action density is high scores higher than the same voice peak during a quiet menu."
- Action density and continuity ("catches multi-kills and clutches as single longer clips instead of breaking them into fragments").
- Clip length by moment type (no fixed windows): one-tap/no-scope 5–8 s; standard kill 10–15 s; multi-kill streak 15–25 s; 1v3/1v4 clutch 20–30 s; round-deciding play with setup 25–40 s; narrative/reaction 15–35 s.
- **Genre buckets at launch (2026-06-01), verbatim:**
  1. "FPS (Valorant, CS2, Apex, similar), weights kill density, recoil/spray context, kill feed parsing."
  2. "Battle royale (Warzone, Fortnite, Arc Raiders, similar), weights drop spots, zone collapse, third-party fights, victory states."
  3. "MOBA (League of Legends, Dota 2, similar), weights teamfights, objective control, jungle pressure, ult timings."
  4. "Tactical shooter (Valorant ranked, CS2 competitive), weights round economy, clutch detection, plant/defuse states."
  5. "Strategy / non-action, weights narrative arc, escalation in voice and chat, game state changes over time."
- Selection: "You do not pick a genre manually. The engine reads game metadata at the moment of VOD import and routes your stream to the right configuration."
- Stated limits: unsupported titles "fall back to general action-detection logic. Clip quality is lower"; pure narrative content yields fewer clips; "the audio composition layer is tuned on English voice"; loud music hurts separation. "Manual selection inside a VOD is on the roadmap."
- Older per-event help (2025-08-18, pre-GI): "Kills, multi-kills, headshots in FPS titles like VALORANT, Warzone, Apex Legends; Objectives, goals, saves, and epic plays in games like Rocket League; Clutch wins, squad wipes, final-round victories in battle royale and survival modes". Help 2026-03-04 adds "Sports/Fighting (e.g., EA FC, Street Fighter): Goals scored, critical KOs, or perfect parries." and "ensure your stream is outputting at a high resolution (1080p is best) so the AI can clearly read the in-game text and kill feeds."

## E. Generic vs game-specific processing, and how the user "selects" a game

- Three tiers of specificity (inferred from the pages above): (1) per-game detection model with its own event taxonomy (Premium-gated for popular titles); (2) per-genre tuning/routing in Gameplay Intelligence (five buckets); (3) generic "dual-engine" fallback — "one part watches the screen, the other part listens to the audio" — used for unsupported games and Just Chatting ("the AI skips these specific visual event triggers for unsupported games" and "shifts its focus entirely to your audio and general on-screen action", help 2026-03-09). 2022 help: "We process the non-supported games through a general AI model".
- Game identification: "Eklipse heavily relies on the game category metadata sent by your streaming platform (Twitch, Kick, YouTube). If you started streaming Valorant but your Twitch category was still set to Just Chatting ... the AI applies the wrong detection model" (help, 2026-03-10). Override: Library → "Resubmit" → "search for the actual game you were playing, and select it" → full reprocess. Manual uploads ask to "Select game".
- One game per VOD: "the Eklipse AI is designed to analyze one game per VOD ... identifies the first game being played, locks in the specific AI detection model for that game"; workarounds are restarting the stream or splitting the VOD; "multi-game detection" is said to be in progress (help, mod 2026-03-10). The Plus→Premium page promises "noticeably better handling if you switch between game genres" per VOD, not within one.
- Layout advice that reveals the OCR dependence: "Keep your gameplay area clear of large overlays or webcam frames covering kill feed, mini-map, or notifications. Use default in-game UI placement" (help, 2025-08-18).
- Discovery: users do not browse pipelines; they browse a games directory (Free vs Premium per game) and per-game SEO pages. The only "selection" is the platform category (implicit) or the Resubmit/upload dropdown (explicit).

## F. Pricing (as published on 2026-10-06)

Official help pages are the primary source; company blog and third-party listings are marked.

| Item | Current figure | Source |
|---|---|---|
| Premium, web | $24.99/month; $112.99 per 6 months; $179.99/year ("~$14.99/month") | /help/how-much-does-eklipse-premium-cost/ (2026-04-12) |
| Premium, iOS/Android | $27.99/month; $112.99/6 months; $179.99/year | same |
| Free tier | Account, channel connection, "An AI scan of one recent stream" preview; "It is not a free plan, and there is no free clipping tier." Instant Edit, Convert to TikTok, manual editing stay free | /help/is-eklipse-free/ (mod 2026-08-30); /help/plus-plan-moves-to-premium/ |
| Free Gameplay Intelligence highlights | "Every account, free or Premium, gets 3 highlights generated by Gameplay Intelligence on June 1, 2026" | /help/how-gameplay-intelligence-picks-moments/ (2026-05-26) |
| Twitch VOD processing credits | Free: 3; Premium: 20/month, 120/6 months, 240/year | /help/what-is-the-twitch-vod-trial-limit-and-how-to-get-more-credits/ (2026-03-30) |
| Twitch link imports (paste URL) | 3 per user total, "exactly the same regardless of your subscription tier" | /help/how-many-twitch-link-imports-trial/ (2026-03-17) |
| Local MP4 upload | Premium only; 10 GB per file; 1 credit each; 10 per month | /help/how-to-import-streams-from-local-storage/ (mod 2026-09-27) |
| Max VOD | 6 h (Twitch & Kick) | help, 2025-08-18 |
| Premium extras | 1080p 60fps, no watermark, priority processing, "clip storage up to 90 days", paywalled games unlocked, Voice Command, Content Agent, Kick, scheduling | help 2026-03-30, 2026-04-01, 2026-07-26 |
| Free trial | "We don't offer a time-limited free trial" | /help/is-there-a-free-trial-for-premium/ (mod 2026-08-13) |
| Public pricing page | None: eklipse.gg/pricing/ redirects to the home page; app.eklipse.gg/pricing and /eklipse-premium return 404 unauthenticated. Company blog: "Eklipse doesn't publish a public pricing page and runs promotions, so the current rate is confirmed at checkout." | curl 2026-10-06; blog (updated 2026-07-18) |

Plus → Premium (help, 2026-05-26): "Eklipse Plus consolidates into Premium on June 1, 2026"; "Your existing Plus subscription rolls into Premium at your current billing rate"; "The consolidation is a name change plus the addition of Gameplay Intelligence, not a feature reduction"; "There is no launch-related price increase". After cancellation "Your account drops to the free tier" and gets the same 3 GI highlights.

Earlier published figures, for the trend (company blog, modified 2024-12-11): Free = 15 clips/stream, 3 h VOD, 720p, watermark, 14-day storage; Premium = $19.99/month or $149.99/year, 100 clips/stream, 12 h VOD, 1080p, 90 days, "3x faster". Capterra (third-party listing, "2026", undated) still shows $19.99 / $99.99 / $149.99 and "Free version: Available", i.e. stale. Company blog 2026-09-27: "No free plan anymore. ... That tier is gone." A 2023-12-31 help note documents an earlier price increase with grandfathering ("your plan will remain at the current price"). Inconsistency: blog 2026-07-18 says "Approximately $12.50/month" annual while help says ~$14.99; the help figure is used here.

## G. API, SDK, plugin system, developer program (H9)

- Home, /features/, /features/ai-highlights/, /about-us/, /esport/, /terms-of-use/ (effective 2024-05-30, last updated 2025-12-20): no mention of API, SDK, developer, webhook or plugin.
- Help-centre search through the site's WordPress REST endpoint (/wp-json/wp/v2/search): "API" → 0 help articles; "SDK" → 0; "webhook" → 0; "developer" → only unrelated articles; "plugin" → only the "Starting Soon Screen" and "BRB Screen" articles, which are OBS/Streamlabs browser-source overlays, not an extension mechanism. Voice Command help: "No plugin needed. There is nothing to install for Streamlabs or OBS."
- Hosts: eklipse.gg/developers/ and /api/ redirect to the home page; developer.eklipse.gg does not resolve through the proxy; api.eklipse.gg answers 404 at the root (host exists). The app's JavaScript bundles reference `https://api.eklipse.gg` and `/v1/...` paths (notifications, bounty, profile, stats) — an internal, undocumented REST API behind the web app (inferred from bundle strings; read only, not called). Not a published API.
- B2B: /esport/ is "Send us the broadcast. We return the highlights." with "Custom model on request"; no API, white-label or self-serve integration.
- Verdict: **H9 confirmed** — no published API, SDK, plugin system or developer program. Nuance for the second half of H9: tuning is per genre (five configurations) in Gameplay Intelligence, but the help centre also documents per-game "dedicated AI detection models" with per-game event lists, so "per genre" is only the routing layer.

## H. Which Eklipse capabilities map to specialised Clips Kitty pipelines

| Eklipse capability | Clips Kitty pipeline shape (inferred) |
|---|---|
| Per-game detection model with event taxonomy (Valorant ACE/CLUTCH, Fortnite ELIMINATION tiers, Marvel Rivals kill-feed scan) | One pipeline per game (e.g. publisher/valorant-highlights) that declares its event labels and uses OCR/YOLO on the kill feed and HUD |
| Per-genre tuning (FPS, BR, MOBA, tactical, strategy) | Genre pipelines as a middle tier (publisher/fps-generic) that specific game pipelines can extend or that run when no game pipeline matches |
| Generic audio/reaction fallback; Just Chatting/IRL/podcast scoring | The existing generic Clips Kitty pipeline (PANNs + transcript + LLM score) as the always-available baseline |
| Scene-aware clip boundaries and length-by-moment-type | A post-scoring "boundary" stage any pipeline can implement; a 5–40 s length table as a declared default |
| Score / Platform Fit / "Why This Clip" | Required output fields on the "scored and labelled time ranges" contract: score, label (event type), reason, suggested platform |
| Game identification from platform category + manual Resubmit | Pipeline auto-select by game id/genre from metadata with a user override and a "re-run with another pipeline" action |
| Voice-command marker (180 s window around "clip it") | A marker pipeline that detects trigger phrases in the transcript and emits candidate ranges |
| Vertical reframe keeping facecam/kill feed/minimap; auto captions | Existing render/captions stages, with layout presets per game |
| Content Agent (approve-before-post queue) and Content Planner | Publish stage with approval; scheduling is out of scope for a local app unless a connector exists |
| Esports "custom model on request" | Community pipelines for leagues/tournament VODs |

## 10. Known incidents and stated limitations

- No security incident involving Eklipse was found in the pages read; a dedicated incident search was not run ("not confirmed").
- Stated limitations: one game per VOD; wrong platform category → wrong model; 6 h VOD cap on Twitch/Kick; muted VODs (copyright) can yield no clips; English-tuned audio layer; loud music; overlays over kill feed/minimap; 1080p source recommended for OCR; Twitch link imports capped at 3 for life; paywalled games on Free; pure narrative content gets fewer clips; "Manual selection inside a VOD is on the roadmap."
- Pricing instability: Free clipping tier removed June 2026; Plus merged into Premium; a documented 2023 price increase; mobile price higher than web; figures differ between help, blog and third-party listings.

## 11. Borrow (for Clips Kitty)

1. A typed event taxonomy per pipeline, split into game events (KILL, ACE, CLUTCH, VICTORY…) and generic signals (HIGH VIEWER, HIGH COMMENT, FUNNY MOMENTS, VOICE COMMAND). Let the manifest declare the labels a pipeline can emit (supports H2 and H8).
2. Three-tier routing: game pipeline → genre pipeline → generic baseline, selected from metadata (game id/category) with a visible manual override, because "wrong game" is Eklipse's most documented failure.
3. Multi-signal scoring with explicit audio separation (voice vs game vs background) and action density/continuity, and variable clip length by moment type instead of fixed windows.
4. Every clip carries a reason (Score, content type, duration, recency, platform fit): cheap to produce, and it makes an approval UI fast.
5. Honest capability notes per pipeline: supported titles, required source resolution, UI-overlay sensitivity, language of audio tuning.
6. A "wanted games" channel: Eklipse routes requests through Discord; a registry can list requested-but-missing pipelines.
7. Clip-the-last-N-seconds voice marker as a pipeline (180 s window, three trigger phrases only, to avoid false triggers).

## 12. Avoid

1. Opaque engine swaps: Eklipse replaced its detection layer wholesale on one date with no way to pin or compare; Clips Kitty pipelines should be versioned and pinnable.
2. Silent fallback: when the game pipeline does not apply, say so in the output (Eklipse only tells users in a help article that quality "is lower").
3. One-game-per-VOD lock-in; design segment-level pipeline selection from the start.
4. Paywalling detection per game and credit-counting imports; irrelevant for a free registry but a reminder that per-game models are costly to build and run — the registry should expect few, specialised, community-maintained game pipelines rather than "3,000+".
5. Feature claims that drift between pages ("1000+" vs "3,000+", Free plan copy left on older pages); keep one source of truth for pipeline capability text.

## 13. Sources (all read 2026-10-06)

Marketing pages (eklipse.gg): / ; /features/ ; /features/ai-highlights/ ; /features/voice-command/ ; /features/eklipse-studio/ ; /features/content-planner/ ; /features/content-agent/ ; /features/ultra-highlights/ ; /features/eklipse-console/ ; /use-case/ ; /use-case/valorant-highlights/ ; /use-case/fortnite-clipper/ ; /use-case/league-of-legends-highlights/ ; /use-case/minecraft-highlights/ ; /use-case/marvel-rivals/ ; /compare/eklipse-vs-medal-tv/ (marketing comparison) ; /esport/ ; /about-us/ ; /terms-of-use/ (effective 2024-05-30, updated 2025-12-20).

Help centre (eklipse.gg/help/…; dates are the article's published/modified dates). Note: several help URLs return HTTP 500 with the article embedded in a WordPress error page; they were read through the site's public REST endpoint https://eklipse.gg/wp-json/wp/v2/docs/{id} or ?slug=.
- how-gameplay-intelligence-picks-moments/ (docs/23995, 2026-05-26) — HTML HTTP 500, REST OK
- plus-plan-moves-to-premium/ (docs/23997, 2026-05-26) — HTML HTTP 500, REST OK
- upgrade-eklipse-premium-before-gameplay-intelligence/ (docs/23993, 2026-05-26) — HTML HTTP 500, REST OK
- maximize-3-free-highlights-new-streamer/ (docs/23999, 2026-05-26)
- help/ index and help-category/ai-highlight-generator-for-games/ (27 articles listed)
- what-to-do-if-you-play-an-unsupported-game/ (2026-03-09) ; can-ai-detect-non-gameplay-moments/ (2026-03-09)
- how-to-fix-ai-detected-wrong-game/ (2026-03-10) ; why-did-the-ai-only-capture-clips-from-my-first-game/ (mod 2026-03-10) ; eklipse-inaccurate-ai/ (mod 2025-08-18)
- what-specific-types-of-in-game-events-can-the-ai-recognize/ (2025-08-18) ; how-does-eklipse-ai-choose-highlights/ (2026-03-04) ; are-there-any-stream-layout-best-practices-…/ (2025-08-18)
- is-there-a-maximum-vod-length-the-ai-can-process/ (2025-08-18)
- does-eklipse-support-valorant/ (2026-03-16, event list is an image) ; does-eklipse-support-fortnite/ (2026-03-15, image) ; does-eklipse-support-new-games/ (2026-03-31) ; why-is-my-game-not-supported-or-processing/ (2026-04-01) ; why-are-some-games-or-clips-paywall-restricted/ (2026-03-30) ; is-eklipse-ai-trained-overwatch-2-and-2k23-nba/ (2022-12-16)
- how-to-use-eklipse-content-agent/ (2026-05-21) ; automate-gaming-content-pipeline/ (2026-05-21)
- what-platforms-devices-and-games-are-supported-voice-command/ (2025-08-19) ; how-much-of-the-stream-is-clipped-per-command/ (mod 2026-07-26)
- how-to-import-streams-from-local-storage/ (mod 2026-09-27) ; how-many-twitch-link-imports-trial/ (2026-03-17) ; what-is-the-twitch-vod-trial-limit-and-how-to-get-more-credits/ (2026-03-30) ; do-i-need-to-install-any-softwares-to-use-eklipse/ (mod 2023-04-03)
- how-much-does-eklipse-premium-cost/ (2026-04-12) ; is-eklipse-free/ (mod 2026-08-30) ; is-there-a-free-trial-for-premium/ (mod 2026-08-13) ; increase-premium-plan-pricing/ and subject-of-price-increase/ (2023-12-31)
- how-does-eklipse-work/ (2025-08-13) ; how-can-i-automatically-generate-clips-from-my-gaming-streams/ (2025-11-13)
- REST searches: /wp-json/wp/v2/search?search=API|SDK|webhook|developer|plugin (0 relevant help hits for API/SDK/webhook)

Probes: eklipse.gg/pricing/ → redirects to / ; app.eklipse.gg/pricing → 404 ; app.eklipse.gg/eklipse-premium → 404 ; eklipse.gg/games → app.eklipse.gg/games (JS-rendered, list not read: "not confirmed") ; eklipse.gg/developers/ and /api/ → redirect to / ; api.eklipse.gg/ → 404 ; developer.eklipse.gg → connection failed (not confirmed). App bundle strings referencing api.eklipse.gg and /v1/ paths were read, not called.

Company blog (blog.eklipse.gg; marketing/company-authored): /streaming-tips/how-gameplay-intelligence-works-2.html (mod 2026-05-26) ; /streaming-tips/gameplay-intelligence.html (mod 2026-05-26) ; /eklipse-news-and-guide/is-eklipse-free-pricing-and-features-explained.html (updated 2026-07-18) ; /featured/eklipse-gg-review.html (2026-09-27) ; /guide/eklipse-free-vs-premium-review.html (mod 2024-12-11, historical pricing).

Third-party: capterra.com/p/10015243/Eklipse/pricing/ (review-site listing, undated, stale figures). Web-search snippets naming a founder, Singapore and "PT Main Games" (Jakarta) and an October 2024 accelerator round were not opened: "not confirmed".

---

## Hypotheses

- **H9 — confirmed.** No API, SDK, plugin system or developer program is published anywhere read (home, features, terms of use, help-centre REST search for API/SDK/webhook = 0 hits; /developers/ and /api/ redirect home; the only "plugin" articles are OBS browser-source overlays). Genre tuning: five buckets named verbatim in the 2026-05-26 help article (FPS, battle royale, MOBA, tactical shooter, strategy/non-action), selected automatically from game metadata. Nuance: per-game "dedicated AI detection models" with per-game event lists also exist (help, March 2026), so detection is per game and per genre.
- **H2 — supported by analogy.** Eklipse's user-visible output is exactly a ranked list of time ranges with a label (content type), a score, a reason and a platform fit; everything else (reframe, captions, posting) is downstream. "Video in, scored and labelled time ranges out" matches the smallest useful unit.
- **H8 — supported by analogy.** Event labels are typed per game (KILL, ACE, CLUTCH… vs ELIMINATION tiers) with a shared generic set; a capability/label vocabulary per pipeline is how several implementations can coexist.

## Matrix rows

Standard template row:

| Platform | Extension type | Model support | Workflow support | Registry | Git-based | Local | Remote | Versioning | Security | Marketplace | What Clips Kitty should learn |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Eklipse.gg | n/a (closed SaaS; internal per-game models + per-genre configs) | internal only (OCR, object detection, audio separation; nothing exposed) | fixed: import → detect → library → Studio → Publisher/Content Agent | n/a (games directory, requests via Discord) | no | no | yes (cloud only) | engine replaced wholesale 2026-06-01; no pinning | OAuth to platforms; approve-before-post | n/a (subscription, $24.99/mo, $179.99/yr) | typed per-game event labels + genre routing + generic fallback; scored, explained time ranges |

Track D row:

| Platform | Main use case | Generic vs game-specific | Automated highlights | Game-specific logic | Extensibility | What Clips Kitty should learn |
|---|---|---|---|---|---|---|
| Eklipse.gg | Cloud auto-clipping of Twitch/Kick/YouTube/Facebook streams into 9:16 clips, scheduled to TikTok/Shorts/Reels; Premium $24.99/mo or $179.99/yr, no free clipping tier since 2026-06-01 | Three tiers: per-game detection models (Premium-gated for popular titles) → five genre configurations (FPS, BR, MOBA, tactical, strategy) → generic audio/reaction fallback for the rest of the "3,000+" games and Just Chatting; game chosen from platform category metadata, one game per VOD, manual Resubmit override | Yes: auto-process on stream end, ranked clips with Score / Platform Fit / "Why This Clip", variable clip lengths 5–40 s by moment type, Content Agent hunt→edit→schedule with approval, 180 s voice-command marker | Kill-feed parsing, eliminations/knockdowns, objective/round/victory states, score changes via OCR + object detection; per-game event taxonomies (Valorant ACE/CLUTCH/TEAM ACE; Fortnite DOUBLE/TRIPLE/MEGA/EPIC ELIMINATION); genre-weighted scoring; audio split into voice/game/background | None published: no API, SDK, plugin system or developer program (H9 confirmed); internal api.eklipse.gg /v1 seen only in app bundle strings; game requests via Discord; esports "custom model on request" | Declare typed event labels per pipeline; route game → genre → generic from metadata with a user override; score on several signals and emit a reason per clip; size clips by moment type; state limits (resolution, overlays, language, one game per VOD) up front |
