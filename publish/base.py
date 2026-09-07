"""Publisher interface.

Each destination platform (YouTube now; TikTok, Reels later) implements this.
Callers only ever talk to the interface.

This module is deliberately stdlib-only. Everything with logic in it — what a
request body may contain, how a schedule is validated, how quota is counted —
lives in modules the tests can import on a CI runner that has none of the
Google libraries installed.

Two entry points, on purpose:

* `publish()` is the real one. It takes a PublishRequest, reports progress, and
  returns everything the caller needs to tell the user what actually happened —
  including what privacy YouTube applied, which is not always what was asked for.
* `upload()` is the original 1.0 signature. `core/scheduler.py` still calls it
  positionally, so it stays exactly as it was and is now a shim over publish().
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# label, bytes done, bytes total. Total is 0 when it isn't known yet.
ProgressFn = Callable[[str, int, int], None]

# YouTube's current limits, all enforced in publish/metadata.py.
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_BUDGET = 500  # total characters across every tag, not a count


@dataclass
class PublishRequest:
    """Everything the public YouTube Data API lets a client set at upload time.

    Fields map 1:1 onto videos.insert. Anything YouTube Studio can do that is
    not in this list is not exposed by the API — see docs/API.md rather than
    adding a field here and hoping.
    """

    video_path: Path
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "22"  # People & Blogs
    privacy: str = "private"  # public | unlisted | private
    # RFC 3339 UTC. Setting this forces privacy to private — YouTube rejects
    # publishAt on anything else, and the video must never have been published.
    publish_at: str | None = None
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    embeddable: bool = True
    public_stats_viewable: bool = True
    license: str = "youtube"  # youtube | creativeCommon
    default_language: str | None = None
    recording_date: str | None = None
    localizations: dict[str, dict[str, str]] | None = None
    thumbnail: Path | None = None
    playlist_id: str | None = None
    notify_subscribers: bool = True


@dataclass
class PublishResult:
    """What happened. `actual_privacy` is the important one.

    An API project that has not passed YouTube's compliance audit has its
    uploads locked to private no matter what was requested, and the lock cannot
    be appealed or undone in Studio. Comparing requested against actual is the
    only way to notice, and it costs nothing — the insert response already
    carries it.
    """

    video_id: str
    url: str
    requested_privacy: str
    actual_privacy: str
    publish_at: str | None = None
    channel_id: str = ""
    channel_title: str = ""
    thumbnail_set: bool = False
    playlist_added: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def locked_private(self) -> bool:
        """True when YouTube overrode a public/unlisted request with private.

        A scheduled upload is private on purpose, so it is never a lock.
        """
        if self.publish_at:
            return False
        return self.requested_privacy in ("public", "unlisted") and self.actual_privacy == "private"

    @property
    def studio_url(self) -> str:
        return f"https://studio.youtube.com/video/{self.video_id}/edit"


class Publisher(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier for logging, e.g. 'youtube_shorts'."""

    @abstractmethod
    def publish(
        self,
        request: PublishRequest,
        on_progress: ProgressFn | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        """Upload one finished clip with its metadata."""

    @property
    def default_privacy(self) -> str:
        return "private"

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Upload one finished clip. Returns the platform's video ID.

        The original 1.0 entry point, kept byte-compatible because
        core/scheduler.py::upload_scheduled still calls it. New code should
        call publish() instead — this throws away everything except the id.
        """
        return self.publish(
            PublishRequest(
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=self.default_privacy,
            )
        ).video_id
