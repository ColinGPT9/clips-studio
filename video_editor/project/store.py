"""Where projects are kept: SQLite, in the app's own database.

The tables are made by core/state.py (_migrate(), PROJECT_TABLES); this file
is every read and write of them. Each function takes the database's
connection (StateDB.conn), so nothing here opens a file but the media assets.

A project's row holds its document as it is now, and three numbers:

- head: goes up by one with EVERY change (an edit, an undo, a redo). A
  change says which head it was made on (`expected_revision`); when the
  project has moved on since, it is refused (Conflict, the route's 409) and
  nothing changes. Never reused, so a caller holding an old number can never
  pass for a current one.
- cursor: which revision the document is at. An edit adds revision
  cursor + 1; undo steps the cursor back and redo forward. An edit made
  after an undo drops the revisions past the cursor (the redo tail).
- base: the revision undo stops at: where the project started, or where its
  oldest revisions were folded away.

project_revisions holds one row per revision: the operations as they were
sent, the forward operations that redo it exactly, and the inverse ones that
undo it exactly (ops.py's `restore`). Every SNAPSHOT_EVERY-th revision, and
the base, also holds the whole document, so any revision can be rebuilt from
the nearest snapshot (document_at). A project keeps at most MAX_REVISIONS
rows: past that, the oldest are folded into the next snapshot, which becomes
the base.

Small tweaks of the same property of the same item, each made within
COALESCE_SECONDS of the one before (a slider being dragged), are one
revision, so one undo takes the whole drag back. The time is the caller's
`now`, in seconds, so a test can say what time it is.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from video_editor.project import migrate, model, ops

SNAPSHOT_EVERY = 25
MAX_REVISIONS = 500
COALESCE_SECONDS = 1.0
LABEL_MAX = 120
LIST_MAX = 500

MEDIA_KINDS = model.MEDIA_KINDS
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXT = re.compile(r"\.[a-z0-9]{1,8}")


class StoreError(Exception):
    """Something the store can't do, in plain words."""


class NotFound(StoreError):
    pass


class Conflict(StoreError):
    """The project changed since the caller last read it. `current` is the
    revision it is at now."""

    def __init__(self, current: int):
        self.current = int(current)
        super().__init__("This project changed since it was last read here. Read it again, then make the change.")


class ReadOnly(StoreError):
    def __init__(self):
        super().__init__(migrate.READ_ONLY)


class Damaged(StoreError):
    """The saved document can't be read."""


class NothingToDo(StoreError):
    """Undo with nothing to undo, or redo with nothing to redo."""


# ---- small helpers ------------------------------------------------------------------------------


@contextmanager
def _writing(conn):
    """One write, whole or not at all, with the database's write lock held
    from its first read: two changes to a project can't both see the same
    head."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _seconds(now) -> float:
    return time.time() if now is None else float(now)


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now).isoformat(timespec="seconds")


def _row(conn, project_id) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (str(project_id),)).fetchone()
    if row is None:
        raise NotFound("There is no such project.")
    return row


def _document(row) -> tuple[dict, bool, bool]:
    """(the document at this version of the format, whether it is read-only,
    whether it was brought up to date from an older version)."""
    try:
        saved = _loads(row["doc"])
    except (TypeError, ValueError) as e:
        raise Damaged("This project's saved data can't be read.") from e
    try:
        doc, read_only = migrate.load(saved)
    except migrate.MigrationError as e:
        raise Damaged(str(e)) from e
    return doc, read_only, doc is not saved


def _clip_id(conn, row) -> int | None:
    """The project's clip, or None when that clip is gone. A re-render takes
    the old clip's row away before the new one is there (server/jobs.py);
    should it stop in between, the project's row still names the old id for
    a while. A clip's id is never used again, so nothing else is ever
    reached through it; here it is simply not reported."""
    clip_id = row["clip_id"]
    if clip_id is None:
        return None
    try:
        found = conn.execute("SELECT 1 FROM clips WHERE id = ?", (clip_id,)).fetchone()
    except sqlite3.OperationalError:
        return clip_id      # a database with no clips table (one a test built by hand)
    return clip_id if found is not None else None


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _loads(text):
    """Saved JSON, read strictly: NaN and Infinity are not JSON, and a row
    holding one is damaged (it could never be written back)."""
    return json.loads(text, parse_constant=_no_constant)


def _public(conn, row, doc: dict, read_only: bool, migrated: bool = False) -> dict:
    fixed = read_only or migrated
    return {
        "id": row["id"],
        "name": doc.get("name", "") if isinstance(doc.get("name"), str) else "",
        "clip_id": _clip_id(conn, row),
        "video_id": row["video_id"],
        "revision": row["head"],
        "can_undo": not fixed and row["cursor"] > row["base"],
        "can_redo": not fixed and row["cursor"] < row["tip"],
        "read_only": read_only,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "document": doc,
    }


def _label(label) -> str:
    # Half of a character (a cut emoji) can't be saved: left out.
    text = str(label or "").encode("utf-8", "ignore").decode("utf-8")
    return " ".join(text.split())[:LABEL_MAX]


def _revision(conn, project_id: str, seq: int):
    return conn.execute("SELECT * FROM project_revisions WHERE project_id = ? AND seq = ?",
                        (project_id, int(seq))).fetchone()


def _add_revision(conn, project_id: str, seq: int, head: int, now: float, *, label: str = "", sent=None,
                  forward=None, inverse=None, snapshot: str | None = None, coalesce: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project_revisions "
        "(project_id, seq, head, label, ops, forward, inverse, snapshot, coalesce, at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, seq, head, label, model.canonical(sent or []), model.canonical(forward or []),
         model.canonical(inverse or []), snapshot, coalesce, now, _stamp(now)))


def _save(conn, row, text: str, doc: dict, now: float, *, cursor: int, tip: int, base: int) -> int:
    """The project's row at its next head; Conflict when it isn't at the head
    it was read at."""
    head = row["head"] + 1
    done = conn.execute(
        "UPDATE projects SET doc = ?, name = ?, doc_schema = ?, head = ?, cursor = ?, tip = ?, base = ?, "
        "updated_at = ? WHERE id = ? AND head = ?",
        (text, doc.get("name", ""), doc["schema"], head, cursor, tip, base, _stamp(now), row["id"], row["head"]))
    if done.rowcount != 1:
        current = conn.execute("SELECT head FROM projects WHERE id = ?", (row["id"],)).fetchone()
        if current is None:
            raise NotFound("There is no such project.")
        raise Conflict(current["head"])
    return head


def _checked(conn, project_id, expected_revision):
    row = _row(conn, project_id)
    if type(expected_revision) is not int or expected_revision != row["head"]:
        raise Conflict(row["head"])
    doc, read_only, migrated = _document(row)
    if read_only:
        raise ReadOnly()
    return row, doc, migrated


# ---- projects -----------------------------------------------------------------------------------


def create(conn, document: dict, *, clip_id: int | None = None, video_id: str = "", now=None) -> dict:
    """A new project holding `document`, at revision 0. With `clip_id`, the
    project of that clip: a clip has one project, so when it already has one
    that project is returned as it is (its "created" is False)."""
    try:
        model.check(document)
    except model.ModelError as e:
        raise StoreError(f"That is not a project: {e}.") from e
    now = _seconds(now)
    with _writing(conn):
        if clip_id is not None:
            found = conn.execute("SELECT id FROM projects WHERE clip_id = ?", (int(clip_id),)).fetchone()
            if found is not None:
                row = _row(conn, found["id"])
                doc, read_only, migrated = _document(row)
                return {**_public(conn, row, doc, read_only, migrated), "created": False}
        project_id = uuid.uuid4().hex
        text = model.canonical(document)
        conn.execute(
            "INSERT INTO projects (id, name, clip_id, video_id, doc_schema, head, cursor, tip, base, doc, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?)",
            (project_id, document["name"], None if clip_id is None else int(clip_id), str(video_id or ""),
             document["schema"], text, _stamp(now), _stamp(now)))
        _add_revision(conn, project_id, 0, 0, now, snapshot=text)
        row = _row(conn, project_id)
    return {**_public(conn, row, model.clone(document), False), "created": True}


def get(conn, project_id) -> dict:
    """The project as it is now. A document saved by an older version comes
    back brought up to date (and is saved that way by its next change); one
    saved by a newer version comes back as it is, read-only."""
    row = _row(conn, project_id)
    return _public(conn, row, *_document(row))


def for_clip(conn, clip_id) -> str | None:
    """The id of a clip's project, or None when it has none."""
    row = conn.execute("SELECT id FROM projects WHERE clip_id = ?", (int(clip_id),)).fetchone()
    return None if row is None else row["id"]


def delete(conn, project_id) -> bool:
    """Remove a project, its history and its export records. Its media
    assets stay: other projects may use them. False when there was none."""
    with _writing(conn):
        gone = conn.execute("DELETE FROM projects WHERE id = ?", (str(project_id),)).rowcount
        conn.execute("DELETE FROM project_revisions WHERE project_id = ?", (str(project_id),))
        conn.execute("DELETE FROM project_exports WHERE project_id = ?", (str(project_id),))
    return bool(gone)


# ---- changing -----------------------------------------------------------------------------------


def _start_again(conn, row, text: str, now: float) -> tuple[int, int, int]:
    """History can't cross a change of the document's format: the revisions
    from before are folded into one snapshot of the document as it is now.
    Returns the project's (cursor, tip, base) from here on."""
    at = row["cursor"]
    conn.execute("DELETE FROM project_revisions WHERE project_id = ?", (row["id"],))
    _add_revision(conn, row["id"], at, row["head"], now, snapshot=text, label="Updated to this version")
    return at, at, at


def _fold(conn, project_id: str, base: int, tip: int) -> int:
    """Keep at most MAX_REVISIONS rows: the oldest are folded into the next
    snapshot, which becomes the base. Returns the base."""
    if tip - base + 1 <= MAX_REVISIONS:
        return base
    wanted = tip - MAX_REVISIONS + 1
    new_base = -(-wanted // SNAPSHOT_EVERY) * SNAPSHOT_EVERY
    row = _revision(conn, project_id, new_base)
    if row is None or row["snapshot"] is None:
        # Never expected (every SNAPSHOT_EVERY-th revision has one): made
        # now, from the rows about to go.
        text = model.canonical(_rebuilt(conn, project_id, new_base, base))
        conn.execute("UPDATE project_revisions SET snapshot = ? WHERE project_id = ? AND seq = ?",
                     (text, project_id, new_base))
    conn.execute("DELETE FROM project_revisions WHERE project_id = ? AND seq < ?", (project_id, new_base))
    return new_base


def apply_ops(conn, project_id, expected_revision, op_list, env, *, label: str = "", dry_run: bool = False,
              now=None) -> dict:
    """`op_list` applied to the project as one revision, when it is still at
    `expected_revision`.

    Returns {"id", "revision", "changed", "dry_run", "document", "inverse",
    "warnings", "created", "can_undo", "can_redo"}. A dry run checks
    everything and returns what would happen, and saves nothing. Raises
    NotFound, Conflict (the project moved on; nothing changed), ReadOnly (a
    newer version's document) and ops.OpError (an operation was refused;
    nothing changed)."""
    now = _seconds(now)
    with _writing(conn):
        row, doc, migrated = _checked(conn, project_id, expected_revision)
        result = ops.apply(doc, op_list, env)
        out = {"id": row["id"], "revision": row["head"], "changed": result.changed, "dry_run": bool(dry_run),
               "document": result.document, "inverse": result.inverse, "warnings": result.warnings,
               "created": result.created, "can_undo": not migrated and row["cursor"] > row["base"],
               "can_redo": not migrated and row["cursor"] < row["tip"]}
        if dry_run:
            if result.changed:
                out["can_undo"], out["can_redo"] = True, False
            return out
        cursor, tip, base = row["cursor"], row["tip"], row["base"]
        if migrated:
            cursor, tip, base = _start_again(conn, row, model.canonical(doc), now)
            if not result.changed:
                # Nothing to add, but the document is saved in today's format.
                conn.execute("UPDATE projects SET doc = ?, doc_schema = ?, cursor = ?, tip = ?, base = ? "
                             "WHERE id = ?", (model.canonical(doc), doc["schema"], cursor, tip, base, row["id"]))
                return out
        elif not result.changed:
            return out
        head = row["head"] + 1
        key = ops.coalesce_key(op_list)
        last = _revision(conn, row["id"], cursor) if key and not migrated and cursor == tip > base else None
        before = None
        if (last is not None and last["coalesce"] == key and last["head"] == row["head"]
                and 0 <= now - last["at"] <= COALESCE_SECONDS):
            try:
                before = ops.apply(doc, _loads(last["inverse"]), env, internal=True).document
            except (ValueError, TypeError):
                before = None       # a row that can't be read: this tweak is a step of its own
        if before is not None and model.canonical(before) == result.text:
            # The tweaks ended where they began (a slider dragged out and
            # back, a switch flipped twice): no step is left to undo, so the
            # revision goes, and the project is at the one before it.
            conn.execute("DELETE FROM project_revisions WHERE project_id = ? AND seq >= ?", (row["id"], cursor))
            seq = cursor - 1
            out["revision"] = _save(conn, row, result.text, result.document, now, cursor=seq, tip=seq, base=base)
            out["can_undo"], out["can_redo"] = seq > base, False
            return out
        if before is not None:
            # One more tweak of the same thing, a moment after the last: the
            # revision now goes from before the first tweak to after this one.
            inverse, forward = ops.steps(before, result.document, None, result.text)
            seq = cursor
            _add_revision(conn, row["id"], seq, head, now, label=_label(label) or last["label"], sent=op_list,
                          forward=forward, inverse=inverse, coalesce=key,
                          snapshot=result.text if seq % SNAPSHOT_EVERY == 0 else None)
        else:
            seq = cursor + 1
            conn.execute("DELETE FROM project_revisions WHERE project_id = ? AND seq > ?", (row["id"], cursor))
            _add_revision(conn, row["id"], seq, head, now, label=_label(label), sent=op_list,
                          forward=result.forward, inverse=result.inverse, coalesce=key,
                          snapshot=result.text if seq % SNAPSHOT_EVERY == 0 else None)
            base = _fold(conn, row["id"], base, seq)
        out["revision"] = _save(conn, row, result.text, result.document, now, cursor=seq, tip=seq, base=base)
        out["can_undo"], out["can_redo"] = seq > base, False
        return out


def _step(conn, project_id, expected_revision, back: bool, now) -> dict:
    now = _seconds(now)
    with _writing(conn):
        row, doc, migrated = _checked(conn, project_id, expected_revision)
        cursor = row["cursor"]
        if back and (migrated or cursor <= row["base"]):
            raise NothingToDo("There is nothing to undo.")
        if not back and (migrated or cursor >= row["tip"]):
            raise NothingToDo("There is nothing to redo.")
        revision = _revision(conn, row["id"], cursor if back else cursor + 1)
        if revision is None:
            raise Damaged("This project's history can't be read.")
        try:
            steps = _loads(revision["inverse" if back else "forward"])
            result = ops.apply(doc, steps, ops.Env(), internal=True)
        except (ValueError, TypeError) as e:
            raise Damaged("This project's history can't be read.") from e
        cursor = cursor - 1 if back else cursor + 1
        head = _save(conn, row, result.text, result.document, now, cursor=cursor, tip=row["tip"], base=row["base"])
        return {"id": row["id"], "revision": head, "changed": result.changed, "document": result.document,
                "label": revision["label"], "can_undo": cursor > row["base"], "can_redo": cursor < row["tip"]}


def undo(conn, project_id, expected_revision, *, now=None) -> dict:
    """The last revision taken back: its inverse operations applied, as a new
    head. Returns {"id", "revision", "document", "label", "can_undo", "can_redo"}."""
    return _step(conn, project_id, expected_revision, True, now)


def redo(conn, project_id, expected_revision, *, now=None) -> dict:
    """The revision that undo took back, applied again, as a new head."""
    return _step(conn, project_id, expected_revision, False, now)


# ---- history ------------------------------------------------------------------------------------


def revisions(conn, project_id, limit: int = 100) -> dict:
    """The project's history, newest first: {"revision", "cursor", "base",
    "tip", "revisions": [{"seq", "label", "ops", "created_at", "current",
    "undone", "start", "snapshot"}]}. "undone" marks what redo would bring
    back; "start" the state undo stops at."""
    row = _row(conn, project_id)
    rows = conn.execute(
        "SELECT seq, label, ops, created_at, snapshot IS NOT NULL AS kept FROM project_revisions "
        "WHERE project_id = ? ORDER BY seq DESC LIMIT ?",
        (row["id"], max(1, min(int(limit), LIST_MAX)))).fetchall()
    listed = []
    for r in rows:
        try:
            sent = _loads(r["ops"])
        except ValueError:
            sent = []
        listed.append({"seq": r["seq"], "label": r["label"], "ops": sent, "created_at": r["created_at"],
                       "current": r["seq"] == row["cursor"], "undone": r["seq"] > row["cursor"],
                       "start": r["seq"] == row["base"], "snapshot": bool(r["kept"])})
    return {"revision": row["head"], "cursor": row["cursor"], "base": row["base"], "tip": row["tip"],
            "revisions": listed}


def _rebuilt(conn, project_id: str, seq: int, base: int) -> dict:
    snap = conn.execute(
        "SELECT seq, snapshot FROM project_revisions WHERE project_id = ? AND seq <= ? AND seq >= ? "
        "AND snapshot IS NOT NULL ORDER BY seq DESC LIMIT 1", (project_id, seq, base)).fetchone()
    if snap is None:
        raise Damaged("This project's history can't be read.")
    try:
        doc = _loads(snap["snapshot"])
        for r in conn.execute(
                "SELECT forward FROM project_revisions WHERE project_id = ? AND seq > ? AND seq <= ? ORDER BY seq",
                (project_id, snap["seq"], seq)).fetchall():
            doc = ops.apply(doc, _loads(r["forward"]), ops.Env(), internal=True).document
    except (ValueError, TypeError) as e:
        raise Damaged("This project's history can't be read.") from e
    return doc


def document_at(conn, project_id, seq: int) -> dict:
    """The document as revision `seq` left it, rebuilt from the nearest
    snapshot before it. As it was saved: not brought up to date."""
    row = _row(conn, project_id)
    if type(seq) is not int or not row["base"] <= seq <= row["tip"]:
        raise NotFound("The project has no such revision.")
    return _rebuilt(conn, row["id"], seq, row["base"])


# ---- media assets -------------------------------------------------------------------------------


def assets_dir(data_dir) -> Path:
    return Path(data_dir) / "media" / "assets"


def asset_path(data_dir, sha256: str, ext: str = "") -> Path:
    """Where an imported file is kept: named by its sha256, so the same file
    imported twice is kept once."""
    if not _SHA256.fullmatch(str(sha256)):
        raise StoreError("That is not the sha256 of an imported file.")
    return assets_dir(data_dir) / sha256[:2] / f"{sha256}{ext if _EXT.fullmatch(ext or '') else ''}"


def add_asset(conn, data_dir, source, *, kind: str, name: str = "", facts: dict | None = None, now=None) -> dict:
    """Copy a file into the project media (data/media/assets/) and record
    it. `facts` is what is known about it ({"duration_us", "width", "height",
    "fps": {"num", "den"}, "has_audio"}). Returns its row; the file it
    already holds, when this one was imported before."""
    if kind not in MEDIA_KINDS:
        raise StoreError(f"A media file is one of {', '.join(MEDIA_KINDS)}.")
    source = Path(source)
    digest, size = hashlib.sha256(), 0
    with open(source, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    sha256 = digest.hexdigest()
    found = asset(conn, sha256)
    ext = source.suffix.lower() if _EXT.fullmatch(source.suffix.lower()) else ""
    target = asset_path(data_dir, sha256, found["ext"] if found else ext)
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
        finally:
            # Never let the tidying replace the error being raised (#74).
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if found is not None:
        return found
    facts = facts or {}
    fps = facts.get("fps") if isinstance(facts.get("fps"), dict) else {}
    now = _seconds(now)
    with _writing(conn):
        conn.execute(
            "INSERT OR IGNORE INTO media_assets (sha256, kind, name, ext, size, duration_us, width, height, "
            "fps_num, fps_den, has_audio, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sha256, kind, str(name or source.name)[:model.NAME_MAX], ext, size, int(facts.get("duration_us") or 0),
             int(facts.get("width") or 0), int(facts.get("height") or 0), int(fps.get("num") or 0),
             int(fps.get("den") or 0), 1 if facts.get("has_audio", kind != "image") else 0, _stamp(now)))
    return asset(conn, sha256)


def asset(conn, sha256: str) -> dict | None:
    row = conn.execute("SELECT * FROM media_assets WHERE sha256 = ?", (str(sha256),)).fetchone()
    return None if row is None else dict(row)


# ---- exports ------------------------------------------------------------------------------------


def add_export(conn, project_id, revision: int, *, clip_id: int | None = None, path: str = "", preset=None,
               status: str = "queued", now=None) -> int:
    """Record an export of the project as it was at `revision` (its head).
    The export compiler (step 7) writes these; nothing here renders."""
    now = _seconds(now)
    with _writing(conn):
        _row(conn, project_id)
        cur = conn.execute(
            "INSERT INTO project_exports (project_id, head, clip_id, path, preset, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(project_id), int(revision), None if clip_id is None else int(clip_id), str(path or ""),
             "" if preset is None else model.canonical(preset), str(status), _stamp(now), _stamp(now)))
    return int(cur.lastrowid)


def set_export(conn, export_id: int, *, now=None, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("clip_id", "path", "status", "error")}
    if not allowed:
        return
    now = _seconds(now)
    with _writing(conn):
        conn.execute(
            f"UPDATE project_exports SET {', '.join(f'{k} = ?' for k in allowed)}, updated_at = ? WHERE id = ?",
            (*allowed.values(), _stamp(now), int(export_id)))


def exports(conn, project_id) -> list[dict]:
    """The project's exports, newest first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM project_exports WHERE project_id = ? ORDER BY id DESC", (str(project_id),)).fetchall()]
