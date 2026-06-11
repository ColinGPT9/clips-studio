"""Shared dataclasses passed between pipeline stages.

Stages communicate only through these types (and files on disk), never by
importing each other's internals.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadedVideo:
    """A source video fetched by a VideoSource."""

    video_id: str
    title: str
    path: Path
    duration: float  # seconds


@dataclass
class Segment:
    """One transcript segment with timestamps in seconds.

    `words` holds word-level timing as [{"start", "end", "word"}] when the
    transcriber provides it (used for word-synced captions); may be None
    for transcripts cached before word timestamps were enabled.
    """

    start: float
    end: float
    text: str
    words: list | None = None


@dataclass
class ClipCandidate:
    """A scored clip moment proposed by the LLM analyzer."""

    start: float
    end: float
    score: int
    hook: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlap_ratio(self, other: "ClipCandidate") -> float:
        """Fraction of the shorter clip covered by the overlap with `other`."""
        overlap = min(self.end, other.end) - max(self.start, other.start)
        if overlap <= 0:
            return 0.0
        return overlap / min(self.duration, other.duration)


@dataclass
class Rejection:
    """A candidate dropped by duplicate prevention, with the audit reason."""

    candidate: ClipCandidate
    reason: str  # timestamp_overlap | transcript_similarity | segment_reuse | below_min_score | over_limit
    kept: ClipCandidate | None = None  # the winning clip it duplicated, if any


@dataclass
class RenderedClip:
    """A finished MP4 clip on disk, ready for metadata/upload stages later."""

    source_video_id: str
    candidate: ClipCandidate
    path: Path
