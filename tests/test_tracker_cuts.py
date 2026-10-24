"""Shot-aware reframing (tracking.shot_aware, on by default), on synthetic clips.

With the setting off, the framing pass handles a camera cut badly, in ways
measured on edited road-trip footage (video/cuts.py and tracker._ShotReplay
say more):

  * the subject on camera before a cut stays the target after it, at their
    old position, until they have been unseen for a second: 28-31 frames of
    the new shot framed on someone who is not in it ("ghost");
  * cuts are only looked for on the sample grid, at a threshold half of the
    real cuts there never reach, and are snapped on the sample after them;
  * with nobody detected on the first sample after a cut there is no snap;
  * an id _assign carries across a cut keeps the old person's averages.

Run on real footage, the mode itself got three things wrong, tested at the end
(F1-F3): a shot that opened with nobody in it cut to the first detection
however faint; one Haar face on a first sighting after a cut was trusted at
once; and a speaker the detector lost for a moment counted as gone, so the
camera left them and the shot plan brought it straight back. Reviewing those
fixes found the edges tested with them (M1-M3): a sighting in the next shot
(the id handed to someone else at the cut) counted for a speaker's dropout;
a waiting shot opened on somebody credible while the plan's speaker was on
screen below the bar, and the plan took the camera to that speaker on the
next sample; and what F2 costs a real face only the cascade finds.

The clip's opening shot is framed like a shot after a cut (O1-O3): first
sightings start the averages from the clip's first sample, and the first
framing is the median of the first 0.5 s. With the setting off a clip opens
on the body box and eases toward the head, and opens wherever its first
detection is, even one off its subject.

The clips here are synthetic, and nothing in them is a real person:
  * video_capture -> a fake capture of flat-colour shots (one colour per
    shot, so the colour change sets the cut's strength) or a rolled texture;
  * _get_model -> None, _detect -> scripted boxes for the frame;
  * speech_envelope and _asd_speakers -> None (no audio, so no TalkNet),
    unless a test stands in for TalkNet (talking_left) or for the Haar
    cascade (haar).

T7 is the guard: with shot_aware set off (tracking.shot_aware: false, or
compute_tracking(shot_aware=False)), compute_tracking returns EXACTLY what it
returned before this mode existed, on every scene in SCENES.
TODAY holds those outputs, recorded from the code before the change (the
parent of the commit that added this file), as (frame, x) keys: every key
time the tracker writes is a frame number over the clip's fps. "Off" in the
tests below is that output too: the framing before this mode existed.

numpy and OpenCV are needed: pytest.importorskip, as CI's light install has
neither. The decision itself is also tested there, in tests/test_cuts.py.
"""

import contextlib
import itertools

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from video import cropper, cuts, tracker  # noqa: E402

W, H = 320, 180          # 16:9, small so a whole clip runs in a fraction of a second
FPS = 29.97              # sample_fps 8 -> one sample every 4 frames, as on real footage
STEP = max(1, round(FPS / 8))
N = 150                  # frames per clip (5 s)

# Flat shot colours (BGR). Their gray levels through video.framing.small_gray
# are A 79, B 200 (121 from A: a strong cut) and WEAK 119 (40 from A: a cut
# the setting off's sample-grid threshold of 55 never sees). Every pair changes hue.
A = (150, 90, 30)
B = (40, 210, 240)
WEAK = (70, 80, 215)
WHITE = (255, 255, 255)


def person(cx, *, width=0.2, head_dx=0.0, conf=0.9, head=True):
    """A standing person centred at cx (a share of the width), the head head_dx off it
    (head=False: the pose model found no head on them)."""
    return ((cx - width / 2) * W, 0.15 * H, (cx + width / 2) * W, 0.98 * H, conf,
            ((cx + head_dx) * W, 0.3 * H, 0.08 * W) if head else None)


def _texture():
    """A smooth coloured picture. Rolled sideways it is a fast handheld pan
    whose pixels, and so whose colours, stay the same."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    tex = np.zeros((H, W, 3), np.float32)
    tex[..., 0] = 128 + 100 * np.sin(2 * np.pi * xx / W * 3 + 0.5) * np.cos(2 * np.pi * yy / H * 2)
    tex[..., 1] = 128 + 90 * np.sin(2 * np.pi * xx / W * 2 + 1.7)
    tex[..., 2] = 128 + 100 * np.cos(2 * np.pi * xx / W * 5 + 0.3) * np.sin(2 * np.pi * yy / H + 0.9)
    return np.clip(tex, 0, 255).astype(np.uint8)


class Scene:
    """colour(i) -> frame i's flat colour (or picture(i) -> the frame itself);
    people(i) -> what the detector finds on it."""

    def __init__(self, colour, people, n=N, picture=None):
        self.colour, self.people, self.n, self.picture = colour, people, n, picture

    def frame(self, i):
        if self.picture is not None:
            return self.picture(i)
        return np.full((H, W, 3), self.colour(i), np.uint8)


class FakeCapture:
    def __init__(self, scene, shift=0):
        self.scene, self.shift = scene, shift   # shift: a seek that lands this many frames late
        self.pos, self.cur = 0, None
        self.retrieved, self.seeks, self.released = 0, 0, False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return FPS
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.scene.n)
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self.pos)
        return 0.0

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self.pos, self.seeks = int(value) + self.shift, self.seeks + 1
            return True
        return False

    def grab(self):
        if self.pos >= self.scene.n:
            return False
        self.cur, self.pos = self.pos, self.pos + 1
        return True

    def retrieve(self):
        if self.cur is None:
            return False, None
        self.retrieved += 1
        return True, self.scene.frame(self.cur)

    def read(self):
        return self.retrieve() if self.grab() else (False, None)

    def release(self):
        self.released = True


def run(scene, patch, opened=None, asd=None, **kw):
    """compute_tracking on a synthetic scene; patch is monkeypatch.setattr.
    Returns (result, the captures it opened, in the order it opened them);
    pass `opened` to see them even when it raises, and `asd` to stand in for
    _asd_speakers (TalkNet), which otherwise has no answer."""
    opened = [] if opened is None else opened

    @contextlib.contextmanager
    def capture(_path, *, required=True):
        cap = FakeCapture(scene)
        opened.append(cap)
        try:
            yield cap
        finally:
            cap.release()

    # One _detect call per sample, in order: the k-th is frame k * STEP.
    calls = iter(range(10**6))

    def detect(_model, _frame, _min_conf):
        return list(scene.people(next(calls) * STEP))

    patch(tracker, "video_capture", capture)
    patch(tracker, "_get_model", lambda _name: None)
    patch(tracker, "_detect", detect)
    patch(tracker, "speech_envelope", lambda *_a, **_k: None)
    patch(tracker, "_asd_speakers", asd or (lambda *_a, **_k: None))
    return tracker.compute_tracking("clip.mp4", **kw), opened


def _cut_at(frame, before, after):
    return lambda i: before if i < frame else after


SCENES = {
    # T1: one subject at 0.25, a hard cut at f60 (a sample frame) to another at 0.75.
    "ghost": Scene(_cut_at(60, A, B), lambda i: [person(0.25)] if i < 60 else [person(0.75)]),
    # T2: the same, but the first subject walks back in at f104 (no cut).
    "ghost_returns": Scene(_cut_at(60, A, B), lambda i: (
        [person(0.25)] if i < 60 else [person(0.75)] if i < 104 else [person(0.75), person(0.25)])),
    # T3: the new subject's box overlaps the old one's (IoU 0.43), so _assign
    # keeps the old track id across the cut; their heads sit on opposite sides.
    "revival": Scene(_cut_at(60, A, B), lambda i: (
        [person(0.50, head_dx=0.03)] if i < 60 else [person(0.58, head_dx=-0.02)])),
    # T4: the cut lands on f61, between the samples at f60 and f64...
    "late": Scene(_cut_at(61, A, B), lambda i: [person(0.25)] if i < 61 else [person(0.75)]),
    # ...and a weaker one (gray 40 across the sample pair) on the same frame.
    "weak": Scene(_cut_at(61, A, WEAK), lambda i: [person(0.25)] if i < 61 else [person(0.75)]),
    # T5: nobody is detected for the first two samples of the new shot.
    "nobody": Scene(_cut_at(60, A, B), lambda i: (
        [person(0.30)] if i < 60 else [] if i < 68 else [person(0.70)])),
    # T6: a fast handheld pan, 12 px a frame: about 57 gray across a sample
    # pair (the grid test of the setting off fires on it) and no change of colour.
    "handheld": Scene(None, lambda i: [person(0.5)],
                      picture=lambda i, _t=_texture(): np.roll(_t, -12 * i, axis=1)),
    # No cut at all: a subject drifting across the frame.
    "steady": Scene(lambda i: A, lambda i: [person(0.30 + 0.30 * i / N)]),
    # A cut where the subject on camera is still on screen after it, now beside
    # a bigger newcomer: the camera stays on the subject it was on.
    "kept": Scene(_cut_at(60, A, B), lambda i: (
        [person(0.40)] if i < 60 else [person(0.40), person(0.75, width=0.26)])),
    # No cut. The bigger of two people leaves after f36 and the camera has to
    # move to the other one once they have been gone a second...
    "leaves": Scene(lambda i: A, lambda i: (
        [person(0.30, width=0.26), person(0.70, width=0.16)] if i < 40 else [person(0.70, width=0.16)])),
    # ...whether or not they walk back in later (f88, inside _assign's 2 s).
    "leaves_returns": Scene(lambda i: A, lambda i: (
        [person(0.30, width=0.26), person(0.70, width=0.16)] if i < 40 or i >= 88
        else [person(0.70, width=0.16)])),
    # Two cuts 239 frames apart (f62 and f301), so the cut reader seeks across
    # the stretch between them instead of reading through it.
    "far": Scene(lambda i: A if i < 62 or i >= 301 else B, lambda i: (
        [person(0.25)] if i < 62 else [person(0.75)] if i < 301 else [person(0.40)]), n=420),
    # A two-frame flash on the sample at f64: not a cut, on either sample pair.
    "flash": Scene(lambda i: WHITE if i in (64, 65) else A, lambda i: [person(0.40)]),
    # Nobody in the first shot; somebody at 0.7 from the cut at f60.
    "empty_start": Scene(_cut_at(60, A, B), lambda i: [] if i < 60 else [person(0.70)]),
}

# compute_tracking's output on every scene before shot-aware mode existed,
# as (frame, x) keys (see the module docstring).
TODAY = {
    "ghost": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.25), (4, 0.25), (8, 0.25), (12, 0.25), (16, 0.25), (20, 0.25), (24, 0.25),
        (28, 0.25), (32, 0.25), (36, 0.25), (40, 0.25), (44, 0.25), (48, 0.25), (52, 0.25),
        (56, 0.25), (59, 0.25), (60, 0.25), (64, 0.25), (68, 0.25), (72, 0.25), (76, 0.25),
        (80, 0.25), (84, 0.25), (87, 0.25), (88, 0.75), (92, 0.75), (96, 0.75), (100, 0.75),
        (104, 0.75), (108, 0.75), (112, 0.75), (116, 0.75), (120, 0.75), (124, 0.75),
        (128, 0.75), (132, 0.75), (136, 0.75), (140, 0.75), (144, 0.75), (148, 0.75),
    ]},
    "ghost_returns": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.25), (4, 0.25), (8, 0.25), (12, 0.25), (16, 0.25), (20, 0.25), (24, 0.25),
        (28, 0.25), (32, 0.25), (36, 0.25), (40, 0.25), (44, 0.25), (48, 0.25), (52, 0.25),
        (56, 0.25), (59, 0.25), (60, 0.25), (64, 0.25), (68, 0.25), (72, 0.25), (76, 0.25),
        (80, 0.25), (84, 0.25), (88, 0.25), (92, 0.25), (96, 0.25), (100, 0.25), (104, 0.25),
        (108, 0.25), (112, 0.25), (116, 0.25), (120, 0.25), (124, 0.25), (128, 0.25),
        (132, 0.25), (136, 0.25), (140, 0.25), (144, 0.25), (148, 0.25),
    ]},
    "revival": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.5), (4, 0.5), (8, 0.5), (12, 0.5), (16, 0.5), (20, 0.5), (24, 0.5), (28, 0.5),
        (32, 0.5), (36, 0.5), (40, 0.5), (44, 0.5), (48, 0.5), (52, 0.5), (56, 0.5), (59, 0.5),
        (60, 0.5297965330781453), (64, 0.5297965330781453), (68, 0.5297965330781453),
        (72, 0.5297965330781453), (76, 0.5297965330781453), (80, 0.5297965330781453),
        (84, 0.5297965330781453), (88, 0.5297965330781453), (92, 0.5297965330781453),
        (96, 0.5297965330781453), (100, 0.5297965330781453), (104, 0.5297965330781453),
        (108, 0.5297965330781453), (112, 0.5297965330781453), (116, 0.5297965330781453),
        (120, 0.5297965330781453), (124, 0.5297965330781453), (128, 0.5297965330781453),
        (132, 0.5297965330781453), (136, 0.5297965330781453), (140, 0.5297965330781453),
        (144, 0.5297965330781453), (148, 0.5297965330781453),
    ]},
    "late": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.25), (4, 0.25), (8, 0.25), (12, 0.25), (16, 0.25), (20, 0.25), (24, 0.25),
        (28, 0.25), (32, 0.25), (36, 0.25), (40, 0.25), (44, 0.25), (48, 0.25), (52, 0.25),
        (56, 0.25), (60, 0.25), (63, 0.25), (64, 0.25), (68, 0.25), (72, 0.25), (76, 0.25),
        (80, 0.25), (84, 0.25), (88, 0.25), (91, 0.25), (92, 0.75), (96, 0.75), (100, 0.75),
        (104, 0.75), (108, 0.75), (112, 0.75), (116, 0.75), (120, 0.75), (124, 0.75),
        (128, 0.75), (132, 0.75), (136, 0.75), (140, 0.75), (144, 0.75), (148, 0.75),
    ]},
    "weak": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.25), (4, 0.25), (8, 0.25), (12, 0.25), (16, 0.25), (20, 0.25), (24, 0.25),
        (28, 0.25), (32, 0.25), (36, 0.25), (40, 0.25), (44, 0.25), (48, 0.25), (52, 0.25),
        (56, 0.25), (60, 0.25), (64, 0.25), (68, 0.25), (72, 0.25), (76, 0.25), (80, 0.25),
        (84, 0.25), (88, 0.25), (91, 0.25), (92, 0.75), (96, 0.75), (100, 0.75), (104, 0.75),
        (108, 0.75), (112, 0.75), (116, 0.75), (120, 0.75), (124, 0.75), (128, 0.75),
        (132, 0.75), (136, 0.75), (140, 0.75), (144, 0.75), (148, 0.75),
    ]},
    "nobody": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.3), (4, 0.3), (8, 0.3), (12, 0.3), (16, 0.3), (20, 0.3), (24, 0.3), (28, 0.3),
        (32, 0.3), (36, 0.3), (40, 0.3), (44, 0.3), (48, 0.3), (52, 0.3), (56, 0.3), (68, 0.3),
        (72, 0.3), (76, 0.3), (80, 0.3), (84, 0.3), (87, 0.3), (88, 0.7), (92, 0.7), (96, 0.7),
        (100, 0.7), (104, 0.7), (108, 0.7), (112, 0.7), (116, 0.7), (120, 0.7), (124, 0.7),
        (128, 0.7), (132, 0.7), (136, 0.7), (140, 0.7), (144, 0.7), (148, 0.7),
    ]},
    "handheld": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.5), (3, 0.5), (4, 0.5), (7, 0.5), (8, 0.5), (11, 0.5), (12, 0.5), (15, 0.5),
        (16, 0.5), (19, 0.5), (20, 0.5), (23, 0.5), (24, 0.5), (27, 0.5), (28, 0.5), (31, 0.5),
        (32, 0.5), (35, 0.5), (36, 0.5), (39, 0.5), (40, 0.5), (43, 0.5), (44, 0.5), (47, 0.5),
        (48, 0.5), (51, 0.5), (52, 0.5), (55, 0.5), (56, 0.5), (59, 0.5), (60, 0.5), (63, 0.5),
        (64, 0.5), (67, 0.5), (68, 0.5), (71, 0.5), (72, 0.5), (75, 0.5), (76, 0.5), (79, 0.5),
        (80, 0.5), (83, 0.5), (84, 0.5), (87, 0.5), (88, 0.5), (91, 0.5), (92, 0.5), (95, 0.5),
        (96, 0.5), (99, 0.5), (100, 0.5), (103, 0.5), (104, 0.5), (107, 0.5), (108, 0.5),
        (111, 0.5), (112, 0.5), (115, 0.5), (116, 0.5), (119, 0.5), (120, 0.5), (123, 0.5),
        (124, 0.5), (127, 0.5), (128, 0.5), (131, 0.5), (132, 0.5), (135, 0.5), (136, 0.5),
        (139, 0.5), (140, 0.5), (143, 0.5), (144, 0.5), (147, 0.5), (148, 0.5),
    ]},
    "steady": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.3), (4, 0.3), (8, 0.3), (12, 0.3), (16, 0.3), (20, 0.3), (24, 0.3), (28, 0.3),
        (32, 0.3), (36, 0.3252), (40, 0.34265999999999996), (44, 0.355863), (48, 0.36672465),
        (52, 0.3762985575), (56, 0.3762985575), (60, 0.3762985575), (64, 0.3762985575),
        (68, 0.3762985575), (72, 0.39956420662500003), (76, 0.41596031364375),
        (80, 0.4285781725040625), (84, 0.43911799487723435), (88, 0.4485148971824789),
        (92, 0.4485148971824789), (96, 0.4485148971824789), (100, 0.4485148971824789),
        (104, 0.4485148971824789), (108, 0.4716831934503634), (112, 0.4880257563976999),
        (116, 0.500614166018735), (120, 0.5111377913103042), (124, 0.5205257852206674),
        (128, 0.5205257852206674), (132, 0.5205257852206674), (136, 0.5205257852206674),
        (140, 0.5205257852206674), (144, 0.5436891818713669), (148, 0.5600290500292519),
    ]},
    "kept": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.4), (4, 0.4), (8, 0.4), (12, 0.4), (16, 0.4), (20, 0.4), (24, 0.4), (28, 0.4),
        (32, 0.4), (36, 0.4), (40, 0.4), (44, 0.4), (48, 0.4), (52, 0.4), (56, 0.4), (59, 0.4),
        (60, 0.4), (64, 0.4), (68, 0.4), (72, 0.4), (76, 0.4), (80, 0.4), (84, 0.4), (88, 0.4),
        (92, 0.4), (96, 0.4), (100, 0.4), (104, 0.4), (108, 0.4), (112, 0.4), (116, 0.4),
        (120, 0.4), (124, 0.4), (128, 0.4), (132, 0.4), (136, 0.4), (140, 0.4), (144, 0.4),
        (148, 0.4),
    ]},
    "leaves": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.3), (4, 0.3), (8, 0.3), (12, 0.3), (16, 0.3), (20, 0.3), (24, 0.3), (28, 0.3),
        (32, 0.3), (36, 0.3), (40, 0.3), (44, 0.3), (48, 0.3), (52, 0.3), (56, 0.3), (60, 0.3),
        (64, 0.3), (67, 0.3), (68, 0.7), (72, 0.7), (76, 0.7), (80, 0.7), (84, 0.7), (88, 0.7),
        (92, 0.7), (96, 0.7), (100, 0.7), (104, 0.7), (108, 0.7), (112, 0.7), (116, 0.7),
        (120, 0.7), (124, 0.7), (128, 0.7), (132, 0.7), (136, 0.7), (140, 0.7), (144, 0.7),
        (148, 0.7),
    ]},
    "leaves_returns": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.3), (4, 0.3), (8, 0.3), (12, 0.3), (16, 0.3), (20, 0.3), (24, 0.3), (28, 0.3),
        (32, 0.3), (36, 0.3), (40, 0.3), (44, 0.3), (48, 0.3), (52, 0.3), (56, 0.3), (60, 0.3),
        (64, 0.3), (68, 0.3), (72, 0.3), (76, 0.3), (80, 0.3), (84, 0.3), (88, 0.3), (92, 0.3),
        (96, 0.3), (100, 0.3), (104, 0.3), (108, 0.3), (112, 0.3), (116, 0.3), (120, 0.3),
        (124, 0.3), (128, 0.3), (132, 0.3), (136, 0.3), (140, 0.3), (144, 0.3), (148, 0.3),
    ]},
    "far": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.25), (4, 0.25), (8, 0.25), (12, 0.25), (16, 0.25), (20, 0.25), (24, 0.25),
        (28, 0.25), (32, 0.25), (36, 0.25), (40, 0.25), (44, 0.25), (48, 0.25), (52, 0.25),
        (56, 0.25), (60, 0.25), (63, 0.25), (64, 0.25), (68, 0.25), (72, 0.25), (76, 0.25),
        (80, 0.25), (84, 0.25), (88, 0.25), (91, 0.25), (92, 0.75), (96, 0.75), (100, 0.75),
        (104, 0.75), (108, 0.75), (112, 0.75), (116, 0.75), (120, 0.75), (124, 0.75),
        (128, 0.75), (132, 0.75), (136, 0.75), (140, 0.75), (144, 0.75), (148, 0.75),
        (152, 0.75), (156, 0.75), (160, 0.75), (164, 0.75), (168, 0.75), (172, 0.75),
        (176, 0.75), (180, 0.75), (184, 0.75), (188, 0.75), (192, 0.75), (196, 0.75),
        (200, 0.75), (204, 0.75), (208, 0.75), (212, 0.75), (216, 0.75), (220, 0.75),
        (224, 0.75), (228, 0.75), (232, 0.75), (236, 0.75), (240, 0.75), (244, 0.75),
        (248, 0.75), (252, 0.75), (256, 0.75), (260, 0.75), (264, 0.75), (268, 0.75),
        (272, 0.75), (276, 0.75), (280, 0.75), (284, 0.75), (288, 0.75), (292, 0.75),
        (296, 0.75), (300, 0.75), (303, 0.75), (304, 0.75), (308, 0.75), (312, 0.75),
        (316, 0.75), (320, 0.75), (324, 0.75), (328, 0.75), (331, 0.75), (332, 0.4), (336, 0.4),
        (340, 0.4), (344, 0.4), (348, 0.4), (352, 0.4), (356, 0.4), (360, 0.4), (364, 0.4),
        (368, 0.4), (372, 0.4), (376, 0.4), (380, 0.4), (384, 0.4), (388, 0.4), (392, 0.4),
        (396, 0.4), (400, 0.4), (404, 0.4), (408, 0.4), (412, 0.4), (416, 0.4),
    ]},
    "flash": {"mode": "track", "face_y": 0.3, "keys": [
        (0, 0.4), (4, 0.4), (8, 0.4), (12, 0.4), (16, 0.4), (20, 0.4), (24, 0.4), (28, 0.4),
        (32, 0.4), (36, 0.4), (40, 0.4), (44, 0.4), (48, 0.4), (52, 0.4), (56, 0.4), (60, 0.4),
        (63, 0.4), (64, 0.4), (67, 0.4), (68, 0.4), (72, 0.4), (76, 0.4), (80, 0.4), (84, 0.4),
        (88, 0.4), (92, 0.4), (96, 0.4), (100, 0.4), (104, 0.4), (108, 0.4), (112, 0.4),
        (116, 0.4), (120, 0.4), (124, 0.4), (128, 0.4), (132, 0.4), (136, 0.4), (140, 0.4),
        (144, 0.4), (148, 0.4),
    ]},
    "empty_start": {"mode": "track", "face_y": 0.3, "keys": [
        (60, 0.7), (64, 0.7), (68, 0.7), (72, 0.7), (76, 0.7), (80, 0.7), (84, 0.7), (88, 0.7),
        (92, 0.7), (96, 0.7), (100, 0.7), (104, 0.7), (108, 0.7), (112, 0.7), (116, 0.7),
        (120, 0.7), (124, 0.7), (128, 0.7), (132, 0.7), (136, 0.7), (140, 0.7), (144, 0.7),
        (148, 0.7),
    ]},
}


def at(result, frame):
    """The crop centre the renderer uses on this frame (video/cropper.py)."""
    return cropper._interpolate(result["path"], frame / FPS)


def frames_of(result):
    """The frames the path has keys on."""
    return [round(t * FPS) for t, _ in result["path"]]


def shot_aware(name, monkeypatch, **kw):
    return run(SCENES[name], monkeypatch.setattr, shot_aware=True, **kw)


def off(name, monkeypatch):
    """The output with the setting off: as before this mode existed (TODAY)."""
    return run(SCENES[name], monkeypatch.setattr, shot_aware=False)[0]


# ---- T7: off means unchanged ----------------------------------------------------------------


@pytest.mark.parametrize("name", list(SCENES))
def test_t7_with_the_setting_off_the_output_is_exactly_as_before(name, monkeypatch):
    want = TODAY[name]
    want = {"mode": want["mode"], "path": [(f / FPS, x) for f, x in want["keys"]], "face_y": want["face_y"]}
    # Set off explicitly: the setting is on unless the settings say off (cuts.shot_aware_on).
    for kw in ({"shot_aware": False}, {"shot_aware": False, "no_subject": "hold"}):
        result, opened = run(SCENES[name], monkeypatch.setattr, **kw)
        assert result == want
        # and nothing extra is decoded: one capture, the tracker's own
        assert len(opened) == 1 and opened[0].released


@pytest.mark.parametrize("settings, passed", [
    ({}, (True, "center")),        # settings.yaml as it ships: no such keys, so on
    ({"shot_aware": None}, (True, "center")),    # the key left empty: on as well
    ({"shot_aware": False}, (False, "center")),  # how to turn it off
    ({"shot_aware": True, "shot_aware_no_subject": "hold"}, (True, "hold")),
    # Quoted, the value is text, and any text but "" is true to a plain truth
    # test: only the words that mean on turn it on (cuts.shot_aware_on).
    ({"shot_aware": "false"}, (False, "center")),
    ({"shot_aware": "off"}, (False, "center")),
    ({"shot_aware": " Yes "}, (True, "center")),
])
def test_the_pipeline_passes_the_settings_and_they_default_to_on(settings, passed, monkeypatch, tmp_path):
    from pathlib import Path

    import core.pipeline as pipeline
    from core import modes
    from core.models import ClipCandidate

    seen = {}

    def tracking(_clip, **kw):
        seen.update(kw)
        return {"mode": "track", "path": [(0.0, 0.5)]}

    def render(_clip, _tracking, output, *_a, **_k):
        Path(output).write_bytes(b"clip")
        return Path(output)

    monkeypatch.setattr(pipeline, "cut_clip", lambda _s, _c, output, **_k: Path(output).write_bytes(b"cut"))
    monkeypatch.setattr(modes, "probe_size", lambda _p: (1920, 1080))
    monkeypatch.setattr(cropper, "render_vertical", render)
    monkeypatch.setattr(tracker, "compute_tracking", tracking)
    config = {"clips": {"captions": False, "outro": False, "vertical": True},
              "paths": {"data_dir": str(tmp_path)},
              "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8, **settings}}
    pipeline._render_files(tmp_path / "source.mp4", ClipCandidate(start=10.0, end=40.0, score=80),
                           [], tmp_path / "clips", config)
    assert seen["shot_aware"] is passed[0] and seen["no_subject"] == passed[1]


# ---- T1-T6 -----------------------------------------------------------------------------------


def test_t1_the_new_shot_is_framed_on_its_own_subject(monkeypatch):
    result, _ = shot_aware("ghost", monkeypatch)
    first_after = next(x for t, x in result["path"] if t >= 60 / FPS)
    assert first_after == pytest.approx(0.75, abs=0.01)
    assert at(result, 59) == 0.25                       # the old framing to the end of its shot
    assert all(at(result, f) == pytest.approx(0.75, abs=0.01) for f in range(60, N))
    # Off: the old subject's 0.25 until f88.
    old = off("ghost", monkeypatch)
    assert at(old, 87) == 0.25 and at(old, 88) == 0.75


def test_t2_nothing_before_a_return_depends_on_it(monkeypatch):
    gone, _ = shot_aware("ghost", monkeypatch)
    back, _ = shot_aware("ghost_returns", monkeypatch)
    assert [k for k in gone["path"] if k[0] < 104 / FPS] == [k for k in back["path"] if k[0] < 104 / FPS]
    assert [at(gone, f) for f in range(104)] == [at(back, f) for f in range(104)]
    # Off, the subject who comes back holds the camera, unseen, from f60 to f104.
    old_gone, old_back = off("ghost", monkeypatch), off("ghost_returns", monkeypatch)
    assert [at(old_gone, f) for f in range(104)] != [at(old_back, f) for f in range(104)]


def test_r5_a_subject_counts_as_gone_whether_or_not_they_come_back(monkeypatch):
    # The same question with no cut at all: the bigger person leaves after f36.
    gone, _ = shot_aware("leaves", monkeypatch)
    back, _ = shot_aware("leaves_returns", monkeypatch)
    assert [at(gone, f) for f in range(88)] == [at(back, f) for f in range(88)]
    assert at(back, 67) == 0.3 and at(back, 68) == pytest.approx(0.7, abs=0.01)
    # Off, the replay reads when each track was last seen in the whole clip,
    # so the person who comes back at f88 never counts as gone before it.
    old_back = off("leaves_returns", monkeypatch)
    assert all(at(old_back, f) == 0.3 for f in range(88))


def test_t3_an_id_carried_across_the_cut_snaps_to_the_new_face(monkeypatch):
    new_face = 0.58 - 0.02
    result, _ = shot_aware("revival", monkeypatch)
    assert at(result, 59) == pytest.approx(0.50 + 0.03)    # the old face, from the clip's start (O1)
    assert at(result, 60) == pytest.approx(new_face, abs=0.02)
    assert all(at(result, f) == pytest.approx(new_face, abs=0.02) for f in range(60, N))
    # Off: the old face's position (0.5298), and it parks there.
    old = off("revival", monkeypatch)
    assert abs(at(old, 60) - new_face) > 0.02 and abs(at(old, N - 1) - new_face) > 0.02


@pytest.mark.parametrize("name", ["late", "weak"])
def test_t4_the_snap_lands_on_the_cut_s_own_frame(name, monkeypatch):
    result, _ = shot_aware(name, monkeypatch)
    assert [at(result, f) for f in range(61)] == [0.25] * 61     # no drift before it
    assert at(result, 61) == pytest.approx(0.75, abs=0.01)
    assert 61 in frames_of(result)
    # Off: f64, still on the old subject ("late"), or not at all ("weak").
    old = off(name, monkeypatch)
    assert at(old, 61) == 0.25 and at(old, 64) == 0.25
    assert (63 in frames_of(old)) == (name == "late")


def test_the_cut_s_frame_is_found_wherever_it_falls_between_two_samples(monkeypatch):
    for cut in (61, 62, 63, 64):
        scene = Scene(_cut_at(cut, A, B), lambda i, c=cut: [person(0.25)] if i < c else [person(0.75)])
        result, _ = run(scene, monkeypatch.setattr, shot_aware=True)
        assert at(result, cut - 1) == 0.25
        assert at(result, cut) == pytest.approx(0.75, abs=0.01)


@pytest.mark.parametrize("no_subject, held", [("center", 0.5), ("hold", 0.3)])
def test_t5_nobody_on_screen_after_a_cut(no_subject, held, monkeypatch):
    result, _ = shot_aware("nobody", monkeypatch, no_subject=no_subject)
    assert [at(result, f) for f in range(56, 60)] == [0.3] * 4
    # From the cut's own frame, the new shot's framing: never anything in between.
    assert [at(result, f) for f in range(60, 68)] == [held] * 8
    # And the first person seen is cut to, not panned to.
    assert at(result, 68) == pytest.approx(0.7, abs=0.01)


def test_a_first_shot_with_nobody_in_it_keeps_the_centre(monkeypatch):
    # The framing of the shot after the cut does not reach back into it.
    result, _ = shot_aware("empty_start", monkeypatch)
    assert [at(result, f) for f in range(60)] == [0.5] * 60
    assert all(at(result, f) == pytest.approx(0.7, abs=0.01) for f in range(60, N))
    # Off, the first framing found holds from the clip's first frame.
    assert at(off("empty_start", monkeypatch), 0) == 0.7


def test_any_other_word_for_no_subject_means_the_centre(monkeypatch):
    result, _ = shot_aware("nobody", monkeypatch, no_subject="centre")
    assert [at(result, f) for f in range(60, 68)] == [0.5] * 8


def test_t6_a_fast_handheld_pan_is_no_cut(monkeypatch):
    found = []
    check = cuts.ShotCuts.check

    def spy(self, *a):
        found.append(check(self, *a))
        return found[-1]

    monkeypatch.setattr(cuts.ShotCuts, "check", spy)
    result, opened = shot_aware("handheld", monkeypatch)
    reader = opened[0]
    assert reader.retrieved > 0                       # the pairs were candidates, and were looked at
    assert found and not any(found)
    assert all(f % STEP == 0 for f in frames_of(result))     # no freeze or snap keys
    # Off: a cut on every sample, each with its freeze key.
    assert sum(f % STEP != 0 for f in frames_of(off("handheld", monkeypatch))) > 30


def test_a_flash_is_no_cut(monkeypatch):
    result, _ = shot_aware("flash", monkeypatch)
    assert all(f % STEP == 0 for f in frames_of(result))
    assert {x for _, x in result["path"]} == {0.4}
    # Off: the flash and the picture coming back are two cuts.
    assert {63, 67} <= set(frames_of(off("flash", monkeypatch)))


def test_the_subject_on_camera_keeps_it_across_a_cut_they_are_still_in(monkeypatch):
    # Chosen afresh, the bigger newcomer at 0.75 would win; a false cut on a
    # handheld stream must not hand the camera to someone else.
    result, _ = shot_aware("kept", monkeypatch)
    assert all(at(result, f) == pytest.approx(0.4, abs=0.01) for f in range(N))


def test_with_no_cut_the_clip_opens_on_the_median_of_its_first_half_second(monkeypatch):
    # A subject drifting across the frame, and no cut.
    result, opened = shot_aware("steady", monkeypatch)
    assert opened[0].retrieved == 0               # no pair was even a candidate: no cut decoded
    assert all(f % STEP == 0 for f in frames_of(result))     # no freeze or snap key
    first = [0.30 + 0.30 * f / N for f in range(0, N, STEP) if f / FPS <= tracker._OPENING]
    assert len(first) == 4                        # f0-f12
    t0, x0 = result["path"][0]
    assert t0 == 0.0 and x0 == pytest.approx(float(np.median(first)), abs=1e-9)   # 0.312
    # Off: the first sample's centre.
    assert off("steady", monkeypatch)["path"][0] == (0.0, 0.3)


def test_the_reader_seeks_across_a_long_stretch_and_still_lands_on_the_frame(monkeypatch):
    result, opened = shot_aware("far", monkeypatch)
    assert opened[0].seeks == 1
    assert at(result, 61) == 0.25 and at(result, 62) == pytest.approx(0.75, abs=0.01)
    assert at(result, 300) == pytest.approx(0.75, abs=0.01)
    assert at(result, 301) == pytest.approx(0.4, abs=0.01)


def test_both_captures_are_released_when_tracking_fails(monkeypatch):
    def people(i):
        if i >= 80:
            raise RuntimeError("detector failed")
        return [person(0.25)] if i < 60 else [person(0.75)]

    opened = []
    with pytest.raises(RuntimeError, match="detector failed"):
        run(Scene(_cut_at(60, A, B), people), monkeypatch.setattr, opened, shot_aware=True)
    # The tracker's capture and the cut reader's: an open one blocks deleting
    # the clip on Windows (#74).
    assert len(opened) == 2 and all(c.released for c in opened)


# ---- R4: a track's first sighting after a cut ------------------------------------------------


def test_r4_a_first_sighting_after_a_cut_starts_the_averages():
    tr = tracker._Track(box=(0, 0, 1, 1, 0.9, None), last_t=0.0, dominance=0.02, face_rate=0.9,
                        face_offset=0.05, face_w=0.2, head_rate=0.9, head_offset=0.04)
    tracker._seed_from_sighting(tr, 0.15, 0.5, (0.48 * W, 50.0, 0.1 * W), None, W)
    assert (tr.dominance, tr.head_rate, tr.face_rate, tr.face_offset) == (0.15, 1.0, 0.0, 0.0)
    assert tr.head_offset == pytest.approx(-0.02) and tr.face_w == pytest.approx(0.1)

    # A Haar face: its offset and width from the sighting, but its rate one hit's worth,
    # as on a brand-new track, so it steers the crop only from its third hit in a row.
    tracker._seed_from_sighting(tr, 0.15, 0.5, None, (0.50 * W, 10, 0.56 * W, 40), W)
    assert (tr.head_rate, tr.head_offset, tr.face_rate) == (0.0, 0.0, 0.2)
    assert tr.face_offset == pytest.approx(0.03) and tr.face_w == pytest.approx(0.06)

    tracker._seed_from_sighting(tr, 0.15, 0.5, None, None, W)
    assert (tr.head_rate, tr.face_rate, tr.face_offset, tr.face_w) == (0.0, 0.0, 0.0, 0.0)


def test_last_t_is_a_moment_field():
    import dataclasses

    assert "last_t" in {f.name for f in dataclasses.fields(tracker._Moment)}


# ---- found on real footage: weak openings, one Haar face, a speaker out of sight -------------


def blob(cx, *, conf, width=0.1, height=0.25, head=True):
    """A detection that may be nobody: a box `height` of the frame tall, centred at cx,
    with a pose head on it or not."""
    y1, y2 = 0.35 * H, (0.35 + height) * H
    return ((cx - width / 2) * W, y1, (cx + width / 2) * W, y2, conf,
            (cx * W, y1 + 0.1 * H, 0.04 * W) if head else None)


FAINT = blob(0.78, conf=0.48)                                   # far off, faint, a head on it
HAND = blob(0.75, conf=0.7, width=0.4, height=0.3, head=False)  # confident, but short and headless


def _after_nobody(first):
    """Before the cut at f60 a person at 0.3; after it, nobody on the first two samples,
    then only `first`, then from f88 a person at 0.4 as well."""
    return lambda i: ([person(0.30)] if i < 60 else [] if i < 68 else [first] if i < 88
                      else [person(0.40), first])


def _two_with_a_dropout(back):
    """No cut, a handheld picture. A big person at 0.72 throughout and, behind them, a small
    faint one at 0.33, the one talking (talking_left), whom the detector loses after f56
    and finds again at frame `back`."""
    return Scene(None, lambda i: [person(0.72, width=0.3)] + (
        [] if 56 < i < back else [person(0.33, width=0.08, conf=0.5)]),
        picture=lambda i, _t=_texture(): np.roll(_t, -12 * i, axis=1))


FOUND = {
    "faint_first": Scene(_cut_at(60, A, B), _after_nobody(FAINT)),
    "hand_first": Scene(_cut_at(60, A, B), _after_nobody(HAND)),
    # The faint shape is on the last sample (f80) before the next cut, two frames before it.
    "faint_late": Scene(lambda i: A if i < 60 or i >= 82 else B, lambda i: (
        [person(0.30)] if i < 60 else [FAINT] if i == 80 else [] if i < 82 else [person(0.70)])),
    # After the cut, a person at 0.6 the pose model finds no head on (the cascade's
    # answers are the test's: haar below).
    "headless": Scene(_cut_at(60, A, B), lambda i: (
        [person(0.30)] if i < 60 else [person(0.60, head=False)])),
    # The faint speaker of _two_with_a_dropout, never found again.
    "speaker_leaves": _two_with_a_dropout(10**6),
}


def haar(answers):
    """A stand-in for _face_box: one answer per call, a face (cx, width) in shares of the
    width or None, then None once they run out."""
    answers = iter(answers)

    def face_box(_frame, _box):
        a = next(answers, None)
        if a is None:
            return None
        cx, fw = a
        return (round((cx - fw / 2) * W), round(0.2 * H), round((cx + fw / 2) * W), round(0.2 * H + fw * W))
    return face_box


def talking_left(_path, samples, _fps):
    """A stand-in for _asd_speakers: wherever two people are on screen, the one on the left is
    talking. With one, there is nobody to choose between and no answer, as with TalkNet."""
    def left(s, tid):
        return (s.state[tid].box[0] + s.state[tid].box[2]) / 2 < W / 2

    return [{tid: (1.0 if left(s, tid) else -1.0) for tid in s.visible} if len(s.visible) > 1 else {}
            for s in samples]


def sample_of(box):
    """A one-detection _Sample, for _credible."""
    m = tracker._Moment(box=box, dominance=0.0, speak=0.0, n_seen=1, head_rate=0.0, face_rate=0.0,
                        head_offset=0.0, face_offset=0.0, face_w=0.0, n_centers=1, last_t=0.0)
    return tracker._Sample(t=0.0, frame_idx=0, w=W, h=H, at_cut=False, visible=[0], state={0: m}, faces={})


def test_credible_is_a_confident_person_with_a_head_or_a_body_s_height():
    def credible(box):
        return tracker._credible(sample_of(box), 0)

    assert credible(person(0.5))
    assert credible(person(0.5, conf=0.6))                        # the bar itself
    assert not credible(person(0.5, conf=0.59))                   # a head is not enough below it
    assert not credible(FAINT)
    assert credible(blob(0.5, conf=0.7))                          # small, but with a head
    assert not credible(HAND)                                     # no head, and short
    assert credible(blob(0.5, conf=0.7, height=0.55, head=False))  # no head, but a body's height
    assert not credible(blob(0.5, conf=0.7, height=0.45, head=False))


@pytest.mark.parametrize("name", ["faint_first", "hand_first"])
def test_f1_a_shot_that_waits_opens_only_on_someone_credible(name, monkeypatch):
    result, _ = run(FOUND[name], monkeypatch.setattr, shot_aware=True)
    assert [at(result, f) for f in range(56, 60)] == [0.3] * 4
    # The new shot's centre until somebody credible turns up, whatever else is seen...
    assert [at(result, f) for f in range(60, 88)] == [0.5] * 28
    # ...and then a cut to them, and the camera stays on them.
    assert all(at(result, f) == pytest.approx(0.40, abs=0.01) for f in range(88, N))


def test_f1_with_hold_the_old_framing_waits_for_someone_credible(monkeypatch):
    result, _ = run(FOUND["faint_first"], monkeypatch.setattr, shot_aware=True, no_subject="hold")
    assert [at(result, f) for f in range(56, 88)] == [0.3] * 32
    assert at(result, 88) == pytest.approx(0.40, abs=0.01)


def test_f1_nothing_faint_is_cut_to_just_before_the_next_cut(monkeypatch):
    result, _ = run(FOUND["faint_late"], monkeypatch.setattr, shot_aware=True)
    assert [at(result, f) for f in range(60, 82)] == [0.5] * 22
    assert all(at(result, f) == pytest.approx(0.70, abs=0.01) for f in range(82, N))


def _faint_speaker_after_nobody(clears=None, leaves=None):
    """A cut at f60 to nobody. From f68 a faint person with a head at 0.25, the one talking
    (talking_left) once a confident person appears at 0.75, at f88. The faint one clears the bar
    from frame `clears` on, or is lost from frame `leaves` on."""
    def people(i):
        if i < 60:
            return [person(0.30)]
        faint = [] if i < 68 or (leaves is not None and i >= leaves) else [
            blob(0.25, conf=0.9 if clears is not None and i >= clears else 0.5)]
        return faint + ([person(0.75)] if i >= 88 else [])
    return Scene(_cut_at(60, A, B), people)


@pytest.mark.parametrize("clears, leaves, opens, x", [
    (None, None, N, None),     # below the bar throughout: the shot waits to the end
    (112, None, 112, 0.25),    # clears it at f112: the shot opens on them
    (None, 104, 104, 0.75),    # lost after f100: the shot opens on the confident person
])
def test_m2_a_waiting_shot_does_not_open_on_someone_the_plan_takes_the_camera_from(
        clears, leaves, opens, x, monkeypatch):
    # The shot plan's speaker is the faint person. Until this fix the shot opened on the
    # confident one at f88, and the plan took the camera to the faint one at f92: a shot one
    # sample long (0.5 -> 0.75 -> 0.25). Letting the plan's speaker through the bar instead
    # would open real footage on what _assign handed their id at a cut (a cartoon, a dashboard).
    result, _ = run(_faint_speaker_after_nobody(clears, leaves), monkeypatch.setattr,
                    asd=talking_left, shot_aware=True)
    assert [at(result, f) for f in range(56, 60)] == [0.3] * 4
    assert [at(result, f) for f in range(60, opens)] == [0.5] * (opens - 60)
    # Whoever it opens on keeps the camera: no change of subject right after the opening.
    assert all(at(result, f) == pytest.approx(x, abs=0.01) for f in range(opens, N))


def test_f2_one_haar_face_on_a_first_sighting_does_not_move_the_crop(monkeypatch):
    # The cascade answers on the first sample after the cut only, 0.15 off the body (a face
    # found on a hand), and never again. Trusted at once, that put the crop on 0.75.
    monkeypatch.setattr(tracker, "_face_box", haar([(0.75, 0.06)]))
    result, _ = run(FOUND["headless"], monkeypatch.setattr, shot_aware=True)
    assert at(result, 59) == 0.3
    assert all(at(result, f) == pytest.approx(0.60, abs=0.01) for f in range(60, N))


def test_f2_a_face_the_cascade_keeps_finding_still_steers_the_crop(monkeypatch):
    monkeypatch.setattr(tracker, "_face_box", haar(itertools.repeat((0.72, 0.06))))
    result, _ = run(FOUND["headless"], monkeypatch.setattr, shot_aware=True)
    # What F2 costs a real face only the cascade finds, taken on purpose against single false
    # faces. Trusted from its third hit (f68), not its first (0.72 at once until F2), the face no
    # longer opens the shot alone: the opening is the median of two body centres (0.60) and two
    # face centres (0.7203), 0.660. The trailing median stays the body's for two more samples,
    # so the crop dips back toward it (0.618 at f68), then reaches the face (0.713 from f88).
    xs = [at(result, f) for f in range(60, N)]
    assert xs[0] == pytest.approx(0.660, abs=0.001)
    assert min(xs) == at(result, 68) == pytest.approx(0.618, abs=0.001)
    assert xs[0] - min(xs) <= 0.05
    assert all(abs(x - 0.72) <= 0.01 for x in xs[88 - 60:])
    assert at(result, N - 1) == pytest.approx(0.713, abs=0.001)


@pytest.mark.parametrize("back", [104, 112])
def test_f3_the_speaker_keeps_the_camera_while_out_of_sight_for_a_moment(back, monkeypatch):
    # Until this fix the faint speaker counted as gone a second after f56: the camera cut to
    # the big person at f88 and the shot plan cut it back when they were found at f104,
    # 0.33 -> 0.72 -> 0.33 in 16 frames, with no cut in the footage. Found at f112 instead,
    # 1.87 s on, they would also have lost the camera to the prominence contest at f108.
    scene = _two_with_a_dropout(back)
    result, _ = run(scene, monkeypatch.setattr, asd=talking_left, shot_aware=True)
    assert all(at(result, f) == pytest.approx(0.33, abs=0.01) for f in range(N))
    # Off, no flick either: the replay reads when they were last seen in the whole clip,
    # and the grid rule takes this pan for a cut on every sample, which starts the
    # prominence contest over each time.
    old, _ = run(scene, monkeypatch.setattr, asd=talking_left, shot_aware=False)
    assert all(at(old, f) == pytest.approx(0.33, abs=0.01) for f in range(N))


def test_f3_a_speaker_not_seen_again_that_soon_still_counts_as_gone(monkeypatch):
    # R5 stands for them: gone a second after they were last seen, at f88.
    result, _ = run(FOUND["speaker_leaves"], monkeypatch.setattr, asd=talking_left, shot_aware=True)
    assert at(result, 87) == pytest.approx(0.33, abs=0.01)
    assert all(at(result, f) == pytest.approx(0.72, abs=0.01) for f in range(88, N))


def test_m1_a_sighting_after_the_next_cut_does_not_count_for_a_dropout(monkeypatch):
    # The faint speaker at 0.33 (talking_left) is last seen at f40, beside a big person at 0.72.
    # After a cut at f96 a confident newcomer at 0.335 gets the speaker's id (_assign: the boxes
    # overlap, 1.87 s on). Taken for the speaker coming back, that held the speaker's empty spot
    # to the cut. They are gone a second after f40, at f72, as before the dropout rule.
    scene = Scene(_cut_at(96, A, B), lambda i: [person(0.72, width=0.3)] + (
        [person(0.33, width=0.08, conf=0.5)] if i <= 40 else [] if i < 96
        else [person(0.335, width=0.08, conf=0.9)]))
    result, _ = run(scene, monkeypatch.setattr, asd=talking_left, shot_aware=True)
    assert all(at(result, f) == pytest.approx(0.33, abs=0.005) for f in range(72))
    assert all(at(result, f) == pytest.approx(0.72, abs=0.005) for f in range(72, 96))
    monkeypatch.setattr(tracker._ShotReplay, "dropout", lambda *_a: False)
    without_the_rule, _ = run(scene, monkeypatch.setattr, asd=talking_left, shot_aware=True)
    assert [at(result, f) for f in range(N)] == [at(without_the_rule, f) for f in range(N)]


# ---- O1-O3: the clip's opening shot --------------------------------------------------------

OPENING = {
    # O1: one person, the head 0.08 right of the body's centre, and no cut.
    "opening_head": Scene(lambda i: A, lambda i: [person(0.40, head_dx=0.08)]),
    # O2: a close-up the pose model finds no head on, detected at 0.2 on the first sample (a
    # box off the person) and at 0.6 from then on. The boxes are wide, so they overlap enough
    # (IoU 0.33) for _assign to keep one id.
    "opening_outlier": Scene(lambda i: A, lambda i: [
        person(0.20 if i == 0 else 0.60, width=0.8, head=False)]),
    # O3: a person at 0.6 the pose model finds no head on (the cascade's answers are the
    # test's: haar).
    "opening_haar": Scene(lambda i: A, lambda i: [person(0.60, head=False)]),
}


def test_o1_the_clip_opens_on_the_head_and_stays_there(monkeypatch):
    result, _ = run(OPENING["opening_head"], monkeypatch.setattr, shot_aware=True)
    xs = [x for _, x in result["path"]]
    assert xs[0] == pytest.approx(0.48) and len(set(xs)) == 1
    # Off: the body box first, the head counted only from the second sighting, then a pan
    # toward it as the averages grow, which parks short of it (0.460).
    old, _ = run(OPENING["opening_head"], monkeypatch.setattr, shot_aware=False)
    assert [at(old, f) for f in (0, 12)] == [pytest.approx(0.40)] * 2
    assert at(old, 28) > 0.45 and abs(at(old, N - 1) - 0.48) > 0.015


def test_o2_a_first_detection_off_its_subject_does_not_open_the_clip(monkeypatch):
    monkeypatch.setattr(tracker, "_face_box", haar([]))
    result, _ = run(OPENING["opening_outlier"], monkeypatch.setattr, shot_aware=True)
    assert at(result, 0) == pytest.approx(0.60)      # the median of 0.2, 0.6, 0.6, 0.6
    # On the next sample the trailing median still holds the first detection (0.4, the
    # median of 0.2 and 0.6), as on the sample after a cut: a dip to 0.56, then 0.593.
    assert all(abs(at(result, f) - 0.60) <= 0.05 for f in range(N))
    # Off: it opens on 0.2, and takes over a second to pan across to 0.6.
    old, _ = run(OPENING["opening_outlier"], monkeypatch.setattr, shot_aware=False)
    assert at(old, 0) == pytest.approx(0.20) and at(old, 32) < 0.55


def test_o3_one_haar_face_on_the_first_sample_does_not_move_the_crop(monkeypatch):
    # The cascade answers on the clip's first sample only, 0.15 off the body (a face found on
    # a hand). Seeded from that sighting, it counts as one hit, as after a cut (F2).
    monkeypatch.setattr(tracker, "_face_box", haar([(0.75, 0.06)]))
    result, _ = run(OPENING["opening_haar"], monkeypatch.setattr, shot_aware=True)
    assert all(at(result, f) == pytest.approx(0.60, abs=1e-9) for f in range(N))


def test_o3_a_face_the_cascade_keeps_finding_steers_the_crop_from_about_a_second_in(monkeypatch):
    # F2's cost at the clip's start: the opening is the median of two body centres (0.60) and
    # two face centres (0.7203), the crop dips back toward the body while the trailing median
    # is the body's, then reaches the face 28 frames in. Off, it eases from the body and parks
    # 0.02 short of the face.
    monkeypatch.setattr(tracker, "_face_box", haar(itertools.repeat((0.72, 0.06))))
    result, _ = run(OPENING["opening_haar"], monkeypatch.setattr, shot_aware=True)
    xs = [at(result, f) for f in range(N)]
    assert xs[0] == pytest.approx(0.660, abs=0.001)
    assert min(xs) == at(result, 8) == pytest.approx(0.618, abs=0.001)
    assert all(abs(x - 0.72) <= 0.01 for x in xs[28:])
    old, _ = run(OPENING["opening_haar"], monkeypatch.setattr, shot_aware=False)
    assert at(old, 0) == pytest.approx(0.60) and abs(at(old, N - 1) - 0.72) > 0.015


# ---- the reader ------------------------------------------------------------------------------


def _thumbs(scene, *frames):
    from video.framing import small_gray

    return [small_gray(scene.frame(i)) for i in frames]


def test_a_pair_below_the_candidate_bar_decodes_nothing():
    scene = Scene(_cut_at(62, A, A), lambda i: [])
    cap = FakeCapture(scene)
    reader = cuts.ShotCuts(cap, fallback=55.0)
    assert reader.check(*_thumbs(scene, 56, 60), 56, 60) is None
    assert cap.retrieved == 0 and cap.seeks == 0


def test_without_a_reader_cuts_are_called_the_old_way():
    scene = Scene(_cut_at(62, A, B), lambda i: [])
    reader = cuts.ShotCuts(None, fallback=55.0)
    assert reader.check(*_thumbs(scene, 60, 64), 60, 64) == 64       # 121 > 55: on the sample
    weak = Scene(_cut_at(62, A, WEAK), lambda i: [])
    assert reader.check(*_thumbs(weak, 60, 64), 60, 64) is None      # 40: not at all


def test_a_seek_that_lands_off_its_frame_is_not_trusted():
    scene = Scene(lambda i: A if i < 202 else B if i < 402 else WEAK, lambda i: [], n=600)
    cap = FakeCapture(scene, shift=3)
    reader = cuts.ShotCuts(cap, fallback=55.0)
    assert reader.check(*_thumbs(scene, 200, 204), 200, 204) == 204  # called the old way
    retrieved = cap.retrieved
    assert reader.check(*_thumbs(scene, 400, 404), 400, 404) == 404   # and from then on,
    assert cap.retrieved == retrieved                                 # without decoding
