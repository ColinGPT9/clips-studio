"""The new editor's project: the document, the operations that change it,
and where it is kept. Engine only (editor suite step 5); nothing here runs
unless a project route is called.

- model.py: the project document (canvas, media, tracks, items, captions,
  branding, export preset), its checks and its one way of being written.
- patch.py: the difference between two documents, and putting it back.
- ops.py: the validated operations, shared by the editor, the AI and MCP.
- env.py: what the operations ask the rest of Clips Kitty (fonts, filters,
  gaming layouts, export presets, which media exist).
- migrate.py: a document saved by an older version, brought up to date.
- importer.py / projection.py: an AI clip as a project, and back.
- store.py: projects, their undo history, media assets and exports in SQLite.

Standard library only: nothing in this package imports numpy, FastAPI or
pydantic, and core/pipeline.py never imports this package
(tests/test_editor_import_guard.py). video_editor/__init__.py must not import
it either. docs/editor-suite/api/step5-project-core.md describes all of it.
"""
