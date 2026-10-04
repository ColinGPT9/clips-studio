"""Finding and reading a broadcast's score bug: what every sport's scoreboard
shares. Each sport says what its bug's text means (its own `parse`, with the
score and the clock); this finds the box on a few frames and reads it from
every keyframe of the video.

Cost, measured on a 1 h 42 min 1080p soccer final:
- Finding the box needs OCR's text search on whole bands of a few frames:
  about 1.5 s a band, so it stops as soon as five frames agree.
- Reading it then needs no search at all. One ffmpeg pass decodes only the
  keyframes (every few seconds) cropped to the box: 1,510 crops in 19 s.
  Each is read as a single line by the recogniser alone: about 16 ms, against
  a second for a full OCR.
It uses the OCR the app already ships for games (RapidOCR, analysis/game_text.py).

A bug that isn't one tight line (basketball's: two rows, logos between a code
and its score) is found as a block of text around its clock (`cluster`) and
read piece by piece with the full OCR (`pieces`): slower than the recogniser
alone, but the recogniser runs a two-row box together into one string of
digits.
"""

import re
import subprocess
import threading

# ffmpeg's showinfo line for each frame it passes on, with the frame's time.
PTS_TIME = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?)")

FIND_FRAMES = 14         # most frames looked at, spread through the match, to find the box
FOUND_AFTER = 5          # ...stopping once this many agree
FOUND_IN = 3             # the fewest frames the box must be seen in
BANDS = ((0.0, 0.0, 1.0, 0.22), (0.0, 0.78, 1.0, 1.0))   # (left, top, right, bottom): top, bottom
READ_WIDTH = 480         # a band is resized to this width for the text search
SAME_RUN = 0.04          # a gap this wide (share of the band's width) still joins text into one run
CLOCK_GAP = 8.0          # ...and the clock joins the score from up to this many text heights away
MIN_CONFIDENCE = 0.5

_rec_engine = None


def texts(img, ocr) -> list[tuple[tuple, str]]:
    """(box in the image's fractions, text) for each line the full OCR finds."""
    import cv2

    h, w = img.shape[:2]
    scale = READ_WIDTH / max(w, 1)
    img = cv2.resize(img, (READ_WIDTH, max(2, round(h * scale))), interpolation=cv2.INTER_LINEAR)
    height, width = img.shape[:2]
    out = []
    for box, text, conf in ocr(img) or []:
        if float(conf) < MIN_CONFIDENCE:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append(((min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height), str(text)))
    return out


def crop(img, box):
    h, w = img.shape[:2]
    left, top, right, bottom = box
    return img[int(h * top):max(int(h * top) + 2, int(h * bottom)),
               int(w * left):max(int(w * left) + 2, int(w * right))]


def score_lines(lines: list[tuple[tuple, str]], aspect: float, parse, clock) -> list[tuple[tuple, str]]:
    """The bug's own lines among everything read in a band: the run of text
    on one row, with no wide gap in it, that `parse` reads a score in. A band
    holds more than the bug (ad boards, a stadium's banners), and far more of
    it in a portrait frame, where the same share of the height is a tall
    strip of stadium. `aspect`: the band's height over its width, to measure a
    gap against the text's height. `clock`: the sport's clock pattern, whose
    nearest run on the row joins the score. [] when no run reads as a score."""
    rows: list[dict] = []
    for box, text in sorted(lines, key=lambda x: (x[0][1] + x[0][3]) / 2):
        mid = (box[1] + box[3]) / 2
        row = next((r for r in rows if r["top"] <= mid <= r["bottom"]), None)
        if row is None:
            rows.append({"top": box[1], "bottom": box[3], "lines": [(box, text)]})
        else:
            row["lines"].append((box, text))
            row["top"], row["bottom"] = min(row["top"], box[1]), max(row["bottom"], box[3])
    for row in rows:
        # The row's height in the width's units.
        height = max(row["bottom"] - row["top"], 1e-6) * aspect
        runs: list[list] = []
        for box, text in sorted(row["lines"], key=lambda x: x[0][0]):
            # A gap wider than a few characters starts another run: the bug
            # is one tight line, anything else on its row stands apart.
            if runs and box[0] - runs[-1][-1][0][2] <= max(3 * height, SAME_RUN):
                runs[-1].append((box, text))
            else:
                runs.append([(box, text)])
        for i, run in enumerate(runs):
            if parse([" ".join(t for _, t in run)]).score is not None:
                # The clock often stands a little apart in the box
                # ("AWO     15:07"): the nearest run on the row that is a clock
                # belongs to it.
                clocks = [other for other in runs[:i] + runs[i + 1:]
                          if clock.search(" ".join(t for _, t in other))
                          and gap(run, other) <= CLOCK_GAP * height]
                if clocks:
                    run = sorted(run + min(clocks, key=lambda other: gap(run, other)),
                                 key=lambda x: x[0][0])
                return run
    return []


def gap(a: list, b: list) -> float:
    """The horizontal space between two runs of text boxes."""
    return max(0.0, max(min(x[0][0] for x in b) - max(x[0][2] for x in a),
                        min(x[0][0] for x in a) - max(x[0][2] for x in b)))


def find_box(grab, duration: float, ocr, parse, clock, frames: int = FIND_FRAMES) -> tuple | None:
    """Where the score bug is: the run of text in the top or bottom band
    where `parse` reads a score on the sampled frames. None when fewer than
    FOUND_IN show one."""
    seen: list[tuple] = []
    for i in range(frames):
        img = grab(duration * (i + 1) / (frames + 1))
        if img is None:
            continue
        for band in BANDS:
            strip = crop(img, band)
            lines = score_lines(texts(strip, ocr), strip.shape[0] / max(strip.shape[1], 1), parse, clock)
            if not lines:
                continue
            # The bug, in whole-frame fractions.
            bl, bt, br, bb = band
            boxes = [(bl + x0 * (br - bl), bt + y0 * (bb - bt), bl + x1 * (br - bl), bt + y1 * (bb - bt))
                     for (x0, y0, x1, y1), _ in lines]
            seen.append((min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes)))
            break
        if len(seen) >= FOUND_AFTER:
            break
    if len(seen) < FOUND_IN:
        return None
    # The box most frames agree on: sorted by position, the middle one. A
    # one-off graphic (a scorer's full-width caption) sorts to an end.
    seen.sort(key=lambda b: ((b[1] + b[3]) / 2, (b[0] + b[2]) / 2))
    left, top, right, bottom = seen[len(seen) // 2]
    pad_x, pad_y = (right - left) * 0.08, (bottom - top) * 0.25
    return (max(0.0, left - pad_x), max(0.0, top - pad_y), min(1.0, right + pad_x), min(1.0, bottom + pad_y))


def cluster(lines: list[tuple[tuple, str]], seed: tuple, aspect: float, across: float = 4.0,
            down: float = 1.2) -> list[tuple[tuple, str]]:
    """The lines joined to `seed` (one of them) by a chain of near
    neighbours: a gap of at most `across` text heights to the side, and
    `down` heights above or below. A bug is one tight block of text, even
    with two rows or a team's logo between a code and its score; ad boards
    and banners stand further off. `aspect`: the image's height over its
    width, to measure gaps in one unit."""
    def height(box):
        return max(box[3] - box[1], 1e-6) * aspect

    def near(a, b) -> bool:
        h = max(height(a), height(b))
        dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
        dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3])) * aspect
        return dx <= across * h and dy <= down * h

    group = [seed]
    rest = [x for x in lines if x is not seed]
    grew = True
    while grew:
        grew = False
        for x in list(rest):
            if any(near(x[0], g[0]) for g in group):
                group.append(x)
                rest.remove(x)
                grew = True
    return group


def pieces(img, ocr) -> list[tuple[tuple, str]]:
    """(box in the image's fractions, text) for each piece of text the full
    OCR finds in a small image (a bug's box): the text search and the
    recogniser, so a two-row bug or a score set apart from its team's logo
    comes back as separate pieces, not one run-together line. Enlarged first
    when small, as rec_line does."""
    import cv2

    h, w = img.shape[:2]
    if h < 64:
        scale = 64 / max(h, 1)
        img = cv2.resize(img, (max(2, round(w * scale)), 64), interpolation=cv2.INTER_CUBIC)
    height, width = img.shape[:2]
    out = []
    for box, text, conf in ocr(img) or []:
        if float(conf) < MIN_CONFIDENCE:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append(((min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height), str(text)))
    return out


def rec_line(img) -> str:
    """The box read as one line by the recogniser alone (no text search)."""
    global _rec_engine
    import cv2

    if _rec_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _rec_engine = RapidOCR()
    h, w = img.shape[:2]
    if h < 64:
        img = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    result, _elapsed = _rec_engine(img, use_det=False, use_cls=False, use_rec=True)
    if not result:
        return ""
    first = result[0]
    return str(first[0] if isinstance(first, (list, tuple)) else first)


def keyframe_crops(path, box: tuple, size: tuple[int, int], on_frame, cancel=None,
                   scale_width: int | None = None) -> list[float]:
    """Decode only the keyframes, cropped to `box` (and scaled to
    `scale_width` when given), calling on_frame(index, image) for each;
    returns each frame's time. One pass over the file."""
    import numpy as np

    from core.binaries import ffmpeg

    width, height = size
    x, y = int(width * box[0]) // 2 * 2, int(height * box[1]) // 2 * 2
    w = max(2, int(width * (box[2] - box[0])) // 2 * 2)
    h = max(2, int(height * (box[3] - box[1])) // 2 * 2)
    filters = f"crop={w}:{h}:{x}:{y}"
    if scale_width:
        out_w = max(2, int(scale_width) // 2 * 2)
        h = max(2, round(h * out_w / w) // 2 * 2)
        w = out_w
        filters += f",scale={w}:{h}"
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "info", "-skip_frame", "nokey", "-i", str(path), "-an",
           "-vf", f"{filters},showinfo", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    times: list[float] = []

    def drain(stream) -> None:
        for raw in stream:
            found = PTS_TIME.search(raw.decode("utf-8", "ignore"))
            if found:
                times.append(float(found.group(1)))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reader = threading.Thread(target=drain, args=(proc.stderr,), daemon=True)
    reader.start()
    frame_bytes = w * h * 3
    index = 0
    try:
        while True:
            if cancel is not None:
                cancel()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            on_frame(index, np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3))
            index += 1
    finally:
        proc.stdout.close()
        proc.wait()
        reader.join(timeout=10)
    return times
