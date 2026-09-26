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
# never become the webcam, however much they "speak"; a VTuber draws their
# box by hand once and it is kept for the creator.
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
# Webcam box margin around the head-and-shoulders region, as a fraction of it.
BOX_PAD = 0.15
# snap_to_frame: how far either side of a box edge (fraction of the frame) the
# webcam's own border is looked for, and how much stronger than the typical
# edge in that band it must be to count as one.
SNAP_SEARCH = 0.05
SNAP_STRENGTH = 3.0
SNAP_FLOOR = 6.0              # grey levels: weaker than this is no border at all
STILLS_WIDTH = 960            # stills are compared at this width
# video_cam: webcam boxes from different windows are one webcam at this IoU.
SAME_CAM_IOU = 0.5


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
    over the clip (a webcam crops the streamer at the chest, so the person box
    is roughly the webcam's content), padded and kept inside the frame."""
    boxes = np.array(track.person, dtype=np.float64)
    x1, y1, x2, y2 = np.median(boxes, axis=0)
    bw, bh = x2 - x1, y2 - y1
    x1, x2 = max(0.0, x1 - BOX_PAD * bw), min(float(w), x2 + BOX_PAD * bw)
    y1, y2 = max(0.0, y1 - BOX_PAD * bh), min(float(h), y2 + BOX_PAD * bh)
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


def _edge(profile: np.ndarray, lo: int, hi: int) -> int | None:
    """The index in profile[lo:hi] of the strongest line, if it is a clear
    border, else None."""
    band = profile[lo:hi]
    if band.size < 3:
        return None
    best = int(np.argmax(band))
    if band[best] < max(SNAP_FLOOR, SNAP_STRENGTH * float(np.median(band))):
        return None
    return lo + best


def snap_to_frame(box: tuple, frames: list) -> tuple:
    """The webcam box with each side moved onto the overlay's own border, where
    one is clearly there.

    The box from webcam_box is the streamer padded by a margin, which can fall
    short of the webcam's edge (showing less of it) or run past it (showing a
    strip of chat or the game beside it). A webcam overlay is a rectangle with
    a hard straight border that stays put while the picture inside and around
    it changes, so on the median of several frames each side shows up as a
    line of strong contrast along the whole side. A side with no such line
    (a webcam that blends into the scene, or one at the frame's edge) keeps
    the padded edge. This looks only near a box TalkNet already chose; it
    never goes looking for regions."""
    if not frames:
        return box
    med = np.median(np.stack(frames).astype(np.float32), axis=0)
    fh, fw = med.shape
    gx = np.abs(np.diff(med, axis=1))          # contrast between columns c and c+1
    gy = np.abs(np.diff(med, axis=0))          # between rows r and r+1
    x, y, w, h = box
    x1, x2 = round(x * fw), round((x + w) * fw)
    y1, y2 = round(y * fh), round((y + h) * fh)
    # Outward, up to SNAP_SEARCH of the frame. Inward, only as far as the
    # padding: a line inside the streamer's own box (a door frame behind
    # them) is the room, not the webcam's edge.
    out_x, out_y = max(2, round(SNAP_SEARCH * fw)), max(2, round(SNAP_SEARCH * fh))
    in_x = max(1, round((x2 - x1) * BOX_PAD / (1 + 2 * BOX_PAD)))
    in_y = max(1, round((y2 - y1) * BOX_PAD / (1 + 2 * BOX_PAD)))
    # Along each side, the 25th percentile of the contrast: a border runs the
    # whole length of the side, a detail in the picture doesn't.
    rows = slice(max(0, y1), min(fh, y2))
    cols = slice(max(0, x1), min(fw, x2))
    col_line = np.percentile(gx[rows], 25, axis=0)     # boundary after column c
    row_line = np.percentile(gy[:, cols], 25, axis=1)  # boundary after row r

    def side(profile, edge, lo, hi):
        found = _edge(profile, max(0, lo - 1), min(len(profile), hi - 1))
        return edge if found is None else found + 1   # the boundary sits after index `found`

    nx1 = x1 if x1 <= 0 else side(col_line, x1, x1 - out_x, x1 + in_x)
    nx2 = x2 if x2 >= fw else side(col_line, x2, x2 - in_x, x2 + out_x)
    ny1 = y1 if y1 <= 0 else side(row_line, y1, y1 - out_y, y1 + in_y)
    ny2 = y2 if y2 >= fh else side(row_line, y2, y2 - in_y, y2 + out_y)
    if nx2 - nx1 < 0.5 * (x2 - x1) or ny2 - ny1 < 0.5 * (y2 - y1):
        return box                                 # a snap that halves the box is not a border
    return (nx1 / fw, ny1 / fh, (nx2 - nx1) / fw, (ny2 - ny1) / fh)


def on_screen(box: tuple, finding: ClipFinding) -> bool:
    """Is somebody on screen at this webcam spot in the clip (for most of it)?"""
    return any(_iou(_xyxy(face.box), _xyxy(box)) >= SAME_CAM_IOU for face in finding.faces.values())


def present_at(box: tuple, tracks: dict, ids: list, w: int, h: int) -> bool:
    """on_screen from the tracks alone, before (or instead of) TalkNet: the
    video's webcam is known, the question is only whether it is showing."""
    return any(_iou(_xyxy(webcam_box(tracks[tid], w, h)[0]), _xyxy(box)) >= SAME_CAM_IOU for tid in ids)
