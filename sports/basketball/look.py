"""Who a reaction shot shows: the bench, courtside, the crowd, a coach.

The cutaway is found from the people the detector sees, or the picture's
colours without it, and the crowd's sound (sports/basketball/reactions.py);
what it shows is told by the same local
model the app already uses to look at a gaming stream's frames
(analysis/game_vision.py): Gemma 3 or Gemma 4 through Ollama, with the
pictures never leaving the PC. Only the kind of shot is asked, never who
someone is: a person is named only by the broadcast's own caption.
Skipped, and said so, with a model that can't take images.
"""

import json
import re

MAX_LOOKS = 10
FRAMES = 2
KINDS = {
    "courtside": "courtside_reaction",
    "crowd": "crowd_reaction",
    "fans": "crowd_reaction",
    "bench": "bench_reaction",
    "coach": "coach_reaction",
    "player": "player_reaction",
}
PROMPT = (
    "These {count} frames are from a basketball broadcast, just after a play, when the camera cut away "
    "from the court. What does the shot show? Answer with one of: courtside (fans in the front row, "
    "beside the court), crowd (fans in the stands), bench (a team's bench, players on it standing or "
    "sitting), coach, player (a player close up), court (the game itself), other. Do not say who "
    "anyone is. Respond with ONLY valid JSON: {{\"shot\": \"<one word>\"}}"
)


def _parse(raw: str) -> str:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return ""
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return ""
    return str(data.get("shot") or "").strip().lower() if isinstance(data, dict) else ""


def look(profile, finalists: list, video_path, llm, grab=None) -> int:
    """Relabel the reaction clips among `finalists` by what the model sees
    in their reaction shot; returns how many were looked at. `grab` (second
    -> frame) stands in for the video in tests."""
    reactions = set(profile.reaction_types)
    todo = [c for c in finalists
            if (c.subscores or {}).get("sport_event") in reactions
            and (c.subscores or {}).get("sport_event") != "celebrity_reaction"][:MAX_LOOKS]
    if not todo:
        return 0
    if grab is None:
        from analysis import game_vision

        try:
            sees = bool(llm.sees_images())
        except Exception:
            sees = False
        if not sees or not game_vision.can_see(llm):
            print(f"  {profile.label}: who the reaction shots show isn't told: "
                  f"{getattr(llm, 'name', 'the AI')} can't look at images (Gemma 3 and Gemma 4 can)")
            return 0
        import cv2

        from video.capture import video_capture

        with video_capture(video_path, required=False) as cap:
            if cap is None:
                return 0

            def grab_frame(t: float):
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, img = cap.read()
                return img if ok else None

            return _look(profile, todo, llm, grab_frame)
    return _look(profile, todo, llm, grab)


def _look(profile, todo: list, llm, grab) -> int:
    from analysis.game_vision import _jpeg
    from core import cancel

    looked = 0
    for c in todo:
        cancel.check_active()
        at = float(c.subscores.get("sport_t", c.start))
        images = [_jpeg(f) for f in (grab(at + 0.5 + i) for i in range(FRAMES)) if f is not None]
        images = [b for b in images if b]
        if not images:
            continue
        try:
            shot = _parse(llm.look(PROMPT.format(count=len(images)), images))
        except Exception as e:
            print(f"  (looking at a reaction shot failed: {e})")
            continue
        looked += 1
        kind = KINDS.get(shot)
        c.subscores["seen_what"] = shot or "unclear"
        if kind:
            c.subscores["sport_event"] = kind
            c.subscores["sport_label"] = profile.event_label(kind)
    return looked
