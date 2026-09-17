"""Chapter timestamps for an assembled highlight video.

A highlight video is a dozen unrelated moments joined end to end, which is
exactly the video a viewer wants to skip around in. YouTube builds that
skipping UI for free if the description carries timestamps, so this turns the
keep-ranges into the lines that produce it:

    0:00 that's the worst plan I have ever heard
    1:24 chat convinced him to try it anyway
    3:58 it worked

Timestamps are positions in the FINISHED video, not the stream: each chapter
starts where the previous ranges have already used up. Names come from the
moment's own hook, which is a line actually spoken in it.

**The rules are YouTube's, and breaking any of them makes YouTube ignore every
chapter silently** (checked against support.google.com/youtube/answer/9884579):

  * the first timestamp must be 0:00
  * there must be at least three
  * each chapter must run 10 seconds or longer
  * they must ascend

So `chapter_lines` returns nothing at all rather than a list that looks right
and does nothing. A highlight video of two long moments has no chapters, which
is the honest outcome.

Highlights only. Edited Stream assembles downtime-trimmed spans of one
continuous stream, and "Section 2, Section 3" is not information.
"""

MIN_CHAPTERS = 3      # fewer and YouTube ignores the lot
MIN_SECONDS = 10.0    # so does a chapter shorter than this
MAX_TITLE = 80        # a chapter title is a label, not a sentence


def _timestamp(seconds: float) -> str:
    """0:00, 4:07, or 1:02:33 past the hour. Seconds are floored: a chapter
    must not start a fraction before the cut it names, or the first frame a
    viewer sees belongs to the moment before."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _title_for(start: float, end: float, candidates, index: int) -> str:
    """The best-scoring moment inside this range names it.

    Ranges are merged when picks are close together, so one range can contain
    several moments; the strongest is the one worth naming it after. Falls
    back to a plain number, because an unnamed chapter still helps someone
    skip, and dropping the whole list over one missing hook would not.
    """
    inside = [c for c in candidates if c.start < end and c.end > start]
    best = max(inside, key=lambda c: c.score, default=None)
    title = " ".join((getattr(best, "hook", "") or "").split()) if best else ""
    if not title:
        return f"Moment {index}"
    return title[: MAX_TITLE - 1].rstrip() + "…" if len(title) > MAX_TITLE else title


def chapter_lines(keep: list[tuple[float, float]], candidates) -> list[str]:
    """Description lines for the assembled video, or [] when YouTube would
    refuse them.

    `keep` is the chronological (start, end) list that was assembled, in the
    same order it was assembled in. `candidates` is the scored moment list the
    ranges were chosen from.
    """
    if len(keep) < MIN_CHAPTERS:
        return []
    if any(end - start < MIN_SECONDS for start, end in keep):
        # One short cut would void every chapter, so say no to all of them
        # rather than shipping a list that quietly does nothing.
        return []

    lines, offset = [], 0.0
    for index, (start, end) in enumerate(keep, start=1):
        lines.append(f"{_timestamp(offset)} {_title_for(start, end, candidates, index)}")
        offset += end - start
    return lines


def with_chapters(description: str, keep: list[tuple[float, float]], candidates) -> str:
    """The description with a chapter list appended, or unchanged when there
    are no usable chapters."""
    lines = chapter_lines(keep, candidates)
    if not lines:
        return description
    return description.rstrip() + "\n\nChapters:\n" + "\n".join(lines)
