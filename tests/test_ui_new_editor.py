r"""The new editor (beta), step 8 part B: the switch, the ways in and the viewer.

Python source tests, as for the app's other screens (the UI has no
JavaScript test runner): they read the TypeScript and hold the rules that
keep the app as it was with the switch off.

- "New editor (beta)" is off by default, kept with the other UI preferences
  (localStorage), and only its own Settings card is new in Settings.
- With it off, the Library (Clip Editor page) shows nothing new: every way in
  sits behind the switch, and today's clip panel gets its button only then.
- App.tsx loads the editor only when a project is opened in it.
- The viewer asks for one still at a time (the older is aborted, a superseded
  answer ignored), draws it without blob: or data: addresses (the packaged
  page's rules allow neither in an <img>), plays the program from two players
  clocked by requestVideoFrameCallback, and offers the phone app overlays the
  gaming preview uses, for 9:16 only.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
EDITOR = UI / "components" / "editor"


def _read(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


# ---- the switch -------------------------------------------------------------------------------


def test_the_switch_is_off_unless_turned_on():
    source = _read("lib/newEditor.ts")
    # On only for the exact stored "1"; anything else, or storage refused, is off.
    assert "localStorage.getItem(SWITCH_KEY) === '1'" in source
    on = source[source.index("export function newEditorOn"):source.index("export function setNewEditorOn")]
    assert "return false" in on
    assert "localStorage" in source and "fetch(" not in source   # a UI preference, not engine state


def test_settings_has_the_one_new_switch_and_it_goes_through_t():
    source = _read("pages/Settings.tsx")
    assert "function NewEditorCard()" in source
    assert "{t('New editor (beta)')}" in source
    assert "setNewEditorOn(e.target.checked)" in source
    assert source.count("<NewEditorCard />") == 1


# ---- with the switch off, nothing new ----------------------------------------------------------


def test_the_librarys_ways_in_sit_behind_the_switch():
    source = _strip_comments(_read("pages/ClipStudio.tsx"))
    assert "const newEditor = useNewEditor()" in source
    for part in ("<NewEditorBar", "<EditWholeVideo", "<EditClipInNewEditor"):
        for match in re.finditer(re.escape(part), source):
            before = source[max(0, match.start() - 80):match.start()]
            assert "newEditor &&" in before or "newEditor ?" in before, f"{part} is shown without the switch"


def test_todays_clip_panel_is_unchanged_without_the_button():
    source = _read("components/ClipEditor.tsx")
    # The only addition: an optional slot, rendered as given (nothing when absent).
    assert "newEditorButton?: JSX.Element" in source
    assert source.count("{newEditorButton}") == 1
    assert "useNewEditor" not in source


def test_app_opens_the_editor_only_when_a_project_is_opened():
    source = _strip_comments(_read("App.tsx"))
    assert "lazy(() => import('./components/editor/EditorScreen'))" in source
    assert "import EditorScreen" not in source
    assert "window.addEventListener(OPEN_PROJECT, open)" in source
    assert re.search(r"\{editorProject && \(", source)
    # The page underneath stays: the editor is drawn over it, not instead of it.
    assert "{page === 'studio' && (" in source
    # ...and can't be reached by Tab or a screen reader while the editor is open.
    assert ".inert = editorProject !== null" in source


def test_nothing_dispatches_open_project_outside_the_new_editor():
    dispatchers = [p.relative_to(UI).as_posix() for p in UI.rglob("*.ts*")
                   if "openProject(" in p.read_text(encoding="utf-8")]
    for path in dispatchers:
        assert path.startswith("components/editor/") or path == "lib/newEditor.ts", path


# ---- the viewer -------------------------------------------------------------------------------


def test_one_still_at_a_time_and_a_superseded_one_is_ignored():
    viewer = _read("components/editor/Viewer.tsx")
    still = viewer[viewer.index("function StillLayer("):viewer.index("/** The bar under a media shown on its own")]
    # Each frame's request is aborted by the next (the effect's clean-up).
    assert "const ask = new AbortController()" in still
    assert "ask.abort()" in still
    assert ".still(projectId, revision, at, ask.signal)" in still
    # Playing doesn't ask: the frame is a constant then.
    assert "active ? frameAt(playhead.get(), fps) : -1" in still
    assert "e instanceof EditorError && e.status === 409" in still
    api = _read("lib/editorApi.ts")
    assert "get superseded()" in api and "signal" in api


def test_stills_are_drawn_without_blob_or_data_addresses():
    for path in [*EDITOR.glob("*.ts*"), UI / "lib" / "editorApi.ts"]:
        source = path.read_text(encoding="utf-8")
        assert "createObjectURL" not in source, path.name
        assert "readAsDataURL" not in source, path.name
    assert "createImageBitmap(" in _read("lib/editorApi.ts")


def test_the_program_plays_from_two_players_on_their_presented_frames():
    player = _read("components/editor/programPlayer.ts")
    assert "requestVideoFrameCallback" in player
    assert re.search(r"constructor\(\s*a: HTMLVideoElement,\s*b: HTMLVideoElement", player)
    viewer = _read("components/editor/Viewer.tsx")
    assert viewer.count("<video ref={aRef}") == 1 and viewer.count("<video ref={bRef}") == 1


def test_the_platform_overlay_reuses_the_gaming_preview_and_only_for_9_16():
    viewer = _read("components/editor/Viewer.tsx")
    assert "import PlatformOverlay, { PLATFORM_UI } from '../PlatformOverlay'" in viewer
    assert "import { SAFE_ZONES } from '../../lib/gamingLayout'" in viewer
    assert "disabled={!vertical || inSource}" in viewer
    assert "vertical && !inSource && PLATFORM_UI[platform] && <PlatformOverlay" in viewer
    prefs = _read("lib/newEditor.ts")
    assert "PLATFORM_CHOICES = ['tiktok', 'reels', 'shorts', 'none']" in prefs
    assert "localStorage.setItem(PLATFORM_KEY, p)" in prefs


def test_the_ai_framing_rule_matches_the_compiler():
    """The viewer frames an item as AI exactly when compose.is_ai_item does."""
    program = _read("lib/editorProgram.ts")
    rule = program[program.index("export function isAiItem"):]
    rule = rule[:rule.index("\n}\n")]
    assert "'crop' in f || 'gaming' in f" in rule
    assert "'fit' in f || 'align' in f || 'pad' in f" in rule
    assert "ctx.origin === 'clip' && item.media_id === ctx.media_id" in rule
    compose = (ROOT / "video_editor" / "compose.py").read_text(encoding="utf-8")
    assert '"crop" in framing or "gaming" in framing' in compose
    assert '"fit" in framing or "align" in framing or "pad" in framing' in compose


def test_the_viewer_answers_the_keys_its_tooltip_lists():
    screen = _read("components/editor/EditorScreen.tsx")
    for key in ("' '", "'ArrowLeft'", "','", "'ArrowRight'", "'.'", "'Home'", "'End'", "'i'", "'o'"):
        assert f"key === {key}" in screen, key
    viewer = _read("components/editor/Viewer.tsx")
    for name in ("Space", "Home", "End"):
        assert f"['{name}'," in viewer


def test_every_change_sends_the_revision_it_last_saw():
    hook = _read("components/editor/useProject.ts")
    assert "editorApi.ops(p.id, p.revision, ops, label)" in hook
    assert "e instanceof EditorError && e.conflict" in hook
    assert "await reload()" in hook


# ---- QC round 1 (8-D1, 8-D6, 8-D7, 8-D8) -------------------------------------------------------


def _frame_us_as_the_ui_has_it(n: int, num: int, den: int) -> int:
    """editorProgram.ts frameUs, as whole numbers: floor((2*n*1e6*den + num) / (2*num))."""
    return (2 * n * 1_000_000 * den + num) // (2 * num)


def test_the_ui_frame_grid_is_the_engines_to_the_microsecond():
    """8-D7: the paused viewer asks for the still at the UI's frame start,
    and split rounds to the engine's; a microsecond apart showed the frame
    before on one frame in three."""
    source = _strip_comments(_read("lib/editorProgram.ts"))
    assert "const top = 2 * n * 1e6 * fps.den + fps.num" in source
    assert "Math.floor(top / (2 * fps.num))" in source
    assert "Math.floor((n * 1e6 * fps.den) / fps.num)" not in source
    model = (ROOT / "video_editor" / "project" / "model.py").read_text(encoding="utf-8")
    assert "return (2 * n * US * den + num) // (2 * num)" in model
    for num, den in ((24000, 1001), (30000, 1001), (30, 1), (60, 1), (25, 1)):
        for n in range(0, 2000):
            engine = (2 * n * 1_000_000 * den + num) // (2 * num)
            assert _frame_us_as_the_ui_has_it(n, num, den) == engine


def test_typing_back_the_shown_time_changes_nothing():
    """8-D8: the Inspector's times show a frame; only a time on another frame is a change."""
    inspector = _strip_comments(_read("components/editor/Inspector.tsx"))
    assert "if (frameAt(us, fps) !== frameAt(value, fps)) onCommit(us)" in inspector
    assert "if (us !== value) onCommit(us)" not in inspector


def test_every_render_of_todays_editor_asks_first():
    """8-D1, 8-D2: "Apply edits & upload" (render_first) and the caption
    save go through the same question as Apply and the chat edit."""
    api = _strip_comments(_read("lib/api.ts"))
    publish = api[api.index("publishClip:"):api.index("cancelPublish:")]
    assert "body.render_first" in publish and "renderRequest<" in publish
    assert "replace_project_edits: true" in publish
    captions = api[api.index("saveCaptions:"):api.index("tightenClip:")]
    assert "renderRequest<" in captions and "'PUT'" in captions


def test_a_clip_changed_since_its_project_is_asked_about():
    """8-D6: opening a clip whose project is older asks; exporting over it asks."""
    entry = _read("components/editor/LibraryEntry.tsx")
    assert "p.clip_changed_since" in entry and "editorApi.restartClipProject" in entry
    assert "t('Start again from the clip as it is now')" in entry and "t('Keep the project')" in entry
    dialog = _read("components/editor/ExportDialog.tsx")
    assert "refusalReason(e) === 'clip_changed'" in dialog and "start(true)" in dialog
    api = _read("lib/editorApi.ts")
    assert "replace_clip_changes: true" in api and "start_again: true, expected_revision: revision" in api
