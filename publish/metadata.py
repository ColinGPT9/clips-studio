"""Turning a PublishRequest into a videos.insert body.

One module owns the field list, so there is exactly one place to check against
the API reference when YouTube changes something — and so the tests can verify
the body without a network call or a Google library.

The limits here are YouTube's, and they are enforced rather than trusted: the
API rejects the whole upload for a title of 101 characters, and losing a
finished render to that would be an unkind way to find out.
"""

from publish.base import DESCRIPTION_MAX, TAGS_BUDGET, TITLE_MAX, PublishRequest

# Five is ours, not YouTube's. A description reads better with a handful of
# real tags than a wall of them, and YouTube only shows the first three above
# the title anyway. Fifteen is the cliff: past that it ignores EVERY hashtag on
# the video rather than the excess, so five sits well clear of it instead of
# near it.
MAX_HASHTAGS = 5

# Angle brackets are rejected outright in titles and descriptions.
_FORBIDDEN = str.maketrans({"<": "", ">": ""})


def clamp_title(title: str) -> str:
    """A title must be non-empty and at most 100 characters."""
    cleaned = title.translate(_FORBIDDEN).strip()
    return cleaned[:TITLE_MAX]


def clamp_description(description: str) -> str:
    return description.translate(_FORBIDDEN)[:DESCRIPTION_MAX]


def clamp_tags(tags: list[str]) -> list[str]:
    """Fit tags into YouTube's 500-character total budget.

    Drops whole tags rather than truncating one, because half a tag is not a
    tag. A tag containing a space counts as quoted, so it costs two extra
    characters — accounted for here, since ignoring it is how you end up just
    over the limit with no idea why.
    """
    kept: list[str] = []
    used = 0
    for raw in tags:
        tag = raw.lstrip("#").strip()
        if not tag:
            continue
        cost = len(tag) + (2 if " " in tag else 0)
        separator = 1 if kept else 0
        if used + separator + cost > TAGS_BUDGET:
            continue
        kept.append(tag)
        used += separator + cost
    return kept


def creator_tag(name: str) -> str:
    """A channel name as a hashtag, or "" when nothing usable is left.

    YouTube hashtags carry no spaces or punctuation, so "Some Streamer" has to
    become "#SomeStreamer": left as it is, YouTube reads the tag "#Some" and
    then some loose words.
    """
    kept = "".join(c for c in (name or "") if c.isalnum())
    return f"#{kept}" if kept else ""


def _normalise(hashtags: list[str]) -> list[str]:
    """Bare words gain their hash; blanks and lone hashes are dropped."""
    out = []
    for raw in hashtags:
        tag = (raw or "").strip()
        if not tag:
            continue
        tag = tag if tag.startswith("#") else f"#{tag}"
        if len(tag) > 1:
            out.append(tag)
    return out


def _without_trailing_hashtags(description: str) -> str:
    """The description minus a final line that is nothing but hashtags.

    Publishing a clip twice would otherwise stack a second block: the editor's
    box is prefilled with whatever was sent last time, which by then already
    ends in the line this module added.
    """
    lines = description.rstrip().split("\n")
    while lines:
        words = lines[-1].split()
        if words and all(w.startswith("#") for w in words):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def description_with_hashtags(
    description: str, hashtags: list[str], creator: str = ""
) -> str:
    """Put the clip's hashtags in the description, the creator's tag first.

    First because the list is cut at MAX_HASHTAGS, so whatever must survive
    has to lead. Duplicates fold together case-insensitively: a generated
    "#penguinz0" and a channel called "penguinz0" are one tag, not two.
    """
    tags = _normalise(([creator_tag(creator)] if creator else []) + list(hashtags))

    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)

    body = _without_trailing_hashtags(description)
    if not unique:
        return clamp_description(body)
    line = " ".join(unique[:MAX_HASHTAGS])
    return clamp_description(f"{body}\n\n{line}" if body else line)


def build_insert_body(request: PublishRequest) -> dict:
    """The request body for videos.insert.

    Only fields the public API actually accepts. The publishAt invariant is
    enforced here rather than at the call site: YouTube rejects publishAt on
    anything but a private video, and a caller that forgets would get a
    confusing 400 instead of a scheduled video.
    """
    snippet: dict = {
        "title": clamp_title(request.title),
        "description": clamp_description(request.description),
        "categoryId": str(request.category_id),
    }
    tags = clamp_tags(request.tags)
    if tags:
        snippet["tags"] = tags
    if request.default_language:
        snippet["defaultLanguage"] = request.default_language

    privacy = request.privacy
    status: dict = {
        "selfDeclaredMadeForKids": bool(request.made_for_kids),
        "embeddable": bool(request.embeddable),
        "publicStatsViewable": bool(request.public_stats_viewable),
        "license": request.license,
    }
    if request.contains_synthetic_media:
        status["containsSyntheticMedia"] = True
    if request.publish_at:
        # Scheduling IS a private upload with a publish time attached.
        privacy = "private"
        status["publishAt"] = request.publish_at
    status["privacyStatus"] = privacy

    body: dict = {"snippet": snippet, "status": status}
    if request.recording_date:
        body["recordingDetails"] = {"recordingDate": request.recording_date}
    if request.localizations:
        body["localizations"] = request.localizations
    return body


def parts_for(request: PublishRequest) -> str:
    """Which `part` values the insert call needs for this body."""
    parts = ["snippet", "status"]
    if request.recording_date:
        parts.append("recordingDetails")
    if request.localizations:
        parts.append("localizations")
    return ",".join(parts)
