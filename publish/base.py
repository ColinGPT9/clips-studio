"""Publisher interface.

Each destination platform (YouTube Shorts now; TikTok, Reels later)
implements this. The scheduler only ever talks to the interface.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class Publisher(ABC):
    @abstractmethod
    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Upload one finished clip. Returns the platform's video ID."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier for logging, e.g. 'youtube_shorts'."""
