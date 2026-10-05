"""Reactions: the crowd, the bench and courtside, as moments of their own.

Basketball broadcasts cut away from the court after a big play: to the
fans on their feet, the bench, a coach, someone famous courtside. WSC Sports
uses crowd reaction to find the moments that matter; here the reaction is
also something people clip on its own. How it is found, from what the app
already has:

- **The cutaway**: the keyframes of the whole game, with the people in each
  found by the app's detector (YOLOv8n). On a broadcast's court shot the
  tallest person is a player seen from the stands, at most about a third
  of the frame's height; a shot of people (the crowd, the bench, a coach,
  courtside) is closer, the tallest 0.4 of it and more. Measured on three
  NBA games, that told 90 of 90 hand-labelled frames apart, where the
  floor's colour couldn't: the lower half of a court shot is the front
  rows. Without the detector, the colour and edge test (`looks`) stands in.
  A run of shots that aren't the court, between two that are, is a
  cutaway (an advert break or a studio segment runs far longer and is left
  out). A frame with no one in it (a stat card, a fade, a replay's wipe)
  is neither: it doesn't start a cutaway, or end one.
- **The play before it**: a cutaway starting within `react_within` seconds
  of a play is that play's reaction. Dunk at 1:23:14, the crowd roars at
  1:23:15, the camera cuts courtside: one moment.
- **The crowd**: a roar over the cutaway (the sound model's crowd curve).
- **A name**: only the broadcast's own caption over the cutaway (a
  lower-third the app's OCR reads), never a face matched to a name. Without
  one it is a "Courtside reaction" or a "Crowd reaction", nobody named.

A cutaway alone isn't a reaction (the camera shows the crowd during free
throws too): it needs the play before it, the roar or a caption.

Who is shown (the bench, courtside, a coach) is told by the local model
looking at the frames (sports/basketball/look.py), when it can take images.
"""

import re
from dataclasses import dataclass

from sports.core.events import SportEvent
from sports.core.windows import window

MAX_CUTAWAY = 25.0       # longer than this off the court is a break, an advert or the studio
THUMB_WIDTH = 192        # keyframes are read this small: a colour and edge count needs no more
DETECT_WIDTH = 640       # ...and this small for the detector: a player from the stands is still 60 px
NAMES_MAX = 60           # cutaways whose caption is read (a full OCR each)
CROWD_AT = 0.4           # the crowd curve's bar for a roar (sports/core/detect.py's)
AFTER_REACTION = 1.5     # seconds a merged clip runs past the end of the reaction shot
REACTION_GAP = 1.0       # a play's clip holds a reaction shot starting at most this long after the clip's end...
REACTION_MOST = 4.0      # ...and at most this much of it...
REACTION_START = 5.0     # ...when it starts at most this long after the play

# Words a lower-third writes that aren't a person's name.
NOT_NAMES = {"live", "replay", "timeout", "time", "out", "quarter", "half", "halftime", "final", "overtime",
             "bonus", "fouls", "foul", "free", "throw", "throws", "shot", "clock", "points", "rebounds",
             "assists", "game", "tonight", "season", "playoffs", "presented", "by", "the", "and", "vs",
             "at", "nba", "wnba", "ncaa", "espn", "tnt", "abc", "nbc", "fox", "cbs", "prime", "video",
             "sports", "network", "arena", "center", "centre", "garden", "court", "home", "away", "fan",
             "fans", "crowd", "kiss", "cam", "dance", "challenge", "review", "lead", "run", "record",
             "streak", "leaders", "stats", "team", "player", "coach", "next", "up", "coming"}
NAME = re.compile(r"^[A-Z][a-zA-Z'\-.]+(?:\s+[A-Z][a-zA-Z'\-.]+){1,2}$")


@dataclass
class Cutaway:
    start: float
    end: float
    crowd: bool = False      # a shot of people was seen: someone close (the detector), or edges (looks())
    name: str = ""           # the broadcast's own caption, when one was read


# ---- telling the court from people ------------------------------------------------


def looks(img, court_share: float, crowd_edges: float) -> str:
    """"court", "people", "other" or "nobody" (a dark frame: a fade, a black
    cut) for one frame (BGR), from its lower half: the share of its most
    common colour, and how much of it is edges."""
    import cv2
    import numpy as np

    h = img.shape[0]
    lower = img[h // 2:, :]
    hsv = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)
    lit = hsv[:, :, 2] > 40
    if lit.sum() < lower.shape[0] * lower.shape[1] * 0.2:
        return "nobody"                                  # a dark frame: a fade, a black cut
    hue = (hsv[:, :, 0][lit] // 10).astype(np.int32)     # 18 hues
    sat = (hsv[:, :, 1][lit] // 64).astype(np.int32)     # 4 saturations
    counts = np.bincount(hue * 4 + sat, minlength=72)
    share = float(counts.max()) / max(1, int(lit.sum()))
    gray = cv2.cvtColor(lower, cv2.COLOR_BGR2GRAY)
    edges = float((cv2.Canny(gray, 80, 160) > 0).mean())
    if share >= court_share and edges < crowd_edges:
        return "court"
    if edges >= crowd_edges:
        return "people"
    return "other"


def shot_kind(people: list, tall: float) -> str:
    """"court", "people" or "nobody" for one frame, from the people the
    detector found in it ((x, y, w, h) in frame fractions): "people" when
    the tallest is at least `tall` of the frame's height, "court" when there
    are people and none that tall, "nobody" when there is no one (a stat
    card, a fade, the arena from above)."""
    if not people:
        return "nobody"
    return "people" if max(p[3] for p in people) >= tall else "court"


def cutaways(shots: list[tuple[float, str]], video_end: float) -> list[Cutaway]:
    """The cutaways in a game: each run of frames that aren't the court,
    between two that are, lasting at most MAX_CUTAWAY. `shots`: (time, what
    shot_kind() or looks() saw) for each keyframe, in order. A frame with
    nobody in it is passed over: a stat card after a dunk is no fan's
    reaction."""
    out: list[Cutaway] = []
    seen_court = False
    run: list[tuple[float, str]] = []
    for t, kind in shots:
        if kind == "nobody":
            continue
        if kind == "court":
            if run and seen_court:
                end = t
                if end - run[0][0] <= MAX_CUTAWAY:
                    out.append(Cutaway(run[0][0], end, crowd=any(k == "people" for _, k in run)))
            run = []
            seen_court = True
        else:
            run.append((t, kind))
    return out


def read_shots(path, duration: float, court_share: float, crowd_edges: float, cancel=None,
               tall: float | None = None, model_name: str = "yolov8n.pt") -> list:
    """(time, shot_kind() or looks()) for every keyframe of the video, in
    one pass: the detector's people when `tall` is given and the detector
    loads, else the colour and edges of a small thumbnail."""
    from core.modes import probe_size
    from sports.basketball import keyframes
    from sports.core.scorebug import keyframe_crops

    model = None
    if tall is not None:
        try:
            from sports.soccer.ball import _model

            model = _model(model_name)
        except Exception as e:                               # no ultralytics, no weights
            print(f"      (cutaways: the detector isn't available ({e}), telling shots by colour)")
    kinds: dict[int, str] = {}
    if model is not None:
        from sports.soccer.ball import detect

        def on_frame(i, img):
            _balls, people = detect(model, img, DETECT_WIDTH)
            kinds[i] = shot_kind(people, tall)

    else:
        def on_frame(i, img):
            kinds[i] = looks(img, court_share, crowd_edges)

    with keyframes.listing(path) as own:
        times = own(keyframe_crops(path, (0.0, 0.0, 1.0, 1.0), probe_size(path), on_frame, cancel,
                                   scale_width=DETECT_WIDTH if model is not None else THUMB_WIDTH))
    # By their own times, two keyframes can swap places.
    return sorted(((t, kinds[i]) for i, t in enumerate(times) if i in kinds), key=lambda shot: shot[0])


# ---- a name, only from the broadcast's own caption --------------------------------


def known_words(*texts) -> set:
    """Every word in these texts (the video's title, the score bug), in
    lower case: a caption made of them is a team or a school, not a person
    ("King Philip" over a girls' game titled "King Philip vs Attleboro")."""
    return {w.lower() for text in texts for w in re.findall(r"[A-Za-z][A-Za-z'\-.]+", str(text or ""))}


def name_in(lines: list[str], exclude=()) -> str:
    """A person's name in a caption's lines: two or three capitalised words,
    none of them a broadcast word or a team code. "" when there is none."""
    banned = {str(x).lower() for x in exclude}
    for raw in lines:
        text = " ".join(str(raw).split()).strip(" .:-|")
        if text.isupper():
            text = text.title()
        if not NAME.match(text) or not 5 <= len(text) <= 32:
            continue
        words = [w.strip(".'-").lower() for w in text.split()]
        if any(w in NOT_NAMES or w in banned for w in words):
            continue
        return text
    return ""


def read_names(path, found: list[Cutaway], exclude=(), grab=None, ocr=None) -> None:
    """The caption over each cutaway, read from its middle frame's lower
    part with the app's OCR (the first NAMES_MAX cutaways). `grab` and `ocr`
    stand in for the video and the OCR in tests."""
    if not found:
        return
    if ocr is None:
        from analysis import game_text

        if not game_text.available():
            return
        ocr = game_text._ocr
    if grab is None:
        import cv2

        from video.capture import video_capture

        with video_capture(path, required=False) as cap:
            if cap is None:
                return

            def grab_frame(t: float):
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, img = cap.read()
                return img if ok else None

            read_names(path, found, exclude, grab_frame, ocr)
        return
    for c in found[:NAMES_MAX]:
        img = grab((c.start + c.end) / 2)
        if img is None:
            continue
        lower = img[int(img.shape[0] * 0.6):, :]
        lines = [str(text) for _box, text, conf in (ocr(lower) or []) if float(conf) >= 0.6]
        c.name = name_in(lines, exclude)


# ---- reactions as moments ----------------------------------------------------------


def _peak(curve, lo: float, hi: float) -> float:
    if curve is None or len(curve) == 0:
        return 0.0
    a, b = int(max(0, lo)), int(min(len(curve), hi + 1))
    return float(max(curve[a:b])) if b > a else 0.0


def moments(profile, events: list[SportEvent], found: list[Cutaway], *, curves: dict, video_end: float,
            min_len: float, max_len: float, react_within: float, focus: bool) -> list[SportEvent]:
    """The reactions, as events, each tied to the play just before it, and
    those plays' windows grown to hold their reaction when it fits.

    `focus`: the job asked for reactions (Fan reactions...): a reaction is
    then the clip's moment, the play its lead-in. Otherwise a reaction tied
    to a play counts for less than the play, so a Dunks clip stays a dunk."""
    reaction_types = set(profile.reaction_types)
    plays = sorted((e for e in events if not e.is_replay and e.type not in reaction_types
                    and e.type != "big_moment" and e.confidence >= 0.66), key=lambda e: e.t)
    crowd = curves.get("crowd")
    out: list[SportEvent] = []
    for c in found:
        play = None
        for e in plays:
            if e.t <= c.start + 1 and c.start - e.t <= react_within:
                play = e
        roar = _peak(crowd, c.start - 2, c.end) >= CROWD_AT
        signals = ["cut away from the court"]
        if play is not None:
            signals.append(f"after the {profile.event_label(play.type).lower()}")
        if roar:
            signals.append("crowd roar")
        if c.name:
            signals.append(f"on screen: {c.name}")
        if len(signals) < 2:
            continue                                  # a crowd shot during free throws, not a reaction
        if c.name:
            kind = "celebrity_reaction"
        elif c.crowd and roar:
            kind = "crowd_reaction"
        else:
            kind = "fan_reaction"
        e = SportEvent(kind, c.start, min(1.0, len(signals) / 3), profile.importance(kind), signals=signals,
                       person=c.name)
        if play is not None:
            # From the play's lead-in to the end of the reaction shot.
            start = max(0.0, play.t - min(profile.window_of(play.type)[0], 6.0))
            end = min(video_end, c.end + AFTER_REACTION)
            if end - start > max_len:
                start = max(0.0, end - max_len)
            if end - start < min_len:
                end = min(video_end, start + min_len)
            e.start, e.end = round(start, 2), round(end, 2)
            if not focus:
                e.importance = min(e.importance, max(0, play.importance - 1))
            # The play's own clip holds its reaction when the reaction follows
            # on from it, and the first seconds of it. A shot of people
            # starting later had the next possession between: on an NBA game
            # a player near the camera in the next fast break read as one, 7 s
            # after a three, and the three's clip ran on through two more plays;
            # one 6.5 s after an and-one, its clip through a turnover and a drive.
            end = min(c.end, c.start + REACTION_MOST) + AFTER_REACTION
            if (c.start <= play.end + REACTION_GAP and c.start - play.t <= REACTION_START and end > play.end
                    and end - play.start <= max_len):
                play.end = round(min(video_end, end), 2)
                play.signals.append(f"then {profile.event_label(kind).lower()}")
        else:
            e.start, e.end = window(*profile.window_of(kind), c.start, min_len=min_len, max_len=max_len,
                                    video_end=video_end, post_extra=c.end - c.start)
        if focus:
            e.importance = 100
        out.append(e)
    return events + out
