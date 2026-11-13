"""A project saved by another version of Clips Kitty.

Every document says which version of the document it is (`schema`).
- An older one is brought up to date when it is loaded, by the migrations
  below, one version at a time. A migration is a pure function: it gets a
  copy and returns the next version's document. Keys it doesn't know stay.
- A newer one, saved by a newer Clips Kitty, opens read-only: it is shown as
  it is, never rewritten, and operations on it are refused in plain words
  (ops.apply, store.py).

MIGRATIONS[n] turns a schema-n document into schema n + 1. There is none
yet: schema 1 is the first. The first change to the document's shape adds
MIGRATIONS[1], and raises model.SCHEMA to 2.
"""

from __future__ import annotations

from video_editor.project import model

MIGRATIONS: dict = {}

READ_ONLY = ("This project was saved by a newer version of Clips Kitty, so it opens read-only here. "
             "Update Clips Kitty to change it.")


class MigrationError(ValueError):
    """A document that can't be brought up to date."""


def load(doc, migrations: dict | None = None, target: int | None = None) -> tuple[dict, bool]:
    """(the document at this version, whether it is read-only).

    The input is never changed. A document from a newer version comes back
    as it is, read-only. `migrations` and `target` are MIGRATIONS and
    model.SCHEMA unless a test says otherwise."""
    migrations = MIGRATIONS if migrations is None else migrations
    target = model.SCHEMA if target is None else target
    if not isinstance(doc, dict) or type(doc.get("schema")) is not int or doc["schema"] < 1:
        raise MigrationError("This is not a project: it doesn't say which version it is.")
    if doc["schema"] > target:
        return doc, True
    if doc["schema"] == target:
        return doc, False
    out = model.clone(doc)
    while out["schema"] < target:
        version = out["schema"]
        step = migrations.get(version)
        if step is None:
            raise MigrationError(f"This project is from version {version} of the project format, "
                                 "which this Clips Kitty can no longer open.")
        out = step(model.clone(out))
        if not isinstance(out, dict) or out.get("schema") != version + 1:
            raise MigrationError(f"The update from version {version} of the project format went wrong.")
    return out, False
