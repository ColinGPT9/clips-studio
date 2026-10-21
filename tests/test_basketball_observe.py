"""Basketball framing's observation stage (sports/basketball/crop/observe.py).

Local only: it decodes real video with FFmpeg and OpenCV, which CI does not
install. A synthetic clip, made here, has one hard cut and a known camera pan,
so pass A1 must put the cut on its exact frame and read the pan's direction
and speed; pass A2 must hand back the detection frames at the documented
cadence, denser around the basket. One more test runs the whole stage with
the COCO detector on a bench clip when the bench data is on this machine.
"""

import json
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from core.binaries import ffmpeg  # noqa: E402
from sports.basketball.crop import observe as ob  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "data" / "framing_bench"

W, H, FPS = 640, 360, 30
CUT = 45                 # the first frame of the second shot
PAN_PX = 4               # the first shot's camera moves this many pixels right per frame
FRAMES = 300             # 10 s


def _texture(seed: int, tint: tuple[int, int, int], size: tuple[int, int]):
    """A blurred random picture, so phase correlation has structure to lock on."""
    rng = np.random.default_rng(seed)
    noise = rng.random((size[1], size[0])).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), 3)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    img = np.stack([noise * c for c in tint], axis=-1)
    return np.clip(img + 20, 0, 255).astype(np.uint8)


def _make_clip(path: Path) -> None:
    """Shot one (frames 0..CUT-1): the camera pans right over a warm picture.
    Shot two (CUT..): a cool, different picture, the camera still."""
    first = _texture(1, (60, 140, 230), (W * 3, H))
    second = _texture(2, (230, 160, 40), (W, H))
    cmd = [ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
           "-g", "15", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(FRAMES):
        frame = first[:, f * PAN_PX:f * PAN_PX + W] if f < CUT else second
        proc.stdin.write(np.ascontiguousarray(frame).tobytes())
    proc.stdin.close()
    assert proc.wait(timeout=120) == 0


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("observe") / "synthetic.mp4"
    _make_clip(path)
    return path


NO_DETECTORS = {"ball": "none", "hoop": "none", "people": "none"}


def test_pass_a1_finds_the_cut_frame_and_the_pan(clip):
    sig = ob.frame_signals(clip, FPS, W, H)
    assert sig.frames == FRAMES
    assert len(sig.d_col) == len(sig.pan_dx) == len(sig.pan_dy) == len(sig.luma) == FRAMES
    # The cut is the biggest change, on its exact frame, in both measures.
    assert max(range(FRAMES), key=lambda k: sig.d_gray[k]) == CUT
    assert max(range(FRAMES), key=lambda k: sig.d_col[k]) == CUT
    assert sig.d_gray[CUT] > 18 and sig.d_col[CUT] > 0.3
    assert max(sig.d_gray[1:CUT] + sig.d_gray[CUT + 1:]) < 0.5 * sig.d_gray[CUT]
    # The camera moved right in shot one (the picture left): positive pan_dx, at its speed.
    pans = sig.pan_dx[1:CUT]
    assert all(p > 0 for p in pans)
    assert float(np.median(pans)) == pytest.approx(PAN_PX / W, rel=0.1)
    assert max(abs(p) for p in sig.pan_dy[1:CUT]) < 0.002
    # Shot two holds still.
    assert max(abs(p) for p in sig.pan_dx[CUT + 1:]) < 0.002
    # Its cuts, and the travel reset at each.
    cuts = ob.cuts_of(sig)
    assert [c.frame for c in cuts] == [CUT]
    px, _ = ob.travel(sig, cuts)
    assert px[CUT - 1] == pytest.approx((CUT - 1) * PAN_PX / W, rel=0.1)
    assert px[CUT] == 0.0


def test_detection_frames_are_denser_around_the_moment(clip):
    moment = 6.0
    obs = ob.observe(clip, NO_DETECTORS, moment_t=moment)
    base, dense, span = ob.schedule(FPS, moment)
    assert (base, dense) == (5, 2)                   # 6 Hz and 15 Hz at 30 fps
    assert span == (60, 240)                          # [moment - 4 s, moment + 2 s]
    want = [n for n in range(FRAMES) if n % 5 == 0 or (60 <= n <= 240 and n % 2 == 0)]
    assert [s.frame for s in obs.samples] == want
    assert [s.i for s in obs.samples] == list(range(len(want)))
    for s in obs.samples:
        assert s.t == pytest.approx(s.frame / FPS, abs=1e-3)
        assert s.dense == (60 <= s.frame <= 240)
    gaps_in = [b.t - a.t for a, b in zip(obs.samples, obs.samples[1:]) if a.dense and b.dense]
    gaps_out = [b.t - a.t for a, b in zip(obs.samples, obs.samples[1:]) if not a.dense and not b.dense]
    assert max(gaps_in) <= 2 / FPS + 1e-3            # times are kept to 0.1 ms
    assert min(gaps_out) == pytest.approx(5 / FPS, abs=1e-3)
    assert obs.geometry.frames == FRAMES and obs.geometry.width == W and obs.geometry.fps == pytest.approx(FPS)
    # The camera's travel per sample: growing through shot one, 0 from the cut on.
    first = [s for s in obs.samples if s.frame < CUT]
    assert all(b.pan[0] > a.pan[0] for a, b in zip(first, first[1:]))
    assert first[-1].pan[0] == pytest.approx(first[-1].frame * PAN_PX / W, rel=0.1)
    assert all(abs(s.pan[0]) < 0.01 for s in obs.samples if s.frame >= CUT)
    # The floor's colour: shot two's picture is the clip's most common colour here.
    assert all(s.court is not None and 0.0 <= s.court <= 1.0 for s in obs.samples)


def test_without_a_moment_every_frame_at_ten_a_second(clip):
    obs = ob.observe(clip, NO_DETECTORS)
    assert [s.frame for s in obs.samples] == list(range(0, FRAMES, 3))
    assert not any(s.dense for s in obs.samples)


def test_a_cancel_stops_both_passes(clip):
    from core.cancel import CancelledError

    calls = []

    def cancel():
        calls.append(1)
        return len(calls) > 5

    with pytest.raises(CancelledError):
        ob.observe(clip, NO_DETECTORS, moment_t=6.0, cancel=cancel)


def test_select_filter_matches_the_python_rule():
    assert ob.select_filter(10, 4, (275, 634)) == \
        "select='not(mod(n\\,10))+between(n\\,275\\,634)*not(mod(n\\,4))'"
    assert ob.select_filter(6, 0, None) == "select='not(mod(n\\,6))'"
    assert ob.schedule(59.94, None) == (6, 0, None)
    assert ob.schedule(59.94, 1.0) == (10, 4, (0, 179))


def _bench_clip():
    meta_path = BENCH / "meta.json"
    if not meta_path.exists():
        pytest.skip("no basketball bench data on this machine")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    found = next(
        ((cid, entry)
         for cid, entry in sorted(meta.get("clips", {}).items(), key=lambda kv: kv[1].get("duration", 1e9))
         if entry.get("sport_t") is not None and (BENCH / entry["path"]).exists()),
        None,
    )
    if found is None:
        pytest.skip("no bench clip with a basket time")
    return found


def test_a_bench_clip_with_the_coco_detector():
    pytest.importorskip("ultralytics")
    if not (ROOT / "yolov8n.pt").exists() and not (ROOT / "models" / "yolov8n.pt").exists():
        pytest.skip("no yolov8n.pt")
    cid, entry = _bench_clip()
    timings: dict = {}
    obs = ob.observe(BENCH / entry["path"], {"ball": "coco", "hoop": "none", "people": "coco"},
                     moment_t=entry["sport_t"], timings=timings)
    assert obs.signals.frames == entry["frames"]
    assert (obs.geometry.width, obs.geometry.height) == (entry["width"], entry["height"])
    base, dense, span = ob.schedule(entry["fps"], entry["sport_t"])
    assert len(obs.samples) == sum(ob.selected(n, base, dense, span) for n in range(entry["frames"]))
    # A broadcast: people in nearly every sample, the floor's colour mostly present.
    assert sum(bool(s.people) for s in obs.samples) >= 0.9 * len(obs.samples)
    assert sorted(s.court for s in obs.samples)[len(obs.samples) // 2] > 0.3
    for s in obs.samples:
        for b in s.balls:
            assert b.src == "coco" and b.w > 0 and b.h > 0
            assert b.orange is not None and 0 <= b.orange <= 1 and b.contrast is not None
    assert timings["samples"] == len(obs.samples) and timings["total"] > 0
