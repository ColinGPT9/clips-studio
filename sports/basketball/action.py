"""Basketball's 9:16 framing for one clip: the TV scoreboard left out, and
the crop planned for the whole clip around the play (sports/basketball/crop:
the ball and its holder, the receiver of a pass, the shot followed to the
basket with the rim, net and backboard in; wider over a blurred background
when the play doesn't fit or can't be followed).

**The TV scoreboard is left out** when the crop would cut through it: a
bug is wider than a 9:16 crop, so half of it showed along the bottom of
nearly every clip. The rows from the bug's top edge down (or from the top
down to its bottom edge) are left out of the crop, which zooms in that much.
The bug's text is found as the score reader finds the bug, taken where
most looks show it (scoreboard.find_text), and its graphic's edge as a
step in brightness at the same row in most looks, with the rows on the
graphic's side holding stiller than those just beyond. Only as far as
that edge: on an NBA game, leaving out everything from the top of the
reader's box (grown to a caption over the bug on one look) took 18-27% of
the height out where the bug was 15%, and cut players at the knees.

The output is the crop path video/cropper.py renders for everything else,
with its "zoom" and "rows".
"""

# The scoreboard left out. On copies of the three NBA games' bugs over a
# moving picture, the graphic reached 0.03-0.06 of the height past its text.
BUG_WIDTH = 480          # frames are looked at this wide, in gray, for the graphic's edge
BUG_REACH = 0.08         # the graphic reaches at most this share of the height past its text
BUG_INSIDE = 0.025       # ...and its edge can sit this far inside the text's box (on an NBA game the box's top,
                         # from looks that box the text differently, came out up to 0.014 above the edge)
BUG_EDGE = 4.0           # its edge: a step of this many gray levels between the same two rows...
BUG_SAME = 0.75          # ...the same way in this share of the looks (a line on the floor moves between them)
BUG_BAND = 0.03          # ...with this much of the height on the graphic's side moving less than
BUG_STILL = 0.8          # this share of what as much beyond does (a see-through graphic shows some
                         # of the picture), when the picture there moves enough to tell
BUG_MOVING = 6.0         # gray levels the picture must move by for any of that to tell anything...
BUG_TEXT = 0.5           # ...else the graphic is taken to reach this many of its text's heights past it
BUG_SLACK = 0.008        # ...plus this much more, so not a row of its border shows (2 rows at BUG_WIDTH)
BUG_MOST = 0.27          # a bug that would take more of the height than this out is left in
BUG_NEAR = 0.1           # a crop this close to the bug's text (share of the width) shows part of it


def bug_edge(grays: list, box: tuple) -> float:
    """Where the score bug's graphic ends past its text (box, in the frame's
    fractions): its top edge for a bug along the bottom, its bottom edge for
    one along the top, as a fraction of the height. The edge is the furthest
    row within BUG_REACH of the text where most looks step in brightness the
    same way, the rows on the text's side moving less than the picture beyond
    when that moves enough to tell; then past the rows beside it that move
    less than the picture too, which the graphic's border partly covers.
    Inside the text's box, only with a moving picture beyond. Without one, or
    when the picture hardly moves, BUG_TEXT of the text's height past it.
    grays: the looks, in gray, all the same size."""
    import numpy as np

    stack = np.stack([g.astype(np.float32) for g in grays])
    height, width = stack.shape[1:]
    part = stack[:, :, int(box[0] * width):max(int(box[0] * width) + 1, int(np.ceil(box[2] * width)))]
    # How much each row moves over the looks, and each look's step in
    # brightness from one row to the next (steps[:, r] is row r + 1 less row r).
    rows = np.median(np.median(np.abs(part - np.median(part, axis=0)), axis=0), axis=1)
    means = part.mean(axis=2)
    steps = means[:, 1:] - means[:, :-1]
    bottom = (box[1] + box[3]) / 2 > 0.5
    text = BUG_TEXT * (box[3] - box[1])
    fallback = max(0.0, box[1] - text) if bottom else min(1.0, box[3] + text)
    if float(np.median(rows[int(0.3 * height):int(0.6 * height)])) < BUG_MOVING:
        return fallback
    reach, band, within = round(BUG_REACH * height), max(2, round(BUG_BAND * height)), round(BUG_INSIDE * height)
    if bottom:
        # r: the graphic's first row, furthest from the text first.
        start = int(box[1] * height)
        tried = range(max(band, start - reach), min(height - band, start + within) + 1)
    else:
        # r: one past the graphic's last row.
        start = min(height, int(np.ceil(box[3] * height)))
        tried = range(min(height - band, start + reach), max(band, start - within) - 1, -1)
    for r in tried:
        step = steps[:, r - 1]
        way = np.sign(np.median(step))
        if not way or np.mean((np.abs(step) >= BUG_EDGE) & (np.sign(step) == way)) < BUG_SAME:
            continue
        inside, beyond = (rows[r:r + band], rows[r - band:r]) if bottom else (rows[r - band:r], rows[r:r + band])
        moving = float(np.median(beyond))
        if moving < BUG_MOVING:
            # A still picture beyond (the floor): the step alone tells, outside the text.
            if (r <= start) if bottom else (r >= start):
                return r / height
            continue
        if float(np.median(inside)) < BUG_STILL * moving:
            # On an NBA game the bar's top row was half picture, and in one window
            # only the step below its border agreed across the looks.
            for _ in range(band):
                past = r - 1 if bottom else r
                if not 0 <= past < height or rows[past] >= BUG_STILL * moving:
                    break
                r += -1 if bottom else 1
            return r / height
    return fallback


def find_bug_text(looks: dict, duration: float) -> tuple[tuple, tuple] | None:
    """The TV scoreboard as scoreboard.find_text reads it over `looks`
    (frames by time): (its box, its text's box), or None without the OCR or
    a bug."""
    from analysis import game_text

    if not looks or not game_text.available():
        return None
    from analysis.game_text import _ocr
    from sports.basketball import scoreboard

    times = sorted(looks)

    def grab(t: float):
        return looks[min(times, key=lambda x: abs(x - t))]

    with scoreboard.OCR:
        return scoreboard.find_text(grab, duration, _ocr)


def hidden_rows(looks: dict, duration: float, path: list, crop_frac: float) -> tuple[float, float] | None:
    """The rows of the frame the crop keeps so the TV scoreboard is out of
    it, (top, bottom) as fractions of the height; None to keep them all: no
    bug found (gym or phone footage, or no OCR), the crop never near it, or
    a bug so tall that leaving it out would zoom in too far. looks: frames
    at the times scoreboard.find_text looks at, by time."""
    found = find_bug_text(looks, duration)
    return rows_for(looks, found, path, crop_frac) if found is not None else None


def rows_for(looks: dict, found: tuple[tuple, tuple], path: list, crop_frac: float) -> tuple[float, float] | None:
    """hidden_rows() once the bug is found: `found` is find_bug_text's.
    `path` is the crop's centre, [(t, x)], or [(t, x, width)] where the crop
    widens past `crop_frac`, its 9:16 width; all fractions of the frame's
    width. A crop widened over a blurred background keeps its full height,
    so it needs the bug out as much as a 9:16 one does."""
    import cv2

    times = sorted(looks)
    box, text = found
    if not any(p[1] - w / 2 < box[2] + BUG_NEAR and p[1] + w / 2 > box[0] - BUG_NEAR
               for p in path for w in (max(crop_frac, p[2] if len(p) > 2 else crop_frac),)):
        return None
    grays = []
    for t in times:
        img = looks[t]
        h, w = img.shape[:2]
        grays.append(cv2.cvtColor(cv2.resize(img, (BUG_WIDTH, max(2, round(h * BUG_WIDTH / w))),
                                             interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
    edge = bug_edge(grays, text)
    rows = (0.0, max(0.0, edge - BUG_SLACK)) if (text[1] + text[3]) / 2 > 0.5 else (min(1.0, edge + BUG_SLACK), 1.0)
    if rows[1] - rows[0] < 1 - BUG_MOST:
        print(f"      Basketball framing: the scoreboard is {1 - (rows[1] - rows[0]):.0%} of the height, "
              "too tall to leave out")
        return None
    return round(rows[0], 4), round(rows[1], 4)


def _looks(clip_path, info: dict) -> dict:
    """The frames scoreboard.find_text looks at (evenly through the clip), by time."""
    from sports.basketball.crop import observe as observe_mod
    from sports.core import scorebug

    fps, duration = info["fps"], info["duration"]
    if duration <= 0:
        return {}
    times = [duration * (i + 1) / (scorebug.FIND_FRAMES + 1) for i in range(scorebug.FIND_FRAMES)]
    wanted = sorted({min(int(t * fps), max(0, int(duration * fps) - 1)) for t in times})
    expr = "select=" + "+".join(f"eq(n\\,{n})" for n in wanted)
    out = {}
    for n, frame in zip(wanted, observe_mod.frames(clip_path, ["-vf", expr, "-fps_mode", "passthrough"],
                                                   info["width"], info["height"])):
        out[n / fps] = frame.copy()
    return out


def bands(settings: dict | None = None) -> tuple[float, float]:
    """The shares of the vertical screen's height kept for blurred bands
    above and below the game picture: the apps' top bar, and their caption,
    username and music line at the bottom (the gaming layouts' safe zones,
    gaming/layouts.json; `safe_zone` in the framing settings, "all" by
    default: the tallest of TikTok, Reels and Shorts at each end).

    The basket sits near the top of a broadcast's picture, where TikTok's
    search and the Reels title cover it on a full-height crop, and the
    players' feet and the floor near the bottom, under the caption. Between
    the two bands the whole play shows. `screen` "full" (the Sports row's
    Crop: Full screen) fills the screen instead, as does `safe_zone` "none"."""
    from gaming.framing import CANVAS_H, safe_zone

    settings = settings or {}
    if str(settings.get("screen") or "safe") == "full":
        return 0.0, 0.0
    zone = safe_zone(str(settings.get("safe_zone") or "all"))
    top = round(min(0.3, max(0.0, zone["top"] / CANVAS_H)), 4)
    bottom = round(min(0.3, max(0.0, zone["bottom"] / CANVAS_H)), 4)
    return top, bottom


def picture_share(settings: dict | None = None) -> float:
    """The share of the vertical screen's height the game picture fills:
    all of it but the bands above and below it (bands())."""
    top, bottom = bands(settings)
    return round(1.0 - top - bottom, 4)


def compute_offline(clip_path, settings: dict | None = None, hints: dict | None = None) -> dict:
    """The crop for one clip, planned for the whole clip (sports/basketball/crop):
    what the detectors saw on it, then the plan, in the form
    video/cropper.render_vertical takes (with "zoom" when the crop widens and
    "rows" when the TV scoreboard is left out), plus its "quality" (popped
    by the caller). `hints` is the clip's render_opts["sport_moment"]: when
    the play's moment is, seconds into the clip, and its kind."""
    from core import cancel
    from sports.basketball.crop import observe as observe_mod
    from sports.basketball.crop import plan_clip
    from sports.basketball.crop.model import Hints

    settings = settings or {}
    h = Hints.from_opts({"sport_moment": hints} if hints else None)
    info = observe_mod.probe(clip_path)
    band_top, _bottom = bands(settings)
    picture = picture_share(settings)
    found = None
    looks: dict = {}
    if settings.get("hide_scoreboard", True):
        looks = _looks(clip_path, info)
        found = find_bug_text(looks, info["duration"])
    obs = observe_mod.observe(clip_path, sources=settings.get("sources"), moment_t=h.moment_t,
                              cancel=cancel.check_active, bug_box=found[0] if found else None,
                              find_bug_box=False, people_scale=h.scale)
    obs.geometry.picture = picture
    obs.geometry.band_top = band_top
    plan = plan_clip(obs, h)
    rows = None
    if found is not None:
        step = max(1, int(round(plan.fps / 10)))
        path = [(f / plan.fps, plan.cx[f], plan.w[f]) for f in range(0, len(plan.cx), step)]
        rows = rows_for(looks, found, path, obs.geometry.base)
        if rows is not None:
            obs.geometry.rows = rows
            plan = plan_clip(obs, h)
    tracking = plan.to_tracking()
    if rows is not None:
        tracking["rows"] = rows
    tracking["quality"] = plan.quality
    return tracking
