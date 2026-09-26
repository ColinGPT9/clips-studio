# Gaming / Reaction

**For game streams and reaction videos: the streamer's webcam in one half, the
game (or the video they're reacting to) in the other.**

A game stream is one wide picture with the game in the middle, a webcam in a
corner, and chat, alerts and panels around the edges. Cropping it to 9:16 the
standard way follows the biggest face, and on a game stream that can be a game
character, a portrait, or the person in a video the streamer is reacting to.

Turn on **Gaming / Reaction** and every clip is laid out for it:

| | What you get |
|---|---|
| A webcam | **Split**: the webcam in the top half (1080×960), the game in the bottom half. The camera can go below instead. |
| No webcam | **The game alone**: the whole stream in the middle of the screen, on a blurred copy of itself. |
| The streamer's camera fills the frame (a just-chatting stretch) | That clip is framed the standard way, following the streamer. |

The game is shown **whole** by default, letterboxed on a blurred copy of itself
rather than black bars. **Zoom to fill** crops it to fill its half instead (and
then Left, Centre or Right chooses which part).

It is a switch of its own, off unless you turn it on, and it can't be combined
with Vertical Live, Podcast or Longform. With it off, nothing about processing
changes. If anything in it fails, that clip is made the standard way.

**It is for streamers on a real camera.** The detection is built to find
people. VTubers aren't supported: an avatar is never taken for the streamer,
so a VTuber stream gets the game on its own.

## Set it up before processing

Every game and every stream overlay is different, so the split can be checked
on the video's own frames before any processing starts. Tick **Gaming /
Reaction** on a video in the Generate bar (a YouTube, Twitch or Kick link, or a
file) and **Set up the split** opens:

- five frames from across the video to pick from (nothing is downloaded for a
  link; each frame is read straight from the stream);
- **Webcam**: *Find it* (automatically, when processing), *Draw it*, or *No
  webcam*; the camera on top or at the bottom;
- **Game**: *Whole game* or *Zoom to fill*, and optionally **Draw the game
  area** around just the game or the video being reacted to, which leaves out
  chat, alerts and panels;
- a live 9:16 preview of the result.

**Use this split** sends it with the video. **Remember for this creator's next
videos** keeps it for them, so their next videos (and a watched channel's) start
from it. **Set up split…** in the Generate bar opens it again.

The same controls are in the **clip editor** (Effects → Layout → **Split** →
*Adjust webcam and game…*) to fix one clip: shown in *Update preview*, saved
on *Apply*.

## Who the streamer is, when it's found automatically

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

The webcam box starts as the streamer's own box, with no margin (a margin ran
past a speedrunner's webcam into the chat beside it). Each side then grows out
to the webcam overlay's own border where there is a clear one, and stops just
inside it, so the half shows the webcam and nothing beside it. On six streams
with a webcam, every side of every box landed inside the hand-marked webcam.

## Where the game comes from

The game area is **never detected**. Earlier attempts looked for the part of
the screen with the most going on, and scrolling chat won every time. So:

- **Whole game**: the biggest picture beside the webcam that leaves it out
  (left, right, above or below it), so the streamer isn't shown twice. With no
  webcam, the whole stream. A chat panel at the side of the stream is part of
  that picture; *Zoom to fill* or a drawn game area leaves it out.
- **Zoom to fill**: a fixed crop from the middle, full height, where chat
  panels and alerts at the edges fall outside it, slid clear of the webcam.
- **A drawn game area** replaces both.

## Scoring

Finding the moments works as usual, with one change: the "reaction" signal
(is a person on screen, being emphasised?) is left neutral. On a game stream it
counts game characters as people, and a top-down game as nobody at all. The
streamer's reactions are in their voice, which the audio and transcript
signals already score.

## Tested on

September 2026, public Twitch VODs, three 40-second windows from each of 13
streams. Webcam positions were marked by hand from a frame of each and compared
with what was found.

| Game | Stream | Result |
|---|---|---|
| Zelda: Breath of the Wild | speedrun, webcam bottom-left, splits timer and chat around it | ✓ split: webcam found, inside the hand-marked box on every side |
| Zelda: Tears of the Kingdom | VTuber | not supported: the avatar wasn't taken for the streamer, and the game shows alone |
| "Zelda" category (really a gacha RPG) | no webcam, a large anime character on screen | ✓ the game alone; the character was not taken for the streamer |
| World of Warcraft | webcam bottom-left, bags and action bars | ✓ split |
| World of Warcraft | just chatting, camera fills the frame | ✓ framed the standard way |
| Grand Theft Auto V | roleplay, no webcam | ✓ the game alone; a driver seen for a moment was not taken for the streamer |
| Grand Theft Auto V | reacting to a bodycam video, small webcam | ✗ webcam not found: the streamer mostly listened, and TalkNet was never confident about their face. Draw it in the setup. |
| League of Legends | lobby and loading screens, webcam bottom-right | ✓ split, inside the hand-marked box on every side |
| League of Legends category | a watch party: two webcams and a documentary | ✓ split with the streamer's webcam; when her camera went full screen, framed the standard way |
| Rust | webcam top-left, chat under it | ✓ split, chat left out of the webcam half |
| Dota 2 | two casters' webcams and a player cam | ✓ split with a caster's webcam |
| Persona 3 Reload | VTuber, voiced characters | ✓ the game alone: no character was taken for a webcam |
| Pixel-art game | VTuber | not supported: the game shows alone |

Time on an RTX 3060: finding the webcam looks at four 40-second pieces of the
video, about 15 seconds each (6 for person tracking, 8 for TalkNet). Each clip
then checks that the webcam is there, a few seconds, instead of the standard
face tracking. A split set up before processing skips the search.

`scripts/gaming_detect_bench.py` repeats the measurement on any footage.

## Known limits

- **VTubers aren't supported.** The detection is for people on camera.
- **One layout per clip.** A clip that moves between the game and a
  full-screen camera keeps one layout.
- **Separate recordings** (the game and the webcam as two files, as some
  recorders make) aren't supported yet.
