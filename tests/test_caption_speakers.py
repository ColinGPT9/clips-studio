"""The other speaker's captions in a second colour (#126).

An option in the caption style, off unless ticked. What it promises:
- off, or absent, a clip's captions are written exactly as before, even when
  its saved lines still carry a speaker from an earlier render;
- on, a line ends where the speaker changes and the other speaker's lines
  burn in the second colour, with the word highlight still working on top;
- lines the user saved keep their text and their grouping.

Who is talking comes from analysis/voice_turns.py as turns, [[start, end,
speaker], ...] in clip seconds. Here they are given.
"""

import re

from core.models import ClipCandidate, Segment
from video.captions import build_caption_lines, build_captions, tag_lines
from video_editor.captions import remap_lines
from video_editor.timeline import EditList

SECOND = "&HFFE15C&"      # the default second colour, #5CE1FF, as ASS writes it
CLIP = ClipCandidate(start=10.0, end=20.0, score=0)
# The other speaker has seconds 2-4 and 7-8 of the clip: words w24-w27, w34-w35.
TURNS = [[2.0, 4.0, 1], [7.0, 8.0, 1]]


def _segments() -> list[Segment]:
    """A word every half second for a minute: w0 at 0.0-0.5, w1 at 0.5-1.0, ..."""
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.5, "word": f"w{i}"} for i in range(120)]
    return [Segment(start=0.0, end=60.0, text=" ".join(w["word"] for w in words), words=words)]


def _said(line: dict) -> list[int]:
    return [int(n) for n in re.findall(r"w(\d+)", line["text"])]


def _burn(tmp_path, style=None, lines=None, name="c.ass") -> str:
    path = build_captions(_segments(), CLIP, tmp_path / name, style=style, lines=lines)
    return path.read_text(encoding="utf-8")


# ---- off ----------------------------------------------------------------------------


def test_without_turns_the_lines_are_grouped_as_they_always_were():
    plain = build_caption_lines(_segments(), CLIP, 3)

    assert build_caption_lines(_segments(), CLIP, 3, None) == plain
    assert build_caption_lines(_segments(), CLIP, 3, []) == plain
    assert [_said(line) for line in plain[:2]] == [[20, 21, 22], [23, 24, 25]]
    assert all(set(line) == {"start", "end", "text"} for line in plain)


def test_off_or_absent_the_file_is_the_one_written_before(tmp_path):
    before = _burn(tmp_path)

    assert _burn(tmp_path, {"second_speaker": False}) == before
    assert _burn(tmp_path, {"second_speaker": False, "second_speaker_color": "#FF0000"}) == before
    assert "\\1c" not in before


def test_a_speaker_left_on_saved_lines_never_colours_a_plain_clip(tmp_path):
    tagged = build_caption_lines(_segments(), CLIP, 3, TURNS)
    plain = [{k: v for k, v in line.items() if k != "speaker"} for line in tagged]
    assert any("speaker" in line for line in tagged)

    assert _burn(tmp_path, lines=tagged) == _burn(tmp_path, lines=plain)
    assert _burn(tmp_path, {"highlight": True}, tagged) == _burn(tmp_path, {"highlight": True}, plain)


def test_the_option_alone_changes_nothing_in_a_clip_with_one_voice(tmp_path):
    assert _burn(tmp_path, {"second_speaker": True}) == _burn(tmp_path)


# ---- on -----------------------------------------------------------------------------


def test_a_line_ends_where_the_speaker_changes():
    lines = build_caption_lines(_segments(), CLIP, 3, TURNS)

    assert [(_said(line), line.get("speaker")) for line in lines[:6]] == [
        ([20, 21, 22], None),
        ([23], None),               # cut short: the other speaker starts
        ([24, 25, 26], 1),
        ([27], 1),                  # and stops
        ([28, 29, 30], None),
        ([31, 32, 33], None),
    ]
    # Every word is still said once, in order.
    assert [n for line in lines for n in _said(line)] == list(range(20, 40))


def test_the_other_speakers_lines_burn_in_the_second_colour(tmp_path):
    lines = build_caption_lines(_segments(), CLIP, 3, TURNS)

    ass = _burn(tmp_path, {"second_speaker": True}, lines)

    events = [e for e in ass.splitlines() if e.startswith("Dialogue:")]
    assert len(events) == len(lines)
    for line, event in zip(lines, events):
        text = event.split(",,", 2)[-1]
        if line.get("speaker"):
            assert text == f"{{\\1c{SECOND}}}{line['text'].upper()}"
        else:
            assert text == line["text"].upper()


def test_the_second_colour_is_the_one_chosen(tmp_path):
    lines = build_caption_lines(_segments(), CLIP, 3, TURNS)

    ass = _burn(tmp_path, {"second_speaker": True, "second_speaker_color": "#FF8800"}, lines)

    assert "{\\1c&H0088FF&}" in ass and SECOND not in ass


def test_the_word_highlight_runs_over_the_second_colour(tmp_path):
    lines = build_caption_lines(_segments(), CLIP, 3, TURNS)
    style = {"second_speaker": True, "highlight": True, "highlight_color": "#FFE600"}

    ass = _burn(tmp_path, style, lines)

    hot, white = "&H00E6FF&", "&HFFFFFF&"
    theirs = [e for e in ass.splitlines() if "W24" in e]
    assert len(theirs) == 3                     # one event per word of the line
    for event in theirs:
        assert f"{{\\1c{SECOND}}}" in event and hot in event and white not in event
    ours = [e for e in ass.splitlines() if "W20" in e]
    assert all(white in e and SECOND not in e for e in ours)
    # A line of one word has nothing to move a highlight across: still theirs.
    assert any(e.endswith(f"{{\\1c{SECOND}}}W27") for e in ass.splitlines())


# ---- lines the user saved -----------------------------------------------------------


def test_saved_lines_keep_their_text_and_grouping_and_are_marked_by_who_talks():
    saved = build_caption_lines(_segments(), CLIP, 3)
    saved[1]["text"] = "what they really said"          # 1.5-3.0: theirs for 1.0 of 1.5 s
    saved[0]["speaker"] = 1                             # stale, from an earlier render

    tagged = tag_lines(saved, TURNS)

    assert [{k: v for k, v in line.items() if k != "speaker"} for line in tagged] == [
        {k: v for k, v in line.items() if k != "speaker"} for line in saved
    ]
    assert [line.get("speaker") for line in tagged[:5]] == [None, 1, 1, None, None]
    assert saved[0]["speaker"] == 1                     # the saved lines are not touched


def test_a_line_half_and_half_stays_the_main_speakers():
    line = {"start": 1.0, "end": 3.0, "text": "a b c d"}    # theirs for exactly half

    assert "speaker" not in tag_lines([line], TURNS)[0]


def test_turns_that_are_not_turns_are_passed_over():
    lines = build_caption_lines(_segments(), CLIP, 3)
    junk = [None, "x", [3.0], [5.0, 4.0, 1], ["a", "b", 1], [2.0, 4.0, 0], {"start": 1}]

    assert build_caption_lines(_segments(), CLIP, 3, junk) == lines
    assert tag_lines(lines, junk) == lines
    # A turn with no speaker number is the second speaker.
    assert tag_lines(lines, [[2.0, 4.0]])[2]["speaker"] == 1


def test_a_cut_keeps_the_mark_on_the_lines_it_moves():
    lines = build_caption_lines(_segments(), CLIP, 3, TURNS)
    edit = EditList.from_dict({"keep": [[0.0, 1.0], [2.0, 10.0]]}, duration=10.0)

    moved = remap_lines(lines, edit)

    theirs = [line for line in moved if line.get("speaker")]
    assert [_said(line) for line in theirs] == [[24, 25, 26], [27], [34, 35]]
    assert theirs[0]["start"] == 1.0                    # a second earlier: the cut before it


# ---- fixed by hand ------------------------------------------------------------------
# In the editor a person can say who is talking where (a clip's speaker_edits):
# [[start, end, speaker], ...] with 0 for the main speaker. Each is laid over
# what was heard, and the result is ordinary turns.


def test_what_a_person_says_is_laid_over_what_was_heard():
    from video.captions import paint_turns

    assert paint_turns(TURNS, None) == TURNS
    assert paint_turns(TURNS, []) == TURNS
    # They are the other speaker here, though nobody was heard.
    assert paint_turns([], [[5.0, 6.0, 1]]) == [[5.0, 6.0, 1]]
    # This is the main speaker, though someone else was heard: the middle goes.
    assert paint_turns(TURNS, [[2.5, 3.0, 0]]) == [[2.0, 2.5, 1], [3.0, 4.0, 1], [7.0, 8.0, 1]]
    assert paint_turns(TURNS, [[1.0, 9.0, 0]]) == []
    # Said next to a heard turn, it is one turn.
    assert paint_turns(TURNS, [[4.0, 5.0, 1]]) == [[2.0, 5.0, 1], [7.0, 8.0, 1]]


def test_the_last_thing_said_about_a_stretch_stands():
    from video.captions import paint_turns

    assert paint_turns([], [[2.0, 4.0, 1], [3.0, 3.5, 0]]) == [[2.0, 3.0, 1], [3.5, 4.0, 1]]
    assert paint_turns([], [[3.0, 3.5, 0], [2.0, 4.0, 1]]) == [[2.0, 4.0, 1]]
    assert paint_turns(TURNS, [[2.0, 4.0, 0], [2.0, 4.0, 1]]) == TURNS


def test_edits_that_are_not_edits_are_passed_over_and_one_beyond_the_clip_is_harmless():
    from video.captions import paint_turns

    junk = [None, "x", [3.0], [5.0, 4.0, 1], ["a", "b", 1], [2.0, 3.0], [2.0, 3.0, -1], {"start": 1}]
    assert paint_turns(TURNS, junk) == TURNS
    assert paint_turns(TURNS, "nonsense") == TURNS

    beyond = [[-4.0, -1.0, 1], [30.0, 40.0, 1]]
    lines = build_caption_lines(_segments(), CLIP, 3, paint_turns([], beyond))
    assert lines == build_caption_lines(_segments(), CLIP, 3)


def test_hand_fixes_alone_colour_a_clip_nobody_else_was_heard_in(tmp_path):
    from video.captions import paint_turns

    lines = build_caption_lines(_segments(), CLIP, 3, paint_turns([], [[2.0, 4.0, 1]]))

    assert [(_said(line), line.get("speaker")) for line in lines[:5]] == [
        ([20, 21, 22], None), ([23], None), ([24, 25, 26], 1), ([27], 1), ([28, 29, 30], None),
    ]
    assert f"{{\\1c{SECOND}}}W24 W25 W26" in _burn(tmp_path, {"second_speaker": True}, lines)


def test_saved_lines_go_by_what_was_said_about_them_over_what_was_heard():
    from video.captions import paint_turns

    saved = build_caption_lines(_segments(), CLIP, 3)       # 0-1.5, 1.5-3.0, 3.0-4.5, ...

    said = paint_turns(TURNS, [[3.0, 4.5, 0], [4.5, 6.0, 1]])

    # 1.5-3.0 is still theirs for 1.0 of its 1.5 s; 3.0-4.5 was said to be the
    # main speaker's; 4.5-6.0 was said to be theirs, heard or not.
    assert [line.get("speaker") for line in tag_lines(saved, said)[:5]] == [None, 1, None, 1, None]


def test_nothing_heard_and_nothing_said_takes_a_stale_mark_off_a_saved_line():
    saved = build_caption_lines(_segments(), CLIP, 3)
    saved[0]["speaker"] = 1

    assert all("speaker" not in line for line in tag_lines(saved, []))


def test_a_saved_line_goes_by_who_says_its_words_not_by_the_pause_inside_it():
    """ "yeah" (fixed by hand) and "right exactly" (heard) are one caption
    with two seconds of silence in the middle. By the clock the other speaker
    has 1.1 s of its 2.8 s; by the words, all of it."""
    from video.captions import clip_words, paint_turns

    words = [
        {"start": 0.0, "end": 0.5, "word": "so"}, {"start": 0.5, "end": 1.0, "word": "then"},
        {"start": 1.0, "end": 1.3, "word": "yeah"}, {"start": 3.0, "end": 3.4, "word": "right"},
        {"start": 3.4, "end": 3.8, "word": "exactly"}, {"start": 4.0, "end": 4.5, "word": "ok"},
    ]
    segments = [Segment(start=0.0, end=4.5, text="", words=words)]
    clip = ClipCandidate(start=0.0, end=4.5, score=0)
    said = paint_turns([[3.0, 3.8, 1]], [[1.0, 1.3, 1]])

    built = build_caption_lines(segments, clip, 3, said)
    assert [(line["text"], line.get("speaker")) for line in built] == [
        ("so then", None), ("yeah right exactly", 1), ("ok", None),
    ]
    # The same lines, saved (the editor sends them when a word is muted or
    # retyped, without their marks), are marked the same again.
    saved = [{k: v for k, v in line.items() if k != "speaker"} for line in built]
    assert tag_lines(saved, said, clip_words(words, 0.0, 4.5)) == built
    assert "speaker" not in tag_lines(saved, said)[1]       # by the clock alone it was lost


def test_a_saved_line_no_word_falls_in_goes_by_the_clock():
    from video.captions import clip_words

    words = clip_words(_segments()[0].words, CLIP.start, CLIP.end)
    odd = [{"start": 2.1, "end": 2.2, "text": "between two words' middles"},      # no word's middle inside
           {"start": 30.0, "end": 31.0, "text": "past the transcript"}]

    assert [line.get("speaker") for line in tag_lines(odd, [[2.0, 4.0, 1], [30.0, 31.0, 1]], words)] == [1, 1]
    assert [line.get("speaker") for line in tag_lines(odd, [[2.17, 2.2, 1]], words)] == [None, None]


def test_what_is_said_about_a_saved_captions_stretch_decides_that_caption():
    """Words said over each other: "trust" runs from 6.22 to 7.10, and its
    middle is the very instant the caption it is written in ends. A word is in
    a line from the line's start up to its end, not on it, the stretch a turn
    covers: so the editor's statement about a caption's stretch reaches every
    word that decides the caption, and the caption switches."""
    from video.captions import paint_turns, tag_lines

    words = [{"start": 5.72, "end": 6.18, "word": "because"}, {"start": 6.18, "end": 6.38, "word": "I"},
             {"start": 6.22, "end": 7.10, "word": "trust"}, {"start": 6.38, "end": 6.66, "word": "don't"},
             {"start": 7.10, "end": 7.88, "word": "them,"}]
    lines = [{"start": 6.18, "end": 6.66, "text": "I trust don't"}, {"start": 7.10, "end": 7.88, "text": "them,"}]

    said = paint_turns([], [[6.18, 6.66, 1]])                     # a click on the first caption
    assert [line.get("speaker") for line in tag_lines(lines, said, words)] == [1, None]
    back = paint_turns([[5.0, 8.0, 1]], [[6.18, 6.66, 0]])        # and one that says it is the main speaker's
    assert [line.get("speaker") for line in tag_lines(lines, back, words)] == [None, 1]


def test_a_fix_moves_with_the_clip_and_is_never_cut_off():
    from video.captions import shift_edits

    edits = [[1.0, 2.5, 1], [6.0, 7.0, 0]]

    later = shift_edits(edits, old_start=10.0, new_start=12.0)        # start trimmed by 2 s
    assert later == [[-1.0, 0.5, 1], [4.0, 5.0, 0]]                   # the first is half outside now
    assert shift_edits(later, old_start=12.0, new_start=10.0) == edits   # and whole again on the way back
    assert shift_edits(edits, 10.0, 10.0) == edits
    # An edge between the middles of two words said at once stays where it is.
    assert shift_edits([[1.0, 1.0075, 1]], 10.0, 8.0) == [[3.0, 3.0075, 1]]
    assert shift_edits([None, [1.0, 2.0, 1], "x"], 10.0, 9.5) == [[1.5, 2.5, 1]]
    assert shift_edits(None, 10.0, 9.5) == []


# ---- the same word, in the editor and in the render -----------------------------------


def test_the_editor_is_given_each_word_as_the_render_reads_it():
    """GET /clips/{id}/words went by these numbers before; the render now asks
    who says a word of the very same ones."""
    from video.captions import clip_words, in_clip

    words = _segments()[0].words
    start, end = 10.37, 20.21

    def as_the_route_did():
        out = []
        for w in words:
            if w["end"] > start and w["start"] < end:
                out.append({"start": round(max(0.0, w["start"] - start), 2),
                            "end": round(min(end - start, w["end"] - start), 2), "word": w["word"]})
        return out

    given = clip_words(words, start, end)
    assert given == as_the_route_did()
    assert given[0] == {"start": 0.0, "end": 0.13, "word": "w20"}       # cut by the clip's start
    assert given[-1] == {"start": 9.63, "end": 9.84, "word": "w40"}     # and by its end
    assert in_clip(words[20], start, end) == (0.0, 0.13)


def test_a_word_cut_by_the_clips_edge_still_takes_its_speaker():
    """Its middle, measured on the whole word, is before the clip begins."""
    clip = ClipCandidate(start=10.4, end=20.4, score=0)      # w20 is 10.0-10.5: 0.1 s of it is in

    lines = build_caption_lines(_segments(), clip, 3, [[0.0, 0.1, 1]])

    assert (_said(lines[0]), lines[0].get("speaker")) == ([20], 1)
    assert "speaker" not in lines[1]


def test_a_word_too_short_to_stand_alone_goes_with_the_word_after_it():
    words = [
        {"start": 0.0, "end": 0.5, "word": "a"},
        {"start": 0.5, "end": 0.5, "word": "b"},      # no length at all
        {"start": 0.5, "end": 1.0, "word": "c"},
        {"start": 1.0, "end": 1.04, "word": "d"},     # 0.04 s
        {"start": 1.04, "end": 1.5, "word": "e"},
        {"start": 1.5, "end": 1.5, "word": "f"},      # the last word: goes with the one before
    ]
    segments = [Segment(start=0.0, end=2.0, text="a b c d e f", words=words)]
    clip = ClipCandidate(start=0.0, end=2.0, score=0)

    lines = build_caption_lines(segments, clip, 6, [[0.5, 1.0, 1]])     # "c" is theirs

    assert [(line["text"], line.get("speaker")) for line in lines] == [("a", None), ("b c", 1), ("d e f", None)]
    # Said of the short word alone, a fix changes nothing: it is not a word a
    # caption could hold.
    assert build_caption_lines(segments, clip, 6, [[1.0, 1.04, 1]]) == build_caption_lines(segments, clip, 6)


def test_a_short_word_that_ends_a_sentence_stays_with_it():
    """It touches the word before and a pause follows: it is that speaker's
    last word, not the first of whoever answers."""
    words = [
        {"start": 0.0, "end": 0.4, "word": "the"},
        {"start": 0.4, "end": 0.44, "word": "fight."},     # 0.04 s, then 0.76 s of nothing
        {"start": 1.2, "end": 1.5, "word": "Wait,"},
        {"start": 1.5, "end": 1.9, "word": "what?"},
    ]
    segments = [Segment(start=0.0, end=1.9, text="", words=words)]
    clip = ClipCandidate(start=0.0, end=1.9, score=0)

    lines = build_caption_lines(segments, clip, 4, [[0.0, 0.44, 1]])

    assert [(line["text"], line.get("speaker")) for line in lines] == [("the fight.", 1), ("Wait, what?", None)]


def test_short_words_in_a_row_and_a_clip_of_nothing_else():
    def said(words, turns, size=9):
        segments = [Segment(start=0.0, end=9.0, text="", words=words)]
        lines = build_caption_lines(segments, ClipCandidate(start=0.0, end=9.0, score=0), size, turns)
        return [(line["text"], line.get("speaker")) for line in lines]

    # Two short words between a long one of each speaker, all touching: both
    # go on to the next word, as one short word would.
    chain = [{"start": 0.0, "end": 0.5, "word": "a"}, {"start": 0.5, "end": 0.52, "word": "b"},
             {"start": 0.52, "end": 0.54, "word": "c"}, {"start": 0.54, "end": 1.0, "word": "d"}]
    assert said(chain, [[0.54, 1.0, 1]]) == [("a", None), ("b c d", 1)]
    # Only short words: nobody to take after, so the main speaker's, in any turn.
    only = [{"start": 1.0, "end": 1.02, "word": "x"}, {"start": 1.02, "end": 1.05, "word": "y"}]
    assert said(only, [[0.0, 9.0, 1]]) == [("x y", None)]


def _captions(words, turns, size=3):
    segments = [Segment(start=0.0, end=9.0, text="", words=words)]
    lines = build_caption_lines(segments, ClipCandidate(start=0.0, end=9.0, score=0), size, turns)
    return [(line["text"], line.get("speaker")) for line in lines]


def test_a_short_word_that_ends_a_sentence_stays_with_it_when_the_answer_comes_at_once():
    """No pause to go by: the full stop says whose word it is."""
    words = [{"start": 0.0, "end": 0.4, "word": "the"}, {"start": 0.4, "end": 0.44, "word": "fight."},
             {"start": 0.44, "end": 0.8, "word": "Wait,"}, {"start": 0.8, "end": 1.2, "word": "what?"}]

    assert _captions(words, [[0.0, 0.44, 1]], size=4) == [("the fight.", 1), ("Wait, what?", None)]


def test_two_short_words_that_end_a_sentence_both_stay_with_it():
    words = [{"start": 0.0, "end": 0.5, "word": "going"}, {"start": 0.5, "end": 0.5, "word": "to"},
             {"start": 0.5, "end": 0.5, "word": "win."}, {"start": 1.3, "end": 1.6, "word": "No"},
             {"start": 1.6, "end": 2.0, "word": "way."}]

    assert _captions(words, [[0.0, 0.5, 1]]) == [("going to win.", 1), ("No way.", None)]


def test_short_words_either_side_of_a_full_stop_part_there():
    """"no." ends what one person says and "I" opens the answer, both squeezed
    to nothing between the two."""
    words = [{"start": 0.0, "end": 0.5, "word": "Oh"}, {"start": 0.5, "end": 0.5, "word": "no."},
             {"start": 0.5, "end": 0.5, "word": "I"}, {"start": 0.5, "end": 0.9, "word": "mean"},
             {"start": 0.9, "end": 1.3, "word": "it."}]

    assert _captions(words, [[0.0, 0.5, 1]]) == [("Oh no.", 1), ("I mean it.", None)]


def test_a_short_word_that_opens_a_sentence_goes_with_it():
    """The word before it ended a sentence: this one starts the next, however
    close to the last it was said."""
    words = [{"start": 0.0, "end": 0.4, "word": "Sure."}, {"start": 0.4, "end": 0.43, "word": "I"},
             {"start": 0.6, "end": 0.9, "word": "think"}, {"start": 0.9, "end": 1.2, "word": "so."}]

    assert _captions(words, [[0.0, 0.4, 1]]) == [("Sure.", 1), ("I think so.", None)]


def test_with_no_full_stop_to_go_by_a_short_word_goes_with_the_nearer_word():
    words = [{"start": 0.0, "end": 0.4, "word": "well"}, {"start": 0.4, "end": 0.43, "word": "uh"},
             {"start": 1.0, "end": 1.4, "word": "okay"}, {"start": 1.4, "end": 1.8, "word": "then"}]

    assert _captions(words, [[0.0, 0.4, 1]]) == [("well uh", 1), ("okay then", None)]
    words[1] = {"start": 0.97, "end": 1.0, "word": "uh"}          # now it sits against "okay"
    assert _captions(words, [[0.0, 0.4, 1]]) == [("well", 1), ("uh okay then", None)]


def test_a_runs_last_words_too_short_for_a_caption_join_the_one_before():
    """"then?" has no length in the transcript and is the fourth word of its
    speaker's four: alone in a caption it would not be burned at all."""
    words = [{"start": 0.0, "end": 0.3, "word": "Is"}, {"start": 0.3, "end": 0.6, "word": "that"},
             {"start": 0.6, "end": 0.9, "word": "so"}, {"start": 0.9, "end": 0.9, "word": "then?"},
             {"start": 1.5, "end": 1.9, "word": "Yes"}, {"start": 1.9, "end": 2.3, "word": "it"},
             {"start": 2.3, "end": 2.7, "word": "is."}]

    assert _captions(words, [[0.0, 0.9, 1]]) == [("Is that so then?", 1), ("Yes it is.", None)]
    # 0.03 s of it is still no caption of its own.
    words[3] = {"start": 0.9, "end": 0.93, "word": "then?"}
    assert _captions(words, [[0.0, 0.9, 1]]) == [("Is that so then?", 1), ("Yes it is.", None)]
    # With nobody else heard the captions are the plain ones, as they always were.
    assert _captions(words, []) == [("Is that so", None), ("then? Yes it", None), ("is.", None)]


def test_a_run_of_short_words_parts_at_its_last_full_stop():
    """Three squeezed to nothing between two speakers, two of them ending a
    sentence: everything up to the last full stop is the first speaker's."""
    words = [{"start": 0.0, "end": 0.5, "word": "Oh"}, {"start": 0.5, "end": 0.5, "word": "no."},
             {"start": 0.5, "end": 0.5, "word": "Yes."}, {"start": 0.5, "end": 0.5, "word": "I"},
             {"start": 0.5, "end": 0.9, "word": "mean"}, {"start": 0.9, "end": 1.3, "word": "it."}]

    assert _captions(words, [[0.0, 0.5, 1]]) == [("Oh no. Yes.", 1), ("I mean it.", None)]


def test_short_words_do_not_decide_a_saved_captions_colour():
    """"Go" is the other speaker's and the two short words after it are said
    with it. Marked again as a saved line, the caption goes by "Go": the
    short words, counted by where they fall, would outweigh it."""
    from video.captions import tag_lines

    words = [{"start": 0.0, "end": 0.06, "word": "Go"}, {"start": 0.06, "end": 0.1, "word": "on."},
             {"start": 0.1, "end": 0.14, "word": "then."}, {"start": 1.0, "end": 1.4, "word": "Fine"}]
    turns = [[0.0, 0.06, 1]]
    segments = [Segment(start=0.0, end=9.0, text="", words=words)]
    lines = build_caption_lines(segments, ClipCandidate(start=0.0, end=9.0, score=0), 3, turns)
    assert [(line["text"], line.get("speaker")) for line in lines] == [("Go on. then.", 1), ("Fine", None)]

    sent = [{k: v for k, v in line.items() if k != "speaker"} for line in lines]
    assert tag_lines(sent, turns, words) == lines


def test_a_burst_of_words_at_one_instant_is_not_one_long_caption():
    """A transcript can time twenty words at the same instant. With a second
    voice heard somewhere in the clip they are not all joined to the next
    word's caption: it takes three more than its three, and the rest stay as
    they are without the option (no length, so not burned)."""
    burst = [{"start": 1.0, "end": 1.0, "word": f"x{i}"} for i in range(20)]
    words = [*burst, {"start": 1.0, "end": 1.2, "word": "go"}, {"start": 1.2, "end": 1.6, "word": "now"},
             {"start": 5.0, "end": 5.4, "word": "okay"}]

    plain = _captions(words, [])
    heard = _captions(words, [[5.0, 5.4, 1]])

    assert max(len(text.split()) for text, _ in plain) == 3
    assert max(len(text.split()) for text, _ in heard) == 6
    assert heard[-1] == ("okay", 1)


def test_a_short_word_timed_inside_the_word_before_does_not_end_the_caption_early():
    """"uh" has no length and is timed in the middle of "long". Joined to
    that caption, it must not take it off the screen while "long" is said."""
    words = [{"start": 0.0, "end": 0.3, "word": "a"}, {"start": 0.3, "end": 0.6, "word": "b"},
             {"start": 0.6, "end": 1.6, "word": "long"}, {"start": 0.9, "end": 0.9, "word": "uh"},
             {"start": 1.6, "end": 2.0, "word": "yes"}]
    segments = [Segment(start=0.0, end=9.0, text="", words=words)]
    clip = ClipCandidate(start=0.0, end=9.0, score=0)

    lines = build_caption_lines(segments, clip, 3, [[1.6, 2.0, 1]])
    assert [(line["text"], line["start"], line["end"]) for line in lines] == [("a b long uh", 0.0, 1.6), ("yes", 1.6, 2.0)]
    # One word a caption: "uh" joins "long" and that caption still burns.
    one = build_caption_lines(segments, clip, 1, [[1.6, 2.0, 1]])
    assert [(line["text"], line["start"], line["end"]) for line in one][2] == ("long uh", 0.6, 1.6)
