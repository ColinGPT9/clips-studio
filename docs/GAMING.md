# Gaming / Split-Screen

**For game streams: the streamer's webcam in one half, the game in the other.**

> **Status:** the processing below is built and tested. The switch in the app
> (Generate bar, queue, watched channels) and the editor's webcam tools come
> next; until then it is reachable through the local API and MCP as
> `gaming: true`.

A game stream is one wide picture with the game in the middle, a webcam in a
corner, and chat, alerts and panels around the edges. Cropping it to 9:16 the
standard way follows the biggest face, and on a game stream that can be a game
character, a portrait, or the person in a video the streamer is reacting to.

Turn on **Gaming / Split-Screen** for a game stream and every clip is laid out
for it:

| | What you get |
|---|---|
| A webcam is found | **Split**: the webcam fills the top half (1080×960), the game the bottom half (1080×960). The camera half can go below instead. |
| No webcam | **The game fills the screen**: a 9:16 crop from the middle of the game. |
| The streamer's camera fills the frame (a just-chatting stretch) | That clip is framed the standard way, following the streamer. |

Finding the moments works as usual, with one change: the "reaction" signal
(is a person on screen, being emphasised?) is left neutral. On a game stream it
counts game characters as people, and a top-down game as nobody at all. The
streamer's reactions are in their voice, which the audio and transcript
signals already score.

It is a switch of its own, off unless you turn it on, and it can't be combined
with Vertical Live, Podcast or Longform. With it off, nothing about processing
changes. If anything in it fails, that clip is made the standard way.

## Who the streamer is

**TalkNet decides**: the streamer is the face that speaks in sync with the
stream's audio. Size never decides. This is the same fix that ended the
"largest face" problem in standard processing: a character can be as big as it
likes, but it doesn't move its mouth with the stream's audio.

Measured on real streams, "who is speaking in this clip" isn't always "who the
streamer is", so four things sit around TalkNet:

- **On screen for most of the clip.** TalkNet scores whatever face it is given.
  A driver glimpsed through a car window in GTA for 3% of a clip got a full
  speaking score. A webcam is on screen the whole time.
- **The most confident speaker.** When two faces both speak (a watch party, the
  streamer and the person in the video), the one TalkNet is surest of wins:
  3.3 against 0.3 on the stream we measured.
- **A real person.** Game characters that lip-flap to voice acting (a 3D visual
  novel) and VTuber avatars score as speaking, but TalkNet is never confident
  about them. A webcam's confidence reached at least 0.3 in some clip of every
  stream measured. Characters and avatars stayed at -0.2 or below.
- **The whole video, not one clip.** In a reaction the person in the watched
  video can out-talk the streamer for a whole clip. So the webcam is decided
  once per video, from four of its clips spread through it: the face that
  speaks from the same spot in the most of them.

Then the webcam box is the streamer's head and shoulders over those clips. Each
side snaps onto the webcam overlay's own border where there is a clear one, so
the half shows the webcam and not a strip of chat beside it.

## Where the game comes from

The game half is **never detected**. Earlier attempts looked for the part of
the screen with the most going on, and scrolling chat won every time. So the
game half is a fixed crop from the middle of the stream, full height, where
chat panels and alerts at the edges fall outside it. The only thing that moves
it is the webcam: it slides sideways until the webcam isn't in it, so the
streamer isn't shown twice.

## When it gets it wrong

(Coming in the clip editor.)

- **Set webcam box**: draw the webcam on a frame of the clip, or say there
  isn't one. Your box always wins over detection.
- **Camera on top or bottom**, and **game Left / Centre / Right**.
- A box you set is remembered for that creator and used for their next videos,
  so it's set once per streamer, not once per video.

## Tested on

September 2026, public Twitch VODs, three 40-second windows from each of 13
streams. Webcam positions were marked by hand from a frame of each and compared
with what was found.

| Game | Stream | Result |
|---|---|---|
| Zelda: Breath of the Wild | speedrun, webcam bottom-left, splits timer and chat around it | ✓ split: webcam found within 1% of the hand-marked box |
| Zelda: Tears of the Kingdom | VTuber | game fills the screen (an avatar isn't found automatically: set the box once) |
| "Zelda" category (really a gacha RPG) | no webcam, a large anime character on screen | ✓ game fills the screen; the character was not taken for the streamer |
| World of Warcraft | webcam bottom-left, bags and action bars | ✓ split |
| World of Warcraft | just chatting, camera fills the frame | ✓ framed the standard way |
| Grand Theft Auto V | roleplay, no webcam | ✓ game fills the screen; a driver seen for a moment was not taken for the streamer |
| Grand Theft Auto V | reacting to a bodycam video, small webcam | ✗ webcam not found: the streamer mostly listened, and TalkNet was never confident about their face. The game (here, the video) fills the screen. Set the box once for this creator. |
| League of Legends | lobby and loading screens, webcam bottom-right | ✓ split: within 1% of the hand-marked box |
| League of Legends category | a watch party: two webcams and a documentary | ✓ split with the streamer's webcam; when her camera went full screen, framed the standard way |
| Rust | webcam top-left, chat under it | ✓ split, chat left out of the game half |
| Dota 2 | two casters' webcams and a player cam | ✓ split with a caster's webcam |
| Persona 3 Reload | VTuber, voiced characters | ✓ game fills the screen: no character was taken for a webcam |
| Pixel-art game | VTuber | game fills the screen (see above) |

Time on an RTX 3060: finding the webcam looks at four 40-second pieces of the
video, about 15 seconds each (6 for person tracking, 8 for TalkNet). Each clip
then checks that the webcam is there, a few seconds, instead of the
standard face tracking.

`scripts/gaming_detect_bench.py` repeats the measurement on any footage.

## Known limits

- **VTubers**: an avatar is not found automatically. Draw the box once.
- **Chat under the game** (a strip along the bottom rather than a panel at the
  side) can show at the bottom of the game half, because the game crop is full
  height. Drawing the game area by hand is planned.
- **One layout per clip.** A clip that moves between the game and a
  full-screen camera keeps one layout.
- **Separate recordings** (the game and the webcam as two files, as some
  recorders make) aren't supported yet. The detection is shaped so that can be
  added.
