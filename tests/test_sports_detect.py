"""Finding a match's moments (sports/core/detect.py, sports/core/clips.py),
reading its scoreboard (sports/soccer/scoreboard.py) and framing it for 9:16
(sports/soccer/ball.py), on synthetic signals, synthetic flat-colour and
textured clips and the lines the OCR really read off a 2018 World Cup final's
score bug. No model runs here."""

import functools

import pytest

pytest.importorskip("yaml")

import sports
from core.models import ClipCandidate, Segment
from sports.core import clips, detect
from sports.soccer import scoreboard as sb


@pytest.fixture
def soccer():
    return sports.profile_for({"clips": {"sport": {"name": "soccer", "highlights": "best"}}})


def _profile(highlights="best", period="full"):
    return sports.profile_for({"clips": {"sport": {"name": "soccer", "highlights": highlights,
                                                   "period": period}}})


# ---- the scoreboard --------------------------------------------------------------


@pytest.mark.parametrize("line, score, teams, minute", [
    ("(> HOm  0 : 0AW0  16:47", (0, 0), ("HOM", "AWO"), 16),
    ("(HOM1:1AW037:37", (1, 1), ("HOM", "AWO"), 37),
    ("( HOM3 : 1AW0  60:07", (3, 1), ("HOM", "AWO"), 60),
    ("(HOM4·2|AW069:17", (4, 2), ("HOM", "AWO"), 69),
    ("(>HOM 4: 2 AW0  90:00 +2:", (4, 2), ("HOM", "AWO"), 90),
    ("HOM 0.0 AWO 8:27", (0, 0), ("HOM", "AWO"), 8),
    (">HOM | 1:O AW0  20:07", (1, 0), ("HOM", "AWO"), 20),     # a 0 read as the letter O
    ("HOM O-2 AWO 50:01", (0, 2), ("HOM", "AWO"), 50),
    ("1 - 0", (1, 0), None, None),
    ("23:27", None, None, 23),
])
def test_the_bug_is_read_as_the_ocr_really_reads_it(line, score, teams, minute):
    r = sb.parse([line])
    assert (r.score, r.teams, r.minute) == (score, teams, minute)


def _readings(scores):
    return [sb.Reading(t=float(t), score=s, teams=("HOM", "AWO") if s else None, visible=s is not None)
            for t, s in scores]


def test_a_goal_needs_two_readings_to_agree():
    board = sb.from_readings(_readings([(0, (0, 0)), (10, (0, 0)), (20, (1, 0)), (30, (1, 0))]))
    assert [(c.after, c.team, c.hi) for c in board.changes] == [((1, 0), "HOM", 20.0)]
    assert board.changes[0].lo <= 10 and board.changes[0].label() == "score 1-0 (HOM)"


def test_a_one_off_misread_is_not_a_goal():
    board = sb.from_readings(_readings([(0, (0, 0)), (10, (0, 0)), (20, (8, 0)), (30, (0, 0)),
                                        (40, (0, 0))]))
    assert board.changes == []


def test_two_goals_at_once_and_a_goal_taken_back_are_not_called():
    jump = sb.from_readings(_readings([(0, (0, 0)), (10, (0, 0)), (20, (2, 0)), (30, (2, 0))]))
    back = sb.from_readings(_readings([(0, (1, 0)), (10, (1, 0)), (20, (0, 0)), (30, (0, 0)),
                                       (40, (1, 0)), (50, (1, 0))]))
    assert jump.changes == []
    assert [c.after for c in back.changes] == [(1, 0)]


def test_the_halves_come_from_the_clock():
    readings = [sb.Reading(t=t, minute=m, visible=True) for t, m in
                [(100, 5), (1000, 20), (2800, 45), (3100, 46), (4000, 60)]]
    board = sb.from_readings(readings)
    assert board.halftime == 3100
    assert board.period_at(1000) == "first_half" and board.period_at(4000) == "second_half"
    assert sb.from_readings([]).period_at(1000) == ""


def test_the_match_minute_is_counted_the_way_a_match_report_counts_it():
    board = sb.from_readings([sb.parse(["HOM 0-0 AWO 17:23"])])
    board.readings[0].t = 1100.0
    assert board.minute_at(1100) == 18             # 17:23 on the clock is the 18th minute
    assert board.minute_at(1150) == 19             # 18:13, from the same reading
    assert board.minute_at(1400) is None           # too far from any reading to say


def test_a_misread_clock_digit_doesnt_move_the_minute():
    # The clock is 95 s behind the video; two readings misread a digit by 5 s.
    readings = [sb.Reading(t=float(t), clock=t - 95, minute=(t - 95) // 60, visible=True)
                for t in range(4000, 4100, 5)]
    readings[9].clock -= 5
    readings[10].clock += 5
    board = sb.from_readings(readings)
    assert board.minute_at(4145.5) == 68           # 67:30.5 on the clock
    assert board.minute_at(4155) == 68             # 67:40
    assert board.minute_at(4175) == 69             # 68:00


def test_the_bug_hidden_is_a_replay_sign():
    board = sb.from_readings(_readings([(0, (1, 0)), (10, None), (20, None), (30, (1, 0))]))
    assert board.hidden(5, 25) and not board.hidden(25, 35)


def test_the_box_is_found_where_a_score_shows():
    np = pytest.importorskip("numpy")  # CI installs only the light dependencies
    pytest.importorskip("cv2")
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    def ocr(img):
        return [([[40, 10], [80, 10], [80, 30], [40, 30]], "HOM 1 0 AWO", 0.9)]

    box = sb.find_box(lambda t: frame, 5400, ocr)
    assert box is not None and box[1] < 0.2


def test_once_the_teams_are_known_a_soft_reading_still_gives_the_score():
    # Soft text (an upscaled or filmed screen) loses the separator and reads
    # 0 as a letter; the flag in front of a code reads as a letter too.
    readings = [sb.parse([text]) for text in
                ["HOM 0:0 AWO 16:00", "DHOM 0:0 AWO 16:05", "HOM 0-0 AWO 16:10",
                 "HOMOOAW016:15", "DHOM1OAW017:02", "HOM1·OAW017:10", "HOM 1 AWO"]]
    for i, r in enumerate(readings):
        r.t = float(i * 10)
    board = sb.from_readings(readings)
    assert [r.score for r in board.readings] == [(0, 0), (0, 0), (0, 0), (0, 0), (1, 0), (1, 0), None]
    assert {r.teams for r in board.readings if r.teams} == {("HOM", "AWO")}
    assert [(c.after, c.team) for c in board.changes] == [((1, 0), "HOM")]


def test_the_box_is_the_score_and_its_clock_not_the_banners_beside_it():
    # A portrait frame's band holds a tall strip of stadium: the score run
    # and the clock a little apart from it make the box; an ad board on the
    # same row, and a banner on another, don't.
    lines = [((0.12, 0.28, 0.24, 0.39), "HOM"), ((0.29, 0.27, 0.43, 0.41), "0:0"),
             ((0.49, 0.28, 0.60, 0.39), "AWO"), ((0.76, 0.28, 0.90, 0.40), "15:07"),
             ((0.10, 0.70, 0.60, 0.80), "WORLD CUP FINAL 2026")]
    run = sb._score_lines(lines, aspect=0.39)
    assert [t for _b, t in run] == ["HOM", "0:0", "AWO", "15:07"]
    assert sb._score_lines(lines[4:], aspect=0.39) == []


def test_no_score_bug_means_no_board():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    board = sb.read(lambda t: frame, 600, find_ocr=lambda img: [], rec=lambda img: "")
    assert board.box is None and board.changes == []


# ---- finding the moments -----------------------------------------------------------


def _curve(n, spans):
    c = [0.0] * n
    for lo, hi, v in spans:
        for i in range(lo, hi + 1):
            c[i] = v
    return c


def _moments(profile, segments, crowd, voice=None, whistle=None, board=None, **kw):
    n = len(crowd)
    return detect.moments(profile, segments, curves={"crowd": crowd, "whistle": whistle or [0.0] * n},
                          voice=voice or [0.0] * n, board=board, video_end=float(n),
                          min_len=10, max_len=60, **kw)


def test_crowd_voice_and_the_commentary_make_a_goal(soccer):
    segs = [Segment(1000, 1004, "and it's in! GOOOAL! what a goal")]
    found = _moments(soccer, segs, _curve(3000, [(1002, 1010, 0.9)]), voice=_curve(3000, [(1001, 1003, 0.8)]))
    assert [(e.type, e.t) for e in found] == [("goal", 1000.0)]
    assert found[0].confidence == 1.0 and "crowd roar" in found[0].why()
    assert found[0].start == 986 and found[0].end == 1010


def test_commentary_alone_is_not_a_moment(soccer):
    segs = [Segment(500, 505, "remember that goal he scored last season?")]
    assert _moments(soccer, segs, [0.0] * 2000) == []


def test_a_roar_alone_is_a_big_moment_dated_back_for_the_lag(soccer):
    found = _moments(soccer, [], _curve(2000, [(600, 606, 0.8)]))
    assert [(e.type, e.t) for e in found] == [("big_moment", 600.0)]
    assert found[0].start == 600 - 1.5 - 10


def test_a_short_roar_is_a_near_miss_not_a_moment(soccer):
    assert _moments(soccer, [], _curve(2000, [(600, 600, 0.9)])) == []


def test_with_the_score_read_only_the_score_makes_a_goal(soccer):
    # "They've scored four!", said over a roar with the score read and
    # unchanged: talk about a goal, not one.
    segs = [Segment(2000, 2004, "they have scored four in a final")]
    readings = _readings([(t, (1, 0)) for t in range(1900, 2300, 20)])
    found = _moments(soccer, segs, _curve(3000, [(2001, 2004, 0.8)]), board=sb.from_readings(readings))
    assert [e.type for e in found] == ["big_moment"]
    # Without the score read there, the commentary and the crowd decide.
    unread = sb.from_readings(_readings([(t, (1, 0)) for t in range(100, 400, 20)]))
    found = _moments(soccer, segs, _curve(3000, [(2001, 2004, 0.8)]), board=unread)
    assert [e.type for e in found] == ["goal"]


def test_the_goal_again_soon_after_is_its_replay(soccer):
    segs = [Segment(1000, 1004, "GOOOAL! what a goal"),
            Segment(1040, 1046, "let's see it again, what a goal that is")]
    crowd = _curve(3000, [(1002, 1010, 0.9), (1042, 1046, 0.7)])
    found = _moments(soccer, segs, crowd, voice=_curve(3000, [(1001, 1003, 0.8)]))
    assert [e.is_replay for e in found] == [False, True]
    assert found[0].group == found[1].group


def test_the_scoreboard_confirms_the_goal_and_names_the_side(soccer):
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1030, (0, 1)), (1040, (0, 1))]))
    board.readings[2].teams = ("HOM", "AWO")
    found = _moments(soccer, [], _curve(3000, [(1002, 1010, 0.9)]), board=board)
    goal = next(e for e in found if e.type == "goal")
    assert goal.team == "AWO" and goal.confidence == 1.0 and "score 0-1 (AWO)" in goal.why()


def test_a_goal_only_the_scoreboard_saw_is_still_a_goal(soccer):
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1030, (1, 0)), (1040, (1, 0))]))
    found = _moments(soccer, [], [0.0] * 3000, board=board)
    assert [(e.type, e.t) for e in found] == [("goal", 1000.0)]


def test_the_crowd_dates_a_goal_the_scoreboard_confirms(soccer):
    # The roar lasts two seconds, too short for a moment of its own, and the
    # new score shows 40 s later: the goal is where the roar starts, less
    # the crowd's lag, and the clip holds the build-up and the celebration.
    board = sb.from_readings(_readings([(0, (0, 0)), (1000, (0, 0)), (1100, (1, 0)), (1110, (1, 0))]))
    found = _moments(soccer, [], _curve(3000, [(1060, 1061, 0.8)]), board=board)
    assert [(e.type, e.t) for e in found] == [("goal", 1058.5)]
    assert (found[0].start, found[0].end) == (1044.5, 1068.5)


def test_the_goal_is_the_loudest_roar_not_an_earlier_chance(soccer):
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1100, (1, 0)), (1110, (1, 0))]))
    # A chance's "ooh" is short; the goal's roar is louder and goes on.
    crowd = _curve(3000, [(950, 953, 0.6), (1060, 1070, 0.9)])
    found = _moments(soccer, [], crowd, board=board)
    assert [(e.type, e.t) for e in found] == [("big_moment", 950.0), ("goal", 1058.5)]


def test_a_goal_is_dated_after_the_score_before_it(soccer):
    # Two goals two minutes apart: the second's search stops at the first's
    # new score, so the first goal's louder roar can't be taken for it.
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1010, (1, 0)), (1020, (1, 0)),
                                        (1130, (2, 0)), (1140, (2, 0))]))
    crowd = _curve(3000, [(1000, 1010, 1.0), (1100, 1104, 0.5)])
    goals = [e for e in _moments(soccer, [], crowd, board=board) if e.type == "goal"]
    assert [e.t for e in goals] == [998.5, 1098.5]


def test_a_roar_soon_after_a_goal_is_its_celebration(soccer):
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1030, (1, 0)), (1040, (1, 0))]))
    crowd = _curve(3000, [(1000, 1008, 0.9), (1025, 1030, 0.8)])
    found = _moments(soccer, [], crowd, board=board)
    assert [(e.type, e.is_replay) for e in found] == [("goal", False), ("big_moment", True)]
    assert found[0].group == found[1].group


def test_goals_and_celebrations_keep_more_after(soccer):
    segs = [Segment(1000, 1004, "GOOOAL!")]
    found = _moments(soccer, segs, _curve(3000, [(1002, 1010, 0.9)]),
                     extra_after=12, extra_types={"goal"})
    assert found[0].end == 1000 + 10 + 12


# ---- from moments to clips -----------------------------------------------------------


def _cand(start, end, score):
    return ClipCandidate(start=start, end=end, score=score, subscores={})


def test_one_clip_per_moment_and_the_bonus_by_worth(soccer):
    segs = [Segment(1000, 1004, "GOOOAL! what a goal"), Segment(1040, 1046, "let's see it again")]
    found = _moments(soccer, segs, _curve(3000, [(1002, 1010, 0.9), (1042, 1046, 0.7)]),
                     voice=_curve(3000, [(1001, 1003, 0.8)]))
    goal_clip, replay_clip, other = _cand(990, 1015, 70), _cand(1035, 1060, 75), _cand(2000, 2030, 60)
    attached = clips.attach(found, [goal_clip, replay_clip, other])
    assert clips.bonus(attached[id(goal_clip)]) == 20 and clips.bonus(attached[id(replay_clip)]) == 0
    kept, dropped, _notes = clips.choose(soccer, [goal_clip, replay_clip, other], attached,
                                         min_score=55, max_len=60)
    assert goal_clip in kept and other in kept
    assert (replay_clip, "same_moment") in dropped


def test_all_goals_keeps_every_confirmed_goal_with_its_build_up():
    profile = _profile("goals")
    segs = [Segment(1000, 1004, "GOOOAL!"), Segment(2000, 2004, "what a save!")]
    found = _moments(profile, segs, _curve(3000, [(1002, 1010, 0.9), (2002, 2008, 0.8)]),
                     voice=_curve(3000, [(1001, 1003, 0.8), (2001, 2003, 0.8)]))
    late_goal, save = _cand(999, 1020, 40), _cand(1995, 2015, 90)
    attached = clips.attach(found, [late_goal, save])
    kept, dropped, _ = clips.choose(profile, [late_goal, save], attached, min_score=55, max_len=60)
    assert kept == [late_goal] and (save, "not_in_highlights") in dropped
    assert late_goal.score == 55 and late_goal.subscores["required"] == "Goal"
    assert (late_goal.start, late_goal.end) == (986, 1010)


def test_best_moments_keeps_a_goal_the_scoreboard_confirmed(soccer):
    # The words alone rated it 10; the score changed for it, so it's kept.
    board = sb.from_readings(_readings([(0, (0, 0)), (900, (0, 0)), (1030, (1, 0)), (1040, (1, 0))]))
    soccer.board = board
    found = _moments(soccer, [], _curve(3000, [(1002, 1010, 0.9)]), board=board)
    quiet_goal, other = _cand(986, 1010, 10), _cand(2000, 2030, 40)
    attached = clips.attach(found, [quiet_goal, other])
    kept, _dropped, _ = clips.choose(soccer, [quiet_goal, other], attached, min_score=55, max_len=60)
    assert quiet_goal in kept and other in kept
    assert quiet_goal.score == 55 and quiet_goal.subscores["required"] == "Goal"
    assert other.score == 40                      # a clip with no confirmed goal is left to its score


def test_the_footage_is_a_broadcast_when_a_score_box_is_on_screen(soccer):
    board = sb.from_readings(_readings([(0, (0, 0)), (10, (0, 0))]), box=(0.1, 0.05, 0.3, 0.1))
    assert soccer.resolve_footage(board) == "broadcast"
    assert soccer.resolve_footage(None) == "sideline"
    assert soccer.resolve_footage(sb.Scoreboard()) == "sideline"
    chosen = _profile("best")
    chosen.option["footage"] = "broadcast"
    assert chosen.resolve_footage(None) == "broadcast"


def test_club_footage_that_confirms_no_goal_gets_its_best_moments():
    # All goals, on footage with no score box and no commentary: rather than
    # nothing, the best moments, and a note that says why.
    profile = _profile("goals")
    profile.resolve_footage(None)
    found = _moments(profile, [], _curve(3000, [(1000, 1004, 0.9)]))
    moment, other = _cand(990, 1015, 70), _cand(2000, 2030, 60)
    attached = clips.attach(found, [moment, other])
    kept, dropped, notes = clips.choose(profile, [moment, other], attached, min_score=55, max_len=60)
    assert kept == [moment, other] and dropped == []
    assert notes and "best moments instead" in notes[0]
    assert clips.report(profile, found, kept, attached, notes)["footage"] == "sideline"
    # On a broadcast, a choice nothing matched still keeps nothing else.
    broadcast = _profile("goals")
    broadcast.resolve_footage(sb.from_readings([], box=(0.1, 0.05, 0.3, 0.1)))
    kept, _dropped, _notes = clips.choose(broadcast, [moment, other], attached, min_score=55, max_len=60)
    assert kept == []


def test_a_half_filter_keeps_what_it_cannot_place():
    profile = _profile("best", "second_half")
    profile.board = sb.from_readings([sb.Reading(t=t, minute=m, visible=True)
                                      for t, m in [(100, 5), (1000, 20), (2800, 45), (3100, 46), (4000, 60)]])
    first, second = _cand(1000, 1030, 70), _cand(4000, 4030, 70)
    kept, dropped, _ = clips.choose(profile, [first, second], {}, min_score=55, max_len=60)
    assert kept == [second] and (first, "other_period") in dropped
    blind = _profile("best", "second_half")
    kept, _, notes = clips.choose(blind, [first], {}, min_score=55, max_len=60)
    assert kept == [first] and notes


def test_the_report_counts_what_the_match_gave(soccer):
    segs = [Segment(1000, 1004, "GOOOAL!")]
    found = _moments(soccer, segs, _curve(3000, [(1002, 1010, 0.9), (2002, 2008, 0.8)]),
                     voice=_curve(3000, [(1001, 1003, 0.8)]))
    rep = clips.report(soccer, found, [], {}, [])
    assert rep["found"] == {"Goal": 1} and rep["big_moments"] == 1 and rep["sport"] == "Soccer"


# ---- framing a match for 9:16 ---------------------------------------------------------


FPS = 30.0      # the clips' frame rate in these tests: sample i (5 a second) is frame 6 * i


def _frames(n, per=None):
    """n samples at 5 per second; per[i] = dict of what sample i saw."""
    per = per or {}
    return [{"t": i / 5, "cut": False, "balls": [], "people": [], **per.get(i, {})} for i in range(n)]


def _plan(samples, crop=0.316):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.soccer.ball import plan

    return plan(samples, crop, FPS)


def _per_frame(path, last):
    """The crop centre the renderer gives frames 0..last (video/cropper.py interpolates
    between keys, at frame / fps)."""
    from video.cropper import _interpolate

    return [_interpolate(path, f / FPS) for f in range(last + 1)]


def test_the_crop_follows_the_ball():
    samples = _frames(40, {i: {"balls": [(0.3 + 0.01 * i, 0.5, 0.6)]} for i in range(40)})
    path, led = _plan(samples)
    assert path[0][1] == pytest.approx(0.31)     # the median of its first 0.5 s: 0.30, 0.31, 0.32
    assert path[-1][1] > 0.6 and led["ball"] == 40


def test_a_ball_that_couldnt_get_there_is_ignored():
    samples = _frames(10, {i: {"balls": [(0.3, 0.5, 0.6)]} for i in range(5)})
    samples[5]["balls"] = [(0.95, 0.5, 0.3)]        # a boot, a head, a crowd ball
    path, _ = _plan(samples)
    assert all(x < 0.5 for _t, x in path)


def test_a_weak_detection_follows_a_ball_but_never_starts_one():
    # A steward's vest at 0.2 on its own isn't the ball...
    lone = _frames(10, {i: {"balls": [(0.8, 0.5, 0.2)]} for i in range(10)})
    _path, led = _plan(lone)
    assert led["ball"] == 0
    # ...but a blurred 0.2 right where the ball just was is.
    followed = _frames(10, {0: {"balls": [(0.4, 0.5, 0.7)]}})
    for i in range(1, 10):
        followed[i]["balls"] = [(0.4 + 0.01 * i, 0.5, 0.2)]
    _path, led = _plan(followed)
    assert led["ball"] == 10


def test_without_the_ball_the_crop_frames_the_play_near_where_it_was():
    samples = _frames(30, {0: {"balls": [(0.7, 0.5, 0.6)]}})
    for s in samples[1:]:
        s["people"] = [(0.72, 0.5, 0.03, 0.1), (0.68, 0.5, 0.03, 0.1), (0.1, 0.5, 0.03, 0.1)]
    path, led = _plan(samples)
    assert path[-1][1] == pytest.approx(0.7, abs=0.05) and led["players"] > 0


def test_a_close_up_frames_the_player():
    samples = _frames(20, {i: {"balls": [(0.2, 0.5, 0.6)],
                                 "people": [(0.75, 0.5, 0.2, 0.8)]} for i in range(20)})
    path, led = _plan(samples)
    assert path[-1][1] > 0.6 and led["close-up"] == 20


def test_the_crop_stays_inside_the_frame():
    samples = _frames(10, {i: {"balls": [(0.01, 0.5, 0.6)]} for i in range(10)})
    path, _ = _plan(samples, crop=0.316)
    assert all(x >= 0.158 for _t, x in path)


# ---- cuts and openings ----------------------------------------------------------------
#
# The renderer interpolates the crop between keys. With one key per sample and
# the cut called on the sample after it, the crop slid across every cut for a
# sample gap (0.2 s); and a shot whose ball was confirmed a sample late opened
# on the players and slid to the ball over about two seconds.


def _cut_scene(cut_frame=57, after=None):
    """Ball at 0.2, then a cut on cut_frame (between samples 9 and 10, frames 54
    and 60, or on 60 itself) to a shot whose ball is at 0.8, or to `after`."""
    samples = _frames(20, {i: {"balls": [(0.2, 0.5, 0.6)]} for i in range(10)})
    for i in range(10, 20):
        samples[i].update(after or {"balls": [(0.8, 0.5, 0.6)]})
    samples[10].update(cut=True, cut_frame=cut_frame)
    return samples


@pytest.mark.parametrize("cut_frame", [55, 57, 60])
def test_a_cut_holds_the_old_framing_to_the_frame_before_it_and_snaps_on_it(cut_frame):
    path, _ = _plan(_cut_scene(cut_frame))
    keys = dict(zip(_key_frames(path), (x for _t, x in path)))
    # The freeze key (the sample's own when the cut is on the frame after it) and the snap key.
    assert keys[cut_frame - 1] == 0.2 and keys[cut_frame] == 0.8
    assert all(t0 < t1 for (t0, _), (t1, _) in zip(path, path[1:]))
    xs = _per_frame(path, 119)
    # Nothing in between, on any frame: the old framing to the frame before the cut,
    # the new one from the cut's own frame.
    assert all(x == pytest.approx(0.2, abs=1e-9) for x in xs[:cut_frame])
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[cut_frame:])


def test_a_cut_without_its_frame_is_on_its_sample_with_the_freeze_key_before_it():
    samples = _cut_scene()
    del samples[10]["cut_frame"]
    xs = _per_frame(_plan(samples)[0], 119)
    assert all(x == pytest.approx(0.2, abs=1e-9) for x in xs[:60])
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[60:])


_PLAYERS = [(0.25, 0.5, 0.03, 0.1), (0.3, 0.5, 0.03, 0.1), (0.35, 0.5, 0.03, 0.1)]


def test_the_clip_opens_on_the_ball_its_first_samples_agree_on():
    # On the first sample the ball is only a weak detection, which never starts one,
    # so that sample frames the players (0.3); from the next the ball is sure (0.7).
    samples = _frames(20, {0: {"balls": [(0.7, 0.5, 0.2)], "people": _PLAYERS}})
    for s in samples[1:]:
        s.update(balls=[(0.7, 0.5, 0.6)], people=_PLAYERS)
    path, led = _plan(samples)
    assert led["players"] == 1 and led["ball"] == 19          # what each sample saw is unchanged
    # The median of the first 0.5 s (0.3, 0.7, 0.7): on the ball from the first frame.
    # Opened on the first sample, the crop started at 0.3 and was still 0.01 short of
    # the ball two seconds in.
    assert all(x == pytest.approx(0.7, abs=1e-9) for x in _per_frame(path, 114))


def test_a_shot_opens_on_the_ball_its_first_samples_agree_on():
    samples = _cut_scene(57, {"balls": [(0.8, 0.5, 0.6)], "people": _PLAYERS})
    samples[10]["balls"] = [(0.8, 0.5, 0.2)]       # weak on the shot's first sample
    path, led = _plan(samples)
    assert led["players"] == 1
    # Opened on its first sample, the shot started on the players (0.3) and slid
    # toward the ball (0.787 by the clip's end).
    xs = _per_frame(path, 114)
    assert all(x == pytest.approx(0.2, abs=1e-9) for x in xs[:57])
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[57:])


def test_one_sample_off_the_play_does_not_open_the_clip():
    # A player filling the frame at 0.7 for the first sample only (crossing the
    # camera), then the play at 0.3: the median of 0.7, 0.3, 0.3. Opened on the first
    # sample, the crop started at 0.7 and slid back.
    samples = _frames(20, {i: {"people": _PLAYERS} for i in range(20)})
    samples[0]["people"] = [*_PLAYERS, (0.7, 0.5, 0.2, 0.8)]
    path, led = _plan(samples)
    assert led["close-up"] == 1
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in _per_frame(path, 114))


def test_a_shot_opens_on_what_its_first_half_second_shows():
    # Nothing is seen on the shot's first two samples, the ball (0.8) from its third
    # (0.4 s in). Those two samples don't count, and the second one holds the opening.
    # Opened on its first sample, the shot started on the centre and slid to the ball
    # from 0.4 s in.
    samples = _cut_scene(58, {"balls": [(0.8, 0.5, 0.6)]})
    samples[10]["balls"] = samples[11]["balls"] = []
    path, led = _plan(samples)
    assert led["held"] == 2
    xs = _per_frame(path, 114)
    assert all(x == pytest.approx(0.2, abs=1e-9) for x in xs[:58])
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[58:])


def test_the_trailing_median_starts_again_at_a_cut():
    # The new shot's second sample is 0.08 off its opening: with the shot's own
    # targets only (0.8, 0.72) the median is inside the hold; with the old shot's
    # 0.2 still in it, it would be 0.72, and the crop would move.
    samples = _cut_scene(58)
    samples[11]["balls"] = [(0.72, 0.5, 0.6)]
    xs = _per_frame(_plan(samples)[0], 114)
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[58:])


def test_the_ball_is_forgotten_at_a_cut():
    # After the cut a weak detection sits where the old shot's ball was. Followed, it
    # would hold the crop at 0.3; the cut clears the ball, a weak one never starts
    # one, and the new shot frames its players (0.8).
    far = [(0.75, 0.5, 0.03, 0.1), (0.8, 0.5, 0.03, 0.1), (0.85, 0.5, 0.03, 0.1)]
    samples = _frames(20, {i: {"balls": [(0.3, 0.5, 0.6)]} for i in range(10)})
    for s in samples[10:]:
        s.update(balls=[(0.32, 0.5, 0.2)], people=far)
    samples[10].update(cut=True, cut_frame=58)
    path, led = _plan(samples)
    assert led["ball"] == 10 and led["players"] == 10
    xs = _per_frame(path, 114)
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in xs[:58])
    assert all(x == pytest.approx(0.8, abs=1e-9) for x in xs[58:])


def test_a_shot_with_no_ball_and_no_players_keeps_the_centre():
    # As before, a shot nothing is seen in is framed on the centre; it now starts
    # on its first frame, with the framing before held to the frame before it.
    samples = _cut_scene(58, {"balls": [], "people": []})
    path, led = _plan(samples)
    assert led["held"] == 10
    xs = _per_frame(path, 114)
    assert all(x == pytest.approx(0.2, abs=1e-9) for x in xs[:58])
    assert all(x == pytest.approx(0.5, abs=1e-9) for x in xs[58:])
    # A clip that opens on nothing opens on the centre and eases to the ball when it
    # appears (sample 5), exactly as before: these are the keys the code before this
    # change wrote.
    samples = _frames(20, {i: {"balls": [(0.7, 0.5, 0.6)]} for i in range(5, 20)})
    path, _ = _plan(samples)
    assert [x for _t, x in path] == [0.5] * 6 + [0.57, 0.6155, 0.6451, 0.6643, 0.6768, 0.6849] + [0.6902] * 8


# compute(): cuts found to the frame on a synthetic clip. The capture is a fake one
# of flat-colour frames (one colour per shot) and the detector scripted per frame.

_W, _H = 320, 180
_A, _B, _WHITE = (150, 90, 30), (40, 210, 240), (255, 255, 255)   # gray 79 and 200: a hard cut


class _Clip:
    """A capture of n frames, colour(i) the flat colour of frame i (BGR), or the frame itself."""

    def __init__(self, colour, n):
        self.colour, self.n = colour, n
        self.pos, self.cur, self.released = 0, None, False

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2

        return {cv2.CAP_PROP_FPS: FPS, cv2.CAP_PROP_FRAME_WIDTH: float(_W),
                cv2.CAP_PROP_FRAME_HEIGHT: float(_H), cv2.CAP_PROP_FRAME_COUNT: float(self.n),
                cv2.CAP_PROP_POS_FRAMES: float(self.pos)}.get(prop, 0.0)

    def set(self, prop, value):
        import cv2

        if prop != cv2.CAP_PROP_POS_FRAMES:
            return False
        self.pos = int(value)
        return True

    def grab(self):
        if self.pos >= self.n:
            return False
        self.cur, self.pos = self.pos, self.pos + 1
        return True

    def retrieve(self):
        import numpy as np

        if self.cur is None:
            return False, None
        return True, np.full((_H, _W, 3), self.colour(self.cur), np.uint8)

    def read(self):
        return self.retrieve() if self.grab() else (False, None)

    def release(self):
        self.released = True


def _compute(monkeypatch, colour, sees, n=150, reader=True):
    """ball.compute on a synthetic clip; sees(frame) -> (balls, people) on that frame.
    reader=False: the cut reader's capture will not open. Returns (result, captures)."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import contextlib

    from sports.soccer import ball
    from video import capture

    opened = []

    @contextlib.contextmanager
    def fake(_path, *, required=True):
        cap = _Clip(colour, n) if required or reader else None
        opened.append(cap)
        try:
            yield cap
        finally:
            if cap is not None:
                cap.release()

    sampled = iter(range(0, n, 6))      # one detector call per sample, in order
    monkeypatch.setattr(capture, "video_capture", fake)
    monkeypatch.setattr(ball, "_model", lambda _name: None)
    monkeypatch.setattr(ball, "detect", lambda _model, _frame, _imgsz: sees(next(sampled)))
    return ball.compute("clip.mp4"), opened


def _key_frames(path):
    return [round(t * FPS, 6) for t, _x in path]


@pytest.mark.parametrize("cut", [61, 62, 64, 66])
def test_compute_finds_the_cut_on_its_frame_and_nothing_slides_across_it(cut, monkeypatch):
    result, opened = _compute(
        monkeypatch, lambda i: _A if i < cut else _B,
        lambda f: ([(0.3, 0.5, 0.6)] if f < cut else [(0.75, 0.5, 0.6)], []))
    path = result["path"]
    assert cut in _key_frames(path)
    assert all(t0 < t1 for (t0, _), (t1, _) in zip(path, path[1:]))
    xs = _per_frame(path, 149)
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in xs[:cut])
    assert all(x == pytest.approx(0.75, abs=1e-9) for x in xs[cut:])
    assert len(opened) == 2 and all(c.released for c in opened)


def test_compute_a_flash_is_not_a_cut(monkeypatch):
    # A two-frame flash on the sample at frame 66. After it the ball is only a weak
    # detection, followed because it is where the ball was. Called a cut (twice, on
    # the grid: into the flash and out of it), the flash cleared the ball, and the
    # crop went to the players at 0.8.
    far = [(0.75, 0.5, 0.03, 0.1), (0.8, 0.5, 0.03, 0.1), (0.85, 0.5, 0.03, 0.1)]
    result, _ = _compute(
        monkeypatch, lambda i: _WHITE if i in (66, 67) else _A,
        lambda f: ([(0.3, 0.5, 0.6 if f < 66 else 0.2)], far))
    path = result["path"]
    assert all(f % 6 == 0 for f in _key_frames(path))      # no freeze or snap keys
    assert all(x == pytest.approx(0.3, abs=1e-9) for _t, x in path)
    assert result["led"]["ball"] == 25


def test_compute_without_the_cut_reader_cuts_on_the_sample_as_before(monkeypatch):
    # The reader's capture will not open: a cut is called on the sample after it, when
    # the pair changes by more than 25 (and its colours by the gate's 0.2), as before,
    # but still with its freeze key.
    result, opened = _compute(
        monkeypatch, lambda i: _A if i < 62 else _B,
        lambda f: ([(0.3, 0.5, 0.6)] if f < 62 else [(0.75, 0.5, 0.6)], []), reader=False)
    assert opened[1] is None
    xs = _per_frame(result["path"], 149)
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in xs[:66])
    assert all(x == pytest.approx(0.75, abs=1e-9) for x in xs[66:])


def test_compute_turns_on_soccer_s_cut_options(monkeypatch):
    from video import cuts

    seen = {}

    class Spy(cuts.ShotCuts):
        def __init__(self, cap, fallback, **options):
            seen.update(options, fallback=fallback)
            super().__init__(cap, fallback, **options)

    monkeypatch.setattr(cuts, "ShotCuts", Spy)
    _compute(monkeypatch, lambda i: _A, lambda f: ([], []))
    assert seen == {"fallback": 25.0, "colour_gate": 0.2, "colour_led": True, "dissolves": True}


def test_compute_a_flash_just_after_a_cut_is_not_a_cut(monkeypatch):
    # A cut on frame 57, then a two-frame flash on the sample at 66. The frames decoded
    # around the flash reach back past the cut, so the old shot is still at their start:
    # what the flash test for dissolves compares the end of the window with is the
    # first sample too, which is the new shot, as the end is once the flash is over.
    result, _ = _compute(
        monkeypatch, lambda i: _WHITE if i in (66, 67) else _A if i < 57 else _B,
        lambda f: ([(0.3, 0.5, 0.6)] if f < 57 else [(0.75, 0.5, 0.6)], []))
    assert [f for f in _key_frames(result["path"]) if f % 6] == [56, 57]


def test_compute_hands_on_an_error_from_the_cut_reader(monkeypatch):
    # The cut checks run on a thread of their own; what goes wrong there is raised here.
    from video import cuts

    def broken(self, *args, **kwargs):
        raise RuntimeError("reader broke")

    monkeypatch.setattr(cuts.ShotCuts, "check", broken)
    with pytest.raises(RuntimeError, match="reader broke"):
        _compute(monkeypatch, lambda i: _A, lambda f: ([], []))


def test_compute_stops_the_cut_reader_when_it_fails(monkeypatch):
    import threading

    calls = iter(range(100))

    def sees(_frame):
        if next(calls) == 5:
            raise RuntimeError("detector broke")
        return [], []

    with pytest.raises(RuntimeError, match="detector broke"):
        _compute(monkeypatch, lambda i: _A, sees)
    assert not any(t.name == "soccer-cuts" and t.is_alive() for t in threading.enumerate())


# Broadcast football pans fast and fades into its replays. These frames are a fixed blocky
# texture over a flat colour: moved a few pixels a frame, it pans (the thumbnail changes a
# lot, the colours hardly at all); blended a step a frame into another, it dissolves.

_GREEN, _ORANGE, _PALE = (60, 140, 70), (200, 90, 40), (180, 170, 210)


@functools.cache
def _texture():
    np = pytest.importorskip("numpy")

    i = np.arange(_H // 8 + 1, dtype=np.int64)[:, None]
    j = np.arange(3 * _W // 8 + 1, dtype=np.int64)[None, :]
    blocks = (((i * 73856093) ^ (j * 19349663)) % 81 - 40).astype(np.int16)
    return np.kron(blocks, np.ones((8, 8), np.int16))[:_H, :3 * _W]


def _textured(colour, shift=0):
    """The texture over `colour`, moved `shift` pixels to the left (it wraps round)."""
    np = pytest.importorskip("numpy")

    tex = _texture()
    cols = (np.arange(_W) + shift) % tex.shape[1]
    return np.clip(np.asarray(colour, np.int16) + tex[:, cols, None], 0, 255).astype(np.uint8)


def _pan(step, cut=None):
    """Frame i of a pan of `step` pixels a frame over the green; from frame `cut`, the
    orange, still panning."""
    return lambda i: _textured(_GREEN if cut is None or i < cut else _ORANGE, step * i)


def _dissolve(first, last):
    """The green, mixed into the pale picture over frames first..last-1; `last` is the
    first frame of the pale one alone. The brightness steps 73 / (last - first + 1) a frame."""
    np = pytest.importorskip("numpy")

    old, new = _textured(_GREEN).astype(float), _textured(_PALE).astype(float)

    def frame(i):
        w = min(1.0, max(0.0, (i - first + 1) / (last - first + 1)))
        return (old * (1 - w) + new * w).round().astype(np.uint8)

    return frame


def _reader_cuts(colour, n=150, step=6, **options):
    """The cuts video/cuts.py's ShotCuts finds in a synthetic clip sampled as compute
    samples it (every 6th frame at 30 fps; every `step`th), with these options; and its
    capture."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from video.cuts import ShotCuts, colours
    from video.framing import small_gray

    cap = _Clip(colour, n)
    reader = ShotCuts(cap, fallback=25.0, **options)
    found, prev, a = [], None, 0
    for b in range(0, n, step):
        frame = np.full((_H, _W, 3), colour(b), np.uint8)
        small = small_gray(frame)
        cut = reader.check(prev, small, a, b, colours=colours(frame))
        if cut is not None:
            found.append(cut)
        prev, a = small, b
    return found, cap


def test_compute_finds_a_cut_out_of_a_fast_pan(monkeypatch):
    # The pan moves the thumbnail about 21 a frame; the cut at frame 62 changes it by 30,
    # and every colour. That is under 3x the pan and under 40: no cut at ShotCuts'
    # defaults, as before, and the crop slid across it. Soccer's colour-led rule finds it.
    clip = _pan(6, cut=62)
    assert _reader_cuts(clip)[0] == []
    assert _reader_cuts(clip, colour_led=True)[0] == [62]
    result, _ = _compute(monkeypatch, clip,
                         lambda f: ([(0.3, 0.5, 0.6)] if f < 62 else [(0.75, 0.5, 0.6)], []))
    xs = _per_frame(result["path"], 149)
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in xs[:62])
    assert all(x == pytest.approx(0.75, abs=1e-9) for x in xs[62:])


def test_compute_a_fast_pan_is_no_cut_and_is_not_even_decoded(monkeypatch):
    # 16 pixels a frame: the thumbnail changes by 28 a frame and 27 a sample pair, the
    # colours by 0.01-0.02. At the defaults every pair is decoded frame by frame and
    # nothing is a cut; with the colour-led rule nothing is either.
    clip = _pan(16)
    found, cap = _reader_cuts(clip)
    assert found == [] and cap.pos > 0
    assert _reader_cuts(clip, colour_led=True)[0] == []
    # Soccer's colour gate: the samples' colours are the same, so no pair is a candidate.
    found, cap = _reader_cuts(clip, colour_gate=0.2, colour_led=True, dissolves=True)
    assert found == [] and cap.pos == 0
    result, opened = _compute(monkeypatch, clip, lambda f: ([(0.4, 0.5, 0.6)], []))
    assert all(f % 6 == 0 for f in _key_frames(result["path"]))      # no freeze or snap keys
    assert opened[1].pos == 0


@pytest.mark.parametrize("first, last", [(61, 70), (61, 67), (61, 65)])
def test_compute_snaps_where_a_dissolve_completes(first, last, monkeypatch):
    # A fade into a replay: no frame changes much, so no frame is a hard cut, and the crop
    # used to slide (or hold the old ball for a second). 61-70 runs across the sample at
    # 66, and the pair after it places it; so does 61-67, though that pair changes too
    # little to be a candidate of its own; 61-65 completes inside one sample gap. The new
    # framing starts on the first frame of the new picture alone.
    clip = _dissolve(first, last)
    assert _reader_cuts(clip)[0] == []                               # the defaults: as before
    assert _reader_cuts(clip, dissolves=True)[0] == [last]
    result, _ = _compute(monkeypatch, clip,
                         lambda f: ([(0.3, 0.5, 0.6)] if f < last else [(0.75, 0.5, 0.6)], []))
    path = result["path"]
    assert last in _key_frames(path)
    xs = _per_frame(path, 149)
    assert all(x == pytest.approx(0.3, abs=1e-9) for x in xs[:last])
    assert all(x == pytest.approx(0.75, abs=1e-9) for x in xs[last:])


def test_a_long_dissolve_is_placed_once():
    # From a dark picture to a bright one over 20 frames (61-80): a pair later it is still
    # changing, so it is placed on that pair's sample (72), where the new picture has taken
    # over, and the change that runs on after it is not a second dissolve.
    np = pytest.importorskip("numpy")

    dark, bright = _textured((40, 60, 45)).astype(float), _textured((215, 215, 235)).astype(float)

    def clip(i):
        w = min(1.0, max(0.0, (i - 60) / 20))
        return (dark * (1 - w) + bright * w).round().astype(np.uint8)

    assert _reader_cuts(clip, dissolves=True)[0] == [72]
    assert _reader_cuts(clip)[0] == []


_SOCCER = {"colour_gate": 0.2, "colour_led": True, "dissolves": True}


def _panning_fade(first, last, before):
    """A pan of 2 pixels a frame over the green, mixed into the pale picture over
    first..last-1 as _dissolve mixes them (`last` is the first frame of the pale one
    alone); frame i is before(i) instead where that is not None."""
    np = pytest.importorskip("numpy")

    def frame(i):
        shown = before(i)
        if shown is not None:
            return shown
        w = min(1.0, max(0.0, (i - first + 1) / (last - first + 1)))
        old, new = _textured(_GREEN, 2 * i).astype(float), _textured(_PALE, 2 * i).astype(float)
        return (old * (1 - w) + new * w).round().astype(np.uint8)

    return frame


def _step(fps):
    """compute's sample step at this frame rate."""
    from sports.soccer.ball import SAMPLE_FPS

    return max(1, round(fps / SAMPLE_FPS))


# A fade over 62..last-1 that runs past the sample after 60 (65 at 25 fps, 66 at 30), so
# the pair after that places it. That pair's frames start LOOK_BACK (8) before its first
# sample, which at these steps reaches back past the last gap, to a change just before the
# fade that is steeper than any of the fade's frames. Its steepest frame was taken for the
# fade's: the fade was placed mid-change, on the frame after that pair's first sample (66
# or 67), and at 25 fps, ending on 71, again on 71, where the pan makes the pair after it
# a candidate of its own. That pair now places it from its own gap. At 50 fps (a step of
# 10) the frames never reached back that far. Placed on the sample at 70 is within a frame
# of 71: a fade still changing a pair later is placed on that pair's sample.


@pytest.mark.parametrize("last", [70, 71])
@pytest.mark.parametrize("fps", [25.0, 30.0, 50.0])
def test_a_fade_just_after_a_cut_is_placed_once_where_it_ends(fps, last):
    # The hard cut is on the sample at frame 60, from a dark picture.
    clip = _panning_fade(62, last, lambda i: _textured((40, 60, 45), 2 * i) if i < 60 else None)
    found = _reader_cuts(clip, step=_step(fps), **_SOCCER)[0]
    assert len(found) == 2 and found[0] == 60 and abs(found[1] - last) <= 1, found


@pytest.mark.parametrize("last", [70, 71])
@pytest.mark.parametrize("fps", [25.0, 30.0, 50.0])
def test_a_fade_just_after_a_flash_is_placed_once_where_it_ends(fps, last):
    # A one-frame camera flash on frame 59, the picture back on the sample at 60: no cut.
    clip = _panning_fade(62, last, lambda i: _WHITE if i == 59 else None)
    found = _reader_cuts(clip, step=_step(fps), **_SOCCER)[0]
    assert len(found) == 1 and abs(found[0] - last) <= 1, found


@pytest.mark.parametrize("cut", [61, 62, 64, 66])
def test_soccer_s_options_leave_a_plain_cut_and_a_flash_as_they_were(cut):
    # ShotCuts at its defaults finds the plain hard cut on its frame and no cut in a flash,
    # as before the options; with every option Soccer passes it finds the same.
    plain = lambda i: _A if i < cut else _B                  # noqa: E731
    flash = lambda i: _WHITE if i in (66, 67) else _A        # noqa: E731
    for options in ({}, {"colour_gate": 0.2, "colour_led": True, "dissolves": True}):
        assert _reader_cuts(plain, **options)[0] == [cut]
        assert _reader_cuts(flash, **options)[0] == []
