"""Dataclasses for the creator-intelligence layer."""

from dataclasses import dataclass, field


# knowledge_type values the extractor may store — anything else is discarded.
KNOWLEDGE_TYPES = (
    "topic",         # recurring subject matter ("fitness", "speedrunning")
    "game",          # game/category they play or cover
    "series",        # named recurring format ("Monday Mailbag")
    "catchphrase",   # phrase the creator repeatedly says
    "joke",          # running joke / recurring bit
    "collaborator",  # person who appears with them
    "format",        # structural pattern ("reacts to fan clips at the end")
    "life",          # personal detail usable as a callback (pet, family, habit)
)

EVENT_STATUSES = ("announced", "in_progress", "completed", "stale")

# Retrieval/prompt budgets: the knowledge base can grow without bound, but
# what we KEEP per creator and SHOW the LLM stays small and recent.
MAX_KNOWLEDGE_PER_CREATOR = 200
STALE_EVENT_DAYS = 60


@dataclass
class CreatorProfile:
    creator_id: int
    display_name: str
    aliases: list[str] = field(default_factory=list)
    learning_enabled: bool = True


@dataclass
class PlatformAccount:
    account_id: int
    creator_id: int
    platform: str          # youtube | twitch | kick
    platform_account_id: str
    username: str = ""
    display_name: str = ""


@dataclass
class KnowledgeItem:
    knowledge_type: str
    information: str
    confidence: str = "medium"   # high | medium (low is discarded at extraction)
    source_video: str | None = None


@dataclass
class CreatorEvent:
    event_name: str
    description: str = ""
    status: str = "announced"
    source_video: str | None = None
