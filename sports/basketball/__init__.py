"""Basketball: the second sport. config/sports.yaml's `basketball` entry is
its data (the taxonomy, the commentary's words, the sounds, the choices);
this package holds what the data can't say: which basket a new score is and
how much the game's situation makes it matter (profile.py, scoreboard.py),
the crowd, bench and courtside reactions (reactions.py, look.py), and the
framing that follows the play to the rim (action.py; not framing.py, which
would shadow the framing() hook below). See docs/SPORTS.md."""

from sports.basketball.profile import BasketballProfile


def profile(config: dict, option: dict, video=None) -> BasketballProfile:
    from sports.core.profile import weights_for

    return BasketballProfile(name="basketball", option=dict(option), weights=weights_for(config))


def framing(clip_path, config: dict) -> dict:
    """The ball, the players around it and the rim, or the reaction, for a
    9:16 crop (sports/basketball/action.py)."""
    import sports
    from sports.basketball import action

    settings = sports.spec("basketball").get("framing") or {}
    tracking = action.compute(clip_path, model_name=str(settings.get("model") or "yolov8n.pt"),
                              imgsz=int(settings.get("imgsz") or 1280))
    led = tracking.pop("led", {})
    print("      Basketball framing: " + ", ".join(f"{k} {v}" for k, v in led.items() if v) + " sample(s)")
    return tracking


def prepass(video_path, duration: float) -> dict:
    """Read while Whisper runs: the scoreboard, and the cutaways from the
    court (the reactions). Each is skipped, and said so, when it can't run."""
    import time

    import sports
    from analysis import game_text
    from core import cancel
    from sports.basketball import reactions, scoreboard

    out: dict = {}
    board = None
    if game_text.available():
        t0 = time.monotonic()
        board = scoreboard.read_video(video_path, duration, cancel=cancel.check_active)
        if board.box is None:
            print("      Scoreboard: none found on screen (gym or phone footage, or no score shown)")
        else:
            print(f"      Scoreboard: read {len(board.readings)} time(s) in {time.monotonic() - t0:.0f}s, "
                  f"{len(board.changes)} basket(s)")
        out["board"] = board
    else:
        print("      (scoreboard: the OCR isn't installed, scoring the game without it)")
    settings = sports.spec("basketball").get("reactions") or {}
    try:
        t0 = time.monotonic()
        model = str((sports.spec("basketball").get("framing") or {}).get("model") or "yolov8n.pt")
        shots = reactions.read_shots(video_path, duration, float(settings.get("court_share", 0.2)),
                                     float(settings.get("crowd_edges", 0.255)), cancel=cancel.check_active,
                                     tall=float(settings.get("people_tall", 0.36)), model_name=model)
        found = reactions.cutaways(shots, duration)
        if settings.get("names_from_screen", True) and found:
            teams = board.teams() if board is not None else None
            reactions.read_names(video_path, found, exclude=teams or ())
        named = sum(1 for c in found if c.name)
        print(f"      Cutaways from the court: {len(found)} in {time.monotonic() - t0:.0f}s"
              + (f", {named} with a name on screen" if named else ""))
        out["cutaways"] = found
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      (cutaways: reading the shots failed: {e})")
    return out

