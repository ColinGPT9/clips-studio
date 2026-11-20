"""How the main process starts and stops Clips Kitty Editor (editor suite K2, U1).

Source checks on ui/src/main/editor.ts and its callers:

- started detached, with no pipes and no console window, so a crash of the
  app leaves the editor free to save and leave by itself;
- the editor's token only in its environment: never on the command line,
  never logged, never sent to the page;
- never killed: closed with host.close (it saves first), on the way out of
  every quit route (before-quit, window-all-closed, the updater);
- every IPC call it answers checks it comes from the app's own window, and
  takes a project id, never a path, from the page.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "ui" / "src" / "main"
EDITOR = MAIN / "editor.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _spawn_call(source: str) -> str:
    start = source.index("child = spawn(")
    depth = 0
    for i in range(start + len("child = spawn"), len(source)):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError("spawn( is not closed")


def test_started_detached_with_no_pipes_and_no_window():
    call = _spawn_call(_read(EDITOR))
    assert "detached: true" in call
    assert "stdio: 'ignore'" in call
    assert "windowsHide: true" in call
    assert "'--embed', hwnd" in call and "'--hidden'" in call
    assert "child.unref()" in _read(EDITOR)


def test_the_token_is_only_in_the_environment():
    source = _read(EDITOR)
    call = _spawn_call(source)
    args = call[call.index("["):call.index("]") + 1]
    assert "token" not in args.lower()
    assert "const env: NodeJS.ProcessEnv = { ...process.env, CONCAT_EMBED_TOKEN: token }" in source
    assert "delete env.ELECTRON_RUN_AS_NODE" in source
    # Never logged, never sent to the page.
    for line in source.splitlines():
        if re.search(r"console\.\w+\(|send\(", line):
            assert "token" not in line.lower(), line
    assert "token" not in _read(ROOT / "ui" / "src" / "preload" / "index.ts").lower().replace("tokens", "")


def test_never_killed():
    source = _read(EDITOR)
    assert "taskkill" not in source
    kills = re.findall(r"\w*\.kill\([^)]*\)", source)
    assert kills == ["process.kill(pid, 0)"], kills          # only asks whether it is there
    assert "rpc(target, 'host.close'" in source


def test_every_quit_route_closes_the_editor_first():
    index = _read(MAIN / "index.ts")
    quit_block = index[index.index("app.on('before-quit'"):]
    assert quit_block.index("confirmQuitWhileExporting(parent)") < quit_block.index("await closeEditor(10000)")
    assert quit_block.index("await closeEditor(10000)") < quit_block.index("quitting = true\n        app.quit()")
    closed = index[index.index("app.on('window-all-closed'"):]
    assert closed.index("closeEditor(3000)") < closed.index("stopChildren()")
    updater = _read(MAIN / "updater.ts")
    install = updater[updater.index("ipcMain.handle('update:install', async"):]
    assert install.index("await closeEditor(3000)") < install.index("autoUpdater.quitAndInstall(")
    assert "setupEditor({ window: () => mainWindow, apiPort: API_PORT, secret: SESSION_SECRET })" in index
    assert "watchEditorWindow(win)" in index


def test_ipc_comes_only_from_the_app_window_and_names_projects_by_id():
    source = _read(EDITOR)
    handlers = re.findall(r"ipcMain\.handle\('(editor:\w+)'", source)
    assert set(handlers) >= {"editor:show", "editor:setBounds", "editor:setVisible", "editor:focus",
                             "editor:blur", "editor:close", "editor:reveal", "editor:ask"}
    for name in handlers:
        body = source[source.index(f"ipcMain.handle('{name}'"):]
        body = body[:body.index("\n  })")]
        assert "ownFrame(event)" in body, name
    assert "const PROJECT_ID = /^[0-9a-f]{32}$/" in source
    show = source[source.index("ipcMain.handle('editor:show'"):]
    assert show.index("PROJECT_ID.test(projectId)") < show.index("serial(")
    # Paths come from the engine, never from the page.
    assert "'/editor/launch'" in source and "`/editor/projects/${projectId}`" in source


def test_one_editor_at_a_time_and_switching_without_a_restart():
    source = _read(EDITOR)
    assert "let ed: Running | null = null" in source
    assert "'host.open_project'" in source
    assert "answer.error?.startsWith('busy:')" in source
    assert "export?.running) return { ok: false, error: 'exporting' }" in source


def test_exports_are_handed_to_the_engine_by_name_only():
    source = _read(EDITOR)
    take = source[source.index("async function takeIn("):]
    take = take[:take.index("\n}\n")]
    assert "win32.basename(" in take
    assert "{ file, export_folder:" in take
    assert "entry.state !== 'done'" in take   # a failed or cancelled export calls no route


def test_preload_exposes_the_editor_bridge():
    preload = _read(ROOT / "ui" / "src" / "preload" / "index.ts")
    for channel in ("editor:show", "editor:setBounds", "editor:setVisible", "editor:focus", "editor:blur",
                    "editor:close", "editor:state", "editor:export", "editor:measure", "editor:words"):
        assert f"'{channel}'" in preload, channel


def _function(source: str, head: str) -> str:
    body = source[source.index(head):]
    return body[:body.index("\n}\n")]


def test_a_scale_change_never_closes_the_editor_while_it_exports():
    """K-D8: the restart for a new screen scale waits for a running export
    to end, and asks again just before it closes the editor."""
    source = _read(EDITOR)
    check = _function(source, "async function checkScale(")
    assert "restartForScale(target)" in check and "closeNow(" not in check
    restart = _function(source, "async function restartForScale(")
    assert "await exportingOn(target)" in restart
    assert restart.index("if (await exportingOn(target)) return false") < restart.index("await closeNow(target)")
    # Every other close of a running editor is asked about or waits too.
    assert "export?.running) return { ok: false, error: 'exporting' }" in source          # switching
    assert "pub.exporting === null" in _function(source, "async function poll(")           # idle close


def test_a_close_takes_in_exports_that_ended_since_the_last_poll():
    """K-D10: closeNow asks the editor's state once more before host.close."""
    close = _function(_read(EDITOR), "async function closeNow(")
    assert close.index("'host.state'") < close.index("queueEnded(target") < close.index("'host.close'")
    assert close.index("target.takeIn") < close.index("'host.close'")


def test_the_quit_question_is_in_the_apps_language():
    """K-D2: the main process has no t(); the page sends the question's
    words through t() and again when the language changes."""
    source = _read(EDITOR)
    assert "buttons: [quitWords.keep, quitWords.quit]" in source and "message: quitWords.message" in source
    assert "ipcMain.handle('editor:words'" in source
    page_lib = _read(ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "editor.ts")
    for words in ("The editor is still exporting. Quit and stop the export?", "Keep exporting", "Quit"):
        assert f"t('{words}')" in page_lib, words
    assert "addEventListener('app-language-changed', sendWords)" in page_lib


def test_the_export_sheet_gets_the_destination_line():
    """UX round: the page's line goes to the editor at start (--export-note)
    and with host.open_project (destination), checked and kept short."""
    source = _read(EDITOR)
    assert "'--export-note', destinationOf(projectId)" in source
    assert "destination: destinationOf(projectId) }" in source
    assert ".slice(0, 400)" in source


def test_the_export_percent_never_goes_back():
    source = _read(EDITOR)
    assert "Math.max(fraction, pub.exporting?.fraction ?? 0)" in source


def test_a_busy_or_unreachable_engine_is_asked_again_before_an_export_is_called_failed():
    """K-D17: a 409 (the engine still taking the same project's exports in)
    or an engine that can't be reached for a moment is asked again; the
    engine answers a file it already took as it recorded it."""
    source = _read(EDITOR)
    take = source[source.index("async function takeIn("):]
    take = take[:take.index("\n}\n")]
    assert "attempt <= TAKE_IN_RETRIES && (answer.status === 0 || answer.status === 409)" in take
    assert take.index("TAKE_IN_RETRIES") < take.index("result: 'failed'")
    assert re.search(r"const TAKE_IN_RETRIES = \d+", source)


def test_with_no_export_folder_the_export_buttons_downloads_folder_is_used():
    """K-D22: no folder from Settings (or one that is not a full path): the
    folder the app's own Export buttons use, Windows' Downloads folder."""
    source = _read(EDITOR)
    show = source[source.index("ipcMain.handle('editor:show'"):]
    show = show[:show.index("ipcMain.handle('editor:setBounds'")]
    assert "win32.isAbsolute(folder) ? folder : app.getPath('downloads')" in show
    assert "app.getPath('downloads')" in _read(MAIN / "index.ts")         # what lib/exportFolder.ts is given
