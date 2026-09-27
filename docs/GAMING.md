# Gaming / Reaction

**For game streams and reaction videos: the streamer's webcam and the game (or
the video they're reacting to) laid out together in a 9:16 Short, in the layout
you choose.**

A game stream is one wide picture with the game in the middle, a webcam in a
corner, and chat, alerts and panels around the edges. Cropping it to 9:16 the
standard way follows the biggest face, and on a game stream that can be a game
character, a portrait, or the person in a video the streamer is reacting to.

It is a switch of its own, off unless you turn it on, and it can't be combined
with Vertical Live, Podcast or Longform. With it off, nothing about processing
changes. If anything in it fails, that clip is made the standard way.

**It is for streamers on a real camera.** The detection is built to find
people. VTubers aren't supported: an avatar is never taken for the streamer,
so a VTuber stream gets the game on its own.

## Layouts

Eleven layouts, chosen from cards that show the video's own frame in each one.
All of them are data (`gaming/layouts.json`), not code.

| Layout | What it is | Boxes it uses |
|---|---|---|
| **Split** | Webcam band on top, the game fills the rest. The divider sets the webcam's share, 25–50% (38% to start). | Webcam, Game |
| **Basecam** | Split with the game on top and the webcam at the bottom. | Webcam, Game |
| **Half** | Webcam and game 50/50. | Webcam, Game |
| **Fullscreen** | The game cropped to fill the Short, no webcam. | Game |
| **Blurred** | The whole game in the middle, on a blurred copy of itself. | Game |
| **Small facecam** | The game fills the Short, a small webcam near the top; move and resize it on the preview. | Webcam, Game |
| **Circle facecam** | The same with a round webcam. | Webcam, Game |
| **Game UI** | Webcam on top, the game below, and a piece of the game's UI (a scoreboard, a map, a timer) as a layer over the game: against the webcam to start, moved and resized on the preview. | Webcam, Game UI, Game |
| **Mosaic** | Webcam and a game UI panel side by side on top, the game below. | Webcam, Game UI, Game |
| **Dual facecam** | The game fills the Short, two round webcams near the top (duo streams), always the same size. | Webcam, Webcam 2, Game |
| **Duo split** | Two webcams side by side on top, the game below. | Webcam, Webcam 2, Game |

**On top** switches the webcam and the game in Split, Half, Game UI, Mosaic and
Duo split; Basecam is the same switch set the other way.

The game is either **zoomed** to fill its part of the Short or shown
**whole**. Shown whole, it sits right against the webcam: never a band of blur
between the streamer and the game. The webcam and the game are stacked
together, and the space left over is a blurred copy of the game, above the two
(just clear of the platform's top bar, so the streamer's head isn't under it)
and below them, where the platform's captions and buttons go anyway. Only
Blurred, the game alone, sits in the middle of the Short. There are no black
bars.

With no webcam found, a layout that needs one falls back to Blurred. A layout
that needs a Game UI or second webcam box that was never drawn falls back to
Split or Small facecam.

## Faces clear of the platform's buttons

TikTok, Reels and Shorts draw their own buttons, captions and top bar over the
video. A webcam crop that is only centred can put the streamer's head under
the top bar: measured on one Marvel Rivals stream, the head sat 2–26 px from
the top of every clip, because that webcam has barely any room above the head.

So the webcam is placed from the streamer's head, not the middle of the box:

- the head is found in the webcam box of each clip (the pose model, a few
  frames, inside the box only; it places the crop, it never decides who);
- the head's top lands at least 6% of the region below its top **and** below the
  chosen platform's top bar, and the chin above the caption area where the
  region allows it;
- the chin comes first: when a tight webcam's face is too tall for its band,
  the hair goes under the top bar (and the preview says so) rather than the
  chin being cut off by the game. A bigger webcam share, or the whole game,
  gives it room;
- when the webcam itself has too little room above the head, the picture
  moves down inside its region (by just what's missing, at most 30% of the
  region), with a blurred copy above it rather than a cut-off head;
- small and round webcams go inside the platform's safe area.

The same stream re-rendered: the head top at 135–158 px with the webcam on top
(TikTok's top bar ends at 140), about 200 px in Small and Circle facecam.

Safe areas, on a 1080×1920 Short. None of the apps publishes these for
ordinary posts, and the guides that do disagree by tens of pixels, so they
were measured (September 2026) from replicas of each app's feed on a phone
taller than 9:16, where the apps fill the height (kreatli.com's safe zone
checkers): each value is just past the app's own element — TikTok's tabs,
the Reels and Shorts title bars, the column of buttons, and a caption block
of two lines.

| Platform | Top | Bottom | Left | Right |
|---|---|---|---|---|
| TikTok | 180 | 420 | 60 | 190 |
| Instagram Reels | 245 | 400 | 60 | 205 |
| YouTube Shorts | 250 | 350 | 60 | 190 |
| All three | 250 | 420 | 60 | 205 |

The platform is chosen in the preview (TikTok to start), which draws that
app's layout over the Short from the same measurements: the top bar, the
buttons down the right with their counts, the name, caption and sound at the
bottom (Shorts with its Subscribe button). The icons are open-licensed sets in
each app's style (Material Icons, which YouTube itself uses, and Lucide), not
the apps' own artwork or logos; "All three" shades the areas they share. It
says whether the face is clear of them: "✓ Face clear of TikTok's UI", or what
to change. Camera on top is usually the fix: with the game on top, the webcam
band sits in the caption area.

## Choose the layout before processing

Every game and every stream overlay is different, so the layout can be set up
on the video's own frames before any processing starts. Tick **Gaming /
Reaction** on a video in the Generate bar (a YouTube, Twitch or Kick link, or a
file) and **Choose a layout** opens:

- **Layouts**: the eleven cards, each a live miniature of this frame;
- **the frame**, with a box for each thing the layout uses (Webcam, Game, Game
  UI, Webcam 2), already on it when it opens, as in StreamLadder: drag a box
  to move it, any of its eight handles to resize it. Boxes snap to the frame's
  middle and edges, to each other and to the stream's solid panels (a game box
  lands exactly on the chat bar's edge), with a guide line; **Grid** adds
  thirds; Alt places a box freely. The Game box starts on the area the
  layout takes the game from; move it and it's yours (**Let Clips Kitty pick
  the game area** gives it back). The dashed line inside is exactly what the
  layout will show. **Snap to the webcam's border** pulls the webcam box out to
  the overlay's own edge. The stream's solid panels are hatched **Left out**;
- **Moment**: a slider over the whole video and five frames spread across it
  (nothing is downloaded for a link; each frame is read straight from the
  stream);
- **Preview**: the 9:16 result, live, with the platform overlay and the face
  check. Drag the line between the webcam and the game to change their shares.
  In the facecam layouts drag the facecam on the preview to move it, and any
  of its eight handles to resize it (it keeps its shape, from the opposite
  side; two facecams resize together); the same for the Game UI layer. While
  dragging it snaps, like a design editor, to the middle of the Short, its
  edges, the platform's safe lines and the other parts' edges and middles,
  and a pink line shows what it lined up with (hold Alt to place it freely).
  **Grid** shows thirds, the middle and the platform's safe box, and adds the
  thirds to what it snaps to. **Reset** puts the layers back. The Game UI in
  Mosaic and Game UI is cut to its space's shape, like every other part: no
  blur round it;
- **On top**: Camera or Game. **Webcam**: *Draw it* (the default: the box on
  the frame), *Find it* (by who is talking, when processing) or *None*.
  **Game**: *Whole* or *Zoom to fill* (and then Left, Centre or Right).

When it opens, the webcam box is moved onto the webcam when Clips Kitty finds
one: a person in the same spot on at least four of the five frames, no more
than a third of the picture, with a real border round at least two of its
inner sides. A game character moves between frames minutes apart, chat has no
person in it, and an avatar has no webcam border, so none of them are
suggested. It's only a starting point, for you to check. With no suggestion
the box waits in the corner for you to drag onto the webcam.

The editor has window buttons top right: **fullscreen** (the whole monitor,
like a video player; Esc leaves it, and it opens that way next time if you
left it so), and **minimise** to a bar in the corner, to get at the rest of
the app and come back with **Restore**. Esc closes the editor, never the clip
editor behind it.

**Use this layout** sends it with the video. **Remember for this creator's
next videos** keeps it for them, so their next videos (and a watched
channel's) start from it. **Change layout…** in the Generate bar opens it
again.

The same editor is in the **clip editor** (Effects → Layout → **Gaming /
Reaction** → *Change layout…*) to change one clip: shown in *Update preview*,
saved on *Apply*.

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

- **Whole game**: the biggest picture beside the webcam and the solid panels
  (below) that leaves them out, so the streamer isn't shown twice. With no
  webcam, the whole stream.
- **Zoom to fill**: a crop at the region's shape, as tall as it can be while
  it stays clear of the webcam and the solid panels, on the middle of the game
  picture.
- **A drawn game area** replaces both.

**Solid panels** are the parts of the stream layout on their own background: a
black chat bar under the game, a speedrun's splits timer. Cropped into the
Short they're a useless bar, so they're found and kept out:

- four or more stacked lines of text in the same place on most of five frames
  spread through the video (the game's own text comes and goes; a panel stays);
- on the same background colour on every frame. Behind see-through chat the
  game changes, so chat drawn over the gameplay stays in the picture: it's
  part of the stream;
- grown out over that colour to the panel's own box, one side at a time.

It is OpenCV only, a fraction of a second, and no motion is involved. On the
13 test streams it found the speedrun's black chat bar and splits timer and
nothing else: not a night-time game (its dark sky shifts between frames), not
a HUD, not see-through chat.

## Scoring: how game moments are found

Standard scoring judges talk: hooks, opinions, drama, quotable lines. On a game
stream the moment is usually something that happened in the game (a kill
streak, a boss going down, a goal) and the reaction to it, often with little
said. Gaming / Reaction scores that way, and so does **Gaming stream**, a
checkbox beside **Vertical Live** for a live that was already vertical (in the
Generate bar, a queued video's settings and a watched channel's). With neither
on, scoring is exactly the standard one.

What goes into it (research: stream-highlight papers on chat, audio and
facecam signals; what gaming clip tools look for; what performs as a Short):

- **The game.** Twitch says which game is played over which part of a stream
  (a stream that goes from Just Chatting to Rust to Fortnite), Kick gives its
  category, and a YouTube video's title and tags often name it.
  `config/gaming.yaml` turns about 150 game names into a kind of game (shooter,
  battle royale, MOBA, sports, fighting, racing, horror, soulslike,
  action-adventure, sandbox, speedrun, party, strategy, reaction) with what a
  highlight is in it and what a streamer says when it happens.
- **The AI is told.** Every scoring prompt says it is a gaming stream, which
  game (for the part of the stream it is reading), what a highlight is in it,
  that a clear in-game moment is a strong clip even when little is said, and
  that menus, queues, loading screens and reading out donations score low.
- **Chat's reactions** (Twitch VODs and YouTube live replays; Kick keeps no
  chat). A burst far above the stream's own message rate marks a moment,
  dated about 6 seconds earlier for chat's delay, and what chat says names it:
  hype (POG, NO WAY, すご), laughing (KEKW, LUL, ｗｗｗ, ㅋㅋㅋ, хаха, jajaja),
  surprised (！？, あ, WHAT), scared (monkaS), a fail (F, NotLikeThis, BigSad)
  or "clip it", including a channel's own emotes by their ending (kittyPog).
  Bursts of hellos don't count, and a burst of long messages is chat
  discussing something, not reacting.
- **The streamer's voice.** A sudden jump in loudness while they are talking
  (a shout, a laugh, a scream): the cheap stand-in for seeing their face react.
- **The game's own sound.** A small sound model (PANNs, trained on AudioSet's
  527 everyday sounds; 24 MB, bundled) listens to every second for gunfire,
  explosions, a crash or a shield shattering, a crowd cheering, a referee's
  whistle, screaming and laughter. What each means depends on the game
  (`config/gaming.yaml`'s `genre_sounds`): gunfire is the fight in a shooter
  and nothing in a football game; a crowd roars at a goal, and Rocket League's
  goals explode. A stream mostly sounds like the streamer's voice and music,
  so a sound is judged against its own level in that stream: a Rocket League
  goal's explosion scores about 0.25 out of 1 and still stands out. It runs
  beside transcription, about 600x realtime on a GPU and 120x on a CPU (a
  3-hour VOD in about a minute and a half).
- **What the game writes on screen.** Games announce their moments in big
  letters (ELIMINATED, VICTORY ROYALE, PENTA KILL, "X A MARQUÉ", YOU DIED,
  ENEMY FELLED) and fill the screen with text when nothing is happening (a
  settings page, a queue). OCR (RapidOCR, bundled) reads the middle of the
  frame in the game-moment windows, strongest first: every 2 seconds near the
  moment, since a banner is only up for two or three, then every 4, for at
  most 2 minutes a video (about 0.4-0.8 s a frame on a CPU). A banner with an
  event word for that kind of game becomes an event the AI reads ("ON SCREEN:
  ACE") and a witness for the bonus; a window where most frames show menu
  words is marked down (-12) and gets no bonus, whatever chat made of it.
  `config/gaming.yaml`'s `screen_text` lists the words in the languages games
  are commonly played in. It reads Latin script and kanji, not kana.
- **A game channel** in the fused score carries all of it (40% of the weight;
  the AI's reading of the words 25%, audio 20%, visuals 10%, a person on
  screen 5% in Vertical Live and 0% in the split). When little is said, the
  weight the words would have had moves to the game and the audio, so a quiet
  streamer's big play isn't marked down for its silence.
- **Each moment is a candidate of its own**, from about 4 seconds before it to
  the reaction, 15 to 35 seconds, even when nothing was said near it. When two
  independent witnesses agree (chat, the game's sound, a banner on screen, the
  streamer shouting or laughing) it gets a bonus; so does chat with a loud
  moment. The game's sound with loudness alone doesn't, because gunfire is
  loud.

The clip's score breakdown shows **game** and what marked the moment.

**What it found on real VODs** (September 2026, public Twitch VODs):

- A 94-minute Japanese Apex Legends VOD. Chat alone marked 4 moments: a death
  the streamer took (chat: the channel's RIP emote, BigSad, NotLikeThis), and
  also a black screen and a character-select screen. With the game's sound,
  23 moments (the budget for that length); the two where chat and gunfire
  agreed came first, and 8 of the top 9 were fights on screen. Reading the
  screen marked the settings page chat had reacted to as a menu (感度, 設定),
  and none of the 22 others; it named no kills, since the game was in
  Japanese and its kill text is kana.
- 30 minutes of Rocket League, played in French: 27 moments, nearly all goals
  (the explosion, with its replay a few seconds later counted as the same
  moment). Reading the screen named 5 of them from the banner ("A MARQUÉ"),
  and marked the one crowd cheer that landed on the matchmaking menu after a
  match (COMPÉTITIF, INDISPONIBLE, MODE DE JEU).
- 30 minutes of a stream filed under a football game that was really two
  streamers watching someone else's IRL stream: one weak moment, nothing
  strong enough to become a candidate on its own.

**What this can't do yet.** Reading the screen only knows the words it has
been given, in the scripts the OCR reads, and a menu without them (or a
black screen, or a character select) goes unnoticed. The AI reads the
transcript and these events, not the picture. The AI looking at the frames
of the best candidates is what comes next.

In gaming mode the "reaction" signal (is a person on screen, being
emphasised?) is left neutral in the split: on a game stream it counts game
characters as people, and a top-down game as nobody at all.

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
- **The Game UI and second webcam boxes are drawn by hand.** Nothing looks
  for a scoreboard or a second streamer.
- **YouTube frames for the editor are read over IPv4.** On some networks
  FFmpeg's IPv6 connection to YouTube hangs for minutes; the editor's frames go
  through a small local relay that forces IPv4.
- **Separate recordings** (the game and the webcam as two files, as some
  recorders make) aren't supported yet.
