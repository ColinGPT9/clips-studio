r"""The new editor (beta), step 8 part C: the timeline, the inspector, the
export dialog, today's editor's question before it renders over a new-editor
export (7-D5), and Storage's "Clear editor cache".

Python source tests, as for the app's other screens (the UI has no
JavaScript test runner). They hold the rules the plan sets for these parts:

- every change goes through step 5's operations (the project's `apply`),
  and a drag or a slider sends one change when it ends (one undo step);
- the timeline draws only what is near the view (a canvas per track for
  pictures and waveforms, buttons only for the items near it), leaves room
  for step 9's caption lane, and its keys are the ones its tooltip lists;
- an AI item shows "AI framing" and "Reset to AI framing", never crop
  numbers;
- the export dialog never names the engine's branch, offers Play and Show in
  folder only for a finished export whose file is there, says in its tooltip
  that a clip's project replaces the clip's file, and has no caption choices
  (step 9's);
- today's editor sends Apply and the chat edit exactly as before, and asks
  only when the engine says the clip's file is a new-editor export;
- Settings shows the editor cache's line only while the switch is on.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
EDITOR = UI / "components" / "editor"


def _read(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


def _code(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


# ---- the timeline -------------------------------------------------------------------------------


def test_the_timeline_changes_the_project_only_through_its_operations():
    timeline = _code(_read("components/editor/TimelinePanel.tsx"))
    assert "fetch(" not in timeline and "editorApi.ops" not in timeline
    for op in ("split_item", "delete_items", "move_item", "trim_item", "insert_clip", "set_track_props"):
        assert f"op: '{op}'" in timeline, op
    # Every operation goes through apply (useProject's: the revision, a 409 reloads).
    assert timeline.count("op: '") == len(re.findall(r"apply\(\s*\[?\s*\{ op: '|targets\.map\(\(it\) => \(\{ op: '", timeline))


def test_a_drag_is_sent_once_when_it_ends():
    timeline = _code(_read("components/editor/TimelinePanel.tsx"))
    moving = _between(timeline, "const onItemMove = ", "const onItemUp = ")
    assert "apply(" not in moving                       # the ghost only, while dragging
    ending = _between(timeline, "const onItemUp = ", "const cancelDrag = ")
    assert "op: 'trim_item'" in ending and "op: 'move_item'" in ending


def test_the_timeline_draws_only_what_is_near_the_view():
    timeline = _code(_read("components/editor/TimelinePanel.tsx"))
    # Buttons only for items in the window (plus the selected and the dragged).
    assert "it.start_us < win.to && itemEnd(it) > win.from" in timeline
    # Pictures and waveforms on one canvas per track, drawn for the view only.
    assert "itemsIn(track, from, to)" in timeline
    assert "drawThumbs(" in timeline and "drawWave(" in timeline
    assert "<img" not in timeline                       # no picture addresses in the page
    media = _code(_read("components/editor/timelineMedia.ts"))
    assert "createImageBitmap(await res.blob())" in _read("lib/editorApi.ts") and ".bitmap(url, this.ask.signal)" in media
    assert "blob:" not in media and "data:" not in media
    # A zoom is one render, and wheel steps between two frames are one zoom.
    assert "requestAnimationFrame" in _between(timeline, "const onWheel = ", "el.addEventListener('scroll'")


def test_the_timeline_leaves_room_for_the_caption_lane():
    timeline = _read("components/editor/TimelinePanel.tsx")
    assert "Step 9's caption lane goes here, under the main track." in timeline
    rows = _read("lib/editorTimeline.ts")
    order = _code(_between(rows, "export function rowsOf(", "/** Items of a track"))
    # Text, then overlays (the higher first), the main track, then the sounds (A1 is V1's own).
    assert order.index("[...text].reverse()") < order.index("[...video].reverse()") < order.index("of audio)")
    assert "t.id !== 'A1'" in order


def test_the_timelines_keys_are_the_ones_its_tooltip_lists():
    screen = _code(_read("components/editor/EditorScreen.tsx"))
    keys = _between(screen, "const onKey = ", "window.addEventListener('keydown', onKey)")
    for check in ("key === 's'", "key === 'Delete'", "key === '+'", "key === '-'", "key === 'z' || key === 'Z') && e.shiftKey",
                  "tl.nudge(key === 'ArrowLeft'"):
        assert check in keys, check
    listed = _between(_read("components/editor/TimelinePanel.tsx"), "export const TIMELINE_SHORTCUTS", "]\n\n")
    for name in ("'S'", "'Delete'", "'Alt+← →'", "'+ −'", "'Ctrl+wheel'", "'Shift+Z'"):
        assert name in listed, name
    # No shortcut while the export dialog is open, or while typing.
    assert "if (typing(e.target) || exporting) return" in keys


def test_items_and_tracks_have_names_a_screen_reader_can_say():
    timeline = _read("components/editor/TimelinePanel.tsx")
    assert 'role="group" aria-label={label}' in timeline
    assert "aria-label={say}" in timeline and "aria-pressed={on}" in timeline
    # A keyboard click (Enter, Space) selects an item.
    assert "if (e.detail === 0) handlers.key(id, e)" in timeline
    assert 'role="toolbar"' in timeline


# ---- the inspector ------------------------------------------------------------------------------


def test_the_inspector_changes_through_operations_and_a_slider_sends_once():
    inspector = _code(_read("components/editor/Inspector.tsx"))
    assert "fetch(" not in inspector
    for op in ("set_item_props", "set_canvas", "set_project_props", "trim_item", "move_item"):
        assert f"op: '{op}'" in inspector, op
    slider = _between(inspector, "function Slider(", "function TimeField(")
    # Dragging moves the number shown; the change is sent when it is let go.
    assert "onChange={(e) => setShown(Number(e.target.value))}" in slider
    assert "onPointerUp={(e) => commit(" in slider and "onKeyUp={(e) => commit(" in slider


def test_an_ai_item_shows_ai_framing_never_crop_numbers():
    inspector = _code(_read("components/editor/Inspector.tsx"))
    assert "{t('AI framing')}" in inspector and "{t('Reset to AI framing')}" in inspector
    # The AI framing comes back from the clip as it was imported.
    assert "imported?.render_opts" in inspector
    # Only the main track has AI framing (compose.is_ai_item is about V1).
    assert "const ai = isMain && isAiItem(doc, item)" in inspector
    # No field shows or takes a crop's numbers.
    assert not re.search(r"value=\{[^}]*\.crop", inspector)
    assert "framing.crop" not in inspector


def test_the_inspectors_ranges_are_the_engines():
    inspector = _code(_read("components/editor/Inspector.tsx"))
    speed = _between(inspector, "label={t('Speed')}", "/>")
    assert "min={0.25}" in speed and "max={4}" in speed
    volume = _between(inspector, "label={t('Volume')}", "/>")
    assert "max={200}" in volume and "volume: Math.round(v) / 100" in inspector
    ops = (ROOT / "video_editor" / "project" / "ops.py").read_text(encoding="utf-8")
    for key in ("brightness", "saturation", "contrast", "temperature", "tint", "sharpen", "vignette"):
        assert f"key: '{key}'" in inspector and f'"{key}"' in ops, key


# ---- the export dialog --------------------------------------------------------------------------


def test_the_export_dialog_never_names_the_branch_or_offers_a_gone_file():
    dialog = _read("components/editor/ExportDialog.tsx")
    code = _code(dialog)
    assert "branch" not in code.lower()
    assert "editorApi.exportProject(" in code and "editorApi.cancelExport(" in code
    # Play only through a row's url (the engine gives one only when done and the file is there).
    assert code.count("setPlaying(") == code.count("setPlaying(null)") + 2
    assert "{r.url && (" in code and "{canShow && r.url && r.path && (" in code
    assert "const gone = r.status === 'done' && !r.file_exists" in code
    assert "replaced: 'Replaced by a later export'" in code


def test_a_clips_project_says_its_export_replaces_the_clip():
    for path in ("components/editor/ExportDialog.tsx", "components/editor/EditorScreen.tsx"):
        source = _read(path)
        assert "This project was made from a clip. Exporting replaces that clip's video in your Library" in source, path


def test_no_caption_choices_in_step_8s_export():
    code = _code(_read("components/editor/ExportDialog.tsx")).lower()
    assert "srt" not in code and "burn" not in code and "captions" not in code


def test_show_in_folder_takes_only_an_existing_video_file():
    main = _read("../../main/index.ts")
    handler = _between(main, "ipcMain.handle('show-in-folder'", "})\n")
    assert "typeof path !== 'string'" in handler and "SHOWABLE.test(path)" in handler
    assert "statSync(path).isFile()" in handler and "shell.showItemInFolder(path)" in handler
    assert "openPath" not in handler and "openExternal" not in handler
    assert "showInFolder: (path: string)" in _read("../../preload/index.ts")


# ---- today's editor (7-D5) ---------------------------------------------------------------------


def test_todays_apply_and_chat_edit_send_what_they_sent_before():
    api = _read("lib/api.ts")
    render = _between(api, "rerenderClip: (", "/** Export with the preset")
    assert "renderRequest<{ job_id: number }>(`/clips/${id}/render`" in render
    assert "...(range ?? {}),\n      render_opts: renderOpts ?? null,\n      ...(suggestions ? { suggestions } : {})" in render
    assert "renderRequest<{ reply: string; job_id: number | null }>(`/clips/${id}/ai-edit`, { message })" in api
    helper = _between(api, "async function renderRequest<T>(", "async function request<T>(")
    # The same body, and replace_project_edits only when the person chose it.
    assert "JSON.stringify(replace ? withReplace(body) : body)" in helper
    assert "(b) => ({ ...b, replace_project_edits: true })" in helper     # the default, Apply's and the chat's
    assert "method: 'POST' | 'PUT' = 'POST'" in helper


def test_the_question_is_asked_only_for_a_project_export():
    prompt = _code(_read("lib/projectExportPrompt.ts"))
    assert "if (res.status !== 409) return res" in prompt
    assert "detail.reason !== 'project_export'" in prompt
    assert "if (choice === 'apply') return send(true)" in prompt
    dialog = _code(_read("components/editor/ProjectExportPrompt.tsx"))
    assert "const canOpen = newEditor && !!question.project_id" in dialog
    assert "{canOpen && (" in dialog      # "Open in new editor" only with the switch on and a project to open
    assert "{t('Apply anyway')}" in dialog and "{t('Cancel')}" in dialog
    assert "<ProjectExportPrompt />" in _read("App.tsx")


def test_the_question_without_a_project_offers_only_apply_anyway_and_cancel():
    """8-D9: the clip's file is a new-editor export whose project was
    deleted. The engine still asks, with project_id null: the dialog says so
    in a sentence of its own through t(), offers no "Open in new editor",
    and its first focus is on "Cancel" (8-D12), never on "Apply anyway"."""
    prompt = _code(_read("lib/projectExportPrompt.ts"))
    # A null project_id reaches the dialog as "" (no project to open).
    assert "project_id: String(detail.project_id ?? '')" in prompt
    dialog = _code(_read("components/editor/ProjectExportPrompt.tsx"))
    assert "? question.message" in dialog
    assert ": t('Its project was deleted, so it can no longer be opened in the new editor." in dialog
    assert "ref={canOpen ? undefined : first}" in dialog
    assert "if (c === 'open' && question.project_id) openProject(question.project_id)" in dialog
    assert ": t(\"Makes the clip again from its own settings here. The new editor's changes to its video are " \
           "lost.\")" in dialog


# ---- Storage ------------------------------------------------------------------------------------


def test_the_editor_cache_line_shows_only_with_the_switch_on():
    settings = _code(_read("pages/Settings.tsx"))
    assert settings.count("<EditorCacheRow />") == 1
    assert "{newEditor && <EditorCacheRow />}" in settings
    row = _code(_read("components/editor/EditorCacheRow.tsx"))
    assert "editorApi.clearCache()" in row and "t('Clear editor cache')" in row


# ---- 8-D12: never a start-again without a word; focus off "Apply anyway" -------------------------


def test_without_open_in_new_editor_first_focus_is_on_cancel():
    """Enter by reflex must never replace the new editor's export: when
    "Open in new editor" is not offered, the focused button is Cancel."""
    dialog = _code(_read("components/editor/ProjectExportPrompt.tsx"))
    apply_at = dialog.index("onClick={() => choose('apply')}")
    cancel_at = dialog.index("onClick={() => choose('cancel')}")
    apply_button = dialog[dialog.rindex("<button", 0, apply_at):apply_at]
    cancel_button = dialog[dialog.rindex("<button", 0, cancel_at):cancel_at]
    assert "ref=" not in apply_button
    assert "ref={canOpen ? undefined : first}" in cancel_button
    assert "if (question) first.current?.focus()" in dialog


def test_a_start_again_is_always_told():
    """The engine's started_again is shown as a plain notice through t()
    when the editor opens, after "Edit in new editor" and after the
    person's own "Start again"."""
    entry = _code(_read("components/editor/LibraryEntry.tsx"))
    assert ("p.started_again ? t('Started again from the clip as it is now. Undo brings back your changes.') "
            ": undefined") in entry
    assert entry.count("openProject(p.id, startedAgainNotice(p))") == 2
    opener = _code(_read("lib/newEditor.ts"))
    assert "export function openProject(projectId: string, notice?: string): void" in opener
    assert "detail: { projectId, notice: notice ?? '' }" in opener
    app = _code(_read("App.tsx"))
    assert "setEditorNotice(detail.notice ?? '')" in app
    assert "openNotice={editorNotice}" in app
    screen = _code(_read("components/editor/EditorScreen.tsx"))
    assert "useProject(projectId, openNotice)" in screen
    hook = _code(_read("components/editor/useProject.ts"))
    assert "useState(() => openNotice ?? '')" in hook


# ---- 8-D13: new files made from a clip's own window ask first ------------------------------------


def test_export_in_other_languages_asks_before_making_files_without_the_editors_changes():
    """POST /translate answers 409 (reason project_export, separate_file)
    for a clip whose file is a new-editor export. The app asks once, in
    words of its own through t(): the new files are made from the clip as
    the AI made it, without the new editor's changes; "Make it anyway"
    sends the request again with without_project_edits, "Cancel" makes
    nothing, and "Open in new editor" is offered only with a project to
    open and the switch on. First focus is never on "Make it anyway"."""
    api = _read("lib/api.ts")
    translate = _between(api, "translateClips: (", "translations: (clipId: number)")
    assert "newFilesRequest<{ job_id: number; languages: string[]; clips: number }>('/translate', body)" in translate
    helper = _between(api, "async function newFilesRequest<T>(", "async function request<T>(")
    assert "JSON.stringify(goAhead ? { ...body, without_project_edits: true } : body)" in helper

    prompt = _code(_read("lib/projectExportPrompt.ts"))
    assert "if (detail.separate_file === true) {" in prompt
    assert "if (choice === 'make') return send(true)" in prompt
    assert "t('Nothing was made.')" in prompt and "t('Opened in the new editor. Nothing was made.')" in prompt

    dialog = _code(_read("components/editor/ProjectExportPrompt.tsx"))
    separate = _between(dialog, "if (question.separate) {", "return (\n    <div")
    for text in ("t(\"The new files are made from the clip as the AI made it, without the new editor's changes.\")",
                 "t(\"The new files are made from those clips as the AI made them, without the new editor's "
                 "changes.\")",
                 "t('Some of these clips were changed in the new editor')",
                 "t('Clips changed in the new editor:')", "{t('Make it anyway')}", "{t('Cancel')}",
                 "{t('Open in new editor')}", "{canOpen && ("):
        assert text in separate, text
    assert "choose('apply')" not in separate          # nothing of the clip is replaced here
    make_at = separate.index("onClick={() => choose('make')}")
    assert "ref=" not in separate[separate.rindex("<button", 0, make_at):make_at]
    cancel_at = separate.index("onClick={() => choose('cancel')}")
    assert "ref={canOpen ? undefined : first}" in separate[separate.rindex("<button", 0, cancel_at):cancel_at]

    panel = _code(_read("components/MultilingualExport.tsx"))
    assert "e instanceof NothingMade ? e.message" in panel


# ---- the UX fix round: where an export goes, and dialogs that keep the keyboard ---------------


def test_a_project_with_no_clip_names_its_export_folder_before_and_after():
    """A project with no clip sends Settings' export folder and says where
    the file goes before Export and where it went after; a clip's project
    sends no folder (it replaces the clip, ED28)."""
    code = _code(_read("components/editor/ExportDialog.tsx"))
    assert "fromClip ? undefined : folder || undefined" in code
    assert "if (!fromClip) {" in code and "getExportFolder()" in code
    assert "{t('Saves to')} {folder}" in code and "{t('Saved in')} {doneFolder}" in code
    assert "t('It is saved in your export folder, named after the project. Change the folder in Settings.')" in code
    assert "t('Change the folder in Settings.')" in code
    assert "path.lastIndexOf('\\\\')" in code          # folderOf splits on either slash
    api = _code(_read("lib/editorApi.ts"))
    assert "...(folder ? { folder } : {})" in api


def test_both_dialogs_keep_the_keyboard_and_close_on_esc_from_anywhere():
    hook = _code(_read("components/editor/useModalFocus.ts"))
    assert "window.addEventListener('keydown', onKey, true)" in hook
    assert "window.removeEventListener('keydown', onKey, true)" in hook
    assert "sibling.inert = true" in hook and "for (const el of changed) el.inert = false" in hook
    assert "e.key === 'Escape'" in hook and "e.key !== 'Tab'" in hook
    assert "document.addEventListener('focusin', onFocus, true)" in hook

    export = _code(_read("components/editor/ExportDialog.tsx"))
    assert "useModalFocus(root, box, onClose)" in export
    assert "ref={root}" in export and "ref={box}" in export
    assert "e.key === 'Escape'" not in export                 # Esc is the hook's, wherever focus is

    entry = _code(_read("components/editor/LibraryEntry.tsx"))
    shape = _between(entry, "function NewProjectDialog(", "export async function importFinished(")
    assert "useModalFocus(root, box, busy ? null : onClose)" in shape
    assert "ref={root}" in shape and "ref={box}" in shape
    assert "addEventListener('keydown'" not in shape


def test_closing_the_editor_gives_focus_back_to_what_opened_it():
    # The opener is noted when the open event arrives, before the page underneath
    # goes inert (inert takes focus away, so the editor itself can't note it).
    app = _code((UI / "App.tsx").read_text(encoding="utf-8"))
    opened = app.index("const active = document.activeElement")
    assert opened < app.index("setEditorProject(detail.projectId)")
    assert "editorOpener.current = active" in app
    assert "if (editorProject === null && editorOpener.current)" in app
    assert "if (opener.isConnected) opener.focus()" in app

    screen = _code(_read("components/editor/EditorScreen.tsx"))
    assert "before?.focus" not in screen
