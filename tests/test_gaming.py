"""Gaming / Split-Screen: who the streamer is, and where each band comes from.

The two rules this pins down:
- TalkNet decides who the streamer is. A bigger face (a game character, a
  portrait, the person in a video being reacted to) never wins on size.
- The game band is a fixed crop that only moves to stay clear of the webcam
  or where the user put it. Nothing picks a region by how much it moves.
"""

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from gaming import detect  # noqa: E402

# ---- who the streamer is -------------------------------------------------------------


def _faces(n=250, loud=None, **logits):
    """{tid: Face} from constant (or given) TalkNet logits over n frames."""
    loud = np.ones(n, dtype=bool) if loud is None else loud
    faces = {}
    for tid, value in logits.items():
        scores = value if isinstance(value, np.ndarray) else np.full(n, value, dtype=np.float32)
        share, conf = detect.speaking(scores, loud)
        faces[tid] = detect.Face((0.0, 0.0, 0.2, 0.3), 1.0, share, conf, True)
    return faces


def test_the_speaking_face_wins_even_when_a_bigger_one_is_on_screen():
    # "big" is a game character filling the screen (silent), "cam" the small webcam face
    faces = _faces(big=-4.0, cam=0.5)
    faces["big"].box = (0.2, 0.0, 0.6, 1.0)
    assert detect.pick_streamer(faces) == "cam"
    assert faces["big"].speaking_share == 0.0


def test_a_lone_face_is_the_streamer_only_if_it_is_speaking():
    assert detect.pick_streamer(_faces(only=0.2)) == "only"
    assert detect.pick_streamer(_faces(only=-4.0)) is None


def test_when_two_faces_speak_the_most_confident_one_wins():
    """A watch party: the streamer and the person in the video both talk for
    the whole clip. TalkNet is far surer of the one in sync with the stream's
    audio (measured: 3.3 against 0.3), whichever comes first or is bigger."""
    assert detect.pick_streamer(_faces(video=0.3, streamer=3.3)) == "streamer"


def test_only_loud_moments_count():
    scores = np.full(250, -4.0, dtype=np.float32)
    scores[:50] = 1.0               # "speaking" only during silence
    loud = np.ones(250, dtype=bool)
    loud[:50] = False
    assert detect.pick_streamer(_faces(loud=loud, only=scores)) is None


def test_frames_a_face_is_off_screen_dont_count_against_it():
    scores = np.full(250, 0.4, dtype=np.float32)
    scores[:200] = -np.inf          # on screen for the last fifth only, speaking throughout
    assert detect.pick_streamer(_faces(only=scores)) == "only"


def _track(times, box=(100, 60, 380, 330), cx=0.12):
    tr = detect.Track()
    for i, t in enumerate(times):
        tr.times.append(t)
        tr.person.append(box)
        tr.seen.append((t, box))
        tr.head_cx.append(cx[i % len(cx)] if isinstance(cx, list) else cx)
    return tr


def test_a_face_seen_for_a_moment_is_not_a_candidate():
    """Measured in a GTA roleplay stream: a driver glimpsed through a car
    window for 3% of a clip got TalkNet's full speaking score. Only a face on
    screen for most of the clip can be the streamer."""
    tracks = {0: _track([i / 8 for i in range(300)]), 1: _track([i / 8 for i in range(10)])}
    assert detect.candidates(tracks, 320) == [0]


def test_a_webcam_face_split_into_two_tracks_is_one_person():
    first = _track([i / 8 for i in range(150)])
    second = _track([20 + i / 8 for i in range(150)])      # same spot, identity lost at 19 s
    elsewhere = _track([i / 8 for i in range(150)], box=(1400, 100, 1800, 900))
    merged = detect.merge_tracks({0: first, 1: second, 2: elsewhere})
    assert len(merged) == 2
    assert detect.candidates(merged, 320)[0] in merged and len(merged[detect.candidates(merged, 320)[0]].times) == 300


def test_two_people_side_by_side_are_not_merged():
    a = _track([i / 8 for i in range(150)])
    b = _track([i / 8 for i in range(150)])                # same box, but on screen together
    assert len(detect.merge_tracks({0: a, 1: b})) == 2


def test_a_speaker_inside_a_small_box_is_an_overlay():
    box, overlay = detect.webcam_box(_track(range(60), cx=[0.12, 0.13, 0.11, 0.15, 0.09]), 1920, 1080)
    assert overlay is True
    x, y, w, h = box
    assert 0 <= x < 0.06 and w < 0.2 and h < 0.4


def test_a_camera_filling_the_frame_is_not_an_overlay():
    assert detect.webcam_box(_track(range(60), box=(300, 50, 1700, 1080), cx=0.5), 1920, 1080)[1] is False
    roaming = _track(range(60), box=(100, 300, 300, 800), cx=[0.1, 0.5, 0.9, 0.3, 0.7])
    assert detect.webcam_box(roaming, 1920, 1080)[1] is False


def _frames(n=6, cam=(0, 372, 160, 540), seed=1):
    """960x540 greyscale stills: a noisy game, a flat chat panel right of a
    webcam in the bottom-left corner, the webcam's picture changing."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        f = rng.integers(40, 120, (540, 960)).astype(np.uint8)          # the game, never still
        f[372:, 160:800] = 20                                           # chat panel beside the webcam
        x1, y1, x2, y2 = cam
        f[y1:y2, x1:x2] = rng.integers(150, 230, (y2 - y1, x2 - x1))    # the webcam picture
        out.append(f)
    return out


def test_the_streamers_box_grows_to_just_inside_the_webcams_border():
    """Measured on a speedrun: a margin around the streamer ran past the
    webcam into the chat panel beside it, and the clip showed a strip of chat
    down the side of the webcam. Now the box grows from the streamer out to
    the webcam's own border, and stops just inside it."""
    streamer = (0.0, 0.72, 0.15, 0.28)                 # true webcam: x 0-160 of 960, y 372-540 of 540
    x, y, w, h = detect.snap_to_frame(streamer, _frames())
    inset = detect.SNAP_INSET
    assert x == 0.0 and y + h == pytest.approx(1.0)    # frame edges stay put
    assert 160 / 960 - inset - 0.003 <= x + w <= 160 / 960       # inside the chat border
    assert 372 / 540 <= y <= 372 / 540 + inset + 0.004           # inside the top border


def test_a_streamer_box_a_few_pixels_past_the_border_comes_back_inside():
    """Measured on a survival game: the person box ran 5 px below the webcam
    into the chat under it. A border that close inside is still the edge."""
    x, _y, w, _h = detect.snap_to_frame((0.0, 0.72, 163 / 960, 0.28), _frames())
    assert x + w <= 160 / 960


def test_nothing_beside_the_webcam_gets_into_its_box():
    """A chat panel's edge further out is stronger than the webcam's own
    border; the nearest border wins, so the chat never comes in."""
    frames = _frames()
    for f in frames:
        f[372:, 230:232] = 255                         # a bright line past the webcam, inside the chat
    x, _y, w, _h = detect.snap_to_frame((0.0, 0.72, 0.15, 0.28), frames)
    assert x + w <= 160 / 960


def test_a_line_inside_the_streamers_own_box_is_not_the_border():
    """Measured: a door frame behind the streamer pulled the box's edge in
    across them. Sides only ever move out from the streamer."""
    frames = _frames(cam=(0, 300, 400, 540))
    for f in frames:
        f[300:, 250:] = 240                            # a bright doorway in the room behind them
    box = (0.0, 0.54, 0.43, 0.46)                      # webcam 0-400, streamer's box well past 250
    x, _y, w, _h = detect.snap_to_frame(box, frames)
    assert x + w > 0.4


def test_a_box_with_no_clear_border_is_left_as_it_was():
    rng = np.random.default_rng(2)
    noise = [rng.integers(0, 255, (540, 960)).astype(np.uint8) for _ in range(6)]
    box = (0.3, 0.3, 0.2, 0.3)
    assert detect.snap_to_frame(box, noise) == box
    assert detect.snap_to_frame(box, []) == box


# ---- the webcam for the whole video ---------------------------------------------------


def _clip(streamer_box=None, confidence=2.0, others=()):
    faces = {}
    if streamer_box:
        faces["s"] = detect.Face(streamer_box, 0.9, 1.0, confidence, True)
    for i, box in enumerate(others):
        faces[f"o{i}"] = detect.Face(box, 0.9, 0.2, -2.0, True)
    return detect.ClipFinding(faces, "s" if streamer_box else None)


CORNER = (0.0, 0.66, 0.25, 0.34)


def test_in_a_reaction_the_streamer_is_who_speaks_from_the_same_spot_all_video():
    """Measured on a reaction stream: the person in the watched video out-talked
    the streamer in one clip. Across the video the streamer speaks from the
    same corner again and again; the people in the content come and go."""
    findings = [
        _clip((0.69, 0.3, 0.16, 0.68), others=[CORNER]),   # the video's subject wins this one
        _clip(CORNER),
        _clip((0.01, 0.65, 0.24, 0.35)),
    ]
    cam = detect.video_cam(findings)
    assert cam is not None and cam[0] < 0.05 and cam[1] > 0.6
    assert detect.on_screen(cam, findings[0])                 # the webcam is there in clip 1 too


def test_one_vote_among_several_clips_is_not_a_webcam():
    assert detect.video_cam([_clip((0.4, 0.4, 0.1, 0.4)), _clip(), _clip()]) is None


def test_a_single_clip_decides_for_itself():
    assert detect.video_cam([_clip(CORNER)]) == CORNER


def test_a_game_character_in_the_same_spot_is_not_a_webcam():
    """Measured on a 3D visual novel: a character sat at the same desk for two
    clips and lip-flapped to the voice acting (TalkNet -0.6 and -0.4). Real
    webcams reached 0.3 to 3.4 in at least one clip."""
    desk = (0.29, 0.31, 0.14, 0.39)
    assert detect.video_cam([_clip(desk, -0.58), _clip(desk, -0.44), _clip()]) is None
    assert detect.video_cam([_clip(CORNER, -0.5), _clip(CORNER, 0.3), _clip()]) is not None


def test_a_vtuber_avatar_is_never_the_webcam():
    """Gaming mode is for people on camera; VTubers are not supported. Measured
    on two VTuber streams: the avatar sat in the same corner in every clip and
    lip-synced to the voice, but TalkNet never got above -0.35 on it. It must
    stay that way: the game fills the screen."""
    avatar = (0.61, 0.5, 0.38, 0.5)
    assert detect.video_cam([_clip(avatar, -0.35), _clip(avatar, -0.73), _clip(avatar, -0.98)]) is None


def test_a_camera_filling_the_frame_never_votes_for_a_split():
    full = detect.ClipFinding({"s": detect.Face((0.2, 0.0, 0.6, 1.0), 1.0, 1.0, 2.0, False)}, "s")
    assert detect.video_cam([full, full]) is None


# ---- one clip's layout (gaming/run.py) ------------------------------------------------


@pytest.fixture
def run(monkeypatch):
    from core import modes
    from gaming import compose
    from gaming import run as run_mod

    plans = []
    monkeypatch.setattr(compose, "render", lambda _i, out, p, **_k: plans.append(p) or out)
    monkeypatch.setattr(modes, "probe_size", lambda _p: (1920, 1080))
    return run_mod, plans


CONFIG = {"clips": {}, "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8}}


def _seen(monkeypatch, tracks, finding=None):
    """Fake the clip's tracks (and TalkNet's verdict on them)."""
    monkeypatch.setattr(detect, "sample_tracks", lambda *_a, **_k: (tracks, 1920, 1080, 30.0, 40.0, 320))
    monkeypatch.setattr(detect, "speaking_scores", lambda *_a, **_k: "scored")
    monkeypatch.setattr(detect, "judge", lambda *_a, **_k: finding or detect.ClipFinding())


WEBCAM = _track([i / 8 for i in range(300)], box=(0, 720, 480, 1080), cx=0.12)


def test_a_box_the_user_drew_wins_without_any_detection(run, monkeypatch):
    run_mod, plans = run
    monkeypatch.setattr(detect, "sample_tracks", lambda *_a, **_k: pytest.fail("detection ran"))
    kept = run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": [0.7, 0.0, 0.3, 0.3], "by": "user"}, CONFIG)
    assert plans[-1].kind == "split" and kept["layout"] == "split"
    run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": None, "by": "user"}, CONFIG)
    assert plans[-1].kind == "fill"


def test_the_videos_webcam_is_used_when_somebody_is_at_it(run, monkeypatch):
    run_mod, plans = run
    _seen(monkeypatch, {0: WEBCAM})
    cam = list(detect.webcam_box(WEBCAM, 1920, 1080)[0])
    kept = run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": cam, "by": "video"}, CONFIG)
    assert plans[-1].kind == "split" and kept["used_cam"] == [round(v, 4) for v in cam]


def test_the_game_fills_the_screen_when_the_webcam_is_empty(run, monkeypatch):
    run_mod, plans = run
    _seen(monkeypatch, {})                         # a BRB screen, or nobody in the corner
    run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": [0.0, 0.6, 0.3, 0.4], "by": "video"}, CONFIG)
    assert plans[-1].kind == "fill"


def test_a_camera_filling_the_frame_goes_to_the_standard_renderer(run, monkeypatch):
    run_mod, plans = run
    full = _track([i / 8 for i in range(300)], box=(400, 50, 1500, 1080), cx=0.5)
    face = detect.Face((0.2, 0.0, 0.6, 1.0), 1.0, 1.0, 2.5, False)
    _seen(monkeypatch, {0: full}, detect.ClipFinding({0: face}, 0))
    assert run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": [0.0, 0.6, 0.3, 0.4], "by": "video"},
                          CONFIG) is None
    assert plans == []


def test_a_big_talking_character_is_not_handed_to_the_standard_renderer(run, monkeypatch):
    """A cutscene or a person in a video being reacted to: speaking, filling
    the frame, but not confidently a real person on camera. The game fills
    the screen rather than the standard tracker following that face."""
    run_mod, plans = run
    full = _track([i / 8 for i in range(300)], box=(400, 50, 1500, 1080), cx=0.5)
    face = detect.Face((0.2, 0.0, 0.6, 1.0), 1.0, 0.7, -0.65, False)
    _seen(monkeypatch, {0: full}, detect.ClipFinding({0: face}, 0))
    run_mod.render(Path("c.mp4"), Path("o.mp4"), {"cam": None, "by": "video"}, CONFIG)
    assert plans[-1].kind == "fill"


def test_a_clip_rerendered_on_its_own_uses_its_own_webcam(run, monkeypatch):
    run_mod, plans = run
    face = detect.Face((0.0, 0.62, 0.27, 0.38), 0.95, 0.8, 2.0, True)
    _seen(monkeypatch, {0: _track([i / 8 for i in range(300)], box=(0, 0, 10, 10))},
          detect.ClipFinding({0: face}, 0))
    run_mod.render(Path("c.mp4"), Path("o.mp4"), {}, CONFIG)
    assert plans[-1].kind == "split"


def test_the_video_is_searched_once_across_clips_spread_through_it(monkeypatch, tmp_path):
    from core.models import ClipCandidate
    from gaming import run as run_mod

    looked = []
    monkeypatch.setattr("video.cutter.cut_clip", lambda _s, c, out, **_k: looked.append(c.start))
    corner = detect.Face(CORNER, 0.9, 0.9, 2.0, True)
    monkeypatch.setattr(detect, "find_cam", lambda *_a, **_k: detect.ClipFinding({"s": corner}, "s"))
    clips = [ClipCandidate(start=s, end=s + 90, score=80) for s in (900, 100, 500, 2000, 1500, 3000)]
    g = run_mod.prepare(tmp_path / "src.mp4", clips, CONFIG, tmp_path)
    assert looked == [100, 900, 1500, 3000] and g == {"cam": list(CORNER), "by": "video", "panels": []}


def test_a_webcam_saved_for_the_creator_skips_the_search(monkeypatch, tmp_path):
    from gaming import run as run_mod

    monkeypatch.setattr(detect, "find_cam", lambda *_a, **_k: pytest.fail("searched"))
    saved = {"cam": [0.8, 0.0, 0.2, 0.3], "game_box": [0.0, 0.0, 0.8, 0.85], "cam_position": "bottom"}
    config = {**CONFIG, "clips": {"gaming_layout": saved}}
    assert run_mod.prepare(tmp_path / "s.mp4", [], config, tmp_path) == {**saved, "by": "creator", "panels": []}


def test_a_split_set_up_before_processing_is_used_as_set_up(monkeypatch, tmp_path):
    from gaming import run as run_mod

    monkeypatch.setattr(detect, "find_cam", lambda *_a, **_k: pytest.fail("searched"))
    given = {"cam": [0.0, 0.66, 0.25, 0.34], "game_fit": "fill", "by": "user", "panels": [[0.2, 0.85, 0.8, 0.15]]}
    config = {**CONFIG, "clips": {"gaming_layout": given}}
    assert run_mod.prepare(tmp_path / "s.mp4", [], config, tmp_path) == given     # its panels too


def test_find_it_automatically_still_keeps_the_rest_of_the_setup(monkeypatch, tmp_path):
    from core.models import ClipCandidate
    from gaming import run as run_mod

    monkeypatch.setattr("video.cutter.cut_clip", lambda *_a, **_k: None)
    corner = detect.Face(CORNER, 0.9, 0.9, 2.0, True)
    monkeypatch.setattr(detect, "find_cam", lambda *_a, **_k: detect.ClipFinding({"s": corner}, "s"))
    config = {**CONFIG, "clips": {"gaming_layout": {"game_box": [0.2, 0.0, 0.8, 0.8], "cam_position": "bottom",
                                                    "by": "user"}}}
    g = run_mod.prepare(tmp_path / "s.mp4", [ClipCandidate(start=0, end=30, score=80)], config, tmp_path)
    assert g["by"] == "video" and g["cam"] == list(CORNER)
    assert g["game_box"] == [0.2, 0.0, 0.8, 0.8] and g["cam_position"] == "bottom"


def test_a_creator_layout_is_trusted_like_one_drawn_for_the_clip(run, monkeypatch):
    run_mod, plans = run
    monkeypatch.setattr(detect, "sample_tracks", lambda *_a, **_k: pytest.fail("detection ran"))
    run_mod.render(Path("c.mp4"), Path("o.mp4"),
                   {"cam": [0.0, 0.66, 0.25, 0.34], "game_box": [0.17, 0.0, 0.83, 0.82], "by": "creator"}, CONFIG)
    p = plans[-1]
    assert p.kind == "split"
    gx, gy, gw, gh = p.element("game").src
    assert gx >= 0.17 * 1920 - 2 and gy + gh <= 0.82 * 1080 + 2   # nothing below the drawn game area


# ---- the pipeline ---------------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch):
    from core import pipeline as pipeline_mod
    from video import cropper, tracker

    ran = []
    monkeypatch.setattr(pipeline_mod, "cut_clip", lambda _s, _c, out, **_k: Path(out).write_bytes(b"clip"))
    monkeypatch.setattr(tracker, "compute_tracking", lambda *_a, **_k: {"mode": "track", "path": [(0.0, 0.5)]})
    monkeypatch.setattr(cropper, "render_vertical",
                        lambda _i, _t, out, **_k: ran.append("standard") or Path(out).write_bytes(b"std"))
    return pipeline_mod, ran


def _render_clip(pipeline_mod, tmp_path, opts=None, **clips):
    import json

    from core.models import ClipCandidate

    config = {"clips": {"captions": False, "outro": False, "vertical": True, **clips},
              "paths": {"data_dir": str(tmp_path)}, "tracking": CONFIG["tracking"]}
    final, opts_json = pipeline_mod._render_files(tmp_path / "s.mp4", ClipCandidate(start=10, end=40, score=80),
                                                  [], tmp_path / "clips", config, opts)
    return final, json.loads(opts_json) if opts_json else {}


def test_with_the_toggle_off_the_gaming_code_is_never_called(pipeline, monkeypatch, tmp_path):
    pipeline_mod, ran = pipeline
    monkeypatch.setattr(pipeline_mod, "_try_gaming_render", lambda *_a, **_k: pytest.fail("gaming ran"))
    _final, opts = _render_clip(pipeline_mod, tmp_path)
    assert ran == ["standard"] and "gaming" not in opts


def test_with_the_toggle_on_the_clip_renders_in_the_gaming_layout(pipeline, monkeypatch, tmp_path):
    from gaming import run as run_mod

    pipeline_mod, ran = pipeline

    def render(_i, out, g, _config, **_k):
        Path(out).write_bytes(b"gaming")
        return {**g, "layout": "split"}

    monkeypatch.setattr(run_mod, "render", render)
    final, opts = _render_clip(pipeline_mod, tmp_path, {"gaming": {"cam": [0, 0.6, 0.3, 0.4], "by": "video"}},
                               gaming=True)
    assert ran == [] and final.read_bytes() == b"gaming"
    assert opts["gaming"]["layout"] == "split"          # kept for editor re-renders


@pytest.mark.parametrize("outcome", ["error", "declined"])
def test_anything_gaming_cant_do_falls_back_to_the_standard_layout(pipeline, monkeypatch, tmp_path, outcome):
    from gaming import run as run_mod

    pipeline_mod, ran = pipeline

    def render(*_a, **_k):
        if outcome == "error":
            raise RuntimeError("boom")
        return None                                     # a camera filling the frame

    monkeypatch.setattr(run_mod, "render", render)
    final, _opts = _render_clip(pipeline_mod, tmp_path, gaming=True)
    assert ran == ["standard"] and final.exists()


def test_vertical_live_and_podcast_take_precedence_over_gaming(pipeline, monkeypatch, tmp_path):
    import video.podcast as podcast
    from core import modes

    pipeline_mod, _ran = pipeline
    monkeypatch.setattr(pipeline_mod, "_try_gaming_render", lambda *_a, **_k: pytest.fail("gaming ran"))
    monkeypatch.setattr(podcast, "analyze", lambda *_a, **_k: {"mode": "track", "path": [(0.0, 0.5)]})
    monkeypatch.setattr(podcast, "render_clip", lambda _i, out, *_a, **_k: Path(out).write_bytes(b"p"))
    _render_clip(pipeline_mod, tmp_path, gaming=True, podcast=True)
    monkeypatch.setattr(modes, "probe_size", lambda _p: (1080, 1920))
    _render_clip(pipeline_mod, tmp_path, gaming=True, vertical_live=True)


def test_a_failed_webcam_search_still_lets_every_clip_render(monkeypatch, tmp_path):
    from core import pipeline as pipeline_mod
    from gaming import run as run_mod

    def broken(*_a, **_k):
        raise RuntimeError("no GPU")

    monkeypatch.setattr(run_mod, "prepare", broken)
    assert pipeline_mod._gaming_prepare(tmp_path / "s.mp4", [], tmp_path, CONFIG) == {"gaming": {}}


def test_a_rerun_records_the_split_its_new_file_was_rendered_with(db, tmp_path):
    """A re-run keeps a clip's title and description, but its split must be
    the one the new file has, or the editor starts from a stale one (measured:
    a clip kept a padded webcam box its file no longer had)."""
    import json

    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate
    from core.pipeline import _register_clip

    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('v', 'Stream', 'done', 'x', 'x')")
    db.conn.commit()
    clip = ClipCandidate(start=10.0, end=40.0, score=80, hook="h")
    meta = ClipMetadata(title="Kept title", description="", hashtags=[])
    old = {"gaming": {"cam": [0.0, 0.69, 0.179, 0.31], "by": "video"}, "caption_style": {"font": "Arial"}}
    _register_clip(db, "v", clip, tmp_path / "a.mp4", meta, json.dumps(old))
    new = {"gaming": {"cam": [0.0, 0.69, 0.163, 0.31], "by": "video", "layout": "split"}}
    _register_clip(db, "v", clip, tmp_path / "b.mp4", ClipMetadata(title="New", description="", hashtags=[]),
                   json.dumps(new))
    row = db.conn.execute("SELECT title, render_opts FROM clips WHERE video_id = 'v'").fetchone()
    opts = json.loads(row["render_opts"])
    assert row["title"] == "Kept title"
    assert opts["gaming"] == new["gaming"] and opts["caption_style"] == {"font": "Arial"}


# ---- the layout editor's starting webcam ------------------------------------------------


def _suggest(tmp_path, monkeypatch, detections, framed=True):
    """suggest_cam on five 960x540 frames, the person detector faked to see
    `detections[i]` (pixel boxes) on frame i."""
    import cv2

    paths = []
    for i in range(5):
        f = np.random.default_rng(i).integers(40, 120, (540, 960, 3)).astype(np.uint8)   # the game, changing
        if framed:
            f[372:, :160] = 200                                  # a webcam overlay with its own edges
            f[372:374, :162] = 255
            f[372:, 160:162] = 255
        path = tmp_path / f"f{i}.png"
        cv2.imwrite(str(path), f)
        paths.append(path)
    calls = iter(detections)
    monkeypatch.setattr(detect, "_get_model", lambda _name: None)
    monkeypatch.setattr(detect, "_detect", lambda _m, _f, _c: [(*b, 0.9, None) for b in next(calls)])
    return detect.suggest_cam(paths)


def test_the_same_person_in_a_framed_webcam_across_the_video_is_suggested(tmp_path, monkeypatch):
    got = _suggest(tmp_path, monkeypatch, [[(20, 390, 140, 540)]] * 5)
    assert got is not None
    x, y, w, _h = got
    assert abs(y - 372 / 540) < 0.01 and abs((x + w) - 160 / 960) < 0.01    # its top and right borders


def test_a_figure_with_no_frame_round_it_is_not_suggested(tmp_path, monkeypatch):
    """Measured: VTuber avatars stand in one place too, but with no overlay
    border round them. VTubers aren't supported, so nothing is suggested."""
    assert _suggest(tmp_path, monkeypatch, [[(20, 390, 140, 540)]] * 5, framed=False) is None


def test_someone_who_moves_between_frames_is_not_suggested(tmp_path, monkeypatch):
    moving = [[(20 + 150 * i, 100, 140 + 150 * i, 300)] for i in range(5)]      # a game character
    assert _suggest(tmp_path, monkeypatch, moving) is None


def test_a_camera_filling_the_frame_is_not_suggested_as_a_webcam(tmp_path, monkeypatch):
    assert _suggest(tmp_path, monkeypatch, [[(100, 0, 860, 540)]] * 5) is None
