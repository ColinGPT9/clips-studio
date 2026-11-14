"""The new editor's media (editor suite step 6): the routes over
video_editor/media/. Engine only: no screen uses them yet, and nothing here
runs unless one of these routes is called.

Every route is internal (server/api_stability.py lists none of them).

- **Routes that change something** (ask for a proxy, cancel one, import a
  file, clear the cache) need the session header and an Origin of this PC's
  own, exactly as the project routes do (projects_api.session_guard).
- **Routes that read** are fetched by <video> and <img> elements too, which
  can't send a header. They take either the session header or the media's
  own token in the address (`?t=`, an HMAC of the media id under the
  session secret, which GET /media/{id}/info gives in its `urls`), and an
  Origin, when there is one, of this PC's own. A page on another origin
  can't send the header, can't compute a token without the secret (new at
  every start of the app), and is refused by its Origin whenever it sends
  one; an <img> or <video> on that page sends no Origin, and without a token
  gets 403 like everything else.

docs/editor-suite/api/step6-media.md has every route, its answers and the
formats.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import quote

import anyio
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from plugins import session
from server.projects_api import local_origin, session_guard
from video_editor.media import cache, fileio, importing, peaks, probe, proxy, resolver, thumbs

INLINE_IMPORT_BYTES = 64 * 1024 * 1024   # larger imports run on the editor lane, with progress
WAIT_MAX = 60.0


def media_token(secret: str, media_id: str) -> str:
    """The token a read route takes in `?t=` for this media id."""
    return hmac.new(secret.encode(), f"clips-kitty-media\0{media_id}".encode(), hashlib.sha256).hexdigest()[:32]


class SourceShrank(OSError):
    """The file got shorter while it was sent: the answer can't reach the
    Content-Length it announced, so the connection is dropped (the server
    closes it on an exception after the answer started) and the <video>
    element asks again."""


class FileStream(Response):
    """A byte range of an open file (fileio.SharedFile), sent in chunks. The
    file is closed as soon as the answer ends, or the client goes away."""

    def __init__(self, opened: fileio.SharedFile, start: int, length: int, status: int, headers: dict,
                 on_close=None):
        super().__init__(status_code=status, headers=headers)
        self.file, self.start, self.length = opened, start, length
        self._on_close = on_close

    def _close(self) -> None:
        self.file.close()
        if self._on_close is not None:
            self._on_close()
            self._on_close = None

    async def __call__(self, scope, receive, send) -> None:
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            if scope.get("method") == "HEAD" or self.length <= 0:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            pos, end = self.start, self.start + self.length
            short = False
            async with anyio.create_task_group() as group:
                async def watch() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            break
                    group.cancel_scope.cancel()

                group.start_soon(watch)
                while pos < end:
                    chunk = await anyio.to_thread.run_sync(self.file.read_at, pos, min(fileio.CHUNK, end - pos))
                    if not chunk:
                        short = True
                        break
                    pos += len(chunk)
                    await send({"type": "http.response.body", "body": chunk, "more_body": pos < end})
                group.cancel_scope.cancel()
            if short:
                # The file got shorter while it was sent. Ending the body here
                # would fall short of the Content-Length already announced; the
                # connection is dropped instead, so the client asks again.
                raise SourceShrank(f"{Path(self.file.path).name} got shorter while it was sent "
                                   f"({pos - self.start} of {self.length} bytes)")
        finally:
            self._close()


def serve_file(request: Request, path: Path, *, folder: Path | None = None) -> Response:
    """The file at `path`, as the Range, If-Range and If-None-Match headers
    ask, with a strong ETag from its size and modification time. `folder`, a
    cache folder, is held (cache.hold) while the answer streams."""
    try:
        opened = fileio.open_shared(path)
    except FileNotFoundError:
        raise HTTPException(404, "That file is missing.") from None
    except OSError as e:
        raise HTTPException(404, f"That file can't be opened ({e.strerror or e}).") from None
    stack = contextlib.ExitStack()
    if folder is not None:
        stack.enter_context(cache.hold(folder))
    try:
        tag = fileio.etag(opened.size, opened.mtime_ns)
        modified = fileio.last_modified(opened.mtime_ns)
        headers = {"Accept-Ranges": "bytes", "ETag": tag, "Last-Modified": modified, "Cache-Control": "no-cache",
                   "Content-Type": fileio.content_type(path)}
        if fileio.if_none_match(request.headers.get("if-none-match"), tag):
            opened.close()
            stack.close()
            return Response(status_code=304, headers={k: v for k, v in headers.items() if k != "Content-Type"})
        wanted = None
        if fileio.if_range(request.headers.get("if-range"), tag, modified):
            try:
                wanted = fileio.parse_range(request.headers.get("range"), opened.size)
            except fileio.Unsatisfiable:
                size = opened.size
                opened.close()
                stack.close()
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes",
                                                          "ETag": tag})
        if wanted is None:
            start, length, status = 0, opened.size, 200
        else:
            start, length, status = wanted[0], wanted[1] - wanted[0] + 1, 206
            headers["Content-Range"] = f"bytes {wanted[0]}-{wanted[1]}/{opened.size}"
        headers["Content-Length"] = str(length)
        return FileStream(opened, start, length, status, headers, on_close=stack.close)
    except BaseException:
        opened.close()
        stack.close()
        raise


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str = ""


def install(app, *, db, data_dir: Path, session_secret: str, lane, config: dict | None = None) -> None:
    data_dir = Path(data_dir)
    changing = [Depends(session_guard(session_secret, data_dir,
                                      "The editor's media can only be changed from the Clips Kitty app on this PC."))]

    def read_guard(request: Request, media_id: str = "") -> None:
        if not local_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "The editor's media can only be opened from the Clips Kitty app on this PC.")
        if session.matches(session_secret, request.headers.get(session.HEADER)):
            return
        given = request.query_params.get("t") or ""
        if media_id and given and hmac.compare_digest(given, media_token(session_secret, media_id)):
            return
        raise HTTPException(403, f"This needs the {session.HEADER} header, or the address GET "
                                 "/media/{id}/info gives for this media.")

    def session_read(request: Request) -> None:
        read_guard(request, "")

    reading = [Depends(read_guard)]
    session_only = [Depends(session_read)]

    def cap() -> int:
        return cache.cap_bytes(config)

    def sent(payload, status: int = 200) -> Response:
        return Response(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), status_code=status,
                        media_type="application/json")

    def resolved(media_id: str) -> resolver.Media:
        d = db()
        try:
            return resolver.resolve(d.conn, data_dir, media_id)
        except resolver.NotFound as e:
            raise HTTPException(404, str(e)) from None
        finally:
            d.close()

    def described(media: resolver.Media) -> tuple[Path, dict]:
        """(its cache folder, what it is), probed once per source."""
        try:
            folder = cache.folder_for(data_dir, media)
        except OSError as e:
            raise HTTPException(404, f"{media.name} can't be read ({e.strerror or e}).") from None
        known = cache.read_json(folder / cache.INFO)
        if isinstance(known, dict) and known.get("version") == 1:
            return folder, known["info"]
        try:
            info = probe.info(media.path)
        except probe.ProbeError as e:
            raise HTTPException(422, f"{media.name} can't be read as media: {e}") from None
        cache.write_json(folder / cache.INFO, {"version": 1, "info": info}, once=True)
        return folder, info

    def urls(media_id: str) -> dict:
        base = f"/media/{quote(media_id, safe=':')}"
        t = f"?t={media_token(session_secret, media_id)}"
        return {"source": f"/media/source/{quote(media_id, safe=':')}{t}", "proxy": f"{base}/proxy{t}",
                "peaks": f"{base}/peaks{t}", "thumbs": f"{base}/thumbs{t}"}

    def state(folder: Path, product: str) -> dict:
        """Where a product is: ready, queued, running, failed, cancelled or none."""
        if (folder / product).exists():
            return {"state": "ready"}
        job = lane.find(f"{product}:{folder.name}")
        if job is None:
            return {"state": "none"}
        out = {"state": job["status"] if job["status"] != "done" else "none", "job_id": job["job_id"],
               "fraction": job["fraction"]}
        if job["error"]:
            out["error"] = job["error"]
        return out

    def finished(_job: dict) -> None:
        cache.enforce_cap(data_dir, cap())

    def ask(kind: str, product: str, folder: Path, work, *, again: bool = True) -> dict:
        """Queue the work for a product unless it is there or on its way.
        A failed one is tried again only when `again`."""
        current = state(folder, product)
        if current["state"] in ("ready", "queued", "running") or (current["state"] == "failed" and not again):
            return current
        lane.submit_task(kind, f"{product}:{folder.name}", work, name=product, on_done=finished)
        return state(folder, product)

    def waited(folder: Path, product: str, seconds: float) -> dict:
        deadline = time.monotonic() + max(0.0, min(WAIT_MAX, seconds))
        current = state(folder, product)
        while current["state"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
            current = state(folder, product)
        return current

    # ---- the work, on the editor lane ---------------------------------------------------------

    def proxy_work(media: resolver.Media, folder: Path, info: dict):
        def work(ctx) -> dict:
            from core.binaries import ffmpeg

            target = folder / proxy.name_for(info)
            with cache.hold(folder), cache.writing(target) as tmp:
                code, err = ctx.run(proxy.command(ffmpeg(), media.path, tmp, info),
                                    duration=info["duration_us"] / 1e6)
                ctx.check()
                if code != 0:
                    last = err.strip().splitlines()[-1] if err.strip() else f"exit code {code}"
                    raise RuntimeError(f"FFmpeg couldn't make the proxy: {last}")
            return {"file": target.name, "size": target.stat().st_size}

        return work

    def peaks_work(media: resolver.Media, folder: Path, info: dict):
        def work(ctx) -> dict:
            with cache.hold(folder):
                made = peaks.compute(media.path, started=ctx.started, on_progress=ctx.progress,
                                     duration_us=info["duration_us"])
                ctx.check()
                with cache.writing(folder / "peaks.bin") as tmp:
                    tmp.write_bytes(made.to_bytes())
            return {"duration_us": made.duration_us}

        return work

    def thumbs_work(media: resolver.Media, folder: Path, info: dict):
        def work(ctx) -> dict:
            with cache.hold(folder), cache.writing(folder / "thumbs") as tmp:
                index = thumbs.make(media.path, tmp, info=info, started=ctx.started, on_progress=ctx.progress,
                                    check_cancel=ctx.check)
                (tmp / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
            return {"count": index["count"]}

        return work

    # ---- reading ------------------------------------------------------------------------------

    @app.head("/media/source/{media_id}", dependencies=reading, include_in_schema=False)
    @app.get("/media/source/{media_id}", dependencies=reading)
    def media_source(media_id: str, request: Request):
        """The media's own file, in byte ranges (Range, If-Range, If-None-Match,
        HEAD). On Windows it stays free to be renamed, replaced or deleted
        while it streams."""
        return serve_file(request, resolved(media_id).path)

    @app.get("/media/{media_id}/info", dependencies=reading)
    def media_info(media_id: str):
        """What the media is, whether it needs a proxy and why, where its
        proxy, peaks and thumbnails are, and the addresses (with the token)
        that <video> and <img> elements use."""
        media = resolved(media_id)
        folder, info = described(media)
        reasons = probe.proxy_reasons(info)
        made = state(folder, proxy.name_for(info)) if info["kind"] != "image" else {"state": "none"}
        if made["state"] in ("ready", "queued", "running") and not reasons:
            reasons = ["a proxy was asked for"]
        return sent({"id": media_id, "ref": media.ref, "name": media.name, **info,
                     "fingerprint": folder.name, "needs_proxy": bool(reasons) and info["kind"] != "image",
                     "proxy_reasons": reasons, "proxy": made,
                     "peaks": state(folder, "peaks.bin") if info["has_audio"] else {"state": "none"},
                     "thumbs": state(folder, "thumbs") if info["kind"] != "audio" else {"state": "none"},
                     "urls": urls(media_id), "token": media_token(session_secret, media_id)})

    @app.head("/media/{media_id}/proxy", dependencies=reading, include_in_schema=False)
    @app.get("/media/{media_id}/proxy", dependencies=reading)
    def media_proxy(media_id: str, request: Request, state_only: bool = False):
        """The proxy file when it is ready (in byte ranges, like the source);
        otherwise where it is: 202 while it is being made, 404 when none was
        asked for, 500 when making it failed. `?state_only=true` always
        answers where it is, as JSON."""
        media = resolved(media_id)
        folder, info = described(media)
        product = proxy.name_for(info)
        current = state(folder, product)
        if current["state"] == "ready" and not state_only:
            cache.touch(folder / product)
            return serve_file(request, folder / product, folder=folder)
        status = {"ready": 200, "queued": 202, "running": 202, "failed": 500}.get(current["state"], 404)
        if state_only:
            status = 200
        if current["state"] == "none":
            current["message"] = "No proxy has been asked for. POST /media/{id}/proxy makes one."
        return sent(current, status)

    @app.get("/media/{media_id}/peaks", dependencies=reading)
    def media_peaks(media_id: str, per_second: int = 64, start_us: int = 0, end_us: int | None = None,
                    format: str = "bin", wait: float = 0.0, retry: bool = False):
        """The waveform: a min/max pyramid at `per_second` bins a second
        (rounded down to 512, 256, ..., 1) and every coarser level, for
        start_us..end_us. Made once, on the editor lane, the first time it is
        asked for: 202 with where it is until then (`wait` waits up to that
        many seconds, at most 60). When making it failed, 500 with why, and
        it is tried again only with `retry=true`."""
        if not 1 <= per_second <= peaks.BASE_RATE:
            raise HTTPException(400, f"per_second is 1 to {peaks.BASE_RATE}.")
        if format not in ("bin", "json"):
            raise HTTPException(400, "format is bin or json.")
        if start_us < 0 or (end_us is not None and end_us < start_us):
            raise HTTPException(400, "start_us and end_us are times in the media, start before end.")
        media = resolved(media_id)
        folder, info = described(media)
        if not info["has_audio"]:
            raise HTTPException(404, f"{media.name} has no sound, so it has no waveform.")
        current = ask("peaks", "peaks.bin", folder, peaks_work(media, folder, info), again=retry)
        if current["state"] in ("queued", "running") and wait > 0:
            current = waited(folder, "peaks.bin", wait)
        if current["state"] != "ready":
            return sent(current, 500 if current["state"] == "failed" else 202)
        cache.touch(folder / "peaks.bin")
        with cache.hold(folder):
            try:
                stored = peaks.Peaks.from_bytes((folder / "peaks.bin").read_bytes())
            except (OSError, ValueError) as e:
                raise HTTPException(404, f"The waveform went missing ({e}). Ask again.") from None
        chosen = stored.select(per_second, start_us, end_us)
        if format == "json":
            return sent(chosen.to_json())
        return Response(chosen.to_bytes(), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/media/{media_id}/thumbs", dependencies=reading)
    def media_thumbs(media_id: str, wait: float = 0.0, retry: bool = False):
        """The thumbnail index (video_editor/media/thumbs.py), each sheet with
        its address. Made once, on the editor lane, the first time it is
        asked for: 202 with where it is until then; `wait` and `retry` as
        for the peaks."""
        media = resolved(media_id)
        folder, info = described(media)
        if info["kind"] == "audio":
            raise HTTPException(404, f"{media.name} has no picture, so it has no thumbnails.")
        current = ask("thumbs", "thumbs", folder, thumbs_work(media, folder, info), again=retry)
        if current["state"] in ("queued", "running") and wait > 0:
            current = waited(folder, "thumbs", wait)
        if current["state"] != "ready":
            return sent(current, 500 if current["state"] == "failed" else 202)
        cache.touch(folder / "thumbs")
        index = cache.read_json(folder / "thumbs" / "index.json")
        if not isinstance(index, dict):
            raise HTTPException(404, "The thumbnails went missing. Ask again.")
        t = f"?t={media_token(session_secret, media_id)}"
        for sheet in index["sheets"]:
            sheet["url"] = f"/media/{quote(media_id, safe=':')}/thumbs/{sheet['index']}{t}"
        return sent(index)

    @app.head("/media/{media_id}/thumbs/{sheet}", dependencies=reading, include_in_schema=False)
    @app.get("/media/{media_id}/thumbs/{sheet}", dependencies=reading)
    def media_thumb_sheet(media_id: str, sheet: int, request: Request):
        """One thumbnail sheet (JPEG)."""
        media = resolved(media_id)
        folder, _info = described(media)
        index = cache.read_json(folder / "thumbs" / "index.json")
        sheets = index.get("sheets") if isinstance(index, dict) else None
        if not sheets or not 0 <= sheet < len(sheets):
            raise HTTPException(404, "There is no such thumbnail sheet. GET /media/{id}/thumbs first.")
        return serve_file(request, folder / "thumbs" / Path(sheets[sheet]["file"]).name, folder=folder)

    @app.get("/media/cache/size", dependencies=session_only)
    def media_cache_size():
        """How big the editor cache is, and its cap."""
        return {**cache.survey(data_dir), "cap_bytes": cap()}

    # ---- changing -----------------------------------------------------------------------------

    @app.post("/media/{media_id}/proxy", dependencies=changing)
    def ask_proxy(media_id: str):
        """Ask for a proxy (540p H.264, a keyframe every half second, AAC),
        made once on the editor lane. 200 when it is ready, 202 while it is
        being made."""
        media = resolved(media_id)
        folder, info = described(media)
        if info["kind"] == "image":
            raise HTTPException(400, f"{media.name} is a picture: it needs no proxy.")
        current = ask("proxy", proxy.name_for(info), folder, proxy_work(media, folder, info))
        return sent(current, 200 if current["state"] == "ready" else 202)

    @app.post("/media/{media_id}/proxy/cancel", dependencies=changing)
    def cancel_proxy(media_id: str):
        """Stop making the proxy: a waiting one is dropped, a running one's
        FFmpeg is stopped and its half-made file removed."""
        media = resolved(media_id)
        folder, info = described(media)
        current = state(folder, proxy.name_for(info))
        if current["state"] in ("queued", "running"):
            lane.cancel(current["job_id"], kind=None)
        return sent(state(folder, proxy.name_for(info)))

    @app.post("/media/import", dependencies=changing)
    def import_media(body: ImportIn):
        """Import a file from this PC for editing: checked with FFprobe and
        copied into the project media by its sha256. No AI run. A file over
        64 MB is copied on the editor lane: 202 with the job, whose progress
        GET /media/import/{job_id} and the media_job events give."""
        try:
            path, st = importing.check_path(body.path)
            info = importing.facts_of(path)
        except importing.Refused as e:
            raise HTTPException(e.status, str(e)) from None
        if st.st_size <= INLINE_IMPORT_BYTES:
            d = db()
            try:
                return sent(importing.import_file(d.conn, data_dir, path, info, name=body.name))
            except importing.Refused as e:
                raise HTTPException(e.status, str(e)) from None
            finally:
                d.close()

        def work(ctx) -> dict:
            def progress(done: int, total: int) -> None:
                ctx.check()
                ctx.progress(done / total if total else 0.0)

            d = db()
            try:
                return importing.import_file(d.conn, data_dir, path, info, name=body.name, progress=progress)
            finally:
                d.close()

        job = lane.submit_task("import", f"import:{path}", work, name=path.name)
        return sent({"job": job}, 202)

    @app.get("/media/import/{job_id}", dependencies=session_only)
    def import_state(job_id: int):
        """A large import's job: its status, its fraction and, once done, the
        asset in `result`."""
        job = lane.get(job_id, kind="import")
        if job is None:
            raise HTTPException(404, "There is no such import.")
        return job

    @app.post("/media/cache/clear", dependencies=changing)
    def clear_media_cache():
        """The "Clear editor cache" button: every proxy, waveform and
        thumbnail sheet goes, but those a job or a stream is using right now.
        They are made again when they are next needed."""
        return cache.clear(data_dir)
