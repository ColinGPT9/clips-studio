"""Where a basketball clip starts (sports/basketball/leadin.py): the job's
clips moved off a TV cutaway onto the court, once they're chosen. The look
at the footage is stubbed with shots made by hand, so all but the last test
need nothing heavier than pytest. Times are the video's; no real names."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("yaml")

import sports
import sports.soccer.ball as ball
from core import cancel
from core.models import ClipCandidate
from sports.basketball import leadin
from sports.basketball.crop import observe, shots
from sports.basketball.crop.model import Shot

FPS = 30.0
FRAME = 1 / FPS
GUARD = shots.CUT_GUARD      # how far a start or an end stays from a cut
CONFIG = {"clips": {"min_duration": 10, "max_duration": 60}}


def _cut(*spec) -> list[Shot]:
    """Shots by hand on the video's clock: (t0, t1, kind) or (t0, t1, kind, replay)."""
    return [Shot(id=i, f0=round(t0 * FPS), f1=round(t1 * FPS), t0=t0, t1=t1, kind=kind,
                 replay=bool(rest and rest[0]))
            for i, (t0, t1, kind, *rest) in enumerate(spec)]


def _clip(start: float, end: float, t: float | None, kind: str = "made_3", **more) -> ClipCandidate:
    scores = {"sport_event": kind, **more}
    if t is not None:
        scores["sport_t"] = t
    return ClipCandidate(start=start, end=end, score=50.0, hook="", subscores=scores)


@pytest.fixture
def look(monkeypatch):
    """leadin's look at the video, stubbed: each clip start gets the shots
    put in `look.shots` for it, and every look is recorded in `look.seen`."""
    state = SimpleNamespace(seen=[], shots={}, duration=600.0, fail=set())
    monkeypatch.setattr(observe, "probe", lambda path: {"fps": FPS, "duration": state.duration})
    monkeypatch.setattr(ball, "_model", lambda name: SimpleNamespace(weights=name))

    def probe(path, t0, t1, fps, model=None, bug_box=None, scale=1.0, camera=None):
        state.seen.append(SimpleNamespace(t0=t0, t1=t1, fps=fps, model=model, bug_box=bug_box, scale=scale))
        if t0 in state.fail:
            raise RuntimeError("the decode failed")
        return state.shots.get(t0, [])

    monkeypatch.setattr(leadin, "probe", probe)
    # The clock on the bug, by time (none read unless a test puts it there).
    state.clock = {}
    monkeypatch.setattr(leadin, "_clock_at", lambda path, t, box: state.clock.get(round(t, 2)))
    return state


def test_a_clip_opening_on_a_closeup_moves_to_the_courts_first_frame(look):
    look.shots[100.0] = _cut((100, 103, "closeup"), (103, 116, "wide"))
    clip = _clip(100.0, 116.0, 111.0)
    out = leadin.refine_windows([clip], "game.mp4", [], CONFIG, [])
    assert out == [clip]
    # Just past the court's first frame (CUT_GUARD), up to the cutter's hundredths. The
    # court runs on after the play with no cut: it's over 2 s after, and nothing of the
    # next possession is added to reach 10 s.
    assert (clip.start, clip.end) == (103.05, 113.0)
    # It looked at the whole clip and on past its end, as far as its end may move and the
    # render's pad past that, at the video's frame rate.
    assert [(s.t0, s.t1, s.fps) for s in look.seen] == [(100.0, 116.0 + leadin.LOOK_PAST + leadin.PAD_LOOK, FPS)]


def test_plays_that_are_reactions_replays_or_outside_the_clip_keep_their_windows(look):
    for t0 in (100.0, 200.0, 300.0, 400.0, 520.0):
        look.shots[t0] = _cut((t0, t0 + 3, "closeup"), (t0 + 3, t0 + 16, "wide"))
    clips = [
        _clip(100.0, 116.0, 111.0, "crowd_reaction"),        # a reaction is the cutaway itself
        _clip(200.0, 216.0, 211.0, "big_moment"),            # nobody knows what the play was
        _clip(300.0, 316.0, 311.0, "made_3", sport_replay=True),
        _clip(400.0, 416.0, None),                           # no play on it at all
        _clip(520.0, 536.0, 537.5),                          # the play is after its end
    ]
    found = [(c.start, c.end) for c in clips]
    leadin.refine_windows(clips, "game.mp4", [], CONFIG, [])
    assert [(c.start, c.end) for c in clips] == found
    # Only their last second and the render's pad are looked at, for where the next shot starts.
    assert [(s.t0, s.t1) for s in look.seen] == [(c.end - 1.0, c.end + leadin.PAD_LOOK) for c in clips]


def test_a_play_that_opens_its_clip_keeps_its_start_but_ends_when_the_play_is_done(look):
    # The basket at 501 s: no time before it to trim, but the broadcast cuts back to the
    # court at 506 s, the next possession. Nothing of that is shown, even to reach 10 s:
    # the clip stays short rather than show the next scene.
    look.shots[500.0] = _cut((500, 503, "wide"), (503, 506, "closeup"), (506, 516, "wide"))
    said = [SimpleNamespace(start=506.5, end=509.0, words=[{"start": 506.5, "end": 507.0, "word": " Shooter"},
                                                           {"start": 507.1, "end": 509.0, "word": " dribbles."}])]
    for words, cfg in ((said, CONFIG), ([], CONFIG), ([], {"clips": {}})):
        clip = _clip(500.0, 516.0, 501.0)
        leadin.refine_windows([clip], "game.mp4", words, cfg, [])
        assert (clip.start, clip.end) == (500.0, 505.95)       # just before the cut, down to hundredths


def test_the_basket_that_ends_the_game_and_a_game_winner_keep_their_ends(look):
    look.shots[500.0] = _cut((500, 503, "wide"), (503, 506, "closeup"), (506, 516, "wide"))
    last = _clip(500.0, 516.0, 501.0)
    leadin.refine_windows([last], "game.mp4", [], {"clips": {}}, [480.0, 501.0], final=[501.0])
    winner = _clip(500.0, 516.0, 501.0, "game_winner")
    leadin.refine_windows([winner], "game.mp4", [], {"clips": {}}, [])
    assert last.end == winner.end == 516.0
    # The last basket read, with the game going on after it: back on the court is the
    # next possession, as after any other.
    going_on = _clip(500.0, 516.0, 501.0)
    leadin.refine_windows([going_on], "game.mp4", [], {"clips": {}}, [480.0, 501.0])
    assert going_on.end == 505.95

def _end(shots_, t, end, said, basket=True):
    return shots.clip_end(shots_, t, end, said, basket=basket, fps=FPS)


def test_the_play_is_over_at_the_cut_back_to_the_court_a_replay_or_the_camera_moving_on():
    # Back to the court after the reaction.
    assert shots.play_over(_cut((0, 3, "wide"), (3, 5, "closeup"), (5, 15, "wide")), 1.0, 15.0) == (
        5, "back to the court after the reaction")
    # A replay starting; a graphic.
    assert shots.play_over(_cut((0, 4, "wide"), (4, 9, "wide", True), (9, 15, "wide")), 1.0, 15.0)[0] == 4
    assert shots.play_over(_cut((0, 4, "wide"), (4, 9, "graphic")), 1.0, 15.0)[0] == 4
    # A cut to another wide court shot 1.5 s or more after a basket (on the NBA compilation
    # the next possession came 1.97-2.8 s after the bug's time); not 1.3 s after; never
    # after a block.
    court = _cut((0, 4.5, "wide"), (4.5, 15, "wide"))
    assert shots.play_over(court, 2.4, 15.0)[0] == 4.5
    assert shots.play_over(court, 3.2, 15.0) == (15.0, "")
    assert shots.play_over(court, 1.0, 15.0, basket=False) == (15.0, "")
    # Nothing within 1.5 s of the play counts: the reaction close-up right after it stays.
    assert shots.play_over(_cut((0, 1.5, "closeup"), (1.5, 15, "wide")), 0.5, 15.0) == (15.0, "")


def test_the_cut_after_the_reaction_ends_the_clip_with_all_he_said_before_it():
    # The clutch shot on the NBA compilation (times from the play): "Shot's good!" at +0.1,
    # "He had missed 9 of 10 shots and it's back up to 7." from +1.8, the next possession
    # on the court at +4.1, "Double-team on the..." from +6.2; the clip ran to +9.1. It
    # runs to the cut, his line cut there, never showing the next possession (backing off
    # to his breath before it made the owner's clips too short).
    t = 100.0
    cuts = _cut((90, t + 1.4, "wide"), (t + 1.4, t + 4.1, "closeup"), (t + 4.1, t + 12, "wide"))
    said = [(t + 0.1, t + 1.0), (t + 1.8, t + 5.0), (t + 6.2, t + 8.0)]
    end, why = shots.clip_end(cuts, t, t + 9.1, said, fps=FPS, breath=[t + 3.6])
    assert end == pytest.approx(t + 4.1 - GUARD) and why == "back to the court after the reaction"
    # The buzzer-beater: "At the buzzer... with a big bucket." from +1.5, over before the cut to
    # another game at +4.8, then "Thank you and..." from +5.0; the clip ran to +12.2.
    cuts = _cut((90, t + 2.2, "wide"), (t + 2.2, t + 4.8, "closeup"), (t + 4.8, t + 14, "wide"))
    said = [(t + 1.5, t + 4.3), (t + 5.0, t + 9.0)]
    end, why = _end(cuts, t, t + 12.2, said)
    assert end == pytest.approx(t + 4.8 - GUARD) and why == "back to the court after the reaction"


def test_everything_he_says_before_the_cut_stays():
    t = 100.0
    cuts = _cut((90, t + 2.0, "wide"), (t + 2.0, t + 6.0, "closeup"), (t + 6.0, t + 20, "wide"))
    # The sentence he began with the play, finished in the reaction shot.
    assert _end(cuts, t, t + 15, [(t + 1.0, t + 4.5)])[0] == pytest.approx(t + 6.0 - GUARD)
    # One running past the cut is cut there: the next scene is never shown.
    assert _end(cuts, t, t + 15, [(t + 1.0, t + 9.0)])[0] == pytest.approx(t + 6.0 - GUARD)
    # One he began 4 s after the play over the reaction ("We're tied again.", "It's back to
    # three."): about the play, kept to the cut.
    assert _end(cuts, t, t + 15, [(t + 1.0, t + 3.5), (t + 4.0, t + 5.5)])[0] == pytest.approx(t + 6.0 - GUARD)
    # One the cut would leave under half said ("There is|"): left out, the clip ending before it.
    end, why = _end(cuts, t, t + 15, [(t + 1.0, t + 3.5), (t + 5.5, t + 8.0)])
    assert end == pytest.approx(t + 5.45) and why.endswith("half said")


def test_in_one_court_shot_he_says_what_he_has_to_about_the_play_then_it_ends():
    t, court = 100.0, _cut((90, 120, "wide"))
    # One wide shot, the next possession coming up the floor: the sentence he began with the
    # play is finished; one he begins on the court more than 5.5 s after is his next thought.
    said = [(t + 0.2, t + 3.0), (t + 6.0, t + 9.0)]
    end, why = shots.clip_end(court, t, t + 7.0, said, fps=FPS, look=t + 11.0)
    assert end == pytest.approx(t + 3.0 + shots.BREATH) and "next thing" in why
    # A follow-up within 5.5 s ("That's one rambunctious rookie." 4.5 s after a three): said
    # too, past the end the clip was found with.
    said = [(t + 0.2, t + 3.0), (t + 4.5, t + 5.5), (t + 6.0, t + 9.0)]
    assert shots.clip_end(court, t, t + 5.0, said, fps=FPS, look=t + 11.0)[0] == pytest.approx(t + 5.5 + shots.BREATH)
    # A sentence about it begun after a silence: said too.
    assert _end(court, t, t + 7.0, [(t + 2.6, t + 4.0)])[0] == pytest.approx(t + 4.0 + shots.BREATH)
    # An analyst's line about it, under way 9.5 s after the play: finished when it ends within a
    # second more ("...tip it to himself with such grace"), else left out, the clip ending after
    # his last whole sentence rather than on half a thought.
    for runs_to, ends in ((t + 10.0, t + 10.0 + shots.BREATH), (t + 11.0, t + 3.0 + shots.BREATH)):
        end, _ = shots.clip_end(court, t, t + 7.0, [(t + 0.2, t + 3.0), (t + 4.0, runs_to)], fps=FPS, look=t + 12.0)
        assert end == pytest.approx(ends)
    # Silent: 2 s after the play.
    assert _end(court, t, t + 7.0, [])[0] == pytest.approx(t + 2.0)


def test_a_block_on_one_wide_shot_ends_with_what_is_said_about_it():
    # On the NBA compilation a block's clip ran 31 s past it, the commentary on to other plays.
    t, court = 100.0, _cut((90, 130, "wide"))
    said = [(t + 0.0, t + 1.5), (t + 5.4, t + 7.0), (t + 11.1, t + 13.0)]
    end, why = shots.clip_end(court, t, t + 31.0, said, basket=False, fps=FPS)
    assert end == pytest.approx(t + 2.0) and "next thing" in why
    # A reaction shot after the play, running to the clip's end: kept.
    react = _cut((90, t + 3, "wide"), (t + 3, t + 20, "closeup"))
    assert shots.clip_end(react, t, t + 18.0, [], basket=False, fps=FPS) == (t + 18.0, "kept")


def test_a_short_shot_at_the_end_and_a_dissolve_are_left_out():
    t = 100.0
    # A 0.5 s close-up, then the court: a flash of it, and the clip ends before it.
    cuts = _cut((90, t + 2.2, "wide"), (t + 2.2, t + 2.7, "closeup"), (t + 2.7, t + 20, "wide"))
    end, why = _end(cuts, t, t + 15, [])
    assert end == pytest.approx(t + 2.2 - GUARD) and why.endswith("a short shot at the end left out")
    # The dunk at 341 s on the NBA compilation: 1.4 s of the crowd, then the court. That
    # is the reaction, and it stays (leaving it out made the owner's clips too short).
    cuts = _cut((90, t + 2.2, "wide"), (t + 2.2, t + 3.6, "crowd"), (t + 3.6, t + 20, "wide"))
    end, why = _end(cuts, t, t + 15, [])
    assert end == pytest.approx(t + 3.6 - GUARD) and why == "back to the court after the reaction"
    # A dissolve back to the court that began 0.4 s before its last frame: the end is before it.
    dissolve = _cut((90, t + 2.0, "wide"), (t + 2.0, t + 6.0, "closeup"), (t + 6.0, t + 20, "wide"))
    dissolve[2] = replace(dissolve[2], evidence={"change_s": 0.4})
    assert _end(dissolve, t, t + 15, [])[0] == pytest.approx(t + 5.6 - GUARD)
    words = [{"start": 1.0, "end": 1.4, "word": "it's"}, {"start": 1.5, "end": 1.9, "word": "good"},
             {"start": 2.3, "end": 2.6, "word": "shooter"}]
    assert shots.breaths([(1.0, 2.8, words)]) == [pytest.approx(2.1), 2.8]


def test_the_cut_ends_the_clip_even_in_the_middle_of_his_sentence(look):
    # The cut back to the court at 506 s; he's saying "on fire in his first finals game."
    # from 503.5 s to 507.5 s. Nothing of the court after the cut is shown.
    look.shots[500.0] = _cut((500, 503, "wide"), (503, 506, "closeup"), (506, 516, "wide"))
    said = [SimpleNamespace(start=503.5, end=507.5, words=[])]
    clip = _clip(500.0, 516.0, 501.0)
    leadin.refine_windows([clip, _clip(506.0, 520.0, None)], "game.mp4", said, {"clips": {}}, [501.0, 520.0])
    assert clip.end == 505.95
    # And where the clip must stop, for the render's pad (0.4 s past its end) to stop too.
    assert clip.subscores["sport_stop"] == 505.95
    # The pad never plays a word of what the clip left out either: ended just before his
    # next sentence, it stops before its first word.
    look.shots[600.0] = _cut((600, 603, "wide"), (603, 620, "wide"))
    nxt = [SimpleNamespace(start=605.0, end=607.0, words=[{"start": 605.2, "end": 605.5, "word": " And"}])]
    clip = _clip(600.0, 605.1, 601.0, "game_winner")
    leadin.refine_windows([clip], "game.mp4", nxt, {"clips": {}}, [])
    assert clip.subscores["sport_stop"] == 605.15


def test_every_clip_records_where_the_next_shot_starts_after_its_end(look):
    # A game-winner keeps its end, 0.3 s before the next shot: the render's pad stops
    # just before that shot. With none within the pad, nothing is recorded, and an old
    # stop goes.
    look.shots[509.0] = _cut((500, 503, "wide"), (503, 510.3, "closeup"), (510.3, 516, "wide"))
    winner = _clip(500.0, 510.0, 501.0, "game_winner")
    leadin.refine_windows([winner], "game.mp4", [], {"clips": {}}, [])
    assert winner.end == 510.0 and winner.subscores["sport_stop"] == 510.25
    look.shots[509.0] = _cut((500, 503, "wide"), (503, 516, "closeup"))
    winner = _clip(500.0, 510.0, 501.0, "game_winner", sport_stop=509.0)
    leadin.refine_windows([winner], "game.mp4", [], {"clips": {}}, [])
    assert "sport_stop" not in winner.subscores


def test_an_end_is_never_inside_a_word():
    words = [{"start": 4.36, "end": 4.72, "word": " good."}, {"start": 4.72, "end": 5.1, "word": " For"}]
    assert shots.off_words(4.67, words) == (4.72, True)                     # the rest of "good.", not "For"
    assert shots.off_words(4.75, words) == (4.72, False)                    # not "For"
    assert shots.off_words(5.5, words) == (5.5, False)
    # Its end past the cut: its start instead; all but said (its transcript end runs long
    # before a pause), where it is.
    assert shots.off_words(4.5, words, ceiling=4.70) == (4.36, False)
    assert shots.off_words(4.67, words, ceiling=4.70) == (4.67, False)
    # Never back before the floor: its end when that fits, else where it is.
    assert shots.off_words(4.5, words, floor=4.4) == (4.72, True)
    assert shots.off_words(4.5, words, ceiling=4.70, floor=4.4) == (4.5, False)


def test_a_jump_cut_between_two_wide_shots_is_found_and_a_strobe_is_not():
    fps = 59.94
    steady = [3.0] * 60
    # A jump to a later possession from nearly the same camera: one frame very different,
    # the camera's motion broken (phase-correlation response 0.05).
    mafd, resp = list(steady), [1.0] * 60
    mafd[30], resp[30] = 45.0, 0.05
    assert [c.frame for c in shots.jump_cuts(mafd, resp, fps)] == [30]
    # The arena's strobe: as different, but the same picture brighter (response 0.9).
    resp[30] = 0.9
    assert shots.jump_cuts(mafd, resp, fps) == []
    # A pan's jump the motion test is unsure of (0.62): for the game clock to confirm.
    resp[30], unsure = 0.62, []
    assert shots.jump_cuts(mafd, resp, fps) == [] and shots.jump_cuts(mafd, resp, fps, unsure=unsure) == []
    assert [c.frame for c in unsure] == [30]
    # A cut the frame-difference test already found isn't found twice.
    resp[30] = 0.05
    assert shots.jump_cuts(mafd, resp, fps, near=[29]) == []
    # One during a fast pan (on the NBA compilation: 31 against the pan's 8, response 0.44).
    pan = [8.3] * 60
    pan[30] = 31.3
    assert [c.frame for c in shots.jump_cuts(pan, [1.0] * 30 + [0.44] + [1.0] * 29, fps)] == [30]
    # Fast motion all around: no single frame stands out.
    busy = [30.0] * 60
    busy[30] = 45.0
    assert shots.jump_cuts(busy, [0.05] * 60, fps) == []


def test_a_short_free_throw_goes_back_onto_the_shooter_at_the_line():
    # On the NBA compilation: the shooter's close-up at the line, then the court shot of
    # the free throw. Without the close-up its clip was 4.7 s.
    cuts = _cut((100, 103.25, "wide"), (103.25, 107.42, "closeup"), (107.42, 120, "wide"))
    start, end, done = shots.fit_length(cuts, 107.45, 112.18, 100.0, 10.0, [], 112.18, FPS, setup=True)
    assert start == pytest.approx(103.25 + 1 / FPS) and end == 112.18 and "shooter at the line" in done
    # Any other play: a close-up before its court shot is a cutaway, and the clip stays short.
    assert shots.fit_length(cuts, 107.45, 112.18, 100.0, 10.0, [], 112.18, FPS)[:2] == (107.45, 112.18)
    # Never back onto a crowd shot, nor before the previous basket + 1 s.
    crowd = _cut((100, 103.25, "wide"), (103.25, 107.42, "crowd"), (107.42, 120, "wide"))
    assert shots.fit_length(crowd, 107.45, 112.18, 100.0, 10.0, [], 112.18, FPS, setup=True)[0] == 107.45
    assert shots.fit_length(cuts, 107.45, 112.18, 105.0, 10.0, [], 112.18, FPS, setup=True)[0] == 105.0


def test_sentences_end_at_a_stop_or_the_end_of_the_transcripts_segment():
    words = [{"start": 1.0, "end": 1.4, "word": " Shot's"}, {"start": 1.5, "end": 1.9, "word": " good!"},
             {"start": 2.5, "end": 2.8, "word": " He"}, {"start": 2.9, "end": 3.6, "word": " scores."},
             {"start": 4.0, "end": 4.3, "word": " Shooter"}]
    # A word ending with a stop ends a sentence; so does its segment's end.
    assert shots.sentences([(1.0, 4.5, words)]) == [(1.0, 1.9), (2.5, 3.6), (4.0, 4.5)]
    run_on = [{"start": 1.0 + 0.6 * k, "end": 1.5 + 0.6 * k, "word": "and"} for k in range(4)]
    assert shots.sentences([(1.0, 3.4, run_on), (5.0, 7.0, [])]) == [(1.0, 3.4), (5.0, 7.0)]
    # A pause of half a second ends one too.
    paused = [{"start": 1.0, "end": 1.4, "word": "it's"}, {"start": 1.5, "end": 1.9, "word": "good"},
              {"start": 2.6, "end": 3.0, "word": "shooter"}]
    assert shots.sentences([(1.0, 3.0, paused)]) == [(1.0, 1.9), (2.6, 3.0)]


def test_a_jump_cut_the_motion_test_is_unsure_of_is_settled_by_the_clock(look):
    # A jump during a camera pan (motion response 0.62): a cut only when the game clock
    # jumps across it (10:48 to 10:12), else its two shots are one again.
    box = (0.2, 0.85, 0.8, 0.98)
    shots_ = _cut((90, 104.37, "wide"), (104.37, 120, "wide"))
    shots_[1].evidence = {"unsure_jump": True}
    look.clock = {104.17: 648.0, 104.57: 612.0}
    settled = leadin._settle("game.mp4", [replace(s) for s in shots_], box)
    assert [s.t0 for s in settled] == [90, 104.37] and settled[1].evidence["clock_jump"] == 36.0
    look.clock = {104.17: 648.0, 104.57: 647.6}
    settled = leadin._settle("game.mp4", [replace(s) for s in shots_], box)
    assert [(s.t0, s.t1) for s in settled] == [(90, 120)]
    assert [(s.t0, s.t1) for s in leadin._settle("game.mp4", [replace(s) for s in shots_], None)] == [(90, 120)]


def test_a_jump_the_clock_gives_away_ends_the_play(look):
    # A courtside shot of the next possession reads as a close-up (big players): the clock
    # on the bug jumps across the cut (3:55 to 3:26), so the play is over there. Across a
    # reaction cut it reads the same, and the reaction stays.
    look.shots[100.0] = _cut((100, 112.2, "wide"), (112.2, 113.7, "closeup"), (113.7, 120, "wide"))
    look.clock = {112.0: 235.0, 112.4: 206.0}
    clip = _clip(100.0, 116.0, 107.9)
    leadin.refine_windows([clip], "game.mp4", [], {"clips": {}}, [], bug_box=(0.2, 0.85, 0.8, 0.98))
    assert clip.end == 112.15
    look.shots[100.0] = _cut((100, 112.2, "wide"), (112.2, 113.7, "closeup"), (113.7, 120, "wide"))
    look.clock = {112.0: 235.0, 112.4: 235.0}
    clip = _clip(100.0, 116.0, 107.9)
    leadin.refine_windows([clip], "game.mp4", [], {"clips": {}}, [], bug_box=(0.2, 0.85, 0.8, 0.98))
    assert clip.end == 113.65           # back to the court after the reaction
    # No bug to read: the close-up is a reaction.
    look.shots[100.0] = _cut((100, 112.2, "wide"), (112.2, 113.7, "closeup"), (113.7, 120, "wide"))
    clip = _clip(100.0, 116.0, 107.9)
    look.clock = {112.0: 235.0, 112.4: 206.0}
    leadin.refine_windows([clip], "game.mp4", [], {"clips": {}}, [])
    assert clip.end == 113.65


def test_a_free_throw_is_over_once_it_drops():
    # The game-tying free throw on the NBA compilation: the close-up after it was the
    # shooter's setup for the second; the clip lingered 4 s on it, then cut before the shot.
    t = 100.0
    cuts = _cut((90, t + 2.0, "wide"), (t + 2.0, t + 6.4, "closeup"), (t + 6.4, t + 20, "wide"))
    end, why = shots.clip_end(cuts, t, t + 6.33, [(t, t + 1.9)], fps=FPS, free_throw=True)
    assert end == pytest.approx(t + 2.0) and why.startswith("a free throw")
    # A basket, not a free throw: the scorer's close-up stays, to the cut.
    assert _end(cuts, t, t + 6.33, [(t, t + 1.9)])[0] == pytest.approx(t + 6.33)


def test_a_trip_to_the_line_is_one_play_until_the_clock_runs_again():
    # A trip to the line the owner wanted whole: the shooter's close-up, back on the court
    # for the second attempt, "Two clutch free throws." as it drops, the clock running
    # again at the inbound, in the same shot (read stopped at 10.5 s, changed at 14 s).
    t = 100.0
    cuts = _cut((90, t + 2.1, "wide"), (t + 2.1, t + 6.35, "closeup"), (t + 6.35, t + 20, "wide"))
    said = [(t, t + 1.96), (t + 9.0, t + 11.2), (t + 11.96, t + 13.9)]
    # (The clip is looked at, and may end, to the trip's end: leadin passes that as its end.)
    trip = (t + 10.5, t + 11.8, t + 14.0)
    end, why = shots.clip_end(cuts, t, t + 14.5, said, fps=FPS, free_throw=True, trip=trip)
    assert end == pytest.approx(t + 11.8) and why.startswith("the free throws are over")
    # The game-tying one on the NBA compilation: read 30.3 at 76.8 s, 27.2 at 81.8 s, the
    # broadcast jumping past the inbound at 81.7 s. Worked back the clock "ran" from 78.7 s,
    # before the second attempt: the cut between the readings is where the trip ends.
    cuts = _cut((60, 72.5, "wide"), (72.5, 76.75, "closeup"), (76.75, 81.7, "wide"), (81.7, 90, "wide"))
    said = [(70.4, 72.36), (79.42, 81.62), (82.36, 84.26)]
    end, why = shots.clip_end(cuts, 70.4, 82.3, said, fps=FPS, free_throw=True, trip=(76.8, 78.7, 81.8))
    assert end == pytest.approx(81.7 - GUARD) and why.startswith("the free throws are over")
    # The go-ahead one: the second attempt missed, the clock ran a moment for the rebound
    # and stopped again, one wide shot all along. Over before "Fight for the rebound.",
    # the first thing he says after the clock was last read stopped.
    one = _cut((1809.14, 1814.64, "wide"), (1814.64, 1826.0, "wide"))
    said = [(1810.22, 1815.06), (1818.5, 1819.84), (1819.84, 1821.0), (1821.0, 1821.38)]
    end, why = shots.clip_end(one, 1812.1, 1826.0, said, fps=FPS, free_throw=True,
                              trip=(1819.32, 1819.32, 1825.49))
    assert end == pytest.approx(1819.84) and why.startswith("the free throws are over")


def test_a_free_throws_clip_runs_through_its_trip_to_the_line(look):
    # The second attempt goes in at 109.5 s, a basket of its own: it doesn't stop the
    # first one's clip, which runs to the inbound at 111.8 s.
    look.shots[95.0] = _cut((90, 102.1, "wide"), (102.1, 106.35, "closeup"), (106.35, 120, "wide"))
    clip = _clip(95.0, 104.0, 100.0, "free_throw")
    leadin.refine_windows([clip], "game.mp4", [], {"clips": {}}, [100.0, 109.5],
                          free_throws=[(100.0, (110.5, 111.8, 114.0)), (109.5, (110.5, 111.8, 114.0))])
    assert clip.end == 111.8
    assert look.seen[0].t1 == pytest.approx(114.0 + leadin.PAD_LOOK)


def test_after_a_block_the_break_the_other_way_is_the_next_play():
    # A block on the NBA compilation: "He picks it up." runs to 2.8 s after it, then at once
    # "Comes the other way." (the other team's break): his next thought.
    t, court = 100.0, _cut((90, 130, "wide"))
    said = [(t + 0.0, t + 1.1), (t + 1.7, t + 2.8), (t + 2.8, t + 3.6), (t + 5.4, t + 7.0)]
    end, why = shots.clip_end(court, t, t + 18.0, said, basket=False, fps=FPS)
    assert end == pytest.approx(t + 2.8) and "next thing" in why


def test_a_flash_left_out_leaves_the_end_where_it_falls():
    # Ending 0.43 s into the scorer's close-up, before his next sentence: a glimpse of it,
    # left out, the clip ending before the close-up even mid-sentence (backing off to an
    # earlier sentence made the owner's clips too short).
    t = 100.0
    cuts = _cut((90, t + 2.3, "wide"), (t + 2.3, t + 2.73, "closeup"), (t + 2.73, t + 20, "wide"))
    said = [(t - 1.5, t + 0.2), (t + 0.2, t + 1.3), (t + 1.6, t + 2.7), (t + 2.75, t + 5.0)]
    end, why = shots.clip_end(cuts, t, t + 12.0, said, fps=FPS)
    assert end == pytest.approx(t + 2.3 - GUARD) and why.endswith("a short shot at the end left out")
    # A clutch three: the scorer's close-up runs on, then a referee crosses the camera and
    # it looks like a cut 0.26 s before the end: that glimpse is left out.
    cuts = _cut((90, t + 6.18, "wide"), (t + 6.18, t + 8.8, "closeup"), (t + 8.8, t + 9.08, "closeup"))
    cuts[2] = replace(cuts[2], evidence={"change_s": 0.23})
    said = [(t + 2.96, t + 5.98), (t + 7.34, t + 9.06), (t + 10.94, t + 13.0)]
    assert shots.clip_end(cuts, t, t + 9.06, said, fps=FPS)[0] == pytest.approx(t + 8.57 - GUARD)


def test_short_clips_go_back_in_the_possession_first_and_never_into_the_next_sentence():
    # The start is mid-shot on the court: it goes back, within that shot, before the end goes on.
    clip = _cut((100, 103, "closeup"), (103, 120, "wide"))
    start, end, done = shots.fit_length(clip, 106.0, 112.0, 100.0, 10.0, [], fps=FPS)
    assert (start, end) == (pytest.approx(103 + FRAME), pytest.approx(113 + FRAME))
    assert done == "starts earlier in the possession; ends a little later"
    # The start at the shot's first frame can't go back: the end goes on, but not into a sentence.
    start, end, done = shots.fit_length(clip, 103.04, 109.0, 100.0, 10.0, [(110.0, 112.0)], fps=FPS)
    assert (start, end) == (103.04, pytest.approx(109.95)) and done == "ends a little later"


def test_a_cut_the_clock_jumps_across_starts_the_clip_a_camera_change_does_not(look):
    # A condensed broadcast: the possession of the three from 105.5 s, after a jump from
    # another moment (the clock on the bug 6:40, then 6:12): the clip starts there.
    box = (0.2, 0.85, 0.8, 0.98)
    look.shots[100.0] = _cut((100, 105.5, "wide"), (105.5, 116, "wide"))
    look.clock = {105.3: 400.0, 105.7: 372.0}
    three = _clip(100.0, 116.0, 111.0, "made_3")
    leadin.refine_windows([three], "game.mp4", [], CONFIG, [], bug_box=box)
    assert three.start == 105.55
    # A camera change the clock reads on across is the same possession: the start stays
    # (the owner wanted the lead-up the job found, as long as it is the play's).
    for clock, bug in (({105.3: 400.0, 105.7: 399.6}, box), ({105.3: 400.0, 105.7: 372.0}, None)):
        look.shots[100.0] = _cut((100, 105.5, "wide"), (105.5, 116, "wide"))
        look.clock = clock
        three = _clip(100.0, 116.0, 111.0, "made_3")
        leadin.refine_windows([three], "game.mp4", [], CONFIG, [], bug_box=bug)
        assert three.start == 100.0


def test_the_previous_basket_bounds_the_start(look):
    look.shots[100.0] = _cut((100, 102, "wide"), (102, 116, "wide"))
    clip = _clip(100.0, 116.0, 111.0)
    leadin.refine_windows([clip], "game.mp4", [], CONFIG, [103.0, 111.0])
    assert clip.start == 104.0       # the basket at 103 s, plus 1 s


def test_a_short_clip_is_never_lengthened_into_the_next_scene(look):
    # 4 s of close-up: from the court on, 104-112.5 is under 10 s. With one wide shot after
    # the play it stays that way, whatever room there is after it.
    look.shots[100.0] = _cut((100, 104, "closeup"), (104, 116, "wide"))
    for baskets, others in (((), ()), ((110.5, 130.0), ()), ((), (_clip(113.0, 125.0, None),))):
        clip = _clip(100.0, 112.0, 110.5)
        leadin.refine_windows([clip, *others], "game.mp4", [], CONFIG, list(baskets))
        assert (clip.start, clip.end) == (104.05, 112.5)


def test_a_failed_look_leaves_the_windows_as_found(look, monkeypatch):
    for t0 in (100.0, 200.0):
        look.shots[t0] = _cut((t0, t0 + 3, "closeup"), (t0 + 3, t0 + 16, "wide"))
    # One clip's decode fails: only that clip keeps its start.
    look.fail = {100.0}
    a, b = _clip(100.0, 116.0, 111.0), _clip(200.0, 216.0, 211.0)
    leadin.refine_windows([a, b], "game.mp4", [], CONFIG, [])
    assert (a.start, a.end) == (100.0, 116.0) and b.start > 203.0
    # The detector can't load: nothing moves.

    def broken(name):
        raise OSError("no weights")

    monkeypatch.setattr(ball, "_model", broken)
    c = _clip(200.0, 216.0, 211.0)
    leadin.refine_windows([c], "game.mp4", [], CONFIG, [])
    assert (c.start, c.end) == (200.0, 216.0)


def test_the_cameras_scale_reaches_every_look(look):
    # Every clip's shots are told apart with how tall the game camera shows
    # the players: a play's clip, and one that keeps its window (a reaction).
    clips = [_clip(100.0, 116.0, 111.0), _clip(200.0, 212.0, None, kind="crowd_reaction")]
    leadin.refine_windows(clips, "game.mp4", [], CONFIG, [111.0], people_scale=1.8)
    assert len(look.seen) == 2 and {s.scale for s in look.seen} == {1.8}


def test_a_cancel_stops_the_job_instead_of_skipping_a_clip(look, monkeypatch):
    def cancelled(*a, **k):
        raise cancel.CancelledError("cancelled")

    monkeypatch.setattr(leadin, "probe", cancelled)
    with pytest.raises(cancel.CancelledError):
        leadin.refine_windows([_clip(100.0, 116.0, 111.0)], "game.mp4", [], CONFIG, [])


def test_a_start_mid_word_moves_to_its_end(look):
    look.shots[100.0] = _cut((100, 103, "closeup"), (103, 116, "wide"))
    clip = _clip(100.0, 116.0, 111.0)
    said = [SimpleNamespace(words=[{"start": 102.9, "end": 103.2, "word": " and"}])]
    leadin.refine_windows([clip], "game.mp4", said, CONFIG, [])
    assert clip.start == 103.2


def test_the_profile_passes_the_baskets_the_bug_and_the_model(monkeypatch):
    got = []
    monkeypatch.setattr(leadin, "refine_windows",
                        lambda candidates, path, segments, config, baskets, **kw: got.append((baskets, kw))
                        or candidates)
    profile = sports.profile_for({"clips": {"sport": {"name": "basketball", "highlights": "best",
                                                      "period": "full"}}})
    profile._baskets = [SimpleNamespace(t=103.4), SimpleNamespace(t=211)]
    profile._changes = {id(profile._baskets[1]): SimpleNamespace(points=1)}     # a free throw at 211 s
    left = {103.4: 300.0, 211.0: 2.0}
    bug, moved = (0.05, 0.85, 0.35, 0.95), (0.6, 0.85, 0.95, 0.95)
    profile.board = SimpleNamespace(box=bug, box_at=lambda t: moved if t > 200 else bug,
                                    period_number=lambda t: 4, last_period=lambda: 4, clock_at=lambda t: left[t],
                                    clock_stopped=lambda t: (t + 6.0, t + 9.0, t + 11.0))
    clips = [_clip(100.0, 116.0, 111.0)]
    assert profile.refine_windows(clips, "game.mp4", [], CONFIG) == clips
    model = str((sports.spec("basketball").get("framing") or {}).get("model") or "yolov8n.pt")
    # The bug's box by the time: where it sat at each clip's play (it moved, two broadcasts joined).
    at = got[0][1].pop("bug_box")
    assert (at(103.4), at(211.0)) == (bug, moved)
    assert got == [([103.4, 211.0], {"model_name": model, "free_throws": [(211.0, (217.0, 220.0, 222.0))],
                                     "final": [211.0], "people_scale": 1.0})]
    # A camera that shows the players taller (the prepass's measure): its
    # shots are told by it, and each clip keeps it for its framing.
    profile.people_scale = 1.8
    got.clear()
    profile.refine_windows(clips, "game.mp4", [], CONFIG)
    assert got[0][1]["people_scale"] == 1.8 and clips[0].subscores["sport_scale"] == 1.8
    profile.people_scale = 1.0
    del clips[0].subscores["sport_scale"]
    # The last basket read with 16 s left: the game went on, its clip ends like any other.
    left[211.0] = 16.3
    got.clear()
    profile.refine_windows(clips, "game.mp4", [], CONFIG)
    assert got[0][1]["final"] == []
    # Gym or phone footage (one camera, no cutaways) and Vertical Live are left alone.
    got.clear()
    profile.footage = "sideline"
    assert profile.refine_windows(clips, "game.mp4", [], CONFIG) == clips
    profile.footage = "broadcast"
    assert profile.refine_windows(clips, "game.mp4", [], {"clips": {"vertical_live": True}}) == clips
    assert got == []


def test_the_look_tells_the_shots_where_the_score_bug_is_up(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frames = [np.full((360, 640, 3), 90, np.uint8) for _ in range(30)]
    monkeypatch.setattr(leadin, "_frames", lambda *a, **k: iter(frames))
    asked = []

    def flags(grays, box):
        asked.append((len(grays), box, grays[0].shape))
        return [k % 2 == 0 for k in range(len(grays))]

    seen = {}

    def classify(found, samples):
        seen["bug"] = [s.bug for s in samples]
        return found

    monkeypatch.setattr(observe, "bug_flags", flags)
    monkeypatch.setattr(shots, "classify_all", classify)
    box = (0.05, 0.85, 0.35, 0.95)
    found = leadin.probe("game.mp4", 100.0, 101.0, FPS, model=None, bug_box=box)
    # 30 frames looked at 4 times a second: every 8th, so 4 samples, each told.
    assert asked == [(4, box, (360, 640))] and seen["bug"] == [True, False, True, False]
    assert found and found[0].t0 == 100.0
    # Without the bug's box, nothing is said about it.
    asked.clear()
    leadin.probe("game.mp4", 100.0, 101.0, FPS, model=None)
    assert asked == [] and seen["bug"] == [None] * 4


def test_a_replay_stinger_starts_the_replay_where_its_logo_appears():
    # A team's network: its logo grows in the middle of the live picture
    # (0.2 s), holds there while the game goes on around it, then fills the
    # screen and the replay cuts in: about 0.9 s the cut test never saw, the
    # clip ending on the logo. The middle's near-white share is 0.00-0.03
    # before it and 0.3-0.45 while it is held.
    fps, cut = 30.0, 60
    white = [0.02] * 33 + [0.1, 0.22, 0.3] + [0.4] * 18 + [0.6, 0.0, 0.0, 0.5, 0.6, 0.6] + [0.1] * 5
    mid = [3.0] * 33 + [20.0, 30.0, 25.0] + [6.0] * 18 + [40.0] * 6 + [3.0] * 5
    edge = [3.0] * 33 + [3.0, 3.0, 8.0] + [10.0] * 18 + [40.0] * 6 + [3.0] * 5
    assert shots.stinger_onset(white, mid, edge, fps, cut) == 33
    # A white jersey turning into the middle of a close-up: it moves with the
    # person, the blurred crowd around it doesn't.
    turning = [m * 3 if 36 <= k < 54 else m for k, m in enumerate(mid)]
    assert shots.stinger_onset(white, turning, edge, fps, cut) is None
    # White throughout (a white jersey filling the close-up): no sudden jump.
    assert shots.stinger_onset([0.4] * 65, mid, edge, fps, cut) is None


def test_after_a_basket_the_play_is_over_where_the_camera_follows_the_next_possession():
    # One court shot, no cut: the camera holds or drifts 0.2 for the inbound,
    # then pans with the other team. On a team's network six clips ran 5-9 s
    # into the next possession, the announcer never stopping.
    times = [100.0 + k / 10 for k in range(90)]
    travel = [0.0] * 15 + [0.01 * k for k in range(10)] + [0.1 - 0.05 * k for k in range(65)]
    on = shots.camera_moves_on(times, travel, 100.0, 95.0, 120.0)
    assert on == pytest.approx(102.6, abs=0.15)
    assert shots.camera_moves_on(times, [0.0] * 90, 100.0, 95.0, 120.0) is None
    court = [Shot(id=0, f0=0, f1=900, t0=95.0, t1=120.0, kind="wide", replay=False)]
    said = [(101.0, 103.0), (104.0, 109.5)]
    done = shots.clip_end(court, 100.0, 104.0, said, look=111.0, camera_on=on)
    # His sentence under way as the camera leaves (to 103.0) finishes; the
    # next one (from 104.0) doesn't start.
    assert done.end == pytest.approx(103.0 + shots.BREATH, abs=0.05)
    assert "the camera follows the next possession" in done.reason
    run_on = [(101.0, 112.0)]                       # an analyst's run-on that doesn't end soon
    assert shots.clip_end(court, 100.0, 104.0, run_on, look=111.0, camera_on=on).end <= on + 0.05
    # Not after a block or a steal: the break the other way is the play.
    assert "next possession" not in shots.clip_end(court, 100.0, 104.0, said, basket=False, look=111.0,
                                                   camera_on=on).reason
