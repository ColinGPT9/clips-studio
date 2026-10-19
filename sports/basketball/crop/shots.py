"""Camera cuts, what each shot shows, and where a clip's lead-in should start.

Cuts come from FrameSignals (one value per frame), so they are frame-exact:
the planner holds the old crop to the last frame of a shot and snaps on the
first frame of the next, and a clip can start on the court's first frame
after a close-up. Shot kinds come from the samples: who the detector saw and
how tall, how much of the picture is the floor, and whether the score bug is
up (it goes away during replays).

Pure Python (see model.py).
"""

from __future__ import annotations

import statistics
from dataclasses import replace

from sports.basketball.crop.model import Cut, FrameSignals, Sample, Shot

# ---- cuts -------------------------------------------------------------------------------------

BASE_REACH = 15          # frames each side of k whose median d_gray is the baseline b(k)
BASE_FLOOR = 2.0         # the baseline never drops below this, so a still picture's noise is no cut
CUT_GRAY = 18.0          # a cut changes the gray thumbnail at least this much...
CUT_RATIO = 3.0          # ...and at least this many times the baseline (a whip pan raises both)...
CUT_COL = 0.20           # ...and the colours at least this much (Bhattacharyya distance)
CUT_GRAY_STRONG = 40.0   # or a gray change this large...
CUT_COL_STRONG = 0.30    # ...with this much colour change, whatever the baseline
FLASH_MAX = 3            # a camera flash lasts at most this many frames
FLASH_JUMP = 12.0        # luma must jump this much (0-255) on the first frame for the flash test to apply
FLASH_MATCH = 0.3        # ...and a frame after it is back to the old luma within this share of the jump
# Without luma, a flash is told by a second change at least this share of the first within
# FLASH_MAX frames: a camera that cuts away settles on its new picture, a flash snaps back. Gray
# differences alone can't show the change goes BACK to the old picture, so a second cut that
# soon is taken for a flash too; it would make a shot shorter than MIN_SHOT anyway.
FLASH_BACK = 0.6
TRANS_WINDOW = 10        # frames in a transition window (a replay wipe, a dissolve)
TRANS_GRAY = 120.0       # the window's summed gray change...
TRANS_COL = 0.35         # ...and summed colour change, with no hard cut in it
TRANS_EDGE = 6.0         # a transition's first and last frames change at least this much (quiet frames trimmed)
MIN_SHOT = 0.3           # seconds; a cut closer than this to the previous one merges into a transition

# ---- shot kinds -------------------------------------------------------------------------------

TALL = 0.36              # a person this tall (share of the frame's height) is near the camera, not on the floor
FLOOR_MIN = 0.08         # a floor player is at least this tall...
FLOOR_REL = 0.5          # ...and at least this share of the tallest person
MEDIUM_MIN = 0.25        # a medium shot's tallest person, at least
CROWD_PERSON = 0.2       # a crowd shot has CROWD_COUNT people at least this tall...
CROWD_COUNT = 4
COURT_MIN = 0.22         # ...and less floor colour than this in its lower middle
CLOSEUP_MAX_BIG = 2      # a close-up has at most this many people taller than TALL...
CLOSEUP_MIN = 0.45       # ...and when its tallest fills the frame less than this while the hoop or a sure ball
GAME_SIGN = 0.5          #    (this sure) shows in it, it is the game seen from a near camera, framed on the play
#                          (medium): a defender in front of the camera behind the basket at a free throw was
#                          taken for a close-up, and the crop held on him while the shot went up. A crowd
#                          shot just over TALL shows neither, and stays as it was
FLOOR_COUNT = 3          # a wide shot has at least this many floor players
GRAPHIC_EMPTY = 0.7      # a shot with nobody in this share of its samples is a graphic
REPLAY_OWN = 0.2         # a replay shows the bug in less than this share of its samples...
REPLAY_OTHERS = 0.6      # ...while the clip's other gameplay samples show it in at least this share
REPLAY_MIN_OTHERS = 3    # and there are at least this many of those to compare with
BUG_KEPT = 0.5           # a shot showing the bug in this share of its samples is live, dissolves or not (on an
#                          NBA compilation two close-ups after a clutch shot were dissolved in and out with the
#                          bug up throughout: no replay, and the announcer's line on the play followed)

# ---- lead-in ----------------------------------------------------------------------------------

PREV_BASKET_GAP = 1.0    # a clip never starts before the previous basket plus this
MOMENT_BACK = 0.5        # the run that holds the play is the one holding the moment minus this
#                          (the score bug dates a basket to about +-0.65 s)
WORD_SNAP = 0.35         # a word under way at the start, ending within this, moves the start to its end
SHORT_RUN = 2.0          # a cutaway the clip opens on, leaving less court than this before the moment, stays

# ---- the end: where the play is over, and where the announcer is done with it -----------------

KEEP_AFTER = 2.0         # a clip keeps at least this much after the moment: the ball going in, the first reaction
CUT_AFTER = 1.5          # ...unless the broadcast cuts away sooner: a cut this long after the moment can end the
#                          play (the bug dates a basket to +-0.65 s: on an NBA compilation 9 clips cut to the next
#                          possession 1.97-2.8 s after its time, and ran on into it)
CUT_GUARD = 0.05         # a clip stays this far from a cut at either end: the look's frames and the render's agree
#                          to about a frame (+-0.03 s on three rendered clips), and one frame of the next shot shows
FINISH_MAX = 4.0         # ...and he finishes what he says about the play by this long after that,
FINISH_GRACE = 1.0       # ...or this much later, a sentence under way then (an analyst's "...tip it to himself
#                          with such grace" ran 0.5 s past it on an NBA compilation)
SHORT_TAIL = 0.6         # a shot after the play shown this short at a clip's end is a flash of it: left out (the
#                          review's flashes on an NBA compilation were 0.35-0.59 s; the reactions of 1-2 s the owner
#                          wants kept: leaving them out made his clips too short)
WORD_SAID = 0.5          # a last word this much said at the cut stays (the transcript's word ends run long:
#                          "bucket." timed 1.1 s; dropping them lost "turnaround", "man", "night" before a cut)
BREATH = 0.25            # ...and the clip ends this long after his last word
SAID_WITH = 5.5          # in one court shot, a sentence he begins later than this after the moment is his next
#                          thought (on an NBA compilation the follow-ups ran to 5.5 s, the next play's talk from
#                          5.9 s; over a cutaway, and before the broadcast cuts away, all of it is about the play)
SAID_WITH_STOP = 2.5     # ...after a block or a steal, sooner: the other team has the ball at once ("Comes the other
#                          way." 2.8 s after a block)
CUT_HEARD = 0.5          # a sentence the cut would leave less of than this heard is left out ("There is|",
#                          "Timeout, San|" on an NBA compilation), the clip ending just before it
PAUSE = 0.5              # a silence this long between words ends a sentence too (the transcript ran some on for
#                          13 s with no stop: "...gets inside left it's good ... the...")
BREATH_GAP = 0.25        # a run-on he can't finish ends at a gap between words at least this long
SENTENCE_END = (".", "!", "?")
FREE_THROW_AFTER = 2.0   # a free throw is over this long after it drops: the close-up after it is the next one's
#                          setup (on an NBA compilation a game-tying one lingered 4 s on it, then cut before the shot)

# Jump cuts the frame-difference cut test misses: a condensed broadcast cuts from
# one wide shot of the court straight to another of a later possession, from
# nearly the same camera. Measured on 14 such cuts of an NBA compilation (the
# mean gray difference at 128x72 jumps to 27-61, 9-55 times the frames around
# it, with no coherent camera motion: phase-correlation response 0.004-0.095),
# and 12 more, most during a camera pan (3-36 times, response up to 0.53, the
# game clock jumping across each); the play's own spikes are the arena's
# strobes and dropped frames, with the picture merely shifted or brightened
# (response 0.89-0.94).
JUMP_MAFD = 20.0         # a frame this different from the one before...
JUMP_RATIO = 3.0         # ...this many times the frames around it...
JUMP_WINDOW = 16         # ...(this many on each side, the still ones left out)...
JUMP_RESPONSE = 0.6      # ...with the camera's motion this incoherent, is a cut
JUMP_UNSURE = 0.85       # ...and up to this, one the game clock must confirm (a jump during a pan scored 0.62)


# ---- cuts -------------------------------------------------------------------------------------


def find_cuts(sig: FrameSignals) -> list[Cut]:
    """The camera cuts in a clip, frame-exact: each Cut's `frame` is the first
    frame of the new shot. Hard cuts first (a camera flash is not one), then
    transitions (replay wipes, dissolves) where no single frame is a cut, then
    cuts too close together for a shot between them merged into one transition."""
    n, fps = sig.frames, sig.fps
    gray, col = sig.d_gray, sig.d_col
    luma = sig.luma if len(sig.luma) == n else None
    hard: list[int] = []
    flash: set[int] = set()
    k = 1
    while k < n:
        if _is_hard(gray, col, k):
            back = _flash_end(gray, luma, k)
            if back is None:
                hard.append(k)
            else:
                flash.update(range(k, back + 1))
                k = back + 1
                continue
        k += 1
    cuts = [Cut(frame=f, t=f / fps) for f in hard]
    cuts += _transitions(gray, col, set(hard) | flash, fps)
    cuts.sort(key=lambda c: c.frame)
    return _merge_short(cuts, fps, gray)


def _at(values: list[float], k: int) -> float:
    return values[k] if 0 <= k < len(values) else 0.0


def _baseline(gray: list[float], k: int) -> float:
    """The median change around frame k (k itself left out), never below BASE_FLOOR."""
    around = gray[max(0, k - BASE_REACH):k] + gray[k + 1:k + 1 + BASE_REACH]
    return max(BASE_FLOOR, statistics.median(around)) if around else BASE_FLOOR


def _is_hard(gray: list[float], col: list[float], k: int) -> bool:
    g, c = gray[k], _at(col, k)
    if g >= CUT_GRAY_STRONG and c >= CUT_COL_STRONG:
        return True
    return g >= CUT_GRAY and c >= CUT_COL and g >= CUT_RATIO * _baseline(gray, k)


def _flash_end(gray: list[float], luma: list[float] | None, k: int) -> int | None:
    """The frame the picture comes back on when the change at k is a flash
    (a camera flash, a strobe), else None."""
    last = min(len(gray) - 1, k + FLASH_MAX)
    if luma is not None:
        jump = abs(luma[k] - luma[k - 1])
        if jump < FLASH_JUMP:
            return None
        for j in range(k + 1, last + 1):
            if abs(luma[j] - luma[k - 1]) <= FLASH_MATCH * jump:
                return j
        return None
    for j in range(k + 1, last + 1):
        if gray[j] >= FLASH_BACK * gray[k]:
            return j
    return None


def _transitions(gray: list[float], col: list[float], blocked: set[int], fps: float) -> list[Cut]:
    """Spans of steady change with no hard cut in them: every TRANS_WINDOW-frame
    window that changes enough, overlapping windows joined, quiet end frames
    trimmed. Each becomes one Cut on the frame after its span."""
    n = len(gray)
    spans: list[list[int]] = []
    for s in range(1, n - TRANS_WINDOW + 1):
        window = range(s, s + TRANS_WINDOW)
        if any(f in blocked for f in window):
            continue
        if sum(gray[f] for f in window) < TRANS_GRAY or sum(_at(col, f) for f in window) < TRANS_COL:
            continue
        if spans and s <= spans[-1][1] + 1:
            spans[-1][1] = s + TRANS_WINDOW - 1
        else:
            spans.append([s, s + TRANS_WINDOW - 1])
    out = []
    for a, b in spans:
        while a < b and gray[a] < TRANS_EDGE:
            a += 1
        while b > a and gray[b] < TRANS_EDGE:
            b -= 1
        if b + 1 < n:
            out.append(Cut(frame=b + 1, t=(b + 1) / fps, kind="transition", span=(a, b)))
    return out


def _first_changed(cut: Cut) -> int:
    return cut.span[0] if cut.span else cut.frame


def _merge_short(cuts: list[Cut], fps: float, gray: list[float] | None = None) -> list[Cut]:
    """A cut less than MIN_SHOT after the previous one leaves no real shot
    between them: the two become one transition over the frames in between.
    Except steady change that ends on a strong hard cut (`gray` at least
    CUT_GRAY_STRONG on its frame): that is the old shot moving, a fast pan,
    then the cut, and the picture changes at the cut. A dissolve ends with
    the picture all but the new shot's, never with a jump (on an NBA
    compilation a pan over the crowd was taken for a 0.8 s dissolve, and the
    clip ending before it lost the announcer's last words)."""
    out: list[Cut] = []
    for c in cuts:
        if out and _first_changed(c) - out[-1].frame < MIN_SHOT * fps:
            prev = out.pop()
            frame = max(prev.frame, c.frame)
            hard = next((x for x in (prev, c) if x.kind == "hard" and x.frame == frame), None)
            other = c if hard is prev else prev
            if (gray is not None and hard is not None and other.kind == "transition"
                    and _at(gray, frame) >= CUT_GRAY_STRONG):
                out.append(hard)
                continue
            c = Cut(frame=frame, t=frame / fps, kind="transition",
                    span=(min(_first_changed(prev), _first_changed(c)), frame - 1))
        out.append(c)
    return out


# ---- shots ------------------------------------------------------------------------------------


def split(frames: int, fps: float, cuts: list[Cut]) -> list[Shot]:
    """The shots between the cuts, kind left at "wide" until classify(). A
    transition's frames end the shot before it. `evidence` notes the kind of
    cut on each side ("in", "out": hard | transition | None at the clip's ends),
    which the replay test reads."""
    kinds: dict[int, str] = {}
    began: dict[int, int] = {}
    for c in cuts:
        if 0 < c.frame < frames:
            kinds[c.frame] = "transition" if kinds.get(c.frame) == "transition" else c.kind
            began[c.frame] = min(began.get(c.frame, c.frame), _first_changed(c))
    edges = [0, *sorted(kinds), frames] if frames > 0 else []
    # "change_s": how long before its first frame the cut into a shot began
    # changing the picture (a dissolve's frames), for an end that must show
    # none of it.
    return [Shot(id=i, f0=f0, f1=f1, t0=f0 / fps, t1=f1 / fps,
                 evidence={"in": kinds.get(f0), "out": kinds.get(f1),
                           "change_s": round((f0 - began.get(f0, f0)) / fps, 4)})
            for i, (f0, f1) in enumerate(zip(edges, edges[1:]))]


def samples_in(shot: Shot, samples: list[Sample]) -> list[Sample]:
    return [s for s in samples if shot.f0 <= s.frame < shot.f1]


def classify_all(shots: list[Shot], samples: list[Sample]) -> list[Shot]:
    """classify() for every shot of a clip."""
    return [classify(shot, samples_in(shot, samples), samples) for shot in shots]


def _look(sample: Sample) -> tuple[float, int, int, int] | None:
    """One sample's people: the tallest's height, how many look like floor
    players, how many are taller than TALL, and how many are at least
    CROWD_PERSON tall, each height over the camera's scale (Sample.scale).
    None when nobody was seen."""
    hs = [p.h / sample.scale for p in sample.people]
    if not hs:
        return None
    tallest = max(hs)
    floor = sum(1 for h in hs if FLOOR_MIN <= h <= TALL and h >= FLOOR_REL * tallest)
    return tallest, floor, sum(1 for h in hs if h >= TALL), sum(1 for h in hs if h >= CROWD_PERSON)


def _gameplay_look(sample: Sample) -> bool:
    look = _look(sample)
    return (look is not None and look[0] < TALL and look[1] >= FLOOR_COUNT
            and (sample.court is None or sample.court >= COURT_MIN))


def _share(flags: list[bool]) -> float:
    return sum(1 for f in flags if f) / len(flags)


def classify(shot: Shot, samples: list[Sample], clip_samples: list[Sample]) -> Shot:
    """The shot with its kind (wide | medium | closeup | crowd | graphic), its
    replay flag and the evidence for both, from medians over its samples.
    `samples` are the shot's (any outside it are ignored); `clip_samples` are
    the whole clip's, for the replay test."""
    own = samples_in(shot, samples)
    looks = [lk for lk in (_look(s) for s in own) if lk is not None]
    courts = [s.court for s in own if s.court is not None]
    bugs = [bool(s.bug) for s in own if s.bug is not None]
    ev = {
        "n": len(own),
        "empty": round(1 - len(looks) / len(own), 3) if own else None,
        "tallest": statistics.median(lk[0] for lk in looks) if looks else None,
        "floor_n": statistics.median(lk[1] for lk in looks) if looks else 0,
        "big_n": statistics.median(lk[2] for lk in looks) if looks else 0,
        "mid_n": statistics.median(lk[3] for lk in looks) if looks else 0,
        "court": statistics.median(courts) if courts else None,
        "bug_share": round(_share(bugs), 3) if bugs else None,
        "game_sign": any(r.score >= GAME_SIGN for s in own for r in s.rims)
        or any(b.conf >= GAME_SIGN for s in own for b in s.balls),
    }
    # A shot no sample landed in has nothing to say against gameplay.
    kind = _kind(ev) if own else shot.kind
    evidence = {**shot.evidence, **ev}
    replay, why = _replay(shot, own, clip_samples, evidence)
    if why:
        evidence["replay_by"] = why
    return replace(shot, kind=kind, replay=replay, evidence=evidence)


def _kind(ev: dict) -> str:
    """The table in the spec. Crowd is tested before close-up: a crowd shot
    with one or two big fans in front would pass the close-up rule too, and
    "crowd" is the more specific of the two (both are cutaways to the planner)."""
    tallest, floor_n, court = ev["tallest"], ev["floor_n"], ev["court"]
    if tallest is None or (ev["empty"] or 0) >= GRAPHIC_EMPTY:
        return "graphic"
    low_court = court is not None and court < COURT_MIN
    if (tallest >= TALL and ev["mid_n"] >= CROWD_COUNT and low_court) or (floor_n >= FLOOR_COUNT and low_court):
        return "crowd"
    near_game = tallest < CLOSEUP_MIN and ev.get("game_sign", False)
    if tallest >= TALL and ev["big_n"] <= CLOSEUP_MAX_BIG:
        return "medium" if near_game else "closeup"
    if tallest < TALL and floor_n >= FLOOR_COUNT and not low_court:
        return "wide"
    if MEDIUM_MIN <= tallest < TALL and not low_court:
        return "medium"
    if floor_n >= 2:
        return "wide"
    # One or two small players on the floor (a breakaway, a free throw) is
    # still the game, unless the floor's colour says otherwise.
    if tallest < TALL and floor_n >= 1 and not low_court:
        return "wide"
    return ("medium" if near_game else "closeup") if tallest >= TALL else "graphic"


def _replay(shot: Shot, own: list[Sample], clip_samples: list[Sample], evidence: dict) -> tuple[bool, str]:
    """A replay: wiped in and out (a transition on both sides) with the score
    bug not seen up through it, or the score bug gone while the rest of the
    clip's gameplay shows it. "The clip's other gameplay" is judged per
    sample (few people, none near the camera, the floor in view), since the
    other shots aren't classified yet."""
    mine = [bool(s.bug) for s in own if s.bug is not None]
    if evidence.get("in") == "transition" and evidence.get("out") == "transition":
        if mine and _share(mine) >= BUG_KEPT:
            return False, ""
        return True, "transitions"
    if not mine or _share(mine) >= REPLAY_OWN:
        return False, ""
    others = [bool(s.bug) for s in clip_samples
              if not (shot.f0 <= s.frame < shot.f1) and s.bug is not None and _gameplay_look(s)]
    if len(others) >= REPLAY_MIN_OTHERS and _share(others) >= REPLAY_OTHERS:
        return True, "bug"
    return False, ""


# ---- lead-in ----------------------------------------------------------------------------------


class LeadIn(tuple):
    """What lead_in_start decided. It unpacks as the pair (start, reason); `end`
    is the end the clip needs, the old end unless the length guard asks for more."""

    end: float

    def __new__(cls, start: float, reason: str, end: float):
        self = super().__new__(cls, (start, reason))
        self.end = end
        return self

    @property
    def start(self) -> float:
        return self[0]

    @property
    def reason(self) -> str:
        return self[1]


def _fps_of(shots: list[Shot]) -> float:
    longest = max(shots, key=lambda s: s.t1 - s.t0)
    return (longest.f1 - longest.f0) / (longest.t1 - longest.t0) if longest.t1 > longest.t0 else 30.0


def _index_at(shots: list[Shot], t: float) -> int | None:
    for i, s in enumerate(shots):
        if s.t0 <= t < s.t1:
            return i
    return len(shots) - 1 if shots and t >= shots[-1].t1 else None


def _past_cutaways(shots: list[Shot], t: float, frame: float = 0.0) -> tuple[float, list[str]]:
    """t, moved to the end of any non-gameplay shot it falls in (and the next,
    and so on), plus `frame`, with the kinds it moved past."""
    passed: list[str] = []
    for s in shots:
        if s.t0 <= t < s.t1 and not s.gameplay:
            t = s.t1 + frame
            passed.append("replay" if s.replay else s.kind)
    return t, passed


def _in_cutaway(shots: list[Shot], t: float) -> bool:
    return any(s.t0 <= t < s.t1 and not s.gameplay for s in shots)


def lead_in_start(shots: list[Shot], moment_t: float, start: float, end: float,
                  prev_basket: float | None = None, min_len: float | None = None,
                  max_len: float | None = None, words: list[tuple[float, float]] | None = None,
                  fps: float | None = None) -> LeadIn:
    """Where a clip around a play should start: on this possession, as the
    broadcast shows it, not on another moment of the game or a cutaway. Used
    by leadin.py. All times are on one clock (source seconds): the shots',
    the moment's (when the basket went in), the clip's start and end, the
    previous basket's and the words' (start, end) pairs.

    1. The earliest start is the clip's, and never before the previous basket + 1 s.
    2. A cut before the play that the game clock jumps across (the shot's
       evidence "clock_jump", leadin reads it) is where this moment of the
       game begins in a condensed broadcast: the clip starts after the last
       one, + CUT_GUARD. A camera change the clock reads on across is the
       same possession and trims nothing (on an NBA compilation 31 of the 35
       cuts an earlier rule started on were jumps, 4 the same possession).
    3. Never on a close-up, crowd, graphic or replay at the opening: past it,
       + CUT_GUARD, when that leaves SHORT_RUN of court before the moment
       (else it is the play's own setup: "kept: short run").
    4. Never mid-word when the word ends within WORD_SNAP.
    5. Clips shorter than `min_len` report the end they need (`.end`, at most
       `max_len` after the start).

    CUT_GUARD is the shots' own uncertainty against the render's: a start
    right on a cut can open on a frame of the shot before. `fps` is the
    source's frame rate (taken from the shots when not given). Returns
    LeadIn: unpacks as (start, reason), with `.end`."""
    if not shots:
        return LeadIn(start, "kept: no shots", end)
    guard = max(1.0 / (fps or _fps_of(shots)), CUT_GUARD)
    lo = max(start, prev_basket + PREV_BASKET_GAP) if prev_basket is not None else start
    jumps = [s.t0 for s in shots[1:]
             if lo - 0.01 < s.t0 <= moment_t - MOMENT_BACK and (s.evidence or {}).get("clock_jump") is not None]
    if jumps:
        new, why = max(jumps) + guard, f"after the broadcast's jump from another moment at {max(jumps):.2f}"
    elif lo > start:
        new, why = lo, "previous basket + 1 s"
    else:
        new, why = start, "kept: the same possession"
    past, passed = _past_cutaways(shots, new, guard)
    if passed:
        if moment_t - past < SHORT_RUN:
            return LeadIn(start if new == start else new, "kept: short run", end)
        new, why = past, ("" if why.startswith("kept") else why + "; ") + "past " + ", ".join(passed)
    for w0, w1 in words or ():
        if w0 < new < w1 and w1 - new <= WORD_SNAP and not _in_cutaway(shots, w1):
            new, why = w1, why + "; word end"
            break
    need = end
    if min_len is not None and end - new < min_len:
        need = new + min_len if max_len is None else new + min(min_len, max_len)
        why += f"; end must extend to {need:.2f}"
    return LeadIn(new, why, need)


# ---- the end ----------------------------------------------------------------------------------


class End(tuple):
    """Where a clip ends, and why: unpacks as (end, reason). `ceiling`: the
    latest the end may go to (a frame before the cut that ends the play, or
    before the announcer's next thought), None when nothing bounds it."""

    ceiling: float | None

    def __new__(cls, end: float, reason: str, ceiling: float | None = None):
        self = super().__new__(cls, (end, reason))
        self.ceiling = ceiling
        return self

    @property
    def end(self) -> float:
        return self[0]

    @property
    def reason(self) -> str:
        return self[1]


def jump_cuts(mafd: list[float], response: list[float], fps: float, near: list[int] | None = None,
              unsure: list[Cut] | None = None) -> list[Cut]:
    """The jump cuts in a run of frames: `mafd[k]` the mean gray difference
    of frame k from k-1 (at 128x72), `response[k]` the phase-correlation
    response between them (0 when unknown). A frame JUMP_RATIO times as
    different as its neighbours (the still ones, a repeated frame, left out),
    at least JUMP_MAFD, with the camera's motion broken (response under
    JUMP_RESPONSE). Not within a third of a second of a cut in `near` (frame
    numbers) or of another found. Given `unsure`, the ones with a response
    from JUMP_RESPONSE to JUMP_UNSURE are put there, for the game clock to
    confirm (leadin)."""
    out: list[Cut] = []
    taken = list(near or ())
    for k in range(1, len(mafd)):
        if mafd[k] < JUMP_MAFD or response[k] >= (JUMP_UNSURE if unsure is not None else JUMP_RESPONSE):
            continue
        around = [d for d in mafd[max(1, k - JUMP_WINDOW):k] + mafd[k + 1:k + 1 + JUMP_WINDOW] if d > 0.5]
        base = statistics.median(around) if around else 0.5
        if mafd[k] < JUMP_RATIO * max(base, 0.5):
            continue
        if any(abs(k - f) < fps / 3 for f in taken):
            continue
        (out if response[k] < JUMP_RESPONSE else unsure).append(Cut(frame=k, t=k / fps, kind="hard"))
        taken.append(k)
    return out


# A network's replay stinger: its logo grows in the middle of the live picture,
# holds there while the game goes on around it, then fills the screen before
# the replay cuts in, about 0.9 s in all. The cut test sees only the replay's
# cut, and four clips of a team's own network ended on the logo.
STINGER_LOOK = 1.5       # seconds before a cut the stinger is looked for in
STINGER_WHITE = 0.25     # the middle's share of near-white pixels (the logo's letters; 0.00-0.03 before)...
STINGER_JUMP = 0.15      # ...up by at least this from its level 0.2-0.6 s before (the logo grows over 0.2 s)...
STINGER_SPIKE = 2.5      # ...as the middle changed at least this many times as much as the edges (it appeared)
STINGER_MIN = 0.3        # seconds the logo is held (its last frames, a wipe or a black frame, may be anything)...
STINGER_HOLD = 1.0       # ...the middle changing no more than the edges meanwhile (median): a logo held still
#                          over the moving game, not a white jersey turning into the middle of a close-up


def stinger_onset(white: list[float], mid: list[float], edge: list[float], fps: float, cut: int) -> int | None:
    """The frame a replay stinger starts on, before the cut at frame `cut`:
    the earliest frame from which the middle of the picture stays near white
    (STINGER_WHITE) for STINGER_MIN, up by STINGER_JUMP on its level 0.2 to
    0.6 s before, where something appeared in the middle (it changed
    STINGER_SPIKE times as much as the edges) and then held still (STINGER_HOLD:
    on a close-up, the back of a white jersey turning into the middle passed
    the rest, and moves with the person). The frames after the hold (the
    logo filling the screen, a grey wipe, a black frame) may be anything. A white
    jersey filling a close-up is white throughout, never a sudden jump (black
    and white both counted, a close-up of dark hair was "logo" throughout).
    None without one. `white`, `mid`, `edge`: per frame, from the frame before."""
    lo = max(1, cut - int(round(STINGER_LOOK * fps)))
    for j in range(lo, cut - int(round(STINGER_MIN * fps)) + 1):
        if min(white[j:j + int(round(STINGER_MIN * fps))], default=0.0) < STINGER_WHITE:
            continue
        before = sorted(white[max(0, j - int(round(0.6 * fps))):max(0, j - int(round(0.2 * fps)))])
        if not before or before[len(before) // 2] > white[j] - STINGER_JUMP:
            continue
        near = range(max(1, j - 6), min(cut, j + 4))
        spikes = [k for k in near if mid[k] >= STINGER_SPIKE * max(edge[k], 0.5)]
        if not spikes:
            continue
        held = sorted(mid[k] / max(edge[k], 0.5) for k in range(j + 3, min(cut, j + 3 + int(round(STINGER_MIN * fps)))))
        if not held or held[len(held) // 2] > STINGER_HOLD:
            continue
        return min(spikes[0], j)
    return None


def play_over(shots: list[Shot], moment_t: float, end: float, basket: bool = True) -> tuple[float, str]:
    """Where the camera says the play is over, on the shots' clock: the first
    cut at least CUT_AFTER after the moment that goes back to the court after
    a cutaway (the next possession), starts a replay or a graphic, or, after a
    basket only, moves from the court to another wide court shot (the next
    possession, or another part of the game). After a block or a steal the
    break the other way is the play, so that last cut doesn't count. And any
    cut the game clock jumps across (evidence "clock_jump", leadin reads it).
    The time is where the picture starts to change (a dissolve's first
    frame). (end, "") when there is no such cut."""
    for prev, s in zip(shots, shots[1:]):
        if s.t0 < moment_t + CUT_AFTER:
            continue
        if s.t0 >= end:
            break
        at = s.t0 - float((s.evidence or {}).get("change_s") or 0)    # where the picture starts to change
        if (s.evidence or {}).get("clock_jump") is not None:
            return at, "the broadcast jumps to another moment"
        if s.replay:
            return at, "a replay starts"
        if s.kind == "graphic":
            return at, "a graphic comes up"
        if s.gameplay and not prev.gameplay:
            return at, "back to the court after the reaction"
        if basket and s.gameplay and prev.gameplay and s.kind == "wide":
            return at, "the camera moves on to the next play"
    return end, ""


def sentences(segments) -> list[tuple[float, float]]:
    """The announcer's sentences as (start, end), from the transcript's
    segments, each (start, end, words): a segment ends a sentence (they end
    at a pause or a sentence's end, and never run over 15 s), and so does a
    word inside one that ends with . ! or ?. The words alone won't do: on an
    NBA broadcast's transcript whole minutes of them came with no stop."""
    out: list[tuple[float, float]] = []
    for seg_start, seg_end, words in segments or ():
        first = last = None
        for w in words or ():
            if "start" not in w or "end" not in w:
                continue
            if first is not None and float(w["start"]) - last >= PAUSE:
                out.append((first, last))
                first = None
            if first is None:
                first = float(w["start"])
            last = float(w["end"])
            if str(w.get("word", "")).strip().endswith(SENTENCE_END):
                out.append((first, last))
                first = None
        if first is not None:
            out.append((first, max(last, min(float(seg_end), last + PAUSE))))
        elif not any("start" in w for w in words or ()):
            out.append((float(seg_start), float(seg_end)))
    return sorted(out)


def breaths(segments) -> list[float]:
    """Where the announcer draws breath, from the transcript's segments as
    sentences() takes them: just after each word followed by a silence of
    BREATH_GAP or more, and at each segment's end."""
    out: list[float] = []
    for _seg_start, seg_end, words in segments or ():
        timed = [w for w in words or () if "start" in w and "end" in w]
        for w, nxt in zip(timed, timed[1:]):
            gap = float(nxt["start"]) - float(w["end"])
            if gap >= BREATH_GAP:
                out.append(float(w["end"]) + min(BREATH, gap / 2))
        out.append(float(seg_end))
    return sorted(out)


TRAVEL_NEXT = 0.25       # frame widths: after a basket, the camera this far past where it held is following the
#                          next possession (it held or drifted 0.2 for the inbound, 2-3 s, then panned 0.3 in 0.5 s)
CAMERA_FINISH = 1.5      # ...the announcer's sentence under way then finishes when it ends by this much later
#                          (an NBA broadcast's "...on fire in his first finals | game." was cut at the pan)


def camera_moves_on(times: list[float], travel: list[float], moment_t: float, t0: float, t1: float) -> float | None:
    """When the camera leaves the basket for the next possession, in one
    court shot [t0, t1) after a basket at moment_t: where its pan (travel,
    frame widths, per time) last sat within the range it held in the
    CUT_AFTER after the basket, before it went TRAVEL_NEXT past that range.
    None when it doesn't, or not in that shot. On a team's own network the
    camera never cut after a basket and the announcer never stopped: six
    clips ran 5 to 9 s into the other team's possession."""
    hold = [v for t, v in zip(times, travel) if moment_t <= t <= moment_t + CUT_AFTER and t0 <= t < t1]
    if not hold:
        return None
    lo, hi = min(hold), max(hold)
    last_in = moment_t + CUT_AFTER
    for t, v in zip(times, travel):
        if t < moment_t + CUT_AFTER or t < t0:
            continue
        if t >= t1:
            break
        if lo <= v <= hi:
            last_in = t
        elif v < lo - TRAVEL_NEXT or v > hi + TRAVEL_NEXT:
            return last_in
    return None


def clip_end(shots: list[Shot], moment_t: float, end: float, said: list[tuple[float, float]],
             basket: bool = True, limit: float | None = None, fps: float | None = None,
             breath: list[float] | None = None, free_throw: bool = False,
             trip: tuple[float, float, float] | None = None, look: float | None = None,
             camera_on: float | None = None) -> End:
    """Where a clip around a play should end (all times the shots' clock):
    when the play is over, with all the announcer says about it and nothing of
    the next scene. `end` is the clip's end as found; `look` how far `shots`
    and `said` run past it (the end may move up to there).

    The play is over at the cut play_over finds: the clip runs to it, ending
    CUT_GUARD before the picture starts to change, and keeps everything he
    says before it, said over the play and its reaction (a sentence still
    going is cut there). A free throw's `trip` to the line is one play,
    whatever the camera does between the attempts (the shooter's close-up, a
    jump to the next): (when the game clock was last read stopped, when it
    runs again, when it was first read changed:
    scoreboard.Scoreboard.clock_stopped). A cut between the first and the
    last is where the trip ends (the clock may have jumped there: only one
    shot in between says it ran on the court); with none, it ends where the
    clock runs again, or, not known to, before the first sentence he begins
    after it was last read stopped. Without one, a `free_throw` is over
    FREE_THROW_AFTER after it drops.

    In one court shot the next possession starts with no cut: a sentence he
    begins on the court more than SAID_WITH after the moment is his next
    thought (said over a cutaway, it is about the play), and the clip ends
    after his last sentence before it, at most FINISH_MAX after SAID_WITH.
    With no cut and nothing more said, a court shot is over KEEP_AFTER after
    the moment, and a reaction running on keeps the clip's end. After a
    basket, a camera that leaves for the next possession in that shot
    (`camera_on`: camera_moves_on) ends the play there, as a cut would.

    The picture's end (_tail): no flash of a shot the clip ends inside. Never
    before KEEP_AFTER after the moment, unless the broadcast cuts away sooner
    (CUT_AFTER at the least), nor past `limit`. `said`: his sentences
    (sentences()); `breath`: where he draws breath (breaths()). With no cut
    to stop at, the end is also the clip's ceiling: nothing after the play is
    added to make the shortest length."""
    gap = max(1.0 / (fps or (_fps_of(shots) if shots else 30.0)), CUT_GUARD)
    look = max(end, look if look is not None else end)
    floor = moment_t + KEEP_AFTER
    least = moment_t + CUT_AFTER
    point, why = play_over(shots, moment_t, look, basket)
    about = moment_t + (SAID_WITH if basket else SAID_WITH_STOP)     # begun on the court after this: his next thought
    top = point - gap if why else float("inf")
    if trip is not None:
        still, runs, changed = trip
        point, why = play_over(shots, max(moment_t, still - CUT_AFTER), look, basket)
        if why and point <= changed + 0.1:
            why = "the free throws are over: " + why
            top = point - gap
        elif runs > still + 0.05:
            point, why, top = runs, "the free throws are over: the clock runs again", runs
        else:
            point = min([changed] + [a for a, _ in said if still < a < changed])
            why, top = "the free throws are over", point
        about = max(about, point)
    elif free_throw and moment_t + FREE_THROW_AFTER < point:
        point, why = moment_t + FREE_THROW_AFTER, "a free throw: over once it drops"
        top = point
    elif basket and camera_on is not None and camera_on < top:
        # His sentence under way as the camera leaves finishes, when it ends
        # soon enough; one barely begun is left out ("That's good. Harper|"),
        # never sooner than CUT_AFTER after the play; no new one starts (an
        # analyst's run-on doesn't end: it is cut there).
        under = next(((a, b) for a, b in said if a < camera_on < b), None)
        stop = camera_on
        if under is not None and under[1] <= camera_on + CAMERA_FINISH:
            stop = under[1] + BREATH
        elif (under is not None and camera_on - under[0] < CUT_HEARD * (under[1] - under[0])
              and under[0] - 0.05 >= moment_t + CUT_AFTER):
            stop = under[0] - 0.05
        point = min(top, stop)
        why, top = "the camera follows the next possession", point
    floor = min(floor, top)             # the broadcast cut away sooner: the play is over there
    over = bool(why)                    # ...at a cut, the trip's end or the free throw's
    if limit is not None:
        top = min(top, limit)

    def done_with(b: float) -> float:
        """Just after his sentence ending at b: a breath, never into his next one."""
        nxt = min((a for a, _ in said if a > b - 0.01), default=float("inf"))
        return min(b + BREATH, max(b, nxt - 0.02))

    def also(more: str) -> str:
        return f"{why}; {more}" if why else more

    def on_court(a: float) -> bool:
        return any(s.t0 <= a < s.t1 and s.gameplay for s in shots)

    # His next thought, before the play is over: a sentence begun on the court
    # after `about`. What he says before it, or over a cutaway, is about the play.
    nxt = min((a for a, _ in said if about < a < top and on_court(a)), default=None)
    if nxt is None and over:
        new = top                       # to the cut, the trip's end or the free throw's
        under = next(((a, b) for a, b in said if a < new < b), None)
        if under is not None and new - under[0] < CUT_HEARD * (under[1] - under[0]) and under[0] - 0.05 >= floor:
            new, why = under[0] - 0.05, also("before a sentence the cut would leave half said")
    else:
        stop = min(nxt if nxt is not None else float("inf"), look)
        cap = about + FINISH_MAX
        # What he says about it: finished by the cap, or within FINISH_GRACE of it when under way
        # there. One running on longer is left out, the clip ending after his last whole sentence
        # ("...get him the basketball in front of the|" cut there, with the next possession playing).
        ends = [done_with(b) for a, b in said if moment_t - 1.0 < a < min(stop, cap) and b > moment_t]
        whole = [e for e in ends if e <= cap + FINISH_GRACE]
        last = max(whole) if whole else (cap if ends else floor)
        if nxt is not None:
            new, why = max(floor, last), also("the announcer moves on to the next thing")
        elif any(not s.gameplay for s in shots if moment_t <= s.t0 < look):
            new, why = max(floor, last, end), also("the reaction runs on")
        else:
            new, why = max(floor, last), also("one wide shot: the play is over")
        new = min(new, stop, top)
        top = min(top, new)             # nothing after the play to make up the length
    new, moved = _tail(shots, moment_t, new, least, gap)
    if moved:
        why = also("a short shot at the end left out")
        top, floor = min(top, new), min(floor, new)
    new = max(new, floor)
    if abs(new - end) < 0.005 and why.endswith("the reaction runs on"):
        return End(end, "kept", None if limit is None else limit)
    return End(new, why, None if top == float("inf") else max(top, floor))


def _tail(shots: list[Shot], moment_t: float, end: float, least: float, gap: float) -> tuple[float, bool]:
    """A clip's end moved off a flash of a new shot: a shot after the play
    shown under SHORT_TAIL at its end is left out, ending `gap` before it (and
    then the one before it is looked at the same way). Ending at the
    broadcast's own cut (`gap` before the next shot starts), a reaction's shots
    count together. Never back before `least`. Returns (end, whether it moved)."""
    def shown(s: Shot) -> float:            # where the picture starts to change to it
        return s.t0 - float((s.evidence or {}).get("change_s") or 0)

    moved = False
    while True:
        k = next((i for i, s in enumerate(shots) if s.t0 <= end - 0.001 < s.t1), None)
        if k is None:
            break
        while k + 1 < len(shots) and shown(shots[k + 1]) <= end - 0.001:     # inside a dissolve to the next
            k += 1
        s = shots[k]
        if s.t0 <= moment_t:                # the play's own shot
            break
        nxt = min((shown(x) for x in shots[k + 1:]), default=None)
        at_cut = nxt is not None and nxt - end <= gap + 0.02
        if at_cut and s.gameplay:
            break
        if at_cut:
            j = k
            while j > 0 and not shots[j - 1].gameplay and shots[j - 1].t0 > moment_t:
                j -= 1
            first = shots[j]
        else:
            first = s
        back = min(shown(first), first.t0) - gap
        if end - shown(first) >= SHORT_TAIL or back < least:
            break
        end, moved = back, True
    return end, moved


def off_words(end: float, words, ceiling: float | None = None,
              floor: float | None = None) -> tuple[float, bool]:
    """`end` moved off a word it falls in, to the nearer of the word's edges,
    never past `ceiling` (then its start) nor back before `floor` (then its
    end, or where it is): a word is never cut in half. One at least WORD_SAID
    said when its end won't fit stays as it is. And whether it moved forward
    (to the word's end, which the next word may start on: the caller rounds
    it down)."""
    for w in words or ():
        if "start" in w and "end" in w and float(w["start"]) + 0.02 < end < float(w["end"]) - 0.02:
            first, last = float(w["start"]), float(w["end"])
            fits = ceiling is None or last <= ceiling
            if fits and last - end <= end - first:
                return last, True
            if not fits and end - first >= WORD_SAID * (last - first):
                return end, False
            if floor is not None and first < floor:
                return (last, True) if fits else (end, False)
            return first, False
    return end, False

def fit_length(shots: list[Shot], start: float, end: float, lo: float, min_len: float | None,
               said: list[tuple[float, float]], limit: float | None = None,
               fps: float | None = None, setup: bool = False) -> tuple[float, float, str]:
    """A clip shorter than `min_len`: its start goes back first, within the
    court shot it is on and never before `lo` (the clip's first look, or the
    previous basket + 1 s); then its end goes on, never into a sentence
    started after it nor past `limit`. Still short, it stays short: a play's
    own clip a little under the shortest length beats one with the next play
    in it. A free throw's (`setup`) may go back onto the close-up right before
    its court shot: the shooter at the line (on an NBA compilation, a 4.7 s
    clip without it).
    Returns (start, end, what was done)."""
    if min_len is None or end - start >= min_len:
        return start, end, ""
    frame = 1.0 / (fps or (_fps_of(shots) if shots else 30.0))
    done = []
    # Within the court shot the start is on: across a cut is a cutaway or,
    # in a condensed broadcast, another possession.
    own = next((s for s in shots if s.t0 <= start < s.t1), None)
    first = own.t0 + frame if own is not None and own.t0 > lo else lo
    back = max(lo, first, end - min_len)
    if back < start - 0.5 and own is not None and own.gameplay:     # a step back worth making
        start = back
        done.append("starts earlier in the possession")
    if setup and end - start < min_len and own is not None and own.gameplay:
        line = next((s for s in shots if abs(s.t1 - own.t0) < 2 * frame and s.kind == "closeup"
                     and not s.replay), None)
        back = max(lo, line.t0 + frame, end - min_len) if line is not None else start
        if back < start - 0.5:
            start = back
            done.append("starts on the shooter at the line")
    if end - start < min_len:
        reach = start + min_len
        nxt = [a for a, _ in said if a > end - 0.05]
        if nxt:
            reach = min(reach, min(nxt) - 0.05)
        if limit is not None:
            reach = min(reach, limit)
        if reach > end:
            end = reach
            done.append("ends a little later")
    return start, end, "; ".join(done)
