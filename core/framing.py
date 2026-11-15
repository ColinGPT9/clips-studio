"""A cut turned into the clip's framed picture: core/pipeline.py's framing
block, moved here unchanged (editor suite step 7) so the export compiler
(video_editor/compose.py) can frame an AI item exactly as a plain run does.

    gaming_kept, framing_quality = frame_intermediate(source, padded, render_path, clip_dir, stem, ...)

It cuts `padded` out of `source` and writes `render_path`: through the
tracked 9:16 path (an intermediate cut, the editor's edits, then the gaming
layout, the podcast framing, or face / sport tracking and
cropper.render_vertical with crop modes track, bias_left, bias_right,
center and letterbox), or the one-encode path (landscape, Vertical Live, or
"vertical" off). Same argv, same files, same log lines as before the move:
tests/test_render_argv_snapshots.py and scripts/golden_renders.py hold it.

Every staging file it makes is appended to `scratch`, which the caller
removes in its own `finally` (as _render_files always has).

Returns (gaming_kept, framing_quality): the gaming layout this clip got
(None when the standard renderer framed it), and how sure a sport's framing
was (None without one).

`cut`, `gaming_render` and `framing_of` default to video.cutter.cut_clip and
this module's _try_gaming_render and _sport_framing; core/pipeline.py passes
its own names for them, so tests that stand in for those on core.pipeline
still reach this code. `track` defaults to video.tracker.compute_tracking;
the export compiler (video_editor/compose.py) passes one that keeps its
result in the editor's media cache. core/pipeline.py never passes it.

Nothing here imports the new editor's project model, media code or
compiler (video_editor.project, video_editor.media, video_editor.compose):
tests/test_editor_import_guard.py.
"""

from pathlib import Path

from core import cancel
from core.paths import discard
from video.cutter import cut_clip


def frame_intermediate(
    source: Path,
    padded,
    render_path: Path,
    clip_dir: Path,
    stem: str,
    *,
    config: dict,
    opts: dict,
    edit=None,
    ass_path: Path | None = None,
    vf_extra: str = "",
    normalize: bool = True,
    landscape: bool = False,
    vertical_live: bool = False,
    podcast: bool = False,
    gaming: bool = False,
    sport_name: str | None = None,
    sport_screen: str | None = None,
    scratch: list | None = None,
    cut=None,
    gaming_render=None,
    framing_of=None,
    track=None,
) -> tuple[dict | None, dict | None]:
    """Cut `padded` (a ClipCandidate, its end already padded) out of `source`
    and frame it into `render_path`. See the module's docstring."""
    cut = cut or cut_clip
    gaming_render = gaming_render or _try_gaming_render
    framing_of = framing_of or _sport_framing
    scratch = scratch if scratch is not None else []
    gaming_kept = None
    framing_quality = None   # the sport framing's quality, saved with the clip
    # Vertical Live takes the single-encode branch below, like longform:
    # the tracked path (intermediate cut, face tracking, TalkNet, layout
    # decisions, frames through Python) is never entered.
    if config["clips"].get("vertical", True) and not landscape and not vertical_live:
        # Cut a horizontal intermediate, track the subject, render 9:16.
        intermediate = clip_dir / f"{stem}.source.mp4"
        scratch.append(intermediate)
        cut(source, padded, intermediate)

        if edit is not None:
            # Apply manual edits BEFORE tracking, so the tracker and
            # captions see the final (edited) timeline.
            from video_editor.export import apply_edits

            edited = clip_dir / f"{stem}.edited.mp4"
            scratch.append(edited)
            apply_edits(intermediate, edit, edited)
            discard(intermediate)
            intermediate = edited

        if gaming:
            gaming_kept = gaming_render(intermediate, render_path, opts, config,
                                             ass_path, vf_extra, normalize)
        if gaming_kept is not None:
            pass  # rendered in the gaming layout (split or game only)
        elif podcast:
            # Separate podcast path (video/podcast.py): per shot, one static
            # crop on the person TalkNet hears talking, moving to a new
            # speaker inside a long shot; every change SNAPS on its frame,
            # with no split screen or letterbox. It reuses the tracker's
            # helpers and score_faces; the stream branch below is untouched.
            from video import podcast as podcast_mod

            tracking_cfg = config["tracking"]
            decision = podcast_mod.analyze(
                intermediate,
                model_name=tracking_cfg["detector"],
                sample_fps=tracking_cfg["sample_fps"],
            )
            # The editor's Layout buttons still win on a podcast clip.
            crop_mode = opts.get("crop", "track")
            if crop_mode == "center":
                decision = {"mode": "track", "path": [(0.0, 0.5)]}
            elif crop_mode == "letterbox":
                decision = {"mode": "fit_blur", "region": None}
            podcast_mod.render_clip(
                intermediate, render_path, decision, ass_path=ass_path,
                vf_extra=vf_extra, normalize=normalize,
            )
        else:
            from video import cuts, tracker  # lazy: tracker imports torch
            from video.cropper import render_vertical

            crop_mode = opts.get("crop", "track")
            # The play's moment, for a sport that frames around it; not
            # once cuts or a speed change have moved the clip's timeline
            # (the camera's scale stands).
            hints = opts.get("sport_moment") if isinstance(opts.get("sport_moment"), dict) else None
            if hints is not None and edit is not None and (edit.keep is not None
                                                           or abs(edit.speed - 1) >= 0.01):
                hints = {k: v for k, v in hints.items() if k == "scale"} or None
            sport_framing = (framing_of(intermediate,
                                            {**config, "sport_screen": sport_screen} if sport_screen else config,
                                            sport_name, hints)
                             if sport_name and crop_mode in ("track", "bias_left", "bias_right")
                             else None)
            if sport_framing is not None:
                framing_quality = sport_framing.pop("quality", None)
            if crop_mode == "center":
                tracking = {"mode": "track", "path": [(0.0, 0.5)]}
            elif sport_framing is not None:
                # A match (sports/): the ball and the play, not a face.
                tracking = sport_framing
                if crop_mode in ("bias_left", "bias_right"):
                    shift = -0.12 if crop_mode == "bias_left" else 0.12
                    tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
            else:
                tracking_cfg = config["tracking"]
                tracking = (track or tracker.compute_tracking)(
                    intermediate,
                    model_name=tracking_cfg["detector"],
                    sample_fps=tracking_cfg["sample_fps"],
                    force_fit_blur=(crop_mode == "letterbox"),
                    # On unless set off (ARCHITECTURE.md §5, video/cuts.py).
                    shot_aware=cuts.shot_aware_on(tracking_cfg),
                    no_subject=tracking_cfg.get("shot_aware_no_subject", "center"),
                )
                if crop_mode == "letterbox" and tracking["mode"] == "fit_blur":
                    # USER-forced letterbox means "show me the WHOLE frame" —
                    # reaction/gaming mixes need both the person and the
                    # content. Cropping tight to the detected person (the
                    # automatic letterbox behavior) threw away the game side.
                    tracking["region"] = None
                if tracking["mode"] == "track" and crop_mode in ("bias_left", "bias_right"):
                    shift = -0.12 if crop_mode == "bias_left" else 0.12
                    tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
            render_vertical(
                intermediate, tracking, render_path, ass_path=ass_path, vf_extra=vf_extra,
                normalize=normalize,
            )
    else:
        if edit is not None:
            # Horizontal (or Vertical Live) output: cut plain first, then
            # apply edits and burn captions in the same pass (they land
            # AFTER the cuts).
            from video_editor.export import apply_edits

            plain = clip_dir / f"{stem}.plain.mp4"
            scratch.append(plain)
            cut(source, padded, plain, vf_extra=vf_extra)
            # final: this pass writes the clip (or what the card joins),
            # with its first frame at 0 in every app.
            apply_edits(plain, edit, render_path, ass_path=ass_path, normalize=normalize,
                        final=True)
        else:
            cut(source, padded, render_path, ass_path=ass_path, vf_extra=vf_extra,
                     normalize=normalize, final=True)
    return gaming_kept, framing_quality


def _try_gaming_render(intermediate: Path, render_path: Path, opts: dict, config: dict,
                       ass_path: Path | None, vf_extra: str, normalize: bool) -> dict | None:
    """One clip in the gaming layout (gaming/run.py). None means the standard
    renderer takes it: a camera filling the frame, or any error at all."""
    try:
        from gaming import run as gaming_run

        g = opts.get("gaming")
        return gaming_run.render(intermediate, render_path, g if isinstance(g, dict) else {}, config,
                                 ass_path=ass_path, vf_extra=vf_extra, normalize=normalize)
    except Exception as e:
        if config.get("clips", {}).get("whole"):
            # Detection can fail; preserve the entire frame using the existing
            # fit composition. A render failure propagates instead of cropping.
            from core.modes import probe_size
            from gaming import compose, layout

            width, height = probe_size(intermediate)
            settings = {"preset": "blurred", "cam": None, "game_box": [0, 0, 1, 1], "panels": []}
            plan = layout.plan(width, height, settings)
            compose.render(intermediate, render_path, plan, ass_path=ass_path,
                           vf_extra=vf_extra, normalize=normalize)
            return {**settings, "layout": plan.kind, "used_preset": plan.preset, "used_cam": None}
        print(f"      (Gaming layout failed, using the standard layout: {e})")
        return None


def _sport_framing(clip_path: Path, config: dict, sport_name: str, hints: dict | None = None) -> dict | None:
    """The sport's own framing for a clip (sports/), or None to use the face
    tracker as always: a sport without framing, or a failure, never stops a
    clip from rendering. `hints`: the clip's render_opts["sport_moment"]."""
    try:
        import sports

        return sports.framing(sport_name, clip_path, config, hints)
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      ({sport_name} framing failed: {e}; framing it the usual way)")
        return None
