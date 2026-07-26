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

# Types whose value is the literal wording — they have to be MATCHED in a
# transcript, so they're held to the repetition rule below.
PHRASE_TYPES = ("catchphrase", "joke")

EVENT_STATUSES = ("announced", "in_progress", "completed", "stale")

# Retrieval/prompt budgets: the knowledge base can grow without bound, but
# what we KEEP per creator and SHOW the LLM stays small and recent.
MAX_KNOWLEDGE_PER_CREATOR = 200
STALE_EVENT_DAYS = 60

# ---- Repetition & dropout ---------------------------------------------------
# The LLM nominates a catchphrase from any line it finds quotable, so a thing
# said ONCE used to land in the creator's catchphrase list forever. A phrase is
# only a catchphrase if the creator actually repeats it, so phrase facts stay
# candidates — stored, shown, but never scored or shown to the LLM — until
# they've been counted in a transcript this many times (in one video or across
# several).
MIN_PHRASE_REPEATS = 3

# Dropout. Knowledge that stops being said stops being true: an old in-joke or
# a game they've moved on from shouldn't keep nudging scores. A fact goes
# dormant once BOTH are true — enough time has passed AND enough of the
# creator's videos have gone by without it being heard again. Requiring both
# means someone who processes a video a month doesn't lose their whole
# knowledge base to the calendar. Being heard again revives it instantly.
DORMANT_DAYS = 90
DORMANT_VIDEOS = 5
# A one-off that was never heard again after all that is finally deleted.
FORGET_DAYS = 180
FORGET_VIDEOS = 10


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
