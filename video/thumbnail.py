"""Thumbnail candidates, generated on this machine.

The picker already offers three frames, at a quarter, half and three quarters
of the clip. That is what YouTube Studio does and it is often not enough: the
frame at the halfway mark is as likely to be a blink or a turned head as a
face. This generates candidates instead — frames where somebody is actually
facing the camera, cropped to 16:9 around them, with the clip's own hook line
across the bottom.

No cloud call and no API key. Faces come from the Haar cascades already
bundled for the tracker, frames from the bundled FFmpeg's decoder through
OpenCV, and the type from a font already on the machine.

**Every step degrades rather than fails**, because a thumbnail is a
convenience and the existing picker is right there:

  * no cascade on disk -> centre crop, no face search
  * no usable font -> the image without text, which is still a thumbnail
  * a clip that yields no readable frame -> an empty list, and the caller
    shows what it showed before

The pure geometry and text fitting live in functions that touch neither
OpenCV nor Pillow, so they are tested on a CI runner that has neither.
"""

from pathlib import Path

# YouTube's own recommendation, and what Studio displays: 1280x720, 16:9.
WIDTH, HEIGHT = 1280, 720
RATIO = WIDTH / HEIGHT
MAX_BYTES = 2 * 1024 * 1024   # YouTube's limit, enforced in publish/images.py too
MAX_LINE = 26                 # characters per line of burned text
MAX_LINES = 2

# Bold faces worth trying, in order, ending with whatever Pillow can always
# give us. Unlike video/outro.py this must never raise: the end card carries
# the product's name and has to be in the right face, a thumbnail does not.
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def candidate_times(duration: float, count: int = 6) -> list[float]:
    """Where to look for a good frame.

    The first and last tenth are skipped: a clip starts on a sentence boundary,
    which is often mid-turn, and ends on the end card, which is not a
    thumbnail of anybody's video.
    """
    if duration <= 0 or count <= 0:
        return []
    first, last = duration * 0.1, duration * 0.9
    if last <= first:
        return [duration / 2]
    if count == 1:
        return [(first + last) / 2]
    step = (last - first) / (count - 1)
    return [round(first + step * i, 3) for i in range(count)]


def crop_box(
    width: int, height: int, face: tuple[int, int, int, int] | None
) -> tuple[int, int, int, int]:
    """The widest 16:9 box that fits, centred on the face when there is one.

    Returned as (left, top, right, bottom) in pixels, always inside the frame.
    A vertical clip is the normal input here, so the box is usually the full
    width and the interesting question is only how high up it sits: put the
    face a little above centre, the way a person frames a photograph.
    """
    if width <= 0 or height <= 0:
        return (0, 0, max(width, 1), max(height, 1))

    box_w, box_h = width, round(width / RATIO)
    if box_h > height:                      # a wide source: limit by height
        box_h, box_w = height, round(height * RATIO)

    if face:
        fx, fy, fw, fh = face
        cx, cy = fx + fw / 2, fy + fh / 2
        # Faces sit above the middle of a good thumbnail, not dead centre.
        top = round(cy - box_h * 0.42)
        left = round(cx - box_w / 2)
    else:
        left = round((width - box_w) / 2)
        top = round((height - box_h) / 2)

    left = max(0, min(left, width - box_w))
    top = max(0, min(top, height - box_h))
    return (left, top, left + box_w, top + box_h)


def wrap_title(text: str, max_line: int = MAX_LINE, max_lines: int = MAX_LINES) -> list[str]:
    """The hook, as the one or two short lines a thumbnail can carry.

    Long hooks are cut at a word with an ellipsis rather than wrapped into a
    paragraph: a thumbnail is read at a glance and at thumbnail size, so a
    third line is unreadable text sitting on top of the picture.
    """
    words = " ".join((text or "").split()).split(" ")
    if not words or words == [""]:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    # Something was left over: mark the cut so it does not read as the whole
    # sentence ending oddly.
    consumed = len(" ".join(lines).split(" "))
    if consumed < len(words):
        last = lines[-1]
        lines[-1] = (last[: max_line - 1].rstrip() + "…") if len(last) >= max_line else last + "…"
    return lines


def _font(size: int):
    """A bold face at this size, or Pillow's built-in as a last resort."""
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _faces(frame):
    """Face boxes in a frame, biggest first, or [] when detection is not
    available. Never raises: see core/binaries.haar_cascade for why a missing
    cascade is a normal condition rather than a fault."""
    import cv2

    from core.binaries import haar_cascade

    path = haar_cascade("haarcascade_frontalface_default.xml")
    if not path:
        return []
    try:
        classifier = cv2.CascadeClassifier(path)
        if classifier.empty():
            return []
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = classifier.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=6,
                                            minSize=(60, 60))
    except cv2.error:
        return []
    return sorted(([int(v) for v in f] for f in found), key=lambda f: f[2] * f[3], reverse=True)


def _sharpness(frame) -> float:
    """How much detail a frame has. A motion-blurred or near-black frame is a
    bad thumbnail however well it is cropped."""
    import cv2

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def score_frame(sharpness: float, face_area_fraction: float, brightness: float) -> float:
    """How good a thumbnail this frame would make.

    A visible face dominates, because a face is what gets clicked; detail and
    a sane exposure break the ties. Pure arithmetic so the weighting is
    testable without decoding a video.
    """
    if brightness < 18 or brightness > 242:   # near black or blown out
        return 0.0
    return (face_area_fraction * 1000.0) + min(sharpness, 500.0) / 10.0


def generate(video_path: Path, hook: str, targets: list[Path]) -> list[Path]:
    """Write up to len(targets) thumbnail candidates. Returns what was written.

    Best candidate first. An empty list means nothing usable came out, which
    is a normal outcome and not an error.
    """
    # The cheap answers come first, and deliberately before the imports: asking
    # for no candidates, or naming a file that is not there, is answerable
    # without OpenCV, and a machine without it should still get the honest
    # empty list rather than an ImportError. CI is exactly that machine.
    if not targets or not Path(video_path).exists():
        return []

    import cv2
    from PIL import Image, ImageDraw

    from video.capture import video_capture

    scored: list[tuple[float, object, tuple | None]] = []
    with video_capture(Path(video_path)) as cap:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = frames / fps if fps else 0.0
        for at in candidate_times(duration, count=max(6, len(targets) * 2)):
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            faces = _faces(frame)
            face = faces[0] if faces else None
            area = (face[2] * face[3]) / float(width * height) if face else 0.0
            brightness = float(frame.mean())
            scored.append((score_frame(_sharpness(frame), area, brightness), frame.copy(), face))

    if not scored:
        return []
    scored.sort(key=lambda row: row[0], reverse=True)

    lines = wrap_title(hook)
    written: list[Path] = []
    for (_score, frame, face), target in zip(scored, targets):
        left, top, right, bottom = crop_box(frame.shape[1], frame.shape[0], face)
        cropped = frame[top:bottom, left:right]
        image = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)).resize(
            (WIDTH, HEIGHT), Image.LANCZOS
        )
        if lines:
            _draw_title(ImageDraw.Draw(image, "RGBA"), lines)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Quality stepped down only if needed: YouTube refuses over 2 MB, and a
        # 1280x720 JPEG is nowhere near it until the picture is very noisy.
        for quality in (88, 75, 60):
            image.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= MAX_BYTES:
                break
        written.append(target)
    return written


def _draw_title(draw, lines: list[str]) -> None:
    """The hook across the bottom, on a band dark enough to read over
    anything. Thumbnails are viewed at a fraction of full size, so the type is
    large and the contrast is not subtle."""
    size = 68 if len(lines) == 1 else 58
    font = _font(size)
    if font is None:
        return
    gap = 12
    heights = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        heights.append(box[3] - box[1])
    block = sum(heights) + gap * (len(lines) - 1)
    band_top = HEIGHT - block - 64
    draw.rectangle([(0, band_top - 26), (WIDTH, HEIGHT)], fill=(0, 0, 0, 140))

    y = band_top
    for line, line_height in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        x = (WIDTH - (box[2] - box[0])) / 2
        draw.text((x, y - box[1]), line, font=font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
        y += line_height + gap
