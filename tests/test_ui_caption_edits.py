"""A word fixed in the editor's captions, in any language (#147).

The editor's transcript lets a person retype a word Whisper misheard, and
mute one (its caption is then censored, f**k). Both are applied to the clip's
caption lines in ui/src/renderer/src/lib/captionEdits.ts, which the editor
draws over the video and Apply sends as caption_lines; the TypeScript runs
under Node here.

A word was found in its line with \\b, which knows only a-z and 0-9: an
Italian "perche" corrected to "perché" never was, and nothing said so; the
render burned the old spelling. The editor has no React test harness, so
how it reaches that module, and what Apply and the clip list do around it,
is checked in their source.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src"
LIB = UI / "lib"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run_ts(script: str) -> str:
    # UTF-8 both ways: the answers hold accented words, and Windows would
    # read Node's output in its own code page.
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return r.stdout


def _texts(tmp_path, lines: list, fixes=(), mutes=()) -> list[str]:
    """Each line's text after the fixes and mutes, as the editor sends it."""
    (tmp_path / "captionEdits.ts").write_text((LIB / "captionEdits.ts").read_text(encoding="utf-8"),
                                              encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps({"lines": lines, "fixes": list(fixes), "mutes": list(mutes)}),
                                        encoding="utf-8")
    script = (
        f"const m = await import({json.dumps((tmp_path / 'captionEdits.ts').as_uri())});"
        "const fs = await import('node:fs');"
        f"const d = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        "console.log(JSON.stringify(m.applyTextEdits(d.lines, d.fixes, d.mutes).map((l) => l.text)));"
    )
    return json.loads(_run_ts(script))


def _line(text: str, start: float = 0.0, end: float = 2.0) -> dict:
    return {"start": start, "end": end, "text": text}


def _fix(word: str, to: str, start: float = 0.5) -> dict:
    return {"start": start, "from": word, "to": to}


@pytest.mark.parametrize(("text", "word", "to", "after"), [
    ("ma perche no", "perche", "perché", "ma perché no"),           # the report
    ("in citta oggi", "citta", "città", "in città oggi"),            # to an accented word
    ("in città oggi", "città", "citta", "in citta oggi"),            # and from one
    ("Città vuota", "città", "Paese", "Paese vuota"),                # any case
    ("ciao, come stai", "ciao,", "salve", "salve, come stai"),       # Whisper's comma, the line's comma
    # The box opens on Whisper's word, comma and all: typed back, it isn't doubled.
    ("ciao, come stai", "ciao,", "ciaoo,", "ciaoo, come stai"),
    ("¿que pasa?", "¿que", "¿qué", "¿qué pasa?"),
    ("ciao l'amico mio", "l'amico", "l'amica", "ciao l'amica mio"),  # an apostrophe in the word
    ("ciao l’amico mio", "l’amico", "l’amica", "ciao l’amica mio"),  # a typographic one
    ("नमस्ते दोस्तों", "नमस्ते", "नमस्कार", "नमस्कार दोस्तों"),      # a vowel sign is part of a word
    # What is inside a word is the word's own: only its ends are punctuation.
    ("c'est peut-etre vrai", "peut-etre", "peut-être", "c'est peut-être vrai"),
    ("donc c'est-a-dire demain", "c'est-a-dire,", "c'est-à-dire", "donc c'est-à-dire demain"),
    ("mon e-mail est", "e-mail", "email", "mon email est"),
    ("il fait 3,5 degrés", "3,5", "3,8", "il fait 3,8 degrés"),
    ("made in the U.S. today", "U.S.", "U.K", "made in the U.K. today"),
    ("من می\u200cخواهم بروم", "می\u200cخواهم", "می\u200cخواستم", "من می\u200cخواستم بروم"),  # a ZWNJ inside
])
def test_a_fixed_word_is_fixed_in_its_line(tmp_path, text, word, to, after):
    assert _texts(tmp_path, [_line(text)], [_fix(word, to)]) == [after]


@pytest.mark.parametrize(("text", "fix"), [
    ("ma perche no", _fix("domani", "dopodomani")),   # a word the line doesn't have
    ("ma perché no", _fix("perch", "X")),             # never a piece of a longer word
    ("ma perche no", _fix("perche", "perché", start=3.0)),  # a word said after the line
    ("il fait 13,5 degrés", _fix("3,5", "X")),        # nor of a longer number
    ("made by UAS today", _fix("U.S.", "X")),         # a dot is a dot, not any character
])
def test_a_fix_that_is_not_in_the_line_leaves_it_alone(tmp_path, text, fix):
    assert _texts(tmp_path, [_line(text)], [fix]) == [text]


def test_a_fix_changes_the_line_it_was_said_in(tmp_path):
    lines = [_line("perche no", 0.0, 2.0), _line("perche sì", 2.0, 4.0)]
    assert _texts(tmp_path, lines, [_fix("perche", "perché", start=2.4)]) == ["perche no", "perché sì"]


def test_what_was_typed_is_burned_as_typed(tmp_path):
    # A replacement string reads "$&" as the word it replaces.
    assert _texts(tmp_path, [_line("costa cinque euro")], [_fix("cinque", "$&5")]) == ["costa $&5 euro"]


def test_a_muted_word_is_still_censored(tmp_path):
    mutes = [{"start": 0.4, "end": 0.9, "word": "perché"}, {"start": 1.2, "end": 1.6, "word": "damn,"}]
    assert _texts(tmp_path, [_line("ma perché, damn, no")], mutes=mutes) == ["ma p****é, d**n, no"]
    # Only in a line it is heard in.
    assert _texts(tmp_path, [_line("damn it", 3.0, 5.0)], mutes=mutes) == ["damn it"]


def test_a_muted_word_with_a_hyphen_is_censored(tmp_path):
    mutes = [{"start": 0.4, "end": 0.9, "word": "peut-être,"}]
    assert _texts(tmp_path, [_line("c'est peut-être, oui")], mutes=mutes) == ["c'est p*******e, oui"]


def test_a_fix_and_a_mute_in_one_line_both_show(tmp_path):
    texts = _texts(tmp_path, [_line("ma perche damn no")], [_fix("perche", "perché")],
                   [{"start": 1.0, "end": 1.4, "word": "damn"}])
    assert texts == ["ma perché d**n no"]


# ---- the editor around it -------------------------------------------------------------


def _source(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_the_editor_draws_and_sends_the_lines_this_module_makes():
    editor = _source("components/TimelineEditor.tsx")
    assert "from '../lib/captionEdits'" in editor
    assert "const applyTextEdits" not in editor and "censorMatch" not in editor
    # The overlay and Apply both.
    assert editor.count("applyTextEdits(base, wordEdits, edit.muted_words)") == 2


def test_a_word_typed_and_clicked_away_from_is_kept():
    editor = _source("components/TimelineEditor.tsx")
    assert re.search(r"if \(e\.key === 'Enter'\) keepWord\(w, editingWord\.value\)", editor)
    assert re.search(r"onBlur=\{\(\) => \{\s*if \(!wordBoxDone\.current\) keepWord\(w, editingWord\.value\)", editor)


def test_escape_in_the_word_box_drops_that_word_and_leaves_the_editor_open():
    # EditorModal closes the editor on Escape, from a listener on the window
    # that the word box's Escape used to reach: one word given up closed the
    # editor with every edit in it.
    editor = _source("components/TimelineEditor.tsx")
    assert re.search(r"if \(e\.key === 'Escape'\) \{\n(\s*//.*\n)*\s*e\.stopPropagation\(\)\n\s*dropWord\(\)", editor)
    modal = _source("components/EditorModal.tsx")
    assert re.search(r"t\.tagName === 'TEXTAREA' \|\|\s*t\.isContentEditable \|\|\s*"
                     r"\(t instanceof HTMLInputElement && !NOT_TYPING\.has\(t\.type\)\)\)\s*"
                     r"if \(e\.key === 'Escape' && !typing\) onClose\(\)", modal)
    # A slider or a checkbox isn't typing: Escape from one still leaves the editor.
    kinds = re.search(r"const NOT_TYPING = new Set\(\[([^\]]*)\]\)", modal)
    assert kinds and {"'range'", "'checkbox'"} <= {k.strip() for k in kinds.group(1).split(",")}


def test_apply_leaves_the_draft_and_the_clip_list_follows_the_new_id():
    editor = _source("components/TimelineEditor.tsx")
    assert re.search(r"await api\.rerenderClip\(clip\.id, undefined, renderOpts, usedSuggestions\(\)\)\n"
                     r"(\s*//.*\n)*\s*backToOriginal\(\)", editor)
    studio = _source("pages/ClipStudio.tsx")
    # Noted as the event comes, before any list is read...
    assert re.search(r"e\.type === 'clip_replaced'\) \{\n.*\n\s*moved\.current\.set\(e\.old, e\.new\)\n"
                     r"\s*if \(activeVideo\) void refreshClips\(activeVideo\)", studio)
    # ...and followed by every refresh, in the update that brings the list
    # (the job's own 'done' can bring it first), to an id that list has.
    refresh = studio[studio.index("const refreshClips"):studio.index("useEffect(", studio.index("const refreshClips"))]
    assert re.search(r"setClips\(c\)\n(\s*//.*\n)*\s*const follow = ", refresh)
    assert "return to !== undefined && c.some((x) => x.id === to) ? to : id" in refresh
    assert "setEditingClipId(follow)" in refresh and "setSelectedClip(follow)" in refresh


def test_a_word_both_corrected_and_muted_is_censored_as_corrected(tmp_path):
    # Looked for by Whisper's spelling after the fix had replaced it, the mute
    # found nothing and the corrected word went out uncensored.
    fixes = [_fix("fuk", "fuck", start=0.5)]
    mutes = [{"start": 0.4, "end": 0.9, "word": "fuk"}]
    assert _texts(tmp_path, [_line("fuk this")], fixes, mutes) == ["f**k this"]
    # A fix to another word in the line leaves the mute as it was.
    fixes = [_fix("this", "that", start=1.2)]
    assert _texts(tmp_path, [_line("fuk this")], fixes, mutes) == ["f*k that"]
