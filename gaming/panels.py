"""The stream's solid panels: a chat box or a splits timer on its own
background, which the game crop keeps out.

A game stream's layout often puts panels round or over the game: chat, a
speedrun's splits, a subscriber list. When one sits on a solid background (a
black chat bar under the game), cropping it into the Short gives a useless
bar. Chat drawn see-through over the gameplay is part of the stream picture
and is left alone.

A panel is found from frames spread through the video, never from motion:

- stacked lines of text (four or more) in the same place on most frames. The
  game's own text comes and goes between frames minutes apart; a panel stays;
- on the same background colour on every frame. Behind see-through chat the
  game changes, so it isn't counted;
- grown out over that background to the panel's own box (the chat text rarely
  reaches the end of its bar), one side at a time so it can't run round a
  corner into the rest of the frame;
- near an edge of the frame, where stream layouts put them.

OpenCV only: no model, a fraction of a second for five frames.
"""

from pathlib import Path

import cv2
import numpy as np

WIDTH = 960               # frames are looked at this wide
LINE_H = (5, 16)          # a line of text's height, px at WIDTH
MIN_LINES = 4             # stacked lines make a panel
BG_TOL = 12               # a background pixel is within this of the panel's colour
BG_STEADY = 8             # the panel's colour moves at most this much between frames (a dark game: 11+)
FILL = 0.9                # a side grows while this much of the next row / column is background
EDGE = 0.08               # a panel reaches within this of an edge of the frame
MAX_AREA = 0.35           # grown past this much of the frame, it was a dark game, not a panel


def _small(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    return cv2.resize(frame, (WIDTH, max(2, round(h * WIDTH / w))), interpolation=cv2.INTER_AREA)


def _text_lines(img: np.ndarray) -> list:
    """Boxes (x, y, w, h) shaped like a line of text."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _t, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # A panel's own straight edges would join its text into one shape: long
    # vertical and horizontal runs are taken out first.
    edges = cv2.bitwise_or(
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3 * LINE_H[1]))),
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (60, 1))))
    bw = cv2.morphologyEx(cv2.subtract(bw, edges), cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(bw, connectivity=8)
    lines = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if LINE_H[0] <= h <= LINE_H[1]:
            if w >= 2.5 * h and area >= 0.35 * w * h:
                lines.append((x, y, w, h))
        elif LINE_H[1] < h <= 12 * LINE_H[1] and w >= 6 * LINE_H[0]:
            lines += _split_rows(bw[y:y + h, x:x + w] > 0, x, y)
    return lines


def _split_rows(ink: np.ndarray, x: int, y: int) -> list:
    """Lines of text set close enough to touch, told apart by the empty rows
    between them."""
    rows = ink.mean(axis=1) > 0.04
    out, start = [], None
    for r, on in enumerate([*rows, False]):
        if on and start is None:
            start = r
        elif not on and start is not None:
            if LINE_H[0] <= r - start <= LINE_H[1]:
                cols = np.flatnonzero(ink[start:r].any(axis=0))
                w = int(cols[-1] - cols[0] + 1)
                if w >= 2.5 * (r - start):
                    out.append((x + int(cols[0]), y + start, w, r - start))
            start = None
    return out


def _blocks(img: np.ndarray) -> list:
    """Lines merged with their neighbours above and below, with at least
    MIN_LINES lines: (x, y, w, h)."""
    h_img, w_img = img.shape[:2]
    lines = _text_lines(img)
    mask = np.zeros((h_img, w_img), np.uint8)
    for x, y, w, h in lines:
        mask[y:y + h, x:x + w] = 255
    merged = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (17, 13)))
    n, lab, stats, _c = cv2.connectedComponentsWithStats(merged, connectivity=8)
    counts = np.zeros(n, int)
    for x, y, w, h in lines:
        counts[lab[y + h // 2, x + w // 2]] += 1
    out = []
    for i in range(1, n):
        if counts[i] >= MIN_LINES:
            x, y, w, h, _a = (int(v) for v in stats[i])
            out.append((x + 8, y + 6, max(1, w - 16), max(1, h - 12)))    # the dilation undone
    return out


def _steady_text(frames: list) -> list:
    """Text blocks in the same place on most frames: (x, y, w, h), each at its
    largest over the frames."""
    h_img, w_img = frames[0].shape[:2]
    per = [_blocks(f) for f in frames]
    votes = np.zeros((h_img, w_img), np.uint8)
    for blocks in per:
        m = np.zeros((h_img, w_img), np.uint8)
        for x, y, w, h in blocks:
            m[y:y + h, x:x + w] = 1
        votes += m
    need = max(3, len(frames) - 2)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats((votes >= need).astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 0.004 * w_img * h_img:
            continue
        x0, y0, x1, y1 = x, y, x + w, y + h
        for blocks in per:
            for bx, by, bw, bh in blocks:
                if bx < x + w and bx + bw > x and by < y + h and by + bh > y:
                    x0, y0, x1, y1 = min(x0, bx), min(y0, by), max(x1, bx + bw), max(y1, by + bh)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def _solid_colour(frames: list, box: tuple) -> np.ndarray | None:
    """The panel's background colour when it's the same on every frame (the
    median of its flat pixels), None when the game shows through it."""
    x, y, w, h = box
    colours = []
    for f in frames:
        crop = f[y:y + h, x:x + w]
        grad = cv2.morphologyEx(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        flat = grad < 12
        if flat.sum() >= 0.2 * w * h:
            colours.append(np.median(crop[flat], axis=0))
    if len(colours) < max(3, len(frames) - 2):
        return None
    colours = np.array(colours)
    if (colours.max(axis=0) - colours.min(axis=0)).max() > BG_STEADY:
        return None
    return np.median(colours, axis=0)


def _grown(frames: list, box: tuple, colour: np.ndarray) -> tuple:
    """The text's box grown out over the panel's colour, one side at a time."""
    h_img, w_img = frames[0].shape[:2]
    votes = np.zeros((h_img, w_img), np.uint8)
    for f in frames:
        diff = np.abs(f.astype(np.int16) - colour.astype(np.int16)).max(axis=2)
        votes += (diff <= BG_TOL).astype(np.uint8)
    bg = (votes >= len(frames) - 1).astype(np.uint8) * 255
    x0, y0 = box[0], box[1]
    x1, y1 = box[0] + box[2], box[1] + box[3]
    bg[y0:y1, x0:x1] = 255
    bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))) > 0
    grew = True
    while grew:
        grew = False
        if x1 < w_img and bg[y0:y1, x1].mean() >= FILL:
            x1, grew = x1 + 1, True
        if x0 > 0 and bg[y0:y1, x0 - 1].mean() >= FILL:
            x0, grew = x0 - 1, True
        if y1 < h_img and bg[y1, x0:x1].mean() >= FILL:
            y1, grew = y1 + 1, True
        if y0 > 0 and bg[y0 - 1, x0:x1].mean() >= FILL:
            y0, grew = y0 - 1, True
    if (x1 - x0) * (y1 - y0) > MAX_AREA * w_img * h_img:
        return box
    return (x0, y0, x1 - x0, y1 - y0)


def solid_panels(frames: list) -> list:
    """The solid panels on these frames (BGR images of one video, spread
    through it), normalized (x, y, w, h). Empty with fewer than three frames."""
    frames = [_small(f) for f in frames if f is not None]
    if len(frames) < 3 or any(f.shape != frames[0].shape for f in frames):
        return []
    h_img, w_img = frames[0].shape[:2]
    out = []
    for box in _steady_text(frames):
        colour = _solid_colour(frames, box)
        if colour is None:
            continue
        x, y, w, h = _grown(frames, box, colour)
        if not (x <= EDGE * w_img or y <= EDGE * h_img or x + w >= (1 - EDGE) * w_img
                or y + h >= (1 - EDGE) * h_img):
            continue
        out.append([round(x / w_img, 4), round(y / h_img, 4), round(w / w_img, 4), round(h / h_img, 4)])
    return out


def panels_in_images(paths: list) -> list:
    return solid_panels([cv2.imread(str(p)) for p in paths])


def panels_in_video(path: Path, n: int = 5, span: tuple = (0.1, 0.9)) -> list:
    """Solid panels from n frames spread over a video file (a source or a clip)."""
    from video.capture import video_capture

    frames = []
    with video_capture(path, required=False) as cap:
        if cap is None:
            return []
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        for i in range(n):
            at = span[0] + (span[1] - span[0]) * i / max(1, n - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(count * at))
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
    return solid_panels(frames)
