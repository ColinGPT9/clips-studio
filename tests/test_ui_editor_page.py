"""The Editor page in the app (editor suite K2, U2 to U5): source checks.

Clips Kitty Editor is a native window the main process lays over the Editor
page's area, so the page has rules a browser test can't see:

- every string it shows goes through t() (lib/i18n.ts);
- the Editor nav entry and every way in exist only while the engine says the
  editor is found: otherwise the app looks exactly as before;
- anything of the app's own that can be drawn over the editor's area while
  the page is up either holds the cover (lib/editorCover.ts) or has a rule of
  its own (the list in docs/editor-suite/api/k2-editor.md);
- the area can be reached with Tab and has a name a screen reader can say.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_i18n_editor import untranslated

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
APP = UI / "App.tsx"
PAGE = UI / "pages" / "Editor.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_editor_page_shows_no_untranslated_english():
    found = [f"{path.relative_to(UI).as_posix()} {problem}"
             for path in (PAGE, UI / "components" / "editor" / "ProjectExportPrompt.tsx")
             for problem in untranslated(_read(path))]
    assert not found, "English on the Editor page that doesn't go through t():\n" + "\n".join(found)


def test_the_editor_libs_say_nothing_without_t():
    # Plain sentences in the libraries reach the page as notices and questions.
    for name in ("editor.ts", "projectExportPrompt.ts"):
        source = _read(UI / "lib" / name)
        for match in re.finditer(r"text: '([^']+)'|askNative\('([^']+)'", source):
            raise AssertionError(f"lib/{name}: a sentence without t(): {match.group(0)}")


def test_existing_labels_are_not_reworded():
    app = _read(APP)
    assert "{ id: 'studio', label: 'Clip Editor', icon: '✂' }" in app
    assert "{ id: 'editor', label: 'Editor'" in app
    # Placed right after Clip Editor.
    assert app.index("id: 'editor'") > app.index("id: 'studio'")
    assert app.index("id: 'editor'") < app.index("id: 'creators'")


def test_nav_entry_and_page_only_when_the_editor_is_found():
    app = _read(APP)
    assert "useEditorFound()" in app
    assert "NAV.filter((item) => item.id !== 'editor' || editorFound)" in app
    assert "page === 'editor' && editorFound &&" in app
    status = _read(UI / "lib" / "editorStatus.ts")
    # Found only with the main process's bridge too (not in the browser shim).
    assert "found && !!window.studio?.editor" in status
    assert "editorApi" in status and ".status()" in status


def test_clip_approvals_are_read_inside_their_provider():
    """K-D9: usePendingApprovals() runs in a component rendered inside
    ClipUpdatesProvider, never in the one that renders the provider (where
    it read the empty default and always gave 0)."""
    app = _read(APP)
    outer = app[app.index("export default function App("):]
    outer = outer[:outer.index("\n}\n")]
    assert "<ClipUpdatesProvider>" in outer and "<Shell />" in outer and "usePendingApprovals" not in outer
    shell = app[app.index("function Shell("):]
    assert "usePendingApprovals()" in shell and "ClipUpdatesProvider" not in shell


def test_every_way_in_is_behind_the_status():
    studio = _read(UI / "pages" / "ClipStudio.tsx")
    assert "onEdit={editorFound ? () => void openInEditor({ kind: 'clip', clip_id: clip.id }) : undefined}" in studio
    assert re.search(r"onOpenInEditor=\{\s*editorFound \? \(\) => void openInEditor\(\{ kind: 'clip', clip_id: current\.id \}\)",
                     studio)
    assert re.search(r"onOpenInEditor=\{\s*editorFound \? \(\) => void openInEditor\(\{ kind: 'clip', clip_id: editingClip\.id \}\)",
                     studio)
    assert "{editorFound && activeVideo && (" in studio and "t('Edit whole video')" in studio
    # The controls themselves draw nothing without their callback.
    assert "{onEdit && (" in _read(UI / "components" / "ClipCard.tsx")
    assert "{onOpenInEditor && (" in _read(UI / "components" / "ClipEditor.tsx")
    assert "{onOpenInEditor && (" in _read(UI / "components" / "EditorModal.tsx")


# Everything App.tsx (or the sidebar it draws) renders that can be drawn over
# the editor's area, and its rule. Keep in step with k2-editor.md.
COVER_RULES = {
    "components/FeedbackHub.tsx": "cover",          # its dialog, opened from the sidebar
    "components/SetupWizard.tsx": "cover",          # full-window wizard
    "components/editor/ProjectExportPrompt.tsx": "cover",
    "components/ApprovalCard.tsx": "approvals",     # cards at the bottom right: the strip's button instead
}
# Rendered from App.tsx but drawn only in the page's flow or the sidebar.
IN_FLOW = {"components/UpdateBanner.tsx", "components/ModelSwitcher.tsx"}

_OVERLAY = re.compile(r"""\bfixed\b[^'"`]*inset|className="fixed|['"`]fixed |createPortal\(|<dialog\b|showModal\(""")


def _app_components() -> set[str]:
    app = _read(APP)
    return {f"{m.group(1)}.tsx" for m in re.finditer(r"from '\./(components/[\w/]+)'", app)}


def test_every_overlay_from_the_shell_has_a_rule():
    unknown = []
    for relative in sorted(_app_components()):
        source = _read(UI / relative)
        if _OVERLAY.search(source) and relative not in COVER_RULES and relative not in IN_FLOW:
            unknown.append(relative)
    assert not unknown, ("Drawn over the page from App.tsx with no rule for Clips Kitty Editor "
                         "(hold useEditorCover, or add it to k2-editor.md and this list):\n" + "\n".join(unknown))


def test_each_rule_is_kept():
    for relative, rule in COVER_RULES.items():
        source = _read(UI / relative)
        if rule == "cover":
            assert "useEditorCover(" in source, relative
    cards = _read(UI / "components" / "ApprovalCard.tsx")
    assert "|| hidden) return null" in cards
    app = _read(APP)
    assert "hidden={editorShown && !approvalsOpen}" in app
    assert "useEditorCover(editorShown && approvalsOpen)" in app
    doc = _read(ROOT / "docs" / "editor-suite" / "api" / "k2-editor.md")
    for relative in list(COVER_RULES) + sorted(IN_FLOW):
        assert Path(relative).stem in doc, f"{relative} is not in k2-editor.md's list"


def test_the_page_hides_the_editor_while_covered_and_when_left():
    page = _read(PAGE)
    assert "const wanted = !!store.shownId && holds === 0" in page
    assert "bridge?.setVisible(wanted)" in page
    assert "() => () => void bridge?.setVisible(false)" in page


def test_the_area_follows_size_place_and_scale():
    page = _read(PAGE)
    for needed in ("new ResizeObserver(send)", "window.devicePixelRatio", "matchMedia(`(resolution:",
                   "bridge.onMeasure(send)", "requestAnimationFrame", "Math.floor(r.left * dpr)",
                   "Math.max(1, Math.round(r.width * dpr))"):
        assert needed in page, needed


def test_keyboard_and_screen_reader():
    page = _read(PAGE)
    assert 'role="application"' in page
    assert "tabIndex={0}" in page
    assert "aria-label={t('Editor. Press Enter to use it, Ctrl+F6 to come back.')}" in page
    assert "bridge?.focus()" in page
    assert 'aria-live="polite"' in page
    # A click in the editor leaves no page button focused (Space would press it).
    assert "(document.activeElement as HTMLElement | null)?.blur?.()" in page
    app = _read(APP)
    assert "document.addEventListener('pointerdown', back, true)" in app
    assert "document.addEventListener('focusin', back, true)" in app


def test_questions_over_the_editor_are_native():
    page = _read(PAGE)
    lib = _read(UI / "lib" / "editor.ts")
    assert "window.confirm(" in page          # delete a project
    assert "bridge.ask(" in lib               # clip changed in Quick edit
    assert "Start a new project from the clip" in lib


def test_quick_edit_asks_before_making_an_editor_export_again():
    api = _read(UI / "lib" / "api.ts")
    for route in ("`/clips/${id}/render`", "`/clips/${id}/ai-edit`", "`/clips/${id}/captions`, { lines }, 'PUT'"):
        assert "renderRequest<" in api and route in api, route
    assert "newFilesRequest<{ job_id: number; languages: string[]; clips: number }>('/translate', body)" in api
    assert "replace_project_edits: true" in api and "without_project_edits: true" in api
    prompt = _read(UI / "components" / "editor" / "ProjectExportPrompt.tsx")
    # First focus never on "Apply anyway" / "Make it anyway".
    assert "ref={canOpen ? undefined : first}" in prompt
    assert prompt.count("ref={first}") == 1
    assert "<ProjectExportPrompt />" in _read(APP)


def test_the_new_way_in_has_its_own_name():
    """UX round: "Open in editor" (new words), not a second "Edit" beside
    "Edit this clip"; the old editor is named by what the app shows."""
    for rel in ("components/ClipCard.tsx", "components/ClipEditor.tsx", "components/EditorModal.tsx"):
        source = _read(UI / rel)
        assert "t('Open in editor')" in source, rel
        assert "t('Edit')" not in source, rel
    lib = _read(UI / "lib" / "editor.ts")
    assert "Quick edit" not in "".join(re.findall(r"t\('([^']*)'\)", lib))


def test_notices_and_exports_reach_a_screen_reader():
    """UX round: the export notice sits in live regions that are always there
    (status, or alert for a failure); an export is announced by quarters."""
    page = _read(PAGE)
    assert page.count('role="status" className="sr-only"') == 1
    assert page.count('role="alert" className="sr-only"') == 1
    assert 'role="status" className="text-sm text-accent empty:hidden"' in page
    assert 'role="alert" className="text-sm text-amber-400 empty:hidden"' in page
    assert "Math.floor(Math.max(0, exporting.fraction) * 4)) * 25" in page
    # The percent shown every poll is not itself a live region.
    assert '<span className="ml-auto text-muted shrink-0">{status}</span>' in page
    lib = _read(UI / "lib" / "editor.ts")
    assert "t('The export did not finish.'), tooltip: answer.error || undefined, failure: true" in lib


def test_the_export_sheet_is_told_where_the_file_goes():
    lib = _read(UI / "lib" / "editor.ts")
    for words in ("Replaces this clip in the Library", "Export folder: {folder}",
                  "Goes to your Downloads folder", "Saved next to this clip, which was changed since"):
        assert f"t('{words}')" in lib, words
    assert lib.count("await showOptions(") == 2


def test_saved_as_this_clips_video_has_no_folder_tooltip():
    """K-D21: the notice after a clip's export names no internal folder (a
    screen reader reads a tooltip too); the folder stays for "Saved to"
    and for an export kept beside the clip."""
    lib = _read(UI / "lib" / "editor.ts")
    case = lib[lib.index("case 'replaced_clip':"):lib.index("case 'kept_beside':")]
    assert "t('Saved as this clip’s video.')" in case and "tooltip" not in case.replace("No tooltip", "")
    rest = lib[lib.index("case 'kept_beside':"):lib.index("case 'failed':")]
    assert rest.count("tooltip: answer.path") == 2


def test_the_opening_bar_never_goes_back_or_says_100_before_the_editor_shows():
    """K-D25: the bar follows the engine's progress (the base video's own),
    never back (the event stream and the page's asking can cross), only the
    project being opened moves it, and it stops at 99% until the editor has
    the project on screen (the page's bar goes when it does)."""
    lib = _read(UI / "lib" / "editor.ts")
    assert "export const OPENING_MOST = 0.99" in lib
    helper = lib[lib.index("export function openingProgress("):]
    helper = helper[:helper.index("\n}\n")]
    assert "Math.min(OPENING_MOST, Math.max(before" in helper and "store.opening.id === project.id" in helper
    ready = lib[lib.index("async function openReady("):lib.index("let opened: EditorProject")]
    assert ready.count("progress: openingProgress(project)") == 2 and "project.progress ||" not in ready
    event = lib[lib.index("export function onEditorProjectEvent("):lib.index("let newsWaiter")]
    assert "store.opening.id !== e.id" in event and "openingProgress({ id: e.id, progress: e.progress })" in event


def test_a_finished_build_is_opened_at_once_not_at_the_next_look():
    """K-D25: the page asks for a project being made once a second, or at
    once when the engine says its build ended."""
    lib = _read(UI / "lib" / "editor.ts")
    ready = lib[lib.index("async function openReady("):lib.index("let opened: EditorProject")]
    assert "await buildNews(1000)" in ready and "await sleep(1000)" not in ready
    event = lib[lib.index("export function onEditorProjectEvent("):lib.index("let newsWaiter")]
    assert "if (e.state && e.state !== 'building') newsWaiter?.()" in event
