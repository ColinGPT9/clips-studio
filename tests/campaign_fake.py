"""Stand-ins for campaign tests: a publisher that writes its rows exactly as
the real ones do, and a webhook destination with its token stored.

FakePublisher goes through the providers' own writers: record_outcomes at
publish time and record_refresh for later states (WoopSocial and
Upload-Post, with publish.uploadpost's FanOutResult / PlatformOutcome), and
add_publish_job + finish_publish_job + record_publish for a direct YouTube
upload. Names and ids are made up.
"""

import json

from discord_fake import CHANNEL_ID, GUILDS, WEBHOOK_ID, WEBHOOK_TOKEN

from campaigns import service
from publish.uploadpost import FanOutResult, PlatformOutcome
from server import uploadpost_service, woopsocial_service

WRITERS = {"woopsocial": woopsocial_service, "upload_post": uploadpost_service}

LINKS = {
    "tiktok": "https://www.tiktok.com/@example/video/73000000000000000{n:02d}",
    "youtube": "https://www.youtube.com/shorts/abcdefghi{n:02d}",
    "instagram": "https://www.instagram.com/reel/Example{n:02d}",
}


def link(platform: str, n: int = 1) -> str:
    """A public post's link on `platform`, the n-th one."""
    return LINKS[platform].format(n=n)


def clip(db, *, video_id: str = "vid", start: float = 0.0, end: float = 30.0, title: str = "A great moment",
         hashtags: str = "") -> int:
    db.upsert_video(video_id, title="Example stream", channel_name="Example Creator")
    return db.add_clip(video_id, start, end, 90, "hook", title=title, hashtags=hashtags)


class FakePublisher:
    """Publishes through `provider` ("woopsocial" or "upload_post")."""

    def __init__(self, db, provider: str = "woopsocial"):
        self.db = db
        self.provider = provider
        self.sent = 0

    def publish(self, clip_id: int, platforms, *, state: str = "queued", url: str = "",
                scheduled_for: str = "", caption: str = "", visibility="default", error: str = "") -> str:
        self.sent += 1
        request_id = f"{self.provider}-request-{self.sent}"
        outcomes = [PlatformOutcome(platform=p, state=state, post_id=f"post-{p}-{self.sent}" if url else "",
                                    post_url=url, error=error) for p in platforms]
        WRITERS[self.provider].record_outcomes(
            self.db, clip_id, self.db.get_clip(clip_id), FanOutResult(request_id=request_id, outcomes=outcomes),
            scheduled_for=scheduled_for, caption=caption, visibility=visibility,
        )
        return request_id

    def refresh(self, clip_id: int, platform: str, state: str, *, url: str = "", error: str = "") -> None:
        row = self.db.conn.execute(
            "SELECT request_id FROM clip_publishes WHERE clip_id = ? AND platform = ?", (clip_id, platform)
        ).fetchone()
        outcome = PlatformOutcome(platform=platform, state=state, post_id=f"post-{platform}" if url else "",
                                  post_url=url, error=error)
        WRITERS[self.provider].record_refresh(
            self.db, clip_id, FanOutResult(request_id=row["request_id"] if row else "", outcomes=[outcome])
        )

    def youtube(self, clip_id: int, *, publish_at: str = "", privacy: str = "public", actual: str = "public",
                youtube_id: str = "abcdefghijk", done: bool = True) -> int:
        """A direct YouTube upload: its job, and once done its uploads row."""
        found = self.db.get_clip(clip_id)
        request = {"title": "A great moment", "privacy": privacy, "publish_at": publish_at or None}
        job_id = self.db.add_publish_job(clip_id, json.dumps(request), video_id=found["video_id"],
                                         start_s=found["start_s"], end_s=found["end_s"])
        if done:
            self.db.finish_publish_job(job_id, "done", youtube_id=youtube_id)
            self.db.record_publish(clip_id, {
                "youtube_id": youtube_id, "video_id": found["video_id"], "start_s": found["start_s"],
                "end_s": found["end_s"], "title": "A great moment", "description": "#clips",
                "privacy": privacy, "actual_privacy": actual, "publish_at": publish_at,
                "channel_id": "UC-example", "channel_title": "Example Channel", "state": "uploaded",
            })
        return job_id


def webhook_destination(db, data_dir, *, state: str = "connected", is_forum: int = 0, label: str = "Example hook",
                        webhook_id: str = WEBHOOK_ID, token: str = WEBHOOK_TOKEN) -> int:
    """A pasted webhook destination with its token in secrets."""
    dest_id = db.insert_destination(label=label, guild_id=GUILDS[0]["id"], guild_name=GUILDS[0]["name"],
                                    channel_id=CHANNEL_ID, webhook_id=webhook_id, webhook_name=label,
                                    is_forum=is_forum, state=state)
    service.save_webhook(data_dir, dest_id, webhook_id, token)
    return dest_id
