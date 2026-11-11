"""Word-synced caption generation.

Builds an ASS subtitle file for one clip — short word groups, bold with a
heavy outline — which FFmpeg burns in during the clip's final encode.

Caption styling is parameterized (size, colour, position, words per group,
casing) so per-clip render options — including the AI edit assistant — can
restyle captions without touching code.

Falls back to spreading a segment's words evenly across its duration when
word-level timestamps are missing (transcripts cached before they were
enabled).
"""

import math
import re
from pathlib import Path

from core.models import ClipCandidate, Segment
from video import alphabets, caption_fit
from video import fonts as bundled

DEFAULT_STYLE = {
    "font": "Arial",          # must be in FONTS (installed on stock Windows, or bundled)
    "font_size": 84,          # at 1080x1920 playback resolution
    "color": "#FFFFFF",       # text colour (hex RGB)
    "position": "bottom",     # bottom | middle | top
    "words_per_caption": 3,
    "uppercase": True,
    "highlight": False,           # light up each word as it is spoken
    "highlight_color": "#FFE600",
    "second_speaker": False,      # another colour when it isn't the main speaker talking (#126)
    "second_speaker_color": "#5CE1FF",
    "animation": "none",          # how each caption comes on screen (ANIMATIONS)
    "safe_zone": "off",           # keep clear of an app's own buttons and text (SAFE_ZONES)
}

# How each caption comes on screen, written as ASS override tags timed from
# the caption's own start (_animated). "none" writes no tag at all.
ANIMATIONS = ("none", "pop", "fade", "bounce", "karaoke", "slide")

# The area each app covers with its own buttons and text, in px on a
# 1080x1920 Short: the bottom bar (name, description, sound) and the column of
# buttons down the right. The same measured guidance values as the Gaming
# layout editor's (gaming/layouts.json, docs/GAMING.md: measured September
# 2026 on each app's feed, kreatli.com's safe zone checkers); a test keeps the
# two the same. Guidance, not a promise: the apps change by device and by
# how long the post's description is.
SAFE_ZONES = {
    "tiktok": {"top": 180, "bottom": 420, "right": 190},
    "reels": {"top": 245, "bottom": 400, "right": 205},
    "shorts": {"top": 250, "bottom": 350, "right": 190},
}
SAFE_ZONE_CHOICES = ("off", *SAFE_ZONES)
_SAFE_CLEARANCE = 20     # px between a caption and the app's own text: the outline and shadow fit in it
_INK_SPREAD = 9          # px the style's outline (7) and shadow (2) reach past the letters, at the sides

# Fonts shipped with every stock Windows install, so a burned clip renders
# identically on any user's machine. Whitelisted: the name goes into the ASS
# header, and unknown names would silently fall back to a default anyway.
WINDOWS_FONTS = [
    "Arial",
    "Arial Black",
    "Impact",
    "Verdana",
    "Tahoma",
    "Trebuchet MS",
    "Segoe UI",
    "Georgia",
    "Comic Sans MS",
    "Courier New",
    # Script-capable Windows-stock fonts (auto-selected by language below).
    "Nirmala UI",
    "Yu Gothic UI",
    "Malgun Gothic",
    "Microsoft YaHei",
    "Leelawadee UI",
]

# Plus the fonts that ship with the app (assets/fonts/, video/fonts.py),
# burned from files staged beside the ASS file. A copy without them has
# only the Windows fonts.
BUNDLED_FONTS = [f for f in bundled.families() if f not in WINDOWS_FONTS]
FONTS = WINDOWS_FONTS + BUNDLED_FONTS

# The classic caption fonts only cover Latin/Cyrillic/Greek. Burning e.g.
# Hindi through Arial produces tofu boxes — permanently, in the video. For
# languages written in other scripts, the caption font is swapped to the
# Windows-stock font that actually has the glyphs.
SCRIPT_FONTS = {
    # Devanagari + the other Indic scripts Nirmala UI covers
    "hi": "Nirmala UI", "mr": "Nirmala UI", "ne": "Nirmala UI",
    "bn": "Nirmala UI", "ta": "Nirmala UI", "te": "Nirmala UI",
    "gu": "Nirmala UI", "kn": "Nirmala UI", "ml": "Nirmala UI",
    "pa": "Nirmala UI", "si": "Nirmala UI",
    "ja": "Yu Gothic UI",
    "ko": "Malgun Gothic",
    "zh": "Microsoft YaHei",
    "th": "Leelawadee UI", "vi": "Segoe UI",
    "ar": "Segoe UI", "fa": "Segoe UI", "ur": "Segoe UI",
}

_LATIN_ONLY = set(WINDOWS_FONTS[:10])

def script_of(language: str | None) -> str:
    """The writing system a language's captions need (video/alphabets.py)."""
    return alphabets.script_of(language)


def caption_font_for(language: str, chosen: str | None) -> str | None:
    """The font captions should actually burn with: the user's choice,
    unless the content language needs a script that choice can't render.

    A bundled font is kept only when its file has every letter the language
    is written with (video/alphabets.py letters_for, checked against the
    font's character map): otherwise libass would draw the missing letters
    in another font in the middle of a word. The captions then take the
    Windows font for that language (SCRIPT_FONTS), or Arial, which has
    Cyrillic, Greek and every accented Latin letter."""
    lang = (language or "en").lower()
    if chosen is not None and bundled.is_bundled(chosen):
        letters = alphabets.letters_for(lang)
        has = bundled.covers(chosen, letters) if letters is not None else False
        if has is None:     # the file cannot be read: go by its manifest (the render then says it is missing)
            has = script_of(lang) in bundled.scripts_for(chosen)
        if has:
            return chosen
        return SCRIPT_FONTS.get(alphabets.base_language(lang), "Arial")
    if lang in SCRIPT_FONTS and (chosen is None or chosen in _LATIN_ONLY):
        return SCRIPT_FONTS[lang]
    return chosen

# position -> (ASS numpad alignment, vertical margin)
_POSITIONS = {"bottom": (2, 440), "middle": (5, 0), "top": (8, 140)}

# Captions clear in the quiet parts of a clip (#228). A caption ends at a
# pause: when nothing is said for longer than PAUSE_SECONDS, the next word
# starts a new caption, however few words the last one has. And no word
# counts as spoken for longer than LONGEST_WORD_SECONDS: a transcript now and
# then stretches a short word's end across the silence after it, which would
# hide the pause and hold the caption up. Measured on real speech (a median
# word 0.18 s, 95 in 100 under 0.6 s): a breath between sentences stays under
# the pause, and a word said slowly on purpose stays under the cap.
# Lines a person saved keep their own times.
PAUSE_SECONDS = 0.8
LONGEST_WORD_SECONDS = 1.2
# A transcript also times a word with no length now and then. Alone beside a
# pause it has no caption to ride in, so it is on screen this long, in the
# silence after it (less than the pause, so it is gone before the next word).
ALONE_SECONDS = 0.4


def build_caption_lines(
    segments: list[Segment],
    candidate: ClipCandidate,
    words_per_caption: int = 3,
    turns: list | None = None,
) -> list[dict]:
    """The caption lines for one clip as editable data:
    [{"start", "end", "text"}] with times relative to the clip start.
    This is what the caption editor in the UI shows and what users correct.

    `turns` (analysis/voice_turns.py) is where someone other than the main
    speaker talks, [[start, end, speaker], ...] in clip seconds. With it a
    line ends where the speaker changes, so no caption mixes two people's
    words, and the other speaker's lines carry "speaker"."""
    words = _words_in_window(segments, candidate.start, candidate.end)
    size = max(1, int(words_per_caption))
    lines = []
    heard = bool(_turns(turns))
    captions: list[tuple[int, list[dict]]] = []
    for speaker, run in _by_speaker(words, turns, candidate.start, candidate.end):
        groups = _grouped(run, size, candidate.start, candidate.end)
        if heard:
            groups = _standing(groups, size, candidate.start, candidate.end)
        captions.extend((speaker, group) for group in groups)
    said: list[dict] = []       # the clip's words as the editor is given them, when they are needed
    for i, (speaker, group) in enumerate(captions):
        start = max(0.0, group[0]["start"] - candidate.start)
        closing = group[-1:]
        if heard:
            # A short word at the end can be timed inside the word before
            # it: the caption stays up until that word has been said.
            stands = [k for k, w in enumerate(group) if _stands(w, candidate.start, candidate.end)]
            closing = group[stands[-1] if stands else 0:]
        end = min(candidate.duration, max(w["end"] for w in closing) - candidate.start)
        # Up until its last word has been said (_said: no word for longer
        # than LONGEST_WORD_SECONDS), so it is gone in the silence after.
        until = max(_said(w, candidate.start, candidate.end)[1] for w in closing)
        after = captions[i + 1][1] if i + 1 < len(captions) else None
        beside = _beside_a_pause(captions[i - 1][1] if i else None, group, after, candidate.start, candidate.end)
        alone = _alone(group, after, candidate.start, candidate.end) if beside else 0.0
        if alone:
            end = until = alone
        if end <= start:
            continue
        line = {"start": round(start, 2), "end": until, "text": " ".join(w["word"] for w in group)}
        if heard and beside and not any(_stands(w, candidate.start, candidate.end) for w in group):
            # A pause made it a caption of its own, and no word in it is long
            # enough to say whose it is: by the clock, as the same line is
            # marked when the editor sends it back.
            said = said or clip_words(words, candidate.start, candidate.end)
            line = tag_lines([line], turns, said)[0]
        elif speaker:
            line["speaker"] = speaker
        lines.append(line)
    return lines


def tag_lines(lines: list[dict], turns: list | None, words: list[dict] | None = None) -> list[dict]:
    """Caption lines marked with who says them: "speaker" on a line that is
    mostly the other speaker's, and on no other. For lines the user saved:
    their text and their grouping stay as they made them.

    `words` are the clip's words (clip_words). With them a line goes by who
    says the words in it, each counted for as long as it is spoken, the same
    answer build_caption_lines gives word by word; a pause inside a caption
    then decides nothing. Without them, or for a line with no word in it to
    go by, it goes by who talks through more of the line's time.

    The words that count are the ones long enough to stand alone (a shorter
    one is said with a word beside it, which may be in another line), each in
    the line its middle falls in, from the line's start up to its end. That
    is the stretch a turn covers, so what the editor says about a caption's
    stretch (its fix for a saved caption) always decides that caption."""
    spans = _turns(turns)
    voices = []     # each word that counts: its middle, how long it is spoken, who says it
    for w in words or ():
        said_from, said_to = in_clip(w, 0.0, float("inf"))
        if said_to - said_from > _TINY:
            middle = (said_from + said_to) / 2
            voices.append((middle, said_to - said_from, next((who for a, b, who in spans if a <= middle < b), 0)))
    tagged = []
    for line in lines:
        line = {k: v for k, v in line.items() if k != "speaker"}
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            tagged.append(line)
            continue
        held: dict[int, float] = {}
        whole = 0.0
        for middle, spoken, speaker in voices:
            if start <= middle < end:
                whole += spoken
                if speaker:
                    held[speaker] = held.get(speaker, 0.0) + spoken
        if whole <= 0:
            # No word to go by: the stretch of time.
            held, whole = {}, end - start
            for turn_start, turn_end, speaker in spans:
                shared = min(end, turn_end) - max(start, turn_start)
                if shared > 0:
                    held[speaker] = held.get(speaker, 0.0) + shared
        if held:
            speaker = max(held, key=held.get)
            if held[speaker] > whole / 2:
                line["speaker"] = speaker
        tagged.append(line)
    return tagged


def words_of(segments: list[Segment], candidate: ClipCandidate) -> list[dict]:
    """A clip's words as the editor is given them (GET /clips/{id}/words):
    the transcript's timed words, in its order. Saved lines are marked by
    these on both sides, so the editor shows the colour the render burns. A
    segment without word timings has none to give; its lines go by the clock."""
    return clip_words((w for seg in segments for w in seg.words or ()), candidate.start, candidate.end)


def _turns(turns: list | None) -> list[tuple[float, float, int]]:
    """Speaker turns as (start, end, speaker). They are read back from a
    clip's saved options, so anything that is not a turn is passed over."""
    out = []
    for turn in turns if isinstance(turns, (list, tuple)) else ():
        try:
            start, end = float(turn[0]), float(turn[1])
            speaker = int(turn[2]) if len(turn) > 2 else 1
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start and speaker > 0:
            out.append((start, end, speaker))
    return out


def _by_speaker(
    words: list[dict], turns: list | None, start: float, end: float
) -> list[tuple[int, list[dict]]]:
    """Words in runs of one speaker: (0 for the main speaker, said). `start`
    and `end` are the clip's, in the words' own time."""
    spans = _turns(turns)
    if not spans:
        return [(0, words)]
    runs: list[tuple[int, list[dict]]] = []
    for w, speaker in zip(words, _speakers(words, spans, start, end)):
        if runs and runs[-1][0] == speaker:
            runs[-1][1].append(w)
        else:
            runs.append((speaker, [w]))
    return runs


# A word this short has no room for a caption of its own (one of no length
# would not be burned at all): it goes with the word beside it.
_TINY = 0.05
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")   # as transcription/cloud.py ends one


def in_clip(word: dict, start: float, end: float) -> tuple[float, float]:
    """When a word is said, in clip seconds: cut to the clip and rounded to a
    hundredth. These are the numbers the editor is given for it (clip_words),
    so whoever is asked "who says this word" is asked about the same word."""
    return round(max(0.0, word["start"] - start), 2), round(min(end - start, word["end"] - start), 2)


def _said(word: dict, start: float, end: float) -> tuple[float, float]:
    """When a word counts as spoken, in clip seconds: as in_clip has it, but
    for no longer than LONGEST_WORD_SECONDS after it starts (in the clip: a
    word the clip's start cuts through counts from there). Text without word
    timings, spread evenly over its segment, is left as long as it was
    spread: nothing was stretched there."""
    said_from, said_to = in_clip(word, start, end)
    if word.get("spread"):
        return said_from, said_to
    return said_from, min(said_to, round(said_from + LONGEST_WORD_SECONDS, 2))


def _quiet(before: list[dict], after: list[dict], start: float, end: float) -> bool:
    """Whether a pause sits between two captions' words: the first have been
    said and the others start more than PAUSE_SECONDS later."""
    until = max(_said(w, start, end)[1] for w in before)
    return round(_said(after[0], start, end)[0] - until, 2) > PAUSE_SECONDS


def _beside_a_pause(
    before: list[dict] | None, group: list[dict], after: list[dict] | None, start: float, end: float
) -> bool:
    """Whether a caption's words have a pause before or after them. `before`
    and `after` are the words of the captions either side of it."""
    return (before is not None and _quiet(before, group, start, end)) or (
        after is not None and _quiet(group, after, start, end))


def _alone(group: list[dict], after: list[dict] | None, start: float, end: float) -> float:
    """Until when a caption beside a pause is on screen when it has no room
    of its own, in clip seconds; 0 for every other caption. Its words take
    no longer than _TINY to say (a transcript times a word with no length
    now and then): it gets ALONE_SECONDS, or as much of that as there is
    before the next caption or the clip's end. Away from a pause such words
    are as they always were (a caption of no length is not burned)."""
    said_from = _said(group[0], start, end)[0]
    if max(_said(w, start, end)[1] for w in group) - said_from > _TINY:
        return 0.0
    until = min(round(end - start, 2), round(said_from + ALONE_SECONDS, 2))
    if after is not None:
        until = min(until, _said(after[0], start, end)[0])
    return until if until > said_from else 0.0


def clip_words(words, start: float, end: float) -> list[dict]:
    """The words said in a clip as the editor's transcript shows them:
    [{"start", "end", "word"}] in clip seconds. `words` is every word of the
    video, in the transcript's order and time."""
    out = []
    for w in words:
        if w["end"] > start and w["start"] < end:
            said_from, said_to = in_clip(w, start, end)
            out.append({"start": said_from, "end": said_to, "word": w["word"]})
    return out


def _speakers(words: list[dict], spans: list[tuple[float, float, int]], start: float, end: float) -> list[int]:
    """Who says each word: the speaker of the turn its middle falls in, 0
    (the main speaker) outside every turn. A word too short to stand alone
    is said by whoever says the word it leans on."""
    when = [in_clip(w, start, end) for w in words]
    own = [next((who for a, b, who in spans if a <= (said_from + said_to) / 2 < b), 0) for said_from, said_to in when]
    return [own[on] if on >= 0 else 0 for on in _leans(words, when)]


def _leans(words: list[dict], when: list[tuple[float, float]]) -> list[int]:
    """For each word, the word that decides who says it: itself, or for a
    word too short to stand alone one of the two words its run of short words
    sits between (-1 in a clip of nothing but short words).

    Words up to the last one in the run that ends a sentence finish what the
    speaker before them was saying; the rest open what comes next. With no
    full stop to go by, in the run or on the word before it, the run goes
    with the nearer of the two words, the one after when they are as near."""
    count = len(words)
    short = [said_to - said_from <= _TINY for said_from, said_to in when]
    on = [-1 if tiny else i for i, tiny in enumerate(short)]
    i = 0
    while i < count:
        if not short[i]:
            i += 1
            continue
        j = i
        while j < count and short[j]:
            j += 1                                  # the run is words i to j - 1
        before, after = i - 1, j
        if before < 0 and after >= count:
            part = None
        elif before < 0:
            part = i
        elif after >= count:
            part = j
        else:
            ends = [k for k in range(i, j) if _ends_sentence(words[k])]
            if ends:
                part = ends[-1] + 1
            elif _ends_sentence(words[before]):
                part = i
            else:
                part = j if when[i][0] - when[before][1] < when[after][0] - when[j - 1][1] else i
        if part is not None:
            on[i:part] = [before] * (part - i)
            on[part:j] = [after] * (j - part)
        i = j
    return on


def _ends_sentence(word: dict) -> bool:
    return bool(_SENTENCE_END.search(str(word.get("word") or "").strip()))


def _stands(word: dict, start: float, end: float) -> bool:
    """Whether a word is long enough to stand alone."""
    said_from, said_to = in_clip(word, start, end)
    return said_to - said_from > _TINY


def _standing(groups: list[list[dict]], size: int, start: float, end: float) -> list[list[dict]]:
    """What one person says, in captions that hold a word long enough to
    stand alone. A group of nothing but shorter words goes in the caption
    before it (the first of them in the one after): alone it would be on
    screen for an instant, or not burned at all, and its colour would be
    decided by a word in another caption.

    A caption takes at most three words more than are set for one. A
    transcript can squeeze a whole burst of words into one instant; the rest
    of those stay as they are without the option.

    Never across a pause, which would keep the caption up through it: each
    stretch between two pauses is done by itself, and short words alone in
    one are a caption together (_alone has it on screen in the silence)."""
    stretches = [groups[:1]]
    for before, group in zip(groups, groups[1:]):
        if _quiet(before, group, start, end):
            stretches.append([group])
        else:
            stretches[-1].append(group)
    if len(stretches) < 2:
        return _stood(groups, size, start, end)
    out: list[list[dict]] = []
    for stretch in stretches:
        if any(_stands(w, start, end) for group in stretch for w in group):
            out.extend(_stood(stretch, size, start, end))
        else:
            short = [w for group in stretch for w in group]
            out.extend(short[i:i + size + 3] for i in range(0, len(short), size + 3))
    return out


def _stood(groups: list[list[dict]], size: int, start: float, end: float) -> list[list[dict]]:
    """_standing for words with no pause between them."""
    limit = size + 3
    out: list[list[dict]] = []
    waiting: list[list[dict]] = []      # short groups in front of the first caption
    seen = room = False                 # a caption is made; the last one can still take words
    for group in groups:
        if any(_stands(w, start, end) for w in group):
            taken: list[dict] = []
            while waiting and len(waiting[-1]) + len(taken) + len(group) <= limit:
                taken = waiting.pop() + taken
            out.extend(waiting)
            out.append(taken + group)
            waiting = []
            seen = room = True
        elif not seen:
            waiting.append(group)
        elif room and len(out[-1]) + len(group) <= limit:
            out[-1] = out[-1] + group
        else:
            out.append(group)
            room = False
    return out if seen else groups      # nothing but short words: as they were


def paint_turns(turns: list | None, edits: list | None) -> list[list]:
    """The turns to caption by: what the render heard (`turns`), with what a
    person said in the editor laid over it.

    `edits` (a clip's saved speaker_edits) is [[start, end, speaker], ...] in
    clip seconds, speaker 0 for the main speaker and 1 for the other. Each is
    a statement about that stretch, whatever was heard there, and a later one
    overrules an earlier one."""
    held = list(_turns(turns))
    for said_from, said_to, speaker in _statements(edits):
        cut: list[tuple[float, float, int]] = []
        for a, b, who in held:
            if a < said_from:
                cut.append((a, min(b, said_from), who))
            if b > said_to:
                cut.append((max(a, said_to), b, who))
        held = cut
        if speaker:
            held.append((said_from, said_to, speaker))
    painted: list[list] = []
    for a, b, who in sorted(held):
        if painted and painted[-1][2] == who and a <= painted[-1][1]:
            painted[-1][1] = max(painted[-1][1], b)     # one turn, said in two pieces
        else:
            painted.append([a, b, who])
    # Not rounded: a statement about one of two words said at once ends
    # between their middles, which can be finer than a hundredth.
    return painted


def shift_edits(edits: list | None, old_start: float, new_start: float) -> list[list]:
    """A clip's speaker_edits carried to the clip after its start moved. They
    are only moved: one that now lies outside the clip says nothing there,
    and is back in place if the clip grows again."""
    moved = old_start - new_start
    # Kept finer than a hundredth, as paint_turns keeps them: an edge between
    # the middles of two words said at once must stay between them.
    return [[round(a + moved, 4), round(b + moved, 4), who] for a, b, who in _statements(edits)]


def _statements(edits: list | None) -> list[tuple[float, float, int]]:
    """A clip's speaker_edits as (start, end, speaker). Unlike a turn, a
    statement can name the main speaker (0) and can lie outside the clip.
    Read back from saved options, so anything else is passed over."""
    out = []
    for edit in edits if isinstance(edits, (list, tuple)) else ():
        try:
            start, end, speaker = float(edit[0]), float(edit[1]), int(edit[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start and speaker >= 0:
            out.append((start, end, 1 if speaker else 0))
    return out


# Saved line times are rounded to a hundredth, and a word often starts on the
# very instant the one before it ends.
_EDGE = 0.006


def refit_caption_lines(
    lines: list[dict],
    segments: list[Segment],
    old_start: float,
    new_start: float,
    new_end: float,
    words_per_caption: int = 3,
) -> list[dict]:
    """Saved caption lines carried to a clip whose start or end has moved.

    Saved lines hold the user's corrected text, timed from the clip start they
    were saved for (`old_start`). Burned in untouched after the clip grew, they
    left the added seconds with no captions on every later render, and a moved
    start put all of them out of sync (#120). So the lines shift with the
    start, the ones the clip no longer holds are dropped, and the stretch
    before the first and after the last is captioned from the transcript. A
    line the user blanked still covers its time, and stays blank."""
    duration = new_end - new_start
    shift = old_start - new_start
    kept = []
    for line in lines:
        try:
            start, end = float(line["start"]) + shift, float(line["end"]) + shift
        except (KeyError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(duration, end)
        if end > start:
            kept.append({**line, "start": round(start, 2), "end": round(end, 2)})
    if not kept:
        return build_caption_lines(
            segments, ClipCandidate(start=new_start, end=new_end, score=0), words_per_caption
        )
    kept.sort(key=lambda l: l["start"])

    # A word the saved lines already hold is never said twice: one cut by the
    # old edge of the clip belongs to the saved line it was in. The head is
    # what the old start left out. The tail goes by where the saved lines
    # end, not by the old end of the clip, so a clip that grew before this
    # existed gets its missing captions back too.
    last = new_start + kept[-1]["end"]
    words = _words_in_window(segments, new_start, new_end)
    head = [w for w in words if w["end"] <= old_start]
    tail = [w for w in words if w["start"] >= last - _EDGE]
    size = max(1, int(words_per_caption))
    added = []
    made = [_grouped(head, size, new_start, new_end), _grouped(tail, size, new_start, new_end)]
    for groups in made:
        for i, group in enumerate(groups):
            start = max(0.0, group[0]["start"] - new_start)
            end = min(duration, group[-1]["end"] - new_start)
            until = _said(group[-1], new_start, new_end)[1]
            after = groups[i + 1] if i + 1 < len(groups) else None
            beside = _beside_a_pause(groups[i - 1] if i else None, group, after, new_start, new_end)
            alone = _alone(group, after, new_start, new_end) if beside else 0.0
            if alone:
                end = until = alone
            if end > start:
                added.append(
                    {"start": round(start, 2), "end": until, "text": " ".join(w["word"] for w in group)}
                )
    return sorted(added + kept, key=lambda l: l["start"])


def build_captions(
    segments: list[Segment],
    candidate: ClipCandidate,
    output_path: Path,
    style: dict | None = None,
    lines: list[dict] | None = None,
    canvas: tuple[int, int] = (1080, 1920),
    language: str = "en",
    karaoke_untimed: bool = False,
) -> Path | None:
    """Write an ASS file with times relative to the clip start.
    `karaoke_untimed`: the lines were moved to an edited timeline (cuts or
    speed), so the transcript's word times no longer match them; karaoke
    then spreads each line's fill over its own time by word length.
    `lines` (user-corrected caption text) overrides the generated ones.
    `canvas` is the output frame (default portrait Shorts; longform passes
    1920x1080 and the style scales to it). Returns the path, or None if
    there is nothing to caption."""
    opts = {**DEFAULT_STYLE, **(style or {})}
    # Non-Latin content: swap in a font that has the glyphs (see SCRIPT_FONTS).
    opts["font"] = caption_font_for(language, opts.get("font")) or opts.get("font")
    if bundled.is_bundled(opts["font"]) and not bundled.usable(opts["font"]):
        # Never a failed render over a font: Arial, and a line in the job log.
        print(f"      The caption font {opts['font']} is missing or damaged in this copy of the app, "
              f"so the captions use Arial")
        opts["font"] = "Arial"
    elif opts.get("font") not in FONTS:
        # A name this copy does not have (an app without its fonts folder,
        # a style saved by a newer app) burns in Arial: the job log says so.
        if opts.get("font"):
            missing = " (the app's own caption fonts are missing)" if bundled.folder() is None else ""
            print(f"      The caption font {opts['font']} is not in this copy of the app, "
                  f"so the captions use Arial{missing}")
        opts["font"] = "Arial"
    animation = opts.get("animation") if opts.get("animation") in ANIMATIONS else "none"
    if animation == "karaoke" and str(language or "").lower().replace("_", "-").split("-")[0] in _RTL_LANGUAGES:
        # libass lays karaoke's per-word runs out left to right, which puts a
        # right-to-left line's words in reverse order: burn it plain instead.
        animation = "none"
        opts = {**opts, "animation": "none", "highlight": False}
    if lines is None:
        lines = build_caption_lines(segments, candidate, opts["words_per_caption"])

    # Word timings for the highlight and karaoke, clip-relative like `lines` are.
    spoken = (
        [
            {"start": w["start"] - candidate.start, "end": w["end"] - candidate.start,
             "word": w["word"]}
            for w in _words_in_window(segments, candidate.start, candidate.end)
        ]
        if opts.get("highlight") or animation == "karaoke"
        else []
    )

    # The other speaker's lines (build_caption_lines, tag_lines) in their own
    # colour. Only with the option on: a "speaker" left on a saved line by an
    # earlier render never colours a plain one.
    second = (
        _inline_color(opts.get("second_speaker_color") or DEFAULT_STYLE["second_speaker_color"])
        if opts.get("second_speaker")
        else None
    )

    # Pop, bounce and slide turn off libass's stacking of captions that are
    # on screen together (hand-edited times can overlap), so a caption that
    # overlaps another is written without them (_animated).
    spans = [
        (_centis(float(line["start"])), _centis(float(line["end"])))
        for line in lines
        if str(line.get("text", "")).strip() and float(line["end"]) > float(line["start"])
    ] if animation in ("pop", "bounce", "slide") else []

    zoned = _zoned(opts, canvas)
    dialogue = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue  # a blanked-out line deletes that caption
        if opts["uppercase"]:
            text = text.upper()
        text = text.replace("\\", "").replace("{", "").replace("}", "")  # ASS control chars
        start, end = float(line["start"]), float(line["end"])
        if end <= start:
            continue
        tint = second if second and line.get("speaker") else None
        if animation != "none" or zoned:
            # What is measured must be what is drawn (_spaced). Never for a
            # plain caption: its text stays exactly as it always was.
            text = _spaced(text)
            if not text:
                continue
        # A safe zone: a word too wide for the safe area shrinks its caption.
        fit = _fit_scale(text, opts, canvas)
        if animation == "karaoke":
            # Karaoke fills each word as it is said: it does the word
            # highlight's job, so the highlight is not drawn on top of it.
            dialogue.extend(_with_scale(
                [_karaoke(text, start, end, [] if karaoke_untimed else spoken, opts, tint)], fit))
            continue
        if opts.get("highlight"):
            events = _highlighted(text, start, end, spoken, opts, tint)
        else:
            events = [f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_tinted(text, tint)}"]
        if animation != "none":
            here = (_centis(start), _centis(end))
            alone = sum(1 for s, e in spans if s < here[1] and e > here[0]) <= 1
            events = _animated(events, animation, opts, canvas, text, alone, fit)
        else:
            events = _with_scale(events, fit)
        dialogue.extend(events)

    if not dialogue:
        return None
    output_path.write_text(_header(opts, canvas) + "\n".join(dialogue) + "\n", encoding="utf-8")
    return output_path


# The animations' timings, in ms from the caption's own start.
_POP_MS = 120          # pop: 80% to 100%
_BOUNCE_MS = (120, 200)    # bounce: 80% to 110% by the first, settled at 100% by the second
_FADE_MS = (120, 80)   # fade: in, and out before the caption's end
_FADE_SHARE = 0.6      # the most of a caption the two fades may take: the rest is held at full colour
_HOLD_MS = 120         # pop and bounce on a caption that cannot be measured: fades in at full size
_SLIDE_MS = 150        # slide: rises into place
_SLIDE_PX = 40         # how far it rises, on the 1920-tall canvas
_EVENT_TEXT = "Default,,0,0,0,,"


_BOUNCE_PEAK = 110     # bounce's overshoot, in %


def _animated(
    events: list[str], animation: str, opts: dict, canvas: tuple[int, int], text: str = "", alone: bool = True,
    fit: int | None = None,
) -> list[str]:
    """One caption's Dialogue events with its animation's override tags.

    The animation plays once, from the moment the caption comes on screen.
    The word highlight writes a caption as several events (one per word);
    each event that starts while the animation is still playing picks it up
    where it has got to (the size, place or fade it has reached at that
    moment) and plays the rest, so a short first word never cuts it off.
    The fade-out runs over the caption's last 80 ms, across events too.

    Times, words and colours stay as they are. Pop and bounce keep the lines
    libass makes at 100%: when the size change would move a line break, the
    breaks are written out (\\N, with \\q2) so the caption never jumps from
    one line to two part way. A caption whose lines cannot be measured
    (caption_fit.lines: an emoji, a script it does not follow such as Thai,
    Hindi or Arabic, a letter the font lacks, a close call) is not
    scaled at all: it fades in at full size over the same time instead
    (_HOLD_MS), so it never changes its lines or overshoots. Bounce
    overshoots to 110%, or less when that would reach past the margins. A
    caption whose time overlaps another caption's gets no pop, bounce or
    slide (they stop libass from stacking the two), only a fade if fade is
    chosen.

    `fit` (_fit_scale) is the size, in %, a safe zone shrinks the caption to
    when one of its words is too wide for the safe area: pop and bounce
    grow to that size instead of 100%, and every other event is drawn at
    it (_with_scale)."""
    if animation in ("pop", "bounce", "slide") and not alone:
        return _with_scale(events, fit)
    timed = []
    for event in events:
        head, sep, body = event.partition(_EVENT_TEXT)
        fields = head.split(",")
        if not sep or len(fields) < 3:
            return _with_scale(events, fit)
        timed.append((head, body, _centis_of(fields[1]), _centis_of(fields[2])))
    first, last = timed[0][2], timed[-1][3]
    peak = _BOUNCE_PEAK if animation == "bounce" else 100
    base = fit or 100                                # the caption's full size, in %
    breaks: set[int] = set()
    hold = False                                     # pop or bounce on a caption that cannot be measured
    if animation in ("pop", "bounce") and text:
        fixed, peak, measured = _scale_lines(text, opts, canvas, peak, base)
        hold = not measured
        if fixed:
            counts = [len(line.split()) for line in fixed]
            breaks = {sum(counts[:k]) - 1 for k in range(1, len(counts))}
    full = _num(base)

    def sized(pct: float) -> str:
        return _num(pct if base == 100 else pct * base / 100)

    x, y = _anchor(opts, canvas)
    out = []
    for head, body, ev_start, ev_end in timed:
        at = (ev_start - first) * 10                 # ms from the caption's start
        tag = ""
        if hold:
            tag = _scale_tag(fit) + _fade_tag(at, (ev_end - ev_start) * 10, (last - first) * 10, (_HOLD_MS, 0))
        elif animation == "pop" and at < _POP_MS:
            s = sized(80 + 20 * at / _POP_MS)
            tag = f"\\fscx{s}\\fscy{s}\\t(0,{_POP_MS - at},\\fscx{full}\\fscy{full})"
        elif animation == "bounce" and at < _BOUNCE_MS[1]:
            a, b = _BOUNCE_MS
            top = sized(peak)
            if at < a:
                s = sized(80 + (peak - 80) * at / a)
                tag = (f"\\fscx{s}\\fscy{s}\\t(0,{a - at},\\fscx{top}\\fscy{top})"
                       f"\\t({a - at},{b - at},\\fscx{full}\\fscy{full})")
            else:
                s = sized(peak - (peak - 100) * (at - a) / (b - a))
                tag = f"\\fscx{s}\\fscy{s}\\t(0,{b - at},\\fscx{full}\\fscy{full})"
        elif animation in ("pop", "bounce"):
            tag = _scale_tag(fit)
        elif animation == "fade":
            tag = _fade_tag(at, (ev_end - ev_start) * 10, (last - first) * 10)
        elif animation == "slide" and at < _SLIDE_MS:
            rise = max(1, round(_SLIDE_PX * canvas[1] / 1920))
            tag = f"\\move({x},{y + round(rise * (1 - at / _SLIDE_MS))},{x},{y},0,{_SLIDE_MS - at})"
        if animation in ("fade", "slide"):
            tag = _scale_tag(fit) + tag
        if breaks:
            tag = f"\\q2{tag}"
            body = _broken(body, breaks)
        out.append(f"{head}{_EVENT_TEXT}{{{tag}}}{body}" if tag else f"{head}{_EVENT_TEXT}{body}")
    return out


def _fade_times(total: int, fade: tuple[int, int] = _FADE_MS) -> tuple[int, int]:
    """How long a caption `total` ms long fades in and out: 120 and 80 ms,
    or shorter in that proportion when those would take more than
    _FADE_SHARE of the caption, so a short caption (one word said quickly)
    still reaches full colour and holds it before it goes. On a 140 ms
    caption that is 50 ms in, 56 ms held, 34 ms out; \\fad(120,80) on it
    would have the fade-out start inside the fade-in, and libass then jumps
    from one to the other between two frames."""
    fade_in, fade_out = fade
    both = fade_in + fade_out
    budget = min(both, math.floor(total * _FADE_SHARE))
    if budget >= both or both <= 0:
        return fade_in, fade_out
    scaled_in = round(budget * fade_in / both)
    return scaled_in, budget - scaled_in


def _fade_tag(at: int, length: int, total: int, fade: tuple[int, int] = _FADE_MS) -> str:
    """Fade's tag for an event `at` ms into a caption `total` ms long: in
    over the caption's first 120 ms, out over its last 80, or less on a
    short caption (_fade_times). A caption that is one event gets \\fad; an
    event of a longer caption gets the part of the fade it is on screen
    for, from where it has got to (\\fade). `fade` is (in, out) in ms: the
    hold of an unmeasured pop or bounce passes (_HOLD_MS, 0)."""
    fade_in, fade_out = _fade_times(total, fade)
    if at == 0 and length == total:
        return f"\\fad({fade_in},{fade_out})"
    out_from = total - fade_out - at                 # when the fade-out starts, from this event's start
    in_now = at < fade_in
    out_now = fade_out > 0 and out_from < length
    if not in_now and not out_now:
        return ""
    if at == 0 and not out_now and length >= fade_in:
        return f"\\fad({fade_in},0)"
    if not in_now and out_from >= 0 and length == total - at:
        return f"\\fad(0,{fade_out})"
    if not in_now:
        a1 = a2 = 0 if out_from >= 0 else round(255 * min(1.0, -out_from / fade_out))
        t2 = 0
    else:
        a1, a2, t2 = round(255 * (1 - at / fade_in)), 0, fade_in - at
    t3 = max(t2, out_from)
    t4 = max(t3, total - at)
    return f"\\fade({a1},{a2},255,0,{t2},{t3},{t4})"


def _scale_lines(
    text: str, opts: dict, canvas: tuple[int, int], peak: int, base: int = 100
) -> tuple[list[str] | None, int, bool]:
    """For pop and bounce: (the lines to write out, when libass would break
    the caption differently at some size between 80% and the peak, else
    None; the peak to use, at most 110% and no wider than the margins; and
    whether the caption could be measured at all). Sizes are in % of
    `base`, the caption's full size (below 100 when a safe zone shrinks it,
    _fit_scale). A character the measuring does not follow and a close call
    it cannot be sure of (caption_fit.lines) give (None, peak, False): the
    caller then holds the caption at full size instead of scaling it. A
    font whose file this machine does not have (never one of Windows' own
    on Windows, nor a bundled one that is whole: a damaged one has already
    been swapped for Arial) is drawn in some other font anyway, so there is
    nothing to measure: it scales as written, (None, peak, True)."""
    font = _style_font(opts)
    if caption_fit.font(font) is None:
        return None, peak, True
    size = _style_size(opts, canvas)
    if base != 100:
        size = size * base / 100
    bold = _style_bold(font)
    _, margin_l, margin_r, _ = _layout(opts, canvas)
    wrap = canvas[0] - margin_l - margin_r
    if text != _spaced(text):
        # Not the text build_captions writes (it comes here already
        # _spaced): what would be measured is not what is drawn, so hold.
        return None, peak, False
    words = text
    at100 = caption_fit.lines(words, font, size, bold, wrap)
    if at100 is None:
        return None, peak, False
    widest = caption_fit.widest(at100, font, size, bold) or 0
    if widest > 0 and peak > 100:
        peak = max(100, min(peak, math.floor(100 * (wrap - 1) / widest)))
    for pct in range(80, peak + 1):
        if caption_fit.lines(words, font, size * pct / 100, bold, wrap) != at100:
            return at100, peak, True
    return None, peak, True


def _broken(body: str, breaks: set[int]) -> str:
    """An event's text with a line break (\\N) in place of the spaces
    between words number b and b + 1 (counted from 0) for each b in
    `breaks`. Override tags ({...}) are left as they are."""
    out = []
    depth = 0
    gap = -1
    in_gap = False
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch.isspace():
            if not in_gap:
                gap += 1
                in_gap = True
                if gap in breaks:
                    out.append("\\N")
            if gap in breaks:
                continue
        elif depth == 0:
            in_gap = False
        out.append(ch)
    return "".join(out)


# Characters with no width and no ink that are not spaces to Python either:
# zero-width space, word joiner, byte-order mark, Mongolian vowel separator.
# (The zero-width joiner and non-joiner stay: they shape Arabic, Indic
# scripts and emoji.)
_NO_WIDTH = dict.fromkeys((0x200B, 0x2060, 0xFEFF, 0x180E))


def _zoned(opts: dict, canvas: tuple[int, int]) -> bool:
    """Whether a safe zone applies: one is chosen and the clip is portrait."""
    name = opts.get("safe_zone")
    return isinstance(name, str) and name in SAFE_ZONES and canvas[1] > canvas[0]


def _spaced(text: str) -> str:
    """A caption's text with single ordinary spaces between its words, as
    it is written to the event when an animation or a safe zone is on.

    libass draws the text as written and breaks a line only at an ordinary
    space (U+0020): two spaces are twice as wide, and words joined by a
    no-break space (U+00A0, U+202F, what French puts before ? ! : ;) are one
    unbreakable run. The measuring behind the safe zones and behind pop and
    bounce (caption_fit) works on words with one space between them, so the
    event gets exactly that text: every run of spaces of any kind (tabs,
    no-break and other Unicode spaces) becomes one ordinary space, the ends
    are trimmed and characters with no width are dropped. The word highlight
    and karaoke have always rebuilt their text this way. A caption with no
    animation and no safe zone never comes here."""
    return " ".join(text.translate(_NO_WIDTH).split())


def _fit_scale(text: str, opts: dict, canvas: tuple[int, int]) -> int | None:
    """With a safe zone, the size (in %) that brings a caption's widest word
    inside the safe area, when that word is wider than the space between
    the margins (libass breaks lines only at spaces, so such a word would
    run under the app's buttons; a caption in a language written without
    spaces, Chinese, Japanese or Thai, is one word). None when the caption
    fits, when no safe zone applies, and when the font or a character
    cannot be measured (caption_fit.widest_word: Hindi, Arabic, an emoji):
    that caption is written as it always was."""
    if not _zoned(opts, canvas):
        return None
    font = _style_font(opts)
    _, margin_l, margin_r, _ = _layout(opts, canvas)
    room = canvas[0] - margin_l - margin_r - 1
    # The text exactly as the event has it (build_captions has already made
    # its spaces plain, _spaced): nothing is collapsed here, and
    # widest_word splits only where libass can break.
    widest = caption_fit.widest_word(text, font, _style_size(opts, canvas), _style_bold(font))
    if not widest or widest <= room:
        return None
    return max(1, math.floor(100 * room / widest))


def _scale_tag(fit: int | None) -> str:
    """The override that draws a caption at `fit` % of its size ("" for none)."""
    return f"\\fscx{fit}\\fscy{fit}" if fit else ""


def _with_scale(events: list[str], fit: int | None) -> list[str]:
    """`events` drawn at `fit` % of their size: the scale goes at the start
    of each event's first override block, or in a block of its own."""
    if not fit:
        return events
    out = []
    for event in events:
        head, sep, body = event.partition(_EVENT_TEXT)
        if not sep:
            out.append(event)
        elif body.startswith("{"):
            out.append(f"{head}{_EVENT_TEXT}{{{_scale_tag(fit)}{body[1:]}")
        else:
            out.append(f"{head}{_EVENT_TEXT}{{{_scale_tag(fit)}}}{body}")
    return out


def _num(value: float) -> str:
    """A tag's number: whole numbers as they are, others to 2 decimals."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _centis(seconds: float) -> int:
    """A time as the ASS file writes it (_ass_time), in hundredths of a second."""
    return _centis_of(_ass_time(seconds))


def _centis_of(stamp: str) -> int:
    h, m, s = stamp.strip().split(":")
    whole, _, frac = s.partition(".")
    return ((int(h) * 60 + int(m)) * 60 + int(whole)) * 100 + int((frac + "00")[:2])


def _anchor(opts: dict, canvas: tuple[int, int]) -> tuple[int, int]:
    """Where a caption's alignment point sits on the frame, as libass places
    an event with no position of its own: across, the middle of the space
    between the margins; down, the margin from the bottom or top, or the
    middle of the frame."""
    alignment, margin_l, margin_r, margin_v = _layout(opts, canvas)
    x = round((margin_l + canvas[0] - margin_r) / 2)
    if alignment == 2:
        y = canvas[1] - margin_v
    elif alignment == 8:
        y = margin_v
    else:
        y = round(canvas[1] / 2)
    return x, y


# Right-to-left languages: no karaoke runs (see build_captions).
_RTL_LANGUAGES = frozenset({"ar", "he", "iw", "fa", "ur", "yi", "ps", "sd", "ug", "dv"})


def _karaoke(
    text: str, start: float, end: float, spoken: list[dict], opts: dict, tint: str | None = None
) -> str:
    """One Dialogue for the caption, each word filling with the highlight
    colour while it is said (\\kf, in hundredths of a second from the
    caption's start), from the caption's own colour (the other speaker's,
    for their lines). A pause between words is an empty \\k. Words without
    exact timings are spread over the caption by length (_word_times)."""
    tokens = text.split()
    base = tint or _inline_color(opts["color"])
    hot = _inline_color(opts.get("highlight_color") or DEFAULT_STYLE["highlight_color"])
    total = max(0, round((end - start) * 100))
    times = _word_times(text, start, end, spoken)
    parts = []
    at = 0                                  # hundredths of a second written so far
    for i, tok in enumerate(tokens):
        w_start, w_end = times[i] if i < len(times) else (end, end)
        said_from = min(total, max(at, round((w_start - start) * 100)))
        upto = round((w_end - start) * 100)
        if i + 1 < len(times):
            upto = min(upto, round((times[i + 1][0] - start) * 100))
        said_to = min(total, max(said_from, upto))
        if said_from > at:
            parts.append(f"{{\\k{said_from - at}}}")        # the pause before the word
        parts.append(f"{{\\kf{said_to - said_from}}}{tok}" + (" " if i < len(tokens) - 1 else ""))
        at = said_to
    return (f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,"
            f"{{\\1c{hot}\\2c{base}}}" + "".join(parts))


def _word_times(text: str, start: float, end: float, spoken: list[dict]) -> list[tuple[float, float]]:
    """When each word of `text` is said, as (start, end) pairs.

    Uses Whisper's real per-word timings when they line up with the line —
    speech is uneven, and real timings are what make the highlight land on
    the beat. Falls back to splitting the line's own span in proportion to
    word length, which is what keeps hand-edited caption text working.
    """
    tokens = text.split()
    if not tokens:
        return []
    # A word's middle, counting it for no longer than a caption does (_said):
    # one stretched across the silence after it is still in its own line.
    inside = [
        w for w in spoken
        if start - 0.01 <= (w["start"] + min(w["end"], w["start"] + LONGEST_WORD_SECONDS)) / 2 <= end + 0.01
    ]
    if len(inside) == len(tokens):
        return [(float(w["start"]), float(w["end"])) for w in inside]

    span = max(0.05, end - start)
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    out, t = [], start
    for wgt in weights:
        step = span * wgt / total
        out.append((t, t + step))
        t += step
    return out


def _tinted(text: str, tint: str | None) -> str:
    """A caption's text in `tint` (an inline colour), or as the style has it."""
    return f"{{\\1c{tint}}}{text}" if tint else text


def _highlighted(
    text: str, start: float, end: float, spoken: list[dict], opts: dict, tint: str | None = None
) -> list[str]:
    """One Dialogue per word: the whole caption, with the word being spoken
    in the highlight colour. `tint` is the caption's own colour when it is
    not the style's (the other speaker's line).

    ASS karaoke (\\k) fills progressively and leaves every passed word
    coloured, which is the sing-along look. Short-form captions highlight
    exactly ONE word at a time, so each state is its own event.
    """
    tokens = text.split()
    times = _word_times(text, start, end, spoken)
    if len(tokens) < 2 or not times:
        return [f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_tinted(text, tint)}"]

    base = tint or _inline_color(opts["color"])
    hot = _inline_color(opts.get("highlight_color", DEFAULT_STYLE["highlight_color"]))
    events = []
    for i, (w_start, w_end) in enumerate(times):
        # Hold each state until the next word begins, so there is never a
        # frame with nothing on screen between words.
        seg_start = start if i == 0 else max(start, w_start)
        seg_end = end if i == len(times) - 1 else max(seg_start, min(end, times[i + 1][0]))
        if seg_end <= seg_start:
            continue
        body = " ".join(
            (f"{{\\1c{hot}}}{tok}{{\\1c{base}}}" if j == i else tok)
            for j, tok in enumerate(tokens)
        )
        events.append(
            f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},Default,,0,0,0,,"
            f"{{\\1c{base}}}{body}"
        )
    return events


def _layout(opts: dict, canvas: tuple[int, int] = (1080, 1920)) -> tuple[int, int, int, int]:
    """(alignment, MarginL, MarginR, MarginV) for the caption style.

    With a safe zone (a portrait clip only: the apps' buttons and text are
    laid over vertical video), a bottom caption sits at least 20 px above
    the app's bottom bar, a top one 20 px below its top bar, and a middle
    one stays in the middle of the frame, which is inside every app's safe
    area. A bottom caption that slides in starts 40 px lower, so it sits
    that much higher again. The side margins both become the width of the
    app's right-hand column of buttons plus the caption's outline and shadow
    (the margins bound the letters, not their outline), so the caption keeps
    clear of it and stays centred."""
    alignment, margin_v = _POSITIONS.get(opts.get("position"), _POSITIONS["bottom"])
    margin_l = margin_r = 60
    name = opts.get("safe_zone")
    zone = SAFE_ZONES.get(name) if isinstance(name, str) and canvas[1] > canvas[0] else None
    if zone:
        if alignment == 2:
            rise = _SLIDE_PX if opts.get("animation") == "slide" else 0
            margin_v = max(margin_v, zone["bottom"] + _SAFE_CLEARANCE + rise)
        elif alignment == 8:
            margin_v = max(margin_v, zone["top"] + _SAFE_CLEARANCE)
        margin_l = margin_r = round((max(60, zone["right"]) + _INK_SPREAD) * canvas[0] / 1080)
    # Style values are calibrated for the 1920-tall Shorts canvas; scale
    # them to whatever frame this clip renders at (e.g. landscape 1080).
    # (canvas[1] / 1920) first, as it always was: the other order rounds a
    # few unusual frame heights 1 px differently.
    return alignment, margin_l, margin_r, round(margin_v * (canvas[1] / 1920))


def _style_font(opts: dict) -> str:
    """The font the ASS style names."""
    return opts.get("font") if opts.get("font") in FONTS else "Arial"


def _style_size(opts: dict, canvas: tuple[int, int]) -> int:
    """The style's font size on this frame."""
    size = max(40, min(140, int(opts["font_size"])))
    return max(24, round(size * (canvas[1] / 1920)))


def _style_bold(font: str) -> bool:
    """Captions are bold. A bundled display face that comes in one regular
    weight (Anton, Bebas Neue...) is already heavy: asking libass for bold
    would thicken it artificially, so its style is not marked bold."""
    weight = bundled.weight_for(font) if bundled.is_bundled(font) else None
    return not (weight is not None and weight < 600)


def _header(opts: dict, canvas: tuple[int, int] = (1080, 1920)) -> str:
    alignment, margin_l, margin_r, margin_v = _layout(opts, canvas)
    color = _ass_color(opts["color"])
    font = _style_font(opts)
    bold = -1 if _style_bold(font) else 0
    size = _style_size(opts, canvas)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas[0]}
PlayResY: {canvas[1]}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{color},{color},&H00000000,&H7F000000,{bold},0,0,0,100,100,0,0,1,7,2,{alignment},{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR (ASS stores colours little-endian)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _inline_color(hex_rgb: str) -> str:
    """#RRGGBB -> &HBBGGRR& — the form an inline \\1c override takes."""
    h = (hex_rgb or "").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def _words_in_window(segments: list[Segment], start: float, end: float) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        if seg.words:
            words.extend(w for w in seg.words if w["end"] > start and w["start"] < end)
        else:
            words.extend(_spread_evenly(seg, start, end))
    return sorted(words, key=lambda w: w["start"])


def _spread_evenly(seg: Segment, start: float, end: float) -> list[dict]:
    tokens = seg.text.split()
    if not tokens:
        return []
    step = (seg.end - seg.start) / len(tokens)
    return [
        {"start": seg.start + i * step, "end": seg.start + (i + 1) * step, "word": tok, "spread": True}
        for i, tok in enumerate(tokens)
        if seg.start + (i + 1) * step > start and seg.start + i * step < end
    ]


def _grouped(words: list[dict], size: int, start: float, end: float) -> list[list[dict]]:
    """Words in captions of `size`, a new one after every pause: when the
    words so far have been said (_said) and the next starts more than
    PAUSE_SECONDS later. `start` and `end` are the clip's, in the words' own
    time. A word of no length before a pause is left alone by it too: its
    caption is on screen in the silence after it (_alone)."""
    groups: list[list[dict]] = []
    until = 0.0                 # how far the caption being filled has been said
    for w in words:
        said_from, said_to = _said(w, start, end)
        paused = round(said_from - until, 2) > PAUSE_SECONDS
        if groups and len(groups[-1]) < size and not paused:
            groups[-1].append(w)
            until = max(until, said_to)
        else:
            groups.append([w])
            until = said_to
    return groups


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
