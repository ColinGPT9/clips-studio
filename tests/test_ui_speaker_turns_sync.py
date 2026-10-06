"""The editor and the render agree on who says each caption (#126).

Who is talking is worked out in two languages: video/captions.py for the
render, ui/src/renderer/src/lib/speakerTurns.ts for the editor's instant
preview and its Fix speakers mode. A difference between them is a caption
shown in one colour and burned in another, so the TypeScript is run under
Node and compared with the Python case by case.

Each side is fed what it is fed in the app, not a shared copy: the Python the
transcript's own words and the clip's start and end in the video, the
TypeScript the clip's words as GET /clips/{id}/words gives them.

The editor draws from those functions and saves the list of fixes it drew
from, so "what is shown is what burns" comes down to the functions agreeing.
The rest here is about what a click adds to that list: the stretch that is
one word's alone, a list tidied without changing what it says, and the
editor's own clicks (fixSpeakers, the code it runs) gone through word after
word.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.models import ClipCandidate, Segment
from video.captions import _speakers, _turns, build_caption_lines, clip_words, paint_turns, tag_lines

LIB = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run_ts(script: str) -> str:
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return r.stdout


def _ts(tmp_path, cases: list, body: str) -> list:
    """Run `body` (JavaScript: `m` is the module, `c` one case) over the cases."""
    (tmp_path / "speakerTurns.ts").write_text((LIB / "speakerTurns.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps((tmp_path / 'speakerTurns.ts').as_uri())});"
        "const fs = await import('node:fs');"
        f"const cases = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'cases.json'))}, 'utf8'));"
        f"console.log(JSON.stringify(cases.map((c) => {{ {body} }})));"
    )
    out = json.loads(_run_ts(script))
    assert len(out) == len(cases)
    return out


# ---- transcripts ----------------------------------------------------------------------


def _steady() -> list[dict]:
    """A word every 0.37 s for two minutes, each ending as the next begins."""
    return [{"start": round(i * 0.37, 2), "end": round((i + 1) * 0.37, 2), "word": f"w{i}"} for i in range(320)]


def _ragged() -> list[dict]:
    """Words as a transcript really has them: pauses, a word of no length now
    and then, one of a few hundredths (some ending a sentence before a pause,
    some opening one after), two that start on the same instant."""
    words, t = [], 3.0
    for i in range(260):
        length = (0.0, 0.31, 0.44, 0.03, 0.52, 0.27, 0.05, 0.38, 0.6, 0.02, 0.41)[i % 11]
        words.append({"start": round(t, 2), "end": round(t + length, 2), "word": f"r{i}"})
        t += length + (0.0, 0.0, 0.2, 0.0, 0.0, 0.55, 0.0)[i % 7]
    return words


def _talked_over() -> list[dict]:
    """Two people at once, as an online transcript writes it: a long word
    with shorter ones said inside it, and words that start before the last
    one has ended."""
    words, t = [], 2.0
    for i in range(200):
        kind = i % 9
        if kind == 3:        # a long word; the next two are said inside it
            words.append({"start": round(t, 2), "end": round(t + 0.9, 2), "word": f"o{i}"})
            t += 0.14
        elif kind in (4, 5):
            # Every other time the second one's middle is the long word's own
            # middle, to the hundredth: those two no stretch of time can part.
            length = 0.18 if kind == 4 or (i // 9) % 2 else 0.26
            words.append({"start": round(t, 2), "end": round(t + length, 2), "word": f"o{i}"})
            t += 0.22 if kind == 4 else 0.6
        elif kind == 7:      # starts before the one before it has ended
            words.append({"start": round(t - 0.12, 2), "end": round(t + 0.3, 2), "word": f"o{i}"})
            t += 0.3
        else:
            words.append({"start": round(t, 2), "end": round(t + 0.33, 2), "word": f"o{i}"})
            t += 0.33
    return sorted(words, key=lambda w: w["start"])


def _spoken() -> list[dict]:
    """Sentences, as a transcript writes people talking: a full stop or a
    question mark on the last word, and some of those last words, or the
    first of the next sentence, squeezed to nothing or a few hundredths,
    alone or two in a row, with and without a pause before the answer."""
    words, t = [], 1.0
    for i in range(264):
        k = i % 12
        length = (0.33, 0.41, 0.28, 0.0, 0.36, 0.03, 0.04, 0.47, 0.3, 0.05, 0.0, 0.38)[k]
        mark = "." if k in (3, 6, 10) else "?" if k == 8 else ""
        words.append({"start": round(t, 2), "end": round(t + length, 2), "word": f"s{i}{mark}"})
        t += length + (0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0)[k]
    return words


def _bursts() -> list[dict]:
    """What a transcript does with fast talk: ten words squeezed into one
    instant (more than a caption will take), three short words in a row of
    which the first two each end a sentence, and a word of no length timed
    inside the word before it."""
    words, t = [], 0.5
    for i in range(220):
        k = i % 22
        if k < 10:
            words.append({"start": round(t, 2), "end": round(t, 2), "word": f"b{i}"})
        elif k in (12, 13, 14):
            words.append({"start": round(t, 2), "end": round(t + 0.03, 2), "word": f"b{i}" + ("." if k < 14 else "")})
            t += 0.03
        elif k == 17:
            words.append({"start": round(t - 0.2, 2), "end": round(t - 0.2, 2), "word": f"b{i}"})
        else:
            words.append({"start": round(t, 2), "end": round(t + 0.4, 2), "word": f"b{i}"})
            t += 0.4 + (0.3 if k == 20 else 0.0)
    return words


TRANSCRIPTS = {"steady": _steady(), "ragged": _ragged(), "talked_over": _talked_over(), "spoken": _spoken(),
               "bursts": _bursts()}
# Clip starts and ends in the video: on a word's edge, and cutting through one.
WINDOWS = [(10.0, 30.0), (249.67 - 240, 31.13), (12.21, 40.5), (3.0, 18.4), (20.36, 44.4)]
TURNS = [
    [],
    [[2.0, 4.0, 1], [7.5, 9.25, 1]],
    [[0.0, 1.1, 1], [5.0, 12.0, 1], [15.5, 30.0, 1]],
    [[3.0, 6.0, 0], [8.0, 9.0], [11.0, 10.0, 1], [12.0, 14.0, 2]],     # a 0, no number, backwards, a third voice
]
EDITS = [
    [],
    [[1.0, 3.0, 1]],
    [[2.5, 3.5, 0], [6.0, 8.0, 1]],
    [[2.0, 9.0, 0], [3.0, 4.0, 1], [3.5, 3.75, 0]],                     # each over the last
    [[-5.0, -1.0, 1], [0.0, 2.2, 1], [60.0, 70.0, 1]],                  # two of them outside the clip
    [None, "x", [4.0], [5.0, 4.0, 1], [1.0, 2.0, -1], [6.0, 7.0, 1]],   # only the last is an edit
    [[4.4375, 6.0625, 1], [5.105, 5.3475, 0]],                          # finer than a hundredth
]


def _cases() -> list[dict]:
    cases = []
    for name, words in TRANSCRIPTS.items():
        for w, (start, end) in enumerate(WINDOWS):
            for t, turns in enumerate(TURNS):
                for e, edits in enumerate(EDITS):
                    cases.append({
                        "transcript": name, "start": start, "end": end, "size": 1 + (w + t + e) % 6,
                        "turns": turns, "edits": edits,
                        # What the editor is given for this clip.
                        "words": clip_words(words, start, end),
                    })
    return cases


def _segments(name: str) -> list[Segment]:
    words = TRANSCRIPTS[name]
    return [Segment(start=words[0]["start"], end=words[-1]["end"], text="", words=words)]


def _lines(case: dict, turns: list) -> list[dict]:
    clip = ClipCandidate(start=case["start"], end=case["end"], score=0)
    return build_caption_lines(_segments(case["transcript"]), clip, case["size"], turns)


def _burned_speakers(case: dict, edits: list) -> list[int]:
    """Who the render says says each of the clip's words, laying `edits` over
    the case's turns: its own reading of its own words."""
    words = TRANSCRIPTS[case["transcript"]]
    inside = sorted((w for w in words if w["end"] > case["start"] and w["start"] < case["end"]),
                    key=lambda w: w["start"])
    return _speakers(inside, _turns(paint_turns(case["turns"], edits)), case["start"], case["end"])


# ---- the same captions ----------------------------------------------------------------


def test_the_editor_lays_hand_fixes_over_what_was_heard_as_the_render_does(tmp_path):
    cases = _cases()

    got = _ts(tmp_path, cases, "return m.paintTurns(c.turns, c.edits)")

    for case, painted in zip(cases, got, strict=True):
        assert painted == paint_turns(case["turns"], case["edits"]), case


def test_the_editor_reads_who_says_each_word_as_the_render_does(tmp_path):
    cases = _cases()

    got = _ts(tmp_path, cases, "return m.speakersOf(c.words, m.paintTurns(c.turns, c.edits))")

    for case, said in zip(cases, got, strict=True):
        assert said == _burned_speakers(case, case["edits"]), (case["transcript"], case["start"], case["edits"])


def test_the_editor_breaks_and_colours_the_captions_as_the_render_does(tmp_path):
    cases = _cases()

    got = _ts(tmp_path, cases, "return m.groupWords(c.words, c.size, m.paintTurns(c.turns, c.edits))")

    for case, lines in zip(cases, got, strict=True):
        assert lines == _lines(case, paint_turns(case["turns"], case["edits"])), case


@pytest.mark.parametrize("with_words", [True, False])
def test_the_editor_colours_saved_captions_whole_as_the_render_does(tmp_path, with_words):
    """Lines the user saved keep their grouping: each takes one colour, by
    who says its words (or, with no words to go by, by the clock)."""
    cases = [{**c, "saved": _lines(c, [])} for c in _cases()]
    body = ("return m.tagLines(c.saved, m.paintTurns(c.turns, c.edits), c.words)" if with_words
            else "return m.tagLines(c.saved, m.paintTurns(c.turns, c.edits))")

    got = _ts(tmp_path, cases, body)

    for case, lines in zip(cases, got, strict=True):
        painted = paint_turns(case["turns"], case["edits"])
        want = tag_lines(case["saved"], painted, case["words"]) if with_words else tag_lines(case["saved"], painted)
        assert lines == want, case


def test_muting_a_word_does_not_change_whose_colour_a_caption_is(tmp_path):
    """With a word muted or retyped the editor sends its own lines, ending
    where the speaker changes, without their marks, and the render marks each
    by who says its words. For words said one after another that is the mark
    the line was built with, so nothing changes colour, pauses and words too
    short to stand alone included.

    Where two people talk at once it need not be (a short word said inside a
    long one is in the long one's caption by the clock), nor for a burst of
    short words too many for one caption (the rest are captions of nothing
    but short words, which go by the clock): there the editor draws what it
    will send through tagLines, the test above, so it still shows what burns."""
    cases = [c for c in _cases() if c["transcript"] not in ("talked_over", "bursts")]

    got = _ts(tmp_path, cases, "return m.groupWords(c.words, c.size, m.paintTurns(c.turns, c.edits))")

    for case, shown in zip(cases, got, strict=True):
        sent = [{k: v for k, v in line.items() if k != "speaker"} for line in shown]
        marked = tag_lines(sent, paint_turns(case["turns"], case["edits"]), case["words"])
        assert marked == shown, (case["transcript"], case["start"], case["turns"], case["edits"])


def test_the_editor_knows_when_its_word_list_misses_some_of_the_captions(tmp_path):
    """With a word muted the editor sends lines it makes from its word list.
    A stretch of the transcript without word timings is in the clip's
    captions (the render spreads its text out evenly) but not in that list,
    so there the editor has to send the clip's own lines, or those captions
    would be gone from the clip."""
    cases = [{**c, "own": _lines(c, [])} for c in _cases() if c["edits"] == [] and c["turns"] == []]
    words = TRANSCRIPTS["steady"]
    before = [w for w in words if w["end"] <= 10.0]
    after = [w for w in words if w["start"] >= 18.0]
    segments = [
        Segment(start=0.0, end=10.0, text="", words=before),
        Segment(start=10.0, end=18.0, text="nobody timed these words at all", words=None),
        Segment(start=18.0, end=after[-1]["end"], text="", words=after),
    ]
    clip = ClipCandidate(start=4.0, end=30.0, score=0)
    cases.append({"words": clip_words(before + after, 4.0, 30.0), "size": 3,
                  "own": build_caption_lines(segments, clip, 3)})

    got = _ts(tmp_path, cases, "return m.wordsMakeLines(c.words, c.own, c.size)")
    untimed = _ts(tmp_path, [cases[-1]], """
        const clip = { words: c.words, heard: [[1, 2, 1]], lines: c.own, saved: false, perCaption: 3,
                       storedPerCaption: 3, duration: 26 };
        const sending = m.fixSpeakers({ ...clip, sending: true });
        const not = m.fixSpeakers({ ...clip, sending: false });
        return { whole: sending.whole, sent: sending.given([[1, 2, 1]]), covered: not.covered, made: not.given([]) };
    """)[0]

    # With a word muted the editor sends the clip's own lines, untimed words
    # and all; with none muted it sends nothing and says its list is short.
    assert untimed == {"whole": True, "sent": cases[-1]["own"], "covered": False, "made": None}
    assert len(got) > 10 and all(got[:-1])
    # The untimed words share captions with timed ones ("w25 w26 nobody"), so
    # a caption having a word in it says nothing: the whole reading is compared.
    assert "w26 nobody timed these words at all w49" in " ".join(line["text"] for line in cases[-1]["own"])
    assert got[-1] is False


# ---- what a click adds ----------------------------------------------------------------


def test_a_statement_about_one_word_changes_that_word_and_no_other(tmp_path):
    """Also when two people talk at once and one word's middle falls inside
    another: the stretch that is a word's alone is cut back to leave it out."""
    cases = [c for c in _cases() if c["edits"] == [] and c["turns"] in (TURNS[0], TURNS[1])]

    got = _ts(tmp_path, cases, """
        const leans = m.leansOn(c.words);
        const spans = c.words.map((w, i) => (leans[i] === i ? m.ownSpan(c.words, i) : null));
        return { leans, spans };
    """)

    checked = twins = 0
    for case, out in zip(cases, got, strict=True):
        before = _burned_speakers(case, [])
        middle = [(w["start"] + w["end"]) / 2 for w in case["words"]]
        for i, span in enumerate(out["spans"]):
            if span is None or i % 3:          # every third word that stands alone
                continue
            after = _burned_speakers(case, [[span[0], span[1], 1 - before[i]]])
            changed = {j for j, (a, b) in enumerate(zip(before, after, strict=True)) if a != b}
            # The word, any word too short to stand alone that leans on it,
            # and a word said at the very same moment (same middle): those
            # two can't be told apart by when they are said, and the editor
            # shows both switched because it draws from this same reading.
            same = {k for k, on in enumerate(out["leans"]) if on == k and abs(middle[k] - middle[i]) < 1e-6}
            assert changed == {j for j, on in enumerate(out["leans"]) if on in same}, (
                case["transcript"], case["start"], i, span)
            checked += 1
            twins += len(same) > 1
    assert checked > 500 and twins > 0


def test_a_tidied_list_of_fixes_says_what_the_list_said(tmp_path):
    cases = [{**c, "edits": [e for e in EDITS[3] + EDITS[2] + EDITS[1] + c["edits"] + EDITS[6] if isinstance(e, list)]}
             for c in _cases()]

    got = _ts(tmp_path, cases, "return m.compactEdits(c.edits)")

    shorter = 0
    for case, tidy in zip(cases, got, strict=True):
        assert paint_turns(case["turns"], tidy) == paint_turns(case["turns"], case["edits"]), case["edits"]
        shorter += len(tidy) < len(case["edits"])
    assert shorter > 0
    # An earlier statement wholly inside a later one is gone; nothing else is.
    assert _ts(tmp_path, [{"edits": [[1, 2, 1], [0, 3, 0], [2.5, 4, 1], [5, 6, 1]]}], "return m.compactEdits(c.edits)") == [
        [[0, 3, 0], [2.5, 4, 1], [5, 6, 1]]
    ]


def test_each_word_belongs_to_the_saved_caption_that_holds_it(tmp_path):
    cases = [{**c, "saved": _lines(c, [])} for c in _cases() if c["edits"] == [] and c["turns"] == []]
    cases.append({
        "words": [{"start": 0.0, "end": 0.5, "word": "a"}, {"start": 0.5, "end": 1.0, "word": "b"},
                  {"start": 1.0, "end": 1.0, "word": "c"}, {"start": 1.0, "end": 1.5, "word": "d"},
                  {"start": 1.5, "end": 2.0, "word": "e"}, {"start": 5.0, "end": 5.5, "word": "f"}],
        "saved": [{"start": 0.0, "end": 1.0, "text": "a b c"}, {"start": 1.0, "end": 2.0, "text": "d e"},
                  {"start": 2.0, "end": 3.0, "text": ""}, {"start": 3.0, "end": 4.0}],
    })

    # Two people at once: "weeeell" is written in the first caption and still
    # being said while the second is up. Its caption is the one it is written
    # in, whichever is on screen at its middle.
    cases.append({
        "words": [{"start": 0.0, "end": 0.33, "word": "so"}, {"start": 0.33, "end": 1.23, "word": "weeeell"},
                  {"start": 0.47, "end": 0.65, "word": "no"}, {"start": 0.69, "end": 0.95, "word": "way"},
                  {"start": 1.23, "end": 1.62, "word": "okay"}],
        "saved": [{"start": 0.0, "end": 0.65, "text": "so weeeell no"}, {"start": 0.69, "end": 0.95, "text": "way"},
                  {"start": 1.23, "end": 1.62, "text": "o**y"}],
    })

    got = _ts(tmp_path, cases, "return m.linesOfWords(c.words, c.saved)")

    written = by_the_clock = 0
    for case, held in zip(cases[:-2], got[:-2], strict=True):
        texts: dict[int, list[str]] = {}
        for w, line in zip(case["words"], held, strict=True):
            middle = (w["start"] + w["end"]) / 2
            inside = [i for i, l in enumerate(case["saved"]) if l["start"] <= middle <= l["end"]]
            if line >= 0 and w["word"] in case["saved"][line]["text"].split():
                texts.setdefault(line, []).append(w["word"])
                written += 1
            else:       # a word in no caption's text (one of no length, alone in its group): by the clock
                assert (line in inside) if inside else line == -1, (w, line)
                by_the_clock += 1
        # Every caption is spelled by the words tied to it, in their order.
        assert [" ".join(texts[k]) for k in sorted(texts)] == [l["text"] for l in case["saved"]], case["transcript"]
    assert written > 1000 and by_the_clock > 0
    # "c" has no length and sits on the instant "a b c" ends and "d e" begins:
    # it goes with the caption it is written in. A blank caption, or one with
    # no text at all, holds nothing. "f" is in none.
    assert got[-2] == [0, 0, 0, 1, 1, -1]
    # "okay" was muted, so its caption reads "o**y": found by the clock.
    assert got[-1] == [0, 0, 0, 1, 2]


# ---- the editor's own clicks ----------------------------------------------------------
# fixSpeakers is what the editor runs for Fix speakers: who it shows saying
# each word, and the statements a click and Swap add. Here it is clicked
# through, word after word, in each of the three ways a clip can stand.

WAYS = {
    "word by word": {"saved": False, "sending": False},     # the render makes its own lines
    "lines sent": {"saved": False, "sending": True},        # a word is muted: Apply sends lines
    "saved lines": {"saved": True, "sending": True},        # caption text was saved: whole captions
}

CLICK_THROUGH = """
    const fix = m.fixSpeakers({ words: c.words, heard: c.turns, lines: c.own, saved: c.saved, sending: c.sending,
                                perCaption: c.size, storedPerCaption: c.size, duration: c.end - c.start });
    const leans = m.leansOn(c.words);
    const held = fix.whole ? m.linesOfWords(c.words, c.own) : [];
    const middle = c.words.map((w) => (w.start + w.end) / 2);
    const before = fix.speakers(c.edits);
    const out = { clicks: 0, unswitched: 0, others: 0, notBack: 0 };
    c.words.forEach((_, i) => {
      if (i % 2 || !fix.stretch(i)) return;
      out.clicks++;
      const once = fix.click(c.edits, i);
      const after = fix.speakers(once);
      if (after[i] === before[i]) out.unswitched++;
      // What may switch with the word: words too short to stand alone that
      // lean on it (or on what it leans on), a word said at the very same
      // moment; where captions are switched whole, the rest of its caption.
      const goesWith = (j) => fix.whole
        ? held[j] === held[i]
        : leans[j] === leans[i] || Math.abs(middle[leans[j]] - middle[leans[i]]) < 1e-6;
      if (after.some((who, j) => who !== before[j] && !goesWith(j))) out.others++;
      if (JSON.stringify(fix.click(once, i)) !== JSON.stringify(c.edits)) out.notBack++;
    });
    const swapped = fix.swap(c.edits);
    const flipped = fix.speakers(swapped);
    out.swapStuck = c.words.filter((_, i) => fix.stretch(i) && flipped[i] === before[i]).length;
    const again = fix.speakers(fix.swap(swapped));
    out.swapTwice = c.words.filter((_, i) => fix.stretch(i) && again[i] !== before[i]).length;
    // Outside the clip (a fix kept from a part trimmed off) nothing is said.
    const end = c.words.reduce((latest, w) => Math.max(latest, w.end), c.end - c.start);
    const outside = (edits) => JSON.stringify(m.paintTurns(c.turns, edits).flatMap(([a, b, who]) =>
      [[a, Math.min(b, 0), who], [Math.max(a, end), b, who]].filter(([from, to]) => to > from)));
    out.swapOutside = outside(swapped) === outside(c.edits) ? 0 : 1;
    return out;
"""


def _clicked(tmp_path, transcripts: tuple, way: str) -> list:
    cases = [{**c, **WAYS[way], "own": _lines(c, []),
              "edits": [e for e in c["edits"] if isinstance(e, list) and len(e) == 3 and e[1] > e[0] and e[2] >= 0]}
             for c in _cases() if c["transcript"] in transcripts]
    return _ts(tmp_path, cases, CLICK_THROUGH)


@pytest.mark.parametrize("way", list(WAYS))
def test_a_click_switches_the_word_and_a_second_click_takes_it_back(tmp_path, way):
    """Every other word of every clip is clicked. It shows the other speaker,
    nothing a person would call another word changes with it, and clicking it
    again leaves the list of fixes as it was. Swap switches every word, and
    Swap twice shows the start again."""
    got = _clicked(tmp_path, ("steady", "ragged", "spoken"), way)

    assert sum(out["clicks"] for out in got) > 5000
    for out in got:
        assert out == {"clicks": out["clicks"], "unswitched": 0, "others": 0, "notBack": 0,
                       "swapStuck": 0, "swapTwice": 0, "swapOutside": 0}


def test_two_people_talking_at_once_are_still_switched_one_word_at_a_time(tmp_path):
    """Words said over each other (one inside another, one starting before the
    last has ended): while the render makes its own lines every word is still
    switched alone, and Swap misses none, which a statement per run of words
    did (the next run's swallowed a word said in between)."""
    got = _clicked(tmp_path, ("talked_over",), "word by word")

    assert sum(out["clicks"] for out in got) > 1500
    for out in got:
        assert out == {"clicks": out["clicks"], "unswitched": 0, "others": 0, "notBack": 0,
                       "swapStuck": 0, "swapTwice": 0, "swapOutside": 0}


@pytest.mark.parametrize("way", ["lines sent", "saved lines"])
def test_a_click_that_would_switch_nothing_says_nothing(tmp_path, way):
    """Where two people talk at once and the render is given the lines, a
    caption goes by who says most of what is said while it is up, so a word
    inside another may not switch on its own. That is a limit; what must hold
    is that such a click adds no statement, and a second click undoes a first."""
    got = _clicked(tmp_path, ("talked_over",), way)

    assert sum(out["clicks"] for out in got) > 1500
    assert all(out["notBack"] == 0 and out["swapOutside"] == 0 for out in got)


def test_each_word_is_in_the_line_the_editor_made_for_it(tmp_path):
    """With lines sent, a word shows the colour of its own line: the one its
    text went into, not one found again by the clock (a word of no length
    sits on the instant two lines meet)."""
    cases = _cases()

    got = _ts(tmp_path, cases, """
        const turns = m.paintTurns(c.turns, c.edits);
        return { lines: m.groupWords(c.words, c.size, turns), held: m.groupOfWords(c.words, c.size, turns) };
    """)

    left_out = 0
    for case, out in zip(cases, got, strict=True):
        texts: dict[int, list[str]] = {}
        for w, line in zip(case["words"], out["held"], strict=True):
            if line < 0:
                assert w["end"] <= w["start"], w        # only a word of no length is in no line
                left_out += 1
            else:
                texts.setdefault(line, []).append(w["word"])
        assert [" ".join(texts[k]) for k in sorted(texts)] == [line["text"] for line in out["lines"]], case
    assert left_out > 0


SHIFT_CLICK = """
    const fix = m.fixSpeakers({ words: c.words, heard: c.turns, lines: c.own, saved: false, sending: false,
                                perCaption: c.size, storedPerCaption: c.size, duration: c.end - c.start });
    const leans = m.leansOn(c.words);
    const middle = c.words.map((w) => (w.start + w.end) / 2);
    const out = { ranges: 0, missed: 0, outside: 0 };
    for (let from = 0; from + 5 < c.words.length; from += 7) {
      const to = from + 5;
      if (!fix.stretch(from) || !fix.stretch(to)) continue;
      out.ranges++;
      const clicked = fix.click(c.edits, from);
      const before = fix.speakers(clicked);
      const after = fix.speakers(fix.upTo(clicked, from, to));
      // The words that decide the ones in the range, and words said at the
      // very same moment as one of those: these may change, and no other.
      const deciding = [];
      for (let k = from; k <= to; k++) if (leans[k] >= 0) deciding.push(leans[k]);
      const inRange = (j) => leans[j] >= 0 && deciding.some((d) => d === leans[j] || Math.abs(middle[d] - middle[leans[j]]) < 1e-6);
      for (let k = from; k <= to; k++) if (fix.stretch(k) && after[k] !== before[from]) { out.missed++; break; }
      if (after.some((who, j) => who !== before[j] && !inRange(j))) out.outside++;
    }
    return out;
"""


def test_a_shift_click_switches_every_word_up_to_it_and_no_other(tmp_path):
    """Also where two people talk at once: one stretch of time from the first
    word to the last would leave out a word said in between whose middle falls
    outside it, and take in one from outside whose middle falls inside. Each
    word in the range is said, by its own stretch."""
    cases = [{**c, "own": _lines(c, []),
              "edits": [e for e in c["edits"] if isinstance(e, list) and len(e) == 3 and e[1] > e[0] and e[2] >= 0]}
             for c in _cases()]

    got = _ts(tmp_path, cases, SHIFT_CLICK)

    assert sum(out["ranges"] for out in got) > 3000
    for case, out in zip(cases, got, strict=True):
        assert (out["missed"], out["outside"]) == (0, 0), (case["transcript"], case["start"], out)


def test_a_muted_word_said_again_a_caption_later_stays_in_its_own_caption(tmp_path):
    """The first "damn" was muted, so its caption reads "d**n"; the next
    caption says the word again. Reading ahead for the word must not find
    that one: a word is only in a caption that is up when it starts."""
    said = lambda *words: [{"start": a, "end": b, "word": w} for a, b, w in words]      # noqa: E731
    cases = [
        {"words": said((0.0, 0.3, "I"), (0.3, 0.6, "damn"), (0.6, 0.9, "you"), (1.0, 1.3, "what"),
                       (1.3, 1.6, "the"), (1.6, 1.9, "damn"), (1.9, 2.3, "man")),
         "saved": [{"start": 0.0, "end": 0.9, "text": "I d**n you"}, {"start": 1.0, "end": 1.9, "text": "what the damn"},
                   {"start": 1.9, "end": 2.3, "text": "man"}]},
        # Retyped: "to" was made "two", and "to" is said again two captions on.
        {"words": said((0.0, 0.3, "up"), (0.3, 0.6, "to"), (0.7, 1.0, "of"), (1.0, 1.3, "them"),
                       (1.4, 1.7, "go"), (1.7, 2.0, "to"), (2.0, 2.3, "it")),
         "saved": [{"start": 0.0, "end": 0.6, "text": "up two"}, {"start": 0.7, "end": 1.3, "text": "of them"},
                   {"start": 1.4, "end": 2.3, "text": "go to it"}]},
    ]

    got = _ts(tmp_path, cases, """
        const fix = m.fixSpeakers({ words: c.words, heard: [], lines: c.saved, saved: true, sending: true,
                                    perCaption: 3, storedPerCaption: 3, duration: 2.3 });
        return { held: m.linesOfWords(c.words, c.saved), stretch: fix.stretch(1), shown: fix.speakers(fix.click([], 1)) };
    """)

    assert got[0] == {"held": [0, 0, 0, 1, 1, 1, 2], "stretch": [0, 0.9], "shown": [1, 1, 1, 0, 0, 0, 0]}
    assert got[1] == {"held": [0, 0, 1, 1, 2, 2, 2], "stretch": [0, 0.6], "shown": [1, 1, 0, 0, 0, 0, 0]}


def test_saved_captions_are_marked_alike_where_words_are_said_over_each_other(tmp_path):
    """"trust" is said from 6.22 to 7.10 and its middle is the instant its
    caption ends: it is not one of that caption's voices, on either side. And
    words too short to stand alone are no caption's voices."""
    cases = [
        {"words": [{"start": 5.72, "end": 6.18, "word": "because"}, {"start": 6.18, "end": 6.38, "word": "I"},
                   {"start": 6.22, "end": 7.1, "word": "trust"}, {"start": 6.38, "end": 6.66, "word": "don't"},
                   {"start": 7.1, "end": 7.88, "word": "them,"}],
         "saved": [{"start": 6.18, "end": 6.66, "text": "I trust don't"}, {"start": 7.1, "end": 7.88, "text": "them,"}],
         "turns": [], "edits": [[6.18, 6.66, 1]], "want": [1, None]},
        {"words": [{"start": 0.0, "end": 0.06, "word": "Go"}, {"start": 0.06, "end": 0.1, "word": "on."},
                   {"start": 0.1, "end": 0.14, "word": "then."}, {"start": 1.0, "end": 1.4, "word": "Fine"}],
         "saved": [{"start": 0.0, "end": 0.14, "text": "Go on. then."}, {"start": 1.0, "end": 1.4, "text": "Fine"}],
         "turns": [[0.0, 0.06, 1]], "edits": [], "want": [1, None]},
    ]

    got = _ts(tmp_path, cases, "return m.tagLines(c.saved, m.paintTurns(c.turns, c.edits), c.words)")

    for case, lines in zip(cases, got, strict=True):
        assert lines == tag_lines(case["saved"], paint_turns(case["turns"], case["edits"]), case["words"])
        assert [line.get("speaker") for line in lines] == case["want"]


def test_a_shift_click_that_would_change_nothing_says_nothing(tmp_path):
    words = [{"start": 0.0, "end": 0.4, "word": "a"}, {"start": 0.4, "end": 0.8, "word": "b"},
             {"start": 0.8, "end": 1.2, "word": "c"}]

    got = _ts(tmp_path, [{"words": words}], """
        const fix = m.fixSpeakers({ words: c.words, heard: [[0.4, 0.8, 1]], lines: null, saved: false, sending: false,
                                    perCaption: 3, storedPerCaption: 3, duration: 1.2 });
        return { same: fix.upTo([], 1, 1), more: fix.upTo([], 1, 2) };
    """)[0]

    assert got == {"same": [], "more": [[0.4, 1.2, 1]]}
