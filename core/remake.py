"""Make clips again (#190): a video's clips made again with new settings,
without adding the video again.

POST /videos/{video_id}/remake (server/api.py) queues an ordinary process job
for the video: forced, as Process again is, plus `replace_untouched`. Only
that flag brings this module in. The worker (server/jobs.py) wraps the run in
a Run, which leaves every clip from before as it was until the run has
finished:

- It doesn't start while something is working with one of the video's
  clips (busy_clips(): a request holding it, core/in_use.py; an export on
  the editor lane; a publish job waiting or under way). The routes refuse,
  and the job, as it starts, waits a little (START_WAIT) and then ends with
  nothing changed. So no clip's file can be in use as it is put aside.
- As the job starts, each old clip's file is put out of the run's way (Run
  set_aside). A run writes a clip's file under a name made from its whole
  seconds, and a story reel's under what it is (core/pipeline.py), so a
  moment found again, or a new one with the same whole seconds, would be
  written over a clip from before, a kept one too.
- The run is given a database (Run.db) that notes each 9:16 clip it makes and
  changes no row from before: what the run would write on one (the new file
  of a moment found again, a story reel joined again) is held back. The
  files it holds back, and those of the clips from before, are held in
  core/in_use.py until the run is over, so nothing deletes them meanwhile
  (Storage's clean-up, DELETE /clips/{clip_id}): no row points at them.
- A run of the video's own copy (`local:<id>`) keeps a second name for it
  while it works, so the run's first step, which deletes a copy FFprobe
  can't read, can't lose it (Run._keep_source).
- Once the run has finished, Run.finish() deletes the video's old Shorts that
  nobody used and that the run didn't make again. An old one nobody used
  that the run did make again takes the run's new file, render options,
  score and hook, and keeps its title, if that file is still there. A clip a
  person or a feature used is kept as it is, its file too (classify,
  below), and so is every Longform output and story reel: the run's own
  render of its moment is dropped. A clip with no fingerprint of what its
  run made it with (core/state.py as_made: made before this update, or by
  another version of Clips Kitty) is kept the same way, unless the run makes
  its window again: then it is made again in place, and keeps its title,
  description and hashtags.
- Run.stopped() after a run that failed or was cancelled: nothing is
  replaced, and every clip from before keeps its own file.
- From just before the first file moves until every one is settled, the
  run's plan for them is saved (JOURNAL), each step in the same commit as
  the row change it goes with. When the app is closed in the middle
  (taskkill, a power cut, Windows shutting down), recover() finishes it as
  the engine starts, before the worker takes a job: every clip from before
  plays its own file again, and the video's own copy is back.

Each old clip is judged again in the moment it would change, deleted or
made again in place: inside a write transaction, so nothing can record a use
of it in between, and while no request can start working on it
(core/in_use.py). Every request that reads a clip and then writes for it (a
publish, an export, a caption fix, a new title, a render) holds it there
until what it did is recorded, so it keeps the clip too.

Nothing from before changes when the run fails, is cancelled, stops halfway
because the app closed, makes no clips, or couldn't make every clip it set
out to make (a render failed): finish() runs only after a run that finished
and wasn't cancelled (server/jobs.py), and changes nothing in the other
cases. A Cancel counts whenever it was pressed after the job started, even
one core/pipeline.py's own fresh start cleared (Run.cancelled), up to the
moment finish() changes its first clip from before. The files put aside go
back under their first names where they can (Run._settle).

Standard library and core/ only, besides core.pipeline._edited (D33), which
is imported when a clip has render options to read, sources.dispatch, which
find_link() imports to read links, and video.encoding, which reads the
video's own copy when its drive can't give it a second name.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from core import cancel, in_use
from core.paths import cached_source, discard, within
from core.state import LANGUAGE_EXPORT, UNMARKED_CLIPS, _now, as_made

# The job options a Make clips again carries over from the video's last run:
# JobIn's (server/api.py) less the link and force, what belonged to whoever
# queued that run rather than to its clips (its webhook, and `then`, such as
# a publish once it finished), and Longform: this makes the 9:16 clips again.
# tests/test_remake_routes.py keeps it in step with JobIn.
OPTIONS = (
    "whole", "max_clips", "caption_style", "captions", "long_clips", "filter", "min_score",
    "focus", "sport", "watermark_profile_id", "podcast", "vertical_live", "gaming",
    "gaming_scoring", "gaming_layout", "gaming_remember", "hashtags", "pipeline", "rate",
    "understand", "edit",
)

# Why a clip is kept, in this order: a clip is counted under its first one.
REASONS = (
    "longform",     # a 16:9 Longform output: never replaced
    "story_reel",   # a match's story reel: never replaced
    "edited",       # a person changed it, asked for a render of it, chose its thumbnail, or decided about
                    # an edit a plugin suggested
    "published",    # an upload or post of it, sent, waiting or failed
    "scheduled",    # it has a schedule slot
    "exported",     # exported (even if un-starred since), or starred by hand
    "translated",   # a translation of it is saved
    "submitted",    # in a clipping campaign (Discord or a website), or a post of it is on its way to one
    "chosen",       # a watched channel's first publish chose it
    "busy",         # a render, translation, export, publish or AI edit of it is waiting or under way
    "older",        # made before this update, or by another version: a person may have changed its words
    "unsure",       # Clips Kitty can't read whether it was used, so it is kept
)

# Why finish() kept a clip it was about to make again, which only its job
# log says: the run's new file of it was gone by then.
_LOGGED = ("new_file_gone",)

# What the log says for each, in plain words.
_SAID = {
    "longform": "16:9 Longform", "story_reel": "story reel", "edited": "edited",
    "published": "published or sent to publish", "scheduled": "scheduled",
    "exported": "exported or starred", "translated": "translated", "submitted": "in a campaign",
    "chosen": "chosen by a watched channel", "busy": "being edited, exported or published",
    "older": "made before this update or by another version", "unsure": "couldn't tell if it was used",
    "new_file_gone": "whose new video was gone",
}

# The statuses a run gives a clip (core/pipeline.py _register_clip, and
# StateDB.add_clip's default). Any other says something happened to it.
_AS_MADE = ("queued", "rendered", "")

_VERTICAL = "__vertical"   # sources/vertical.py SUFFIX: Vertical Live's own download

# campaigns/post_links.py PENDING_PREFIX: the app_state key a publish with
# "send the link to Discord" saves its intent under before it uploads.
# tests/test_remake.py keeps the two the same.
_POST_LINK = "discord_post_link:"

# StateDB.add_clip's parameters after (video_id, start, end), in order.
_ADD_CLIP = ("score", "hook", "path", "status", "title", "description", "hashtags", "scores", "render_opts")

# What an old clip remade in place (_in_place: nobody used it, or it was made
# before edits were marked) takes from the run that made its window again,
# over what core/pipeline.py _register_clip would have written on its row
# (the new file and scores, held back while the run works: _Noting.set_clip).
# Without them its render options would still be the old run's, and the
# editor and every later render of it would use those (old captions on or
# off, the old caption style). Its title, description and hashtags stay, as
# in a forced run: one written by hand before edited_at existed can't be
# told apart.
_FROM_RUN = ("score", "hook", "render_opts")

# What marks a file put out of a run's way (Run.set_aside), before the clip's
# id: clip_00010-00070.clip-17.mp4. No run writes such a name.
_ASIDE = ".clip-"

# The publish worker's (server/publisher.py) jobs that may still read their
# clip's file.
_PUBLISHING = ("queued", "running")

# How long a Make clips again job waits, as it starts, for its video's clips
# to be free (busy_clips) before it ends with nothing changed, and how often
# it looks. A caption fix or a new title takes a moment; a publish a few
# minutes, and the routes refuse to queue one while it's sending.
START_WAIT = 120.0
_LOOK_AGAIN = 0.5

# Why a Make clips again isn't started, and why one that was queued ended as
# it started, in plain words.
BUSY = ("Clips Kitty is working with one of this video's clips right now (publishing, exporting or "
        "editing it). Make its clips again once that's done.")
BUSY_AT_START = ("Clips Kitty was still working with one of this video's clips (publishing, exporting "
                 "or editing it), so nothing was made again. Make its clips again once that's done.")
UNREADABLE_COPY = ("Clips Kitty couldn't read this video's own copy just now, so nothing was made again. "
                   "The copy is kept: try again, or add the video again.")

# The second name a run of the video's own copy keeps for it while it works
# (Run._keep_source): <video id>.remaking.mp4 beside it, a hard link.
# core/housekeeping.py knows it too.
_KEPT = ".remaking"

# app_state: a Make clips again run's plan for the files of the clips from
# before and the video's own copy, keyed by the video's id, from just before
# the first of them moves until the last is settled (Run._write_journal).
# What recover() finishes when the app was closed in between.
JOURNAL = "remake_journal:"


# ---- the last run's options, and the source --------------------------------


def _payload(raw) -> dict | None:
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _makes_shorts(payload: dict) -> bool:
    """A process job that makes 9:16 clips: no Longform, or Longform with
    "Also make 9:16 Shorts"."""
    longform = payload.get("longform")
    return not longform or bool(isinstance(longform, dict) and longform.get("shorts"))


def last_options(db, video_id: str) -> tuple[dict, int | None]:
    """The options the video's last 9:16 run used, cleaned to OPTIONS, and
    that job's id. A finished run first, else the last one that failed or
    was cancelled; never one still waiting or running. ({}, None) when no
    such job is left (clearing the queue's history deletes them): what a job
    with nothing chosen runs with, which is the settings' own."""
    found = None
    for row in db.conn.execute(
        "SELECT id, status, payload FROM jobs WHERE type = 'process' AND video_id = ? "
        "AND status IN ('done', 'failed', 'cancelled') ORDER BY id DESC",
        (video_id,),
    ).fetchall():
        payload = _payload(row["payload"])
        if payload is None or not _makes_shorts(payload):
            continue
        if row["status"] == "done":
            found = (row, payload)
            break
        if found is None:
            found = (row, payload)
    if found is None:
        return {}, None
    row, payload = found
    return {k: payload[k] for k in OPTIONS if payload.get(k) is not None}, int(row["id"])


def find_link(db, video_id: str) -> str:
    """The link this video can be downloaded from again: the one its runs
    recorded (videos.source_url), else one a process job or a watched channel
    used for it. Only a link that names this very video counts: a channel or
    live page would fetch another one."""
    from sources.dispatch import identify

    found = []
    row = db.conn.execute("SELECT source_url FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    if row is not None and row["source_url"]:
        found.append(row["source_url"])
    for job in db.conn.execute(
        "SELECT payload FROM jobs WHERE type = 'process' AND video_id = ? ORDER BY id DESC", (video_id,)
    ).fetchall():
        payload = _payload(job["payload"]) or {}
        found.append(str(payload.get("url") or ""))
    found += [r["url"] for r in db.conn.execute(
        "SELECT url FROM watch_items WHERE video_id = ?", (video_id,)).fetchall()]
    for url in found:
        url = (url or "").strip()
        if not url or url.startswith("local:"):
            continue
        try:
            if identify(url)[1] == video_id:
                return url
        except Exception:
            continue
    return ""


def source(db, video_id: str, downloads: Path) -> dict:
    """Where a Make clips again of this video gets it from:

    - kind: "file" for a video added from this PC, "link" for one added by link;
    - downloaded: whether its download is in data/downloads;
    - link: where it can be downloaded again ("" for a file, or none found);
    - url: what the job is queued with: the link (the run uses the download
      when it is there, and downloads it again when not), "local:<id>" for a
      file, or for a linked video whose link is lost but whose download is
      here; "" when neither is left;
    - usable: whether a job can be queued at all."""
    local = video_id.startswith("local_")   # server/api.py add_local_video's ids
    plain = cached_source(downloads, video_id) is not None
    vertical = not local and cached_source(downloads, video_id + _VERTICAL) is not None
    link = "" if local else find_link(db, video_id)
    if local:
        url = f"local:{video_id}" if plain else ""
    else:
        url = link or (f"local:{video_id}" if plain else "")
    return {"kind": "file" if local else "link", "downloaded": plain or vertical, "link": link,
            "url": url, "usable": bool(url)}


# ---- which clips are kept ----------------------------------------------------


def _edited(opts: dict) -> bool:
    """core.pipeline._edited (D33): the clip holds a key only a person sets on
    one clip. Imported here, when a clip has options to read: core.pipeline
    brings in the whole engine."""
    from core.pipeline import _edited as by_hand

    return by_hand(opts)


def _window(start, end) -> tuple[float, float]:
    """A clip's window as rows know it (StateDB.add_clip)."""
    return round(float(start), 2), round(float(end), 2)


def _unmarked_through(db) -> int:
    """The last clip made before edits were marked (core/state.py
    UNMARKED_CLIPS): a person may have changed the words of any clip up to
    it with no mark. Unreadable, every clip there is now (when in doubt, a
    clip is kept)."""
    row = db.conn.execute("SELECT value FROM app_state WHERE key = ?", (UNMARKED_CLIPS,)).fetchone()
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return int(db.conn.execute("SELECT COALESCE(MAX(id), 0) FROM clips").fetchone()[0] or 0)


def _evidence(db, video_id: str, data_dir: Path | None = None) -> dict:
    """Everything recorded about the video's clips that says one was used,
    read once: by clip id, and by window (a post, an upload and a campaign
    submission keep the window of the clip they were made from). With the
    data folder, the thumbnails people chose are looked for too."""
    mine = "SELECT id FROM clips WHERE video_id = ?"

    def ids(table: str) -> set[int]:
        return {int(r[0]) for r in db.conn.execute(
            f"SELECT clip_id FROM {table} WHERE clip_id IN ({mine})", (video_id,)).fetchall()}

    def windows(table: str) -> set[tuple[float, float]]:
        return {_window(r[0], r[1]) for r in db.conn.execute(
            f"SELECT start_s, end_s FROM {table} WHERE video_id = ?", (video_id,)).fetchall()}

    posts = ("uploads", "clip_publishes", "publish_jobs")
    feedback: dict[int, set[str]] = {}
    for r in db.conn.execute(f"SELECT clip_id, action FROM clip_feedback WHERE clip_id IN ({mine})",
                             (video_id,)).fetchall():
        feedback.setdefault(int(r[0]), set()).add(str(r[1] or ""))
    chosen: set[tuple[float, float]] = set()
    for r in db.conn.execute("SELECT chosen_clips FROM watch_items WHERE video_id = ? AND chosen_clips != ''",
                             (video_id,)).fetchall():
        try:
            chosen |= {_window(start, end) for start, end in json.loads(r[0])}
        except (TypeError, ValueError):
            continue
    busy: set[int] = set()
    # Exported in other languages, as the translate job's own row says: a
    # translated export that has run (or stopped partway) names its clips.
    # Another version of Clips Kitty leaves no other mark of one (this one
    # also writes note_language_export's), and the row lasts until the
    # Queue page's history is cleared.
    language_jobs: set[int] = set()
    # A render a person asked for (Apply, a caption fix, a new start or end,
    # the AI chat, a render before publishing), in any state: one that
    # failed or was cancelled still holds their edit, for Retry. The routes
    # also mark the clip edited as they queue it, which lasts when the Queue
    # page's history is cleared (core/state.py StateDB.mark_edited).
    rendered: set[int] = set()
    for r in db.conn.execute("SELECT type, status, payload FROM jobs WHERE type IN ('translate', 'render')"
                             ).fetchall():
        payload = _payload(r["payload"]) or {}
        named = [payload.get("clip_id")] if r["type"] == "render" else list(payload.get("clip_ids") or [])
        live = r["status"] in ("queued", "running")
        exported = r["type"] == "translate" and not live and payload.get("stage", "export") != "translate"
        for clip_id in named:
            try:
                clip_id = int(clip_id)
            except (TypeError, ValueError):
                continue
            if live:
                busy.add(clip_id)
            if exported:
                language_jobs.add(clip_id)
            if r["type"] == "render":
                rendered.add(clip_id)
    submitted_ids, submitted_windows = ids("campaign_submissions"), windows("campaign_submissions")
    # A publish that sends its link to Discord saves that intent before it
    # uploads (campaigns/post_links.py prepare), and its submission row comes
    # only once the post is live: the clip is on its way to one already.
    for r in db.conn.execute("SELECT value FROM app_state WHERE key GLOB ?", (_POST_LINK + "*",)).fetchall():
        intent = _payload(r[0]) or {}
        if intent.get("video_id") != video_id:
            continue
        try:
            submitted_ids.add(int(intent["clip_id"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            submitted_windows.add(_window(intent["start_s"], intent["end_s"]))
        except (KeyError, TypeError, ValueError):
            pass
    # Exported in other languages (note_language_export): the clip's id is
    # in the key, so a mark whose value can't be read still counts. Ids are
    # never reused (AUTOINCREMENT), and a re-render marks its new row edited.
    language_exports = set(language_jobs)
    for (key,) in db.conn.execute("SELECT key FROM app_state WHERE key GLOB ?",
                                  (LANGUAGE_EXPORT + "*",)).fetchall():
        try:
            language_exports.add(int(str(key)[len(LANGUAGE_EXPORT):]))
        except ValueError:
            continue
    return {
        "published_ids": set().union(*(ids(t) for t in posts)),
        "published_windows": set().union(*(windows(t) for t in posts)),
        "translated": ids("clip_translations"),
        "language_exports": language_exports,
        "submitted_ids": submitted_ids,
        "submitted_windows": submitted_windows,
        "feedback": feedback,
        "rendered": rendered,
        # A clip opened in the new editor has a project (core/state.py
        # projects.clip_id, which follows the clip through a re-render): a
        # person's work on it, whether or not it was rendered since.
        "projects": _project_clips(db, video_id),
        "chosen": chosen,
        "busy": busy,
        # A thumbnail a person chose for the clip (server/youtube_api.py
        # choose_thumbnail) is kept only as this file, which both publish
        # dialogs send: thumbnails/clip_<id>_chosen.jpg.
        "thumbnails": None if data_dir is None else Path(data_dir) / "thumbnails",
        "unmarked_through": _unmarked_through(db),
    }


def _project_clips(db, video_id: str) -> set[int]:
    """The video's clips that have a project in the new editor. None on a
    database with no projects table (one a test built by hand)."""
    import sqlite3

    try:
        rows = db.conn.execute(
            "SELECT clip_id FROM projects WHERE clip_id IN (SELECT id FROM clips WHERE video_id = ?)",
            (video_id,)).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {int(r[0]) for r in rows}


def _made_as(row) -> str:
    """The row's fingerprint of what the run that made it gave it
    (core/state.py as_made), "" when it has none."""
    try:
        return str(row["as_made"] or "")
    except (IndexError, KeyError):
        return ""


def _first_exported(row) -> str:
    """When the clip was first really exported (core/state.py
    StateDB.mark_exported), which neither the star nor erasing a creator's
    learning clears; "" when never, or when the row has no such column."""
    try:
        return str(row["first_exported_at"] or "")
    except (IndexError, KeyError):
        return ""


def _suggestion_decided(scores) -> bool:
    """A person used or hid an edit a plugin suggested for the clip
    (plugins/edit_marks.py): a run writes them all as "new"."""
    try:
        entries = (json.loads(scores or "{}") or {}).get("plugin_edits") or []
    except (TypeError, ValueError, AttributeError):
        return False
    return any(isinstance(e, dict) and e.get("state") not in (None, "new") for e in entries)


def _reasons(row, seen: dict, busy: set[int]) -> list[str]:
    try:
        opts = json.loads(row["render_opts"] or "{}")
    except (TypeError, ValueError):
        opts = None
    if isinstance(opts, dict) and opts.get("profile"):
        return ["longform"]
    if isinstance(opts, dict) and opts.get("reel"):
        return ["story_reel"]
    clip_id = int(row["id"])
    window = _window(row["start_s"], row["end_s"])
    actions = seen["feedback"].get(clip_id, set())
    status = row["status"] or ""
    found = set()
    if not isinstance(opts, dict):
        found.add("unsure")
    elif opts and _edited(opts):
        found.add("edited")
    if (row["edited_at"] or _suggestion_decided(row["scores"]) or clip_id in seen["rendered"]
            or actions - {"exported"}):   # captions_edited, timestamps_adjusted, rerendered
        found.add("edited")
    if seen["thumbnails"] is not None and (seen["thumbnails"] / f"clip_{clip_id}_chosen.jpg").is_file():
        found.add("edited")
    if clip_id in seen.get("projects", ()):
        found.add("edited")
    made = _made_as(row)
    if not made:
        # No fingerprint: made before this update, or by a version of Clips
        # Kitty that keeps none, so a person may have changed its words with
        # no mark this version reads.
        found.add("older")
    elif made != as_made(row["title"], row["description"], row["hashtags"], row["start_s"], row["end_s"]):
        # Its words, start or end aren't what its run made it with: changed
        # since, in this version or another.
        found.add("edited")
    if (status in ("uploaded", "failed") or clip_id in seen["published_ids"]
            or window in seen["published_windows"]):
        found.add("published")
    if status == "scheduled" or row["scheduled_for"]:
        found.add("scheduled")
    if status not in (*_AS_MADE, "uploaded", "failed", "scheduled"):
        found.add("unsure")
    if (row["exported_at"] or _first_exported(row) or "exported" in actions
            or clip_id in seen["language_exports"]):
        found.add("exported")
    if clip_id in seen["translated"]:
        found.add("translated")
    if clip_id in seen["submitted_ids"] or window in seen["submitted_windows"]:
        found.add("submitted")
    if window in seen["chosen"]:
        found.add("chosen")
    if clip_id in seen["busy"] or clip_id in busy:
        found.add("busy")
    if clip_id <= seen["unmarked_through"]:
        found.add("older")
    return [r for r in REASONS if r in found]


def _in_place(clip: dict) -> bool:
    """An old clip a run that makes its window again remakes in place, with
    what the run made it with (Run._made_again): one nobody used, or one
    whose only reason is that it has no fingerprint of its run, made before
    this update or by another version (its title, description and hashtags
    stay, the only things a person could have changed on it without a mark
    this version reads)."""
    return clip["replace"] or clip["reasons"] == ["older"]


def classify(db, video_id: str, busy: Iterable[int] = (), data_dir: Path | None = None) -> list[dict]:
    """Every clip of the video, as a Make clips again would treat it:
    {"id", "start_s", "end_s", "replace", "reasons"}. `replace` is true for a
    9:16 clip nobody used: one the run makes again is made with the new
    settings, and one it doesn't is deleted. Any reason keeps the clip as it
    is (REASONS, in that order), except a clip made before edits were marked
    whose window the run makes again (_in_place). `busy` is the clips the
    editor lane is exporting or about to, which only the app's memory knows;
    the clips a request is working with (core/in_use.py) are added to it.
    With `data_dir`, the thumbnails people chose are looked for too.

    "Used" is every mark Clips Kitty keeps of a person or a feature using a
    clip (docs/editor-suite/api/step2-remake.md lists them), read now, so
    something done while the run was working counts too."""
    seen = _evidence(db, video_id, data_dir)
    busy = {int(b) for b in busy} | in_use.held()
    out = []
    for row in db.conn.execute(
        "SELECT * FROM clips WHERE video_id = ? ORDER BY start_s, end_s, id", (video_id,)
    ).fetchall():
        reasons = _reasons(row, seen, busy)
        out.append({"id": int(row["id"]), "start_s": float(row["start_s"]), "end_s": float(row["end_s"]),
                    "replace": not reasons, "reasons": reasons})
    return out


def counts(judged: list[dict], order: tuple[str, ...] = REASONS) -> dict:
    """{"total", "replaced", "kept", "kept_because": {reason: n}}, each kept
    clip counted under its first reason, so kept_because adds up to kept."""
    first = [clip["reasons"][0] for clip in judged if clip["reasons"]]
    because = {reason: first.count(reason) for reason in order if reason in first}
    kept = len(first)
    return {"total": len(judged), "replaced": len(judged) - kept, "kept": kept, "kept_because": because}


def note_language_export(db, clip, languages: Iterable[str]) -> None:
    """Mark a clip as exported in other languages (server/jobs.py, the
    translate job's export stage), before its files are written: the
    subtitled, burned or dubbed videos go only into the person's folder, so
    nothing else would say the clip was used, and Make clips again keeps it
    (exported). An app_state key, so nothing else reads it: Export all and
    the star still go by exported_at, and creator learning by clip_feedback."""
    clip_id = int(clip["id"])
    key = f"{LANGUAGE_EXPORT}{clip_id}"
    before = _payload(db.get_flag(key, "")) or {}
    done = before.get("languages") if isinstance(before.get("languages"), list) else []
    db.set_flag(key, json.dumps({
        "clip_id": clip_id, "video_id": clip["video_id"],
        "languages": sorted({*map(str, done), *map(str, languages)}), "at": _now(),
    }))


def _reading(db, video_id: str, held: Iterable[int], lane: Iterable[int]) -> set[int]:
    """The clips something may be reading the file of now: one a request
    holds (core/in_use.py `held`), one the editor lane is exporting or about
    to (`lane`), and one a YouTube publish is sending or about to send (the
    publish worker reads its path as it starts), found by its id or, for
    this video, by its window."""
    out = {int(c) for c in held} | {int(c) for c in lane}
    by_window: dict[tuple[float, float], set[int]] = {}
    for r in db.conn.execute("SELECT id, start_s, end_s FROM clips WHERE video_id = ?", (video_id,)).fetchall():
        by_window.setdefault(_window(r[1], r[2]), set()).add(int(r[0]))
    marks = ", ".join("?" * len(_PUBLISHING))
    for r in db.conn.execute(
            f"SELECT clip_id, video_id, start_s, end_s FROM publish_jobs WHERE status IN ({marks})",
            _PUBLISHING).fetchall():
        out.add(int(r[0]))
        if r[1] == video_id:
            out |= by_window.get(_window(r[2], r[3]), set())
    return out


def busy_clips(db, video_id: str, lane: Iterable[int] = ()) -> set[int]:
    """The video's clips something is working with right now: a request
    holds it (a publish, an export, a caption fix, a render being queued:
    core/in_use.py), the editor lane is exporting it or about to (`lane`), or
    a publish job of it is waiting or under way. Make clips again doesn't
    start while there is one (BUSY), so no file it puts out of the run's way
    is being read."""
    mine = {int(r[0]) for r in db.conn.execute("SELECT id FROM clips WHERE video_id = ?", (video_id,)).fetchall()}
    return mine & _reading(db, video_id, in_use.held(), lane)


# ---- deleting --------------------------------------------------------------------


def _same_file(path) -> str:
    """One key per file, however its path is written (core/in_use.py)."""
    return in_use.file_key(path)


def _files_in_use(db, paths: Iterable) -> set[str]:
    """Which of these files a clip row points at, as _same_file keys. Only
    the rows whose file has one of their names are resolved."""
    paths = [p for p in paths if p]
    wanted = {_same_file(p) for p in paths}
    names = ({os.path.basename(key) for key in wanted}
             | {os.path.normcase(os.path.basename(str(p))) for p in paths})
    used = set()
    for (path,) in db.conn.execute("SELECT path FROM clips WHERE path IS NOT NULL AND path != ''").fetchall():
        if os.path.normcase(os.path.basename(str(path))) in names:
            key = _same_file(path)
            if key in wanted:
                used.add(key)
    return used


def _rows_at(db, path) -> list[tuple[int, str]]:
    """The clip rows that point at this file, however each path is written:
    (id, the path as the row has it)."""
    if not path:
        return []
    key, name = _same_file(path), os.path.normcase(os.path.basename(str(path)))
    return [(int(r[0]), str(r[1])) for r in db.conn.execute(
        "SELECT id, path FROM clips WHERE path IS NOT NULL AND path != ''").fetchall()
        if os.path.normcase(os.path.basename(str(r[1]))) == name and _same_file(r[1]) == key]


def file_in_use(db, path, but: object | None = None) -> bool:
    """Whether a clip row points at this file, however each path is written,
    or it is held (core/in_use.py: a Make clips again run holds the renders
    no row points at yet, and the files of the clips from before, until it
    is over). Two clips can share one: a run names a clip's file by its whole
    seconds (core/pipeline.py), so a window with the same whole-second start
    and end as an older clip's was written over that clip's file. Deleting
    one of them (DELETE /clips/{clip_id}, remove_clips) leaves the file the
    other still plays, and the file a run will give a clip. `but` leaves out
    the files that holder holds."""
    if not path:
        return False
    return bool(_files_in_use(db, [path])) or _same_file(path) in in_use.files_held(but)


def remove_clips(db, clip_ids: Iterable[int], data_dir: Path) -> int:
    """Delete clips as DELETE /clips/{clip_id} (server/api.py) does: each row
    with the rows that hang off it (StateDB.delete_clip), its editor preview,
    and its file when that is inside the data folder, no other clip points
    to it and nothing holds it (file_in_use). A file Windows holds open stays
    too, and Storage's clean-up reclaims it later. Returns the bytes freed."""
    rows: dict[int, str | None] = {}
    for clip_id in clip_ids:
        rows.setdefault(int(clip_id), db.delete_clip(int(clip_id)))
    return _clear_files(db, rows, data_dir)


def _clear_files(db, rows: dict[int, str | None], data_dir: Path, but: object | None = None) -> int:
    """What is left of clips whose rows are gone ({id: the path the row
    had}): each one's editor preview, and its file when no clip left points
    to it, nothing but `but` holds it (core/in_use.py) and it is inside the
    data folder (remove_clips). Returns the bytes freed."""
    data_dir = Path(data_dir)
    files = {}
    for clip_id, path in rows.items():
        if path:
            files.setdefault(_same_file(path), Path(path))
        discard(data_dir / "previews" / f"clip_{int(clip_id)}.mp4")
    if not files:
        return 0
    left = _files_in_use(db, files.values()) | (set(files) & in_use.files_held(but))
    freed = 0
    for key, path in files.items():
        if key in left or not path.is_file() or not within(data_dir, path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if discard(path):
            freed += size
    return freed


# ---- the files of the clips from before, while the run works --------------------


def _aside_name(own: Path, clip_id: int) -> Path:
    """Where Run.set_aside puts clip `clip_id`'s file `own` while the run
    works: beside it, under a name no run writes."""
    return own.with_name(f"{own.stem}{_ASIDE}{int(clip_id)}{own.suffix}")


def _first_name(path: Path, clip_id: int) -> Path | None:
    """The name a file put aside for clip `clip_id` had (_aside_name), or
    None when `path` isn't one."""
    mark = f"{_ASIDE}{int(clip_id)}"
    if not path.stem.endswith(mark) or path.stem == mark:
        return None
    return path.with_name(path.stem[: -len(mark)] + path.suffix)


def _rename(src: Path, dst: Path) -> None:
    """os.replace: raises PermissionError on Windows while something holds
    `src` or `dst` open without letting it be deleted (a player does)."""
    os.replace(src, dst)


def _copy(src: Path, dst: Path) -> None:
    """A copy of `src` at `dst`, or nothing at `dst` and the error."""
    try:
        shutil.copy2(src, dst)
    except OSError:
        discard(dst)
        raise


def _put_back(aside: Path, own: Path) -> bool:
    """A file put aside goes back under its first name, over whatever the run
    left there. False when it can't (held open), or there is no file there
    to put back; nothing else is deleted (Run._settle_one decides about a
    file under the first name)."""
    if not aside.is_file():
        return False
    try:
        _rename(aside, own)
        return True
    except OSError:
        return False


def _there(path) -> bool:
    """Whether a file the run made is still on disk (finish() gives a clip
    only a file that is)."""
    return bool(path) and Path(path).is_file()


def _kept_copy(downloads: Path, video_id: str) -> Path | None:
    """The second name a run of the video's own copy gave it
    (Run._keep_source), when one is there."""
    if not downloads.is_dir():
        return None
    for path in downloads.iterdir():
        if path.is_file() and path.stem == f"{video_id}{_KEPT}":
            return path
    return None


# ---- after the app was closed in the middle of a run ------------------------------


def _entry_json(entry: dict) -> dict:
    """A file of the clips from before (Run.aside), as the journal keeps it."""
    return {"own": str(entry["own"]), "aside": str(entry["aside"]), "ids": [int(i) for i in entry["ids"]],
            "was": {str(i): str(p) for i, p in entry["was"].items()}, "state": entry["state"]}


def _entry_from_json(raw) -> dict | None:
    """A journal's file of the clips from before, or None when it can't be
    read (then nothing of it is touched)."""
    try:
        return {"own": Path(raw["own"]), "aside": Path(raw["aside"]), "ids": [int(i) for i in raw["ids"]],
                "was": {int(i): str(p) for i, p in (raw.get("was") or {}).items()},
                "state": str(raw.get("state") or ""), "copied": False}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def recover(db, data_dir: Path, video_id: str | None = None, busy: Callable[[], Iterable[int]] | None = None) -> int:
    """Finish what a Make clips again run left when the app was closed in the
    middle of it (taskkill as the app closes, a power cut, Windows shutting
    down): its journal (JOURNAL), settled as stopped() settles a run. Every
    clip from before then plays its own file, under its first name or, when
    a clip of that run took the first name, its put-aside one; the video's
    own copy is back under its name. What finish() had already changed
    (clips deleted, or made again in place) stays changed, and the clips the
    run made stay. The worker calls it as the engine starts, before it takes
    a job (server/jobs.py), and a Make clips again run for its own video
    before it starts. `video_id` limits it to that video's journal; `busy`
    is the clips the editor lane is exporting. Never raises. Returns how
    many journals it finished."""
    done = 0
    try:
        if video_id is not None:
            keys = [JOURNAL + video_id]
        else:
            keys = [k for (k,) in db.conn.execute(
                "SELECT key FROM app_state WHERE key GLOB ?", (JOURNAL + "*",)).fetchall()]
        for key in keys:
            row = db.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
            if row is None:
                continue
            vid = key[len(JOURNAL):]
            journal = _payload(row[0])
            if journal is None:
                # Nothing in it can be trusted: no file is touched. The next
                # Make clips again of the video finds each one where it is.
                print(f"      (Make clips again: the record of an interrupted run of {vid} couldn't be read; "
                      "its clips' files stay where they are)")
                db.conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
                db.conn.commit()
                continue
            Run._left(db, vid, Path(data_dir), journal, busy)._settle()
            done += 1
            print(f"      Make clips again: Clips Kitty was closed while {vid}'s clips were being made again. "
                  "Every clip from before has its own video back")
    except Exception as e:
        try:
            print(f"      (Make clips again: an interrupted run couldn't be finished yet: {e})")
        except Exception:
            pass
    return done


# ---- around the run ----------------------------------------------------------------


def _is_short(render_opts) -> bool:
    """A 9:16 clip: not a Longform output, not a story reel."""
    try:
        opts = json.loads(render_opts or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(opts, dict) and not opts.get("profile") and not opts.get("reel")


class _Noting:
    """The worker's database as a Make clips again run sees it: the same in
    every way, except that

    - each 9:16 clip the run registers is noted by its window (add_clip).
      core/pipeline.py _register_clip calls add_clip for every clip a run
      makes, a window made again as well as a new one (the UNIQUE window
      turns the insert into an update there). For a window made again, what
      the run made it with is noted too (_FROM_RUN), for finish();
    - no row from before the run is changed while it works (set_clip): what
      the run would write on one (the new file and scores of a moment found
      again, a story reel joined again) is held back, and only finish() gives
      it to a clip it replaces. The file it would point at is held
      (core/in_use.py) until the run is over: no row points at it;
    - how many 9:16 clips the run sets out to make is noted (set_outcome:
      core/pipeline.py records it before it renders), so finish() knows
      when a render failed;
    - a Cancel pressed since the job started that the run's own fresh start
      cleared (core/pipeline.py, longform/process.py) is asked for again
      whenever the run reads or writes, so it stops at its next check."""

    def __init__(self, db, run: Run):
        self._db, self._run = db, run

    def __getattr__(self, name):
        if name.startswith("__") or name in ("_db", "_run"):
            raise AttributeError(name)
        self._run._keep_cancel()
        return getattr(self._db, name)

    def add_clip(self, video_id, start, end, *args, **kwargs):
        self._run._keep_cancel()
        clip_id = self._db.add_clip(video_id, start, end, *args, **kwargs)
        given = {**dict(zip(_ADD_CLIP, args)), **kwargs}
        if video_id == self._run.video_id:
            self._run._wrote_file(given.get("path"))
        if video_id == self._run.video_id and _is_short(given.get("render_opts", "")):
            window = _window(start, end)
            self._run.made.add(window)
            self._run.registered += 1
            if clip_id is None:
                self._run.again[window] = {k: given[k] for k in (*_FROM_RUN, "path") if k in given}
                in_use.hold_files(self._run, [given.get("path")])
            else:
                self._run.added.append(int(clip_id))
        return clip_id

    def set_clip(self, clip_id, **fields):
        self._run._keep_cancel()
        if int(clip_id) in self._run.old:
            self._run.held_back.setdefault(int(clip_id), {}).update(fields)
            in_use.hold_files(self._run, [fields.get("path")])
            self._run._wrote_file(fields.get("path"))
            return
        self._db.set_clip(clip_id, **fields)

    def set_outcome(self, video_id, outcome):
        self._run._keep_cancel()
        self._db.set_outcome(video_id, outcome)
        # Its `clips` is how many it is about to render (core/outcome.py
        # summarise_run). Longform's own record of a match isn't this run's.
        made = outcome.get("clips") if isinstance(outcome, dict) and not outcome.get("longform_only") else None
        if video_id == self._run.video_id and isinstance(made, int) and not isinstance(made, bool):
            self._run.to_make = made


class _Cancelled(Exception):
    """A Cancel finish() found before it changed any clip from before."""


class Run:
    """One Make clips again job, around its run (server/jobs.py).

    Built as the job starts, so `old` is the video's clips that existed then,
    and their files are put out of the run's way then (set_aside). The run is
    given `db`; finish() is called only once it has finished, and stopped()
    when it failed or was cancelled. `cancels` is how many times the video
    had been cancelled before the job started (core/cancel.py
    times_requested, read before the worker's own fresh start): any Cancel
    since counts (cancelled()).

    `wait`: the worker's job first waits up to that many seconds for the
    video's clips to be free (busy_clips), and ends with nothing changed if
    they aren't (BUSY_AT_START). Without it (None) nothing is waited for, and
    a file something may be reading is copied aside instead of moved. `url`
    is what the job runs (a video's own copy, `local:<id>`, is kept safe:
    _keep_source).

    A run of the video the app was closed in the middle of is finished
    first (recover()), so this run starts from every clip on its own file."""

    def __init__(self, db, video_id: str, data_dir: Path, busy: Callable[[], Iterable[int]] | None = None,
                 cancels: int | None = None, url: str = "", wait: float | None = None):
        recover(db, data_dir, video_id, busy)
        self._begin(db, video_id, data_dir, busy, cancels)
        try:
            self.set_aside(wait)
            self._keep_source(url)
        except BaseException as e:
            self._settle()        # what was put aside goes back
            if isinstance(e, cancel.CancelledError):
                self._say_stopped("was cancelled")
            raise

    def _begin(self, db, video_id: str, data_dir: Path, busy: Callable[[], Iterable[int]] | None,
               cancels: int | None) -> None:
        self.video_id = video_id
        self.data_dir = Path(data_dir)
        self._real = db
        self._busy = busy or (lambda: ())
        self._cancels = cancel.times_requested(video_id) if cancels is None else int(cancels)
        self.old = {int(r[0]) for r in db.conn.execute(
            "SELECT id FROM clips WHERE video_id = ?", (video_id,)).fetchall()}
        self.made: set[tuple[float, float]] = set()
        self.again: dict[tuple[float, float], dict] = {}
        self.held_back: dict[int, dict] = {}   # what the run would have written on rows from before
        self.aside: list[dict] = []            # the files set_aside() put out of the run's way, still to settle
        self.plan: list[dict] = []             # all of them, as the journal (JOURNAL) has them
        self.wrote: set[str] = set()           # the files the run handed a clip (_wrote_file)
        self.added: list[int] = []     # the 9:16 clips the run added: windows the video had no clip at
        self.registered = 0            # the 9:16 clips the run made, windows found again included
        self.to_make: int | None = None   # how many it set out to make (_Noting.set_outcome)
        self.source_kept: tuple[Path, Path] | None = None   # the video's own copy, and its second name
        self._source_note: dict | None = None   # and both, as the journal has them
        self.removed: list[int] = []   # the old clips finish() has deleted so far
        self.remade: list[int] = []    # and the ones it has made again in place
        self.finished = False
        self.db = _Noting(db, self)

    @classmethod
    def _left(cls, db, video_id: str, data_dir: Path, journal: dict,
              busy: Callable[[], Iterable[int]] | None = None) -> Run:
        """The run a journal describes, which the app was closed in the
        middle of, ready to be settled as stopped() settles one (recover()).
        It knows of no file it wrote, so it deletes none under a first name."""
        run = cls.__new__(cls)
        run._begin(db, video_id, data_dir, busy, 0)
        entries = journal.get("entries") if isinstance(journal.get("entries"), list) else []
        run.plan = [e for e in map(_entry_from_json, entries) if e is not None]
        run.aside = list(run.plan)
        source = journal.get("source")
        if isinstance(source, dict) and source.get("copy") and source.get("second"):
            run.source_kept = (Path(source["copy"]), Path(source["second"]))
            run._source_note = {"copy": str(source["copy"]), "second": str(source["second"])}
        run.finished = True
        return run

    def _wrote_file(self, path) -> None:
        """A file the run handed one of its clips: under a first name, one
        settling may delete when no clip plays it (_settle_one)."""
        if path:
            self.wrote.add(_same_file(path))

    # ---- a Cancel --------------------------------------------------------------

    def cancelled(self) -> bool:
        """Whether this video was cancelled after the job started: its flag,
        or a Cancel counted since, which survives the flag being cleared."""
        return (cancel.is_cancelled(self.video_id)
                or cancel.times_requested(self.video_id) != self._cancels)

    def check_cancelled(self) -> None:
        """Raise CancelledError when the video was cancelled after the job
        started (cancelled())."""
        if self.cancelled():
            raise cancel.CancelledError(self.video_id)

    def _keep_cancel(self) -> None:
        """A Cancel pressed since the job started, whose flag something
        cleared, is asked for again: the run stops at its next check."""
        vid = self.video_id
        if (not self.finished and cancel.times_requested(vid) != self._cancels
                and not cancel.is_cancelled(vid)):
            cancel.request_cancel(vid)

    # ---- the files of the clips from before ----------------------------------

    def _reading(self, held: Iterable[int]) -> set[int]:
        """The clips something may be reading the file of now (module
        _reading): `held` by a request, on the editor lane, or published."""
        return _reading(self._real, self.video_id, held, self._busy())

    def set_aside(self, wait: float | None = None) -> None:
        """Put each old clip's file out of the run's way, as the job starts.
        A run writes a clip's file under a name made from its whole seconds,
        and a story reel's under what it is (core/pipeline.py), so a moment
        found again, or a new one with the same whole seconds, would be
        written over a clip from before. Each file is renamed beside itself
        (clip_00010-00070.clip-17.mp4) and its rows point there, so they play
        their own file throughout. One that Windows won't rename (a player
        holds it open) is copied there instead, and its first name left
        alone. A file outside the data folder stays: no run writes there. One
        already aside (a run the app closed halfway) stays where it is. Each
        file, under both names, is held (core/in_use.py) until the run is
        over. Raises when a file can't be put aside; then the job fails
        before its run starts.

        With `wait` (the worker's job), none of the old clips may be in use
        (busy_clips) as their files move: all of them move inside one
        in_use.settled(), where no request can start holding one, and only
        once none is held, on the editor lane or being published. Until
        then the job waits, up to `wait` seconds, and a Cancel stops it;
        after that it raises BUSY_AT_START with nothing changed. So nothing
        can be reading a file the run may write over under its first name.
        Without `wait`, a file something may be reading (_reading) is copied
        aside instead, and its first name left alone.

        Before the first file moves, the plan for all of them is saved
        (_plan_aside: JOURNAL), and each file's step after it is saved in
        the commit that points its rows aside, so recover() can finish it
        whatever moment the app is closed at."""
        files: dict[str, tuple[Path, dict[int, str]]] = {}
        for row in self._real.conn.execute(
                "SELECT id, path FROM clips WHERE video_id = ? ORDER BY id", (self.video_id,)).fetchall():
            if int(row["id"]) in self.old and row["path"]:
                own = Path(row["path"])
                files.setdefault(_same_file(own), (own, {}))[1][int(row["id"])] = row["path"]
        until = None if wait is None else time.monotonic() + max(0.0, float(wait))
        said = False
        conn = self._real.conn
        while True:
            if conn.in_transaction:
                conn.commit()
            with in_use.settled() as held:
                if until is None or not (self.old & self._reading(held)):
                    self._plan_aside(files.values())
                    for entry in list(self.plan):
                        self._set_aside_one(entry)
                    return
            if time.monotonic() >= until:
                raise RuntimeError(BUSY_AT_START)
            if not said:
                said = True
                print("      Make clips again: waiting for this video's clips to finish being published, "
                      "exported or edited")
            self.check_cancelled()
            time.sleep(_LOOK_AGAIN)

    def _plan_aside(self, files: Iterable[tuple[Path, dict[int, str]]]) -> None:
        """What happens to each file of the old clips (`files`: the file
        their rows say, and {id: that path as each row has it}, more than
        one when they share it), saved as the run's journal before any of
        them moves, in one commit with the rows _planned() points back at a
        file's first name."""
        conn = self._real.conn
        repairs: list[tuple[str, int, str]] = []
        for own, was in files:
            entry = self._planned(own, was, repairs)
            if entry is not None:
                self.plan.append(entry)
                self.aside.append(entry)
        if conn.in_transaction:
            conn.commit()
        try:
            conn.executemany("UPDATE clips SET path = ? WHERE id = ? AND path = ?", repairs)
            self._write_journal()
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _planned(self, own: Path, was: dict[int, str], repairs: list) -> dict | None:
        """One file's plan: {"own": its first name, "aside": the name it is
        put aside under, "ids", "was": {id: the path each row goes back to},
        "state", "copied"}, or None for a file outside the data folder (no
        run writes there). `state` is "planned" until its rows point aside,
        "aside" while they do, and "back" once settling has pointed them back
        at its first name and the file follows (_settle_one).

        A file already under a clip's put-aside name (a run the app closed
        halfway, or a kept clip whose first name a clip of an earlier run
        took) stays there ("aside"), unless it isn't there any more and its
        first name is, which no other clip plays: the app was closed as it
        was put back. Then the file under its first name is the clip's, and
        its rows are pointed back there (`repairs`, saved with the plan) for
        it to be put aside as any file is."""
        ids = sorted(was)
        if not within(self.data_dir, own):
            return None
        for clip_id in ids:
            first = _first_name(own, clip_id)
            if first is None:
                continue
            others = {i for i, _ in _rows_at(self._real, first)} - set(ids)
            if own.is_file() or not first.is_file() or others:
                return {"own": first, "aside": own, "ids": ids, "was": {}, "state": "aside", "copied": False}
            repairs += [(str(first), i, path) for i, path in was.items()]
            own, was = first, {i: str(first) for i in ids}
            break
        return {"own": own, "aside": _aside_name(own, min(ids)), "ids": ids, "was": dict(was),
                "state": "planned", "copied": False}

    def _set_aside_one(self, entry: dict) -> None:
        """One file of the old clips: renamed to its put-aside name (or
        copied there, when something may be reading it or Windows won't
        rename it), then its rows pointed there, in one commit with its step
        in the journal. A file already aside only gets held."""
        own, aside, ids = entry["own"], entry["aside"], entry["ids"]
        # Held from now until the run is over, under both names: no row
        # points at the first name while the file is aside.
        in_use.hold_files(self, [own, aside])
        if entry["state"] != "planned":
            return
        conn = self._real.conn
        if conn.in_transaction:
            conn.commit()
        # Inside settled(): no request can start reading one of these clips
        # until its row says where its file is now.
        with in_use.settled() as held:
            copied = False
            if own.is_file():
                copied = bool(set(ids) & self._reading(held))
                if not copied:
                    try:
                        _rename(own, aside)
                    except OSError:           # held open: Windows won't rename it
                        copied = True
                if copied:
                    try:
                        _copy(own, aside)
                    except OSError as e:
                        raise RuntimeError(f"A clip from before couldn't be kept safe while its video is "
                                           f"made again ({e}), so nothing was made again") from e
            # A file that is missing has its rows pointed aside all the same:
            # the run's render of their moment mustn't become theirs.
            entry["copied"] = copied
            try:
                conn.executemany("UPDATE clips SET path = ? WHERE id = ?", [(str(aside), i) for i in ids])
                entry["state"] = "aside"
                self._write_journal()
                conn.commit()
            except BaseException:
                entry["state"] = "planned"
                if conn.in_transaction:
                    conn.rollback()       # every row still says its first name: _settle puts the file back
                raise

    # ---- the journal -----------------------------------------------------------------

    def _write_journal(self) -> None:
        """The run's plan for the files of the clips from before and the
        video's own copy, as it stands, into the transaction open on the
        worker's connection: the caller commits it with the row changes it
        goes with, so it always says where each row points."""
        self._real.conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (JOURNAL + self.video_id, json.dumps({
                "video_id": self.video_id, "entries": [_entry_json(e) for e in self.plan],
                "source": self._source_note}, ensure_ascii=False)))

    def _clear_journal(self) -> None:
        conn = self._real.conn
        if conn.in_transaction:
            conn.commit()
        conn.execute("DELETE FROM app_state WHERE key = ?", (JOURNAL + self.video_id,))
        conn.commit()

    # ---- once the run is over ----------------------------------------------------------

    def _settle(self) -> None:
        """Where each file put aside ends up, once the run is over (finish()
        or stopped()), and the video's own copy back under its name if it
        went (_restore_source). Then the run's journal goes, unless a file
        couldn't be settled (recover() tries it again at the next start),
        and the run holds no file any more. Never raises."""
        entries, self.aside = self.aside, []
        whole = True
        for entry in entries:
            try:
                self._settle_one(entry)
            except Exception as e:
                whole = False
                try:
                    print(f"      (Make clips again: {entry['aside'].name} stays where it is: {e})")
                except Exception:
                    pass
        try:
            self._restore_source()
        except Exception as e:
            whole = False
            try:
                print(f"      (Make clips again: this video's own copy stays where it is: {e})")
            except Exception:
                pass
        if whole:
            try:
                self._clear_journal()
            except Exception:
                pass
        in_use.let_go_of_files(self)

    def _settle_one(self, entry: dict) -> None:
        """A clip still on its file put aside (one that is kept, or every one
        after a run that stopped) gets it back under its first name, over
        whatever the run left there: unless a clip of this run took that name
        (the same whole seconds) or another run holds it, something may be
        reading the clip, or the file is held open. Then it stays aside. Its
        rows are pointed back first, in one commit with that step in the
        journal ("back"), and the file follows them (_follow_rows): if the
        app is closed in between, recover() moves it at the next start. A
        file aside that no clip plays any more (its clips were deleted, or
        took the run's new file) goes, and so does a file this run wrote
        under the first name that no clip plays: unless something may be
        reading one of the entry's clips (a request holding it, the lane, a
        publish). Nothing else under a first name is deleted: a file the run
        didn't write there may be a clip's own (an app closed halfway)."""
        db = self._real
        conn = db.conn
        own, aside = entry["own"], entry["aside"]
        if conn.in_transaction:
            conn.commit()
        with in_use.settled() as held:
            if entry["state"] != "aside":
                # Its rows say its first name: the job stopped before they
                # were pointed aside, or settling pointed them back.
                self._follow_rows(entry)
                return
            rows = _rows_at(db, aside)
            busy = bool(set(entry["ids"]) & self._reading(held))
            # A row points at the first name now (a clip of this run, or one
            # made again in place), or another holder holds it: not this
            # run's own holds, which end here.
            taken = file_in_use(db, own, but=self)
            if rows and not taken and not busy:
                if not aside.is_file() and within(self.data_dir, own) and self._wrote(own):
                    # Its file was missing before the run, and stays so: the
                    # run's render of its moment isn't its file.
                    discard(own)
                # Each row as it was written (or the first name of a file an
                # earlier run left aside).
                try:
                    conn.executemany("UPDATE clips SET path = ? WHERE id = ? AND path = ?",
                                     [(entry["was"].get(i, str(own)), i, path) for i, path in rows])
                    entry["state"] = "back"
                    self._write_journal()
                    conn.commit()
                except BaseException:
                    entry["state"] = "aside"
                    if conn.in_transaction:
                        conn.rollback()
                    raise
                self._follow_rows(entry)
                return
            if busy:
                return            # nothing deleted that something may be reading
            if not rows and within(self.data_dir, aside):
                discard(aside)
            if not taken and within(self.data_dir, own) and self._wrote(own):
                discard(own)

    def _follow_rows(self, entry: dict) -> None:
        """A file whose rows say its first name, still under its put-aside
        one, goes there: over what the run left there once settling pointed
        the rows back ("back"), or when nothing is there (the job stopped
        between moving the file and pointing its rows aside). Under
        "planned", with a file under its first name, what is aside is the
        copy the job made, or one from before that no clip plays: it goes.
        When the file can't move (held open), its rows point at it where it
        is. A file a clip plays under its put-aside name stays put."""
        db = self._real
        conn = db.conn
        own, aside, state = entry["own"], entry["aside"], entry["state"]
        if not aside.is_file() or _rows_at(db, aside):
            return
        if own.exists() and state != "back":
            if state == "planned" and within(self.data_dir, aside):
                discard(aside)
            return
        if _put_back(aside, own):
            return
        # Held open: the rows go to the file, where it is.
        mine = [(i, path) for i, path in _rows_at(db, own) if i in entry["ids"]]
        if conn.in_transaction:
            conn.commit()
        try:
            conn.executemany("UPDATE clips SET path = ? WHERE id = ? AND path = ?",
                             [(str(aside), i, path) for i, path in mine])
            entry["state"] = "aside"
            self._write_journal()
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _wrote(self, path) -> bool:
        """Whether the run handed one of its clips this file (_wrote_file)."""
        return bool(path) and _same_file(path) in self.wrote

    def _keep_source(self, url: str) -> None:
        """A run of the video's own copy (`local:<id>`: a file from this PC,
        or a linked video whose link is lost) could lose it: the run's first
        step deletes a copy FFprobe can't read within a minute
        (core/pipeline.py _cached_or_download), and nothing can bring that
        copy back. So while the run works, the copy has a second name beside
        it (<id>.remaking.mp4, a hard link: no more space on the drive),
        held so that nothing deletes it, and the copy goes back under its own
        name once the run is over if it went (_restore_source). On a drive
        with no hard links the copy is read first, as that step reads it,
        and the job stops before it, the copy left as it is, if it can't be
        read (UNREADABLE_COPY). A second name a run the app closed halfway
        left is put back, or dropped when the copy is there. Both names are
        saved in the run's journal before the second one is made, so
        recover() puts the copy back if the app is closed from then on."""
        if not str(url or "").startswith("local:"):
            return
        downloads = self.data_dir / "downloads"
        copy = cached_source(downloads, self.video_id)
        left = _kept_copy(downloads, self.video_id)
        if left is not None:
            if copy is None:
                copy = left.with_name(f"{self.video_id}{left.suffix}")
                _rename(left, copy)
            else:
                discard(left)
        if copy is None:
            return            # nothing to make clips from: the run says so
        second = copy.with_name(f"{copy.stem}{_KEPT}{copy.suffix}")
        self._source_note = {"copy": str(copy), "second": str(second)}
        conn = self._real.conn
        if conn.in_transaction:
            conn.commit()
        try:
            self._write_journal()
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        try:
            os.link(copy, second)
        except OSError:
            from video.encoding import readable_video

            if not readable_video(copy):
                raise RuntimeError(UNREADABLE_COPY) from None
            return
        self.source_kept = (copy, second)
        in_use.hold_files(self, [second])

    def _restore_source(self) -> None:
        """The video's own copy, kept under a second name while the run
        worked (_keep_source): back under its own name if the run deleted it
        (the video is still in the library and has no copy), or the second
        name goes."""
        if self.source_kept is None:
            return
        copy, second = self.source_kept
        self.source_kept = None
        if not second.is_file():
            return
        known = self._real.conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (self.video_id,)).fetchone()
        if known is not None and cached_source(copy.parent, self.video_id) is None:
            _rename(second, copy)
            print("      Make clips again: this video's own copy was put back, as the run had deleted it")
        else:
            discard(second)

    # ---- the end of the run ------------------------------------------------------

    def _say_stopped(self, how: str) -> None:
        try:
            print(f"      Make clips again: this run {how}, so nothing was replaced and the "
                  f"{len(self.old)} clip(s) from before are all kept")
        except Exception:
            pass

    def stopped(self, how: str) -> None:
        """The run failed or was cancelled (`how`: "failed", "was
        cancelled"), so nothing is replaced: every clip from before keeps its
        own file and everything it had (what the run would have written on
        one is dropped), and the job log says so. Nothing at all once
        finish() has run (a job that fails after its run, in what it was
        asked to do next). Never raises."""
        if self.finished:
            return
        self.finished = True
        self._settle()
        self._say_stopped(how)

    def renders_failed(self) -> int:
        """How many of the 9:16 clips the run set out to make it didn't:
        core/pipeline.py records how many it is about to render (its
        outcome's `clips`, _Noting.set_outcome) and registers each one it
        made (add_clip), while a render that failed registers nothing. 0
        when the run never said."""
        if self.to_make is None:
            return 0
        return max(0, int(self.to_make) - self.registered)

    def finish(self) -> dict:
        """Delete the old 9:16 clips nobody used that this run didn't make
        again, give the unused ones it made again in place what it made them
        with, and say in the job log what was replaced and what was kept, and
        why. Each clip is judged again in the moment it would change
        (_change). Every other clip from before keeps its own file. Nothing
        changes when a render failed (renders_failed).

        Raises CancelledError, with nothing changed, for a Cancel since the
        job started that comes before the first clip from before changes:
        the job ends cancelled (server/jobs.py). Otherwise it never raises:
        the clips this run made are made either way."""
        self.finished = True
        try:
            try:
                return self._finish()
            finally:
                self._settle()
        except _Cancelled:
            self._say_stopped("was cancelled")
            raise cancel.CancelledError(self.video_id) from None
        except Exception as e:
            if self.removed or self.remade:
                done = ("were removed" if not self.remade else
                        f"were replaced ({len(self.removed)} removed, {len(self.remade)} made again)")
                print(f"      Make clips again: {len(self.removed) + len(self.remade)} clip(s) from before "
                      f"that nobody had used {done}, then the rest couldn't be ({e}), so they're kept")
            else:
                print(f"      Make clips again: the clips from before couldn't be replaced ({e}), "
                      "so they're all kept")
            return {**self._made(), "again": len(self.remade), "removed": list(self.removed),
                    "replaced": len(self.removed) + len(self.remade), "error": str(e)}

    def _old_judged(self) -> list[dict]:
        """The clips from before the run, judged now (classify)."""
        return [c for c in classify(self._real, self.video_id, self._busy(), self.data_dir)
                if c["id"] in self.old]

    def _from_run(self, clip_id: int, window: tuple[float, float]) -> dict:
        """What an old clip made again in place takes: its new file and
        scores (what core/pipeline.py _register_clip wrote, held back
        meanwhile) and what the run made it with (_FROM_RUN)."""
        fields = dict(self.held_back.get(clip_id, {}))
        run = self.again.get(window, {})
        fields.update({k: run[k] for k in _FROM_RUN if k in run})
        if not fields.get("path") and run.get("path"):
            fields["path"] = run["path"]
        return fields

    def _stop_if_cancelled(self) -> None:
        """A Cancel since the job started, while no clip from before has
        changed: the job ends cancelled, with nothing replaced."""
        if not (self.removed or self.remade) and self.cancelled():
            raise _Cancelled()

    def _change(self, plan: list[tuple[str, dict]]) -> dict:
        """Each old clip in `plan` changes as planned ("again": made again in
        place; "remove": deleted), judged again in the moment it changes:
        inside a write transaction (BEGIN IMMEDIATE), so nothing can record a
        use of it in between, and inside in_use.settled(), so no request can
        start working on it until the change is committed. A request that
        starts then finds it gone, or as it is now. Before the first change,
        a Cancel since the job started stops it all (_Cancelled).

        One a person or a feature used after all is kept as it is ("spared",
        with its reasons now); one deleted meanwhile is left; one to make
        again whose new file is gone (deleted by hand, say) keeps its own
        ("gone"). Each one deleted joins self.removed and each one made again
        self.remade as it goes. Returns {"again", "older", "spared", "gone",
        "vanished", "freed"}: "older" is the ones made again that count as
        kept, made before this update (the dialog counted them kept: their
        title, description and hashtags stay); "vanished" the ones deleted
        meanwhile."""
        db = self._real
        conn = db.conn
        rows: dict[int, str | None] = {}
        out: dict = {"again": [], "older": [], "spared": {}, "gone": [], "vanished": [], "freed": 0}
        try:
            for what, clip in plan:
                clip_id = int(clip["id"])
                if conn.in_transaction:
                    conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    with in_use.settled() as held:
                        self._stop_if_cancelled()
                        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
                        if row is None:      # deleted meanwhile, by a person
                            conn.rollback()
                            out["vanished"].append(clip_id)
                            continue
                        busy = {int(b) for b in self._busy()} | held
                        reasons = _reasons(row, _evidence(db, self.video_id, self.data_dir), busy)
                        if what == "again":
                            if not _in_place({"replace": not reasons, "reasons": reasons}):
                                conn.rollback()
                                out["spared"][clip_id] = reasons
                                continue
                            fields = self._from_run(clip_id, _window(row["start_s"], row["end_s"]))
                            if not _there(fields.get("path")):
                                conn.rollback()
                                out["gone"].append(clip_id)
                                continue
                            db.set_clip(clip_id, **fields)     # its commit ends the transaction
                            self.remade.append(clip_id)
                            out["again"].append(clip_id)
                            if reasons:                        # ["older"]: kept, and made again in place
                                out["older"].append(clip_id)
                        else:
                            if reasons:
                                conn.rollback()
                                out["spared"][clip_id] = reasons
                                continue
                            rows[clip_id] = db.delete_clip(clip_id)   # so does this one's
                            self.removed.append(clip_id)
                except BaseException:
                    if conn.in_transaction:
                        conn.rollback()
                    raise
        finally:
            out["freed"] = _clear_files(db, rows, self.data_dir, but=self)
        return out

    def _made(self) -> dict:
        """What the run made: its 9:16 clips, how many of them are new rows,
        and how many are at the same start and end as a clip from before
        (core/pipeline.py's own "Done (N clips)" counts only the new ones)."""
        return {"made": len(self.made), "new": len(self.added), "found_again": len(self.again)}

    def _finish(self) -> dict:
        judged = self._old_judged()
        if not self.made:
            self._stop_if_cancelled()
            print(f"      Make clips again: this run made no clips, so the {len(judged)} "
                  f"clip(s) from before are all kept as they were")
            return {**self._made(), "again": 0, "removed": [], "replaced": 0, "unchanged": len(judged),
                    **_kept(judged)}
        failed = self.renders_failed()
        if failed:
            self._stop_if_cancelled()
            print(f"      Make clips again: {failed} of the {self.to_make} clip(s) this run set out to make "
                  f"couldn't be made, so nothing was replaced: the {len(judged)} clip(s) from before are "
                  f"all kept, and the {len(self.added)} new clip(s) it made are added beside them")
            return {**self._made(), "again": 0, "removed": [], "replaced": 0, "renders_failed": failed,
                    "unchanged": len(judged), "kept": len(judged),
                    "kept_because": _kept(judged)["kept_because"]}
        plan = []
        for clip in judged:
            window = _window(clip["start_s"], clip["end_s"])
            if window in self.made and _in_place(clip):
                plan.append(("again", clip))
            elif window not in self.made and clip["replace"]:
                plan.append(("remove", clip))
        done = self._change(plan)
        self._stop_if_cancelled()         # nothing changed at all: a Cancel still counts
        removed, again, older = set(self.removed), set(done["again"]), set(done["older"])
        # Replaced, as the dialog counted it: the ones nobody used. A clip made
        # before this update that the run found again is made again in place,
        # but it is kept (its title, description and hashtags stay), as the
        # dialog said it would be.
        replaced = len(again - older) + len(removed)
        rest, unchanged = [], 0
        for clip in judged:
            if clip["id"] in removed or clip["id"] in again - older or clip["id"] in done["vanished"]:
                continue
            if clip["id"] in older:
                reasons = ["older"]
            else:
                unchanged += 1
                reasons = done["spared"].get(clip["id"]) or (
                    ["new_file_gone"] if clip["id"] in done["gone"] else clip["reasons"])
            rest.append({**clip, "reasons": reasons})
        kept = _kept(rest)
        made = self._made()
        line = (f"      Make clips again: this run made {made['made']} clip(s): {made['new']} new, "
                f"{made['found_again']} at the same start and end as a clip from before. Replaced {replaced} "
                f"clip(s) from before that nobody had used ({len(removed)} removed, "
                f"{len(again - older)} made again with the new settings).")
        if kept["kept"]:
            line += f" Kept {kept['kept']}: " + ", ".join(
                f"{n} {_SAID[reason]}" for reason, n in kept["kept_because"].items()) + "."
        print(line)
        if older:
            print(f"      Make clips again: {len(older)} of the kept clip(s), made before this update, "
                  "were found again at the same start and end, so their video was made again with the new "
                  "settings; their title, description and hashtags stay")
        if done["gone"]:
            print(f"      Make clips again: the new video of {len(done['gone'])} clip(s) was gone by the "
                  "end of the run, so they keep their own")
        return {**made, "again": len(again), "removed": list(self.removed), "replaced": replaced,
                "unchanged": unchanged, "bytes_freed": done["freed"], **kept}


def _kept(judged: list[dict]) -> dict:
    out = counts(judged, REASONS + _LOGGED)
    return {"kept": out["kept"], "kept_because": out["kept_because"]}


# What summary() keeps of Run.finish()'s answer: the counts.
_SUMMED = ("made", "new", "again", "replaced", "kept", "unchanged", "renders_failed")


def summary(out: dict) -> dict:
    """What a Make clips again job that finished did, in numbers, as its row
    keeps it (jobs.result) and its "done" event carries it, for the Queue
    page and the Library to say (lib/remake.ts remakeResultText). From
    Run.finish()'s answer:

    - made: the 9:16 clips the run made; new: the ones that are new clips;
    - again: clips from before made again in place with the new settings
      (one nobody used, or one made before this update that keeps its
      words); removed: clips from before deleted;
    - replaced and kept, as the dialog counted them (a clip made before this
      update that was made again counts as kept);
    - unchanged: clips from before left as they were (absent when the
      replacing stopped partway: `error` is then true);
    - renders_failed: clips the run set out to make and couldn't, when any
      (then nothing was replaced)."""
    out = out if isinstance(out, dict) else {}
    said = {k: out[k] for k in _SUMMED if isinstance(out.get(k), int) and not isinstance(out.get(k), bool)}
    said["removed"] = len(out.get("removed") or [])
    if out.get("error"):
        said["error"] = True
    return said
