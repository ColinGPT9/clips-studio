"""Translate the post itself — title, description, hashtags.

Captions alone don't let anyone publish: a creator with a Spanish version
of their clip still has to write a Spanish title and description, which is
exactly what they can't do if they don't speak it. This writes a ready-to-
paste post text file per language beside the video.

Falls back to the original text on any failure — a post in the wrong
language is still publishable, a crash is not.
"""

import re
from pathlib import Path

from multilingual.languages import prompt_name

_PROMPT = Path(__file__).parent.parent / "config" / "prompts" / "translate_metadata.txt"


def translate_metadata(
    title: str,
    description: str,
    hashtags: list[str],
    target: str,
    llm,
    terms: list[str] | None = None,
) -> dict:
    """{"title", "description", "hashtags"} rendered in `target`."""
    out = {"title": title, "description": description, "hashtags": list(hashtags)}
    try:
        prompt = (
            _PROMPT.read_text(encoding="utf-8")
            .replace("{language}", prompt_name(target))
            .replace("{terms}", "\n".join(f"  - {t}" for t in terms) if terms else "  (none)")
            .replace("{title}", title or "")
            .replace("{description}", description or "")
            .replace("{hashtags}", " ".join(hashtags))
        )
        raw = llm.generate(prompt, json_mode=False)
        parsed = _parse(raw)
        if parsed.get("title"):
            out["title"] = parsed["title"][:120]
        if parsed.get("description"):
            out["description"] = parsed["description"]
        if parsed.get("hashtags"):
            out["hashtags"] = parsed["hashtags"][:6]
    except Exception as e:
        print(f"      (post text kept in the original language: {e})")
    return out


# Models translate the LABELS too ("## Titulo:", "DESCRIPción:") however
# firmly the prompt says not to, so labels are matched by prefix across
# languages, and anything still unmatched falls back to line order.
_LABELS = {
    "title": ("tit", "tít", "titr", "titel", "заголов", "загол", "judul", "شيرة", "عنوان"),
    "description": ("desc", "descri", "besch", "opis", "описан", "deskripsi", "説明", "وصف"),
    "hashtags": ("hash", "tag", "хэш", "タグ", "وسوم"),
}


def _clean_label(text: str) -> str:
    return text.strip().lstrip("#*- ").strip().lower()


def _parse(raw: str) -> dict:
    found: dict = {}
    ordered: list[str] = []
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*[#*\-\s]*([^:：]{2,30})[:：]\s*(.+?)\s*\**\s*$", line)
        if not m:
            continue
        label = _clean_label(m.group(1))
        value = m.group(2).strip().strip('"').strip("*").strip()
        if not value:
            continue
        key = None
        if "#" in value and value.count("#") >= 2:
            key = "hashtags"
        else:
            for name, prefixes in _LABELS.items():
                if label.startswith(prefixes):
                    key = name
                    break
        if key and key not in found:
            found[key] = value
        ordered.append(value)

    # Models often emit the title as a bare markdown heading with no label
    # ("## Posible Hermano & Desafío"), which no label rule can catch. If a
    # title is still missing, take the first unlabelled, non-hashtag line.
    if "title" not in found:
        for line in (raw or "").splitlines():
            stripped = line.strip()
            if not stripped or re.match(r"^[^:：]{2,30}[:：]", stripped):
                continue
            cleaned = re.sub(r"^[#*\-\s]+", "", stripped).strip().strip('"')
            if cleaned and not cleaned.startswith("#"):
                found["title"] = cleaned
                break

    # Nothing recognised but three lines came back: trust the order the
    # prompt asked for (title, description, hashtags).
    if len(found) < 2 and len(ordered) >= 3:
        found.setdefault("title", ordered[0])
        found.setdefault("description", ordered[1])
        found.setdefault("hashtags", ordered[2])

    if isinstance(found.get("hashtags"), str):
        found["hashtags"] = [
            t if t.startswith("#") else f"#{t}"
            for t in found["hashtags"].split()
            if len(t) > 1
        ]
    return found


def write_post_file(meta: dict, path: Path) -> Path:
    """The post text, ready to copy into the upload form."""
    body = (
        f"TITLE\n{meta.get('title', '').strip()}\n\n"
        f"DESCRIPTION\n{meta.get('description', '').strip()}\n\n"
        f"HASHTAGS\n{' '.join(meta.get('hashtags', []))}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path
