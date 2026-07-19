"""Caption translation through the LLM the app already runs locally.

Two things make this survive a 7B model:

* Numbered items, not JSON — local models drop or mangle JSON on long
  lists but reliably echo "12: text". Anything the model skips keeps its
  ORIGINAL text: a track with a few untranslated lines is usable, a track
  whose timings shifted because lines merged is not.
* SENTENCES, not caption lines — caption lines are 2-4 word fragments and
  Whisper splits them mid-word. Translating "We could low" / "-key be
  siblings" separately produced "we could lower" in Spanish. Fragments are
  rejoined into sentences, translated, then spread back over the original
  line slots so the timings never move.

Nothing here is imported by the clipping pipeline.
"""

import re
from pathlib import Path

from multilingual.languages import prompt_name

_PROMPT = Path(__file__).parent.parent / "config" / "prompts" / "translate_captions.txt"
BATCH = 10           # sentences per call: small enough that a 7B stays aligned
MAX_CHARS = 2400     # ...and short enough to leave room for its answer
GROUP_MAX_LINES = 8  # never let one "sentence" swallow a whole clip


def translate_lines(
    lines: list[dict],
    target: str,
    llm,
    terms: list[str] | None = None,
    on_progress=None,
) -> list[dict]:
    """Caption lines with `text` translated into `target`. Times untouched.

    Never raises: on any failure the affected batch keeps its original
    text, so the caller always gets a complete, correctly-timed track."""
    if not lines:
        return []
    template = _PROMPT.read_text(encoding="utf-8")
    term_block = "\n".join(f"  - {t}" for t in terms) if terms else "  (none)"
    out = [dict(line) for line in lines]
    groups = _group_sentences(out)

    for start in range(0, len(groups), BATCH):
        chunk = groups[start : start + BATCH]
        numbered: list[str] = []
        budget = 0
        for i, (text, _idx) in enumerate(chunk):
            numbered.append(f"{i + 1}: {text}")
            budget += len(text)
            if budget > MAX_CHARS:
                chunk = chunk[: i + 1]
                break
        prompt = (
            template.replace("{language}", prompt_name(target))
            .replace("{terms}", term_block)
            .replace("{lines}", "\n".join(numbered))
        )
        try:
            raw = llm.generate(prompt, json_mode=False)
            for num, text in _parse(raw).items():
                if 1 <= num <= len(chunk) and text:
                    _spread(text, chunk[num - 1][1], out)
        except Exception as e:  # keep the originals for this batch
            print(f"      (translation batch failed, keeping source text: {e})")
        if on_progress:
            on_progress(min(start + BATCH, len(groups)), len(groups))
    return out


def _group_sentences(lines: list[dict]) -> list[tuple[str, list[int]]]:
    """Join caption fragments into sentences, remembering which line slots
    each sentence came from."""
    groups: list[tuple[str, list[int]]] = []
    buf: list[str] = []
    idx: list[int] = []
    for i, line in enumerate(lines):
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        # Rejoin words Whisper split across two caption lines, so the model
        # sees "low-key" instead of "low" followed by "-key".
        if buf and (text.startswith(("-", "'", "’", ",", ".")) or buf[-1].endswith("-")):
            buf[-1] = (buf[-1].rstrip("-") + "-" if buf[-1].endswith("-") else buf[-1]) + text
        else:
            buf.append(text)
        idx.append(i)
        if text.endswith((".", "!", "?", "…", ":")) or len(idx) >= GROUP_MAX_LINES:
            groups.append((" ".join(buf), idx))
            buf, idx = [], []
    if idx:
        groups.append((" ".join(buf), idx))
    return groups


def _spread(translated: str, indices: list[int], out: list[dict]) -> None:
    """Distribute a translated sentence back over its line slots, each slot
    getting a share of the words proportional to its original length — the
    timings are fixed, so the text has to fit them."""
    words = translated.split()
    if not words or not indices:
        return
    if len(indices) == 1:
        out[indices[0]]["text"] = translated
        return
    weights = [max(1, len(str(out[i].get("text", "")))) for i in indices]
    total = sum(weights)
    cursor = 0
    for n, i in enumerate(indices):
        remaining_slots = len(indices) - n - 1
        if remaining_slots == 0:
            take = len(words) - cursor
        else:
            take = int(round(len(words) * weights[n] / total))
            take = max(1, min(take, len(words) - cursor - remaining_slots))
        out[i]["text"] = " ".join(words[cursor : cursor + take])
        cursor += take


def _parse(raw: str) -> dict[int, str]:
    """'12: hola' -> {12: 'hola'}, ignoring any preamble the model adds."""
    found: dict[int, str] = {}
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*\**\s*(\d{1,3})\s*[:.)\]]\s*(.+?)\s*\**\s*$", line)
        if not m:
            continue
        text = m.group(2).strip().strip('"').strip()
        # A model restating the rules isn't giving us a caption.
        if text and not text.lower().startswith(("translat", "here are", "sure,")):
            found[int(m.group(1))] = text
    return found
