"""A clip's base video made from the clip's own framing, in seconds.

render_base (kitty_editor/build.py) makes a clip project's caption-free
video the way the clip itself was made: the cut, face tracking (YOLO and
TalkNet), the crop, colour and loudness. The tracking is most of a minute
on a 25-second 60 fps clip, and all it finds is what the clip in the
Library already shows: where the crop sits in each frame.

So for a clip framed by the tracker's moving crop, the crop is read back
from the clip's own file: a small grey copy of each sampled frame of the
clip is matched against a small grey copy of the source's, along the width
only, at the tracker's own sample frames (and at each frame between two
samples where the crop jumps, as at a camera cut). Then one FFmpeg pass
crops the source there and lays it out as video/cropper._render_tracked
does, with the clip's colour and loudness.

Two layouts are read back, the two _render_tracked makes without a sport's
zoom: the 9:16 crop filling the screen ("full"), and the face-safe 4:5 crop
shown under the apps' top bar over a blurred copy of itself ("face", for a
face high in the frame). The captions, a watermark or a logo in the clip's
file take no part in the match: each frame is compared in eight bands across
its height, and only the five that agree best count.

Any other clip (letterbox, split-screen, podcast, gaming, sport, Vertical
Live, Longform, a clip with an edit of its own) is left to render_base's
full render: make() returns None. So is a clip any sampled frame of which
matches badly, so a framing that can't be read back is never guessed.

Nothing here is used by a clipping run or by Quick edit.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

SCAN_HEIGHT = 360       # the grey copies of the source are this tall
CLIP_SCAN = (360, 640)  # and the clip's this size
BANDS = 8               # each frame is compared in this many bands across its height
KEEP = 5                # the bands that agree best: captions and logos cover the others
MIN_SCORE = 0.50        # a sampled frame matching worse than this is not the clip's crop
MIN_DETAIL = 0.30       # nor one whose fine detail (DETAIL_BLUR) matches worse than this,
SURE_SCORE = 0.95       # unless it matches this well (a blurred frame of the clip's own crop: unsure)
DETAIL_BLUR = 1.5       # the fine detail: a frame less itself blurred this much (grey pixels)
MIN_LEAD = 0.015        # a match this little better than any 3% of the width away is not sure
JUMP = 0.05             # the crop moving this share of the source's width between samples: a cut
MOST_UNSURE = 0.5       # more of the samples than this unsure: the framing can't be read
DECIDE = 3              # sure samples that decide the layout
OUT_W, OUT_H = 1080, 1920
FACE_TOP = int(0.115 * 1920)   # video/cropper._render_tracked: the 4:5 picture starts under the apps' tabs
UNTAGGED = "setparams=range=unknown:color_primaries=unknown:color_trc=unknown:colorspace=unknown"


class NotPlain(Exception):
    """The clip is not framed by a moving crop, or it can't be read back."""


@dataclass(frozen=True)
class Layout:
    name: str
    crop_w: int         # the crop in the source
    crop_h: int
    top: int            # where the picture starts on the 1080x1920 screen
    height: int         # and how tall it is there


def layouts(src_w: int, src_h: int) -> tuple[Layout, ...]:
    """The crops video/cropper._render_tracked takes from a source this size
    (rows None, no zoom or bands): 9:16 filling the screen, and 4:5 at full
    width under the apps' top bar."""
    out = []
    for name, aspect in (("full", 9 / 16), ("face", 4 / 5)):
        crop_w = int(src_h * aspect)
        crop_w -= crop_w % 2
        crop_w = min(crop_w, src_w)
        if name == "full":
            out.append(Layout(name, crop_w, src_h, 0, OUT_H))
        else:
            height = round(OUT_W * src_h / crop_w / 2) * 2       # FFmpeg's scale=1080:-2
            if height < OUT_H - FACE_TOP:
                out.append(Layout(name, crop_w, src_h, FACE_TOP, height))
    return tuple(out)


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def plain_layout(opts: dict, config: dict) -> bool:
    """Whether the clip's render took core.pipeline._render_files' tracked
    branch with the standard crop: no podcast, gaming, sport, Vertical Live,
    Longform, user-chosen letterbox, or edit of its own (whose cuts and speed
    the clip's file has, and the base has not)."""
    from core import modes

    clips = config.get("clips") or {}
    if not clips.get("vertical", True) or opts.get("profile"):
        return False
    if modes.is_vertical_live(opts) or modes.is_vertical_live(config):
        return False
    if modes.sport(opts) or modes.sport(config):
        return False
    if opts.get("podcast") or clips.get("podcast"):
        return False
    if modes.is_gaming(opts) or (modes.is_gaming(config) and not ("gaming" in opts and opts["gaming"] is None)):
        return False
    if opts.get("crop", "track") not in ("track", "center", "bias_left", "bias_right"):
        return False
    return not opts.get("edit")


def window(row, opts: dict, config: dict) -> tuple[float, float]:
    """The clip's cut as render_base makes it: its start, and its length
    with the render's end pad (core.pipeline._render_files)."""
    from core.pipeline import END_PAD

    start, end = float(row["start_s"]), float(row["end_s"])
    whole = bool((config.get("clips") or {}).get("whole") or opts.get("whole"))
    pad = 0.0 if whole else END_PAD
    stop = opts.get("stop_by")
    if isinstance(stop, (int, float)) and float(stop) >= (end - start) - 0.01:
        pad = max(0.0, min(pad, float(stop) - (end - start)))
    return start, (end + pad) - start


def _rate(fps: str) -> float:
    num, _, den = fps.partition("/")
    return float(num) / float(den or 1)


def _grey(cmd: list[str], width: int, height: int):
    """The frames an FFmpeg command writes as grey rawvideo, as arrays."""
    import numpy as np

    size = width * height
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_flags())
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                return
            yield np.frombuffer(buf, np.uint8).reshape(height, width)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()


def _source_filters(fps: str, vf_extra: str = "") -> str:
    """The source's frames at the clip's constant rate on its own clock, as
    the standard render's cut makes them (video/cutter.cut_clip's -vsync cfr,
    with -fps_mode cfr on the output): a first frame that falls nearer the
    second slot is shown in both, as there. Frame n is the clip's frame n,
    at n / rate (the crop script's times)."""
    return ",".join(p for p in (f"fps={fps}", vf_extra) if p)


def _match(src, tmpl, lead_px: int) -> tuple[float | None, float, float | None]:
    """Where `tmpl` (the clip's picture, grey, scaled to the crop's size in
    the grey source frame) sits along `src`'s width: its left edge in grey
    pixels, or None when unsure; how well it matches there (1 = the same);
    and how well its fine detail does (None when it has none to speak of).

    The second tells a crop from what only looks like one: a blurred wash
    (the letterbox layout's bands) or a whole frame scaled down correlates
    with the source well enough, its fine detail never does (about 0.05,
    against 0.9 and more for the clip's own crop)."""
    import cv2
    import numpy as np

    src = src.astype(np.float32)
    tmpl = tmpl.astype(np.float32)
    # Found on half-size copies, then placed to the pixel on the full ones
    # around there: an eighth of the work of a full search.
    small_src, small_tmpl = cv2.pyrDown(src), cv2.pyrDown(tmpl)
    score = _scores(small_src, small_tmpl)
    if score is None:
        return None, 0.0, None
    best = int(score.argmax())
    far = np.abs(np.arange(score.size) - best) > max(1, lead_px // 2)
    lead = float(score[best] - score[far].max()) if far.any() else 1.0
    if lead < MIN_LEAD:
        return None, float(score[best]), None
    width = tmpl.shape[1]
    lo = max(0, 2 * best - 3)
    hi = min(src.shape[1], 2 * best + 3 + width)
    fine = _near_scores(src[:, lo:hi], tmpl)
    if fine is None:
        return None, float(score[best]), None
    near = int(fine.argmax())
    x = float(lo + near)
    if 0 < near < fine.size - 1:
        left, mid, right = (float(v) for v in fine[near - 1:near + 2])
        bend = left - 2 * mid + right
        if bend < 0:
            x += max(-0.5, min(0.5, 0.5 * (left - right) / bend))
    at = lo + near
    strip = src[:, max(0, at - 1):min(src.shape[1], at + 1 + width)]
    detail = _near_scores(_detail(strip), _detail(tmpl))
    return x, float(fine[near]), None if detail is None else float(detail.max())


def _detail(picture):
    """A grey picture's fine detail: itself less a blurred copy."""
    import cv2

    return picture - cv2.GaussianBlur(picture, (0, 0), DETAIL_BLUR)


def _scores(src, tmpl):
    """How well `tmpl` matches `src` at each position along its width: the
    mean correlation of the KEEP bands that agree best (None when fewer than
    three bands have anything in them)."""
    import cv2
    import numpy as np

    h = src.shape[0]
    edges = np.linspace(0, h, BANDS + 1).astype(int)
    scores = []
    for a, b in zip(edges, edges[1:]):
        band = np.ascontiguousarray(tmpl[a:b])
        if b - a < 2 or float(band.std()) < 2.0:
            continue                            # a flat band (black, a plain wall) says nothing
        scores.append(cv2.matchTemplate(np.ascontiguousarray(src[a:b]), band, cv2.TM_CCOEFF_NORMED)[0])
    if len(scores) < 3:
        return None
    stack = np.sort(np.nan_to_num(np.array(scores), nan=-1.0), axis=0)[::-1]
    return stack[:min(KEEP, len(scores))].mean(axis=0)


def _near_scores(src, tmpl):
    """_scores for a strip only a few positions wider than `tmpl`, worked
    out directly (matchTemplate's set-up costs more than the sums here)."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    h, width = tmpl.shape
    if src.shape[1] < width:
        return None
    views = sliding_window_view(src, width, axis=1)          # (h, positions, width)
    edges = np.linspace(0, h, BANDS + 1).astype(int)
    scores = []
    for a, b in zip(edges, edges[1:]):
        band = tmpl[a:b]
        if b - a < 2 or float(band.std()) < 2.0:
            continue
        t = (band - band.mean()).ravel()
        patches = views[a:b].transpose(1, 0, 2).reshape(views.shape[1], -1)
        patches = patches - patches.mean(axis=1, keepdims=True)
        den = np.sqrt((patches * patches).sum(axis=1) * float(t @ t))
        scores.append(np.where(den > 0, patches @ t / np.maximum(den, 1e-9), -1.0))
    if len(scores) < 3:
        return None
    stack = np.sort(np.array(scores), axis=0)[::-1]
    return stack[:min(KEEP, len(scores))].mean(axis=0)


class _Reader:
    """Matches one layout's picture, frame after frame."""

    def __init__(self, layout: Layout, scale: float, sw: int, sh: int):
        self.layout = layout
        self.scale = scale
        self.tw = max(2, min(sw, round(layout.crop_w * scale)))
        self.sh = sh
        cw, ch = CLIP_SCAN
        self.rows = (round(layout.top * ch / OUT_H), round((layout.top + layout.height) * ch / OUT_H))
        self.lead_px = max(2, round(0.03 * sw))
        self.jump_px = JUMP * sw
        self.keys: list[tuple[int, float]] = []
        self.scores: list[float] = []
        self.seen: list[tuple[int, float, float]] = []     # (frame, score, detail), for the record
        self.unsure = 0
        self.last: float | None = None

    def template(self, clip):
        import cv2

        a, b = self.rows
        return cv2.resize(clip[a:b], (self.tw, self.sh), interpolation=cv2.INTER_AREA)

    def sample(self, frame: int, src, clip, held: list) -> bool:
        """Read the crop at a sample frame; False when it doesn't match."""
        x, score, detail = _match(src, self.template(clip), self.lead_px)
        if x is None or detail is None:
            self.unsure += 1
            return True
        self.seen.append((frame, round(score, 3), round(detail, 3)))
        if score < MIN_SCORE or detail < MIN_DETAIL:
            if score >= SURE_SCORE:
                self.unsure += 1
                return True
            return False
        self.scores.append(score + detail)
        if self.last is not None and abs(x - self.last) > self.jump_px:
            # A jump (a camera cut): each frame between the two samples, so the
            # crop moves on the frame it moved on in the clip.
            for f, s, c in held:
                fx, fscore, fdetail = _match(s, self.template(c), self.lead_px)
                if fx is not None and fscore >= MIN_SCORE and (fdetail or 0) >= MIN_DETAIL:
                    self.keys.append((f, fx))
        self.keys.append((frame, x))
        self.last = x
        return True


def read_framing(source: Path, clip_file: Path, start: float, seconds: float, *, src_w: int, src_h: int,
                 fps: str, step: int, vf_extra: str = "", progress=None,
                 record: list | None = None) -> tuple[Layout, list, int]:
    """The clip's layout and the crop's left edge in the source at its sample
    frames, read back from the clip's file: (layout, [(frame, x)], frames).
    Raises NotPlain when the clip's frames are not such a crop of the source's.
    `record`, when given, gets each sample's (layout, frame, score, detail),
    also when it raises."""
    from core.binaries import ffmpeg

    scale = min(1.0, SCAN_HEIGHT / src_h)
    sh = max(2, round(src_h * scale))
    sw = max(2, round(src_w * scale))
    cw, ch = CLIP_SCAN
    total = max(1, round(seconds * _rate(fps)))
    # Without the decoders' deblocking: a tenth quicker, and nothing a third
    # of the size can see.
    fast = ["-skip_loop_filter", "all"]
    src_cmd = [ffmpeg(), "-v", "error", *fast, "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{seconds:.2f}",
               "-an", "-sn", "-vf", f"{_source_filters(fps, vf_extra)},scale={sw}:{sh}:flags=area,format=gray",
               "-fps_mode", "cfr", "-f", "rawvideo", "-"]
    clip_cmd = [ffmpeg(), "-v", "error", *fast, "-i", str(clip_file), "-an", "-sn",
                "-vf", f"scale={cw}:{ch}:flags=area,format=gray", "-f", "rawvideo", "-"]
    readers = [_Reader(layout, scale, sw, sh) for layout in layouts(src_w, src_h)]
    try:
        return _read(list(readers), src_cmd, clip_cmd, sw, sh, step, total, scale, progress)
    finally:
        if record is not None:
            record.extend((r.layout.name, *seen) for r in readers for seen in r.seen)


def _read(readers: list, src_cmd: list, clip_cmd: list, sw: int, sh: int, step: int, total: int, scale: float,
          progress) -> tuple[Layout, list, int]:
    """read_framing's work, frame after frame. (Matching in a second thread
    while the frames are read was measured no faster: decoding the two
    videos takes the CPU.)"""
    cw, ch = CLIP_SCAN
    sources, clips = _grey(src_cmd, sw, sh), _grey(clip_cmd, cw, ch)
    held: list = []                             # the frames since the last sample: (frame, src, clip)
    sampled = 0
    frame = -1
    try:
        for frame, (src, clip) in enumerate(zip(sources, clips)):
            if frame % step:
                held.append((frame, src, clip))
                continue
            sampled += 1
            readers[:] = [r for r in readers if r.sample(frame, src, clip, held)]
            if not readers:
                raise NotPlain(f"frame {frame} is not a crop of the video")
            if len(readers) > 1 and min(len(r.scores) for r in readers) >= DECIDE:
                # The layout is the clip's for its whole length: the one that matches.
                readers[:] = [max(readers, key=lambda r: sum(r.scores[:DECIDE]))]
            held = []
            if progress is not None:
                progress(min(1.0, frame / total))
    finally:
        sources.close()
        clips.close()
    if len(readers) != 1:
        raise NotPlain("the layout could not be told")
    reader = readers[0]
    if frame < 0 or not reader.keys or reader.unsure > MOST_UNSURE * sampled:
        raise NotPlain("too few frames could be matched")
    if frame + 1 < total - 1:
        raise NotPlain(f"the clip's file ends at frame {frame + 1} of {total}")
    return reader.layout, [(f, x / scale) for f, x in reader.keys], frame + 1


def crop_lines(keys: list, frames: int, *, src_w: int, crop_w: int, fps: str) -> tuple[int, str]:
    """The crop's left edge at each frame, the keys joined by straight lines
    (as video/cropper._interpolate joins the tracker's): the first frame's,
    and an FFmpeg sendcmd script for each frame where it changes."""
    import numpy as np

    at = np.array([k[0] for k in keys], dtype=float)
    xs = np.array([k[1] for k in keys], dtype=float)
    edge = np.interp(np.arange(frames, dtype=float), at, xs)
    left = np.clip(np.rint(edge), 0, max(0, src_w - crop_w)).astype(int)
    rate = _rate(fps)
    lines = [f"{(n - 0.5) / rate:.6f} crop@base x {int(left[n])};" for n in range(1, frames)
             if left[n] != left[n - 1]]
    return int(left[0]), "\n".join(lines) + "\n"


def colour_steps(source: Path) -> str:
    """What turns the source's pictures into the colours _render_tracked's
    frames have when they reach FFmpeg: decoded to BGR with the source's own
    colour matrix (video/encoding.sampled_frames), piped in as raw pictures
    with no colour tags, and converted back with FFmpeg's default (BT.601),
    untagged. For BT.709 video, the usual, one matrix change does the same
    (measured: SSIM against the old base 0.9865 to 0.9900, against 0.9834 to
    0.9897 by way of BGR, and a second faster); for untagged or BT.601 video
    it is no change at all. Anything else (full range, more than 8 bits,
    another matrix) goes by way of BGR, as the standard render does."""
    from core.binaries import ffprobe

    try:
        done = subprocess.run(
            [ffprobe(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt,color_space,color_range", "-of", "json", str(source)],
            capture_output=True, text=True, timeout=60, creationflags=_flags())
        stream = (json.loads(done.stdout or "{}").get("streams") or [{}])[0]
    except (OSError, ValueError, subprocess.SubprocessError):
        stream = {}
    space = str(stream.get("color_space") or "unknown")
    if stream.get("pix_fmt") != "yuv420p" or str(stream.get("color_range") or "tv") not in ("tv", "unknown"):
        return f"format=bgr24,{UNTAGGED}"
    if space == "bt709":
        return f"colormatrix=bt709:bt601,{UNTAGGED}"
    if space in ("unknown", "bt470bg", "smpte170m"):
        return UNTAGGED
    return f"format=bgr24,{UNTAGGED}"


def filter_graph(layout: Layout, fps: str, first_x: int, script_name: str, vf_extra: str = "",
                 colours: str = UNTAGGED) -> str:
    """The source's window cropped where the script says and laid out as
    video/cropper._render_tracked lays out this layout, with its colours
    (colour_steps) and the clip's colour filters (vf_extra, on the picture
    only, as there)."""
    crop = (f"sendcmd=f={script_name},crop@base=w={layout.crop_w}:h={layout.crop_h}:x={first_x}:y=0:exact=1,"
            f"{colours}")
    colour = f",{vf_extra}" if vf_extra else ""
    if layout.name == "full":
        return f"[0:v]{_source_filters(fps)},{crop},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1{colour}[v]"
    # The 4:5 picture over a wash of itself: _render_tracked's blur of the
    # picture scaled to cover the screen, made from the 135x240 copy it
    # blurs (going through 1080x1920 first changes nothing a 12-pixel blur
    # leaves; SSIM 0.9861 against 0.9866 on the old base).
    return (f"[0:v]{_source_filters(fps)},{crop},split=2[a][b];"
            f"[a]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,gblur=sigma=12,"
            f"scale={OUT_W}:{OUT_H}:flags=bilinear[bg];"
            f"[b]scale={OUT_W}:-2:flags=lanczos{colour}[fg];"
            f"[bg][fg]overlay=0:{layout.top},setsar=1[v]")


def render(source: Path, out: Path, start: float, seconds: float, *, graph: str, workdir: Path,
           normalize: bool = True, progress=None, frames: int = 0) -> Path:
    """One FFmpeg pass of the filter graph over the source's window, with the
    loudness levelling of the standard render, encoded as renders are."""
    from core.binaries import ffmpeg
    from video.encoding import audio_filter_args, final_movflags, video_encoder_args

    cmd = [ffmpeg(), "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
           "-ss", f"{start:.2f}", "-i", str(Path(source).resolve()), "-t", f"{seconds:.2f}",
           "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
           *video_encoder_args(), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", *audio_filter_args(normalize),
           "-fps_mode", "cfr", *final_movflags(), "-shortest", str(Path(out).resolve())]
    proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, creationflags=_flags())
    tail: list[str] = []

    def drain() -> None:
        for line in proc.stderr:
            tail.append(line)
            del tail[:-30]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    for line in proc.stdout:
        if progress is not None and frames and line.startswith("frame="):
            try:
                progress(min(1.0, int(line[6:].strip()) / frames))
            except ValueError:
                pass
    proc.wait()
    reader.join(timeout=10)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg base render failed:\n" + "".join(tail)[-2000:])
    return out


def make(row, opts: dict, config: dict, source: Path, clip_file: Path | None, work_dir: Path,
         progress=None) -> Path | None:
    """The clip's base video from its own framing, or None when the clip is
    not framed by a moving crop or its frames can't be read back (render_base
    then makes it the full way). progress(0..1) as it goes."""
    from kitty_editor.build import probe
    from video.filters import combined_chain

    if clip_file is None or not Path(clip_file).is_file() or not plain_layout(opts, config):
        return None
    src, clip = probe(source), probe(clip_file)
    if not src["video"] or not clip["video"] or (clip["width"], clip["height"]) != (OUT_W, OUT_H):
        return None
    if src["width"] < 16 or src["height"] < 16 or src["width"] * 16 < src["height"] * 9:
        return None                             # narrower than 9:16: no crop moves
    if (src["fps_num"], src["fps_den"]) != (clip["fps_num"], clip["fps_den"]):
        return None
    fps = f"{src['fps_num']}/{src['fps_den']}"
    start, seconds = window(row, opts, config)
    if clip["seconds"] < seconds - 0.05:
        return None
    sample_fps = float((config.get("tracking") or {}).get("sample_fps") or 8.0)
    step = max(1, round(_rate(fps) / sample_fps))
    filter_name = opts.get("filter") or (config.get("clips") or {}).get("filter") or "none"
    vf_extra = combined_chain(filter_name, opts.get("adjust"))

    def part(lo: float, hi: float):
        return (lambda v: progress(lo + (hi - lo) * v)) if progress is not None else None

    seen: list = []
    try:
        # Matched without the clip's colour: the comparison takes no notice of
        # brightness and contrast, and the colour filters (a sharpen, a
        # vignette) would make the source's decoding several times slower.
        layout, keys, frames = read_framing(source, clip_file, start, seconds, src_w=src["width"],
                                            src_h=src["height"], fps=fps, step=step,
                                            progress=part(0.0, 0.25), record=seen)
    except NotPlain as e:
        print(f"      Editor base: the clip's framing can't be read back ({e}); making it in full")
        if work_dir is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "framing.json").write_text(json.dumps({"failed": str(e), "seen": seen}), encoding="utf-8")
        return None
    first_x, script_text = crop_lines(keys, frames, src_w=src["width"], crop_w=layout.crop_w, fps=fps)
    work_dir.mkdir(parents=True, exist_ok=True)
    script = work_dir / "crop.cmd"
    script.write_text(script_text, encoding="utf-8")
    (work_dir / "framing.json").write_text(
        json.dumps({"layout": layout.name, "frames": frames, "keys": keys, "seen": seen}), encoding="utf-8")
    return render(source, work_dir / "base.mp4", start, seconds,
                  graph=filter_graph(layout, fps, first_x, script.name, vf_extra, colour_steps(source)),
                  workdir=work_dir,
                  normalize=bool(opts.get("normalize_audio", True)), progress=part(0.25, 1.0), frames=frames)
