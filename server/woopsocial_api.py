"""HTTP routes for publishing through WoopSocial.

Mirrors server/uploadpost_api.py: same route shapes, same rules, same
`clip_publishes` table underneath, so the renderer talks to whichever
provider is switched on without caring which it is. Installed with one call
from create_app().

The two rules that hold everywhere here:

* A switched-off feature 404s and /woopsocial/status says only
  `{"enabled": false}`. Off should read as absent.
* **The API key never leaves the backend.** The UI gets `has_key` and four
  characters. Errors go through redact().
"""

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from publish.errors import PublishError
from server import woopsocial_service as service
from server.feedback import redact


class KeyIn(BaseModel):
    api_key: str


class SettingsIn(BaseModel):
    enabled: bool | None = None
    platforms: list[str] | None = None
    common_description: str | None = None
    affiliate_url: str | None = None


class ConnectIn(BaseModel):
    platform: str = ""


class BatchIn(BaseModel):
    clip_ids: list[int] = []
    platforms: list[str] = []
    overrides: dict[str, dict] = {}
    # Hours between posts. 24 is one a day, which is the usual shape for
    # spreading a video's clips across a month.
    every_hours: float = 0
    start_at: str = ""
    # Added to every clip in the run, on top of whatever tags each clip
    # already carries. The "#" is optional; duplicates are dropped.
    hashtags: list[str] = []


class PublishIn(BaseModel):
    platforms: list[str] = []
    title: str = ""
    description: str = ""
    tags: list[str] = []
    overrides: dict[str, dict] = {}
    scheduled_date: str = ""


def install(app, *, config, db, data_dir, publish_worker=None) -> None:
    data_path = Path(data_dir)

    def _guard(d):
        if not service.is_enabled(d):
            raise HTTPException(404, "WoopSocial publishing is not enabled")

    def _fail(e: Exception) -> HTTPException:
        message = e.message if isinstance(e, PublishError) else str(e)
        return HTTPException(400, redact(message)[:500])

    def _client():
        if not service.has_key(data_path):
            raise HTTPException(400, "No WoopSocial API key is set.")
        return service.make_client(data_path)

    def _project(d, client) -> str:
        """The project id, created on first use.

        Their accounts hang off a project, and a creator should never be
        asked to make one — so if none is remembered, an existing one is
        adopted or a new one is created under a fixed name.
        """
        settings = service.load_settings(d)
        existing = settings.get("project_id") or ""
        if existing:
            return existing
        projects = client.projects()
        if projects:
            found = str(projects[0].get("id") or "")
        else:
            found = str((client.create_project(service.DEFAULT_PROJECT_NAME) or {}).get("id") or "")
        if found:
            service.save_settings(d, {"project_id": found})
        return found

    # ---- status and settings --------------------------------------------

    @app.get("/woopsocial/status")
    def woopsocial_status():
        d = db()
        try:
            return service.status_payload(d, data_path)
        finally:
            d.close()

    @app.patch("/woopsocial/settings")
    def woopsocial_settings(body: SettingsIn):
        d = db()
        try:
            service.save_settings(
                d, {k: v for k, v in body.model_dump().items() if v is not None}
            )
            return service.status_payload(d, data_path)
        finally:
            d.close()

    # ---- the key ---------------------------------------------------------

    @app.put("/woopsocial/key")
    def put_key(body: KeyIn):
        key = (body.api_key or "").strip()
        if not key:
            raise HTTPException(400, "Enter your WoopSocial API key.")
        service.save_key(data_path, key)
        d = db()
        try:
            try:
                account = service.make_client(data_path).validate_key()
            except PublishError as e:
                # A key that does not work is discarded rather than left
                # making the card claim a connection that is not there.
                service.wipe_key(data_path)
                raise _fail(e) from e
            return {
                **service.status_payload(d, data_path),
                "projects": len(account.get("projects") or []),
            }
        finally:
            d.close()

    @app.delete("/woopsocial/key")
    def delete_key():
        removed = service.wipe_key(data_path)
        d = db()
        try:
            return {"removed": removed, **service.status_payload(d, data_path)}
        finally:
            d.close()

    # ---- social accounts -------------------------------------------------

    @app.get("/woopsocial/connections")
    def connections():
        d = db()
        try:
            _guard(d)
            client = _client()
            try:
                return {"connected": client.connected_platforms(_project(d, client))}
            except PublishError as e:
                raise _fail(e) from e
        finally:
            d.close()

    @app.post("/woopsocial/connect")
    def connect(body: ConnectIn):
        """A browser link for connecting one platform.

        Unlike Upload-Post's single hosted page, WoopSocial authorises one
        platform at a time, so the caller names which.
        """
        d = db()
        try:
            _guard(d)
            if not body.platform:
                raise HTTPException(400, "Which platform?")
            client = _client()
            try:
                url = client.connect_url(_project(d, client), body.platform)
            except PublishError as e:
                raise _fail(e) from e
            if not url:
                raise HTTPException(
                    400, "WoopSocial did not return a connection link. Try again."
                )
            return {"url": url, "platform": body.platform}
        finally:
            d.close()

    # ---- publishing ------------------------------------------------------

    @app.post("/woopsocial/clips/{clip_id}/publish")
    def publish_clip(clip_id: int, body: PublishIn):
        from publish.uploadpost import validate_schedule
        from publish.woopsocial import WoopSocialPublisher

        d = db()
        try:
            _guard(d)
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            if not body.title.strip():
                raise HTTPException(400, "A title is required.")
            if not body.platforms:
                raise HTTPException(400, "Pick at least one platform.")
            if not (clip["path"] and Path(clip["path"]).exists()):
                raise HTTPException(
                    400, "This clip has no rendered file yet. Apply your edits first."
                )

            settings = service.load_settings(d)
            text = body.description
            standing = (settings.get("common_description") or "").strip()
            if standing:
                text = f"{text}\n\n{standing}".strip()
            if body.tags:
                text = f"{text}\n\n{' '.join('#' + t.lstrip('#') for t in body.tags)}".strip()

            try:
                when = validate_schedule(body.scheduled_date)
            except PublishError as e:
                raise _fail(e) from e

            client = _client()
            publisher = WoopSocialPublisher(client, _project(d, client))
            try:
                result = publisher.start(
                    Path(clip["path"]),
                    platforms=body.platforms,
                    title=body.title.strip(),
                    text=text,
                    scheduled_for=when,
                    overrides=body.overrides,
                )
            except PublishError as e:
                raise _fail(e) from e

            _record(d, clip_id, clip, result)
            service.save_settings(d, {"platforms": body.platforms})
            return _payload(d, clip_id, result.request_id)
        finally:
            d.close()

    @app.post("/woopsocial/batch/plan")
    def plan_batch(body: BatchIn):
        """Work out what a batch WOULD do. Creates and publishes nothing.

        This is the half of batch publishing the assistant is allowed to
        call. It resolves the clips, the platforms and the exact time each
        post would land, and hands that back for a person to look at and
        agree to — the same shape as /publish/plan for YouTube, and for the
        same reason: an upload cannot be taken back, so a model may describe
        one but never start one.
        """
        from datetime import datetime, timedelta
        from datetime import timezone as tz

        d = db()
        try:
            _guard(d)
            clip_ids = list(body.clip_ids)
            if not clip_ids:
                raise HTTPException(400, "No clips were chosen.")
            platforms = body.platforms or ["youtube"]

            start = datetime.now(tz.utc) + timedelta(minutes=2)
            if body.start_at:
                try:
                    start = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
                except ValueError as e:
                    raise HTTPException(400, "That start time could not be read.") from e

            items, warnings = [], []
            for index, clip_id in enumerate(clip_ids):
                clip = d.get_clip(clip_id)
                if clip is None:
                    warnings.append(f"Clip {clip_id} no longer exists.")
                    continue
                if not (clip["path"] and Path(clip["path"]).exists()):
                    warnings.append(f"Clip {clip_id} has not been rendered yet.")
                    continue
                at = start + timedelta(hours=body.every_hours * index)
                items.append(
                    {
                        "clip_id": clip_id,
                        "title": (clip["title"] or clip["hook"] or f"Clip {clip_id}").strip(),
                        "publish_at": at.isoformat() if body.every_hours or body.start_at else "",
                    }
                )

            connected: list[str] = []
            try:
                client = _client()
                connected = client.connected_platforms(_project(d, client))
            except (HTTPException, PublishError):
                # Worth planning anyway — the person may be about to connect.
                warnings.append("Could not check which accounts are connected.")
            if connected:
                for p in platforms:
                    if p not in connected:
                        warnings.append(f"{p} is not connected yet and would be skipped.")

            return {
                "provider": "woopsocial",
                "platforms": platforms,
                "every_hours": body.every_hours,
                "hashtags": [h.lstrip("#").strip() for h in body.hashtags if h.strip()],
                "items": items,
                "warnings": warnings,
            }
        finally:
            d.close()

    @app.post("/woopsocial/batch")
    def publish_batch(body: BatchIn):
        """Publish several clips, spaced out over time.

        One post per clip, because each clip is different media. The spacing
        is done with WoopSocial's own scheduler rather than a timer here, so
        a run that stretches over days keeps going with Clips Kitty closed —
        which is the whole point of scheduling a month of Shorts.
        """
        from datetime import datetime, timedelta
        from datetime import timezone as tz

        from publish.uploadpost import validate_schedule
        from publish.woopsocial import WoopSocialPublisher

        if not body.clip_ids:
            raise HTTPException(400, "No clips were chosen.")
        if not body.platforms:
            raise HTTPException(400, "Pick at least one platform.")

        d = db()
        try:
            _guard(d)
            settings = service.load_settings(d)
            standing = (settings.get("common_description") or "").strip()
            client = _client()
            publisher = WoopSocialPublisher(client, _project(d, client))

            start = datetime.now(tz.utc)
            if body.start_at:
                try:
                    validate_schedule(body.start_at)
                    start = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
                except (PublishError, ValueError) as e:
                    raise _fail(
                        e
                        if isinstance(e, PublishError)
                        else PublishError("That start time could not be read.")
                    ) from e

            started, skipped = [], []
            for index, clip_id in enumerate(body.clip_ids):
                clip = d.get_clip(clip_id)
                if clip is None:
                    skipped.append({"clip_id": clip_id, "reason": "no such clip"})
                    continue
                if not (clip["path"] and Path(clip["path"]).exists()):
                    skipped.append({"clip_id": clip_id, "reason": "no rendered file yet"})
                    continue

                text = (clip["description"] or "").strip()
                if standing:
                    text = f"{text}\n\n{standing}".strip()
                # The clip's own tags first, then anything asked for
                # across the whole run, with duplicates dropped so a tag
                # the clip already had is not repeated.
                tags = _tags_of(clip)
                for extra in body.hashtags:
                    cleaned = extra.lstrip("#").strip()
                    if cleaned and cleaned.lower() not in {t.lower() for t in tags}:
                        tags.append(cleaned)
                if tags:
                    text = f"{text}\n\n{' '.join('#' + t for t in tags)}".strip()

                when = ""
                if body.every_hours or body.start_at:
                    at = start + timedelta(hours=body.every_hours * index)
                    # Their scheduler wants UTC and refuses a past time; the
                    # first slot of a "starting now" run would otherwise be a
                    # second or two behind by the time it arrives.
                    if at <= datetime.now(tz.utc):
                        at = datetime.now(tz.utc) + timedelta(minutes=2)
                    when = at.isoformat()

                try:
                    result = publisher.start(
                        Path(clip["path"]),
                        platforms=body.platforms,
                        title=(clip["title"] or clip["hook"] or f"Clip {clip_id}").strip(),
                        text=text,
                        scheduled_for=when,
                        overrides=body.overrides,
                    )
                except PublishError as e:
                    # One clip's problem must not cost the rest of the batch.
                    skipped.append({"clip_id": clip_id, "reason": e.message})
                    continue

                _record(d, clip_id, clip, result)
                started.append(
                    {"clip_id": clip_id, "request_id": result.request_id, "scheduled_for": when}
                )

            service.save_settings(d, {"platforms": body.platforms})
            return {"started": started, "skipped": skipped}
        finally:
            d.close()

    def _tags_of(clip) -> list[str]:
        """A clip's hashtags, stored as a JSON list in one column."""
        import json

        raw = clip["hashtags"] if "hashtags" in clip.keys() else ""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(t).lstrip("#") for t in parsed if str(t).strip()]

    @app.post("/woopsocial/refresh/{post_id}")
    def refresh(post_id: str):
        from publish.woopsocial import WoopSocialPublisher

        d = db()
        try:
            _guard(d)
            rows = d.publishes_for_request(post_id)
            if not rows:
                raise HTTPException(404, "no such publish")
            clip_id = int(rows[0]["clip_id"])
            clip = d.get_clip(clip_id)
            client = _client()
            publisher = WoopSocialPublisher(client, _project(d, client))
            try:
                result = publisher.check(post_id)
            except PublishError as e:
                raise _fail(e) from e
            if result.outcomes:
                _record(d, clip_id, clip, result)
            return _payload(d, clip_id, post_id)
        finally:
            d.close()

    def _record(d, clip_id: int, clip, result) -> None:
        for outcome in result.outcomes:
            d.record_clip_publish(
                clip_id,
                outcome.platform,
                {
                    "provider": "woopsocial",
                    "video_id": (clip["video_id"] if clip is not None else "") or "",
                    "start_s": (clip["start_s"] if clip is not None else 0) or 0,
                    "end_s": (clip["end_s"] if clip is not None else 0) or 0,
                    "state": outcome.state,
                    "post_id": outcome.post_id,
                    "post_url": outcome.post_url,
                    "error": outcome.error,
                    "request_id": result.request_id,
                },
            )

    def _payload(d, clip_id: int, request_id: str) -> dict:
        rows = [dict(r) for r in d.clip_publishes(clip_id)]
        live = [r for r in rows if r.get("state") in ("queued", "processing")]
        return {
            "clip_id": clip_id,
            "request_id": request_id,
            "platforms": rows,
            "done": not live,
        }
