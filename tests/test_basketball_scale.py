"""A game camera closer than the national broadcasts the basketball framing was set on
(reactions.people_scale > 1) shows the ball bigger and moving farther across the frame.
E-BARD's input size and the ball gates follow it; at scale 1 nothing changes."""

import pytest

from sports.basketball.crop import ball, sources
from sports.basketball.crop.model import BallCand, Sample


@pytest.mark.parametrize("scale, width, size", [
    (1.0, 1920, 960),      # the national broadcasts: as before
    (0.8, 1920, 960),      # never larger than before on a 1080p video
    (1.79, 1920, 640),     # a team's own network
    (1.2, 1920, 960),      # up to 1.2, as before
    (2.5, 1920, 512),      # no smaller than EBARD_IMGSZ_MIN
    (1.26, 1280, 1280),    # a 720p video keeps its own size
    (1.0, 854, 864),       # a 480p one too, in steps of 32
])
def test_the_input_size_follows_the_camera(scale, width, size):
    assert sources.ebard_imgsz_for(scale, width) == size


def test_the_detectors_pick_it_unless_the_config_sets_one(monkeypatch):
    made = []

    class Stub:
        def __init__(self, *a, imgsz=0, **k):
            self.imgsz = imgsz
            made.append(imgsz)

    monkeypatch.setattr(sources, "ebard_weights", lambda cfg: "weights.pt")
    monkeypatch.setattr(sources, "EbardSource", Stub)
    monkeypatch.setattr(sources, "CocoSource", Stub)
    cfg = {"ball": "ebard", "hoop": "ebard", "people": "ebard"}
    assert sources.Detectors(cfg, scale=1.79, width=1920).imgsz == 640
    assert sources.Detectors({**cfg, "ebard_imgsz": 704}, scale=1.79, width=1920).imgsz == 704
    assert sources.Detectors(cfg).imgsz == 960


def _at(k: float):
    return ball._SCALE.set(k)


def test_a_fast_pass_on_a_close_camera_still_links():
    # 0.8 frame heights in 0.2 s: past a national broadcast's reach, a pass on a camera 1.8x closer.
    a, b = [(0.0, 0.0, 0.5)], [(0.2, 0.8, 0.5)]
    assert ball._link(a, b, None, None)[0] == "restart"
    token = _at(1.8)
    try:
        assert ball._link(a, b, None, None)[0] == "bridge"
    finally:
        ball._SCALE.reset(token)


def test_a_low_dribble_on_a_close_camera_is_not_taken_for_the_front_row():
    def low_ball():
        s = Sample(i=0, frame=0, t=0.0)
        s.balls = [BallCand(x=0.5, y=0.85, w=0.03, h=0.05, conf=0.6, src="ebard", orange=0.8)]
        return s

    ctx = {"wide": True, "player_h": None, "static": {}, "fans": {}}
    assert "f" in ball.gate(low_ball(), ctx)[0].rejected
    token = _at(1.8)
    try:
        assert "f" not in ball.gate(low_ball(), ctx)[0].rejected
    finally:
        ball._SCALE.reset(token)


def test_track_shot_takes_the_scale_from_its_samples_and_puts_it_back():
    seen = []
    real = ball._track_shot
    try:
        ball._track_shot = lambda samples, ctx, debug: seen.append(ball._SCALE.get()) or []
        ball.track_shot([Sample(i=0, frame=0, t=0.0, scale=1.79)])
        ball.track_shot([Sample(i=0, frame=0, t=0.0)])
    finally:
        ball._track_shot = real
    assert seen == [1.79, 1.0] and ball._SCALE.get() == 1.0


def test_the_far_sides_players_stay_on_the_floor_on_a_close_camera():
    from sports.basketball.crop import players
    from sports.basketball.crop.model import Person

    def sample(scale):
        s = Sample(i=0, frame=0, t=0.0, scale=scale)
        # Near players' feet at 0.95; a far-side player's at 0.62, 0.33 higher.
        s.people = [Person(x=0.2 + 0.1 * n, y=0.95 - 0.15, w=0.05, h=0.3) for n in range(4)]
        s.people.append(Person(x=0.7, y=0.62 - 0.1, w=0.04, h=0.2))
        return s

    assert len(players.floor_players(sample(1.0))) == 4           # a national broadcast: the stands
    assert len(players.floor_players(sample(1.8))) == 5           # a close camera: still the floor


def test_the_picture_sits_between_the_apps_top_bar_and_their_caption():
    from sports.basketball import action
    from sports.basketball.crop.model import Geometry, Plan

    # The tallest top bar and the tallest caption of TikTok, Reels and Shorts (gaming/layouts.json "all").
    assert action.bands({}) == (pytest.approx(250 / 1920, abs=1e-4), pytest.approx(420 / 1920, abs=1e-4))
    share = action.picture_share({})
    assert share == pytest.approx(1 - (250 + 420) / 1920, abs=1e-4)
    # The Sports row's Crop: Full screen, and the gaming layouts' "none", fill the screen.
    assert action.bands({"screen": "full"}) == (0.0, 0.0) and action.picture_share({"screen": "full"}) == 1.0
    assert action.picture_share({"safe_zone": "none"}) == 1.0
    g = Geometry(width=1920, height=1080, fps=30.0, frames=30)
    nine16 = g.base
    g.picture = share
    assert g.base == pytest.approx(nine16 / share) and g.levels()[0] == pytest.approx(g.base, abs=1e-6)
    planned = Plan(fps=30.0, base=g.base, cx=[0.5], w=[g.base], band_top=0.1302, band_bottom=0.2188).to_tracking()
    assert planned["band_top"] == 0.1302 and planned["band_bottom"] == 0.2188
    assert "band_top" not in Plan(fps=30.0, base=nine16, cx=[0.5], w=[nine16]).to_tracking()
    assert "band_bottom" not in Plan(fps=30.0, base=nine16, cx=[0.5], w=[nine16]).to_tracking()


def test_the_crop_choice_is_checked_and_only_full_screen_is_written():
    import sports

    assert "screen" not in sports.clean({"name": "basketball"})
    assert "screen" not in sports.clean({"name": "basketball", "screen": "safe"})
    assert sports.clean({"name": "basketball", "screen": "full"})["screen"] == "full"
    with pytest.raises(ValueError, match="screen must be one of"):
        sports.clean({"name": "basketball", "screen": "letterbox"})
    # Only basketball offers it: soccer's choices stay as they were.
    offered = {s["id"]: s for s in sports.available()}
    assert [c["id"] for c in offered["basketball"]["screens"]] == ["safe", "full"]
    assert "screens" not in offered["soccer"]
    assert "screen" not in sports.clean({"name": "soccer", "screen": "full"})


def test_a_close_up_is_centred_on_the_face_when_the_pose_model_found_it():
    from sports.basketball.crop import planner
    from sports.basketball.crop.model import Person, Shot

    def close_up(head_x):
        samples = []
        for k in range(10):
            s = Sample(i=k, frame=k * 3, t=k * 0.1)
            # A player leaning in: his box's middle at 0.45, his face at 0.6.
            s.people = [Person(x=0.45, y=0.5, w=0.3, h=0.9, head_x=head_x)]
            samples.append(s)
        data = planner.ShotData(shot=Shot(id=0, f0=0, f1=30, t0=0.0, t1=1.0, kind="closeup"), samples=samples)
        cx, _w = planner._person_shot(data, 30.0, [0.36])
        return cx[15]

    assert close_up(0.6) == pytest.approx(0.6, abs=0.01)
    assert close_up(None) == pytest.approx(0.45, abs=0.01)      # no face found: his box, as before


def test_pose_heads_go_to_the_person_box_they_overlap():
    from sports.basketball.crop.model import Person

    np = pytest.importorskip("numpy")

    class Kp:
        def __init__(self):
            # One person 0.4-0.7 wide in a 100x100 frame: nose and eyes at x 60, ears unseen.
            self.xy = Arr(np.array([[[60, 20], [58, 18], [62, 18], [0, 0], [0, 0]] + [[0, 0]] * 12], float))
            self.conf = Arr(np.array([[0.9, 0.9, 0.9, 0.1, 0.1] + [0.0] * 12]))

    class Arr:
        def __init__(self, a):
            self.a = a

        def cpu(self):
            return self

        def numpy(self):
            return self.a

    class Boxes:
        xyxy = Arr(np.array([[40.0, 10.0, 70.0, 100.0]]))

        def __len__(self):
            return 1

    class Result:
        keypoints, boxes = Kp(), Boxes()

    class Model:
        def predict(self, *a, **k):
            return [Result()]

    heads = sources.PoseHeads.__new__(sources.PoseHeads)
    heads.model, heads.imgsz = Model(), 640
    people = [Person(x=0.55, y=0.55, w=0.3, h=0.9), Person(x=0.1, y=0.5, w=0.1, h=0.3)]
    heads.fill(np.zeros((100, 100, 3), np.uint8), people)
    assert people[0].head_x == pytest.approx(0.6) and people[1].head_x is None


def test_a_close_up_keeps_its_subject_and_holds_still():
    from sports.basketball.crop import planner
    from sports.basketball.crop.model import Person, Shot

    samples = []
    for k in range(20):
        s = Sample(i=k, frame=k * 3, t=k * 0.1)
        s.people = [Person(x=0.4 + 0.01 * (k % 3), y=0.5, w=0.3, h=0.95, head_x=0.4 + 0.01 * (k % 3))]
        if k in (5, 6, 12):   # someone leaning in, bigger, at the other side
            s.people.append(Person(x=0.8, y=0.5, w=0.4, h=1.0, head_x=0.82))
        samples.append(s)
    data = planner.ShotData(shot=Shot(id=0, f0=0, f1=60, t0=0.0, t1=2.0, kind="closeup"), samples=samples)
    cx, _w = planner._person_shot(data, 30.0, [0.36])
    assert max(cx) - min(cx) < 1e-9 and cx[0] == pytest.approx(0.41, abs=0.011)
