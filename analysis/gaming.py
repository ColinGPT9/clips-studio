"""Scoring a gaming stream as a gaming stream.

The standard scorer judges talk: hooks, opinions, drama, quotable lines. On a
game stream the moment is usually something that happened in the game (a
kill streak, a boss going down, a goal) and the reaction to it, often with
little said at all. This module is the gaming profile's knowledge:

- which game is being played (the platform's category, else the title) and so
  which kind of game (config/gaming.yaml);
- the guidance that tells the model what a highlight is in it;
- the weights the fused score uses.

It only applies when a job scores as a gaming stream (core.modes.gaming_scoring):
Gaming / Reaction, or "Gaming stream" with Vertical Live. Pure Python: the
per-second signals live in analysis/chat_moments.py and fusion.
"""

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "config" / "gaming.yaml"

# The fused score's weights for a gaming stream. The game channel (evidence
# that something happened in the game: chat, the streamer's voice, and later
# game sounds and on-screen text) carries the most; the model's reading of
# the words still counts, less than for talk. Reaction (a person on screen,
# being emphasised) only means something where it is measured: Vertical Live.
GAMING_WEIGHTS = {"text": 0.20, "engagement": 0.05, "audio": 0.20, "visual": 0.10,
                  "reaction": 0.05, "game": 0.40}


@lru_cache(maxsize=1)
def knowledge() -> dict:
    import yaml

    return yaml.safe_load(KNOWLEDGE_PATH.read_text(encoding="utf-8")) or {}


def _phrase(term: str) -> re.Pattern:
    """A game name matched as whole words, ignoring case ("rust" is not in
    "trust", "ark" is not in "dark")."""
    return re.compile(r"(?<![\w])" + re.escape(term.lower()) + r"(?![\w])")


def genre_of(name: str) -> str | None:
    """The genre a game's name (or a title) points to, by the longest known
    name inside it; None when nothing matches."""
    text = (name or "").lower()
    best, best_len = None, 0
    for genre, names in (knowledge().get("games") or {}).items():
        for term in names:
            t = str(term)
            if len(t) > best_len and _phrase(t).search(text):
                best, best_len = genre, len(t)
    return best


# Twitch/Kick categories that are not a game: a stream's talking parts.
NOT_GAMES = {"just chatting", "irl", "music", "art", "talk shows & podcasts", "special events",
             "sports", "travel & outdoors", "asmr", "pools, hot tubs, and beaches", "makers & crafting",
             "food & drink", "science & technology", "software and game development"}


def _is_game(name: str) -> bool:
    return bool(name) and name.strip().lower() not in NOT_GAMES


@dataclass
class GamingProfile:
    genre: str = "generic"
    game: str = ""                      # the main game's name, when known
    games: list = field(default_factory=list)
    split_layout: bool = False          # Gaming / Reaction (reaction not measured)
    weights: dict = field(default_factory=dict)

    @property
    def spec(self) -> dict:
        return _genre_spec(self.genre)

    def game_at(self, start: float, end: float | None = None) -> tuple[str, str]:
        """(game, genre) being played over [start, end]: the platform's game
        for that part of the stream (a stream that goes from Just Chatting to
        Rust to Fortnite), else the stream's main game."""
        end = start if end is None else end
        mid = (start + end) / 2
        for g in self.games:
            name = str(g.get("name") or "").strip()
            if name and float(g.get("start") or 0) <= mid < float(g.get("end") or 0):
                return name, self._genre_for(name)
        return self.game, self.genre

    def _genre_for(self, name: str) -> str:
        if not _is_game(name):
            return "reaction"
        return genre_of(name) or ("generic" if name != self.game else self.genre)

    def genre_track(self, seconds: int) -> list[str]:
        """The kind of game at each second of the video, as game_at() has it."""
        track = [self.genre] * seconds
        known: dict[str, str] = {}
        for g in reversed(self.games):          # the first range listed wins, as in game_at
            name = str(g.get("name") or "").strip()
            if not name:
                continue
            if name not in known:
                known[name] = self._genre_for(name)
            lo = max(0, math.ceil(float(g.get("start") or 0)))
            hi = min(seconds, math.ceil(float(g.get("end") or 0)))
            track[lo:hi] = [known[name]] * max(0, hi - lo)
        return track

    def guidance(self, kind: str = "clips", start: float | None = None, end: float | None = None) -> str:
        """The block the scoring prompts carry (their {mode_guidance}), for
        the game played over [start, end] when given."""
        game, genre = (self.game, self.genre) if start is None else self.game_at(start, end)
        spec = _genre_spec(genre)
        label = spec.get("label", "game")
        highlights = " ".join(str(spec.get("highlights", "")).split())
        callouts = ", ".join(f'"{c}"' for c in (spec.get("callouts") or [])[:8])
        what = f"{game} ({label})" if game else f"a {label}"
        if genre == "reaction":
            what = f"a reaction / talking part of the stream ({game})" if game else "a reaction stream"
        lines = [
            f"THIS IS A GAMING / REACTION STREAM: {what}. Judge it the way a gaming clip "
            "channel would:",
            f"- The best clips are what happens in the game and the reaction to it: {highlights}.",
            f"- The streamer's callouts show when something just happened ({callouts}), and the "
            "GAME / CHAT events listed with the transcript mark it too.",
            "- A clear in-game moment is a strong clip even when little is said: a shout, a laugh, "
            "a scream or silence over a big play still counts. Do not mark a moment down just "
            "because there is little talking.",
            "- The payoff is the play and the reaction. Include a few seconds of setup before it "
            "and end once the reaction lands; 15-35 seconds for one moment is ideal.",
            "- Score low: menus, lobbies, queues and loading screens, reading out donations or "
            "subscriptions, long off-topic chatting, and dead time between plays.",
        ]
        if genre == "reaction":
            lines.append("- In a reaction, the payoff is the streamer's strongest reactions to "
                         "what they are watching (shock, laughter, a strong take), not the "
                         "watched video on its own.")
        if kind == "rerank":
            lines = [*lines[:2], "- Prefer the clip with a real in-game moment and a reaction "
                                 "over one that is only talk."]
        return "\n".join(lines)


def _genre_spec(genre: str) -> dict:
    genres = knowledge().get("genres") or {}
    return genres.get(genre) or genres.get("generic") or {}


def games_summary(games: list | None) -> str:
    """The main game: the one played longest in total. Just Chatting and the
    like only count when nothing else was played."""
    totals: dict[str, float] = {}
    for g in games or []:
        name = str(g.get("name") or "").strip()
        if name:
            span = max(0.0, float(g.get("end") or 0) - float(g.get("start") or 0))
            totals[name] = totals.get(name, 0.0) + span
    if not totals:
        return ""
    real = {k: v for k, v in totals.items() if _is_game(k)} or totals
    return max(real, key=real.get)


def profile_for(config: dict, games: list | None = None, title: str = "") -> GamingProfile:
    """The gaming profile for a job: the game (platform category first, then
    the title), its genre, and the weights, taking scoring.profiles.gaming
    from the settings when a user has set it."""
    from core import modes

    game = games_summary(games)
    genre = None
    if game:
        genre = genre_of(game)
    if genre is None:
        # The title of a stream often names the game or says "speedrun"; a
        # YouTube video's tags often name it too.
        hints = " ".join(str(g.get("hint") or "") for g in games or [])
        genre = genre_of(title) or genre_of(hints)
    split = modes.is_gaming(config)
    weights = dict(((config.get("scoring") or {}).get("profiles") or {}).get("gaming", {})
                   .get("weights") or GAMING_WEIGHTS)
    weights.setdefault("game", GAMING_WEIGHTS["game"])
    if split:
        # Nothing measures a person on screen in the split layout, so its
        # weight moves to the game instead of scoring a constant.
        weights["game"] = weights.get("game", 0) + weights.get("reaction", 0)
        weights["reaction"] = 0.0
    return GamingProfile(genre=genre or "generic", game=game, games=list(games or []),
                         split_layout=split, weights=weights)
