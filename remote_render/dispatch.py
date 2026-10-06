"""The pipeline's side of remote rendering: send clips out, take results in.

Only used when Settings → Advanced settings → Remote rendering is on and the
render mode isn't "This computer" (renderer_for returns None otherwise, and
the pipeline keeps its own local loop, untouched).

render_all() yields (candidate, meta, get_result) as each clip is ready,
exactly like the local loop's futures: get_result() returns (clip path,
render options JSON) or raises the reason the clip failed. Clips that can't
go remote render here in "Automatic"; in "only on <worker>" they wait for
that worker unless the user presses Render locally.
"""

import dataclasses
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

from core import cancel, progress
from remote_render import piece, protocol, settings

UNCLAIMED_GRACE = 60.0   # Automatic: a clip no worker picks up in this long renders here
POLL = 0.5


def renderer_for(config: dict):
    """A RemoteRenderer when remote rendering is on and not set to this
    computer, else None (the pipeline renders locally, as always)."""
    from core.paths import resolve_data_dir

    data_dir = resolve_data_dir(config)
    s = settings.load(data_dir)
    if not s.get("enabled") or s.get("mode", "local") == "local":
        return None
    from remote_render import service

    gateway = service.ensure_gateway(data_dir, int(s.get("port") or 8766))
    if gateway is None:
        print("      Remote rendering: the render gateway isn't running; rendering here")
        return None
    return RemoteRenderer(gateway, s["mode"], data_dir)


def _clip_name(candidate) -> str:
    return f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"


def _candidate_dict(candidate) -> dict:
    fields = {f.name for f in dataclasses.fields(candidate)}
    d = {k: v for k, v in dataclasses.asdict(candidate).items() if k in fields}
    d.pop("subscores", None)       # not used by a render
    return d


def _segments_for(segments, start: float, end: float) -> list[dict]:
    out = []
    for s in segments:
        if s.end < start - 2 or s.start > end + 2:
            continue
        out.append({"start": s.start, "end": s.end, "text": s.text,
                    "words": [dict(w) for w in (s.words or []) if "start" in w and "end" in w] or None})
    return out


def _assets(render_opts: dict | None, config: dict) -> dict:
    """Branding files the render needs: a custom watermark image."""
    wm = (render_opts or {}).get("watermark") if "watermark" in (render_opts or {}) \
        else (config.get("clips") or {}).get("watermark")
    name = (wm or {}).get("image_asset") if isinstance(wm, dict) else None
    return {str(name): True} if name else {}


class RemoteRenderer:
    def __init__(self, gateway, mode: str, data_dir: Path):
        self.gateway = gateway
        self.queue = gateway.queue
        self.mode = mode
        self.target = mode[len("worker:"):] if mode.startswith("worker:") else ""
        self.data_dir = Path(data_dir)
        self.pieces = self.data_dir / "remote_render" / "pieces"

    def _worker_name(self, worker_id: str) -> str:
        for w in self.queue.workers():
            if w["id"] == worker_id:
                return w["name"]
        return "the render PC"

    def render_all(self, video_id: str, source: Path, items: list, segments: list, clip_dir: Path,
                   config: dict, render_opts: dict | None, language: str, workers: int, local=None,
                   opts_for=None):
        """opts_for(meta), when given, is each clip's own render options
        (e.g. its headline, video/post_style.py) in place of render_opts."""
        per_clip = {id(c): opts_for(m) for c, m in items} if opts_for else {}

        def opts_of(c):
            return per_clip.get(id(c), render_opts)

        if local is None:
            from core.pipeline import _render_files

            def local(c):
                return _render_files(source, c, segments, clip_dir, config, opts_of(c), language)
        rcfg = protocol.render_config(config)
        framing = protocol.needs_framing(rcfg, render_opts)
        known = {w["id"] for w in self.queue.workers()}
        if self.target and self.target not in known:
            print("      Remote rendering: the chosen render worker isn't paired any more; rendering here")
            yield from self._all_local(items, local, workers)
            return
        if not self.target and not self.queue.online(framing):
            print("      Remote rendering: no render worker online that can render these; rendering here")
            yield from self._all_local(items, local, workers)
            return

        where = self._worker_name(self.target) if self.target else "a render worker"
        print(f"      Remote rendering: sending {len(items)} clip(s) to {where}")
        pending: dict[str, tuple] = {}
        for candidate, meta in items:
            cancel.check_active()
            ropts = opts_of(candidate)
            style = (ropts or {}).get("caption_style") or (config.get("clips") or {}).get("caption_style") or {}
            if style.get("second_speaker"):
                # Who is talking when is heard here, with the whole video to
                # know its main speaker by: the worker gets a piece of it.
                # Imported only for a clip with the option ticked, so a run
                # without it loads none of the pipeline.
                from core.pipeline import _speaker_turns, _wants_second_speaker

                if _wants_second_speaker(config, ropts or {}):
                    ropts = {**(ropts or {}),
                             "speaker_turns": _speaker_turns(source, candidate, segments, config, ropts or {})}
            jid = protocol.job_id(video_id, candidate.start, candidate.end, ropts, config)
            known_job = self.queue.job(jid)
            if known_job and known_job["state"] == "completed" and Path(known_job["result_path"]).exists():
                pending[jid] = (candidate, meta)
                continue
            path = self.pieces / f"{jid}.mp4"
            offset = piece.cut(source, candidate.start, candidate.end, path)
            spec = {"protocol": protocol.PROTOCOL, "video_id": video_id,
                    "label": f"{int(candidate.start)}s-{int(candidate.end)}s",
                    "start": candidate.start, "end": candidate.end, "offset": offset,
                    "candidate": _candidate_dict(candidate),
                    "segments": _segments_for(segments, candidate.start, candidate.end),
                    "render_opts": ropts, "language": language, "config": rcfg}
            self.queue.submit({"id": jid, "video_id": video_id, "label": spec["label"], "target": self.target,
                               "needs_framing": framing, "spec": spec, "piece_path": str(path),
                               "piece_sha": piece.sha256(path), "piece_size": path.stat().st_size,
                               "assets": _assets(ropts, config)})
            pending[jid] = (candidate, meta)

        done = 0
        total = len(items)
        no_worker_since: float | None = None
        last_note = ""
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            here: dict = {}
            while pending or here:
                try:
                    cancel.check_active()
                except cancel.CancelledError:
                    self.queue.cancel_video(video_id)
                    raise
                # Automatic: no worker able to take what's waiting -> render it here.
                if not self.target:
                    if self.queue.online(framing):
                        no_worker_since = None
                    else:
                        no_worker_since = no_worker_since or time.time()
                for jid in list(pending):
                    job = self.queue.job(jid) or {"state": "local"}
                    candidate, meta = pending[jid]
                    state = job["state"]
                    if state == "completed":
                        del pending[jid]
                        done += 1
                        yield candidate, meta, self._take(job, clip_dir, candidate)
                        self._discard_piece(jid)
                    elif state == "local" or (state == "failed" and not self.target) or (
                            state == "queued" and no_worker_since is not None
                            and time.time() - no_worker_since > UNCLAIMED_GRACE):
                        if state != "local":
                            why = job.get("error") or "no render worker took it"
                            print(f"      Remote rendering: {job.get('label', '')} renders here ({why})")
                            self.queue.release_to_local(video_id, jid)
                        del pending[jid]
                        here[pool.submit(local, candidate)] = (candidate, meta)
                        self._discard_piece(jid)
                    elif state == "failed":
                        del pending[jid]
                        done += 1
                        error = job.get("error") or "the render worker failed"
                        yield candidate, meta, _raiser(RuntimeError(f"on {self._worker_name(job['worker_id'])}: {error}"))
                    elif state == "cancelled":
                        del pending[jid]
                        done += 1
                        yield candidate, meta, _raiser(cancel.CancelledError(video_id))
                for fut in [f for f in here if f.done()]:
                    candidate, meta = here.pop(fut)
                    done += 1
                    yield candidate, meta, fut.result
                note = self._note(pending, no_worker_since)
                if note and note != last_note:
                    progress.emit(stage="render", video_id=video_id, clip=min(total, done + 1), total=total,
                                  remote=note)
                    last_note = note
                if pending or here:
                    time.sleep(POLL)

    def _note(self, pending: dict, no_worker_since) -> str:
        """"on Gaming PC · uploading 62%", for the queue's progress line."""
        jobs = [j for j in (self.queue.job(jid) for jid in pending) if j]
        active = [j for j in jobs if j["state"] in ("assigned", "transferring", "rendering", "uploading", "verifying")]
        if active:
            j = max(active, key=lambda j: j["updated"])
            stage = j.get("stage") or j["state"]
            pct = f" {int(round(float(j.get('progress') or 0) * 100))}%" if stage in ("transferring", "uploading") else ""
            return f"on {self._worker_name(j['worker_id'])} · {stage}{pct}"
        if jobs and self.target and no_worker_since is None:
            return f"waiting for {self._worker_name(self.target)}"
        return ""

    def _take(self, job: dict, clip_dir: Path, candidate):
        """The returned clip into the library under its usual name. Written
        into place (copy, not rename): a clip open in the app's preview can
        be written but not replaced on Windows."""
        src = Path(job["result_path"])
        dest = Path(clip_dir) / _clip_name(candidate)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(src, dest)
            src.unlink(missing_ok=True)
        except OSError as e:
            return _raiser(RuntimeError(f"could not save the rendered clip: {e}"))
        opts = job.get("render_opts") or ""
        return lambda: (dest, opts)

    def _discard_piece(self, jid: str) -> None:
        # A piece still open elsewhere is cleared with the next one; it's scratch.
        with suppress(OSError):
            (self.pieces / f"{jid}.mp4").unlink(missing_ok=True)

    def _all_local(self, items: list, local, workers: int):
        from concurrent.futures import as_completed

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(local, c): (c, m) for c, m in items}
            for fut in as_completed(futures):
                cancel.check_active()
                c, m = futures[fut]
                yield c, m, fut.result


def _raiser(error: Exception):
    def get():
        raise error
    return get
