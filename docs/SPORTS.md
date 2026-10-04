# Sports

**For matches: the goals, saves, cards and big chances of a game, each as its
own clip with the build-up and the reaction, framed to follow the ball.**

A match isn't a stream. Nobody on screen is the streamer, the moments that
matter are over in two seconds, and a 9:16 crop that follows the biggest face
frames the nearest player while the goal goes in off the side. Sports is a
switch of its own that scores and frames a video as a match.

Soccer (football) is the first sport and [Basketball](#basketball) the second,
on the same framework. The design is modular so more can be added (see
[Adding a sport](#adding-a-sport)).

It is off unless you turn it on. With it off, nothing about processing changes
and none of its code runs.

## Turning it on

- **A video or file**: tick **Sports** in the Generate bar. A row appears under
  the video:
  - **Sport**: ⚽ Soccer / Football or 🏀 Basketball. Cricket is listed under them as coming soon.
  - **Quarter** (Basketball only): the entire game, one quarter, or overtime.
  - **Highlights**: which moments become clips (below). Hover a choice in the
    list to see what it keeps.
  - **Teams or players** (optional): clips where the commentary names them get
    extra points. Nothing else is left out for it.
  - **Also make**: the [story reels](#story-reels-the-match-in-one-video).

  The whole match is always clipped. To ask for particular moments in your own
  words, or to give the [match events](#match-events-the-goals-as-you-have-them),
  use Ask Clips Kitty, the box at the bottom.
- **A match streamed 9:16**: tick **Vertical Live** and choose **⚽ Soccer /
  Football** or **🏀 Basketball** as its content, after Talking / IRL and Gaming / reaction.
- **A queued video**: the same, in its **Settings**.
- **A watched channel**: the same, in the channel's clip settings, so every
  match a channel posts is clipped this way.
- **The API, the assistant and MCP** take `sport` (see
  [the API](#the-api)).

Sports can't be combined with **Podcast** or **Gaming / Reaction**: each scores
and lays out the video its own way. It works with **Longform** (16:9 clips of
the moments) and with **Vertical Live**, where Soccer is one of the content
choices (a match filmed 9:16 keeps its own layout).

### Highlights

| Choice | What becomes a clip |
|---|---|
| **Best moments** | Everything, best first, and always every goal the scoreboard confirmed |
| **All goals** | Every goal (penalty goals and own goals too), each with its build-up |
| **Goals + celebrations** | The same with 12 more seconds after each, for the celebration |
| **Best saves** | Saves, and penalties saved or missed |
| **Best chances** | Big chances, shots, and missed penalties |
| **Attacking plays** | Goals, chances, shots, penalties, free kicks, corners |
| **Cards** | Red and yellow cards, and VAR reviews |
| **Penalties** | Penalties given, scored and missed |

With a choice other than Best moments, the clips of those moments are kept even
when their score is under your minimum, and the others are set aside with a
reason ("not in the chosen highlights"). To ask for something in your own words
("the saves and the late chances"), say it in the box at the bottom of the app:
those moments get extra points.

## What it finds, and how

It uses the models the app already runs. There is no second AI stack.

| Signal | From | What it tells |
|---|---|---|
| **The scoreboard** | The score box in the corner, read with the app's OCR (RapidOCR) | A goal, for certain: the score changed. Which side scored. The match clock, so the half and the minute. |
| **The crowd** | The app's sound model (PANNs) | A roar that lasts: something happened |
| **The whistle** | The same | Play stopped: fouls, cards, penalties, the end of a half |
| **The commentary** | Whisper's transcript | What happened, in the commentator's words ("what a save", "penalty", "he's sent off"), in the main football languages |
| **The screen** | The app's OCR | Graphics like "VAR CHECK" or "RED CARD". Off for soccer (`read_screen` in `config/sports.yaml`): on the test final it took two minutes and found nothing the score box didn't |
| **The clip itself** | Your AI model, reading the transcript and the signals | How good the clip is, as for any video |

**The scoreboard is ground truth.** It is found once (a small box of text that
stays put, with a score and a clock), then read from every keyframe of the
match. A goal is a score that changed and stayed changed for two readings, so a
misread digit is never a goal. On a 1h42m official upload of a World Cup final
it read the box 1,541 times (49 seconds on its own, 141 while Whisper was also
running) and found all six goals, with the right side for each.

The box is the score's own line of text, with the clock beside it, not
everything written near it: a stadium's ad boards and banners are left out,
which matters most in a 9:16 frame, where the same strip of the picture holds
far more stadium. Soft text (an upscaled video, a phone filming a screen)
loses the separator and reads a 0 as the letter O ("HOMOOAWO" for HOM 0-0 AWO); once the teams
are known, the score is read from between their codes. On the final that took
the readings with a score from 1,016 to 1,269, and on a 9:16 version of it from
59 of 301 to 213, with all its goals found.

**The crowd dates the goal.** The new score shows up some time after the ball
goes in: 8 seconds after one goal of that final, 40 after another, and on other
broadcasts only after the replays. So a goal is placed where the crowd's
loudest five seconds start, between the score before it and the new one. On
the final, every goal was placed within two seconds of the ball going in, and
the clock read off the box gave the minutes the match record gives: 18', 28',
38', 59', 65', 69'. A goal whose crowd can't be heard is placed half a minute
before the new score.

**A moment is named only when the evidence agrees.** A goal the scoreboard
confirms is a goal, and where the score is being read, nothing else is: "they've
scored four!" said over a cheer, with the score unchanged, is talk about a goal.
Other moments are named only when the commentary names them and at least one
other signal (the crowd, the whistle, the screen) agrees. The commentary alone
is never enough, and a moment the signals mark without a name is kept as a
**big moment**, with the signals it had. The crowd counts when it cheers above
its usual level for two seconds or more; a one-second "ooh" doesn't. Tackles,
dribbles, assists and key passes aren't claimed.

**Replays are grouped, not clipped twice.** Broadcasts replay a goal two or
three times, and each replay has the same words and a smaller roar. A cheer
within 45 seconds after a goal is its celebration (no kick-off comes that
soon). Within 90 seconds, a moment that looks like another goal without the
score changing (or while the score box is hidden, or when the commentator says
"replay" or "watch it again") is a replay of it. Both are grouped with the
goal, and only the goal is clipped.

The voice jump the gaming profile uses (the streamer suddenly twice as loud)
barely fires on a broadcast. The commentary is compressed: in the final above it
reached twice the commentator's usual level 14 times in the match, around one of
the six goals. It is left as it is, and the crowd, the commentary and the
scoreboard carry the detection.

## The clips

Each moment gets its own window, set per type in `config/sports.yaml`:

| Moment | Before | After |
|---|---|---|
| Goal | 14 s (the build-up) | 10 s (the celebration) |
| Penalty goal | 10 s | 10 s |
| Save | 8 s | 6 s |
| Red card | 8 s | 10 s |
| Yellow card | 6 s | 8 s |

The crowd reacts a second or two after the moment, so a moment found by the
crowd is moved back 1.5 s. A window never runs past the video, and when the
clip is too long, the build-up is trimmed first.

Every clip is scored the normal way, then its moment adds up to 20 points (a
goal most, a replay nothing). One clip per moment: when two candidates show the
same goal, the better one is kept. A goal the scoreboard confirmed is kept even
when the words around it score low: on the test final, the words alone rated
two of its six goals 10 and 25 out of 100.

The clip card names the moment ("Goal · 18'", and the side that scored), and
the clip page opens with a short note: what was found, the score read, and
anything that couldn't be confirmed (a moment whose half couldn't be told is
kept, and says so).

## Framing: follow the ball

A 16:9 match cropped to 9:16 keeps a third of the picture, so the crop has to
be where the play is. Sports frames the way the automatic camera systems do
(Veo, Pixellot, Hudl, Trace):

- **The ball**, from the object detector the app already ships (YOLOv8n,
  "sports ball"). A detection is followed only if the ball could have got
  there since the last one, and a new ball is only picked up from a confident
  detection, so a boot or a steward's vest isn't followed.
- **When the ball is lost**, the players near where it was last seen.
- **In a close-up** (a player filling the frame, a celebration), that player.
- **Moves** the way the rest of the app's framing does (a hold, a smooth
  move), a little faster for play, and **cuts** when the broadcast cuts,
  never panning across a camera change.

The ball in a broadcast wide shot is a few pixels across, so the detector runs
at 1280 px. Measured on 200 frames of the final, in the 123 wide shots:

| Detector | Ball found | Time per frame (GPU) |
|---|---|---|
| YOLOv8n at 640 px | 35 | 25 ms |
| YOLOv8n at 960 px | 77 | 28 ms |
| **YOLOv8n at 1280 px** (used) | **90** | **34 ms** |
| YOLOv8s at 1280 px (a 22 MB download) | 78 | 44 ms |

The bigger model found fewer, and was slower, so no new model is needed. The
detector and its size are set in `config/sports.yaml` (`framing`).

## Vertical videos

A match filmed or streamed 9:16 (a phone at the side of the pitch, a vertical
feed) keeps its own picture, as Vertical Live does, with nothing to switch on:
the app notices the shape, doesn't reframe it, and finds the moments the same
way, score box included. On a 9:16 version of 25 minutes of the final it found
all three goals at their minutes, and each clip is the whole vertical picture,
processed in 5 minutes.

With **Longform**, the clips stay 16:9, and they and the Highlights reel are
the match's moments too.

## Club and phone footage

A club's own camera (an auto-camera like Veo, a parent's phone at the
touchline) is the footage clubs actually own, and it is nothing like a
broadcast: no score box, no commentary, a handful of people watching. The app
tells which it is by itself: a match with no score box on screen is taken as
club or phone footage, whichever way it was filmed.

On club or phone footage:
- **The framing still follows the ball.** A wide club camera is cropped to
  9:16 on the ball and the play, exactly as a broadcast is; a phone video
  filmed 9:16 is kept as filmed.
- **Nothing is named a goal it can't confirm.** On two club matches, nothing
  audible marked the goals. On an auto-camera match with 11 goals, the sound
  model heard no crowd and no whistle, Whisper heard no words (the players'
  shouts are too far off), and a sudden loud burst found 7 of the goals only
  by firing 69 more times elsewhere; the camera's movement found none. A
  phone filming from the stand picked up a few cheers but no words.
- **A choice like All goals keeps the best moments instead**, when nothing
  confirmed a goal, and the clip page says so, rather than giving no clips.

## Match events: the goals as you have them

Tell **Ask Clips Kitty**, the box at the bottom, the match's goals and other
moments as you already have them, with the link, one per line: from your club
app (Veo tags its goals), the match report, or the video's own description.
Each one becomes a clip, and it's certain: "from your match events".

The box needs a Gemma 4 model. If it says no installed model can use tools, see
[The text box at the bottom needs Gemma 4](../README.md#the-text-box-at-the-bottom-needs-gemma-4).

```
09:22 Kick off
18:16 Goal Player A
45+2' Yellow card Team B
1:00:40 Second half
```

- **Times** can be a time in the video (`18:16`, `1:06:51`, even `1:14.29` as
  typed) or a match minute from a report (`18'`, `45+2'`, or a bare `67` at
  the start of a line).
- **A match minute is placed** by the score box's clock when one was read, and
  otherwise by the list's own kick-off lines (`Kick off`, `Second half`); the
  crowd then says when in that minute it happened. With neither, the line is
  listed back on the clip page as one that couldn't be placed.
- **A time in the video** is taken as a tag. With a crowd to date it (a
  broadcast), the goal is placed where the roar starts. With nothing else to
  go on, the clip runs from 5 seconds before the tag to 40 after: a club app's
  goal tags on the test match sat 21 to 27 seconds before the ball went in, at
  the start of the attack, and the clip then holds the attack, the goal and the
  celebration.
- **What the list names wins**: a moment found near a listed one is that
  moment, and gets its kind, player or team from the list. A goal the score
  box already dated to the second keeps its time.
- **The rest of a line** after the time and the kind is who it was (a player or
  a team) and shows on the clip.
- **A line with no time or no kind of moment** is listed back, never guessed.
  A list with nothing readable at all is refused when it's queued.

It belongs to one video: the next video in the Generate list starts without it.

## Story reels: the match in one video

**Also make** in the Sports row joins the match's clips into longer videos (Ask
Clips Kitty can ask for them too):

- **Match recap**: every named moment (the goals, cards, saves and chances the
  evidence confirmed), in match order, titled with the score read ("HOM 2-1
  AWO: match recap").
- **Team reels**: a video per team of the moments the score box or your match
  events give it.
- **Player reels**: a video per player named in two moments or more, by your
  match events, or by the commentary for a name you type in **Teams or
  players** (the whole name or its surname, accents and case aside). A team
  the score box read, typed as its code or a name starting with it ("Home
  United" for HOM), is left to its team reel. A player in three moments or
  more, and more than anyone else, is titled "the most moments": the stand-in
  for a player of the match, and said to be only that.

How they're made:

- **A reel needs two moments.** Big moments and replays aren't in one.
- **They're joined, not rendered.** The reels are joined from the clips already
  made, without re-encoding, so they take seconds.
- **One end card.** Each clip's own end card is left off, and the reel gets one
  at its end.
- **Chapters.** The description lists where each moment starts ("0:22 Goal
  28' AWO").
- **With Longform,** its 16:9 clips make 16:9 reels too. Its Highlights video
  already is the match's recap.
- **Changing a reel.** A reel isn't re-rendered on its own: re-render its
  clips, then process the video again. Each reel is made again into the same
  clip, keeping its title.

## Measured and left out

Two ideas were measured on the broadcast final before anything was built, each
against a bar set beforehand. Neither cleared it, so neither is in the app.

- **Reading shirt numbers**, so "#10" could pick a player's clips. Over the six
  goals' windows the person detector found 118 players close enough to the camera,
  and the app's text reader read a number on 15 of them (13%). Checked by eye on 64:
  about 20 showed a number a person could read, the reader got 5 of those right,
  and 2 of its 7 reads were numbers nobody wore (a referee read as 11, and a 39).
  The bar was 70% right and at most 5% wrong. Players are found by the commentary
  and by your match events instead.
- **MatchVision**, a soccer event model (UniSoccer, Apache-2.0 checkpoints: a 1.7 GB
  classifier on a 0.8 GB SigLIP backbone; trained on a dataset released for research
  use). On the 12 moments the app clipped, it named five of the six goals (the score
  box already finds all six) and called the sixth, a goalkeeper's mistake, a
  clearance. On the other six it was right on one (a corner), close on two (a kick-off,
  the closing minutes) and wrong on three, mostly at 17-35% confidence. The bar was
  clearly better naming of saves and chances without missing a goal, so it isn't
  offered, even as an optional download.

## Basketball

**The play, the situation, the reaction: a dunk, the arena erupting, someone
famous courtside on their feet, as one clip.**

Basketball is built on everything above: the same scoring, windows, Highlights
and Period choices, replays grouped, story reels, Longform, Vertical Live,
watched channels and publishing, and the same Whisper and AI model. What it adds
is in `sports/basketball/` and its entry in `config/sports.yaml`. The design takes
its ideas from public descriptions of WSC Sports (the NBA's automated highlights,
which use crowd reaction to find big moments), BARD's multi-label basketball
actions, basketball_event_tracking's split of ball, players and possession, and
LumenSport's separate finding, scoring and editing. Nothing is copied from them,
and no NBA tracking data is assumed: it works on any game, from the NBA to a rec
league.

### Highlights

| Choice | What becomes a clip |
|---|---|
| **Best moments** | Everything, best first, each play with its reaction when it fits |
| **Best plays + reactions** | The same with 6 more seconds after each play |
| **All scoring** | Every basket (dunks, threes, layups, putbacks...) |
| **Dunks** | Dunks, alley-oops, posters and putback slams |
| **Threes** | Made threes, corner and deep ones too |
| **Blocks** / **Steals** / **Assists** | Those plays, with the action around them |
| **Clutch moments** | Game winners, buzzer-beaters, tying and go-ahead baskets, late baskets in a close game |
| **Fan reactions** | Every reaction: the crowd, the bench, courtside, a coach |
| **Celebrity reactions** | Courtside reactions, named only when the broadcast captions them |
| **Crowd reactions** / **Bench reactions** | Those reactions |

**Teams or players** works as for soccer ("Lakers", "Curry"): clips where the
commentary names them get extra points, and nobody is guessed. Ask Clips Kitty
is told the choices, so "find Curry's best threes" can be asked as Threes with
Curry, and a reactions choice also takes words ("fans reacting to the biggest
dunks").

### The moments

The kinds of moment are data: about 70 of them in `config/sports.yaml`, in
scoring, shooting, defense, rebounding, passing, ball handling, game events and
reactions, each with its worth and its window. A new kind is a line there and its
commentary words, no code. The same rule as soccer's holds: **a moment is named
only when the evidence agrees.** The commentary ("throws it down", "for three",
"rejected", "and one") names it, and the crowd, the whistle, the buzzer or the
score bug has to agree. Fancy moves (a crossover, a euro step, a no-look pass)
are in the taxonomy but named only when the commentary says so and another signal
agrees; the picture isn't read for them.

**The score bug is ground truth.** It is found and read the way soccer's is
(`sports/core/scorebug.py`, shared): the teams, the scores, the quarter and the
game clock ("LAL 98 BOS 101 4TH 0:32"). A score up by 1, 2 or 3 on one side,
seen on two readings, is a free throw, a basket or a three, and which team
scored. The commentary's name for it stands when the points agree (a dunk is 2,
never 3), and the crowd dates it. A jump of more than 3 at once (two baskets
between readings) isn't called a basket.

**The situation sets the worth.** The quarter, the clock and the score before the
basket decide:

- the last basket of the game that takes the lead in the last 10 seconds is a
  **Game winner**; one at 0.0, or with the buzzer heard, a **Buzzer-beater**;
- late in the 4th or overtime: a **Game-tying shot**, a **Go-ahead basket**, or a
  **Clutch shot** in a one-possession game;
- every moment's points are multiplied by the situation: up to ×1.8 late in a
  close game or in overtime, and down to ×0.55 in a blowout. A game-winning three
  gets the full bonus; a first-quarter three about two thirds of it.

Without a score bug (a gym camera, a phone in the stands), every moment counts as
its kind.

### Reactions

Broadcasts cut away from the court after a big play. Each **cutaway** is found
from the keyframes of the whole game, read small: a court shot is mostly one
floor colour in its lower half with few edges, a shot of people many colours and
edges. A run of shots that aren't the court, between two that are and at most 25
seconds long, is a cutaway (an advert break runs longer).

- **Tied to the play before it.** A cutaway starting within 12 seconds of a play
  is its reaction: the play's clip is grown to hold it when it fits, so a dunk,
  the roar and the courtside shot are one clip.
- **A reaction needs more than a crowd shot**: the play before it, a roar over it,
  or a name on screen. A crowd shot during free throws isn't one.
- **Standing on its own**: with a reactions choice, a reaction is the clip's
  moment and the play its lead-in; a reaction with no play before it (the crowd
  on its feet for a timeout comeback) is its own clip.
- **Who is shown** (courtside, the crowd, the bench, a coach) is told by the local
  model looking at two frames, as it looks at a gaming stream's (Gemma 3 or 4
  through Ollama; skipped, and said so, with a model that can't take images). It
  is asked only what kind of shot it is, never who anyone is.
- **A name** comes only from the broadcast's own caption over the cutaway (a
  lower third read with the app's OCR): "Celebrity reaction · Spike Lee". Faces
  are never matched to names. Without a caption it is a courtside or crowd
  reaction, nobody named.

### Framing

The 9:16 crop follows, in order: a reaction shot (the biggest person reacting,
not where the court was), a close-up, the ball with the players around it (the
ball handler and the defenders), leaning toward the rim as the ball heads for it
near an edge, and the players when the ball is lost. Same detector as soccer
(YOLOv8n), at 960 px since a basketball is bigger in a wide shot. Cuts snap, never
pan. A game filmed 9:16 (1080×1920, 720×1280, 1440×2560) keeps its own picture.

### What isn't measured yet

The thresholds (the crowd, the cutaway's colour and edge shares, the framing's
lean) start from soccer's measured values and synthetic frames. They are in
`config/sports.yaml` to be tuned on real games.

## What it doesn't do yet

- Goals in club and phone footage with no score box or commentary, unless
  their times are added as match events.
- Tackles, dribbles, assists and key passes aren't named.
- Basketball: shot trajectory, a hoop detector, player tracking and shirt numbers
  aren't read; moves like a crossover are named only from the commentary.

## The API

`POST /jobs`, `/jobs/batch`, `/videos/local`, `PATCH /jobs/{id}` and a watched
channel's options take `sport` (`"name": "soccer"` or `"basketball"`):

```json
{"sport": {"name": "soccer", "highlights": "goals", "period": "full", "teams": "Team A"}}
```

- `highlights`: soccer `best`, `goals`, `goals_celebrations`, `saves`, `chances`,
  `attacking`, `cards`, `penalties`, `custom`; basketball `best`,
  `plays_reactions`, `scoring`, `dunks`, `threes`, `blocks`, `steals`, `assists`,
  `clutch`, `fan_reactions`, `celebrity_reactions`, `crowd_reactions`,
  `bench_reactions`, `custom`.
- `period`: soccer `full`, `first_half`, `second_half`, `extra_time`; basketball
  `full`, `q1`, `q2`, `q3`, `q4`, `ot`.
- `footage` (optional): `auto` (the default), `broadcast` or `sideline` (club
  or phone footage).
- `events` (optional): the match's events as text, one per line, up to 4000
  characters (see [Match events](#match-events-the-goals-as-you-have-them)).
  Refused only when no line has both a time and a kind of moment.
- `reels` (optional): the story reels to make, any of `recap`, `teams` and
  `players` (see [Story reels](#story-reels-the-match-in-one-video)).
- `teams` (optional): up to 200 characters.
- `request` (optional, with `custom` or a basketball reactions choice): the
  moments wanted, in words.

`GET /sports` lists the sports and their choices. An unknown sport or choice is
refused with a 400 that lists what is allowed, and so is Sports together with
Gaming / Reaction or Podcast.

A finished run's outcome carries `sport`: the moments found by type, the big
moments, the replays grouped, and the score read. Each clip's scores carry the
moment: `sport_event`, `sport_label`, `sport_minute` (from the clock), `sport_t`
(seconds into the video), `sport_why` (the signals), `sport_team`,
`sport_player` (from your match events), `sport_period` and `sport_bonus`; for
basketball also `sport_when` (the game clock, "Q4 0:32"), `sport_context` (the
situation, "takes the lead, 0:02 left") and `sport_person` (a name the broadcast
captioned). A
story reel is a clip whose scores carry `sport_reel` (`recap`, `team` or
`player`) and `sport_parts` (how many moments it joins); its render options
carry `reel` and `of` (its team or player), and it can't be re-rendered on its
own.

## Adding a sport

Everything sport-specific is data and one package:

```
config/sports.yaml     the sport's entry: its moments (importance, window),
                       commentary words in each language, on-screen words,
                       sound weights, highlight choices, periods, framing
sports/
  __init__.py          the registry: SPORTS = {"soccer": ..., "basketball": ...}
  core/                shared by every sport
    profile.py         SportProfile: what the scoring asks a sport, and the
                       rule for naming a moment (two signals agree); the hooks a
                       sport can change (scoring types, sound curves, the
                       situation's weight, moments of its own)
    scorebug.py        finding and reading a score bug (each sport parses it)
    events.py          a moment: type, time, confidence, window, signals,
                       replay, group
    detect.py          moments from the crowd, the voice, the commentary,
                       the screen and a scoreboard
    windows.py         the window around a moment
    select.py          which moments a Highlights choice keeps
    clips.py           moments onto the scored clips: the bonus, one clip per
                       moment, the Highlights and Period choices, the report
  soccer/
    __init__.py        profile(), framing(), prepass() (the scoreboard)
    profile.py         what soccer adds: own goals, penalty goals and misses
    scoreboard.py      reading the score box
    ball.py            following the ball, for the framing
  basketball/
    __init__.py        profile(), framing(), prepass() (the score bug, the cutaways)
    profile.py         which basket, the situation's weight, plays named together
    scoreboard.py      reading the score bug: points, quarter, game clock
    reactions.py       cutaways from the court, tied to the play before them
    look.py            who a reaction shot shows, by the local model
    action.py          following the ball, the play and the rim, for the framing
```

A new sport needs:

1. An entry in `config/sports.yaml`, like soccer's.
2. A `sports/<name>/` package with a `profile(config, option, video)` function.
   `framing(clip_path, config)` and `prepass(video_path, duration)` are
   optional: without them the clips are framed the standard way, and nothing
   is read before scoring.
3. A line in `SPORTS` in `sports/__init__.py`.

The app then offers it in the Sport menu, with its own highlight and period
choices, and nothing else changes.
