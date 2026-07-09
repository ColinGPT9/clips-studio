"""Creator identity: map (platform, channel) -> creator profile.

Every distinct platform account starts as its OWN creator profile — the same
person on Twitch and YouTube shows up as two profiles until the user links
them. Automatic matching only ever SUGGESTS (usernames differ across
platforms, get renamed, or gain numbers — and different people share names),
so linking/merging is always a user action.
"""

import json
import re

from core.state import StateDB, _now


def platform_of(video_id: str) -> str:
    """Platform from the video-id convention used across the app."""
    if video_id.startswith("tw_"):
        return "twitch"
    if video_id.startswith("kick_"):
        return "kick"
    return "youtube"


def normalize(name: str) -> str:
    """Comparison form of a username: lowercase, letters only.
    'StreamerName123' and 'streamer_name' both -> 'streamername'."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def resolve(
    db: StateDB, video_id: str, channel_name: str, platform: str | None = None
) -> int | None:
    """Creator id for this video's channel, creating account + profile on
    first sight. Returns None only when the channel is unknown/empty.
    `platform` overrides the video-id inference (uploaded local files carry
    the platform the user chose in the upload form)."""
    channel = (channel_name or "").strip()
    if not channel:
        return None
    platform = platform or platform_of(video_id)

    row = db.conn.execute(
        "SELECT creator_id FROM platform_accounts WHERE platform = ? AND platform_account_id = ?",
        (platform, channel),
    ).fetchone()
    if row:
        return row["creator_id"]

    cur = db.conn.execute(
        "INSERT INTO creators (display_name, aliases, created_at) VALUES (?, ?, ?)",
        (channel, json.dumps([channel]), _now()),
    )
    creator_id = cur.lastrowid
    db.conn.execute(
        "INSERT INTO platform_accounts (creator_id, platform, platform_account_id, username, display_name)"
        " VALUES (?, ?, ?, ?, ?)",
        (creator_id, platform, channel, channel, channel),
    )
    db.conn.commit()
    return creator_id


def tag_video(
    db: StateDB, video_id: str, channel_name: str, platform: str | None = None
) -> int | None:
    """Resolve and stamp creator_id onto the video row. A video that was
    already assigned a creator (e.g. by the upload form, where the user
    picked the platform themselves) keeps that assignment."""
    row = db.conn.execute(
        "SELECT creator_id FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    if row and row["creator_id"] is not None:
        return row["creator_id"]
    creator_id = resolve(db, video_id, channel_name, platform=platform)
    if creator_id is not None:
        db.conn.execute(
            "UPDATE videos SET creator_id = ? WHERE video_id = ?", (creator_id, video_id)
        )
        db.conn.commit()
    return creator_id


def backfill(db: StateDB) -> int:
    """One-time catch-up: give every already-processed video a creator.
    Safe to run at every startup — does nothing once tagged."""
    rows = db.conn.execute(
        "SELECT video_id, channel_name FROM videos WHERE creator_id IS NULL"
    ).fetchall()
    tagged = 0
    for r in rows:
        if tag_video(db, r["video_id"], r["channel_name"] or "") is not None:
            tagged += 1
    return tagged


def suggestions(db: StateDB) -> list[dict]:
    """Possible same-person profiles (similar normalized names on DIFFERENT
    platforms). Suggestions only — merging is the user's call."""
    accounts = db.conn.execute(
        "SELECT a.creator_id, a.platform, a.platform_account_id, c.display_name"
        " FROM platform_accounts a JOIN creators c ON c.creator_id = a.creator_id"
    ).fetchall()
    out, seen = [], set()
    for i, a in enumerate(accounts):
        for b in accounts[i + 1 :]:
            if a["creator_id"] == b["creator_id"]:
                continue
            na, nb = normalize(a["platform_account_id"]), normalize(b["platform_account_id"])
            if not na or not nb:
                continue
            # Same normalized name, or one contains the other ('streamername'
            # vs 'streamernamelive').
            if na == nb or na in nb or nb in na:
                key = tuple(sorted((a["creator_id"], b["creator_id"])))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "creator_a": {"id": a["creator_id"], "name": a["display_name"], "platform": a["platform"]},
                        "creator_b": {"id": b["creator_id"], "name": b["display_name"], "platform": b["platform"]},
                        "reason": f"similar usernames: {a['platform_account_id']!r} / {b['platform_account_id']!r}",
                    }
                )
    return out


def merge(db: StateDB, from_id: int, into_id: int) -> None:
    """Fold one creator profile into another: accounts, videos, knowledge,
    events, feedback and aliases all move; the source profile is removed."""
    if from_id == into_id:
        return
    row_from = db.conn.execute(
        "SELECT display_name, aliases FROM creators WHERE creator_id = ?", (from_id,)
    ).fetchone()
    row_into = db.conn.execute(
        "SELECT aliases FROM creators WHERE creator_id = ?", (into_id,)
    ).fetchone()
    if row_from is None or row_into is None:
        raise ValueError("unknown creator id")

    aliases = set(json.loads(row_into["aliases"] or "[]"))
    aliases.update(json.loads(row_from["aliases"] or "[]"))
    aliases.add(row_from["display_name"])

    for table in ("platform_accounts", "videos", "creator_knowledge", "creator_events", "clip_feedback"):
        db.conn.execute(
            f"UPDATE {table} SET creator_id = ? WHERE creator_id = ?", (into_id, from_id)
        )
    db.conn.execute(
        "UPDATE creators SET aliases = ? WHERE creator_id = ?",
        (json.dumps(sorted(aliases)), into_id),
    )
    db.conn.execute("DELETE FROM creators WHERE creator_id = ?", (from_id,))
    db.conn.commit()


def add_account(db: StateDB, creator_id: int, platform: str, channel: str) -> int:
    """Manually attach a channel to a creator profile — for channels the
    automatic matcher can't connect (completely different names, alt
    accounts). If the channel is already known under ANOTHER profile, it
    moves here along with its videos; if it's brand new, future videos from
    it will resolve straight to this creator. Returns the account_id."""
    channel = (channel or "").strip()
    platform = (platform or "").strip().lower()
    if platform not in ("youtube", "twitch", "kick"):
        raise ValueError(f"unknown platform {platform!r}")
    if not channel:
        raise ValueError("channel name is required")
    if db.conn.execute(
        "SELECT 1 FROM creators WHERE creator_id = ?", (creator_id,)
    ).fetchone() is None:
        raise ValueError("unknown creator id")

    existing = db.conn.execute(
        "SELECT account_id, creator_id FROM platform_accounts"
        " WHERE platform = ? AND platform_account_id = ?",
        (platform, channel),
    ).fetchone()
    if existing:
        old_creator = existing["creator_id"]
        if old_creator != creator_id:
            db.conn.execute(
                "UPDATE platform_accounts SET creator_id = ? WHERE account_id = ?",
                (creator_id, existing["account_id"]),
            )
            # The channel's videos follow it to the new profile.
            db.conn.execute(
                "UPDATE videos SET creator_id = ? WHERE creator_id = ? AND channel_name = ?",
                (creator_id, old_creator, channel),
            )
            # A profile left with no accounts and no videos is an empty shell.
            leftover = db.conn.execute(
                "SELECT (SELECT COUNT(*) FROM platform_accounts WHERE creator_id = ?)"
                " + (SELECT COUNT(*) FROM videos WHERE creator_id = ?) AS n",
                (old_creator, old_creator),
            ).fetchone()
            if leftover and leftover["n"] == 0:
                db.conn.execute("DELETE FROM creators WHERE creator_id = ?", (old_creator,))
            db.conn.commit()
        return existing["account_id"]

    cur = db.conn.execute(
        "INSERT INTO platform_accounts (creator_id, platform, platform_account_id, username, display_name)"
        " VALUES (?, ?, ?, ?, ?)",
        (creator_id, platform, channel, channel, channel),
    )
    # Add the channel name to the profile's aliases for future matching.
    row = db.conn.execute(
        "SELECT aliases FROM creators WHERE creator_id = ?", (creator_id,)
    ).fetchone()
    aliases = set(json.loads(row["aliases"] or "[]"))
    aliases.add(channel)
    db.conn.execute(
        "UPDATE creators SET aliases = ? WHERE creator_id = ?",
        (json.dumps(sorted(aliases)), creator_id),
    )
    db.conn.commit()
    return cur.lastrowid


def split_account(db: StateDB, account_id: int) -> int:
    """Detach one platform account into its own new creator profile (undo a
    merge, or separate a second channel with different content). The
    account's videos follow it; learned knowledge stays with the original."""
    acc = db.conn.execute(
        "SELECT * FROM platform_accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    if acc is None:
        raise ValueError("unknown account id")
    cur = db.conn.execute(
        "INSERT INTO creators (display_name, aliases, created_at) VALUES (?, ?, ?)",
        (acc["display_name"] or acc["username"], json.dumps([acc["username"]]), _now()),
    )
    new_id = cur.lastrowid
    db.conn.execute(
        "UPDATE platform_accounts SET creator_id = ? WHERE account_id = ?", (new_id, account_id)
    )
    db.conn.execute(
        "UPDATE videos SET creator_id = ? WHERE creator_id = ? AND video_id IN ("
        "  SELECT video_id FROM videos WHERE "
        "    (? = 'twitch' AND video_id LIKE 'tw_%') OR"
        "    (? = 'kick' AND video_id LIKE 'kick_%') OR"
        "    (? = 'youtube' AND video_id NOT LIKE 'tw_%' AND video_id NOT LIKE 'kick_%')"
        ") AND channel_name = ?",
        (new_id, acc["creator_id"], acc["platform"], acc["platform"], acc["platform"],
         acc["platform_account_id"]),
    )
    db.conn.commit()
    return new_id
