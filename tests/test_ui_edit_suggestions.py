"""Suggested edits in the timeline editor (ui/src/renderer/src/lib/editSuggestions.ts).

The TypeScript runs under Node here, as tests/test_ui_steps.py runs
lib/steps.ts, and is checked against the engine's own code: the shared cases
in tests/fixtures/edit_marks/ that plugins/edit_marks.py records a render
by, and video_editor/timeline.py, which renders the edit. The editor has no
React test harness, so the parts that live in TimelineEditor.tsx and the
job form are checked in their source.

Times in a suggestion are seconds of the video; the editor's are seconds of
the clip. The clip here is 100-130 s of a Quarkbloom Arena (a made-up game)
video, unless a test moves it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins import edit_marks
from video_editor.timeline import EditList

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
LIB = UI / "lib"
FIXTURES = ROOT / "tests" / "fixtures" / "edit_marks"
AFTER_RENDER = json.loads((FIXTURES / "after_render.json").read_text(encoding="utf-8"))
CARRY = json.loads((FIXTURES / "carry.json").read_text(encoding="utf-8"))
# The suggestion the shared cases use: a cut, a mute, a fade out, a hook title and a layout.
ENTRY = AFTER_RENDER["cases"][0]["entries"][0]
WINDOW = (100.0, 130.0)
# The clip's transcript words, in seconds of the clip (api.clipWords).
WORDS = [
    {"start": 8.3, "end": 8.6, "word": "Quark"},
    {"start": 9.0, "end": 9.4, "word": "burst"},
    {"start": 15.0, "end": 15.4, "word": "respawn"},
    {"start": 15.5, "end": 15.9, "word": "timer"},
]


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _run(tmp_path, body: str, data=None):
    """Run `body` (JavaScript: `m` is lib/editSuggestions.ts, `data` the JSON
    given, `stateOf(opts, duration)` the editor's state for saved render
    options, as the editor loads them) and return what it prints as JSON."""
    module = tmp_path / "editSuggestions.ts"
    module.write_text((LIB / "editSuggestions.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        "const fs = await import('node:fs');"
        f"const data = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'data.json'))}, 'utf8'));"
        "const stateOf = (opts, duration) => ({edit: {...m.defaultEdit(duration), ...((opts && opts.edit) || {})},"
        " layout: (opts && opts.crop) || 'track'});"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _norm(x):
    """Lists for tuples, and numbers to 6 decimals, so TypeScript's and Python's answers compare."""
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_norm(v) for v in x]
    if isinstance(x, float):
        return round(x, 6) + 0.0
    if isinstance(x, int) and not isinstance(x, bool):
        return float(x)
    return x


def _opts(state: dict) -> dict:
    """The editor's state as the render options Apply saves."""
    return {"edit": state["edit"], "crop": state["layout"]}


def _source(name: str) -> str:
    return (UI / "components" / name).read_text(encoding="utf-8")


# ---- what a suggestion says --------------------------------------------------------------------


def test_a_suggestion_reads_in_the_editors_own_words(tmp_path):
    """One line per part, in the editor's names: a mute says how many words
    it hides in the captions, or that they are unchanged; a hook title is a
    "Hook title". The card says who suggested it and why, in quotes."""
    other = {"cuts": [[103.0, 104.0], [110.0, 111.5]], "mutes": [[114.9, 115.95], [120.0, 120.5]],
             "fade_in": 0.3, "speed": 1.25, "volume": 0.8, "crop": "letterbox"}
    quiet = {"mutes": [[120.0, 120.5]]}
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const said = []
        m.summaryParts(data.entry.edit, ctx, (s) => { said.push(s); return s })
        return [m.summaryParts(data.entry.edit, ctx), m.summaryParts(data.other, ctx),
                m.summaryParts(data.quiet, ctx), m.summaryParts(data.quiet, {start: 100, end: 130}),
                m.summaryParts({crop: 'square', title_overlay: {text: '  '}, speed: 1, volume: 1}, ctx), said,
                m.suggestionCards([data.entry], stateOf(null, 30), {}, ctx).cards[0]]
    """, {"entry": ENTRY, "other": other, "quiet": quiet, "words": WORDS})
    assert got[0] == ["Cuts 1 part · 4.5 s shorter", "Mutes 1 part · hides 1 word in the captions", "Fades out 0.5 s",
                      "Hook title: “Quark burst!” for 3 s", "Layout: Center"]
    assert got[1] == ["Cuts 2 parts · 2.5 s shorter", "Mutes 2 parts · hides 2 words in the captions",
                      "Fades in 0.3 s", "Speed 1.25×", "Volume 80%", "Layout: Letterbox"]
    assert got[2] == ["Mutes 1 part · captions are unchanged"]
    # Without the clip's words it doesn't count them.
    assert got[3] == ["Mutes 1 part"]
    # An unknown layout, a blank hook title and values that change nothing say nothing.
    assert got[4] == []
    # Through tr, so the words can be translated.
    assert {"Cuts", "part", "s shorter", "Mutes", "hides", "word in the captions", "Fades out", "Hook title", "for",
            "Layout", "Center"} <= set(got[5])
    # The layouts by the names on the editor's Layout buttons.
    lib = (LIB / "editSuggestions.ts").read_text(encoding="utf-8")
    names = re.search(r"const LAYOUTS: Record<string, string> = \{(.*?)\}", lib).group(1)
    layouts = dict(re.findall(r"(\w+): '([^']+)'", names))
    buttons = dict(re.findall(r"\['(\w+)', '([^']+)',", _source("TimelineEditor.tsx")))
    assert layouts == {"track": "Auto (AI)", "letterbox": "Letterbox", "center": "Center"}
    assert all(buttons.get(key) == name for key, name in layouts.items())
    card = got[6]
    assert card["title"] == "Suggested by Quarkbloom Trimmer 1.0.0"
    assert card["parts"] == got[0]
    assert card["reason"] == "“Cuts the wait for the respawn timer”"
    assert (card["notes"], card["status"], card["actions"], card["useOff"]) == ([], "", ["use", "hide"], "")


def test_the_clip_panel_line_says_who_what_why_and_what_came_of_it(tmp_path):
    entries = [ENTRY, {**ENTRY, "state": "used"}, {**ENTRY, "state": "used", "remade": True},
               {**ENTRY, "state": "hidden", "name": "", "version": "", "reason": ""}]
    got = _run(tmp_path, "return data.map((e) => m.suggestionLine(e, {start: 100, end: 130}))", entries)
    what = ("Cuts 1 part · 4.5 s shorter · Mutes 1 part · Fades out 0.5 s · Hook title: “Quark burst!” for 3 s · "
            "Layout: Center")
    assert got == [
        f"Edit suggested by Quarkbloom Trimmer 1.0.0: {what} · Cuts the wait for the respawn timer",
        f"Edit suggested by Quarkbloom Trimmer 1.0.0: {what} · Cuts the wait for the respawn timer (used)",
        (f"Edit suggested by Quarkbloom Trimmer 1.0.0: {what} · Cuts the wait for the respawn timer (used, but the "
         "clip was made again without it)"),
        f"Edit suggested by example-dev/quarkbloom-trimmer: {what} (hidden)",
    ]
    panel = _source("ClipEditor.tsx")
    assert "{suggestionLine(e, { start: clip.start_s, end: clip.end_s })}" in panel
    # Clip Studio's chip shows while a suggestion is new.
    chips = _run(tmp_path, "return [m.hasNewSuggestion(data), m.hasNewSuggestion(data.slice(1)),"
                           " m.hasNewSuggestion(undefined), m.hasNewSuggestion([null, {id: 'x', state: 'new'}])]",
                 entries)
    assert chips == [True, False, False, False]
    card = _source("ClipCard.tsx")
    assert "const suggested = hasNewSuggestion(clip.scores?.plugin_edits)" in card
    assert "Suggested edit\n" in card and "${suggested ? ', suggested edit' : ''}" in card


# ---- Use -----------------------------------------------------------------------------------------


def test_cuts_become_keep_over_a_changed_window_and_never_cut_what_was_added(tmp_path):
    """The plugin answered for 100-130 s. Converted with the clip's start now
    and clamped to it, the cuts land on the same seconds of the video, and
    what the creator added at either end is kept."""
    edit = {"cuts": [[100.0, 103.0], [128.0, 130.0]]}
    windows = [[98.0, 132.0], [101.0, 130.0], [104.0, 126.0], [100.0, 130.0]]
    got = _run(tmp_path, """
        return data.windows.map(([start, end]) => {
          const ctx = {start, end}
          const used = m.applySuggestion(stateOf(null, end - start), data.edit, ctx)
          return {keep: used.state.edit.keep, removed: used.delta.removed, changed: used.changed,
                  parts: m.summaryParts(data.edit, ctx)}
        })
    """, {"edit": edit, "windows": windows})
    assert got[0]["keep"] == [[0, 2], [5, 30], [32, 34]]
    assert got[0]["parts"] == ["Cuts 2 parts · 5 s shorter"]
    assert got[1]["keep"] == [[2, 27]]
    # Wholly outside the clip now: nothing to cut, and Use changes nothing.
    assert (got[2]["keep"], got[2]["removed"], got[2]["changed"], got[2]["parts"]) == ([[0, 22]], [], False, [])
    assert got[3]["keep"] == [[3, 28]]
    for (start, end), answer in zip(windows, got):
        cut = edit_marks.intersect(edit["cuts"], [(start, end)])
        # What Use takes out is exactly the suggestion's cuts inside the clip, in seconds of the video...
        assert _norm([[a + start, b + start] for a, b in answer["removed"]]) == _norm(cut)
        # ...and the render keeps everything else, the added ends too.
        rendered = EditList.from_dict({"keep": answer["keep"]}, end - start)
        kept = [(start, end)] if rendered is None or rendered.keep is None else \
            [(start + a, start + b) for a, b in rendered.keep]
        assert _norm(edit_marks.minus([(start, end)], cut)) == _norm(kept)
    assert _norm(got[0]["keep"][0]) == [0.0, 2.0] and _norm(got[0]["keep"][-1]) == [32.0, 34.0]  # 98-100, 130-132


def test_cuts_add_to_the_creators_mutes_append_and_values_replace(tmp_path):
    """Laid over the creator's own edit: the cuts are added (their cut stays),
    the mute is appended beside their word mute, never merged with it, the
    words inside it are hidden, and fades, volume, speed, the hook title and
    the layout replace theirs. The delta holds only what Use added."""
    edit = {"cuts": [[102.0, 106.5], [119.0, 121.0]], "mutes": [[114.9, 115.95]], "fade_out": 0.5, "volume": 0.8,
            "speed": 1.25, "title_overlay": {"text": "Quark burst!", "seconds": 3}, "crop": "center"}
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        let s = stateOf({edit: {keep: [[0, 20], [22, 30]], fade_out: 0.3, volume: 1.2}, crop: 'letterbox'}, 30)
        s = {...s, edit: m.toggledWord(s.edit, data.words[2], 30)}
        const used = m.applySuggestion(s, data.edit, ctx)
        const again = m.applySuggestion(used.state, data.edit, ctx)
        return {before: s, notes: m.replaceNotes(data.edit, s, ctx), used,
                again: again.changed, notesAfter: m.replaceNotes(data.edit, used.state, ctx)}
    """, {"edit": edit, "words": WORDS})
    respawn = {"start": 15.0, "end": 15.4, "word": "respawn"}
    timer = {"start": 15.5, "end": 15.9, "word": "timer"}
    assert got["before"]["edit"]["mutes"] == [[14.96, 15.44]]
    after, delta = got["used"]["state"], got["used"]["delta"]
    assert after["edit"]["keep"] == [[0, 2], [6.5, 19], [22, 30]]
    assert delta["removed"] == [[2, 6.5], [19, 20]]  # 20-22 was the creator's own cut
    assert after["edit"]["mutes"] == [[14.96, 15.44], [14.9, 15.95]]
    assert delta["mutes"] == [[14.9, 15.95]]
    assert after["edit"]["muted_words"] == [respawn, timer]
    assert delta["muted_words"] == [timer]  # respawn was already muted by hand
    assert {k: after["edit"][k] for k in ("fade_in", "fade_out", "volume", "speed", "mute_all", "music")} == {
        "fade_in": 0, "fade_out": 0.5, "volume": 0.8, "speed": 1.25, "mute_all": False, "music": None}
    assert after["edit"]["hook"] == {"text": "Quark burst!", "seconds": 3}
    assert after["layout"] == "center"
    assert delta["values"] == {
        "volume": {"before": 1.2, "after": 0.8}, "fade_out": {"before": 0.3, "after": 0.5},
        "speed": {"before": 1, "after": 1.25}, "hook": {"before": None, "after": {"text": "Quark burst!", "seconds": 3}},
        "crop": {"before": "letterbox", "after": "center"}}
    assert got["notes"] == ["Replaces your fade out (0.3 s → 0.5 s)", "Replaces your volume (120% → 80%)",
                            "Replaces your layout (Letterbox → Center)"]
    # A second Use finds it all there already, and nothing left to replace.
    assert got["again"] is False and got["notesAfter"] == []
    # The render merges the two mutes itself; the editor keeps them apart.
    rendered = EditList.from_dict(after["edit"], 30.0)
    assert _norm(rendered.mutes) == [[14.9, 15.95]]
    assert _norm(edit_marks.view(_opts(after), WINDOW)["removed"]) == [[102.0, 106.5], [119.0, 122.0]]


def test_a_suggested_mute_hides_the_words_inside_it(tmp_path):
    """Every transcript word whose middle is inside a suggested mute is added
    to muted_words, so the captions hide it as a hand word mute does. A span
    with no word inside leaves the captions as they are, and says so."""
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data}
        const s = stateOf(null, 30)
        const quark = m.applySuggestion(s, {mutes: [[108.25, 108.75]]}, ctx)
        const none = m.applySuggestion(s, {mutes: [[120.0, 120.5]]}, ctx)
        const edge = m.applySuggestion(s, {mutes: [[109.0, 109.15]]}, ctx)
        return [quark.state.edit.muted_words, quark.delta.muted_words, m.wordMuted(quark.state.edit, data[0]),
                none.state.edit.muted_words, none.state.edit.mutes, m.summaryParts({mutes: [[120.0, 120.5]]}, ctx),
                edge.state.edit.muted_words, m.summaryParts({mutes: [[109.0, 109.15]]}, ctx)]
    """, WORDS)
    assert got[0] == got[1] == [WORDS[0]]
    assert got[2] is True
    assert got[3] == [] and got[4] == [[20, 20.5]]
    assert got[5] == ["Mutes 1 part · captions are unchanged"]
    # "burst" (9.0-9.4) starts inside this span but its middle doesn't: not hidden, and the card agrees.
    assert got[6] == [] and got[7] == ["Mutes 1 part · captions are unchanged"]
    editor = _source("TimelineEditor.tsx")
    assert "const wordMuted = (w: Word): boolean => isWordMuted(edit, w)" in editor
    assert "const toggleWord = (w: Word): void => push(toggledWord(edit, w, duration))" in editor


def test_a_hand_word_mute_still_toggles_off_after_a_use_over_it(tmp_path):
    """The creator muted "respawn" by hand, then used a suggestion whose mute
    covers it. Unmuting "respawn" removes their own span and caption entry;
    the suggestion's span stays. Merged into one span, theirs couldn't be
    found again, and the word's sound would stay muted with its caption back."""
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data}
        let s = stateOf(null, 30)
        s = {...s, edit: m.toggledWord(s.edit, data[2], 30)}
        const used = m.applySuggestion(s, {mutes: [[114.9, 115.95]]}, ctx).state
        const off = m.toggledWord(used.edit, data[2], 30)
        const timerOff = m.toggledWord(used.edit, data[3], 30)
        const merged = m.toggledWord({...used.edit, mutes: m.spans(used.edit.mutes)}, data[2], 30)
        return [used.edit.mutes, used.edit.muted_words, off.mutes, off.muted_words, m.wordMuted(off, data[2]),
                timerOff.mutes, timerOff.muted_words, merged.mutes]
    """, WORDS)
    assert got[0] == [[14.96, 15.44], [14.9, 15.95]]
    assert got[1] == [WORDS[2], WORDS[3]]
    assert got[2] == [[14.9, 15.95]] and got[3] == [WORDS[3]] and got[4] is False
    # Unmuting a word the suggestion hid brings its caption back; its span keeps the sound silent.
    assert got[5] == got[0] and got[6] == [WORDS[2]]
    # What merging would have done: the creator's span can't be found to remove.
    assert got[7] == [[14.9, 15.95]]


def test_replaces_appears_only_when_it_does(tmp_path):
    suggestion = {"fade_in": 0.3, "fade_out": 0.5, "speed": 2, "volume": 0.8,
                  "title_overlay": {"text": "Quark burst!", "seconds": 3}, "crop": "letterbox"}
    cases = [
        ({}, "track", suggestion, None, []),
        ({"fade_out": 0.3}, "track", suggestion, None, ["Replaces your fade out (0.3 s → 0.5 s)"]),
        ({"fade_out": 0.5}, "track", suggestion, None, []),
        ({"fade_in": 1}, "track", suggestion, None, ["Replaces your fade in (1 s → 0.3 s)"]),
        ({"speed": 1.5}, "track", suggestion, None, ["Replaces your speed (1.5× → 2×)"]),
        ({"speed": 2}, "track", suggestion, None, []),
        ({"volume": 1.2}, "track", suggestion, None, ["Replaces your volume (120% → 80%)"]),
        ({"hook": {"text": "GG", "seconds": 3}}, "track", suggestion, None, ["Replaces your hook title"]),
        ({"hook": {"text": "Quark burst!", "seconds": 3}}, "track", suggestion, None, []),
        ({}, "center", suggestion, None, ["Replaces your layout (Center → Letterbox)"]),
        ({}, "letterbox", suggestion, None, []),
        ({}, "center", suggestion, "gaming", []),
        # The creator's values, and a suggestion that doesn't touch them.
        ({"fade_out": 0.3, "speed": 1.5, "volume": 1.2, "hook": {"text": "GG", "seconds": 3}}, "center",
         {"cuts": [[102.0, 104.0]]}, None, []),
    ]
    got = _run(tmp_path, """
        return data.map(([edit, layout, suggestion, noLayout]) =>
          m.replaceNotes(suggestion, stateOf({edit, crop: layout}, 30), {start: 100, end: 130, noLayout}))
    """, [[edit, layout, s, why] for edit, layout, s, why, _ in cases])
    assert got == [notes for *_, notes in cases]
    # Only a new suggestion's card names them.
    cards = _run(tmp_path, """
        const s = stateOf({edit: {fade_out: 0.3}}, 30)
        return [{...data, state: 'new'}, {...data, state: 'used'}]
          .map((e) => m.suggestionCards([e], s, {}, {start: 100, end: 130}).cards[0].notes)
    """, ENTRY)
    assert cards == [["Replaces your fade out (0.3 s → 0.5 s)"], []]


def test_a_layout_is_never_set_on_gaming_vertical_live_or_landscape_clips(tmp_path):
    got = _run(tmp_path, """
        return [null, 'gaming', 'vertical_live', 'landscape'].map((why) => {
          const ctx = {start: 100, end: 130, words: data, noLayout: why ?? undefined}
          const s = stateOf(null, 30)
          const entry = {id: 'a1', plugin: 'example-dev/quarkbloom-framer', state: 'new',
                         edit: {fade_out: 0.5, crop: 'center'}}
          const used = m.applySuggestion(s, entry.edit, ctx)
          const delta = {removed: [], mutes: [], muted_words: [],
                         values: {crop: {before: 'letterbox', after: 'center'}}}
          const card = m.suggestionCards([entry], s, {}, ctx).cards[0]
          const only = m.suggestionCards([{...entry, edit: {crop: 'center'}}], s, {}, ctx).cards[0]
          return {layout: used.state.layout, crop: 'crop' in used.delta.values, fade: used.state.edit.fade_out,
                  notes: card.notes, useOff: card.useOff, onlyOff: only.useOff,
                  back: m.takeBack({...s, layout: 'center'}, delta, ctx).state.layout}
        })
    """, WORDS)
    plain, gaming, vertical_live, landscape = got
    assert plain == {"layout": "center", "crop": True, "fade": 0.5, "notes": [], "useOff": "", "onlyOff": "",
                     "back": "letterbox"}
    notes = {"Not used on this clip: layout (a Gaming / Reaction clip keeps its split)": gaming,
             "Not used on this clip: layout (Vertical Live keeps the stream’s own 9:16 layout)": vertical_live,
             "Not used on this clip: layout (a 16:9 clip has no layout to choose)": landscape}
    for note, answer in notes.items():
        # The rest of the suggestion is used; the layout stays as it is, and Take it back never sets one.
        assert answer == {"layout": "track", "crop": False, "fade": 0.5, "notes": [note], "useOff": "",
                          "onlyOff": "Nothing in this suggestion can be used on this clip.", "back": "center"}
    editor = _source("TimelineEditor.tsx")
    assert ("noLayout: isLandscape ? 'landscape' : isVerticalLive ? 'vertical_live' : gaming ? 'gaming' : undefined"
            in editor)


def test_an_unrounded_window_gives_no_made_before_note(tmp_path):
    """The window is kept to 2 decimals, as the clip's start and end are; a
    difference within 0.01 s is no change. Only a suggestion with cuts or
    mutes, whose times would land elsewhere, gets the note."""
    note = "Made before you changed this clip’s start or end. Check the cuts before you apply."
    windows = [[100.004, 129.996], [100.0099, 130.0049], [99.995, 130.0], [100.012, 130.0], [101.0, 130.0],
               [100.0, 131.0]]
    got = _run(tmp_path, """
        const card = (entry, start, end) => m.suggestionCards([entry], stateOf(null, end - start), {}, {start, end})
          .cards[0].notes
        return [data.windows.map(([start, end]) => [m.madeForAnotherWindow(data.entry, {start, end}),
                                                    card(data.entry, start, end).includes(data.note)]),
                card({...data.entry, window: [100.003, 129.998]}, 100, 130),
                card({...data.entry, edit: {fade_out: 0.5}}, 101, 130),
                card({...data.entry, window: undefined}, 101, 130)]
    """, {"entry": ENTRY, "windows": windows, "note": note})
    assert got[0] == [[False, False]] * 3 + [[True, True]] * 3
    assert got[1] == got[2] == got[3] == []


def test_use_is_off_under_one_second_and_warns_under_the_shortest_clip(tmp_path):
    """With the creator's own cuts: under 1 s left (pieces under 0.25 s don't
    count) Use is off; under the shortest clip the video was made with, the
    card warns and the creator decides."""
    cases = [
        ({"cuts": [[100.0, 129.2]]}, None, 10),
        ({"cuts": [[100.5, 100.9]]}, {"keep": [[0, 1.2]]}, 10),
        ({"cuts": [[100.1, 129.0]]}, None, 10),  # 0-0.1 s is under 0.25 s: 1 s is left
        ({"cuts": [[100.0, 124.0]]}, None, 10),
        ({"speed": 2}, None, 20),
        ({"cuts": [[100.0, 118.0]]}, None, 10),
        ({"cuts": [[100.0, 124.0]]}, None, 0),
        ({"fade_out": 0.5}, {"keep": [[0, 3]]}, 10),
        # The creator kept 0-5 s, and the suggestion cuts all of it, or all but 0.1 s: no piece is left.
        ({"cuts": [[100.0, 105.0]]}, {"keep": [[0, 5]]}, 10),
        ({"cuts": [[100.0, 104.9]]}, {"keep": [[0, 5]]}, 10),
    ]
    got = _run(tmp_path, """
        return data.cases.map(([edit, keep, min]) => {
          const card = m.suggestionCards([{...data.entry, edit, min_length: min}], stateOf({edit: keep}, 30), {},
                                         {start: 100, end: 130}).cards[0]
          return [card.useOff, card.notes]
        })
    """, {"entry": ENTRY, "cases": [[edit, keep or {}, least] for edit, keep, least in cases]})
    off = "With your own cuts, these would leave almost nothing of the clip."
    assert got == [
        [off, []],
        [off, []],
        ["", ["With your own cuts, this leaves 1.0 s, shorter than the 10 s shortest clip this video was made with."]],
        ["", ["With your own cuts, this leaves 6.0 s, shorter than the 10 s shortest clip this video was made with."]],
        ["", [("With your own cuts, this leaves 15.0 s, shorter than the 20 s shortest clip this video was made "
               "with.")]],
        ["", []],
        ["", []],
        ["", []],
        [off, []],
        [off, []],
    ]
    cards = _source("EditSuggestions.tsx")
    assert "disabled={busy || Boolean(card.useOff)}" in cards
    # The editor never draws a suggestion its card turns Use off for, so it never writes an empty keep.
    editor = _source("TimelineEditor.tsx")
    assert re.search(r"const off = suggestionView\.cards\.find\(\(c\) => c\.id === id\)\?\.useOff\s*"
                     r"if \(off\) \{\s*setNotice\(off\)\s*return", editor)


def test_use_waits_for_the_clips_words_when_the_suggestion_mutes(tmp_path):
    """Until the clip's words are read, a suggested mute couldn't hide them in
    the captions, so Use is off for a suggestion with mutes, and the card
    says nothing about the captions. Words read as none (no transcript) are
    an answer: Use is on."""
    got = _run(tmp_path, """
        const card = (words, edit) => m.suggestionCards([{...data.entry, edit}], stateOf(null, 30), {},
                                                        {start: 100, end: 130, words}).cards[0]
        const mute = {mutes: [[108.25, 108.75]]}
        return [card(undefined, mute), card(data.words, mute), card([], mute), card(undefined, {fade_out: 0.5}),
                m.WORDS_PENDING]
    """, {"entry": ENTRY, "words": WORDS})
    waiting, read, none, fade, pending = got
    assert waiting["useOff"] == pending and waiting["parts"] == ["Mutes 1 part"]
    assert pending == "Use is off until this clip’s words are read, so its mutes can hide them in the captions too."
    assert read["useOff"] == "" and read["parts"] == ["Mutes 1 part · hides 1 word in the captions"]
    assert none["useOff"] == "" and none["parts"] == ["Mutes 1 part · captions are unchanged"]
    assert fade["useOff"] == ""
    editor = _source("TimelineEditor.tsx")
    assert "words: wordsRead ? words : undefined," in editor
    assert re.search(r"setWords\(r\.words\)\s*setWordsRead\(true\)", editor)
    assert re.search(r"setWordsRead\(false\)\s*api\s*\.clipWords\(clip\.id\)", editor)


# ---- Take it back, Undo and Reset ------------------------------------------------------------------


def test_take_it_back_takes_out_only_the_suggestions_parts_after_a_mixed_session(tmp_path):
    """Use, then by hand: a cut, a word mute, a fade in, a new fade out and a
    trim of both ends. Take it back puts back only the suggestion's cut
    inside the trimmed clip, removes its own mute and word, and restores the
    hook title and layout, which are still the suggestion's; the fade out the
    creator changed stays theirs."""
    edit = {"cuts": [[102.0, 106.5], [127.0, 129.0]], "mutes": [[108.25, 108.75]], "fade_out": 0.5,
            "title_overlay": {"text": "Quark burst!", "seconds": 3}, "crop": "center"}
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const start = stateOf({edit: {keep: [[0, 20], [22, 30]], fade_out: 0.3}, crop: 'letterbox'}, 30)
        const used = m.applySuggestion(start, data.edit, ctx)
        let s = used.state
        s = {...s, edit: {...s.edit, keep: m.minus(s.edit.keep, [[10, 12]])}}                // a hand cut
        s = {...s, edit: m.toggledWord(s.edit, data.words[2], 30)}                          // a word mute
        s = {...s, edit: {...s.edit, fade_in: 1, fade_out: 0.8}}                            // the fades
        s = {...s, edit: {...s.edit, keep: m.intersect(s.edit.keep, [[1, 26]])}}            // a trim
        const back = m.takeBack(s, used.delta, ctx)
        const twice = m.takeBack(back.state, used.delta, ctx)
        const right = m.takeBack(used.state, used.delta, ctx)
        return {mixed: s, back, twice: twice.changed, right: right.state, start,
                held: m.held(used.delta, back.state, 30)}
    """, {"edit": edit, "words": WORDS})
    assert got["mixed"]["edit"]["keep"] == [[1, 2], [6.5, 10], [12, 20], [22, 26]]
    back = got["back"]["state"]
    assert got["back"]["changed"] is True
    # The cut 2-6.5 s comes back; 27-29 s is beyond the trim, which stays, as do both of the creator's cuts.
    assert back["edit"]["keep"] == [[1, 10], [12, 20], [22, 26]]
    assert back["edit"]["mutes"] == [[14.96, 15.44]]
    assert back["edit"]["muted_words"] == [WORDS[2]]
    assert (back["edit"]["fade_in"], back["edit"]["fade_out"]) == (1, 0.8)
    assert back["edit"]["hook"] is None and back["layout"] == "letterbox"
    # Nothing is left to take out: the editor says so and offers Hide.
    assert got["twice"] is False
    # 27-29 s is still out, but by the creator's trim, past Take it back's reach: the card agrees.
    assert got["held"] == {"removed": [], "mutes": [], "muted_words": [], "values": {}}
    # Straight after the Use, Take it back gives the creator's edit back exactly.
    assert got["right"] == got["start"]
    editor = _source("TimelineEditor.tsx")
    assert "pushSuggestion(back.state.edit, back.state.layout, { id, kind: 'take_back', delta: session })" in editor
    assert "fromApplied(entry.applied, clip.start_s)" in editor


def test_take_it_back_puts_back_a_suggested_trim_of_either_end(tmp_path):
    """A suggestion that trims the clip (the SDK's trim() and keep_only())
    cuts at its start or end. Take it back puts those cuts back while each
    edge is where the suggestion left it, in this session and after Apply,
    and the card agrees with the button, as the engine does. A trim the
    creator made since stays."""
    cases = [["ends", [[100.0, 103.0], [127.0, 130.0]]], ["start_and_middle", [[100.0, 103.0], [110.0, 112.0]]]]
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const s0 = stateOf(null, 30)
        const card = (entries, state, uses) => {
          const c = m.suggestionCards(entries, state, uses, ctx).cards[0]
          return [c.status, c.actions]
        }
        return data.cases.map(([id, cuts]) => {
          const entry = {...data.entry, id, edit: {cuts}, state: 'new'}
          const used = m.applySuggestion(s0, entry.edit, ctx)
          const back = m.takeBack(used.state, used.delta, ctx)
          const applied = m.toApplied(used.delta, 100)
          const stored = {...entry, state: 'used', applied}
          const later = m.takeBack(used.state, m.fromApplied(applied, 100), ctx)
          const keep = m.intersect(used.state.edit.keep, [[5, 30]])
          const trimmed = {...used.state, edit: {...used.state.edit, keep}}
          return {used: used.state, applied, back: back.state.edit.keep, changed: back.changed,
                  session: card([entry], used.state, {[id]: used.delta}),
                  sessionAfter: card([entry], back.state, {[id]: used.delta}),
                  stored: card([stored], used.state, {}), later: later.state.edit.keep,
                  laterAfter: card([stored], later.state, {}),
                  trimmed: m.takeBack(trimmed, used.delta, ctx).state.edit.keep}
        })
    """, {"entry": ENTRY, "words": WORDS, "cases": cases})
    ends, middle = got
    assert ends["used"]["edit"]["keep"] == [[3, 27]] and middle["used"]["edit"]["keep"] == [[3, 10], [12, 30]]
    added = "Added to your edit. It goes into the clip when you apply your edits. Undo takes it back."
    for case in got:
        assert case["changed"] is True and case["back"] == [[0, 30]] and case["later"] == [[0, 30]]
        assert case["session"] == [added, ["take_back"]]
        assert case["sessionAfter"] == ["Nothing of this suggestion is left in your edit.", ["hide"]]
        assert case["stored"] == ["You used this suggestion.", ["take_back"]]
        assert case["laterAfter"] == ["Nothing of this suggestion is left in your edit.", ["hide"]]
    # Trimmed to start at 5 s since: the start cut is out of reach and the trim stays; the end cut comes back.
    assert ends["trimmed"] == [[5, 30]] and middle["trimmed"] == [[5, 30]]
    # The engine agrees: after Apply the suggestion stays used with all of it, and once taken
    # back and applied it is hidden.
    for (sid, cuts), case in zip(cases, got):
        entry = {**ENTRY, "id": sid, "edit": {"cuts": cuts}, "state": "used", "applied": case["applied"]}
        saved = _opts(case["used"])
        (kept,), _ = edit_marks.after_render([entry], [], saved, saved, WINDOW)
        assert kept["state"] == "used" and _norm(kept["applied"]) == _norm(case["applied"])
        assert edit_marks.still_held([entry], sid, saved, WINDOW) is True
        back = {**saved, "edit": {**saved["edit"], "keep": case["back"]}}
        (kept,), _ = edit_marks.after_render([entry], [], saved, back, WINDOW)
        assert kept["state"] == "hidden"


def test_a_used_suggestion_taken_back_but_not_applied_offers_no_hide(tmp_path):
    """Take it back changes only the editor's edit. While the clip's saved
    edit still holds the suggestion, Hide would lose its Take it back (the
    API refuses it too): the card says to apply instead. Applying hides it."""
    case = next(c for c in AFTER_RENDER["cases"] if c["name"] == "take_it_back")
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const saved = stateOf(data.case.before, 30)
        const back = m.takeBack(saved, m.fromApplied(data.case.entries[0].applied, 100), ctx).state
        const card = (entries, state, savedState) =>
          m.suggestionCards(entries, state, {}, {...ctx, saved: savedState}).cards[0]
        const unapplied = card(data.case.entries, back, saved)
        const applied = card(data.case.entries, back, back)
        const remade = card([{...data.case.entries[0], remade: true}], back, saved)
        return [[unapplied.status, unapplied.actions], [applied.status, applied.actions], remade.actions,
                card(data.case.entries, saved, saved).actions]
    """, {"case": case, "words": WORDS})
    unapplied, applied, remade, before = got
    taken = "Taken out of your edit. Your own changes stay. Apply your edits to make the clip without it."
    assert unapplied == [taken, []]
    assert applied == ["Nothing of this suggestion is left in your edit.", ["hide"]]
    assert remade == ["make_again"] and before == ["take_back"]
    # The engine says the same about the saved edit.
    sid = case["entries"][0]["id"]
    assert edit_marks.still_held(case["entries"], sid, case["before"], case["window"]) is True
    assert edit_marks.still_held(case["entries"], sid, case["after"], case["window"]) is False
    editor = _source("TimelineEditor.tsx")
    assert "edit: { ...defaultEdit(duration), ...(baked ?? {}) }," in editor


def test_a_suggestion_step_holds_the_layout_only_when_it_changes_it():
    """Layout buttons push no Undo step, so a Use or Take it back that left the
    layout alone mustn't hold one: its Undo would take back a layout the
    creator chose by hand since."""
    editor = _source("TimelineEditor.tsx")
    step = "const step: Past = nextLayout !== layout ? { edit, layout, suggestion: mark } : { edit, suggestion: mark }"
    assert step in editor
    assert "setHistory((h) => pushed<Past>(h, step))" in editor


def test_reset_and_undo_clear_the_use_mark_and_later_edits_keep_it(tmp_path):
    """The Use marks live outside the Undo history: 31 later edits push the
    Use out of the 30 steps Undo keeps, and Apply still sends it. Undo of the
    Use drops its mark, Undo of a Take it back puts it back, and Reset clears
    them all, from the history too."""
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const s0 = stateOf(null, 30)
        const used = m.applySuggestion(s0, data.entry.edit, ctx)
        const id = data.entry.id
        const uses = {[id]: used.delta}
        const mark = {id, kind: 'use', delta: used.delta}
        let history = m.pushed([], {edit: s0.edit, layout: s0.layout, suggestion: mark})
        for (let i = 0; i < 31; i++) history = m.pushed(history, {edit: {...used.state.edit, volume: 1 + i / 100}})
        const taken = m.marksWithout(uses, [id])
        const short = m.pushed(m.pushed([], {edit: s0.edit, layout: 'track', suggestion: mark}),
                               {edit: used.state.edit})
        const reset = m.withoutMarks(short)
        return {steps: m.UNDO_STEPS, length: history.length, useInHistory: history.some((h) => 'suggestion' in h),
                sent: m.usedForRender(uses, [data.entry], 100), applied: m.toApplied(used.delta, 100),
                undone: m.marksAfterUndo(uses, mark), taken,
                restored: Object.keys(m.marksAfterUndo(taken, {id, kind: 'take_back', delta: used.delta})),
                server: m.marksAfterUndo(taken, {id, kind: 'take_back', delta: null}),
                reset, kept: 'suggestion' in short[0], afterReset: m.usedForRender({}, [data.entry], 100) ?? null}
    """, {"entry": ENTRY, "words": WORDS})
    assert got["steps"] == 30 and got["length"] == 31 and got["useInHistory"] is False
    assert got["sent"] == {"used": [{"id": ENTRY["id"], "applied": got["applied"]}]}
    assert got["undone"] == {} and got["taken"] == {} and got["server"] == {}
    assert got["restored"] == [ENTRY["id"]]
    assert all("suggestion" not in step for step in got["reset"])
    assert got["reset"][0]["layout"] == "track" and got["reset"][1]["edit"]["keep"] == [[0, 2], [6.5, 30]]
    assert got["kept"] is True  # the history it was given is left as it was
    assert got["afterReset"] is None
    editor = _source("TimelineEditor.tsx")
    assert ("type Past = { edit: EditData; layout?: string; suggestion?: UseMark } | { speakers: SpeakerTurn[] | null }"
            in editor)
    assert "setHistory((h) => pushed<Past>(h, { edit }))" in editor
    assert "setUses((u) => marksAfterUndo(u, mark))" in editor
    assert "if (last.layout !== undefined) setLayout(last.layout)" in editor
    assert re.search(r"setUses\(\{\}\)\s*"
                     r"setHistory\(\(h\) => withoutMarks\(h\.filter\(\(step\) => 'edit' in step\)\)\)", editor)
    assert re.search(r"setHistory\(\[\]\)\s*setUses\(\{\}\)", editor)  # another clip starts with none


# ---- what Apply records ------------------------------------------------------------------------------


def test_apply_sends_what_each_use_added_and_the_api_takes_it(tmp_path):
    """Apply edits and Apply edits & upload send {used: [{id, applied}]}, in
    seconds of the video; a suggestion the clip no longer has is left out,
    and the record stays inside what the API accepts."""
    many = [{**ENTRY, "id": f"s{i}"} for i in range(9)]
    got = _run(tmp_path, """
        const ctx = {start: 100, end: 130, words: data.words}
        const used = m.applySuggestion(stateOf(null, 30), data.entry.edit, ctx)
        const uses = Object.fromEntries(data.many.map((e) => [e.id, used.delta]))
        const wide = {...used.delta, removed: Array.from({length: 70}, (_, i) => [i * 0.4, i * 0.4 + 0.2])}
        return [m.usedForRender({...uses, gone: used.delta}, data.many, 100),
                m.usedForRender({[data.entry.id]: wide}, [data.entry], 100),
                m.usedForRender({gone: used.delta}, [data.entry], 100) ?? null,
                m.usedForRender({[data.entry.id]: used.delta}, undefined, 100) ?? null]
    """, {"entry": ENTRY, "many": many, "words": WORDS})
    assert [u["id"] for u in got[0]["used"]] == [f"s{i}" for i in range(8)]
    assert len(got[1]["used"][0]["applied"]["removed"]) == 64
    assert got[2] is None and got[3] is None
    for sent in got[:2]:
        assert _norm(edit_marks.clean_used(sent)) == _norm(sent)
    editor = _source("TimelineEditor.tsx")
    assert "await api.rerenderClip(clip.id, undefined, renderOpts, usedSuggestions())" in editor
    assert "pendingRender={dirty ? { render_opts: buildRenderOpts(), suggestions: usedSuggestions() } : null}" in editor
    assert "render_first: pendingRender ?? null" in _source("YouTubePanel.tsx")
    api = (LIB / "api.ts").read_text(encoding="utf-8")
    assert "...(suggestions ? { suggestions } : {})" in api
    assert "suggestion?: { id: string; state: 'hidden' | 'new' }" in api


def test_the_shared_cases_give_the_same_answer_as_the_engine(tmp_path):
    """tests/fixtures/edit_marks/: the editor's Use, Take it back and Reset
    make the render options each case renders, what it sends is what the
    engine records, its reading of the saved options is the engine's, and
    each card says what the engine will make of the clip."""
    cases = AFTER_RENDER["cases"]
    got = _run(tmp_path, """
        const byName = Object.fromEntries(data.cases.map((c) => [c.name, c]))
        const views = data.cases.map((c) => {
          const window = c.new_window ?? c.window
          const start = window[0], duration = window[1] - window[0]
          const shift = (list) => list.map(([a, b]) => [a + start, b + start])
          return [c.before, c.after].map((opts) => {
            const v = m.viewOf(stateOf(opts, duration), duration)
            return {kept: shift(v.kept), removed: shift(v.removed), mutes: shift(v.mutes),
                    words: v.words.map((w) => [w.start + start, w.end + start, w.word]), values: v.values}
          })
        })
        const ctx = {start: 100, end: 130, words: data.words}
        const first = byName.used_keeps_only_what_the_render_changed
        const use = m.applySuggestion(stateOf(first.before, 30), first.entries[0].edit, ctx)
        const undone = byName.nothing_in_the_render_is_not_marked
        const undoUse = m.marksAfterUndo({[undone.entries[0].id]: use.delta}, {id: undone.entries[0].id, kind: 'use'})
        const tb = byName.take_it_back
        const back = m.takeBack(stateOf(tb.before, 30), m.fromApplied(tb.entries[0].applied, 100), ctx)
        const held = data.cases.filter((c) => c.entries[0].state === 'used').map((c) => {
          const window = c.new_window ?? c.window
          const start = window[0], duration = window[1] - window[0]
          const state = stateOf(c.after, duration)
          const card = m.suggestionCards(c.entries, state, {}, {start, end: window[1]}).cards[0]
          const still = m.held(m.fromApplied(c.entries[0].applied, start), state, duration)
          return {name: c.name, applied: m.toApplied(still, start), status: card.status, actions: card.actions,
                  notes: card.notes}
        })
        return {views, use: {state: use.state, sent: m.usedForRender({[first.entries[0].id]: use.delta},
                                                                       first.entries, 100)},
                undone: m.usedForRender(undoUse, undone.entries, 100) ?? null, back: back.state, held}
    """, {"cases": cases, "words": WORDS})
    by_name = {c["name"]: c for c in cases}

    # The editor reads saved options as the engine does.
    for case, views in zip(cases, got["views"]):
        window = case.get("new_window") or case["window"]
        for opts, view in zip((case["before"], case["after"]), views):
            assert _norm(view) == _norm(edit_marks.view(opts, window)), case["name"]

    # Use, then Apply: the options are the case's, and the engine records all the Use sent.
    first = by_name["used_keeps_only_what_the_render_changed"]
    used = got["use"]
    assert _norm(edit_marks.view(_opts(used["state"]), WINDOW)) == _norm(edit_marks.view(first["after"], WINDOW))
    assert _norm(used["sent"]["used"][0]["applied"]) == _norm(first["expect"]["entries"][0]["applied"])
    entries, lines = edit_marks.after_render(first["entries"], used["sent"]["used"], first["before"],
                                             _opts(used["state"]), first["window"])
    assert _norm(entries) == _norm(first["expect"]["entries"]) and lines == []

    # Use, then Undo: the editor sends nothing, and the suggestion stays new.
    undone = by_name["nothing_in_the_render_is_not_marked"]
    assert got["undone"] is None
    entries, _ = edit_marks.after_render(undone["entries"], [], undone["before"], undone["after"], undone["window"])
    assert entries == undone["expect"]["entries"]

    # Take it back, then Apply: the creator's own edit, and the suggestion is hidden.
    taken = by_name["take_it_back"]
    assert _norm(edit_marks.view(_opts(got["back"]), WINDOW)) == _norm(edit_marks.view(taken["after"], WINDOW))
    entries, lines = edit_marks.after_render(taken["entries"], [], taken["before"], _opts(got["back"]),
                                             taken["window"])
    assert entries == taken["expect"]["entries"] and lines == taken["expect"]["lines"]

    # Every later render: what the editor finds still in the clip is what the engine keeps, and the
    # card says it before the creator applies.
    assert [h["name"] for h in got["held"]] == ["take_it_back", "reset", "hand_change", "moved_start",
                                                "make_it_again_drops_remade"]
    for held in got["held"]:
        expect = by_name[held["name"]]["expect"]["entries"][0]
        if expect["state"] == "hidden":
            assert held["applied"] == {}, held["name"]
            assert (held["status"], held["actions"]) == ("Nothing of this suggestion is left in your edit.", ["hide"])
        else:
            assert _norm(held["applied"]) == _norm(expect["applied"]), held["name"]
            assert held["status"] == "You used this suggestion."
            assert held["actions"][0] == "take_back"
    remade = got["held"][-1]
    assert remade["actions"] == ["take_back", "make_again"]
    assert remade["notes"] == ["This clip was made again without your saved edits, so its file doesn’t have them."]


def test_the_cards_read_every_state_a_rerun_leaves(tmp_path):
    """After plugins/edit_marks.carry: a used suggestion offers Take it back
    (and Make it again with my edits when remade), a new one Use and Hide,
    and a hidden one only counts toward "Show"."""
    carried = [edit_marks.carry(c["old"], c["new"], c["kept"], c["rendered"]) for c in CARRY["cases"]]
    states = [c["kept"] for c in CARRY["cases"]]
    got = _run(tmp_path, """
        return data.carried.map((entries, i) => {
          const view = m.suggestionCards(entries, stateOf(data.states[i], 30), {}, {start: 100, end: 130})
          return {cards: view.cards.map((c) => [c.id, c.actions, c.notes.includes(data.remade)]), hidden: view.hidden}
        })
    """, {"carried": carried, "states": states,
          "remade": "This clip was made again without your saved edits, so its file doesn’t have them."})
    seen = set()
    for entries, answer in zip(carried, got):
        shown = {cid: (actions, remade) for cid, actions, remade in answer["cards"]}
        for e in entries:
            if e["state"] == "hidden":
                assert e["id"] in answer["hidden"] and e["id"] not in shown
                seen.add("hidden")
                continue
            actions, remade = shown[e["id"]]
            if e["state"] == "new":
                assert actions == ["use", "hide"] and not remade
            elif e.get("remade"):
                assert actions in (["take_back", "make_again"], ["hide", "make_again"]) and remade
            else:
                assert actions in (["take_back"], ["hide"]) and not remade
            seen.add("remade" if e.get("remade") else e["state"])
    assert seen == {"new", "used", "hidden", "remade"}
    editor = _source("TimelineEditor.tsx")
    assert "await api.rerenderClip(clip.id)\n" in editor  # Make it again: the saved options, no new ones


# ---- the clip's length, as the render makes it -------------------------------------------------------


LENGTH_CASES = [
    ({}, 30.0),
    ({"keep": [[0, 30]]}, 30.0),
    ({"keep": [[0.03, 29.98]]}, 30.0),  # the whole clip: not a cut
    ({"keep": [[0, 2], [6.5, 20], [22, 30]]}, 30.0),
    ({"keep": [[0, 2], [6.5, 20], [22, 30]], "speed": 1.25}, 30.0),
    ({"keep": [[0, 0.2], [5, 10]]}, 30.0),  # a piece under 0.25 s is dropped
    ({"keep": [[5, 10], [9, 12]]}, 30.0),
    ({"keep": [[0, 10], [10.005, 20]]}, 30.0),
    ({"keep": [[-3, 4], [28, 40]]}, 30.0),
    ({"keep": [[0, 0.2], [3, 3.1]]}, 30.0),  # keeps nothing: the render refuses it
    ({"keep": [[29.2, 30]], "speed": 5}, 30.0),
    ({"keep": [[1, 11]], "speed": 0.2}, 30.0),
    ({"keep": [[1, 11]], "speed": 2, "volume": None}, 30.0),  # one value that isn't a number: all default
    ({"keep": []}, 30.0),
    ({"speed": 1.5}, 17.4),
]


def _render_length(edit: dict, duration: float):
    """The rendered clip's length (EditList.final_duration); None when the
    render refuses the edit for keeping nothing."""
    parsed = EditList.from_dict(edit, duration)
    if parsed is not None:
        return parsed.final_duration()
    if EditList.from_dict({**edit, "mute_all": True}, duration) is None:
        return None
    return duration  # nothing to do: the clip as it is


def test_the_kept_length_is_the_length_the_render_makes(tmp_path):
    got = _run(tmp_path, "return data.map(([edit, duration]) => m.finalLength(edit, duration))",
               [list(c) for c in LENGTH_CASES])
    for (edit, duration), length in zip(LENGTH_CASES, got):
        expect = _render_length(edit, duration)
        if expect is None:
            assert length is None, edit
        else:
            assert length == pytest.approx(expect, abs=1e-9), edit


# ---- the job form --------------------------------------------------------------------------------------


def test_the_promise_sentence_is_in_the_switch_title():
    """One sentence says when a suggestion reaches a clip, the same on the
    Suggest edits switch as in the Marketplace."""
    marketplace = (LIB / "marketplace.ts").read_text(encoding="utf-8")
    promise = re.search(r"export const SUGGESTION_PROMISE =\s*'([^']*)'", marketplace)
    assert promise
    assert promise.group(1) == ("Clips Kitty doesn’t put a suggestion into a clip until you use it in the editor and "
                                "apply your edits (Apply edits, or Apply edits & upload).")
    form = _source("queue/AddVideos.tsx")
    switch = re.search(r"key: 'edit',\s*label: 'Suggest edits',\s*hint: '\(Marketplace\)',\s*title:\s*'([^']*)'", form)
    assert switch and promise.group(1) in switch.group(1)
    assert switch.group(1).endswith("Not with Longform.")
    assert "if (key === 'edit') return Boolean(o.edit?.length)" in form
    assert "if (tg.key === 'edit' && editUsable.length === 0 && !slot.options.edit?.length) return null" in form
    fields = _source("queue/StepFields.tsx")
    assert "edit: { first: 'Suggest edits with', more: 'and with', none: 'No suggestions' }" in fields
    assert "t('Each plugin’s suggestion is shown on its own. You choose which to use.')" in fields
