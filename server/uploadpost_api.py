"""HTTP routes for publishing to several platforms through Upload-Post.

Structured like server/youtube_api.py and installed the same way, with one
call from create_app(). The same two rules hold:

* When the feature is switched off, every route here 404s and /uploadpost/status
  answers `{"enabled": false}` and nothing else. A disabled feature should look
  absent, not refused — an install that never touches Upload-Post makes no
  network calls to it and shows no trace of it.
* **The API key never leaves the backend.** No route returns it, echoes it back
  after saving, or puts it in an error. The UI is told `has_key` and the last
  four characters, which is enough to answer "which key is this?" and useless
  to anyone who reads it over a shoulder. Errors go through redact().
"""

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from publish.errors import PublishError
from server import uploadpost_service as service
from server.feedback import redact


class KeyIn(BaseModel):
    api_key: str


class SettingsIn(BaseModel):
    enabled: bool | None = None
    profile: str | None = None
    platforms: list[str] | None = None
    common_description: str | None = None
    first_comment: str | None = None
    affiliate_url: str | None = None


class PublishIn(BaseModel):
    platforms: list[str] = []
    title: str = ""
    description: str = ""
    tags: list[str] = []
    first_comment: str = ""
    # True means "use the thumbnail already chosen for this clip". A path is
    # never accepted from a caller, same rule the YouTube route follows.
    thumbnail: bool = False
    # {"instagram": {"title": "..."}, "youtube": {"privacy": "public"}}.
    # Only fields the platform documents survive; see PLATFORM_FIELDS.
    overrides: dict[str, dict] = {}
    # Scheduling and queueing are separate features and cannot be combined.
    scheduled_date: str = ""
    timezone: str = ""
    add_to_queue: bool = False


class BatchIn(BaseModel):
    clip_ids: list[int] = []
    platforms: list[str] = []
    overrides: dict[str, dict] = {}
    # Spacing. Zero with no start_at means every clip goes at once, which is
    # only sensible for a handful.
    every_hours: float = 0
    start_at: str = ""
    timezone: str = ""
    add_to_queue: bool = False


class ConnectIn(BaseModel):
    # Upload-Post needs a profile name to hang the social connections off.
    # Defaults to the saved one; only sent when creating the first.
    username: str = ""


def install(app, *, config, db, data_dir, publish_worker=None) -> None:
    """Attach the Upload-Post routes. `db` is the per-request StateDB factory."""

    data_path = Path(data_dir)

    def _guard(d):
        if not service.is_enabled(d):
            raise HTTPException(404, "Upload-Post publishing is not enabled")

    def _fail(e: Exception) -> HTTPException:
        message = e.message if isinstance(e, PublishError) else str(e)
        return HTTPException(400, redact(message)[:500])

    def _client():
        if not service.has_key(data_path):
            raise HTTPException(400, "No Upload-Post API key is set.")
        return service.make_client(data_path)

    # ---- status and settings --------------------------------------------

    @app.get("/uploadpost/status")
    def uploadpost_status():
        d = db()
        try:
            return service.status_payload(d, data_path)
        finally:
            d.close()

    @app.patch("/uploadpost/settings")
    def uploadpost_settings(body: SettingsIn):
        d = db()
        try:
            patch = {k: v for k, v in body.model_dump().items() if v is not None}
            service.save_settings(d, patch)
            return service.status_payload(d, data_path)
        finally:
            d.close()

    # ---- the key ---------------------------------------------------------

    @app.put("/uploadpost/key")
    def put_key(body: KeyIn):
        """Store the key, then prove it works before reporting success.

        Validating here rather than on first publish means a typo is caught
        while the user is still looking at the field they typed it into.
        """
        key = (body.api_key or "").strip()
        if not key:
            raise HTTPException(400, "Enter your Upload-Post API key.")

        service.save_key(data_path, key)
        d = db()
        try:
            try:
                account = service.make_client(data_path).validate_key()
            except PublishError as e:
                # Do not keep a key that does not work — leaving it stored
                # makes the settings card claim a connection there isn't one.
                service.wipe_key(data_path)
                raise _fail(e) from e
            return {
                **service.status_payload(d, data_path),
                "plan": account.get("plan") or "",
                "email": account.get("email") or "",
            }
        finally:
            d.close()

    @app.delete("/uploadpost/key")
    def delete_key():
        removed = service.wipe_key(data_path)
        d = db()
        try:
            return {"removed": removed, **service.status_payload(d, data_path)}
        finally:
            d.close()

    # ---- social accounts -------------------------------------------------

    @app.get("/uploadpost/profiles")
    def profiles():
        d = db()
        try:
            _guard(d)
            try:
                return {"profiles": _client().list_profiles()}
            except PublishError as e:
                raise _fail(e) from e
        finally:
            d.close()

    @app.get("/uploadpost/connections")
    def connections():
        """Which platforms this profile has linked.

        The app polls this after sending someone to the connection page, so
        the setup finishes by itself rather than asking them to come back and
        press something.
        """
        d = db()
        try:
            _guard(d)
            profile = service.load_settings(d).get("profile") or service.DEFAULT_PROFILE
            try:
                linked = _client().connected_platforms(profile)
            except PublishError as e:
                raise _fail(e) from e
            return {"profile": profile, "connected": linked}
        finally:
            d.close()

    @app.post("/uploadpost/connect")
    def connect(body: ConnectIn):
        """Hand back a hosted page for linking social accounts.

        The renderer opens this in the real browser. Clips Kitty never sees a
        social password and runs no OAuth of its own — Upload-Post owns those
        connections, which is the point of using it.
        """
        d = db()
        try:
            _guard(d)
            settings = service.load_settings(d)
            # Falls back to the default rather than refusing: asking a
            # creator to name a profile they will never see again is a step
            # that earns nothing.
            username = (
                body.username or settings.get("profile") or service.DEFAULT_PROFILE
            ).strip()
            client = _client()
            try:
                existing = {p.get("username") for p in client.list_profiles()}
                if username not in existing:
                    client.create_profile(username)
                url = client.connect_url(username)
            except PublishError as e:
                raise _fail(e) from e
            if not url:
                raise HTTPException(
                    400, "Upload-Post did not return a connection link. Try again."
                )
            service.save_settings(d, {"profile": username})
            # 48 hours, per Upload-Post. Worth telling the user so a link left
            # in a tab overnight is not a mystery when it stops working.
            return {"url": url, "expires_hours": 48, "profile": username}
        finally:
            d.close()

    # ---- publishing ------------------------------------------------------

    @app.post("/uploadpost/clips/{clip_id}/publish")
    def publish_clip(clip_id: int, body: PublishIn):
        """Send one clip to every selected platform in a single upload.

        One request, not one per platform: the bytes go up once and
        Upload-Post fans them out. Returns immediately with a request_id —
        their synchronous mode is cut off at 59 seconds, so the poll happens
        through /uploadpost/refresh.
        """
        from publish.uploadpost import (
            UploadPostPublisher,
            build_fields,
            missing_required,
            platform_overrides,
            schedule_fields,
            validate_schedule,
        )

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

            overrides = platform_overrides(body.overrides)

            # Facebook needs a page id, Pinterest a board id. Checked before
            # the upload, because their end reports it only after the video
            # has been sent — the user waits for a publish that could never
            # have worked.
            lacking = missing_required(body.platforms, dict(overrides))
            if lacking:
                raise HTTPException(
                    400,
                    f"These are needed before publishing: {', '.join(sorted(set(lacking)))}.",
                )

            try:
                when = validate_schedule(body.scheduled_date)
                timing = schedule_fields(
                    scheduled_date=when,
                    timezone=body.timezone,
                    add_to_queue=body.add_to_queue,
                )
            except PublishError as e:
                raise _fail(e) from e
            if not (clip["path"] and Path(clip["path"]).exists()):
                raise HTTPException(
                    400, "This clip has no rendered file yet. Apply your edits first."
                )

            thumbnail = None
            if body.thumbnail:
                thumbnail = Path(data_dir) / "thumbnails" / f"clip_{int(clip_id)}_chosen.jpg"
                if not thumbnail.exists():
                    raise HTTPException(
                        400, "No thumbnail has been chosen for this clip yet."
                    )

            settings = service.load_settings(d)
            description = body.description
            standing = (settings.get("common_description") or "").strip()
            if standing:
                description = f"{description}\n\n{standing}".strip()

            publisher = UploadPostPublisher(_client(), settings.get("profile") or "")
            fields = (
                build_fields(
                    title=body.title,
                    description=description,
                    tags=body.tags,
                    first_comment=body.first_comment or settings.get("first_comment") or "",
                )
                + overrides
                + timing
            )

            try:
                result = publisher.start(
                    Path(clip["path"]),
                    platforms=body.platforms,
                    fields=fields,
                    thumbnail=thumbnail,
                    # Stable per clip, so a retry after a timeout cannot post
                    # the same clip to the same places twice.
                    idempotency_key=f"clips-kitty-{clip_id}-{clip['created_at']}",
                )
            except PublishError as e:
                raise _fail(e) from e

            _record(d, clip_id, clip, result)
            service.save_settings(d, {"platforms": body.platforms})
            return _publish_payload(d, clip_id, result.request_id)
        finally:
            d.close()

    @app.post("/uploadpost/batch")
    def publish_batch(body: BatchIn):
        """Publish several clips, each to every selected platform.

        One fan-out per clip, because each clip is different media — there is
        no endpoint that takes several videos at once, and pretending
        otherwise would just hide the loop somewhere worse.

        What this does NOT do is fire them all at the same moment. Posting
        twelve clips simultaneously looks like spam to every platform
        involved and burns the per-account daily caps in one go. `every_hours`
        spreads them using Upload-Post's own scheduler, so the spacing is
        their job rather than a timer inside a desktop app that may be shut.
        """
        from datetime import datetime, timedelta
        from datetime import timezone as tz

        from publish.uploadpost import (
            UploadPostPublisher,
            build_fields,
            missing_required,
            platform_overrides,
            schedule_fields,
            validate_schedule,
        )

        if not body.clip_ids:
            raise HTTPException(400, "No clips were chosen.")
        if not body.platforms:
            raise HTTPException(400, "Pick at least one platform.")

        d = db()
        try:
            _guard(d)
            overrides = platform_overrides(body.overrides)
            lacking = missing_required(body.platforms, dict(overrides))
            if lacking:
                raise HTTPException(
                    400,
                    f"These are needed before publishing: {', '.join(sorted(set(lacking)))}.",
                )

            settings = service.load_settings(d)
            publisher = UploadPostPublisher(_client(), settings.get("profile") or "")
            standing = (settings.get("common_description") or "").strip()

            # The first slot. Spacing runs from here, not from "now", so a
            # batch queued at 2am can still be told to start at 9.
            start = datetime.now(tz.utc)
            if body.start_at:
                try:
                    validate_schedule(body.start_at)
                    start = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
                except (PublishError, ValueError) as e:
                    raise _fail(
                        e if isinstance(e, PublishError)
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

                title = (clip["title"] or clip["hook"] or f"Clip {clip_id}").strip()
                description = (clip["description"] or "").strip()
                if standing:
                    description = f"{description}\n\n{standing}".strip()

                timing: list = []
                if body.add_to_queue:
                    timing = schedule_fields(add_to_queue=True)
                elif body.every_hours or body.start_at:
                    at = start + timedelta(hours=body.every_hours * index)
                    timing = schedule_fields(
                        scheduled_date=at.isoformat(), timezone=body.timezone
                    )

                fields = (
                    build_fields(
                        title=title,
                        description=description,
                        # Stored as a JSON list, not a space-separated string.
                        tags=_tags_of(clip),
                        first_comment=settings.get("first_comment") or "",
                    )
                    + overrides
                    + timing
                )

                try:
                    result = publisher.start(
                        Path(clip["path"]),
                        platforms=body.platforms,
                        fields=fields,
                        idempotency_key=f"clips-kitty-{clip_id}-{clip['created_at']}",
                    )
                except PublishError as e:
                    # One clip's problem must not cost the rest of the batch,
                    # the same rule publish_plan_execute follows.
                    skipped.append({"clip_id": clip_id, "reason": e.message})
                    continue

                _record(d, clip_id, clip, result)
                started.append({"clip_id": clip_id, "request_id": result.request_id})

            service.save_settings(d, {"platforms": body.platforms})
            return {"started": started, "skipped": skipped}
        finally:
            d.close()

    @app.post("/uploadpost/refresh/{request_id}")
    def refresh(request_id: str):
        """Poll one fan-out and update every platform's row."""
        from publish.uploadpost import UploadPostPublisher

        d = db()
        try:
            _guard(d)
            rows = d.publishes_for_request(request_id)
            if not rows:
                raise HTTPException(404, "no such publish")
            clip_id = int(rows[0]["clip_id"])
            clip = d.get_clip(clip_id)

            settings = service.load_settings(d)
            publisher = UploadPostPublisher(_client(), settings.get("profile") or "")
            try:
                result = publisher.check(request_id)
            except PublishError as e:
                raise _fail(e) from e

            if result.outcomes:
                _record(d, clip_id, clip, result)
            return _publish_payload(d, clip_id, request_id)
        finally:
            d.close()

    @app.post("/uploadpost/retry/{request_id}")
    def retry(request_id: str):
        """Re-run only the platforms that failed.

        Through Upload-Post's own retry, which reuses the media it already
        holds. Publishing again from scratch would re-upload the clip and
        risk a duplicate on every platform that already succeeded.
        """
        from publish.uploadpost import UploadPostPublisher

        d = db()
        try:
            _guard(d)
            rows = d.publishes_for_request(request_id)
            if not rows:
                raise HTTPException(404, "no such publish")
            if not any(r["state"] == "failed" for r in rows):
                raise HTTPException(400, "Nothing failed on that publish.")

            clip_id = int(rows[0]["clip_id"])
            clip = d.get_clip(clip_id)
            settings = service.load_settings(d)
            publisher = UploadPostPublisher(_client(), settings.get("profile") or "")
            try:
                result = publisher.retry(request_id)
            except PublishError as e:
                raise _fail(e) from e

            if result.outcomes:
                _record(d, clip_id, clip, result)
            else:
                # Their retry can answer with an acknowledgement rather than
                # fresh per-platform detail. Put the failed rows back into
                # flight so the poller picks the real states up.
                for row in rows:
                    if row["state"] == "failed":
                        d.record_clip_publish(
                            clip_id, row["platform"], {"state": "queued", "error": ""}
                        )
            return _publish_payload(d, clip_id, request_id)
        finally:
            d.close()

    @app.get("/uploadpost/capabilities")
    def capabilities():
        """What each platform actually supports.

        Served from the same table the publish path uses, so the UI cannot
        drift into offering a control that would be dropped on the way out.
        """
        from publish.uploadpost import PLATFORM_FIELDS

        return {
            "platforms": {
                name: {
                    "description": bool(spec.get("description")),
                    "first_comment": bool(spec.get("first_comment")),
                    "ai_disclosure": bool(spec.get("ai_disclosure")),
                    "thumbnail": bool(spec.get("thumbnail")),
                    "requires": spec.get("requires") or {},
                    "extra": sorted((spec.get("extra") or {}).keys()),
                }
                for name, spec in PLATFORM_FIELDS.items()
            }
        }

    def _tags_of(clip) -> list[str]:
        """A clip's hashtags, which are stored as a JSON list in one column."""
        import json

        raw = clip["hashtags"] if "hashtags" in clip.keys() else ""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [str(t).lstrip("#") for t in parsed if str(t).strip()] if isinstance(parsed, list) else []

    def _record(d, clip_id: int, clip, result) -> None:
        """Write one row per destination, keeping each platform's own state."""
        for outcome in result.outcomes:
            d.record_clip_publish(
                clip_id,
                outcome.platform,
                {
                    "provider": "upload_post",
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

    def _publish_payload(d, clip_id: int, request_id: str) -> dict:
        rows = [dict(r) for r in d.clip_publishes(clip_id)]
        live = [r for r in rows if r.get("state") in ("queued", "processing")]
        return {
            "clip_id": clip_id,
            "request_id": request_id,
            "platforms": rows,
            # The UI stops polling on this rather than guessing from states.
            "done": not live,
        }

    # ---- history ---------------------------------------------------------

    @app.get("/uploadpost/clips/{clip_id}")
    def clip_publishes(clip_id: int):
        """Per-platform state for one clip."""
        d = db()
        try:
            _guard(d)
            return {"platforms": [dict(r) for r in d.clip_publishes(clip_id)]}
        finally:
            d.close()
