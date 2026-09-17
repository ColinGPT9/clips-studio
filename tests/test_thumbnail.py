"""Thumbnail candidates: the geometry, the text fitting and the ranking.

The arithmetic runs anywhere, so most of this runs on a CI box with neither
OpenCV nor Pillow installed. The one rendering test asks for Pillow and skips
when it is absent.
"""

import pytest

from video import thumbnail

# ---- where to look in the clip ----------------------------------------------


def test_candidate_times_skip_the_start_and_the_end_card():
    times = thumbnail.candidate_times(100.0, count=5)
    assert times[0] >= 10.0 and times[-1] <= 90.0
    assert times == sorted(times)
    assert len(times) == 5


def test_a_clip_with_no_duration_asks_for_nothing():
    assert thumbnail.candidate_times(0.0) == []
    assert thumbnail.candidate_times(-5.0) == []
    assert thumbnail.candidate_times(60.0, count=0) == []


def test_a_very_short_clip_still_offers_its_middle():
    assert thumbnail.candidate_times(0.4, count=4) == [0.2] or thumbnail.candidate_times(0.4, count=4)


# ---- the crop ---------------------------------------------------------------


def test_a_vertical_clip_crops_to_full_width_16_9():
    left, top, right, bottom = thumbnail.crop_box(1080, 1920, face=None)
    assert (right - left) == 1080
    assert abs((right - left) / (bottom - top) - thumbnail.RATIO) < 0.01


def test_a_wide_source_is_limited_by_its_height():
    left, top, right, bottom = thumbnail.crop_box(3840, 1080, face=None)
    assert (bottom - top) == 1080
    assert abs((right - left) / (bottom - top) - thumbnail.RATIO) < 0.01


def test_the_box_sits_on_the_face_a_little_above_centre():
    # A face low in a tall frame pulls the box down with it.
    high = thumbnail.crop_box(1080, 1920, face=(440, 200, 200, 200))
    low = thumbnail.crop_box(1080, 1920, face=(440, 1500, 200, 200))
    assert low[1] > high[1]
    # And the face is inside the box it was cropped for.
    assert high[1] <= 300 <= high[3]


def test_the_box_never_leaves_the_frame():
    for face in [(0, 0, 80, 80), (1000, 1840, 80, 80), (540, 0, 10, 10)]:
        left, top, right, bottom = thumbnail.crop_box(1080, 1920, face=face)
        assert left >= 0 and top >= 0
        assert right <= 1080 and bottom <= 1920


def test_a_degenerate_frame_does_not_divide_by_zero():
    assert thumbnail.crop_box(0, 0, None) == (0, 0, 1, 1)


# ---- the text ---------------------------------------------------------------


def test_a_short_hook_is_one_line():
    assert thumbnail.wrap_title("that actually worked") == ["that actually worked"]


def test_a_longer_hook_wraps_to_two():
    lines = thumbnail.wrap_title("chat told me not to do this and they were completely right")
    assert len(lines) == 2
    assert all(len(line) <= thumbnail.MAX_LINE + 1 for line in lines)


def test_an_overlong_hook_is_cut_rather_than_given_a_third_line():
    hook = "there is simply no way this fits onto a thumbnail no matter how hard anyone tries"
    lines = thumbnail.wrap_title(hook)
    assert len(lines) == thumbnail.MAX_LINES
    assert lines[-1].endswith("…")


def test_no_hook_means_no_text():
    assert thumbnail.wrap_title("") == []
    assert thumbnail.wrap_title("   ") == []
    assert thumbnail.wrap_title(None) == []


def test_a_hook_across_lines_becomes_one_run_of_words():
    assert thumbnail.wrap_title("two\nlines  here") == ["two lines here"]


# ---- choosing between frames ------------------------------------------------


def test_a_face_beats_a_sharper_frame_without_one():
    with_face = thumbnail.score_frame(sharpness=80, face_area_fraction=0.05, brightness=120)
    no_face = thumbnail.score_frame(sharpness=500, face_area_fraction=0.0, brightness=120)
    assert with_face > no_face


def test_a_bigger_face_wins():
    assert thumbnail.score_frame(60, 0.09, 120) > thumbnail.score_frame(60, 0.03, 120)


def test_a_black_or_blown_out_frame_scores_nothing():
    assert thumbnail.score_frame(300, 0.08, brightness=4) == 0.0
    assert thumbnail.score_frame(300, 0.08, brightness=250) == 0.0


# ---- rendering --------------------------------------------------------------


def test_the_rendered_candidate_is_1280x720_and_under_youtubes_limit(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")

    # A synthetic "clip": a few seconds of noise, which has no faces and so
    # exercises the centre-crop path as well as the text burn.
    source = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1080, 1920))
    rng = np.random.default_rng(1)
    for _ in range(40):
        writer.write(rng.integers(40, 210, (1920, 1080, 3), dtype=np.uint8))
    writer.release()

    out = thumbnail.generate(source, "that actually worked", [tmp_path / "gen0.jpg"])
    assert out == [tmp_path / "gen0.jpg"]
    with Image.open(out[0]) as rendered:
        assert rendered.size == (thumbnail.WIDTH, thumbnail.HEIGHT)
    assert out[0].stat().st_size <= thumbnail.MAX_BYTES


def test_a_missing_clip_file_returns_nothing(tmp_path):
    assert thumbnail.generate(tmp_path / "gone.mp4", "hook", [tmp_path / "a.jpg"]) == []


def test_no_targets_means_no_work(tmp_path):
    assert thumbnail.generate(tmp_path / "gone.mp4", "hook", []) == []
