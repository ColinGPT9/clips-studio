"""Clips Kitty Editor inside Clips Kitty (editor suite step K2).

Clips Kitty Editor is the full editor behind the Editor page: a separate
program (based on Concat, AGPL-3.0-or-later) that the desktop app shows
inside its own window. This package is the engine's side of it:

- registry.py    the projects Clips Kitty made for it, one folder each under
                 data/editor/projects/<id>/ with a manifest, kitty.json;
- automation.py  talking to the editor with no window (its own automation,
                 MCP over standard input and output);
- build.py       making a project for a clip, a whole video or a file;
- intake.py      taking an export back in: as the clip's video, or into the
                 person's export folder.

The routes are in server/kitty_editor_api.py; the contract is
docs/editor-suite/api/k2-editor.md.

Rules of the package (tests/test_editor_import_guard.py holds them):
standard library only when it is imported; whatever it asks of the rest of
the app it imports inside a function; core/pipeline.py and everything that
loads never import it, and core/state.py and server/jobs.py never do at the
top of the file. A plain clipping run never loads it.
"""
