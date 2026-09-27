"""Who the streamer is in a gaming video, and where their webcam sits.

TalkNet decides WHO. The streamer is the face that speaks in time with the
audio. Size never picks the person: that is how earlier versions ended up
framing a game character, a portrait or the person in a video being reacted
to (the "largest face" problem TalkNet already fixed in the standard tracker).

Measured on real game streams (scripts/gaming_detect_bench.py, docs/GAMING.md),
TalkNet alone answers "who is speaking in this clip", which is not always the
streamer, so three things sit around it:

- Presence. A face must be on screen for most of the clip to be a candidate.
  TalkNet scores whatever crop it is given, and a face seen for a moment (a
  driver glimpsed through a car window in GTA) scored as confidently as a
  real speaker. A webcam is there the whole time.
- Ranking, not a fixed level. TalkNet's absolute logit moves with how well a
  crop matches its training, so faces are compared against each other (the
  standard tracker does the same). Of the candidates that speak for a real
  share of the loud moments, the most confident speaker wins.
- The video, not one clip. In a reaction the person in the video being
  watched can out-talk the streamer for a whole clip. Across the video,
  though, the streamer is the one speaking from the same spot again and
  again; the people in the content come and go. So the webcam is decided
  from several windows of the video together (video_cam), and each clip
  checks whether that webcam is on screen.

Once the streamer is known, their person box over the clip gives the webcam
box, and its size says whether it is an overlay on the game (a split) or a
camera filling the frame (not a gaming layout; the standard tracker frames
that).

Nobody speaking is an answer too: then there is no webcam, and the caller
falls back to a box the user drew or one saved for the creator. Never to size.

Isolation: imported only when the Gaming / Split-Screen toggle is on. It reuses
the tracker's detector, track matching and TalkNet crop read-only, and
video/tracker.py is not modified.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from video.capture import video_capture
from video.tracker import (
    _ASD_MAX_TRACKS,
    _ASD_MIN_FACE_SAMPLES,
    _ASD_SPEECH,
    _assign,
    _clip_pcm,
    _contested_spans,
    _detect,
    _get_model,
    _interp_boxes,
    _mouth_patch,
    head_box,
)

MIN_CONFIDENCE = 0.4          # the tracker's own person-detection bar
# A candidate must be on screen for this share of the clip's sampled frames.
MIN_PRESENCE = 0.5
# TalkNet's logit at or above which a face counts as speaking at that moment.
SPEAK_LOGIT = -1.0
# Of the loud moments a candidate is on screen for, the share it must be
# speaking in to be a speaker at all. Among those, the most confident wins.
MIN_SPEAKING_SHARE = 0.25
# A camera shows a real person, and TalkNet is sure of a real person talking:
# a webcam's median logit reached 0.3 to 3.4 in at least one clip of every
# stream measured. Game characters with voiced lip-flaps (a 3D visual novel,
# a gacha RPG's lead) and VTuber avatars stayed at -0.2 or below, so they
# never become the webcam, however much they "speak". This is built for
# people on camera: VTubers are not supported, and their streams get the
# game filling the screen.
CAMERA_CONFIDENCE = 0.0
# Two track pieces are the same person when their median boxes overlap this
# much and they are hardly ever on screen at the same time (the tracker
# starts a new identity after a missed detection or a fast movement).
MERGE_IOU = 0.5
# An overlay webcam is small...
OVERLAY_MAX_AREA = 0.35
# ...and the streamer's head stays inside it: its spread over the clip is at
# most this fraction of the box width. A camera filling the frame fails one.
OVERLAY_SPREAD = 0.35
# snap_to_frame: how far out from the streamer's box (fraction of the frame)
# the webcam's own border is looked for, and how much stronger than the
# typical edge in that band it must be to count as one.
SNAP_SEARCH = 0.08
SNAP_STRENGTH = 3.0
SNAP_FLOOR = 6.0              # grey levels: weaker than this is no border at all
# A snapped side sits this far inside the border (fraction of the frame), so
# neither the overlay's frame line nor what is beside it shows in the band.
SNAP_INSET = 0.004
# ...and how far back INTO the streamer's box: a person box can overshoot
# the webcam's edge by a few pixels (measured 0.5% of the frame), while a
# door frame behind a streamer sat 1.1% inside their box. Between the two.
SNAP_BACK = 0.006
STILLS_WIDTH = 960            # stills are compared at this width
# video_cam: webcam boxes from different windows are one webcam at this IoU.
SAME_CAM_IOU = 0.5
# present_at / on_screen: the streamer is at the webcam when this much of
# their box is inside it.
INSIDE = 0.7


@dataclass
class Track:
    """What one person track was, over the clip."""
    times: list = field(default_factory=list)     # sample times the person was seen
    seen: list = field(default_factory=list)      # [(t, (x1, y1, x2, y2) head box px)]
    person: list = field(default_factory=list)    # [(x1, y1, x2, y2) person box px]
    head_cx: list = field(default_factory=list)   # normalized head centre x


@dataclass
class Face:
    """One candidate in one clip."""
    box: tuple                    # normalized (x, y, w, h) webcam box around the person
    presence: float               # share of the clip's samples it is on screen
    speaking_share: float         # share of loud on-screen moments it is speaking
    confidence: float | None      # median TalkNet logit over those moments
    overlay: bool                 # small and contained: a webcam over the game


@dataclass
class ClipFinding:
    """Every candidate in a clip, and the one TalkNet picked."""
    faces: dict = field(default_factory=dict)     # {track id: Face}
    streamer: int | None = None
    reason: str = ""

    @property
    def cam(self) -> Face | None:
        return self.faces.get(self.streamer) if self.streamer is not None else None

    @property
    def camera(self) -> Face | None:
        """The streamer, when TalkNet is sure enough that this is a real
        person on camera (CAMERA_CONFIDENCE) to act on it within one clip."""
        face = self.cam
        return face if face is not None and face.confidence >= CAMERA_CONFIDENCE else None


# ---- tracks ---------------------------------------------------------------------------


def sample_tracks(clip_path: Path, model_name: str, sample_fps: float = 8.0):
    """Person tracks over the clip, with the tracker's own detector and matching.
    Returns (tracks, width, height, video_fps, duration, n_samples)."""
    import cv2

    tracks_state: dict = {}
    tracks: dict[int, Track] = {}
    w = h = 0
    n_samples = 0
    with video_capture(clip_path) as cap:
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(video_fps / sample_fps))
        model = _get_model(model_name)
        idx = 0
        last_t = 0.0
        while True:
            if not cap.grab():
                break
            if idx % step:
                idx += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            t = idx / video_fps
            last_t = t
            n_samples += 1
            for tid in _assign(tracks_state, _detect(model, frame, MIN_CONFIDENCE), t):
                det = tracks_state[tid].box
                head = det[5]
                tr = tracks.setdefault(tid, Track())
                tr.times.append(t)
                tr.person.append(tuple(det[:4]))
                if head is not None:
                    tr.seen.append((t, head_box(head)))
                    tr.head_cx.append(head[0] / w)
            idx += 1
    return merge_tracks(tracks), w, h, video_fps, last_t, n_samples


def _iou(a, b) -> float:
    """IoU of two (x1, y1, x2, y2) boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge_tracks(tracks: dict) -> dict:
    """Join track pieces that are one person at one spot: a webcam face that
    lost its identity for a frame comes back as a new track, and split in two
    neither piece is on screen long enough to count."""
    ids = sorted(tracks, key=lambda tid: -len(tracks[tid].times))
    merged: dict[int, Track] = {}
    for tid in ids:
        tr = tracks[tid]
        if not tr.person:
            continue
        box = np.median(np.array(tr.person), axis=0)
        mine = set(np.round(np.array(tr.times), 3))
        for keep in merged.values():
            kbox = np.median(np.array(keep.person), axis=0)
            together = len(mine & set(np.round(np.array(keep.times), 3)))
            if _iou(box, kbox) >= MERGE_IOU and together <= 0.1 * min(len(mine), len(keep.times)):
                _absorb(keep, tr)
                break
        else:
            merged[tid] = Track(list(tr.times), list(tr.seen), list(tr.person), list(tr.head_cx))
    return merged


def _absorb(keep: Track, other: Track) -> None:
    order = np.argsort(np.array(keep.times + other.times), kind="stable")
    for name in ("times", "person"):
        joined = getattr(keep, name) + getattr(other, name)
        setattr(keep, name, [joined[i] for i in order])
    keep.seen = sorted(keep.seen + other.seen, key=lambda s: s[0])
    keep.head_cx = keep.head_cx + other.head_cx


def candidates(tracks: dict, n_samples: int, min_presence: float = MIN_PRESENCE) -> list:
    """Tracks on screen long enough to be the streamer, longest first."""
    keep = [tid for tid, tr in tracks.items()
            if n_samples and len(tr.times) / n_samples >= min_presence
            and len(tr.seen) >= _ASD_MIN_FACE_SAMPLES]
    keep.sort(key=lambda tid: -len(tracks[tid].times))
    return keep[:_ASD_MAX_TRACKS]


# ---- TalkNet --------------------------------------------------------------------------


def speaking_scores(clip_path: Path, tracks: dict, ids: list, duration: float, video_fps: float):
    """TalkNet's per-frame (25fps) score for each of `ids`, INCLUDING a lone
    face, plus the audio loudness per frame.

    The tracker's own score_faces only answers when two faces share the screen
    (for framing, one face is nobody to choose between). Here one face is
    exactly the question: is this the streamer talking, or a character? So this
    scores wherever any candidate is on screen, with the tracker's crop and
    interpolation, and TalkNet's own windowed scoring.

    Returns (scores, loud) or None when there is no model, no audio, or
    nothing to score.
    """
    from video import asd

    if not ids or not asd.available():
        return None
    pcm = _clip_pcm(clip_path, asd.AUDIO_SR)
    if pcm is None or pcm.size < asd.AUDIO_SR // 4:
        return None
    n_frames = int(duration * asd.FPS)
    if n_frames < asd.FPS:
        return None
    times = np.arange(n_frames, dtype=np.float64) / asd.FPS
    boxes = {tid: _interp_boxes(tracks[tid].seen, times) for tid in ids}
    present = {tid: ~np.isnan(boxes[tid][:, 0]) for tid in ids}
    anyone = np.any(list(present.values()), axis=0)
    spans = _contested_spans(anyone, asd.FPS, n_frames)
    if not spans:
        return None

    size = asd.FACE_SIZE
    crops = {tid: np.zeros((n_frames, size, size), dtype=np.uint8) for tid in ids}
    with video_capture(clip_path, required=False) as cap:
        if cap is None:
            return None
        wanted = np.rint(times * video_fps).astype(np.int64)
        needed = np.zeros(n_frames, dtype=bool)
        for a, b in spans:
            needed[a:b] = True
        idx, j = 0, 0
        while j < n_frames:
            while j < n_frames and not needed[j]:
                j += 1
            if j >= n_frames or not cap.grab():
                break
            if idx == wanted[j]:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                while j < n_frames and wanted[j] == idx:
                    for tid in ids:
                        patch = _mouth_patch(frame, boxes[tid][j], size)
                        if patch is not None:
                            crops[tid][j] = patch
                    j += 1
            idx += 1

    scores = {tid: np.full(n_frames, -np.inf, dtype=np.float32) for tid in ids}
    per_frame = asd.AUDIO_SR / asd.FPS
    for a, b in spans:
        chunk = pcm[int(a * per_frame):int(b * per_frame)]
        if chunk.size < asd.AUDIO_SR // 4:
            continue
        for tid in ids:
            got = np.asarray(asd.score_track(crops[tid][a:b], chunk), dtype=np.float32)[: b - a]
            got[~present[tid][a:a + len(got)]] = -np.inf
            scores[tid][a:a + len(got)] = got

    # Loudness per 25fps frame: speech happens only when the stream is loud
    # enough, relative to its own 90th percentile (the tracker's own gate).
    per = int(per_frame)
    env = np.sqrt(np.array([np.mean(pcm[i * per:(i + 1) * per] ** 2) if (i + 1) * per <= pcm.size else 0.0
                            for i in range(n_frames)]))
    floor = _ASD_SPEECH * float(np.percentile(env, 90)) if env.size else 0.0
    loud = env >= floor
    return scores, loud


def speaking(scores: np.ndarray, loud: np.ndarray) -> tuple[float, float | None]:
    """(speaking share, median logit) over the loud moments a face is on screen."""
    on = np.isfinite(scores) & loud
    if not on.any():
        return 0.0, None
    return float(np.mean(scores[on] >= SPEAK_LOGIT)), float(np.median(scores[on]))


def pick_streamer(faces: dict) -> int | None:
    """Of the faces that speak for a real share of the loud moments, the one
    TalkNet is most confident about. None when nobody speaks. Size plays no
    part."""
    speakers = [tid for tid, f in faces.items()
                if f.speaking_share >= MIN_SPEAKING_SHARE and f.confidence is not None]
    if not speakers:
        return None
    return max(speakers, key=lambda tid: faces[tid].confidence)


# ---- where ----------------------------------------------------------------------------


def webcam_box(track: Track, w: int, h: int) -> tuple[tuple, bool]:
    """(normalized x, y, w, h box, is_overlay) for a person track.

    The box is the person's head-and-shoulders region: the median person box
    over the clip, kept inside the frame. No margin is added: a margin runs
    past the webcam into whatever is beside it (measured: a strip of chat down
    the side of a speedrunner's webcam). snap_to_frame grows it out to the
    webcam's own border where there is one."""
    boxes = np.array(track.person, dtype=np.float64)
    x1, y1, x2, y2 = np.median(boxes, axis=0)
    x1, x2 = max(0.0, x1), min(float(w), x2)
    y1, y2 = max(0.0, y1), min(float(h), y2)
    box = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)
    small = box[2] * box[3] <= OVERLAY_MAX_AREA
    contained = bool(track.head_cx) and float(np.std(track.head_cx)) <= OVERLAY_SPREAD * box[2]
    return tuple(float(v) for v in box), bool(small and contained)


def judge(tracks: dict, n_samples: int, w: int, h: int, scored) -> ClipFinding:
    """A ClipFinding from the tracks and TalkNet's scores (see find_cam)."""
    if scored is None:
        return ClipFinding(reason="no face TalkNet could score (no audio, no model, or none on screen long enough)")
    scores, loud = scored
    faces = {}
    for tid, s in scores.items():
        share, conf = speaking(s, loud)
        box, overlay = webcam_box(tracks[tid], w, h)
        faces[tid] = Face(box, len(tracks[tid].times) / max(1, n_samples), share, conf, overlay)
    streamer = pick_streamer(faces)
    return ClipFinding(faces, streamer, "streamer found by TalkNet" if streamer is not None
                       else "nobody on screen is speaking")


def find_cam(clip_path: Path, model_name: str = "yolov8n-pose.pt", sample_fps: float = 8.0) -> ClipFinding:
    """Every webcam candidate in one clip, and who TalkNet says is the streamer."""
    tracks, w, h, video_fps, duration, n_samples = sample_tracks(clip_path, model_name, sample_fps)
    ids = candidates(tracks, n_samples)
    if not ids or not w:
        return ClipFinding(reason="nobody on screen for most of the clip")
    return judge(tracks, n_samples, w, h, speaking_scores(clip_path, tracks, ids, duration, video_fps))


# ---- the video ------------------------------------------------------------------------


def _xyxy(box: tuple) -> tuple:
    x, y, w, h = box
    return (x, y, x + w, y + h)


def video_cam(findings: list) -> tuple | None:
    """The streamer's webcam box for the whole video, from several clips.

    Every clip votes for its streamer's box when that is an overlay. Boxes at
    the same spot are one webcam; the webcam voted for by the most clips wins
    (TalkNet's confidence breaks a tie), and it needs more than one vote when
    more than one clip was looked at. In a reaction, the person in the
    watched video can win one clip; the streamer wins the rest from the same
    corner. And in at least one of its clips TalkNet has to have been sure
    it is a real person talking (CAMERA_CONFIDENCE): a game character who
    stands in the same spot and lip-flaps to voice acting is not a webcam.

    Returns the median normalized (x, y, w, h) box, or None.
    """
    votes = [f.cam for f in findings if f.cam is not None and f.cam.overlay]
    groups: list[list[Face]] = []
    for face in votes:
        for g in groups:
            if _iou(_xyxy(face.box), _xyxy(g[0].box)) >= SAME_CAM_IOU:
                g.append(face)
                break
        else:
            groups.append([face])
    if not groups:
        return None
    best = max(groups, key=lambda g: (len(g), float(np.mean([f.confidence for f in g]))))
    if len(findings) > 1 and len(best) < 2:
        return None
    if max(f.confidence for f in best) < CAMERA_CONFIDENCE:
        return None
    return tuple(float(v) for v in np.median(np.array([f.box for f in best]), axis=0))


def stills(clip_path: Path, n: int = 6) -> list:
    """n greyscale frames spread over the clip, STILLS_WIDTH wide."""
    import cv2

    out = []
    with video_capture(clip_path, required=False) as cap:
        if cap is None:
            return out
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        for i in range(n):
            if total:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * total / n))
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            out.append(cv2.resize(grey, (STILLS_WIDTH, round(h * STILLS_WIDTH / w))))
    return out


def _edge(profile: np.ndarray, lo: int, hi: int, near: int) -> int | None:
    """The clear border in profile[lo:hi] nearest index `near`, else None.
    Going out from the streamer, the first border crossed is the webcam's
    own; a stronger one further out belongs to something beside it."""
    if hi - lo < 3:
        return None
    # A border stands out from what is on BOTH sides of it (the webcam's
    # picture on one, whatever is beside it on the other), a few pixels away
    # so a two-pixel line isn't compared with itself.
    strong = []
    for c in range(lo, hi):
        left, right = profile[max(0, c - 7):max(0, c - 1)], profile[c + 2:c + 8]
        around = max(float(np.median(left)) if left.size else 0.0,
                     float(np.median(right)) if right.size else 0.0)
        if profile[c] >= max(SNAP_FLOOR, SNAP_STRENGTH * around):
            strong.append(c)
    if not strong:
        return None
    return min(strong, key=lambda c: abs(c - near))


def snap_to_frame(box: tuple, frames: list) -> tuple:
    """snap_details' box."""
    return snap_details(box, frames)[0]


def snap_details(box: tuple, frames: list) -> tuple[tuple, list]:
    """(box, bordered): the streamer's box grown out to the webcam overlay's own border, side by
    side, where one is clearly there, and set just inside it.

    A webcam overlay is a rectangle with a hard straight border that stays put
    while the picture inside and around it changes, so on the median of
    several frames each side shows up as a line of strong contrast along the
    whole side. Sides move OUT from the streamer (a line well inside their
    own box, like a door frame behind them, is the room; SNAP_BACK allows the
    few pixels a person box overshoots by), to the nearest such line. A side with no clear border keeps the streamer's own edge: showing a
    little less of the webcam beats showing chat beside it. This looks only
    around a box TalkNet already chose; it never goes looking for regions.

    bordered: for left, top, right, bottom, whether that side found a border
    (a side without one keeps its exact value)."""
    none = [False, False, False, False]
    if not frames:
        return box, none
    med = np.median(np.stack(frames).astype(np.float32), axis=0)
    fh, fw = med.shape
    gx = np.abs(np.diff(med, axis=1))          # contrast between columns c and c+1
    gy = np.abs(np.diff(med, axis=0))          # between rows r and r+1
    x, y, w, h = box
    x1, x2 = round(x * fw), round((x + w) * fw)
    y1, y2 = round(y * fh), round((y + h) * fh)
    out_x, out_y = max(2, round(SNAP_SEARCH * fw)), max(2, round(SNAP_SEARCH * fh))
    ix, iy = round(SNAP_INSET * fw), round(SNAP_INSET * fh)
    bx, by = round(SNAP_BACK * fw), round(SNAP_BACK * fh)
    # Along each side, the 25th percentile of the contrast: a border runs the
    # whole length of the side, a detail in the picture doesn't.
    rows = slice(max(0, y1), min(fh, y2))
    cols = slice(max(0, x1), min(fw, x2))
    col_line = np.percentile(gx[rows], 25, axis=0)     # boundary after column c
    row_line = np.percentile(gy[:, cols], 25, axis=1)  # boundary after row r

    def side(profile, edge, lo, hi, inset):
        # profile index c is the boundary between c and c+1, so the edge
        # coordinate is c + 1; `inset` moves a snapped side back inside the
        # border. The nearest border in [lo, hi) wins. None: no border.
        found = _edge(profile, max(0, lo - 1), min(len(profile), hi), edge - 1)
        return None if found is None else found + 1 + inset

    sides = [
        None if x1 <= 0 else side(col_line, x1, x1 - out_x, x1 + bx, ix),
        None if y1 <= 0 else side(row_line, y1, y1 - out_y, y1 + by, iy),
        None if x2 >= fw else side(col_line, x2, x2 - bx, x2 + out_x, -ix),
        None if y2 >= fh else side(row_line, y2, y2 - by, y2 + out_y, -iy),
    ]
    size = (fw, fh, fw, fh)
    exact = (x, y, x + w, y + h)
    nx1, ny1, nx2, ny2 = (exact[i] if v is None else v / size[i] for i, v in enumerate(sides))
    if nx2 - nx1 < 0.5 * w or ny2 - ny1 < 0.5 * h:
        return box, none                           # a snap that halves the box is not a border
    return (nx1, ny1, nx2 - nx1, ny2 - ny1), [v is not None for v in sides]


def head_in_box(clip_path: Path, box: tuple, model_name: str = "yolov8n-pose.pt", n: int = 8) -> tuple | None:
    """The streamer's head inside a webcam box over the clip, for framing
    (gaming/framing.py): (centre x, top, chin) in source px, the median of n
    frames, or None when nobody shows in it.

    This is framing, not deciding who: the box is already the streamer's
    (TalkNet's choice, or drawn by the user), so the person in it is them.
    Only the box is looked at, a little larger to catch a head at its edge."""
    import cv2

    heads = []
    with video_capture(clip_path, required=False) as cap:
        if cap is None:
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        model = _get_model(model_name)
        for i in range(n):
            if total:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * total / n))
            ok, frame = cap.read()
            if not ok:
                break
            fh, fw = frame.shape[:2]
            x, y, w, h = box
            x0, y0 = max(0, int((x - 0.1 * w) * fw)), max(0, int((y - 0.1 * h) * fh))
            x1, y1 = min(fw, int((x + 1.1 * w) * fw)), min(fh, int((y + 1.1 * h) * fh))
            if x1 - x0 < 16 or y1 - y0 < 16:
                break
            people = [d for d in _detect(model, frame[y0:y1, x0:x1], MIN_CONFIDENCE) if d[5] is not None]
            if not people:
                continue
            d = max(people, key=lambda d: d[4])            # the surest detection, never the biggest
            cx, top, chin = _head_of(d)
            heads.append((x0 + cx, y0 + top, y0 + chin))
    if not heads:
        return None
    return tuple(float(v) for v in np.median(np.array(heads), axis=0))


def _head_of(d: tuple) -> tuple:
    """(centre x, top, chin) px from one pose detection. The top is the person
    box's (hair), but not above a head's height over the eyes (a raised arm).
    Keypoints sit at the eyes and nose; the chin is about half a head width
    below them (measured on a rendered clip: 0.53)."""
    hx, hy, hw = d[5]
    return hx, max(d[1], hy - 1.0 * hw), hy + 0.55 * hw


def people_on(image_path: Path, model_name: str = "yolov8n-pose.pt") -> dict:
    """Everyone the person detector finds on one frame of a video, for the
    layout editor's face check: {"size": [w, h], "people": [{"box": [x, y, w,
    h], "head": [cx, top, chin] | None, "confidence"}]}, all normalized."""
    import cv2

    frame = cv2.imread(str(image_path))
    if frame is None:
        return {"size": [0, 0], "people": []}
    fh, fw = frame.shape[:2]
    people = []
    for d in _detect(_get_model(model_name), frame, MIN_CONFIDENCE):
        head = None
        if d[5] is not None:
            cx, top, chin = _head_of(d)
            head = [round(cx / fw, 4), round(top / fh, 4), round(chin / fh, 4)]
        people.append({"box": [round(d[0] / fw, 4), round(d[1] / fh, 4), round((d[2] - d[0]) / fw, 4),
                               round((d[3] - d[1]) / fh, 4)],
                       "head": head, "confidence": round(float(d[4]), 3)})
    return {"size": [fw, fh], "people": people}


# suggest_cam: a person counts as the same webcam across frames at this IoU,
# and must be in all but one of the frames (they are spread over the video).
SUGGEST_SAME_IOU = 0.4


def suggest_cam(image_paths: list, model_name: str = "yolov8n-pose.pt") -> list | None:
    """A starting point for the layout editor, before any processing: the
    webcam, or None.

    Only a few still frames are available here (no audio, so no TalkNet), so
    this suggests rather than decides, and the user confirms it by looking.
    What it looks for is what a webcam overlay is and a game character isn't:
    a person in the SAME place in frames spread across the whole video, small
    enough to be an overlay, inside a rectangle with its own border (the
    border snap finds at least one side). Never the biggest person, never
    anything that moves."""
    import cv2

    frames, boxes = [], []
    for path in image_paths:
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        frames.append(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (STILLS_WIDTH, round(fh * STILLS_WIDTH / fw))))
        boxes.append([((d[0] / fw, d[1] / fh, (d[2] - d[0]) / fw, (d[3] - d[1]) / fh), d[4])
                      for d in _detect(_get_model(model_name), frame, MIN_CONFIDENCE)])
    if len(frames) < 3:
        return None
    groups: list[list] = []
    for i, found in enumerate(boxes):
        for box, conf in found:
            for g in groups:
                if g[-1][0] != i and _iou(_xyxy(box), _xyxy(g[0][1])) >= SUGGEST_SAME_IOU:
                    g.append((i, box, conf))
                    break
            else:
                groups.append([(i, box, conf)])
    need = max(3, len(frames) - 1)
    steady = [g for g in groups if len({i for i, _b, _c in g}) >= need]
    # Most frames first, then the surest detections: never the biggest.
    steady.sort(key=lambda g: (-len(g), -float(np.mean([c for _i, _b, c in g]))))
    for g in steady:
        box = tuple(float(v) for v in np.median(np.array([b for _i, b, _c in g]), axis=0))
        if box[2] * box[3] > OVERLAY_MAX_AREA:
            continue                                    # a camera filling the frame: no split
        snapped, bordered = snap_details(box, frames)
        inner = _inner_sides(box)
        # Measured on 13 streams: webcam overlays had a border on 2 or 3 of
        # their inner sides (a rectangle with its own frame); VTuber avatars
        # on at most 1 (a figure with no frame around it), and they are not
        # suggested: VTubers aren't supported.
        if sum(b and i for b, i in zip(bordered, inner, strict=True)) >= min(2, sum(inner)) > 0:
            return [round(v, 4) for v in snapped]
    return None


def _inner_sides(box: tuple) -> list:
    """For left, top, right, bottom: whether that side is inside the frame
    rather than on its own edge."""
    x, y, w, h = box
    edge = 0.005
    return [x > edge, y > edge, x + w < 1 - edge, y + h < 1 - edge]


def _inside(inner: tuple, outer: tuple) -> float:
    """How much of box `inner` lies inside box `outer` (both x, y, w, h)."""
    a, b = _xyxy(inner), _xyxy(outer)
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = inner[2] * inner[3]
    return ix * iy / area if area > 0 else 0.0


def on_screen(box: tuple, finding: ClipFinding) -> bool:
    """Is somebody in this webcam in the clip (for most of it)? Inside it, not
    overlapping it: a streamer can sit small in a wide webcam."""
    return any(_inside(face.box, box) >= INSIDE for face in finding.faces.values())


def present_at(box: tuple, tracks: dict, ids: list, w: int, h: int) -> bool:
    """on_screen from the tracks alone, before (or instead of) TalkNet: the
    video's webcam is known, the question is only whether it is showing."""
    return any(_inside(webcam_box(tracks[tid], w, h)[0], box) >= INSIDE for tid in ids)
