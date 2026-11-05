r"""New editor code shows no English that bypasses t().

The new editor (ui/src/renderer/src/components/editor/) is written after the
app was translated, so every string it shows goes through t() with English
as the fallback (lib/i18n.ts): `{t("Export")}`, `title={t("Split")}`. This
fails on the two ways an English string slips past that:

- bare JSX text: `<button>Export</button>`, `<span>{n} clips</span>`, or a
  lone literal standing in for it, `<p>{"Export"}</p>`;
- a literal title, placeholder or aria-label: `title="Split"`,
  `aria-label={"Close"}`.

Text with no letters in it (`·`, `&nbsp;`, `0:00`) and values that are not
words (a URL, a #colour, an @handle) are fine.

The folder doesn't exist yet. The self-tests run the detector on sample TSX,
so it means something before then. tests/test_ui_translatable_attributes.py
does the same job for the existing screens' attributes.

The detector reads TSX well enough to tell JSX from TypeScript: a `<` is a
tag only where an expression can start, and something that looked like one
but never closes (`type Fn = <T>(x: T) => T`) is read again as code.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
EDITOR = UI / "components" / "editor"
ATTRIBUTES = ("title", "placeholder", "aria-label")

_LETTER = re.compile(r"[A-Za-z]")
_ENTITY = re.compile(r"&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"[\w.]+")
_TAG = re.compile(r"[A-Za-z_$][\w$.:-]*")
_ATTR = re.compile(r"[A-Za-z_$][\w$:-]*")
_LITERAL = re.compile(r"""\s*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`)\s*""",
                      re.DOTALL)
# Where an expression can start: after these a `<` opens a tag and a `/` a
# regex. After a name, a number, a string or a `)` it is an operator.
_KEYWORDS = {"return", "yield", "await", "default", "case", "else", "do", "in", "of", "throw",
             "typeof", "void", "delete", "new", "instanceof"}
_TAG_AFTER = {"", "=>", "(", "[", "{", "}", ",", ";", "=", ":", "?", "&", "|", "!"}
_REGEX_AFTER = _TAG_AFTER | {"~", "+", "-", "*", "%", "^", "<", ">"}
_VALUE = "\0value"


def _not_words(value: str) -> bool:
    """A value that is not words in a language, so there is nothing to translate."""
    value = value.strip()
    return (not _LETTER.search(_ENTITY.sub("", value))
            or value.startswith(("http://", "https://", "#"))
            or bool(re.fullmatch(r"@\w+", value)))


def _literal(expr: str) -> str | None:
    """The text of a lone string or template literal, or None for anything else."""
    match = _LITERAL.fullmatch(expr)
    if not match:
        return None
    text = next(group for group in match.groups() if group is not None)
    return re.sub(r"\$\{[^}]*\}", " ", text)      # a template's ${...} is not text


class _NotJsx(Exception):
    """What looked like a tag is not one: a generic, a comparison."""


class _Scanner:
    def __init__(self, source: str):
        self.s = source
        self.i = 0
        self.found: list[str] = []

    def problems(self) -> list[str]:
        self._code(inside_braces=False)
        return self.found

    def _report(self, at: int, what: str) -> None:
        self.found.append(f"line {self.s.count(chr(10), 0, at) + 1}: {what}")

    def _peek(self, text: str) -> bool:
        return self.s.startswith(text, self.i)

    def _space(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    # ---- TypeScript ------------------------------------------------------------------------

    def _code(self, inside_braces: bool) -> None:
        """Code up to the end, or past the `}` that closes the braces it is in."""
        s, depth, prev = self.s, 0, ""
        while self.i < len(s):
            c = s[self.i]
            if c.isspace():
                self.i += 1
            elif self._peek("//"):
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif self._peek("/*"):
                end = s.find("*/", self.i + 2)
                self.i = len(s) if end < 0 else end + 2
            elif c in "'\"":
                self._string(c)
                prev = _VALUE
            elif c == "`":
                self._template()
                prev = _VALUE
            elif c == "{":
                depth += 1
                self.i += 1
                prev = "{"
            elif c == "}":
                self.i += 1
                if depth == 0 and inside_braces:
                    return
                depth = max(0, depth - 1)
                prev = "}"
            elif c == "/" and (prev in _REGEX_AFTER or prev in _KEYWORDS):
                self._regex()
                prev = _VALUE
            elif c == "<" and (prev in _TAG_AFTER or prev in _KEYWORDS) and self._jsx():
                prev = _VALUE
            elif c.isalpha() or c in "_$":
                word = _WORD.match(s, self.i)
                prev = word.group()
                self.i = word.end()
            elif c.isdigit():
                self.i = _NUMBER.match(s, self.i).end()
                prev = _VALUE
            elif self._peek("=>"):
                self.i += 2
                prev = "=>"
            else:
                self.i += 1
                prev = c

    def _string(self, quote: str) -> None:
        s, i = self.s, self.i + 1
        while i < len(s) and s[i] not in (quote, "\n"):
            i += 2 if s[i] == "\\" else 1
        self.i = i + 1

    def _template(self) -> None:
        self.i += 1
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
            elif c == "`":
                self.i += 1
                return
            elif self._peek("${"):
                self.i += 2
                self._code(inside_braces=True)
            else:
                self.i += 1

    def _regex(self) -> None:
        s, i, in_class = self.s, self.i + 1, False
        while i < len(s) and s[i] != "\n":
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            elif c == "/" and not in_class:
                i += 1
                while i < len(s) and s[i].isalpha():
                    i += 1
                self.i = i
                return
            i += 1
        self.i += 1          # a lone slash, not a regex after all

    # ---- JSX ----------------------------------------------------------------------------------

    def _jsx(self) -> bool:
        """Read the element at `<`, or put everything back and say it wasn't one."""
        start, seen = self.i, len(self.found)
        try:
            self._element()
            return True
        except _NotJsx:
            self.i = start
            del self.found[seen:]
            return False

    def _element(self) -> None:
        s = self.s
        self.i += 1
        self._space()
        if self._peek(">"):                      # a fragment
            self.i += 1
            self._children("")
            return
        tag = _TAG.match(s, self.i)
        if not tag:
            raise _NotJsx
        self.i = tag.end()
        self._space()
        if self._peek("<"):                      # <Select<Option> ...>
            self._type_arguments()
        if self._peek(",") or self._peek("extends"):
            raise _NotJsx                         # <T,>(x: T) => x, <T extends U>
        while True:
            self._space()
            if self.i >= len(s):
                raise _NotJsx
            if self._peek("/>"):
                self.i += 2
                return
            if self._peek(">"):
                self.i += 1
                self._children(tag.group())
                return
            if self._peek("{"):                  # {...props}, {/* a comment */}
                self.i += 1
                self._code(inside_braces=True)
                continue
            attr = _ATTR.match(s, self.i)
            if not attr:
                raise _NotJsx
            self.i = attr.end()
            self._space()
            if not self._peek("="):
                continue
            self.i += 1
            self._space()
            at = self.i
            if self._peek('"') or self._peek("'"):
                end = s.find(s[self.i], self.i + 1)
                if end < 0:
                    raise _NotJsx
                self._attribute(attr.group(), s[self.i + 1:end], at)
                self.i = end + 1
            elif self._peek("{"):
                self.i += 1
                self._code(inside_braces=True)
                value = _literal(s[at + 1:self.i - 1])
                if value is not None:
                    self._attribute(attr.group(), value, at)
            elif self._peek("<"):
                self._element()
            else:
                raise _NotJsx

    def _type_arguments(self) -> None:
        depth = 0
        while self.i < len(self.s):
            if self._peek("=>"):
                self.i += 2
                continue
            c = self.s[self.i]
            self.i += 1
            if c == "<":
                depth += 1
            elif c == ">":
                depth -= 1
                if depth == 0:
                    return
        raise _NotJsx

    def _children(self, tag: str) -> None:
        s, start = self.s, self.i
        shown = tag.lower() not in ("style", "script")      # CSS and code are not text on screen
        while self.i < len(s):
            c = s[self.i]
            if c == "<":
                if shown:
                    self._text(start, self.i)
                if self._peek("</"):
                    self.i += 2
                    self._space()
                    closing = _TAG.match(s, self.i)
                    if closing:
                        self.i = closing.end()
                    self._space()
                    if (closing.group() if closing else "") != tag or not self._peek(">"):
                        raise _NotJsx
                    self.i += 1
                    return
                self._element()
                start = self.i
            elif c == "{":
                if shown:
                    self._text(start, self.i)
                at = self.i
                self.i += 1
                self._code(inside_braces=True)
                text = _literal(s[at + 1:self.i - 1])
                if shown and text is not None and not _not_words(text):
                    self._report(at, f"text in a literal: {s[at:self.i]}")
                start = self.i
            else:
                self.i += 1
        raise _NotJsx

    def _text(self, start: int, end: int) -> None:
        raw = self.s[start:end]
        if _LETTER.search(_ENTITY.sub("", raw)):
            lead = len(raw) - len(raw.lstrip())
            self._report(start + lead, f"bare text {' '.join(raw.split())!r}")

    def _attribute(self, name: str, value: str, at: int) -> None:
        if name.lower() in ATTRIBUTES and not _not_words(value):
            self._report(at - len(name) - 1, f"literal {name}: {value!r}")


def untranslated(source: str) -> list[str]:
    """Every bare English text and literal title/placeholder/aria-label in TSX."""
    return _Scanner(source).problems()


def test_the_new_editor_shows_no_untranslated_english():
    files = sorted(EDITOR.rglob("*.tsx")) if EDITOR.is_dir() else []
    found = [f"{path.relative_to(UI).as_posix()} {problem}"
             for path in files for problem in untranslated(path.read_text(encoding="utf-8"))]
    assert not found, "English in the new editor that doesn't go through t():\n" + "\n".join(found)


# ---- the detector itself ----------------------------------------------------------------------

CAUGHT = {
    "bare text": 'export const A = () => <button className="primary">Export</button>',
    "text beside a value": "export const B = ({ n }: { n: number }) => <span>{n} clips</span>",
    "a string literal as text": 'export const C = () => <p>{"Clip saved"}</p>',
    "a template literal as text": "export const D = ({ n }: { n: number }) => <p>{`Clip ${n} saved`}</p>",
    "a literal title": 'export const E = () => <Tool title="Split clip" />',
    "a literal placeholder": "export const F = () => <input placeholder='Search media' />",
    "a literal aria-label": 'export const G = () => <button aria-label={"Close panel"} />',
    "a template literal title": "export const H = () => <div title={`Trim start`} />",
    "text in a nested element": ("export const I = ({ open }: { open: boolean }) => (\n"
                                 "  <div>{open && <span>Saved</span>}</div>\n)"),
    "text in a fragment": "export function J() {\n  return (\n    <>\n      <h2>Timeline</h2>\n    </>\n  )\n}",
    "an element passed as a prop": "export const K = () => <List empty={<p>Nothing here</p>} />",
    "an element a callback returns": 'export const L = () => <List row={() => <Row title="Row" />} />',
    "text with an apostrophe": ("export const M = () => <p>Don't stop</p>\n"
                                'export const N = () => <b title={t("Fine")}>{t("Fine")}</b>'),
}

ACCEPTED = r"""
import { useState } from 'react'
import { t } from '../../lib/i18n'

type Fn = <T>(x: T) => T
const identity = <T,>(x: T): T => x
function first<T extends object>(items: Array<T>): T | undefined { return items[0] }
// <b>Bold</b> in a comment is not JSX
/* nor <b>Bold</b> in this one */
const html = "<b>Bold</b>"
const tag = /<b>(.*?)<\/b>/g
const half = 6 / 3 / 2

export function Panel({ count, open }: { count: number; open: boolean }) {
  const [name, setName] = useState<string>('')
  const few = count < 3 && count > 0
  return (
    <div className="flex gap-2" data-testid="panel">
      <button title={t("Split clip")} aria-label={t('Close panel')} onClick={() => setName(name)}>
        {t("Export")}
      </button>
      <span>{count} {t("clips")}</span>
      <input placeholder={t(`Search media`)} value={name} onChange={(e) => setName(e.target.value)} />
      <input placeholder="#FFFFFF" title="https://example.com" aria-label={"@YourChannel"} />
      <span>·</span> <span>&nbsp;&times;</span> <span>{"0:00"}</span> <span> 2 / 3 </span>
      {few ? <em>{t("A few")}</em> : null}
      {open && <Row render={() => <b>{t("Open")}</b>} />}
      {/* a comment inside JSX */}
      <style>{`@keyframes pulse { from { opacity: 1 } to { opacity: 0.4 } }`}</style>
    </div>
  )
}
"""


def test_the_detector_catches_each_kind_of_untranslated_english():
    for kind, source in CAUGHT.items():
        found = untranslated(source)
        assert len(found) == 1, f"{kind}: {found}"


def test_the_detector_accepts_t_and_typescript_that_only_looks_like_jsx():
    assert untranslated(ACCEPTED) == []


def test_the_detector_says_where():
    assert untranslated("const a = 1\nconst b = (\n  <p>Hello</p>\n)") == ["line 3: bare text 'Hello'"]
    assert untranslated('const c = <input\n  placeholder="Search" />') == ["line 2: literal placeholder: 'Search'"]
