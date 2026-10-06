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

import re
from pathlib import Path

from core.models import ClipCandidate, Segment

DEFAULT_STYLE = {
    "font": "Arial",          # must be in FONTS (installed on stock Windows)
    "font_size": 84,          # at 1080x1920 playback resolution
    "color": "#FFFFFF",       # text colour (hex RGB)
    "position": "bottom",     # bottom | middle | top
    "words_per_caption": 3,
    "uppercase": True,
    "highlight": False,           # light up each word as it is spoken
    "highlight_color": "#FFE600",
    "second_speaker": False,      # another colour when it isn't the main speaker talking (#126)
    "second_speaker_color": "#5CE1FF",
}

# Fonts shipped with every stock Windows install, so a burned clip renders
# identically on any user's machine. Whitelisted: the name goes into the ASS
# header, and unknown names would silently fall back to a default anyway.
FONTS = [
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

_LATIN_ONLY = set(FONTS[:10])


def caption_font_for(language: str, chosen: str | None) -> str | None:
    """The font captions should actually burn with: the user's choice,
    unless the content language needs a script that choice can't render."""
    lang = (language or "en").lower()
    if lang in SCRIPT_FONTS and (chosen is None or chosen in _LATIN_ONLY):
        return SCRIPT_FONTS[lang]
    return chosen

# position -> (ASS numpad alignment, vertical margin)
_POSITIONS = {"bottom": (2, 440), "middle": (5, 0), "top": (8, 140)}


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
    for speaker, said in _by_speaker(words, turns, candidate.start, candidate.end):
        groups = _grouped(said, size)
        if heard:
            groups = _standing(groups, size, candidate.start, candidate.end)
        for group in groups:
            start = max(0.0, group[0]["start"] - candidate.start)
            last = group[-1]["end"]
            if heard:
                # A short word at the end can be timed inside the word before
                # it: the caption stays up until that word has been said.
                stands = [i for i, w in enumerate(group) if _stands(w, candidate.start, candidate.end)]
                last = max(w["end"] for w in group[stands[-1] if stands else 0:])
            end = min(candidate.duration, last - candidate.start)
            if end <= start:
                continue
            line = {"start": round(start, 2), "end": round(end, 2), "text": " ".join(w["word"] for w in group)}
            if speaker:
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
    of those stay as they are without the option."""
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
    for group in _grouped(head, size) + _grouped(tail, size):
        start = max(0.0, group[0]["start"] - new_start)
        end = min(duration, group[-1]["end"] - new_start)
        if end > start:
            added.append(
                {"start": round(start, 2), "end": round(end, 2), "text": " ".join(w["word"] for w in group)}
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
) -> Path | None:
    """Write an ASS file with times relative to the clip start.
    `lines` (user-corrected caption text) overrides the generated ones.
    `canvas` is the output frame (default portrait Shorts; longform passes
    1920x1080 and the style scales to it). Returns the path, or None if
    there is nothing to caption."""
    opts = {**DEFAULT_STYLE, **(style or {})}
    # Non-Latin content: swap in a font that has the glyphs (see SCRIPT_FONTS).
    opts["font"] = caption_font_for(language, opts.get("font")) or opts.get("font")
    if lines is None:
        lines = build_caption_lines(segments, candidate, opts["words_per_caption"])

    # Word timings for the highlight, clip-relative like `lines` are.
    spoken = (
        [
            {"start": w["start"] - candidate.start, "end": w["end"] - candidate.start,
             "word": w["word"]}
            for w in _words_in_window(segments, candidate.start, candidate.end)
        ]
        if opts.get("highlight")
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
        if opts.get("highlight"):
            dialogue.extend(_highlighted(text, start, end, spoken, opts, tint))
        else:
            dialogue.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{_tinted(text, tint)}"
            )

    if not dialogue:
        return None
    output_path.write_text(_header(opts, canvas) + "\n".join(dialogue) + "\n", encoding="utf-8")
    return output_path


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
    inside = [w for w in spoken if start - 0.01 <= (w["start"] + w["end"]) / 2 <= end + 0.01]
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


def _header(opts: dict, canvas: tuple[int, int] = (1080, 1920)) -> str:
    alignment, margin_v = _POSITIONS.get(opts["position"], _POSITIONS["bottom"])
    color = _ass_color(opts["color"])
    size = max(40, min(140, int(opts["font_size"])))
    font = opts.get("font") if opts.get("font") in FONTS else "Arial"
    # Style values are calibrated for the 1920-tall Shorts canvas; scale
    # them to whatever frame this clip renders at (e.g. landscape 1080).
    scale = canvas[1] / 1920
    size = max(24, round(size * scale))
    margin_v = round(margin_v * scale)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas[0]}
PlayResY: {canvas[1]}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{color},{color},&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,7,2,{alignment},60,60,{margin_v},1

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
        {"start": seg.start + i * step, "end": seg.start + (i + 1) * step, "word": tok}
        for i, tok in enumerate(tokens)
        if seg.start + (i + 1) * step > start and seg.start + i * step < end
    ]


def _grouped(words: list[dict], size: int) -> list[list[dict]]:
    return [words[i : i + size] for i in range(0, len(words), size)]


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
