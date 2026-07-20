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
    "zh": ("Chinese (Simplified)", "简体中文", "Simplified Chinese (Mandarin)"),
    "vi": ("Vietnamese", "Tiếng Việt", "Vietnamese"),
    "tl": ("Filipino", "Filipino", "Filipino (Tagalog)"),
    "tr": ("Turkish", "Türkçe", "Turkish"),
    "ur": ("Urdu", "اردو", "Urdu"),
    "bn": ("Bengali", "বাংলা", "Bengali (Bangla)"),
    "th": ("Thai", "ไทย", "Thai"),
    "ko": ("Korean", "한국어", "Korean"),
    "it": ("Italian", "Italiano", "Italian"),
}


# What a voice says when a creator auditions it. It has to be IN the language
# being auditioned: reading English with a Turkish voice tells you nothing
# about how a Turkish dub will sound, and defeats the point of the preview.
# Every language here needs a row — never fall back to another language's text.
SAMPLES: dict[str, str] = {
    "en": "Hi, this is how your video will sound in English.",
    "es": "Hola, así va a sonar tu vídeo en español.",
    "pt": "Olá, é assim que o seu vídeo vai soar em português.",
    "fr": "Bonjour, voici à quoi ressemblera votre vidéo en français.",
    "de": "Hallo, so wird dein Video auf Deutsch klingen.",
    "hi": "नमस्ते, आपका वीडियो हिंदी में ऐसा सुनाई देगा।",
    "id": "Halo, beginilah suara video Anda dalam bahasa Indonesia.",
    "ja": "こんにちは、あなたの動画は日本語でこんなふうに聞こえます。",
    "ru": "Привет, вот как будет звучать ваше видео на русском.",
    "ar": "مرحبا، هكذا سيبدو الفيديو الخاص بك بالعربية.",
    "zh": "你好，这就是你的视频用中文配音的效果。",
    "vi": "Xin chào, đây là giọng đọc video của bạn bằng tiếng Việt.",
    "tl": "Kumusta, ganito ang tunog ng iyong video sa Filipino.",
    "tr": "Merhaba, videonuz Türkçe olarak böyle duyulacak.",
    "ur": "ہیلو، آپ کی ویڈیو اردو میں ایسی سنائی دے گی۔",
    "bn": "হ্যালো, আপনার ভিডিও বাংলায় এমন শোনাবে।",
    "th": "สวัสดี วิดีโอของคุณจะฟังดูแบบนี้ในภาษาไทย",
    "ko": "안녕하세요, 당신의 영상은 한국어로 이렇게 들릴 거예요.",
    "it": "Ciao, ecco come suonerà il tuo video in italiano.",
}


def sample_text(code: str) -> str | None:
    """The audition line for this language, or None if there isn't one.
    None means refuse the preview — speaking English through the voice
    would misrepresent it."""
    return SAMPLES.get(code)


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def prompt_name(code: str) -> str:
    """How the language is described to the translation model."""
    return LANGUAGES.get(code, ("", "", code))[2]


def english_name(code: str) -> str:
    return LANGUAGES.get(code, (code, "", ""))[0]
