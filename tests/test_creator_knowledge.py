"""Catchphrase repetition and knowledge dropout.

The bug these exist for: the extractor took the model's word for what a
catchphrase was, so any quotable line said ONCE became a permanent
"catchphrase" and fed the scoring bonus forever. Every phrase row in a real
library had been heard exactly once.
"""

import pytest

from creator import extractor, retrieval
from creator.models import MIN_PHRASE_REPEATS


# --------------------------------------------------------------- counting
@pytest.mark.parametrize(
    "phrase,text,expected",
    [
        # Punctuation and case must not matter — Whisper writes the same
        # phrase differently every time it appears.
        ("let's get it", "Let's get it! okay. let's get it, again. LET'S GET IT", 3),
        ("we are ready", "We are ready. We are ready! we are ready?", 3),
        # Substrings of longer words must not count.
        ("go", "going gone golden", 0),
        ("run it back", "nothing like that here", 0),
    ],
)
def test_counts_real_occurrences(phrase, text, expected):
    assert retrieval.count_phrase(phrase, text) == expected


def test_phrase_of_strips_the_explanation():
    # The extractor stores "phrase - what it means"; only the phrase itself
    # can be matched against a transcript.
    assert retrieval.phrase_of("let's get it - his hype phrase") == "let's get it"
    assert retrieval.phrase_of("'quoted phrase'") == "quoted phrase"


def test_filler_only_phrases_are_rejected():
    # "you know what I mean" repeats constantly and means nothing.
    assert not retrieval.is_phrase_like("the a is on")
    assert retrieval.is_phrase_like("let's get it")


# --------------------------------------------------------------- the gate
def test_phrase_said_once_is_not_a_catchphrase(db, creator, segments):
    transcript = " ".join(s.text for s in segments)
    facts = [
        # Said twice in this transcript — still short of the threshold.
        {"type": "catchphrase", "information": "let's get it - hype phrase", "confidence": "high"},
        {"type": "catchphrase", "information": "I only said this once", "confidence": "high"},
    ]
    stored = extractor._store_facts(db, creator, "vid1", facts, transcript)
    assert stored == 2, "both are kept as candidates, they just cannot be used yet"

    # Nothing has repeated enough to be usable, so there is no context at all.
    # context_for returning None IS the pass here: an unconfirmed phrase must
    # never reach scoring or the metadata prompt.
    ctx = retrieval.context_for(db, creator)
    phrases = ctx.phrases if ctx else []
    assert "I only said this once" not in phrases
    assert "let's get it" not in phrases


def test_repeated_phrase_becomes_usable(db, creator):
    transcript = "let's get it. let's get it! okay let's get it again."
    facts = [{"type": "catchphrase", "information": "let's get it", "confidence": "high"}]
    extractor._store_facts(db, creator, "vid1", facts, transcript)

    row = db.conn.execute("SELECT times_seen FROM creator_knowledge").fetchone()
    assert row["times_seen"] >= MIN_PHRASE_REPEATS

    ctx = retrieval.context_for(db, creator)
    assert "let's get it" in ctx.phrases


def test_invented_phrase_is_discarded(db, creator):
    """The model paraphrases constantly. A phrase that is not in the
    transcript at all was never said."""
    facts = [{"type": "catchphrase", "information": "never uttered by anyone", "confidence": "high"}]
    stored = extractor._store_facts(db, creator, "vid1", facts, "completely different words here")
    assert stored == 0
    assert db.conn.execute("SELECT COUNT(*) c FROM creator_knowledge").fetchone()["c"] == 0


def test_one_video_cannot_confirm_itself(db, creator):
    """Chunks are sampled from a single transcript and the model repeats
    itself across them. Without a guard, a fact would confirm itself off one
    video's chunking."""
    transcript = "I only said this once, honestly."
    facts = [{"type": "catchphrase", "information": "I only said this once", "confidence": "high"}]

    extractor._store_facts(db, creator, "vid1", facts, transcript)
    extractor._store_facts(db, creator, "vid1", facts, transcript)  # another chunk
    extractor._store_facts(db, creator, "vid1", facts, transcript)

    seen = db.conn.execute("SELECT times_seen FROM creator_knowledge").fetchone()["times_seen"]
    assert seen == 1, "the same video reinforced a fact more than once"


def test_a_second_video_does_reinforce(db, creator):
    facts = [{"type": "catchphrase", "information": "I only said this once", "confidence": "high"}]
    extractor._store_facts(db, creator, "vid1", facts, "I only said this once.")
    extractor._store_facts(db, creator, "vid2", facts, "I only said this once.")

    seen = db.conn.execute("SELECT times_seen FROM creator_knowledge").fetchone()["times_seen"]
    assert seen == 2


# --------------------------------------------------------------- scoring
def test_unconfirmed_phrase_scores_nothing(db, creator):
    facts = [{"type": "catchphrase", "information": "I only said this once", "confidence": "high"}]
    extractor._store_facts(db, creator, "vid1", facts, "I only said this once.")

    ctx = retrieval.context_for(db, creator)
    bonus, _ = retrieval.context_bonus("I only said this once, honestly", ctx)
    assert bonus == 0


def test_confirmed_phrase_scores_but_is_capped(db, creator):
    facts = [{"type": "catchphrase", "information": "let's get it", "confidence": "high"}]
    extractor._store_facts(db, creator, "vid1", facts, "let's get it. let's get it. let's get it.")

    ctx = retrieval.context_for(db, creator)
    bonus, reasons = retrieval.context_bonus("man let's get it, we up", ctx)
    assert bonus > 0
    assert reasons
    # Additive and capped: learned data must never be able to dominate a score.
    assert bonus <= 6


def test_bonus_is_never_negative(db, creator):
    """The whole design rests on this: accumulated knowledge can only ever
    raise a score, so it cannot degrade clip quality over time."""
    ctx = retrieval.context_for(db, creator)  # nothing learned yet
    bonus, _ = retrieval.context_bonus("any text at all", ctx)
    assert bonus == 0


# --------------------------------------------------------------- dropout
def test_silence_is_not_evidence_without_videos(db, creator):
    """Nothing goes dormant until the creator has actually posted videos it
    could have been said in. Otherwise someone who processes one video a
    month loses their whole knowledge base to the calendar."""
    assert retrieval.dormant_before(db, creator, days=90, videos=5) is None
