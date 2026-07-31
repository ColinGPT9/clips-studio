"""Active speaker detection: who, out of the faces on screen, is talking.

Mouth-region pixel motion cannot answer this. It measures MOVEMENT, and two
people in a lively room move about equally — measured on a real two-person
clip the speaker and the listener scored 0.0746 and 0.0801, a 7% difference,
while their on-screen sizes differed by 45%. Anything built on that signal
ends up following whoever is bigger.

TalkNet-ASD answers it properly: a network trained on labelled footage to
judge whether a face's mouth movement matches the sound. It is the same class
of model the commercial clipping tools use.

Everything here runs locally. The weights ship with the app the same way the
YOLO weights do (see clips-studio.spec) — nothing is fetched at runtime.

COST, measured on a 60s clip on an RTX 3060, because the obvious way to feed
this model is five times slower than the careful way and that is not evident
from reading it:

    tracking as it stands (8fps)              62.3s
    TalkNet forward pass, two people           1.2s   <- not the expensive part
    frame reads + crops, reusing 8fps boxes     8.4s   -> +15% overall
    re-detecting with YOLO at 25fps          280.2s   -> +452% overall

The model is nearly free. The cost is entirely in getting frames to it, and
the trap is that TalkNet wants 25fps while the tracker samples at 8. Running
the detector again at 25fps to satisfy that makes every job five times
slower; interpolating the boxes already computed at 8fps and reading frames
between them costs 15%. Do not "simplify" this by detecting at 25fps.

At 15% it can run on everything rather than behind a "does this look like two
people talking?" test — which would be a heuristic guarding the very question
the model exists to answer, and would fall back silently to the mouth-motion
path when it guessed wrong. The one worthwhile skip is not a judgement at
all: with fewer than two faces on screen there is nobody to choose between.

The model itself is vendored, unmodified, under third_party/talknet/ (MIT,
(c) 2021 Tao Ruijie). This module is the adapter: it owns loading, the input
format, and a single scoring call, so the vendored files stay exactly as
published and can be re-fetched or upgraded without untangling our changes
from theirs.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _ROOT / "third_party" / "talknet"

# What the network was trained on; none of these are free parameters.
FPS = 25            # visual frames per second
AUDIO_SR = 16000    # audio sample rate for the MFCCs
FACE_SIZE = 112     # face crop, greyscale, square
_MFCC_PER_FRAME = 4  # 100fps MFCC against 25fps video

_model = None
_head = None


@contextmanager
def _vendor_on_path():
    """TalkNet's modules import each other flatly ("from model.talkNetModel
    import ..."), which only resolves with its own root on sys.path. Adding it
    for the duration of the import keeps the vendored files unmodified."""
    sys.path.insert(0, str(_VENDOR))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(_VENDOR))
        except ValueError:
            # Already gone: something else cleaned sys.path while the import
            # ran. The goal is "this entry is not left behind", which is
            # satisfied either way, so there is nothing to report.
            pass


def weights_path() -> Path:
    """Where the checkpoint lives, frozen build or checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "pretrain_TalkSet.model"  # type: ignore[attr-defined]
    return _ROOT / "models" / "pretrain_TalkSet.model"


def available() -> bool:
    """True when speaker detection can actually run. Callers fall back to the
    motion heuristic rather than failing a render."""
    return weights_path().exists() and _VENDOR.exists()


def _load():
    """Build the network and load the checkpoint. Cached: this is expensive
    and every clip in a video needs it."""
    global _model, _head
    if _model is not None:
        return _model, _head

    import torch

    with _vendor_on_path():
        from loss import lossAV
        from model.talkNetModel import talkNetModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, head = talkNetModel().to(device), lossAV().to(device)

    # The checkpoint was saved from the training wrapper, so its keys carry
    # "model." and "lossAV." prefixes for the two halves.
    state = torch.load(weights_path(), map_location=device, weights_only=True)
    model.load_state_dict(
        {k[len("model."):]: v for k, v in state.items() if k.startswith("model.")}
    )
    head.load_state_dict(
        {k[len("lossAV."):]: v for k, v in state.items() if k.startswith("lossAV.")}
    )
    model.eval()
    head.eval()
    _model, _head = model, head
    return _model, _head


def mfcc(pcm: np.ndarray) -> np.ndarray:
    """13-band MFCCs at 100fps, the audio side of the model's input.

    `pcm` is ordinary normalised audio, floats in -1..1.

    python_speech_features is what TalkNet was written against; its default
    settings are part of the trained model's expected input, so they are
    spelled out rather than left to a different library's defaults.

    The x32768 matters and is not cosmetic. Upstream reads its audio with
    scipy's wavfile.read, which hands back INT16 sample values, so every MFCC
    the model was trained on carries the energy of a signal 32768 times
    larger. MFCCs are log-energy, so getting this wrong does not scale the
    features, it OFFSETS them by about 20 per band — a constant the model has
    never seen. Passing normalised floats scored a minute of two-way
    conversation as nobody speaking at all.
    """
    from python_speech_features import mfcc as _mfcc

    return _mfcc(pcm * 32768.0, AUDIO_SR, numcep=13, winlen=0.025, winstep=0.010)


# How the model is asked, and it is not "all of it at once".
#
# TalkNet was trained on short segments and its own evaluation scores a track
# in windows of a few seconds, then averages the windows. Handing it a whole
# 60s clip in one forward pass is far outside what it ever saw: measured that
# way on real two-person footage EVERY frame of EVERY face came back negative
# — a confident "nobody here is speaking" for a minute of conversation. The
# model was not wrong, it was being asked a question in a shape it does not
# take. Windowing it fixed that.
#
# Several window lengths, averaged, because a 1s window is decisive but
# twitchy and a 6s window is stable but smears the moment somebody starts.
_WINDOWS = (1, 2, 4, 6)   # seconds
# Visual frames per forward pass. The 3D frontend expands each frame a long
# way, so this is the knob that decides peak VRAM; 1500 is 60 seconds of video
# and fits comfortably beside YOLO on a 6GB card.
_BATCH_FRAMES = 1500


def score_track(faces: np.ndarray, pcm: np.ndarray) -> np.ndarray:
    """How strongly this face is speaking, one value per visual frame.

    faces: (T, 112, 112) uint8 greyscale crops centred on ONE person's face,
           sampled at 25fps.
    pcm:   mono float32 at 16kHz covering the same span.

    Higher is more likely to be speaking; positive means speaking. The scale
    is the model's own logit, so the useful comparison is between faces at the
    same moment rather than against a fixed number.
    """
    import torch

    model, head = _load()
    device = next(model.parameters()).device

    audio = mfcc(pcm)
    # The two streams must line up: 4 MFCC rows per visual frame. Trim to
    # whichever runs out first — a face track can start or end mid-clip.
    frames = min(len(faces), audio.shape[0] // _MFCC_PER_FRAME)
    if frames < FPS:  # under a second: no window to put it in
        return np.zeros(len(faces), dtype=np.float32)
    audio = audio[: frames * _MFCC_PER_FRAME]
    vis = faces[:frames]

    passes = []
    with torch.no_grad():
        for seconds in _WINDOWS:
            v_step = seconds * FPS
            got = np.zeros(frames, dtype=np.float32)
            # The windows are all the same length bar the last, so they go
            # through as ONE batch rather than one call each. Same arithmetic,
            # a fraction of the launches: scoring a 60s clip one window at a
            # time took 23s, batched it takes a few.
            whole = frames // v_step
            for start, count, step in ((0, whole, v_step),
                                       (whole * v_step, 1, frames - whole * v_step)):
                if count == 0 or step < 2:
                    continue
                for lo in range(0, count, max(1, _BATCH_FRAMES // step)):
                    n = min(count - lo, max(1, _BATCH_FRAMES // step))
                    off = start + lo * step
                    v = vis[off:off + n * step].reshape(n, step, FACE_SIZE, FACE_SIZE)
                    a = audio[off * _MFCC_PER_FRAME:
                              (off + n * step) * _MFCC_PER_FRAME]
                    a = a.reshape(n, step * _MFCC_PER_FRAME, -1)
                    at = torch.tensor(a, dtype=torch.float32, device=device)
                    vt = torch.tensor(v, dtype=torch.float32, device=device)
                    ae = model.forward_audio_frontend(at)
                    ve = model.forward_visual_frontend(vt)
                    ae, ve = model.forward_cross_attention(ae, ve)
                    out = model.forward_audio_visual_backend(ae, ve)
                    s = np.asarray(head.forward(out, labels=None)).reshape(-1)
                    got[off:off + n * step] = s[: n * step]
            passes.append(got)

    scores = np.mean(np.stack(passes), axis=0)
    if len(scores) < len(faces):  # pad the trimmed tail with its last value
        scores = np.concatenate([scores, np.repeat(scores[-1:], len(faces) - len(scores))])
    return scores[: len(faces)]
