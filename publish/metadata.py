"""Turning a PublishRequest into a videos.insert body.

One module owns the field list, so there is exactly one place to check against
the API reference when YouTube changes something — and so the tests can verify
the body without a network call or a Google library.

The limits here are YouTube's, and they are enforced rather than trusted: the
API rejects the whole upload for a title of 101 characters, and losing a
finished render to that would be an unkind way to find out.
"""

from publish.base import DESCRIPTION_MAX, TAGS_BUDGET, TITLE_MAX, PublishRequest

# YouTube ignores ALL hashtags in a description once there are more than 15,
# rather than just the excess. Our generated descriptions append the clip's
# hashtags, so this is easy to trip.
MAX_HASHTAGS = 15

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


def description_with_hashtags(description: str, hashtags: list[str]) -> str:
    """Append the clip's hashtags, capped so YouTube doesn't ignore them all."""
    tags = [h if h.startswith("#") else f"#{h}" for h in hashtags if h.strip()]
    if not tags:
        return clamp_description(description)
    body = description.rstrip()
    line = " ".join(tags[:MAX_HASHTAGS])
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
