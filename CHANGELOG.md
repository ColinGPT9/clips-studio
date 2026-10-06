# Changelog

What changed, written for people who use Clips Studio rather than people who
read the commits. Dates are release dates.

This project is in **alpha**: versions move fast, and things listed as fixed
were often broken in a way that only showed up on somebody else's machine.

---

## Unreleased

### Added

- **Basketball, the second sport.** Tick **Sports** and choose 🏀 Basketball: dunks,
  threes, blocks, steals and game winners each become a clip with the possession before
  them and the reaction after. The score bug confirms every basket and its points, and
  the quarter and game clock make a game-winning three in overtime count for far more
  than a first-quarter one. The broadcast's cutaways to the crowd, the bench and
  courtside are found too: merged into the play's clip, or clipped on their own with
  **Fan reactions**, **Celebrity reactions**, **Crowd reactions** or **Bench reactions**.
  A person is named only when the broadcast captions them. A **Quarter** menu picks part
  of the game, the 9:16 crop follows the ball, the play around it and the rim, and a game
  filmed 9:16 keeps its picture. It runs on the same Whisper and AI model as everything
  else. Measured on three NBA broadcasts: the score bug is read piece by piece (two-row
  bugs, logos beside bare scores), court and crowd shots are told apart by how big the
  people in them are, and the crop no longer snaps when the camera pans. See
  [docs/SPORTS.md](docs/SPORTS.md#basketball).

### Changed

- **The box at the bottom offers to install Gemma 4.** Ask Clips Kitty only runs on a
  Gemma 4 model, and setup installs a different one on most PCs. The box used to say so
  in a line of small grey text. It now says "Install Gemma 4 to use this box" and has an
  **Install Gemma 4** button, with the build already chosen for your graphics card and
  the size of the download beside it. The percentage shows on the button, and the box
  starts working by itself when it finishes. The model that picks your clips stays as it
  was (#121).

### Fixed

- **A model download that failed no longer says it finished.** When Ollama could not
  fetch a model (no connection, a name that does not exist, a full disk), the Models
  page, setup and the box above all showed the download as done, with nothing installed.
  They now say "Download failed" and why.

- **H.265 files no longer fail at transcription.** Adding a file that was not H.264 (an
  H.265 export from DaVinci Resolve, a phone or GoPro recording) converted it on the spot,
  which takes minutes for a long recording while the button only said "Starting…". If
  that was stopped part-way, by closing the app or pressing Generate again, a half-written
  copy was left behind and used anyway, and the job failed with "Invalid data found when
  processing input". The file itself was never the problem. Now:
  - adding an H.265, AV1 or VP9 file takes seconds, and the conversion happens when the
    video is processed, where you can see it, for 16:9 clips as well as Shorts;
  - a copy is only ever used once it is complete, and one left half-written by an earlier
    version is redone when you add the file again (#122).
- **Captions on the seconds you add to a clip.** A clip made longer, in AI Edit or the
  editor, came back with no captions on the added seconds, and neither re-rendering nor
  changing the font brought them. It happened to a clip whose captions had been saved
  before, and opening the Captions panel and pressing Apply was enough to save them.
  Saved captions now follow the clip: the added seconds are captioned, your corrected
  text stays, and a clip already stuck this way is put right the next time it is
  re-rendered (#120).

---

## 2.0.0: Soccer, and NVIDIA PCs transcribe again

If videos stopped at "Transcribing" on your NVIDIA PC, this release fixes it. It also
brings Sports (Soccer first), Gaming / Reaction layouts, watched channels that clip and
post on their own, and the option to use your own AI key.

### Added

- **Sports, starting with Soccer.** Tick **Sports** for a match, and choose which moments
  to keep (Best moments, All goals, Goals + celebrations, Best saves, Best chances,
  Attacking plays, Cards or Penalties, each explained in the list), and optionally the
  teams or players to favour. The whole match is always clipped. Each moment becomes one clip with its
  build-up and its reaction, and the 9:16 crop follows the ball instead of the biggest face.
  Also in a queued video's settings, a watched channel's settings, the API and MCP. It uses
  the models you already have: no new download.
  - The score box is read through the whole match, so a goal is certain, with the side
    that scored and the minute. The crowd's roar dates it, since the score changes after
    the ball goes in. On a World Cup final all six goals were found at the right minutes.
  - Other moments are named only when the commentary names them and the crowd, the
    whistle or the screen agrees; otherwise they're big moments. Replays and celebrations
    are grouped with their goal, never clipped twice.
  - The clip card names the moment ("Goal · 65'", and the side that scored), and the clip page says what the
    match gave and the score read. See docs/SPORTS.md, which also shows how to add a sport.
  - Vertical videos too: a match filmed or streamed 9:16 is kept as filmed, with nothing to switch on, and its
    goals are found the same way. With Longform, the 16:9 clips and the Highlights reel are the match's
    moments as well.
  - Club and phone footage, recognised by itself when no score box shows. Club footage is
    framed on the ball like a broadcast, but with no score box or commentary nothing marks its goals, so none
    are claimed: a choice like All goals keeps the best moments instead, and the clip page says so.
  - **Match events**: tell Ask Clips Kitty the match's goals as you have them (from your club app, the match
    report or the video's description: "18:16 Goal Player A", "45+2' yellow card") and each becomes a clip,
    with the player or team on it. Match minutes are placed by the clock on screen or the list's kick-off
    times; a club app's tag gets a clip long enough to hold the attack, the goal and the celebration.
  - The Sport menu shows ⚽ Soccer / Football, with Basketball and Cricket listed as coming soon.
    Hover a Highlights choice to see what it keeps. For a match streamed 9:16, Soccer is a Vertical Live
    content choice.
  - **Story reels**: **Also make** a Match recap (every goal, card and save, in match order), a reel per team,
    and a reel per player named in two moments or more, by your match events or by the commentary for a name in
    Teams or players. Joined from the clips already made, so they take seconds, with one end card at the end and
    the moments listed in the description. With Longform, in 16:9 too.

- **Remote rendering (experimental).** Another computer of yours can render the clips,
  so the one you use stays free while a long stream is processed. Settings → Advanced
  settings (off by default, and invisible until you switch it on): pair a render PC with
  a one-time code, then render on this computer, automatically on a render PC when one
  is free, or only on a chosen one. Only the rendering moves; the clips it sends back
  are identical to local ones. Transfers resume, results are checked, a render PC that
  drops out gives its clips back, and nothing needs your router opened (Tailscale for
  PCs in different places). See docs/REMOTE-RENDERING.md.

- **The Clip Editor groups your videos by creator.** Pick a creator, then a video from its list, so a
  big library stays tidy.

- **Gaming / Reaction, for game streams and reaction videos.** The streamer's webcam and
  the game (or the video they're reacting to) laid out together, in one of eleven layouts
  like StreamLadder's: Split, Basecam (game on top), Half, Fullscreen, Blurred, Small or
  Circle facecam, Game UI, Mosaic, Dual facecam and Duo split. With no webcam, the game on
  its own. Tick **Gaming / Reaction** and **Choose a layout** opens on the video's own
  frames before anything is processed: pick a layout from cards that show this video in
  each, drag and resize the webcam and game boxes, choose Camera or Game on top, drag the
  line between them, and see the 9:16 result live. Remembered per creator if you want.
  - Faces stay clear of TikTok's, Reels' and Shorts' buttons and captions. The webcam is
    placed from where the streamer's head is, and the preview shows the chosen platform's
    UI with "✓ Face clear", or what to change. A webcam at the top with no room above the
    head is moved down on a blur, never cut off at the top; one under the game stays
    right against it, with no blur between the game and the streamer's head.
  - A whole game sits right against the webcam: never a band of blur between the
    streamer and the game. The blur goes above the two (clear of the platform's top bar)
    and below them. No black bars.
  - A stream's solid panels (a black chat bar under the game, a speedrun's splits) are
    found and kept out of the game, so the Short never ends in a useless bar. Chat drawn
    see-through over the gameplay stays: it's part of the stream.
  - The editor opens with the webcam and game boxes already on the frame to drag, as in
    StreamLadder: **Draw it** is the default, and the webcam box moves onto the webcam
    when Clips Kitty finds one.
  - On the preview, as in StreamLadder: drag the small or round facecam to move it and any
    of its eight handles to resize it, and the same for the Game UI layer. Dual facecam is
    two round webcams, always the same size. The Game UI is cut to its space, with no blur
    round it.
  - Parts snap into line like a design editor: to the middle of the Short, its edges, the
    platform's safe lines and each other, with a guide line showing it (Alt to place freely).
    **Grid** shows thirds, the middle and the safe box. The boxes on the video frame snap
    the same way, to each other and to the chat bar's edge, and show the grid while dragged.
  - The game box on the video frame stops at the webcam: its edges show as dashed lines
    while you drag, and the game snaps to them instead of going over it, so the streamer
    isn't shown in the game too (Alt places it anyway, with a note saying so). The
    automatic game area keeps the same distance, so the webcam's border no longer shows
    as a line down the game.
  - Zoomed to fill, the game box has the shape of the game's space on the Short and follows
    it as the webcam's share or the layout changes, so what's inside the box is what renders.
  - The layout editor has fullscreen (like a video player, Esc to leave, remembered) and
    minimise to a bar in the corner.
  - The preview draws TikTok's, Instagram Reels' or YouTube Shorts' own layout over the
    Short, measured from each app's feed, with icons in each app's style. The safe areas
    the face check uses come from the same measurements.
  - When the editor opens, a webcam is suggested: the same person in the same framed spot
    across the video. Found automatically when processing, the streamer is whoever TalkNet
    says is talking in sync with the audio, across several clips of the video: never the
    biggest face, so game characters, portraits and the people in a watched video aren't
    taken for the streamer. Tested on 13 streams including World of Warcraft, Zelda, GTA V
    and League of Legends (docs/GAMING.md has each result).
  - The webcam box stops just inside the webcam's own border: no chat or panel beside it.
  - A stream that changes partway is handled clip by clip. An hour of Just Chatting before
    the game, or a reaction streamer going full screen on their camera between videos: those
    clips are framed like a talking-head clip, even with the webcam drawn or remembered for
    the creator, and the rest keep the split. A webcam that moves when the streamer changes
    scene is followed to where it is in each clip. The automatic webcam search looks past
    full-screen clips, so a long chatting opening no longer leaves the game clips without
    the streamer.
  - The clip editor's Effects tab has the same editor (Layout → **Gaming / Reaction** →
    **Change layout…**) to change one clip.
  - In gaming mode the "person on screen" part of the score is left neutral, so top-down
    games like League and Dota are no longer scored as having nobody in them.
  - For streamers on a real camera; VTubers aren't supported.
- **Gaming streams are scored as gaming streams.** A kill streak, a boss going down or
  a goal counts, even from a streamer who says little. Gaming / Reaction scores this
  way, and so does a live that was already vertical with **Vertical Live content** set to
  **Gaming / reaction** (a row under Vertical Live, like Longform's output), the same scoring
  in full (game characters and the people in a watched
  video aren't taken for the streamer there either). What is said still counts exactly as on any stream (a reaction is mostly
  talk); the game adds to a clip on top of that, up to +12, like creator context does,
  and carries the quiet stretches. The AI is told which game it is (Twitch says per part of the stream; Kick
  and YouTube give the category, title or tags) and what a highlight is in that kind
  of game; chat's reactions mark moments and what kind (hype, laughing, surprised,
  scared, a fail, "clip it", in any language and with a channel's own emotes); a
  sudden shout or laugh from the streamer marks them too, and so does the game's own
  sound (gunfire and explosions in a fight, a crowd or a goal explosion in a sports
  game, a crash in a race, a scream in a horror game), heard by a small bundled sound
  model even when chat and the streamer are quiet. The screen is read too: a banner
  (ELIMINATED, VICTORY ROYALE, "X A MARQUÉ", YOU DIED) names the moment, and a menu,
  queue or settings page that chat reacted to is marked down. Last, a local AI model that
  takes images (Gemma 3 or 4) looks at a few frames of the best clips: a clear moment
  moves up, a menu or a black screen moves down. The picture never goes to a cloud AI.
  A menu or no gameplay only costs a quiet clip: a Just Chatting clip full of talk
  loses nothing for it.
  Each moment becomes a
  15-35 s candidate starting just before it, and the clip's score breakdown shows what
  marked it. Standard scoring is unchanged.
- **Vertical Live, for streams that were vertical all along.** A YouTube vertical live,
  the vertical feed of a Twitch Dual Format or Streamlabs Dual Output stream, or a
  downloaded Instagram or TikTok live is already a finished 9:16 video. Tick **Vertical
  Live** and Clips Kitty keeps its layout: no face tracking or reframing, one encode of
  the whole frame at 1080x1920. The moments are found exactly as before, with your chosen
  AI, and clips caption, brand, publish and schedule as usual.
  - YouTube vertical lives now download at 1080x1920 in this mode (standard processing
    picks a smaller size for vertical video). A video with no vertical version is refused
    with what to do instead, never swapped for the horizontal one.
  - A file that isn't 9:16 is refused before any work, with **Use standard processing**.
    A 9:16 file you add gets "Is it a vertical live?", and can carry its original link.
  - Watched channels have the same switch. Videos with no vertical version are skipped,
    so a Streamlabs dual-output channel isn't clipped twice.
  - In Clips Kitty Web too: a Twitch or Kick VOD is cut from its vertical version.
  - Twitch's vertical VODs couldn't be downloaded by link when this was built: download
    the vertical recording and add it as a file.

- **Watch a channel and let Clips Kitty do the rest.** Add a YouTube, Twitch or Kick
  channel on the new **Watched channels** page. When it posts, the new video joins the
  queue by itself, and once the clips are made they are published through WoopSocial,
  either straight away or after you press Publish, which is the default. Set it up
  once per channel: the clip settings, where the clips go, how many posts a day, and a
  line under every caption that can link back to the full video.
  - Adding a channel never clips what is already on it. Those videos are listed, one
    click from being clipped if you want them.
  - A video is only ever clipped once, and a clip is only ever posted to a platform
    once, even after a crash or a restart.
  - Live streams, premieres and Shorts are waited for or skipped rather than grabbed
    half-finished.
  - Videos posted while Clips Kitty was closed are found when it opens. You choose
    whether to clip only the newest, all of them, the last day's, or none.
  - To keep watching on a spare PC, turn on **Keep watching when the window is
    closed**. Clips Kitty then stays in the system tray until you quit it from there.
    It is off unless you turn it on.
  - **See it working.** A live panel at the top of Watched channels shows that
    Clips Kitty is watching and what it is doing right now: checking a channel,
    a new video found, clips being made with a progress bar, clips scheduled, a
    post going live. A green dot beside Watched channels in the sidebar shows it
    from every page, and each channel carries a Watching badge.
  - Kick has no official way to list a channel's videos, so Kick watching uses the
    same unofficial one Kick downloads already rely on, and says so if it stops
    working.
  - Opening Clips Kitty a second time now brings the running window forward instead
    of starting a second copy that cannot work.
  - **Hands-off, for an always-on PC.** Choose "Clip and publish automatically" when
    you add a channel, tick where the clips go, and leave Clips Kitty running. Every
    new video is then queued, clipped and published with nobody at the PC. It keeps
    going through the usual hiccups: a failed download is tried again, a publish that
    cannot reach WoopSocial waits and tries again, and posts a platform turned down
    are sent again later, only those ones. Turn on **Delete each watched video's
    download once its clips are published** so the disk does not fill up.
  - **Set everything up once.** Before you even press Add, choose how many of each
    video's clips to post (the best ones first, or all), whether to post right away
    or space them out, posts per day, hours apart
    and the time the first post of each day goes out, with a preview of the next
    posting times, just like the Publish dialog. The channel then opens its whole
    setup for the rest. Add your
    own hashtags, like `#creatorname #twitch`; they lead every caption so they are
    always kept, and you can leave out the AI's hashtags entirely. Each platform
    has its own settings: YouTube visibility, who can watch on TikTok and whether
    comments, duets and stitches are allowed, Reel or Story on Instagram and
    Facebook, and your Pinterest board.
  - **It learns about the creator you watch.** Adding a channel gives it a
    profile in Creators straight away, marked Watched, and every video it clips
    is learned into that profile. The channel's card shows how much it knows so
    far, with a button that opens the profile, and the live panel says what each
    new video taught it.

- **PC too old to run the AI? Bring your own API key.** Clips Kitty still runs
  everything on your PC by default, free and private. For a laptop with a small
  graphics card, or a low-spec mini PC watching channels, **Settings → AI** can
  now hand the work to a cloud provider on your own account instead. Ollama stays
  first; **OpenRouter** is the recommended cloud option, one key for many models
  you can switch between, shown with each model's price; OpenAI, Google Gemini,
  Anthropic Claude, xAI Grok, Meta's Muse Spark, DeepSeek and Qwen are there
  too, as direct providers under Advanced (a Qwen key's Alibaba Cloud region is
  found for you). The provider bills you directly; Clips Kitty has no key, credits or
  server of its own. Where local AI struggles (setup on a PC with no graphics
  card or a small one, a model too big for the card, a job that failed in the
  local AI, Watched channels on a small PC) the app points to OpenRouter. With a cloud model chosen, everything the local model did uses it: clip
  picking, titles, creator learning, clip edits, translation and the assistant.
  - Transcription is its own choice: Whisper on your PC, or online with your key
    through OpenAI, OpenRouter or xAI, with the word timings captions need.
  - With OpenRouter, pick the text model and the voice model from its live
    catalogue, each showing OpenRouter's current prices (and when they were
    fetched), with a refresh. A voice model not yet known to give word timings
    is checked with a three-second test clip before it is used.
  - **Sign in with OpenRouter**: approve Clips Kitty in your browser and it gets a key of
    your own, on your OpenRouter credits, with nothing to copy or paste (OAuth with PKCE).
    Pasting your own key is still there, under Advanced. **Copy sign-in link** connects a
    different OpenRouter account from a private window.
  - The Gemma models come first in the text model list, under the preferred one.
  - **★ Preferred** models head each list, one click away: Gemma 4 26B-A4B for text
    (about a cent for a two-hour stream) and Whisper large-v3 turbo for voice (about
    $0.01 an hour, with word timings). A free (`:free`) model's note offers its paid
    version, and a job it stops says it was the free version's limit.
  - Online transcription no longer stops at a stretch where nothing is said: a
    stream's ten-minute music intro used to fail it as "no word timings". And the
    voice detector local Whisper uses now runs on each part first, so the "Thank
    you." Whisper makes up in quiet stretches (640 in one stream) is dropped, and a
    part where nobody speaks isn't sent or paid for.
  - Your key is checked before it is saved, stored encrypted, and never shown
    again apart from its last four characters. **Test connection** checks the
    key and model without spending anything.
  - **Experimental: use a ChatGPT plan you already pay for** instead of an API
    key, through OpenAI's own Codex (OpenAI / ChatGPT plan, under Advanced). Sign
    in with ChatGPT in your browser; pick a model your plan offers; the card shows
    how much of the plan's limit is used. It stops at the limit rather than spend
    ChatGPT credits, and Watched channels only use it if you allow them. It needs
    a paid plan (OpenAI refuses free accounts outside its own app), and for now
    only runs from source. Claude and Google plans can't be used by other apps;
    their entries say so, and why.
  - If the provider fails (a bad key, no credit left, rate limits) the job says
    so plainly. Nothing ever switches to another provider, or back to the local
    model, without you choosing it.
  - How it works, what it sends and what it costs: `docs/AI-BACKENDS.md`.

- **Ask an AI assistant to do it.** Clips Kitty now speaks MCP, so Claude, Cursor
  or any MCP client can queue a stream, follow the job, read back the clips it
  chose and export one, in plain language. It needs no API key of any kind,
  because the model doing the work is the one already on your PC.

- **Integrations can be told when a job finishes.** Send a webhook address with a
  video and Clips Kitty posts to it once, the moment that video is done, so a
  dock or an automation can sit quiet instead of asking every few seconds. Add a
  secret and the message is signed, so your listener knows it is really us.

- **Thumbnails made from the clip, on your PC.** Press **Make thumbnails** in the
  YouTube panel and Clips Kitty looks through the clip for frames where someone
  is facing the camera, crops each to 16:9 around them and puts the clip's hook
  across the bottom. Pick one like any other thumbnail. It costs nothing and
  sends nothing anywhere: the faces, the frames and the type all come from what
  is already on your machine. The three fixed suggestions are still there, and a
  clip with no usable frame simply offers none.

- **Highlight videos get chapter timestamps.** The description now lists each
  moment with the time it starts, so viewers can skip straight to the bit they
  want. They are added only when YouTube's own rules allow it: a chapter list
  that breaks them is ignored completely, and a list that silently does nothing
  is worse than none.

- **Tell Clips Kitty what to do, in a sentence.** There is a box on the dashboard
  now. Write something like "clip the newest stream and schedule the clips an
  hour apart from tomorrow morning" and a Gemma model running on your PC works
  out which steps to take and takes them, showing you each one as it goes.
  Uploading is the exception: it can plan a batch of uploads, but the plan comes
  back for you to read, and nothing goes to YouTube until you press the button.
  The box needs a model that can call tools, which means Gemma 4 or newer; if the
  model you have cannot, it says so rather than guessing.

- **Plan a batch of uploads before any of it happens.** Assistants and MCP
  clients can ask for a publishing plan: which clips, what each one's title and
  description will be, and when each goes out. Nothing is created until the plan
  is sent back for execution, so a batch with the wrong description is something
  you catch while reading rather than something you undo thirty times.

- **Put the same links under every video.** The YouTube panel has an "Add to
  every description" box for your Twitch, your Discord, whatever you always
  paste. It goes under each description as the clip is published, once, without
  repeating itself when you publish a clip again.

- **Say how you want the clips made, in the same sentence.** The box and any
  MCP client can now set what the panel's tick boxes and menus set: captions on
  or off, the caption font, size, colour, position and word count, a watermark
  by the name you saved it under, podcast footage, longer clips, and the
  horizontal longform modes. "Clip this with big yellow captions at the top and
  my Main channel watermark" now does all three. A font or colour it does not
  have is refused with the list of real ones, rather than quietly rendering
  every clip of the stream in the wrong one.

- **Drag the assistant to the height you want it.** There is a grip on its top
  edge, and the size is remembered. It never grows past the space there is, so
  the box you type into stays on screen.

- **16:9 and 9:16 clips of the same video in one go.** With Longform on, tick
  **Also make 9:16 Shorts** beside *Longform output*: the video's vertical Shorts
  are made first, then the 16:9 output, in one run. Each clip is its own card, and
  the horizontal ones are marked 16:9.

- **Tell it what the clips should be about.** Say it in the box at the bottom when
  you ask for a video: "clip this stream, make sure you include when I died to the
  boss, prioritize funny moments, and more from the WoW part around 1:35". The clips
  are still picked the usual way; the direction only adds to it:
  - points for clips about the topic or moment you named (the AI adds the words it
    comes up with, so "WoW" also finds "Azeroth"), inside a time you gave ("1:35-1:55",
    "around 45 minutes", "near the end"), or with the funny, laughing, hype or
    reaction moments you asked for;
  - a look at moments there the first pass skipped, across the whole stream;
  - a must-have ("make sure", "at least two clips of") that was actually said is kept,
    even under the quality bar or a clip cap.

  It never lowers a score or removes a clip, so "avoid the intro" is noted rather than
  applied. A must-have that was never said is reported ("Couldn't find: ...") and no
  clip is made up for it. The video's clip page shows what was understood, and each
  clip says what it got extra points for. A video asked for without a direction is
  processed exactly as before. Works with every AI setup; on the API and MCP it's `focus`.

### Fixed

- **16:9 clips no longer fail with "error 404".** Longform asked for the AI model named in
  settings even when setup had downloaded a different one for your graphics card, so every
  run stopped with a 404. It now uses the model you have, as 9:16 clips already did.
  Translating clips and the clip editor's chat did the same, and are fixed too (#118).
- **Cancel stops a long video while it's being analysed.** On a long VOD, pressing Cancel
  during "Finding the best moments" did nothing until a whole-video pass (its sound and
  picture, the chat replay) had finished, which on a 4-hour stream looked like loading
  forever. Cancel now stops it within a couple of seconds, decoding and all.

- **One odd answer from the AI model no longer fails a whole video.** When the model
  put a line of text where a clip belonged, the video stopped with "'str' object has
  no attribute 'get'", ten minutes into a long match. That entry is now skipped and
  the rest of the answer kept, like any other entry it can't read.

- **Videos no longer fail at "Transcribing" on NVIDIA PCs** with "Library
  cublas64_12.dll is not found". Whisper's engine needs NVIDIA's cuBLAS 12, which
  the app stopped carrying when it moved to CUDA 13 for the RTX 50-series; it now
  ships with the app, so GPU transcription works with nothing else to install. And
  if the GPU's libraries can't be loaded for any reason, the video is transcribed on
  the CPU (slower) with a note in the log saying why, instead of failing.

- **Minimum score can be changed in Settings** (Advanced settings). When a video
  gives no clips, the app suggests lowering it, but until now it could only be
  changed in the settings file.

- **Closing the window closes the app when nothing is being watched.** With "Keep
  watching when the window is closed" ticked, Clips Kitty went to the system tray
  even with watching switched off or no channels on. It now stays in the tray only
  while it's actually watching a channel.

- **Adding a video again for the other format makes its clips.** After 16:9
  (Longform) clips of a video, adding it again for 9:16 Shorts finished in two
  seconds with nothing made. It now makes the Shorts, and Longform after Shorts no
  longer asks "already processed". A file you've really made Shorts of before
  now offers **Process again**, as a pasted link does, instead of a silent run.

- **Gemma 4 on your own PC reads the stream again.** With gemma4:e2b or gemma4:e4b
  (what setup installs for graphics cards of 6 GB and under) as the AI, the part that
  reads what was said came back empty for most of a video, so clips were picked
  almost only from sound and movement, with titles like "High-energy moment", and it
  took ten minutes to find one. Gemma 4 now answers without its thinking step and
  without Ollama's JSON mode, which made it repeat itself until Ollama gave up: on a
  13 minute stream, 16-17 moments found in under a minute instead of one.

- **The box at the bottom answers every time on Gemma 4.** About one request in
  three came back blank, with nothing done: the app's instructions and tool list
  filled Ollama's default 4K context, and Gemma 4 ran out of room while thinking. It
  now gets 16K, and a model that still says nothing is reported instead of an empty box.

- **Esc in the Gaming / Reaction layout editor no longer closes the clip editor
  behind it.** Opened from the clip editor, Esc closed both; it now leaves
  fullscreen first and then closes only the layout editor.

- **Editing a clip can no longer lose it.** Applying edits deleted the clip
  before rendering the new version and put it back afterwards, so a render
  that failed, or closing the app halfway through one, lost the clip and its
  translations for good. The clip is now only replaced once the new version
  has rendered.

- **The Dashboard's Donate button lines up with Start posting everywhere
  again.** The posting card's text grew, and the Donate button beside it
  stayed high; both buttons now sit on one line however long either card is.

- **A watched channel uses your clip settings.** Its clips came out with the
  app's defaults, captions included, even with captions unticked: the choice
  lived in the Generate bar and never reached the channel, and the channel's
  own Clip settings needed a separate Save that was easy to miss. Adding a
  channel now starts from your Generate settings, shown in the form, and a
  channel's Clip settings and Publishing save themselves as you change them.

- **Watched channels skip YouTube Shorts.** There is nothing to clip from a
  Short, so they are no longer listed or queued; only full videos and
  finished streams are.

- **Hashtags come out as separate hashtags.** The AI sometimes wrote several
  hashtags run together, like `#creatorname#drama#apology`, and they were
  posted that way, as one long unreadable tag. They are now split into
  `#creatorname #drama #apology`, for new clips and for clips you already have.

- **No more "Clip from:" captions.** When the AI could not write a clip's caption,
  the clip was given "Clip from: <the video's title>" and a #clips hashtag. Every
  clip of that video then went out with the same caption announcing it was a
  repost, and TikTok flagged them as unoriginal content. A clip like that now
  gets no caption text beyond its title and your hashtags, and clips that already
  had it are published without it.

- **Publishing from the chat box no longer sends everything at once.** "Process
  this and publish them all" and "publish all my clips" used to post every clip
  in one go, and WoopSocial allows about five YouTube posts a day, so most were
  rejected. Unless you say how fast, clips now go out five a day, an hour
  apart, after anything already scheduled, and the plan you are shown before
  saying yes lists those exact times. Hashtags you asked for in the chat now
  reach the posts too; the confirm button used to drop them.

- **WoopSocial posts now show what really happened to them.** Every post sent
  through WoopSocial stayed at "processing" for good, because Clips Kitty was
  reading the wrong field of WoopSocial's reply. Posts that went out now show as
  published with their link, and ones that did not show as failed with the
  reason in plain words, such as WoopSocial's five-a-day YouTube allowance or
  TikTok's posting limit. Posts you already sent correct themselves the next
  time Clips Kitty checks, within a few minutes of opening it.

- **Hashtags now actually appear under your videos.** Clips Kitty has always
  chosen hashtags for a clip, but they were being sent to YouTube's keyword
  field, which nobody sees, and the description went up without them. They are
  now written into the description, where they show above the title and are
  searchable. This reaches clips you made months ago, because it happens when
  the clip is published rather than when it was created.

- **Your channel name is always the first hashtag.** Descriptions were missing
  the one hashtag that matters most for finding your other videos. Each clip now
  carries at most five, and the creator's name leads them, so the cap can never
  cut it off.

- **An age-restricted video says so.** YouTube will not hand these over to
  anyone who is not signed in, and Clips Kitty downloads without an account. It
  used to fail with a wall of yt-dlp text about exporting cookies, which read
  like a crash. It now says what happened and what will work instead. The same
  plain wording now covers failures during the first check of a link, which is
  the likeliest moment to fail and the one place it was missing.

- **The activity list follows what is happening.** New lines were being added
  at the top, so the newest event was never where you were looking. It now
  reads downwards with the newest at the bottom and scrolls to keep up, unless
  you have scrolled up to read something, in which case it leaves you alone.

---

## 1.2.0: every clip now ends with a Clips Kitty end card

### Added

- **Every clip ends with a short Clips Kitty end card.** A 2.9-second card with
  the Clips Kitty mascot is added to the end of each clip you make, so people
  who watch your clips can find the app that made them. It is joined on without
  re-encoding, so the clip itself is untouched.

- **Korean is now fully translated**, thanks to
  [@doeil1614-ops](https://github.com/doeil1614-ops). Korean was listed as a
  finished language and was not: 68 of the app's phrases were falling back to
  English. All of them are translated now, and the existing wording was reviewed
  by a native speaker, so clips are called "하이라이트 영상" (highlight videos)
  where that is what Korean creators say. If something still reads oddly,
  [#45](https://github.com/ColinGPT9/clips-studio/issues/45) is the place to say
  so.

- **Publish to YouTube from the editor.** Finish a clip, press **YouTube**, fill
  in the title, description, tags, thumbnail, playlist, audience and visibility,
  and press Upload. Clips Kitty renders your unsaved edits and uploads straight
  to your channel. No exporting the file first, no hunting for it on disk, no
  separate publishing screen. You never leave the editor.

  **Scheduling uploads the video now** and asks YouTube to publish it later, so
  you can close Clips Kitty and switch your computer off. There is no timer in
  this app and nothing to leave running.

  It is **off until you turn it on** in Settings, and it uses your own free
  Google API key rather than a shared one, which is what stops every user in the
  world drawing from the same daily upload allowance. If you never enable it, the
  editor looks exactly as it did.

  One thing worth knowing before you rely on it: until your Google Cloud project
  passes YouTube's free audit, **YouTube locks every video uploaded through an
  API to private, permanently**, and it cannot be undone in Studio. That is
  YouTube's policy, not a bug, and no software can work around it. Clips Kitty
  checks after every upload and tells you if it happened instead of claiming
  success. Uploading as unlisted or private is unaffected. See
  [README "Posting publicly"](README.md#posting-publicly).

  This is new, and few people have tried it yet. If you set it up, tell us how it
  went through the Feedback Hub, whether it worked or not.

- **Star the clips you have exported, and export a whole video at once.** Each
  clip in the Clip Editor has a star next to its delete button. Exporting a clip
  stars it, and you can star or unstar any clip by hand. **Export all** saves
  every clip from a video or stream that is not starred yet, so nothing gets
  exported twice. Clips you exported before this update start out starred, from
  the app's export history.

- **"No clips" explains itself.** When a video produces nothing, the app now
  says so where the clips would have been, with the numbers behind it: how many
  moments were considered, the best score any of them reached, and the
  threshold they were measured against.

  When the reason is that nobody was on screen: gameplay, top-down games,
  anything without a person in frame. It says that too. Part of a clip's score
  is whether someone is visible, so that footage scores zero on it rather than
  merely low, lands under the threshold, and comes back empty. That is working
  as designed, and until now the app gave no hint of it: the screen just said
  "No clips for this video yet", which reads like a fault.

  It only names that cause when the detector actually looked and found nobody.
  A quiet talking-head video that simply did not score well is told apart from
  gameplay and gets the plain numbers instead: being confidently wrong about
  someone's footage would be worse than saying nothing.

- **Reasoning models find clips.** DeepSeek-R1, OpenAI's gpt-oss and NVIDIA's
  Nemotron 3 Nano could finish a video with no clips and no error: Ollama either
  spent their answer on thinking or blanked it while they reasoned. Each is now
  sent what it needs, and all three are listed on the Models page. The models
  setup installs are sent exactly what they were before.

- **Streamer tools can hand a finished stream to Clips Kitty.** A new supported
  API, `/integrations/streams`, takes a stream's platform, channel and start and
  end times.
  - **Finds the VOD:** looks up the one Twitch or YouTube publishes afterwards,
    and queues it once.
  - **Asks when it can't look:** Kick, or no channel name, gets a request for the
    link.
  - **Never starts other waiting videos** along with it.
  - **Reports progress and time left** in the same terms the app shows.

  The OBS plugin is the first thing built on it. `/health` now also reports
  `app_version` and `api_version`, so a tool can tell when Clips Kitty needs
  updating.

### Fixed

- **The local API only answers requests addressed to this computer.** A web page
  could previously reach it by pointing its own domain at 127.0.0.1 (DNS
  rebinding). Requests carrying any other host name are now refused.
- **Bug reports lost the one field that mattered.** The reporter is required to
  say which video they were processing, and the answer was then dropped before
  the report was built. It now appears, and the question is a list of your
  recent videos rather than a text box.
- Reports also carry the video and the run summary automatically, so they are
  useful even when the description is three words. Previously the video was
  guessed from "most recently updated", which found nothing at all if the
  reporter had deleted the video first, as they usually have.
- **Editing a clip you had translated no longer fails the render.** Translating a
  clip in the Subtitles tab and then pressing "Apply edits" failed the job with a
  database error, mentioning nothing you had actually done. Re-rendering replaces
  the clip's row, and the translation still pointed at the old one. Translations
  now follow the clip across a re-render, and so do publishing records.

- **Your YouTube sign-in is no longer stored in readable form.** The saved token
  used to sit in plain JSON in the data folder. It is now encrypted against your
  Windows account, so copying the folder to another PC or user does not carry it
  over. Existing sign-ins are moved across automatically the first time.

- **`python main.py auth` works in the installed app.** It looked for your
  credentials file relative to whatever folder it happened to be started from,
  which for an installed copy is not where the file is.

- **A blocked YouTube sign-in now says how to fix it.** If your Google Cloud
  project is still set to Testing, Google refuses the sign-in, and the app used
  to tell you that you had declined the permission request. It now says to open
  Google Auth Platform, then Audience, and press Publish app.

- **Updates no longer offer an older version.** An installed copy could offer to
  "update" itself to a version older than the one it was running whenever the
  update feed was behind it.

- **Downloading an update shows real progress.** After a small first step, the
  bar used to sit at 100% for the whole multi-gigabyte download, which looked
  frozen. From this version on it counts through the app files as they arrive.

- **AI models from older versions are found again.** Since 1.1.3 the app kept its
  models in a different folder from the rest of its data, so people coming from
  0.1.x downloaded their model a second time. It now keeps using whichever folder
  already has your models.

---

## 1.1.4: setup stops asking for a model you never chose

### Fixed

- **Setup no longer asks for a model you were never meant to install.** On a PC
  without a graphics card, setup downloads the smaller AI model that suits it,
  and then told you a *different* model was missing, with a red error, directly
  under a line confirming a model was installed. The download had worked. The
  check was asking the wrong question: it wanted one specific model rather than
  any model that runs.

  It now checks whether an AI model is available at all, and names the one it
  will actually use. Downloading a model from setup also selects it, unless you
  already have a working one chosen, so picking a bigger model to try later
  will not switch you over without asking.

  **This affected every PC whose recommended model was not the shipped
  default**, which is any machine without an 8 GB graphics card. If setup told
  you `gemma:7b` was not installed straight after a download finished, this was
  why, and there was no way past it.

- **A failed YouTube download now says what went wrong.** Pasting a YouTube
  link could fail with a wall of technical text ending in `HTTP Error 403:
  Forbidden`. That looks like a broken app and reads like a broken link, and it
  is neither. YouTube hands over the video's details and then refuses to send
  the actual data to your network, which is Google rate-limiting the connection
  itself. It usually clears on its own within an hour, and Twitch, Kick and
  local files keep working while it does. The app now explains that in plain
  English instead of printing the error.

  **What the app cannot do is prevent it.** Nothing in Clips Kitty can persuade
  Google to serve a connection it has decided to throttle. If it keeps
  happening, switching off a VPN or moving to a different network is what
  actually fixes it. Reported by a user through the in-app feedback hub
  ([#81](https://github.com/ColinGPT9/clips-studio/issues/81)).

- **RTX 50-series cards no longer crash every job.** On a GeForce RTX 50 card
  processing failed part-way through with `CUDA error: no kernel image is
  available for execution on the device`, every single time. Clips Kitty was
  built against a version of CUDA that predates those cards, so it could see
  the GPU, report it as working, and then have no code it could actually run on
  it. It now ships CUDA 13, which supports them properly. Reported by a user
  through the in-app feedback hub
  ([#83](https://github.com/ColinGPT9/clips-studio/issues/83)).

- **A GPU that cannot be used falls back to the CPU instead of failing.**
  Whichever version of CUDA the app ships, some graphics card sits outside it.
  Until now that meant a job died in the middle; it now finishes on the CPU and
  says which card it could not use and why. The startup check reports this too,
  rather than calling the GPU fine right up until the crash.

### Security

- **Voice files are now found by listing the folder rather than by building a
  path from the requested name.** No release was vulnerable. The name was
  already checked against a strict pattern that rejects anything resembling a
  path, but the check was a rule about the text, and this is a property of
  where the value comes from, which is the stronger of the two. A related
  pattern that only rejected a name ending in a newline was tightened at the
  same time.

### Changed

- **GTX 10-series and older cards now run on the CPU.** Supporting the RTX
  50-series meant moving to a newer CUDA, and that does not reach back to cards
  that old. **GTX 16-series and every RTX card are unaffected**. An RTX 2060 is
  the oldest card that still uses its GPU. On the machines this does affect,
  everything still works and produces identical clips, just more slowly, and
  video encoding uses the GPU exactly as before. No version of CUDA supports
  both those cards and current ones, so it was a choice between the two.

- **The YouTube downloader is six weeks newer** (yt-dlp 2026.8.19). YouTube
  changes how it serves video often enough that this is the one component worth
  keeping current, and the shipped copy had fallen behind. Older copies
  gradually lose access to formats as YouTube moves on.

---

## 1.1.3: the app is now called Clips Kitty

> **Same app, same data, nothing to do.** Your clips, settings and creator
> profiles stay exactly where they are and open as normal. Only the name
> changed.

**Why:** the old name was too close to existing software to be listed on the
Microsoft Store. The clip editor page had the same problem and is now called
**Clip Editor**.

Everything that is a link stayed a link: the GitHub repository, this website
and the download addresses are all unchanged, so nothing anyone has bookmarked
or shared has broken.

**The version jumped from 0.1.2 to 1.1.3**, which looks odd and is deliberate.
The Store will not accept a version starting with 0, so every release used to
carry two numbers (0.1.2 in the app and 1.1.2.0 on the Store) and somebody
had to remember the mapping. 1.1.3 is above the 1.1.2.0 already published, so
from here the app version and the Store version are the same number. This is
still alpha software; the leading 1 is a Store requirement, not a claim.

### Fixed

- **Processing no longer needs to reach GitHub.** Every video tried to download
  a 7 MB detection model, even though that file was already inside the
  installer. If the download failed, so did the job: "Download failure … Retry
  limit reached". It now uses the copy it shipped with.

  **This affected 0.1.2**, so if a video failed with a download error, this was
  why. It was unpredictable rather than universal: the app looked for the file
  in whatever folder Windows happened to start it from, so it worked or failed
  depending on where the shortcut pointed, and it always worked when run from a
  developer's own copy of the source. That is why it survived to a release.
- **A video whose details were lost keeps its name.** Reprocessing a video
  after the database had been reset or moved showed the raw ID instead of the
  title, and no channel at all. The empty channel was the worse half: creator
  profiles are matched on it, so catchphrase learning and preference history
  quietly did not run for that video. It now re-fetches the title and channel
  without re-downloading the video, and still works offline.
- **The Models page headings no longer run together.** "Recommended" was wider
  than its column and collided with the next heading, reading as
  "RECOMMENDEDWHY". The column is now labelled "Model", which is what it holds.

### Added

- **Russian is now fully translated**, thanks to [@4nmus](https://github.com/4nmus),
  the first contribution to Clips Studio from outside. Russian was listed as a
  finished language and was not: 92 of the app's 208 phrases were quietly
  falling back to English. All 92 are translated now, and 56 of the existing
  ones were rewritten by someone who actually speaks Russian rather than by a
  machine. If you use the app in Russian and something still reads oddly,
  [#60](https://github.com/ColinGPT9/clips-studio/issues/60) is the place to say so.
- **Brazilian Portuguese is now fully translated**, thanks to
  [@espinafr](https://github.com/espinafr), the second contribution from
  outside. Portuguese was listed as finished and was not: 133 of the app's 208
  phrases were falling back to English. All of them are translated now, and 30
  of the existing ones were rewritten by someone who speaks the language. The
  most visible change is that clips are "cortes" rather than "clipes", which is
  what Brazilian editors actually call them. If something still reads oddly,
  [#59](https://github.com/ColinGPT9/clips-studio/issues/59) is the place to
  say so: two phrases are already known to need a second opinion.
- **Clips Studio is coming to the Microsoft Store.** Same application, same
  local processing; the Store version is updated by the Store rather than by
  the in-app updater, and its donate button opens your browser. The standalone
  installer is unchanged and stays the main way to get it.

---

## 0.1.2 (2026-08-10)

### Fixed

- **Your clip settings are remembered again.** Turning captions off, closing
  the app and reopening it brought captions back on. Five settings behaved this
  way: captions, 60s+ clips, podcast mode, longform and its mode. They were
  read from storage on startup but never actually saved.
- **The Watermark tickbox works.** It silently refused to stay ticked when no
  branding profile existed yet. It now explains that a profile has to be
  created first, rather than looking broken.
- **A failed render says what went wrong.** A memory failure used to print
  pages of encoder output for every affected clip. It now says so in one
  sentence, once, however many clips were hit.

### Changed

- **The Models page makes sense.** It had one heading, "Your hardware", over
  rows like "Multilingual" and "Newer Gemma", which are not hardware, and a
  "Why" column carrying licences and warnings at the same time. There are now
  two tables: what your machine can run, and what to pick for a particular job.
  `gemma3:4b` also appeared twice; it is one row now.
- **More models to choose from**, all free to run locally and all usable on
  clips you earn from. `gemma4:e2b` and `gemma4:e4b` are built for ordinary
  local machines and are recommended alongside the Gemma 3 line. `e2b` suits a
  low-power or older PC, `e4b` anywhere `gemma3:4b` fits. Qwen3 for translation
  and multilingual work, and Mistral Nemo or Phi-4 for anyone who wants a
  plainly permissive licence.

---

## 0.1.1 (2026-08-09)

The release that made 0.1.0 usable. Both bugs were packaging mistakes, and both
were invisible on a development machine, which is exactly how they reached a
release.

### Fixed

- **No clip could be produced, from any source.** Every job died partway with
  `No module named matplotlib`. The bundle excluded a library that the tracking
  model needs in order to load at all.
- **YouTube downloads failed** with `ffmpeg is not installed`. FFmpeg ships
  inside the app, but the downloader looked for it on the system instead of
  being told where it lived, so it could not join YouTube's separate video and
  audio streams. Twitch and Kick were unaffected, because their recordings
  arrive as a single stream, which is what made it look like a YouTube
  problem rather than a packaging one.

---

## 0.1.0 (2026-08-08)

First public alpha.

- Paste a Twitch VOD, Kick VOD or YouTube link and get vertical clips with
  word-synced captions and written titles.
- Everything runs on your own computer. No uploads, no subscription, no cap on
  how many clips you make.
- One installer. It carries the app, the engine, FFmpeg, the AI runtime and the
  tracking and transcription models. The only thing fetched afterwards is the
  language model, sized to your graphics card on first launch.
- Editor for fixing anything the AI got wrong, multilingual captions, creator
  profiles that learn from your corrections, and a queue that runs unattended.
