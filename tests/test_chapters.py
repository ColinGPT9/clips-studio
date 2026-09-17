"""Chapter timestamps: correct offsets, YouTube's rules, or nothing at all.

Pure functions over ranges and candidates, so these run in CI without video,
torch or numpy.
"""

from dataclasses import dataclass

from longform import chapters


@dataclass
class _Candidate:
    start: float
    end: float
    score: int
    hook: str = ""


def _moments(*specs) -> list[_Candidate]:
    return [_Candidate(*spec) for spec in specs]


def test_timestamps_count_the_finished_video_not_the_stream():
    # Ranges taken from all over a stream, but a viewer scrubs the assembled
    # video, where the second chapter starts as soon as the first ends.
    keep = [(1000.0, 1060.0), (2000.0, 2090.0), (3000.0, 3030.0)]
    cands = _moments((1000.0, 1060.0, 90, "first thing"), (2000.0, 2090.0, 80, "second thing"),
                     (3000.0, 3030.0, 70, "third thing"))
    assert chapters.chapter_lines(keep, cands) == [
        "0:00 first thing",
        "1:00 second thing",
        "2:30 third thing",
    ]


def test_the_first_chapter_is_always_zero():
    keep = [(500.0, 530.0), (600.0, 640.0), (700.0, 730.0)]
    lines = chapters.chapter_lines(keep, _moments((500.0, 530.0, 90, "a"), (600.0, 640.0, 80, "b"),
                                                 (700.0, 730.0, 70, "c")))
    assert lines[0].startswith("0:00 ")


def test_past_an_hour_the_stamp_grows_an_hour_field():
    assert chapters._timestamp(0) == "0:00"
    assert chapters._timestamp(59.9) == "0:59"
    assert chapters._timestamp(3600) == "1:00:00"
    assert chapters._timestamp(3753) == "1:02:33"


def test_fewer_than_three_chapters_produces_none():
    # YouTube ignores the whole list below three, so emitting two would look
    # right in the description and do nothing on the video.
    keep = [(0.0, 60.0), (100.0, 160.0)]
    assert chapters.chapter_lines(keep, _moments((0.0, 60.0, 90, "a"), (100.0, 160.0, 80, "b"))) == []


def test_a_chapter_under_ten_seconds_voids_the_list():
    keep = [(0.0, 60.0), (100.0, 104.0), (200.0, 260.0)]
    cands = _moments((0.0, 60.0, 90, "a"), (100.0, 104.0, 80, "b"), (200.0, 260.0, 70, "c"))
    assert chapters.chapter_lines(keep, cands) == []


def test_a_merged_range_is_named_after_its_strongest_moment():
    # select_highlights merges picks closer than MERGE_GAP, so one range can
    # hold several moments. The best one names it.
    keep = [(0.0, 90.0), (200.0, 240.0), (300.0, 340.0)]
    cands = _moments(
        (0.0, 40.0, 60, "the quiet setup"),
        (42.0, 90.0, 95, "the punchline everyone clipped"),
        (200.0, 240.0, 70, "later on"),
        (300.0, 340.0, 65, "the end"),
    )
    assert chapters.chapter_lines(keep, cands)[0] == "0:00 the punchline everyone clipped"


def test_a_moment_with_no_hook_still_gets_a_usable_chapter():
    keep = [(0.0, 60.0), (100.0, 160.0), (200.0, 260.0)]
    cands = _moments((0.0, 60.0, 90, ""), (100.0, 160.0, 80, "named"), (200.0, 260.0, 70, ""))
    assert chapters.chapter_lines(keep, cands) == ["0:00 Moment 1", "1:00 named", "2:00 Moment 3"]


def test_a_long_hook_is_trimmed_to_a_label():
    hook = "a hook that simply will not stop going on and on and on about what happened next " * 2
    keep = [(0.0, 60.0), (100.0, 160.0), (200.0, 260.0)]
    cands = _moments((0.0, 60.0, 90, hook), (100.0, 160.0, 80, "b"), (200.0, 260.0, 70, "c"))
    first = chapters.chapter_lines(keep, cands)[0]
    assert len(first) <= len("0:00 ") + chapters.MAX_TITLE
    assert first.endswith("…")


def test_hooks_spanning_lines_become_one_line():
    # A description line break inside a chapter title would split the list.
    keep = [(0.0, 60.0), (100.0, 160.0), (200.0, 260.0)]
    cands = _moments((0.0, 60.0, 90, "two\nlines  here"), (100.0, 160.0, 80, "b"),
                     (200.0, 260.0, 70, "c"))
    assert chapters.chapter_lines(keep, cands)[0] == "0:00 two lines here"


def test_with_chapters_appends_under_a_heading():
    keep = [(0.0, 60.0), (100.0, 160.0), (200.0, 260.0)]
    cands = _moments((0.0, 60.0, 90, "a"), (100.0, 160.0, 80, "b"), (200.0, 260.0, 70, "c"))
    out = chapters.with_chapters("The best 3 minutes of the stream.", keep, cands)
    assert out.startswith("The best 3 minutes of the stream.\n\nChapters:\n0:00 a")


def test_with_chapters_leaves_a_description_alone_when_there_are_none():
    original = "Full stream with 40 minutes of downtime removed."
    assert chapters.with_chapters(original, [(0.0, 60.0)], []) == original
