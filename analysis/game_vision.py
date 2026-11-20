"""The AI looks at the best candidates of a gaming stream.

Everything else the gaming profile scores with is a stand-in for the
picture: chat, the game's sound, a banner's words, the transcript. The last
check before ranking shows the local model a few frames of each of the best
candidates and asks what happens in them: a fight, a goal, a boss going down,
or a menu, a loading screen, nothing at all.

Only a local model that takes images does it (Gemma 3 and Gemma 4 through
Ollama; gemma:7b can't). The video picture never leaves the PC, as the
privacy page promises, so a cloud AI skips this step. Its verdict moves a
clip by at most 10 points either way, within a time budget. It only takes
points off as far as the clip is quiet: what is said counts as on any stream,
so a just-chatting stretch or a reaction with no gameplay in the picture
isn't marked down for that.
"""

import json
import re
import time
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "look_at_game.txt"

MAX_CANDIDATES = 12
FRAMES = 4
# Longest side of each frame sent: Gemma's image encoder works at 896, and
# at 512 gemma3:4b took two Apex fights for ordinary play.
SIZE = 896
QUALITY = 85
TIME_BUDGET = 240.0    # seconds of looking per video, at most
MAX_ADJUST = 10


def _parse(raw: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_can_see: dict[str, bool] = {}


def can_see(llm) -> bool:
    """Whether the model really gets the images, asked once per model with a
    plain red square. A model can say it takes images and not receive them:
    gemma4:e4b on one Ollama release described "a dark, abstract background"
    for a settings page, and its verdicts were noise."""
    name = getattr(llm, "name", "")
    if name not in _can_see:
        import cv2
        import numpy as np

        red = np.zeros((96, 96, 3), dtype=np.uint8)
        red[:, :, 2] = 255
        ok, buf = cv2.imencode(".jpg", red)
        try:
            answer = llm.look('What single colour fills this image? Respond with ONLY valid JSON: '
                              '{"colour": "<colour>"}', [buf.tobytes()]) if ok else ""
        except Exception:
            answer = ""
        _can_see[name] = "red" in str(answer).lower()
    return _can_see[name]


def frame_times(start: float, end: float, count: int = FRAMES) -> list[float]:
    """Evenly through the clip: a game moment's window starts a few seconds
    before the play, so these cover the setup, the play and the reaction."""
    span = max(0.0, end - start)
    return [start + (i + 0.5) * span / count for i in range(count)]


def _jpeg(img) -> bytes:
    import cv2

    h, w = img.shape[:2]
    scale = SIZE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (max(2, round(w * scale)), max(2, round(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
    return buf.tobytes() if ok else b""


def verdict_delta(data: dict | None) -> tuple[int, str] | None:
    """(score change, what was seen) from the model's answer; None when it
    can't be read. Not gameplay: the full -10. Otherwise strength 5 (ordinary
    play) changes nothing, 10 adds 10, 0 takes 10 off."""
    if not data:
        return None
    moment = " ".join(str(data.get("moment") or "").split())[:80]
    gameplay = data.get("gameplay")
    if gameplay is False or str(gameplay).lower() == "false":
        return -MAX_ADJUST, moment or "not gameplay"
    try:
        strength = float(data.get("strength"))
    except (TypeError, ValueError):
        return None
    strength = min(10.0, max(0.0, strength))
    return max(-MAX_ADJUST, min(MAX_ADJUST, round((strength - 5) * 2))), moment


def look_at(candidates: list, video_path, llm, gaming, segments, events, grab=None,
            budget: float = TIME_BUDGET, max_candidates: int = MAX_CANDIDATES, talk=None) -> int:
    """Adjust the best `candidates` (by score) by what the model sees in
    them; returns how many were looked at. `grab` (second -> frame) stands in
    for the video in tests. `talk` (candidate -> 0 silent .. 1 steady talking)
    scales a verdict that takes points off: none of it for a clip full of
    talk, all of it for a silent one."""
    from core import cancel

    template = PROMPT_PATH.read_text(encoding="utf-8")
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)[:max_candidates]
    if not ranked:
        return 0
    if grab is not None:
        return _look(ranked, template, llm, gaming, segments, events, grab, budget, cancel, talk)
    import cv2

    from video.capture import video_capture

    with video_capture(video_path, required=False) as cap:
        if cap is None:
            return 0

        def grab_frame(t: float):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, img = cap.read()
            return img if ok else None
        return _look(ranked, template, llm, gaming, segments, events, grab_frame, budget, cancel, talk)


def _look(ranked, template, llm, gaming, segments, events, grab, budget, cancel, talk=None) -> int:
    from analysis.gaming import genre_spec
    from core import progress

    t0 = time.monotonic()
    looked = 0
    for li, c in enumerate(ranked, 1):
        if time.monotonic() - t0 > budget:
            break
        cancel.check_active()
        # One image call per clip, each tens of seconds on CPU — without this
        # the bar sat at the end of "analyze" for minutes, reading as a hang.
        progress.emit(stage="looking", current=li, total=len(ranked))
        times = frame_times(c.start, c.end)
        frames = [grab(t) for t in times]
        images = [_jpeg(f) for f in frames if f is not None]
        images = [b for b in images if b]
        if len(images) < 2:
            continue
        game, genre = gaming.game_at(c.start, c.end)
        spec = genre_spec(genre)
        said = " ".join(s.text.strip() for s in segments if s.end > c.start and s.start < c.end)
        seen = [f"{sec - c.start:.0f}s: {desc}" for sec, desc in events if c.start - 1 <= sec <= c.end]
        prompt = (template
                  .replace("{game}", f"{game} ({spec.get('label', 'game')})" if game else spec.get("label", "a game"))
                  .replace("{highlights}", " ".join(str(spec.get("highlights", "")).split()))
                  .replace("{count}", str(len(images)))
                  .replace("{times}", ", ".join(f"{t - c.start:.0f}" for t in times))
                  .replace("{transcript}", said[:1200] or "(nothing)")
                  .replace("{events}", "\n".join(seen[:8]) or "(nothing)"))
        try:
            result = verdict_delta(_parse(llm.look(prompt, images)))
        except Exception as e:
            print(f"  (looking at a clip failed: {e})")
            result = None
        looked += 1
        if result is None:
            continue
        delta, moment = result
        if delta < 0 and talk is not None:
            delta = round(delta * (1.0 - max(0.0, min(1.0, talk(c)))))
        c.score = max(0, min(100, c.score + delta))
        c.subscores = c.subscores or {}
        c.subscores["seen"] = delta
        if moment:
            c.subscores["seen_what"] = moment
            why = c.subscores.get("game_why")
            c.subscores["game_why"] = f"SEEN: {moment}" + (f"; {why}" if why else "")
    return looked
