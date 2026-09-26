"""Measure Gaming / Split-Screen detection on real footage.

Two stages, so the decision rules can be tuned without re-running the models:

  measure: for every video given (or every .mp4 in a folder), cut a few
           windows and save each one's person tracks, TalkNet scores and a
           mid-window frame under <out>/raw/.
  decide:  apply gaming.detect's rules to the saved windows: per window the
           faces and the streamer TalkNet picks, per video the webcam the
           windows agree on, and the layout each window gets. Writes
           bench.json and one PNG per window: the frame with every candidate
           (red, with its speaking share and confidence), the webcam (green)
           and the game band (blue), next to a preview of the 1080x1920
           result.

Check the pictures by eye: is the green box the streamer's webcam, and is
chat outside the blue band?

    python scripts/gaming_detect_bench.py measure <folder or files> --out <dir>
    python scripts/gaming_detect_bench.py decide --out <dir>
"""

import argparse
import json
import pickle
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.binaries import ffmpeg  # noqa: E402
from gaming import detect, layout  # noqa: E402
from video.capture import video_capture  # noqa: E402
from video.tracker import _ASD_MAX_TRACKS, _ASD_MIN_FACE_SAMPLES  # noqa: E402


def cut(src: Path, start: float, length: float, dst: Path) -> bool:
    """A re-encoded window, so audio and video start on the same frame."""
    r = subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-ss", str(start), "-i", str(src), "-t", str(length),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", str(dst)],
        capture_output=True, timeout=600,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def mid_frame(clip: Path, t: float):
    with video_capture(clip, required=False) as cap:
        if cap is None:
            return None
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
    return frame if ok else None


def measure(args):
    files = []
    for i in args.inputs:
        files += sorted(i.glob("*.mp4")) if i.is_dir() else [i]
    files = [f for f in files if f.stat().st_size > 0]
    raw = args.out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            for start in (float(s) for s in args.starts.split(",")):
                key = f"{f.stem}_{int(start)}"
                if (raw / f"{key}.pkl").exists():
                    continue
                clip = Path(tmp) / f"{key}.mp4"
                if not cut(f, start, args.length, clip):
                    print(f"{key}: could not cut")
                    continue
                t0 = time.perf_counter()
                tracks, w, h, fps, duration, n = detect.sample_tracks(clip, args.model)
                t_tracks = time.perf_counter() - t0
                # Score the longest-seen faces whatever their presence, so the
                # presence bar can be tuned in `decide`.
                ids = sorted((tid for tid, tr in tracks.items() if len(tr.seen) >= _ASD_MIN_FACE_SAMPLES),
                             key=lambda tid: -len(tracks[tid].times))[:_ASD_MAX_TRACKS]
                t1 = time.perf_counter()
                scored = detect.speaking_scores(clip, tracks, ids, duration, fps) if ids else None
                t_asd = time.perf_counter() - t1
                frame = mid_frame(clip, args.length / 2)
                if frame is not None:
                    cv2.imwrite(str(raw / f"{key}.jpg"), frame)
                with open(raw / f"{key}.pkl", "wb") as fh:
                    pickle.dump({"video": f.stem, "start": start, "tracks": tracks, "w": w, "h": h,
                                 "duration": duration, "n": n, "scored": scored,
                                 "time_tracks": t_tracks, "time_talknet": t_asd}, fh)
                print(f"{key}: {len(tracks)} tracks, {len(ids)} scored, "
                      f"{t_tracks:.1f}s tracks + {t_asd:.1f}s TalkNet", flush=True)


def preview(frame, p: layout.Plan):
    """The 1080x1920 result, drawn with OpenCV (the real render is FFmpeg)."""
    out = np.zeros((layout.OUT_H, layout.OUT_W, 3), dtype=np.uint8)

    def fit(box, w, h):
        x, y, bw, bh = box
        region = frame[y:y + bh, x:x + bw]
        want = w / h
        if bw / bh > want:
            nw = int(bh * want)
            region = region[:, (bw - nw) // 2:(bw - nw) // 2 + nw]
        else:
            nh = int(bw / want)
            region = region[(bh - nh) // 2:(bh - nh) // 2 + nh]
        return cv2.resize(region, (w, h))

    if p.kind == "fill":
        out[:] = fit(p.game, layout.OUT_W, layout.OUT_H)
    else:
        cam = fit(p.cam, layout.OUT_W, layout.BAND_H)
        game = fit(p.game, layout.OUT_W, layout.BAND_H)
        top, bottom = (cam, game) if p.cam_position == "top" else (game, cam)
        out[:layout.BAND_H] = top
        out[layout.BAND_H:] = bottom
    return out


def annotate(frame, finding: detect.ClipFinding, cam, p: layout.Plan, kind: str):
    img = frame.copy()
    h, w = img.shape[:2]
    for tid, face in finding.faces.items():
        x, y, bw, bh = face.box
        cv2.rectangle(img, (int(x * w), int(y * h)), (int((x + bw) * w), int((y + bh) * h)), (0, 0, 255), 2)
        conf = "-" if face.confidence is None else f"{face.confidence:.1f}"
        cv2.putText(img, f"#{tid} p={face.presence:.2f} s={face.speaking_share:.2f} c={conf}",
                    (int(x * w) + 4, int(y * h) + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    if cam:
        x, y, bw, bh = cam
        cv2.rectangle(img, (int(x * w), int(y * h)), (int((x + bw) * w), int((y + bh) * h)), (0, 255, 0), 5)
    gx, gy, gw, gh = p.game
    cv2.rectangle(img, (gx, gy), (gx + gw - 1, gy + gh - 1), (255, 128, 0), 4)
    cv2.putText(img, kind, (20, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4)
    scale = layout.OUT_H / h / 2
    left = cv2.resize(img, (int(w * scale), int(h * scale)))
    right = cv2.resize(preview(frame, p), (layout.OUT_W // 2, layout.OUT_H // 2))
    pad = np.zeros((right.shape[0], left.shape[1], 3), dtype=np.uint8)
    top = (right.shape[0] - left.shape[0]) // 2
    pad[top:top + left.shape[0]] = left
    return np.hstack([pad, right])


def decide(args):
    raw = args.out / "raw"
    windows = defaultdict(list)
    for pkl in sorted(raw.glob("*.pkl")):
        with open(pkl, "rb") as fh:
            d = pickle.load(fh)
        d["key"] = pkl.stem
        windows[d["video"]].append(d)

    results = {}
    for video, ws in windows.items():
        ws.sort(key=lambda d: d["start"])
        findings = []
        for d in ws:
            ids = set(detect.candidates(d["tracks"], d["n"]))
            scored = d["scored"]
            if scored is not None:
                scores, loud = scored
                scores = {tid: s for tid, s in scores.items() if tid in ids}
                scored = (scores, loud) if scores else None
            findings.append(detect.judge(d["tracks"], d["n"], d["w"], d["h"], scored)
                            if ids and d["w"] else detect.ClipFinding(reason="nobody on screen for most of the clip"))
        cam = detect.video_cam(findings)
        for d, finding in zip(ws, findings):
            if cam is not None and detect.on_screen(cam, finding):
                kind, box = "split", cam
            elif finding.camera is not None and not finding.camera.overlay:
                kind, box = "standard", None
            else:
                kind, box = "fill", None
            p = layout.plan(d["w"] or 1920, d["h"] or 1080, box)
            results[d["key"]] = {
                "video": video, "kind": kind, "video_cam": [round(v, 3) for v in cam] if cam else None,
                "streamer": finding.streamer, "reason": finding.reason,
                "faces": {str(t): {"box": [round(v, 3) for v in f.box], "presence": round(f.presence, 2),
                                   "share": round(f.speaking_share, 2),
                                   "confidence": None if f.confidence is None else round(f.confidence, 2),
                                   "overlay": f.overlay} for t, f in finding.faces.items()},
                "time": round(d["time_tracks"] + d["time_talknet"], 1),
            }
            faces = ", ".join(f"#{t} p={f.presence:.2f} s={f.speaking_share:.2f} "
                              f"c={'-' if f.confidence is None else round(f.confidence, 2)}"
                              f"{' ov' if f.overlay else ''}" for t, f in finding.faces.items())
            print(f"{d['key']:18} {kind:8} streamer={finding.streamer} [{faces}]")
            frame_path = raw / f"{d['key']}.jpg"
            if frame_path.exists() and d["w"]:
                frame = cv2.imread(str(frame_path))
                cv2.imwrite(str(args.out / f"{d['key']}.png"), annotate(frame, finding, box, p, kind))
        print(f"== {video}: webcam {results[ws[-1]['key']]['video_cam']}")
    (args.out / "bench.json").write_text(json.dumps(results, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    m = sub.add_parser("measure")
    m.add_argument("inputs", nargs="+", type=Path)
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--starts", default="15,75,135", help="window starts, seconds")
    m.add_argument("--length", type=float, default=40.0)
    m.add_argument("--model", default="yolov8n-pose.pt")
    d = sub.add_parser("decide")
    d.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    measure(args) if args.stage == "measure" else decide(args)


if __name__ == "__main__":
    main()
