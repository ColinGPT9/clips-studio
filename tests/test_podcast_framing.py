"""Podcast mode (video/podcast.py), on synthetic clips.

Each shot is framed on ONE person with a static crop, and every change is a
one-frame snap. What is pinned down here is how that person is chosen and
when the crop moves:

  * cuts come from video/cuts.py's ShotCuts, on their exact frame;
  * a candidate is seen as a face on min(8, half the shot's) samples, is at
    least 3% of the frame and a confident detection (a lamp is not);
  * TalkNet's speaker on a frame is the top logit when it is at least 0 and
    1.0 ahead of every other candidate; where only one candidate is on
    screen, it is theirs;
  * a turn inside a shot needs 1 s of its speaker's speech to move the crop
    (the smallest short turn is dropped first), and then the crop moves on
    its first frame;
  * a shot under 0.5 s is never slid across, a solo shot frames its only
    face, an empty one holds the framing before it;
  * TalkNet is asked per shot, about that shot's candidates, on stretches of
    at least 26 frames (25 come back all zeros), through optional arguments
    of video.tracker.score_faces that the stream tracker leaves at their
    defaults.

The clips are synthetic, and nothing in them is a real person:
  * video_capture -> a fake capture of flat-colour shots (one colour a shot);
  * _get_model -> None, _detect -> scripted boxes for the frame, _face_box ->
    None (every person here has a pose head);
  * score_faces -> a stand-in for TalkNet that scores the faces a script says,
    only where two candidates are on screen, as the real one does.

numpy and OpenCV are needed: pytest.importorskip, as CI's light install has
neither.
"""

import contextlib
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from video import asd, cropper, podcast, tracker  # noqa: E402

W, H = 320, 180          # 16:9, small so a whole clip runs in a fraction of a second
FPS = 29.97              # sample_fps 8 -> one sample every 4 frames, as on real footage
STEP = max(1, round(FPS / 8))
N = 180                  # frames per clip (6 s)
TURN = int(round(podcast._TURN * FPS))

# Flat shot colours (BGR); every pair is a strong cut.
A = (150, 90, 30)
B = (40, 210, 240)
C = (70, 80, 215)


def person(cx, *, width=0.2, conf=0.9, head=True):
    """A seated person centred at cx (a share of the width); the pose model finds their head
    above the centre unless head=False."""
    return ((cx - width / 2) * W, 0.15 * H, (cx + width / 2) * W, 0.98 * H, conf,
            (cx * W, 0.3 * H, 0.08 * W) if head else None)


def face(cx):
    """The crop centre podcast.py aims at for a person at cx: their face box's centre."""
    b = tracker.head_box((cx * W, 0.3 * H, 0.08 * W))
    return (b[0] + b[2]) / 2 / W


class Scene:
    """colour(i) -> frame i's flat colour; people(i) -> what the detector finds on it."""

    def __init__(self, colour, people, n=N):
        self.colour, self.people, self.n = colour, people, n

    def frame(self, i):
        return np.full((H, W, 3), self.colour(i), np.uint8)


class FakeCapture:
    def __init__(self, scene):
        self.scene = scene
        self.pos, self.cur = 0, None

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return FPS
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.scene.n)
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self.pos)
        return 0.0

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self.pos = int(value)
            return True
        return False

    def grab(self):
        if self.pos >= self.scene.n:
            return False
        self.cur, self.pos = self.pos, self.pos + 1
        return True

    def retrieve(self):
        if self.cur is None:
            return False, None
        return True, self.scene.frame(self.cur)

    def read(self):
        return self.retrieve() if self.grab() else (False, None)

    def release(self):
        pass


def shots(*parts, n=N):
    """A clip of hard cuts: parts are (first frame, colour, people) in order, people being a
    list of person(...) for the whole shot."""
    def find(i):
        return [p for p in parts if p[0] <= i][-1]
    return Scene(lambda i: find(i)[1], lambda i: list(find(i)[2]), n=n)


def run(scene, monkeypatch, talk=None):
    """podcast.analyze on a synthetic scene. talk(t, x) is TalkNet's logit for the face at x
    (a share of the width) at time t, or None; without it TalkNet has no answer.
    Returns (decision, the score_faces calls)."""

    @contextlib.contextmanager
    def capture(_path, *, required=True):
        yield FakeCapture(scene)

    # One _detect call per sample, in order: the k-th is frame k * STEP.
    calls = iter(range(10**6))

    def detect(_model, _frame, _min_conf):
        return list(scene.people(next(calls) * STEP))

    asked = []

    def score_faces(_clip, seen, duration, video_fps, **kw):
        asked.append(SimpleNamespace(seen=seen, duration=duration, fps=video_fps, kw=kw))
        if talk is None:
            return None
        cands = [t for t, v in seen.items() if len(v) >= kw["min_faces"]]
        cands = sorted(cands, key=lambda t: -len(seen[t]))[:kw["max_tracks"]]
        if len(cands) < 2:
            return None
        n = int(duration * asd.FPS)
        times = np.arange(n) / asd.FPS
        present = {t: (times >= seen[t][0][0]) & (times <= seen[t][-1][0]) for t in cands}
        together = np.sum(list(present.values()), axis=0) >= 2
        scores = {}
        for tid in cands:
            x = float(np.median([(b[0] + b[2]) / 2 / W for _, b in seen[tid]]))
            a = np.full(n, -np.inf, np.float32)
            for j in np.flatnonzero(together & present[tid]):
                v = talk(j / asd.FPS, x)
                if v is not None:
                    a[j] = v
            scores[tid] = a
        return cands, scores, n

    monkeypatch.setattr(podcast, "video_capture", capture)
    monkeypatch.setattr(podcast, "_get_model", lambda _name: None)
    monkeypatch.setattr(podcast, "_detect", detect)
    monkeypatch.setattr(podcast, "_face_box", lambda _frame, _box: None)
    monkeypatch.setattr(podcast, "score_faces", score_faces)
    return podcast.analyze("clip.mp4"), asked


def speaking(who):
    """TalkNet as a script: who(t) is the x of the person talking at time t (None: nobody).
    The talker scores +3, everyone else -2."""
    def talk(t, x):
        w = who(t)
        return 3.0 if w is not None and abs(x - w) < 0.01 else -2.0
    return talk


def at(result, frame):
    """The crop centre the renderer uses on this frame (video/cropper.py)."""
    return cropper._interpolate(result["path"], frame / FPS)


def assert_never_slides(result):
    """Every change of framing is a one-frame snap: two keys whose x differ are one frame apart."""
    keys = result["path"]
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        assert t1 >= t0
        if x0 != x1:
            assert round((t1 - t0) * FPS) == 1, (t0 * FPS, x0, t1 * FPS, x1)


def framings(result, n=N):
    """The crop centre on every frame."""
    return [at(result, f) for f in range(n)]


def only(xs, cx):
    """Every one of these frames is framed on the face of the person at cx."""
    assert xs and all(x == pytest.approx(face(cx)) for x in xs), sorted(set(xs))


# ---- cuts -----------------------------------------------------------------------------------


def test_the_crop_snaps_on_the_cut_s_own_frame(monkeypatch):
    # The cut lands on f61, between the samples at f60 and f64.
    result, _ = run(shots((0, A, [person(0.25)]), (61, B, [person(0.75)])), monkeypatch)
    assert at(result, 60) == pytest.approx(face(0.25))
    assert at(result, 61) == pytest.approx(face(0.75))
    assert set(framings(result)) == {at(result, 0), at(result, N - 1)}
    assert_never_slides(result)


# ---- who is framed --------------------------------------------------------------------------


def test_a_solo_shot_frames_its_only_face_whatever_talknet_says(monkeypatch):
    # The body is centred at 0.4; the crop goes to the face, and TalkNet is not asked.
    body = person(0.4)
    off_centre = (*body[:5], (0.5 * W, 0.3 * H, 0.08 * W))
    result, asked = run(shots((0, A, [off_centre])), monkeypatch, talk=speaking(lambda t: 0.9))
    only(framings(result), 0.5)
    assert asked == []


def test_the_talker_is_framed_not_the_bigger_person(monkeypatch):
    # A small talker at 0.3 and a big listener at 0.7, together for the whole shot.
    people = [person(0.3, width=0.15), person(0.7, width=0.3)]
    result, asked = run(shots((0, A, people)), monkeypatch, talk=speaking(lambda t: 0.3))
    only(framings(result), 0.3)
    assert len(asked) == 1
    # With no verdict at all, the most prominent person is framed.
    result, _ = run(shots((0, A, people)), monkeypatch, talk=speaking(lambda t: None))
    only(framings(result), 0.7)


@pytest.mark.parametrize("conf, framed", [(0.46, 0.7), (0.5, 0.25)])
def test_a_low_confidence_person_is_no_candidate(conf, framed, monkeypatch):
    # A big "person" at 0.25 the detector half believes in, with a pose head (a studio lamp),
    # beside the real one at 0.7. Under 0.5 median confidence it is nobody, so the shot is solo;
    # at 0.5 it counts, and with TalkNet silent the bigger of the two takes the shot.
    lamp = person(0.25, width=0.3, conf=conf)
    result, _ = run(shots((0, A, [lamp, person(0.7)])), monkeypatch, talk=speaking(lambda t: None))
    only(framings(result), framed)


def _p(n_face, area=0.1, conf=0.9):
    return SimpleNamespace(n_face=n_face, area=area, conf=conf)


def test_the_candidate_test():
    cands = podcast._candidates
    # Seen as a face on 8 samples, or on half the shot's samples when that is fewer.
    assert cands({1: _p(8), 2: _p(7)}, 20) == [1]
    assert cands({1: _p(3), 2: _p(2)}, 6) == [1]
    # One sighting is enough only in a shot of one or two samples: an edge body seen once in a
    # long shot is not a candidate.
    assert cands({1: _p(1)}, 2) == [1]
    assert cands({1: _p(1)}, 20) == []
    # Never without a face, never under 3% of the frame, never under 0.5 median confidence.
    assert cands({1: _p(0)}, 1) == []
    assert cands({1: _p(8, area=0.029), 2: _p(8, area=0.03)}, 20) == [2]
    assert cands({1: _p(8, conf=0.46), 2: _p(8, conf=0.5)}, 20) == [2]


# ---- the speaker rule -----------------------------------------------------------------------


def test_the_speaker_needs_a_logit_of_zero_and_a_lead_of_one():
    speaker = podcast._speaker
    assert speaker({1: 0.5, 2: -0.6}) == 1       # 1.1 ahead
    assert speaker({1: 0.5, 2: -0.4}) is None    # 0.9 ahead: a close call is nobody
    assert speaker({1: 2.0, 2: 1.0}) == 1        # exactly 1.0 ahead
    assert speaker({1: -0.1, 2: -5.0}) is None   # far ahead, but not speaking
    assert speaker({1: 3.0, 2: 1.5, 3: -2.0}) == 1
    assert speaker({1: 3.0, 2: 2.5, 3: -2.0}) is None  # ahead of every other, not just the last
    assert speaker({2: 0.0}) == 2                # the only face scored, at the boundary
    assert speaker({}) is None


def test_where_one_candidate_is_on_screen_it_is_theirs(monkeypatch):
    # The left person is alone for the first 4 s and talks (TalkNet scores nobody there: one
    # face); the right one joins for the last 2 s and TalkNet names them. Without the alone rule
    # the right one, on screen for a third of the shot, would take all of it.
    left, right = person(0.3), person(0.7)
    scene = Scene(lambda i: A, lambda i: [left] if i < 120 else [left, right])
    result, _ = run(scene, monkeypatch, talk=speaking(lambda t: 0.7))
    xs = framings(result)
    switch = next(f for f in range(N) if xs[f] != xs[0])
    assert xs[0] == pytest.approx(face(0.3))
    assert 118 <= switch <= 124
    only(xs[switch:], 0.7)
    assert_never_slides(result)
    # Frames 0-2 are nearest the sample at frame 0, where only 5 was seen: theirs, whatever
    # TalkNet said; frame 3 is nearest the one at 4, where both were.
    people = {5: SimpleNamespace(frames=[0, 4]), 6: SimpleNamespace(frames=[4])}
    assert podcast._alone([None, 6, None, None], 0, people, [5, 6]) == [5, 5, 5, None]


# ---- turns inside a shot --------------------------------------------------------------------


def _first_frame(t):
    """The first video frame TalkNet reads at time t or later (frame f reads round(f / fps * 25))."""
    return next(f for f in range(N) if round(f / FPS * asd.FPS) / asd.FPS >= t)


def test_an_interjection_does_not_move_the_crop_and_a_new_turn_does_on_its_first_frame(monkeypatch):
    # One two-shot: left talks 0-2 s, right cuts in for 0.6 s, left again until 4 s, then right
    # for 2 s.
    def who(t):
        return 0.7 if 2.0 <= t < 2.6 or t >= 4.0 else 0.3

    result, _ = run(shots((0, A, [person(0.3), person(0.7)])), monkeypatch, talk=speaking(who))
    xs = framings(result)
    turn = _first_frame(4.0)
    only(xs[:turn], 0.3)
    only(xs[turn:], 0.7)
    assert_never_slides(result)


def test_turns_under_a_second_are_dropped_smallest_first():
    kept = podcast._kept_turns
    # Left 40 frames, right 10, left 20, right 40 (a turn is 30 here). The right's 10 go first,
    # which makes the left's two turns one (60 frames), so its 20 never get dropped.
    v = [1] * 40 + [2] * 10 + [1] * 20 + [2] * 40
    assert kept(v, 30) == [(0, 70, 1), (70, 110, 2)]
    # Silence holds the last speaker; the shot opens on the first one.
    v = [None] * 10 + [1] * 40 + [None] * 20 + [2] * 40
    assert kept(v, 30) == [(0, 70, 1), (70, 110, 2)]
    # No full turn anywhere: the turn left after the smallest-first drops takes the shot. Here
    # that is the candidate with the most speech, as it usually (not always) is.
    assert kept([1] * 5 + [2] * 9 + [1] * 3, 30) == [(0, 17, 2)]
    assert kept([None] * 20, 30) is None


def test_a_turn_is_one_second_of_speech():
    assert TURN == round(FPS)
    v = [1] * 60 + [2] * (TURN - 1) + [1] * 60
    assert podcast._kept_turns(v, TURN) == [(0, len(v), 1)]
    v = [1] * 60 + [2] * TURN + [1] * 60
    assert podcast._kept_turns(v, TURN) == [(0, 60, 1), (60, 60 + TURN, 2), (60 + TURN, len(v), 1)]


def test_a_small_move_inside_a_shot_is_not_made(monkeypatch):
    # Two people 0.04 of the width apart (the same place, as far as a crop is concerned): the
    # turn changes who is framed but not where.
    people = [person(0.48, width=0.1), person(0.52, width=0.1)]
    result, _ = run(shots((0, A, people)), monkeypatch,
                    talk=speaking(lambda t: 0.48 if t < 3.0 else 0.52))
    assert len(set(framings(result))) == 1


# ---- short, solo and empty shots ------------------------------------------------------------


@pytest.mark.parametrize("middle, framed", [
    ([person(0.5), person(0.8)], 0.25),   # two people: holds the framing before it
    ([person(0.5)], 0.5),                 # one: its face
    ([], 0.25),                           # nobody: holds
])
def test_a_shot_under_half_a_second_is_never_slid_across(middle, framed, monkeypatch):
    # A 10-frame shot (0.33 s) between two cuts.
    scene = shots((0, A, [person(0.25)]), (60, B, middle), (70, C, [person(0.75)]))
    result, asked = run(scene, monkeypatch, talk=speaking(lambda t: 0.8))
    xs = framings(result)
    only(xs[:60], 0.25)
    only(xs[60:70], framed)
    only(xs[70:], 0.75)
    assert_never_slides(result)
    assert asked == []   # a short shot is not scored


def test_an_empty_shot_holds_the_framing_and_an_empty_start_takes_the_first(monkeypatch):
    result, _ = run(shots((0, A, [person(0.3)]), (60, B, [])), monkeypatch)
    only(framings(result), 0.3)
    result, _ = run(shots((0, A, []), (60, B, [person(0.7)])), monkeypatch)
    only(framings(result), 0.7)


# ---- how TalkNet is asked -------------------------------------------------------------------


def test_talknet_is_asked_per_shot_about_that_shot_s_people(monkeypatch):
    two = [person(0.3), person(0.7)]
    result, asked = run(shots((0, A, two), (90, B, two)), monkeypatch,
                        talk=speaking(lambda t: 0.3))
    assert len(asked) == 2
    last_sample = (N - 1) // STEP * STEP
    for call, (f0, f1) in zip(asked, [(0, 90), (90, N)]):
        assert call.kw == {"max_tracks": 4, "min_faces": 3, "window": 26}
        assert call.duration == pytest.approx(last_sample / FPS)
        assert len(call.seen) == 2
        for sightings in call.seen.values():
            assert all(f0 / FPS <= t < f1 / FPS for t, _ in sightings)


@pytest.fixture
def talknet_windows(monkeypatch):
    """score_faces with the model stood in for: the length of every stretch handed to
    asd.score_track, in order."""
    lengths = []

    def score_track(faces, pcm):
        lengths.append(len(faces))
        return np.ones(len(faces), np.float32)

    @contextlib.contextmanager
    def capture(_path, *, required=True):
        yield FakeCapture(Scene(lambda i: A, lambda i: [], n=300))

    monkeypatch.setattr(asd, "available", lambda: True)
    monkeypatch.setattr(asd, "score_track", score_track)
    monkeypatch.setattr(tracker, "_clip_pcm", lambda _path, sr: np.zeros(sr * 10, np.float32))
    monkeypatch.setattr(tracker, "video_capture", capture)
    return lengths


def _together(n_tracks=2, sightings=9):
    """Faces seen together for half a second from t = 2 s, 16 times a second."""
    return {tid: [(2.0 + k / 16, (40.0 + 50 * tid, 40.0, 90.0 + 50 * tid, 90.0))
                  for k in range(sightings)] for tid in range(n_tracks)}


def test_the_podcast_path_scores_a_short_two_shot_on_26_frames(talknet_windows):
    # Half a second together is 13 frames at 25 fps, grown to a window the model can take:
    # 25 frames for the stream tracker, as it has always been, and 26 for the podcast path.
    assert tracker.score_faces("clip.mp4", _together(), 8.0, FPS) is not None
    assert talknet_windows == [25, 25]
    talknet_windows.clear()
    tracker.score_faces("clip.mp4", _together(), 8.0, FPS, window=26)
    assert talknet_windows == [26, 26]


def test_25_frames_is_under_talknet_s_minimum():
    # Why 26: the audio of N frames comes to N - 1 frames of MFCCs, and asd.score_track returns
    # zeros below asd.FPS of them.
    pytest.importorskip("python_speech_features")
    per_frame = asd.AUDIO_SR // asd.FPS
    assert len(asd.mfcc(np.zeros(25 * per_frame, np.float32))) // 4 < asd.FPS
    assert len(asd.mfcc(np.zeros(26 * per_frame, np.float32))) // 4 == asd.FPS


# ---- the stream tracker is unchanged --------------------------------------------------------


def test_with_the_defaults_score_faces_is_as_before(talknet_windows):
    # 8 face sightings and at most 4 people, as before; the podcast path asks for 3 and 4 a shot.
    assert tracker.score_faces("clip.mp4", _together(sightings=7), 8.0, FPS) is None
    assert tracker.score_faces("clip.mp4", _together(sightings=7), 8.0, FPS, min_faces=3) is not None
    talknet_windows.clear()
    cands, _, _ = tracker.score_faces("clip.mp4", _together(n_tracks=5), 8.0, FPS)
    assert len(cands) == 4 and talknet_windows == [25] * 4
    cands, _, _ = tracker.score_faces("clip.mp4", _together(n_tracks=5), 8.0, FPS, max_tracks=2)
    assert len(cands) == 2


def test_with_no_window_the_contested_spans_are_as_before():
    rng = np.random.default_rng(0)
    for _ in range(200):
        n = int(rng.integers(30, 400))
        mask = rng.random(n) < rng.random() * 0.2
        assert tracker._contested_spans(mask, 25, n) == tracker._contested_spans(mask, 25, n, None)
    mask = np.zeros(500, bool)
    mask[100:150] = mask[175:200] = True     # 26 frames apart
    assert tracker._contested_spans(mask, 25, 500) == [(100, 150), (175, 200)]
    assert tracker._contested_spans(mask, 25, 500, 26) == [(100, 200)]
    mask = np.zeros(500, bool)
    mask[100:113] = True
    assert tracker._contested_spans(mask, 25, 500) == [(94, 119)]
    assert tracker._contested_spans(mask, 25, 500, 26) == [(94, 120)]


def test_the_stream_tracker_asks_talknet_with_no_podcast_arguments(monkeypatch):
    asked = []

    def score_faces(*a, **kw):
        asked.append(kw)

    monkeypatch.setattr(asd, "available", lambda: True)
    monkeypatch.setattr(tracker, "score_faces", score_faces)
    samples = [SimpleNamespace(t=k / 8, faces={0: (0, 0, 10, 10)}) for k in range(10)]
    assert tracker._asd_speakers("clip.mp4", samples, FPS) is None
    assert asked == [{}]
