"""YOLOv8 + OpenCV subject tracking (v2).

Input:  a clip video file.
Output: a tracking result dict the cropper renders from:

  {"mode": "track", "path": [(t, center_x), ...]}
      Follow-the-subject mode: a smoothed horizontal crop path
      (center_x normalized 0..1).

  {"mode": "split", "webcam_box": (x, y, w, h)}
      Gameplay + facecam layout detected (all values normalized 0..1):
      render the webcam region stacked on top of a centered gameplay crop.
      Both regions are static, so this mode cannot jitter at all.

v2 upgrades over v1:
  - Identity tracking: detections are chained into tracks by IoU, so the
    system follows *people*, not per-frame boxes.
  - Target hysteresis: the camera switches subjects only when a challenger
    clearly dominates for >= 1.5s — no ping-ponging mid-conversation.
  - Two-person framing: when exactly two subjects persist close together,
    the crop frames their midpoint.
  - Pan-speed clamp: the window can never move faster than max_pan_speed
    (fraction of frame width per second) — kills whip-pans on detector noise.
  - Facecam layout detection for gameplay streams.

v3 brings over what the Podcast rebuild proved out (see video/framing.py):
  - Hold-then-move: the crop parks and only moves on genuine sustained
    drift, instead of correcting every sample. The old chain oscillated
    inside a narrow band — many small reversals that read as the camera
    wandering and never committing to a face.
  - A trailing-median target, so one bad detection box cannot yank framing.
  - Commit to the speaker: two subjects share a midpoint crop only while
    NOBODY is talking; once someone speaks the crop frames them, because a
    midpoint between two faces frames neither.
  - Cut awareness: cuts are detected every frame; the crop snaps across
    them rather than panning, and mouth-motion state is dropped so the
    talking detector doesn't score everyone a speaker after a cut. Single
    continuous-camera footage never trips this and is unaffected.

v4 follows whoever is SPEAKING, and there is exactly one thing that decides
that: TalkNet (see video/asd.py). Mouth-region pixel movement is still
measured, but only as a prominence tiebreak — it is not a speaker detector
and must never be used as one again. It measures MOVEMENT, and two people in
a lively room move about equally, so it reliably picks whoever is BIGGER.

An earlier version kept it as a fallback for when TalkNet could not run. That
was a mistake: it meant the wrong behaviour could still happen silently, on
whichever clips took the other branch, with nothing in the output to say
which detector had decided. The weights ship with the app, so there was
nothing to fall back FOR. When TalkNet has no answer nobody is named and the
crop simply stays put, which is the honest thing to do when it is not known
who is talking.

Changing subject is a CUT, never a pan (_SWITCH_CUT). Which subject, and
when, is planned for the whole clip up front rather than decided sample by
sample — see _shot_plan for why an online decision was always late.

Fully decoupled from clip selection: this module knows nothing about
transcripts, scores, or uploads.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# Shared framing components, factored out of the Podcast rebuild. Cut
# detection, "commit to one subject", and hold-then-move all apply here too.
from video.framing import HoldMove, is_cut, small_gray, stable_target

# Cut threshold for SAMPLE-grid detection. Adjacent frames barely move so the
# podcast path uses the shared default (~25); across a sample gap ordinary
# motion is much larger (a fast stream pan peaks near 40), so streams need a
# higher bar to avoid false cuts — only a genuine hard scene change (75+)
# should snap the crop and reset the talking detector.
_CUT_DIFF_SAMPLED = 55.0

_model = None  # loaded once per process; YOLO init is expensive
# The single YOLO instance is shared across parallel render threads, and
# ultralytics inference is NOT thread-safe on one model. This serializes the
# GPU inference (the GPU runs one at a time anyway) while FFmpeg encodes —
# which release the GIL in subprocesses — still overlap.
_infer_lock = threading.Lock()
# OpenCV CascadeClassifier.detectMultiScale mutates internal scale state and
# crashes when one instance is used from two threads at once. Give each
# render thread its own cascades (cheap to construct) — full parallelism, safe.
_thread_local = threading.local()


def _get_model(model_name: str):
    global _model
    with _infer_lock:
        if _model is None:
            import torch
            from ultralytics import YOLO  # lazy: heavy import, pulls in torch

            _model = YOLO(model_name)
            if torch.cuda.is_available():
                _model.to("cuda")  # explicit: detection runs on the GPU
    return _model


def _get_cascades():
    # Per-thread instances: detectMultiScale is not thread-safe on a shared one.
    cascades = getattr(_thread_local, "cascades", None)
    if cascades is None:
        frontal = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        profile = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_profileface.xml"
        )
        cascades = (frontal, profile)
        _thread_local.cascades = cascades
    return cascades


def _face_box(frame, box) -> tuple[int, int, int, int] | None:
    """Find the face inside a person box. In close-ups the person box centers
    on the torso, which can sit far from the face — the face box drives both
    framing and the talking detector.

    Detection order: frontal face -> left profile -> right profile (the
    profile cascade only knows one side, so the mirrored image covers the
    other). Returns the face box in absolute pixels, or None when no face is
    visible at all (e.g. subject facing away) — the caller then falls back
    to the person-box center, which is the best anyone can do without a face.
    """
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    rx, ry = max(0, x1), max(0, y1)
    head_h = max((y2 - y1) // 2, 40)
    roi = frame[ry : y1 + head_h, rx:x2]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    frontal, profile = _get_cascades()

    faces = frontal.detectMultiScale(gray, 1.15, 4, minSize=(36, 36))
    if len(faces) == 0:
        faces = profile.detectMultiScale(gray, 1.15, 4, minSize=(36, 36))
    if len(faces) == 0:
        flipped = profile.detectMultiScale(cv2.flip(gray, 1), 1.15, 4, minSize=(36, 36))
        if len(flipped) > 0:
            fx, fy, fw, fh = max(flipped, key=lambda f: f[2] * f[3])
            fx = gray.shape[1] - (fx + fw)  # mirror x back to the original
            faces = [(fx, fy, fw, fh)]
    if len(faces) == 0:
        return None
    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face
    return (rx + fx, ry + fy, rx + fx + fw, ry + fy + fh)


def _update_speaking(tr: "_Track", frame, face: tuple[int, int, int, int]) -> None:
    """Talking proxy: motion energy in the mouth region (lower part of the
    face box) between consecutive samples. A talking mouth changes shape
    constantly; a listening one doesn't. Comparing tracks RELATIVELY makes
    this robust to global camera motion, which inflates everyone equally."""
    fx1, fy1, fx2, fy2 = face
    my1 = fy1 + int((fy2 - fy1) * 0.55)
    mouth = frame[my1:fy2, fx1:fx2]
    if mouth.size == 0:
        return
    mouth = cv2.cvtColor(mouth, cv2.COLOR_BGR2GRAY)
    mouth = cv2.resize(mouth, (48, 24)).astype(np.float32) / 255.0
    if tr.prev_mouth is not None:
        motion = float(np.abs(mouth - tr.prev_mouth).mean())
        tr.speak = 0.65 * tr.speak + 0.35 * motion
    tr.prev_mouth = mouth


# ---- who is actually speaking -------------------------------------------------

_SYNC_WINDOW = 16        # samples (2s at 8fps): long enough to correlate,
                         # short enough to still be about *now*



def speech_envelope(clip_path: Path, sample_fps: float) -> "np.ndarray | None":
    """Loudness of the clip's audio, one value per video sample.

    Mouth-region motion alone cannot tell talking from any other movement: in
    a lively two-person shot both people score within a few percent of each
    other, so whoever is bigger wins by default. The audio knows when speech
    is actually happening, and only the person producing it moves their mouth
    in time with it.

    None when the clip has no audio, which is not an error — the caller falls
    back to the visual-only behaviour.
    """
    sr = 16000
    pcm = _clip_pcm(clip_path, sr)
    if pcm is None:
        return None
    if pcm.size < sr // 4:
        return None
    per = max(1, int(sr / sample_fps))
    bins = pcm.size // per
    if bins < _SYNC_WINDOW:
        return None
    return np.sqrt(np.array([
        np.mean(pcm[i * per:(i + 1) * per] ** 2) for i in range(bins)
    ]))


# ---- who is actually speaking, asked properly ---------------------------------
#
# Everything above is a correlation between pixel movement and loudness. It
# was built because the alternative looked expensive, and it does work — but
# it is a proxy, and it is only sure about a quarter of the time. TalkNet was
# trained on labelled footage to answer this exact question, so where it can
# answer, it answers, and the correlation above stays as the fallback for
# clips with no audio or an unavailable model.

_ASD_MIN_FACE_SAMPLES = 8    # a track needs this many face sightings to be
                             # worth cropping 25fps frames for
_ASD_MAX_TRACKS = 4          # crops are held in memory; four faces is already
                             # more than any framing decision needs
_ASD_MAX_GAP = 0.6           # seconds: past this, a face box is too stale to
                             # interpolate through and the track counts absent
_ASD_LEAD = 0.5              # the winner's logit must beat the runner-up by
                             # this much before it counts as a verdict
_ASD_SPEECH = 0.35           # ...and the clip must be this loud, relative to
                             # its own 90th percentile, for anyone to BE
                             # speaking. See _asd_verdict.

# ---- how the camera behaves once it knows who is speaking ---------------------
#
# Knowing the speaker is only half of it; the other half is that a viewer
# judges the RESULT, and the result was wrong in three ways that have nothing
# to do with who was picked. Watched back on real two-person footage, the crop
# slid between people instead of cutting, flicked back and forth during a
# quick exchange, and sat between the two of them so that each face was
# jammed against an edge with the floor filling the middle.
#
# The podcast path already solved all three, because it was rebuilt against
# real footage: it cuts at shot boundaries, never pans, and frames ONE
# person's face rather than the midpoint of two. These bring the same
# behaviour to the ordinary tracker.
_SWITCH_HOLD = 1.5      # seconds a subject keeps the camera before another
                        # verdict can take it. A conversation trades the
                        # verdict about once a second; honouring every trade
                        # reads as flicking. An editor lets a shot sit.
_SWITCH_CUT = True      # changing subject is a CUT, not a pan. Panning across
                        # a room to find the next speaker takes the viewer with
                        # it and arrives late; a cut is there on the first
                        # frame. This is how the footage itself is edited.


def _clip_pcm(clip_path: Path, sr: int) -> "np.ndarray | None":
    """The clip's audio as mono float32. None when it has none."""
    import subprocess

    from core.binaries import ffmpeg

    try:
        out = subprocess.run(
            [ffmpeg(), "-v", "error", "-i", str(clip_path),
             "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
            capture_output=True, timeout=300,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as e:
        print(f"      (clip audio unavailable: {e})")
        return None


def _interp_boxes(seen: list, times: "np.ndarray") -> "np.ndarray | None":
    """A face box for every time in `times`, interpolated from the samples
    where the face was actually detected.

    `seen` is [(t, (x1, y1, x2, y2)), ...] in time order. Rows outside the
    track's own span, or across a gap longer than _ASD_MAX_GAP, come back NaN:
    the person is not there to be cropped, and a stale box would feed the
    model somebody else's face.
    """
    ts = np.array([s[0] for s in seen], dtype=np.float64)
    box = np.array([s[1] for s in seen], dtype=np.float64)
    out = np.empty((len(times), 4), dtype=np.float64)
    for k in range(4):
        out[:, k] = np.interp(times, ts, box[:, k])
    # np.interp clamps outside the range; NaN those, plus anything sitting in
    # a long gap between two sightings.
    nearest = np.abs(times[:, None] - ts[None, :]).min(axis=1)
    out[(times < ts[0]) | (times > ts[-1]) | (nearest > _ASD_MAX_GAP)] = np.nan
    return out


def _mouth_patch(gray, box, size: int):
    """The square TalkNet expects, cut from a greyscale frame around `box`.

    Not the face box. TalkNet's own pipeline takes a region about 0.7x the
    face box, shifted DOWN so it is centred on the mouth rather than the eyes,
    which is what a lip-sync model is looking at. Feeding it a plain face crop
    is feeding it a different picture than it was trained on.

    Out-of-frame edges are padded with mid-grey (110) exactly as upstream does
    — clipping instead would squash the face when someone reaches the edge of
    the shot, and a squashed face is a face the model has never seen.
    """
    if np.isnan(box[0]):
        return None
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    bs = max(box[2] - box[0], box[3] - box[1]) / 2
    if bs < 6:
        return None
    half = 0.7 * bs
    cy += 0.4 * bs           # down onto the mouth
    x0, y0 = int(round(cx - half)), int(round(cy - half))
    side = max(8, int(round(2 * half)))

    h, w = gray.shape[:2]
    patch = np.full((side, side), 110, dtype=np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w, x0 + side), min(h, y0 + side)
    if sx1 - sx0 < 4 or sy1 - sy0 < 4:
        return None
    patch[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = gray[sy0:sy1, sx0:sx1]
    return cv2.resize(patch, (size, size))


def _contested_spans(mask, fps: int, n: int) -> list:
    """The stretches worth scoring, as (start, end) frame indices.

    `mask` marks frames where two or more people are on screen. Runs shorter
    than a second are grown to a second and near neighbours are merged,
    because the model's shortest window is a second and a run of six frames
    would otherwise be scored on padding.
    """
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    gap = fps  # a second apart or less: one span, not two
    breaks = np.flatnonzero(np.diff(idx) > gap)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]] + 1

    spans = []
    for a, b in zip(starts, ends):
        a, b = int(a), int(b)
        if b - a < fps:
            a = max(0, a - (fps - (b - a)) // 2)
            b = min(n, a + fps)
            a = max(0, b - fps)
        if spans and a <= spans[-1][1]:      # grew into its neighbour
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    return [(a, b) for a, b in spans if b - a >= fps]


def _asd_speakers(clip_path: Path, samples: list, video_fps: float) -> list | None:
    """Per sample, how strongly each visible person is speaking, from TalkNet.

    Returns a list parallel to `samples` of {track_id: logit}, or None when
    the question does not arise or cannot be answered — no model, no audio, or
    fewer than two faces on screen, which is the one skip worth having because
    it is not a judgement: with one face there is nobody to choose between.

    The cost is in getting frames to the model, not in the model. Reusing the
    boxes tracking already found and interpolating them onto a 25fps grid adds
    about 15% to a clip; re-running the detector at 25fps to get the same crops
    adds 452%. Do not "simplify" this into a second detection pass.
    """
    from video import asd

    if not asd.available() or not samples:
        return None

    seen_by_track: dict[int, list] = {}
    for s in samples:
        for tid, fb in s.faces.items():
            seen_by_track.setdefault(tid, []).append((s.t, fb))
    candidates = [t for t, v in seen_by_track.items() if len(v) >= _ASD_MIN_FACE_SAMPLES]
    if len(candidates) < 2:
        return None
    # Most-seen first, so a capped list keeps the people who are actually in
    # the clip rather than whoever the tracker noticed first.
    candidates.sort(key=lambda t: -len(seen_by_track[t]))
    candidates = candidates[:_ASD_MAX_TRACKS]

    pcm = _clip_pcm(clip_path, asd.AUDIO_SR)
    if pcm is None or pcm.size < asd.AUDIO_SR // 4:
        return None

    duration = samples[-1].t
    n_frames = int(duration * asd.FPS)
    if n_frames < asd.FPS:  # under a second of video: nothing to decide
        return None
    times = np.arange(n_frames, dtype=np.float64) / asd.FPS
    boxes = {tid: _interp_boxes(seen_by_track[tid], times) for tid in candidates}
    present = {tid: ~np.isnan(boxes[tid][:, 0]) for tid in candidates}

    # ---- only where the answer can change anything ------------------------
    # Reading frames and scoring them IS the cost, so both run only over the
    # stretches where two or more candidates are on screen at the same time.
    # Everywhere else there is one person to frame and the verdict changes
    # nothing, so scoring it would be paying for an answer nobody reads. On a
    # real 60s two-person clip this is about a quarter of the frames.
    #
    # This is the same skip as the "fewer than two faces" one above, measured
    # per moment instead of per clip — not a guess about who is speaking.
    together = np.sum(list(present.values()), axis=0) >= 2
    spans = _contested_spans(together, asd.FPS, n_frames)
    if not spans:
        return None

    # ---- one pass over the video, cropping every candidate per frame ------
    size = asd.FACE_SIZE
    crops = {tid: np.zeros((n_frames, size, size), dtype=np.uint8) for tid in candidates}
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return None
    try:
        wanted = np.rint(times * video_fps).astype(np.int64)
        needed = np.zeros(n_frames, dtype=bool)
        for a, b in spans:
            needed[a:b] = True
        idx, j = 0, 0
        while j < n_frames:
            if not needed[j]:
                # Skip forward without decoding: grab() alone does not
                # reconstruct the picture, which is where the time goes.
                while j < n_frames and not needed[j]:
                    j += 1
                if j >= n_frames:
                    break
            ok = cap.grab()
            if not ok:
                break
            if idx == wanted[j]:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Several 25fps slots can map to one source frame on low-fps
                # video; fill all of them from this decode rather than
                # re-reading it.
                while j < n_frames and wanted[j] == idx:
                    for tid in candidates:
                        patch = _mouth_patch(gray, boxes[tid][j], size)
                        if patch is not None:
                            crops[tid][j] = patch
                    j += 1
            idx += 1
    finally:
        cap.release()

    # ---- score each contested stretch, on its own slice of the audio ------
    # -inf everywhere else: a frame nobody scored is not a quiet frame, it is
    # a frame with no answer, and the two must not be confused.
    scores = {tid: np.full(n_frames, -np.inf, dtype=np.float32) for tid in candidates}
    per_frame = asd.AUDIO_SR / asd.FPS
    for a, b in spans:
        chunk = pcm[int(a * per_frame):int(b * per_frame)]
        if chunk.size < asd.AUDIO_SR // 4:
            continue
        for tid in candidates:
            try:
                s = asd.score_track(crops[tid][a:b], chunk)
            except Exception as e:  # a bad clip must not fail a render
                print(f"      (speaker detection failed, using motion instead: {e})")
                return None
            s = np.asarray(s, dtype=np.float32)[: b - a]
            s[~present[tid][a:a + len(s)]] = -np.inf
            scores[tid][a:a + len(s)] = s

    # ---- back onto the tracker's own sample grid --------------------------
    out = []
    for s in samples:
        j = min(n_frames - 1, int(round(s.t * asd.FPS)))
        out.append({tid: float(scores[tid][j]) for tid in candidates
                    if np.isfinite(scores[tid][j])})
    return out


def _shot_plan(asd_scores: list, samples: list, env, floor: float,
               min_shot: float) -> list:
    """Who the camera should be on at each sample, decided for the WHOLE clip
    at once. One entry per sample; None where nobody is known to be speaking.

    This exists because the framing used to be decided online, sample by
    sample, from a rolling vote — and an online decision is always late. The
    clip opened on whoever was BIGGEST, because at sample zero no verdict had
    arrived yet, and then corrected once enough votes accumulated. Watched
    back, that is a shot that starts on the wrong person and switches
    mid-sentence. The switch back was worse: the same lag plus a hold.

    None of that latency is necessary. TalkNet has already scored every sample
    before framing begins, so the whole timeline is known — the same thing an
    editor has in front of them. They do not discover the speaker three beats
    after the audience does.

    Short runs are merged into their neighbour rather than being refused, so a
    minimum shot length falls out of the plan instead of being enforced by
    declining to switch. Refusing a switch keeps the camera on the wrong
    person; merging moves the boundary.
    """
    n = len(samples)
    if not asd_scores or n == 0:
        return [None] * n

    # 1. the raw per-sample opinion, where there is one
    raw: list = []
    for i, s in enumerate(samples):
        loud = env is None or i >= len(env) or env[i] >= floor
        raw.append(_asd_verdict(asd_scores[i], s.visible, loud))

    # 2. hold the last speaker through gaps — silence between two sentences
    #    from the same person is not a reason to go anywhere
    held: list = []
    current = None
    for v in raw:
        if v is not None:
            current = v
        held.append(current)
    # and backfill the opening, so the clip starts on whoever speaks first
    # rather than on whoever happens to be largest
    first = next((v for v in held if v is not None), None)
    held = [v if v is not None else first for v in held]
    if first is None:
        return [None] * n

    # 3. merge runs shorter than a shot into whatever precedes them
    dt = (samples[-1].t - samples[0].t) / max(1, n - 1)
    min_len = max(1, int(round(min_shot / dt)) if dt > 0 else 1)
    out = list(held)
    i = 0
    while i < n:
        j = i
        while j < n and out[j] == out[i]:
            j += 1
        if j - i < min_len and i > 0:
            for k in range(i, j):
                out[k] = out[i - 1]
        i = j
    return out


def _asd_verdict(frame_scores: dict, visible, loud: bool) -> int | None:
    """Who TalkNet says is speaking here, or None when it is not sure.

    Same contract as _sync_leader: None is the common answer and means "no new
    information", never "reframe". Only visible people can win, and only by a
    clear margin — the logits of two quiet faces sit close together, and
    following the marginally-less-quiet one is exactly the wandering this is
    supposed to stop.

    The margin is the whole test; there is deliberately no "is this above
    zero" floor. The logit's absolute level moves with how well the crop
    matches what the model was trained on, and these crops are built from
    pose keypoints rather than the detector upstream used, so on real footage
    the winning face sat around -0.8 while still beating the other person
    consistently. A floor there rejected every verdict in a minute of
    conversation. What the score IS good for is ranking faces against each
    other at the same instant, which is exactly the question being asked.

    `loud` is what replaces the floor, and it comes from the audio rather than
    from the model: during silence nobody is speaking, so no margin between
    two quiet faces means anything.
    """
    if not loud:
        return None
    ranked = sorted(
        ((sc, tid) for tid, sc in frame_scores.items() if tid in visible),
        reverse=True,
    )
    if not ranked:
        return None
    if len(ranked) == 1:
        return ranked[0][1]
    return ranked[0][1] if ranked[0][0] - ranked[1][0] > _ASD_LEAD else None


@dataclass
class _Sample:
    """One processed frame, kept so the framing decision can be made after
    TalkNet has run rather than before.

    Tracking is a streaming pass, but "who is talking" cannot be answered
    streaming: the model needs a whole face track and the audio beside it. So
    the frame pass records what it saw, TalkNet answers, and the framing pass
    replays these in order. Track state (box, dominance, speak) is snapshotted
    per sample because it is a moving average — replaying against the final
    value would frame the start of the clip using what was learned by the end.
    """
    t: float
    frame_idx: int
    w: int
    h: int
    at_cut: bool
    visible: list
    state: dict     # tid -> _Moment: everything the framing pass reads
    faces: dict     # tid -> Haar face box, only where one was detected


@dataclass
class _Moment:
    """One track's mutable state at one sample.

    Every field here is either a moving average or the LENGTH of a growing
    history, and every one of them is read by the framing pass. Leaving any
    of them out does not fail — it silently replays the whole clip against
    the values the track ended with. n_centers is the subtle one: the framing
    target is a trailing median of the last few centers, so an untruncated
    list makes every sample in the clip aim at where the subject finished.
    """
    box: tuple
    dominance: float
    speak: float
    n_seen: int
    head_rate: float
    face_rate: float
    head_offset: float
    face_offset: float
    face_w: float
    n_centers: int


@dataclass
class _Track:
    box: tuple                     # last (x1, y1, x2, y2, conf, head) in pixels
    last_t: float
    dominance: float = 0.0         # EMA of confidence x area
    speak: float = 0.0             # EMA of mouth-region motion (talking proxy)
    prev_mouth: object = None      # last mouth crop (np array) for motion diff
    face_rate: float = 0.0         # EMA of "was a face detected this sample?"
    face_offset: float = 0.0       # EMA of (face cx - body cx), normalized
    face_w: float = 0.0            # EMA of face box width, normalized
    head_rate: float = 0.0         # EMA of "pose head keypoints seen this sample?"
    head_offset: float = 0.0       # EMA of (head cx - body cx), normalized
    head_cys: list = field(default_factory=list)  # normalized head center-y history
    n_seen: int = 0
    centers: list = field(default_factory=list)   # normalized cx history
    areas: list = field(default_factory=list)     # area fraction history
    norm_boxes: list = field(default_factory=list)  # normalized (x1, y1, x2, y2)


def compute_tracking(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    sample_fps: float = 8.0,
    smoothing: float = 0.45,
    dead_zone: float = 0.03,
    max_pan_speed: float = 0.30,   # max window movement, frame-widths/second
    min_confidence: float = 0.4,
    switch_margin: float = 1.5,    # challenger must dominate by this factor...
    switch_seconds: float = 1.5,   # ...for this long before the camera switches
    fit_blur_fraction: float = 0.5,   # letterbox only when MOST of the clip
                                      # genuinely can't fit a 9:16 crop
    force_fit_blur: bool = False,     # user override: always letterbox, cropped
                                      # tight to the subject like the automatic one
) -> dict:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, round(video_fps / sample_fps))
    model = _get_model(model_name)

    tracks: dict[int, _Track] = {}
    active_id: int | None = None
    challenger_id: int | None = None
    challenger_since = 0.0

    # dead_zone becomes the distance the subject may drift before the camera
    # bothers to move; settling well inside it is what makes a move look
    # finished rather than endlessly corrected.
    pan = HoldMove(
        move_trigger=max(dead_zone, 0.05),
        settle=dead_zone * 0.4,
        smoothing=smoothing,
        max_pan_speed=max_pan_speed,
    )
    # The audio, one value per sample. Mouth motion alone cannot separate a
    # talker from a listener who is simply moving; matching each person's
    # mouth against the sound can. None for a silent clip, which just means
    # the visual-only behaviour below.
    env = speech_envelope(clip_path, sample_fps)

    # What each sample saw, so framing can be decided after TalkNet has had a
    # look rather than guessed at during the frame pass. See _Sample.
    samples: list[_Sample] = []

    path: list[tuple[float, float]] = []
    smoothed_x: float | None = None
    prev_small = None            # previous frame's thumbnail, for cut detection
    last_sample_t = 0.0          # real time of the last processed sample
    n_samples = 0
    wide_boxes: list[tuple[float, float, float, float]] = []  # subject bbox when a
    #                                             9:16 crop can't hold it (normalized)
    frame_idx = 0

    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_idx % frame_step != 0:
            frame_idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break

        t = frame_idx / video_fps
        h, w = frame.shape[:2]
        n_samples += 1

        # ---- camera cuts, checked on the SAMPLE grid ---------------------
        # Only sample frames are decoded. Decoding every frame just to spot
        # the occasional cut doubled processing time on long streams — and
        # streams are one continuous camera that almost never cuts. Between
        # samples there is far more motion than between adjacent frames, so
        # the threshold is raised well above that motion (a fast pan peaks
        # around 40; a real hard cut is 75+): it fires only on an unmistakable
        # scene change, so a continuous stream never trips it, while edited
        # sources (talking heads, highlight reels) still snap at their cuts.
        cur_small = small_gray(frame)
        at_cut = is_cut(prev_small, cur_small, _CUT_DIFF_SAMPLED)
        prev_small = cur_small
        if at_cut:
            # Per-subject state belongs to the shot that just ended. Mouth
            # motion especially: across a cut every mouth region changes at
            # once, so the talking detector would score everyone a speaker.
            for tr in tracks.values():
                tr.prev_mouth = None
                tr.speak = 0.0
            # NB: dropping the challenger at a cut belongs to the framing
            # pass, not here — see the loop below. Doing it in this pass sets
            # a variable that pass never reads.

        visible = _assign(tracks, _detect(model, frame, min_confidence), t)
        faces_here: dict[int, tuple] = {}   # for TalkNet, after the pass
        for tid in visible:
            tr = tracks[tid]
            x1, y1, x2, y2, conf = tr.box[:5]
            area_frac = (x2 - x1) * (y2 - y1) / (w * h)
            tr.dominance = 0.7 * tr.dominance + 0.3 * (conf * area_frac)
            tr.n_seen += 1
            # Anti-jitter center: the body box is always stable; the HEAD
            # refines it as a SMOOTHED OFFSET from the body center. The head
            # position comes from pose keypoints (nose/eyes/ears) — these
            # work even when the face is wet, tilted, turned, or too small
            # for the face detector (pool/beach/action shots), so the crop
            # keeps priority on the head over the body. The Haar face box is
            # the fallback signal and still drives the talking detector.
            body_cx = ((x1 + x2) / 2) / w
            head = tr.box[5] if len(tr.box) > 5 else None
            if head is not None:
                head_cx, head_cy, head_w = head
                tr.head_rate = 0.8 * tr.head_rate + 0.2
                tr.head_offset = 0.7 * tr.head_offset + 0.3 * (head_cx / w - body_cx)
                tr.face_w = 0.7 * tr.face_w + 0.3 * (head_w / w)
                tr.head_cys.append(head_cy / h)
            else:
                tr.head_rate = 0.8 * tr.head_rate
            face = _face_box(frame, tr.box)
            if face is not None:
                faces_here[tid] = face
            elif head is not None:
                # The Haar cascade only finds a face on a minority of samples
                # — it wants a frontal, well-lit, reasonably large face, and
                # people in conversation are turned away half the time. Pose
                # keypoints put the head somewhere on nearly every sample, and
                # a head square is a good enough box for the mouth patch. The
                # first version of this used Haar alone and TalkNet ended up
                # scoring a fifth of the clip.
                hcx, hcy, hw = head
                faces_here[tid] = (hcx - hw / 2, hcy - hw / 2,
                                   hcx + hw / 2, hcy + hw / 2)
            if face is not None:
                fx1, fy1, fx2, fy2 = face
                face_cx = ((fx1 + fx2) / 2) / w
                tr.face_rate = 0.8 * tr.face_rate + 0.2
                tr.face_offset = 0.7 * tr.face_offset + 0.3 * (face_cx - body_cx)
                if head is None:
                    tr.face_w = 0.7 * tr.face_w + 0.3 * ((fx2 - fx1) / w)
                    tr.head_cys.append(((fy1 + fy2) / 2) / h)
                _update_speaking(tr, frame, face)
            else:
                tr.face_rate = 0.8 * tr.face_rate  # detection getting unreliable
                tr.speak *= 0.9  # no visible face: talking evidence fades
            # Framing priority: pose head keypoints > face box > body center.
            if tr.head_rate > 0.3:
                refine = tr.head_offset
            elif tr.face_rate > 0.45:
                refine = tr.face_offset
            else:
                refine = 0.0
            tr.centers.append(body_cx + refine)
            tr.areas.append(area_frac)
            tr.norm_boxes.append((x1 / w, y1 / h, x2 / w, y2 / h))

        samples.append(_Sample(
            t=t, frame_idx=frame_idx, w=w, h=h, at_cut=at_cut,
            visible=list(visible),
            state={tid: _Moment(
                box=tracks[tid].box,
                dominance=tracks[tid].dominance,
                speak=tracks[tid].speak,
                n_seen=tracks[tid].n_seen,
                head_rate=tracks[tid].head_rate,
                face_rate=tracks[tid].face_rate,
                head_offset=tracks[tid].head_offset,
                face_offset=tracks[tid].face_offset,
                face_w=tracks[tid].face_w,
                n_centers=len(tracks[tid].centers),
            ) for tid in visible},
            faces=faces_here,
        ))
        frame_idx += 1

    cap.release()

    # ---- who is speaking, before deciding where to point ------------------
    # None when TalkNet cannot or need not answer (no model, no audio, fewer
    # than two faces); the mouth-motion correlation below covers those.
    asd_scores = _asd_speakers(clip_path, samples, video_fps)
    # How loud this clip has to be for someone to be speaking, judged against
    # its own level rather than an absolute one — a quiet phone recording and
    # a studio mic are both fine, and room tone is well below either's speech.
    speech_floor = (
        _ASD_SPEECH * float(np.percentile(env, 90)) if env is not None else 0.0
    )
    # Who to be on, for every sample, decided up front. See _shot_plan.
    shots = _shot_plan(asd_scores, samples, env, speech_floor, _SWITCH_HOLD)

    # Track state is about to be rewound sample by sample. Everything after
    # this loop — the letterbox decision, the facecam layout, face_y — reads
    # the FINAL values, so they are put back at the end.
    finals = {tid: _Moment(
        box=tr.box, dominance=tr.dominance, speak=tr.speak, n_seen=tr.n_seen,
        head_rate=tr.head_rate, face_rate=tr.face_rate,
        head_offset=tr.head_offset, face_offset=tr.face_offset,
        face_w=tr.face_w, n_centers=len(tr.centers),
    ) for tid, tr in tracks.items()}
    all_centers = {tid: tr.centers for tid, tr in tracks.items()}

    def rewind(tid: int, m: _Moment) -> None:
        tr = tracks[tid]
        (tr.box, tr.dominance, tr.speak, tr.n_seen, tr.head_rate, tr.face_rate,
         tr.head_offset, tr.face_offset, tr.face_w) = (
            m.box, m.dominance, m.speak, m.n_seen, m.head_rate, m.face_rate,
            m.head_offset, m.face_offset, m.face_w)
        # The list is truncated rather than the reads changed, because the
        # framing helpers take a _Track and there is no honest way for them
        # to know which sample is being replayed.
        tr.centers = all_centers[tid][:m.n_centers]


    for s_idx, s in enumerate(samples):
        t, w, h, at_cut, visible = s.t, s.w, s.h, s.at_cut, s.visible
        frame_idx = s.frame_idx
        was_active = active_id
        for tid, snap in s.state.items():
            rewind(tid, snap)
        if at_cut:
            # A challenger was building a case about the shot that just
            # ended; across a cut it means nothing.
            challenger_id = None

        if visible:
            # ---- choose the target, with hysteresis ----------------------
            # Who to follow = size/confidence dominance x WHO IS TALKING.
            # Mouth-region motion is the talking proxy, so in group shots the
            # camera prefers the speaker, not just the biggest person.
            max_speak = max((tracks[tid].speak for tid in visible), default=0.0)

            # max_speak is bound as a default so the closure cannot pick up a
            # later iteration's value. It is consumed immediately below, so
            # this is defensive rather than a fix — but the day someone stores
            # this function instead of calling it, the bug would be subtle.
            def _score(tid: int, max_speak: float = max_speak) -> float:
                tr = tracks[tid]
                if max_speak < 0.004:  # nobody visibly talking: size decides
                    return tr.dominance
                return tr.dominance * (0.35 + 0.65 * (tr.speak / max_speak))

            # Who the shot plan says to be on here, if it says anything. The
            # plan was built from the whole clip before this loop started, so
            # there is no lag to make up and no opening guess to correct —
            # see _shot_plan.
            best_voice = shots[s_idx] if shots[s_idx] in visible else None

            top = max(visible, key=_score)
            active_gone = (
                active_id is None
                or active_id not in tracks
                or (active_id not in visible and t - tracks[active_id].last_t > 1.0)
            )
            if active_gone:
                # Nobody to keep. Prefer whoever the plan says is talking;
                # otherwise the most prominent person, which is all there is
                # to go on. On the first sample the plan already knows who
                # speaks first, so this no longer opens on the biggest person.
                active_id, challenger_id = (best_voice or top), None
            elif best_voice is not None and best_voice != active_id:
                # The speaker OVERRIDES size. The old rule multiplied talking
                # BY prominence, so a person 1.4x wider on screen could not be
                # interrupted however much the other one talked.
                #
                # No hold is needed here any more: the plan has already merged
                # runs shorter than _SWITCH_HOLD, so a switch that reaches this
                # point is one that lasts. Holding on top of that only kept the
                # camera on the wrong person.
                active_id, challenger_id = best_voice, None
            elif best_voice is not None:
                challenger_id = None  # the person we are on is the one talking
            elif top != active_id and _score(top) > switch_margin * _score(active_id):
                # No audio verdict from anyone. Fall back to the prominence
                # contest, which is right when nobody is speaking (an action
                # beat, a silent reaction) and unreachable when someone is.
                if challenger_id != top:
                    challenger_id, challenger_since = top, t
                elif t - challenger_since >= switch_seconds:
                    active_id, challenger_id = top, None  # sustained takeover
            else:
                challenger_id = None

            crop_frac = (h * 9 / 16) / w  # crop width as fraction of frame width
            raw_x = _target_x(
                tracks, visible, active_id, crop_frac,
                someone_talking=max_speak >= 0.004,
            )

            # ---- when is a plain 9:16 crop NOT enough? -------------------
            # A single upright person is ALWAYS fine as a normal crop — we just
            # center on their face/torso, even if their shoulders are wider
            # than the narrow 9:16 window (that's normal for a talking-head
            # Short). The letterbox is only for cases a vertical crop genuinely
            # can't hold:
            #   * TWO+ real people spread wider than the crop, or
            #   * a SINGLE person lying down (box wider than tall).
            # Minor/background/low-confidence detections are ignored so a
            # motorcycle or a bystander never forces it.
            if active_id in tracks:
                # Measure co-subjects against the most prominent person on
                # screen, NOT against whoever the camera happens to be on.
                #
                # This used to be the active subject's dominance, which was
                # the same number back when the camera always sat on the
                # biggest person. It is not the same number now that it
                # follows the speaker: point at the smaller person and the
                # yardstick shrinks, so "at least 0.75 of it" lets in people
                # who were never near-equals, they read as two subjects
                # spread wide, and the clip silently becomes a letterbox.
                # Real footage flipped from track to fit_blur for exactly
                # this reason, with nothing about the scene having changed.
                #
                # Whether a 9:16 crop can hold the scene is a fact about the
                # scene. It must not move when the choice of subject moves.
                active_dom = max(tracks[tid].dominance for tid in visible)
                subjects = [
                    tracks[tid].box
                    for tid in visible
                    # A co-subject must be a NEAR-EQUAL of the main subject —
                    # prominent, persistent through the clip, and confidently
                    # a person. Swimmers/bystanders drifting through a pool
                    # shot must never drag a centered creator into letterbox.
                    if (tracks[tid].box[2] - tracks[tid].box[0])
                    * (tracks[tid].box[3] - tracks[tid].box[1])
                    / (w * h)
                    > 0.06
                    and (
                        tid == active_id
                        or (
                            tracks[tid].dominance >= 0.75 * active_dom
                            # s_idx + 1 is how many samples have gone by at
                            # this point, which is what this used to compare
                            # against when it ran inside the frame loop.
                            and tracks[tid].n_seen >= max(8, 0.3 * (s_idx + 1))
                        )
                    )
                    and tracks[tid].box[4] >= 0.6  # confidently a person
                ]
                # Two people sitting apart is NOT a reason to letterbox.
                #
                # It used to be: if they were spread wider than a 9:16 window
                # for most of the clip, the whole thing became a letterbox
                # holding both. On real footage that meant a crop 72% of the
                # source width, shown as a 1080x636 strip — 67% of the screen
                # blurred, both people small, and the space between them
                # occupying the middle of the shot. It framed neither of them.
                #
                # That rule was written when the camera could not tell who was
                # talking, so showing everyone was the safe answer. It can tell
                # now. Two people spread wide is the case for CUTTING between
                # them, which is what the footage itself does and what the
                # vertical format is for.
                is_wide = False
                if len(subjects) == 1:
                    b = subjects[0]
                    bw, bh = b[2] - b[0], b[3] - b[1]
                    # Letterbox a SINGLE person only when they are clearly lying
                    # FLAT (box 2x+ wider than tall) — a genuinely horizontal
                    # head-to-toe pose a vertical crop would cut. Seated,
                    # reclined, arms-out, or close-up people (box near square or
                    # taller) are always a normal crop centered on the face.
                    is_wide = bw > bh * 2.0 and bw / w > crop_frac * 1.4
                if is_wide:
                    wide_boxes.append((
                        min(b[0] for b in subjects) / w,
                        min(b[1] for b in subjects) / h,
                        max(b[2] for b in subjects) / w,
                        max(b[3] for b in subjects) / h,
                    ))

            # ---- hold still, or move deliberately ------------------------
            # Previously this corrected toward the target on every sample, so
            # the crop spent its life making small adjustments it immediately
            # part-undid — an oscillation inside a narrow band that reads as
            # drift. HoldMove keeps the frame parked until the subject has
            # genuinely moved, then makes one purposeful move and settles.
            changed_subject = _SWITCH_CUT and active_id != was_active and was_active is not None
            if at_cut or changed_subject:
                # Freeze the outgoing framing on the last frame of the old
                # shot, then jump. Easing across a cut is never right — and
                # changing who the camera is on IS a cut, even though the
                # footage itself did not cut. Panning there instead sweeps the
                # viewer across the room and arrives after the person has
                # started talking; the whole reason to know who is speaking is
                # to be on them from the first word.
                if pan.x is not None and frame_idx > 0:
                    path.append(((frame_idx - 1) / video_fps, float(pan.x)))
                smoothed_x = pan.snap(raw_x)
            else:
                # Containment is a TRIGGER, not a position override. If the
                # head is nearing the edge of the window, stop holding and
                # move to re-center it. (Overriding the position after the
                # fact instead meant the clamp and the controller fought each
                # other every sample — the clamp nudged the crop back to the
                # boundary, the controller tried to hold, and the crop
                # chattered. Moving to the centered target resolves it once.)
                if pan.x is not None and _head_escaping(
                    tracks, active_id, pan.x, crop_frac, w
                ):
                    pan.moving = True
                smoothed_x = pan.update(raw_x, max(t - last_sample_t, 1e-6))
            last_sample_t = t

            # ---- last-resort containment ---------------------------------
            # The trigger above normally re-centers the subject long before
            # this matters. This only catches the case where they outran the
            # pan-speed clamp, and guarantees the face is never cut off.
            if active_id in tracks and (
                tracks[active_id].head_rate > 0.3 or tracks[active_id].face_rate > 0.45
            ):
                atr = tracks[active_id]
                face_cx = _head_cx(atr, w)
                tol = _contain_tolerance(atr, crop_frac)
                if face_cx < smoothed_x - tol:
                    smoothed_x = face_cx + tol
                elif face_cx > smoothed_x + tol:
                    smoothed_x = face_cx - tol
                # The clamp is the real output, so the controller has to adopt
                # it — otherwise it keeps holding against a position the crop
                # no longer has and the next move starts from a stale place.
                pan.x = float(smoothed_x)

            path.append((t, float(smoothed_x)))

    for tid, snap in finals.items():
        rewind(tid, snap)

    # User-forced letterbox: same tight subject-region crop the automatic
    # letterbox uses (person large, minimal dead space) — just without the
    # "won't fit a 9:16 crop" trigger. Falls back to the full frame only
    # when no person was ever detected.
    if force_fit_blur:
        if active_id in tracks and tracks[active_id].norm_boxes:
            boxes = np.array(tracks[active_id].norm_boxes)
            # Horizontal: the subject's TYPICAL width centered where they
            # usually are — NOT the union of every position. A person leaning
            # around would otherwise stretch the region wide, and a wide
            # region means a short (small) letterbox video.
            widths = boxes[:, 2] - boxes[:, 0]
            half = float(np.percentile(widths, 70)) / 2 + 0.05
            cx = float(np.median((boxes[:, 0] + boxes[:, 2]) / 2))
            return {
                "mode": "fit_blur",
                "region": (
                    round(max(0.0, cx - half), 4),
                    round(max(0.0, float(np.percentile(boxes[:, 1], 10)) - 0.06), 4),
                    round(min(1.0, cx + half), 4),
                    round(min(1.0, float(np.percentile(boxes[:, 3], 90)) + 0.06), 4),
                ),
            }
        return {"mode": "fit_blur", "region": None}

    # Blurred-letterbox only when the subject genuinely won't fit a 9:16 crop
    # for a meaningful part of the clip. Crop TIGHTLY to the subject's bounding
    # box (both axes, padded) so they fill the frame and there's minimal dead
    # space — not the full frame height with the person small in the middle.
    if n_samples > 0 and len(wide_boxes) / n_samples > fit_blur_fraction:
        def _pct(vals: list[float], p: float) -> float:
            vals = sorted(vals)
            return vals[min(len(vals) - 1, max(0, int(p * len(vals))))]

        pad_x, pad_y = 0.04, 0.06
        x0 = max(0.0, _pct([b[0] for b in wide_boxes], 0.10) - pad_x)
        y0 = max(0.0, _pct([b[1] for b in wide_boxes], 0.10) - pad_y)
        x1 = min(1.0, _pct([b[2] for b in wide_boxes], 0.90) + pad_x)
        y1 = min(1.0, _pct([b[3] for b in wide_boxes], 0.90) + pad_y)
        return {
            "mode": "fit_blur",
            "region": (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)),
        }

    layout = _detect_facecam_layout(tracks, active_id, n_samples)
    if layout is not None:
        return layout
    if not path:
        return {"mode": "track", "path": [(0.0, 0.5)]}  # nothing detected: center
    # Where the subject's face sits vertically (median, normalized 0..1).
    # The renderer uses this to keep faces out of the zone TikTok/Instagram
    # cover with their own UI at the top of the screen.
    face_y = None
    if active_id in tracks and tracks[active_id].head_cys:
        face_y = round(float(np.median(tracks[active_id].head_cys)), 4)
    return {"mode": "track", "path": path, "face_y": face_y}


# ---- detection + identity assignment ----------------------------------------


def _detect(model, frame, min_confidence) -> list[tuple]:
    """Person detections as (x1, y1, x2, y2, conf, head). With a pose model,
    head is (head_cx_px, head_cy_px, head_w_px) from the nose/eye/ear keypoints — the most
    reliable "where is the head" signal there is (needs no visible face). With
    a plain detection model, head is None and the Haar face box fills in."""
    with _infer_lock:
        results = model.predict(frame, classes=[0], conf=min_confidence, verbose=False)
    out = []
    for r in results:
        kp = getattr(r, "keypoints", None)
        kxy = kp.xy.tolist() if kp is not None and kp.xy is not None else None
        kconf = kp.conf.tolist() if kp is not None and kp.conf is not None else None
        for i, b in enumerate(r.boxes):
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            head = None
            if kxy is not None and kconf is not None and i < len(kxy):
                # COCO keypoints 0-4: nose, eyes, ears.
                pts = [kxy[i][j] for j in range(5) if kconf[i][j] > 0.5]
                if pts:
                    head_cx = sum(p[0] for p in pts) / len(pts)
                    head_cy = sum(p[1] for p in pts) / len(pts)
                    xs = [p[0] for p in pts]
                    spread = max(xs) - min(xs)
                    head_w = max(spread * 1.6, (y2 - y1) * 0.12)
                    head = (head_cx, head_cy, head_w)
            out.append((x1, y1, x2, y2, float(b.conf[0]), head))
    return out


def _assign(tracks: dict, detections: list, t: float) -> list[int]:
    """Greedy IoU matching of detections to live tracks; unmatched detections
    start new tracks. Returns the track ids visible in this frame."""
    pairs = []
    for tid, tr in tracks.items():
        if t - tr.last_t > 2.0:
            continue  # stale track — don't revive identities after long gaps
        for i, d in enumerate(detections):
            iou = _iou(tr.box[:4], d[:4])
            if iou >= 0.25:
                pairs.append((iou, tid, i))

    visible, used_t, used_d = [], set(), set()
    for _, tid, i in sorted(pairs, key=lambda p: p[0], reverse=True):
        if tid in used_t or i in used_d:
            continue
        tracks[tid].box = detections[i]
        tracks[tid].last_t = t
        visible.append(tid)
        used_t.add(tid)
        used_d.add(i)

    for i, d in enumerate(detections):
        if i not in used_d:
            tid = max(tracks, default=-1) + 1
            tracks[tid] = _Track(box=d, last_t=t)
            visible.append(tid)
    return visible


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


# ---- framing decisions --------------------------------------------------------


def _contain_half(atr: "_Track", crop_frac: float) -> float:
    """Half-width of the head region the crop tries to keep inside the window.

    Capped well inside the crop's own half-width. In close-ups the head is
    nearly as wide as the 9:16 window, and demanding the WHOLE head box stay
    inside then collapses into "center exactly on the head, every sample":
    the containment rule alone decides the framing, pinning the crop to the
    raw per-sample head position and cancelling every bit of smoothing above
    it. That is what made the camera micro-correct constantly instead of
    holding. Keeping the head CENTER comfortably inside is the real goal.
    """
    return min(atr.face_w / 2 + 0.02, crop_frac * 0.35)


def _contain_tolerance(atr: "_Track", crop_frac: float) -> float:
    """How far the head center may sit from the crop center before we move."""
    return max(0.03, crop_frac / 2 - _contain_half(atr, crop_frac))


def _head_cx(atr: "_Track", w: int) -> float:
    """Best estimate of the head center, normalized."""
    x1, _, x2, _ = atr.box[:4]
    off = atr.head_offset if atr.head_rate > 0.3 else atr.face_offset
    return (x1 + x2) / 2 / w + off


def _head_escaping(
    tracks: dict,
    active_id: int | None,
    center_x: float,
    crop_frac: float,
    w: int,
) -> bool:
    """Has the active subject's head drifted far enough to be worth a move?

    Judged on the head CENTER against a tolerance, so the camera starts
    moving before the face is anywhere near clipped — the move reads as
    anticipatory rather than as a save.
    """
    if active_id is None or active_id not in tracks:
        return False
    atr = tracks[active_id]
    if not (atr.head_rate > 0.3 or atr.face_rate > 0.45):
        return False
    return abs(_head_cx(atr, w) - center_x) > _contain_tolerance(atr, crop_frac)


def _target_x(
    tracks: dict,
    visible: list[int],
    active_id: int,
    crop_frac: float,
    someone_talking: bool = False,
) -> float:
    """Where the crop wants to be centered.

    Normally the active subject. Two persistent subjects that fit the window
    together may share the frame on their midpoint — but ONLY while nobody is
    talking: once someone speaks, sitting on the midpoint frames neither face,
    so the crop commits to the speaker instead.

    The center is a short trailing median rather than the newest sample, so a
    single bad detection box cannot yank the framing.
    """
    if not someone_talking:
        strong = [
            tid for tid in visible
            if tracks[tid].n_seen >= 8
            and tracks[tid].dominance > 0.2 * max(tracks[active_id].dominance, 1e-9)
        ]
        if len(strong) == 2:
            xa = stable_target(tracks[strong[0]].centers)
            xb = stable_target(tracks[strong[1]].centers)
            if abs(xa - xb) < crop_frac * 0.7:  # both fit in the 9:16 window
                return (xa + xb) / 2
    return stable_target(tracks[active_id].centers)


def _detect_facecam_layout(tracks: dict, active_id: int | None, n_samples: int) -> dict | None:
    """Gameplay + facecam streams: the streamer's face sits inside a small,
    static webcam overlay. If the dominant subject barely moves, is small,
    and is present in >=70% of samples -> stacked split layout."""
    if active_id is None or active_id not in tracks or n_samples == 0:
        return None
    tr = tracks[active_id]
    if tr.n_seen < 10 or tr.n_seen < 0.7 * n_samples:
        return None

    # A gameplay facecam overlay contains ONE streamer. If somebody else is on
    # screen for a good share of the clip this is a conversation, and stacking
    # it into a webcam/gameplay split frames neither person.
    #
    # This guard was not needed while the camera always followed the LARGEST
    # subject, because that person was too big to look like an overlay. Once
    # the crop began following the speaker, a smaller seated person could
    # become active and satisfy every test below — small, still, present
    # throughout — so two-person footage started rendering as a split.
    companions = [
        other for tid, other in tracks.items()
        if tid != active_id and other.n_seen >= 0.5 * n_samples
    ]
    if companions:
        return None

    centers = np.array(tr.centers)
    if centers.std() > 0.025 or float(np.mean(tr.areas)) > 0.12:
        return None

    # Median normalized face box, padded 35% to capture the webcam frame.
    boxes = np.array(tr.norm_boxes)
    x1, y1, x2, y2 = np.median(boxes, axis=0)
    pw, ph = (x2 - x1) * 0.35, (y2 - y1) * 0.35
    x = max(0.0, float(x1 - pw))
    y = max(0.0, float(y1 - ph))
    bw = min(1.0 - x, float(x2 - x1 + 2 * pw))
    bh = min(1.0 - y, float(y2 - y1 + 2 * ph))
    return {"mode": "split", "webcam_box": (x, y, bw, bh)}
