"""HTTP routes for publishing to YouTube.

Kept out of server/api.py deliberately. That file is 2,300 lines with every
route as a closure in one factory, and this feature is optional, self-contained
and removable — which stays true only if it is not threaded through there. It
is installed with one call from create_app().

Two rules hold everywhere below:

* When the feature is switched off, every route here 404s and /youtube/status
  returns `{"enabled": false}` and nothing else. A disabled feature should look
  absent, not disabled.
* No response, log line or error message may carry a token, a refresh token or
  a client secret. Errors go through server.feedback.redact() on the way out.
"""

import json
import subprocess
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server import youtube_service as service
from server.feedback import redact

# One consent attempt at a time, held here because it outlives the request
# that started it.
_flow = {"thread": None}


class YouTubeSettingsPatch(BaseModel):
    enabled: bool | None = None
    privacy: str | None = None
    category_id: str | None = None
    made_for_kids: bool | None = None
    playlists_enabled: bool | None = None
    notify_subscribers: bool | None = None
    region: str | None = None


class CredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class ConnectIn(BaseModel):
    playlists: bool = False
    # Connecting normally REPLACES nothing — each consent adds a channel. This
    # exists so the UI can be explicit about which it meant.
    add: bool = False


class DisconnectIn(BaseModel):
    channel_id: str | None = None


class DefaultAccountIn(BaseModel):
    channel_id: str


class RenderFirst(BaseModel):
    start: float | None = None
    end: float | None = None
    render_opts: dict | None = None


class PublishIn(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = []
    category_id: str = "22"
    privacy: str = "public"
    publish_at: str | None = None
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    embeddable: bool = True
    public_stats_viewable: bool = True
    license: str = "youtube"
    default_language: str | None = None
    notify_subscribers: bool = True
    playlist_id: str | None = None
    thumbnail: str | None = None
    channel_id: str | None = None
    render_first: RenderFirst | None = None


class ThumbnailIn(BaseModel):
    path: str | None = None
    t: float | None = None


# Where a just-granted token waits while we ask YouTube which channel it is
# for. Never the unqualified slot — that may already hold another channel.
PENDING = "pending"

PRIVACIES = ("public", "unlisted", "private")
LICENSES = ("youtube", "creativeCommon")


def install(app, *, config, db, data_dir, worker, publish_worker) -> None:
    """Attach the publishing routes. `db` is the per-request StateDB factory."""

    def _guard(d):
        """404 when the feature is off, so it looks absent rather than refused."""
        if not service.is_enabled(d):
            raise HTTPException(404, "YouTube publishing is not enabled")

    def _publisher(privacy: str = "private", channel_id: str | None = None):
        return service.make_publisher(config, data_dir, privacy, channel_id)

    def _default(d) -> str | None:
        return service.default_channel_id(d)

    def _fail(e: Exception) -> HTTPException:
        from publish.errors import PublishError

        message = e.message if isinstance(e, PublishError) else str(e)
        return HTTPException(400, redact(message)[:500])

    # ---- status and settings --------------------------------------------

    @app.get("/youtube/status")
    def youtube_status():
        d = db()
        try:
            return service.status_payload(d, config, data_dir)
        finally:
            d.close()

    @app.patch("/youtube/settings")
    def patch_youtube_settings(body: YouTubeSettingsPatch):
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        if "privacy" in patch and patch["privacy"] not in PRIVACIES:
            raise HTTPException(400, f"privacy must be one of {', '.join(PRIVACIES)}")
        d = db()
        try:
            saved = service.save_settings(d, patch)
            return {"settings": saved, "status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    # ---- credentials (bring your own Google Cloud project) ---------------

    @app.put("/youtube/credentials")
    def put_credentials(body: CredentialsIn):
        from core import secrets
        from publish.oauth import client_config, looks_like_desktop_client
        from publish.youtube_shorts import CLIENT_SECRET

        d = db()
        try:
            _guard(d)
        finally:
            d.close()

        built = client_config(body.client_id, body.client_secret)
        if not looks_like_desktop_client(built) or not body.client_secret.strip():
            raise HTTPException(400, "Both a client ID and a client secret are needed.")
        secrets.save(Path(data_dir), CLIENT_SECRET, built)
        # Never echo the secret back, not even masked beyond recognition.
        return {"ok": True, "client_id_tail": body.client_id.strip()[-12:]}

    @app.delete("/youtube/credentials")
    def delete_credentials():
        from core import secrets
        from publish.youtube_shorts import CLIENT_SECRET, TOKEN_SECRET, token_name_for

        secrets.wipe(Path(data_dir), CLIENT_SECRET)
        secrets.wipe(Path(data_dir), TOKEN_SECRET)
        d = db()
        try:
            # Every channel's token, not just the default one: the client they
            # were all issued against is gone, so none of them can refresh.
            for account in service.load_accounts(d):
                secrets.wipe(Path(data_dir), token_name_for(account["id"]))
                service.remove_account(d, account["id"])
            service.remember_channel(d, None)
        finally:
            d.close()
        return {"cleared": True}

    # ---- connect / disconnect -------------------------------------------

    @app.post("/youtube/connect")
    def start_connect(body: ConnectIn):
        from core import secrets
        from publish.oauth import ConnectFlow, require_client, scopes_for
        from publish.youtube_shorts import CLIENT_SECRET

        d = db()
        try:
            _guard(d)
        finally:
            d.close()

        existing = _flow["thread"]
        if existing is not None and existing.is_alive():
            return {"state": "waiting"}

        stored = secrets.load(Path(data_dir), CLIENT_SECRET)
        try:
            client = require_client(stored)
        except Exception as e:
            raise _fail(e) from e

        thread = ConnectFlow(client, scopes_for(body.playlists))
        _flow["thread"] = thread
        thread.start()
        return {"state": "waiting"}

    @app.get("/youtube/connect")
    def poll_connect():
        thread = _flow["thread"]
        if thread is None:
            return {"state": "idle"}
        if thread.state == "waiting":
            return {"state": "waiting"}
        if thread.state == "error":
            return {"state": "error", "error": redact(thread.error)[:300]}

        # Consent finished, but we do not yet know WHICH channel was picked,
        # and the token has to be filed under that channel's id. So it lands in
        # a scratch slot first — never the unqualified one, which may already
        # hold a different channel's token that this would clobber.
        from core import secrets
        from publish.youtube_shorts import token_name_for

        creds = thread.credentials
        token = json.loads(creds.to_json())
        secrets.save(Path(data_dir), token_name_for(PENDING), token)
        _flow["thread"] = None

        channel = None
        error = ""
        try:
            channel = _publisher(channel_id=PENDING).channel_info()
        except Exception as e:
            error = redact(str(getattr(e, "message", e)))[:300]

        d = db()
        try:
            if channel:
                secrets.save(Path(data_dir), token_name_for(channel["id"]), token)
                service.add_account(d, channel, list(getattr(creds, "scopes", []) or []))
            secrets.wipe(Path(data_dir), token_name_for(PENDING))
            return {
                "state": "done",
                "channel": channel,
                "error": error,
                "status": service.status_payload(d, config, data_dir),
            }
        finally:
            d.close()

    @app.post("/youtube/disconnect")
    def disconnect(body: DisconnectIn | None = None):
        """Disconnect one channel, or every channel when none is named."""
        from core import secrets
        from publish.oauth import revoke
        from publish.youtube_shorts import TOKEN_SECRET, token_name_for

        channel_id = body.channel_id if body else None
        d = db()
        try:
            accounts = service.load_accounts(d)
            targets = [a for a in accounts if a["id"] == channel_id] if channel_id else accounts

            for account in targets:
                name = (
                    TOKEN_SECRET if account.get("legacy_token") else token_name_for(account["id"])
                )
                stored = secrets.load(Path(data_dir), name) or {}
                revoke(stored.get("refresh_token") or stored.get("token") or "")
                secrets.wipe(Path(data_dir), name)
                service.remove_account(d, account["id"])

            if not channel_id:
                # Belt and braces: an install that predates the roster has a
                # token in the unqualified slot and nothing to enumerate.
                stored = secrets.load(Path(data_dir), TOKEN_SECRET) or {}
                revoke(stored.get("refresh_token") or stored.get("token") or "")
                secrets.wipe(Path(data_dir), TOKEN_SECRET)
                service.remember_channel(d, None)

            return {"disconnected": True, "status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    @app.patch("/youtube/default-account")
    def set_default_account(body: DefaultAccountIn):
        d = db()
        try:
            _guard(d)
            service.set_default_account(d, body.channel_id)
            return {"status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    # ---- reference data --------------------------------------------------

    @app.get("/youtube/categories")
    def categories(region: str = "US"):
        d = db()
        try:
            _guard(d)
            channel_id = _default(d)
        finally:
            d.close()
        try:
            return {"categories": _publisher(channel_id=channel_id).categories(region)}
        except Exception as e:
            raise _fail(e) from e

    @app.get("/youtube/playlists")
    def playlists():
        d = db()
        try:
            _guard(d)
            channel_id = _default(d)
        finally:
            d.close()
        try:
            return {"playlists": _publisher(channel_id=channel_id).playlists()}
        except Exception as e:
            raise _fail(e) from e

    # ---- publishing ------------------------------------------------------

    @app.get("/clips/{clip_id}/publish")
    def clip_publish_status(clip_id: int):
        d = db()
        try:
            _guard(d)
            row = d.get_upload(clip_id)
            active = d.active_publish_job_for_clip(clip_id)
            return {
                "upload": dict(row) if row else None,
                "job": dict(active) if active else None,
            }
        finally:
            d.close()

    @app.post("/clips/{clip_id}/publish")
    def publish_clip(clip_id: int, body: PublishIn):
        from publish.schedule import validate_publish_at

        d = db()
        try:
            _guard(d)
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            if d.active_publish_job_for_clip(clip_id):
                raise HTTPException(409, "This clip is already being published.")

            if not body.title.strip():
                raise HTTPException(400, "A title is required.")
            if body.privacy not in PRIVACIES:
                raise HTTPException(400, f"privacy must be one of {', '.join(PRIVACIES)}")
            if body.license not in LICENSES:
                raise HTTPException(400, f"license must be one of {', '.join(LICENSES)}")

            publish_at = None
            if body.publish_at:
                try:
                    publish_at = validate_publish_at(body.publish_at)
                except Exception as e:
                    raise HTTPException(400, str(getattr(e, "message", e))) from e

            thumbnail = None
            if body.thumbnail:
                thumbnail = _resolve_thumbnail(body.thumbnail)

            # Which channel this goes to. Named explicitly, or the default.
            channel_id = body.channel_id or service.default_channel_id(d)
            if body.channel_id:
                known = {a["id"] for a in service.load_accounts(d)}
                if body.channel_id not in known:
                    raise HTTPException(
                        400, "That YouTube channel is not connected. Reconnect it in Settings."
                    )

            # Chain onto the EXISTING render path rather than re-implementing
            # one: same job type, same worker, same code that Apply edits runs.
            after_job_id = 0
            if body.render_first is not None:
                payload = {"clip_id": clip_id}
                if body.render_first.start is not None:
                    payload["start"] = body.render_first.start
                if body.render_first.end is not None:
                    payload["end"] = body.render_first.end
                if body.render_first.render_opts:
                    payload["render_opts"] = body.render_first.render_opts
                after_job_id = d.add_job("render", json.dumps(payload))
            elif not (clip["path"] and Path(clip["path"]).exists()):
                raise HTTPException(
                    400, "This clip has no rendered file yet. Apply your edits first."
                )

            request = {
                "title": body.title,
                "description": body.description,
                "tags": body.tags,
                "category_id": body.category_id,
                "privacy": body.privacy,
                "publish_at": publish_at,
                "made_for_kids": body.made_for_kids,
                "contains_synthetic_media": body.contains_synthetic_media,
                "embeddable": body.embeddable,
                "public_stats_viewable": body.public_stats_viewable,
                "license": body.license,
                "default_language": body.default_language,
                "notify_subscribers": body.notify_subscribers,
                "playlist_id": body.playlist_id,
                "thumbnail": str(thumbnail) if thumbnail else None,
            }
            # Not part of PublishRequest — it selects which token to publish
            # with, rather than describing the video.
            request["channel_id"] = channel_id
            job_id = d.add_publish_job(
                clip_id,
                json.dumps(request),
                video_id=clip["video_id"],
                start_s=clip["start_s"],
                end_s=clip["end_s"],
                after_job_id=after_job_id,
            )
        finally:
            d.close()

        if after_job_id:
            worker.notify()
        publish_worker.notify()
        return {"publish_job_id": job_id, "render_job_id": after_job_id or None}

    @app.post("/publish/{job_id}/cancel")
    def cancel_publish(job_id: int):
        d = db()
        try:
            _guard(d)
            job = d.get_publish_job(job_id)
            if job is None:
                raise HTTPException(404, "no such publish job")
            if job["status"] == "queued":
                d.finish_publish_job(job_id, "cancelled", error="Cancelled.")
                return {"cancelled": True}
        finally:
            d.close()
        publish_worker.cancel(job_id)
        return {"cancelling": True}

    @app.get("/youtube/uploads")
    def uploads(limit: int = 50):
        d = db()
        try:
            _guard(d)
            return {"uploads": [dict(r) for r in d.recent_uploads(max(1, min(limit, 200)))]}
        finally:
            d.close()

    # ---- thumbnails ------------------------------------------------------

    @app.get("/clips/{clip_id}/frame")
    def clip_frame(clip_id: int, t: float = 0.0):
        """One JPEG frame from a clip, for the thumbnail picker."""
        d = db()
        try:
            _guard(d)
            clip = d.get_clip(clip_id)
        finally:
            d.close()
        if clip is None or not clip["path"]:
            raise HTTPException(404, "no such clip")
        source = Path(clip["path"])
        if not source.exists():
            raise HTTPException(404, "this clip has no rendered file")

        target = Path(data_dir) / "thumbnails" / f"clip_{clip_id}_{int(max(0.0, t) * 1000)}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            _extract_frame(source, max(0.0, t), target)
        return FileResponse(str(target), media_type="image/jpeg")

    @app.post("/clips/{clip_id}/thumbnail")
    def choose_thumbnail(clip_id: int, body: ThumbnailIn):
        """Pick a thumbnail: either a local image, or a frame from the clip."""
        d = db()
        try:
            _guard(d)
            clip = d.get_clip(clip_id)
        finally:
            d.close()
        if clip is None:
            raise HTTPException(404, "no such clip")

        target = Path(data_dir) / "thumbnails" / f"clip_{clip_id}_chosen.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)

        if body.path:
            chosen = _resolve_thumbnail(body.path)
            if chosen.stat().st_size > 2 * 1024 * 1024:
                raise HTTPException(400, "YouTube's limit for a thumbnail is 2 MB.")
            target.write_bytes(chosen.read_bytes())
        elif body.t is not None:
            source = Path(clip["path"] or "")
            if not source.exists():
                raise HTTPException(404, "this clip has no rendered file")
            _extract_frame(source, max(0.0, body.t), target)
        else:
            raise HTTPException(400, "Give either an image path or a time in the clip.")
        return {"thumbnail": str(target)}


def _resolve_thumbnail(raw: str) -> Path:
    """Validate a user-supplied image path.

    The same shape as core/paths.py::picked_file — an absolute path the user
    chose in a native dialog, checked for a real image extension so nothing
    else can be handed to the uploader.
    """
    path = Path(raw)
    if not path.is_absolute() or not path.exists() or not path.is_file():
        raise HTTPException(400, "That image could not be found.")
    if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(400, "YouTube accepts JPEG and PNG thumbnails.")
    return path


def _extract_frame(source: Path, at: float, target: Path) -> None:
    from core.binaries import ffmpeg

    result = subprocess.run(
        [
            ffmpeg(), "-y", "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", str(source),
            "-frames:v", "1",
            # YouTube wants 16:9 and at least 1280 wide. A vertical Short
            # letterboxed into that is what Studio shows too.
            "-vf", "scale=1280:-2",
            "-q:v", "3",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.exists():
        raise HTTPException(500, "Could not read a frame from this clip.")
