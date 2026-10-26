"""Export an external plugin's SDK moments through Clips Kitty's renderer.

This developer demo accepts original synthetic test media only. It uses no
detector, model, or renderer copied from the plugin, and downloads nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.binaries import ffmpeg, ffprobe  # noqa: E402
from core.models import ClipCandidate, Segment  # noqa: E402


def probe_media(path: Path) -> dict:
    """Read actual exported streams with the app's resolved FFprobe."""
    result = subprocess.run(
        [ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def sdk_testing():
    """The SDK's test runner from this checkout's own SDK (sdk/python), put on
    the path the way the engine does it, so the demo needs no pip-installed
    SDK, which could come from another commit than this app."""
    import importlib

    import plugins._sdk  # noqa: F401  (puts sdk/python on the path)

    return importlib.import_module("clipskitty_sdk.testing")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_demo(
    *, plugin: Path, video: Path, transcript: Path, output: Path,
    synthetic_demo: bool = False, max_clips: int = 2,
    vertical: bool = False, captions: bool = True,
) -> dict:
    """Run the public SDK plugin process and existing app export functions.

    This writes a standalone MP4 for each returned moment (at most two),
    caption ASS files, a JSON manifest, log, and isolated StateDB history.
    It never opens the user's application database or installs the plugin.
    """
    if not synthetic_demo:
        raise ValueError("Pass --synthetic-demo to affirm these inputs are original synthetic test assets")
    if not 1 <= max_clips <= 2:
        raise ValueError("This technical demo exports one or two clips only")
    plugin, video, transcript, output = (Path(p).resolve() for p in (plugin, video, transcript, output))
    if output in (plugin, ROOT) or plugin in output.parents or ROOT in output.parents:
        raise ValueError("Write demo artifacts outside the plugin and app source folders")
    if not video.is_file() or not transcript.is_file():
        raise ValueError("Supply an existing synthetic MP4 and its authored transcript JSON")
    said = json.loads(transcript.read_text(encoding="utf-8"))
    if not isinstance(said, dict) or not isinstance(said.get("segments"), list):
        raise ValueError("The transcript must contain a segments list")

    # Public SDK and app entry points; the external plugin remains a process.
    testing = sdk_testing()

    from core.state import StateDB
    from video.captions import build_captions
    from video.cutter import cut_clip
    from video.encoding import video_encoder_args

    source_probe = probe_media(video)
    duration = float(source_probe["format"]["duration"])
    source_video = next((s for s in source_probe["streams"] if s["codec_type"] == "video"), None)
    if source_video is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Synthetic input must have a video stream and a positive finite duration")
    segments = [Segment(start=float(s["start"]), end=float(s["end"]), text=str(s["text"]),
                        words=s.get("words")) for s in said["segments"]]
    output.mkdir(parents=True, exist_ok=True)
    with (output / "demo.log").open("w", encoding="utf-8") as log:
        def say(message: str) -> None:
            print(message)
            log.write(message + "\n")
            log.flush()

        say("Synthetic media validation only; no real gameplay or human quality review.")
        say(f"FFmpeg: {ffmpeg()}; FFprobe: {ffprobe()}")
        say(f"Running external plugin through clipskitty_sdk.testing: {plugin}")
        run = testing.run_plugin(plugin, video=video, transcript=said, duration=duration,
                                 steps=("find", "understand"),
                                 tmp_path=output / "sdk-jobs", timeout=120)
        for event in run.events:
            say(f"SDK {event.get('type', 'event')}: {event.get('message', '')}")
        if not run.ok:
            raise RuntimeError(f"SDK plugin run failed: {run.error}")
        if not run.moments:
            raise RuntimeError("The plugin returned no moments; no export was invented")
        job = json.loads((run.folder / "job.json").read_text(encoding="utf-8"))
        plugin_info = job.get("plugin", {})
        say(f"Plugin returned {len(run.moments)} moment(s); exporting at most {max_clips}.")
        # Use the same CPU selection used by the app, with no hardware/model probes.
        video_encoder_args({"video": {"encoder": "cpu"}})
        canvas = (1080, 1920) if vertical else (source_video["width"], source_video["height"])
        records = []
        source_hash = _sha256(video)
        video_id = "synthetic-plugin-demo-" + source_hash[:16]
        history = StateDB(output / "history.sqlite3")
        try:
            history.upsert_video(video_id, title="Original synthetic plugin integration test", duration=duration)
            for index, moment in enumerate(run.moments[:max_clips], 1):
                start, end = float(moment.start), float(moment.end)
                if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 0.05):
                    raise RuntimeError("SDK returned invalid clip bounds")
                candidate = ClipCandidate(
                    start=start, end=min(duration, end), score=round(moment.score or 0),
                    hook=moment.title or moment.label, reason=moment.reason,
                    source=f"plugin:{plugin_info.get('id', '')}@{plugin_info.get('version', '')}",
                    subscores={"plugin": plugin_info.get("id", ""),
                               "plugin_version": plugin_info.get("version", ""),
                               "plugin_label": moment.label, "plugin_why": moment.reason},
                )
                stem = f"highlight-{index:02d}"
                ass = build_captions(segments, candidate, output / f"{stem}.ass", canvas=canvas,
                                     language=str(said.get("language") or "en")) if captions else None
                destination = output / f"{stem}.mp4"
                if destination == video:
                    raise ValueError("The demo output must not replace its source video")
                say(f"Exporting {start:.2f}–{end:.2f}s via existing app renderer: {destination}")
                if vertical:
                    from video.cropper import render_vertical

                    staging = output / f"{stem}.source.mp4"
                    cut_clip(video, candidate, staging, normalize=False)
                    render_vertical(staging, {"mode": "fit_blur"}, destination, ass_path=ass,
                                    normalize=True)
                else:
                    cut_clip(video, candidate, destination, ass_path=ass, normalize=True, final=True)
                exported = probe_media(destination)
                exported_duration = float(exported["format"]["duration"])
                if abs(exported_duration - candidate.duration) > 0.25:
                    raise RuntimeError("Exported duration differs from the SDK moment by more than 0.25 seconds")
                if not any(s["codec_type"] == "video" for s in exported["streams"]):
                    raise RuntimeError("Export has no video stream")
                if any(s["codec_type"] == "audio" for s in source_probe["streams"]) and not any(
                    s["codec_type"] == "audio" for s in exported["streams"]
                ):
                    raise RuntimeError("Export lost the source audio stream")
                clip_id = history.add_clip(
                    video_id, candidate.start, candidate.end, candidate.score, candidate.hook,
                    path=str(destination), title="Synthetic plugin integration highlight",
                    scores=json.dumps(candidate.subscores),
                    render_opts=json.dumps({"synthetic_demo": True, "layout": "fit_blur" if vertical else "original",
                                            "captions": ass is not None, "encoder": "cpu"}),
                )
                if clip_id is None:
                    clip_id = next((row["id"] for row in history.clips_for_video(video_id)
                                    if abs(row["start_s"] - candidate.start) < 0.01
                                    and abs(row["end_s"] - candidate.end) < 0.01), None)
                records.append({"path": str(destination), "source_start": candidate.start,
                                "source_end": candidate.end, "expected_duration": candidate.duration,
                                "category": moment.label, "detector_score": moment.score,
                                "reason": moment.reason, "captions": str(ass) if ass else None,
                                "caption_origin": "authored synthetic transcript; audio contains tones",
                                "history_clip_id": clip_id, "sha256": _sha256(destination), "probe": exported})
                say(f"Verified {exported_duration:.3f}s with video and preserved source audio.")
            history.set_outcome(video_id, {"synthetic_demo": True, "plugin": plugin_info,
                                          "exported_clips": len(records)})
            history.set_video_status(video_id, "done")
        finally:
            history.close()
        manifest = {
            "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "synthetic_demo": True, "consent": "Caller supplied --synthetic-demo for original test assets",
            "scope": "Technical SDK-to-existing-app-export validation; no real gameplay or quality claim",
            "manual_quality_review": "not_performed", "plugin": {**plugin_info, "path": str(plugin)},
            "source": {"video": str(video), "transcript": str(transcript), "sha256": source_hash,
                       "transcript_sha256": _sha256(transcript), "probe": source_probe},
            "sdk_job_folder": str(run.folder), "sdk_notes": run.notes,
            "renderer": {"cutter": "video.cutter.cut_clip", "encoder": "cpu", "normalize_audio": True,
                         "vertical": "video.cropper.render_vertical:fit_blur" if vertical else None,
                         "captions": "video.captions.build_captions" if captions else None,
                         "ffmpeg": ffmpeg(), "ffprobe": ffprobe()},
            "history_database": str(output / "history.sqlite3"), "clips": records,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        say(f"Saved manifest: {manifest_path}")
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True, help="External MIT plugin checkout")
    parser.add_argument("--video", type=Path, required=True, help="Original synthetic MP4")
    parser.add_argument("--transcript", type=Path, required=True, help="Authored synthetic transcript JSON")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "scratch" / "plugin-export-demo")
    parser.add_argument("--synthetic-demo", action="store_true", help="Affirm inputs are original synthetic test assets")
    parser.add_argument("--max-clips", type=int, choices=(1, 2), default=2)
    parser.add_argument("--vertical", action="store_true", help="Use existing 1080x1920 fit_blur renderer")
    parser.add_argument("--captions", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if not args.synthetic_demo:
        parser.error("--synthetic-demo is required for this synthetic-only integration check")
    run_demo(plugin=args.plugin, video=args.video, transcript=args.transcript, output=args.output,
             synthetic_demo=args.synthetic_demo, max_clips=args.max_clips,
             vertical=args.vertical, captions=args.captions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
