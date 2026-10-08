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
- when a webcam at the top of the Short has too little room above the head,
  the picture moves down inside its region (by just what's missing, at most
  30% of the region), with a blurred copy above it rather than a cut-off
  head. A webcam lower down (under the game in the split) doesn't move: there
  the top bar isn't over it, and the move only put a band of blur between the
  game and the streamer's head and pushed the chin toward the captions;
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
  lands exactly on the chat bar's edge), with a guide line. While a box is
  dragged the grid shows (thirds and the middle) and it snaps to that too;
  **Grid** keeps it on. Alt places a box freely. **The Game box stops at the
  webcam**: while it's dragged, the webcam's edges show as dashed lines across
  the frame, 1% of the frame past the webcam box so its border is left out
  too, and the game box snaps to them and stops there instead of going over
  the webcam, so the streamer isn't shown in the game as well. With Alt it
  goes over, and a note says the webcam will show in the game. **Zoomed to
  fill, the Game box has the shape of the game's space on the Short**: change
  the webcam's share on the preview, the layout or which goes on top, and the
  box takes the new shape (its size and middle kept, clear of the webcam), so
  what's inside it is exactly what renders. Before, the render cut a slice of
  a different shape out of the middle of the box. The box as drawn is kept, so
  going back and forth doesn't wear it down; with **Whole** the game is shown
  whole, so the box stays as drawn. The Game box starts on the area the
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
saved on *Apply*. Processing the video again with Gaming / Reaction on keeps a
clip you edited in its own layout. When that clip's split was found
automatically or remembered for the creator, a webcam you set up for this run,
or one remembered for the creator, reaches it too. A split you set up before an
earlier run, a webcam you drew for that clip, or a split you turned off for it
stays as it is, even with Gaming / Reaction off for the new run (a Sports job
uses its own layout). Change it in the clip editor.

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

## A stream that changes partway

Streams don't keep one layout. Many open with an hour of Just Chatting
before the game; a reaction streamer often goes full screen on their camera
between videos; and the webcam itself moves when the streamer changes scene
(measured: top left over a browser while chatting, lower down on the left
for the game; bottom left, then top left, in a two-hour reaction). So the
layout is decided per clip, from what is on screen in it, not per video, and
not from the stream's category (YouTube and Kick don't say which part of a
stream is which). Each clip gets a quick look (two frames a second), and
somebody at the webcam keeps the split with no more checks. Otherwise, even
with a webcam drawn in the setup or remembered for the creator:

- **The streamer's camera filling the frame: framed like a talking-head
  clip** by the standard renderer. TalkNet runs only when somebody on screen
  is too big to be in a webcam, and only a confident real person takes the
  clip: the streamer watching an old stream of their own, their face filling
  the video, kept the split with their real webcam.
- **The webcam somewhere else: followed there.** The clip is looked at the
  way the setup editor suggests a webcam (a person in the same spot in
  nearly all of six frames, overlay-sized, inside a real border), about
  half a second a clip, and the split uses that box for this clip. Game
  characters standing around in GTA were never taken for it.
- **Nothing certain** (a cutscene, an empty chair) keeps the webcam that was
  set up: a person's choice isn't overridden on absence alone.
- **The automatic webcam search looks past those clips.** A clip showing the
  camera filling the frame says nothing about where the webcam is, so it
  doesn't count as one of the four looks; another clip, from further across
  the stream, is looked at instead (eight at most). Before, an opening hour
  of chatting could take three of the four, leave one vote for the webcam,
  and every game clip rendered without the streamer.
- **Such a clip is saved as what it got**, so the clip editor shows its
  standard framing and a re-render keeps it; **Gaming / Reaction** in its
  Layout row switches it back.
- **Scoring is the same across the parts.** What is said counts as on any
  stream, and the game adds on top; what the picture shows only takes points
  off a quiet clip, so a Just Chatting clip with no gameplay isn't marked
  down for it.

## Where the game comes from

The game area is **never detected**. Earlier attempts looked for the part of
the screen with the most going on, and scrolling chat won every time. So:

- **Whole game**: the biggest picture beside the webcam and the solid panels
  (below) that leaves them out, so the streamer isn't shown twice. With no
  webcam, the whole stream.
- **Clear of the webcam's border**: both keep 1% of the frame away from the
  webcam box on every side. The box stops just inside the overlay's border,
  so a game cut right at its edge showed the border as a line down the game
  (seen on a reaction whose webcam moved to the top left).
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
said. Gaming / Reaction scores that way, and so does a live that was already
vertical: tick **Vertical Live** and set **Vertical Live content** (the row
under it, like Longform's output) to **Gaming / reaction** instead of
**Talking / IRL**, in the Generate bar, a queued video's settings and a
watched channel's, whether it's a game or reacting to videos. One Vertical
Live, two kinds of content, not two pipelines. Everything below
applies to both; only the framing differs (a Vertical Live keeps its own
9:16 layout, so the webcam and game area aren't needed). With neither
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
- **The AI is told.** Every scoring prompt says it is a gaming or reaction
  stream and which game (for the part of the stream it is reading), to judge
  what is said exactly as on any stream, and to count in-game moments too:
  what a highlight is in that game, that a clear in-game moment is a strong
  clip even when little is said, and that menus, queues, loading screens and
  reading out donations score low.
  The moments the signals found are read eight at a time: all at once, a
  20-minute stretch's 45 moments ran past the model's answer budget and every
  one fell back to a neutral 50.
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
  It is measured against their own talking over the minute around it, twice
  their usual voice and up. Against the stream's overall level, which on a
  quiet game stream is its silences, just talking counted as a shout.
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
  ACE") and the clip's breakdown shows; it adds no points of its own, because
  most videos never show one and a caption can look like one. A window where
  at least two thirds of the frames show menu words is marked down a little
  (-6, less the more is said over it: nothing for a clip full of talk) and
  gets no bonus, whatever chat made of it.
  `config/gaming.yaml`'s `screen_text` lists the words in the languages games
  are commonly played in. It reads Latin script and kanji, not kana.
- **The AI looks at the best clips.** Last, before the final ranking, the
  local AI model is shown four frames (at 896 pixels) of each of the best 12
  clips, with what was said and what the signals picked up, and asked what is
  on screen and what happens: a clear moment raises a clip by up to 10, a
  menu, a loading or black screen lowers it by 10, ordinary play leaves it
  alone. What takes points off counts only as far as the clip is quiet: the
  full 10 when nothing is said, half when half of it is talk, nothing for a
  Just Chatting clip full of talk. The clip's breakdown says what it saw ("SEEN: scores a goal"). About
  10 seconds a clip on a GPU, for at most 4 minutes a video. Only a local
  model that takes images does this (Gemma 3 and Gemma 4 do, gemma:7b
  doesn't), and it is first asked the colour of a plain red square, since a
  model can say it takes images and not get them. The video picture never
  goes to a cloud AI, so with one this step is skipped, and the job log says
  why.
- **Talk first, the game on top.** A clip with talking is scored exactly as on
  any stream, with the same weights: the words count as much as ever. The
  game adds to that the way creator context does, never taking anything away:
  up to +7 for how strongly the game channel (everything above) marks the
  clip, and +5 more when two independent witnesses agree (chat, the game's
  sound, the streamer shouting or laughing) or chat and a loud moment do;
  +12 at most. The game's sound with loudness alone doesn't
  count as two, because gunfire is loud. When little is said, the weight the
  words would have had goes to the game channel and the sound instead, so a
  quiet streamer's big play isn't marked down for its silence. (Weighting the
  game at 40% of every clip, as this did at first, left a two-hour reaction
  to BlizzCon with 13 clips: most of a reaction is talk.) The standard
  "active content" bonus (a person on screen, moving) doesn't apply: a
  facecam over a moving game is that all stream long.
- **Each moment is a candidate of its own**, from about 4 seconds before it to
  the reaction, 15 to 35 seconds, even when nothing was said near it.

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
- The AI looking (gemma3:4b, the model setup installs for a PC without a
  graphics card) at six of these moments got all six right: the Apex settings
  page and a black screen with only the webcam (-10 each), two Apex fights
  (+6, "firefight with multiple opponents"), a Rocket League goal (+6), and
  the matchmaking menu (-10). At 512 pixels a frame it had taken the fights
  for ordinary play. gemma4:e4b on the Ollama release installed here said it
  takes images but described a settings page as "a dark, abstract
  background": the red square catches that.
- 30 minutes of a stream filed under a football game that was really two
  streamers watching someone else's IRL stream: one weak moment, nothing
  strong enough to become a candidate on its own.

**What this can't do.** Reading the screen only knows the words it has been
given, in the scripts the OCR reads. The AI only looks at the best 12 clips,
not the whole stream, and only with a local model that takes images: with
gemma:7b or a cloud AI, a menu chat reacted to is caught only if its words are
on the list. Nothing here has been run on a whole quiet vertical stream yet.

Scored as gaming, the "reaction" signal (is a person on screen, being
emphasised?) is left neutral, in the split and in a Vertical Live alike: on a
game stream it counts game characters as people, and a top-down game as
nobody at all; on a reaction stream, the people in the video being watched.
A Vertical Live set to **Talking / IRL** keeps it.

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
| Grand Theft Auto V, after an hour of Just Chatting | camera full screen at the start, then the webcam top left over a browser, then lower on the left in the game | ✓ 27 of 27 clips (9 clips, each with the webcam drawn on a chat frame, drawn on a game frame, and found): full-screen camera framed the standard way, the webcam followed to where it was in each part |
| World of Warcraft reaction to a games showcase | webcam bottom left, top left for a while | ✓ followed to the top left in that stretch; the other 5 of 6 clips kept the drawn webcam |

Time on an RTX 3060: finding the webcam looks at four 40-second pieces of the
video, about 15 seconds each (6 for person tracking, 8 for TalkNet), and a
few more when some show the camera filling the frame. Each clip then checks
that the webcam is there, a few seconds, instead of the standard face
tracking. A split set up before processing skips the search; its clips get
the quicker look (two frames a second) for a camera filling the frame.

`scripts/gaming_detect_bench.py` repeats the measurement on any footage.

## Known limits

- **VTubers aren't supported.** The detection is for people on camera.
- **One layout per clip.** A clip that moves between the game and a
  full-screen camera, or across a webcam move, keeps one layout: the one
  most of the clip shows.
- **The Game UI and second webcam boxes are drawn by hand.** Nothing looks
  for a scoreboard or a second streamer.
- **YouTube frames for the editor are read over IPv4.** On some networks
  FFmpeg's IPv6 connection to YouTube hangs for minutes; the editor's frames go
  through a small local relay that forces IPv4.
- **Separate recordings** (the game and the webcam as two files, as some
  recorders make) aren't supported yet.
