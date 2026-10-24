"""The basketball crop planner against the owner's rules, on synthetic scenes
(tests/basketball_scene.py) whose truth says where the ball, the players and
the baskets really were.

His rules: the crop follows the play and keeps the ball in; a pass keeps the
ball and reaches the receiver before the catch; the basket may be out of view
during the play, but when the shot goes up the crop follows the ball to the
basket and, from just before the ball reaches the rim until the result, holds
the rim, the net and the backboard (its edge past the backboard); it widens
rather than cuts; it never blends across a camera cut; when the ball can't be
followed it falls back to a wider view and says so.

Pure Python, so these run on CI.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from basketball_scene import scene

from sports.basketball.crop import (
    phases,
    plan_clip,
    planner,
    validate,
)
from sports.basketball.crop.model import Hints

ROOT = Path(__file__).resolve().parent.parent
SLACK = 0.01


def _crop(plan, t):
    f = min(len(plan.cx) - 1, max(0, round(t * plan.fps)))
    return plan.cx[f] - plan.w[f] / 2, plan.cx[f] + plan.w[f] / 2, plan.w[f]


def _holds(plan, t, x0, x1):
    lo, hi, _ = _crop(plan, t)
    return lo - SLACK <= x0 and x1 <= hi + SLACK


def _person(built, k, label):
    """The box (x0, x1) of the person labelled `label` ("player:5") at sample k."""
    s = built.obs.samples[k]
    for p, who in zip(s.people, built.truth.who[k]):
        if who == label:
            return p.x - p.w / 2, p.x + p.w / 2
    return None


def _sample_near(built, t):
    times = built.truth.times
    return min(range(len(times)), key=lambda k: abs(times[k] - t))


def _basket_span(built, k, name="right"):
    rim = built.truth.rims[k].get(name)
    board = built.truth.backboards[k].get(name)
    if rim is None or board is None:
        return None
    return min(rim[0], board[0]), max(rim[2], board[2])


def _pass_then_three(seed=3, rate=0.35, backboard=True):
    return (scene(duration=15, seed=seed).players(10).closeup(0, 3.0).basket("right", backboard=backboard)
            .ball_dribble(3.0, 6.0, player=2).ball_pass(6.0, 6.6, frm=2, to=5)
            .ball_shot(7.4, 8.6, shooter=5, basket="right").false_ball("logo").ball_detection(rate).build())


def test_a_pass_then_a_three_keeps_the_ball_the_receiver_the_shooter_and_the_basket():
    for seed in (3, 4, 7):
        built = _pass_then_three(seed)
        plan = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"))
        truth = built.truth
        # The ball, wherever it is on screen, from the play's start to the result.
        seen = [k for k, t in enumerate(truth.times) if 3.6 <= t <= 10.0 and truth.in_frame(k)]
        kept = [k for k in seen if _holds(plan, truth.times[k], truth.ball[k][0], truth.ball[k][0])]
        assert len(kept) >= 0.95 * len(seen), (seed, len(kept), len(seen))
        # The receiver is in before the catch.
        catch = truth.event("pass")["catch"]
        k = _sample_near(built, catch - 0.1)
        box = _person(built, k, "player:5")
        assert box is not None and _holds(plan, truth.times[k], *box), seed
        # The shooter at the release.
        k = _sample_near(built, truth.event("shot")["release"])
        box = _person(built, k, "player:5")
        assert box is not None and _holds(plan, truth.times[k], box[0], box[1]), seed
        # The basket: rim and backboard from just before the ball reaches the rim
        # and the second after (the crop never widens: it pans on with the play
        # after that, as on his House of Highlights reference).
        rim_t = truth.event("shot")["rim"]
        for k, t in enumerate(truth.times):
            if rim_t - 0.3 <= t <= rim_t + 1.0:
                span = _basket_span(built, k)
                assert span is not None and _holds(plan, t, *span), (seed, t, span, _crop(plan, t))


def test_b_the_net_only_when_they_shoot_and_held_after_the_rim():
    built = _pass_then_three(seed=5, rate=0.5)
    plan = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"))
    truth = built.truth
    # During the dribble the basket is far off and not forced in: no widening for it.
    for t in (3.8, 4.5, 5.2):
        assert _crop(plan, t)[2] <= plan.base + 1e-6, t
        span = _basket_span(built, _sample_near(built, t))
        assert span is None or not _holds(plan, t, *span), t
    # After the ball reaches the rim, the basket stays at least 1 s.
    rim_t = truth.event("shot")["rim"]
    for t in (rim_t, rim_t + 0.5, rim_t + 1.0):
        span = _basket_span(built, _sample_near(built, t))
        assert span is not None and _holds(plan, t, *span), t


def test_c_a_cut_holds_then_snaps_with_no_frame_blended_across():
    built = _pass_then_three(seed=3)
    plan = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"))
    cut = next(c for c in plan.cuts if abs(c.t - 3.0) < 0.05)
    f = cut.frame
    # The close-up's crop is held to the last frame before the cut...
    assert plan.cx[f - 1] == plan.cx[f - 2]
    # ...and the court's crop starts on the cut's first frame (not a blend of the two).
    assert abs(plan.cx[f] - plan.cx[f - 1]) > 0.05
    assert plan.mode[f - 1] == "person" and plan.mode[f] != "person"


def test_d_a_short_hide_is_bridged_and_the_crop_stays_steady():
    built = (scene(duration=10, seed=2).players(10).basket("right")
             .ball_dribble(1.0, 8.0, player=3).hide_ball(4.0, 4.5).build())
    plan = plan_clip(built.obs)
    # No jump while the ball is hidden behind a player: the crop moves no faster than its limit.
    f0, f1 = round(3.8 * plan.fps), round(4.8 * plan.fps)
    assert max(abs(plan.cx[f + 1] - plan.cx[f]) for f in range(f0, f1)) <= planner.V_MAX / plan.fps + 1e-6
    truth = built.truth
    for t in (3.9, 4.2, 4.6):
        k = _sample_near(built, t)
        assert _holds(plan, t, truth.ball[k][0], truth.ball[k][0]), t


def test_e_a_fast_cross_court_move_is_followed_without_jitter():
    built = (scene(duration=10, seed=4).players(10).basket("right")
             .ball_transition(2.0, 4.0, player=1, to_x=0.8, camera=False).build())
    plan = plan_clip(built.obs)
    truth = built.truth
    for k, t in enumerate(truth.times):
        if 2.0 <= t <= 6.0 and truth.in_frame(k):
            assert _holds(plan, t, truth.ball[k][0], truth.ball[k][0]), t
    # Speed and acceleration limits, and no back-and-forth.
    v = [(plan.cx[f + 1] - plan.cx[f]) * plan.fps for f in range(len(plan.cx) - 1)]
    assert max(abs(x) for x in v) <= planner.V_MAX + 1e-6
    a = [(v[f + 1] - v[f]) * plan.fps for f in range(len(v) - 1)]
    assert max(abs(x) for x in a) <= planner.A_MAX * 1.01 + 1e-6
    moving = [x for x in v if abs(x) > 0.02]
    flips = sum(1 for p, q in zip(moving, moving[1:]) if (p > 0) != (q > 0))
    assert flips <= 2 * len(plan.cx) / plan.fps / 10 + 1


def test_f_when_the_ball_cant_be_followed_the_crop_stays_9_16_and_the_quality_says_so():
    # It used to widen to 1:1 here: "I don't like the zoom in and out". The
    # crop stays 9:16 on the players and where the camera points.
    built = (scene(duration=10, seed=6).players(10).basket("right")
             .ball_dribble(1.0, 8.0, player=3).ball_detection(0.0).build())
    plan = plan_clip(built.obs)
    assert plan.quality["ball"] == 0.0
    gameplay = [f for f, m in enumerate(plan.mode) if m != "person"]
    assert all(abs(plan.w[f] - plan.base) < 1e-5 for f in gameplay)
    assert not any(m == "tracked" for m in plan.mode)


def test_g_the_crop_edge_sits_past_the_backboard_found_or_estimated():
    for backboard in (True, False):
        built = _pass_then_three(seed=8, rate=0.6, backboard=backboard)
        plan = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"))
        rim_t = built.truth.event("shot")["rim"]
        for t in (rim_t - 0.3, rim_t, rim_t + 0.7, rim_t + 1.3):
            k = _sample_near(built, t)
            board = built.truth.backboards[k]["right"]
            lo, hi, _ = _crop(plan, t)
            # The real backboard's outer (baseline-side) edge is inside the crop.
            assert hi + SLACK >= board[2], (backboard, t, hi, board)
            rim = built.truth.rims[k]["right"]
            assert lo - SLACK <= rim[0], (backboard, t, lo, rim)


def test_a_closeup_follows_the_person_who_walks_across_it():
    from sports.basketball.crop.model import Person, Sample, Shot

    fps, n = 30.0, 120
    shot = Shot(id=0, f0=0, f1=n, t0=0.0, t1=n / fps, kind="closeup")
    # A player 0.6 of the height tall, walking from left of centre to the right over 4 s.
    samples = [Sample(i=k, frame=f, t=f / fps, people=[Person(0.35 + 0.4 * f / n, 0.5, 0.2, 0.6)])
               for k, f in enumerate(range(0, n, 6))]
    base = 0.3164
    cx, w = planner._person_shot(planner.ShotData(shot=shot, samples=samples), fps, [base, 0.4, 0.5625, 1.0])
    assert len(cx) == n and w == [base] * n
    # He is in the picture at the start and at the end, and the crop never jumps.
    assert abs(cx[0] - 0.35) < base / 2 and abs(cx[-1] - 0.75 + 0.4 / n * 6) < base / 2
    assert max(abs(cx[f + 1] - cx[f]) for f in range(n - 1)) <= planner.V_MAX / fps + 1e-6
    # Nobody found: the middle, held.
    cx, _ = planner._person_shot(planner.ShotData(shot=shot, samples=[]), fps, [base, 0.4, 0.5625, 1.0])
    assert cx == [0.5] * n


CLOSE_BASE = 0.3164        # a 9:16 crop of a 16:9 frame


def _closeup(people_at, seconds: float = 3.0, fps: float = 30.0, every: int = 3):
    """A close-up `seconds` long sampled every `every` frames: people_at(k, t) is what the
    detectors (and the pose model, head_x) found on sample k."""
    from sports.basketball.crop.model import Sample, Shot

    n = round(seconds * fps)
    shot = Shot(id=0, f0=0, f1=n, t0=0.0, t1=seconds, kind="closeup")
    samples = [Sample(i=k, frame=f, t=f / fps, people=people_at(k, f / fps))
               for k, f in enumerate(range(0, n, every))]
    data = planner.ShotData(shot=shot, samples=samples)
    cx, w = planner._person_shot(data, fps, [CLOSE_BASE])
    assert len(cx) == n and w == [CLOSE_BASE] * n
    return data, cx


def test_a_closeup_never_follows_its_player_onto_a_box_at_the_side():
    from sports.basketball.crop.model import Person

    # The player, face found, right of the middle and all but still. From 1 s someone is cut off
    # by the frame's right side with no face found (a fan, the bench) and drifts out; for 0.4 s the
    # player himself is not found. Following whoever was nearest slid onto the box at the side
    # while he was hidden and rode it out of the picture: the crop sat on the seats.
    def people_at(k, t):
        found = []
        if not 12 <= k <= 15:
            found.append(Person(0.66, 0.5, 0.32, 0.95, head_x=0.70))
        if k >= 10:
            found.append(Person(0.86 + 0.004 * (k - 10), 0.5, 0.28, 0.9))
        return found

    _data, cx = _closeup(people_at)
    assert all(abs(x - 0.70) < 0.01 for x in cx)

    # The same nearer the side, where the box at the side covers where his face was: it is still
    # never him.
    def near_the_side(k, t):
        found = []
        if not 12 <= k <= 15:
            found.append(Person(0.80, 0.5, 0.22, 0.95, head_x=0.80))
        if k >= 10:
            found.append(Person(0.90 + 0.004 * (k - 10), 0.5, 0.22, 0.9))
        return found

    _data, cx = _closeup(near_the_side)
    assert all(abs(x - 0.80) < 0.01 for x in cx)


def test_a_closeup_never_jumps_from_its_players_face_onto_a_box_with_none():
    from sports.basketball.crop.model import Person

    # The player, face found, left of the middle and still. For 0.8 s someone walks past in front
    # of him: he is not found, and the one passing is, without a face, a little to his right.
    # Following whoever was nearest jumped onto the passer and back, and the crop left the player.
    def people_at(k, t):
        if 10 <= k <= 17:
            return [Person(0.57, 0.5, 0.12, 0.9)]
        return [Person(0.42, 0.5, 0.22, 0.95, head_x=0.40)]

    _data, cx = _closeup(people_at)
    assert all(abs(x - 0.40) < 0.01 for x in cx)


def test_a_closeup_never_opens_on_a_box_at_the_side():
    from sports.basketball.crop.model import Person

    # A big blurred shoulder in front, cut off by the frame's left side with no face found, is
    # the most prominent box on every sample; the player behind it, face found, is smaller. The
    # close-up is on the player, never the shoulder.
    def people_at(k, t):
        return [Person(0.15, 0.5, 0.30, 1.0), Person(0.60, 0.5, 0.20, 0.7, head_x=0.60)]

    _data, cx = _closeup(people_at)
    assert all(abs(x - 0.60) < 0.01 for x in cx)
    # With no face found in the shot, boxes alone, a box at the side counts as before.
    _data, cx = _closeup(lambda k, t: [Person(0.15, 0.5, 0.30, 1.0)])
    assert all(abs(x - CLOSE_BASE / 2) < 1e-9 for x in cx)


def test_a_closeup_player_who_turns_away_is_still_followed():
    from sports.basketball.crop.model import Person

    # His face is found for 2 s; then he turns his back (no face found, his box still is) and
    # walks right, with a team-mate's face across the frame. His box, over where his face was, is
    # still him: following faces only left him behind.
    def x(t):
        return 0.40 + 0.3 * max(0.0, t - 2.0)

    def people_at(k, t):
        return [Person(x(t), 0.5, 0.30, 0.95, head_x=0.40 if t < 2.0 else None),
                Person(0.85, 0.5, 0.16, 0.6, head_x=0.85)]

    data, cx = _closeup(people_at)
    assert all(abs(cx[s.frame] - x(s.t)) < CLOSE_BASE / 2 for s in data.samples)


def test_a_cutaway_stays_on_who_the_camera_is_centred_on():
    from sports.basketball.crop.model import Person

    # A cutaway to someone in the crowd: him in the middle, biggest, his face not found for its
    # first 0.4 s (turned away, a hand in front); fans either side whose faces are found all
    # along. The close-up is on him (the people's centre, as a director frames a cutaway), not on
    # the first face found.
    def people_at(k, t):
        return [Person(0.52, 0.5, 0.34, 0.95, head_x=0.50 if k >= 4 else None),
                Person(0.22, 0.5, 0.18, 0.6, head_x=0.22), Person(0.80, 0.5, 0.16, 0.55, head_x=0.80)]

    _data, cx = _closeup(people_at)
    assert all(abs(x - 0.50) < 0.011 for x in cx)


def test_a_closeup_keeps_up_with_a_face_that_moves_fast():
    from sports.basketball.crop.model import Person

    # He stands, then crosses half the frame in 0.45 s (the camera left behind), then stands
    # again, with someone at the side all along.
    def face(t):
        u = min(1.0, max(0.0, (t - 0.9) / 0.45))
        return 0.25 + 0.5 * (3 * u * u - 2 * u ** 3)

    def people_at(k, t):
        return [Person(face(t), 0.5, 0.30, 0.95, head_x=face(t)), Person(0.05, 0.5, 0.10, 0.6, head_x=0.05)]

    _data, cx = _closeup(people_at, seconds=2.4, every=2)
    # His face never leaves the picture, and the crop ends on him (within its dead zone).
    assert all(abs(x - face(f / 30.0)) < CLOSE_BASE / 2 for f, x in enumerate(cx))
    assert abs(cx[0] - 0.25) < 0.01 and abs(cx[-1] - 0.75) <= planner.DEAD_ZONE * CLOSE_BASE


def test_a_still_closeup_player_is_held_still():
    from sports.basketball.crop.model import Person

    # He hardly moves, but his face wanders 0.08 of the width from sample to sample, more than
    # the smoothing lets pass (DEAD_ZONE); a smaller team-mate at the side all along, and someone
    # bigger leaning in for a moment: the crop holds still on him, as a camera operator would.
    def people_at(k, t):
        found = [Person(0.42 + 0.04 * (k % 3), 0.5, 0.30, 0.95, head_x=0.42 + 0.04 * (k % 3)),
                 Person(0.90, 0.5, 0.20, 0.8, head_x=0.90)]
        if k in (5, 6, 12):
            found.append(Person(0.62, 0.5, 0.45, 1.0, head_x=0.62))
        return found

    _data, cx = _closeup(people_at, seconds=2.0)
    assert max(cx) - min(cx) < 1e-9 and abs(cx[0] - 0.46) <= 0.011


def test_the_backboard_note_is_said_only_of_the_plays_own_basket():
    def three(**baskets):
        built = scene(duration=15, seed=3).players(10).closeup(0, 3.0)
        for side, place in baskets.items():
            built = built.basket(side, **place)
        built = (built.ball_dribble(3.0, 6.0, player=2).ball_pass(6.0, 6.6, frm=2, to=5)
                 .ball_shot(7.4, 8.6, shooter=5, basket="right").ball_detection(0.6).build())
        notes = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3")).quality["notes"]
        return [n for n in notes if n.startswith("the broadcast cuts the backboard")]

    # The play's basket with its board past the picture's edge: said.
    assert len(three(right={"x": 0.975, "bb_rate": 1.0})) == 1
    # The other end's basket cut by the edge while the play goes to this one: nothing to say.
    assert three(right={}, left={"x": 0.025, "bb_rate": 1.0}) == []


# The picture's share under the apps' top bar alone (the layout before the bottom band): the
# narrowest banded crop, the hardest for fitting the shooter, the basket and the rail together.
# The default Crop's picture (action.picture_share()) is shorter and its crop wider: see
# test_between_the_apps_text_the_shot_still_has_the_shooter_and_the_basket. At that width the
# rail fits in the one case here that tests it can't (test_the_rim_stays_left_of_the_right_rail_*).
PICTURE = 0.8698


def _shot_at(release, rim, shooter_x, basket_x, seed):
    """Player 4 dribbles, then shoots at the right basket: released at
    `release`, at the rim at `rim`; the other players out of his way."""
    homes = [(0.12, 0.62), (0.22, 0.8), (0.3, 0.66), (0.38, 0.84), (shooter_x, 0.72), (0.55, 0.6),
             (0.7, 0.82), (0.8, 0.64), (0.88, 0.85), (0.95, 0.7)]
    built = (scene(duration=8, seed=seed).players(10, homes=homes, wander=0.01).basket("right", x=basket_x)
             .ball_dribble(0.5, release, player=4).ball_shot(release, rim, shooter=4, basket="right")
             .ball_detection(0.7).build())
    built.obs.geometry.picture = PICTURE
    return built


def test_a_short_shot_shows_the_shooter_and_the_basket_together_from_the_release():
    # Spec M4: on broadcasters' Shorts a short or mid-range shot had the
    # shooter and the rim in the picture together (2/2); a jump shot from
    # distance never had the rim in at the release (0/6), it came in during
    # the flight.
    for shooter_x, basket_x, seed in ((0.45, 0.62, 3), (0.42, 0.60, 4), (0.47, 0.66, 6)):
        built = _shot_at(3.0, 3.7, shooter_x, basket_x, seed)
        plan = plan_clip(built.obs, Hints(moment_t=3.7, kind="made_2"))
        for t in (3.0, 3.1, 3.2, 3.4):
            k = _sample_near(built, t)
            span = _basket_span(built, k)
            assert span is not None and _holds(plan, built.truth.times[k], *span), (seed, t)
        k = _sample_near(built, 3.0)
        box = _person(built, k, "player:4")
        assert box is not None and _holds(plan, built.truth.times[k], *box), seed
    # From distance: the shooter at the release, the basket only later.
    built = _shot_at(3.0, 3.7, 0.30, 0.75, 3)
    plan = plan_clip(built.obs, Hints(moment_t=3.7, kind="made_2"))
    k = _sample_near(built, 3.0)
    box = _person(built, k, "player:4")
    assert box is not None and _holds(plan, built.truth.times[k], *box)
    assert not _holds(plan, built.truth.times[k], *_basket_span(built, k))
    for t in (3.4, 3.6):
        k = _sample_near(built, t)
        assert _holds(plan, built.truth.times[k], *_basket_span(built, k)), t


def test_between_the_apps_text_the_shot_still_has_the_shooter_and_the_basket():
    # The default Crop: the picture between the apps' top bar and their caption
    # (action.bands), so shorter and wider than under the top bar alone. The
    # short shot keeps the shooter and the basket together from the release,
    # and the plan says where each band goes.
    from sports.basketball import action

    top, bottom = action.bands({})
    for shooter_x, basket_x, seed in ((0.45, 0.62, 3), (0.42, 0.60, 4), (0.47, 0.66, 6)):
        built = _shot_at(3.0, 3.7, shooter_x, basket_x, seed)
        built.obs.geometry.picture = action.picture_share({})
        built.obs.geometry.band_top = top
        plan = plan_clip(built.obs, Hints(moment_t=3.7, kind="made_2"))
        assert abs(plan.band_top - top) < 1e-4 and abs(plan.band_bottom - bottom) < 1e-4
        tracking = plan.to_tracking()
        assert tracking["band_top"] == plan.band_top and tracking["band_bottom"] == plan.band_bottom
        for t in (3.0, 3.2, 3.4):
            k = _sample_near(built, t)
            span = _basket_span(built, k)
            assert span is not None and _holds(plan, built.truth.times[k], *span), (seed, t)
        k = _sample_near(built, 3.0)
        box = _person(built, k, "player:4")
        assert box is not None and _holds(plan, built.truth.times[k], *box), seed
    # A geometry with only a picture share (made before the bottom band) keeps its band on top.
    built = _shot_at(3.0, 3.7, 0.45, 0.62, 3)
    plan = plan_clip(built.obs, Hints(moment_t=3.7, kind="made_2"))
    assert abs(plan.band_top - (1 - PICTURE)) < 1e-4 and plan.band_bottom == 0.0


def _basket_phase(kind, ball_x, rim_x=0.6, seconds=3.0, fps=30.0):
    """One wide shot in one phase: a basket with its baseline on the right,
    its rim centred at rim_x, and the ball held at ball_x. Returns the shot's
    plan, the crop's width, the rim's centre and what must be held."""
    from sports.basketball.crop import basket
    from sports.basketball.crop.model import BallPoint, Basket, Geometry, Phase, Sample, Shot

    n = round(seconds * fps)
    rim = (rim_x - 0.011, 0.33, rim_x + 0.011, 0.345)
    board = (rim_x + 0.008, 0.25, rim_x + 0.042, 0.35)
    unit, unit_y, _ = basket.unit(rim, board, "right")
    samples, points, rows = [], [], []
    for k, f in enumerate(range(0, n, 3)):
        samples.append(Sample(i=k, frame=f, t=f / fps))
        points.append(BallPoint(t=f / fps, x=ball_x, y=0.5, status="detected", conf=0.9))
        rows.append([Basket(id=0, shot=0, t=f / fps, rim=rim, backboard=board, side="right", unit=unit,
                            unit_y=unit_y)])
    data = planner.ShotData(shot=Shot(id=0, f0=0, f1=n, t0=0.0, t1=seconds, kind="wide"), samples=samples,
                            points=points, rows=rows, relevant=0, phases=[Phase(kind, 0.0, seconds)])
    geometry = Geometry(width=1920, height=1080, fps=fps, frames=n, picture=PICTURE)
    return planner.plan_shot(data, geometry), geometry.base, rim_x, unit


def test_the_rim_stays_left_of_the_right_rail_where_the_crop_can():
    # Spec M3, 6.6 and 6.11: the apps' buttons run down the right of the
    # screen; broadcasters keep the rim left of x 0.81 with the ball at the
    # rim. After the basket the ball steers the crop away from the rim (left):
    # held only by the basket's unit, the rim sat at 0.85.
    got, w, rim_x, unit = _basket_phase("result", ball_x=0.35)
    xs = [(rim_x - (cx - w / 2)) / w for cx in got["cx"][30:]]
    assert max(xs) <= 1 - planner.RAIL + 0.005, max(xs)
    assert all(cx - w / 2 - SLACK <= unit[0] and unit[1] <= cx + w / 2 + SLACK for cx in got["cx"][30:])
    # Where the ball must be in too and the rail won't fit with it, the ball
    # and the basket are held and the rim may go past 0.81: no frame fails.
    got, w, rim_x, unit = _basket_phase("rim", ball_x=0.33)
    xs = [(rim_x - (cx - w / 2)) / w for cx in got["cx"][30:]]
    assert max(xs) > 1 - planner.RAIL
    assert not validate.failures(got["cx"], got["w"], got["req"])
    # A rim already left of the rail: the crop stays on its focus (the ball
    # weighted twice, the basket once), as it always did.
    got, w, rim_x, unit = _basket_phase("result", ball_x=0.62)
    focus = (2 * 0.62 + (unit[0] + unit[1]) / 2) / 3
    assert all(abs(cx - focus) < 0.005 for cx in got["cx"][30:])


def _results(built):
    working: dict = {}
    plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"), debug=working)
    return [p for d in working["shots"] for p in d.phases if p.kind == "result"]


def test_the_basket_is_let_go_once_the_camera_leaves_it_after_the_make():
    # A 2019 game-winner: the ball dropped through and the broadcast panned
    # after the scorer; the crop, still holding the rim for the whole result,
    # slid the other way off the celebration. The camera leaving the basket
    # ends the result, after the RESULT_HOLD every make keeps.
    def three(*camera):
        return (scene(duration=15, seed=3).players(10).camera(*camera).basket("right")
                .ball_dribble(3.0, 6.0, player=2).ball_pass(6.0, 6.6, frm=2, to=5)
                .ball_shot(7.4, 8.6, shooter=5, basket="right").ball_detection(0.6).build())

    still = _results(three((0.0, 0.0)))
    assert still
    rim_t = still[0].t0                    # the ball reaching the rim zone
    assert max(p.t1 for p in still) >= rim_t + phases.RESULT - 0.1
    # Panning left after the make takes the right basket toward the edge: let go.
    away = _results(three((0.0, 0.0), (8.8, 0.0), (10.0, -0.2)))
    assert away and max(p.t1 for p in away) < rim_t + phases.RESULT - 0.2, [(p.t0, p.t1) for p in away]
    assert min(p.t1 for p in away) >= rim_t + phases.RESULT_HOLD - 0.1
    # Panning right, toward the basket, keeps it.
    toward = _results(three((0.0, 0.0), (8.8, 0.0), (10.0, 0.1)))
    assert toward and max(p.t1 for p in toward) >= rim_t + phases.RESULT - 0.1


def test_the_plan_keeps_its_invariants():
    built = _pass_then_three(seed=9)
    a = plan_clip(built.obs, Hints(moment_t=8.6, kind="made_3"))
    b = plan_clip(_pass_then_three(seed=9).obs, Hints(moment_t=8.6, kind="made_3"))
    assert a.cx == b.cx and a.w == b.w                      # deterministic
    assert len(a.cx) == built.obs.geometry.frames
    for f, (x, w) in enumerate(zip(a.cx, a.w)):
        assert a.base - 1e-9 <= w <= 1.0 + 1e-9
        assert x - w / 2 >= -1e-9 and x + w / 2 <= 1 + 1e-9, f
    starts = {c.frame for c in a.cuts}
    for f in range(len(a.cx) - 1):
        if f + 1 not in starts:
            assert abs(a.cx[f + 1] - a.cx[f]) <= planner.V_MAX / a.fps + 1e-6, f
    tracking = a.to_tracking()
    assert len(tracking["path"]) == len(a.cx)
    narrow = a.__class__(fps=a.fps, base=a.base, cx=a.cx, w=[a.base] * len(a.cx))
    assert "zoom" not in narrow.to_tracking()


def test_the_planning_core_needs_no_numpy_or_cv2():
    code = (
        "import builtins, sys\n"
        "real = builtins.__import__\n"
        "def guard(name, *a, **k):\n"
        "    if name.split('.')[0] in ('numpy', 'cv2', 'torch', 'ultralytics'):\n"
        "        raise ImportError('blocked ' + name)\n"
        "    return real(name, *a, **k)\n"
        "builtins.__import__ = guard\n"
        "from sports.basketball.crop import plan_clip, phases, planner, validate, ball, basket, shots, players\n"
        "from sports.basketball import leadin\n"
        "print('ok')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
