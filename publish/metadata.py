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


def _strip_our_last_tag_line(description: str, ours: set[str]) -> str:
    """The description minus the tag line THIS module put there last time.

    Publishing a clip twice would otherwise stack a second line: the editor's
    box is prefilled with whatever went up before, which already ends in one.

    Recognised by content, not by position. A creator's standing block can end
    in a hashtag of its own, and removing any trailing hashtag line would eat
    that on the first publish, leave the block no longer matching, and re-add
    the whole thing on the next one, growing the description forever. A line
    only qualifies if every tag on it is one we are about to write anyway.
    """
    lines = description.rstrip().split("\n")
    if lines:
        words = lines[-1].split()
        if (
            words
            and all(w.startswith("#") for w in words)
            and {w.lower() for w in words} <= ours
        ):
            lines.pop()
    return "\n".join(lines).rstrip()


def with_common_block(description: str, common: str) -> str:
    """The description with the creator's standing block under it.

    The block is the same on every video: where to watch live, the Discord,
    the socials. It goes between the clip's own description and the hashtag
    line, which is where a viewer expects it and where it does not push the
    first sentence out of the preview.

    Skipped when the text is already there, so re-publishing a clip does not
    repeat the links.
    """
    block = (common or "").strip()
    if not block:
        return description
    body = description.rstrip()
    if block in body:
        return body
    return f"{body}\n\n{block}" if body else block


def description_with_hashtags(
    description: str, hashtags: list[str], creator: str = ""
) -> str:
    """Put the clip's hashtags in the description, the creator's tag first.

    First because the list is cut at MAX_HASHTAGS, so whatever must survive
    has to lead. Duplicates fold together case-insensitively: a generated
    "#creatorname" and a channel called "CreatorName" are one tag, not two.
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

    body = _strip_our_last_tag_line(description, {tag.lower() for tag in unique})
    if not unique:
        return clamp_description(_strip_our_last_tag_line(description, set()))
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
