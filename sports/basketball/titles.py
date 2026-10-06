"""A basketball clip's title and description, held to its play: what the
scoreboard read (its points, who leads after it) and who the commentary
says scored it.

On an NBA game the titles got a fact wrong on 8 of 10 clips with the
scoreboard's note and the rules in their prompt: "Thunder Surge Ahead!"
and "tie the game" for a basket that left them 6 behind, a description
saying the shot missed, a three credited to the passer and another to a
name heard once, a 5-point lead called 3, and a clip the model skipped
titled with raw commentary. Each clip is checked here; one that says
otherwise is written again, told what it got wrong, and one that still
does is written from the play itself."""

import re
from dataclasses import dataclass, replace

from sports.basketball.commentary import _WORD, NOT_NAMES, SCORED, bare

PERIODS = {"Q1": "1st quarter", "Q2": "2nd quarter", "Q3": "3rd quarter", "Q4": "4th quarter",
           "H1": "1st half", "H2": "2nd half", "OT": "overtime"}
NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_N = r"(\d{1,2}|" + "|".join(NUMBERS) + r")"

LEAD = r"(?:leads?|leading|ahead|in\s+front|on\s+top)"
# Words between a team and "lead" that make it the other side's: "the Thunder cut the lead".
CUT = set("cut cuts cutting trim trims trimming narrow narrows narrowing shrink shrinks shrinking chip chips "
          "chipping slice slices eat eats into reduce reduces reducing whittle whittles erase erases erasing "
          "end ends ending snap snaps wipe wipes overturn overturns stop stops halt halts flip flips "
          "lose loses losing lost blow blows blowing blew give gives giving gave surrender surrenders "
          "surrendered relinquish relinquishes relinquished squander squanders squandered".split())
# Words between a team and "lead" that start another clause ("the Warriors answer but the Mavericks lead").
JOINS = set("but and as while yet though although after before until when since because or nor than".split())
# "Mavericks Up 2!": a lead, as a headline gives it.
UP = r"up\s+(?:by\s+)?(?:\d{1,2}|" + "|".join(NUMBERS) + r"|big|double[- ](?:digits?|figures))"
TRAIL = r"(?:trails?|trailing|behind|down\s+(?:by\s+)?" + _N + r")"
TIE = re.compile(r"\b(?:ties?|tied|tying|knots?|knotted|evens?\s+(?:it|the\s+score|things)|all\s+square"
                 r"|levels?\s+(?:it|the\s+score)|squares?\s+(?:it|things)|squared)\b", re.I)
UNTIE = re.compile(r"\b(?:break(?:s|ing)?\s+(?:the|a)\s+tie|tie[- ]?break(?:er|ing)?|untie)", re.I)
GO_AHEAD = re.compile(r"\b(?:(?:take[sn]?|took|taking|grab(?:s|bed|bing)?|seiz(?:e|es|ed|ing)|snatch(?:es|ed|ing)?"
                      r"|claim(?:s|ed|ing)?)\s+(?:the\s+|a\s+|an\s+)?(?:(?:early|narrow|slim|first|late|brief)\s+)?lead"
                      r"|go(?:es)?[- ]ahead|went\s+ahead|(?:move[sd]?|pull(?:s|ed)?|surge[sd]?|jump(?:s|ed)?"
                      r"|edge[sd]?|nose[sd]?|inch(?:es|ed)?)\s+(?:ahead|in\s+front)|lead\s+change"
                      r"|(?:retake[sn]?|regain(?:s|ed)?)\s+the\s+lead)\b", re.I)
# The game won by this basket: only a game winner, or the last basket of a game the scorers won...
WIN = re.compile(r"\b(?:game[- ]winn(?:er|ing)|winn(?:er|ing)\s+(?:shot|basket|bucket|three|dunk|layup|free\s+throws?)"
                 r"|wins?\s+(?:it|the\s+game|game)|won\s+(?:it|the\s+game)|for\s+the\s+(?:win|victory)"
                 r"|(?:gets?|getting|got|grabs?|grabbed|earns?|earned|claims?|claimed)\s+(?:the|a|an|their|its)\s+"
                 r"(?:[\w'’-]+\s+){0,2}?(?:win|victory|w))\b", re.I)
# ...and put away: also a basket in the last two minutes by the team that went on to win.
SEAL = re.compile(r"\b(?:seal(?:s|ed|ing)?\s+(?:it|the\s+(?:win|game|deal|victory)|game)|clinch(?:es|ed|ing)?"
                  r"|ices?\s+(?:it|the\s+game)|iced|puts?\s+(?:it|the\s+game)\s+away|dagger"
                  r"|(?:secur|lock|nail|wrap|seal|ic)\w*\s+(?:up\s+|down\s+)?(?:(?:the|a|an|their|its)\s+)?"
                  r"(?:[\w'’-]+\s+){0,2}?(?:win|victory|w)"
                  r"|clos(?:e|es|ed|ing)\s+(?:it|the\s+game|things|the\s+win|the\s+victory)\s+out"
                  r"|clos(?:e|es|ed|ing)\s+out\s+(?:the\s+)?(?:game|win|victory|contest|it)"
                  r"|out\s+of\s+reach|on\s+ice|lock(?:s|ed|ing)?\s+(?:it|the\s+game)\s+up"
                  r"|finish(?:es|ed|ing)?\s+(?:them|it|the\s+(?:job|game))\s+off|victor(?:y|ies|ious))\b", re.I)
# Words that put the scorers behind after the basket ("Harper's Three Keeps
# Hope Alive" on an NBA game, for a team 12 up), coming back from behind,
# or pulling away from a lead they didn't have.
BEHIND = re.compile(r"\b(?:(?:keeps?|keeping|kept)\s+(?:them|it|things|the\s+[\w'’-]+)\s+alive|hopes?\s+alive"
                    r"|stays?\s+alive|staying\s+alive|signs?\s+of\s+life|won['’]?t\s+go\s+away"
                    r"|within\s+(?:striking\s+distance|reach)|chip(?:s|ping|ped)?\s+away"
                    r"|cut(?:s|ting)?\s+into\s+(?:the|their|its|a)\s+(?:lead|deficit|gap|margin)"
                    r"|(?:clos(?:e|es|ing|ed)|narrow(?:s|ing|ed)?)\s+the\s+gap"
                    r"|(?:pull(?:s|ing|ed)?|inch(?:es|ing|ed)?|creep(?:s|ing)?|crept|gets?|getting|edg(?:e|es|ing|ed)"
                    r"|mov(?:e|es|ing|ed))\s+closer|mak(?:e|es|ing)\s+it\s+(?:a\s+game|interesting))\b", re.I)
# Words that have the scorers come off worse on their own basket: on a
# 79-minute NBA game "Warriors Struggle!" and "the Warriors continue to fall
# behind", for a Warriors basket that cut the Lakers' lead from 6 to 4.
SLUMP = (r"(?:struggl(?:e|es|ed|ing)|falter(?:s|ed|ing)?|stumbl(?:e|es|ed|ing)|sputter(?:s|ed|ing)?"
         r"|slump(?:s|ed|ing)?|collaps(?:e|es|ed|ing)|crumbl(?:e|es|ed|ing)"
         r"|(?:fall(?:s|ing)?|fell|drop(?:s|ped|ping)?|slip(?:s|ped|ping)?)\s+(?:further\s+|farther\s+)?behind"
         r"|(?:los(?:e|es|ing)|lost)\s+(?:ground|touch|steam))")
CAME_BACK = re.compile(r"\b(?:come[- ]?backs?|rall(?:y|ies|ying|ied)"
                       r"|(?:fight(?:s|ing)?|fought|claw(?:s|ing|ed)?|battl(?:e|es|ing|ed)|storm(?:s|ing|ed)?"
                       r"|roar(?:s|ing|ed)?)\s+back)\b", re.I)
EXTEND = re.compile(r"\b(?:(?:extend(?:s|ing|ed)?|stretch(?:es|ing|ed)?|pad(?:s|ding|ded)?|widen(?:s|ing|ed)?"
                    r"|increas(?:e|es|ing|ed)|build(?:s|ing)?\s+on|built\s+on|add(?:s|ing|ed)?\s+to|grow(?:s|ing)?)"
                    r"\s+(?:the|their|its|his|a)\s+(?:[\w'’-]+\s+)?(?:lead|advantage|cushion|margin)"
                    r"|pull(?:s|ing|ed)?\s+away|lead\s+(?:grows|grew|swells|balloons))\b", re.I)
MISS = re.compile(r"\b(?:miss(?:es|ed)?|bricks?|bricked|air\s?balls?|airballed|no\s+good|rims?\s+out|rimmed\s+out"
                  r"|(?:falls?|fell|comes?|came)\s+(?:up\s+)?short|off\s+the\s+(?:rim|mark))\b", re.I)
NOT = re.compile(r"\b(?:doesn't|don't|didn't|never|can't|cannot|won't|not|no)\s+(?:\w+\s+)?$", re.I)
WENT_IN = re.compile(r"\b(?:and\s+in|(?:falls?|fell|drops?|dropped|goes|went|rattles?|rattled|rolls?|rolled)\s+in"
                     r"|counts?|good)\b", re.I)
THREE = re.compile(r"\b(?:three[- ]?pointers?|triples?|treys?|downtown|3[- ]?pointers?|3pt"
                   r"|from\s+(?:deep|three|beyond\s+the\s+arc|the\s+logo)"
                   r"|(?:a|the|that|his|her|another|corner|deep|step[- ]?back|pull[- ]?up|logo)\s+(?:three|3)\b"
                   r"(?![- ]?(?:point\s+(?:lead|game|play|deficit|margin|cushion|edge)|seconds?|minutes?|times?|fouls?"
                   r"|games?|straight|quarters?|\d)))", re.I)
TWO = re.compile(r"\b(?:dunks?|dunked|slam(?:s|med)?|jams?|jammed|throws?\s+it\s+down|threw\s+it\s+down|lay[- ]?ups?"
                 r"|lays?\s+it\s+(?:in|up)|laid\s+it\s+(?:in|up)|alley[- ]oops?|putbacks?|tip[- ]ins?|finger\s+rolls?"
                 r"|floaters?)\b", re.I)
SCORE_WORDS = re.compile(r"\b(?:three|dunk|slam|jam|lay[- ]?up|bucket|basket|scores?|scored|points?|jumper|triple|deep"
                         r"|splash|range|bang|finish(?:es)?|buries|drills|nails|hits|knocks|drains|sinks|cans|puts?"
                         r"|lays|laid|and[- ]one|money|floater|it\s+in|tip|counts?)\b", re.I)
OTHER_PLAY = re.compile(r"\b(?:blocks?|blocked|rejects?|rejected|rejection|swats?|swatted|steals?|stole|stolen"
                        r"|picks?\s+off|rebounds?|boards?|turnovers?|fouls?|fouled|charges?|travels?|pass(?:es)?"
                        r"|assists?|dimes?|screens?|rolls?)\b", re.I)
MARGINS = [re.compile(p, re.I) for p in (
    _N + r"[- ]point\s+(?:lead|game|advantage|cushion|deficit|margin|edge|hole|gap)\b",
    r"\blead\s+(?:to|of|by|at)\s+" + _N + r"\b",
    r"\b(?:up|ahead)\s+(?:by\s+)?" + _N + r"\b(?!\s*[-–])",
    r"\b(?:cut|cuts|trim|trims|narrow|narrows|shrink|shrinks)\s+(?:it|the\s+lead|the\s+deficit|the\s+gap)\s+to\s+"
    + _N + r"\b",
    r"\bwithin\s+" + _N + r"\b",
)]
SCORE_LINE = re.compile(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b")
# A lead grown or cut "by" so many points: by the basket's own points ("increasing
# their lead by four points" for a three that took it from 1 to 4).
BY_CHANGE = re.compile(r"\b(?:extend|stretch|pad|widen|increas|build|grow|push|bump|boost|cut|trim|narrow|shrink"
                       r"|reduc|slash|chop)\w*\s+(?:it|(?:the|their|its|his|her|a|an)\s+(?:[\w'’-]+\s+)?"
                       r"(?:lead|advantage|cushion|margin|deficit|gap|edge|hole))\s+by\s+" + _N + r"\b", re.I)
# The start of a game, which the 3rd quarter on isn't ("Mavericks Take the Early
# Lead!", "start strong", with 5:19 left in the 3rd).
EARLY = re.compile(r"\b(?:early\s+(?:lead|advantage|edge|cushion|run|statement|momentum|tone|spark|surge|burst|push"
                   r"|stages|going|minutes)|(?:start(?:s|ing|ed)?|get(?:s|ting)?\s+off)\s+(?:strong|fast|hot|quick(?:ly)?)"
                   r"|(?:strong|fast|hot|quick|flying)\s+start|set(?:s|ting)?\s+the\s+tone|out\s+of\s+the\s+gate"
                   r"|opening\s+(?:minutes|basket|bucket|score|points|salvo|stretch))\b", re.I)
# The quarter, the half, overtime and the time left, as a title or description gives them.
ORDINALS = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4,
            "final": 4, "last": 4}
QUARTER = re.compile(r"\b(1st|first|2nd|second|3rd|third|4th|fourth|final|last)\s+(?:quarter|qtr|frame)\b", re.I)
HALF = re.compile(r"\b(1st|first|2nd|second)\s+half\b", re.I)
HALFTIME = re.compile(r"\b(?:(?:before|at|end\s+of|heading\s+into|going\s+into)\s+(?:the\s+)?(?:half|halftime|half-time)"
                      r"|halftime\s+(?:buzzer|horn|lead))\b", re.I)
OVERTIME = re.compile(r"\b(?:[Oo]vertime|OVERTIME|[Ee]xtra\s+(?:period|session)|OT)\b")
SHOT_CLOCK = r"(?!\s+(?:on|of|in)\s+the\s+shot[- ]?clock)"       # time on the shot clock is another clock
SECONDS_LEFT = re.compile(r"\b(\d{1,2}(?:\.\d)?|" + "|".join(NUMBERS) + r")\s+(?:seconds?|secs?|ticks?)\s+"
                          r"(?:left|remaining|to\s+go|to\s+play|on\s+the\s+clock)\b" + SHOT_CLOCK, re.I)
MINUTES_LEFT = re.compile(r"\b(\d{1,2}|" + "|".join(NUMBERS) + r"|a)\s+minutes?\s+(?:left|remaining|to\s+go|to\s+play"
                          r"|on\s+the\s+clock)\b", re.I)
SECONDS_TO_GO = re.compile(r"\b(?:(?:just|only|mere|a\s+few|few|final|closing|dying|last|waning)\s+seconds"
                           r"|seconds\s+(?:left|remaining|to\s+go|to\s+play))\b" + SHOT_CLOCK, re.I)
MINUTES_TO_GO = re.compile(r"\b(?:(?:just|only|mere|a\s+few|few)\s+)?minutes\s+(?:left|remaining|to\s+go|to\s+play)\b",
                           re.I)
LAST_MINUTE = re.compile(r"\b(?:final|last|closing)\s+minute\b", re.I)
BUZZER = re.compile(r"\b(?:buzzer[- ]?beat(?:er|ing)|beats?\s+the\s+(?:buzzer|horn)|at\s+the\s+(?:buzzer|horn)"
                    r"|as\s+time\s+(?:expires|runs\s+out)|no\s+time\s+left)\b", re.I)
# Words before a name that are no first name ("guard Schroder", "Lakers star Reaves").
ROLES = set("""guard forward center centre big star superstar veteran rookie sharpshooter shooter captain legend
reserve sixth man sub coach then now later again also still even just only finally meanwhile
""".split())
# Clips written again in one call: the title writer's own batch (analysis/metadata.py),
# which numbers its clips from 0, so each "CLIP k" rule meets its own clip.
AT_ONCE = 8


@dataclass
class Play:
    """One basket, as the scoreboard read it and the commentary called it."""
    points: int
    kind: str = ""          # the moment's type (made_3, dunk, game_winner...)
    shot: str = ""          # what it was, in words ("three", "step-back three", "dunk")
    team: str = ""          # the scorers, as the scoreboard or the description names them; "" when unknown
    other: str = ""
    mine: int = 0           # the score after it, the scorers' first
    theirs: int = 0
    before: int = 0         # the scorers' margin before it (below 0: behind)
    when: str = ""          # "Q4 0:52"
    scorer: str = ""        # as the commentary says it; "" when it doesn't
    sealed: bool = False    # the game's last basket, in its last minutes, by the team that won
    late_win: bool = False  # in the last minutes, by the team that won, ahead after it
    aliases: tuple = ()     # every name the scorers go by ("Spurs", "San Antonio Spurs", "San Antonio")
    other_aliases: tuple = ()
    overtime: bool = False  # the game went to overtime (the bug read a period past the last)

    @property
    def after(self) -> int:
        return self.mine - self.theirs

    @property
    def took_lead(self) -> bool:
        return self.before <= 0 < self.after


def problems(play: Play, meta, names=None) -> list[str]:
    """What a clip's title and description get wrong about its play, each
    as the title model is told it. [] when nothing."""
    out: list[str] = []
    title = str(getattr(meta, "title", "") or "")
    description = str(getattr(meta, "description", "") or "")
    # The highlights post style's title card (video/post_style.py), when the job has one.
    card = " ".join(str(getattr(meta, k, "") or "") for k in ("headline", "subline")).strip()
    if not description.strip():
        out.append("Write a title and a description for it.")
    fields = [f for f in (title, description, card) if f]
    text = "\n".join(fields)
    if play.team and play.other:
        out += _state(play, text)
    if any(_missed(f) for f in fields):
        out.append("The shot went in (the score changed): don't call it a miss.")
    if play.points != 3 and THREE.search(text):
        out.append(f"It was a {play.points}-point {'free throw' if play.points == 1 else 'basket'}, not a three.")
    if play.points != 2 and TWO.search(text):
        out.append("It was a three, not a dunk or a layup." if play.points == 3
                   else "It was a free throw, not a dunk or a layup.")
    if ((WIN.search(text) and play.kind != "game_winner" and not play.sealed)
            or (SEAL.search(text) and play.kind != "game_winner" and not play.sealed and not play.late_win)):
        out.append("It didn't win or seal the game.")
    if OTHER_PLAY.search(title) and not SCORE_WORDS.search(title):
        out.append(f"Title it for the {play.shot or 'basket'}, not for the play before it.")
    if names is not None and _wrong_names(play, (title, card), description, names):
        out.append(f"The commentary says {play.scorer} scored it: name only {play.scorer}, or no one."
                   if play.scorer else "The commentary doesn't say who scored it: name no player.")
    elif names is not None and _added_name(play, (title, card), description, names):
        out.append(f"The commentary calls the scorer {play.scorer}: name the scorer only that way, "
                   "with no first name it doesn't give.")
    out += _time(play, text)
    if names is not None and not (play.team and play.other) and _team_named(text, names):
        out.append("The scoreboard doesn't say which team scored it: name no team.")
    return list(dict.fromkeys(out))


def _team_named(text: str, names) -> bool:
    """Whether `text` names one of the teams ("Warriors", "Golden State"):
    with the bug's logos and no result to tell its sides apart, nothing says
    which team scored or leads."""
    return any(bare(w).lower() in names.teams and bare(w).lower() not in NOT_NAMES for w in _WORD.findall(text))


def _state(play: Play, text: str) -> list[str]:
    """Who leads after the basket, and by how much, as the text says it."""
    us, them = play.aliases or (play.team,), play.other_aliases or (play.other,)
    after = play.after
    by = list(BY_CHANGE.finditer(text))
    changed = any(_number(m.group(1)) != play.points for m in by)
    slumped = _slumps(text, us, them)
    wrong = (
        (after <= 0 and _leads(text, us, erased=False))
        or (after >= 0 and _leads(text, them, erased=play.took_lead))
        or (after > 0 and _trails(text, us)) or (after < 0 and _trails(text, them))
        or (after != 0 and TIE.search(text) is not None and not UNTIE.search(text))
        or (not play.took_lead and GO_AHEAD.search(text) is not None)
        or (after > 0 and BEHIND.search(text) is not None)
        or (play.before > 0 and CAME_BACK.search(text) is not None)
        or (play.before <= 0 and EXTEND.search(text) is not None)
        or _wrong_margin(text, abs(after), skip=[m.span() for m in by])
        or _wrong_score(text, play)
        or changed
        or slumped
    )
    if not wrong:
        return []
    out = [f"After it {_score_words(play)}."]
    if changed:
        out.append(f"{_cap(_the(play.team))} were {_margin_words(play.before)} before it and "
                   f"{_margin_words(after)} after it: it was worth {play.points}.")
    if slumped:
        out.append(f"{_cap(_the(play.team))} scored it: don't say they struggle or fall behind.")
    return out


def _margin_words(margin: int) -> str:
    """ "up 4", "down 2", "level"."""
    return f"up {margin}" if margin > 0 else f"down {-margin}" if margin < 0 else "level"


def _number(word: str) -> int:
    word = word.lower()
    return int(float(word)) if word[:1].isdigit() else NUMBERS.get(word, 1 if word == "a" else -1)


def _score_words(play: Play) -> str:
    """ "the Thunder still trail 103-109: they didn't tie it or go ahead"."""
    us, them = _the(play.team), _the(play.other)
    if play.after == 0:
        return f"it's tied {play.mine}-{play.theirs}: no one leads"
    if play.after < 0:
        return (f"{us} still trail {play.mine}-{play.theirs}, by {-play.after}: "
                f"they didn't tie it or go ahead, and {them} still lead")
    if play.took_lead:
        return f"{us} lead {play.mine}-{play.theirs}, by {play.after}: they took the lead"
    return f"{us} lead {play.mine}-{play.theirs}, by {play.after}: they already led, and {them} trail"


def _leads(text: str, team: tuple, *, erased: bool) -> bool:
    """Whether `text` says `team` leads: "Spurs lead by 5", "the Spurs' lead",
    "Thunder Surge Ahead", "extending the Spurs' lead". With `erased`, a lead
    it says was ended ("erasing the Thunder's lead") is not one."""
    names = "|".join(re.escape(t) for t in team if t)
    if not names:
        return False
    for m in re.finditer(rf"\b(?:{names})(?:['’]s?)?((?:\s+[\w'’-]+){{0,3}}?)\s+(?:{LEAD}|{UP})\b", text, re.I):
        between = {w.lower() for w in m.group(1).split()}
        if between & (CUT | JOINS):
            continue
        if m.group(0).lower().rstrip().endswith(("lead", "leads", "leading")) and re.search(r"['’]s?\s", m.group(0)):
            # "the Spurs' lead": the lead is theirs, unless it was just ended.
            before = text[max(0, m.start() - 30):m.start()].lower().split()[-3:]
            if erased and set(before) & CUT:
                continue
        return True
    return False


def _slumps(text: str, team: tuple, other: tuple) -> bool:
    """Whether `text` has `team` struggling or falling behind ("Warriors
    Struggle!", "the Warriors continue to fall behind"), which a basket of
    theirs never shows. Not when the words between start another clause or
    name the other side ("the Warriors stun the struggling Lakers"), or end
    it ("the Warriors snap their slump", "Curry ends the Warriors' slump")."""
    names = "|".join(re.escape(t) for t in team if t)
    theirs = "|".join(re.escape(t) for t in other if t)
    if not names:
        return False
    for m in re.finditer(rf"\b(?:{names})(?:['’]s?)?((?:\s+[\w'’-]+){{0,3}}?)\s+{SLUMP}\b", text, re.I):
        between = {w.lower() for w in m.group(1).split()}
        if between & (CUT | JOINS) or (theirs and re.search(rf"\b(?:{theirs})\b", m.group(1), re.I)):
            continue
        if theirs and re.match(rf"\s+(?:the\s+)?(?:{theirs})\b", text[m.end():], re.I):
            continue
        if set(text[max(0, m.start() - 30):m.start()].lower().split()[-3:]) & CUT:
            continue
        return True
    return False


def _trails(text: str, team: tuple) -> bool:
    names = "|".join(re.escape(t) for t in team if t)
    return bool(names) and re.search(rf"\b(?:{names})(?:\s+(?:still|now))?\s+{TRAIL}\b", text, re.I) is not None


def _wrong_margin(text: str, margin: int, skip=()) -> bool:
    """A lead or deficit given as other than `margin` points ("a Spurs three-point lead" when it's 5),
    leaving out the spans in `skip` (a lead grown "by" so many points: the basket's)."""
    for pattern in MARGINS:
        for m in pattern.finditer(text):
            if any(a <= m.start() < b for a, b in skip):
                continue
            n = m.group(1).lower()
            if (int(n) if n.isdigit() else NUMBERS[n]) != margin:
                return True
    return False


def _wrong_score(text: str, play: Play) -> bool:
    """A score other than the one after the basket ("109-103")."""
    ok = {(play.mine, play.theirs), (play.theirs, play.mine)}
    return any((int(a), int(b)) not in ok for a, b in SCORE_LINE.findall(text))


def _missed(text: str) -> bool:
    """Whether `text` says the shot missed: a miss not denied ("doesn't
    miss") with no basket after it in its sentence ("misses, but Castle puts
    it back")."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for m in MISS.finditer(sentence):
            if NOT.search(sentence[:m.start()]):
                continue
            rest = sentence[m.end():].lower()
            if not any(p.search(rest) for _fits, p in SCORED) and not TWO.search(rest) and not WENT_IN.search(rest):
                return True
    return False


def _wrong_names(play: Play, titles: tuple, description: str, names) -> bool:
    """A title naming a player other than the scorer (any player, when the
    commentary doesn't say who scored), or a description naming players but
    not the scorer: a passer can be named beside the scorer there, as in
    "Parker finds Champagnie, who nails the three"."""
    for title in titles:
        for w in _players(title, names):
            if not play.scorer or not _same(w, play.scorer):
                return True
    named = _players(description, names)
    if not named:
        return False
    return not play.scorer or not any(_same(w, play.scorer) for w in named)


def _players(text: str, names) -> list[str]:
    """The players `text` names: words the commentary writes as names, and
    any capitalised word given a basket ("Campbell's Corner Three!",
    "Campbell scores"), which Whisper may have heard only where a sentence
    starts."""
    out = []
    for m in _WORD.finditer(text or ""):
        word = m.group(0)
        if names.is_name(word):
            out.append(word)
        elif word[:1].isupper() and _credited(word, text[m.end():], names):
            out.append(word)
    return out


def _credited(word: str, rest: str, names) -> bool:
    """Whether a capitalised word is given the basket: a possessive, or the
    one a scoring word follows. Not the teams or common words."""
    low = bare(word).lower()
    if low in NOT_NAMES or low in names.teams or (word.isupper() and len(word) <= 4):
        return False
    if bare(word) != word:
        return True
    follows = rest.lower()
    return any(p.match(follows.lstrip()) for _fits, p in SCORED) and follows[:1].isspace()


def _same(word: str, scorer: str) -> bool:
    """Whether `word` is the scorer's name, or a short form of it ("Wemby")."""
    word = bare(word).lower()
    for part in _WORD.findall(scorer):
        part = part.lower()
        if word == part or (min(len(word), len(part)) >= 4 and word[:4] == part[:4]):
            return True
    return False


def _added_name(play: Play, titles: tuple, description: str, names) -> bool:
    """A first name before the scorer's that the commentary doesn't say with
    it and the video's own text doesn't spell with it: one the title writer
    made up ("Dennis Schroder" for the commentary's "Schroeder") or another
    player's ("Vincent Raves": the passer's name on the shooter's)."""
    if not play.scorer:
        return False
    own = {w.lower() for w in _WORD.findall(play.scorer)}
    for text, sentences in [*((t, False) for t in titles), (description, True)]:
        found = list(_WORD.finditer(text or ""))
        for prev, cur in zip(found, found[1:]):
            first, name = prev.group(0), cur.group(0)
            low = first.lower()
            if (not _same(name, play.scorer) or not first[:1].isupper() or bare(first) != first or low in own
                    or text[prev.end():cur.start()].strip() or low in NOT_NAMES or low in names.teams or low in ROLES
                    or (low, bare(name).lower()) in names.pairs):
                continue
            if names.is_name(first):
                return True             # another player's name, or one the commentary never says with this one
            if sentences and not names.lower[low] and not (first.isupper() and len(first) <= 4):
                return True             # in a description, a capitalised word the commentary never says
    return False


def _clock(play: Play) -> tuple[str, float | None]:
    """("Q4", 6.0) for a basket at "Q4 0:06"; ("Q4", None) without the clock; ("", None) unknown."""
    period, _, left = (play.when or "").partition(" ")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", left.strip())
    return period, (int(m.group(1)) * 60 + int(m.group(2)) if m else None)


def _time(play: Play, text: str) -> list[str]:
    """The quarter, the half, overtime and the time left as the text gives
    them, against the clock where the basket went in; and an early lead or a
    strong start from the 3rd quarter on ("Mavericks Take the Early Lead!",
    5:19 into the 3rd), or "just minutes remaining" with 6 seconds left."""
    period, left = _clock(play)
    if period not in PERIODS:
        return []
    halves = period.startswith("H")
    n = 5 if period == "OT" else int(period[1])
    last = 2 if halves else 4
    wrong = any(halves or ORDINALS[m.group(1).lower()] != n for m in QUARTER.finditer(text))
    half = n if halves else (1 if n <= 2 else 2 if n <= 4 else 0)
    wrong |= any(ORDINALS[m.group(1).lower()] != half for m in HALF.finditer(text))
    wrong |= HALFTIME.search(text) is not None and half != 1
    extra = (OVERTIME.search(text) is not None and period != "OT"
             and not (play.overtime and play.after == 0 and n == last))
    if left is not None:
        wrong |= any(left >= 60 or abs(_number(m.group(1)) - left) > 2 for m in SECONDS_LEFT.finditer(text))
        wrong |= any(left < max(30, (_number(m.group(1)) - 1) * 60) or left > (_number(m.group(1)) + 1) * 60
                     for m in MINUTES_LEFT.finditer(text))
        wrong |= SECONDS_TO_GO.search(text) is not None and left >= 60
        wrong |= MINUTES_TO_GO.search(text) is not None and left < 60
        wrong |= LAST_MINUTE.search(text) is not None and left > 61
        wrong |= play.kind != "buzzer_beater" and left > 3 and any(
            "shot clock" not in text[max(0, m.start() - 12):m.start()].lower().replace("-", " ")
            for m in BUZZER.finditer(text))
    out = []
    if wrong or extra:
        when = (f"with {_left_words(left)} left in the {PERIODS[period]}" if left is not None
                else f"in the {PERIODS[period]}")
        out.append(f"It came {when}" + (" and the game didn't go to overtime" if extra and not play.overtime else "")
                   + ": say that, or nothing about the time.")
    second = 2 if halves else 3                 # the period after halftime: its first minutes start a half
    if EARLY.search(text) and n >= second and not (n == second and left is not None
                                                    and left >= (1020 if halves else 540)):
        out.append(f"It came in the {PERIODS[period]}, not early in the game: "
                   "don't call it an early lead or a strong start.")
    return out


def _left_words(left: float) -> str:
    """ "6 seconds", "5:19"."""
    if left < 60:
        return "1 second" if int(left) == 1 else f"{int(left)} seconds"
    return f"{int(left) // 60}:{int(left) % 60:02d}"


# ---- written from the play itself --------------------------------------

SHOTS = {"corner_three": "corner three", "deep_three": "deep three", "dunk": "dunk", "poster_dunk": "poster dunk",
         "alley_oop": "alley-oop", "putback": "putback", "tip_in": "tip-in", "layup": "layup",
         "euro_step": "euro-step finish", "difficult_finish": "tough finish", "fast_break_score": "fast-break bucket",
         "and_one": "and-one", "free_throw": "free throw"}
STYLES = {"step_back": "step-back", "fadeaway": "fadeaway", "pull_up": "pull-up", "isolation_score": "isolation"}


def shot_words(kind: str, points: int) -> str:
    """What the basket was, in words: "three", "step-back three", "dunk", "bucket"."""
    if kind in STYLES:
        return f"{STYLES[kind]} three" if points == 3 else f"{STYLES[kind]} jumper"
    if kind in SHOTS and (points != 3 or "three" in SHOTS[kind] or kind == "and_one"):
        return SHOTS[kind]
    return {3: "three", 1: "free throw"}.get(points, "bucket")


def written(play: Play, meta, index: int = 0):
    """A title and description from the play itself, for a clip whose own
    still got it wrong: who scored (as the commentary says), the shot, when,
    and the score after it. Never a fact the play doesn't hold."""
    title = _title(play, index)
    description = _description(play)
    hashtags = list(getattr(meta, "hashtags", None) or []) or _hashtags(play)
    fields = {"title": title, "description": description, "hashtags": hashtags}
    for card in ("headline", "subline"):        # the highlights title card: made again from the title
        if hasattr(meta, card):
            fields[card] = ""
    return replace(meta, **fields)


def _title(play: Play, index: int) -> str:
    who, shot = play.scorer, _title_case(play.shot or shot_words(play.kind, play.points))
    team = _the(play.team)                       # "the Spurs", inside a title
    lead = _cap(team)                            # "The Spurs", starting one
    m = abs(play.after)
    if play.sealed:
        options = ([f"{who} Seals It for {team}!", f"{who} Puts It Away!"] if who and team
                   else [f"{who} Seals It!"] if who else [f"{lead} Seal It!"] if team else ["The Final Basket!"])
    elif play.kind == "game_winner":
        options = [f"{who} Wins It!", f"{who} With the Game-Winner!"] if who else [f"{lead} Win It!" if team
                                                                                  else "The Game-Winner!"]
    elif not (play.team and play.other):
        verbs = {3: ["Knocks Down the {s}!", "Drills the {s}!", "Buries the {s}!"],
                 1: ["at the Line"]}.get(play.points, ["With the {s}!", "Gets the {s}!"])
        options = [f"{who} " + v.format(s=shot) for v in verbs] if who else [f"What a {shot}!"]
    elif play.after == 0:
        options = [f"{who}'s {shot} Ties It!", f"{who} Ties It Up!"] if who else [f"{lead} Tie It Up!"]
    elif play.took_lead:
        options = ([f"{who} Puts {team} Ahead!", f"{who}'s {shot} Gives {team} the Lead!"] if who
                   else [f"{lead} Take the Lead!"])
    elif play.after < 0:
        options = ([f"{who}'s {shot} Cuts It to {m}", f"{who} Gets {team} Within {m}"] if who
                   else [f"{lead} Cut It to {m}"])
    else:
        options = ([f"{who}'s {shot} Puts {team} Up {m}", f"{who} Pushes the Lead to {m}"] if who
                   else [f"{_cap(_a(shot))} Puts {team} Up {m}"])
    return options[index % len(options)]


def _description(play: Play) -> str:
    shot = play.shot or shot_words(play.kind, play.points)
    did = {"free throw": "makes a free throw", "dunk": "throws down the dunk", "layup": "lays it in"}.get(
        shot, f"hits {_a(shot)}" if play.points == 3 else f"scores on {_a(shot)}" if shot != "bucket" else "scores")
    if play.scorer:
        first = f"{play.scorer} {did}" + (f" for {_the(play.team)}" if play.team else "")
    elif play.team:
        first = f"{_cap(_the(play.team))} " + _plural(did)
    else:
        first = _cap(_a(shot))
    period, _, left = play.when.partition(" ")
    if period:
        first += f" with {left} left in the {PERIODS.get(period, period)}" if left else f" in the {PERIODS.get(period, period)}"
    out = first + "."
    if play.team and play.other:
        us, them = _cap(_the(play.team)), _cap(_the(play.other))
        if play.sealed:
            out += f" {us} win it, {play.mine}-{play.theirs}."
        elif play.after == 0:
            out += f" It's tied at {play.mine}."
        elif play.after > 0:
            out += f" {us} {'take the lead' if play.took_lead else 'lead'}, {play.mine}-{play.theirs}."
        else:
            out += f" {them} still lead, {play.theirs}-{play.mine}."
    return out


def _hashtags(play: Play) -> list[str]:
    tags = ["#basketball"]
    for team in (play.team, play.other):
        tag = "#" + re.sub(r"[^\w]", "", team or "").lower()
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    return tags


def _the(team: str) -> str:
    """ "the Spurs"; a code in capitals stays as it is ("OKC")."""
    return team if not team or (team.isupper() and len(team) <= 4) else f"the {team}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _title_case(words: str) -> str:
    """ "step-back three" -> "Step-Back Three"."""
    return re.sub(r"(?<![\w'’])[a-z]", lambda m: m.group(0).upper(), words)


def _a(noun: str) -> str:
    return ("an " if noun[:1].lower() in "aeiou" else "a ") + noun


def _plural(did: str) -> str:
    """ "hits a three" -> "hit a three", for a team ("the Spurs hit a three")."""
    verb, _, rest = did.partition(" ")
    verb = {"throws": "throw", "lays": "lay", "makes": "make", "hits": "hit", "scores": "score"}.get(verb, verb)
    return f"{verb} {rest}".strip()


def check(profile, candidates: list, metas: list, rewrite) -> list:
    """The clips' metadata with every basket clip's title and description
    held to its play: one that gets it wrong is written again (`rewrite`:
    (clips, rules) -> their metadata, the same batch call with these rules),
    and one that still does is written from the play. Any other clip as it is."""
    plays = [profile.play_of(c) for c in candidates]
    names = getattr(profile, "names", None)
    checked = sum(p is not None for p in plays)
    wrong = {i: problems(p, m, names) for i, (p, m) in enumerate(zip(plays, metas)) if p is not None}
    wrong = {i: w for i, w in wrong.items() if w}
    if not wrong:
        if checked:
            print(f"      Titles: all {checked} true to their play")
        return metas
    redo = sorted(wrong)
    again: list = []
    for at in range(0, len(redo), AT_ONCE):
        part = redo[at:at + AT_ONCE]
        rules = "\n".join([profile.title_rules(), *(f"- CLIP {k}: " + " ".join(wrong[i]) for k, i in enumerate(part))])
        try:
            got = list(rewrite([candidates[i] for i in part], rules) or [])
        except Exception as e:
            print(f"      (titles: writing them again failed: {e})")
            got = []
        again += (got + [None] * len(part))[:len(part)]
    out = list(metas)
    fixed: list[int] = []
    for i, meta in zip(redo, again):
        if meta is not None and not problems(plays[i], meta, names):
            out[i] = meta
            fixed.append(i)
        else:
            out[i] = written(plays[i], metas[i], i)
    print(f"      Titles: {len(redo)} of {checked} got their play wrong; "
          f"{len(fixed)} written again, {len(redo) - len(fixed)} written from the scoreboard")
    # Which ones, by where they are in the video.
    for what, which in (("written again", fixed), ("from the scoreboard", [i for i in redo if i not in fixed])):
        if which:
            print(f"        {what}: " + ", ".join(_span(candidates[i]) for i in which))
    return out


def _span(candidate) -> str:
    """ "142-163 s": a clip by where it is in the video."""
    return f"{float(getattr(candidate, 'start', 0) or 0):.0f}-{float(getattr(candidate, 'end', 0) or 0):.0f} s"
