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
    assert ("made_2", 198) in named and ("made_3", 498) in named
    assert all(e.confirmed and e.team for e in m if e.type in ("made_2", "made_3"))


def test_a_free_throw_is_one_point():
    m = _moments(_profile(), board=_board([(300, 0, 1)]))
    assert [e.type for e in m if e.confirmed] == ["free_throw"]


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
    assert "takes the lead" in winner.context and winner.when.startswith("Q4 0:0")


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
    assert tie.type == "game_tying" and "ties it" in tie.context
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


def test_a_basket_is_dated_by_the_roar_just_before_its_score_not_an_earlier_play():
    # A highlights package: a big play's roar at 478, then a three at 502
    # whose crowd is quieter. The score shows at the 504 reading, the old one
    # last read at 500. Searching 25 s back took the earlier play's roar.
    profile = _profile()
    m = _moments(profile, board=_board([(502, 0, 3)]), curves=_curves(roars=[(478, 8), (503, 3)]))
    three = next(e for e in m if e.confirmed)
    assert 496 <= three.t <= 503 and three.start <= 502 <= three.end


def test_a_basket_nothing_heard_is_dated_when_the_old_score_was_last_read():
    profile = _profile()
    m = _moments(profile, board=_board([(702, 1, 2)]))                 # no crowd, no commentary
    basket = next(e for e in m if e.confirmed)
    assert basket.t == 700.0 and basket.start <= 700 <= basket.end    # not half a minute before


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
        "sport_label": "Three", "sport_team": "SAS", "sport_when": "Q3 5:12",
        "sport_why": "score 60-55 (SAS), +3; crowd roar"})
    winner = ClipCandidate(start=200, end=228, score=90, subscores={
        "sport_label": "Game winner", "sport_team": "OKC", "sport_when": "Q4 0:03",
        "sport_why": "score 111-110 (OKC), +2", "sport_context": "takes the lead"})
    goal = ClipCandidate(start=300, end=320, score=80, subscores={"sport_label": "Goal", "sport_minute": 67})
    metadata.generate_metadata_batch([three, winner, goal], _segments({}), "Spurs at Thunder", Model())
    assert ("CLIP 0 (the scoreboard: Three by SAS, making it 60-55; 3rd quarter with 5:12 left; "
            "not crunch time):") in prompts[0]
    assert ("CLIP 1 (the scoreboard: Game winner by OKC, making it 111-110; 4th quarter with 0:03 left; "
            "takes the lead):") in prompts[0]
    assert "CLIP 2:\n" in prompts[0]                    # a soccer clip's block, as it always was


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
    assert dunk[0].start <= 448 and dunk[0].end >= 462              # the build-up, the dunk, the reaction
    assert dunk[0].subscores["sport_when"].startswith("Q4")
    assert profile.report_data["sport"] == "Basketball" and profile.report_data["found"]["Dunk"] == 1
