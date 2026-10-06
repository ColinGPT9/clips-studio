"""Basketball's own rules, on top of the data in config/sports.yaml: which
basket a new score is, how much the game's situation makes it matter, the
plays the commentary names together, and the reactions."""

import re
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
# The words said as a basket went in name what it was ("who goes in for the
# dunk"): the shared reading types it from the sentences around it, and on an
# NBA game one 23-second sentence held the last three plays, so its final dunk
# was typed by its points, a basket, and its clip went to the layup before it.
CALL_BEFORE = 3.0
CALL_AFTER = 3.0
# An and-one is called as the ball goes in ("and the foul!", "and one"), or
# just after: the shared reading makes a basket an and-one for a foul said
# anywhere in the sentences around it, and on an NBA game a three ("two big
# threes") was called an and-one for a foul said 10 s from it.
FOUL_AFTER = 5.0
FOUL_WORDS = {"and_one", "foul", "shooting_foul"}
# Any other kind the commentary names (a dunk, an alley-oop, a deep three) is
# said as the ball goes in, or just after, too: on an NBA game a layup ("a
# scoop to the hoop") was called a dunk for a "jam" said 12 s after it, about
# the next play.
SAID_AFTER = 5.0
# A basket by its points alone, as the scoreboard reads it.
POINTS_KINDS = {"made_2", "made_3", "free_throw"}
# Kinds the scoreboard's situation names, never the commentary alone.
SITUATIONS = {"game_winner", "buzzer_beater", "game_tying", "go_ahead", "clutch_shot"}
# The Highlights choices of a game's best moments, which a plain free throw isn't.
BEST = {"best", "plays_reactions"}


@dataclass
class BasketballProfile(SportProfile):
    # Each confirmed basket's score change, by the event it confirmed.
    _changes: dict = field(default_factory=dict)
    # The video's own title and description: who won, for which team is which (names.sides).
    video_text: tuple = ("", "")
    _sided: bool = False
    # The bug's own team letters, as read ("GSW", "DAL"), before they were named.
    _letters: tuple = ()
    # The confirmed baskets, in time order, once dated; and the players the
    # commentary names (commentary.Names), for who scored each.
    _baskets: list = field(default_factory=list)
    names: object = None

    @property
    def one_play_per_clip(self) -> bool:
        """Each clip is one play: a highlights package puts a basket every
        10-15 s, and two baskets are two clips however close they come."""
        return True

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
                # Not found between them: just after the old score, or, with
                # the keyframes far apart, the middle of where the ball could
                # have gone in, as a pinpointed basket is dated. On an NBA game
                # keyframes 8.4 s apart put a three 2.5 s early, and its clip
                # ended as the bug showed the new score.
                middle = (change.last_old + change.hi) / 2 - (BUG_LAG + BUG_LAG_MOST) / 2
                t = round(min(max(change.last_old + AFTER_OLD, middle), change.hi - BUG_LAG), 2)
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
        self._baskets = sorted((e for e in events if id(e) in self._changes), key=lambda e: e.t)
        if self._baskets:
            self._said(segments, video_end=video_end, max_len=max_len)
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
        # A plain free throw is none of a game's best moments: one that ties
        # the game, puts a team ahead or wins it late is typed so, and one the
        # person listed stays. On a 79-minute NBA game two of the ten clips
        # were single free throws ("Lakers Still Trail by Three").
        if (self.option or {}).get("highlights", "best") in BEST:
            events = [e for e in events if e.type != "free_throw" or "from your match events" in e.signals]
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
        # No such shot seen (the players celebrating on the court read as a
        # court shot): the game is over, and what follows is the celebration.
        return e.t + CELEBRATION_WITHIN if people is None else people + CELEBRATION

    def _said(self, segments, *, video_end: float, max_len: float) -> None:
        """What the commentary says at each confirmed basket: what kind it was
        (_called) and who scored it (commentary.scorer), each from the words
        said between the baskets either side of it."""
        from sports.basketball import commentary

        self.names = commentary.Names(segments, self._known_names(), teams=self._team_names())
        for i, e in enumerate(self._baskets):
            change = self._changes[id(e)]
            lo = self._baskets[i - 1].t + 1 if i else None
            hi = self._baskets[i + 1].t - 1 if i + 1 < len(self._baskets) else None
            self._unsaid(e, change, segments, lo, hi, video_end=video_end, max_len=max_len)
            self._called(e, change, segments, lo, hi, video_end=video_end, max_len=max_len)
            e.player = commentary.scorer(segments, e.t, int(change.points or 0), self.names, lo, hi) or e.player

    def _called(self, e, change, segments, lo, hi, *, video_end: float, max_len: float) -> None:
        """A basket typed by the words said as it went in, when they name a
        kind its points fit that is worth more than its type ("for the dunk":
        a dunk), its window grown or shrunk to that kind's."""
        from sports.basketball import commentary

        a = e.t - CALL_BEFORE if lo is None else max(e.t - CALL_BEFORE, lo)
        b = e.t + CALL_AFTER if hi is None else min(e.t + CALL_AFTER, hi)
        points = int(getattr(change, "points", 0) or 0)
        said = [(k, w) for k, w in self.callouts_in(" ".join(w.text for w in commentary.words(segments, a, b)))
                if _fits(k, points) and k not in SITUATIONS]
        if not said:
            return
        kind, word = max(said, key=lambda kw: self.importance(kw[0]))
        if self.importance(kind) <= e.importance:
            return
        self._typed(e, kind, video_end=video_end, max_len=max_len)
        if f'said "{word}"' not in e.signals:
            e.signals.append(f'said "{word}"')

    def _unsaid(self, e, change, segments, lo, hi, *, video_end: float, max_len: float) -> None:
        """A basket the commentary typed keeps its type only when the words
        for it were said as it went in: an and-one's foul (FOUL_AFTER), any
        other kind's own words, a dunk's "throws it down" (SAID_AFTER). Else
        it is the basket its points make it, as the scoreboard read it (a
        three, a basket), or the game's situation when that is worth more.
        A kind the person listed (sports/core/detect.py) stands as listed."""
        if e.type in POINTS_KINDS or e.type in SITUATIONS or "from your match events" in e.signals:
            return
        from sports.basketball import commentary

        foul = e.type == "and_one"
        after = FOUL_AFTER if foul else SAID_AFTER
        a = e.t - CALL_BEFORE if lo is None else max(e.t - CALL_BEFORE, lo)
        b = e.t + after if hi is None else min(e.t + after, hi)
        said = {k for k, _w in self.callouts_in(" ".join(w.text for w in commentary.words(segments, a, b)))}
        if said & (FOUL_WORDS if foul else {e.type}):
            return
        points = int(getattr(change, "points", 0) or 0)
        kind = {3: "made_3", 2: "made_2", 1: "free_throw"}.get(points, "made_2")
        board = getattr(self, "board", None)
        if board is not None:
            situation = self._situation(board, change, e, shown_at(change, e.t))
            if situation and self.importance(situation) > self.importance(kind):
                kind = situation
        self._typed(e, kind, video_end=video_end, max_len=max_len)

    def _typed(self, e, kind: str, *, video_end: float, max_len: float) -> None:
        """`e` typed `kind`, its window grown or shrunk to that kind's."""
        (pre, post), (new_pre, new_post) = self.window_of(e.type), self.window_of(kind)
        e.type, e.importance = kind, self.importance(kind)
        e.start = round(max(0.0, e.start - (new_pre - pre)), 2)
        e.end = round(min(max(video_end, e.end), e.end + (new_post - post)), 2)
        if e.end - e.start > max_len:
            e.start = round(e.end - max_len, 2)

    def _known_names(self) -> str:
        """The names the video's own title and description spell (names.hint)."""
        from sports.basketball import names

        return names.hint(*self.video_text)

    def _team_names(self) -> tuple:
        """Every name the two teams go by here: the scoreboard's, the
        description's result line's ("San Antonio Spurs"), the title's
        ("SPURS at THUNDER") and the job's."""
        from sports.basketball import names

        board = getattr(self, "board", None)
        teams = set(board.teams() or ()) if board is not None else set()
        for c in board.changes if board is not None else []:
            teams |= {c.team, c.other} - {""}
        said = names.result(self.video_text[1])
        if said is not None:
            teams |= {said[0], said[1]}
        # The teams the title and description name, for a bug with logos:
        # without them "Warriors" read as a player's name, and every title
        # naming a team as one naming a player who didn't score. An NBA
        # team's city and short name too ("Golden State", "Dubs"), and the
        # letters the bug gave it.
        teams |= set(names.teams(*self.video_text))
        for city, nick, codes, short in names.nba(*self.video_text, self._letters):
            teams |= {f"{city} {nick}", nick, *short, *(c for c in codes if c in self._letters)}
        teams |= {t.strip() for t in re.split(r",|\bvs?\b\.?|\bversus\b|/", str((self.option or {}).get("teams") or ""))
                  if t.strip()}
        return tuple(sorted(t for t in teams if t))

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
        if not _fits(kind, points):
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
        """The teams by name, once: the bug's own letters as the NBA teams the
        video names ("GSW" as the Warriors, names.nba_letters), else, and
        when the letters weren't read (a logo, letters on their side), from
        the video's description of the result and the bug's last score
        (names.sides). On an NBA game titles given "the scorers 97, the other
        side 86" put the wrong team ahead, from a "timeout OKC" in the
        commentary, and on another "GSW" and "DAL" made titles like "GSW Tie
        It Up!". Nothing when neither says: the titles then say no team
        leads, or name the teams by the bug's letters."""
        board = getattr(self, "board", None)
        if self._sided or board is None:
            return
        self._sided = True
        from sports.basketball import names

        title, description = self.video_text
        letters = board.teams()
        self._letters = tuple(letters or ())
        named = names.nba_letters(letters, title, description) if letters is not None else None
        if named is None:
            named = names.sides(title, description, board.final())
        if named is None:
            return
        if letters is None:
            for r in board.readings:
                r.teams = named
        # A bug's readings keep the letters it shows, which a listed event's
        # team is matched against ("dunk LAL"); its baskets get the names.
        for c in board.changes:
            c.team, c.other = named[c.side], named[1 - c.side]

    def title_rules(self) -> str:
        """What the title model is told about a basketball game's clips, on
        top of each clip's scoreboard note. On an NBA game a three was
        credited to the star the commentator named for the pass and the
        rebound, a step-back two was called a three, and a sideline report
        titled a clip whose play it never mentioned."""
        return "\n".join([
            ("- Each clip is one play: its note says what the scoreboard read (the play, its points, the quarter "
             "and the clock, and the score with whose is whose when the scoreboard names the teams). Title the "
             "clip for that play, not for what else is said around it."),
            ("- Name a player only as the note's scorer, the one the commentary says scored this play. A player "
             "named for a pass, a rebound, a block or the defense didn't score. When the note names no scorer, "
             "name no one."),
            ("- Say a team leads, trails, wins or loses only as the note says it, and a basket is worth the "
             "points the note gives it. The shot went in: never call it a miss."),
        ])

    def play_of(self, candidate):
        """The basket a clip shows, as titles.Play (its points, who scored it
        and who leads after it), or None for a clip of anything else."""
        from sports.basketball import titles

        t = (candidate.subscores or {}).get("sport_t")
        e = next((e for e in self._baskets if round(e.t, 1) == t), None) if t is not None else None
        change = self._changes.get(id(e)) if e is not None else None
        points = int(getattr(change, "points", 0) or 0)
        if change is None or not points:
            return None
        board = getattr(self, "board", None)
        side = change.side
        mine, theirs = change.after[side], change.after[1 - side]
        # In the game's last minutes, by the team that went on to win: it put
        # the game away; the game's last basket sealed it.
        late_win = sealed = False
        final = board.final() if board is not None else None
        if final and mine > theirs and final[side] > final[1 - side]:
            at = shown_at(change, e.t)
            period, left = board.period_number(at), board.clock_at(at)
            late_win = period is not None and period >= board.last_period() and left is not None and left <= LATE
            sealed = late_win and change is board.changes[-1]
        team_names = self._team_names()
        from sports.basketball import names

        nba = names.nba(*self.video_text, self._letters)

        def aliases(team: str) -> tuple:
            """ "Spurs", and the names that end with it ("San Antonio Spurs") and their
            city; an NBA team's short names and the letters the bug gave it too."""
            if not team:
                return ()
            full = [t for t in team_names if t != team and t.lower().endswith(team.lower())]
            cities = [t[:-len(team)].strip() for t in full]
            row = next((r for r in nba if r[1] == team), None)
            more = [*row[3], *(c for c in row[2] if c in self._letters)] if row is not None else []
            return tuple(dict.fromkeys([team, *full, *(c for c in cities if c), *more]))

        overtime = board is not None and any(r.period is not None and r.period > board.last_period()
                                             for r in board.readings)
        return titles.Play(points=points, kind=e.type, shot=titles.shot_words(e.type, points),
                           team=change.team or "", other=change.other or "", mine=mine, theirs=theirs,
                           before=change.before[side] - change.before[1 - side], when=e.when or "",
                           scorer=e.player or "", sealed=sealed, late_win=late_win, aliases=aliases(change.team or ""),
                           other_aliases=aliases(change.other or ""), overtime=overtime)

    def check_titles(self, candidates: list, metas: list, rewrite) -> list:
        """Each basket clip's title and description held to its play, and
        written again when they get it wrong (sports/basketball/titles.py)."""
        from sports.basketball import titles

        return titles.check(self, candidates, metas, rewrite)

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


def _fits(kind: str, points: int) -> bool:
    """Whether a kind of basket is worth `points`: a dunk is 2, never 3."""
    return (kind in ANY_DISTANCE or (kind in THREES and points == 3) or (kind in TWOS and points == 2)
            or (kind == "free_throw" and points == 1))


def shown_at(change, t: float) -> float:
    """When the scoreboard shows a basket's quarter and clock: its time t,
    kept between the last reading of the old score and the first of the new
    one, which the basket went in between."""
    lo = change.last_old if change.last_old is not None else change.lo
    return min(max(t, lo), change.hi)


def score_line(change) -> str:
    """The new score in words, whose is whose and who leads, for the clip's
    title: "SAS 52, OKC 53: SAS still trail by 1". The bug's "52-53" doesn't
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
    # From the scorers' side: told "OKC 103, SAS 109: SAS still lead by 6" of
    # OKC's basket, the titles said the Thunder surged ahead and tied it.
    return line + f"{us} still trail by {theirs - mine}"


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
