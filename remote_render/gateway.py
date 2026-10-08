"""The worker-facing server on the main PC.

A separate server from the app's own API (127.0.0.1:8765, which has no
authentication and never leaves this PC). This one listens on the network
for render workers only, over HTTPS (tls.py), and every route but pairing
needs a worker's own credential. It serves structured jobs, the clip pieces
and branding files those jobs name, and takes results back; nothing here
runs a command or reads a path a worker chose.
"""

import json
import os
import re
import shutil
import socket
import threading
import time
from contextlib import suppress
from pathlib import Path

from remote_render import piece, protocol, tls
from remote_render.queue import RenderQueue

JOB_ID = re.compile(r"[0-9a-f]{32}")
ASSET = re.compile(r"[0-9A-Za-z_-]{1,80}\.(png|jpg|jpeg|webp)")
MAX_RESULT = 20 * 1024 ** 3        # a clip is far smaller; this only stops a runaway upload
SWEEP_EVERY = 10.0


def _no(status: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status, detail)


def _inside(folder: Path, name: str) -> Path:
    """`folder`/`name`, refused unless it stays inside `folder`: the name
    comes from a request (a job id, an asset's file name), so nothing it
    could contain reaches outside the folder."""
    base = os.path.normpath(folder)
    target = os.path.normpath(os.path.join(base, name))
    if not target.startswith(base + os.sep):
        raise _no(400, "bad name")
    return Path(target)


def create_app(queue: RenderQueue, data_dir: Path):
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, Response

    app = FastAPI(title="Clips Kitty render gateway", docs_url=None, redoc_url=None, openapi_url=None)
    results = Path(data_dir) / "remote_render" / "results"
    uploads = Path(data_dir) / "remote_render" / "uploads"
    assets_dir = Path(data_dir) / "branding" / "assets"

    def worker_of(request: Request) -> str:
        wid = request.headers.get("x-worker-id", "")
        auth = request.headers.get("authorization", "")
        secret = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not wid or not secret or not queue.authenticate(wid, secret):
            raise _no(401, "not a paired worker")
        return wid

    def job_of(job_id: str, wid: str) -> dict:
        if not JOB_ID.fullmatch(job_id):
            raise _no(400, "bad job id")
        row = queue.owned(job_id, wid)
        if row is None:
            raise _no(409, "not your job, or no longer running")
        return row

    @app.post("/v1/pair")
    async def pair(request: Request):
        body = await request.json()
        caps = body.get("caps") or {}
        if int(caps.get("protocol") or 0) != protocol.PROTOCOL:
            raise _no(409, "This render worker is a different version of Clips Kitty. Update both to the same version.")
        got = queue.redeem(str(body.get("code") or ""), str(body.get("name") or caps.get("name") or ""), caps)
        if got is None:
            raise _no(403, "That pairing code is wrong or has expired. Make a new one on the main PC.")
        worker_id, secret = got
        return {"worker_id": worker_id, "secret": secret, "protocol": protocol.PROTOCOL}

    @app.post("/v1/heartbeat")
    async def heartbeat(request: Request):
        wid = worker_of(request)
        body = await request.json()
        caps = body.get("caps") or {}
        if caps.get("protocol") is not None and int(caps.get("protocol") or 0) != protocol.PROTOCOL:
            raise _no(409, "worker update required")
        out = queue.heartbeat(wid, caps, bool(body.get("draining")), int(body.get("max_jobs") or 1),
                              list(body.get("running") or []))
        return {**out, "protocol": protocol.PROTOCOL}

    @app.post("/v1/claim")
    def claim(request: Request):
        wid = worker_of(request)
        job = queue.claim(wid)
        if job is None:
            return Response(status_code=204)
        return job

    @app.get("/v1/jobs/{job_id}/piece")
    def get_piece(job_id: str, request: Request):
        wid = worker_of(request)
        row = job_of(job_id, wid)
        # FileResponse answers Range requests, so an interrupted download
        # carries on from where it stopped.
        return FileResponse(row["piece_path"], media_type="video/mp4")

    @app.get("/v1/jobs/{job_id}/assets/{name}")
    def get_asset(job_id: str, name: str, request: Request):
        wid = worker_of(request)
        row = job_of(job_id, wid)
        if not ASSET.fullmatch(name) or name not in json.loads(row["assets"] or "{}"):
            raise _no(404, "not an asset of this job")
        path = _inside(assets_dir, name)
        if not path.is_file():
            raise _no(404, "asset missing on the main PC")
        return FileResponse(path)

    @app.post("/v1/jobs/{job_id}/progress")
    async def progress(job_id: str, request: Request):
        wid = worker_of(request)
        job_of(job_id, wid)
        body = await request.json()
        ok = queue.progress(job_id, wid, str(body.get("stage") or ""), float(body.get("progress") or 0))
        return {"ok": ok}

    @app.get("/v1/jobs/{job_id}/result")
    def result_status(job_id: str, request: Request):
        wid = worker_of(request)
        job_of(job_id, wid)
        part = _inside(uploads, f"{job_id}.part")
        return {"received": part.stat().st_size if part.exists() else 0}

    @app.put("/v1/jobs/{job_id}/result")
    async def result_chunk(job_id: str, offset: int, request: Request):
        """One chunk of the finished clip, appended at `offset`. A chunk that
        doesn't start where the file ends is refused with what was received,
        so the worker resumes from there instead of corrupting the file."""
        wid = worker_of(request)
        job_of(job_id, wid)
        uploads.mkdir(parents=True, exist_ok=True)
        part = _inside(uploads, f"{job_id}.part")
        have = part.stat().st_size if part.exists() else 0
        if offset != have:
            raise _no(409, json.dumps({"received": have}))
        written = have
        with open(part, "ab") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_RESULT:
                    raise _no(413, "result too large")
                f.write(chunk)
        return {"received": written}

    @app.post("/v1/jobs/{job_id}/complete")
    async def complete(job_id: str, request: Request):
        """Accept the clip only if it is whole and readable: the same size and
        hash the worker computed, and FFmpeg can read a sensible duration."""
        wid = worker_of(request)
        row = job_of(job_id, wid)
        body = await request.json()
        part = _inside(uploads, f"{job_id}.part")
        if not part.exists():
            raise _no(409, json.dumps({"received": 0}))
        size = part.stat().st_size
        problem = ""
        if size != int(body.get("size") or -1):
            problem = f"size {size} != {body.get('size')}"
        elif piece.sha256(part) != str(body.get("sha256") or ""):
            problem = "checksum mismatch"
        else:
            spec = json.loads(row["spec"])
            want = protocol.expected_seconds(spec)
            got = piece.duration(part)
            if not (want * 0.5 <= got <= want + 60):
                problem = f"duration {got:.1f}s, expected about {want:.1f}s"
        if problem:
            part.unlink(missing_ok=True)
            state = queue.fail(job_id, wid, f"the returned clip was damaged ({problem})")
            return {"ok": False, "state": state, "error": problem}
        results.mkdir(parents=True, exist_ok=True)
        final = _inside(results, f"{job_id}.mp4")
        shutil.move(str(part), final)
        if not queue.complete(job_id, wid, final, str(body.get("render_opts") or "")):
            final.unlink(missing_ok=True)       # a duplicate: the job was already done
            return {"ok": False, "state": "duplicate"}
        return {"ok": True}

    @app.post("/v1/jobs/{job_id}/fail")
    async def fail(job_id: str, request: Request):
        wid = worker_of(request)
        if not JOB_ID.fullmatch(job_id):
            raise _no(400, "bad job id")
        body = await request.json()
        state = queue.fail(job_id, wid, str(body.get("error") or "render failed"), str(body.get("log") or ""))
        _inside(uploads, f"{job_id}.part").unlink(missing_ok=True)
        return {"state": state}

    return app


_VIRTUAL = ("virtualbox", "vmware", "vethernet", "hyper-v", "wsl", "docker", "loopback", "npcap", "bluetooth")


def _primary() -> str:
    """The address this PC uses to reach the network (the default route's
    interface). A UDP connect sends nothing; it only picks the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def addresses() -> list[str]:
    """This PC's addresses a worker could reach, the one it really uses
    first: the local network's, and a Tailscale one (100.64.0.0/10) when it's
    on a tailnet. Virtual adapters (VirtualBox, WSL, Docker, Hyper-V) are
    left out: a worker can't reach this PC through them."""
    import ipaddress

    import psutil

    primary = _primary()
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    found = []
    # A machine whose interfaces can't be listed still has the primary address.
    with suppress(Exception):
        for name, addrs in psutil.net_if_addrs().items():
            virtual = any(v in name.lower() for v in _VIRTUAL)
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                ip = ipaddress.ip_address(a.address)
                if ip.is_loopback or ip.is_link_local:
                    continue
                if ip in tailnet or (ip.is_private and (not virtual or a.address == primary)):
                    found.append(a.address)
    if primary and primary not in found:
        found.append(primary)
    return sorted(set(found), key=lambda a: (a != primary, not a.startswith("100."), a))


class Gateway:
    """The gateway server, run in a thread inside the engine."""

    def __init__(self, data_dir: Path, port: int, host: str = "0.0.0.0"):
        self.data_dir = Path(data_dir)
        self.port = int(port)
        self.host = host
        self.queue = RenderQueue(self.data_dir)
        self.cert, self.key = tls.ensure_certificate(self.data_dir / "remote_render" / "tls")
        self.fingerprint = tls.fingerprint_of_file(self.cert)
        self._server = None
        self._thread = None
        self._sweeper = None
        self._stop = threading.Event()
        self.error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self.error

    def start(self) -> bool:
        import uvicorn

        if self.running:
            return True
        cfg = uvicorn.Config(create_app(self.queue, self.data_dir), host=self.host, port=self.port,
                             ssl_certfile=str(self.cert), ssl_keyfile=str(self.key),
                             log_level="warning", access_log=False)
        self._server = uvicorn.Server(cfg)
        self.error = ""

        def serve():
            try:
                self._server.run()
            except (SystemExit, Exception) as e:     # a port in use exits the server
                self.error = f"could not listen on port {self.port} ({e.__class__.__name__})"

        self._thread = threading.Thread(target=serve, daemon=True, name="render-gateway")
        self._thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not self._server.started and self._thread.is_alive():
            time.sleep(0.05)
        if not self._server.started:
            self.error = self.error or f"could not listen on port {self.port}"
            return False
        self._stop.clear()
        self._sweeper = threading.Thread(target=self._sweep, daemon=True, name="render-gateway-sweep")
        self._sweeper.start()
        print(f"      Remote rendering: listening for render workers on port {self.port}")
        return True

    def _sweep(self) -> None:
        while not self._stop.wait(SWEEP_EVERY):
            # A locked database this time round is simply swept on the next.
            with suppress(Exception):
                n = self.queue.sweep()
                if n:
                    print(f"      Remote rendering: a worker went offline; {n} job(s) back in the queue")

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None
