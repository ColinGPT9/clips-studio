"""Why a finished run produced the clips it did.

This exists because of one recurring bug report: "clips didn't get created",
critical severity, three vague words, no video named. The run was fine. The
person fed in gameplay, or footage with nobody on screen, and got exactly what
the scoring is designed to give them — nothing.

The reason was always knowable. It is in the scores the fusion pass already
computed. It was simply never written anywhere a user could see it: the
pipeline prints one line to a log nobody opens, and the explanation of the
gameplay case lives in KNOWN-ISSUES.md, which nobody opens either. Meanwhile
the app says "No clips for this video yet", which reads like a fault.

So: summarise the run, name the cause when the evidence supports naming it, and
say nothing more than the numbers when it does not. Being confidently wrong
about someone's footage would produce a worse bug report than saying nothing.

Pure and stdlib-only, so it tests on a CI runner with four packages installed.
"""

# A reaction subscore is the "is a prominent person on screen" signal. At or
# below this it means the detector looked and found nothing person-shaped,
# rather than finding someone unremarkable.
NOTHING_DETECTED = 5

# How much of the measured evidence has to agree before the cause is named.
# Well short of everything: one candidate that happens to catch a face in a
# stream overlay should not veto an otherwise unanimous read.
AGREEMENT = 0.8

# Below this many measured candidates there is not enough to generalise from.
MIN_EVIDENCE = 3


def summarise_run(candidates, rejections, config) -> dict:
    """What happened in this run, as plain numbers plus a cause when earned.

    `candidates` are the ones that survived, `rejections` the ones that did
    not — each carrying the `.candidate.score` and `.candidate.subscores` the
    scorer produced.
    """
    min_score = int((config.get("clips") or {}).get("min_score", 0))
    kept = list(candidates or [])
    dropped = list(rejections or [])

    scores = [c.score for c in kept]
    scores += [r.candidate.score for r in dropped if r.candidate is not None]

    by_reason: dict[str, int] = {}
    for r in dropped:
        by_reason[r.reason] = by_reason.get(r.reason, 0) + 1

    # Only candidates the detector actually looked at can say anything about
    # who was on screen. The rest carry a placeholder.
    measured = []
    for sub in _subscores(kept, dropped):
        if sub.get("reaction_measured"):
            measured.append(int(sub.get("reaction", 0)))

    out = {
        "clips": len(kept),
        "candidates": len(kept) + len(dropped),
        "best_score": max(scores) if scores else None,
        "min_score": min_score,
        "rejected": by_reason,
        "measured": len(measured),
        "nothing_detected": sum(1 for r in measured if r <= NOTHING_DETECTED),
    }
    out["cause"] = _cause(out) if not kept else None
    return out


def _subscores(kept, dropped):
    for c in kept:
        yield c.subscores or {}
    for r in dropped:
        if r.candidate is not None:
            yield r.candidate.subscores or {}


def _cause(out: dict) -> str | None:
    """Name the cause, or None when the evidence does not support one."""
    if not out["candidates"]:
        # Nothing was even proposed, so scoring never came into it.
        return "no_candidates"

    measured, blank = out["measured"], out["nothing_detected"]
    if measured >= MIN_EVIDENCE and blank >= measured * AGREEMENT:
        return "no_people"

    dropped_low = out["rejected"].get("below_min_score", 0)
    dups = sum(n for reason, n in out["rejected"].items()
               if reason not in ("below_min_score", "over_limit"))
    if dropped_low and dups > dropped_low:
        return "duplicates"
    if dropped_low:
        return "below_threshold"
    return None


def explain_no_clips(out: dict) -> str:
    """One line for the log. The UI builds its own wording from the numbers,
    so this stays terse and does not try to be the user-facing copy."""
    best, floor = out.get("best_score"), out.get("min_score")
    head = f"No clips: {out.get('candidates', 0)} candidate(s) considered"
    if best is not None:
        head += f", best scored {best} against a threshold of {floor}"

    cause = out.get("cause")
    if cause == "no_people":
        return (head + ". Nothing person-shaped was detected in "
                f"{out['nothing_detected']} of {out['measured']} measured windows, which is "
                "what gameplay and top-down footage look like to the scorer.")
    if cause == "duplicates":
        return head + ". Most candidates repeated one already kept."
    if cause == "no_candidates":
        return "No clips: nothing was proposed as a candidate at all."
    return head + ". Lowering clips.min_score in settings would let more through."
