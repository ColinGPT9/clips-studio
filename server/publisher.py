"""The thread that uploads finished clips to YouTube.

Deliberately NOT the pipeline worker, for three reasons that each on their own
would be enough:

* An upload is minutes of network I/O. Behind an hour of GPU work, "Upload now"
  is a lie; in front of it, one person's slow upstream stalls an overnight
  batch.
* The pipeline worker claims nothing while the queue is paused (core/queue.py),
  and paused is its DEFAULT state. An upload queued there would simply never
  start, with no visible reason.
* `recover_interrupted_jobs()` re-queues every running job on startup. For a
  video that resumes a stage. For an upload it would post the same video to
  someone's channel twice.

That last one is why publish jobs live in their own table with the opposite
recovery rule: a publish caught by a crash becomes 'interrupted' and stops
there, and the user is told to check their channel rather than have the app
guess.
"""

import importlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.state import StateDB
from server import posts
from server import youtube_service as service
from server.events import broadcaster

# How long after an upload to read the video's status back. YouTube needs a
# moment to process before rejection or the privacy lock is visible.
VERIFY_AFTER_SECONDS = 60


class PublishWorker(threading.Thread):
    def __init__(self, config: dict, db_path: Path, data_dir: Path):
        super().__init__(daemon=True, name="publish-worker")
        self.config = config
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancelled: set[int] = set()
        self._verify: list[tuple[float, int, str]] = []  # (due_at, clip_id, video_id)
        # When to next ask a provider what became of the posts it accepted.
        self._next_provider_check = 0.0
        # When to next ask about posts published without a link.
        self._next_url_check = 0.0
        # Upload-Post request id -> unix time it was last asked about. In
        # memory only: after a restart each due request is asked once and its
        # cadence carries on (uploadpost_service.due_requests).
        self._uploadpost_checked: dict[str, float] = {}
        # When to next look for scheduled YouTube uploads whose time came, and
        # clip id -> unix time each was last tried (youtube_service.due_live_checks).
        self._next_youtube_check = 0.0
        self._youtube_tried: dict[int, float] = {}
        # Campaign submissions (campaigns/queue.py advance): when to next run
        # a pass, whether one was asked for now, and until when Discord's
        # global rate limit holds every webhook send. In memory only: every
        # other piece of their state is in campaign_submissions.
        self._next_campaign_check = 0.0
        self._campaigns_now = False
        self._campaign_pause = 0.0

    # ---- lifecycle -------------------------------------------------------

    def notify(self) -> None:
        self._wake.set()

    def notify_campaigns(self) -> None:
        """Run the campaign step on the next turn of the loop instead of at
        its 30-second tick: an attach, a typed link or an approval is then
        looked at (and a webhook row sent) at once."""
        self._campaigns_now = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def cancel(self, publish_job_id: int) -> None:
        self._cancelled.add(int(publish_job_id))

    def run(self) -> None:
        db = StateDB(self.db_path)  # one connection per thread; sqlite insists
        try:
            recovered = db.recover_running_publish_jobs()
            if recovered:
                print(f"{recovered} upload(s) were interrupted by a restart and will not retry.")
            self._recover_campaign_sends(db)

            while not self._stop.is_set():
                self._run_due_verifications(db)
                self._refresh_provider_posts(db)
                self._refresh_missing_urls(db)
                self._check_scheduled_youtube(db)
                self._advance_campaigns(db)
                job = db.claim_next_publish_job()
                if job is None:
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
                    continue
                try:
                    self._publish(db, job)
                except Exception as e:
                    self._fail(db, job, e)
        finally:
            db.close()

    # ---- what became of what we sent -------------------------------------

    #: How often to ask. Delivery takes hours or days, so a tight loop would
    #: spend its life asking about posts due on Thursday.
    PROVIDER_CHECK_SECONDS = 180

    def _refresh_provider_posts(self, db: StateDB) -> None:
        """Move accepted posts off "processing" once they actually land.

        A batch is accepted in one call and delivered at the provider's own
        pace, so without this every row sat at "processing" for good: 60 posts
        read as "sending" long after some of them had published and others had
        failed, and the only way to find out was the provider's own website.

        Contained and quiet: this is a read, and a provider being unreachable
        must never disturb the uploads this worker is actually here to run.
        Each provider is contained on its own, so one failing, or having
        nothing to ask about, never stops the other being asked.
        """
        now = time.monotonic()
        if now < self._next_provider_check:
            return
        self._next_provider_check = now + self.PROVIDER_CHECK_SECONDS
        self._refresh_woopsocial(db)
        self._refresh_uploadpost(db)

    def _refresh_woopsocial(self, db: StateDB) -> None:
        try:
            from server import woopsocial_service as woop

            if not woop.is_enabled(db) or not woop.has_key(self.data_dir):
                return
            # First, anything a stop in mid-send left unsettled, so it is either
            # followed like any other post or safe to send again. Contained on
            # its own: failing here must not stop the ordinary refresh below.
            try:
                settled = woop.reconcile_sending(db, self.data_dir)
                if settled["adopted"] or settled["failed"]:
                    print(
                        f"  Interrupted sends: {settled['adopted']} found on WoopSocial, "
                        f"{settled['failed']} never sent."
                    )
            except Exception as e:
                print(f"  Could not settle interrupted sends: {e}")
            if not woop.in_flight(db):
                return
            out = woop.refresh_in_flight(db, self.data_dir)
            if out.get("updated"):
                print(
                    f"  Publish status: {out['updated']} updated, "
                    f"{out['still_waiting']} still waiting."
                )
        except Exception as e:
            print(f"  Could not check publish status: {e}")

    def _refresh_uploadpost(self, db: StateDB) -> None:
        """Follow Upload-Post publishes in the background, as the open panel
        does in the foreground: only requests that are due, on a cadence that
        slows as they age (uploadpost_service.due_requests)."""
        try:
            from server import uploadpost_service as up

            if not up.is_enabled(db) or not up.has_key(self.data_dir):
                return
            checked = getattr(self, "_uploadpost_checked", None)
            if checked is None:
                checked = self._uploadpost_checked = {}
            now = datetime.now(timezone.utc)
            ids = up.due_requests(db, now=now, last_checked=checked)
            if not ids:
                return
            out = up.refresh_in_flight(db, self.data_dir, ids=ids, now=now, last_checked=checked)
            if out.get("updated"):
                print(
                    f"  Upload-Post status: {out['updated']} updated, "
                    f"{out['still_waiting']} still waiting."
                )
        except Exception as e:
            print(f"  Could not check Upload-Post status: {e}")

    #: How often to ask again about posts published without a link. Slower
    #: than the status check: these are live already, only the link is late.
    URL_CHECK_SECONDS = 15 * 60

    def _refresh_missing_urls(self, db: StateDB) -> None:
        """Ask again about posts a provider called published without a link.

        The status check above follows only posts still on their way, so a
        link that came after the post went live was never fetched. Each one is
        asked about for a day after it was due. Its own timer, and each
        provider contained on its own.
        """
        mono = time.monotonic()
        if mono < getattr(self, "_next_url_check", 0.0):
            return
        self._next_url_check = mono + self.URL_CHECK_SECONDS
        now = datetime.now(timezone.utc)
        for name, module in (("WoopSocial", "woopsocial_service"),
                             ("Upload-Post", "uploadpost_service")):
            try:
                provider = importlib.import_module(f"server.{module}")
                if not provider.is_enabled(db) or not provider.has_key(self.data_dir):
                    continue
                out = provider.refresh_missing_urls(db, self.data_dir, now=now)
                if out.get("updated"):
                    print(f"  {name} links: {out['updated']} post(s) updated.")
            except Exception as e:
                print(f"  Could not check {name} links: {e}")

    #: How often to look for scheduled YouTube uploads whose time has come.
    YOUTUBE_LIVE_CHECK_SECONDS = 10 * 60

    def _check_scheduled_youtube(self, db: StateDB, *, now=None) -> None:
        """Ask YouTube whether scheduled uploads went live once their time came.

        A scheduled upload goes up private, and the only read-back was the one
        a minute after the upload, in memory, so here it stayed private for
        good. Each one is asked a few times after its time, one quota unit
        each (youtube_service.due_live_checks), through the channel it went up
        on. Contained and quiet like the provider checks: a read must never
        disturb the uploads this worker is here to run.
        """
        mono = time.monotonic()
        if mono < getattr(self, "_next_youtube_check", 0.0):
            return
        self._next_youtube_check = mono + self.YOUTUBE_LIVE_CHECK_SECONDS
        try:
            if not service.is_enabled(db):
                return
            tried = getattr(self, "_youtube_tried", None)
            if tried is None:
                tried = self._youtube_tried = {}
            current = posts.as_utc(now)
            stamp = current.isoformat().replace("+00:00", "Z")
            unreachable = 0
            for row in service.due_live_checks(db, current, last_tried=tried):
                clip_id, video_id = row["clip_id"], row["youtube_id"]
                tried[clip_id] = current.timestamp()
                try:
                    publisher = service.make_publisher(
                        self.config, self.data_dir, channel_id=row["channel_id"] or None
                    )
                    status = publisher.video_status(video_id)
                except Exception:
                    # Not counted as a check: the next pass tries it again.
                    unreachable += 1
                    continue
                fields = service.live_check_fields(status, checked_at=stamp)
                db.set_upload_check(clip_id, **fields)
                if fields.get("state") in ("rejected", "failed"):
                    self._emit(0, clip_id, phase="checked", fraction=1.0, message=fields["error"],
                               terminal="checked", state=fields["state"], youtube_id=video_id)
            if unreachable:
                print(f"  Could not check {unreachable} scheduled YouTube upload(s).")
        except Exception as e:
            print(f"  Could not check scheduled YouTube uploads: {e}")

    # ---- campaign submissions --------------------------------------------

    #: How often the campaign step runs when nothing asks for it sooner.
    CAMPAIGN_CHECK_SECONDS = 30
    # Caught Discord errors may carry webhook credentials: these steps log
    # only which operation failed, never the exception's text.

    def _recover_campaign_sends(self, db: StateDB) -> None:
        """At startup, every webhook send a restart cut off becomes
        awaiting confirmation ("check the channel"), and is never sent again
        by itself: it may have reached Discord. Contained: an error here
        (sqlite's "database is locked" while other threads run their own
        recovery) must not end this thread and every upload with it."""
        try:
            from campaigns.statemachine import REASONS

            settled = db.recover_sending_submissions(REASONS["restart"])
            if settled:
                print(f"{settled} campaign send(s) were cut off by a restart: check Discord.")
        except Exception:
            print("Could not settle campaign sends cut off by a restart.")

    def _advance_campaigns(self, db: StateDB, *, now: float | None = None) -> None:
        """Move campaign submissions on (campaigns/queue.py advance): follow
        each post until it is live with a link, then send what a webhook may
        send. Every CAMPAIGN_CHECK_SECONDS, at once after notify_campaigns(),
        and again in a couple of seconds while sends are left over. Contained
        like the provider checks: it never raises into the loop."""
        mono = time.monotonic()
        asked = getattr(self, "_campaigns_now", False)
        if not asked and mono < getattr(self, "_next_campaign_check", 0.0):
            return
        self._campaigns_now = False
        self._next_campaign_check = mono + self.CAMPAIGN_CHECK_SECONDS
        try:
            from campaigns import discord_http, post_links, queue
            from campaigns.model import SEND_SPACING_SECONDS

            http = getattr(self, "campaign_http", None) or discord_http.Http()
            post_links.recover(db)
            out = queue.advance(db, self.data_dir, now=time.time() if now is None else now, http=http,
                                emit=self._emit_campaign, paused_until=getattr(self, "_campaign_pause", 0.0))
            self._campaign_pause = out["paused_until"]
            if out["more"]:
                self._next_campaign_check = mono + SEND_SPACING_SECONDS
        except Exception:
            print("  Could not move campaign submissions on.")

    def _emit_campaign(self, event: dict) -> None:
        """A submission changed status: the `campaign` event on /ws."""
        broadcaster.publish(event)

    # ---- one job ---------------------------------------------------------

    def _publish(self, db: StateDB, job) -> None:
        from publish.base import PublishRequest
        from publish.errors import PublishCancelled, PublishError, QuotaExceeded

        job_id = job["id"]
        request_data = json.loads(job["request"])

        clip = self._resolve_clip(db, job)
        if clip is None:
            raise PublishError(
                "That clip no longer exists. It may have been deleted while the upload was queued."
            )
        if clip["id"] != job["clip_id"]:
            # A chained render replaced the row. Follow it.
            db.set_publish_job_clip(job_id, clip["id"])
        clip_id = clip["id"]

        path = Path(clip["path"] or "")
        if not path.exists():
            raise PublishError("This clip's file is missing. Re-render it and try again.")

        ledger = service.load_ledger(db)
        if ledger.exhausted():
            raise QuotaExceeded(
                "Your API key's daily upload allowance is used up. It resets at "
                "midnight Pacific time.",
                resets_at=ledger.blocked_until,
            )

        self._emit(job_id, clip_id, phase="prepare", fraction=0.02,
                   message="Getting the clip ready")

        # channel_id selects which connected channel's token to publish with;
        # it is not part of the video's metadata, so it comes out here.
        channel_id = request_data.pop("channel_id", None)
        destination_id = request_data.pop("discord_destination_id", None)
        if destination_id is not None:
            from campaigns import post_links

            post_links.prepare(db, destination_id=destination_id, clip_id=clip_id, platforms=["youtube"],
                               provider="youtube", publish_job_id=job_id, title=request_data.get("title", ""))
        publisher = service.make_publisher(
            self.config, self.data_dir, channel_id=channel_id
        )
        # Hashtags belong in the DESCRIPTION, which is where viewers see them
        # and where the creator's own tag has to appear. Until now the clip's
        # hashtags were only ever sent as `tags`, YouTube's invisible keyword
        # field, so no published clip ever carried one. Done here rather than
        # in the editor because this is the one point both the editor and an
        # agent pass through, so neither can skip it, and because it applies to
        # clips that were generated long before any of this existed.
        request_data["description"] = _describe(db, clip, request_data.get("description", ""))

        request = PublishRequest(
            video_path=path,
            **{k: v for k, v in request_data.items() if k != "video_path"},
        )
        if request.thumbnail:
            request.thumbnail = Path(request.thumbnail)

        def on_progress(label: str, done: int, total: int) -> None:
            fraction = 0.05 + 0.85 * (done / total) if total else 0.05
            self._emit(job_id, clip_id, phase="upload", fraction=min(fraction, 0.9),
                       message=label)

        def cancelled() -> bool:
            return job_id in self._cancelled

        try:
            result = publisher.publish(request, on_progress=on_progress, should_cancel=cancelled)
        except PublishCancelled:
            self._cancelled.discard(job_id)
            db.finish_publish_job(job_id, "cancelled", error="Cancelled.")
            self._emit(job_id, clip_id, phase="cancelled", fraction=0,
                       message="Upload cancelled", terminal="cancelled")
            return
        except QuotaExceeded as e:
            ledger.record_quota_error()
            service.save_ledger(db, ledger)
            raise e

        ledger.record_upload()
        service.save_ledger(db, ledger)

        self._emit(job_id, clip_id, phase="metadata", fraction=0.95,
                   message="Applying metadata")

        db.record_publish(clip_id, {
            "youtube_id": result.video_id,
            "video_id": clip["video_id"],
            "start_s": clip["start_s"],
            "end_s": clip["end_s"],
            "title": request.title,
            # As it went up, hashtags included (_describe above).
            "description": request.description,
            "privacy": result.requested_privacy,
            "actual_privacy": result.actual_privacy,
            "publish_at": result.publish_at or "",
            "channel_id": result.channel_id,
            "channel_title": result.channel_title,
            "thumbnail_set": 1 if result.thumbnail_set else 0,
            "playlist_id": request.playlist_id or "",
            "state": "locked_private" if result.locked_private else "uploaded",
            "error": "",
        })
        db.finish_publish_job(job_id, "done", youtube_id=result.video_id)

        self._emit(
            job_id, clip_id,
            phase="done",
            fraction=1.0,
            message="Scheduled on YouTube" if result.publish_at else "Published on YouTube",
            terminal="done",
            youtube_id=result.video_id,
            url=result.url,
            warnings=result.warnings,
            locked_private=result.locked_private,
        )
        self._verify.append((time.monotonic() + VERIFY_AFTER_SECONDS, clip_id, result.video_id))

    def _resolve_clip(self, db: StateDB, job):
        """Find the clip, following a re-render that gave it a new id."""
        clip = db.get_clip(job["clip_id"])
        if clip is not None:
            return clip
        if not job["video_id"]:
            return None
        return db.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (job["video_id"], round(job["start_s"], 2), round(job["end_s"], 2)),
        ).fetchone()

    def _fail(self, db: StateDB, job, error: Exception) -> None:
        from publish.errors import PublishError
        from server.feedback import redact

        message = error.message if isinstance(error, PublishError) else str(error)
        # Never let a raw exception string reach the UI or the log: an OAuth
        # failure can carry a token fragment in its body.
        message = redact(message)[:500]
        db.finish_publish_job(job["id"], "failed", error=message)
        print(f"Publish job {job['id']} failed: {message}")
        self._emit(job["id"], job["clip_id"], phase="failed", fraction=0,
                   message=message, terminal="failed", error=message)

    # ---- deferred verification -------------------------------------------

    def _run_due_verifications(self, db: StateDB) -> None:
        """A minute after an upload, read the video back.

        Three things only become visible after YouTube processes the file:
        rejection (duplicate, copyright), processing failure, and confirmation
        that an unaudited project had its upload locked to private. One
        quota unit each, and without it the app would report success for a
        video that never plays.
        """
        if not self._verify:
            return
        now = time.monotonic()
        due = [item for item in self._verify if item[0] <= now]
        if not due:
            return
        self._verify = [item for item in self._verify if item[0] > now]

        for _, clip_id, video_id in due:
            row = db.get_upload(clip_id)
            if row is None or row["youtube_id"] != video_id:
                continue
            try:
                # Read back through the channel it was published to — another
                # channel's token cannot see a private video on this one.
                publisher = service.make_publisher(
                    self.config, self.data_dir, channel_id=row["channel_id"] or None
                )
                status = publisher.video_status(video_id)
            except Exception:
                continue  # advisory only; never fail a finished upload over it
            if not status:
                continue
            state = "uploaded"
            error = ""
            if status.get("upload_status") == "rejected":
                state = "rejected"
                error = f"YouTube rejected this video: {status.get('rejection_reason') or 'unknown'}."
            elif status.get("upload_status") == "failed":
                state = "failed"
                error = f"YouTube could not process this video: {status.get('failure_reason') or 'unknown'}."
            elif (
                row["privacy"] in ("public", "unlisted")
                and status.get("privacy") == "private"
                and not row["publish_at"]
            ):
                state = "locked_private"
            db.record_publish(clip_id, {
                "actual_privacy": status.get("privacy", ""),
                "state": state,
                "error": error,
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "youtube_id": video_id,
            })
            if state != "uploaded":
                self._emit(0, clip_id, phase="checked", fraction=1.0, message=error or
                           "YouTube locked this video to private.", terminal="checked",
                           state=state, youtube_id=video_id)

    # ---- events ----------------------------------------------------------

    def _emit(self, job_id: int, clip_id: int, **fields) -> None:
        """Publish straight to the broadcaster rather than through
        core.progress: the pipeline worker installs a global progress handler
        that stamps every event with the currently running JOB id, which would
        attribute an upload to whatever video happens to be rendering.

        The event type is 'publish', not 'job', so lib/jobProgress.ts leaves
        the global processing bar alone.
        """
        broadcaster.publish({
            "type": "publish",
            "publish_job": job_id,
            "clip_id": clip_id,
            "stage": "publish",
            **fields,
        })


def next_reset_local_hint() -> str:
    """A human-readable UTC instant for the next quota reset."""
    from publish.quota import next_reset

    return next_reset().isoformat().replace("+00:00", "Z")


def seconds_until(iso: str) -> float:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    return max(0.0, (moment - datetime.now(timezone.utc)) / timedelta(seconds=1))


def _describe(db: StateDB, clip, description: str) -> str:
    """The description as it will appear on YouTube, hashtags included.

    The creator's tag comes from the video's channel name rather than from
    anything typed per clip, so "#streamername on every video" needs no setup
    and cannot be forgotten on clip 14 of 30.

    Failure here must never cost an upload that is otherwise ready, so a
    missing video row or unreadable hashtags just means the description goes up
    as written.
    """
    from publish.metadata import description_with_hashtags, with_common_block

    try:
        hashtags = json.loads(clip["hashtags"] or "[]")
        if not isinstance(hashtags, list):
            hashtags = []
    except (TypeError, ValueError):
        hashtags = []

    creator = ""
    try:
        row = db.conn.execute(
            "SELECT channel_name FROM videos WHERE video_id = ?", (clip["video_id"],)
        ).fetchone()
        if row:
            creator = row["channel_name"] or ""
    except Exception:
        creator = ""

    # Order: the clip's own words, then the standing block, then the tags.
    common = ""
    try:
        common = service.load_settings(db).get("common_description") or ""
    except Exception:
        common = ""

    return description_with_hashtags(
        with_common_block(description, common), hashtags, creator=creator
    )
