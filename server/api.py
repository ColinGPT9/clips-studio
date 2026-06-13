"""Local HTTP API for the desktop app.

Bound to 127.0.0.1 only — this is a local service, not a web server.
Start with:  python main.py serve   (default port 8765)

The Electron renderer talks exclusively to this API; it never touches
Python or the filesystem directly.
"""

import asyncio
import json
import re
import shutil
import threading
from pathlib import Path

import requests as _requests
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.state import StateDB
from server.events import broadcaster
from server.jobs import Worker


# ---- request bodies ----------------------------------------------------------


class JobIn(BaseModel):
    url: str
    force: bool = False
    max_clips: int | None = None  # per-job override of clips.max_clips_per_video


class ClipPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


class RenderIn(BaseModel):
    start: float | None = None
    end: float | None = None


class ExportIn(BaseModel):
    folder: str


class BatchExportIn(BaseModel):
    clip_ids: list[int]
    folder: str


class ModelIn(BaseModel):
    tag: str


class SettingsPatch(BaseModel):
    model: str | None = None
    channel: str | None = None
    auto_upload: bool | None = None
    privacy: str | None = None


# ---- app factory ---------------------------------------------------------------


def create_app(config: dict, settings_path: Path) -> FastAPI:
    app = FastAPI(title="Clips Studio API", version="0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    data_dir = Path(config["paths"]["data_dir"]).resolve()
    db_path = data_dir / "state.db"
    worker = Worker(config)

    def db() -> StateDB:
        # sqlite connections aren't shareable across FastAPI's threadpool
        # threads; per-request connections are effectively free.
        return StateDB(db_path)

    @app.on_event("startup")
    async def _startup():
        broadcaster.attach_loop(asyncio.get_running_loop())
        worker.start()

    @app.on_event("shutdown")
    async def _shutdown():
        worker.stop()

    # ---- health / system -----------------------------------------------

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/system/stats")
    def system_stats():
        import psutil

        disk = shutil.disk_usage(data_dir)
        stats = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_percent": psutil.virtual_memory().percent,
            "data_dir_bytes": sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file()),
            "disk_free_bytes": disk.free,
            "gpu": _gpu_stats(),
        }
        return stats

    # ---- jobs -------------------------------------------------------------

    @app.post("/jobs")
    def create_job(body: JobIn, status_code=201):
        payload: dict = {"url": body.url, "force": body.force}
        if body.max_clips is not None:
            payload["max_clips"] = max(1, min(10, body.max_clips))
        d = db()
        try:
            job_id = d.add_job("process", json.dumps(payload))
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id}

    @app.get("/jobs")
    def jobs():
        d = db()
        try:
            return [dict(r) for r in d.list_jobs()]
        finally:
            d.close()

    @app.get("/jobs/{job_id}")
    def job(job_id: int):
        d = db()
        try:
            row = d.get_job(job_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such job")
        return dict(row)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        queue = broadcaster.subscribe()
        try:
            while True:
                event = await queue.get()
                await socket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unsubscribe(queue)

    # ---- videos + clips ------------------------------------------------------

    @app.get("/videos")
    def videos():
        d = db()
        try:
            rows = d.conn.execute(
                """SELECT v.*, COUNT(c.id) AS clip_count
                   FROM videos v LEFT JOIN clips c ON c.video_id = v.video_id
                   GROUP BY v.video_id ORDER BY v.created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            d.close()

    @app.get("/videos/{video_id}/clips")
    def clips_for_video(video_id: str):
        d = db()
        try:
            return [_clip_json(r) for r in d.clips_for_video(video_id)]
        finally:
            d.close()

    @app.patch("/clips/{clip_id}")
    def patch_clip(clip_id: int, body: ClipPatch):
        d = db()
        try:
            if d.get_clip(clip_id) is None:
                raise HTTPException(404, "no such clip")
            fields = {}
            if body.title is not None:
                fields["title"] = body.title.strip()[:100]
            if body.description is not None:
                fields["description"] = body.description.strip()
            if body.hashtags is not None:
                fields["hashtags"] = json.dumps(body.hashtags)
            if fields:
                d.set_clip(clip_id, **fields)
            return _clip_json(d.get_clip(clip_id))
        finally:
            d.close()

    @app.post("/clips/{clip_id}/render")
    def rerender_clip(clip_id: int, body: RenderIn):
        d = db()
        try:
            if d.get_clip(clip_id) is None:
                raise HTTPException(404, "no such clip")
            payload = {"clip_id": clip_id}
            if body.start is not None:
                payload["start"] = body.start
            if body.end is not None:
                payload["end"] = body.end
            job_id = d.add_job("render", json.dumps(payload))
        finally:
            d.close()
        worker.notify()
        return {"job_id": job_id}

    @app.get("/media/{clip_id}")
    def media(clip_id: int):
        d = db()
        try:
            row = d.get_clip(clip_id)
        finally:
            d.close()
        if row is None or not row["path"]:
            raise HTTPException(404, "no such clip")
        path = Path(row["path"]).resolve()
        if not path.exists() or data_dir not in path.parents:
            raise HTTPException(404, "clip file missing")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/clips/{clip_id}/export")
    def export_clip(clip_id: int, body: ExportIn):
        return {"exported": _export([clip_id], Path(body.folder))}

    @app.post("/export/batch")
    def export_batch(body: BatchExportIn):
        return {"exported": _export(body.clip_ids, Path(body.folder))}

    def _export(clip_ids: list[int], folder: Path) -> list[str]:
        folder.mkdir(parents=True, exist_ok=True)
        d = db()
        exported = []
        try:
            for cid in clip_ids:
                row = d.get_clip(cid)
                if row is None or not row["path"] or not Path(row["path"]).exists():
                    continue
                name = _slugify(row["title"] or row["hook"] or Path(row["path"]).stem)
                target = _unique_path(folder, name)
                shutil.copy2(row["path"], target)
                exported.append(str(target))
        finally:
            d.close()
        return exported

    # ---- models ------------------------------------------------------------

    ollama_host = config["llm"].get("ollama_host", "http://localhost:11434").rstrip("/")

    @app.get("/models")
    def models():
        from llm.manager import RECOMMENDATIONS, installed_models

        try:
            installed = installed_models(ollama_host)
        except Exception:
            raise HTTPException(503, "Ollama is not reachable — is it running?")
        return {
            "active": config["llm"]["backend"],
            "installed": installed,
            "recommendations": [
                {"hardware": h, "model": m, "note": n} for h, m, n in RECOMMENDATIONS
            ],
        }

    @app.post("/models/activate")
    def activate_model(body: ModelIn):
        from llm.manager import installed_models, switch_model

        try:
            installed = {m["name"] for m in installed_models(ollama_host)}
        except Exception:
            installed = set()
        if installed and body.tag not in installed:
            raise HTTPException(400, f"'{body.tag}' is not pulled yet")
        spec = switch_model(settings_path, body.tag)
        config["llm"]["backend"] = spec  # live config follows the file
        return {"active": spec}

    @app.post("/models/pull")
    def pull_model(body: ModelIn):
        def _pull():
            try:
                with _requests.post(
                    f"{ollama_host}/api/pull", json={"model": body.tag}, stream=True, timeout=3600
                ) as resp:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        info = json.loads(line)
                        broadcaster.publish(
                            {
                                "type": "model_pull",
                                "tag": body.tag,
                                "status": info.get("status", ""),
                                "completed": info.get("completed"),
                                "total": info.get("total"),
                            }
                        )
                broadcaster.publish({"type": "model_pull", "tag": body.tag, "status": "done"})
            except Exception as e:
                broadcaster.publish({"type": "model_pull", "tag": body.tag, "status": "error", "error": str(e)})

        threading.Thread(target=_pull, daemon=True).start()
        return {"started": body.tag}

    @app.delete("/models/{tag:path}")
    def delete_model(tag: str):
        resp = _requests.delete(f"{ollama_host}/api/delete", json={"model": tag}, timeout=60)
        if resp.status_code != 200:
            raise HTTPException(400, f"Ollama refused: {resp.text[:200]}")
        return {"deleted": tag}

    # ---- settings (quick-setup keys only) -----------------------------------

    @app.get("/settings")
    def get_settings():
        return {
            "model": config["llm"]["backend"].split("/", 1)[-1],
            "channel": config.get("channel", ""),
            "auto_upload": config.get("upload", {}).get("enabled", False),
            "privacy": config.get("upload", {}).get("privacy", "public"),
        }

    @app.patch("/settings")
    def patch_settings(body: SettingsPatch):
        text = settings_path.read_text(encoding="utf-8")
        edits = {
            "model": body.model,
            "channel": f'"{body.channel}"' if body.channel is not None else None,
            "auto_upload": str(body.auto_upload).lower() if body.auto_upload is not None else None,
            "privacy": body.privacy,
        }
        for key, value in edits.items():
            if value is None:
                continue
            text, n = re.subn(rf"(?m)^({key}:\s*)\S*", rf"\g<1>{value}", text, count=1)
            if n == 0:
                raise HTTPException(400, f"no '{key}:' line in settings.yaml")
        settings_path.write_text(text, encoding="utf-8")
        return {"ok": True, "note": "restart serve to apply pipeline-level changes"}

    return app


# ---- helpers --------------------------------------------------------------------


def _clip_json(row) -> dict:
    d = dict(row)
    d["hashtags"] = json.loads(d["hashtags"]) if d.get("hashtags") else []
    d["scores"] = json.loads(d["scores"]) if d.get("scores") else {}
    return d


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)[:60].strip("-")
    return slug or "clip"


def _unique_path(folder: Path, name: str) -> Path:
    target = folder / f"{name}.mp4"
    i = 2
    while target.exists():
        target = folder / f"{name}-{i}.mp4"
        i += 1
    return target


def _gpu_stats() -> dict | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return {
            "name": pynvml.nvmlDeviceGetName(handle),
            "vram_used": mem.used,
            "vram_total": mem.total,
            "gpu_percent": util.gpu,
        }
    except Exception:
        return None  # no NVIDIA GPU / driver — the UI shows CPU-only mode
