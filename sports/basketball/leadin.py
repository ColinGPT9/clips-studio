"""Where a basketball clip starts and ends: on the play's own possession,
and when the play is over, with all the announcer says about it.

A clip's window is set a fixed time before and after the basket
(config/sports.yaml), from a basket time read off the score bug. In a
condensed broadcast that start often holds another moment of the game
spliced in ahead of the play, or a close-up, the crowd or a replay, and the
end runs on into the next play, a replay or another game. This looks at the
clip's footage, finds the camera's cuts and what each shot is (crop/shots.py:
wide or medium court shots, close-ups, crowd, graphics, replays), reads the
game clock on the score bug across the cuts that matter (_clock_jumps), and:
- keeps the start, the play's lead-up, unless the clock jumps across a cut
  before the play (another moment of the game: it starts after the last) or
  it opens on a cutaway (crop/shots.lead_in_start);
- ends the clip at the broadcast's cut that ends the play, with everything the
  announcer says before it; in one court shot, before his next thought; a
  trip to the free-throw line when the clock runs again (crop/shots.clip_end);
- keeps the clip the job's shortest length by going back in the possession
  first, never into the next play (crop/shots.fit_length);
- saves where each clip must stop for the render's pad (_stop_before).
A moment that is itself a reaction (Fan reactions and the like) is left
alone, and the basket that ends the game and a game-winner keep their ends
(the celebration on the court).

A replay is told by the score bug: the broadcast takes it away for the
replay and puts it back after. The bug's box is the one the job's
scoreboard reading found (the profile passes it); without one, only a replay
wiped in and out is caught.

Runs in the job, right after the clips are chosen and before anything is
built on their windows (captions, titles, plugins, the database), so all of
those see the window the clip will really have (BasketballProfile.
refine_windows, core/pipeline._refine_windows).

About 5 s a clip: its footage, and 4 s past its end, decoded small at full
frame rate (the cuts) and 4 frames a second at 640 px through the person
detector (the shot kinds), and the bug read about a second each side of a
cut before the play or of a jump the cut tests are unsure of.
"""

from __future__ import annotations

import math
import subprocess

PROBE_HZ = 4.0           # frames a second looked at for the people in them
PROBE_SIZE = (640, 360)  # ...at this size (the shot kinds need people's heights, not their faces)
TINY = (48, 27)          # the gray thumbnail the cut test compares (video/framing.small_gray's size)
JUMP_SIZE = (128, 72)    # ...and the jump-cut test's (crop/shots.jump_cuts), and the camera's pan
PAN_CUT = 25.0           # ...not across a frame this different from the last (a cut)...
PAN_SURE = 0.1           # ...nor where phase correlation is this unsure
STINGER_SIZE = (192, 108)                 # ...and the replay stinger's: its middle, and its edges outside
STINGER_ROWS, STINGER_COLS = slice(27, 81), slice(48, 144)
STINGER_INNER = (slice(18, 90), slice(32, 160))
MIN_BEFORE = 2.0         # a play this soon after the start has nothing before it to trim
PAD_LOOK = 0.5           # the look runs this far past a clip's end: the render pads its end 0.4 s (core/pipeline),
#                          and a cut in there would show the next shot
WORD_GUARD = 0.05        # ...and the pad stops this far before the next word he says
CLOCK_JUMP = 3.0         # a cut the game clock moves this much across is a jump to another moment, not a reaction
#                          (a courtside shot of the next possession, 3:55 then 3:26, read as a close-up)
CLOCK_SIDE = 0.2         # ...read this far each side of the cut
LOOK_PAST = 5.0          # a clip's end may move this far past where it was found: the announcer's follow-ups and
#                          the cut after them (on an NBA compilation a follow-up on a scorer's 40 points began 0.9 s past the end,
#                          an analyst's line about a three ran on 4.2 s past it)
TRIP_MOST = 30.0         # a trip to the line is over within this of a free throw (two attempts and the shooter's
#                          close-ups between them: 9-12 s on an NBA compilation)
KEEP_END = {"game_winner", "game_end"}   # their celebration on the court is the point: the end stays

# What a clip opened on, for the log.
OPENED_ON = {"closeup": "a close-up", "crowd": "the crowd", "graphic": "a graphic", "replay": "a replay"}


def _frames(video_path, t0: float, t1: float, size: tuple[int, int], fps: float):
    """Every frame of [t0, t1) of the video at `size`, BGR. Resampled to
    `fps`, so the n-th frame is at n / fps even in a variable-frame-rate
    video."""
    import numpy as np

    from core.binaries import ffmpeg

    w, h = size
    cmd = [ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{max(0.0, t1 - t0):.3f}",
           "-i", str(video_path), "-an", "-sn", "-dn", "-vf", f"fps={fps:.6f},scale={w}:{h}:flags=area",
           "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    size_b = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size_b)
            if len(buf) < size_b:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()


def probe(video_path, t0: float, t1: float, fps: float, model=None, bug_box=None, scale: float = 1.0,
          camera: dict | None = None) -> list:
    """The camera shots of [t0, t1) of the video, classified, on the video's
    clock (crop/model.Shot). `bug_box`: the score bug's (left, top, right,
    bottom) in frame fractions, for telling replays; `scale`: how tall the
    game camera shows the players (reactions.people_scale, Sample.scale).
    `camera`, when given, is filled with the camera's pan: "t" (each frame's
    time on the video's clock) and "travel" (frame widths it has panned since
    t0, the pan between frames from phase correlation; nothing across a cut)."""
    import dataclasses

    import cv2
    import numpy as np

    from sports.basketball.crop import observe, shots
    from sports.basketball.crop.model import FrameSignals, Person, Sample
    from sports.soccer.ball import detect

    d_gray, d_col, luma = [], [], []
    mafd, response = [0.0], [1.0]           # the jump-cut test's (crop/shots.jump_cuts)
    white, mid_d, edge_d = [0.0], [0.0], [0.0]  # the replay stinger's (crop/shots.stinger_onset)
    travel = [0.0]                              # the camera's pan (crop/shots.camera_moves_on)
    prev_look = None
    edge = np.ones((STINGER_SIZE[1], STINGER_SIZE[0]), bool)
    edge[STINGER_INNER] = False
    samples: list[Sample] = []
    hists, grays = [], []
    prev_small = prev_hist = prev_mid = None
    rows = slice(int(0.08 * JUMP_SIZE[1]), int(0.78 * JUMP_SIZE[1]))    # the score bug and the boards left out
    hann = cv2.createHanningWindow((JUMP_SIZE[0], rows.stop - rows.start), cv2.CV_32F)
    every = max(1, int(round(fps / PROBE_HZ)))
    for n, frame in enumerate(_frames(video_path, t0, t1, PROBE_SIZE, fps)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, TINY, interpolation=cv2.INTER_AREA).astype("float32")
        hsv = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
        hist = cv2.normalize(cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256]), None)
        d_gray.append(float(abs(small - prev_small).mean()) if prev_small is not None else 0.0)
        d_col.append(float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)) if prev_hist is not None else 0.0)
        luma.append(float(gray.mean()))
        mid = cv2.resize(gray, JUMP_SIZE, interpolation=cv2.INTER_AREA).astype("float32")
        if prev_mid is not None:
            mafd.append(float(abs(mid - prev_mid).mean()))
            (dx, _dy), resp = cv2.phaseCorrelate(prev_mid[rows], mid[rows], hann)
            response.append(float(resp) if mafd[-1] >= shots.JUMP_MAFD else 1.0)
            # The picture moved dx: the camera the other way (not across a cut).
            travel.append(travel[-1] + (-dx / JUMP_SIZE[0] if mafd[-1] < PAN_CUT and resp > PAN_SURE else 0.0))
        prev_small, prev_hist, prev_mid = small, hist, mid
        look = cv2.resize(gray, STINGER_SIZE, interpolation=cv2.INTER_AREA).astype("float32")
        if prev_look is not None:
            middle = look[STINGER_ROWS, STINGER_COLS]
            diff = abs(look - prev_look)
            mid_d.append(float(diff[STINGER_ROWS, STINGER_COLS].mean()))
            edge_d.append(float(diff[edge].mean()))
            white.append(float((middle > 225).mean()))
        prev_look = look
        if n % every == 0:
            people = []
            if model is not None:
                _balls, found = detect(model, frame, PROBE_SIZE[0])
                people = [Person(x, y, w, h) for x, y, w, h in found]
            samples.append(Sample(i=len(samples), frame=n, t=n / fps, people=people, scale=scale))
            hists.append(observe._court_hist(frame))
            grays.append(gray)
    if not d_gray:
        return []
    for s, share in zip(samples, observe.court_shares(hists, samples)):
        s.court = share
    if bug_box is not None:
        for s, up in zip(samples, observe.bug_flags(grays, bug_box)):
            s.bug = up
    sig = FrameSignals(fps=fps, width=PROBE_SIZE[0], height=PROBE_SIZE[1], d_gray=d_gray, d_col=d_col,
                       pan_dx=[0.0] * len(d_gray), pan_dy=[0.0] * len(d_gray), luma=luma)
    cuts, unsure = shots.find_cuts(sig), []
    cuts = sorted(cuts + shots.jump_cuts(mafd, response, fps, [c.frame for c in cuts], unsure) + unsure,
                  key=lambda c: c.frame)
    if camera is not None:
        camera["t"] = [t0 + n / fps for n in range(len(travel))]
        camera["travel"] = travel
    found = shots.classify_all(shots.split(len(d_gray), fps, cuts), samples)
    # A replay stinger before a cut: the replay starts where its logo appears.
    for k in range(1, len(found)):
        onset = shots.stinger_onset(white, mid_d, edge_d, fps, found[k].f0)
        if onset is not None:
            found[k] = dataclasses.replace(found[k], replay=True, evidence={
                **(found[k].evidence or {}), "replay_by": "stinger",
                "change_s": round((found[k].f0 - onset) / fps, 3)})
    marked = {c.frame for c in unsure}
    return [dataclasses.replace(s, t0=s.t0 + t0, t1=s.t1 + t0,
                                evidence={**(s.evidence or {}), **({"unsure_jump": True} if s.f0 in marked else {})})
            for s in found]


def _words(segments, t0: float, t1: float) -> list[tuple[float, float]]:
    out = []
    for seg in segments or []:
        for w in getattr(seg, "words", None) or []:
            if "start" in w and "end" in w and t0 - 1 <= w["start"] <= t1:
                out.append((float(w["start"]), float(w["end"])))
    return out


def _opened_on(found: list, start: float) -> str:
    """What the clip's first frame showed, in words."""
    s = next((s for s in found if s.t0 <= start + 0.05 < s.t1), None)
    if s is None:
        return "the court"
    return OPENED_ON.get("replay" if s.replay else s.kind, "the court")


def _clock_at(video_path, t: float, box: tuple) -> float | None:
    """The game clock on the bug at video time t, read as the scoreboard reads
    a keyframe (scorebug.pieces, scoreboard.parse_pieces), or None."""
    import cv2

    from analysis.game_text import _ocr
    from sports.basketball import scoreboard
    from sports.core import scorebug
    from video.capture import video_capture

    try:
        with video_capture(video_path, required=False) as cap:
            if cap is None:
                return None
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, img = cap.read()
        if not ok:
            return None
        return scoreboard.parse_pieces(scorebug.pieces(scorebug.crop(img, box), _ocr)).clock
    except Exception:
        return None


def _jumped(video_path, at: float, box) -> float | None:
    """How far the game clock jumps across a cut at `at` (CLOCK_SIDE each
    side), when more than CLOCK_JUMP; None when it reads on, or isn't read."""
    before, after = _clock_at(video_path, at - CLOCK_SIDE, box), _clock_at(video_path, at + CLOCK_SIDE, box)
    if before is not None and after is not None and abs(before - after) > CLOCK_JUMP:
        return round(before - after, 1)
    return None


def _settle(video_path, found: list, box) -> list:
    """The shots with each cut the jump test was unsure of (a camera pan:
    crop/shots.JUMP_UNSURE) settled by the game clock: a jump to another
    moment when it jumps across it (evidence "clock_jump"), else no cut at
    all, its two shots one again."""
    import dataclasses

    out: list = []
    for s in found:
        if out and (s.evidence or {}).get("unsure_jump"):
            jump = _jumped(video_path, s.t0, box) if box is not None else None
            if jump is None:
                out[-1] = dataclasses.replace(out[-1], f1=s.f1, t1=s.t1)
                continue
            s.evidence = {**(s.evidence or {}), "clock_jump": jump}
        out.append(s)
    return out


def _clock_jumps(video_path, found: list, start: float, t: float, end: float, box) -> None:
    """Marks the cuts the game clock jumps across (evidence "clock_jump": how
    far), read only where it matters, about a second each: every cut between
    the clip's start and the play (another moment of the game there: the
    clip starts after it, crop/shots.lead_in_start), and each cut after the
    play from the court to what looks like a reaction (a jump to another
    moment: the play is over, crop/shots.play_over)."""
    from sports.basketball.crop import shots

    if box is None:
        return
    for prev, s in zip(found, found[1:]):
        before = start < s.t0 <= t - shots.MOMENT_BACK
        after = t + shots.CUT_AFTER <= s.t0 < end and prev.gameplay and not s.gameplay and not s.replay
        if (before or after) and (s.evidence or {}).get("clock_jump") is None:
            jump = _jumped(video_path, s.t0, box)
            if jump is not None:
                s.evidence = {**(s.evidence or {}), "clock_jump": jump}


def _look_to(c, duration: float | None) -> float:
    """How far a clip's look runs when only its stop is wanted: PAD_LOOK
    past its end, within the video."""
    return min(c.end + PAD_LOOK, duration) if duration else c.end + PAD_LOOK


def _reach(c, duration: float | None, trip: tuple | None = None) -> float:
    """How far a clip's end may move: LOOK_PAST past it, or a free throw's
    trip to the line, when that is over later, within the video."""
    to = max(c.end + LOOK_PAST, trip[2] if trip else 0.0)
    return min(to, duration) if duration else to


def _trip(trips: dict, t: float, kind: str) -> tuple[float, float, float] | None:
    """A free throw's trip to the line (scoreboard.Scoreboard.clock_stopped),
    when known and over within TRIP_MOST of it."""
    trip = next((v for f, v in trips.items() if abs(f - t) < 0.5), None)
    if trip is None or trip[2] - t > TRIP_MOST:
        return None
    return trip


def _stop_before(c, found: list, end: float, segments=None) -> None:
    """The latest a clip may run to: CUT_GUARD before the next shot starts (a
    dissolve's first frame), or WORD_GUARD before the next word he says,
    whichever is sooner, saved in its scores as `sport_stop` when within
    PAD_LOOK of its end: the render's pad stops there (core/pipeline._stop_by),
    showing no frame of the next shot and playing no word of what the clip left
    out. None near leaves no stop."""
    from sports.basketball.crop import shots

    cut = min((s.t0 - float((s.evidence or {}).get("change_s") or 0) for s in found[1:]
               if s.t0 - float((s.evidence or {}).get("change_s") or 0) > end - 0.01), default=None)
    word = min((float(w["start"]) for seg in segments or () for w in getattr(seg, "words", None) or ()
                if "start" in w and end - 0.01 <= float(w["start"]) <= end + PAD_LOOK), default=None)
    stops = ([cut - shots.CUT_GUARD] if cut is not None else []) + ([word - WORD_GUARD] if word is not None else [])
    scores = dict(getattr(c, "subscores", None) or {})
    stop = min(stops) if stops else None
    if stop is not None and stop - end <= PAD_LOOK:
        scores["sport_stop"] = max(end, math.floor(stop * 100) / 100)
    else:
        scores.pop("sport_stop", None)
    c.subscores = scores


def _end_limit(c, t: float, candidates: list, baskets: list[float], duration: float | None) -> float | None:
    """How far a clip's end may be pushed to keep it long enough: never past
    the video's end, into the next basket's play (that basket - 1 s), or
    past the start of the next clip."""
    from sports.basketball.crop.shots import PREV_BASKET_GAP

    limits = [duration] if duration else []
    later = [b for b in baskets if b > t + 0.5]
    if later:
        limits.append(min(later) - PREV_BASKET_GAP)
    starts = [o.start for o in candidates if o is not c and o.start > c.start]
    if starts:
        limits.append(min(starts))
    return min(limits) if limits else None


def _said(segments, t0: float, t1: float) -> tuple[list[tuple[float, float]], list[float]]:
    """The announcer's sentences around [t0, t1], and where he draws breath
    (crop/shots.sentences, crop/shots.breaths)."""
    from sports.basketball.crop import shots

    near = [(seg.start, seg.end, getattr(seg, "words", None) or []) for seg in segments or []
            if getattr(seg, "end", 0) >= t0 - 2 and getattr(seg, "start", 0) <= t1 + 2]
    return shots.sentences(near), shots.breaths(near)


def refine_windows(candidates: list, video_path, segments, config: dict, baskets: list[float], *,
                   bug_box=None, model_name: str = "yolov8n.pt", free_throws=(), final=(),
                   people_scale: float = 1.0) -> list:
    """Move each basketball clip's start onto the court run that holds its
    play, and its end to where the announcer is done with the play (see the
    module's notes). `baskets` are the confirmed baskets' times: a clip never
    starts before the previous basket + 1 s nor grows into the next. `bug_box`
    is the score bug's box (replays), or a function of the time to it (the
    bug moved: scoreboard.Scoreboard.box_at), `model_name` the person detector's
    weights, `free_throws` the baskets worth one point, each a time or (its
    time, its trip to the line: scoreboard.Scoreboard.clock_stopped), and
    `final` those of the baskets that end the game, which keep their ends
    (the celebration), and `people_scale` how tall the game camera shows the
    players (reactions.people_scale: its shots are told apart with it). Every clip, these and the rest (reactions, replays),
    gets its `sport_stop` (_stop_before) for the render's pad. Edits the
    candidates in place and returns them."""
    import sports
    from core import cancel
    from sports.basketball.crop import observe, shots
    from sports.soccer.ball import _model

    clips_cfg = (config or {}).get("clips") or {}
    min_len = float(clips_cfg.get("min_duration") or 0) or None
    trips = {float(f[0]) if isinstance(f, (tuple, list)) else float(f):
             (tuple(f[1]) if isinstance(f, (tuple, list)) and f[1] else None) for f in free_throws}
    scoring = set(sports.spec("basketball").get("scoring_events") or ())
    todo = []
    for c in candidates:
        s = getattr(c, "subscores", None) or {}
        t, kind = s.get("sport_t"), str(s.get("sport_event") or "")
        if not isinstance(t, (int, float)) or not kind or kind.endswith("_reaction") or kind == "big_moment":
            continue
        if s.get("sport_replay") or not c.start <= t <= c.end:
            continue
        do_start = t - c.start >= MIN_BEFORE
        do_end = kind not in KEEP_END and not any(abs(t - f) < 0.5 for f in final)
        if do_start or do_end:
            todo.append((c, float(t), kind, do_start, do_end))
    if not candidates:
        return candidates
    try:
        info = observe.probe(video_path)
        fps = float(info.get("fps") or 0) or 30.0
        duration = float(info.get("duration") or 0) or None
        model = _model(model_name)
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      (clip starts and ends: couldn't look at the video: {e})")
        return candidates
    starts = ends = 0
    for c, t, kind, do_start, do_end in todo:
        cancel.check_active()
        box = bug_box(t) if callable(bug_box) else bug_box
        try:
            trip = _trip(trips, t, kind)
            reach = _reach(c, duration, trip)
            camera: dict = {}
            found = probe(video_path, c.start, min(reach + PAD_LOOK, duration) if duration else reach + PAD_LOOK,
                          fps, model, box, people_scale, camera)
        except cancel.CancelledError:
            raise
        except Exception as e:
            print(f"      (clip {c.start:.0f}s: couldn't look at it: {e})")
            continue
        found = _settle(video_path, found, box)
        _clock_jumps(video_path, found, c.start, t, reach, box)
        prev = max((b for b in baskets if b < t - 0.5), default=None)
        limit = _end_limit(c, t, candidates, baskets, duration)
        said, breath = _said(segments, c.start, reach + shots.FINISH_MAX)
        # Finishing his sentence about the play may run into the next clip
        # (clips may overlap): only the next basket's play and the video's end stop it.
        finish = _end_limit(c, t, [], [b for b in baskets if trip is None or b > trip[2]], duration)
        start, end, notes, at_cut = c.start, c.end, [], False
        words = [w for seg in segments or [] for w in getattr(seg, "words", None) or []
                 if c.start - 1 <= w.get("start", -1e9) <= reach + shots.FINISH_MAX]
        if do_start:
            lead = shots.lead_in_start(found, t, c.start, c.end, prev, words=_words(segments, c.start, t), fps=fps)
            if lead.start > c.start + 0.05:
                start = lead.start
                notes.append(f"starts {start - c.start:.1f}s later, on the court (it opened on "
                             f"{_opened_on(found, c.start)}): {lead.reason}")
        one = kind == "free_throw" or any(abs(t - f) < 0.5 for f in trips)
        if do_end:
            # The court shot the camera is in once the basket is in (a cut just
            # after it, too soon to end the play, moves on to the next one).
            play = next((s for s in found if s.t0 <= t + shots.CUT_AFTER < s.t1), None)
            moves = (shots.camera_moves_on(camera.get("t") or [], camera.get("travel") or [], t, play.t0, play.t1)
                     if play is not None and play.gameplay else None)
            done = shots.clip_end(found, t, c.end, said, basket=kind in scoring, limit=finish, fps=fps,
                                  breath=breath, free_throw=one, trip=trip, look=reach, camera_on=moves)
            if done.ceiling is not None:
                limit = done.ceiling if limit is None else min(limit, done.ceiling)
            if abs(done.end - c.end) > 0.05:
                end, forward = shots.off_words(done.end, words, done.ceiling, t + shots.CUT_AFTER)
                at_cut = "finishes" not in done.reason or forward
                notes.append(f"ends {abs(c.end - end):.1f}s {'sooner' if end < c.end else 'later'}: {done.reason}")
        lo = max(c.start, prev + shots.PREV_BASKET_GAP) if prev is not None else c.start
        start, fitted_end, fitted = shots.fit_length(found, start, end, lo, min_len, said, limit, fps, setup=one)
        if fitted:
            notes.append(fitted)
            if fitted_end != end:
                # Grown to the shortest length: off any word, never past the cut.
                fitted_end, forward = shots.off_words(fitted_end, words, limit)
                at_cut = forward or (limit is not None and fitted_end >= limit - 1e-6)
            end = fitted_end
        if not notes:
            _stop_before(c, found, c.end, segments)
            continue
        print(f"      Clip {c.start:.0f}s-{c.end:.0f}s " + "; ".join(notes))
        # The cutter's hundredths (video/cutter.py seeks to them): the start
        # up, never onto the frame before the court; an end at a cut, or
        # before a new sentence, down, never onto what comes after.
        new_start = math.ceil(start * 100 - 1e-6) / 100
        new_end = (math.floor(end * 100 + 1e-6) if at_cut else math.ceil(end * 100 - 1e-6)) / 100
        starts += new_start > c.start + 0.05
        ends += abs(new_end - c.end) > 0.05
        c.start, c.end = new_start, new_end
        _stop_before(c, found, new_end, segments)
    # The rest keep their windows: only where the next shot starts after the
    # end is looked for, in their last second and the pad.
    looked = {id(c) for c, *_ in todo}
    for c in candidates:
        if id(c) in looked:
            continue
        cancel.check_active()
        box = bug_box(c.end) if callable(bug_box) else bug_box
        try:
            found = _settle(video_path, probe(video_path, max(c.start, c.end - 1.0), _look_to(c, duration), fps,
                                              model, box, people_scale), box)
        except cancel.CancelledError:
            raise
        except Exception:
            continue
        _stop_before(c, found, c.end, segments)
    if starts or ends:
        print(f"      Clip starts and ends: {starts} of {len(todo)} start on the court later, "
              f"{ends} end when the play is done")
    return candidates
