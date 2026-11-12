"""The render PC's side: pair with a main PC, then render the jobs it hands out.

Run by `main.py render-worker` (api.exe render-worker in an installed copy),
either headless on an always-on box or started by the desktop app when
"Use this PC as a render worker" is on. The worker connects out to the main
PC, so nothing on this PC is opened to the network.

Each job renders in a child process (`render-worker --run-job DIR`): a cancel
kills that process and everything under it, so no FFmpeg is left running,
and a crash stays in the child.
"""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

from remote_render import piece, protocol, settings, tls

HEARTBEAT = 10.0
CHUNK = 8 * 1024 * 1024


class PairingError(Exception):
    pass


_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*")


def _split(main: str) -> tuple[str, int]:
    """(host, port) from what someone typed: an IP address or a plain host
    name (a PC's name, a .local or tailnet name), and a port. Anything else,
    a URL or a path, is refused before any connection is made."""
    host, _, port = str(main).strip().rpartition(":")
    if not host:
        host, port = str(main).strip(), str(settings.DEFAULTS["port"])
    host = host.strip("[]")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            raise PairingError(f"{main!r} isn't an address. Use the one the main PC shows, like 192.168.1.20:8766.")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise PairingError(f"{main!r} has no valid port. The main PC shows it, like 192.168.1.20:8766.")
    return host, int(port)


def pair(data_dir: Path, main: str, code: str, name: str = "") -> dict:
    """Pair this PC with a main PC: pin its certificate, trade the one-time
    code for this worker's own credential, and remember both."""
    host, port = _split(main)
    try:
        fingerprint = tls.fingerprint_of_server(host, port)
    except OSError as e:
        raise PairingError(f"Can't reach {host}:{port} ({e.__class__.__name__}). Is remote rendering on "
                           "there, and is this PC on the same network (or tailnet)?") from e
    session = tls.pinned_session(fingerprint)
    caps = protocol.capabilities(full=True)
    try:
        r = session.post(f"https://{host}:{port}/v1/pair", json={"code": code, "name": name or caps["name"],
                                                                  "caps": caps}, timeout=30)
    except Exception as e:
        raise PairingError(f"Pairing failed: {e.__class__.__name__}") from e
    if r.status_code != 200:
        detail = ""
        # An answer that isn't JSON: the status code below says enough.
        with suppress(Exception):
            detail = r.json().get("detail", "")
        raise PairingError(detail or f"Pairing failed ({r.status_code})")
    body = r.json()
    settings.update(data_dir, this_pc={"main": f"{host}:{port}", "fingerprint": fingerprint,
                                       "worker_id": body["worker_id"], "secret": body["secret"]})
    return {"main": f"{host}:{port}", "fingerprint": tls.short(fingerprint)}


def _child_command(job_dir: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "render-worker", "--run-job", str(job_dir)]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py), "render-worker", "--run-job", str(job_dir)]


def _kill_tree(proc: subprocess.Popen) -> None:
    import psutil

    # A process that already exited, or a child that went first: nothing left to stop.
    with suppress(Exception):
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            with suppress(Exception):
                child.kill()
        parent.kill()


class Worker:
    def __init__(self, data_dir: Path, status_file: Path | None = None):
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "remote_render" / "jobs"
        self.status_file = status_file or (self.data_dir / "remote_render" / "worker_status.json")
        self._stop = threading.Event()
        self._running: dict[str, dict] = {}   # job id -> {"stage", "progress", "proc", "cancel"}
        self._lock = threading.Lock()
        self._caps: dict = {}
        self.last_error = ""
        self.connected = False

    # ---- plumbing ----------------------------------------------------------------------

    def _conf(self) -> dict:
        return settings.load(self.data_dir)["this_pc"]

    def _session(self, conf: dict):
        s = tls.pinned_session(conf["fingerprint"])
        s.headers.update({"X-Worker-Id": conf["worker_id"], "Authorization": f"Bearer {conf['secret']}"})
        return s

    def _url(self, conf: dict, path: str) -> str:
        return f"https://{conf['main']}{path}"

    def _write_status(self, conf: dict) -> None:
        with self._lock:
            running = [{"id": k, "stage": v["stage"], "progress": v["progress"], "label": v.get("label", "")}
                       for k, v in self._running.items()]
        # The status file only feeds the Settings page; the next heartbeat rewrites it.
        with suppress(OSError):
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            self.status_file.write_text(json.dumps({
                "time": time.time(), "connected": self.connected, "error": self.last_error,
                "main": conf.get("main", ""), "running": running, "draining": bool(conf.get("draining")),
            }), encoding="utf-8")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for job in self._running.values():
                job["cancel"] = True
                if job.get("proc") is not None:
                    _kill_tree(job["proc"])

    # ---- the loop ----------------------------------------------------------------------

    def run(self) -> int:
        conf = self._conf()
        if not (conf.get("worker_id") and conf.get("secret") and conf.get("fingerprint")):
            print("This PC isn't paired with a main PC. Pair it first (Settings → Advanced settings on "
                  "the main PC makes a code), then run:  render-worker --pair HOST:PORT CODE")
            return 2
        print(f"Render worker: rendering for {conf['main']} (Ctrl+C to stop)")
        self._caps = protocol.capabilities(full=True)
        last_beat = 0.0
        while not self._stop.is_set():
            conf = self._conf()
            session = self._session(conf)
            now = time.time()
            if now - last_beat >= HEARTBEAT:
                last_beat = now
                self._heartbeat(session, conf)
                self._write_status(conf)
            free = int(conf.get("max_jobs") or 1) - len(self._running)
            if self.connected and free > 0 and not conf.get("draining"):
                job = self._claim(session, conf)
                if job is not None:
                    self._start(job, conf)
                    continue
            self._stop.wait(2.0)
        return 0

    def _heartbeat(self, session, conf: dict) -> None:
        with self._lock:
            running = [{"id": k, "stage": v["stage"], "progress": v["progress"]} for k, v in self._running.items()]
        caps = {**protocol.capabilities(full=False), "encoders": self._caps.get("encoders"),
                "ffmpeg": self._caps.get("ffmpeg"), "framing": self._caps.get("framing")}
        try:
            r = session.post(self._url(conf, "/v1/heartbeat"), timeout=20,
                             json={"caps": caps, "draining": bool(conf.get("draining")),
                                   "max_jobs": int(conf.get("max_jobs") or 1), "running": running})
        except Exception as e:
            self.connected = False
            self.last_error = f"can't reach the main PC ({e.__class__.__name__})"
            return
        if r.status_code == 401:
            self.connected = False
            self.last_error = "the main PC no longer knows this worker; pair it again"
            return
        if r.status_code == 409:
            self.connected = False
            self.last_error = "worker update required: install the same Clips Kitty version as the main PC"
            return
        if r.status_code != 200:
            self.connected = False
            self.last_error = f"heartbeat refused ({r.status_code})"
            return
        self.connected = True
        self.last_error = ""
        for job_id in r.json().get("stop") or []:
            with self._lock:
                job = self._running.get(job_id)
                if job is not None:
                    job["cancel"] = True
                    if job.get("proc") is not None:
                        _kill_tree(job["proc"])

    def _claim(self, session, conf: dict) -> dict | None:
        try:
            r = session.post(self._url(conf, "/v1/claim"), timeout=30)
        except Exception:
            return None
        return r.json() if r.status_code == 200 else None

    def _start(self, job: dict, conf: dict) -> None:
        with self._lock:
            self._running[job["id"]] = {"stage": "transferring", "progress": 0.0, "proc": None, "cancel": False,
                                        "label": job["spec"].get("label", "")}
        threading.Thread(target=self._do_job, args=(job, conf), daemon=True, name=f"render-job-{job['id'][:8]}").start()

    def _set(self, session, conf: dict, job_id: str, stage: str, progress: float) -> None:
        with self._lock:
            if job_id in self._running:
                self._running[job_id].update(stage=stage, progress=progress)
        # Progress is informational; the next heartbeat carries it anyway.
        with suppress(Exception):
            session.post(self._url(conf, f"/v1/jobs/{job_id}/progress"), timeout=15,
                         json={"stage": stage, "progress": progress})

    def _cancelled(self, job_id: str) -> bool:
        with self._lock:
            return bool(self._running.get(job_id, {}).get("cancel")) or self._stop.is_set()

    # ---- one job -------------------------------------------------------------------------

    def _do_job(self, job: dict, conf: dict) -> None:
        job_id = job["id"]
        session = self._session(conf)
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        log = ""
        try:
            src = job_dir / "piece.mp4"
            self._download(session, conf, f"/v1/jobs/{job_id}/piece", src, job["piece_size"],
                           lambda p: self._set(session, conf, job_id, "transferring", p), job_id)
            if piece.sha256(src) != job["piece_sha"]:
                src.unlink(missing_ok=True)
                raise RuntimeError("the clip's video arrived damaged (checksum mismatch)")
            assets_dir = self.data_dir / "branding" / "assets"
            for name in job.get("assets") or {}:
                dest = assets_dir / Path(name).name
                if not dest.exists():
                    self._download(session, conf, f"/v1/jobs/{job_id}/assets/{Path(name).name}", dest, 0,
                                   lambda _p: None, job_id)
            if self._cancelled(job_id):
                return
            (job_dir / "job.json").write_text(json.dumps(job["spec"]), encoding="utf-8")
            self._set(session, conf, job_id, "rendering", 0.0)
            started = time.time()
            proc = subprocess.Popen(_child_command(job_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**os.environ, "CLIPS_STUDIO_DATA_DIR": str(self.data_dir)})
            with self._lock:
                if job_id in self._running:
                    self._running[job_id]["proc"] = proc
            out, _ = proc.communicate()
            log = (out or b"").decode("utf-8", errors="replace")[-8000:]
            if self._cancelled(job_id):
                return
            result = job_dir / "result.mp4"
            if proc.returncode != 0 or not result.exists():
                reason = (job_dir / "error.txt").read_text(encoding="utf-8") if (job_dir / "error.txt").exists() \
                    else f"the render process exited with code {proc.returncode}"
                raise RuntimeError(reason.strip()[:500])
            print(f"Render worker: rendered {job['spec'].get('label', job_id[:8])} in {time.time() - started:.0f}s")
            render_opts = json.loads((job_dir / "result.json").read_text(encoding="utf-8")).get("render_opts", "")
            self._upload(session, conf, job_id, result,
                         lambda p: self._set(session, conf, job_id, "uploading", p))
            r = session.post(self._url(conf, f"/v1/jobs/{job_id}/complete"), timeout=120,
                             json={"sha256": piece.sha256(result), "size": result.stat().st_size,
                                   "render_opts": render_opts})
            if r.status_code != 200 or not r.json().get("ok"):
                print(f"Render worker: the main PC didn't accept {job_id[:8]}: {r.text[:200]}")
        except Exception as e:
            if not self._cancelled(job_id):
                print(f"Render worker: job {job_id[:8]} failed: {e}")
                # Unreachable main PC: it requeues the job itself when this
                # worker's heartbeats stop.
                with suppress(Exception):
                    session.post(self._url(conf, f"/v1/jobs/{job_id}/fail"), timeout=20,
                                 json={"error": str(e)[:500], "log": log})
        finally:
            with self._lock:
                self._running.pop(job_id, None)
            shutil.rmtree(job_dir, ignore_errors=True)

    def _download(self, session, conf, path: str, dest: Path, size: int, on_progress, job_id: str) -> None:
        """Resumes from what's already on disk (HTTP Range)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        for _attempt in range(5):
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with session.get(self._url(conf, path), headers=headers, stream=True, timeout=60) as r:
                    if r.status_code == 416:
                        break
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"download refused ({r.status_code})")
                    mode = "ab" if r.status_code == 206 else "wb"
                    done = have if r.status_code == 206 else 0
                    with open(part, mode) as f:
                        for block in r.iter_content(CHUNK):
                            if self._cancelled(job_id):
                                return
                            f.write(block)
                            done += len(block)
                            if size:
                                on_progress(min(1.0, done / size))
                break
            except (OSError, RuntimeError) as e:
                if "refused" in str(e):
                    raise
                time.sleep(2)            # a dropped connection: resume from what arrived
        part.replace(dest)

    def _upload(self, session, conf, job_id: str, path: Path, on_progress) -> None:
        """Sends the clip in chunks, resuming from what the main PC has."""
        size = path.stat().st_size
        for _attempt in range(10):
            try:
                r = session.get(self._url(conf, f"/v1/jobs/{job_id}/result"), timeout=30)
                have = int(r.json().get("received", 0)) if r.status_code == 200 else 0
                with open(path, "rb") as f:
                    f.seek(have)
                    while have < size:
                        if self._cancelled(job_id):
                            return
                        block = f.read(CHUNK)
                        resp = session.put(self._url(conf, f"/v1/jobs/{job_id}/result"), params={"offset": have},
                                           data=block, timeout=120)
                        if resp.status_code == 409:
                            break            # out of step: ask again where to carry on from
                        if resp.status_code != 200:
                            raise RuntimeError(f"upload refused ({resp.status_code})")
                        have = int(resp.json().get("received", have + len(block)))
                        on_progress(min(1.0, have / size))
                if have >= size:
                    return
            except (OSError, ValueError) as e:
                print(f"Render worker: upload interrupted ({e.__class__.__name__}), resuming")
                time.sleep(2)
        raise RuntimeError("could not upload the rendered clip")


def _music_not_here(render_opts) -> str:
    """Why this PC can't put the clip's free music under it ("" when it can,
    or the clip has none): its copy of the track is missing, damaged or not
    one this version has. A local render makes such a clip without music; a
    render PC hands it back instead, so the main PC, which has the track,
    makes it, and nobody gets a silent clip that says it has music."""
    if isinstance(render_opts, str):
        try:
            render_opts = json.loads(render_opts)
        except ValueError:
            return ""
    edit = render_opts.get("edit") if isinstance(render_opts, dict) else None
    music = edit.get("music") if isinstance(edit, dict) else None
    if not isinstance(music, dict) or not music.get("library") or str(music.get("path") or "").strip():
        return ""
    from video import audio_library

    if audio_library.resolve(music["library"], kind="music") is not None:
        return ""
    return audio_library.why_not(music["library"], "music") + " on the render PC"


def run_job(job_dir: Path) -> int:
    """The child process: render one job with the same code as a local
    render, in this PC's own data folder, with only the job's settings."""
    job_dir = Path(job_dir)
    try:
        spec = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        missing = _music_not_here(spec.get("render_opts"))
        if missing:
            (job_dir / "error.txt").write_text(missing, encoding="utf-8")
            print(f"render handed back: {missing}")
            return 1
        from core.models import ClipCandidate, Segment
        from core.paths import resolve_data_dir
        from core.pipeline import _render_files

        data_dir = resolve_data_dir({"paths": {"data_dir": os.environ.get("CLIPS_STUDIO_DATA_DIR", "data")}})
        config = {**spec["config"], "paths": {"data_dir": str(data_dir)}}
        off = float(spec["offset"])
        c = spec["candidate"]
        candidate = ClipCandidate(**{**c, "start": float(c["start"]) - off, "end": float(c["end"]) - off})
        segments = []
        for s in spec.get("segments") or []:
            words = [{**w, "start": float(w["start"]) - off, "end": float(w["end"]) - off}
                     for w in (s.get("words") or []) if "start" in w and "end" in w]
            segments.append(Segment(start=float(s["start"]) - off, end=float(s["end"]) - off,
                                    text=s.get("text", ""), words=words or None))
        out_dir = job_dir / "out"
        final, render_opts = _render_files(job_dir / "piece.mp4", candidate, segments, out_dir, config,
                                           spec.get("render_opts"), spec.get("language") or "en")
        shutil.move(str(final), job_dir / "result.mp4")
        (job_dir / "result.json").write_text(json.dumps({"render_opts": render_opts}), encoding="utf-8")
        return 0
    except Exception as e:
        try:
            from core.pipeline import _render_failure_reason

            reason = _render_failure_reason(e)
        except Exception:
            reason = str(e)
        (job_dir / "error.txt").write_text(reason, encoding="utf-8")
        print(f"render failed: {reason}")
        return 1
