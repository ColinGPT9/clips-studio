"""Clipping campaigns: the campaigns themselves and their submissions.

The routes over campaigns/ (model.clean_campaign holds every campaign rule,
queue.py every submission step, views.py the shapes), which CI's main job
tests without FastAPI. Every route that changes something needs the desktop
app's session secret in the X-Clips-Kitty-Session header, the same value the
plugin and Discord routes take; only the preview, which changes nothing, is
open. The /campaigns/submissions routes are declared before
/campaigns/{campaign_id}, which would otherwise take "submissions" for an id.

The publish routes are left alone: the UI attaches a published clip here
right after its publish call, so a campaign problem can never fail a publish.
After a change the publish worker is asked to run its campaign step at once.
Every route is internal (server/api_stability.py).
"""

import time
from typing import Any, Literal

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from campaigns import eligibility, formatter, model, queue, statemachine, urls, views
from plugins import session


class AttachItem(BaseModel):
    clip_id: int
    platforms: list[str]


class AttachIn(BaseModel):
    items: list[AttachItem]
    campaign_ids: list[int]
    provider: str = ""
    publish_job_id: int = 0
    mode: Literal["after_publish", "now", "review", "later"] = "after_publish"
    post_url: str = ""
    origin: Literal["editor", "later", "publish_all"] = "editor"
    reopen: bool = False


class SubmissionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_url: str | None = None
    message_text: str | None = None
    arg_values: dict[str, Any] | None = None
    extra_values: dict[str, Any] | None = None
    destination_id: int | None = None
    method: str | None = None
    hold: bool | None = None


class MoveIn(BaseModel):
    campaign_id: int


class RetryIn(BaseModel):
    send_again: bool = False


class RecordIn(BaseModel):
    outcome: Literal["submitted", "confirmed", "rejected"]
    note: str = ""
    message_link: str = ""


class CampaignIn(BaseModel):
    """Every campaign field, typed loosely: model.clean_campaign checks them
    and says what is wrong in words, which a type error here would not."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    enabled: bool | None = None
    method: str | None = None
    guild_id: str | None = None
    guild_name: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    channel_link: str | None = None
    destination_id: int | None = None
    website_url: str | None = None
    requirements: str | None = None
    platforms: list[str] | str | None = None
    required_accounts: dict[str, str] | str | None = None
    trust_account: bool | None = None
    required_hashtags: list[str] | str | None = None
    required_mentions: list[str] | str | None = None
    required_text: str | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None
    deadline: str | None = None
    approval_required: bool | None = None
    instructions: str | None = None
    command_name: str | None = None
    command_args: list[dict[str, Any]] | str | None = None
    url_argument: str | None = None
    extra_fields: list[dict[str, Any]] | str | None = None
    message_template: str | None = None
    thread_name_template: str | None = None
    review_first: bool | None = None
    filters: dict[str, Any] | str | None = None
    notes: str | None = None

    def given(self) -> dict:
        return self.model_dump(exclude_unset=True)


class PreviewIn(BaseModel):
    campaign: CampaignIn | None = None
    campaign_id: int | None = None
    clip_id: int | None = None
    platform: str = ""
    url: str = ""
    caption: str = ""
    scheduled_for: str = ""


def _refused(e: Exception) -> HTTPException:
    """A submission step that can't happen: 404, 400 or 409 with its reason."""
    if isinstance(e, LookupError):
        return HTTPException(404, str(e).strip("'\""))
    if isinstance(e, statemachine.Conflict):
        return HTTPException(409, "It changed meanwhile: look at it again")
    if isinstance(e, statemachine.NotAllowed):
        return HTTPException(409, str(e))
    return HTTPException(400, str(e))


def install(app, *, db, data_dir, publish_worker, session_secret: str) -> None:
    """Add the /campaigns routes. `session_secret` is plugins_api.install's
    answer; `publish_worker` runs the background step (campaigns.queue.advance)."""

    def require_session(x_clips_kitty_session: str | None = Header(default=None)) -> None:
        if not session.matches(session_secret, x_clips_kitty_session):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a script "
                                     f"finds it in {session.path(data_dir)}.")

    guarded = [Depends(require_session)]

    def nudge() -> None:
        poke = getattr(publish_worker, "notify_campaigns", None)
        if poke is not None:
            poke()

    def show(d, row) -> dict:
        return views.submission_view(d, row)

    def step(fn, *args, **kw) -> dict:
        """Run one submission step in its own connection, answer the row."""
        d = db()
        try:
            row = fn(d, *args, now=time.time(), **kw)
            shown = show(d, row)
        except (LookupError, ValueError, statemachine.NotAllowed) as e:
            raise _refused(e) from None
        finally:
            d.close()
        nudge()
        return shown

    # ---- submissions (before /campaigns/{campaign_id}) -----------------------------------

    @app.get("/campaigns/submissions")
    def campaign_submissions(status: str = "", campaign_id: int | None = None, clip_id: int | None = None,
                             video_id: str | None = None, group: str = "", limit: int = 200):
        statuses = tuple(s for s in (p.strip() for p in status.split(",")) if s)
        unknown = set(statuses) - set(model.STATUSES)
        if unknown:
            raise HTTPException(400, f"Unknown statuses: {', '.join(sorted(unknown))}")
        if group and group not in model.GROUPS:
            raise HTTPException(400, f"Unknown group: {group}")
        limit = max(1, min(int(limit), 1000))
        wanted = statuses
        if group:
            wanted = tuple(s for s in views.GROUP_STATUSES[group] if not statuses or s in statuses)
        d = db()
        try:
            rows = []
            if wanted or not group:
                rows = d.list_submissions(statuses=wanted, campaign_id=campaign_id, clip_id=clip_id,
                                          video_id=video_id, limit=1_000_000 if group else limit)
            if group:
                rows = [r for r in rows if model.group(r) == group][:limit]
            user = views.discord_user(d)
            campaigns: dict[int, Any] = {}
            shown = []
            for row in rows:
                cid = int(row["campaign_id"])
                if cid not in campaigns:
                    campaigns[cid] = d.get_campaign(cid)
                shown.append(views.submission_view(d, row, campaign=campaigns[cid], user=user))
            counted = views.counts_of(d.submission_counts(campaign_id=campaign_id, clip_id=clip_id,
                                                          video_id=video_id))
        finally:
            d.close()
        return {"submissions": shown, **counted}

    @app.post("/campaigns/submissions", dependencies=guarded)
    def campaign_attach(body: AttachIn):
        d = db()
        try:
            got = queue.attach(
                d, items=[i.model_dump() for i in body.items], campaign_ids=body.campaign_ids, now=time.time(),
                mode=body.mode, provider=body.provider, publish_job_id=body.publish_job_id,
                post_url=body.post_url, origin=body.origin, reopen_rows=body.reopen,
            )
            answer = {
                "created": [show(d, r) for r in got["created"]],
                "existing": [show(d, r) for r in got["existing"]],
                "refused": got["refused"],
            }
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        finally:
            d.close()
        nudge()
        return answer

    @app.get("/campaigns/submissions/{submission_id}")
    def campaign_submission(submission_id: int):
        d = db()
        try:
            row = d.get_submission(submission_id)
            if row is None:
                raise HTTPException(404, f"No submission #{submission_id}")
            return show(d, row)
        finally:
            d.close()

    @app.patch("/campaigns/submissions/{submission_id}", dependencies=guarded)
    def campaign_edit_submission(submission_id: int, body: SubmissionPatch):
        return step(queue.edit, submission_id, **body.model_dump(exclude_unset=True))

    @app.post("/campaigns/submissions/{submission_id}/move", dependencies=guarded)
    def campaign_move_submission(submission_id: int, body: MoveIn):
        d = db()
        try:
            cancelled, moved = queue.move(d, submission_id, body.campaign_id, now=time.time())
            answer = {"cancelled": show(d, cancelled), "moved": show(d, moved)}
        except (LookupError, ValueError, statemachine.NotAllowed) as e:
            raise _refused(e) from None
        finally:
            d.close()
        nudge()
        return answer

    @app.post("/campaigns/submissions/{submission_id}/approve", dependencies=guarded)
    def campaign_approve(submission_id: int):
        return step(queue.approve, submission_id)

    @app.post("/campaigns/submissions/{submission_id}/submit", dependencies=guarded)
    def campaign_submit(submission_id: int):
        return step(queue.submit, submission_id)

    @app.post("/campaigns/submissions/{submission_id}/retry", dependencies=guarded)
    def campaign_retry(submission_id: int, body: RetryIn | None = None):
        return step(queue.retry, submission_id, send_again=bool(body and body.send_again))

    @app.post("/campaigns/submissions/{submission_id}/opened", dependencies=guarded)
    def campaign_opened(submission_id: int):
        return step(queue.opened, submission_id)

    @app.post("/campaigns/submissions/{submission_id}/record", dependencies=guarded)
    def campaign_record(submission_id: int, body: RecordIn):
        return step(queue.record, submission_id, body.outcome, note=body.note, message_link=body.message_link)

    @app.post("/campaigns/submissions/{submission_id}/cancel", dependencies=guarded)
    def campaign_cancel(submission_id: int):
        return step(queue.cancel, submission_id)

    # ---- campaigns ---------------------------------------------------------------------------

    def destination_ok(d, fields: dict) -> None:
        dest_id = int(fields.get("destination_id") or 0)
        if dest_id and d.get_destination(dest_id) is None:
            raise HTTPException(400, "That webhook destination doesn't exist (Settings → Discord → Destinations)")

    @app.get("/campaigns")
    def campaigns_list(archived: bool = False):
        d = db()
        try:
            return {"campaigns": [views.campaign_view(d, row) for row in d.list_campaigns(archived=archived)]}
        finally:
            d.close()

    @app.post("/campaigns", dependencies=guarded)
    def campaign_create(body: CampaignIn):
        d = db()
        try:
            try:
                fields = model.clean_campaign(body.given(), partial=False)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            destination_ok(d, fields)
            campaign_id = d.insert_campaign(fields.pop("name"), **fields)
            return {"campaign": views.campaign_view(d, d.get_campaign(campaign_id))}
        finally:
            d.close()

    @app.post("/campaigns/preview")
    def campaign_preview(body: PreviewIn):
        # Changes nothing, so no session header.
        d = db()
        try:
            stored = d.get_campaign(body.campaign_id) if body.campaign_id else None
            if body.campaign_id and stored is None:
                raise HTTPException(404, f"No campaign #{body.campaign_id}")
            if body.campaign is None and stored is None:
                raise HTTPException(400, "Give the campaign's settings or its id")
            campaign = dict(stored) if stored is not None else {}
            if body.campaign is not None:
                try:
                    campaign.update(model.clean_campaign(body.campaign.given(), partial=True, current=stored))
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None
            return _preview(d, campaign, body)
        finally:
            d.close()

    @app.get("/campaigns/{campaign_id}")
    def campaign_get(campaign_id: int):
        d = db()
        try:
            row = d.get_campaign(campaign_id)
            if row is None:
                raise HTTPException(404, f"No campaign #{campaign_id}")
            return views.campaign_view(d, row)
        finally:
            d.close()

    @app.patch("/campaigns/{campaign_id}", dependencies=guarded)
    def campaign_update(campaign_id: int, body: CampaignIn):
        d = db()
        try:
            before = d.get_campaign(campaign_id)
            if before is None:
                raise HTTPException(404, f"No campaign #{campaign_id}")
            try:
                fields = model.clean_campaign(body.given(), partial=True, current=before)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            destination_ok(d, fields)
            if fields:
                d.set_campaign(campaign_id, **fields)
            updated = queue.reapply_campaign(d, campaign_id, before, now=time.time())
            answer = {"campaign": views.campaign_view(d, d.get_campaign(campaign_id)),
                      "submissions_updated": updated}
        finally:
            d.close()
        nudge()
        return answer

    @app.delete("/campaigns/{campaign_id}", dependencies=guarded)
    def campaign_delete(campaign_id: int):
        d = db()
        try:
            result = queue.remove_campaign(d, campaign_id, now=time.time())
        finally:
            d.close()
        if not result:
            raise HTTPException(404, f"No campaign #{campaign_id}")
        return {"result": result}


def _preview(d, campaign: dict, body: PreviewIn) -> dict:
    """What the campaign's settings make of a clip (or of sample values):
    the message, the command, the thread title, the length and the problems,
    plus the checks that can run before the post exists."""
    sample: dict[str, str] = {}
    duration = None
    clip = d.get_clip(body.clip_id) if body.clip_id else None
    if body.clip_id and clip is None:
        raise HTTPException(404, f"No clip #{body.clip_id}")
    if clip is not None:
        sub = {"clip_id": int(clip["id"]), "video_id": clip["video_id"], "start_s": clip["start_s"],
               "end_s": clip["end_s"], "platform": body.platform or "tiktok", "clip_title": queue.clip_title(clip),
               "post_url": "", "account": "", "posted_at": "", "extra_values": "{}"}
        values = formatter.values_for(d, sub, campaign, discord_user=views.discord_user(d))
        sample = {k: v for k, v in values.items() if v and k not in ("url", "account", "posted_at")}
        duration = float(clip["end_s"]) - float(clip["start_s"])
    if body.platform:
        sample.update(platform=model.platform_label(body.platform), platform_id=body.platform)
    if body.url:
        normalised = urls.validate(body.platform, body.url)[0] if body.platform else ""
        sample["url"] = normalised or body.url
    shown = formatter.preview(campaign, sample)
    checks = []
    if body.platform:
        checks = eligibility.precheck(campaign, platform=body.platform, duration_s=duration or 0,
                                      caption=body.caption, scheduled_for=body.scheduled_for, now=time.time())
        if duration is None:
            checks = [c for c in checks if c.name != "duration"]  # no clip, no length to check
    return {
        "message": shown["message"],
        "command": {"line": shown["command"]["line"], "args": shown["command"]["args"]},
        "thread_name": shown["thread_name"],
        "length": shown["length"],
        "problems": shown["problems"],
        "checks": [c.as_dict() for c in checks],
    }
