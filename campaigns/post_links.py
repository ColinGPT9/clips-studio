"""Send published clip links to a chosen webhook using the durable queue."""

import json
import time

from campaigns import queue

PENDING_PREFIX = "discord_post_link:"


def validate_destination(db, destination_id: int | None) -> None:
    if destination_id is not None and db.get_destination(destination_id) is None:
        raise ValueError("That Discord destination doesn't exist.")


def campaign_for_destination(db, destination_id: int) -> int:
    validate_destination(db, destination_id)
    # Keep the original internal name so manual publishes and watched
    # channels share the same delivery identity, including after an upgrade.
    name = f"Watched channel links: Discord {destination_id}"
    with db.transaction():
        row = db.conn.execute(
            "SELECT id FROM campaigns WHERE name = ? AND destination_id = ? "
            "AND method = 'discord_webhook' ORDER BY id LIMIT 1",
            (name, destination_id),
        ).fetchone()
        return int(row["id"]) if row is not None else db.insert_campaign(
            name, method="discord_webhook", destination_id=destination_id,
            message_template="{title}\n{url}", approval_required=0,
        )


def prepare(db, *, destination_id: int | None, clip_id: int, platforms: list[str],
            provider: str, title: str = "", publish_job_id: int = 0) -> None:
    """Save delivery intent before uploading; only a live, public post sends.

    A request cut off after uploading still has its intent on disk. Missing,
    failed or refused posts never supply a live URL and cannot send a message.
    Repeated requests keep the original submission, so never send twice.
    """
    if destination_id is None:
        return
    validate_destination(db, destination_id)
    if provider != "youtube":
        # The next provider request id is not known until upload returns.
        # Keep intent separately so a previous publish's live URL cannot be
        # picked up while the new upload is still in progress.
        recover(db)
        clip = db.get_clip(clip_id)
        if clip is None:
            raise ValueError("That clip no longer exists.")
        previous = {row["platform"]: [row["request_id"], row["created_at"]]
                    for row in db.clip_publishes(clip_id)}
        intent = {"destination_id": destination_id, "clip_id": clip_id, "provider": provider,
                  "title": title, "video_id": clip["video_id"], "start_s": clip["start_s"],
                  "end_s": clip["end_s"], "before": {p: previous.get(p) for p in platforms}}
        db.set_flag(f"{PENDING_PREFIX}{destination_id}:{provider}:{clip_id}", json.dumps(intent))
        return
    _attach(db, destination_id=destination_id, clip_id=clip_id, platforms=platforms,
            provider=provider, title=title, publish_job_id=publish_job_id)


def _attach(db, *, destination_id: int, clip_id: int, platforms: list[str],
            provider: str, title: str, publish_job_id: int = 0) -> dict:
    with db.transaction():
        campaign_id = campaign_for_destination(db, destination_id)
        result = queue.attach(
            db, items=[{"clip_id": clip_id, "platforms": platforms}], campaign_ids=[campaign_id],
            provider=provider, publish_job_id=publish_job_id, now=time.time(),
        )
        for row in result["created"]:
            if title:
                db.set_submission(int(row["id"]), clip_title=title)
        return result


def send_existing(db, *, destination_id: int, clip_id: int, platform: str, provider: str) -> dict:
    """Queue a saved live post from the editor, without publishing it again."""
    from server import posts

    post = next((p for p in posts.posts_for_clip(db, clip_id)
                 if p["platform"] == platform and p["provider"] == provider and p["live"]), None)
    if post is None:
        raise ValueError("This post doesn't have a live public link yet.")
    with db.transaction():
        result = _attach(db, destination_id=destination_id, clip_id=int(post["clip_id"]),
                         platforms=[platform], provider=provider, title="")
        rows = result["created"] + result["existing"]
        if not rows:
            raise ValueError(result["refused"][0]["reason"] if result["refused"] else "Couldn't queue this link.")
        row = rows[0]
        if row["status"] in ("manual", "review", "failed", "expired", "cancelled"):
            raise ValueError(row["status_reason"] or "This delivery needs attention before it can be sent.")
        return {"submission_id": int(row["id"]), "status": row["status"],
                "already_sent": row["sent_by"] in ("webhook", "user"),
                "check_discord": row["status"] == "awaiting_confirmation" and not row["sent_by"]}


def recover(db, *, completed_clip: int = 0, provider: str = "") -> None:
    """Attach saved outcomes, including after a crash between publish and attach.

    A refresh changes neither request_id nor created_at, so refreshing an
    older post cannot fulfil the new publish's intent. Explicit completion
    also handles providers returning the same idempotent result again.
    """
    from server.woopsocial_service import ALIVE

    pending = db.conn.execute("SELECT key FROM app_state WHERE key GLOB ?",
                              (PENDING_PREFIX + "*",)).fetchall()
    for entry in pending:
        with db.transaction():
            # Re-read under the write lock: another worker may have settled it.
            saved = db.get_flag(entry["key"])
            if not saved:
                continue
            intent = json.loads(saved)
            if db.get_destination(intent["destination_id"]) is None:
                db.conn.execute("DELETE FROM app_state WHERE key = ?", (entry["key"],))
                continue
            finished = completed_clip == intent["clip_id"] and provider == intent["provider"]
            rows = db.conn.execute(
                "SELECT * FROM clip_publishes WHERE provider = ? AND (clip_id = ? OR "
                "(video_id = ? AND start_s = ? AND end_s = ?))",
                (intent["provider"], intent["clip_id"], intent["video_id"], intent["start_s"], intent["end_s"]),
            ).fetchall()
            for row in rows:
                platform = row["platform"]
                if platform not in intent["before"]:
                    continue
                if not finished and intent["before"][platform] == [row["request_id"], row["created_at"]]:
                    continue
                if row["state"] in ALIVE:
                    _attach(db, destination_id=intent["destination_id"], clip_id=int(row["clip_id"]),
                            platforms=[platform], provider=intent["provider"], title=intent["title"])
                elif row["state"] not in ("failed", "skipped"):
                    continue
                del intent["before"][platform]
            if intent["before"]:
                db.conn.execute("UPDATE app_state SET value = ? WHERE key = ?",
                                (json.dumps(intent), entry["key"]))
            else:
                db.conn.execute("DELETE FROM app_state WHERE key = ?", (entry["key"],))
