"""Which moments a job keeps: the Highlights choice (All goals, Best saves...)
picks event types, and the period picks part of the match. Nothing is kept or
dropped on a guess: when the period of a moment isn't known, a period filter
keeps it and says so, rather than silently losing a goal."""

from sports.core.events import SportEvent


def event_types(spec: dict, highlights: str) -> set[str] | None:
    """The event types a Highlights choice keeps; None for all of them."""
    choice = (spec.get("highlights_choices") or {}).get(highlights) or {}
    events = choice.get("events", "all")
    if events == "all":
        return None
    return {str(e) for e in events}


def post_extra(spec: dict, highlights: str) -> float:
    """Extra seconds a Highlights choice keeps after the moment (the
    celebration, for "Goals + celebrations")."""
    choice = (spec.get("highlights_choices") or {}).get(highlights) or {}
    try:
        return max(0.0, float(choice.get("post_extra") or 0))
    except (TypeError, ValueError):
        return 0.0


def select(events: list[SportEvent], spec: dict, highlights: str = "best",
           period: str = "full") -> tuple[list[SportEvent], list[str]]:
    """(the events kept, notes for the run's outcome)."""
    notes: list[str] = []
    types = event_types(spec, highlights)
    kept = [e for e in events if types is None or e.type in types]
    if period and period != "full":
        known = [e for e in kept if e.period]
        unknown = [e for e in kept if not e.period]
        kept = [e for e in known if e.period == period] + unknown
        if unknown:
            label = (spec.get("periods") or {}).get(period, period)
            notes.append(f"{len(unknown)} moment(s) kept for {label} without knowing their "
                         f"{spec.get('period_word') or 'half'}: the match clock wasn't read there")
    return kept, notes
