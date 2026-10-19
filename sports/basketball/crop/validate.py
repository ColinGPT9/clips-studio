"""Checking a planned crop frame by frame, and saying how sure it is.

A frame passes when everything the planner required for it (the ball with its
margin, the players of the phase, the whole basket from rim approach to the
result) is inside the crop. planner.py repairs what fails (less smoothing
there, then a wider level, then the shot's fallback) and asks again; what is
left is reported in the quality, never hidden.

Pure Python.
"""

from __future__ import annotations

SLACK = 0.01             # frame widths a required span may poke out of the crop before it counts as cut:
                         # every span carries a wider margin (the ball 0.04, players 0.025, the basket's
                         # past its backboard at least 0.012), so the thing itself is still in

# Quality: what share of the gameplay frames says what.
BALL_LED = 0.6           # "tracked" needs the ball to lead at least this share...
PASSING = 0.97           # ...this share of the frames to pass...
BASKET_OK = 0.97         # ...and the basket held in this share of the frames that need it
ASSISTED = 0.4           # "assisted": players or assisted framing this share or more
WIDE = 0.3               # "wide": the whole frame this share or more
UNCERTAIN = 0.03         # "uncertain": more than this share still failing after repair


def inside(cx: float, w: float, span: tuple[float, float] | None) -> bool:
    """The span (x0, x1) is inside the crop centred on cx, w wide."""
    if span is None:
        return True
    return cx - w / 2 - SLACK <= span[0] and span[1] <= cx + w / 2 + SLACK


def failures(cx: list[float], w: list[float], required: list[tuple[float, float] | None],
             frames: range | None = None) -> list[int]:
    """The frames whose required span is not inside the crop."""
    frames = frames if frames is not None else range(len(cx))
    return [f for f in frames if not inside(cx[f], w[f], required[f])]


def spans(frames: list[int], pad: int = 0, limit: int | None = None) -> list[tuple[int, int]]:
    """Runs of failing frames, each padded by `pad` frames (clipped to [0, limit)): (first, one past last)."""
    out: list[tuple[int, int]] = []
    for f in sorted(frames):
        a, b = f - pad, f + pad + 1
        if limit is not None:
            a, b = max(0, a), min(limit, b)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((max(0, a), b))
    return out


def quality(mode: list[str], gameplay: list[bool], failing: list[int], basket_frames: list[int],
            basket_failing: list[int], base: float, w: list[float], notes: list[str], cuts: int,
            fps: float) -> dict:
    """The clip's framing quality: a status (tracked, assisted, wide or
    uncertain) and what it rests on, for the log and the clip's saved options."""
    play = [f for f, g in enumerate(gameplay) if g]
    n = len(play) or 1
    led = sum(1 for f in play if mode[f] == "tracked") / n
    helped = sum(1 for f in play if mode[f] in ("assisted", "players")) / n
    wide = sum(1 for f in play if w[f] >= 0.999) / n
    failing_set = set(failing)
    fail_share = sum(1 for f in play if f in failing_set) / n
    basket_share = 1.0 - (len(basket_failing) / len(basket_frames)) if basket_frames else 1.0
    if not play:
        status = "wide" if any(x >= 0.999 for x in w) else "tracked"
    elif fail_share > UNCERTAIN:
        status = "uncertain"
    elif wide >= WIDE:
        status = "wide"
    elif helped >= ASSISTED:
        status = "assisted"
    elif led >= BALL_LED and 1 - fail_share >= PASSING and basket_share >= BASKET_OK:
        status = "tracked"
    else:
        status = "assisted"
    zoomed = sum(1 for x in w if x > base + 2e-3)
    return {"status": status, "ball": round(led, 3), "passing": round(1 - fail_share, 3),
            "basket": round(basket_share, 3), "cuts": cuts, "wide_s": round(zoomed / max(fps, 1e-6), 2),
            "notes": list(dict.fromkeys(notes))}
