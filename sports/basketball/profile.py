"""Basketball's own rules, on top of the data in config/sports.yaml: which
basket a new score is, how much the game's situation makes it matter, the
plays the commentary names together, and the reactions."""

from dataclasses import dataclass, field

import sports
from sports.basketball.reactions import _peak as reactions_peak
from sports.core.profile import SportProfile

THREES = {"made_3", "corner_three", "deep_three"}
# Baskets worth 2 (or 3 for the any-distance kinds), never a free throw.
TWOS = {"made_2", "dunk", "alley_oop", "putback", "tip_in", "poster_dunk", "layup", "euro_step",
        "difficult_finish"}
ANY_DISTANCE = {"and_one", "fast_break_score", "buzzer_beater", "game_winner", "game_tying", "go_ahead",
                "clutch_shot", "step_back", "fadeaway", "pull_up", "isolation_score"}

LATE = 120.0             # the last two minutes of the 4th quarter or overtime
FINAL_SECONDS = 24.0     # ...and its last possession
CLOSE = 5                # a margin this small is a close game
BLOWOUT = 20             # ...this big, a blowout
GARBAGE = 13             # ...and this big, late on, the game is decided
BUZZER_AT = 0.6          # the buzzer curve's bar
EDGE_SENTENCE = 1.5      # a clip starts at its sentence's start, and ends at its end, this close to them


@dataclass
class BasketballProfile(SportProfile):
    # Each confirmed basket's score change, by the event it confirmed.
    _changes: dict = field(default_factory=dict)

    # ---- the reactions -----------------------------------------------------

    @property
    def reaction_types(self) -> tuple:
        return tuple(sports.spec(self.name).get("reaction_events") or ())

    @property
    def focus_reactions(self) -> bool:
        """Whether the Highlights choice asks for reactions (Fan reactions...)."""
        choice = (sports.spec(self.name).get("highlights_choices") or {}).get(
            (self.option or {}).get("highlights", "best")) or {}
        return choice.get("focus") == "reactions"

    def extra_moments(self, events, segments, *, curves, video_end, min_len, max_len):
        from sports.basketball import reactions

        # A basket neither the crowd nor the commentary dated: the shared
        # reading puts it half a minute before its new score shows (a soccer
        # score shows minutes after the goal). A basketball score shows
        # seconds after the basket, so it is dated to when the old score was
        # last read, its window moved with it.
        for e in events:
            change = self._changes.get(id(e))
            if (change is not None and change.last_old is not None and e.signals
                    and all(s.startswith("score ") for s in e.signals)):
                moved = change.last_old - e.t
                e.t = change.last_old
                e.start = round(max(0.0, e.start + moved), 2)
                e.end = round(min(max(video_end, e.end), e.end + moved), 2)
        # The buzzer marks the end of a period: a basket just before it.
        buzzer = curves.get("buzzer")
        for e in events:
            if reactions_peak(buzzer, e.t - 1, e.t + 3) >= BUZZER_AT and "buzzer" not in e.signals:
                e.signals.append("buzzer")
        settings = sports.spec(self.name).get("reactions") or {}
        found = list(getattr(self, "cutaways", None) or [])
        events = reactions.moments(self, events, found, curves=curves, video_end=video_end, min_len=min_len,
                                   max_len=max_len, react_within=float(settings.get("react_within", 18)),
                                   focus=self.focus_reactions)
        # The game clock, for the clip card ("Q4 0:32").
        board = getattr(self, "board", None)
        if board is not None:
            for e in events:
                e.when = e.when or board.when(e.t)
        # Between words: a clip that started or ended mid-sentence on the
        # PC's NBA game (7 of 10, mostly by under a second) starts and ends
        # with the commentator's sentence when it is that close.
        for e in events:
            e.start, e.end = speech_edges(segments, e.start, e.end, video_end)
        return events

    def clip_span(self, candidate, event) -> tuple[float, float]:
        """A basket's clip is its own window: the possession, the basket and
        the reaction. The scorer's window around it can hold two to four
        plays (a highlights package puts a basket every 10-15 s), and a clip
        posted on its own is one play."""
        if getattr(event, "confirmed", False):
            return event.start, event.end
        return super().clip_span(candidate, event)

    def guidance(self, kind: str = "clips", start: float | None = None, end: float | None = None) -> str:
        """What the scoring prompts are told a basketball game is (the
        shared one speaks of a pitch and goals)."""
        s = sports.spec(self.name)
        highlights = " ".join(str(s.get("highlights", "")).split())
        lines = [
            ("THIS IS BASKETBALL GAME FOOTAGE: a broadcast or recording of a game (pro, college, amateur "
             "or a rec league), with commentary when there is any. The clips are the plays on the court "
             "and the reactions to them."),
            f"- The moments that matter: {highlights}.",
            ("- The commentary names them as they happen ('for three', 'throws it down', 'and one', "
             "'blocked'), and the CROWD / WHISTLE / BUZZER / ON SCREEN events listed with the transcript "
             "mark them too. The arena erupting is the surest sign; it alone doesn't say what happened."),
            ("- A play is worth more late in a close game (the 4th quarter or overtime, a one-possession "
             "margin) than early or in a blowout."),
            ("- Include the possession that led to the play and the reaction after it (the crowd, the "
             "bench, courtside). 12-40 seconds is ideal."),
            ("- Score low: free-throw routines, timeouts, studio talk, adverts, ordinary half-court "
             "passing and replays of a play already clipped."),
        ]
        if kind == "rerank":
            lines = [lines[0], ("- Between clips that are otherwise as good, prefer the bigger moment: a "
                                "game winner or buzzer-beater, then a dunk, a block or a clutch three, then "
                                "an ordinary basket.")]
        return "\n".join(lines)

    def look(self, finalists: list, video_path, llm) -> int:
        """Who each reaction clip's shot shows, told by the local model
        (sports/basketball/look.py); fusion calls it on the finalists."""
        from sports.basketball import look

        return look.look(self, finalists, video_path, llm)

    # ---- what the commentary names together --------------------------------

    def classify(self, said, signals):
        """Basketball's combinations: a basket with the foul is an and-one, a
        dunk off a lob an alley-oop, a dunk over someone a poster; a shot
        that's called made isn't also a miss."""
        kinds = {k for k, _ in said}
        scored = kinds & (TWOS | THREES | ANY_DISTANCE)

        def renamed(old: set, new: str):
            return [(new, w) for k, w in said if k in old] + [(k, w) for k, w in said if k not in old]

        if "and_one" in kinds or (scored and {"foul", "shooting_foul"} & kinds):
            said = renamed(scored | {"foul", "shooting_foul", "and_one"}, "and_one")
        elif "alley_oop" in kinds and "dunk" in kinds:
            said = renamed({"alley_oop", "dunk"}, "alley_oop")
        elif "poster_dunk" in kinds and "dunk" in kinds:
            said = renamed({"poster_dunk", "dunk"}, "poster_dunk")
        if "miss" in {k for k, _ in said} and scored:
            said = [(k, w) for k, w in said if k != "miss"]
        return super().classify(said, signals)

    def importance(self, event_type: str) -> int:
        """A reaction is worth the most when the job asks for reactions."""
        if self.focus_reactions and event_type in self.reaction_types:
            return 100
        return super().importance(event_type)

    # ---- the score bug: which basket, and how much it mattered ------------

    def confirmed_type(self, change, event) -> str:
        """The basket a new score confirms: the commentary's name for it when
        its points agree (a dunk is 2, never 3), else by its points (a three,
        a basket, a free throw); then, from the game's situation, a game
        winner, buzzer-beater, tying or go-ahead basket, or a clutch shot,
        when that is worth more."""
        points = int(getattr(change, "points", 0) or 0)
        kind = event.type if event is not None else ""
        fits = (kind in ANY_DISTANCE or (kind in THREES and points == 3) or (kind in TWOS and points == 2)
                or (kind == "free_throw" and points == 1))
        if not fits:
            kind = {3: "made_3", 2: "made_2", 1: "free_throw"}.get(points, "made_2")
        board = getattr(self, "board", None)
        if event is not None and board is not None and points:
            self._changes[id(event)] = change
            event.when = board.when(event.t)
            situation, why = self._situation(board, change, event)
            if situation and self.importance(situation) > self.importance(kind):
                kind = situation
            if why:
                event.context = why
        return kind

    def _situation(self, board, change, event) -> tuple[str, str]:
        """(a situational kind for this basket or "", the situation in words)."""
        period = board.period_number(event.t)
        left = board.clock_at(event.t)
        last = board.last_period()
        side = change.side
        before = change.before[side] - change.before[1 - side]
        after = change.after[side] - change.after[1 - side]
        late = period is not None and period >= last and left is not None and left <= LATE
        is_last_basket = change is board.changes[-1] if board.changes else False
        buzzer = reactions_peak((getattr(self, "curves", None) or {}).get("buzzer"), event.t - 1, event.t + 3) >= BUZZER_AT
        words = []
        if after == 0:
            words.append("ties it")
        elif before <= 0 < after:
            words.append("takes the lead")
        if left is not None and period is not None:
            words.append(f"{int(left) // 60}:{int(left) % 60:02d} left")
        kind = ""
        if late and is_last_basket and before <= 0 < after and left <= 10:
            kind = "game_winner"
        elif left is not None and change.points >= 2 and (left <= 1.0 or (buzzer and left <= 3.0)):
            # The clock is read every few seconds and the basket dated by the
            # crowd, so with the buzzer heard a few seconds' doubt is allowed.
            kind = "buzzer_beater"
        elif late and after == 0 and left <= 60:
            kind = "game_tying"
        elif late and before <= 0 < after:
            kind = "go_ahead"
        elif late and abs(before) <= CLOSE and change.points >= 2:
            kind = "clutch_shot"
        return kind, ", ".join(words)

    def context_weight(self, event) -> float:
        """How much the game's situation lifts or lowers this moment: the
        last minutes of a close 4th quarter or overtime most, a blowout
        least. 1.0 without a read scoreboard (gym or phone footage)."""
        board = getattr(self, "board", None)
        if board is None or not getattr(board, "readings", None):
            return 1.0
        period = board.period_number(event.t)
        left = board.clock_at(event.t)
        change = self._changes.get(id(event))
        score = change.before if change is not None else board.score_before(event.t)
        margin = abs(score[0] - score[1]) if score else None
        last = board.last_period()
        weight = 1.0
        notes = []
        if period is not None and period > last:
            weight *= 1.25
            notes.append("overtime")
        elif period is not None and period == last:
            weight *= 1.15
        if margin is not None:
            late = period is not None and period >= last and left is not None and left <= LATE
            if margin >= BLOWOUT or (late and margin >= GARBAGE):
                weight *= 0.55
                notes.append(f"a {margin}-point game")
            elif margin >= GARBAGE:
                weight *= 0.8
            elif late and margin <= CLOSE:
                weight *= 1.5 if left <= FINAL_SECONDS else 1.35
                notes.append("late in a close game")
        if change is not None:
            side = change.side
            before = change.before[side] - change.before[1 - side]
            after = change.after[side] - change.after[1 - side]
            if after == 0 or before <= 0 < after:
                weight *= 1.15
        if left is not None and left <= 2 and change is not None:
            weight *= 1.2                         # beating the end of a period
        if notes and not event.context:
            event.context = ", ".join(notes)
        return max(0.5, min(1.8, weight))


def speech_edges(segments, start: float, end: float, video_end: float) -> tuple[float, float]:
    """(start, end) moved off the middle of what the commentator is saying:
    back to the start of the sentence under way at `start` when it began at
    most EDGE_SENTENCE before it, else to the start of the word under way;
    the end on to its sentence's end, or its word's, likewise. Never shorter."""
    def under_way(t: float):
        return next((sg for sg in segments if sg.start < t < sg.end), None)

    def word_at(sg, t: float):
        return next((w for w in (sg.words or []) if w["start"] < t < w["end"]), None) if sg is not None else None

    first, last, until = under_way(start), under_way(end), max(video_end, end)
    if first is not None and start - first.start <= EDGE_SENTENCE:
        start = first.start
    elif (w := word_at(first, start)) is not None:
        start = w["start"]
    if last is not None and last.end - end <= EDGE_SENTENCE:
        end = last.end
    elif (w := word_at(last, end)) is not None:
        end = w["end"]
    return round(max(0.0, start), 2), round(min(until, end), 2)
