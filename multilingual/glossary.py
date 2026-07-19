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


def _candidate_terms(text: str) -> list[str]:
    """Proper-noun-ish tokens: capitalised words, ALLCAPS, or CamelCase."""
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}", text or ""):
        if tok.lower() in _STOPWORDS:
            continue
        if tok.isupper() or tok[0].isupper() or re.search(r"[a-z][A-Z]", tok):
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

    for t in _candidate_terms(video_title):
        add(t)
    return terms[:limit]
