"""Clips must land inside the requested duration range. Always.

`_fit_to_segments` snaps clips to sentence boundaries so they don't start or
stop mid-thought. That is a preference. The duration range is a promise, and
these are the two cases where the promise used to lose:

  * A lone short segment surrounded by silence. Growing means swallowing the
    silence and breaking max_duration, so the old code gave up and returned
    the bare segment — half a second.
  * One enormous segment. Whisper emits a single 150s segment for a long
    unpunctuated stretch, and the trailing-trim loop needs `hi > lo` to run,
    so with one segment it could not.

Both shipped from a real gym stream: 0.52s, 0.62s and 93.89s clips. Sparse
speech is the trigger, which makes workout, gameplay and music streams the
natural habitat — and signal-sourced candidates (an action moment nobody
narrated) the natural source.
"""

from analysis.highlights import _fit_to_segments
from core.models import ClipCandidate, Segment

MIN, MAX = 10.0, 60.0


def fit(candidate, segments, lo=MIN, hi=MAX):
    return _fit_to_segments(candidate, segments, lo, hi)


def duration(clip) -> float:
    """Length at the precision that actually reaches FFmpeg.

    Boundaries are stored rounded to 2dp, and neither 9.35 nor 70.35 is
    exactly representable in binary — their difference comes out as
    60.99999999999999. Rounding here compares what the renderer is really
    handed, so a 1e-14 artefact cannot fail a test while a real 9.9s clip
    still does.
    """
    return round(clip.end - clip.start, 2)


def test_lone_segment_before_a_long_silence_is_padded():
    """The real one: "Yo." at 9.35-9.87, nothing again until 74.26.

    Reaching the next segment would make a 66s clip, over the cap, and there
    is nothing behind index 0 to grow into. This used to ship 0.52 seconds.
    """
    segments = [
        Segment(start=9.35, end=9.87, text="Yo."),
        Segment(start=74.26, end=75.44, text="That's all I gotta say."),
        Segment(start=75.74, end=77.22, text="There's water in the pool."),
    ]
    out = fit(ClipCandidate(start=9.35, end=9.87, score=50, source="signal"), segments)

    assert duration(out) >= MIN
    # Padding goes forward: the moment plays out after its peak, and there is
    # no video behind 9.35 to take.
    assert out.start == 9.35


def test_silent_moment_deep_in_the_video_is_padded():
    """Same failure with silence on BOTH sides — clip 1897, "Veins are
    popping." at 2517.24, with 84s of quiet before it."""
    segments = [
        Segment(start=2432.92, end=2433.80, text="Can I flex my ass real quick?"),
        Segment(start=2517.24, end=2517.86, text="Veins are popping."),
    ]
    out = fit(ClipCandidate(start=2517.24, end=2517.86, score=50, source="signal"), segments)

    assert duration(out) >= MIN


def test_one_enormous_segment_is_cut_to_the_cap():
    """Whisper hands back 153 seconds as a single segment when speech runs on
    without punctuation. With lo == hi there is no trailing segment to trim,
    so the whole thing used to ship as one clip."""
    segments = [Segment(start=327.08, end=480.20, text="oh you add some weight all right")]
    out = fit(ClipCandidate(start=327.08, end=480.20, score=50, source="signal"), segments)

    assert duration(out) <= MAX


def test_the_cap_keeps_the_moment_that_was_detected():
    """Cutting a 153s segment to 60s must not cut away the interesting part.

    The candidate marks where the peak actually was; the giant segment does
    not know. So the kept window has to contain it.
    """
    segments = [Segment(start=0.0, end=153.0, text="one very long unpunctuated stretch")]
    peak = ClipCandidate(start=120.0, end=125.0, score=50, source="signal")
    out = fit(peak, segments)

    assert duration(out) <= MAX
    assert out.start <= 120.0 and out.end >= 125.0


def test_a_normal_clip_is_left_alone():
    """The fallbacks are last resorts. Ordinary speech still snaps to
    sentences rather than being squared off to exactly min_duration."""
    segments = [
        Segment(start=0.0, end=6.0, text="So here is the thing about that."),
        Segment(start=6.0, end=13.0, text="It took me three years to work out."),
        Segment(start=13.0, end=21.0, text="And I still get it wrong sometimes."),
        Segment(start=21.0, end=29.0, text="Anyway, that is the story."),
    ]
    out = fit(ClipCandidate(start=0.0, end=21.0, score=80), segments)

    assert MIN <= duration(out) <= MAX
    # Landed on real segment edges, not on a clock-time fallback.
    assert out.start in {s.start for s in segments}
    assert out.end in {s.end for s in segments}


def test_long_clip_mode_uses_its_own_range():
    """61-180s mode (TikTok monetisation) must not inherit the 10s floor."""
    segments = [
        Segment(start=9.35, end=9.87, text="Yo."),
        Segment(start=200.0, end=201.0, text="Much later."),
    ]
    out = fit(ClipCandidate(start=9.35, end=9.87, score=50, source="signal"), segments, 61.0, 180.0)

    assert duration(out) >= 61.0


def test_video_shorter_than_the_floor_is_not_invented():
    """Never pad past the end of the video to satisfy the floor — that would
    ask the renderer for footage that does not exist."""
    segments = [Segment(start=0.0, end=4.0, text="Very short video.")]
    out = fit(ClipCandidate(start=0.0, end=4.0, score=50, source="signal"), segments)

    assert out.start >= 0.0
    assert out.end <= 4.0


def test_range_holds_for_every_single_segment():
    """Whatever the peak lands on, the result is in range. Mixed gaps and
    segment lengths, mirroring how a sparse-speech stream actually looks."""
    segments = [
        Segment(start=0.0, end=0.5, text="Yo."),
        Segment(start=90.0, end=91.2, text="Back again."),
        Segment(start=91.5, end=140.0, text="A long unpunctuated run of talking"),
        Segment(start=300.0, end=302.0, text="Much later on."),
        Segment(start=302.0, end=460.0, text="An enormous single segment"),
    ]
    for seg in segments:
        out = fit(ClipCandidate(start=seg.start, end=seg.end, score=50, source="signal"), segments)
        d = duration(out)
        assert MIN <= d <= MAX, f"segment at {seg.start}s produced {d:.2f}s"
        assert out.start < out.end
