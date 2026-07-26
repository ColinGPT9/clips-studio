"""Whisper repetition loops.

On music, crowd noise or long near-silence, Whisper can lock onto one phrase
and emit it over and over inside a SINGLE segment. A real case from a music
video: one 184-second "segment" of 165 words with 5 unique ones. Nothing
downstream could tell that from speech, so it reached scoring, titles and
creator knowledge, and produced clips whose hook was the looped phrase.

The detector keys on vocabulary collapse rather than duration, and these
tests exist mostly to hold that line: an earlier duration-based version
destroyed a legitimate 101-second outro.
"""

from core.models import Segment
from transcription.transcriber import _collapse_repetition_loops


def seg(start, end, text, word_count=None):
    """A segment with word timings spread evenly, as Whisper produces."""
    words = text.split()
    n = word_count or len(words)
    step = (end - start) / max(1, n)
    return Segment(
        start=start,
        end=end,
        text=text,
        words=[
            {
                "start": round(start + i * step, 2),
                "end": round(start + (i + 1) * step, 2),
                "word": words[i % len(words)],
            }
            for i in range(n)
        ],
    )


def test_collapses_a_real_loop():
    loop = seg(0.0, 120.0, "we are ready. " * 45)
    assert _collapse_repetition_loops([loop]) == 1
    assert loop.end < 20, "the segment should end at the last real word"
    assert len(loop.text) < 60


def test_sparse_speech_survives():
    """A gym or IRL stream says little over a long stretch. Duration-based
    detection destroyed exactly this."""
    s = seg(
        0.0,
        90.0,
        "alright so today we're doing legs and honestly my quads are still "
        "cooked from the session on monday but we're going anyway",
    )
    before = (s.start, s.end, s.text)
    assert _collapse_repetition_loops([s]) == 0
    assert (s.start, s.end, s.text) == before


def test_short_chant_survives():
    """A genuine hype chant is real content, and short because the pauses in
    it become segment boundaries."""
    s = seg(10.0, 14.0, "let's go " * 8)
    assert _collapse_repetition_loops([s]) == 0


def test_emphasis_inside_normal_speech_survives():
    s = seg(
        0.0,
        30.0,
        "no no no no no that is absolutely not what happened here and I want "
        "to be really clear about the whole situation for everyone watching",
    )
    assert _collapse_repetition_loops([s]) == 0


def test_long_rich_monologue_survives():
    s = seg(0.0, 60.0, " ".join(f"word{i}" for i in range(80)))
    assert _collapse_repetition_loops([s]) == 0


def test_word_timings_are_trimmed_with_the_text():
    """Captions render from `words`. Collapsing the text but leaving 165 word
    timings would still show the loop on screen."""
    loop = seg(0.0, 120.0, "we are ready. " * 45)
    _collapse_repetition_loops([loop])
    assert len(loop.words) <= len(loop.text.split()) + 1
    assert loop.words[-1]["end"] <= loop.end + 0.01


def test_segments_without_word_timings_are_left_alone():
    """Transcripts cached before word timestamps existed have no `words`."""
    s = Segment(start=0.0, end=200.0, text="we are ready. " * 45, words=None)
    assert _collapse_repetition_loops([s]) == 0
