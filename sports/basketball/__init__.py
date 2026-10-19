"""Basketball: the second sport. config/sports.yaml's `basketball` entry is
its data (the taxonomy, the commentary's words, the sounds, the choices);
this package holds what the data can't say: which basket a new score is and
how much the game's situation makes it matter (profile.py, scoreboard.py),
the crowd, bench and courtside reactions (reactions.py, look.py), where a
clip starts (leadin.py), and the framing planned around the play to the rim
(crop/, run by action.py; not framing.py, which would shadow the framing()
hook below). See docs/SPORTS.md."""

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


# framing() takes the clip's hints (its render_opts["sport_moment"]): sports.framing passes them.
FRAMING_HINTS = True


def framing(clip_path, config: dict, hints: dict | None = None) -> dict:
    """The 9:16 crop for one clip, planned for the whole clip around the play
    (sports/basketball/crop: the ball and its holder, the receiver of a pass,
    the shot to the basket with the rim, net and backboard in)."""
    import sports
    from sports.basketball import action

    settings = dict(sports.spec("basketball").get("framing") or {})
    # The Sports row's Crop for this clip (core/pipeline passes it): "full" fills the screen.
    if config.get("sport_screen") == "full":
        settings["screen"] = "full"
    tracking = action.compute_offline(clip_path, settings, hints)
    quality = tracking.get("quality") or {}
    print("      Basketball framing: " + str(quality.get("status", "?"))
          + f"; the ball led {float(quality.get('ball') or 0):.0%} of the play"
          + f", the basket kept in {float(quality.get('basket') or 0):.0%} of the frames that need it"
          + (f", wider than 9:16 for {quality['wide_s']}s" if quality.get("wide_s") else "")
          + (f"; the scoreboard left out ({1 - (tracking['rows'][1] - tracking['rows'][0]):.0%} of the height)"
             if tracking.get("rows") else "")
          + "".join(f"; {n}" for n in quality.get("notes") or []))
    _debug_video(clip_path, settings, hints)
    return tracking


def _debug_video(clip_path, settings: dict, hints: dict | None) -> None:
    """With CLIPS_STUDIO_FRAMING_DEBUG set to a folder, in a source checkout
    only (never a packaged app), also write the framing's debug video there
    (sports/basketball/crop/debug.py). The clip itself is never touched."""
    import os
    import sys

    folder = os.environ.get("CLIPS_STUDIO_FRAMING_DEBUG")
    if not folder or getattr(sys, "frozen", False):
        return
    try:
        from pathlib import Path

        from sports.basketball.crop import debug, observe, plan_clip
        from sports.basketball.crop.model import Hints

        h = Hints.from_opts({"sport_moment": hints} if hints else None)
        obs = observe.observe(clip_path, sources=settings.get("sources"), moment_t=h.moment_t, find_bug_box=False,
                              people_scale=h.scale)
        from sports.basketball.action import bands, picture_share

        obs.geometry.picture = picture_share(settings)
        obs.geometry.band_top = bands(settings)[0]
        working: dict = {}
        plan = plan_clip(obs, h, debug=working)
        out = Path(folder) / (Path(str(clip_path)).stem + ".framing.mp4")
        debug.render(clip_path, obs, plan, working["shots"], out)
        print(f"      Basketball framing: debug video {out}")
    except Exception as e:  # a debug aid never stops a render
        print(f"      (framing debug video failed: {e})")


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
        shots, scale = reactions.read_shots(video_path, duration, float(settings.get("court_share", 0.2)),
                                            float(settings.get("crowd_edges", 0.255)), cancel=cancel.check_active,
                                            tall=float(settings.get("people_tall", 0.36)), model_name=model)
        found = reactions.cutaways(shots, duration)
        out["shots"] = shots                       # (the celebration after the game's last basket: profile.py)
        # How tall the game camera shows the players: every height test of
        # the clips' shots and framing is scaled by it (profile.refine_windows).
        out["people_scale"] = scale
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
              + (f", {named} with a name on screen" if named else "")
              + (f"; the camera shows the players {scale:.1f}x as tall as a national broadcast's"
                 if scale > 1.05 else ""))
        out["cutaways"] = found
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      (cutaways: reading the shots failed: {e})")
    return out

