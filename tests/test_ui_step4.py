"""Step 4 in the desktop app: "About this creator" and the Free music picker.

The app has no React test harness, so, as tests/test_ui_caption_edits.py
does, the parts that live in the pages are checked in their source: that
the box and the picker are there, talk to the routes the engine has, and
keep the promises the user guide makes (the picker lists music only, one
track plays at a time and stops when the tab closes, the person's own file
and a free track clear each other, a clip whose only edit is a free track
is still sent to render).
"""

import re
from pathlib import Path

from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"


def _source(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---- About this creator -----------------------------------------------------------------------

def test_the_box_is_on_the_creators_page_and_saves_through_the_route():
    page = _flat(_source("pages/Creators.tsx"))
    assert "{t('About this creator')}" in page
    assert page.count("<textarea") == 1
    assert "onBlur={() => void saveAbout()}" in page
    assert "api.setCreatorDescription(now.id, now.text)" in page
    api = _flat(_source("lib/api.ts"))
    assert "`/creators/${creatorId}/description`" in api
    assert "body: JSON.stringify({ description })" in api
    # The page shows what the engine stored (tidied), not what was sent.
    assert "const { description } = await api.setCreatorDescription" in page
    assert re.search(r"interface CreatorDetail \{[^}]*\bdescription: string", _source("lib/types.ts"))


def test_the_box_stops_at_the_engines_limit_and_counts_only_near_it():
    page = _flat(_source("pages/Creators.tsx"))
    state = (ROOT / "core" / "state.py").read_text(encoding="utf-8")
    limit = re.search(r"^CREATOR_DESCRIPTION_MAX = (\d+)", state, re.MULTILINE).group(1)
    assert f"const ABOUT_MAX = {limit}" in page
    assert "maxLength={ABOUT_MAX}" in page
    assert "{about.length >= ABOUT_MAX - 100 && (" in page


def test_the_tooltip_gives_an_example_and_says_where_the_note_goes():
    page = _source("pages/Creators.tsx")
    tip = re.search(r"<textarea.*?title=\{t\(\s*'(.*?)'\s*\)\}", page, re.DOTALL).group(1)
    assert "Example:" in tip
    assert "stays on this PC" in tip
    assert "cloud AI with your own key" in tip and "sent to that provider" in tip


def test_the_creators_page_gained_no_other_control():
    """One box. The wipe button keeps its wording and gets no new text."""
    page = _source("pages/Creators.tsx")
    assert "Wipe learned memory for this creator" in page
    assert page.count("<textarea") == 1
    assert page.count("<input") == 3          # learning, profile name, channel to add


# ---- Free music --------------------------------------------------------------------------------

def test_the_picker_shows_no_english_that_bypasses_t():
    assert untranslated(_source("components/FreeMusicPicker.tsx")) == []


def test_every_mood_in_the_library_is_a_key_translations_can_carry():
    """t() looks its text up by the English in the source, so a mood handed
    to it from the manifest would never be translated: each one is written
    out in the picker."""
    import json

    picker = _source("components/FreeMusicPicker.tsx")
    items = json.loads((ROOT / "assets" / "library" / "manifest.json").read_text(encoding="utf-8"))["items"]
    moods = {e["mood"] for e in items if e["kind"] == "music"}
    assert moods == {"chill", "upbeat", "funny", "tense"}
    for mood in moods:
        assert f"{mood}: t('{mood}')" in picker, mood
    assert "t(track.mood)" not in picker and "{moodLabel(track.mood)}" in picker


def test_the_picker_lists_music_only():
    picker = _flat(_source("components/FreeMusicPicker.tsx"))
    assert "r.items.filter((s) => s.kind === 'music')" in picker
    assert "sfx" not in picker
    api = _flat(_source("lib/api.ts"))
    assert "request<{ items: LibrarySound[] }>('/assets/library')" in api
    assert "`${API_BASE}${sound.url}`" in api


def test_one_track_plays_at_a_time_and_it_stops_when_the_tab_closes():
    picker = _flat(_source("components/FreeMusicPicker.tsx"))
    # Starting a track stops the one before it.
    toggle = picker[picker.index("const toggle ="):picker.index("// A copy of the app")]
    assert toggle.index("stop()") < toggle.index("new Audio(")
    # The player is paused when the component leaves the screen...
    assert "useEffect( () => () => { player.current?.pause()" in picker
    # ...and it is on screen only while the Effects tab is: one use, inside that tab's block.
    editor = _source("components/TimelineEditor.tsx")
    assert editor.count("<FreeMusicPicker") == 1
    effects = editor.index("{/* speed / hook title / music — the Effects tab */}\n      {activeTab === 'motion' && (")
    following = editor.index("{activeTab === 'color' && (")
    assert effects < editor.index("<FreeMusicPicker") < following


def test_a_free_track_and_the_persons_own_file_clear_each_other():
    editor = _flat(_source("components/TimelineEditor.tsx"))
    # Picking a track writes a music with no path; picking it again takes the music off.
    # A free track starts at 40, not 25: the tracks are stored quieter than a typical song.
    assert ("music: id ? { library: id, volume: edit.music?.volume ?? 0.4, "
            "duck: edit.music?.duck ?? true } : null") in editor
    # Typing or browsing a file writes a music with no library.
    assert editor.count("music: { path, volume: edit.music?.volume ?? 0.25, duck: edit.music?.duck ?? true }") == 1
    assert ("? { path: e.target.value, volume: edit.music?.volume ?? 0.25, "
            "duck: edit.music?.duck ?? true } : edit.music?.library ? edit.music : null") in editor
    # (An emptied path box leaves a picked free track on the clip.)
    # As in the engine (EditList.from_dict), a path wins over a library id.
    assert "picked={edit.music?.path ? undefined : edit.music?.library}" in editor
    assert "music: { path?: string; library?: string; volume: number; duck: boolean } | null" in _source("lib/types.ts")


def test_space_on_a_picker_button_presses_it_and_leaves_the_video_alone():
    """The editor plays or pauses the video on Space for every control that
    is not a text box. The picker's buttons are marked, and the editor's
    handler steps aside for them before it reaches its shortcuts."""
    picker = _source("components/FreeMusicPicker.tsx")
    assert picker.count("data-free-music\n") == 2          # the button and the list
    editor = _source("components/TimelineEditor.tsx")
    keys = editor[editor.index("const onKey = (e: KeyboardEvent)"):editor.index("window.addEventListener('keydown', onKey, true)")]
    skip = "if (e.key === ' ' && t?.closest('[data-free-music]')) return"
    assert skip in keys
    assert keys.index(skip) < keys.index("switch (e.key)")


def test_picking_a_track_does_not_move_the_list():
    """Volume and "Duck under voice" appear when music is switched on. They
    come after the list, and the button keeps its width while the list is
    open, so the row the pointer is on stays the row that was clicked."""
    editor = _flat(_source("components/TimelineEditor.tsx"))
    after = editor[editor.index("<FreeMusicPicker"):editor.index("{activeTab === 'color' && (")]
    assert '<label className="order-last flex items-center gap-1.5"> Vol' in after
    assert '<label className="order-last flex items-center gap-1.5 cursor-pointer"' in after
    picker = _flat(_source("components/FreeMusicPicker.tsx"))
    assert 'className="basis-full order-last grid' in picker
    assert "{current && !open ? `: ${current.title}` : ''}" in picker


def test_a_clip_whose_only_edit_is_a_free_track_is_not_treated_as_unedited():
    editor = _flat(_source("components/TimelineEditor.tsx"))
    unedited = editor[editor.index("function isDefault("):editor.index("function origToBaked(")]
    assert "!e.music?.path && !e.music?.library" in unedited
