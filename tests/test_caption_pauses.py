"""Captions clear in the quiet parts of a clip (#228).

Captions were cut from the transcript in fixed groups of words, each on
screen from its first word to its last. A pause inside a group kept the
caption up through the silence, and a short word whose end the transcript
stretched across a silence hid the pause altogether.

- A caption ends at a pause longer than PAUSE_SECONDS; the next word starts
  a new one, however few words the last one has.
- No word counts as spoken for longer than LONGEST_WORD_SECONDS, for finding
  pauses and for where a caption ends.
- A word the transcript timed with no length (or, with the second speaker's
  turns, too short to stand alone) never holds a caption up through a pause:
  alone before one it is on screen for ALONE_SECONDS.
- On every path that makes lines from the transcript: plain, with the second
  speaker's turns, a clip whose start or end moved, and the ASS file with
  the word highlight or karaoke.
- Lines a person saved keep their own times.
- A transcript with no pause that long and no word that long gives exactly
  the lines it always gave, and so does text without word timings.

Standard library only: these run in CI's light install.
"""

import re

from core.models import ClipCandidate, Segment
from video.captions import (
    ALONE_SECONDS,
    LONGEST_WORD_SECONDS,
    PAUSE_SECONDS,
    build_caption_lines,
    build_captions,
    refit_caption_lines,
)


def _words(*timed: tuple[float, float, str]) -> list[dict]:
    return [{"start": a, "end": b, "word": w} for a, b, w in timed]


def _segments(words: list[dict]) -> list[Segment]:
    return [Segment(start=words[0]["start"], end=words[-1]["end"], text="", words=words)]


def _clip(start: float, end: float) -> ClipCandidate:
    return ClipCandidate(start=start, end=end, score=0)


def _as_before(words: list[dict], clip: ClipCandidate, size: int) -> list[dict]:
    """The lines as they were made before #228: fixed groups, first word's
    start to last word's end."""
    inside = sorted((w for w in words if w["end"] > clip.start and w["start"] < clip.end), key=lambda w: w["start"])
    lines = []
    for i in range(0, len(inside), size):
        group = inside[i:i + size]
        start = max(0.0, group[0]["start"] - clip.start)
        end = min(clip.duration, group[-1]["end"] - clip.start)
        if end > start:
            lines.append({"start": round(start, 2), "end": round(end, 2), "text": " ".join(w["word"] for w in group)})
    return lines


def _silent(lines: list[dict], words: list[dict], clip: ClipCandidate) -> float:
    """Seconds a caption is on screen while no word is being said, a word
    counted for no longer than LONGEST_WORD_SECONDS."""
    said = sorted((max(0.0, w["start"] - clip.start),
                   min(clip.duration, w["end"] - clip.start, max(0.0, w["start"] - clip.start) + LONGEST_WORD_SECONDS))
                  for w in words)
    quiet = 0.0
    for line in lines:
        at = line["start"]
        for a, b in said:
            if b <= at or a >= line["end"]:
                continue
            quiet += max(0.0, a - at)
            at = max(at, b)
        quiet += max(0.0, line["end"] - at)
    return quiet


def _events(path) -> list[tuple[str, str, str]]:
    """Each Dialogue's start, end and text."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:"):
            fields = line[len("Dialogue: "):].split(",", 9)
            out.append((fields[1], fields[2], fields[9]))
    return out


# ---- a caption ends at a pause --------------------------------------------------------


def test_a_pause_ends_the_caption_and_the_next_word_starts_a_new_one():
    words = _words((0.0, 0.4, "so"), (0.4, 0.9, "yeah"), (3.0, 3.4, "right"), (3.4, 3.8, "that"),
                   (3.8, 4.2, "is"), (4.2, 4.6, "it"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 5.0), 3)

    # It was "so yeah right" from 0.0 to 3.4: up through 2.1 s of silence.
    assert lines == [
        {"start": 0.0, "end": 0.9, "text": "so yeah"},
        {"start": 3.0, "end": 4.2, "text": "right that is"},
        {"start": 4.2, "end": 4.6, "text": "it"},
    ]
    assert _silent(lines, words, _clip(0.0, 5.0)) == 0


def test_only_a_pause_longer_than_the_threshold_ends_a_caption():
    def texts(gap: float) -> list[str]:
        words = _words((0.0, 0.5, "a"), (0.5, 1.0, "b"), (1.0 + gap, 1.5 + gap, "c"), (1.5 + gap, 2.0 + gap, "d"))
        return [line["text"] for line in build_caption_lines(_segments(words), _clip(0.0, 9.0), 3)]

    assert texts(PAUSE_SECONDS - 0.01) == ["a b c", "d"]
    assert texts(PAUSE_SECONDS) == ["a b c", "d"]              # a pause of exactly that is not one yet
    assert texts(PAUSE_SECONDS + 0.01) == ["a b", "c d"]


def test_one_word_at_a_time_is_as_it_always_was():
    """A caption of one word never held a pause."""
    words = _words((0.0, 0.4, "so"), (0.4, 0.9, "yeah"), (3.0, 3.4, "right"), (5.2, 5.6, "ok"))

    assert build_caption_lines(_segments(words), _clip(0.0, 6.0), 1) == _as_before(words, _clip(0.0, 6.0), 1)


def test_every_word_is_still_in_a_caption_in_the_order_it_was_said():
    words, t = [], 0.0
    for i in range(200):
        length = (0.2, 0.35, 1.5, 0.1, 0.0, 0.5, 2.5)[i % 7]
        words.append({"start": round(t, 2), "end": round(t + length, 2), "word": f"w{i}"})
        t += length + (0.0, 0.0, 0.3, 1.1, 0.0, 0.0, 0.9, 0.0, 4.0)[i % 9]
    clip = _clip(0.0, t + 1.0)

    for size in range(1, 7):
        lines = build_caption_lines(_segments(words), clip, size)
        shown = " ".join(line["text"] for line in lines).split()
        # A word of no length alone in a caption of its own was never burned
        # (before a pause it now is).
        missing = [w["word"] for w in words if w["word"] not in shown]
        assert shown == [w["word"] for w in words if w["word"] in shown]
        assert all(w["end"] == w["start"] for w in words if w["word"] in missing)
        assert len(missing) <= len(_words_missing_before(words, clip, size))
        assert all(len(line["text"].split()) <= size for line in lines)
        assert all(line["end"] > line["start"] for line in lines)
        assert _silent(lines, words, clip) <= sum(line["end"] - line["start"] for line in lines) * 0.5
        assert _silent(lines, words, clip) < _silent(_as_before(words, clip, size), words, clip)


def _words_missing_before(words: list[dict], clip: ClipCandidate, size: int) -> list[str]:
    shown = " ".join(line["text"] for line in _as_before(words, clip, size)).split()
    return [w["word"] for w in words if w["word"] not in shown]


def test_a_word_of_no_length_before_a_pause_does_not_hold_the_caption_up():
    """It used to open "four. five six" and keep it up through the pause,
    with the words not yet said. It is said before the pause, so it is on
    screen there, for ALONE_SECONDS, and the caption after starts clean."""
    words = _words((0.0, 0.4, "one"), (0.4, 0.8, "two"), (0.8, 1.2, "three"), (1.2, 1.2, "four."),
                   (3.0, 3.4, "five"), (3.4, 3.8, "six"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 4.0), 3)

    assert lines == [
        {"start": 0.0, "end": 1.2, "text": "one two three"},
        {"start": 1.2, "end": round(1.2 + ALONE_SECONDS, 2), "text": "four."},
        {"start": 3.0, "end": 3.8, "text": "five six"},
    ]
    assert ALONE_SECONDS < PAUSE_SECONDS        # gone before the next word


def test_a_word_of_no_length_that_opens_the_clip_before_a_pause_is_shown_and_gone():
    words = _words((1.0, 1.0, "Hey"), (5.0, 5.3, "what"), (5.3, 5.6, "is"), (5.6, 6.0, "that"))

    for size in (1, 3, 6):
        lines = build_caption_lines(_segments(words), _clip(0.0, 7.0), size)

        assert lines[0] == {"start": 1.0, "end": round(1.0 + ALONE_SECONDS, 2), "text": "Hey"}
        assert lines[1]["start"] == 5.0
        assert round(_silent(lines, words, _clip(0.0, 7.0)), 2) == ALONE_SECONDS


def test_a_word_of_no_length_with_no_pause_after_it_is_as_it_always_was():
    """It rides in the caption of the word that follows it; one word to a
    caption it has none, and is not burned."""
    words = _words((0.0, 0.4, "one"), (0.4, 0.4, "two"), (0.6, 1.0, "three"), (1.0, 1.4, "four"))

    for size in (1, 2, 3):
        assert build_caption_lines(_segments(words), _clip(0.0, 2.0), size) == _as_before(words, _clip(0.0, 2.0), size)
    assert [line["text"] for line in build_caption_lines(_segments(words), _clip(0.0, 2.0), 1)] == [
        "one", "three", "four"]


def test_a_word_of_no_length_after_a_pause_at_the_clips_end_is_shown():
    """It used to ride in the caption before it, through the pause."""
    words = _words((0.0, 0.4, "one"), (0.4, 0.8, "two"), (3.0, 3.0, "three"))

    assert build_caption_lines(_segments(words), _clip(0.0, 5.0), 3) == [
        {"start": 0.0, "end": 0.8, "text": "one two"},
        {"start": 3.0, "end": round(3.0 + ALONE_SECONDS, 2), "text": "three"},
    ]
    # For no longer than the clip is.
    assert build_caption_lines(_segments(words), _clip(0.0, 3.1), 3)[-1] == {"start": 3.0, "end": 3.1, "text": "three"}


def test_a_very_short_word_alone_before_a_pause_is_on_screen_long_enough_to_read():
    words = _words((0.0, 0.4, "so"), (0.4, 0.8, "we"), (0.8, 1.2, "went"), (2.0, 2.02, "Look!"),
                   (7.0, 7.3, "this"), (7.3, 7.6, "is"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 8.0), 3)

    assert lines == [
        {"start": 0.0, "end": 1.2, "text": "so we went"},
        {"start": 2.0, "end": round(2.0 + ALONE_SECONDS, 2), "text": "Look!"},
        {"start": 7.0, "end": 7.6, "text": "this is"},
    ]


# ---- no word is spoken for longer than the cap ------------------------------------------


def test_a_word_stretched_across_the_silence_after_it_no_longer_hides_the_pause():
    """The transcript ends "honestly" where the next word starts, 3 s later:
    read as it is written there is no pause at all."""
    words = _words((0.0, 0.3, "well"), (0.3, 3.3, "honestly"), (3.3, 3.7, "it"), (3.7, 4.1, "was"), (4.1, 4.5, "fine"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 5.0), 3)

    assert lines == [
        {"start": 0.0, "end": round(0.3 + LONGEST_WORD_SECONDS, 2), "text": "well honestly"},
        {"start": 3.3, "end": 4.5, "text": "it was fine"},
    ]


def test_a_long_word_with_no_pause_after_it_stays_in_its_caption():
    """ "it" is written 1.44 s long: counted for 1.2 s the next word follows
    0.24 s later, which is no pause. The caption still ends when its own last
    word has been said."""
    words = _words((0.0, 0.3, "and"), (0.3, 1.74, "it"), (1.74, 2.1, "goes"), (2.1, 2.5, "on"), (2.5, 4.0, "so"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 6.0), 3)

    assert lines == [
        {"start": 0.0, "end": 2.1, "text": "and it goes"},
        {"start": 2.1, "end": round(2.5 + LONGEST_WORD_SECONDS, 2), "text": "on so"},
    ]


def test_a_word_as_long_as_the_cap_is_left_as_it_is():
    words = _words((0.0, LONGEST_WORD_SECONDS, "looooong"), (LONGEST_WORD_SECONDS, 1.6, "word"))

    assert build_caption_lines(_segments(words), _clip(0.0, 2.0), 1) == [
        {"start": 0.0, "end": LONGEST_WORD_SECONDS, "text": "looooong"},
        {"start": LONGEST_WORD_SECONDS, "end": 1.6, "text": "word"},
    ]


# ---- at the clip's edges --------------------------------------------------------------


def test_a_word_the_clips_start_cuts_through_counts_from_the_clips_start():
    """ "before" starts 2 s ahead of the clip and is written as lasting 0.5 s
    into it: it is shown for that half second, not dropped, and the pause
    after it ends its caption."""
    words = _words((8.0, 10.5, "before"), (12.0, 12.4, "then"), (12.4, 12.8, "this"))

    lines = build_caption_lines(_segments(words), _clip(10.0, 20.0), 3)

    assert lines == [{"start": 0.0, "end": 0.5, "text": "before"}, {"start": 2.0, "end": 2.8, "text": "then this"}]


def test_a_stretched_word_cut_by_the_clips_start_is_capped_from_there():
    words = _words((8.0, 14.0, "before"), (14.0, 14.4, "then"))

    lines = build_caption_lines(_segments(words), _clip(10.0, 20.0), 3)

    assert lines == [{"start": 0.0, "end": LONGEST_WORD_SECONDS, "text": "before"},
                     {"start": 4.0, "end": 4.4, "text": "then"}]


def test_a_caption_at_the_clips_end_still_ends_with_the_clip():
    words = _words((0.0, 0.4, "one"), (0.4, 0.8, "two"), (3.0, 3.4, "three"), (3.4, 9.0, "four"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 4.0), 3)

    # "four" is cut by the clip's end before the cap is reached.
    assert lines == [{"start": 0.0, "end": 0.8, "text": "one two"}, {"start": 3.0, "end": 4.0, "text": "three four"}]


def test_a_clip_that_opens_and_closes_in_silence_has_no_caption_there():
    words = _words((5.0, 5.4, "only"), (5.4, 5.8, "these"), (5.8, 6.2, "words"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 30.0), 3)

    assert lines == [{"start": 5.0, "end": 6.2, "text": "only these words"}]


# ---- with the second speaker's turns --------------------------------------------------


def test_a_pause_ends_a_caption_inside_one_speakers_turn_too():
    words = _words((0.0, 0.4, "so"), (0.4, 0.8, "what"), (1.0, 1.4, "well"), (3.5, 3.9, "I"), (3.9, 4.3, "think"),
                   (4.3, 4.7, "so"), (6.5, 6.9, "ok"))
    turns = [[1.0, 4.7, 1]]

    lines = build_caption_lines(_segments(words), _clip(0.0, 8.0), 3, turns)

    assert lines == [
        {"start": 0.0, "end": 0.8, "text": "so what"},
        {"start": 1.0, "end": 1.4, "text": "well", "speaker": 1},
        {"start": 3.5, "end": 4.7, "text": "I think so", "speaker": 1},
        {"start": 6.5, "end": 6.9, "text": "ok"},
    ]


def test_a_stretched_word_is_capped_in_a_speakers_turn_too():
    words = _words((0.0, 0.4, "so"), (0.4, 0.8, "what"), (1.0, 4.0, "well"), (4.0, 4.4, "I"), (4.4, 4.8, "think"))
    turns = [[1.0, 4.8, 1]]

    lines = build_caption_lines(_segments(words), _clip(0.0, 8.0), 3, turns)

    assert lines == [
        {"start": 0.0, "end": 0.8, "text": "so what"},
        {"start": 1.0, "end": round(1.0 + LONGEST_WORD_SECONDS, 2), "text": "well", "speaker": 1},
        {"start": 4.0, "end": 4.8, "text": "I think", "speaker": 1},
    ]


def test_with_turns_a_short_word_between_two_pauses_is_alone_and_none_is_lost():
    """Words too short to stand alone go in the caption beside them
    (_standing), but not across a pause: "a b c d" was up from 0.0 to 3.02."""
    words = _words((0.0, 0.4, "a"), (0.4, 0.8, "b"), (0.8, 1.2, "c"), (3.0, 3.02, "d"), (5.0, 5.4, "e"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 6.0), 3, [[5.0, 5.4, 1]])

    assert lines == [
        {"start": 0.0, "end": 1.2, "text": "a b c"},
        {"start": 3.0, "end": round(3.0 + ALONE_SECONDS, 2), "text": "d"},
        {"start": 5.0, "end": 5.4, "text": "e", "speaker": 1},
    ]


def test_with_turns_a_short_word_after_a_pause_goes_in_the_caption_that_follows():
    """One word to a caption: "years. 30" was up through the pause between
    the two words."""
    words = _words((0.0, 0.4, "for"), (0.4, 0.8, "years."), (2.0, 2.02, "30"), (2.02, 2.4, "of"), (2.4, 2.8, "them"),
                   (4.0, 4.4, "ok"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 5.0), 1, [[4.0, 4.4, 1]])

    assert lines == [
        {"start": 0.0, "end": 0.4, "text": "for"},
        {"start": 0.4, "end": 0.8, "text": "years."},
        {"start": 2.0, "end": 2.4, "text": "30 of"},
        {"start": 2.4, "end": 2.8, "text": "them"},
        {"start": 4.0, "end": 4.4, "text": "ok", "speaker": 1},
    ]


def test_with_turns_a_word_of_no_length_that_opens_a_turn_before_a_pause_is_alone():
    words = _words((0.0, 0.4, "so"), (0.4, 0.8, "what"), (3.0, 3.0, "look"), (4.0, 4.4, "this"), (4.4, 4.8, "is"))

    lines = build_caption_lines(_segments(words), _clip(0.0, 5.0), 3, [[2.9, 4.8, 1]])

    # Marked by the clock, as the same line is when the editor sends it back.
    assert lines == [
        {"start": 0.0, "end": 0.8, "text": "so what"},
        {"start": 3.0, "end": round(3.0 + ALONE_SECONDS, 2), "text": "look", "speaker": 1},
        {"start": 4.0, "end": 4.8, "text": "this is", "speaker": 1},
    ]


# ---- unchanged where there is no pause and no long word ---------------------------------


def _even_talk() -> list[dict]:
    """Talk with breaths in it but no pause over the threshold and no word
    over the cap: words of no length, one of a few hundredths, two that
    start together, gaps of up to exactly the threshold."""
    words, t = [], 3.0
    for i in range(300):
        length = (0.0, 0.31, 0.44, 0.03, 0.52, 0.27, 0.05, 0.38, 1.2, 0.02, 0.41)[i % 11]
        words.append({"start": round(t, 2), "end": round(t + length, 2), "word": f"r{i}"})
        t += length + (0.0, 0.0, 0.2, 0.0, 0.8, 0.55, 0.0)[i % 7]
    assert max(round(w["end"] - w["start"], 2) for w in words) <= LONGEST_WORD_SECONDS
    return words


def test_talk_without_a_long_pause_or_a_long_word_gives_the_lines_it_always_gave():
    words = _even_talk()
    for start, end in [(0.0, 400.0), (10.0, 30.0), (12.21, 40.5), (9.67, 31.13), (3.0, 18.4)]:
        for size in range(1, 7):
            clip = _clip(start, end)
            lines = build_caption_lines(_segments(words), clip, size)
            assert lines == _as_before(words, clip, size), (start, end, size)
            assert repr(lines) == repr(_as_before(words, clip, size))      # to the byte
    assert len(_as_before(words, _clip(0.0, 400.0), 3)) > 90


def test_text_without_word_timings_gives_the_lines_it_always_gave():
    """Spread evenly over its segment there is no pause in it, and a word
    spread over more than the cap was not stretched by anything: slow speech
    keeps its caption up."""
    segments = [
        Segment(start=0.0, end=4.0, text="these words have no timings", words=None),
        Segment(start=4.0, end=10.0, text="said very slowly", words=None),                # 2 s a word
        Segment(start=10.0, end=10.5, text="and then quick ones follow straight on", words=None),
    ]
    spread = []
    for seg in segments:
        tokens = seg.text.split()
        step = (seg.end - seg.start) / len(tokens)
        spread += [{"start": seg.start + i * step, "end": seg.start + (i + 1) * step, "word": tok}
                   for i, tok in enumerate(tokens)]

    for start, end in [(0.0, 10.5), (1.3, 9.1), (4.0, 10.5)]:
        for size in (1, 2, 3, 5):
            clip = _clip(start, end)
            assert repr(build_caption_lines(segments, clip, size)) == repr(_as_before(spread, clip, size))
    assert build_caption_lines(segments, _clip(0.0, 10.5), 1)[5] == {"start": 4.0, "end": 6.0, "text": "said"}


def test_silence_between_two_stretches_of_untimed_text_ends_the_caption():
    """The one pause text without word timings can have: between segments."""
    segments = [
        Segment(start=0.0, end=2.0, text="one two three four", words=None),
        Segment(start=5.0, end=7.0, text="five six seven eight", words=None),
    ]

    lines = build_caption_lines(segments, _clip(0.0, 8.0), 3)

    assert lines == [
        {"start": 0.0, "end": 1.5, "text": "one two three"},
        {"start": 1.5, "end": 2.0, "text": "four"},                # it was "four five six", up through 3 s of silence
        {"start": 5.0, "end": 6.5, "text": "five six seven"},
        {"start": 6.5, "end": 7.0, "text": "eight"},
    ]


# ---- lines a person saved, and a clip that moved ----------------------------------------


def test_saved_lines_keep_their_own_times(tmp_path):
    words = _words((0.0, 0.4, "so"), (0.4, 0.9, "yeah"), (3.0, 3.4, "right"), (3.4, 6.0, "then"))
    saved = [{"start": 0.0, "end": 3.4, "text": "so yeah right"}, {"start": 3.4, "end": 6.0, "text": "then"}]

    path = build_captions(_segments(words), _clip(0.0, 6.0), tmp_path / "saved.ass", lines=saved)

    assert [(a, b) for a, b, _ in _events(path)] == [("0:00:00.00", "0:00:03.40"), ("0:00:03.40", "0:00:06.00")]
    # And through a clip whose end moved: only what is added is made anew.
    refit = refit_caption_lines(saved, _segments(words), old_start=0.0, new_start=0.0, new_end=6.0)
    assert refit == saved


def test_the_seconds_a_moved_clip_gains_are_captioned_with_the_pauses_out():
    words = _words((0.0, 0.5, "a"), (0.5, 1.0, "b"), (1.0, 1.5, "c"), (1.5, 2.0, "d"), (4.0, 4.5, "e"),
                   (4.5, 7.5, "f"), (7.5, 8.0, "g"))
    saved = [{"start": 0.0, "end": 1.5, "text": "A, B, C!"}]

    lines = refit_caption_lines(saved, _segments(words), old_start=0.0, new_start=0.0, new_end=9.0)

    assert lines == [
        {"start": 0.0, "end": 1.5, "text": "A, B, C!"},
        {"start": 1.5, "end": 2.0, "text": "d"},
        {"start": 4.0, "end": round(4.5 + LONGEST_WORD_SECONDS, 2), "text": "e f"},
        {"start": 7.5, "end": 8.0, "text": "g"},
    ]


def test_a_word_of_no_length_before_a_pause_in_the_seconds_a_moved_clip_gains():
    words = _words((0.0, 0.5, "a"), (0.5, 1.0, "b"), (1.0, 1.5, "c"), (1.5, 1.5, "d"), (4.0, 4.5, "e"),
                   (4.5, 5.0, "f"))
    saved = [{"start": 0.0, "end": 1.5, "text": "A, B, C!"}]

    lines = refit_caption_lines(saved, _segments(words), old_start=0.0, new_start=0.0, new_end=6.0)

    assert lines == [
        {"start": 0.0, "end": 1.5, "text": "A, B, C!"},
        {"start": 1.5, "end": round(1.5 + ALONE_SECONDS, 2), "text": "d"},
        {"start": 4.0, "end": 5.0, "text": "e f"},
    ]


# ---- the word highlight and karaoke stay in step ----------------------------------------

STRETCHED = _words((0.0, 0.3, "well"), (0.3, 3.3, "honestly"), (3.3, 3.7, "it"), (3.7, 4.1, "was"), (4.1, 4.5, "fine"))


def test_the_word_highlight_follows_the_new_lines(tmp_path):
    path = build_captions(_segments(STRETCHED), _clip(0.0, 5.0), tmp_path / "hl.ass", style={"highlight": True})
    plain = [(a, b, re.sub(r"\{[^}]*\}", "", text)) for a, b, text in _events(path)]
    lit = [re.findall(r"\\1c&H[0-9A-F]+&\}([A-Z]+)\{", text) for _, _, text in _events(path)]

    # One event per word, each word lit from when it starts to be said, by
    # the transcript's own timings: "honestly" until its caption ends (the
    # cap), not for the 3 s it is written as lasting.
    assert plain == [
        ("0:00:00.00", "0:00:00.30", "WELL HONESTLY"), ("0:00:00.30", "0:00:01.50", "WELL HONESTLY"),
        ("0:00:03.30", "0:00:03.70", "IT WAS FINE"), ("0:00:03.70", "0:00:04.10", "IT WAS FINE"),
        ("0:00:04.10", "0:00:04.50", "IT WAS FINE"),
    ]
    assert lit == [["WELL"], ["HONESTLY"], ["IT"], ["WAS"], ["FINE"]]


def test_karaoke_fills_each_word_inside_its_own_caption(tmp_path):
    path = build_captions(_segments(STRETCHED), _clip(0.0, 5.0), tmp_path / "k.ass", style={"animation": "karaoke"})
    events = _events(path)

    assert [(a, b) for a, b, _ in events] == [("0:00:00.00", "0:00:01.50"), ("0:00:03.30", "0:00:04.50")]
    # In hundredths from the caption's start: "well" 30, "honestly" the 120
    # the caption has left; then 40 each.
    assert [re.findall(r"\\kf?(\d+)\}", text) for _, _, text in events] == [["30", "120"], ["40", "40", "40"]]
    for a, b, text in events:
        length = round((float(b.split(":")[-1]) - float(a.split(":")[-1])) * 100)
        assert sum(int(n) for n in re.findall(r"\\kf?(\d+)\}", text)) == length
