"""Basketball framing, planned for the whole clip before a frame is rendered.

observe (what the detectors saw) -> shots (camera cuts and shot kinds) -> ball
(one plausible ball path per shot) -> players -> basket (rim, net and
backboard) -> phases (possession, pass, release, flight, rim, result...) ->
planner (the crop for every frame) -> validate (and repair). See
docs/SPORTS.md. Everything but observe, sources, rim_finder and debug is pure
Python, so CI tests it."""

from __future__ import annotations

from sports.basketball.crop.model import Hints, Observation, Plan


def plan_clip(obs: Observation, hints: Hints | None = None, debug: dict | None = None) -> Plan:
    """The crop for every frame of one clip from what the detectors saw.

    Each camera shot is worked out on its own: its players tracked, its ball
    path chosen, its baskets found, the basket its play is about, its phases;
    then the planner frames the whole clip and checks every frame. Pass a
    dict as `debug` to get each shot's working (planner.ShotData) for the
    debug video."""
    from sports.basketball.crop import ball, basket, phases, planner, players, shots

    geometry = obs.geometry
    aspect = geometry.aspect
    frames = geometry.frames or obs.signals.frames
    cuts = shots.find_cuts(obs.signals)
    shot_list = shots.classify_all(shots.split(frames, geometry.fps, cuts), obs.samples)
    notes: list[str] = []
    data: list[planner.ShotData] = []
    next_track, next_basket = 0, 0
    for shot in shot_list:
        own = shots.samples_in(shot, obs.samples)
        d = planner.ShotData(shot=shot, samples=own)
        if shot.kind in ("wide", "medium") and own:
            tracks = players.track(own, aspect, first_id=next_track)
            next_track = max(tracks, default=next_track - 1) + 1
            ctx = ball.context(own, aspect, wide=shot.kind == "wide", bug_box=obs.bug_box)
            d.points = ball.track_shot(own, ctx)
            d.rows = basket.shot_baskets(own, shot, None, aspect=aspect, first_id=next_basket)
            next_basket += len(d.rows[0]) if d.rows else 0
            d.relevant = basket.relevant(d.rows, d.points, hints, samples=own, aspect=aspect)
            d.phases = phases.phases(shot, own, d.points, d.rows, d.relevant, hints, aspect)
            # Said only of the play's own basket, where the crop has to hold it.
            need = [(p := phases.at(d.phases, s.t)) is not None and p.kind in ("rim", "result") for s in own]
            note = basket.cut_note(shot, d.rows, d.relevant, need)
            if note:
                notes.append(note)
        data.append(d)
    if debug is not None:
        debug["shots"] = data
        debug["notes"] = notes
    return planner.plan(geometry, data, cuts, notes, aspect)
