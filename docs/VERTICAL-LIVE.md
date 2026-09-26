# Vertical Live

**For livestreams that were already composed as 9:16 during the broadcast.**

More streams now go out vertically: YouTube vertical lives, the vertical feed of
a Twitch Dual Format stream, a Streamlabs Dual Output stream, or an Instagram or
TikTok live. The streamer already placed the gameplay, the webcam and the chat
on a 9:16 canvas. Their recording is a finished vertical video, and there is
nothing to reframe.

Turn on **Vertical Live** for one of those, and Clips Kitty keeps the stream's
own layout. It finds the moments exactly as it always does, and cuts each clip
from the whole frame at 1080×1920.

| | Standard processing | Vertical Live |
|---|---|---|
| Finding the moments | Transcript, AI clip picking, audio, motion, on-screen reactions, chat | The same |
| Framing | Follows faces, picks the speaker, may crop, split or letterbox the frame to make it 9:16 | None: the live's own 9:16 layout is kept as it is |
| Output | 1080×1920 | 1080×1920 (a smaller 9:16 source is scaled up to fit, never cropped) |
| Captions, branding, end card, publishing, scheduling | Yes | Yes, the same |

## Turning it on

It is its own switch, never turned on for you:

- **A video or file**: tick **Vertical Live** in the Generate bar, or in a queued
  video's settings. When you add a file that is 9:16, the app asks "Is it a
  vertical live?", but only the switch decides.
- **A watched channel**: tick **Vertical Live** in the channel's clip settings.
  Every video it finds is then processed that way. A video that only comes in
  landscape is skipped with a reason, so for a Streamlabs Dual Output channel
  the horizontal copy of each live isn't clipped twice. **Clip this** on a
  skipped one clips it the standard way.
- **The assistant and MCP** take `vertical_live` too.

It can't be combined with Podcast (which reframes a multi-camera set) or
Longform (which makes 16:9 video).

If the video isn't actually 9:16, it is refused before any work, with
"This video is not a vertical 9:16 source. Vertical Live mode expects a
vertically composed video." Press **Use standard processing** to clip it the
normal way.

## Where the vertical video comes from

- **A YouTube vertical live.** Its replay is a vertical video; paste its link.
  Clips Kitty downloads the 9:16 version at 1080×1920. (Standard processing
  downloads vertical videos at a lower size; Vertical Live asks for 1080 on the
  short side.)
- **Streamlabs Dual Output to YouTube.** It makes two broadcasts, and the
  vertical one's title ends in "-vert" (Streamlabs). Use that one's link, or
  watch the channel with Vertical Live on.
- **YouTube's own dual stream** (horizontal and vertical from the Live Control
  Room) is one video. YouTube doesn't say whether its replay keeps the vertical
  version. If it doesn't, Clips Kitty says "YouTube has no vertical version of
  this video" rather than clipping the horizontal one.
- **Twitch Dual Format.** Twitch says Dual Format gives "both a horizontal and a
  vertical version of your Clips and VODs", and the vertical VOD expires 7 days
  after the stream (Streamlabs' guide). Clips Kitty asks for the vertical version
  of the VOD. When it tested this in September 2026, Twitch didn't hand the
  vertical version to the downloader at all, so for now **download the vertical
  recording and add it as a file**. If a VOD has no vertical version, Clips Kitty
  says so rather than using the horizontal one.
- **A downloaded live MP4** from Instagram, TikTok, YouTube, Twitch or anywhere
  else: add it as a file with Vertical Live on. The platform doesn't matter once
  you have the file. You can add its **original link** (optional; never
  required), which is kept with the video and its clips.

## What it skips, and what it keeps

The rule is simple: anything that decides **where to point the frame** is
skipped; anything that decides **which moments matter** runs as usual.

Skipped (render stage, after the clips are picked):
- face tracking and following, and TalkNet speaker detection;
- the fallback face finder, podcast shot framing;
- the layout decisions (letterbox, facecam split, top-of-frame shifts);
- the two-pass tracked render. Each clip is one FFmpeg encode of the whole
  frame, and a 1080×1920 source isn't rescaled at all.

Kept:
- transcription (local Whisper, or online with your key);
- AI clip picking, titles and creator learning, on whichever AI you chose
  (Ollama, OpenRouter with its text and voice models, or a direct provider);
- the audio, motion and scene-cut signals;
- the on-screen reaction check (YOLO), which scores whether someone on screen
  is reacting or moving. That is how a streamer's big reaction gets found, so it
  stays;
- captions, watermark, end card;
- publishing and scheduling through WoopSocial or your other connected
  services.

## In Clips Kitty Web

The browser tool has the same switch and the same rules. It never crops or
reframes anyway (it cuts clips straight out of the recording, with no
re-encode), so there Vertical Live means cutting a Twitch or Kick VOD from its
vertical version, and refusing a file that isn't 9:16. A 1080×1920 recording
gives 1080×1920 clips. A smaller 9:16 recording keeps its own size in the
browser; the desktop app scales it to 1080×1920.

## Why this matters now

- YouTube has a vertical live feed in its mobile app
  ([YouTube Help](https://support.google.com/youtube/answer/13822251)), and
  can stream horizontal and vertical versions of one live at once, the vertical
  one shown in the Shorts feed
  ([YouTube Help](https://support.google.com/youtube/answer/2474026)).
- Twitch launched Dual Format streaming on 17 June 2026, with vertical versions
  of Clips and VODs, and says "70% of new viewers are coming to Twitch on
  mobile" ([Twitch blog](https://blog.twitch.tv/en/2026/06/17/introducing-dual-format-and-2k-streaming-on-twitch/)).
- Streamlabs Desktop's Dual Output streams a separate 1080×1920 vertical canvas
  alongside the horizontal one
  ([Streamlabs](https://streamlabs.com/content-hub/post/twitch-dual-format-streamlabs-desktop)).

## For contributors

`core/modes.py` is the one definition (what counts as 9:16, the refusal, the
output size). `web/lib/vertical.ts` mirrors it, and
`tests/test_web_vertical_sync.py` fails if they drift. Downloads are in
`sources/vertical.py`; the render branch is in `core/pipeline._render_files`.
