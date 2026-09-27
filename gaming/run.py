"""The two calls the pipeline makes when Gaming / Reaction is on.

A clip's settings travel in render_opts["gaming"]:
    cam           normalized [x, y, w, h] of the webcam, or None for none
    by            who decided the webcam:
                    "user"    drawn in the editor for this clip
                    "creator" remembered for this creator from an earlier edit
                    "video"   found across the whole video (prepare)
                    "clip"    to be found in this clip alone (the editor's
                              Split layout on a clip made without the switch)
    preset        the layout (gaming/layouts.json): split, basecam, half,
                  fullscreen, blurred, small_cam, circle_cam, game_ui, mosaic,
                  dual_cam, duo_split. Absent (clips from before layouts):
                  half, with cam_position giving the order.
    order         "cam_top" | "game_top": which goes on top in a stacked layout
    divider       the webcam band's share of the height (each layout's range)
    safe          the platform whose UI the face is kept clear of: tiktok,
                  reels, shorts, all, none (gaming/framing.py)
    game_align    "left" | "center" | "right": where a zoomed game crop sits
    game_box      normalized [x, y, w, h] of the game (or the video being
                  reacted to) drawn by the user, replacing the automatic region
    game_fit      "fit": the game whole, right against the webcam, with blur
                  above the two (clear of the platform's top bar) and below;
                  "fill": zoomed to fill its region
    ui_box        normalized box of a piece of the game's UI (Game UI, Mosaic)
    cam2          a second webcam (Dual facecam, Duo split), drawn by the user
    panels        normalized boxes of the stream's solid panels (a black chat
                  bar, a splits timer) that the game crop keeps out
                  (gaming/panels.py). Absent: looked for when the clip renders
    places        where the user put layers on the Short: {"cam", "cam2", "ui"}
                  -> normalized [x, y, w, h] of the canvas (a facecam keeps
                  its shape; two facecams are always the same size)
    cam_position  from before layouts: "bottom" meant order game_top

prepare(): once per video, before its clips render. The layout remembered for
    the creator, else the streamer's webcam found from several of the video's
    clips together (detect.video_cam).
render(): per clip. Decides this clip's layout and renders it:
    1. a webcam the user drew, or remembered for the creator, always wins (as
       does their "no webcam"): no detection runs;
    2. the video's webcam, when somebody is at it in this clip: a split;
    3. otherwise TalkNet on this clip: a camera filling the frame (a
       just-chatting stretch) goes to the standard renderer, which frames
       people; with no video-level answer, a webcam overlay this clip finds
       is used;
    4. otherwise the game fills the screen.

Both are called inside the pipeline's guards: any exception means the
standard renderer takes the clip, the same as with the switch off.
"""

from dataclasses import replace
from pathlib import Path

from core.paths import discard
from gaming import compose, detect, framing, layout, panels

PROBE_CLIPS = 4       # clips of the video looked at to find its webcam
PROBE_SECONDS = 40.0  # of each, from its start
TRUSTED = ("user", "creator")     # decided by a person: no detection second-guesses it
LAYOUT_KEYS = ("cam", "cam_position", "game_align", "game_box", "game_fit", "preset", "order", "divider",
               "safe", "ui_box", "cam2", "panels", "places")


def _spread(candidates: list, n: int) -> list:
    """Up to n clips spread through the video, so one scene (a reaction to one
    video, one cutscene) doesn't decide for all of it."""
    ordered = sorted(candidates, key=lambda c: c.start)
    if len(ordered) <= n:
        return ordered
    step = (len(ordered) - 1) / (n - 1)
    return [ordered[round(i * step)] for i in range(n)]


def saved_layout(layout_: dict | None) -> dict:
    """A layout set up before processing or remembered for a creator, cleaned
    to the keys a clip uses. No "cam" key means "find the webcam"."""
    if not isinstance(layout_, dict):
        return {}
    return {k: layout_[k] for k in LAYOUT_KEYS if k in layout_}


def _panels(video: Path) -> list:
    """The video's solid panels; none when it can't be read."""
    try:
        return panels.panels_in_video(video)
    except Exception as e:  # a crop without them is still a clip
        print(f"      Gaming: couldn't look for chat panels ({e})")
        return []


def prepare(source: Path, candidates: list, config: dict, work_dir: Path) -> dict:
    """This video's gaming settings, which every clip starts from."""
    from video.cutter import cut_clip

    given = config["clips"].get("gaming_layout")
    saved = saved_layout(given)
    if "panels" not in saved:
        saved["panels"] = _panels(source)
        if saved["panels"]:
            print(f"      Gaming: {len(saved['panels'])} solid panel(s) (chat, splits) kept out of the game")
    if "cam" in saved:
        by = "user" if given.get("by") == "user" else "creator"
        print("      Gaming: using the split " + ("set up for this video" if by == "user"
                                                  else "saved for this creator"))
        return {**saved, "by": by}

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
    # Anything else that was set up (game area, top or bottom) still applies.
    return {**saved, "cam": list(cam) if cam else None, "by": "video"}


def render(intermediate: Path, output: Path, g: dict, config: dict, ass_path: Path | None = None,
           vf_extra: str = "", normalize: bool = True) -> dict | None:
    """Render one clip in its gaming layout. Returns the settings to keep with
    the clip, or None when the standard renderer should frame it instead."""
    from core.modes import probe_size

    cam = g.get("cam")
    use = None
    if g.get("by") in TRUSTED:
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
            if face is not None and g.get("by") in (None, "clip"):
                # Nothing decided for the video: this clip's own webcam.
                use = list(face.box)

    src_w, src_h = probe_size(intermediate)
    if "panels" not in g:
        g = {**g, "panels": _panels(intermediate)}
    settings = {**g, "cam": list(use) if use else None}
    # The streamer's head in each webcam, so the crop keeps it on screen and
    # clear of the platform's UI (gaming/framing.py).
    detector = config["tracking"]["detector"]
    heads = {}
    for role in ("cam", "cam2"):
        if settings.get(role):
            head = detect.head_in_box(intermediate, tuple(settings[role]), detector)
            if head is not None:
                heads[role] = head
    p = layout.plan(src_w, src_h, settings, heads)
    compose.render(intermediate, output, p, ass_path=ass_path, vf_extra=vf_extra, normalize=normalize)
    face = None
    cam_el = p.element("cam")
    if cam_el is not None and "cam" in heads:
        face = framing.face_clear(heads["cam"], cam_el.src, cam_el.dest, cam_el.shift,
                                  framing.safe_zone(p.safe))
    return {**g, "layout": p.kind, "used_preset": p.preset,
            "used_cam": [round(v, 4) for v in use] if use else None,
            **({"face": face} if face is not None else {})}
