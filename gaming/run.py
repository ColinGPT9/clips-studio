"""The two calls the pipeline makes when Gaming / Split-Screen is on.

prepare(): once per video, before its clips render. Finds the streamer's
    webcam from several of the video's clips together (detect.video_cam), or
    takes the one saved for the creator. Its result travels to every clip in
    render_opts["gaming"].
render(): per clip. Decides this clip's layout and renders it:
    1. a webcam box the user drew for this clip always wins (or their "no
       webcam");
    2. the video's (or creator's) webcam, when somebody is at it in this clip:
       a split;
    3. otherwise TalkNet on this clip: a camera filling the frame (a
       just-chatting stretch) goes to the standard renderer, which frames
       people; with no video-level answer, a webcam overlay this clip finds
       is used;
    4. otherwise the game fills the screen.

Both are called inside the pipeline's guards: any exception means the
standard renderer takes the clip, the same as with the toggle off.
"""

from dataclasses import replace
from pathlib import Path

from core.paths import discard
from gaming import compose, detect, layout

PROBE_CLIPS = 4       # clips of the video looked at to find its webcam
PROBE_SECONDS = 40.0  # of each, from its start


def _spread(candidates: list, n: int) -> list:
    """Up to n clips spread through the video, so one scene (a reaction to one
    video, one cutscene) doesn't decide for all of it."""
    ordered = sorted(candidates, key=lambda c: c.start)
    if len(ordered) <= n:
        return ordered
    step = (len(ordered) - 1) / (n - 1)
    return [ordered[round(i * step)] for i in range(n)]


def prepare(source: Path, candidates: list, config: dict, work_dir: Path) -> dict:
    """{"cam": [x, y, w, h] | None, "by": "creator" | "video"} for this video."""
    from video.cutter import cut_clip

    saved = config["clips"].get("gaming_cam")
    if saved:
        print("      Gaming: using the webcam box saved for this creator")
        return {"cam": [float(v) for v in saved], "by": "creator"}

    tracking = config["tracking"]
    findings, frames = [], []
    work_dir.mkdir(parents=True, exist_ok=True)
    for c in _spread(candidates, PROBE_CLIPS):
        probe = work_dir / f"gaming_probe_{int(c.start):05d}.mp4"
        try:
            cut_clip(source, replace(c, end=min(c.end, c.start + PROBE_SECONDS)), probe)
            findings.append(detect.find_cam(probe, tracking["detector"], tracking["sample_fps"]))
            frames += detect.stills(probe)
        finally:
            discard(probe)
    cam = detect.video_cam(findings)
    if cam is not None:
        cam = detect.snap_to_frame(cam, frames)
    if cam is None:
        print(f"      Gaming: no webcam found in {len(findings)} clip(s); the game fills the screen")
    else:
        x, y, w, h = cam
        print(f"      Gaming: webcam found at {x:.2f},{y:.2f} ({w:.2f}x{h:.2f} of the frame)")
    return {"cam": list(cam) if cam else None, "by": "video"}


def render(intermediate: Path, output: Path, g: dict, config: dict, ass_path: Path | None = None,
           vf_extra: str = "", normalize: bool = True) -> dict | None:
    """Render one clip in its gaming layout. Returns the settings to keep with
    the clip, or None when the standard renderer should frame it instead."""
    from core.modes import probe_size

    cam = g.get("cam")
    use = None
    if g.get("by") == "user":
        use = cam
    else:
        tracking = config["tracking"]
        tracks, w, h, fps, duration, n = detect.sample_tracks(intermediate, tracking["detector"],
                                                             tracking["sample_fps"])
        ids = detect.candidates(tracks, n)
        if cam and ids and detect.present_at(tuple(cam), tracks, ids, w, h):
            use = cam
        elif ids:
            finding = detect.judge(tracks, n, w, h, detect.speaking_scores(intermediate, tracks, ids, duration, fps))
            face = finding.camera
            if face is not None and not face.overlay:
                return None
            if face is not None and not g.get("by"):
                # Nothing decided for the video (a clip re-rendered on its
                # own): this clip's own webcam overlay.
                use = list(face.box)

    src_w, src_h = probe_size(intermediate)
    p = layout.plan(src_w, src_h, tuple(use) if use else None,
                    cam_position=g.get("cam_position", "top"), game_align=g.get("game_align", "center"))
    compose.render(intermediate, output, p, ass_path=ass_path, vf_extra=vf_extra, normalize=normalize)
    return {**g, "layout": p.kind, **({"used_cam": [round(v, 4) for v in use]} if use else {})}
