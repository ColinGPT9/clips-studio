"""What the game writes on screen, for the gaming profile.

Games announce their moments in big letters: ELIMINATED, VICTORY ROYALE,
PENTA KILL, "X SCORED", YOU DIED, ENEMY FELLED. They also fill the screen
with text when nothing is happening: a settings page, a queue, a loading
screen, which chat will happily react to. Reading the screen tells the two
apart where the transcript, chat and the game's sound can't.

OCR (RapidOCR: PaddleOCR's models on onnxruntime, Apache-2.0) costs about a
second a frame on a CPU, so it only reads the game-moment windows, a frame
every few seconds, strongest first, within a time budget. It reads Latin
script and kanji (not kana), and config/gaming.yaml's screen_text lists
each word in the languages games are commonly played in.

  - A banner (big text near the middle) with an event word becomes an
    "ON SCREEN:" event for the model and a witness that something happened.
  - A window where most frames show menu words (a few different ones across
    it) is marked as a menu, and scores lower.
"""

import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Seconds between the frames read in a window: closer together over its
# first seconds, where the moment is (a window starts 4 s before it), since
# a banner is only up for two or three; further apart after.
SAMPLE_EVERY = 4.0
SAMPLE_CLOSE = 2.0
CLOSE_FOR = 12.0
MAX_FRAMES = 160
TIME_BUDGET = 120.0    # seconds of reading per video, at most
# The middle of the frame, read at this width: banners are there, and a
# stylised one is only legible this large (read whole at 960 wide, a Rocket
# League goal banner wasn't read at all).
CENTRE = (0.12, 0.12, 0.88, 0.82)   # left, top, right, bottom as shares of the frame
WIDTH = 1280
BANNER_HEIGHT = 0.035  # a banner's letters are at least this share of that height;
                       # a stream's chat overlay is 0.02-0.03
MIN_CONFIDENCE = 0.6
# A window is a menu, queue or settings screen when at least half its frames
# show a menu word, and this many different ones appear across it (a
# settings page shows a few per screen, and not always the same few).
MENU_WORDS = 2
WHOLE_WORD = 5         # Latin words this short only match on their own
# What the OCR often reads one letter as, folded together on both sides.
_FOLD = str.maketrans({"Q": "O", "0": "O", "1": "I", "5": "S"})
_SEPARATORS = re.compile(r"[\s:!?.,|/\\\[\]()_\-、。，．・：！？「」『』（）]+")

_engine = None


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class ScreenText:
    """events: (second, "ON SCREEN: ...") for the model; menus: (start, end,
    what was read) for the windows that read as a menu, queue or loading
    screen; frames: how many were read."""
    events: list = field(default_factory=list)
    menus: list = field(default_factory=list)
    frames: int = 0


def _norm(text: str) -> str:
    """Upper case, no accents, letters and digits only, the usual misreads
    folded: a stylised banner loses its spaces and accents, and "KIMOU11 A
    MARQUÉ" comes back as "KIMOU1TAMARQUE" or "...MAROUE"."""
    t = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in t.upper() if ch.isalnum() and not unicodedata.combining(ch)).translate(_FOLD)


class Lexicon:
    """Words to find in what the OCR read. Short Latin ones (ACE, GOAL, PLAY)
    only as a whole word, so ACE isn't found in PLACE or PLAY in PLAYER.
    Japanese and Chinese are written without spaces, so a kanji word (設定,
    感度) is found anywhere in the text."""

    def __init__(self, terms):
        self.terms = [(str(t), _norm(t)) for t in terms or [] if _norm(t)]

    def find(self, text: str) -> list[str]:
        compact = _norm(text)
        words = {_norm(w) for w in _SEPARATORS.split(str(text))}
        hits = []
        for term, n in self.terms:
            whole = n.isascii() and len(n) <= WHOLE_WORD
            if (n in words or n == compact) if whole else n in compact:
                hits.append(term)
        return hits


def _ocr(img):
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    result, _elapsed = _engine(img, use_cls=False)
    return result or []


def read_frame(img, events: Lexicon, menu: Lexicon, ocr=None) -> tuple[list[str], set]:
    """(event words in a banner, menu words) in the middle of one frame."""
    import cv2

    h, w = img.shape[:2]
    left, top, right, bottom = CENTRE
    img = img[int(h * top):int(h * bottom), int(w * left):int(w * right)]
    h, w = img.shape[:2]
    # Linear, not area-averaged, even when shrinking: stylised letters kept
    # their shapes better (area turned "11 A MARQUÉ" into "LFASMAROUE").
    img = cv2.resize(img, (WIDTH, max(2, round(h * WIDTH / w))), interpolation=cv2.INTER_LINEAR)
    height, width = img.shape[:2]
    found, menus = [], set()
    for box, text, conf in (ocr or _ocr)(img):
        if float(conf) < MIN_CONFIDENCE:
            continue
        menus.update(menu.find(text))
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        tall = (max(ys) - min(ys)) / height
        cx, cy = sum(xs) / len(xs) / width, sum(ys) / len(ys) / height
        # A stream's chat overlay and the HUD are small, or at the edges.
        if tall >= BANNER_HEIGHT and 0.05 <= cx <= 0.95 and 0.03 <= cy <= 0.97:
            hits = events.find(text)
            if hits:
                found.append(max(hits, key=len))
    return found, menus


def read_screen(path: Path, windows: list, genre_at, lexicon: dict, grab=None, read=None,
                budget: float = TIME_BUDGET, max_frames: int = MAX_FRAMES) -> ScreenText:
    """Read the screen in each window (strongest first, as given). genre_at:
    (start, end) -> the kind of game there, which picks its event words.
    `grab` (second -> frame) and `read` stand in for the video and the OCR
    in tests."""
    if not windows:
        return ScreenText()
    if grab is not None:
        return _read_windows(windows, genre_at, lexicon, grab, read or read_frame, budget, max_frames)
    import cv2

    from video.capture import video_capture

    with video_capture(path, required=False) as cap:
        if cap is None:
            return ScreenText()

        def grab_frame(t: float):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, img = cap.read()
            return img if ok else None
        return _read_windows(windows, genre_at, lexicon, grab_frame, read or read_frame, budget, max_frames)


def _read_windows(windows, genre_at, lexicon: dict, grab, read, budget: float, max_frames: int) -> ScreenText:
    out = ScreenText()
    events_by_genre = lexicon.get("events") or {}
    menu = Lexicon(lexicon.get("menu"))
    t0 = time.monotonic()
    for start, end in windows:
        if out.frames >= max_frames or time.monotonic() - t0 > budget:
            break
        genre = genre_at(start, end)
        events = Lexicon([*(events_by_genre.get("generic") or []),
                          *(events_by_genre.get(genre) or [] if genre != "generic" else [])])
        read_here = menu_frames = 0
        menu_seen: dict[str, int] = {}
        named = set()
        t = float(start) + 1.0
        while t < end and out.frames < max_frames:
            img = grab(t)
            if img is None:
                break
            out.frames += 1
            read_here += 1
            found, menus = read(img, events, menu)
            if menus:
                menu_frames += 1
                for m in menus:
                    menu_seen[m] = menu_seen.get(m, 0) + 1
            for term in found:
                if term not in named:
                    named.add(term)
                    out.events.append((t, f"ON SCREEN: {term}"))
            t += SAMPLE_CLOSE if t - start < CLOSE_FOR else SAMPLE_EVERY
        if read_here and menu_frames * 2 >= read_here and len(menu_seen) >= MENU_WORDS:
            words = sorted(menu_seen, key=lambda m: (-menu_seen[m], m))[:3]
            out.menus.append((float(start), float(end), ", ".join(words)))
    out.events.sort(key=lambda ev: ev[0])
    return out
