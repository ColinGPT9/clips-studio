"""Terms that must survive translation unchanged.

A creator's channel name, the games they play, their collaborators and
their catchphrases are the things a translator most often mangles — and
they're exactly what the app already learned into creator_knowledge. This
turns that into a do-not-translate list the translation prompt carries, so
"Emjayplays" stays "Emjayplays" and a catchphrase renders the same way in
every video instead of drifting per clip.

Read-only against the existing tables: nothing here writes to or changes
creator data.
"""

import re

# Knowledge types whose text names a THING (proper nouns), versus types
# that describe behaviour and shouldn't be mined for terms.
_NAME_TYPES = ("game", "series", "collaborator", "catchphrase")
_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "they", "them", "their",
    "about", "into", "when", "what", "where", "which", "while", "would",
    "could", "should", "have", "has", "had", "for", "his", "her", "its",
}


def _unmistakable(tok: str) -> bool:
    """ALLCAPS, CamelCase or digit-bearing — a name no matter the context."""
    return bool(tok.isupper() or re.search(r"[a-z][A-Z]", tok) or re.search(r"\d", tok))


def _candidate_terms(text: str, strict: bool = False) -> list[str]:
    """Names worth protecting from translation.

    strict=True keeps ONLY unmistakable ones. Clip titles are Title Case,
    so every ordinary word there looks like a proper noun — "Possible
    Sibling & Candy Challenge" put Possible/Sibling/Candy/Challenge on the
    do-not-translate list and the model then (correctly) refused to
    translate the title at all.

    strict=False additionally keeps multi-word capitalised sequences —
    "Sara Saffari" is a real name, "Black" on its own is not."""
    text = text or ""
    out: list[str] = []
    if not strict:
        for seq in re.findall(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text):
            if not all(w.lower() in _STOPWORDS for w in seq.split()):
                out.append(seq)
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}", text):
        if tok.lower() in _STOPWORDS:
            continue
        if _unmistakable(tok):
            out.append(tok)
    return out


def build(db, creator_id: int | None, video_title: str = "", limit: int = 25) -> list[str]:
    """Do-not-translate terms for this creator, most useful first.

    Never raises and never blocks translation: with no creator or no
    knowledge it simply returns what it can find in the video title."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        key = t.lower()
        if key not in seen and 2 < len(t) <= 40:
            seen.add(key)
            terms.append(t)

    try:
        if creator_id is not None:
            row = db.conn.execute(
                "SELECT display_name FROM creators WHERE creator_id = ?", (creator_id,)
            ).fetchone()
            if row and row["display_name"]:
                add(str(row["display_name"]))
            for k in db.conn.execute(
                "SELECT knowledge_type, information FROM creator_knowledge"
                " WHERE creator_id = ? ORDER BY knowledge_id DESC LIMIT 60",
                (creator_id,),
            ):
                if k["knowledge_type"] in _NAME_TYPES:
                    for t in _candidate_terms(k["information"]):
                        add(t)
            for a in db.conn.execute(
                "SELECT username FROM platform_accounts WHERE creator_id = ?", (creator_id,)
            ):
                if a["username"]:
                    add(str(a["username"]))
    except Exception:
        pass  # glossary is a bonus, never a blocker

    # Titles are Title Case: only unmistakable names from them.
    for t in _candidate_terms(video_title, strict=True):
        add(t)
    return terms[:limit]
