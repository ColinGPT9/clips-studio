# Changelog

What changed, written for people who use Clips Studio rather than people who
read the commits. Dates are release dates.

This project is in **alpha**: versions move fast, and things listed as fixed
were often broken in a way that only showed up on somebody else's machine.

---

## Unreleased

### Added

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
  - **Set everything up once.** A channel you add opens its whole setup: clip
    settings, where the clips go, posts per day, hours apart and the time the first
    post of each day goes out, with a preview of the next posting times. Add your
    own hashtags, like `#creatorname #twitch`; they lead every caption so they are
    always kept, and you can leave out the AI's hashtags entirely. Each platform
    has its own settings: YouTube visibility, who can watch on TikTok and whether
    comments, duets and stitches are allowed, Reel or Story on Instagram and
    Facebook, and your Pinterest board.

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

### Fixed

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
