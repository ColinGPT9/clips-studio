"""A debug video of basketball framing, for development only.

Never imported by a normal render: only with the CLIPS_STUDIO_FRAMING_DEBUG
variable set to a folder, and only from a source checkout, never the packaged
app (sports/basketball/__init__.py). Exported clips never carry any of this.

Left, the full frame (1280x720) with what the framing saw and decided:
- ball candidates: grey with the gate's reject letter, yellow when kept;
- the ball's chosen path: green where detected, magenta where interpolated,
  cyan where predicted, with its last second as a trail;
- people (thin), the ball's holder (orange) and the phase's actors (passer
  or shooter red, receiver blue);
- each basket: the rim, the backboard (found: white, estimated: grey) and
  the span a crop must hold (the "unit") as a bar above it;
- the planned crop: green tracked, yellow assisted or players, red wide,
  blue on a person, with its confidence bar;
- top left: the shot's kind (and replay), the phase, a CUT banner on a cut;
- along the bottom: the clip's phases, with the playhead.
Right, the 9:16 output preview of that crop (wider crops over grey).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LEFT_W, LEFT_H = 1280, 720
RIGHT_W = 406             # even: H.264 needs even dimensions
STRIP_H = 22

COL = {"detected": (60, 220, 60), "interpolated": (220, 60, 220), "predicted": (230, 200, 40),
       "tracked": (60, 220, 60), "assisted": (40, 210, 230), "players": (40, 210, 230), "wide": (60, 60, 230),
       "person": (230, 140, 40)}
PHASE_COL = {"possession": (90, 160, 90), "pass": (200, 160, 60), "release": (60, 90, 230), "flight": (40, 140, 240),
             "rim": (40, 40, 220), "result": (120, 40, 200), "attack": (60, 60, 160), "rebound": (150, 90, 200),
             "transition": (200, 200, 60), "loose": (110, 110, 110), "reaction": (160, 160, 160)}


def _nearest(times: list[float], t: float) -> int:
    from bisect import bisect_left

    k = bisect_left(times, t)
    if k <= 0:
        return 0
    if k >= len(times):
        return len(times) - 1
    return k if times[k] - t < t - times[k - 1] else k - 1


def render(clip_path, obs, plan, shots_data, out_path, fps: float | None = None) -> Path:
    """Write the debug video of `plan` over `clip_path` to `out_path` (mp4)."""
    import cv2
    import numpy as np

    from core.binaries import ffmpeg
    from sports.basketball.crop import phases as phases_mod
    from video.encoding import sampled_frames

    g = obs.geometry
    fps = fps or g.fps
    sx, sy = LEFT_W / g.width, LEFT_H / g.height
    by_shot = {d.shot.id: d for d in shots_data}
    shot_of_frame = []
    for d in shots_data:
        shot_of_frame += [d.shot.id] * (d.shot.f1 - d.shot.f0)
    cut_frames = {c.frame for c in plan.cuts}
    total = len(plan.cx)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = LEFT_W + RIGHT_W
    height = LEFT_H + STRIP_H
    cmd = [ffmpeg(), "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps}",
           "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # The phase strip, drawn once.
    strip = np.full((STRIP_H, LEFT_W, 3), 30, np.uint8)
    for d in shots_data:
        for p in d.phases:
            x0 = int(p.t0 * fps / max(1, total) * LEFT_W)
            x1 = max(x0 + 1, int(p.t1 * fps / max(1, total) * LEFT_W))
            cv2.rectangle(strip, (x0, 2), (x1, STRIP_H - 3), PHASE_COL.get(p.kind, (90, 90, 90)), -1)
    for f in cut_frames:
        x = int(f / max(1, total) * LEFT_W)
        cv2.line(strip, (x, 0), (x, STRIP_H), (0, 0, 255), 2)

    def P(x, y):
        return int(round(x * g.width * sx)), int(round(y * g.height * sy))

    try:
        for f, frame in sampled_frames(Path(clip_path), 1, g.width, g.height):
            if f >= total:
                break
            t = f / fps
            left = cv2.resize(frame, (LEFT_W, LEFT_H), interpolation=cv2.INTER_AREA)
            sid = shot_of_frame[f] if f < len(shot_of_frame) else None
            d = by_shot.get(sid)
            label = ""
            if d is not None:
                label = d.shot.kind + (" (replay)" if d.shot.replay else "")
                ph = phases_mod.at(d.phases, t) if d.phases else None
                if ph is not None:
                    label += f" | {ph.kind}"
                if d.samples:
                    times = [s.t for s in d.samples]
                    k = _nearest(times, t)
                    s = d.samples[k]
                    near = abs(s.t - t) <= 0.5 / 6
                    if near:
                        for c in s.balls:
                            colour = (150, 150, 150) if c.rejected else (0, 230, 255)
                            cv2.circle(left, P(c.x, c.y), max(4, int(c.w * LEFT_W / 2) + 2), colour, 1)
                            if c.rejected:
                                cv2.putText(left, c.rejected[:2], P(c.x + 0.006, c.y - 0.01),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1)
                    holder = None
                    if d.points and k < len(d.points):
                        from sports.basketball.crop import players as players_mod

                        holder = players_mod.handler(s, d.points[k], g.aspect)
                    actors = ph.actors if ph is not None else []
                    for p in s.people:
                        colour, th = (180, 180, 180), 1
                        if p.track is not None and p.track == holder:
                            colour, th = (0, 140, 255), 2
                        elif actors and p.track == actors[0]:
                            colour, th = (40, 40, 230), 2
                        elif len(actors) > 1 and p.track == actors[1]:
                            colour, th = (230, 120, 40), 2
                        cv2.rectangle(left, P(p.x - p.w / 2, p.y - p.h / 2), P(p.x + p.w / 2, p.y + p.h / 2), colour, th)
                    # The ball's chosen path: a trail over the last second.
                    for j in range(max(0, k - 10), min(k + 1, len(d.points))):
                        bp = d.points[j]
                        if bp.x is None or bp.status == "missing":
                            continue
                        r = 7 if j == k else 3
                        cv2.circle(left, P(bp.x, bp.y), r, COL.get(bp.status, (255, 255, 255)), -1 if j == k else 1)
                    if d.rows and k < len(d.rows):
                        for b in d.rows[k]:
                            if not b.in_frame:
                                continue
                            colour = (40, 40, 255) if b.id == d.relevant else (120, 120, 200)
                            cv2.rectangle(left, P(b.rim[0], b.rim[1]), P(b.rim[2], b.rim[3]), colour, 2)
                            if b.backboard:
                                cv2.rectangle(left, P(b.backboard[0], b.backboard[1]),
                                              P(b.backboard[2], b.backboard[3]),
                                              (255, 255, 255) if b.bb_src == "found" else (140, 140, 140), 1)
                            y = max(0.02, b.rim[1] - 0.06)
                            cv2.line(left, P(b.unit[0], y), P(b.unit[1], y), colour, 3)
            # The planned crop.
            cx, w = plan.cx[f], plan.w[f]
            mode = plan.mode[f]
            colour = COL.get(mode, (255, 255, 255))
            band = obs.geometry.rows or (0.0, 1.0)
            cv2.rectangle(left, P(cx - w / 2, band[0]), P(cx + w / 2, band[1] - 0.002), colour, 3)
            conf = plan.conf[f] if f < len(plan.conf) else 0.0
            x0, _ = P(cx - w / 2, 0)
            cv2.rectangle(left, (x0, LEFT_H - 10), (x0 + int(120 * conf), LEFT_H - 4), colour, -1)
            text = f"{t:6.2f}s  {label}  crop {mode} w={w:.3f}"
            cv2.putText(left, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(left, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            if f in cut_frames or (f - 1) in cut_frames or (f - 2) in cut_frames:
                cv2.rectangle(left, (0, 0), (LEFT_W - 1, LEFT_H - 1), (0, 0, 255), 8)
                cv2.putText(left, "CUT", (LEFT_W - 90, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3)
            # The 9:16 preview.
            right = np.full((LEFT_H, RIGHT_W, 3), 60, np.uint8)
            y0, y1 = int(band[0] * g.height), int(band[1] * g.height)
            cw = max(2, int(w * g.width))
            x0 = max(0, min(g.width - cw, int(cx * g.width - cw / 2)))
            crop = frame[y0:y1, x0:x0 + cw]
            ph_h = int(round(RIGHT_W * crop.shape[0] / max(1, crop.shape[1])))
            if ph_h >= LEFT_H:
                scaled = cv2.resize(crop, (max(RIGHT_W, int(round(LEFT_H * crop.shape[1] / crop.shape[0]))), LEFT_H))
                off = max(0, (scaled.shape[1] - RIGHT_W) // 2)
                right[:, :] = scaled[:, off:off + RIGHT_W]
            else:
                scaled = cv2.resize(crop, (RIGHT_W, max(2, ph_h)))
                top = (LEFT_H - scaled.shape[0]) // 2
                right[top:top + scaled.shape[0], :] = scaled
            canvas = np.zeros((height, width, 3), np.uint8)
            canvas[:LEFT_H, :LEFT_W] = left
            canvas[:LEFT_H, LEFT_W:] = right
            canvas[LEFT_H:, :LEFT_W] = strip
            x = int(f / max(1, total) * LEFT_W)
            cv2.line(canvas, (x, LEFT_H), (x, height - 1), (255, 255, 255), 2)
            proc.stdin.write(canvas.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    return out_path
