"""Target languages for multilingual publishing.

The same ten the interface is translated into — the biggest YouTube
markets — plus the ones with a legal or commercial pull for translated
captions (Canadian French, US Spanish). Adding a language is one row: the
translator and subtitle writer are language-agnostic.
"""

# code -> (English name, native name, name used in the translation prompt)
LANGUAGES: dict[str, tuple[str, str, str]] = {
    "en": ("English", "English", "English"),
    "es": ("Spanish", "Español", "Spanish (neutral Latin American)"),
    "pt": ("Portuguese", "Português", "Brazilian Portuguese"),
    "fr": ("French", "Français", "French"),
    "de": ("German", "Deutsch", "German"),
    "hi": ("Hindi", "हिन्दी", "Hindi"),
    "id": ("Indonesian", "Bahasa Indonesia", "Indonesian"),
    "ja": ("Japanese", "日本語", "Japanese"),
    "ru": ("Russian", "Русский", "Russian"),
    "ar": ("Arabic", "العربية", "Modern Standard Arabic"),
}


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def prompt_name(code: str) -> str:
    """How the language is described to the translation model."""
    return LANGUAGES.get(code, ("", "", code))[2]


def english_name(code: str) -> str:
    return LANGUAGES.get(code, (code, "", ""))[0]
