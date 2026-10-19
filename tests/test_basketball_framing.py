"""Basketball framing's planning core (sports/basketball/crop/) on synthetic
broadcasts (tests/basketball_scene.py): pure Python, so CI runs it with
nothing heavier than pytest. Players are numbers; no real names."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from basketball_scene import FPS, scene

from sports.basketball.crop import players, shots
from sports.basketball.crop.model import BallPoint, FrameSignals, Person, Sample, Shot

ROOT = Path(__file__).resolve().parent.parent
FRAME = 1 / FPS
GUARD = shots.CUT_GUARD      # how far a start or an end stays from a cut


def _shots_of(built) -> list[Shot]:
    sig = built.obs.signals
    return shots.classify_all(shots.split(sig.frames, sig.fps, shots.find_cuts(sig)), built.obs.samples)


def _cut(*spec) -> list[Shot]:
    """Shots by hand: (t0, t1, kind) or (t0, t1, kind, replay), back to back."""
    return [Shot(id=i, f0=round(t0 * FPS), f1=round(t1 * FPS), t0=t0, t1=t1, kind=kind,
                 replay=bool(rest and rest[0]))
            for i, (t0, t1, kind, *rest) in enumerate(spec)]


# ---- cuts -------------------------------------------------------------------------------------


def test_cuts_land_on_their_frames_a_flash_is_none_and_a_wipe_is_a_transition():
    built = (scene(duration=14, seed=1)
             .cut_at(3.0)
             .flash(5.0)                                  # a camera flash: one bright frame
             .camera((5.5, 0.0), (6.5, 0.5))              # a fast pan is no cut either
             .cut_at(8.0, jump=0.3)
             .wipe(11.0)                                  # a replay wipe: 12 frames, none a cut alone
             .build())
    cuts = shots.find_cuts(built.obs.signals)
    assert [(c.frame, c.kind) for c in cuts] == built.truth.cuts
    wipe = cuts[-1]
    assert wipe.span == built.truth.spans[wipe.frame]
    assert built.truth.flashes[0] not in {c.frame for c in cuts}
    assert all(abs(c.t - c.frame / FPS) < 1e-9 for c in cuts)


def test_a_flash_is_told_from_a_cut_without_luma_too():
    built = scene(duration=8, seed=5).cut_at(2.0).flash(4.0).flash(6.0, frames=2).build()
    signals = replace(built.obs.signals, luma=[])
    assert [(c.frame, c.kind) for c in shots.find_cuts(signals)] == built.truth.cuts == [(120, "hard")]


def test_two_cuts_too_close_for_a_shot_between_them_merge_into_one_transition():
    built = scene(duration=8, seed=6).cut_at(3.0).cut_at(3.15).build()
    cuts = shots.find_cuts(built.obs.signals)
    first, second = built.truth.cuts[0][0], built.truth.cuts[1][0]
    assert [(c.frame, c.kind, c.span) for c in cuts] == [(second, "transition", (first, second - 1))]


def test_a_fast_pan_ending_on_a_hard_cut_is_no_dissolve():
    # The crowd panned fast for half a second (a change of 14 a frame), then a hard cut (63):
    # the picture changes at the cut, not when the pan began (a clip ending before that "dissolve"
    # lost the announcer's last words). A wipe, with no jump at its end, stays a transition.
    n = 150
    gray, col = [2.0] * n, [0.02] * n
    for f in range(60, 90):
        gray[f], col[f] = 14.0, 0.05
    gray[90], col[90] = 63.0, 0.6
    sig = FrameSignals(fps=FPS, width=48, height=27, d_gray=gray, d_col=col, luma=[100.0] * n)
    assert [(c.frame, c.kind, c.span) for c in shots.find_cuts(sig)] == [(90, "hard", None)]
    gray[90], col[90] = 14.0, 0.05
    sig = FrameSignals(fps=FPS, width=48, height=27, d_gray=gray, d_col=col, luma=[100.0] * n)
    assert [(c.kind, c.span is not None) for c in shots.find_cuts(sig)] == [("transition", True)]


# ---- shot kinds -------------------------------------------------------------------------------


def test_each_shot_is_told_by_who_is_in_it():
    built = scene(duration=16, seed=2).closeup(0, 3).crowd(6, 8).graphic(10, 11).build()
    found = _shots_of(built)
    assert [(s.f0, s.f1) for s in found] == [(s.f0, s.f1) for s in built.truth.shots]
    assert [s.kind for s in found] == [s.kind for s in built.truth.shots] == [
        "closeup", "wide", "crowd", "wide", "graphic", "wide"]
    assert [s.gameplay for s in found] == [False, True, False, True, False, True]
    assert found[0].evidence["tallest"] == 0.6 and found[1].evidence["floor_n"] >= 3


def test_a_replay_is_flagged_by_its_wipes_or_by_the_missing_score_bug():
    for wipes, why in ((True, "transitions"), (False, "bug")):
        built = scene(duration=20, seed=3).replay(8, 12, wipes=wipes).build()
        found = _shots_of(built)
        assert [(s.kind, s.replay) for s in found] == [("wide", False), ("wide", True), ("wide", False)]
        assert found[1].evidence["replay_by"] == why
        assert not found[1].gameplay


def test_a_shot_where_the_bug_is_seen_is_no_replay():
    built = scene(duration=12, seed=7).cut_at(4.0).cut_at(8.0).build()
    assert [s.replay for s in _shots_of(built)] == [False, False, False]
    # Dissolved in and out, but with the bug up throughout: live, not a replay.
    built = scene(duration=20, seed=3).wipe(8.0).wipe(12.0).build()
    assert not any(s.replay for s in _shots_of(built))


# ---- lead-in (test C) -------------------------------------------------------------------------


def test_a_closeup_lead_in_moves_just_past_the_courts_first_frame():
    # [closeup 0-3.0 s] | [wide 3.0-15 s], the basket at 11 s.
    assert shots.lead_in_start(_cut((0, 3.0, "closeup"), (3.0, 15, "wide")), 11.0, 0.0, 15.0, fps=FPS) == (
        3.0 + GUARD, "past closeup")
    # The same from the synthetic broadcast, cut found from its frames.
    found = _shots_of(scene(duration=15).closeup(0, 3.0).build())
    start, reason = shots.lead_in_start(found, 11.0, 0.0, 15.0)
    assert abs(start - (found[1].t0 + GUARD)) < 1e-6 and abs(start - (3.0 + GUARD)) < FRAME


def test_the_start_stays_on_the_possession_and_never_before_the_previous_basket():
    court = _cut((0, 2.0, "wide"), (2.0, 6.0, "wide"), (6.0, 15, "wide"))
    # On the court all along, camera changes and all: the same possession, the start stays.
    assert shots.lead_in_start(court, 11.0, 0.0, 15.0, fps=FPS) == (0.0, "kept: the same possession")
    # A cut the game clock jumps across is another moment of the game: just past the last.
    jumped = _cut((0, 2.0, "wide"), (2.0, 6.0, "wide"), (6.0, 15, "wide"))
    jumped[1].evidence, jumped[2].evidence = {"clock_jump": 22.0}, {"clock_jump": 31.0}
    start, why = shots.lead_in_start(jumped, 11.0, 0.0, 15.0, fps=FPS)
    assert start == 6.0 + GUARD and why.startswith("after the broadcast's jump")
    # Never before the previous basket + 1 s.
    assert shots.lead_in_start(court, 11.0, 0.0, 15.0, prev_basket=4.0, fps=FPS) == (5.0, "previous basket + 1 s")
    assert shots.lead_in_start(court, 11.0, 0.0, 15.0, prev_basket=6.5, fps=FPS) == (7.5, "previous basket + 1 s")
    # After a close-up, the bound still holds.
    after = _cut((0, 3.0, "closeup"), (3.0, 15, "wide"))
    assert shots.lead_in_start(after, 11.0, 0.0, 15.0, prev_basket=4.5, fps=FPS)[0] == 5.5


def test_a_start_never_lands_on_a_cutaway_or_a_replay():
    for cutaway in ((4.0, 5.2, "closeup"), (4.0, 5.2, "wide", True), (4.0, 5.2, "crowd")):
        clip = _cut((0, 4.0, "wide"), cutaway, (5.2, 15, "wide"))
        # The previous basket puts the earliest start inside the cutaway.
        start, reason = shots.lead_in_start(clip, 8.5, 0.0, 15.0, prev_basket=4.0, fps=FPS)
        assert start == pytest.approx(5.2 + GUARD) and "past" in reason


def test_a_start_mid_word_moves_to_the_words_end_when_it_ends_soon():
    clip = _cut((0, 3.0, "closeup"), (3.0, 15, "wide"))
    assert shots.lead_in_start(clip, 11.0, 0.0, 15.0, words=[(2.8, 3.2)], fps=FPS)[0] == 3.2
    assert shots.lead_in_start(clip, 11.0, 0.0, 15.0, words=[(2.8, 3.5)], fps=FPS)[0] == 3.0 + GUARD


def test_the_length_guard_asks_for_a_later_end_or_keeps_a_short_run():
    clip = _cut((0, 3.0, "closeup"), (3.0, 15, "wide"))
    lead = shots.lead_in_start(clip, 11.0, 0.0, 14.0, min_len=12.0, max_len=60.0, fps=FPS)
    assert lead.start == 3.0 + GUARD and abs(lead.end - (3.0 + GUARD + 12.0)) < 1e-9
    assert "end must extend" in lead.reason
    assert shots.lead_in_start(clip, 11.0, 0.0, 15.0, min_len=10.0, fps=FPS).end == 15.0
    # Only 1.5 s of court before the basket: the original start stays.
    late = _cut((0, 9.5, "closeup"), (9.5, 15, "wide"))
    assert shots.lead_in_start(late, 11.0, 0.0, 15.0, fps=FPS) == (0.0, "kept: short run")


# ---- players ----------------------------------------------------------------------------------


def _ids_by_player(built) -> dict[str, set]:
    out: dict[str, set] = {}
    for s, who in zip(built.obs.samples, built.truth.who):
        for p, w in zip(s.people, who):
            out.setdefault(w, set()).add(p.track)
    return out


def test_tracking_keeps_every_players_id_through_a_fast_pan():
    # A pan of 0.3 of the frame in 0.36 s: people move 0.15 frame heights
    # between samples in the picture, more than the matching gate.
    built = (scene(duration=6, seed=4).players(10, x_range=(0.32, 0.98), wander=0.01)
             .camera((1.0, 0.0), (1.36, 0.3)).build())
    aspect = built.obs.geometry.aspect
    tracks = players.track(built.obs.samples, aspect)
    ids = _ids_by_player(built)
    on_floor = {w: v for w, v in ids.items() if w.startswith("player:")}
    assert len(on_floor) == 10 and all(len(v) == 1 for v in on_floor.values())
    assert len(set().union(*on_floor.values())) == 10                       # nobody shares an id
    assert sum(len(v) for v in tracks.values()) == sum(len(s.people) for s in built.obs.samples)
    # The pan really is what keeps them: without it, ids break at the pan.
    for s in built.obs.samples:
        s.pan = (0.0, 0.0)
    players.track(built.obs.samples, aspect)
    assert any(len(v) > 1 for w, v in _ids_by_player(built).items() if w.startswith("player:"))


def test_the_floor_players_are_told_from_the_crowd():
    built = scene(duration=4, seed=8).build()
    for s, who in zip(built.obs.samples, built.truth.who):
        floor = {who[s.people.index(p)] for p in players.floor_players(s)}
        assert floor == {w for w in who if w.startswith("player:")}


def test_a_closer_camera_scales_every_height_test():
    # A team's own network: its game camera shows the players 1.8 times as
    # tall as the national broadcasts the bars were set on (0.37-0.49 of the
    # height on its wide shots). Unscaled, most of them were "near the
    # camera": dropped from the floor players the crop follows, no frame
    # looked wide enough to learn the floor's colour from, and its court
    # shots were read as the crowd or close-ups.
    np = pytest.importorskip("numpy")
    from sports.basketball.crop import observe

    heights = (0.49, 0.44, 0.39, 0.37, 0.33, 0.3, 0.28)
    floor = [Person(0.1 + 0.12 * k, 0.75 - h / 2 + 0.2, 0.06, h) for k, h in enumerate(heights)]
    close = [Person(0.5, 0.55, 0.45, 0.95)]

    def sample(i, people, scale):
        return Sample(i=i, frame=i, t=i / 4, people=people, scale=scale)

    for scale, kept in ((1.0, 3), (1.8, 7)):
        assert len(players.floor_players(sample(0, floor, scale))) == kept
    assert shots._look(sample(0, floor, 1.0))[0] == 0.49
    assert shots._look(sample(0, floor, 1.8))[0] == pytest.approx(0.49 / 1.8)
    assert shots._look(sample(0, close, 1.8))[0] >= shots.TALL            # a close-up is still one
    # The floor's colour, learned from the frames that look wide: the court's
    # (hue bin 3), not the close-ups' (hue bin 9) that outnumber them in this clip.
    court_bin, face_bin = np.zeros(observe.COURT_BINS), np.zeros(observe.COURT_BINS)
    court_bin[3, 3], face_bin[9, 6] = 100.0, 100.0
    for scale, share in ((1.0, 0.0), (1.8, 1.0)):
        samples = ([sample(i, floor, scale) for i in range(observe.WIDE_MIN)]
                   + [sample(10 + i, close, scale) for i in range(observe.WIDE_MIN + 2)])
        hists = [court_bin] * observe.WIDE_MIN + [face_bin] * (observe.WIDE_MIN + 2)
        assert observe.court_shares(hists, samples)[0] == share


def test_the_stands_line_drops_tall_enough_people_high_in_the_frame():
    # A far camera: players 0.14 tall, so fans 0.1 tall pass the height test.
    floor = [Person(0.1 + 0.08 * k, 0.75 - 0.07 - 0.02 * (k % 3), 0.033, 0.14) for k in range(8)]
    fans = [Person(0.3, 0.2, 0.024, 0.1), Person(0.6, 0.22, 0.024, 0.1)]
    s = Sample(i=0, frame=0, t=0.0, people=floor + fans)
    assert players.floor_players(s) == floor


HOMES = [(0.06, 0.62), (0.12, 0.84), (0.2, 0.66), (0.3, 0.75), (0.86, 0.6),
         (0.9, 0.82), (0.7, 0.7), (0.95, 0.66), (0.84, 0.74), (0.04, 0.8)]


def test_the_handler_is_the_player_dribbling_and_nobody_while_the_pass_is_in_the_air():
    built = (scene(duration=6, seed=9).players(10, homes=HOMES, wander=0.0)
             .ball_dribble(1.0, 4.0, player=3)
             .ball_pass(4.0, 4.6, frm=3, to=6)
             .ball_dribble(4.6, 6.0, player=6)
             .build())
    aspect = built.obs.geometry.aspect
    players.track(built.obs.samples, aspect)
    checked = {"dribble": 0, "air": 0, "catch": 0}
    for i, s in enumerate(built.obs.samples):
        ball, who = built.truth.ball_point(i), built.truth.who[i]
        ids = {w: p.track for p, w in zip(s.people, who)}
        got = players.handler(s, ball, aspect)
        if 1.1 <= s.t <= 3.9 and "player:3" in ids:
            assert got == ids["player:3"]
            checked["dribble"] += 1
            near = {who[s.people.index(p)] for p in players.active(s, ball, got)}
            assert near == {w for w in who if w in ("player:2", "player:3")}
        elif 4.15 <= s.t <= 4.45:
            assert got is None
            checked["air"] += 1
        elif 4.7 <= s.t and "player:6" in ids:
            assert got == ids["player:6"]
            checked["catch"] += 1
    assert checked["dribble"] >= 20 and checked["air"] >= 3 and checked["catch"] >= 10
    # Without the ball, the handler's own position says who's active.
    s = built.between(2.0, 2.1)[0]
    lost = BallPoint(t=s.t)
    handler = next(p for p, w in zip(s.people, built.truth.who[s.i]) if w == "player:3")
    by_id, by_person = players.active(s, lost, handler.track), players.active(s, lost, handler)
    assert handler in by_id and by_id == by_person


# ---- the scene itself, and purity -------------------------------------------------------------


def test_the_scene_is_the_same_every_time():
    def make():
        return (scene(duration=5, seed=11).basket("right").false_ball("logo").false_ball("head")
                .ball_dribble(0.5, 2.0, player=1).ball_shot(2.5, 3.6, shooter=1).build())
    a, b = make(), make()
    assert a.obs == b.obs and a.truth == b.truth
    assert "logo" in {lb for labels in a.truth.labels for lb in labels}


def test_the_planning_core_imports_without_numpy_or_cv2():
    # Every module here but observe, sources, rim_finder and debug must run
    # on CI, where neither is installed: add each as it lands.
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "for name in ('numpy', 'cv2', 'torch', 'ultralytics'):\n"
        "    sys.modules[name] = None   # importing any of them now raises ImportError\n"
        "try:\n"
        "    import numpy\n"
        "    raise SystemExit('numpy was not blocked')\n"
        "except ImportError:\n"
        "    pass\n"
        "import sports.basketball.crop.model, sports.basketball.crop.shots, sports.basketball.crop.players\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr


# ==== basket: rim, backboard, the unit and which basket (sports/basketball/crop/basket.py) ======

import statistics  # noqa: E402

import pytest  # noqa: E402

from sports.basketball.crop import basket  # noqa: E402
from sports.basketball.crop.model import Basket, Hints, RimCand  # noqa: E402

# How far a basket's edges may be from the truth: the scene's found boards are off by gaussian
# noise of 0.002 and its rims by 0.0015 and 5% of their width; 30 seeds' worst was 0.0081.
EDGE_TOL = 0.01


def _baskets(built, notes=None) -> dict[int, list[list[Basket]]]:
    return basket.baskets(built.obs.samples, _shots_of(built), aspect=built.obs.geometry.aspect, notes=notes)


def _rw(b: Basket) -> float:
    return b.rim[2] - b.rim[0]


def test_basket_on_the_right_with_its_backboard_found_holds_the_board_and_the_margin():
    built = scene(duration=4, seed=21).basket("right", backboard=True).build()
    rows = _baskets(built)[0]
    board = built.truth.baskets["right"]["backboard"]
    sources = set()
    for row in rows:
        assert len(row) == 1
        b = row[0]
        assert (b.side, b.in_frame, b.clipped) == ("right", True, "")
        # The crop's edge on the baseline side sits m_out past the board's outer edge...
        assert b.unit[1] == pytest.approx(max(b.rim[2], b.backboard[2]) + basket.m_out(_rw(b)))
        assert abs(b.unit[1] - (board[2] + basket.m_out(_rw(b)))) < EDGE_TOL
        # ...and only M_IN inside the rim on the court side.
        assert b.unit[0] == pytest.approx(min(b.rim[0], b.backboard[0]) - basket.M_IN)
        sources.add(b.bb_src)
    # Samples that didn't find the board carry the one found on the others.
    assert sources == {"found", "model"}
    assert len({b.id for row in rows for b in row}) == 1


def test_with_no_backboard_found_the_allowance_reaches_past_the_rim_on_the_baseline_side():
    # A board reaching 1.26 rim widths past the rim: farther than 95% of those measured on broadcasts.
    built = scene(duration=4, seed=22).basket("right", backboard=False, bb_out=1.26).build()
    board = built.truth.baskets["right"]["backboard"]
    for row in _baskets(built)[0]:
        b = row[0]
        rw = _rw(b)
        assert (b.bb_src, b.side) == ("allowance", "right")      # the players are to its left
        assert b.backboard[0] == pytest.approx(b.rim[0] + 0.3 * rw)
        assert b.backboard[2] == pytest.approx(b.rim[2] + basket.ALLOW_OUT * rw)
        assert b.unit == pytest.approx((b.rim[0] - basket.M_IN, b.rim[2] + basket.ALLOW_OUT * rw + basket.m_out(rw)))
        # The estimate covers the real board, with the margin past it.
        assert b.unit[1] >= board[2] + basket.m_out(rw) - 0.003


def test_with_the_baseline_side_unknown_the_allowance_is_symmetric():
    # The rim near the middle of the picture, the players spread evenly about it, no board found.
    homes = [(0.55 + d, feet) for d, feet in ((-0.3, 0.62), (-0.2, 0.8), (-0.1, 0.7),
                                              (0.1, 0.7), (0.2, 0.8), (0.3, 0.62))]
    built = (scene(duration=4, seed=23).players(6, homes=homes, wander=0.0, detect=1.0)
             .basket("right", x=0.55, backboard=False).build())
    for row in _baskets(built)[0]:
        b = row[0]
        rw, mid = _rw(b), (b.rim[0] + b.rim[2]) / 2
        assert (b.side, b.bb_src) == ("unknown", "allowance")
        reach = rw / 2 + basket.ALLOW_OUT * rw + basket.m_out(rw)
        assert b.unit == pytest.approx((mid - reach, mid + reach))


def test_a_backboard_past_the_frame_edge_clips_the_unit_and_leaves_a_note():
    confs = {}
    for found in (True, False):
        notes: list[str] = []
        built = scene(duration=4, seed=24).basket("right", x=0.975, backboard=found, bb_rate=1.0).build()
        rows = _baskets(built, notes)[0]
        for row in rows:
            b = row[0]
            assert b.unit[1] == 1.0 and b.in_frame and b.clipped == "right"
            assert b.unit[0] == pytest.approx(b.rim[0] - basket.M_IN)
        assert len(notes) == 1 and notes[0].startswith("the broadcast cuts the backboard")
        confs[found] = statistics.median(row[0].conf for row in rows)
    # The same basket with its board in the picture: no note, and surer.
    notes = []
    rows = _baskets(scene(duration=4, seed=24).basket("right", backboard=True, bb_rate=1.0).build(), notes)[0]
    assert notes == [] and all(row[0].clipped == "" for row in rows)
    assert statistics.median(row[0].conf for row in rows) > confs[True]


def test_a_basket_is_carried_through_a_pan_where_its_rim_is_not_seen():
    built = scene(duration=6, seed=25).basket("right", x=0.8).camera((1.5, 0.0), (3.5, 0.3)).build()
    for s in built.samples:
        if 1.8 <= s.t < 3.2:          # nothing finds the rim mid-pan
            s.rims = []
    rows = _baskets(built)[0]
    carried = []
    for s, row in zip(built.samples, rows):
        b = row[0]
        rim, board = built.truth.rims[s.i]["right"], built.truth.backboards[s.i]["right"]
        assert abs((b.rim[0] + b.rim[2]) / 2 - (rim[0] + rim[2]) / 2) < 0.007
        assert abs(b.unit[1] - (board[2] + basket.m_out(_rw(b)))) < EDGE_TOL     # the board moves with it
        if 1.8 <= s.t < 3.2:
            assert (b.status, b.bb_src, b.in_frame) == ("propagated", "model", True)
            carried.append(b)
    assert len(carried) >= 13
    # It crossed a good part of the picture while unseen, on the camera's travel alone.
    assert carried[0].rim[0] - carried[-1].rim[0] > 0.18


def test_two_baskets_in_one_shot_are_kept_apart_with_their_own_sides_and_ids():
    built = scene(duration=5, seed=26).basket("left").basket("right").cut_at(2.5).build()
    found = _baskets(built)
    truth = built.truth.baskets
    assert len(found) == 2
    ids = []
    for rows in found.values():
        for row in rows:
            assert [b.side for b in row] == ["left", "right"]
            left, right = row
            assert left.unit[0] <= truth["left"]["backboard"][0] - basket.m_out(_rw(left)) + EDGE_TOL
            assert left.unit[1] == pytest.approx(left.rim[2] + basket.M_IN)
            assert right.unit[1] >= truth["right"]["backboard"][2] + basket.m_out(_rw(right)) - EDGE_TOL
            assert right.unit[0] == pytest.approx(right.rim[0] - basket.M_IN)
        ids.append({b.id for row in rows for b in row})
    # Two per shot, and no id is shared between shots.
    assert [len(i) for i in ids] == [2, 2] and not ids[0] & ids[1]


def test_a_rim_seen_a_few_times_is_no_basket():
    built = scene(duration=4, seed=27).basket("right").build()
    for s in (built.samples[5], built.samples[20], built.samples[35]):
        s.rims.append(RimCand(x=0.4, y=0.3, w=0.02, h=0.012, score=0.9))   # 3 of 40 samples where it'd show
    assert all([b.side for b in row] == ["right"] for row in _baskets(built)[0])
    assert all(row == [] for row in _baskets(scene(duration=3, seed=28).build())[0])


def test_the_relevant_basket_is_the_one_the_release_heads_for():
    for target in ("left", "right"):
        built = (scene(duration=6, seed=31).players(10, homes=HOMES, wander=0.0).basket("left").basket("right")
                 .ball_dribble(0.5, 2.5, player=3).ball_shot(2.5, 3.6, shooter=3, basket=target).build())
        rows = _baskets(built)[0]
        ids = {b.side: b.id for b in rows[0]}
        balls = [built.truth.ball_point(i) for i in range(len(built.samples))]
        assert basket.choose(rows, balls, {"release": 2.5}) == (ids[target], "the ball reaches its rim")
        # Seen only until 0.3 s after the release: its velocity still says where it goes.
        early = [bp if bp.t <= 2.8 else BallPoint(t=bp.t) for bp in balls]
        assert basket.choose(rows, early, {"release": 2.5}) == (ids[target], "the release heads for it")
        # The moment alone (the score bug's time) is enough too.
        assert basket.relevant(rows, balls, Hints(moment_t=3.6)) == ids[target]


def test_without_a_shot_the_attack_direction_then_the_basket_in_view_decide():
    times = [k / 10 for k in range(30)]

    def rows_at(*xs):
        return [[Basket(id=k, shot=0, t=t, rim=(x - 0.011, 0.35, x + 0.011, 0.37), backboard=None,
                        in_frame=0.0 <= x <= 1.0) for k, x in enumerate(xs)] for t in times]

    two = rows_at(0.14, 0.86)
    lost = [BallPoint(t=t) for t in times]
    running = [BallPoint(t=t, x=0.5, y=0.7, status="detected", conf=0.8, vx=0.6) for t in times]
    assert basket.choose(two, running) == (1, "the attack direction")
    # No ball: the camera panning left says the attack goes left.
    panning = [Sample(i=k, frame=6 * k, t=t, pan=(-0.1 * t, 0.0)) for k, t in enumerate(times)]
    assert basket.choose(two, lost, samples=panning) == (0, "the attack direction")
    # Nothing moving: the only basket in the picture...
    assert basket.choose(rows_at(0.3, 1.25), lost) == (0, "the only one in view")
    # ...else the one nearest the ball.
    still = [BallPoint(t=t, x=0.8, y=0.7, status="detected", conf=0.8) for t in times]
    assert basket.choose(two, still, Hints(moment_t=2.0)) == (1, "nearest the ball")
    assert basket.relevant([[] for _ in times], still) is None


def test_the_basket_module_imports_without_numpy_or_cv2():
    code = ("import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "for name in ('numpy', 'cv2', 'torch', 'ultralytics'):\n"
            "    sys.modules[name] = None\n"
            "import sports.basketball.crop.basket\n"
            "print('ok')\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr


# ==== ball: one ball path per camera shot (sports/basketball/crop/ball.py) =====================

from sports.basketball.crop import ball  # noqa: E402

# Two players facing each other across the frame, everyone else out at the sides, so a pass
# between them crosses open floor.
ACROSS = [(0.2, 0.7), (0.75, 0.7), (0.05, 0.62), (0.1, 0.84), (0.93, 0.6), (0.96, 0.8),
          (0.89, 0.7), (0.04, 0.74), (0.12, 0.6), (0.9, 0.86)]


def _track(built, debug: list | None = None) -> list[BallPoint]:
    """ball.track_shot on each real camera shot; the points line up with obs.samples."""
    aspect = built.obs.geometry.aspect
    points: list[BallPoint] = []
    for shot in built.truth.shots:
        own = [s for s in built.obs.samples if shot.f0 <= s.frame < shot.f1]
        info: dict = {}
        points += ball.track_shot(own, {"aspect": aspect, "wide": shot.kind == "wide",
                                        "bug_box": built.obs.bug_box}, info)
        if debug is not None:
            debug.append(info)
    return points


def _chosen(built, debug: list) -> list[str]:
    """What each detection in the chosen chains really was: ball, logo, head, shoe or noise."""
    out = []
    for info in debug:
        for track in info["chain"].used(info["tracklets"]):
            for i, cand in track:
                s = built.obs.samples[i]
                out.append(built.truth.labels[i][next(n for n, c in enumerate(s.balls) if c is cand)])
    return out


def _labelled(built, label: str) -> list:
    return [c for s, labels in zip(built.obs.samples, built.truth.labels) for c, lb in zip(s.balls, labels)
            if lb == label]


# ---- occlusion (test D) -----------------------------------------------------------------------


def test_half_a_second_hidden_is_bridged_with_a_lower_conf():
    # In flight: a long pass hidden for 0.5 s in the middle.
    built = (scene(duration=6, seed=31).players(10, homes=ACROSS).ball_detection(rate=1.0)
             .ball_dribble(0.5, 2.0, player=0).ball_pass(2.0, 3.4, frm=0, to=1).ball_dribble(3.4, 6.0, player=1)
             .hide_ball(2.45, 2.95).build())
    points = _track(built)
    hidden = [k for k, s in enumerate(built.obs.samples) if 2.45 <= s.t < 2.95]
    assert len(hidden) == 5
    seen = [p.conf for p in points if p.status == "detected" and 1.8 <= p.t <= 3.6]
    for k in hidden:
        p, (tx, _) = points[k], built.truth.ball[k]
        assert (p.status, p.via) == ("interpolated", "bridge")
        assert p.conf < min(seen) and ball.usable(p)
        assert abs(p.x - tx) < 0.02
    # In a player's hands: 0.5 s of dribbling hidden, placed at his hands.
    built = (scene(duration=6, seed=32).players(10, homes=ACROSS).ball_detection(rate=1.0)
             .ball_dribble(0.5, 5.5, player=0).hide_ball(2.45, 2.95).build())
    points = _track(built)
    for k, s in enumerate(built.obs.samples):
        if 2.45 <= s.t < 2.95:
            assert (points[k].status, points[k].via) == ("interpolated", "carried")
            assert points[k].conf == ball.BRIDGE_CONF * ball.CARRY_FACTOR and ball.usable(points[k])
            assert abs(points[k].x - built.truth.ball[k][0]) < 0.03


def test_a_second_and_a_half_hidden_goes_missing_with_short_predictions():
    built = (scene(duration=6, seed=33).players(10, homes=ACROSS).ball_detection(rate=1.0)
             .ball_dribble(0.5, 6.0, player=0).hide_ball(2.0, 3.5).build())
    points = _track(built)
    last_seen = max(p.t for p in points if p.t < 2.0 and p.status == "detected")
    next_seen = min(p.t for p in points if p.t >= 3.5 and p.status == "detected")
    gap = [p for p in points if last_seen < p.t < next_seen]
    assert {p.status for p in gap} == {"predicted", "missing"}
    assert any(p.status == "missing" for p in gap if 2.5 <= p.t <= 3.0)
    for p in gap:
        if p.status == "predicted":
            assert p.t - last_seen < ball.PREDICT_AHEAD or next_seen - p.t < ball.PREDICT_BACK
            assert 0 < p.conf <= ball.PREDICT_CONF
    assert max(p.t for p in gap if p.status == "predicted" and p.t < 2.75) - last_seen <= 0.4


def test_a_still_decoy_does_not_take_the_ball_that_turns_up_far_off():
    # A 2019 game-winner: the ball hid in the net long enough for its tracklet to end,
    # a decoy at the frame's edge stood still for two samples, and the decoy's tracklet
    # took the ball back at the rim a pass-length away: the crop swung to the edge and
    # back. A tracklet that has stood still reaches only GATE_SLOW x dt; a passed ball
    # starts its own tracklet, which the chain links.
    from sports.basketball.crop.model import BallCand

    dt = 1 / 6
    samples, gated = [], []
    for k in range(12):
        t = round(k * dt, 4)
        cands = []
        if k in (3, 4):
            cands.append(BallCand(x=0.17, y=0.6, w=0.02, h=0.035, conf=0.5, score=0.55))   # the decoy
        if k >= 6:                                                                        # the ball, 0.33 s on
            cands.append(BallCand(x=0.45, y=0.55 - 0.01 * (k - 6), w=0.02, h=0.035, conf=0.8, score=0.8))
        samples.append(Sample(i=k, frame=round(t * FPS), t=t, balls=cands))
        gated.append(cands)
    aspect = 16 / 9
    jump = ((0.45 - 0.17) * aspect, 0.05)
    assert (jump[0] ** 2 + jump[1] ** 2) ** 0.5 <= ball.GATE_R + 0.5 * ball.V_MAX * 2 * dt   # the old gate took it
    tracks = ball.tracklets(samples, gated, aspect)
    decoy = [tr for tr in tracks if any(c.x == 0.17 for _, c in tr)]
    assert len(decoy) == 1 and all(c.x == 0.17 for _, c in decoy[0])
    assert any(len(tr) == 6 and all(c.x == 0.45 for _, c in tr) for tr in tracks)


def test_a_jump_no_ball_can_make_restarts_the_chain_and_is_never_bridged():
    # Player 0's dribble, then, 0.1 s later, player 1's half a frame away.
    homes = [(0.2, 0.7), (0.7, 0.7), *ACROSS[2:]]
    built = (scene(duration=6, seed=34).players(10, homes=homes).ball_detection(rate=1.0)
             .ball_dribble(0.5, 3.0, player=0).ball_dribble(3.0, 6.0, player=1).build())
    debug: list = []
    points = _track(built, debug)
    chain = debug[0]["chain"]
    assert chain.links.count("restart") == 1
    used = chain.used(debug[0]["tracklets"])
    at = chain.links.index("restart")
    (_, a), (_, b) = used[at][-1], used[at + 1][0]
    (ta, xa, ya), (tb, xb, yb) = (ball.path_of(used[n], built.obs.samples)[end] for n, end in ((at, -1), (at + 1, 0)))
    assert abs(b.x - a.x) >= 0.45 and tb - ta <= 0.25        # half the frame in a moment
    assert ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5 > ball.LINK_SLACK + ball.V_MAX * (tb - ta)
    assert all(p.via == "" for p in points)                       # nothing bridged anywhere
    for k, p in enumerate(points):
        if ball.usable(p) and p.status == "detected":
            assert abs(p.x - built.truth.ball[k][0]) < 0.02    # each side follows its own ball


def test_nothing_is_bridged_across_a_cut():
    def make(cut: bool):
        sc = (scene(duration=8, seed=35).players(10, homes=ACROSS).ball_detection(rate=1.0)
              .ball_dribble(0.5, 8.0, player=0).hide_ball(3.75, 4.25))
        return (sc.cut_at(4.0) if cut else sc).build()
    # Without the cut, the hidden 0.5 s are carried in his hands...
    points = _track(make(cut=False))
    assert {p.status for p in points if 3.75 <= p.t < 4.25} == {"interpolated"}
    # ...with it, each shot is tracked on its own: predictions at most, each from its own shot's side.
    built = make(cut=True)
    cut = built.truth.cuts[0][0] / FPS
    points = _track(built)
    last = max(p.t for p in points if p.status == "detected" and p.t < cut)
    first = min(p.t for p in points if p.status == "detected" and p.t >= cut)
    near = [p for p in points if last < p.t < first]
    assert {p.status for p in near} == {"predicted", "missing"}
    assert all(p.t - last < ball.PREDICT_AHEAD for p in near if p.status == "predicted" and p.t < cut)
    assert all(first - p.t < ball.PREDICT_BACK for p in near if p.status == "predicted" and p.t >= cut)


# ---- false balls ------------------------------------------------------------------------------


def _play(seed: int, rate: float, homes=None):
    return (scene(duration=12, seed=seed).players(10, homes=homes).ball_detection(rate=rate).basket("right")
            .ball_dribble(0.5, 3.0, player=0).ball_pass(3.0, 3.6, frm=0, to=3).ball_dribble(3.6, 5.0, player=3)
            .ball_shot(5.0, 6.3, shooter=3))


# Player 8 stands right in front of the floor logo at (0.5, 0.74) the whole time.
PARKED = [(0.2, 0.7), (0.35, 0.8), (0.45, 0.65), (0.55, 0.78), (0.62, 0.62),
          (0.3, 0.6), (0.7, 0.84), (0.15, 0.82), (0.5, 0.88), (0.25, 0.86)]


def test_a_floor_logo_is_static_and_never_the_ball():
    for seed, homes, pan in ((41, None, False), (42, PARKED, False), (43, PARKED, True), (44, None, True)):
        sc = _play(seed, 0.35, homes).false_ball("logo").false_ball("noise")
        built = (sc.camera((2.0, 0.0), (4.0, 0.15)) if pan else sc).build()
        debug: list = []
        _track(built, debug)
        logos = _labelled(built, "logo")
        assert len(logos) > 30 and all("t" in c.rejected for c in logos)
        assert "logo" not in _chosen(built, debug)
        # The real ball is only taken for a fixed mark when it sits right on the logo (dribbled
        # over it) or lies still on the floor with nobody holding it (after the basket).
        for k, (s, labels) in enumerate(zip(built.obs.samples, built.truth.labels)):
            for c, lb in zip(s.balls, labels):
                if lb == "ball" and "t" in c.rejected:
                    on_logo = abs(c.x + s.pan[0] - 0.5) < 0.02 and abs(c.y + s.pan[1] - 0.74) < 0.03
                    dead = built.truth.handler[k] is None and built.truth.ball_v[k] == (0.0, 0.0)
                    assert on_logo or dead
    # A mark that stays put in the picture (a screen overlay) while the camera pans is static too.
    built = _play(45, 0.6).camera((2.0, 0.0), (4.0, 0.15)).false_ball("logo", x=0.3, y=0.4, world=False).build()
    _track(built)
    assert all("t" in c.rejected for c in _labelled(built, "logo"))


def test_a_head_in_the_crowd_is_rejected():
    for seed in (46, 47):
        built = _play(seed, 0.35).false_ball("head", rate=0.6).false_ball("head", fan=2, rate=0.6).build()
        debug: list = []
        _track(built, debug)
        heads = _labelled(built, "head")
        assert len(heads) > 30 and all("h" in c.rejected for c in heads)   # even when the fan himself was missed
        assert "head" not in _chosen(built, debug)


def test_an_orange_shoe_at_a_players_feet_is_penalised():
    # The same candidate in his hands and at his feet: the feet cost exactly the penalty.
    him = Person(0.5, 0.6, 0.052, 0.22)
    hands, feet = (ball.BallCand(0.51, y, 0.015, 0.026, 0.5, orange=0.8) for y in (0.58, 0.705))
    ball.gate(Sample(i=0, frame=0, t=0.0, people=[him], balls=[hands, feet]), {"player_h": 0.22, "aspect": 16 / 9})
    assert not hands.rejected and not feet.rejected
    assert abs(hands.score - feet.score - ball.W_FEET) < 1e-6
    # In play: shoes seen often at the feet of two players away from the ball are never taken for it,
    # not even while the ball goes unseen.
    homes = [(0.15, 0.7), (0.5, 0.7), (0.85, 0.62), (0.92, 0.75), (0.04, 0.6), (0.06, 0.84), (0.97, 0.6),
             (0.78, 0.86), (0.97, 0.84), (0.3, 0.86)]
    built = (scene(duration=10, seed=48).players(10, homes=homes).ball_detection(rate=0.35)
             .ball_dribble(0.5, 3.0, player=0).ball_pass(3.0, 3.6, frm=0, to=1).ball_dribble(3.6, 10.0, player=1)
             .false_ball("shoe", player=2, rate=0.6).false_ball("shoe", player=3, rate=0.6).build())
    debug: list = []
    _track(built, debug)
    assert len(_labelled(built, "shoe")) > 60 and "shoe" not in _chosen(built, debug)


# ---- passes and shots -------------------------------------------------------------------------


def test_the_ball_follows_a_pass_from_one_handler_to_the_other():
    built = (scene(duration=8, seed=51).players(10, homes=ACROSS).ball_detection(rate=0.8)
             .ball_dribble(0.5, 3.0, player=0).ball_pass(3.0, 3.8, frm=0, to=1).ball_dribble(3.8, 8.0, player=1)
             .false_ball("noise").build())
    debug: list = []
    points = _track(built, debug)
    assert "restart" not in debug[0]["chain"].links
    aspect = built.obs.geometry.aspect
    flight = [k for k, s in enumerate(built.obs.samples) if 3.05 <= s.t <= 3.75]
    on_it = [k for k in flight if ball.usable(points[k]) and abs(points[k].x - built.truth.ball[k][0]) < 0.04]
    assert len(on_it) >= 0.8 * len(flight)
    # Who has it, by the tracked ball: player 0 before the pass, player 1 after (nobody, at
    # times, when the detector missed him or the bounce left his box; a stray sample or two at
    # most someone else, around a false ball taken in for a frame).
    for side, (t0, t1), want in (("A", (1.5, 2.9), "player:0"), ("B", (4.0, 6.0), "player:1")):
        named = []
        for k, s in enumerate(built.obs.samples):
            if t0 <= s.t <= t1 and ball.usable(points[k]):
                who = {p.track: w for p, w in zip(s.people, built.truth.who[k])}
                named.append(who.get(players.handler(s, points[k], aspect)))
        assert len(named) >= 10 and len(named) - named.count(want) - named.count(None) <= 2, side
        assert named.count(want) >= 0.6 * len(named), side


def test_a_shot_in_flight_is_predicted_along_its_arc():
    # On its own: a ball rising then falling, seen to 0.8 s, predicted 0.3 s on.
    arc = [(t / 10, 1.0 + 0.6 * t / 10, 0.5 - 0.6 * t / 10 + 0.5 * (t / 10) ** 2) for t in range(9)]
    x, y, vx, vy = ball.predict(arc, 1.1)
    assert abs(x - 1.66) < 1e-6 and abs(y - (0.5 - 0.66 + 0.605)) < 1e-6
    assert abs(vx - 0.6) < 1e-6 and abs(vy - (-0.6 + 1.1)) < 1e-6
    # Backward from the start of the same arc: the parabola too.
    assert abs(ball.predict(arc, -0.2)[1] - (0.5 + 0.12 + 0.02)) < 1e-6
    # In a scene: the ball is lost three quarters of the way to the rim.
    built = (scene(duration=8, seed=52).players(10, homes=ACROSS).ball_detection(rate=1.0).basket("right")
             .ball_dribble(0.5, 4.0, player=0).ball_shot(4.0, 5.2, shooter=0).hide_ball(4.95, 8.0).build())
    debug: list = []
    points = _track(built, debug)
    # Its dribble, up and down every 0.55 s, is no flight: it is carried on in a straight line.
    tracks = debug[0]["tracklets"]
    dribble = ball.path_of(next(tr for tr in tracks if len(tr) > 20), built.obs.samples)
    dribble = [p for p in dribble if p[0] < 3.5][-8:]
    near, far = (ball.predict(dribble, dribble[-1][0] + dt) for dt in (0.1, 0.3))
    assert near[2:] == far[2:]                                    # the same speed however far ahead
    last = max(p.t for p in points if p.status == "detected" and p.t < 4.95)
    ahead = [(k, p) for k, p in enumerate(points) if p.status == "predicted" and p.t > last]
    assert ahead and all(p.t - last < ball.PREDICT_AHEAD for _, p in ahead)
    on_arc = [(k, p) for k, p in ahead if p.t <= 5.2 + FRAME]   # until it reaches the rim
    assert len(on_arc) >= 3
    for k, p in on_arc:
        assert abs(p.x - built.truth.ball[k][0]) < 0.02 and abs(p.y - built.truth.ball[k][1]) < 0.03
    falls = [p.vy for _, p in on_arc]
    assert all(b > a + 0.05 for a, b in zip(falls, falls[1:]))   # faster and faster: it bends with gravity


# ---- determinism and purity ---------------------------------------------------------------------


def test_ball_tracking_is_the_same_every_time():
    def run():
        built = (_play(53, 0.35).false_ball("logo").false_ball("head").false_ball("shoe", player=2)
                 .false_ball("noise").cut_at(8.0).build())
        debug: list = []
        points = _track(built, debug)
        return built, points, [(d["chain"].ids, d["chain"].links) for d in debug]
    (built, a, chains_a), (_, b, chains_b) = run(), run()
    assert a == b and chains_a == chains_b
    assert [p.t for p in a] == [s.t for s in built.obs.samples]
    assert _track(built) == a                                     # and again on the same samples


def test_ball_tracking_imports_without_numpy_or_cv2():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "for name in ('numpy', 'cv2', 'torch', 'ultralytics'):\n"
        "    sys.modules[name] = None\n"
        "import sports.basketball.crop.ball\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr


# ---- it pans, it never zooms (the owner's House of Highlights reference) ---------------------------

from sports.basketball.crop import plan_clip, planner  # noqa: E402


def test_the_crop_pans_and_never_zooms_with_or_without_the_ball():
    # His reference holds one full-height 9:16 crop from first frame to last.
    # A shot whose ball the detector hardly sees stayed at 4:5 or 1:1 and
    # zoomed in for a moment whenever it was seen, then out again; after a
    # basket it widened to hold the players as they spread out.
    built = scene(duration=6, seed=3).players(10, homes=HOMES, wander=0.02).basket("right").build()
    plan = plan_clip(built.obs, Hints(moment_t=4.0, kind="made_2"))
    base = built.obs.geometry.base
    assert plan.w and all(abs(w - base) < 1e-5 for w in plan.w)
    dribble = (scene(duration=6, seed=9).players(10, homes=HOMES, wander=0.0).basket("right")
               .ball_dribble(1.0, 4.0, player=3).build())
    plan = plan_clip(dribble.obs, Hints(moment_t=4.5, kind="layup"))
    assert all(abs(w - dribble.obs.geometry.base) < 1e-5 for w in plan.w)
    # A graphic, or a shot nobody is found in (the back of a head read as one),
    # is held at 9:16 too: shown whole, it was a zoom out at the cut.
    card = scene(duration=6, seed=3).players(10, homes=HOMES).graphic(2.0, 4.0).build()
    plan = plan_clip(card.obs)
    assert all(abs(w - card.obs.geometry.base) < 1e-5 for w in plan.w)


def test_what_cant_fit_keeps_the_side_the_play_is_on():
    # The shooter in the corner and the basket across the floor: no zoom out,
    # the crop holds the side its focus (the ball) is on, not the middle,
    # which held neither of them.
    assert planner._contain(0.2, 0.3, (0.1, 0.9)) == pytest.approx(0.25)
    assert planner._contain(0.85, 0.3, (0.1, 0.9)) == pytest.approx(0.75)
    assert planner._contain(0.5, 0.3, (0.4, 0.55)) == pytest.approx(0.5)


def test_a_close_up_is_on_the_person_the_camera_is_on():
    # A blurred shoulder in front can be the biggest box: the crop sat on an
    # ear or the back of a head. Near the middle wins over a little bigger.
    front = Person(0.08, 0.6, 0.3, 0.9)
    subject = Person(0.52, 0.5, 0.26, 0.85)
    built = scene(duration=3, seed=4).closeup(0.0, 3.0, cuts=False).build()
    for s in built.obs.samples:
        s.people = [front, subject]
    data = planner.ShotData(shot=Shot(id=0, f0=0, f1=int(3 * FPS), t0=0.0, t1=3.0, kind="closeup"),
                            samples=built.obs.samples)
    cx, w = planner._person_shot(data, FPS, built.obs.geometry.levels())
    assert all(abs(x - 0.52) < 0.03 for x in cx[int(FPS):]) and w[0] == pytest.approx(built.obs.geometry.base, abs=1e-5)
