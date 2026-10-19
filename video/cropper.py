"""Vertical 9:16 rendering from a tracking result.

Three modes (see video/tracker.py for how they're chosen):

  track    - OpenCV follows the smoothed crop path frame by frame, then FFmpeg
             scales to 1080x1920, burns captions, and muxes the audio back in.
             With a "zoom" path that widens past 9:16 (basketball framing
             stepping out to hold a pass or the basket), OpenCV composes
             each 1080x1920 frame itself: the wider crop at full width over
             a blurred wash, like fit_blur.
  split    - gameplay + facecam layout: webcam region stacked on top (35%),
             centered gameplay crop below (65%). Both regions static, so this
             renders in ONE pure-FFmpeg pass with no per-frame Python.
  fit_blur - subjects too spread out to crop without cutting someone off: show
             the full width fit into 1080 wide, centered, with the empty top
             and bottom filled by a blurred, zoomed copy of the video.

No-stretch guarantee in every mode: content keeps its aspect ratio and only
ever gets uniformly scaled — distortion is impossible by construction.
"""

import subprocess
import threading
from bisect import bisect_left
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from core.binaries import ffmpeg
from video.capture import video_capture
from video.encoding import audio_filter_args, video_encoder_args

CAM_H = 672    # webcam band height in the 1080x1920 split layout (35%)
GAME_H = 1248  # gameplay band height (65%)

# A zoom path wider than the 9:16 crop by more than this (pixels of the
# source) is composed frame by frame; within it, rounding is all it is, and
# the clip renders exactly as a plain path does.
ZOOM_SLACK_PX = 2
# A widened crop scaled to 1080 wide at least this tall fills the screen
# (trimmed to 1920); shorter, it sits over a blurred wash.
FILL_MIN_H = 1916
# The wash is remade every this many frames: it is a blur of the picture,
# so half the frame rate is indistinguishable, and it is the costly part.
WASH_EVERY = 2


def render_vertical(
    clip_path: Path,
    tracking: dict,
    output_path: Path,
    ass_path: Path | None = None,
    vf_extra: str = "",
    cam_position: str = "top",
    normalize: bool = True,
) -> Path:
    """vf_extra: an optional FFmpeg filter fragment (color preset) applied
    after scaling and before captions, so captions stay unfiltered.
    cam_position: 'top' | 'bottom' — which band the facecam occupies in the
    split (gameplay + webcam) layout. Ignored by the other modes."""
    if tracking["mode"] == "split":
        return _render_split(
            clip_path, tracking["webcam_box"], output_path, ass_path, vf_extra,
            cam_position=cam_position, normalize=normalize,
        )
    if tracking["mode"] == "fit_blur":
        return _render_fit_blur(
            clip_path, tracking.get("region"), output_path, ass_path, vf_extra,
            normalize=normalize,
        )
    return _render_tracked(
        clip_path, tracking["path"], output_path, ass_path, vf_extra,
        face_y=tracking.get("face_y"), normalize=normalize, rows=tracking.get("rows"),
        zoom=tracking.get("zoom"), band_top=tracking.get("band_top"),
        band_bottom=tracking.get("band_bottom"),
    )


def _render_fit_blur(
    clip_path: Path,
    region: tuple[float, float, float, float] | None,
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
    normalize: bool = True,
) -> Path:
    """The subject's bounding region (normalized x0,y0,x1,y1) shown at FULL
    output width on a heavily blurred backdrop — blurred bands land on the top
    and bottom only, never at the sides, and nobody is cropped out. Nothing is
    stretched: the region keeps its own aspect ratio. Pure FFmpeg.
    """
    x0, y0, x1, y1 = (region or (0.0, 0.0, 1.0, 1.0))
    x0 = max(0.0, min(0.85, x0))
    x1 = max(x0 + 0.15, min(1.0, x1))
    y0 = max(0.0, min(0.85, y0))
    y1 = max(y0 + 0.15, min(1.0, y1))
    rw, rh, rx, ry = x1 - x0, y1 - y0, x0, y0
    crop_region = f"crop=iw*{rw:.4f}:ih*{rh:.4f}:iw*{rx:.4f}:ih*{ry:.4f}"

    # Background: the same crop, blown up to COVER 1080x1920, then destroyed:
    # downscaled hard + blurred + upscaled = an unrecognizable color wash.
    bg = (
        f"{crop_region},scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,scale=135:240,gblur=sigma=12,scale=1080:1920:flags=bilinear"
    )
    # Foreground: full width; height follows the region's own aspect ratio
    # (capped at the screen so an unusually tall region can never overflow).
    fg = (
        f"{crop_region},scale=1080:-2:flags=lanczos,crop=w=1080:h=min(ih\\,1920)"
        + (f",{vf_extra}" if vf_extra else "")
    )
    filters = f"[0:v]split=2[a][b];[a]{bg}[bg];[b]{fg}[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1[v]"
    if ass_path is not None:
        filters += f";[v]subtitles={ass_path.name}[v2]"
        vout = "[v2]"
    else:
        vout = "[v]"

    cmd = [
        ffmpeg(), "-y",
        # No -hwaccel here: GPU decode feeding this split/overlay/blur graph can
        # drift the video timing off the audio. CPU decode keeps A/V locked.
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", vout, "-map", "0:a:0?",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        *audio_filter_args(normalize),  # sync + loudness normalise (final encode)
        "-fps_mode", "cfr",          # constant output frame rate
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    return output_path


# ---- follow-the-subject mode -------------------------------------------------


def _render_tracked(
    clip_path: Path,
    crop_path: list[tuple[float, float]],
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
    face_y: float | None = None,
    normalize: bool = True,
    rows: tuple[float, float] | None = None,
    zoom: list[tuple[float, float]] | None = None,
    band_top: float | None = None,
    band_bottom: float | None = None,
) -> Path:
    """rows: the band of the frame's height the crop keeps, (top, bottom) in
    fractions of it; all of it when None. A sport's framing leaves its TV
    scoreboard out this way (sports/basketball/action.py): the crop is then
    that much shorter, and as much narrower, so it stays 9:16.

    zoom: (t, width) keys like the path's, the crop's width as a fraction of
    the source's. Where it is wider than 9:16 the crop keeps its full height,
    is shown at the screen's full width and is centred over a blurred wash
    (_ZoomComposer). A zoom that never gets wider than 9:16, or none, renders
    exactly as before. Ignored with the 4:5 face-safe layout (face_y), which
    no framing that zooms sets.

    band_top: a share of the screen's height kept for a blurred band across
    the top, under the apps' own bar (sports/basketball/action.picture_share).
    The picture fills the rest, full width, so the crop is that much wider
    than 9:16; the band is a blurred copy of it. Takes the place of `zoom`.

    band_bottom: the same across the bottom, under the apps' caption,
    username and music line, so the picture sits between the two bands."""
    with video_capture(clip_path) as cap:

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        y0, y1 = 0, src_h
        if rows is not None:
            y0 = max(0, min(src_h - 2, int(round(rows[0] * src_h))))
            y1 = max(y0 + 2, min(src_h, int(round(rows[1] * src_h))))
            y1 -= (y1 - y0) % 2  # even height required by H.264
        crop_h = y1 - y0

        # Face in the top quarter of a full-height crop would sit under the
        # TikTok/Instagram tab bar. For those clips, crop a WIDER 4:5 window
        # instead: shown at full output width it's shorter than the screen, so it
        # can start below the tab area — blurred bands land on top/bottom ONLY
        # (never at the sides), and the wider view shows more of the scene.
        top_safe = face_y is not None and face_y < 0.25
        band_px = _even(band_top * 1920) if band_top and not top_safe and 0 < band_top < 0.5 else 0
        bottom_px = _even(band_bottom * 1920) if band_bottom and not top_safe and 0 < band_bottom < 0.5 else 0
        if band_px + bottom_px > 1920 * 0.6:      # never leave the picture under 40% of the screen
            band_px = bottom_px = 0
        banded = band_px or bottom_px
        aspect = 4 / 5 if top_safe else 1080 / (1920 - band_px - bottom_px) if banded else 9 / 16
        crop_w = int(crop_h * aspect)
        crop_w -= crop_w % 2  # even width required by H.264
        crop_w = min(crop_w, src_w)
        if banded and crop_w < int(crop_h * aspect) - 2:
            # A source too narrow for the picture between the bands (a 3:4 or
            # 4:5 phone video): it is shown at its own shape, never squashed,
            # by keeping the middle rows that fit, as a full crop trims them.
            keep = _even(crop_w / aspect)
            y0 += (crop_h - keep) // 2
            y1 = y0 + keep
            crop_h = keep

        composer = None
        if banded:
            composer = _ZoomComposer(src_w, crop_w, y0, y1, top=band_px, bottom=bottom_px)
        elif zoom and not top_safe and any(w * src_w > crop_w + ZOOM_SLACK_PX for _, w in zoom):
            composer = _ZoomComposer(src_w, crop_w, y0, y1)

        def produce(write) -> None:
            """Decode, follow the subject, and hand each cropped frame to ffmpeg.

            Frames go straight down the pipe — no intermediate file. Writing an
            mp4v staging file here cost a whole CPU encode and threw away detail
            the NVENC pass then could not get back.

            Decoding happens in FFmpeg, on the GPU, not in cv2. Same frames in the
            same order — sampled_frames(every=1) is every frame — but cv2 has no
            CUDA in any pip wheel and its decoder ignores every thread limit going,
            so it cost 6.2 CORES to decode a 23s 1080p60 clip. Three clips render
            at once, which is why the machine locked up during a job. NVDEC does it
            for 0.4 cores and leaves the CPU for the crop and the pipe.

            cv2 stays as the fallback: if FFmpeg cannot start or the stream ends
            early, this drops back rather than producing a truncated clip.
            """
            from video.encoding import sampled_frames

            written = 0

            def emit(frame, frame_idx: int) -> None:
                t = frame_idx / fps
                if composer is not None:
                    width = _interpolate(zoom, t) if zoom and not banded else crop_w / src_w
                    write(composer.frame(frame, frame_idx, _interpolate(crop_path, t), width))
                    return
                center_x = _interpolate(crop_path, t) * src_w
                x0 = int(round(center_x - crop_w / 2))
                x0 = max(0, min(src_w - crop_w, x0))  # clamp inside the frame
                # Column slices are non-contiguous views; the pipe needs bytes.
                write(np.ascontiguousarray(frame[y0:y1, x0 : x0 + crop_w]).tobytes())

            try:
                for frame_idx, frame in sampled_frames(clip_path, 1, src_w, src_h):
                    emit(frame, frame_idx)
                    written += 1
            except Exception as e:
                print(f"      (GPU decode unavailable, using CPU: {e})")

            if written == 0:
                # Nothing came through — fall back rather than emit an empty clip.
                frame_idx = 0
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    emit(frame, frame_idx)
                    frame_idx += 1

        # Input 0 is the raw cropped video on stdin; input 1 stays the original
        # clip, which is still where the audio comes from.
        pipe_in = [
            "-f", "rawvideo", "-pix_fmt", "bgr24",   # OpenCV hands us BGR
            "-s", "1080x1920" if composer is not None else f"{crop_w}x{crop_h}", "-r", f"{fps}",
            "-i", "pipe:0",
        ]

        # Platform-safe letterbox: the wider 4:5 crop at FULL output width (1080)
        # is shorter than the screen, so it starts just below the tab area with
        # heavily blurred bands above and below it — never at the sides — and the
        # face lands toward the center of the screen. Nothing is stretched.
        if top_safe:
            top = int(0.115 * 1920)            # content starts under the FYP tabs
            fg = "scale=1080:-2:flags=lanczos" + (f",{vf_extra}" if vf_extra else "")
            filters = (
                f"[0:v]split=2[a][b];"
                # Downscale hard + blur + upscale: an unrecognizable color wash.
                f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"scale=135:240,gblur=sigma=12,scale=1080:1920:flags=bilinear[bg];"
                f"[b]{fg}[fg];"
                f"[bg][fg]overlay=0:{top},setsar=1[v]"
            )
            if ass_path is not None:
                filters += f";[v]subtitles={ass_path.name}[v]"
            cmd = [
                ffmpeg(), "-y",
                *pipe_in,                         # cropped frames on stdin
                "-i", str(clip_path.resolve()),   # source of the audio
                "-filter_complex", filters,
                "-map", "[v]", "-map", "1:a:0?",
                *video_encoder_args(),
                # 4:2:0. Feeding NVENC a bgr24 pipe makes it default to gbrp
                # (High 4:4:4), which browsers and many players CANNOT decode —
                # the clip renders but won't play. Force standard yuv420p.
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                *audio_filter_args(normalize),
                "-fps_mode", "cfr",
                "-movflags", "+faststart",
                "-shortest",
                str(output_path.resolve()),
            ]
            _run_ffmpeg_piped(cmd, ass_path, produce)
            return output_path

        # Uniform scale to 1080x1920 + color filter + captions + mux audio.
        vf = "scale=1080:1920:flags=lanczos,setsar=1"
        if composer is not None:
            vf = "setsar=1"   # the composed frames are 1080x1920 already
        if vf_extra:
            vf += f",{vf_extra}"
        if ass_path is not None:
            vf += f",subtitles={ass_path.name}"
        cmd = [
            ffmpeg(), "-y",
            *pipe_in,                         # cropped frames on stdin
            "-i", str(clip_path.resolve()),   # source of the audio
            "-map", "0:v:0", "-map", "1:a:0?",
            "-vf", vf,
            *video_encoder_args(),  # NVENC when available
            "-pix_fmt", "yuv420p",  # 4:2:0 — a bgr24 pipe defaults to gbrp 4:4:4,
                                    # which browsers/players can't decode
            "-c:a", "aac", "-b:a", "128k",
            *audio_filter_args(normalize),             # sync + loudness normalise
            "-fps_mode", "cfr",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path.resolve()),
        ]
        _run_ffmpeg_piped(cmd, ass_path, produce)
        return output_path


def _interpolate(path: list[tuple[float, float]], t: float) -> float:
    """Linear interpolation of center_x at time t over the crop path.

    The path is in time order (every tracker writes it so). A binary search
    finds the pair a linear scan would have, the first with t0 <= t <= t1,
    and the arithmetic is the scan's, so the value is identical to the last
    bit. The scan cost a pass over the path per frame, which a path with a
    key per frame (basketball framing) turns into minutes on a long clip.
    """
    if t <= path[0][0]:
        return path[0][1]
    if t >= path[-1][0]:
        return path[-1][1]
    i = bisect_left(path, t, key=_key_time)   # path[i - 1] < t <= path[i]
    (t0, x0), (t1, x1) = path[i - 1], path[i]
    if t1 == t0:
        return x0
    return x0 + (x1 - x0) * (t - t0) / (t1 - t0)


def _key_time(key) -> float:
    return key[0]


def _even(v: float) -> int:
    return int(round(v / 2.0)) * 2


def _middle_9x16(region):
    """The middle of `region` at the screen's 9:16 shape (cover, not fit):
    the sides trimmed if it is wider, the top and bottom if it is taller."""
    h, w = region.shape[:2]
    cw = max(1, min(w, int(round(h * 9 / 16))))
    ch = max(1, min(h, int(round(w * 16 / 9))))
    x, y = (w - cw) // 2, (h - ch) // 2
    return region[y:y + ch, x:x + cw]


def _resized(img, size: tuple[int, int]):
    """`img` at size (w, h): area averaging to shrink, which keeps detail
    without aliasing; bicubic to enlarge."""
    shrink = size[0] < img.shape[1]
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)


def _wash(region):
    """fit_blur's background, made in OpenCV: the region's middle covering
    1080x1920, shrunk to 135x240, blurred (sigma 12) and blown back up, which
    leaves only its colours.

    Measured on 1080p broadcast frames, the cheap choices here change the
    wash by at most 4 levels of 255 and not its flicker: a plain bilinear
    shrink (the blur that follows averages far more than area averaging
    would) and the same Gaussian as a separable filter, which OpenCV runs
    five times faster than GaussianBlur's 73-tap 8-bit path (0.8 ms against
    4.3)."""
    small = cv2.resize(_middle_9x16(region), (135, 240), interpolation=cv2.INTER_LINEAR)
    kernel = _wash_kernel()
    small = cv2.sepFilter2D(small, -1, kernel, kernel)
    return cv2.resize(small, (1080, 1920), interpolation=cv2.INTER_LINEAR)


@cache
def _wash_kernel():
    """GaussianBlur's own kernel for sigma 12 on 8-bit pictures (73 taps)."""
    return cv2.getGaussianKernel(73, 12, cv2.CV_32F)


class _ZoomComposer:
    """Builds each 1080x1920 frame of a crop whose width changes.

    At 9:16 the crop fills the screen, as a plain path does. Wider, it keeps
    its full height, is scaled to the screen's full width (shorter than the
    screen, never stretched) and is centred over a blurred copy of itself,
    so the blurred bands land above and below the picture, never at its
    sides: the same look as fit_blur and the face-safe 4:5 layout.

    With `top` and `bottom` (pixels), the picture always sits that far from
    the top and the bottom, filling what is between, and only the bands
    outside it are blurred: a fixed layout that keeps the picture under the
    apps' top bar and above their caption.
    """

    def __init__(self, src_w: int, base_w: int, y0: int, y1: int, top: int | None = None,
                 bottom: int | None = None):
        self.src_w, self.base_w = src_w, base_w
        self.y0, self.y1 = y0, y1
        self.top = top or 0
        self.bottom = bottom or 0
        self.crop_h = y1 - y0
        self.wash = None
        self.wash_frame = 0

    def frame(self, frame, frame_idx: int, center: float, width: float) -> memoryview:
        """The screen for one source frame: the crop centred on `center`,
        `width` wide (fractions of the source's width), clamped inside it.

        Handed back as a view of a new array, not bytes: the pipe takes it
        as it is, and copying 6 MB a frame to make bytes cost 1.3 ms."""
        cw = min(self.src_w, max(self.base_w, _even(width * self.src_w)))
        x0 = int(round(center * self.src_w - cw / 2))
        x0 = max(0, min(self.src_w - cw, x0))   # clamp inside the frame
        region = frame[self.y0:self.y1, x0:x0 + cw]
        if self.top or self.bottom:
            stale = frame_idx - self.wash_frame
            if self.wash is None or not 0 <= stale < WASH_EVERY:
                self.wash, self.wash_frame = _wash(region), frame_idx
            end = 1920 - self.bottom
            out = np.empty((1920, 1080, 3), np.uint8)
            out[:self.top] = self.wash[:self.top]
            out[self.top:end] = _resized(region, (1080, end - self.top))
            out[end:] = self.wash[end:]
            return memoryview(out).cast("B")
        fh = _even(1080 * self.crop_h / cw)
        if fh >= FILL_MIN_H:
            # Tall enough to fill: trim the few rows or columns over 9:16
            # rather than stretch, and scale straight to the screen.
            return memoryview(_resized(_middle_9x16(region), (1080, 1920))).cast("B")
        stale = frame_idx - self.wash_frame
        if self.wash is None or not 0 <= stale < WASH_EVERY:
            self.wash, self.wash_frame = _wash(region), frame_idx
        top = (1920 - fh) // 2
        out = np.empty((1920, 1080, 3), np.uint8)
        out[:top] = self.wash[:top]
        out[top:top + fh] = _resized(region, (1080, fh))
        out[top + fh:] = self.wash[top + fh:]
        return memoryview(out).cast("B")


# ---- gameplay + facecam split mode --------------------------------------------


def _render_split(
    clip_path: Path,
    webcam_box: tuple[float, float, float, float],
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
    cam_position: str = "top",
    normalize: bool = True,
) -> Path:
    with video_capture(clip_path) as cap:
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    bx, by, bw, bh = webcam_box
    cam_x, cam_y = int(bx * src_w), int(by * src_h)
    cam_w, cam_h = int(bw * src_w), int(bh * src_h)
    cam_w -= cam_w % 2
    cam_h -= cam_h % 2

    # Gameplay band: center crop at exactly the band's aspect (1080:1248).
    game_w = int(src_h * 1080 / GAME_H)
    game_w -= game_w % 2
    game_w = min(game_w, src_w)
    game_x = (src_w - game_w) // 2

    # Webcam band: crop the overlay region, fill the 1080x672 band
    # (uniform scale up, then trim overflow — never stretch). The vstack
    # order decides which band is on top — user-switchable per clip.
    stack = "[game][cam]" if cam_position == "bottom" else "[cam][game]"
    filters = (
        f"[0:v]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},"
        f"scale=1080:{CAM_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1080:{CAM_H},setsar=1[cam];"
        f"[0:v]crop={game_w}:{src_h}:{game_x}:0,"
        f"scale=1080:{GAME_H}:flags=lanczos,setsar=1[game];"
        f"{stack}vstack=inputs=2[v]"
    )
    if vf_extra:
        filters += f";[v]{vf_extra}[v]"
    if ass_path is not None:
        filters += f";[v]subtitles={ass_path.name}[v]"

    cmd = [
        ffmpeg(), "-y",
        # CPU decode for the filter graph — keeps A/V locked (see _render_fit_blur).
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", "[v]", "-map", "0:a:0?",
        *video_encoder_args(),  # NVENC when available
        "-c:a", "aac", "-b:a", "128k",
        *audio_filter_args(normalize),
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    return output_path


def _run_ffmpeg(cmd: list[str], ass_path: Path | None) -> None:
    # cwd is the ass file's folder: the subtitles filter gets a bare filename,
    # avoiding fragile Windows path escaping inside filter args.
    workdir = ass_path.parent if ass_path is not None else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed:\n{result.stderr[-2000:]}")


def _run_ffmpeg_piped(cmd: list[str], ass_path: Path | None, produce) -> None:
    """Run ffmpeg with frames fed to its stdin by `produce(write)`.

    The tracked crop used to stage its frames in an mp4v file written by
    OpenCV, which ffmpeg then re-decoded and re-encoded — two encodes, the
    first on the CPU and lossy, so the real encode started from degraded
    frames. Feeding raw frames straight in removes that pass entirely.
    """
    workdir = ass_path.parent if ass_path is not None else None
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=workdir
    )
    assert proc.stdin is not None and proc.stderr is not None

    # ffmpeg chatters on stderr while it encodes. If nobody empties that pipe
    # it fills, ffmpeg blocks writing to it, and we block writing frames to
    # stdin — a deadlock that turned a 45-second render into fifteen minutes.
    # subprocess.run() drains both for you; feeding stdin ourselves means we
    # have to. A reader thread does it.
    tail: list[bytes] = []

    def drain() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line)
            del tail[:-40]  # keep only the last lines, for the error message

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        produce(proc.stdin.write)
    except (BrokenPipeError, OSError):
        pass  # ffmpeg died early; its stderr below is the real error
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # ffmpeg already exited and closed its end
    proc.wait()
    reader.join(timeout=10)
    if proc.returncode != 0:
        msg = b"".join(tail).decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"ffmpeg render failed:\n{msg}")
