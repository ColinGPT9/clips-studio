"""Sports (docs/SPORTS.md): sport-specific intelligence on top of the normal
clipping pipeline, one sport per package. Soccer first, then basketball.

A job with the Sports toggle on carries `sport`, for example
{"name": "soccer", "highlights": "goals", "period": "full", "teams": "Team A"}
(and with Custom highlights, `request`: the moments in the person's words).
Without it nothing here runs and the video is clipped exactly as before.

It adds no pipeline of its own. The sport's profile plugs into the same
scoring the gaming profile uses (analysis/fusion.py), with the same Whisper,
the same AI (any provider), the same renderer, queue, watches and publishing.

Adding a sport: an entry in config/sports.yaml, a sports/<name>/ package with
a `profile(config, option, video)` function, and a line in SPORTS below.
"""

import importlib
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sports.yaml"

# Each sport and the package that knows it, imported only when a job asks.
SPORTS = {"soccer": "sports.soccer", "basketball": "sports.basketball"}

TEAMS_MAX = 200


@lru_cache(maxsize=1)
def knowledge() -> dict:
    import yaml

    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def spec(name: str) -> dict:
    """A sport's entry in config/sports.yaml, or {}."""
    return knowledge().get(name) or {}


def sound_groups(name: str) -> dict:
    """The sounds a match is listened for: the app's own groups
    (config/gaming.yaml) and the sport's (`sound_groups`, like basketball's
    buzzer), so a sport's sound never changes how a game is scored."""
    from analysis import gaming

    return {**(gaming.knowledge().get("sound_groups") or {}), **(spec(name).get("sound_groups") or {})}


def available() -> list[dict]:
    """The sports the app offers, with their highlight and period choices,
    for the Sports toggle's menus."""
    out = []
    for name in SPORTS:
        s = spec(name)
        if not s:
            continue
        out.append({
            "id": name,
            "label": s.get("label") or name.title(),
            "highlights": [{"id": k, "label": v.get("label") or k}
                           for k, v in (s.get("highlights_choices") or {}).items()],
            "periods": [{"id": k, "label": v} for k, v in (s.get("periods") or {}).items()],
            "footage": [{"id": k, "label": v} for k, v in (s.get("footage_choices") or {}).items()],
            # Whether the app's Sport row offers the period (basketball's
            # quarters), and what it calls it. Soccer always clips the whole match.
            **({"period_menu": str(s.get("period_menu"))} if s.get("period_menu") else {}),
        })
    return out


def clean(raw) -> dict:
    """A job's sport option reduced to valid values: the API's check, so a
    job can't be queued for a sport or choice that doesn't exist. Raises
    ValueError with what is allowed."""
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise ValueError("sport must be an object like {\"name\": \"soccer\"}")
    name = str(raw.get("name") or "").strip().lower()
    s = spec(name) if name in SPORTS else {}
    if not s:
        raise ValueError(f"unknown sport {name!r}; available: {', '.join(SPORTS)}")
    choices = s.get("highlights_choices") or {}
    highlights = str(raw.get("highlights") or "best")
    if highlights not in choices:
        raise ValueError(f"highlights must be one of: {', '.join(choices)}")
    periods = s.get("periods") or {}
    period = str(raw.get("period") or "full")
    if period not in periods:
        raise ValueError(f"period must be one of: {', '.join(periods)}")
    footage_choices = s.get("footage_choices") or {"auto": "Automatic"}
    footage = str(raw.get("footage") or "auto")
    if footage not in footage_choices:
        raise ValueError(f"footage must be one of: {', '.join(footage_choices)}")
    out = {"name": name, "highlights": highlights, "period": period}
    if footage != "auto":
        out["footage"] = footage
    teams = " ".join(str(raw.get("teams") or "").split())[:TEAMS_MAX]
    if teams:
        out["teams"] = teams
    # Story reels to make from the match's clips (sports/core/reels.py).
    raw_reels = raw.get("reels") or []
    if isinstance(raw_reels, str):
        raw_reels = [r.strip() for r in raw_reels.split(",")]
    from sports.core.reels import KINDS

    reels = [str(r) for r in raw_reels if str(r).strip()]
    bad = [r for r in reels if r not in KINDS]
    if bad:
        raise ValueError(f"reels must be some of: {', '.join(KINDS)}")
    if reels:
        out["reels"] = [k for k in KINDS if k in reels]
    # The match's events as the person has them (sports/core/events_import.py).
    # Refused only when not one line can be read: the rest are listed back
    # with the match's report.
    events = str(raw.get("events") or "").strip()
    if events:
        from sports.core import events_import

        if len(events) > events_import.MAX_TEXT:
            raise ValueError(f"events can be at most {events_import.MAX_TEXT} characters")
        read, _unread = events_import.parse(events, s)
        if not read:
            raise ValueError("events: no line had both a time and a kind of moment, "
                             "like \"18:16 Goal\" or \"45+2' yellow card\"")
        out["events"] = events
    # Custom highlights: the moments described in the person's own words,
    # which become a clip direction (analysis/intent.py). Only with Custom,
    # or a choice that narrows by it (basketball's "fans reacting to dunks").
    request = " ".join(str(raw.get("request") or "").split())[:TEAMS_MAX]
    if request and (highlights == "custom" or choices[highlights].get("takes_request")):
        out["request"] = request
    return out


def direction(opt: dict) -> str:
    """What a sport option asks for in words, as a clip direction
    (analysis/intent.py): Custom's description, then the teams or players.
    "" when it asks for nothing in words."""
    parts = []
    if opt.get("request"):
        parts.append(f"{str(opt['request']).rstrip('.')}.")
    if opt.get("teams"):
        parts.append(f"More clips involving {opt['teams']}.")
    return " ".join(parts)


def option(config_or_opts: dict | None) -> dict | None:
    """The sport a job config (its clips section) or a clip's render options
    carries, or None."""
    if not config_or_opts:
        return None
    raw = config_or_opts.get("sport")
    if raw is None and isinstance(config_or_opts.get("clips"), dict):
        raw = config_or_opts["clips"].get("sport")
    if not raw:
        return None
    try:
        return clean(raw)
    except ValueError:
        return None


def profile_for(config: dict, video=None):
    """The sport's profile for this job, or None when it has none."""
    opt = option(config)
    if opt is None:
        return None
    module = importlib.import_module(SPORTS[opt["name"]])
    return module.profile(config, opt, video)


def framing(name: str, clip_path, config: dict) -> dict | None:
    """The sport's crop path for one clip ({"mode": "track", "path": ...}),
    or None when the sport has no framing of its own."""
    if name not in SPORTS:
        return None
    module = importlib.import_module(SPORTS[name])
    frame = getattr(module, "framing", None)
    return frame(clip_path, config) if frame is not None else None


def prepass(config: dict, video_path, duration: float) -> dict:
    """What the sport reads from the video itself while Whisper runs (soccer:
    the scoreboard), as attributes for its profile. {} for a sport with none."""
    opt = option(config)
    if opt is None:
        return {}
    module = importlib.import_module(SPORTS[opt["name"]])
    read = getattr(module, "prepass", None)
    return read(video_path, duration) if read is not None else {}


def prepass_wait(config: dict, duration: float) -> float:
    """How long a job waits for its sport's prepass after its other passes:
    900 s, or what the sport says its pass needs on a video this long
    (basketball's scoreboard, read keyframe by keyframe, took 25 minutes on
    a 79-minute game). 900 for every sport without the hook, Soccer too."""
    opt = option(config)
    if opt is None:
        return 900.0
    wait = getattr(importlib.import_module(SPORTS[opt["name"]]), "prepass_wait", None)
    return float(wait(duration)) if wait is not None else 900.0


def reads_description(config: dict) -> bool:
    """Whether this job's sport reads the video's own description
    (basketball: its players' names for Whisper, and who won for which team
    is which), so a job whose download was reused asks for it. False for
    every other job."""
    opt = option(config)
    if opt is None:
        return False
    return bool(getattr(importlib.import_module(SPORTS[opt["name"]]), "READS_DESCRIPTION", False))


def hotwords(config: dict, video) -> str | None:
    """Names for Whisper to listen for in this job's video (basketball: the
    players and teams its title and description spell), or None: a sport
    without the hook, and every job that isn't a sport, is transcribed as
    always."""
    opt = option(config)
    if opt is None:
        return None
    module = importlib.import_module(SPORTS[opt["name"]])
    names = getattr(module, "hotwords", None)
    return names(opt, video) if names is not None else None
