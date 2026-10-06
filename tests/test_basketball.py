"""Basketball, the second sport on the Sports framework (sports/basketball/,
docs/SPORTS.md): its moments, how much the game's situation makes them
matter, the crowd, bench and courtside reactions, the framing, the way in,
and that soccer is untouched. Synthetic signals only: no model runs and no
footage is needed."""

import json

import pytest

pytest.importorskip("yaml")

import sports
from core.models import ClipCandidate, Segment
from sports.basketball import reactions
from sports.basketball import scoreboard as bb
from sports.core import clips, detect

N = 900


def _profile(highlights="best", period="full", **extra):
    return sports.profile_for({"clips": {"sport": {"name": "basketball", "highlights": highlights,
                                                   "period": period, **extra}}})


def _segments(said: dict[int, str]):
    return [Segment(start=float(s), end=float(s + 4), text=said.get(s, "bringing it up the floor"))
            for s in range(0, N, 4)]


def _curves(roars=(), buzzer=(), whistle=()):
    np = pytest.importorskip("numpy")
    crowd = np.zeros(N, dtype=np.float32)
    for t, length in roars:
        crowd[t:t + length] = 0.9
    buzz = np.zeros(N, dtype=np.float32)
    for t in buzzer:
        buzz[t:t + 2] = 0.9
    whis = np.zeros(N, dtype=np.float32)
    for t in whistle:
        whis[t:t + 2] = 0.9
    return {"crowd": crowd, "crowd_heard": crowd, "buzzer": buzz, "whistle": whis}


def _board(baskets, period=1, start_clock=600.0, teams=("LAL", "BOS"), start=(0, 0), every=4):
    """A read score bug: the score every `every` seconds, the clock running
    down from start_clock. `baskets`: (video second, side, points)."""
    score = list(start)
    readings = []
    todo = sorted(baskets)
    for t in range(0, N, every):
        while todo and todo[0][0] <= t:
            _at, side, points = todo.pop(0)
            score[side] += points
        readings.append(bb.Reading(t=float(t), score=tuple(score), teams=teams, period=period,
                                   clock=max(0.0, start_clock - t), visible=True))
    return bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1))


def _moments(profile, said=None, curves=None, board=None, cutaways=()):
    profile.board = board
    profile.cutaways = list(cutaways)
    profile.curves = curves or _curves()
    return detect.moments(profile, _segments(said or {}), curves=profile.curves, board=board,
                          video_end=float(N), min_len=10, max_len=60)


def _named(moments):
    return [(e.type, round(e.t)) for e in moments if not e.is_replay and e.type != "big_moment"]


# ---- registration and the way in ------------------------------------------------------


def test_basketball_is_offered_beside_soccer_with_its_quarters():
    offered = {s["id"]: s for s in sports.available()}
    assert list(offered) == ["soccer", "basketball"]
    ball = offered["basketball"]
    assert ball["period_menu"] == "Quarter" and "period_menu" not in offered["soccer"]
    assert [p["id"] for p in ball["periods"]] == ["full", "q1", "q2", "q3", "q4", "ot"]
    for choice in ("best", "scoring", "dunks", "threes", "blocks", "steals", "assists", "clutch",
                   "fan_reactions", "celebrity_reactions", "crowd_reactions", "plays_reactions", "custom"):
        assert choice in {h["id"] for h in ball["highlights"]}


def test_a_long_games_scoreboard_is_waited_for():
    """A 79-minute NBA game's scoreboard took 25 minutes to read on a test
    PC. The job waited 900 s for it, went on without it, and its clips came
    out as plain clips that missed the game's ending. A basketball job now
    waits as long as the video runs; every other sport, Soccer too, still
    waits 900 s."""
    basketball = {"clips": {"sport": {"name": "basketball"}}}
    assert sports.prepass_wait(basketball, 4740.0) == 4740.0
    assert sports.prepass_wait(basketball, 600.0) == 900.0
    assert sports.prepass_wait({"clips": {"sport": {"name": "soccer"}}}, 4740.0) == 900.0
    assert sports.prepass_wait({"clips": {}}, 4740.0) == 900.0


def test_a_job_waits_for_its_sports_reading_as_long_as_the_sport_says(monkeypatch):
    pytest.importorskip("numpy")
    import core.pipeline as pipeline
    from core import cancel
    from core.models import DownloadedVideo

    basketball = {"clips": {"sport": {"name": "basketball"}}}
    waited = []
    monkeypatch.setattr(pipeline.MatchReading, "_listen", lambda self: None)
    monkeypatch.setattr(pipeline.MatchReading, "_prepass", lambda self: None)
    monkeypatch.setattr(cancel, "wait", lambda thread, timeout=None, video_id=None: waited.append(timeout))
    monkeypatch.setattr(pipeline, "_sport_inputs", lambda *_a: ("profile", None, None))
    video = DownloadedVideo(video_id="g3", title="Game", path=None, duration=4740.0)
    assert pipeline.MatchReading(basketball, video).finish() == ("profile", None, None)
    assert waited == [900, 4740.0]


def test_the_taxonomy_is_data_and_every_choice_names_real_events():
    spec = sports.spec("basketball")
    kinds = set(spec["events"])
    assert {"made_2", "made_3", "dunk", "alley_oop", "and_one", "buzzer_beater", "game_winner", "block",
            "steal", "assist", "putback", "technical_foul", "ejection", "fight"} <= kinds
    for word, kind in spec["listed_words"]:
        assert kind in kinds, word
    for kind in list(spec["callouts"]) + spec["scoring_events"] + spec["reaction_events"]:
        assert kind in kinds
    for choice in spec["highlights_choices"].values():
        assert choice["events"] == "all" or set(choice["events"]) <= kinds


def test_the_option_is_cleaned():
    assert sports.clean({"name": "Basketball", "highlights": "threes", "period": "q4", "teams": " Curry "}) == {
        "name": "basketball", "highlights": "threes", "period": "q4", "teams": "Curry"}
    # A reactions choice takes words ("fans reacting to the dunks"); soccer's goals don't.
    assert sports.clean({"name": "basketball", "highlights": "fan_reactions",
                         "request": "the biggest dunks"})["request"] == "the biggest dunks"
    assert "request" not in sports.clean({"name": "soccer", "highlights": "goals", "request": "x"})
    with pytest.raises(ValueError):
        sports.clean({"name": "basketball", "period": "second_half"})
    with pytest.raises(ValueError):
        sports.clean({"name": "basketball", "highlights": "goals"})


def test_it_uses_the_same_ai_and_scoring_as_soccer():
    """No AI of its own: the same profile interface, the same weights and the
    same scoring path (analysis/fusion.py) as soccer, with its own words."""
    ball, soccer = _profile(), sports.profile_for({"clips": {"sport": {"name": "soccer"}}})
    assert ball.weights == soccer.weights and ball.games == [] and ball.genre == "basketball"
    assert "BASKETBALL" in ball.guidance() and "pitch" not in ball.guidance()
    assert ball.sound_curves() == ("crowd", "whistle", "buzzer")
    assert "buzzer" in sports.sound_groups("basketball") and "buzzer" not in sports.sound_groups("soccer")


def test_the_assistant_is_told_about_basketball():
    pytest.importorskip("requests")
    from server.mcp import SPORT_PARAM

    text = str(SPORT_PARAM)
    assert "basketball" in text and "fan_reactions" in text and "q4" in text


# ---- the score bug ---------------------------------------------------------------------


@pytest.mark.parametrize("line, score, teams, period, clock", [
    ("LAL 98  BOS 101  4TH  0:32  14", (98, 101), ("LAL", "BOS"), 4, 32),
    ("LAL98 BOS101 4TH 2:05.4", (98, 101), ("LAL", "BOS"), 4, 125),
    ("98 LAL 101 BOS Q3 11:42", (98, 101), ("LAL", "BOS"), 3, 702),
    ("LAL 98 BOS 101 OT 24.3", (98, 101), ("LAL", "BOS"), 5, 24.3),
    ("LAL 7 BOS 9 2OT 1:00", (7, 9), ("LAL", "BOS"), 6, 60),
    ("BONUS LAL 98 BOS 101 4TH :32", (98, 101), ("LAL", "BOS"), 4, 32),
    ("DUKE 45 UNC 44 2ND HALF 0:03", (45, 44), ("DUKE", "UNC"), 2, 3),
    ("98 - 101", (98, 101), None, None, None),
    # A high-school bug, as the OCR read it off a 2022 broadcast: whole school
    # names, and every box run together into one word.
    ("TAUNTON49ATTLEBORO434TH", (49, 43), ("TAUNTON", "ATTLEBORO"), 4, None),
    ("TAUNTON 49 ATTLEBORO 43 4TH", (49, 43), ("TAUNTON", "ATTLEBORO"), 4, None),
    ("LAL98BOS101Q3", (98, 101), ("LAL", "BOS"), 3, None),
])
def test_the_bug_is_read(line, score, teams, period, clock):
    r = bb.parse([line])
    assert (r.score, r.teams, r.period, r.clock) == (score, teams, period, clock)


def test_a_basket_is_a_score_up_by_one_two_or_three():
    board = _board([(100, 0, 2), (200, 1, 3), (300, 0, 1), (400, 1, 2)])
    assert [(c.points, c.team) for c in board.changes] == [(2, "LAL"), (3, "BOS"), (1, "LAL"), (2, "BOS")]
    assert board.changes[1].label() == "score 2-3 (BOS), +3"


def test_a_misread_or_a_jump_of_two_baskets_is_not_a_basket():
    readings = [bb.Reading(t=float(t), score=s, teams=("LAL", "BOS"), visible=True)
                for t, s in [(0, (10, 10)), (4, (10, 10)), (8, (18, 10)), (12, (10, 10)), (16, (10, 10)),
                             (20, (15, 10)), (24, (15, 10))]]
    assert bb.from_readings(readings).changes == []


def test_the_clock_and_quarter_are_read_for_each_moment():
    board = _board([], period=4, start_clock=700.0)
    assert board.period_at(100) == "q4" and board.when(100) == "Q4 10:00"
    assert _board([], period=5).period_at(10) == "ot"
    assert board.minute_at(100) is None


# Real NBA bugs aren't one tight line (measured on three games): two rows
# with the teams' letters on their side, or logos and two bare numbers, with
# the shot clock and the fouls beside them. The full OCR returns them as
# pieces, in the box's fractions.

def _two_rows(first, second, clock, shot):
    return [((0.10, 0.05, 0.25, 0.45), "LAL"), ((0.30, 0.05, 0.42, 0.45), str(first)),
            ((0.10, 0.55, 0.25, 0.95), "BOS"), ((0.30, 0.55, 0.42, 0.95), str(second)),
            ((0.55, 0.10, 0.70, 0.40), "2ND"), ((0.55, 0.55, 0.75, 0.90), clock),
            ((0.85, 0.60, 0.92, 0.85), str(shot))]


def _bare(first, second, clock, shot, fouls):
    # [logo] 98 [logo] 101  4TH 0:32  14, the team fouls small under each score
    return [((0.10, 0.15, 0.18, 0.75), str(first)), ((0.35, 0.15, 0.43, 0.75), str(second)),
            ((0.11, 0.80, 0.14, 0.95), str(fouls[0])), ((0.36, 0.80, 0.39, 0.95), str(fouls[1])),
            ((0.55, 0.25, 0.62, 0.65), "4TH"), ((0.65, 0.25, 0.75, 0.65), clock),
            ((0.82, 0.30, 0.87, 0.60), str(shot))]


def _read(pieces_at, scores):
    readings = []
    for i, (first, second) in enumerate(scores):
        r = bb.parse_pieces(pieces_at(i, first, second))
        r.t = i * 4.0
        readings.append(r)
    return bb.from_readings(readings, box=(0.0, 0.85, 0.4, 0.97))


def test_a_two_row_bug_is_read_piece_by_piece():
    scores = [(10, 8), (10, 8), (12, 8), (12, 8), (12, 8), (12, 11), (12, 11), (13, 11), (13, 11),
              (15, 11), (15, 11), (15, 11)]
    board = _read(lambda i, a, b: _two_rows(a, b, f"7:{47 - i:02d}", 24 - i), scores)
    assert [(c.points, c.team) for c in board.changes] == [(2, "LAL"), (3, "BOS"), (1, "LAL"), (2, "LAL")]
    assert board.final() == (15, 11) and board.readings[0].period == 2 and board.readings[0].clock == 467


def test_bare_scores_beside_logos_are_told_from_the_shot_clock_and_the_fouls():
    scores = [(98, 99), (98, 99), (98, 101), (98, 101), (101, 101), (101, 101), (101, 101), (102, 101),
              (102, 101), (102, 101)]
    fouls = [(2, 3), (2, 3), (2, 3), (3, 3), (3, 3), (3, 4), (3, 4), (3, 4), (4, 4), (4, 4)]
    board = _read(lambda i, a, b: _bare(a, b, f"1:{50 - i * 3:02d}", (20 - 3 * i) % 25, fouls[i]), scores)
    assert [(c.points, c.side) for c in board.changes] == [(2, 1), (3, 0), (1, 0)]
    assert board.final() == (102, 101)


def test_a_bug_read_as_one_word_still_gives_its_baskets():
    # A 2022 high-school broadcast: the OCR ran the whole bug together.
    scores = [(37, 36), (37, 36), (39, 36), (39, 36), (39, 39), (39, 39), (40, 39), (40, 39)]
    board = _read(lambda i, a, b: [((0.05, 0.2, 0.95, 0.8), f"TAUNTON{a}ATTLEBORO{b}4TH")], scores)
    assert [(c.points, c.team) for c in board.changes] == [(2, "TAUNTON"), (3, "ATTLEBORO"), (1, "TAUNTON")]
    assert board.readings[0].period == 4


def test_too_few_readings_say_nothing_about_bare_numbers():
    board = _read(lambda i, a, b: _bare(a, b, "1:00", 14, (1, 1)), [(98, 99), (98, 99)])
    assert board.changes == [] and board.final() is None


def test_the_box_is_found_around_the_clock_with_logos_between_its_scores():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[860:] = 80                                         # the bottom band holds text; the top is dark

    def ocr(img):
        # The bottom band, read 480 px wide: an ad board far to the left,
        # then the bug: 98 [logo] 101  4TH 0:32  14.
        if not img.any():
            return []
        return [([[10, 10], [60, 10], [60, 22], [10, 22]], "SHOP NOW 50", 0.9),
                ([[200, 8], [214, 8], [214, 24], [200, 24]], "98", 0.9),
                ([[240, 8], [258, 8], [258, 24], [240, 24]], "101", 0.9),
                ([[270, 10], [285, 10], [285, 22], [270, 22]], "4TH", 0.9),
                ([[290, 10], [310, 10], [310, 22], [290, 22]], "0:32", 0.9),
                ([[322, 10], [332, 10], [332, 22], [322, 22]], "14", 0.9)]

    box = bb.find_box(lambda t: frame, 2880, ocr)
    assert box is not None and box[1] > 0.78                # in the bottom band
    assert 0.38 < box[0] < 0.42 and 0.68 < box[2] < 0.72     # the bug, not the ad board


def test_the_bugs_text_is_where_most_frames_show_it_not_a_caption_joined_to_it_once():
    """find_box grows to every frame's block of text, a caption over the bug
    on one frame included: on an NBA game its top sat 10 points of the
    height above the bug's. The framing takes the bug's text where most
    frames show it."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    plain = np.zeros((1080, 1920, 3), dtype=np.uint8)
    plain[860:] = 80                                         # the bottom band holds text; the top is dark
    captioned = plain.copy()
    captioned[860:] = 120

    def ocr(img):
        # The bottom band, read 480 px wide: the bug, two rows; on one frame
        # a scorer's caption right over it.
        if img.mean() < 1:
            return []
        bug = [([[200, 30], [214, 30], [214, 46], [200, 46]], "98", 0.9),
               ([[240, 30], [258, 30], [258, 46], [240, 46]], "101", 0.9),
               ([[270, 32], [285, 32], [285, 44], [270, 44]], "4TH", 0.9),
               ([[290, 32], [310, 32], [310, 44], [290, 44]], "0:32", 0.9)]
        caption = [([[200, 14], [300, 14], [300, 26], [200, 26]], "JONES 31 PTS", 0.9)]
        return bug + (caption if img.mean() > 100 else [])

    looks = [plain, captioned, plain, plain, plain, plain]

    def grab(t):
        return looks[round(t / (2880 / 15)) - 1]

    box, text = bb.find_text(grab, 2880, ocr)
    assert box == bb.find_box(grab, 2880, ocr)
    bug_top = 0.78 + 0.22 * 30 / 59
    assert box[1] < 0.78 + 0.22 * 14 / 59 < bug_top                    # grown to the caption, and padded
    assert abs(text[1] - bug_top) < 0.005 and abs(text[3] - (0.78 + 0.22 * 46 / 59)) < 0.005


def _bug_frames(game):
    """What the full OCR read in the score bug of three NBA broadcasts, 20
    keyframes each, with what a person read there (tests/fixtures)."""
    import copy
    from pathlib import Path

    games = json.loads((Path(__file__).parent / "fixtures" / "basketball_bugs.json").read_text("utf-8"))
    return copy.deepcopy(games[game]["frames"]), tuple(games[game]["box"])


def _board_of(frames, box=None):
    readings, truths = [], []
    for f in frames:
        w, h = f["w"], f["h"]
        pieces = [((x0 / w, y0 / h, x1 / w, y1 / h), text) for x0, y0, x1, y1, text, conf in f["pieces"]
                  if conf >= 0.5]                             # as scorebug.pieces keeps them
        r = bb.parse_pieces(pieces)
        r.t = f["t"]
        readings.append(r)
        truths.append(f["truth"])
    return bb.from_readings(readings, box), truths


def _real_bugs():
    return {name: _board_of(*_bug_frames(name)) for name in ("game1", "game2", "game3")}


def _dense(frames, times: dict | None = None, copies: int = 3):
    """A game's keyframes as a whole game reads them: each seen on `copies`
    keyframes running (1-3 s apart, between baskets), or on as many as
    `times` says for its time."""
    import copy

    out = []
    for f in frames:
        for k in range((times or {}).get(f["t"], copies)):
            g = copy.deepcopy(f)
            g["t"] = round(f["t"] + 0.3 * k, 2)
            out.append(g)
    return sorted(out, key=lambda f: f["t"])


def _clock(text):
    minutes, _, seconds = text.rpartition(":")
    return int(minutes or 0) * 60 + float(seconds)


@pytest.mark.parametrize("game, teams", [("game1", None), ("game2", ("GSW", "DAL")), ("game3", ("LAL", "GS"))])
def test_real_nba_bugs_are_read(game, teams):
    # Game 1: two rows of big scores, sideways team letters and seed numbers
    # beside them. Game 2: logos and bare scores, the shot clock ":24", the
    # teams' records under them. Game 3: one line, "4 TH" read with a space.
    board, truths = _real_bugs()[game]
    for r, truth in zip(board.readings, truths):
        if None not in truth["score"]:                       # not mid-roll
            assert r.score == tuple(truth["score"]), (r.t, r.text)
        if r.period is not None:
            assert r.period == truth["period"], (r.t, r.text)
        assert r.clock == pytest.approx(_clock(truth["clock"]), abs=0.11), (r.t, r.text)
    assert sum(r.period is not None for r in board.readings) >= 19
    assert board.teams() == teams                            # sideways letters are no code: none is guessed


def test_a_score_rolling_over_is_not_a_basket():
    # Game 1 as a whole game reads it: each keyframe seen three times
    # running; the score mid-roll at 307.54 ("4U" over "12") once, and one
    # more mid-roll early on (8 rolling to 10, the same pieces). One read of
    # two numbers at a score's place mustn't split that team's score in two.
    frames, box = _bug_frames("game1")
    roll = next(f for f in frames if f["t"] == 307.54)
    early = json.loads(json.dumps(roll))
    early["t"] = 67.0
    for p in early["pieces"]:
        p[4] = {"4U": "1U", "12": "8", "33": "4", "7:26": "9:15", "2ND": "1ST"}.get(p[4], p[4])
    board, truths = _board_of(_dense([*frames, early], {307.54: 1, 67.0: 1}), box)
    wrong = [r.t for r, truth in zip(board.readings, truths) if None not in truth["score"]
             and r.score != tuple(truth["score"])]
    assert wrong == []
    made = [(c.before, c.after, c.points) for c in board.changes]
    assert ((70, 65), (73, 65), 3) in made and ((92, 86), (95, 86), 3) in made
    assert all(after not in ((12, 33), (3, 65)) and points in (1, 2, 3) for _before, after, points in made)
    assert board.final() == (111, 103)


def test_a_teams_fouls_beside_its_score_keep_a_place_of_their_own():
    # "LAL 4 98   BOS 2 101   4TH 5:00": each team's fouls just left of its
    # score, closer than SLOT_X. They aren't a score mid-roll: the scores
    # keep their own places.
    def reading(t, a, b, fa, fb):
        r = bb.parse_pieces([((0.02, 0.3, 0.10, 0.7), "LAL"), ((0.22, 0.4, 0.25, 0.6), str(fa)),
                             ((0.26, 0.1, 0.31, 0.9), str(a)), ((0.45, 0.3, 0.52, 0.7), "BOS"),
                             ((0.64, 0.4, 0.67, 0.6), str(fb)), ((0.68, 0.1, 0.74, 0.9), str(b)),
                             ((0.80, 0.3, 0.86, 0.7), "4TH"), ((0.88, 0.3, 0.97, 0.7), f"5:{59 - t:02d}")])
        r.t = t
        return r

    seq = [(0, 90, 95, 1, 2), (2, 90, 95, 1, 2), (4, 92, 95, 2, 2), (6, 92, 95, 2, 2), (8, 92, 98, 2, 3),
           (10, 92, 98, 2, 3), (12, 95, 98, 3, 3), (14, 95, 98, 3, 4), (16, 95, 100, 4, 4), (18, 95, 100, 4, 4)]
    board = bb.from_readings([reading(*x) for x in seq])
    assert [c.label() for c in board.changes] == ["score 92-95 (LAL), +2", "score 92-98 (BOS), +3",
                                                  "score 95-98 (LAL), +3", "score 95-100 (BOS), +2"]
    assert board.final() == (95, 100)


def test_a_plus_three_over_the_score_is_no_score():
    # After a three the bug shows "+3" over the scorer's score for a few
    # seconds: read on two keyframes running, it mustn't take the three away.
    frames, box = _bug_frames("game1")
    board, _ = _board_of(_dense(frames, copies=2), box)
    assert ((70, 65), (73, 65), 3) in [(c.before, c.after, c.points) for c in board.changes]


def test_a_score_read_once_says_nothing_about_the_game():
    # The score just before a moment is one seen twice running: not "12-33"
    # read mid-roll in a 40-33 game, nor "3-65" read off the "+3".
    frames, box = _bug_frames("game1")
    board, _ = _board_of(_dense(frames, {307.54: 1, 555.12: 1}), box)
    assert board.score_before(307.6) == (40, 33)
    assert board.score_before(555.2) == (70, 65)


def test_a_box_that_hasnt_changed_is_not_read_again(monkeypatch):
    # A full OCR is most of a second a keyframe. While the clock is stopped
    # the box doesn't change: those keyframes read as the last full read
    # did, until FULL_EVERY of them have passed. (The first full read is
    # trusted once a second finds its pieces in the same places.)
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import contextlib

    from sports.core import scorebug

    still = np.full((64, 200, 3), 30, dtype=np.uint8)
    noisy = still.copy()
    noisy[::3, ::3] += 20                                  # compression noise: no change
    boxes = [still.copy(), still.copy(), noisy] + [still.copy() for _ in range(bb.FULL_EVERY + 1)]
    read = []

    def crops(path, box, size, on_frame, cancel=None, scale_width=None):
        for i, img in enumerate(boxes):
            on_frame(i, img)
        return [2.0 * i for i in range(len(boxes))]

    def ocr_pieces(img, ocr):
        read.append(next(i for i, b in enumerate(boxes) if b is img))
        return [((0.1, 0.2, 0.2, 0.8), "98"), ((0.7, 0.2, 0.8, 0.8), "101")]

    @contextlib.contextmanager
    def capture(path, required=True):
        yield object()

    monkeypatch.setattr("video.capture.video_capture", capture)
    monkeypatch.setattr("core.modes.probe_size", lambda path: (1920, 1080))
    monkeypatch.setattr(bb, "find_box", lambda grab, duration, ocr: (0.3, 0.8, 0.7, 0.9))
    monkeypatch.setattr(bb, "recognise", lambda img: pytest.fail("nothing changed"))
    monkeypatch.setattr(scorebug, "keyframe_crops", crops)
    monkeypatch.setattr(scorebug, "pieces", ocr_pieces)
    board = bb.read_video("game.mp4", 22.0)
    assert read == [0, 1, bb.FULL_EVERY + 2]
    assert len(board.readings) == len(boxes)


def test_only_the_pieces_that_changed_are_read_again(monkeypatch):
    # Between baskets only the clocks change. A piece whose pixels changed is
    # read again by the recogniser alone, where it sat (milliseconds, against
    # most of a second). The box is read whole again when a piece read again
    # is unsure (a score mid-roll, the bug hidden), when a piece whose digits
    # changed shows more of them read wider (a score grown past its place),
    # and after a full read that found no bug or isn't yet trusted.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    still[16:48, 20:60] = 250                              # "98"
    still[16:48, 100:140] = 250                            # "95"
    still[16:48, 300:360] = 250                            # "7:46"
    ticked = still.copy()
    ticked[20:44, 340:356] = 90                            # the clock's last digit
    blurred = still.copy()
    blurred[20:44, 330:356] = 120                          # ...and again, read unsurely
    grew = still.copy()
    grew[16:48, 60:80] = 250                               # "101": the score past its piece
    gone = np.full((64, 400, 3), 30, dtype=np.uint8)       # the bug hidden
    again = still.copy()
    score, other, clock = (0.05, 0.25, 0.15, 0.75), (0.25, 0.25, 0.35, 0.75), (0.75, 0.25, 0.9, 0.75)
    full = {id(still): [(score, "98"), (other, "95"), (clock, "7:46")],
            id(blurred): [(score, "98"), (other, "95"), (clock, "7:44")],
            id(grew): [((0.05, 0.25, 0.2, 0.75), "101"), (other, "95"), (clock, "7:46")],
            id(gone): [],
            id(again): [(score, "98"), (other, "95"), (clock, "7:46")]}
    read, recognised = [], []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or full[id(img)])
    answers = iter([("7:45", 0.95), ("7:45", 0.93),        # the clock ticked; no more digits read wider
                    ("7:4", 0.6),                          # unsure
                    ("10", 0.95), ("101", 0.95),           # the score's place reads two digits, wider three
                    ("", 0.0)])                            # the bug hidden

    def rec(img):
        recognised.append(img.shape[:2])
        return next(answers)

    reader = bb.BoxReader(ocr=None, rec=rec)
    texts = [[text for _box, text in reader.read(img)] for img in (still, still, ticked, blurred, grew, grew, gone,
                                                                    again)]
    assert texts == [["98", "95", "7:46"], ["98", "95", "7:46"], ["98", "95", "7:45"], ["98", "95", "7:44"],
                     ["101", "95", "7:46"], ["101", "95", "7:46"], [], ["98", "95", "7:46"]]
    assert [id(img) for img in read] == [id(still), id(still), id(blurred), id(grew), id(gone), id(again)]
    assert recognised == [(32, 60), (32, 98), (32, 60), (32, 40), (32, 78), (32, 60)]


def test_a_plus_three_over_a_score_reads_the_box_whole(monkeypatch):
    # A "+3" drawn over a score is as many characters as the score. Read
    # again alone, the "+3" would stand where the score was, and then the
    # score where the "+3" was, at the graphic's place and size, where it is
    # no score. A piece read again as characters of another kind reads the
    # box whole, as the full OCR reads it.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    still[19:45, 0:16] = 250                               # a team's fouls, "2"
    still[16:48, 20:60] = 250                              # "80"
    still[16:48, 100:140] = 250                            # "66"
    plus = still.copy()
    plus[16:48, 100:140] = 120                             # "+3" over it
    after = still.copy()
    after[16:48, 120:140] = 200                            # "69"
    fouls, first, second = (0.0, 0.3, 0.04, 0.7), (0.05, 0.25, 0.15, 0.75), (0.25, 0.25, 0.35, 0.75)
    full = {id(still): [(fouls, "2"), (first, "80"), (second, "66")],
            id(plus): [(fouls, "2"), (first, "80"), ((0.27, 0.3, 0.33, 0.7), "+3")],
            id(after): [(fouls, "2"), (first, "80"), (second, "69")]}
    read = []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or full[id(img)])
    answers = iter([("+3", 0.97), ("69", 0.98)])
    reader = bb.BoxReader(ocr=None, rec=lambda img: next(answers))
    texts = [[text for _box, text in reader.read(img)] for img in (still, still, plus, after)]
    assert texts == [["2", "80", "66"], ["2", "80", "66"], ["2", "80", "+3"], ["2", "80", "69"]]
    assert [id(img) for img in read] == [id(still), id(still), id(plus), id(after)]


def test_a_full_read_of_a_score_mid_roll_is_read_again(monkeypatch):
    # A full read can catch a score rolling in, half out of its place. Read
    # again alone there, the keyframes after it would have the new score at
    # that place and size, where it is no score; so a full read whose pieces
    # aren't where the one before found them is followed by another.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    still[16:48, 20:60] = 250                              # "98"
    still[16:48, 100:140] = 250                            # "95"
    still[16:48, 300:360] = 250                            # "7:46"
    rolling = still.copy()
    rolling[16:48, 100:140] = 30
    rolling[3:29, 100:140] = 250                           # "95" on its way out, upwards
    after = still.copy()
    after[16:48, 120:140] = 200                            # "97" in its place
    score, other, clock = (0.05, 0.25, 0.15, 0.75), (0.25, 0.25, 0.35, 0.75), (0.75, 0.25, 0.9, 0.75)
    full = {id(still): [(score, "98"), (other, "95"), (clock, "7:46")],
            id(rolling): [(score, "98"), ((0.25, 0.05, 0.35, 0.45), "95"), (clock, "7:46")],
            id(after): [(score, "98"), (other, "97"), (clock, "7:46")]}
    read = []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or full[id(img)])
    answers = iter([("97", 0.5)])                          # mid-roll: unsure
    reader = bb.BoxReader(ocr=None, rec=lambda img: next(answers, ("97", 0.95)))
    got = [reader.read(img) for img in (still, still, rolling, after, after)]
    assert [[text for _box, text in pieces] for pieces in got] == [["98", "95", "7:46"]] * 3 + [["98", "97", "7:46"]] * 2
    assert [id(img) for img in read] == [id(still), id(still), id(rolling), id(after)]
    assert got[-1][1] == (other, "97")


def test_a_piece_read_again_as_it_was_stands_however_unsure(monkeypatch):
    # Over a moving picture small pieces never read surely, and letters read
    # a little differently each time ("OKC", "OKO"). A piece read again as it
    # was stands however unsure, and letters stand as the full read read
    # them; but a number or a "+" over letters reads the box whole.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    moved = still + 60                                     # the picture behind the bug moved: every piece changed
    covered = still + 120
    pieces = [((0.0, 0.25, 0.04, 0.75), "8"), ((0.05, 0.25, 0.15, 0.75), "98"), ((0.2, 0.25, 0.31, 0.75), "95"),
              ((0.4, 0.25, 0.53, 0.75), "OKC"), ((0.75, 0.25, 0.9, 0.75), "7:46")]
    read = []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or list(pieces))
    same = {16: ("8", 0.45), 40: ("98", 0.5), 44: ("95", 0.97)}          # each piece by its width
    answers = {id(moved): {**same, 52: ("OKO", 0.8), 60: ("7:45", 0.95), 98: ("7:45", 0.95)},
               id(covered): {**same, 52: ("+3", 0.95)}}
    frame = {}
    reader = bb.BoxReader(ocr=None, rec=lambda img: answers[frame["id"]][img.shape[1]])
    texts = []
    for img in (still, still, moved, covered):
        frame["id"] = id(img)
        texts.append([text for _box, text in reader.read(img)])
    assert texts == [["8", "98", "95", "OKC", "7:46"]] * 2 + [["8", "98", "95", "OKC", "7:45"],
                                                              ["8", "98", "95", "OKC", "7:46"]]
    assert [id(img) for img in read] == [id(still), id(still), id(covered)]


def test_a_piece_read_again_spaced_otherwise_reads_the_box_whole(monkeypatch):
    # A space parts two numbers: the full OCR can read "112" as "1 12", and
    # the recogniser alone "8:28 1.6" (a clock and a shot clock) as
    # "8:281.6". Whichever is right, a piece read again spaced otherwise
    # than the full read read it goes to a full read.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    still[16:48, 20:60] = 250                              # "111"
    still[16:48, 100:140] = 250                            # "112"
    still[16:48, 300:360] = 250                            # "8:28 1.6"
    scored = still.copy()
    scored[20:44, 110:130] = 120
    ticked = scored.copy()
    ticked[20:44, 340:356] = 120
    first, second, clock = (0.05, 0.25, 0.15, 0.75), (0.25, 0.25, 0.35, 0.75), (0.75, 0.25, 0.9, 0.75)
    full = {id(still): [(first, "111"), (second, "1 12"), (clock, "8:28 1.6")],
            id(scored): [(first, "111"), (second, "112"), (clock, "8:28 1.6")],
            id(ticked): [(first, "111"), (second, "112"), (clock, "8:27 1.5")]}
    read = []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or full[id(img)])
    answers = iter([("112", 1.0), ("8:271.5", 1.0)])
    reader = bb.BoxReader(ocr=None, rec=lambda img: next(answers))
    texts = [[text for _box, text in reader.read(img)] for img in (still, still, scored, ticked)]
    assert texts == [["111", "1 12", "8:28 1.6"]] * 2 + [["111", "112", "8:28 1.6"], ["111", "112", "8:27 1.5"]]
    assert [id(img) for img in read] == [id(still), id(still), id(scored), id(ticked)]


def test_a_full_read_that_dropped_a_score_is_read_again(monkeypatch):
    # A full read mid-roll can miss a score while another piece splits in
    # two ("7:46 24" read as "7:46" and "24"), as many pieces as before,
    # each where one was. With no piece where the score was, the keyframes
    # after it would have no score: it is followed by another full read.
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.core import scorebug

    still = np.full((64, 400, 3), 30, dtype=np.uint8)
    still[16:48, 20:60] = 250                              # "98"
    still[16:48, 100:140] = 250                            # "95"
    still[16:48, 300:380] = 250                            # "7:46 24"
    rolling = still.copy()
    rolling[16:48, 100:140] = 30                           # the score between two numbers
    after = rolling.copy()
    after[16:48, 100:140] = 200                            # "97"
    first, second, strip = (0.05, 0.25, 0.15, 0.75), (0.25, 0.25, 0.35, 0.75), (0.75, 0.25, 0.95, 0.75)
    full = {id(still): [(first, "98"), (second, "95"), (strip, "7:46 24")],
            id(rolling): [(first, "98"), ((0.75, 0.25, 0.85, 0.75), "7:46"), ((0.88, 0.25, 0.95, 0.75), "24")],
            id(after): [(first, "98"), (second, "97"), (strip, "7:46 24")]}
    read = []
    monkeypatch.setattr(scorebug, "pieces", lambda img, ocr: read.append(img) or full[id(img)])
    answers = iter([("9", 0.4)])                           # the score mid-roll: unsure
    reader = bb.BoxReader(ocr=None, rec=lambda img: next(answers))
    texts = [[text for _box, text in reader.read(img)] for img in (still, still, rolling, after)]
    assert texts == [["98", "95", "7:46 24"]] * 2 + [["98", "7:46", "24"], ["98", "97", "7:46 24"]]
    assert [id(img) for img in read] == [id(still), id(still), id(rolling), id(after)]


def test_a_piece_is_read_again_as_the_full_ocr_reads_it(monkeypatch):
    # The recogniser alone, with the engine the full OCR uses; it answers
    # ([[text, confidence]], times), or nothing. A piece half again as tall
    # as it is wide is turned on its side first, as the full OCR turns
    # sideways team letters.
    np = pytest.importorskip("numpy")
    from analysis import game_text

    seen = []

    def engine(img, use_det=True, use_cls=True, use_rec=True):
        seen.append((img.shape[:2], use_det, use_cls, use_rec))
        return ([["OKC", 0.97]], [0.01]) if img.shape[1] > 30 else (None, None)

    monkeypatch.setattr(game_text, "_engine", engine)
    assert bb.recognise(np.zeros((90, 30, 3), dtype=np.uint8)) == ("OKC", 0.97)
    assert bb.recognise(np.zeros((20, 20, 3), dtype=np.uint8)) == ("", 0.0)
    assert seen == [((30, 90), False, False, True), ((20, 20), False, False, True)]


def test_a_header_over_the_shot_clock_is_no_team():
    # A short clip of game 2's bug: logos and bare scores, "RIVALS WEEK" over
    # the shot clock, the records under the scores. Too few readings to tell
    # the scores by their places: the header and the clock are no team and
    # score, the records no score.
    frames, box = _bug_frames("game2")
    base = frames[0]                                     # 84.13: "1sT 8:32 :24  3  6  GSW(25-20) DAL(18-26)"
    clip = []
    for t, shot, left, right in ((0, ":21", "3", "6"), (4, ":21", "3", "6"), (8, ":24", "3", "6"),
                                 (12, ":24", "3", "9"), (16, ":24", "3", "9")):
        f = json.loads(json.dumps(base))
        f["t"] = t
        for p in f["pieces"]:
            p[4] = {":24": shot, "3": left, "6": right}.get(p[4], p[4])
        clip.append(f)
    board, _ = _board_of(clip, box)
    assert board.teams() is None and board.final() is None and board.changes == []


def test_a_network_logo_beside_the_teams_is_no_team():
    # Game 3's bug: "ESPN LAL 61 GS 54 3RD 9:47". The logo read as "ESPN"
    # every time sits at a place of its own on the scores' row, but beside no
    # score: the teams are still the codes beside the scores.
    frames, box = _bug_frames("game3")
    for f in frames:
        for p in f["pieces"]:
            if p[0] < 100:
                p[4] = "ESPN"
    board, _ = _board_of(frames, box)
    assert board.teams() == ("LAL", "GS")


def test_scores_side_by_side_are_each_read():
    # Game 3's bug in the other common order, "ESPN LAL 61 - 54 GS 3RD
    # 9:47": its real pieces, the second score moved beside the first, a
    # tenth of the box from it. Both are the box's biggest numbers: each
    # keeps a place of its own.
    frames, box = _bug_frames("game3")
    for f in frames:
        first = next(p for p in f["pieces"] if 330 < p[0] < 400 and p[4].strip().isdigit())
        second = next(p for p in f["pieces"] if 590 < p[0] < 720 and p[4].strip().isdigit())
        code = next(p for p in f["pieces"] if p[4] == "GS")
        x, width, code_width = second[0], second[2] - second[0], code[2] - code[0]
        second[0], second[2] = first[2] + 35, first[2] + 35 + width
        code[0], code[2] = x + 20, x + 20 + code_width
    board, truths = _board_of(_dense(frames), box)
    assert [r.score for r in board.readings] == [tuple(t["score"]) for t in truths]
    assert board.teams() == ("LAL", "GS") and board.final() == (97, 90)


def test_a_one_digit_score_is_read_not_the_fouls_beside_it():
    # "MIA 8 ²" over "NYK 11 ¹": each team's fouls small, right after its
    # score. A score's place is where it sits over the whole game, three
    # digits by the end, so a one-digit score early on is as far from it as
    # the fouls beside it are. The fouls are smaller: the score is read.
    def reading(t, a, b):
        pieces = [((0.45, 0.4, 0.55, 0.6), "4TH"), ((0.62, 0.4, 0.78, 0.6), f"{t // 60 % 12}:{t % 60:02d}")]
        for top, code, score, fouls in ((0.1, "MIA", a, 2), (0.55, "NYK", b, 1)):
            right = 0.25 + 0.05 * len(str(score))
            pieces += [((0.05, top, 0.17, top + 0.35), code), ((0.25, top, right, top + 0.35), str(score)),
                       ((right + 0.005, top + 0.1, right + 0.025, top + 0.25), str(fouls))]
        r = bb.parse_pieces(pieces)
        r.t = float(t)
        return r

    scores = [(0, 0)]
    while scores[-1] != (104, 104):
        a, b = scores[-1]
        scores.append((a + 2, b) if a == b else (a, b + 2))
    board = bb.from_readings([reading(4 * i + k, a, b) for i, (a, b) in enumerate(scores) for k in (0, 2)])
    assert [r.score for r in board.readings] == [s for s in scores for _ in (0, 2)]
    assert len(board.changes) == len(scores) - 1


def test_the_last_basket_read_once_still_makes_the_final_score():
    # Highlights that stop at the buzzer: 111-103 is on the last keyframe
    # only. A score read once is no misread when neither side of it is below
    # the score before it; one with a digit missed is.
    frames, box = _bug_frames("game1")
    board, _ = _board_of(_dense(frames, {307.54: 1, 555.12: 1, 852.03: 1}), box)
    assert board.final() == (111, 103)
    misread = json.loads(json.dumps(next(f for f in frames if f["t"] == 852.03)))
    misread["t"] = 860.0
    for p in misread["pieces"]:
        p[4] = {"?111": "?11"}.get(p[4], p[4])
    board, _ = _board_of([*_dense(frames), misread], box)
    assert board.readings[-1].score == (11, 103) and board.final() == (111, 103)


@pytest.mark.parametrize("row, teams", [
    ("HOME|{a}|-|{b}|AWAY", ("HOME", "AWAY")),       # "LAL 98 - 101 BOS"
    ("HOME|{a} - {b}|AWAY", ("HOME", "AWAY")),       # the dash read with the scores
    ("ESPN|{a}|-|{b}", None),                        # a network's logo, then bare scores
    ("{a}|-|{b}", None),                             # the teams' logos, no text
])
def test_a_short_clip_reads_its_score_off_the_row(row, teams):
    # 40 s in which one team scores once: too little to tell the scores'
    # places by, so each reading keeps the score its row reads.
    readings = []
    for i in range(10):
        pieces, x = [], 0.02
        for text in row.format(a=45 if i < 5 else 47, b=44).split("|"):
            width, tall = 0.022 * len(text), text[0].isdigit()
            pieces.append(((x, 0.1 if tall else 0.25, x + width, 0.9 if tall else 0.75), text))
            x += width + 0.03
        r = bb.parse_pieces([*pieces, ((0.70, 0.25, 0.76, 0.75), "2ND"),
                             ((0.78, 0.25, 0.87, 0.75), f"5:{40 - 4 * i:02d}")])
        r.t = 4.0 * i
        readings.append(r)
    board = bb.from_readings(readings)
    assert [(c.after, c.points) for c in board.changes] == [((47, 44), 2)]
    assert board.final() == (47, 44) and board.teams() == teams


def test_a_seed_beside_a_teams_letters_is_no_score():
    # Game 1 at 192.71 (27-20): the left team's sideways letters read "SSS"
    # beside its seed "2", on a row with "20 OKC". A seed is far smaller
    # than a score: no teams and score are read from them.
    frames, _ = _bug_frames("game1")
    f = next(f for f in frames if f["t"] == 192.71)
    r = bb.parse_pieces([((x0 / f["w"], y0 / f["h"], x1 / f["w"], y1 / f["h"]), text)
                         for x0, y0, x1, y1, text, conf in f["pieces"] if conf >= 0.5])
    assert (r.teams, r.score) == (None, None)


# ---- events -----------------------------------------------------------------------------


def test_made_two_and_three_come_from_the_score_bug():
    m = _moments(_profile(), board=_board([(200, 0, 2), (500, 1, 3)]),
                 curves=_curves(roars=[(199, 3), (499, 3)]))
    named = _named(m)
    assert ("made_2", 197) in named and ("made_3", 497) in named        # a second after the old score
    assert all(e.confirmed and e.team for e in m if e.type in ("made_2", "made_3"))


def test_a_free_throw_is_one_point():
    m = _moments(_profile("custom"), board=_board([(300, 0, 1)]))
    assert [e.type for e in m if e.confirmed] == ["free_throw"]


def test_a_plain_free_throw_is_none_of_a_games_best_moments():
    """On a 79-minute NBA game two of the ten clips were single free throws
    ("Lakers Still Trail by Three"). A game's best moments leave a plain one
    out; one that ties the game late is a game-tying moment, and stays."""
    for choice in ("best", "plays_reactions"):
        assert not [e for e in _moments(_profile(choice), board=_board([(300, 0, 1)])) if e.confirmed]
    late = _moments(_profile(), board=_board([(300, 0, 1)], period=4, start_clock=330.0, start=(99, 100)))
    assert [e.type for e in late if e.confirmed] == ["game_tying"]


def test_the_commentary_names_a_dunk_and_the_bug_confirms_it():
    m = _moments(_profile(), said={300: "he throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 4)]))
    assert [e.type for e in m if e.confirmed] == ["dunk"]


def test_a_three_called_on_a_two_point_basket_is_a_basket():
    m = _moments(_profile(), said={300: "from downtown"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 4)]))
    assert [e.type for e in m if e.confirmed] == ["made_2"]


@pytest.mark.parametrize("said, kind", [
    ("blocked! get that out of here", "block"),
    ("steal! picks his pocket", "steal"),
    ("what a pass, the dime", "dime"),
    ("great assist", "assist"),
    ("offensive rebound, keeps it alive", "offensive_rebound"),
    ("and he misses, off the rim", "miss"),
    ("he gets the foul call", "foul"),
    ("technical foul on the coach", "technical_foul"),
    ("and he's been ejected", "ejection"),
    ("alley-oop! he slams it", "alley_oop"),
    ("dunk and one! plus the foul", "and_one"),
])
def test_the_commentary_names_the_play_when_the_crowd_agrees(said, kind):
    m = _moments(_profile(), said={400: said}, curves=_curves(roars=[(401, 4)]))
    assert kind in [e.type for e in m]


def test_the_commentary_alone_is_never_a_moment():
    m = _moments(_profile(), said={400: "blocked! get that out of here"})
    assert m == []


def test_everyday_words_are_not_plays():
    profile = _profile()
    for said in ("three seconds in the lane", "he takes a shot at the referee", "the arena is full tonight"):
        assert profile.callouts_in(said) == [], said


# ---- the game's situation ------------------------------------------------------------


def _weighted(baskets, *, period, start_clock, start=(0, 0), said=None, roars=(), buzzer=()):
    profile = _profile()
    board = _board(baskets, period=period, start_clock=start_clock, start=start)
    m = _moments(profile, said=said, board=board, curves=_curves(roars=roars, buzzer=buzzer))
    e = next(e for e in m if e.confirmed)
    return e, clips.bonus(e, profile), profile.context_weight(e)


def test_a_game_winner_is_worth_far_more_than_a_first_quarter_three():
    winner, winner_bonus, _w = _weighted([(500, 0, 3)], period=4, start_clock=505.0, start=(99, 100),
                                         roars=[(499, 4)])
    routine, routine_bonus, _r = _weighted([(500, 0, 3)], period=1, start_clock=900.0, start=(10, 20),
                                           roars=[(499, 4)])
    assert winner.type == "game_winner" and routine.type == "made_3"
    assert winner_bonus > routine_bonus + 5
    assert winner.context == "LAL 102, BOS 100: LAL take the lead" and winner.when.startswith("Q4 0:0")


def test_a_late_block_in_a_close_game_beats_one_in_a_blowout():
    def block(start, period, clock):
        profile = _profile()
        board = _board([], period=period, start_clock=clock, start=start)
        m = _moments(profile, said={400: "blocked! rejected"}, board=board, curves=_curves(roars=[(401, 4)]))
        e = next(e for e in m if e.type == "block")
        return clips.bonus(e, profile)

    assert block((100, 101), 4, 450.0) > block((70, 101), 4, 450.0) + 5


def test_overtime_lifts_a_moment():
    _e, _b, ot = _weighted([(300, 0, 2)], period=5, start_clock=700.0, start=(100, 90))
    _e, _b, q2 = _weighted([(300, 0, 2)], period=2, start_clock=700.0, start=(50, 40))
    assert ot > q2


def test_end_of_quarter_basket_with_the_buzzer_is_a_buzzer_beater():
    e, _b, _w = _weighted([(500, 0, 3)], period=2, start_clock=500.5, start=(40, 50), roars=[(499, 4)],
                          buzzer=[500])
    assert e.type == "buzzer_beater"


def test_a_tying_basket_late_and_a_go_ahead_one():
    tie, _b, _w = _weighted([(500, 0, 2)], period=4, start_clock=540.0, start=(98, 100), roars=[(499, 4)])
    assert tie.type == "game_tying" and tie.context == "LAL 100, BOS 100: LAL tie it"
    ahead, _b, _w = _weighted([(500, 0, 3), (520, 1, 2)], period=4, start_clock=590.0, start=(98, 100),
                              roars=[(499, 4)])
    assert ahead.type == "go_ahead"


def test_a_late_possession_in_a_close_game_is_clutch():
    e, _b, w = _weighted([(500, 0, 2)], period=4, start_clock=560.0, start=(90, 93), roars=[(499, 4)])
    assert e.type == "clutch_shot" and w > 1.3


def test_without_a_score_bug_every_moment_counts_as_it_is():
    profile = _profile()
    m = _moments(profile, said={400: "throws it down, what a dunk"}, curves=_curves(roars=[(401, 4)]))
    assert profile.context_weight(m[0]) == 1.0


# ---- reactions ------------------------------------------------------------------------


def test_court_and_people_are_told_apart():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    court = np.zeros((108, 192, 3), dtype=np.uint8)
    court[:, :] = (60, 120, 190)                       # one floor colour
    rng = np.random.default_rng(0)
    crowd = rng.integers(0, 255, (108, 192, 3), dtype=np.uint8)
    assert reactions.looks(court, 0.3, 0.12) == "court"
    assert reactions.looks(crowd, 0.3, 0.12) == "people"
    # A fade to black between shots is no one's reaction, as with the detector.
    assert reactions.looks(np.zeros((108, 192, 3), dtype=np.uint8), 0.3, 0.12) == "nobody"


def test_the_tallest_person_tells_a_court_shot_from_people():
    # Court players from the stands are 0.18-0.33 of the frame's height; the
    # crowd, the bench and courtside, 0.4 and more (three NBA games).
    court = [(0.2, 0.5, 0.05, 0.22), (0.5, 0.55, 0.06, 0.31), (0.7, 0.8, 0.1, 0.33)]
    fans = [*court, (0.6, 0.6, 0.3, 0.55)]
    assert reactions.shot_kind(court, 0.36) == "court"
    assert reactions.shot_kind(fans, 0.36) == "people"
    assert reactions.shot_kind([], 0.36) == "nobody"


def test_a_stat_card_after_a_play_is_no_cutaway():
    # A full-screen stat card after a dunk has nobody in it: it is no
    # reaction shot. A card inside a cutaway doesn't end it either.
    card = [(296, "court"), (300, "court"), (306, "nobody"), (308, "nobody"), (310, "court")]
    assert reactions.cutaways(card, 400) == []
    bench = [(296, "court"), (300, "people"), (302, "nobody"), (304, "people"), (308, "court")]
    assert [(c.start, c.end, c.crowd) for c in reactions.cutaways(bench, 400)] == [(300, 308, True)]


def test_shots_are_read_by_the_detector_and_by_colour_without_it(monkeypatch):
    np = pytest.importorskip("numpy")
    from sports.core import scorebug
    from sports.soccer import ball

    people = {0: [(0.5, 0.5, 0.05, 0.25)], 1: [(0.5, 0.5, 0.3, 0.7)], 2: [(0.5, 0.5, 0.05, 0.25)]}
    widths = []

    def crops(path, box, size, on_frame, cancel=None, scale_width=None):
        widths.append(scale_width)
        for i in range(3):
            on_frame(i, np.full((9, 16, 3), i, dtype=np.uint8))
        return [0.0, 4.0, 8.0]

    monkeypatch.setattr(scorebug, "keyframe_crops", crops)
    monkeypatch.setattr("core.modes.probe_size", lambda path: (1920, 1080))
    monkeypatch.setattr(ball, "_model", lambda name: "model")
    monkeypatch.setattr(ball, "detect", lambda model, img, imgsz: ([], people[int(img[0, 0, 0])]))
    shots = reactions.read_shots("game.mp4", 12.0, 0.2, 0.255, tall=0.36)
    assert shots == [(0.0, "court"), (4.0, "people"), (8.0, "court")] and widths == [reactions.DETECT_WIDTH]

    def no_model(name):
        raise ImportError("no ultralytics")

    monkeypatch.setattr(ball, "_model", no_model)
    monkeypatch.setattr(reactions, "looks", lambda img, share, edges: "court")
    assert [k for _t, k in reactions.read_shots("game.mp4", 12.0, 0.2, 0.255, tall=0.36)] == ["court"] * 3
    assert widths[-1] == reactions.THUMB_WIDTH


def test_cutaways_are_the_shots_between_court_shots():
    shots = [(0, "court"), (4, "court"), (8, "people"), (10, "people"), (14, "court"),
             (20, "court"), (24, "other"), (80, "other"), (84, "court")]
    found = reactions.cutaways(shots, 100)
    assert [(c.start, c.end, c.crowd) for c in found] == [(8, 14, True)]     # the 60 s one is a break


def test_a_name_comes_only_from_the_broadcasts_caption():
    assert reactions.name_in(["SPIKE LEE"]) == "Spike Lee"
    assert reactions.name_in(["Jack Nicholson", "LIVE"]) == "Jack Nicholson"
    for not_a_name in (["REPLAY"], ["4TH QTR 0:32"], ["Crypto.com Arena"], ["Kiss Cam"], ["LAL Bos"]):
        assert reactions.name_in(not_a_name, exclude=("LAL", "BOS")) == "", not_a_name


def test_a_school_on_screen_is_not_a_person():
    # "King Philip", a school on a full-screen timeout graphic, passes as a
    # name: the video's title and the score bug say it is a team.
    title = "King Philip vs Attleboro girls basketball 2022"
    assert reactions.name_in(["King Philip"]) == "King Philip"
    assert reactions.name_in(["King Philip"], exclude=reactions.known_words(title)) == ""
    assert reactions.name_in(["Spike Lee"], exclude=reactions.known_words(title, "KP 41 ATT 38 4TH")) == "Spike Lee"


def test_a_crowd_reaction_after_a_dunk_is_one_moment_with_it():
    profile = _profile()
    cut = reactions.Cutaway(306.0, 312.0, crowd=True)
    m = _moments(profile, said={300: "throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 8)]), cutaways=[cut])
    dunk = next(e for e in m if e.type == "dunk")
    reaction = next(e for e in m if e.type == "crowd_reaction")
    assert dunk.end >= 312 and reaction.group == dunk.group           # the dunk's clip holds it
    assert reaction.importance < dunk.importance and "after the dunk" in reaction.signals


def test_a_celebrity_reaction_is_named_only_by_the_caption():
    profile = _profile()
    named = reactions.Cutaway(306.0, 311.0, crowd=True, name="Spike Lee")
    unnamed = reactions.Cutaway(606.0, 611.0, crowd=True)
    m = _moments(profile, said={300: "what a dunk", 600: "for three! from downtown"},
                 board=_board([(304, 0, 2), (604, 1, 3)]), curves=_curves(roars=[(301, 8), (601, 8)]),
                 cutaways=[named, unnamed])
    celeb = next(e for e in m if e.type == "celebrity_reaction")
    assert celeb.person == "Spike Lee" and "on screen: Spike Lee" in celeb.signals
    other = next(e for e in m if e.t == 606.0)
    assert other.type == "crowd_reaction" and other.person == ""


def test_a_crowd_shot_with_nothing_happening_is_not_a_reaction():
    m = _moments(_profile(), cutaways=[reactions.Cutaway(400.0, 405.0, crowd=True)])
    assert m == []


def test_a_strong_reaction_with_no_play_stands_on_its_own():
    m = _moments(_profile(), curves=_curves(roars=[(400, 6)]),
                 cutaways=[reactions.Cutaway(402.0, 408.0, crowd=True, name="Jack Nicholson")])
    celeb = next(e for e in m if e.type == "celebrity_reaction")
    assert celeb.start <= 402 and celeb.end >= 408


def test_fan_reactions_make_the_reaction_the_clips_moment():
    profile = _profile("fan_reactions")
    m = _moments(profile, said={300: "throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 8)]), cutaways=[reactions.Cutaway(306.0, 312.0, crowd=True)])
    reaction = next(e for e in m if e.type == "crowd_reaction")
    assert reaction.importance == 100
    assert reaction.start <= 300 and reaction.end >= 312              # the dunk, then the reaction
    candidate = ClipCandidate(start=reaction.start, end=reaction.end, score=50)
    attached = clips.attach(m, [candidate])
    kept, _dropped, _notes = clips.choose(profile, [candidate], attached, min_score=40, max_len=60)
    assert kept and attached[id(candidate)].type == "crowd_reaction"


def test_dunks_keeps_the_dunk_with_its_reaction_inside():
    profile = _profile("dunks")
    m = _moments(profile, said={300: "throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 8)]), cutaways=[reactions.Cutaway(306.0, 312.0, crowd=True)])
    dunk = next(e for e in m if e.type == "dunk")
    candidate = ClipCandidate(start=dunk.start, end=dunk.end, score=50)
    attached = clips.attach(m, [candidate])
    kept, dropped, _notes = clips.choose(profile, [candidate], attached, min_score=40, max_len=60)
    assert kept and not dropped and attached[id(candidate)].type == "dunk"


def test_a_basket_is_dated_by_its_score_not_an_earlier_plays_roar():
    # A highlights package: a big play's roar at 478, then a three at 502
    # whose crowd is quieter. The score shows at the 504 reading, the old one
    # last read at 500. Searching 25 s back took the earlier play's roar.
    profile = _profile()
    m = _moments(profile, board=_board([(502, 0, 3)]), curves=_curves(roars=[(478, 8), (503, 3)]))
    three = next(e for e in m if e.confirmed)
    assert 496 <= three.t <= 503 and three.start <= 502 <= three.end


def test_a_basket_nothing_heard_is_dated_just_after_the_old_score_was_last_read():
    profile = _profile()
    m = _moments(profile, board=_board([(702, 1, 2)]))                 # no crowd, no commentary
    basket = next(e for e in m if e.confirmed)
    assert basket.t == 701.0 and basket.start <= 702 <= basket.end    # not half a minute before


def _playoff_three(roars):
    """An NBA game's 81-79 to 84-79 three, its bug read at keyframes about 5 s
    apart: the ball went in at 615.6, the old score last read at 613.7 and
    the new one first at 618.8."""
    times = (598.4, 603.5, 608.6, 613.7, 618.8, 623.9)
    readings = [bb.Reading(t=t, score=(81, 79) if t < 615 else (84, 79), teams=("SAS", "OKC"), period=4,
                           clock=675.0 - (t - 600), visible=True) for t in times]
    profile = _profile()
    m = _moments(profile, board=bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1)), curves=_curves(roars=roars))
    return next(e for e in m if e.confirmed)


def test_a_basket_is_dated_by_its_score_bug_not_the_crowd_roaring_through_the_possession():
    """On an NBA game the bug showed each new score 1.3-2.6 s after the ball went
    in, while the crowd's loudest moment put four of five baskets 4-12 s
    early: this three's own window ended 3.6 s before the ball went in."""
    for roars in ([(610, 6)], [(605, 12)], []):
        three = _playoff_three(roars)
        assert three.t == pytest.approx(614.7), roars
        assert three.start <= 615.6 - 5 and three.end >= 615.6 + 3, roars      # the shot, then the landing
        assert three.when == "Q4 11:00", roars


def test_a_cutaway_after_the_next_possession_is_not_the_baskets_reaction():
    """On an NBA game a dunk's clip ran on through the next possession (a drive and
    a block) to a crowd shot 14 s after the dunk, and a three's through the
    next three to the shooter's close-ups: a clip is one play."""
    profile = _profile()
    close_ups = reactions.Cutaway(306.0, 309.0)
    stands = reactions.Cutaway(316.0, 319.0, crowd=True)
    m = _moments(profile, said={300: "throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 8), (316, 4)]), cutaways=[close_ups, stands])
    dunk = next(e for e in m if e.type == "dunk")
    assert 309 <= dunk.end < 316 and "after the dunk" not in next(e for e in m if e.t == 316.0).signals


def test_a_close_shot_well_after_a_basket_is_not_its_reaction():
    """On an NBA game an and-one's clip ran on through a turnover and a drive
    to a close shot of players 6.5 s after the basket: a play's clip holds a
    reaction only when it starts within REACTION_START of the play."""
    late = reactions.Cutaway(307.5, 311.0)
    m = _moments(_profile(), said={300: "throws it down! what a dunk"}, board=_board([(304, 0, 2)]),
                 curves=_curves(roars=[(301, 8)]), cutaways=[late])
    dunk = next(e for e in m if e.type == "dunk")
    assert dunk.t + reactions.REACTION_START < late.start and dunk.end < late.start + 1


def test_an_and_one_is_one_only_when_the_foul_is_called_as_it_goes_in():
    """On an NBA game a three ("two big threes") was called an and-one for a
    foul said 10 s from it, and its title said and-one."""
    foul_before = "a foul called on the other end"
    m = _moments(_profile(), said={488: foul_before, 500: "for three! from downtown"},
                 board=_board([(502, 0, 3)]), curves=_curves(roars=[(501, 6)]))
    three = next(e for e in m if e.confirmed)
    assert three.type == "made_3" and three.end - three.t == pytest.approx(5.0)       # a three's window
    m = _moments(_profile(), said={488: foul_before, 500: "drives, scores, and the foul!"},
                 board=_board([(502, 0, 3)]), curves=_curves(roars=[(501, 6)]))
    assert next(e for e in m if e.confirmed).type == "and_one"


def test_a_dunk_is_one_only_when_it_is_called_as_it_goes_in():
    """On an NBA game a layup ("a scoop to the hoop") was called a dunk for a
    "jam" said 12 s after it, about the next play, and its title said dunk."""
    m = _moments(_profile(), said={500: "a scoop to the hoop", 504: "and the follow jam"},
                 board=_board([(502, 0, 2)]), curves=_curves(roars=[(501, 6)]))
    basket = next(e for e in m if e.confirmed)
    assert (basket.type, basket.t) == ("made_2", 501.0)
    for said in ({500: "drives and throws it down!"}, {496: "he goes up for the slam", 500: "what a play"}):
        m = _moments(_profile(), said=said, board=_board([(502, 0, 2)]), curves=_curves(roars=[(501, 6)]))
        assert next(e for e in m if e.confirmed).type == "dunk"


def test_a_basket_between_keyframes_far_apart_is_dated_in_the_middle():
    """With the new score not found between keyframes 8.4 s apart, a basket
    dated just after the old score was 2.5 s early on an NBA game, and its
    clip ended as the bug changed: it is dated in the middle of where the
    ball could have gone in, as a pinpointed one is."""
    times = (781.0, 786.1, 794.5, 799.6)
    readings = [bb.Reading(t=t, score=(104, 95) if t < 790 else (107, 95), teams=("SAS", "OKC"), period=4,
                           clock=300.0 - (t - 781), visible=True) for t in times]
    m = _moments(_profile(), board=bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1)))
    three = next(e for e in m if e.confirmed)
    assert three.t == pytest.approx((786.1 + 794.5) / 2 - (1.3 + 2.6) / 2)
    assert three.end >= 793


def _three_samples(misread=None):
    """(time, the numbers read at the scorer's place) five times a second between
    keyframes 8.4 s apart, as on an NBA game: the old score until 790.3, the bug
    mid-roll, then the new score from 791.9."""
    out = []
    for i in range(43):
        t = round(786.1 + i * 0.2, 2)
        values = {104} if t < 790.4 else set() if t < 791.85 else {107}
        out.append((t, {107} if misread is not None and abs(t - misread) < 0.01 else values))
    return out


def test_a_baskets_new_score_is_pinpointed_between_its_keyframes():
    """On an NBA game keyframes 8 s apart dated a three 4 s early, and its clip
    ended as the ball went in: the bug is read five times a second between
    them for when it changed."""
    def change():
        return bb.ScoreChange(lo=776.1, hi=794.5, before=(104, 95), after=(107, 95), points=3, side=0,
                              last_old=786.1)

    c = change()
    assert bb.pinpoint(c, _three_samples(), lambda values: values)
    assert (c.last_old, c.shown) == (790.3, 791.9)
    # A "107" read once while the old score still shows is a misread.
    c = change()
    assert bb.pinpoint(c, _three_samples(misread=788.1), lambda values: values)
    assert (c.last_old, c.shown) == (790.3, 791.9)
    # The new score never read between them: the keyframes' times stand.
    c = change()
    assert not bb.pinpoint(c, [(t, v) for t, v in _three_samples() if 107 not in v], lambda values: values)
    assert (c.last_old, c.shown) == (786.1, None)


def test_a_score_is_read_again_where_its_piece_sat_widened_into_the_free_space():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    # A two-row bug 400 x 64: "SAS 104 | 3RD" over "OKC 95 | 8:41".
    pieces = [((0.02, 0.05, 0.18, 0.45), "SAS"), ((0.22, 0.05, 0.38, 0.45), "104"),
              ((0.60, 0.05, 0.75, 0.45), "3RD"), ((0.02, 0.55, 0.18, 0.95), "OKC"),
              ((0.22, 0.55, 0.34, 0.95), "95"), ((0.60, 0.55, 0.75, 0.95), "8:41")]
    new = bb.Reading(t=794.5, pieces=pieces)
    old = bb.Reading(t=786.1, pieces=[(b, "101" if text == "104" else text) for b, text in pieces])
    seen = []

    def rec(crop):
        seen.append((int(crop[0, 0, 0]) * 256 + int(crop[0, 0, 1]), int(crop[0, -1, 0]) * 256 + int(crop[0, -1, 1]),
                     int(crop[0, 0, 2]), int(crop[-1, 0, 2])))
        return "+3 107", 0.6

    read = bb._score_reader(new, old, (0.30, 0.25, 0.4), rec)
    img = np.zeros((64, 400, 3), np.uint8)
    img[..., 0], img[..., 1] = np.arange(400)[None, :] // 256, np.arange(400)[None, :] % 256
    img[..., 2] = np.arange(64)[:, None]
    assert read(img) == {107}                         # the "+3" drawn over it is no number of its own
    x0, x1, y0, y1 = seen[0]
    # Its own piece (88-152) grown by WIDEN of its height each side, but not into "SAS" (to 72).
    assert 74 <= x0 < 88 and 152 < x1 <= 152 + round(bb.WIDEN * 26) and (y0, y1) == (3, 28)
    assert bb._score_reader(new, old, (0.5, 0.25, 0.4), rec) is None    # nothing sits there


def test_a_synthetic_games_baskets_are_pinpointed_to_a_fifth_of_a_second(tmp_path):
    """The whole way, on a see-through bug over a moving picture: the
    keyframes are 2 s apart, the new score is found within 0.2 s."""
    import shutil
    import subprocess

    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("rapidocr_onnxruntime")
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg")
    width, height, fps, seconds, changes = 640, 360, 25, 26, [(9.3, 0, 3), (16.7, 1, 2)]
    path = tmp_path / "game.mp4"
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s",
                             f"{width}x{height}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast",
                             "-g", "50", "-keyint_min", "50", "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(path)],
                            stdin=subprocess.PIPE)
    x = np.linspace(0, 6.28, width)
    for i in range(fps * seconds):
        t = i / fps
        img = np.zeros((height, width, 3), np.uint8)
        img[:, :, 1] = (90 + 60 * np.sin(x + t))[None, :].astype(np.uint8)
        img[:, :, 2] = (60 + 40 * np.cos(x * 0.5 - t))[None, :].astype(np.uint8)
        score = [60, 55]
        for at, side, points in changes:
            score[side] += points if t >= at else 0
        img[300:350, 30:250] = (img[300:350, 30:250] * 0.35).astype(np.uint8)
        for row, (code, value) in enumerate((("SAS", score[0]), ("OKC", score[1]))):
            cv2.putText(img, code, (38, 320 + row * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(img, str(value), (95, 320 + row * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        left = 700 - int(t)
        cv2.putText(img, "3RD", (160, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.putText(img, f"{left // 60}:{left % 60:02d}", (160, 344), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 2)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    board = bb.read_video(path, float(seconds))
    found = [(c.after, c.last_old, c.shown) for c in board.changes]
    assert [a for a, _old, _new in found] == [(63, 55), (63, 57)]
    for (at, _side, _points), (_after, last_old, shown) in zip(changes, found):
        assert last_old < at <= shown <= at + 0.25


def test_a_pinpointed_basket_is_dated_from_when_the_bug_changed():
    """The NBA game's three again, its new score found between keyframes 8.4 s
    apart: dated from the bug's change, its clip runs on past it."""
    times = (775.9, 781.0, 786.1, 794.5, 799.6)
    readings = [bb.Reading(t=t, score=(104, 95) if t < 790 else (107, 95), teams=("SAS", "OKC"), period=4,
                           clock=227.0 - (t - 787), visible=True) for t in times]
    board = bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1))
    change = board.changes[0]
    assert bb.pinpoint(change, _three_samples(), lambda values: values)
    three = next(e for e in _moments(_profile(), board=board) if e.confirmed)
    assert three.t == pytest.approx((790.3 + 791.9) / 2 - 1.95)       # was 787.1 from the keyframes
    assert three.end >= 794.0                                          # 2 s past the bug's change, not 0.2 s


def test_a_shot_of_people_after_the_next_possession_doesnt_hold_the_basket():
    """On an NBA game a three's only reaction was a 1 s close-up as the bug
    changed; a player near the camera in the next fast break, 7 s after the
    three, read as a shot of people, and the three's clip ran on through two
    more possessions to its end."""
    times = (141.2, 142.0, 143.8, 147.1, 153.1, 157.2, 162.3, 167.4)
    readings = [bb.Reading(t=t, score=(21, 13) if t < 152 else (24, 13), teams=("SAS", "OKC"), period=1,
                           clock=331.0 - (t - 147), visible=True) for t in times]
    board = bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1))
    board.changes[0].last_old, board.changes[0].shown = 151.88, 153.08
    m = _moments(_profile(), board=board, cutaways=[reactions.Cutaway(157.2, 162.3, crowd=True)])
    three = next(e for e in m if e.confirmed)
    assert three.start <= 145 and 153.08 + 2 <= three.end < 157.2


def test_the_games_last_basket_runs_on_to_the_celebration():
    """On an NBA game the final dunk's clip stopped 2 s after it: the clock then
    ran out, and the bench celebrated 13 s after the dunk."""
    def ending(clock_left):
        times = (831.8, 836.8, 841.9, 847.0, 852.1, 857.1, 862.2, 867.3)
        readings = [bb.Reading(t=t, score=(109, 103) if t < 844 else (111, 103), teams=("SAS", "OKC"), period=4,
                               clock=max(0.0, clock_left - max(0.0, t - 846)), visible=True) for t in times]
        profile = _profile()
        profile.shots = [(t, "court") for t in times[:5]] + [(t, "people") for t in times[5:]]
        m = _moments(profile, board=bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1)))
        return next(e for e in m if e.confirmed)

    dunk = ending(4.1)
    assert dunk.end == pytest.approx(857.1 + 4.0)                      # into the first shot of the celebration
    assert ending(300.0).end < 852                                     # a game still on: one play


def test_a_clip_starts_and_ends_with_the_commentators_sentence():
    from sports.basketball.profile import speech_edges

    def seg(start, end, *words):
        step = (end - start) / len(words)
        return Segment(start=start, end=end, text=" ".join(words),
                       words=[{"start": start + i * step, "end": start + (i + 1) * step, "word": w}
                              for i, w in enumerate(words)])

    segments = [seg(10.0, 14.0, "he", "brings", "it", "up"), seg(14.0, 22.0, "fires", "from", "deep", "and",
                                                                     "it's", "good", "what", "a shot")]
    # Under a second into a sentence, or a second from its end: its edges.
    assert speech_edges(segments, 10.8, 21.0, 900.0) == (10.0, 22.0)
    # Deep inside one: the edges of the word under way, never shorter.
    assert speech_edges(segments, 16.5, 17.5, 900.0) == (16.0, 18.0)
    # Between sentences, nothing to move; and never past the video's end.
    assert speech_edges(segments, 14.0, 21.0, 21.5) == (14.0, 21.5)


def test_a_best_moments_basket_clip_is_the_one_play_not_the_scorers_longer_window():
    profile = _profile()
    m = _moments(profile, said={500: "for three! got it"}, board=_board([(502, 0, 3)]),
                 curves=_curves(roars=[(503, 3)]))
    three = next(e for e in m if e.confirmed)
    candidate = ClipCandidate(start=three.start - 14, end=three.end + 12, score=70)   # two more plays
    attached = clips.attach(m, [candidate])
    kept, _dropped, _notes = clips.choose(profile, [candidate], attached, min_score=40, max_len=60)
    assert kept and (candidate.start, candidate.end) == (three.start, three.end)


def test_a_basketball_clips_title_is_written_knowing_the_quarter_the_clock_and_the_score():
    from analysis import metadata

    prompts = []

    class Model:
        def generate(self, prompt, json_mode=False):
            prompts.append(prompt)
            return '{"items": []}'

    three = ClipCandidate(start=100, end=113, score=70, subscores={
        "sport_label": "Three", "sport_team": "SAS", "sport_when": "Q3 5:12", "sport_player": "Fox",
        "sport_why": "score 60-55 (SAS), +3; crowd roar"})
    winner = ClipCandidate(start=200, end=228, score=90, subscores={
        "sport_label": "Game winner", "sport_team": "OKC", "sport_when": "Q4 0:03",
        "sport_why": "score 110-111 (OKC), +2", "sport_context": "OKC 111, SAS 110: OKC take the lead"})
    goal = ClipCandidate(start=300, end=320, score=80, subscores={"sport_label": "Goal", "sport_minute": 67})
    # The teams not known: the score, whose is whose unsaid, is left out.
    step_back = ClipCandidate(start=400, end=414, score=70, subscores={
        "sport_label": "Step-back", "sport_when": "Q4 11:28", "sport_why": "score 81-79, +2; crowd roar"})
    metadata.generate_metadata_batch([three, winner, goal, step_back], _segments({}), "Spurs at Thunder", Model())
    # Who scored, as the commentary says it, or that it doesn't say.
    assert ("CLIP 0 (the scoreboard: Three (3 points) by SAS, making it 60-55; the commentary says Fox scored it; "
            "3rd quarter with 5:12 left; not crunch time, so not clutch or late-game):") in prompts[0]
    assert ("CLIP 1 (the scoreboard: Game winner (2 points) by OKC; OKC 111, SAS 110: OKC take the lead; "
            "the commentary doesn't say who scored it; 4th quarter with 0:03 left):") in prompts[0]
    assert "CLIP 2:\n" in prompts[0]                    # a soccer clip's block, as it always was
    assert ("CLIP 3 (the scoreboard: Step-back (2 points); the commentary doesn't say who scored it; "
            "4th quarter with 11:28 left; not crunch time, so not clutch or late-game):") in prompts[0]
    assert "RULES FOR THESE CLIPS" not in prompts[0]     # none given: the prompt as it always was


def test_a_basketball_games_titles_are_told_which_player_to_name():
    from analysis import metadata
    from sports.basketball.profile import BasketballProfile

    prompts = []

    class Model:
        def generate(self, prompt, json_mode=False):
            prompts.append(prompt)
            return '{"items": []}'

    three = ClipCandidate(start=100, end=113, score=70, subscores={
        "sport_label": "Three", "sport_team": "SAS", "sport_when": "Q3 5:12", "sport_why": "score 60-55 (SAS), +3"})
    rules = BasketballProfile(name="basketball", option={}).title_rules()
    metadata.generate_metadata_batch([three], _segments({}), "Spurs at Thunder", Model(), rules=rules)
    head, _, clips = prompts[0].partition("CLIPS:\n")
    assert clips.startswith("RULES FOR THESE CLIPS:\n- Each clip is one play")
    assert "the note's scorer" in clips and clips.index("RULES") < clips.index("CLIP 0 (the scoreboard")
    assert "Champagnie" not in rules                     # no real player's name to copy into another game's titles


def test_a_basketball_clips_title_is_written_from_what_is_said_in_the_clip_only():
    from analysis import metadata

    prompts = []

    class Model:
        def generate(self, prompt, json_mode=False):
            prompts.append(prompt)
            return '{"items": []}'

    def words(start, text):
        return [{"start": start + i, "end": start + i + 0.8, "word": w} for i, w in enumerate(text.split())]

    # One long sentence runs over three plays, as commentary does.
    said = "Fox drives and kicks Wembanyama for three and he hits it Holmgren answers"
    segments = [Segment(start=90.0, end=104.0, text=said, words=words(90.0, said))]
    three = ClipCandidate(start=94.0, end=100.0, score=70, subscores={
        "sport_label": "Three", "sport_team": "SAS", "sport_when": "Q3 5:12"})
    goal = ClipCandidate(start=94.0, end=100.0, score=70, subscores={"sport_label": "Goal", "sport_minute": 67})
    metadata.generate_metadata_batch([three, goal], segments, "Spurs at Thunder", Model())
    assert "not clutch or late-game):\nWembanyama for three and he hits\n" in prompts[0]
    assert f"CLIP 1:\n{said}" in prompts[0]             # a soccer clip: the sentences, as always


def test_a_title_never_calls_a_basket_clutch_when_the_scoreboard_says_it_was_not():
    from analysis import metadata

    class Model:
        def generate(self, prompt, json_mode=False):
            return json.dumps({"items": [
                {"index": i, "title": "CLUTCH three from Wemby!", "description": "A clutch three.",
                 "hashtags": ["nba"]} for i in range(3)]})

    def clip(when):
        return ClipCandidate(start=100, end=113, score=70, hook="Wemby from deep",
                             subscores={"sport_label": "Three", "sport_when": when} if when else
                             {"sport_label": "Goal", "sport_minute": 67})

    second, late, soccer = metadata.generate_metadata_batch(
        [clip("Q2 2:52"), clip("Q4 0:40"), clip("")], _segments({}), "Spurs at Thunder", Model())
    assert (second.title, second.description) == ("three from Wemby!", "A three.")
    assert late.title == soccer.title == "CLUTCH three from Wemby!"

    # ...nor late-game, nor game-changing (an NBA game: 7:49 left, and a three at +12 with 3:47 left).
    class Oversold:
        def generate(self, prompt, json_mode=False):
            return json.dumps({"items": [
                {"index": 0, "title": "Hartenstein's Late-Game Score!", "description": "A late game dunk.",
                 "hashtags": ["nba"]},
                {"index": 1, "title": "Harper's Game-Changing Three!", "description": "A game-changing three.",
                 "hashtags": ["nba"]}]})

    dunk, three = metadata.generate_metadata_batch([clip("Q4 7:49"), clip("Q4 3:47")], _segments({}),
                                                   "Spurs at Thunder", Oversold())
    assert (dunk.title, dunk.description) == ("Hartenstein's Score!", "A dunk.")
    assert (three.title, three.description) == ("Harper's Three!", "A three.")


def test_the_score_line_says_whose_score_is_whose_and_who_leads():
    from sports.basketball.profile import score_line

    def change(before, after, side, team="SAS", other="OKC"):
        return bb.ScoreChange(lo=0, hi=1, before=before, after=after, team=team, other=other,
                              points=after[side] - before[side], side=side)

    assert score_line(change((49, 50), (52, 50), 0)) == "SAS 52, OKC 50: SAS take the lead"
    assert score_line(change((55, 50), (58, 50), 0)) == "SAS 58, OKC 50: SAS lead by 8"
    assert score_line(change((50, 49), (50, 52), 1, "OKC", "SAS")) == "OKC 52, SAS 50: OKC take the lead"
    assert score_line(change((49, 53), (52, 53), 0)) == "SAS 52, OKC 53: SAS still trail by 1"
    assert score_line(change((50, 53), (53, 53), 0)) == "SAS 53, OKC 53: SAS tie it"
    # Without the teams, nothing: "the scorers 97, the other side 86" put the wrong team ahead.
    assert score_line(change((1, 0), (3, 0), 0, "", "")) == ""


def test_the_teams_are_named_from_the_videos_result_when_the_bug_has_no_letters():
    """Game 7's bug shows a logo and letters on their side, so the titles were
    told "the scorers 97, the other side 86", and one put OKC ahead from a
    "timeout OKC" in the commentary. The video's description says the Spurs
    won 111-103, and the bug's last score says which side that is."""
    from core.models import DownloadedVideo
    from sports.basketball import names

    title = "SPURS at THUNDER | FULL GAME 7 HIGHLIGHTS | May 28, 2026"
    assert names.sides(title, NBA_DESCRIPTION, (111, 103)) == ("Spurs", "Thunder")
    assert names.sides(title, NBA_DESCRIPTION, (103, 111)) == ("Thunder", "Spurs")
    assert names.sides("Game 7", NBA_DESCRIPTION, (111, 103)) == ("San Antonio Spurs", "Oklahoma City Thunder")
    # The bug's last score isn't the result, or the description doesn't say: no side guessed.
    assert names.sides(title, NBA_DESCRIPTION, (109, 103)) is None
    assert names.sides(title, "Game 7 of the Western Conference Finals.", (111, 103)) is None

    video = DownloadedVideo(video_id="g7", title=title, path=None, duration=955.4, description=NBA_DESCRIPTION)
    profile = sports.profile_for({"clips": {"sport": {"name": "basketball"}}}, video)
    board = _board([(690, 0, 2), (800, 0, 14), (800, 1, 17)], period=4, teams=None, start=(95, 86))
    basket = next(e for e in _moments(profile, board=board) if e.confirmed)
    assert basket.team == "Spurs" and basket.context == "Spurs 97, Thunder 86: Spurs lead by 11"
    # Without the description the score's sides stay unsaid.
    board = _board([(690, 0, 2), (800, 0, 14), (800, 1, 17)], period=4, teams=None, start=(95, 86))
    basket = next(e for e in _moments(_profile(), board=board) if e.confirmed)
    assert basket.team == "" and "lead" not in basket.context


def _said_at(start, text, step=0.4):
    """One commentary sentence from `start`, a word every `step` seconds."""
    words = text.split()
    return Segment(start=start, end=start + step * len(words), text=text,
                   words=[{"start": start + i * step, "end": start + (i + 1) * step, "word": " " + w}
                          for i, w in enumerate(words)])


def test_the_scorer_is_the_player_the_commentary_names_as_the_ball_goes_in():
    """On an NBA game the titles gave a three to the passer, another to a
    name heard once, and a third to a shot-blocker. The scorer is the player
    named last before the words that say the ball went in: not the passer,
    not one a pass to someone unnamed came after, not a name heard once."""
    from sports.basketball import commentary

    talk = [_said_at(10, "Marsh brings it up and Ruiz sets the screen"),
            _said_at(30, "Ruiz on the roll, spins to Okafor, Okafor knocks down the three!"),
            _said_at(60, "over to Marsh, Marsh the three"),
            _said_at(90, "Okafor drives, kicks it out, bang!"),
            _said_at(120, "Marsh has it, ahead to Fennimore, who goes in for the dunk"),
            _said_at(150, "Ruiz scores! And then Okafor gets a three"),
            _said_at(180, "a deep hit for Ruiz tonight")]
    names = commentary.Names(talk, known="Tobias Okafor, Atlanta Hawks", teams=("Hawks", "Atlanta Hawks"))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert who(35, 3) == "Okafor"              # the one who spun to him passed
    assert who(62, 3) == "Marsh"
    assert who(92, 3) == ""                    # kicked out to someone nobody named
    assert who(124, 2) == ""                   # "Fennimore", heard once and in no description
    assert who(154, 3) == "Okafor"             # a three: the words that say a three
    assert who(181, 3) == "Ruiz"               # named after the words
    assert names.is_name("Okafor") and names.is_name("MARSH'S") and not names.is_name("Hawks")
    assert not names.is_name("Bang")


def test_a_player_who_cant_get_the_board_is_not_the_scorer():
    """On an NBA game "Holmgren can't get the board ... you got a three on two
    here. Champagnie, Keldon Johnson off the wing" gave the basket to the
    player who missed the rebound before it: "can't" isn't "cans it", "the
    score" and "can score" say no basket, and a three on two is a fast break."""
    from sports.basketball import commentary

    talk = [_said_at(10, "a lob to Okafor, Okafor again, to Ruiz, Ruiz"),
            _said_at(30, "Okafor can't get the board. Gotta play fast. You got a three on two here. Ruiz off the wing."),
            _said_at(60, "Okafor can score from anywhere, the score is tied"),
            _said_at(90, "Okafor cans it from the corner")]
    names = commentary.Names(talk, teams=("Hawks",))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert names.sure("Okafor") and names.sure("Ruiz")
    assert who(36, 2) == "" and who(36, 3) == ""
    assert who(64, 2) == ""
    assert who(91, 3) == "Okafor"


def test_words_said_well_before_a_basket_are_the_play_befores():
    """On an NBA game "Carter Bryant secures it, Fox advances it, Bryant, got
    it!", six seconds before a Thunder three nobody called, gave the three to
    Bryant: the words that say the ball went in are said with it."""
    from sports.basketball import commentary

    talk = [_said_at(300.6, "Carter Bryant secures it, Fox advances it, Bryant, got it!", step=0.6),
            _said_at(307.0, "We open the season here with a double overtime thriller")]
    names = commentary.Names(talk, teams=("Spurs", "Thunder"))

    assert names.sure("Bryant")
    assert commentary.scorer(talk, 312.9, 3, names, lo=300, hi=320) == ""
    assert commentary.scorer(talk, 307.5, 2, names, lo=300, hi=320) == "Bryant"


def test_a_shot_the_commentary_takes_back_is_no_ones_basket():
    """On an NBA game "Holgren thought about the three, didn't take it. Caruso
    will for the lead, got it" gave the three to the player who passed it up."""
    from sports.basketball import commentary

    talk = [_said_at(10, "a board for Holgren, and Holgren again, to Caruso"),
            _said_at(30, "Caruso the pitch. Holgren thought about the three, didn't take it. Caruso will for the lead, got it,"),
            _said_at(60, "Holgren for three, no good. Caruso with the board"),
            _said_at(90, "Holgren for three, didn't miss, Caruso can't believe it"),
            _said_at(120, "Holgren can't finish, but Caruso does!"),
            _said_at(150, "Holgren misses the three, Caruso for three, got it"),
            _said_at(180, "he can't miss tonight, Holgren for three"),
            _said_at(210, "Holgren doesn't miss the three")]
    names = commentary.Names(talk, teams=("Thunder",))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert names.sure("Holgren") and names.sure("Caruso")
    assert who(35, 3) == "Caruso"
    assert who(62, 3) == ""                    # no good
    assert who(91, 3) == "Holgren"             # "didn't miss" says it went in
    assert who(121, 2) == ""                   # an NBA game named the player who couldn't finish
    assert who(153, 3) == "Caruso"
    assert who(182, 3) == "Holgren"
    assert who(211, 3) == "Holgren"


def test_a_name_whisper_didnt_know_before_the_call_names_no_one():
    """On an NBA game "Williams, pitched it outside. Swarer's hit the 3!" gave
    the three to Williams, who passed it. A word with a capital the
    commentary never says without one is a name Whisper didn't know, and it
    may be the scorer's; one said in lower case elsewhere is just a word."""
    from sports.basketball import commentary

    talk = [_said_at(10, "up top Williams, Williams again, a wide look for Fox"),
            _said_at(30, "Williams, pitched it outside. Swarer's hit the three!"),
            _said_at(60, "Williams drives. Swarer's hit the three!"),
            _said_at(90, "Williams drives. He hits the three!"),
            _said_at(120, "Williams. Wide open, knocks it down!")]
    names = commentary.Names(talk, teams=("Thunder",))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert names.sure("Williams") and not names.is_name("Swarer's")
    assert who(32, 3) == ""                    # passed outside, to someone the commentary didn't name
    assert who(61, 3) == ""                    # a name Whisper didn't know hit it
    assert who(91, 3) == "Williams"
    assert who(121, 2) == "Williams"


def test_a_defender_or_a_passer_named_before_the_call_is_not_the_scorer():
    """On a Warriors-Mavericks game "drives into Washington, and scores" gave
    a Warriors three to the Mavericks' Washington, the defender. A name just
    after "into", "by", "over" or "from" is the defender's or the passer's:
    no one is named then. One the ball is passed to still scores ("ahead to
    Ruiz, Ruiz knocks down the three"), and "hands it over," names no one."""
    from sports.basketball import commentary

    talk = [_said_at(10, "Marsh with it, Marsh drives into Okafor, and scores!"),
            _said_at(40, "the lob from Okafor, throws it down!"),
            _said_at(70, "Marsh blows by Okafor, lays it in"),
            _said_at(100, "Marsh rises over Kai Ruiz, slams it home"),
            _said_at(130, "Okafor has it, ahead to Ruiz, Ruiz knocks down the three"),
            _said_at(160, "Okafor hands it over, Marsh knocks down the three")]
    names = commentary.Names(talk, known="Kai Ruiz", teams=("Hawks",))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert names.sure("Marsh") and names.sure("Okafor")
    assert who(14, 2) == "" and who(43, 2) == "" and who(73, 2) == "" and who(103, 2) == ""
    assert who(135, 3) == "Ruiz"
    assert who(165, 3) == "Marsh"


def test_a_name_a_letter_off_the_descriptions_names_no_one():
    """On a 79-minute NBA game the commentary wrote the player the video's
    description spells "Reaves" as "Reeves" 63 times and "Raves" 3 times, and
    a title went out naming "Raves". A name a letter off one the description
    spells is Whisper mishearing it: no name rather than a wrong one, and not
    the description's spelling put in its place, since it may be another
    player's ("Jovic" beside a description's "Jokic")."""
    from sports.basketball import commentary

    talk = [_said_at(10, "over to Reeves, Reeves the three!"),
            _said_at(40, "a lob to Raves, Raves lays it in"),
            _said_at(70, "and Reaves for three, got it"),
            _said_at(100, "over to Marsh, Marsh the three!")]
    names = commentary.Names(talk, known="Austin Reaves, Los Angeles Lakers", teams=("Lakers", "Los Angeles Lakers"))

    def who(t, points):
        return commentary.scorer(talk, t, points, names, lo=t - 20, hi=t + 20)

    assert names.is_name("Reeves") and names.is_name("Raves")
    assert not names.sure("Reeves") and not names.sure("Raves") and names.sure("Reaves") and names.sure("Marsh")
    assert who(13, 3) == "" and who(43, 2) == ""
    assert who(72, 3) == "Reaves"
    assert who(103, 3) == "Marsh"
    assert commentary._near("fussell", "vassell") and not commentary._near("brown", "braun")
    assert not commentary._near("fox", "box") and not commentary._near("curry", "murray")


def _basket_game(said, baskets, description="", title="Spurs at Thunder", teams=("Spurs", "Thunder"), start=(0, 0),
                 **profile_extra):
    """A profile run over `said` (segments with word timings) and a board of
    `baskets` (video second, side, points) between the Spurs and the Thunder."""
    from core.models import DownloadedVideo

    video = DownloadedVideo(video_id="g", title=title, path=None, duration=N, description=description)
    profile = sports.profile_for({"clips": {"sport": {"name": "basketball", **profile_extra}}}, video)
    board = _board(baskets, period=4, teams=teams, start_clock=900.0, start=start)
    profile.board, profile.cutaways, profile.curves = board, [], _curves()
    filler = [Segment(start=float(s), end=float(s + 4), text="bringing it up the floor") for s in range(0, N, 4)
              if not any(s < seg.end and seg.start < s + 4 for seg in said)]
    segments = sorted(said + filler, key=lambda s: s.start)
    moments = detect.moments(profile, segments, curves=profile.curves, board=board, video_end=float(N),
                             min_len=10, max_len=60)
    return profile, moments, segments


def test_a_bugs_team_letters_are_the_nba_teams_the_video_names():
    """On an NBA game the bug read "GSW" and "DAL": the titles written from the
    scoreboard said "GSW Tie It Up!", a title saying "the Warriors' lead" for a
    Mavericks three went through, and "Dallas" read as a player's name. The
    letters are the NBA teams the video names, each by its nickname."""
    from sports.basketball import names, titles

    title = "WARRIORS at MAVERICKS | FULL GAME HIGHLIGHTS | January 22, 2026"
    description = ("The Mavericks defeated the Warriors, 123-115 tonight in Dallas. Cooper Flagg scored 30 and "
                   "P.J. Washington added 20.")
    assert names.nba_letters(("GSW", "DAL"), title, description) == ("Warriors", "Mavericks")
    assert names.nba_letters(("LAL", "GS"), "Lakers vs Warriors WILD Christmas Day Ending",
                             "...to lift the Lakers over Golden State, 115-113.") == ("Lakers", "Warriors")
    # A nickname that is also a word or a name names no team on its own.
    assert names.nba_letters(("LAL", "BOS"), "Magic Johnson's Best Plays") is None
    assert names.nba("Heat Check: Curry Goes Off") == []

    said = [_said_at(596.0, "Washington, Washington from the corner, got it!", 0.5)]
    profile, moments, _segments = _basket_game(said, [(603, 1, 3)], description=description, title=title,
                                               teams=("GSW", "DAL"), start=(106, 118))
    basket = next(e for e in moments if e.confirmed)
    assert basket.team == "Mavericks" and basket.context.startswith("Mavericks 121, Warriors 106")
    assert profile.board.teams() == ("GSW", "DAL")                 # as the bug shows them
    assert not profile.names.is_name("Dallas") and profile.names.is_name("Washington")
    c = ClipCandidate(start=basket.start, end=basket.end, score=80)
    clips.mark(c, basket, profile.event_label(basket.type), 10)
    play = profile.play_of(c)
    assert (play.team, play.other, play.scorer) == ("Mavericks", "Warriors", "Washington")
    assert {"Dallas", "Mavs", "DAL"} <= set(play.aliases) and "Golden State" in play.other_aliases
    wrong = _meta("Washington's Three!", "P.J. Washington scores a three-pointer, extending the Warriors' lead.")
    assert any("Mavericks lead 121-106" in p for p in titles.problems(play, wrong, profile.names))
    assert titles.problems(play, _meta("Washington From the Corner!", "Dallas pulls away."), profile.names) == []
    assert titles.written(play, wrong).title == "Washington's Three Puts the Mavericks Up 15"


def test_with_neither_side_known_a_title_names_no_team():
    """A game whose bug shows logos, and whose description gives the result
    in no shape the sides can be told from ("to lift the Lakers over Golden
    State"), had "Lakers", "Warriors" and "Golden State" read as players'
    names: every title naming a team was told to name no player, and came
    back naming the team again. The teams the video names are teams, and
    since nothing says which one scored, a title naming one is told so."""
    from sports.basketball import names, titles

    title = "Lakers vs Warriors WILD Christmas Day Ending | NBA Classic Game"
    description = ("Austin Reaves sealed the win with a running layup with 1 second left to lift the Lakers over "
                   "Golden State, 115-113.")
    assert names.teams("SPURS at THUNDER | FULL GAME HIGHLIGHTS", "the Spurs beat the Thunder") == ("Spurs", "Thunder")
    assert names.teams("Curry vs LeBron: The Duel", "Stephen Curry and LeBron James go at it.") == ()
    said = [_said_at(596.0, "Reaves, Reaves running floater, good!", 0.5)]
    profile, moments, _segments = _basket_game(said, [(603, 0, 2)], description=description, title=title,
                                               teams=None, start=(111, 113))
    basket = next(e for e in moments if e.confirmed)
    c = ClipCandidate(start=basket.start, end=basket.end, score=80)
    clips.mark(c, basket, profile.event_label(basket.type), 10)
    play = profile.play_of(c)
    assert (play.team, play.other, play.scorer) == ("", "", "Reaves")
    assert not any(profile.names.is_name(w) for w in ("Lakers", "Warriors", "Golden", "State"))
    named = _meta("Reaves Ties It for the Lakers!", "Austin Reaves ties it for Los Angeles.")
    assert titles.problems(play, named, profile.names) == [
        "The scoreboard doesn't say which team scored it: name no team."]
    assert titles.problems(play, _meta("Reaves Ties It!", "Austin Reaves ties it."), profile.names) == []


def test_a_basket_is_typed_and_credited_by_the_words_said_as_it_went_in():
    """Game 7's last sentence ran 23 s over three plays, so its final dunk was
    typed by its points, a basket, and its one clip went to the layup before
    it. The words said as each basket went in type it and name its scorer."""
    said = [_said_at(496.0, "Okafor has it, ahead to Ruiz, who goes in for the dunk and the crowd goes wild", 0.5)]
    profile, moments, _segments = _basket_game(said, [(503, 0, 2)], description="Kai Ruiz scored 30.")
    dunk = next(e for e in moments if e.confirmed)
    assert dunk.type == "dunk" and dunk.player == "Ruiz"
    assert dunk.end - dunk.t >= 6.5                                    # a dunk's reaction, not a layup's
    assert 'said "dunk"' in dunk.signals
    # A word for another kind of basket doesn't retype it: a three isn't a dunk.
    said = [_said_at(496.0, "Okafor has it, ahead to Ruiz, who goes in for the dunk and the crowd goes wild", 0.5)]
    _profile3, moments, _segments = _basket_game(said, [(503, 0, 3)], description="Kai Ruiz scored 30.")
    assert next(e for e in moments if e.confirmed).type == "made_3"


def test_two_baskets_are_two_clips_however_close():
    """On an NBA game a Thunder three 6 s after a Spurs three was "the same
    moment" and had no clip, and a sentence's clip over the last layup and the
    final dunk showed the layup, so the dunk had no window of its own."""
    _profile_, moments, _segments = _basket_game([], [(500, 0, 3), (506, 1, 3)])
    first, second = sorted((e for e in moments if e.confirmed), key=lambda e: e.t)
    assert first.overlaps(second) and first.group != second.group
    candidate = ClipCandidate(start=first.start - 1, end=second.end, score=70)      # over both
    shown = clips.attach(moments, [candidate])[id(candidate)]
    other = second if shown is first else first
    assert other in clips.windows_to_add(moments, [candidate], shown_only=True)
    assert other not in clips.windows_to_add(moments, [candidate])                  # Soccer's, as it was


def test_soccer_keeps_overlapping_moments_as_one():
    soccer = sports.profile_for({"clips": {"sport": {"name": "soccer"}}})
    assert soccer.one_play_per_clip is False and _profile().one_play_per_clip is True
    from sports.core.events import SportEvent, group_moments

    goals = [SportEvent("goal", 100.0, 1.0, 100, 90.0, 120.0, confirmed=True),
             SportEvent("goal", 106.0, 1.0, 100, 96.0, 126.0, confirmed=True)]
    assert [e.group for e in group_moments(goals, soccer.replay_within)] == [1, 1]
    assert [e.group for e in group_moments(goals, soccer.replay_within, True)] == [1, 2]


def test_the_games_last_basket_runs_on_without_a_shot_of_people():
    """The players celebrating on the court read as a court shot: the game is
    over, so the final basket's clip still runs on into the celebration."""
    times = (831.8, 836.8, 841.9, 847.0, 852.1, 857.1, 862.2, 867.3)
    readings = [bb.Reading(t=t, score=(109, 103) if t < 844 else (111, 103), teams=("SAS", "OKC"), period=4,
                           clock=max(0.0, 4.1 - max(0.0, t - 846)), visible=True) for t in times]
    profile = _profile()
    profile.shots = [(t, "court") for t in times]
    dunk = next(e for e in _moments(profile, board=bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1)))
                if e.confirmed)
    assert dunk.t + 18.5 <= dunk.end <= dunk.t + 21.5                  # (ending with the sentence under way)


def _play(**kw):
    from sports.basketball import titles

    base = {"points": 3, "kind": "made_3", "shot": "three", "team": "Spurs", "other": "Thunder", "mine": 52,
            "theirs": 53, "before": -4, "when": "Q2 0:53", "scorer": "Marsh",
            "aliases": ("Spurs", "San Antonio Spurs", "San Antonio"),
            "other_aliases": ("Thunder", "Oklahoma City Thunder", "Oklahoma City")}
    return titles.Play(**{**base, **kw})


def _meta(title, description="A three for the Spurs."):
    from analysis.metadata import ClipMetadata

    return ClipMetadata(title=title, description=description, hashtags=["#nba"])


def _names():
    from sports.basketball import commentary

    talk = [_said_at(10, "over to Marsh, Marsh the three, Ruiz with the rebound, Ruiz again, Okafor and Okafor")]
    return commentary.Names(talk, teams=("Spurs", "Thunder", "San Antonio Spurs", "Oklahoma City Thunder"))


@pytest.mark.parametrize("play, title, description, wrong", [
    # The kinds of wrong an NBA game's titles got, each on a made-up play.
    ({}, "Marsh Finds the Range!", "Marsh hits the three, extending the Spurs' lead.", "still trail 52-53"),
    ({"team": "Thunder", "other": "Spurs", "mine": 103, "theirs": 109, "before": -8, "scorer": "",
      "aliases": ("Thunder",), "other_aliases": ("Spurs",), "points": 2, "shot": "bucket"},
     "Thunder Surge Ahead!", "The Thunder tie the game with a layup.", "still trail 103-109"),
    ({"mine": 68, "theirs": 63, "before": 2}, "Marsh Pulls Away!", "Marsh's three gives the Spurs a three-point lead.",
     "lead 68-63, by 5"),
    ({"points": 2, "shot": "bucket", "scorer": ""}, "Late Shot Clock Drama!",
     "A heave at the shot clock, but it misses.", "don't call it a miss"),
    ({}, "Ruiz's Rookie Spin!", "Ruiz spins and finds Marsh, who nails the three.", "name only Marsh"),
    ({"scorer": ""}, "Okafor Blocks It!", "Okafor with the block.", "name no player"),
    ({"points": 2, "shot": "bucket"}, "Marsh's Step-Back Three!", "Marsh scores.", "not a three"),
    ({}, "Marsh From Deep!", "", "Write a title and a description"),
    ({"mine": 107, "theirs": 95, "before": 9}, "Marsh's Three Keeps Hope Alive", "Marsh buries the three.",
     "they already led"),
    ({"mine": 107, "theirs": 95, "before": 9}, "The Spurs Rally!", "Marsh buries the three.", "they already led"),
    ({}, "Marsh Extends the Lead", "Marsh hits the three.", "still trail 52-53"),
    # ...and a 79-minute game's and a 16-minute one's, after the first round of fixes.
    ({"mine": 61, "theirs": 63, "before": -5}, "Spurs Up 2!", "The Spurs take a two-point lead.", "still trail 61-63"),
    ({"team": "Thunder", "other": "Spurs", "mine": 63, "theirs": 61, "before": -1, "scorer": "", "points": 2,
      "shot": "bucket", "aliases": ("Thunder",), "other_aliases": ("Spurs",)},
     "Spurs Up 2!", "A bucket.", "they took the lead"),
    ({"mine": 72, "theirs": 69, "before": 1, "points": 2, "shot": "bucket", "scorer": "", "when": "Q3 5:19"},
     "Spurs Take the Early Lead!", "The Spurs start strong.", "they already led"),
    ({"mine": 72, "theirs": 69, "before": 1, "points": 2, "shot": "bucket", "scorer": "", "when": "Q3 5:19"},
     "Spurs Take the Early Lead!", "The Spurs start strong, establishing an early advantage.",
     "not early in the game"),
    ({"mine": 102, "theirs": 98, "before": 1}, "Marsh From Deep!",
     "Marsh hits a three, increasing their lead by four points.", "up 1 before it and up 4 after it"),
    ({"mine": 90, "theirs": 97, "before": -8, "points": 1, "shot": "free throw", "kind": "free_throw", "scorer": "",
      "when": "Q4 6:52"}, "Spurs Secure Free Throw Win", "The Spurs close out the game at the line.",
     "didn't win or seal"),
    ({"mine": 90, "theirs": 97, "before": -8, "points": 1, "shot": "free throw", "kind": "free_throw", "scorer": "",
      "when": "Q4 6:52"}, "Spurs at the Line", "A free throw, securing the victory.", "didn't win or seal"),
    ({"mine": 113, "theirs": 113, "before": -3, "kind": "game_tying", "when": "Q4 0:06"},
     "Game-Tying Shot by Marsh!", "Marsh ties it at 113 with just minutes remaining.",
     "6 seconds left in the 4th quarter"),
    ({"mine": 113, "theirs": 113, "before": -3, "kind": "game_tying", "when": "Q4 0:06"},
     "Marsh Forces Overtime!", "Marsh ties it at 113.", "didn't go to overtime"),
    ({"when": "Q3 5:19"}, "Marsh Cuts It to 1", "Marsh hits a three in the fourth quarter.", "in the 3rd quarter"),
    ({"when": "Q2 3:40"}, "Marsh Cuts It to 1", "Marsh's three with 50 seconds left.", "3:40 left"),
    ({"when": "Q2 3:40"}, "Marsh Beats the Buzzer!", "Marsh's three.", "3:40 left"),
    # ...and the same 79-minute game's, after the second.
    ({"mine": 42, "theirs": 46, "before": -6, "points": 2, "shot": "bucket", "scorer": ""}, "Spurs Struggle!",
     "The Spurs continue to fall behind, struggling to contain the Thunder's offense.", "don't say they struggle"),
    ({"mine": 42, "theirs": 46, "before": -6, "points": 2, "shot": "bucket", "scorer": ""}, "Spurs Struggle!",
     "A bucket for the Spurs.", "don't say they struggle"),
    ({"mine": 42, "theirs": 46, "before": -6, "points": 2, "shot": "bucket", "scorer": ""}, "A Bucket for the Spurs",
     "The San Antonio Spurs fall further behind.", "still trail 42-46"),
])
def test_a_title_that_gets_its_play_wrong_is_caught(play, title, description, wrong):
    from sports.basketball import titles

    found = titles.problems(_play(**play), _meta(title, description), _names())
    assert any(wrong in p for p in found), found


@pytest.mark.parametrize("play, title, description", [
    ({}, "Marsh Cuts It to 1!", "Marsh hits the three. The Thunder still lead 53-52."),
    ({"mine": 95, "theirs": 86, "before": 6}, "Marsh Drops the Heat!", "Marsh doesn't miss: Spurs up 9."),
    ({"mine": 73, "theirs": 65, "before": 5}, "Spurs Pull Away", "Ruiz finds Marsh, who nails the three."),
    ({"team": "Thunder", "other": "Spurs", "mine": 103, "theirs": 109, "before": -8, "scorer": "", "points": 2,
      "shot": "bucket", "aliases": ("Thunder",), "other_aliases": ("Spurs",)},
     "Thunder Cut It to 6", "The Thunder cut the Spurs' lead to 6 with 52 seconds left."),
    ({"mine": 111, "theirs": 103, "before": 6, "sealed": True, "points": 2, "shot": "dunk"},
     "Marsh Seals It!", "Marsh throws it down and the Spurs win it, 111-103."),
    ({}, "Marsh Keeps Hope Alive", "Marsh hits the three to cut into the lead. The Thunder still lead 53-52."),
    ({"mine": 55, "theirs": 53, "before": -1}, "The Spurs Complete the Comeback!",
     "Marsh's three gives the Spurs the lead."),
    ({"mine": 123, "theirs": 111, "before": 9, "when": "Q4 1:30"}, "Marsh's Three Puts the Spurs Up 12",
     "Marsh hits a three with 1:30 left. The Spurs lead, 123-111."),
    ({"mine": 102, "theirs": 98, "before": 1}, "Marsh From Deep!", "Marsh hits a three, extending the Spurs' lead by 3."),
    ({"mine": 113, "theirs": 113, "before": -3, "kind": "game_tying", "when": "Q4 0:06"},
     "Game-Tying Shot by Marsh!", "Marsh ties it at 113 with 6 seconds left in the fourth quarter."),
    ({"mine": 113, "theirs": 113, "before": -3, "kind": "game_tying", "when": "Q4 0:06", "overtime": True},
     "Marsh Forces Overtime!", "Marsh ties it at 113 in the final seconds."),
    ({"mine": 30, "theirs": 24, "before": 3, "when": "Q1 4:00"}, "Marsh From Deep",
     "Marsh drills a three, pushing the Spurs' early lead to 6."),
    ({"when": "Q2 0:53"}, "Marsh Cuts It to 1 Before the Half",
     "Ruiz finds Marsh, who hits the three in the 2nd quarter. The Thunder still lead 53-52."),
    ({"mine": 72, "theirs": 69, "before": 1, "points": 2, "shot": "bucket", "scorer": "", "when": "Q3 5:19"},
     "Spurs Stay in Front", "The Spurs hold the lead in the third quarter, a shot-clock buzzer-beater."),
    ({"when": "Q2 3:40"}, "Marsh Cuts It to 1", "Marsh hits a three with 5 seconds left on the shot clock."),
    ({"mine": 72, "theirs": 69, "before": 1, "points": 2, "shot": "bucket", "scorer": "", "when": "Q3 10:30"},
     "Spurs Out of the Gate", "The Spurs come out of the gate strong after halftime."),
    ({"mine": 42, "theirs": 46, "before": -6, "points": 2, "shot": "bucket", "scorer": ""}, "Spurs Snap Their Slump",
     "A bucket ends the Spurs' slump. The Thunder still lead 46-42."),
    ({"mine": 60, "theirs": 52, "before": 5}, "Spurs Stun the Struggling Thunder",
     "Marsh's three puts the Spurs up 8, and the Spurs score while Thunder struggle."),
])
def test_a_title_true_to_its_play_stays(play, title, description):
    from sports.basketball import titles

    assert titles.problems(_play(**play), _meta(title, description), _names()) == []


def test_a_first_name_the_commentary_doesnt_give_the_scorer_is_caught():
    """On a 79-minute NBA game a description gave a scorer the commentary
    calls "Schroeder" a first name it never says, and another joined the
    passer's name to the shooter's ("Vincent Raves"). A first name stays
    only as the commentary says it with the name, or as the video's own
    description spells it; a passer named apart, or a word like "guard",
    is no first name."""
    from sports.basketball import commentary, titles

    talk = [_said_at(10, "over to Okafor, out to Marsh, Marsh the three! Kai Ruiz with the rebound, Ruiz again")]
    names = commentary.Names(talk, known="Jay Marsh", teams=("Spurs", "Thunder"))

    def found(scorer, description, title="From Deep!"):
        return titles.problems(_play(scorer=scorer), _meta(title, description), names)

    added = "with no first name it doesn't give"
    assert any(added in p for p in found("Marsh", "Okafor Marsh hits the three."))
    assert any(added in p for p in found("Ruiz", "Leo Ruiz hits the three."))
    assert any("name only Ruiz" in p for p in found("Ruiz", "Ruiz hits the three.", title="Okafor Ruiz From Deep!"))
    for scorer, description in (("Marsh", "Jay Marsh hits the three."), ("Marsh", "Okafor finds Marsh for three."),
                                ("Ruiz", "Kai Ruiz hits the three."), ("Ruiz", "Spurs guard Ruiz hits the three."),
                                ("Ruiz", "The Spurs' Ruiz hits the three.")):
        assert found(scorer, description) == [], description


def test_a_title_written_from_the_play_says_only_what_it_holds():
    from sports.basketball import titles

    meta = _meta("wrong", "")
    w = titles.written(_play(), meta, 0)
    assert (w.title, w.description) == ("Marsh's Three Cuts It to 1",
                                        "Marsh hits a three for the Spurs with 0:53 left in the 2nd quarter. "
                                        "The Thunder still lead, 53-52.")
    w = titles.written(_play(scorer="", team="Thunder", other="Spurs", mine=103, theirs=109, before=-8, points=2,
                             shot="bucket", kind="made_2", when="Q4 0:52"), meta, 1)
    assert (w.title, w.description) == ("The Thunder Cut It to 6",
                                        "The Thunder score with 0:52 left in the 4th quarter. "
                                        "The Spurs still lead, 109-103.")
    w = titles.written(_play(mine=111, theirs=103, before=6, points=2, shot="dunk", kind="dunk", sealed=True,
                             when="Q4 0:03"), meta, 0)
    assert w.title == "Marsh Seals It for the Spurs!" and w.description.endswith("The Spurs win it, 111-103.")
    w = titles.written(_play(team="", other="", scorer="", when=""), meta, 0)
    assert (w.title, w.description) == ("What a Three!", "A three.")
    for i, play in enumerate((_play(), _play(mine=68, theirs=63, before=2), _play(mine=53, theirs=53))):
        w = titles.written(play, meta, i)
        assert titles.problems(play, w, _names()) == [], (w.title, w.description)


def test_a_wrong_title_is_written_again_and_then_from_the_scoreboard():
    """The NBA game's titles, checked: a clip whose title gets its play wrong
    is written again, told what it got wrong; one still wrong, or skipped,
    is written from the scoreboard; a right one is left alone."""
    from sports.basketball import titles

    said = [_said_at(196.0, "Marsh on the wing, Marsh the three!", 0.5),
            _said_at(296.0, "over to Vance, Vance lets it fly, got it!", 0.5),
            _said_at(394.0, "back out to Okafor, Okafor to Ruiz, Ruiz knocks down the three", 0.5)]
    profile, moments, segments = _basket_game(said, [(203, 0, 3), (303, 1, 3), (403, 0, 3)],
                                              description="Jay Marsh, Leo Vance and Kai Ruiz.")
    shown = sorted((e for e in moments if e.confirmed), key=lambda e: e.t)
    assert [e.player for e in shown] == ["Marsh", "Vance", "Ruiz"]
    candidates = []
    for e in shown:
        c = ClipCandidate(start=e.start, end=e.end, score=80)
        clips.mark(c, e, profile.event_label(e.type), 10)
        candidates.append(c)
    metas = [_meta("Marsh Drills It!", "Marsh hits the three. Spurs lead."),          # right
             _meta("Spurs Take the Lead!", "The Spurs lead."),                         # wrong: the Thunder tied it
             _meta("Okafor's Dime!", "Okafor finds Ruiz.")]                            # wrong: Ruiz scored
    asked = []

    def rewrite(subset, rules):
        asked.append((len(subset), rules))
        return [_meta("Vance Ties It for the Thunder!", "Vance hits a three for the Thunder."),
                _meta("Okafor Again!", "Okafor.")]                                     # still wrong

    out = titles.check(profile, candidates, metas, rewrite)
    assert out[0] is metas[0]
    assert out[1].title == "Vance Ties It for the Thunder!"
    assert "Ruiz" in out[2].title and "Okafor" not in out[2].title + out[2].description
    count, rules = asked[0]
    assert count == 2 and "- CLIP 1: " in rules and "name only Ruiz" in rules


def test_more_wrong_titles_than_one_batch_are_written_again_batch_by_batch(capsys):
    """The title writer numbers each batch of 8 clips from 0: a ninth wrong
    title is written again in a call of its own, its rule numbered as its
    clip is, and the log says how many were wrong (or that none were)."""
    from sports.basketball import titles

    said = [_said_at(394.0, "back out to Okafor, Okafor to Ruiz, Ruiz knocks down the three", 0.5)]
    profile, moments, _segments = _basket_game(said, [(403, 0, 3)], description="Kai Ruiz.")
    e = next(e for e in moments if e.confirmed)
    c = ClipCandidate(start=e.start, end=e.end, score=80)
    clips.mark(c, e, profile.event_label(e.type), 10)
    asked = []

    def rewrite(subset, rules):
        asked.append((len(subset), rules))
        return [_meta("Ruiz Knocks It Down!", "Ruiz hits a three.") for _ in subset]

    out = titles.check(profile, [c] * 9, [_meta("Okafor's Dime!", "Okafor finds Ruiz.")] * 9, rewrite)
    assert [n for n, _rules in asked] == [8, 1]
    assert "- CLIP 0: " in asked[1][1] and "- CLIP 1: " not in asked[1][1]
    assert [m.title for m in out] == ["Ruiz Knocks It Down!"] * 9
    log = capsys.readouterr().out
    assert "Titles: 9 of 9 got their play wrong; 9 written again, 0 written from the scoreboard" in log
    assert f"written again: {c.start:.0f}-{c.end:.0f} s, " in log and "from the scoreboard:" not in log
    assert titles.check(profile, [c], out[:1], rewrite) == out[:1]
    assert "Titles: all 1 true to their play" in capsys.readouterr().out


def test_a_baskets_quarter_and_clock_are_read_where_the_bug_changed():
    # A highlights package cuts from the end of the 2nd quarter to the 3rd:
    # the crowd's roar from the last play of the half is still in the search.
    readings = [bb.Reading(t=float(t), score=(58, 55), teams=("SAS", "OKC"), period=2, clock=45.0 - (t - 400),
                           visible=True) for t in range(400, 444, 4)]
    readings += [bb.Reading(t=float(t), score=(58, 55) if t < 452 else (60, 55), teams=("SAS", "OKC"), period=3,
                            clock=606.0 - (t - 444), visible=True) for t in range(444, 480, 4)]
    board = bb.from_readings(readings, box=(0.0, 0.0, 0.3, 0.1))
    profile = _profile()
    m = _moments(profile, board=board, curves=_curves(roars=[(439, 3)]))
    e = next(e for e in m if e.confirmed)
    assert e.when.startswith("Q3 10:0"), e.when


class _Looks:
    """A local model that takes images, answering what a shot shows."""

    def __init__(self, shot):
        self.shot = shot

    def look(self, _prompt, images):
        assert images
        return json.dumps({"shot": self.shot})


@pytest.mark.parametrize("shot, kind", [("bench", "bench_reaction"), ("courtside", "courtside_reaction"),
                                        ("crowd", "crowd_reaction"), ("coach", "coach_reaction")])
def test_the_local_model_tells_who_a_reaction_shot_shows(shot, kind):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.basketball import look

    profile = _profile()
    clip = ClipCandidate(start=300.0, end=320.0, score=60,
                         subscores={"sport_event": "fan_reaction", "sport_label": "Fan reaction", "sport_t": 306.0})
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    assert look.look(profile, [clip], "game.mp4", _Looks(shot), grab=lambda _t: frame) == 1
    assert clip.subscores["sport_event"] == kind
    assert clip.subscores["sport_label"] == profile.event_label(kind)


def test_the_local_model_never_names_anyone():
    from sports.basketball import look

    assert "Do not say who" in look.PROMPT


# ---- framing ----------------------------------------------------------------------------


def _sample(t, balls=(), people=(), cut=False):
    return {"t": t, "cut": cut, "balls": list(balls), "people": list(people)}


def test_the_crop_follows_the_ball_and_the_players_around_it():
    pytest.importorskip("cv2")      # video/framing.py's HoldMove
    from sports.basketball import action

    # Mid-court, away from either rim.
    samples = [_sample(i / 5, balls=[(0.42 + i * 0.005, 0.6, 0.8)], people=[(0.44 + i * 0.005, 0.6, 0.05, 0.2)])
               for i in range(30)]
    path, led = action.plan(samples, 0.316)
    assert led["ball"] > 20 and abs(path[-1][1] - 0.57) < 0.08


def test_the_crop_leans_toward_the_rim_on_a_drive():
    pytest.importorskip("cv2")      # video/framing.py's HoldMove
    from sports.basketball import action

    drive = [_sample(i / 5, balls=[(min(0.9, 0.3 + i * 0.03), 0.5, 0.8)]) for i in range(30)]
    path, led = action.plan(drive, 0.316)
    assert led["rim"] > 0 and path[-1][1] >= 0.8          # as far right as the crop goes, the rim in it


def test_the_crop_moves_to_the_reaction_shot():
    pytest.importorskip("cv2")      # video/framing.py's HoldMove
    from sports.basketball import action

    court = [_sample(i / 5, balls=[(0.3, 0.6, 0.8)]) for i in range(10)]
    fans = [(0.1 + k * 0.05, 0.5, 0.05, 0.15) for k in range(7)] + [(0.8, 0.5, 0.2, 0.4)]
    cutaway = [_sample(2 + i / 5, people=fans, cut=(i == 0)) for i in range(10)]
    path, led = action.plan(court + cutaway, 0.316)
    assert led["close-up"] >= 9 and path[-1][1] > 0.7          # on the biggest reacting person


def test_without_the_ball_the_crop_follows_the_players_not_the_stands():
    pytest.importorskip("cv2")      # video/framing.py's HoldMove
    from sports.basketball import action

    players = [(0.75, 0.6, 0.06, 0.28), (0.8, 0.62, 0.06, 0.3), (0.85, 0.6, 0.05, 0.26)]
    stands = [(0.1 + k * 0.04, 0.2, 0.02, 0.08) for k in range(14)]
    path, led = action.plan([_sample(i / 5, people=players + stands) for i in range(15)], 0.316)
    assert led["players"] == 15 and path[-1][1] > 0.7       # at the free throw, not mid-court


def test_a_close_up_is_framed_on_the_player():
    pytest.importorskip("cv2")      # video/framing.py's HoldMove
    from sports.basketball import action

    path, led = action.plan([_sample(i / 5, people=[(0.25, 0.5, 0.3, 0.8)]) for i in range(10)], 0.316)
    assert led["close-up"] == 10 and path[-1][1] < 0.35


def test_a_ball_in_the_front_rows_or_at_a_players_feet_is_not_followed():
    from sports.basketball import action

    player = (0.5, 0.5, 0.06, 0.3)                           # from y 0.35 to 0.65
    balls = [(0.3, 0.4, 0.9), (0.6, 0.9, 0.9), (0.51, 0.64, 0.9), (0.51, 0.45, 0.9)]
    # Kept: the ball in the air and the one in the player's hands; dropped:
    # the front rows' and the shoe's.
    assert action.real_balls(balls, [player]) == [(0.3, 0.4, 0.9), (0.51, 0.45, 0.9)]


def test_a_pan_across_the_court_is_no_cut_but_another_camera_is():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from sports.basketball import action
    from video.framing import is_cut as pixels_changed
    from video.framing import small_gray

    rng = np.random.default_rng(1)
    # The court: the stands above a wood floor with players on it, the
    # camera whipping across it between two samples.
    court = np.zeros((360, 1920, 3), dtype=np.uint8)
    court[:, :] = (60, 120, 190)
    court[:150] = rng.integers(0, 255, (150, 1920, 3), dtype=np.uint8)
    for x in range(0, 1920, 160):
        court[180:330, x:x + 60] = (230, 230, 230) if (x // 160) % 2 else (20, 20, 120)
    before, after = court[:, :640].copy(), court[:, 80:720].copy()
    # Another camera: a fan in a dark top against a blue wall.
    fan = np.zeros((360, 640, 3), dtype=np.uint8)
    fan[:, :] = (150, 60, 20)
    fan[60:360, 200:440] = (30, 30, 30)

    def cut(a, b):
        return action.is_cut(small_gray(a), small_gray(b), action.colours(a), action.colours(b))

    assert pixels_changed(small_gray(before), small_gray(after))      # the shared test calls the pan a cut
    assert not cut(before, after) and not cut(before, before)
    assert cut(before, fan)


def test_the_framing_hook_reaches_the_basketball_follower(monkeypatch):
    from sports.basketball import action

    monkeypatch.setattr(action, "compute", lambda path, model_name, imgsz, hide_scoreboard: {
        "mode": "track", "path": [(0.0, 0.4)], "led": {"ball": 1}})
    assert sports.framing("basketball", "clip.mp4", {}) == {"mode": "track", "path": [(0.0, 0.4)]}


# ---- the TV scoreboard, left out of the crop --------------------------------------------


def _looks(np, panel=(200, 262), text=(220, 250), see_through=0.0, moving=True, top=False, n=14):
    """Gray 480x270 looks at a broadcast: a picture that moves between looks
    (or doesn't), and a score bug's graphic, rows `panel`, over it (along the
    top when `top`), with its text in rows `text`."""
    rng = np.random.default_rng(3)
    still = rng.integers(0, 255, (270, 480)).astype(np.float32)
    out = []
    for i in range(n):
        img = rng.integers(0, 255, (270, 480)).astype(np.float32) if moving else still.copy()
        bug = np.full((panel[1] - panel[0], 192), 30.0)
        bug[text[0] - panel[0]:text[1] - panel[0], 20:170] = 230.0 if i % 3 else 200.0     # the digits change
        rows = slice(270 - panel[1], 270 - panel[0]) if top else slice(*panel)
        img[rows, 144:336] = see_through * img[rows, 144:336] + (1 - see_through) * (bug[::-1] if top else bug)
        out.append(img.astype(np.uint8))
    return out


def test_the_scoreboard_s_graphic_is_found_past_its_text():
    np = pytest.importorskip("numpy")
    from sports.basketball import action

    box = (0.32, 220 / 270, 0.68, 250 / 270)
    # Its top edge, opaque or see-through, not its text's (which is 20 rows lower).
    assert abs(action.bug_edge(_looks(np), box) - 200 / 270) <= 1 / 270
    assert abs(action.bug_edge(_looks(np, see_through=0.4), box) - 200 / 270) <= 1 / 270
    # Along the top: its bottom edge.
    top = (0.32, 20 / 270, 0.68, 50 / 270)
    assert abs(action.bug_edge(_looks(np, top=True), top) - 70 / 270) <= 1 / 270
    # A picture that doesn't move tells nothing: half the text's height past it.
    assert action.bug_edge(_looks(np, moving=False), box) == pytest.approx(box[1] - 0.5 * (box[3] - box[1]))


def _bar_looks(np, agree=True, n=14):
    """Gray 480x270 looks at an NBA bar as a playoff game showed it: the picture
    (crowd and court, dark in some looks, bright in others) over rows 0-227,
    the bar's top row (228) half picture, a light border (229) and the dark
    bar with its text (rows 236-256) below. `agree`: the border is lighter
    than the picture above it in every look; else only in the dark ones, so
    only the step from the border to the bar agrees across them."""
    rng = np.random.default_rng(7)
    border = 230.0 if agree else 150.0
    out = []
    for i in range(n):
        level = 60.0 if i % 2 else (90.0 if agree else 200.0)
        img = level + rng.uniform(-40, 40, (270, 480))
        picture = img[228].copy()
        bar = np.full((42, 480), 30.0)
        bar[:2] = border
        bar[8:28, 160:320] = 230.0 if i % 3 else 200.0       # the digits change
        img[228:270] = bar
        img[228] = 0.5 * picture + 0.5 * border
        out.append(np.clip(img, 0, 255).astype(np.uint8))
    return out


def test_the_scoreboard_s_edge_is_found_where_its_text_s_box_starts_below_it():
    """On an NBA game the bar's top was at 0.844 of the height in every window, but
    in four of ten the bug's text came out with its top at 0.830 or 0.844, at
    or above the edge, and the search, which looked only above the text, cut
    22-24% where the bar is 16%."""
    np = pytest.importorskip("numpy")
    from sports.basketball import action

    edge = 228 / 270
    for top in (224 / 270, 0.844, 236 / 270):
        box = (0.33, top, 0.67, 256 / 270)
        assert abs(action.bug_edge(_bar_looks(np), box) - edge) <= 1 / 270, top
        # Only the step under the border agrees: the half-covered row and the
        # border above it still go.
        assert abs(action.bug_edge(_bar_looks(np, agree=False), box) - edge) <= 1 / 270, top


def test_each_keyframe_is_dated_by_its_own_time_not_the_next_ones():
    """ffprobe's listing of an open-GOP video's keyframes, decoded as the
    reader decodes them: once one came out of order, ffmpeg dated each
    picture by the next keyframe's packet (and the first came out third)."""
    from sports.basketball import keyframes

    listing = "\n".join([
        "pts_time=2.585917|best_effort_timestamp_time=2.585917",
        "pts_time=16.232883|best_effort_timestamp_time=16.232883",
        "pts_time=0.000000|best_effort_timestamp_time=17.667650",
        "pts_time=22.439083|best_effort_timestamp_time=22.422400",
        "pts_time=17.684333|best_effort_timestamp_time=25.475450",
        "pts_time=N/A|best_effort_timestamp_time=31.598233",
    ])
    ffmpeg = [2.58592, 16.2329, 17.6677, 22.4224, 25.4755, 31.5982]       # showinfo's 6 digits
    assert keyframes.own_times(ffmpeg, listing) == [2.586, 16.233, 0.0, 22.439, 17.684, 31.5982]
    # ffmpeg counts from the file's start time (here 1.5 s); ffprobe doesn't.
    later = "\n".join(f"pts_time={own + 1.5}|best_effort_timestamp_time={best + 1.5}"
                       for own, best in [(0.5, 0.5), (4.0, 2.0), (2.0, 4.0)])
    assert keyframes.own_times([0.5, 2.0, 4.0], later) == [0.5, 4.0, 2.0]
    # A listing that isn't the same pictures leaves ffmpeg's times as they were.
    assert keyframes.own_times(ffmpeg, listing.replace("25.475450", "27.0")) is ffmpeg
    assert keyframes.own_times(ffmpeg, "") is ffmpeg


def _court_looks(np, see_through=0.0, n=14):
    """Gray 480x270 looks at a wide shot: moving players over rows 0-160, a
    floor that hardly moves below them with a sideline across it (about rows
    212-217, as the camera tilts), and a score bug's graphic (rows 225-258,
    its text 232-252)."""
    rng = np.random.default_rng(5)
    out = []
    for i in range(n):
        img = rng.integers(0, 255, (270, 480)).astype(np.float32)
        img[160:225] = 140.0 + rng.uniform(-4, 4, (65, 480))
        line = 212 + i % 5
        img[line:line + 2] = 220.0
        bug = np.full((33, 192), 30.0)
        bug[7:27, 20:170] = 230.0 if i % 3 else 200.0
        img[225:258, 144:336] = see_through * img[225:258, 144:336] + (1 - see_through) * bug
        out.append(img.astype(np.uint8))
    return out


def test_the_scoreboard_is_left_out_as_far_as_its_edge_not_the_still_floor_above_it(monkeypatch):
    """On an NBA game everything still past the bug's text was left out,
    the floor too: 19-27% of the height where the bug was 17%, and the
    nearest players cut at the knees."""
    np = pytest.importorskip("numpy")
    from sports.basketball import action

    box = (0.32, 232 / 270, 0.68, 252 / 270)
    for see_through in (0.0, 0.4):
        assert abs(action.bug_edge(_court_looks(np, see_through), box) - 225 / 270) <= 1 / 270
    looks = _hide(monkeypatch, np, box, _court_looks(np))
    top, bottom = action.hidden_rows(looks, 30.0, [(t / 5, 0.5) for t in range(50)], 0.316)
    assert top == 0.0 and abs(bottom - (225 / 270 - action.BUG_SLACK)) <= 1 / 270


def _hide(monkeypatch, np, box, frames=None):
    pytest.importorskip("cv2")
    from analysis import game_text
    from sports.basketball import scoreboard

    monkeypatch.setattr(game_text, "available", lambda: True)
    monkeypatch.setattr(scoreboard, "find_text", lambda grab, duration, ocr: box and (box, box))
    grays = frames or _looks(np)
    return {i * 2.0: np.repeat(g[:, :, None], 3, axis=2) for i, g in enumerate(grays)}


def test_the_crop_leaves_the_scoreboard_out_when_it_would_cut_it(monkeypatch):
    np = pytest.importorskip("numpy")
    from sports.basketball import action

    box = (0.32, 220 / 270, 0.68, 250 / 270)
    looks = _hide(monkeypatch, np, box)
    middle = [(t / 5, 0.5) for t in range(50)]
    top, bottom = action.hidden_rows(looks, 30.0, middle, 0.316)
    assert top == 0.0 and 200 / 270 - action.BUG_SLACK - 1 / 270 <= bottom <= 200 / 270 - action.BUG_SLACK + 1 / 270
    # A crop that stays far from it shows none of it, so nothing is left out...
    assert action.hidden_rows(looks, 30.0, [(t / 5, 0.12) for t in range(50)], 0.2) is None
    # ...nor when no scoreboard is found (gym or phone footage).
    _hide(monkeypatch, np, None)
    assert action.hidden_rows(looks, 30.0, middle, 0.316) is None


def test_a_scoreboard_too_tall_to_leave_out_is_left_in(monkeypatch):
    np = pytest.importorskip("numpy")
    from sports.basketball import action

    box = (0.32, 196 / 270, 0.68, 250 / 270)
    looks = _hide(monkeypatch, np, box, _looks(np, panel=(180, 262), text=(196, 250)))   # a third of the height
    assert action.hidden_rows(looks, 30.0, [(t / 5, 0.5) for t in range(50)], 0.316) is None


def test_with_the_scoreboard_left_out_the_crop_is_narrower_and_says_so(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import sports.soccer.ball as ball
    from sports.basketball import action

    clip = tmp_path / "clip.mp4"
    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (480, 270))
    for frame in _looks(np, n=40):
        out.write(np.repeat(frame[:, :, None], 3, axis=2))
    out.release()
    monkeypatch.setattr(ball, "_model", lambda name: None)
    monkeypatch.setattr(ball, "detect", lambda model, frame, imgsz: ([], []))
    seen = {}

    def rows(looks, duration, path, crop_frac):
        seen.update(looks=len(looks), duration=duration, crop_frac=crop_frac)
        return (0.0, 0.75)

    monkeypatch.setattr(action, "hidden_rows", rows)
    tracking = action.compute(clip)
    assert tracking["rows"] == (0.0, 0.75) and seen["looks"] == 14 and seen["duration"] == pytest.approx(4.0)
    # 9:16 of three quarters of the height: the crop's path keeps inside a narrower crop.
    assert seen["crop_frac"] == pytest.approx(270 * 9 / 16 / 480)
    assert all(0.75 * seen["crop_frac"] / 2 - 1e-6 <= x for _, x in tracking["path"])
    assert "rows" not in action.compute(clip, hide_scoreboard=False)


def test_the_vertical_crop_keeps_only_the_rows_it_is_given(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import video.cropper as cropper

    clip = tmp_path / "clip.mp4"
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :] = (np.arange(360) // 2).astype(np.uint8)[:, None, None]      # each row its own shade
    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    for _ in range(3):
        out.write(frame)
    out.release()
    piped: dict = {}

    def run(cmd, ass_path, produce):
        chunks = []
        produce(chunks.append)
        piped.update(cmd=cmd, frames=chunks)

    monkeypatch.setattr(cropper, "_run_ffmpeg_piped", run)
    path = [(0.0, 0.5)]
    cropper.render_vertical(clip, {"mode": "track", "path": path}, tmp_path / "all.mp4")
    assert piped["cmd"][piped["cmd"].index("-s") + 1] == "202x360"           # every row, as always
    assert len(piped["frames"][0]) == 202 * 360 * 3
    cropper.render_vertical(clip, {"mode": "track", "path": path, "rows": (0.0, 0.75)}, tmp_path / "top.mp4")
    assert piped["cmd"][piped["cmd"].index("-s") + 1] == "150x270"            # the top three quarters, 9:16
    kept = np.frombuffer(piped["frames"][0], np.uint8).reshape(270, 150, 3)
    assert abs(int(kept[-1, 75, 0]) - 269 // 2) <= 6                          # down to row 269, no further


# ---- the pipeline: vertical sources, Vertical Live, rendering --------------------------


class _Stop(Exception):
    pass


def test_a_game_filmed_9x16_keeps_its_composition(monkeypatch, tmp_path, db):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import core.pipeline as pipeline
    from core import modes
    from core.models import DownloadedVideo

    source = tmp_path / "phone.mp4"
    source.write_bytes(b"not really a video")
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: DownloadedVideo(
        video_id="local_phone", title="Phone", path=source, duration=600.0))
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr("analysis.audio_features.extract_audio_features", lambda _p: {})
    monkeypatch.setattr("analysis.visual_features.extract_visual_features", lambda _p: {})
    monkeypatch.setattr("analysis.hype.audience_signals", lambda *_a, **_k: (None, None))
    seen: dict = {}

    class Reading:
        def __init__(self, config, video):
            seen["clips"] = config["clips"]
            raise _Stop

    monkeypatch.setattr(pipeline, "MatchReading", Reading)
    config = {"clips": {"captions": False, "sport": {"name": "basketball"}}, "paths": {"data_dir": str(tmp_path)}}
    for size, kept in (((1080, 1920), True), ((720, 1280), True), ((1440, 2560), True), ((1920, 1080), False)):
        monkeypatch.setattr(modes, "probe_size", lambda _p, s=size: s)
        with pytest.raises(_Stop):
            pipeline.process_video("local:phone", config, db, force=True)
        assert bool(seen["clips"].get("vertical_live")) is kept, size


def test_vertical_live_basketball_is_scored_as_basketball():
    from core import modes

    config = {"clips": {"vertical_live": True, "sport": {"name": "basketball", "highlights": "dunks"}}}
    assert modes.sport(config) == "basketball" and modes.is_vertical_live(config)
    assert type(sports.profile_for(config)).__name__ == "BasketballProfile"


def test_a_16x9_game_is_framed_by_the_play_not_a_face(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from pathlib import Path

    import core.pipeline as pipeline
    import video.cropper as cropper
    import video.tracker as tracker
    from core import modes

    rendered: dict = {}

    def render(_clip, tracking, output, *_a, **_k):
        rendered["tracking"] = tracking
        Path(output).write_bytes(b"clip")
        return Path(output)

    def no_faces(*_a, **_k):
        raise AssertionError("face tracking ran")

    monkeypatch.setattr(pipeline, "cut_clip", lambda _s, _c, output, **_k: Path(output).write_bytes(b"cut"))
    monkeypatch.setattr(modes, "probe_size", lambda _p: (1920, 1080))
    monkeypatch.setattr(sports, "framing", lambda name, path, config: {"mode": "track", "path": [(0.0, 0.7)]})
    monkeypatch.setattr(cropper, "render_vertical", render)
    monkeypatch.setattr(tracker, "compute_tracking", no_faces)
    config = {"clips": {"captions": False, "outro": False, "vertical": True, "sport": {"name": "basketball"}},
              "paths": {"data_dir": str(tmp_path)}, "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8}}
    final, opts_json = pipeline._render_files(tmp_path / "source.mp4", ClipCandidate(start=10.0, end=40.0, score=80),
                                              [], tmp_path / "clips", config)
    assert final.exists() and rendered["tracking"]["path"] == [(0.0, 0.7)]
    assert json.loads(opts_json)["sport"] == "basketball"


def test_a_basketball_clip_card_carries_the_clock_and_the_situation():
    profile = _profile()
    m = _moments(profile, said={500: "for the win! from downtown"},
                 board=_board([(504, 0, 3)], period=4, start_clock=508.0, start=(99, 100)),
                 curves=_curves(roars=[(501, 6)]))
    e = next(e for e in m if e.confirmed)
    c = ClipCandidate(start=e.start, end=e.end, score=70)
    clips.mark(c, e, profile.event_label(e.type), clips.bonus(e, profile))
    assert c.subscores["sport_event"] == "game_winner"
    assert c.subscores["sport_when"].startswith("Q4") and c.subscores["sport_context"]


def test_the_quarter_choice_keeps_its_quarter():
    profile = _profile("best", "q4")
    board = _board([(304, 0, 2)], period=3)
    m = _moments(profile, said={300: "what a dunk"}, board=board, curves=_curves(roars=[(301, 6)]))
    candidate = ClipCandidate(start=290.0, end=320.0, score=60)
    attached = clips.attach(m, [candidate])
    kept, dropped, _notes = clips.choose(profile, [candidate], attached, min_score=40, max_len=60)
    assert not kept and dropped[0][1] == "other_period"


def test_typed_events_use_basketballs_words():
    from sports.core import events_import

    read, unread = events_import.parse("1:23:14 dunk LeBron\n45:02 3pt Curry\n10:00 FT\n3 pointer",
                                       sports.spec("basketball"))
    assert [(e.kind, e.who, e.video_t) for e in read] == [
        ("dunk", "LeBron", 5_000 - 6), ("made_3", "Curry", 2702.0), ("free_throw", "", 600.0)]
    assert unread == ["3 pointer"]                      # "3" is no match minute in basketball


# ---- soccer is unchanged -------------------------------------------------------------------


def test_soccer_keeps_its_own_values():
    soccer = sports.profile_for({"clips": {"sport": {"name": "soccer"}}})
    assert soccer.scoring_types == detect.GOALS and soccer.celebration == detect.CELEBRATION
    assert soccer.sound_curves() == ("crowd", "whistle")
    assert type(soccer).__name__ == "SoccerProfile"
    e = detect.SportEvent("goal", 100.0, 1.0, 100)
    assert soccer.context_weight(e) == 1.0
    assert clips.bonus(e, soccer) == clips.bonus(e) == clips.BONUS_MAX
    assert soccer.extra_moments([e], [], curves={}, video_end=200, min_len=10, max_len=60) == [e]


# ---- through the scorer (analysis/fusion.py), as a real job runs it ----------------------


class _Says:
    def generate(self, *_a, **_k):
        return "{}"


def test_a_dunk_and_its_reaction_become_one_marked_clip_through_the_scorer(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from analysis import fusion, highlights

    def score_windows(_segments, _llm, windows, **_k):
        return [ClipCandidate(start=a, end=b, score=55, hook="w", source="signal") for a, b in windows]

    picks = [(0, 30, 62), (100, 130, 64), (200, 230, 61)]
    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=a, end=b, score=s, hook="h", reason="r") for a, b, s in picks], []))
    monkeypatch.setattr(highlights, "score_windows", score_windows)
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)
    config = {
        "clips": {"min_duration": 10, "max_duration": 60, "min_score": 40, "max_clips_per_video": 0,
                  "sport": {"name": "basketball", "highlights": "best"}},
        "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30, "long_video_threshold_seconds": 3600,
                     "max_overlap": 0.3, "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
        "scoring": {"rerank_pool": 0, "read_screen": False},
        "tracking": {"detector": "yolov8n.pt"},
    }
    profile = sports.profile_for(config)
    profile.board = _board([(454, 0, 2)], period=4, start_clock=900.0, start=(80, 82))
    profile.cutaways = [reactions.Cutaway(456.0, 462.0, crowd=True)]
    profile.curves = _curves(roars=[(451, 8)])
    segs = _segments({448: "he throws it down! what a dunk"})
    kept, _rejected = fusion.find_clips("game.mp4", segs, _Says(), config,
                                        signals=({"spike": np.zeros(N)}, {"motion": np.zeros(N)}),
                                        measure_reaction=False, sport=profile)
    dunk = [c for c in kept if (c.subscores or {}).get("sport_event") == "dunk"]
    assert dunk and dunk[0].subscores["sport_bonus"] > 0
    # The build-up, the dunk, and the reaction's first seconds (reactions.REACTION_MOST).
    assert dunk[0].start <= 448 and dunk[0].end >= 456 + reactions.REACTION_MOST
    assert dunk[0].subscores["sport_when"].startswith("Q4")
    assert profile.report_data["sport"] == "Basketball" and profile.report_data["found"]["Dunk"] == 1


def test_the_final_dunk_after_a_layup_gets_a_clip_of_its_own_through_the_scorer(monkeypatch):
    """Game 7's ending: one 23-second sentence over the Thunder's layup and the
    Spurs' final dunk 8 s later. Its candidate showed the layup, the dunk was
    typed a basket with no window of its own, and the game's best moment had
    no clip. Now the dunk is typed by its call, gets its own window, and runs
    on into the celebration."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from analysis import fusion, highlights

    words = ("and remember no timeouts for the Thunder eight point Spurs lead Wallace attacking to the basket "
             "laid it in six point game Champagnie the rebound eight seconds left Fox has it ahead to Vassell "
             "who goes in for the dunk and the Spurs have done it").split()
    sentence = _said_at(827.2, " ".join(words), 0.35)                   # "laid" at 833, "dunk" at 841
    assert [round(sentence.words[words.index(w)]["start"]) for w in ("laid", "dunk")] == [833, 841]
    segs = sorted([sentence] + [s for s in _segments({}) if s.end <= sentence.start or s.start >= sentence.end],
                  key=lambda s: s.start)

    def score_windows(_segments, _llm, windows, **_k):
        return [ClipCandidate(start=a, end=b, score=55, hook="w", source="signal") for a, b in windows]

    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=827.2, end=847.0, score=80, hook="h", reason="r")], []))
    monkeypatch.setattr(highlights, "score_windows", score_windows)
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)
    config = {
        "clips": {"min_duration": 10, "max_duration": 60, "min_score": 40, "max_clips_per_video": 0,
                  "sport": {"name": "basketball", "highlights": "best"}},
        "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30, "long_video_threshold_seconds": 3600,
                     "max_overlap": 0.3, "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
        "scoring": {"rerank_pool": 0, "read_screen": False},
        "tracking": {"detector": "yolov8n.pt"},
    }
    profile = sports.profile_for(config)
    profile.board = _board([(834, 1, 2), (844, 0, 2)], period=4, start_clock=850.0, teams=("SAS", "OKC"),
                           start=(109, 101))
    profile.cutaways, profile.curves = [], _curves()
    kept, _rejected = fusion.find_clips("game.mp4", segs, _Says(), config,
                                        signals=({"spike": np.zeros(N)}, {"motion": np.zeros(N)}),
                                        measure_reaction=False, sport=profile)
    shown = {c.subscores.get("sport_event"): c for c in kept if (c.subscores or {}).get("sport_t")}
    assert "dunk" in shown
    dunk = shown["dunk"]
    assert dunk.start <= 841 - 7 and dunk.end >= 841 + 18                # the play, then the celebration


def test_the_winning_basket_after_the_tying_three_keeps_its_clip_through_the_scorer(monkeypatch):
    """A 79-minute NBA game's ending: a three tied it with 6 seconds left and
    the winning basket came at 0:00, 20 s later in the video, one sentence
    of commentary running over both. The tying three's clip was kept first,
    and the winner's was left out as a repeat of it for sharing that
    sentence. Two plays are two clips, however much they share."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from analysis import fusion, highlights

    tying = _said_at(812.0, "Curry for three to tie it, got it! Tie game, six seconds left, Lakers ball, Reaves "
                            "drives, lays it in and the Lakers win it", 1.56)
    after = _said_at(851.0, "Austin Reaves at the buzzer, what a finish on Christmas Day", 1.9)
    segs = sorted([tying, after] + [s for s in _segments({}) if s.end <= 812 or s.start >= 870],
                  key=lambda s: s.start)

    def score_windows(_segments, _llm, windows, **_k):
        return [ClipCandidate(start=a, end=b, score=55, hook="w", source="signal") for a, b in windows]

    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=806.0, end=828.0, score=95, hook="h", reason="r")], []))
    monkeypatch.setattr(highlights, "score_windows", score_windows)
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)
    config = {
        "clips": {"min_duration": 10, "max_duration": 60, "min_score": 40, "max_clips_per_video": 0,
                  "sport": {"name": "basketball", "highlights": "best"}},
        "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30, "long_video_threshold_seconds": 3600,
                     "max_overlap": 0.4, "max_text_similarity": 0.7, "max_segment_reuse": 0.4},
        "scoring": {"rerank_pool": 0, "read_screen": False},
        "tracking": {"detector": "yolov8n.pt"},
    }
    profile = sports.profile_for(config)
    profile.board = _board([(824, 1, 3), (844, 0, 2)], period=4, start_clock=830.0, teams=("LAL", "GS"),
                           start=(113, 110))
    profile.cutaways, profile.curves = [], _curves()
    kept, rejected = fusion.find_clips("game.mp4", segs, _Says(), config,
                                       signals=({"spike": np.zeros(N)}, {"motion": np.zeros(N)}),
                                       measure_reaction=False, sport=profile)
    shown = {c.subscores.get("sport_event") for c in kept if (c.subscores or {}).get("sport_t")}
    assert {"game_tying", "game_winner"} <= shown, [(r.candidate.start, r.reason) for r in rejected]


# ---- names: what Whisper listens for ------------------------------------------------


NBA_DESCRIPTION = """Victor Wembanyama (35 PTS, 12 REB) and the San Antonio Spurs defeated Shai Gilgeous-Alexander
(31 PTS) and the Oklahoma City Thunder, 111-103, in Game 7 of the Western Conference Finals. Julian Champagnie
added 18 PTS.

Subscribe to the NBA: https://www.youtube.com/nba?sub_confirmation=1
For news, stories, highlights and more, go to our official website at https://www.nba.com
Get NBA League Pass: https://www.nba.com/watch/league-pass-stream"""


def test_whisper_listens_for_the_names_the_videos_own_title_and_description_spell():
    from core.models import DownloadedVideo
    from sports.basketball import names

    video = DownloadedVideo(video_id="x", title="SPURS at THUNDER | FULL GAME 7 HIGHLIGHTS | May 28, 2026",
                            path=None, duration=900.0, description=NBA_DESCRIPTION)
    hint = ("Spurs, Thunder, Victor Wembanyama, San Antonio Spurs, Shai Gilgeous-Alexander, Oklahoma City Thunder, "
            "Julian Champagnie")
    assert sports.hotwords({"clips": {"sport": {"name": "basketball"}}}, video) == hint
    # The teams the job names count too, once; soccer, and a job with no sport, keep Whisper as it was.
    assert names.for_video("Highlights", "", "Spurs, De'Aaron Fox") == "Spurs, De'Aaron Fox"
    assert names.for_video("FULL GAME HIGHLIGHTS", "Subscribe to the NBA") is None
    assert sports.hotwords({"clips": {"sport": {"name": "soccer"}}}, video) is None
    assert sports.hotwords({"clips": {}}, video) is None


def test_a_reused_download_still_gives_whisper_the_names_in_its_description(monkeypatch):
    """On the PC the game was already on disk, and the job listened for "Spurs,
    Thunder" alone: a reused download comes back without its description."""
    pytest.importorskip("numpy")    # core/pipeline.py imports analysis/fusion.py
    from core import pipeline
    from core.models import DownloadedVideo
    from sources import dispatch

    asked = []
    monkeypatch.setattr(dispatch, "description", lambda url: asked.append(url) or NBA_DESCRIPTION)
    url = "https://www.youtube.com/watch?v=1bOMYQFgK4I"

    def video(description=""):
        return DownloadedVideo(video_id="1bOMYQFgK4I", title="SPURS at THUNDER | FULL GAME 7 HIGHLIGHTS",
                               path=None, duration=955.4, description=description)

    basketball = {"clips": {"sport": {"name": "basketball"}}}
    reused = video()
    assert "Victor Wembanyama" in pipeline._listening_for(basketball, reused, url)
    assert reused.description == NBA_DESCRIPTION and asked == [url]
    # Downloaded just now, it has its own; soccer reads none, and asks nothing.
    assert "Victor Wembanyama" in pipeline._listening_for(basketball, video(NBA_DESCRIPTION), url)
    assert pipeline._listening_for({"clips": {"sport": {"name": "soccer"}}}, video(), url) is None
    assert asked == [url]


def test_whisper_is_given_the_names_only_when_there_are_some(tmp_path):
    from unittest.mock import patch

    from transcription import transcriber

    calls = []

    class Model:
        def transcribe(self, *a, **k):
            calls.append(k)

            class Info:
                duration, language = 10.0, "en"
            return iter(()), Info()

    with patch.object(transcriber, "_load_model", return_value=Model()):
        transcriber.transcribe(tmp_path / "v.mp4", "a", tmp_path, model_size="small", device="cpu",
                               hotwords="Victor Wembanyama")
        transcriber.transcribe(tmp_path / "v.mp4", "b", tmp_path, model_size="small", device="cpu")
    assert calls[0]["hotwords"] == "Victor Wembanyama" and "hotwords" not in calls[1]
