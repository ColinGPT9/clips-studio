"""The letters each caption language needs, for choosing a font that has them.

A bundled caption font (assets/fonts/) is kept for a clip only when its
character map has every letter the clip's language is written with, in both
cases (captions are uppercase by default). Otherwise libass draws each
missing letter in another font in the middle of a word, so the captions
take a Windows font that has them (video/captions.py caption_font_for).

SCRIPT_LETTERS are the writing systems assets/fonts/manifest.json lists per
family ("scripts"), each a full set: a family lists a script only when its
file has every letter of it (tests/test_caption_fonts.py reads each file's
character map). LANGUAGE_LETTERS are what one language adds to its script's
base letters. Standard library only.
"""

from __future__ import annotations


def _span(first: int, last: int, skip: tuple[int, ...] = ()) -> str:
    return "".join(chr(c) for c in range(first, last + 1) if c not in skip)


def _cased(letters: str) -> str:
    """The letters with their other case, as one string without repeats."""
    out = []
    for ch in letters:
        for form in (ch, ch.upper(), ch.lower()):
            if len(form) == 1 and form not in out:
                out.append(form)
    return "".join(out)


_ASCII = _span(0x41, 0x5A) + _span(0x61, 0x7A)
# Western European: the Latin-1 letters (French, German, Spanish,
# Portuguese, Italian, Dutch, the Nordic languages, Icelandic...) and
# French's Œ and Ÿ.
_WESTERN = _span(0xC0, 0xFF, skip=(0xD7, 0xF7)) + "ŒœŸ"
_CYRILLIC_CORE = _span(0x410, 0x44F) + "Ёё"

SCRIPT_LETTERS = {
    "latin": _ASCII + _WESTERN,
    # Latin Extended-A in full (but for ĸ, ŉ and ſ, which no language
    # writes today), with Romanian's comma-below Ș and Ț.
    "latin-ext": _span(0x100, 0x17F, skip=(0x138, 0x149, 0x17F)) + _span(0x218, 0x21B),
    # Russian, Ukrainian, Belarusian, Bulgarian, Serbian, Macedonian (but
    # for Ѐ and Ѝ, stress marks that are not letters of any alphabet).
    "cyrillic": _span(0x400, 0x45F, skip=(0x400, 0x40D, 0x450, 0x45D)) + "Ґґ",
    # Kazakh, Kyrgyz, Tajik, Mongolian, Bashkir and Tatar's own letters.
    "cyrillic-ext": _cased("әғқңөұүһӣӯҳҷҙҡҫҗ"),
    "greek": "Ά" + _span(0x388, 0x38A) + "Ό" + _span(0x38E, 0x3A1) + _span(0x3A3, 0x3CE),
    "vietnamese": _cased("ăâđêôơư") + _span(0x1EA0, 0x1EF9) + _cased("àáãèéìíòóõùúýĩũ"),
    "thai": _span(0xE01, 0xE3A) + _span(0xE3F, 0xE5B),
}

# What each language is written with beyond its script's base letters (the
# Latin alphabet's A-Z and Western European letters; Russian's Cyrillic).
LANGUAGE_LETTERS = {
    "pl": _cased("ąćęłńóśźż"),
    "cs": _cased("áčďéěíňóřšťúůýž"),
    "sk": _cased("áäčďéíĺľňóôŕšťúýž"),
    "hu": _cased("áéíóöőúüű"),
    "ro": _cased("ăâîșțşţ"),
    "tr": _cased("çğöşü") + "İı",
    "hr": _cased("čćđšž"),
    "bs": _cased("čćđšž"),
    "sl": _cased("čšž"),
    "lt": _cased("ąčęėįšųūž"),
    "lv": _cased("āčēģīķļņšūž"),
    "et": _cased("äõöüšž"),
    "az": _cased("çəğöşü") + "İı",
    "sq": _cased("çë"),
    "mt": _cased("ċġħżàèìòù"),
    "uz": _cased("ʻ"),
    "tk": _cased("äçňöşüýž"),
    "eo": _cased("ĉĝĥĵŝŭ"),
    "haw": _cased("āēīōūʻ"),
    "ln": _cased("ɛɔ"),
    "yo": _cased("ẹọṣ") + "̀́",
    "ha": _cased("ɓɗƙƴ") + "ʼ",
    "cy": _cased("âêîôûŵŷ"),
    "mi": _cased("āēīōū"),
    "uk": _cased("єіїґ"),
    "be": _cased("іў"),
    "sr": _cased("ђјљњћџ"),
    "mk": _cased("ѓѕјљњќџ"),
    "kk": _cased("әғқңөұүһі"),
    "ky": _cased("ңөү"),
    "tg": _cased("ғӣқӯҳҷ"),
    "mn": _cased("өү"),
    "ba": _cased("ғҙҡңөҫүһә"),
    "tt": _cased("әҗңөүһ"),
}

# The writing system each language's captions need. A language not listed
# is written in the Latin alphabet with at most Western European letters.
_SCRIPT_OF = {
    **dict.fromkeys(("ru", "uk", "bg", "sr", "mk", "be"), "cyrillic"),
    **dict.fromkeys(("kk", "ky", "tg", "mn", "ba", "tt"), "cyrillic-ext"),
    "el": "greek",
    "vi": "vietnamese",
    "th": "thai",
    **dict.fromkeys((
        "pl", "cs", "sk", "hu", "ro", "tr", "hr", "sl", "lt", "lv", "et", "az", "sq", "bs", "mt", "uz", "tk",
        "eo", "haw", "ln", "yo", "ha", "cy", "mi"), "latin-ext"),
    **dict.fromkeys(("hi", "mr", "ne", "sa"), "devanagari"),
    **dict.fromkeys((
        "bn", "ta", "te", "gu", "kn", "ml", "pa", "si", "as", "or", "ja", "ko", "zh", "yue", "ar", "fa", "ur",
        "ps", "sd", "ug", "he", "yi", "hy", "ka", "am", "km", "lo", "my", "bo", "dv"), "other"),
}


def base_language(language: str | None) -> str:
    return (language or "en").lower().replace("_", "-").split("-")[0]


def script_of(language: str | None) -> str:
    """The writing system a language's captions need (see _SCRIPT_OF)."""
    return _SCRIPT_OF.get(base_language(language), "latin")


def letters_for(language: str | None) -> str | None:
    """Every letter a caption font needs for `language`, or None for a
    writing system no bundled font has (Devanagari, Japanese, Arabic...)."""
    lang = base_language(language)
    script = script_of(lang)
    if script in ("devanagari", "other"):
        return None
    letters = SCRIPT_LETTERS["latin"]
    if script in ("cyrillic", "cyrillic-ext"):
        letters += _CYRILLIC_CORE
    elif script in ("greek", "vietnamese", "thai"):
        letters += SCRIPT_LETTERS[script]
    return letters + LANGUAGE_LETTERS.get(lang, "")


def scripts_in(codepoints) -> list[str]:
    """The writing systems (SCRIPT_LETTERS, in that order) a font with these
    characters has in full."""
    have = set(codepoints)
    return [name for name, letters in SCRIPT_LETTERS.items() if all(ord(c) in have for c in letters)]
