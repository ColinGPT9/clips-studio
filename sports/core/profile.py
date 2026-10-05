"""A sport's profile: what the scoring pipeline asks of it.

It answers the same questions analysis/fusion.py already asks the gaming
profile (the weights, the prompt guidance, what kind of "game" each part of
the video is), so the proven game-evidence path (the voice jump, the crowd and
whistle, on-screen text, event windows, the capped bonus, the frame check) runs
for a match exactly as it does for a game, with the sport's data in it. On top
it knows the sport's moments: their types, worth and windows, and the words
the commentary uses for each.

Everything here is data-driven from config/sports.yaml; a sport's own package
subclasses it only for rules the data can't say (a penalty that goes in is a
penalty goal, not a penalty and a goal).
"""

import re
from dataclasses import dataclass, field

import sports

STANDARD_WEIGHTS = {"text": 0.30, "visual": 0.20, "reaction": 0.20, "audio": 0.20, "engagement": 0.10}

# Independent signals needed before a moment is called by its type. One is a
# "big moment" at most: a roar on its own could be a near miss or a fight.
TYPED_AT = 2


@dataclass
class SportProfile:
    name: str
    option: dict = field(default_factory=dict)       # the job's choice (sports.clean)
    weights: dict = field(default_factory=lambda: dict(STANDARD_WEIGHTS))
    games: list = field(default_factory=list)        # the gaming interface: none for a match
    split_layout: bool = False
    # What the footage is, once known (resolve_footage): "broadcast" (a score
    # box, commentary, a stadium) or "sideline" (a club camera, a phone).
    footage: str = "broadcast"

    def resolve_footage(self, board) -> str:
        """The footage this match is scored as: the job's choice, or with
        Automatic, club or phone footage when no score box was found."""
        chosen = str((self.option or {}).get("footage") or "auto")
        if chosen in ("broadcast", "sideline"):
            self.footage = chosen
        else:
            self.footage = "broadcast" if board is not None and getattr(board, "box", None) else "sideline"
        return self.footage

    # ---- the interface fusion's gaming path uses ----------------------------

    @property
    def spec(self) -> dict:
        return {**sports.spec(self.name), "label": f"{self.label} match"}

    @property
    def label(self) -> str:
        return sports.spec(self.name).get("label") or self.name.title()

    @property
    def genre(self) -> str:
        return self.name

    @property
    def game(self) -> str:
        return ""

    def game_at(self, start: float, end: float | None = None) -> tuple[str, str]:
        return "", self.name

    def genre_track(self, seconds: int) -> list[str]:
        return [self.name] * max(0, int(seconds))

    def spec_for(self, genre: str) -> dict:
        """What the frame check (analysis/game_vision.py) is told this is."""
        s = sports.spec(self.name)
        return {"label": f"{self.label} match", "highlights": s.get("highlights", "")}

    @property
    def reads_screen(self) -> bool:
        """Whether reading the screen (analysis/game_text.py) is worth its time
        for this sport: `read_screen` in its config/sports.yaml entry."""
        return bool(sports.spec(self.name).get("read_screen", True))

    @property
    def screen_lexicon(self) -> dict:
        """The on-screen words analysis/game_text.py looks for in a match."""
        words = [str(w) for w in (sports.spec(self.name).get("screen_text") or [])]
        return {"events": {"generic": [], self.name: words}, "menu": []}

    def sound_weights(self) -> dict:
        """What each sound group means in this sport (game_audio.sound_signal)."""
        return {self.name: dict(sports.spec(self.name).get("sounds") or {})}

    def guidance(self, kind: str = "clips", start: float | None = None, end: float | None = None) -> str:
        s = sports.spec(self.name)
        highlights = " ".join(str(s.get("highlights", "")).split())
        callouts = []
        for words in (s.get("callouts") or {}).values():
            callouts += [str(w) for w in words[:2]]
        lines = [
            (f"THIS IS {self.label.upper()} MATCH FOOTAGE: a broadcast or recording of a match, "
             "with commentary when there is any. The clips are the moments on the pitch."),
            f"- The moments that matter: {highlights}.",
            (f"- The commentary names them as they happen ({', '.join(repr(c) for c in callouts[:10])}), "
             "and the CROWD / WHISTLE / ON SCREEN events listed with the transcript mark them too. "
             "A crowd roaring is the surest sign, and a goal's roar is the loudest and longest."),
            ("- For a goal, include the attack that led to it and the celebration; for any moment, "
             "a few seconds of build-up before it and the reaction after. 15-40 seconds is ideal."),
            ("- Score low: stoppages, routine passing in midfield, substitutions, pre-match and "
             "half-time studio talk, adverts and replays of a moment already clipped."),
        ]
        if kind == "rerank":
            lines = [lines[0], ("- Between clips that are otherwise as good, prefer the bigger moment: "
                                "a goal, then a great save, a penalty or a red card, then a big chance.")]
        return "\n".join(lines)

    # ---- what a sport can change (soccer's values are the defaults) ---------

    @property
    def scoring_types(self) -> tuple:
        """The moments a new score on the scoreboard confirms, the first being
        what a score change with nothing else to go on is called:
        `scoring_events` in config/sports.yaml (soccer: its goals)."""
        return tuple(sports.spec(self.name).get("scoring_events") or ("goal", "penalty_goal", "own_goal"))

    @property
    def celebration(self) -> float:
        """A scoring-like moment this soon after a confirmed score, with no new
        score of its own, is that score's celebration or replay:
        `celebration_seconds` (soccer: 45, no kick-off comes sooner)."""
        return float(sports.spec(self.name).get("celebration_seconds", 45) or 45)

    def sound_curves(self) -> tuple:
        """The sound groups the moments are found from, each as a curve of its
        own: `curves` (soccer: the crowd and the whistle)."""
        return tuple(sports.spec(self.name).get("curves") or ("crowd", "whistle"))

    def confirmed_type(self, change, event) -> str:
        """What a score change confirms `event` (the moment found there, or
        None) to be: its own type when that is a scoring one, else the
        sport's first scoring type."""
        if event is not None and event.type in self.scoring_types:
            return event.type
        return self.scoring_types[0]

    def context_weight(self, event) -> float:
        """How much the game's situation lifts or lowers a moment's worth
        (a basket in the last seconds of a close game, against one in a
        blowout). 1.0: a sport without it scores every moment by its type."""
        return 1.0

    def extra_moments(self, events: list, segments, *, curves: dict, video_end: float, min_len: float,
                      max_len: float) -> list:
        """The moments, with whatever else the sport finds on its own added
        (basketball: the crowd and bench reactions). Unchanged by default."""
        return events

    @property
    def one_play_per_clip(self) -> bool:
        """Whether two confirmed moments are two clips however close they come
        (basketball), each a window of its own. False: moments whose windows
        overlap are one, and a candidate covering a moment's window covers it
        (sports/core/events.py, clips.py)."""
        return False

    def clip_span(self, candidate, event) -> tuple[float, float]:
        """(start, end) of a Best moments clip that holds a confirmed moment:
        by default the scorer's window and the moment's own together, so the
        clip keeps the moment's build-up (sports/core/clips.py)."""
        return min(candidate.start, event.start), max(candidate.end, event.end)

    # ---- the sport's moments ------------------------------------------------

    def events_spec(self) -> dict:
        return sports.spec(self.name).get("events") or {}

    def importance(self, event_type: str) -> int:
        try:
            return int((self.events_spec().get(event_type) or {}).get("importance", 0))
        except (TypeError, ValueError):
            return 0

    def event_label(self, event_type: str) -> str:
        return (self.events_spec().get(event_type) or {}).get("label") or event_type.replace("_", " ")

    def window_of(self, event_type: str) -> tuple[float, float]:
        e = self.events_spec().get(event_type) or self.events_spec().get("big_moment") or {}
        return float(e.get("pre", 10)), float(e.get("post", 8))

    @property
    def crowd_lag(self) -> float:
        return float(sports.spec(self.name).get("crowd_lag_seconds", 0) or 0)

    @property
    def replay_within(self) -> float:
        return float(sports.spec(self.name).get("replay_within_seconds", 90) or 90)

    def replay_said(self, text: str) -> bool:
        """Whether the commentary says a replay is showing ("let's see it again")."""
        low = (text or "").lower()
        return any(re.search(rf"(?<!\w){re.escape(str(w).lower())}(?!\w)", low)
                   for w in sports.spec(self.name).get("replay_words") or [])

    def _callouts(self) -> list[tuple[str, re.Pattern]]:
        if getattr(self, "_compiled", None) is None:
            s = sports.spec(self.name)
            compiled = []
            for kind, words in (s.get("callouts") or {}).items():
                for w in words or []:
                    body = r"\s+".join(re.escape(part) for part in str(w).split())
                    exact = str(w).isupper() and len(str(w)) > 1       # "VAR", not "var"
                    compiled.append((kind, re.compile(rf"(?<!\w){body}(?!\w)", 0 if exact else re.I)))
            for kind, patterns in (s.get("patterns") or {}).items():
                for p in patterns or []:
                    compiled.append((kind, re.compile(str(p), re.I)))
            self._compiled = compiled
        return self._compiled

    def callouts_in(self, text: str) -> list[tuple[str, str]]:
        """(event type, what was said) for every callout in `text`."""
        found = []
        for kind, pattern in self._callouts():
            m = pattern.search(text or "")
            if m:
                found.append((kind, m.group(0)))
        return found

    def classify(self, said: list[tuple[str, str]], signals: list[str]) -> tuple[str, float]:
        """(event type, confidence) from what the commentary said and the other
        signals seen (the crowd, the commentator's voice jumping, a whistle,
        the scoreboard, on-screen text).

        A type needs TYPED_AT independent signals, the callout counting as one.
        The commentary alone is never a moment: commentators name a substitution
        in passing, or recall an earlier goal in a lull. Signals without a
        callout make a "big moment", never a guessed type."""
        others = len(signals)
        kinds = {k for k, _ in said}
        if kinds and others >= TYPED_AT - 1:
            kind = max(kinds, key=self.importance)
            return kind, min(1.0, (1 + others) / 3)
        if others >= 1:
            return "big_moment", min(1.0, others / 3)
        return "", 0.0


def weights_for(config: dict) -> dict:
    """The scoring weights a sport runs with: the user's gaming/sports profile
    weights when set, else the standard ones (the sport adds its bonus on top,
    as a game does)."""
    scoring = config.get("scoring") or {}
    profiles = scoring.get("profiles") or {}
    weights = dict((profiles.get("sports") or {}).get("weights") or scoring.get("weights") or STANDARD_WEIGHTS)
    weights.pop("game", None)
    return weights
