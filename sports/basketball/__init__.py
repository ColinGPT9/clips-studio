"""Basketball: the second sport. config/sports.yaml's `basketball` entry is
its data (the taxonomy, the commentary's words, the sounds, the choices);
this package holds what the data can't say: which basket a new score is and
how much the game's situation makes it matter (profile.py, scoreboard.py),
the crowd, bench and courtside reactions (reactions.py, look.py), and the
framing that follows the play to the rim (action.py; not framing.py, which
would shadow the framing() hook below). See docs/SPORTS.md."""

from sports.basketball.profile import BasketballProfile

# The video's own description names the players (hotwords) and says who won
# (names.sides): a job whose download was reused asks for it (core/pipeline.py).
READS_DESCRIPTION = True


def profile(config: dict, option: dict, video=None) -> BasketballProfile:
    from sports.core.profile import weights_for

    return BasketballProfile(name="basketball", option=dict(option), weights=weights_for(config),
                             video_text=(str(getattr(video, "title", "") or ""),
                                         str(getattr(video, "description", "") or "")))


def hotwords(option: dict, video) -> str | None:
    """The players and teams the video's own title and description spell,
    and the teams the job names, for Whisper to listen for (names.py)."""
    from sports.basketball import names

    return names.for_video(str(getattr(video, "title", "") or ""), str(getattr(video, "description", "") or ""),
                           str((option or {}).get("teams") or ""))


def framing(clip_path, config: dict) -> dict:
    """The ball, the players around it and the rim, or the reaction, for a
    9:16 crop (sports/basketball/action.py)."""
    import sports
    from sports.basketball import action

    settings = sports.spec("basketball").get("framing") or {}
    tracking = action.compute(clip_path, model_name=str(settings.get("model") or "yolov8n.pt"),
                              imgsz=int(settings.get("imgsz") or 1280),
                              hide_scoreboard=bool(settings.get("hide_scoreboard", True)))
    led = tracking.pop("led", {})
    print("      Basketball framing: " + ", ".join(f"{k} {v}" for k, v in led.items() if v) + " sample(s)"
          + (f"; the scoreboard left out ({1 - (tracking['rows'][1] - tracking['rows'][0]):.0%} of the height)"
             if tracking.get("rows") else ""))
    return tracking


def prepass_wait(duration: float) -> float:
    """How long a job waits for prepass() once its other passes are done:
    the video's own length, and never under the 900 s every sport gets. The
    score bug is read keyframe by keyframe, about 1.2 s each on a test PC: a
    79-minute game's took 25 minutes, and at 900 s its scoreboard was
    dropped, so its clips came out as plain clips that missed its ending."""
    return max(900.0, float(duration or 0))


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
            timed = sum(c.shown is not None for c in board.changes)
            print(f"      Scoreboard: read {len(board.readings)} time(s) in {time.monotonic() - t0:.0f}s, "
                  f"{len(board.changes)} basket(s)" + (f", {timed} timed between keyframes" if timed else ""))
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
        out["shots"] = shots                       # (the celebration after the game's last basket: profile.py)
        if settings.get("names_from_screen", True) and found:
            # A caption of the teams' or schools' own words names no one:
            # those on the bug and in the video's title.
            from pathlib import Path

            teams = board.teams() if board is not None else None
            seen = [r.text for r in board.readings] if board is not None else []
            exclude = set(teams or ()) | reactions.known_words(Path(str(video_path)).stem, *seen)
            reactions.read_names(video_path, found, exclude=exclude)
        named = sum(1 for c in found if c.name)
        print(f"      Cutaways from the court: {len(found)} in {time.monotonic() - t0:.0f}s"
              + (f", {named} with a name on screen" if named else ""))
        out["cutaways"] = found
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      (cutaways: reading the shots failed: {e})")
    return out

