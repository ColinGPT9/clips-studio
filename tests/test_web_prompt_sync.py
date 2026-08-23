"""The web tool must score with the SAME prompt the desktop app ships.

`web/` is a static bundle with no server, so it cannot read
`config/prompts/` at runtime — the scoring prompt is embedded in
`web/lib/score.ts` as a template literal instead.

That duplication is the point of this test. The web page exists to show a
stranger what Clips Kitty's judgement is like before they download it. If the
prompt improves here and the copy over there silently stays behind, the demo
starts advertising a product that no longer exists, and nothing fails — the
page keeps working, just worse than the thing it is selling. Nobody would
notice for months.

If this test fails, copy `config/prompts/score_clips.txt` into the
`SCORE_PROMPT` literal in `web/lib/score.ts`. Do not "fix" it by editing the
prompt file to match the web copy.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT / "config" / "prompts" / "score_clips.txt"
SCORE_TS = ROOT / "web" / "lib" / "score.ts"

# The literal is written as:  const SCORE_PROMPT = `...`;
_LITERAL = re.compile(r"const SCORE_PROMPT = `(.*?)`;", re.DOTALL)


def test_embedded_prompt_matches_the_shipped_one():
    embedded = _LITERAL.search(SCORE_TS.read_text(encoding="utf-8"))
    assert embedded, (
        "No SCORE_PROMPT template literal found in web/lib/score.ts. If it was "
        "renamed, update this test — the sync still needs guarding."
    )

    # The file ends with a newline and so does the literal; compare stripped so
    # a trailing-whitespace difference is not reported as a prompt change.
    assert embedded.group(1).strip() == PROMPT_FILE.read_text(
        encoding="utf-8"
    ).strip(), (
        "web/lib/score.ts has drifted from config/prompts/score_clips.txt. "
        "Copy the .txt into the SCORE_PROMPT literal."
    )


def test_placeholders_are_all_filled_by_the_web_port():
    """Every {placeholder} in the prompt must be replaced somewhere in score.ts.

    A placeholder the Python pipeline fills but the TypeScript port forgets
    reaches the model as the literal text `{events}`, which it will cheerfully
    treat as part of the instructions.
    """
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    source = SCORE_TS.read_text(encoding="utf-8")

    for placeholder in set(re.findall(r"\{(\w+)\}", prompt)):
        assert f'"{{{placeholder}}}"' in source, (
            f"web/lib/score.ts never replaces {{{placeholder}}}, so it would be "
            "sent to the model verbatim."
        )
