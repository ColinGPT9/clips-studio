"""Podcast clips: a separate, opt-in path for multi-cam, multi-person footage.

A podcast is a sequence of SHOTS: it hard-cuts between camera angles, and
every angle shows people at different positions and sizes. So the framing is
decided shot by shot, the way the podcast itself is edited, and inside a shot
it is ONE static, face-centred crop on ONE person. Nothing pans: every change
of framing is a one-frame snap.

v5 keeps that and changes how the person and the cuts are found. v4 picked
each shot's person by mouth motion, which a laughing or nodding listener wins
as easily as the talker, and it could never follow the talk inside a long
wide shot. Measured on 39 clips of a multi-camera podcast, against TalkNet's
own verdicts, the talker was outside v4's crop on 36.9% of the frames with
two or more faces on screen; with the rules below, 4.3%. v4 also dropped
shots under half a second, so the renderer slid across them.

  1. Cuts. video/cuts.py's ShotCuts, run exactly as the shot-aware tracker
     runs it: a candidate sample pair on the 8/s grid is decoded frame by
     frame on a capture of its own, and the cut lands on its exact frame
     (a camera flash is no cut).
  2. People, per shot. Identities never cross a cut: tracks are linked with
     the tracker's _assign on a fresh table in every shot. A track is a
     CANDIDATE when it is seen as a face (the pose head, else a Haar face) on
     at least min(_MIN_FACES, half the shot's samples) samples, its median
     body box is at least _MIN_AREA of the frame and its median detector
     confidence at least _MIN_CONF. A shoulder at the frame's edge, a
     detection seen once and a lamp the detector half believes in are not.
  3. Who is talking, per shot. TalkNet (video.tracker.score_faces) scores the
     shot's candidates only, at most _ASD_TRACKS of them, each seen as a face
     on at least _ASD_SIGHTINGS samples, so nothing is scored across a cut.
     Its contested stretches are at least _ASD_WINDOW frames long:
     asd.score_track returns all zeros for a 25-frame stretch, which is what
     every stretch under a second used to be grown to, so a 1-1.5 s two-shot
     got no verdict at all. These are optional arguments of score_faces that
     only this path sets.
     On each frame, the candidate with the top logit is the speaker when it
     is at least _FLOOR and ahead of every other scored candidate by _LEAD;
     otherwise nobody is. Where exactly one candidate is on screen, it is the
     speaker whatever TalkNet said (TalkNet only scores stretches with two).
  4. Turns inside a shot. Silence holds the last speaker; the shot opens on
     the first. A turn with under _TURN seconds of its speaker's own speech
     is dropped, the smallest first, until every turn has that much or one is
     left: an interjection, a laugh or a stray verdict never moves the crop,
     and a turn split by one is whole again. Each turn is framed on its
     speaker from its first frame (the analysis is offline, so there is no
     lag to make up). A move inside a shot under _X_EPS is not made.
     With no verdict at all, the shot goes to its most prominent candidate.
  5. Solo, empty and short shots. One candidate: that face, whatever TalkNet
     says. None: the previous framing is held (at the clip's start, the first
     framing found). A shot under _MIN_SHOT goes to its only candidate or
     holds the previous framing; it is never dropped, so nothing slides
     across it.
  6. The path: one static x per segment (a shot, or a turn inside one), the
     median face centre of its person over the segment, written as two keys,
     its first and last frame. Every cut and every switch is a one-frame snap.

Why one person, not two: averaging two nearby people into one wide crop
pulled every shot toward the centre and showed both faces small. No split
screens and no automatic letterbox either; both produced bad results on real
footage. The editor's Layout buttons (Center/Letterbox) still win per clip.

Isolation: imported only when the Podcast toggle set the flag. It reuses the
tracker's detection helpers and score_faces; score_faces' optional arguments
(max_tracks, min_faces, window) are set only here, and at their defaults the
stream tracker runs exactly as before.
"""

from pathlib import Path

import cv2
import numpy as np

from video.asd import FPS as _ASD_FPS
from video.capture import video_capture
from video.cuts import ShotCuts
from video.framing import small_gray

# Read-only reuse of the tracker's detection machinery. Importing these
# changes nothing about how the stream path behaves.
from video.tracker import (
    _CUT_DIFF_SAMPLED,
    _assign,
    _detect,
    _face_box,
    _get_model,
    head_box,
    score_faces,
)

_MIN_SHOT = 0.5       # seconds: a shorter shot gets its only face or holds the framing before it
_MIN_AREA = 0.03      # a candidate's median body box is at least this share of the frame...
_MIN_CONF = 0.5       # ...its median detector confidence at least this (a studio lamp scored 0.46)...
_MIN_FACES = 8        # ...and it is seen as a face on this many samples (a second at 8/s),
_FACE_SHARE = 0.5     #    or on this share of the shot's samples, whichever is fewer
_FLOOR = 0.0          # TalkNet logit the speaker must reach: the model's own speaking boundary
_LEAD = 1.0           # ...and its lead over every other scored candidate on that frame
_TURN = 1.0           # seconds of its speaker's speech a turn inside a shot needs to move the crop
_X_EPS = 0.05         # frame widths: a move inside a shot smaller than this is not made
_ASD_TRACKS = 4       # TalkNet scores at most this many candidates a shot, most face sightings first,
_ASD_SIGHTINGS = 3    # ...each seen as a face on at least this many samples (8 would miss 1 s shots),
_ASD_WINDOW = 26      # ...on stretches of at least this many 25 fps frames (25 come back all zeros)


class _Person:
    """One track of one shot: where it was seen, and the summaries the rules read."""

    def __init__(self) -> None:
        self.frames: list[int] = []     # sample frames it was detected on
        self.faces: list[tuple] = []    # (frame, face cx, face cy, face box): face sightings only
        self.areas: list[float] = []    # body box, share of the frame
        self.confs: list[float] = []    # detector confidence

    def summarise(self) -> None:
        self.n_face = len(self.faces)
        self.area = float(np.median(self.areas))
        self.conf = float(np.median(self.confs))
        self.face_x = float(np.median([f[1] for f in self.faces])) if self.faces else None
        self.face_y = float(np.median([f[2] for f in self.faces])) if self.faces else None


def _people(samples: list, fps: float, w: int, h: int) -> dict:
    """The shot's tracks, {tid: _Person}, from its samples [(frame, detections, faces)].

    Linked by _assign on a fresh table, so no identity crosses a cut."""
    table: dict = {}
    people: dict[int, _Person] = {}
    for idx, dets, faces in samples:
        face_of = {id(d): f for d, f in zip(dets, faces)}
        for tid in _assign(table, list(dets), idx / fps):
            det = table[tid].box
            p = people.setdefault(tid, _Person())
            p.frames.append(idx)
            p.areas.append((det[2] - det[0]) * (det[3] - det[1]) / (w * h))
            p.confs.append(float(det[4]))
            face = face_of.get(id(det))
            if face is not None:
                box = tuple(float(v) for v in face)
                p.faces.append((idx, (box[0] + box[2]) / 2 / w, (box[1] + box[3]) / 2 / h, box))
    for p in people.values():
        p.summarise()
    return people


def _candidates(people: dict, n_samples: int) -> list[int]:
    """The tracks worth framing: seen as a face often enough, big enough, and a confident
    detection. In track id order."""
    need = min(_MIN_FACES, _FACE_SHARE * n_samples)
    return sorted(
        tid for tid, p in people.items()
        if p.n_face > 0 and p.n_face >= need and p.area >= _MIN_AREA and p.conf >= _MIN_CONF
    )


def _speaker(scores: dict):
    """{tid: logit} of the candidates TalkNet scored on one frame -> the speaker, or None.

    The top logit wins when it reaches _FLOOR and beats every other scored candidate by
    _LEAD; a close call, or everyone below the floor, is nobody."""
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    tid, best = ranked[0]
    if best < _FLOOR:
        return None
    if len(ranked) > 1 and best - ranked[1][1] < _LEAD:
        return None
    return tid


def _verdicts(clip_path: Path, f0: int, f1: int, fps: float, people: dict, cands: list,
              duration: float) -> list:
    """TalkNet's speaker on every frame f0..f1-1 of one shot (None: nobody clear).

    score_faces is handed this shot's candidates only, so no stretch it scores reaches
    into another shot's people."""
    seen = {tid: [(idx / fps, box) for idx, _, _, box in people[tid].faces] for tid in cands}
    got = score_faces(clip_path, seen, duration, fps, max_tracks=_ASD_TRACKS,
                      min_faces=_ASD_SIGHTINGS, window=_ASD_WINDOW)
    if got is None:
        return [None] * (f1 - f0)
    scored, scores, n_frames = got
    out = []
    for f in range(f0, f1):
        j = min(int(round(f / fps * _ASD_FPS)), n_frames - 1)
        here = {tid: float(scores[tid][j]) for tid in scored if np.isfinite(scores[tid][j])}
        out.append(_speaker(here))
    return out


def _alone(v: list, f0: int, people: dict, cands: list) -> list:
    """Where exactly one candidate is on screen, that candidate is the speaker.

    On screen = detected on the shot's sample nearest the frame (of the samples with
    anyone on them). score_faces only scores stretches with two faces, so without this
    a person talking alone on screen for most of a shot lost it to someone on screen
    for a quarter of it."""
    on: dict[int, set] = {}
    for tid in cands:
        for idx in people[tid].frames:
            on.setdefault(idx, set()).add(tid)
    frames = np.array(sorted({idx for p in people.values() for idx in p.frames}))
    if frames.size == 0:
        return list(v)
    out = list(v)
    for i in range(len(out)):
        here = on.get(int(frames[int(np.argmin(np.abs(frames - (f0 + i))))]), ())
        if len(here) == 1:
            out[i] = next(iter(here))
    return out


def _turns(v: list) -> list:
    """Turns from per-frame verdicts: [(start, end, tid, frames of tid's own speech)].

    Silence (None) holds the last speaker; the opening takes the first one."""
    first = next((x for x in v if x is not None), None)
    if first is None:
        return []
    held, cur = [], first
    for x in v:
        if x is not None:
            cur = x
        held.append(cur)
    runs, k = [], 0
    while k < len(held):
        e = k
        while e < len(held) and held[e] == held[k]:
            e += 1
        runs.append((k, e, held[k], sum(1 for x in v[k:e] if x == held[k])))
        k = e
    return runs


def _kept_turns(v: list, turn_frames: int) -> list | None:
    """The turns the crop follows, [(start, end, tid)] relative to the shot, or None when
    there is no verdict at all.

    A turn with under turn_frames of its speaker's own speech is dropped (its verdicts
    ignored, the smallest first, the turns worked out again) until every turn has that
    much or one is left. Its frames go to the speaker before it, or at the shot's start
    to the one after. When no turn reaches turn_frames, the turn left after those drops
    takes the shot. That is usually, but not always, the candidate with the most speech:
    drops go turn by turn, so speech split over several short turns can lose to one turn."""
    v = list(v)
    runs = _turns(v)
    if not runs:
        return None
    while len(runs) > 1:
        short = [r for r in runs if r[3] < turn_frames]
        if not short:
            break
        k, e, tid, _ = min(short, key=lambda r: (r[3], r[0]))
        for i in range(k, e):
            if v[i] == tid:
                v[i] = None
        runs = _turns(v)
    return [(k, e, tid) for k, e, tid, _ in runs]


def _shot_segments(clip_path: Path, f0: int, f1: int, fps: float, people: dict, cands: list,
                   duration: float) -> list:
    """Who frames each part of one shot: [(first frame, end frame, tid or None)], None
    holding the framing before it."""
    if not cands:
        return [(f0, f1, None)]
    if len(cands) == 1:
        return [(f0, f1, cands[0])]
    if (f1 - f0) / fps < _MIN_SHOT:
        return [(f0, f1, None)]
    v = _alone(_verdicts(clip_path, f0, f1, fps, people, cands, duration), f0, people, cands)
    turns = _kept_turns(v, int(round(_TURN * fps)))
    if turns is None:
        # TalkNet named nobody: the most prominent candidate.
        return [(f0, f1, max(cands, key=lambda t: (people[t].area, -t)))]
    return [(f0 + a, f0 + b, tid) for a, b, tid in turns]


def analyze(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    sample_fps: float = 8.0,
) -> dict:
    """Shot-by-shot framing for a podcast clip.

    Returns {"mode": "track", "path": [...], "face_y": ...} where the path is
    a STEP function: two keys per segment (a shot, or a speaker's turn inside
    one) at its first and last frame, so it is flat inside a segment and every
    change is a one-frame snap. Rendered by the existing renderer unchanged."""
    # The cut reader decodes the frames around a candidate cut on a capture of
    # its own, opened around the whole pass so it is released on every way out.
    with video_capture(clip_path, required=False) as cut_cap, video_capture(clip_path) as cap:
        reader = ShotCuts(cut_cap, fallback=_CUT_DIFF_SAMPLED)
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_step = max(1, round(video_fps / sample_fps))
        model = _get_model(model_name)

        samples: list[tuple] = []   # (frame, detections, face box or None per detection)
        cuts: list[int] = []        # first frames of new shots
        prev_small = None
        w = h = None
        frame_idx = 0
        while True:
            if not cap.grab():
                break
            # Only sample frames are decoded and analysed; ShotCuts decodes the
            # frames between two samples only when the pair looks like a cut.
            if frame_idx % frame_step != 0:
                frame_idx += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            cur_small = small_gray(frame)
            k = reader.check(prev_small, cur_small, frame_idx - frame_step, frame_idx)
            if k is not None:
                cuts.append(int(k))
            prev_small = cur_small

            dets = _detect(model, frame, 0.4)
            # The face: the pose head when the pose model found one, else Haar
            # (79-124 ms a call, so only when pose found nothing).
            faces = [head_box(d[5]) if len(d) > 5 and d[5] is not None else _face_box(frame, d)
                     for d in dets]
            samples.append((frame_idx, dets, faces))
            frame_idx += 1
    n_frames = frame_idx

    if not samples or w is None:
        return {"mode": "track", "path": [(0.0, 0.5)]}

    fps = video_fps
    bounds = [0, *sorted({k for k in cuts if 0 < k < n_frames}), n_frames]
    shots = [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]
    duration = samples[-1][0] / fps   # what the stream tracker hands score_faces too

    segments: list[list] = []   # [first frame, end frame, x or None, y or None]
    switches = 0
    for f0, f1 in shots:
        inside = [s for s in samples if f0 <= s[0] < f1]
        people = _people(inside, fps, w, h)
        cands = _candidates(people, len(inside))
        placed = []
        for fa, fb, tid in _shot_segments(clip_path, f0, f1, fps, people, cands, duration):
            if tid is None:
                placed.append([fa, fb, None, None])
                continue
            # The person's median face centre over the segment: a robust median,
            # so a stray sighting can't drag the crop.
            p = people[tid]
            xs = [x for idx, x, _, _ in p.faces if fa <= idx < fb]
            ys = [y for idx, _, y, _ in p.faces if fa <= idx < fb]
            placed.append([fa, fb, float(np.median(xs)) if xs else p.face_x,
                           float(np.median(ys)) if ys else p.face_y])
        for prev, cur in zip(placed, placed[1:]):
            # Inside a shot, a small move is not made: the same place, or the
            # same person under a new track id.
            if cur[2] is not None and prev[2] is not None and abs(cur[2] - prev[2]) < _X_EPS:
                cur[2] = prev[2]
        switches += len(placed) - 1
        segments.extend(placed)

    # A segment with nobody holds the framing before it; at the clip's start,
    # the first framing there is.
    x_prev = next((s[2] for s in segments if s[2] is not None), 0.5)
    path: list[tuple[float, float]] = []
    for s in segments:
        if s[2] is None:
            s[2] = x_prev
        x_prev = s[2]
        path.append((s[0] / fps, float(s[2])))
        path.append(((s[1] - 1) / fps, float(s[2])))
    print(f"      Podcast layout: {len(shots)} shot(s), {len(shots) - 1} cut(s), "
          f"{switches} speaker switch(es) inside a shot; static face-centred framing")

    ys = [s[3] for s in segments if s[3] is not None]
    face_y = round(float(np.median(ys)), 4) if ys else None
    return {"mode": "track", "path": path, "face_y": face_y}


def render_clip(
    intermediate: Path,
    output_path: Path,
    decision: dict,
    ass_path: Path | None = None,
    vf_extra: str = "",
    normalize: bool = True,
) -> None:
    """Render by the analyzer's decision through the existing renderer:
    nothing podcast-specific left at render time."""
    from video.cropper import render_vertical

    render_vertical(
        intermediate, decision, output_path,
        ass_path=ass_path, vf_extra=vf_extra, normalize=normalize,
    )
