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
# A basket by its score bug. On an NBA game the bug showed the new score 1.3-2.6 s
# after the ball went in, all ten times, and the ball went in 1.0 s before to
# 2.3 s after the old score was last read; the crowd's loudest moment put four
# of five baskets 4-12 s early (a playoff crowd roars through the possession).
BUG_LAG = 1.3            # the ball went in at least this long before the new score showed...
BUG_LAG_MOST = 2.6       # ...and at most this long
AFTER_OLD = 1.0          # with only the keyframes' readings: this long after the old score was last read,
BUG_GAP = 10.0           # ...when the two readings are at most this far apart (else the bug was hidden)
# The game's last basket in its last seconds: its clip runs on to the
# celebration, the first shot of people after it (on an NBA game 13 s after
# the dunk, once the clock ran out), and this long into it.
CELEBRATION = 4.0
CELEBRATION_WITHIN = 20.0


@dataclass
class BasketballProfile(SportProfile):
    # Each confirmed basket's score change, by the event it confirmed.
    _changes: dict = field(default_factory=dict)
    # The video's own title and description: who won, for which team is which (names.sides).
    video_text: tuple = ("", "")
    _sided: bool = False

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

        # A basket the scoreboard confirmed is dated by it, its window moved
        # with it: the shared reading dates it by the crowd, or half a minute
        # before its new score shows when nothing else does (a soccer score
        # shows minutes after the goal; a basketball score seconds after).
        # With the bug hidden between the two readings, a basket neither the
        # crowd nor the commentary dated is put where the old score was last read.
        board = getattr(self, "board", None)
        for e in events:
            change = self._changes.get(id(e))
            if change is None or change.last_old is None:
                continue
            if change.shown is not None and change.shown - change.last_old <= BUG_GAP:
                # Pinpointed between its keyframes (scoreboard.pinpoint): the
                # bug changed between the old score's last reading and the
                # new one's first, BUG_LAG to BUG_LAG_MOST after the ball.
                t = round((change.last_old + change.shown) / 2 - (BUG_LAG + BUG_LAG_MOST) / 2, 2)
            elif change.hi - change.last_old <= BUG_GAP:
                t = round(min(change.last_old + AFTER_OLD, change.hi - BUG_LAG), 2)
            elif e.signals and all(s.startswith("score ") for s in e.signals):
                t = change.last_old
            else:
                continue
            moved = t - e.t
            e.t = t
            e.start = round(max(0.0, e.start + moved), 2)
            e.end = round(min(max(video_end, e.end), e.end + moved), 2)
            if board is not None:
                e.when = board.when(shown_at(change, t))
        # The buzzer marks the end of a period: a basket just before it.
        buzzer = curves.get("buzzer")
        for e in events:
            if reactions_peak(buzzer, e.t - 1, e.t + 3) >= BUZZER_AT and "buzzer" not in e.signals:
                e.signals.append("buzzer")
        settings = sports.spec(self.name).get("reactions") or {}
        found = list(getattr(self, "cutaways", None) or [])
        events = reactions.moments(self, events, found, curves=curves, video_end=video_end, min_len=min_len,
                                   max_len=max_len, react_within=float(settings.get("react_within", 10)),
                                   focus=self.focus_reactions)
        # The game clock, for the clip card ("Q4 0:32").
        if board is not None:
            for e in events:
                e.when = e.when or board.when(e.t)
            # The game's last basket in its last seconds: on to the celebration.
            for e in events:
                change = self._changes.get(id(e))
                end = self._celebration(e, change, board) if change is not None else None
                if end is not None and end > e.end and end - e.start <= max_len:
                    e.end = round(min(max(video_end, e.end), end), 2)
        # Between words: a clip that started or ended mid-sentence on the
        # PC's NBA game (7 of 10, mostly by under a second) starts and ends
        # with the commentator's sentence when it is that close.
        for e in events:
            e.start, e.end = speech_edges(segments, e.start, e.end, video_end)
        return events

    def _celebration(self, e, change, board) -> float | None:
        """Where the clip of the game's last basket ends when it came in the
        last seconds of the game: CELEBRATION into the first shot of people
        after the clock ran out (after the basket, when that wasn't read), at
        most CELEBRATION_WITHIN after the basket. None for any other basket,
        and when no such shot was seen."""
        if not board.changes or change is not board.changes[-1]:
            return None
        at = shown_at(change, e.t)
        period, left = board.period_number(at), board.clock_at(at)
        if period is None or period < board.last_period() or left is None or left > FINAL_SECONDS:
            return None
        out = next((r.t for r in board.readings if r.t >= at and r.clock is not None and r.clock < 1), e.t)
        people = next((t for t, kind in (getattr(self, "shots", None) or [])
                       if max(out, e.t) < t <= e.t + CELEBRATION_WITHIN and kind == "people"), None)
        return None if people is None else people + CELEBRATION

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
        self._name_sides()
        points = int(getattr(change, "points", 0) or 0)
        kind = event.type if event is not None else ""
        fits = (kind in ANY_DISTANCE or (kind in THREES and points == 3) or (kind in TWOS and points == 2)
                or (kind == "free_throw" and points == 1))
        if not fits:
            kind = {3: "made_3", 2: "made_2", 1: "free_throw"}.get(points, "made_2")
        board = getattr(self, "board", None)
        if event is not None and board is not None and points:
            self._changes[id(event)] = change
            # The quarter and the clock where the bug changed: a basket dated
            # a few seconds early by the crowd sat in the play before, and on
            # an NBA game a 3rd-quarter dunk was labelled "Q2 0:35".
            at = shown_at(change, event.t)
            event.when = board.when(at)
            situation = self._situation(board, change, event, at)
            if situation and self.importance(situation) > self.importance(kind):
                kind = situation
            event.context = score_line(change)
        return kind

    def _name_sides(self) -> None:
        """The teams by name, once, when the bug's own letters weren't read
        (a logo, letters on their side): from the video's description of the
        result and the bug's last score (names.sides). On an NBA game titles
        given "the scorers 97, the other side 86" put the wrong team ahead,
        from a "timeout OKC" in the commentary. Nothing when either doesn't
        say: the titles then say no team leads."""
        board = getattr(self, "board", None)
        if self._sided or board is None:
            return
        self._sided = True
        if board.teams() is not None:
            return
        from sports.basketball import names

        title, description = self.video_text
        named = names.sides(title, description, board.final())
        if named is None:
            return
        for r in board.readings:
            r.teams = named
        for c in board.changes:
            c.team, c.other = named[c.side], named[1 - c.side]

    def title_rules(self) -> str:
        """What the title model is told about a basketball game's clips, on
        top of each clip's scoreboard note. On an NBA game a three was
        credited to the star the commentator named for the pass and the
        rebound, a step-back two was called a three, and a sideline report
        titled a clip whose play it never mentioned."""
        return "\n".join([
            "- Each clip is one play: its note says what the scoreboard read (the play, its points, the quarter "
            "and the clock, and the score with whose is whose when the scoreboard names the teams). Title the "
            "clip for that play, not for what else is said around it.",
            "- Name a player only as the one the commentary says scored this play (in \"X knocks down the "
            "three\", X scored). A player named for a pass, a rebound, a block or the defense didn't score. "
            "When the commentary doesn't say who scored, name no one.",
            "- Say a team leads, trails, wins or loses only as the note says it, and a basket is worth the "
            "points the note gives it.",
        ])

    def _situation(self, board, change, event, at: float) -> str:
        """A situational kind for this basket, or ""."""
        period = board.period_number(at)
        left = board.clock_at(at)
        last = board.last_period()
        side = change.side
        before = change.before[side] - change.before[1 - side]
        after = change.after[side] - change.after[1 - side]
        late = period is not None and period >= last and left is not None and left <= LATE
        is_last_basket = change is board.changes[-1] if board.changes else False
        buzzer = reactions_peak((getattr(self, "curves", None) or {}).get("buzzer"), event.t - 1, event.t + 3) >= BUZZER_AT
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
        return kind

    def context_weight(self, event) -> float:
        """How much the game's situation lifts or lowers this moment: the
        last minutes of a close 4th quarter or overtime most, a blowout
        least. 1.0 without a read scoreboard (gym or phone footage)."""
        board = getattr(self, "board", None)
        if board is None or not getattr(board, "readings", None):
            return 1.0
        change = self._changes.get(id(event))
        at = shown_at(change, event.t) if change is not None else event.t
        period = board.period_number(at)
        left = board.clock_at(at)
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


def shown_at(change, t: float) -> float:
    """When the scoreboard shows a basket's quarter and clock: its time t,
    kept between the last reading of the old score and the first of the new
    one, which the basket went in between."""
    lo = change.last_old if change.last_old is not None else change.lo
    return min(max(t, lo), change.hi)


def score_line(change) -> str:
    """The new score in words, whose is whose and who leads, for the clip's
    title: "SAS 52, OKC 53: OKC still lead by 1". The bug's "52-53" doesn't
    say whose 52 it is, and on an NBA game the titles called a three that
    made it 52-53 a tie and gave a run to the wrong team. "" when the teams
    aren't known: "the scorers 97, the other side 86" had the titles put the
    team the commentary named ahead, the wrong one."""
    if not change.team or not change.other:
        return ""
    side = change.side
    mine, theirs = change.after[side], change.after[1 - side]
    us, them = change.team, change.other
    before = change.before[side] - change.before[1 - side]
    line = f"{us} {mine}, {them} {theirs}: "
    if mine == theirs:
        return line + f"{us} tie it"
    if mine > theirs:
        return line + (f"{us} take the lead" if before <= 0 else f"{us} lead by {mine - theirs}")
    return line + f"{them} still lead by {theirs - mine}"


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
