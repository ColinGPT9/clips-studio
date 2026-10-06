"""LLM-generated upload metadata: title, description, hashtags.

Never fails the pipeline: any LLM misbehavior falls back to metadata
derived from the clip's hook and the source video title.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.models import ClipCandidate, Segment
from llm.base import LLMBackend, generate_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata.txt"

MAX_TITLE_LEN = 95  # leave headroom under YouTube's 100-char limit

# The shapes metadata.txt and metadata_batch.txt ask for. A cloud model on the
# user's key is held to them (llm.base.generate_json); _parse still checks.
_METADATA_FIELDS = {
    "title": {"type": "string"},
    "description": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
}
METADATA_SCHEMA = {
    "type": "object",
    "properties": _METADATA_FIELDS,
    "required": ["title", "description", "hashtags"],
    "additionalProperties": False,
}
# The highlights post style (video/post_style.py) also asks for the two
# lines of its title card.
_CARD_FIELDS = {"headline": {"type": "string"}, "subline": {"type": "string"}}
BATCH_SCHEMA_HIGHLIGHTS = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, **_METADATA_FIELDS, **_CARD_FIELDS},
        "required": ["index", "title", "description", "hashtags", "headline", "subline"],
        "additionalProperties": False,
    }}},
    "required": ["items"],
    "additionalProperties": False,
}
BATCH_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, **_METADATA_FIELDS},
        "required": ["index", "title", "description", "hashtags"],
        "additionalProperties": False,
    }}},
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass
class ClipMetadata:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    # The highlights post style's title card (video/post_style.py). Empty
    # for every other style; never stored as metadata, only as render options.
    headline: str = ""
    subline: str = ""


def generate_metadata(
    candidate: ClipCandidate,
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
) -> ClipMetadata:
    clip_text = " ".join(
        s.text for s in segments if s.end > candidate.start and s.start < candidate.end
    )
    fallback = _fallback(candidate, video_title)
    if not clip_text:
        return fallback

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{video_title}", video_title)
        .replace("{clip_text}", clip_text)
    )
    try:
        raw = generate_json(llm, prompt, METADATA_SCHEMA)
        parsed = _parse(raw)
    except Exception:
        parsed = None
    if parsed is None:
        return fallback

    title = _clean_title(parsed.get("title", "")) or fallback.title
    description = str(parsed.get("description", "")).strip() or fallback.description
    hashtags = _clean_hashtags(parsed.get("hashtags", [])) or fallback.hashtags
    return ClipMetadata(title=title, description=description, hashtags=hashtags)


BATCH_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata_batch.txt"
# Per post style (video/post_style.py): the words written to match the look.
# A style not listed here writes the usual Shorts metadata.
STYLE_PROMPT_PATHS = {
    "highlights": BATCH_PROMPT_PATH.with_name("metadata_batch_highlights.txt"),
}


def generate_metadata_batch(
    candidates: list[ClipCandidate],
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
    batch_size: int = 8,
    creator_context: str = "",
    rules: str = "",
    style: str = "default",
) -> list[ClipMetadata]:
    """Metadata for ALL clips in a few LLM calls instead of one per clip —
    on a long stream this cuts dozens of model calls from the analysis time.
    Any clip the model skips or mangles falls back to hook-based metadata.
    creator_context (optional): learned facts about the creator — series
    names, running jokes, collaborators — for more accurate titles/hashtags.
    rules (optional): a sport's own rules for its clips' titles (basketball:
    which player to name); "" for every other job, whose prompt is unchanged.
    style: the clip's post style; "highlights" writes highlight-page captions."""
    results: list[ClipMetadata] = [_fallback(c, video_title) for c in candidates]
    template = STYLE_PROMPT_PATHS.get(style, BATCH_PROMPT_PATH).read_text(encoding="utf-8")
    card = style == "highlights"
    schema = BATCH_SCHEMA_HIGHLIGHTS if card else BATCH_SCHEMA
    if rules:
        template = template.replace("{clips}", "RULES FOR THESE CLIPS:\n" + rules + "\n\n{clips}")
    if creator_context:
        template = template.replace(
            "{clips}",
            "CREATOR CONTEXT (background knowledge — use for accuracy when relevant,"
            " never invent beyond it):\n" + creator_context + "\n\n{clips}",
        )

    for base in range(0, len(candidates), batch_size):
        batch = candidates[base : base + batch_size]
        blocks = []
        for i, c in enumerate(batch):
            text = _clip_text(c, segments)[:900]
            blocks.append(f"CLIP {i}{_scoreboard_note(c)}:\n{text or '(no speech)'}")
        prompt = (
            template.replace("{video_title}", video_title)
            .replace("{count}", str(len(batch)))
            .replace("{clips}", "\n\n".join(blocks))
        )
        try:
            data = _parse(generate_json(llm, prompt, schema))
        except Exception:
            data = None
        if not data or not isinstance(data.get("items"), list):
            continue  # whole batch falls back
        for item in data["items"]:
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            fallback = results[base + idx]
            results[base + idx] = ClipMetadata(
                title=_clean_title(item.get("title", "")) or fallback.title,
                description=str(item.get("description", "")).strip() or fallback.description,
                hashtags=_clean_hashtags(item.get("hashtags", [])) or fallback.hashtags,
                headline=_clean_card_line(item.get("headline", ""), 40) if card else "",
                subline=_clean_card_line(item.get("subline", ""), 48) if card else "",
            )
    for c, m in zip(candidates, results):
        _earned(c, m)
    return results


_PERIODS = {"Q1": "1st quarter", "Q2": "2nd quarter", "Q3": "3rd quarter", "Q4": "4th quarter",
            "H1": "1st half", "H2": "2nd half", "OT": "overtime"}
_SCORE = re.compile(r"\bscore (\d+-\d+)")
# The points a score change was worth, as ScoreChange.label() writes it ("score 81-79 (SAS), +2").
_POINTS = re.compile(r"\bscore \d+-\d+(?: \([^)]*\))?, \+(\d)\b")
_CRUNCH = 120  # seconds left in the last quarter (or overtime) that make crunch time


def _crunch(c: ClipCandidate) -> bool | None:
    """Whether the scoreboard says a clip's moment came in crunch time: the
    last two minutes of the 4th quarter (or 2nd half) or overtime. None for
    a clip with no game clock read (every sport but basketball)."""
    when = str((c.subscores or {}).get("sport_when") or "")
    if not when:
        return None
    period, _, left = when.partition(" ")
    try:
        minutes, seconds = (int(x) for x in left.split(":"))
        return period in ("Q4", "H2", "OT") and minutes * 60 + seconds <= _CRUNCH
    except ValueError:
        return period in ("Q4", "H2", "OT")


def _scoreboard_note(c: ClipCandidate) -> str:
    """What the game's own scoreboard says about a clip's moment, for its
    title: the play, the team, the score in words (whose is whose and who
    leads), the quarter and the clock, and when it was not crunch time. On
    an NBA game the titles called a 3rd quarter put-back "Late-Game" and a
    shot with 11:30 left "Clutch". Only a sport that reads the game's clock
    (basketball) sets one; "" for every other clip, whose prompt is
    unchanged."""
    crunch = _crunch(c)
    if crunch is None:
        return ""
    s = c.subscores or {}
    period, _, left = str(s["sport_when"]).partition(" ")
    play = str(s.get("sport_label") or "a play")
    team = str(s.get("sport_team") or "")
    context = str(s.get("sport_context") or "")
    why = str(s.get("sport_why") or "")
    score, points = _SCORE.search(why), _POINTS.search(why)
    # Its points: on an NBA game a "Step-back" two was described as a three.
    words = [play + (f" ({points.group(1)} points)" if points else "") + (f" by {team}" if team else "")]
    # The score only with whose is whose: "making it 97-86" alone had the
    # titles put the team the commentary named ahead, the wrong one.
    if score and not context and team:
        words[0] += f", making it {score.group(1)}"
    if context:
        words.append(context)
    if points:
        # Who scored, as the commentary says it (sports/basketball/commentary.py):
        # left to the model, a three went to the player who passed for it.
        player = str(s.get("sport_player") or "")
        words.append(f"the commentary says {player} scored it" if player
                     else "the commentary doesn't say who scored it")
    words.append(_PERIODS.get(period, period) + (f" with {left} left" if left else ""))
    if not crunch:
        words.append("not crunch time, so not clutch or late-game")
    return " (the scoreboard: " + "; ".join(words) + ")"


def _clip_text(c: ClipCandidate, segments: list[Segment]) -> str:
    """What is said in a clip, for its title: the sentences it overlaps. A
    clip with a game clock read (basketball) gets only the words said inside
    it: a highlights package's commentary runs on from play to play, and on
    an NBA game the sentences around 12 s clips had the titles name players
    from the plays before and after them."""
    if _crunch(c) is None:
        return " ".join(s.text for s in segments if s.end > c.start and s.start < c.end)
    words = []
    for s in segments:
        if s.end <= c.start or s.start >= c.end:
            continue
        if not s.words:
            words.append(s.text)
            continue
        words += [w["word"] for w in s.words if c.start <= (w["start"] + w["end"]) / 2 <= c.end]
    return " ".join(words)


# What only crunch time earns. On an NBA game, with the note saying it wasn't
# crunch time, a 2nd-quarter basket was still titled "Clutch", one with 7:49
# left "Late-Game" and a three at +12 "Game-Changing".
_CLUTCH = re.compile(r"\b(?:clutch|late[- ]game|crunch[- ]time|game[- ]changing)\b[ \t]*", re.IGNORECASE)


def _earned(c: ClipCandidate, metadata: ClipMetadata) -> None:
    """A title and description that don't call a clip clutch (or
    late-game, crunch-time, game-changing) when the scoreboard says it
    wasn't crunch time. Every other clip as written."""
    if _crunch(c) is not False:
        return
    title = re.sub(r"\s{2,}", " ", _CLUTCH.sub("", metadata.title)).strip()
    metadata.title = title if re.search(r"\w", title) else (_clean_title(c.hook) or metadata.title)
    metadata.description = re.sub(r"[ \t]{2,}", " ", _CLUTCH.sub("", metadata.description)).strip()


def _fallback(candidate: ClipCandidate, video_title: str) -> ClipMetadata:
    """What a clip carries when the model could not write its metadata.

    No description and no hashtags. This used to be "Clip from: <video title>"
    with #clips, and every clip of a video whose metadata failed got that same
    caption: seven TikTok posts went out reading "Clip from: my brother exposes
    me… #clips" and were flagged as unoriginal content, which is exactly what a
    caption announcing a repost invites. The title still comes from the hook,
    and the creator's and required hashtags are added where they always were.
    """
    title = _clean_title(candidate.hook) or _clean_title(video_title) or "Clip"
    return ClipMetadata(title=title, description="", hashtags=[])


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_title(title: str) -> str:
    title = re.sub(r"[<>]", "", str(title)).strip().strip('"')
    return title[:MAX_TITLE_LEN].strip()


def _clean_card_line(text, limit: int) -> str:
    """One line of the highlights title card: one line, no quotes, no
    hashtags, and short. An overlong one is cut at a word; the card shrinks
    and wraps what is left (video/post_style.py)."""
    text = re.sub(r"\s+", " ", re.sub(r"[<>\"]", "", str(text or ""))).strip()
    # A hashtag has a letter in it: "#1 PICK" and "#23" are a draft rank and a
    # jersey number, and stay (the card's own filter in video/post_style.py).
    text = re.sub(r"(^|\s)#(?=\w*[^\W\d_])\w+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text


def _clean_hashtags(tags) -> list[str]:
    """One hashtag per entry, at most five.

    The model sometimes answers with several run together in one string,
    "#creatorname#drama#apology", and keeping that as one tag put it into
    posts as a single unreadable hashtag. Split on every # and every space.
    """
    if not isinstance(tags, list):
        return []
    cleaned: list[str] = []
    for entry in tags:
        for part in re.split(r"[#\s]+", str(entry).lower()):
            word = re.sub(r"[^\w]", "", part)
            tag = f"#{word}"
            if word and tag != "#shorts" and tag not in cleaned:
                cleaned.append(tag)
    return cleaned[:5]
