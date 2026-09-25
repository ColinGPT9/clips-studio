"""Take anything key-shaped out of text before it is shown, stored or sent.

One list for the whole app: bug reports (server/feedback.redact), job errors,
and every message that comes back from a cloud AI provider. Keys belong in
request headers and the credential store only; this is the net under that
rule, for the day a provider echoes one back or a library prints one.
"""

import re

SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),              # Google / Gemini API keys
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),              # OpenAI, OpenRouter (sk-or-v1-), Anthropic (sk-ant-)
    re.compile(r"\bxai-[A-Za-z0-9_\-]{20,}"),             # xAI
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),   # an Authorization header, printed
    re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),            # GitHub tokens
    re.compile(r"oauth:[0-9a-zA-Z]{10,}"),                # Twitch chat oauth
    re.compile(r"eyJ[0-9A-Za-z_\-]{20,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),  # JWTs
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,}"),
    re.compile(r"[A-Za-z0-9+/]{48,}={0,2}"),              # long base64 blobs
    re.compile(r"[0-9a-fA-F]{40,}"),                      # long hex blobs
    # A long run of key characters with a digit in it: the shape of a key from
    # a provider with no prefix of its own. Words and paths are not this long.
    re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9_\-])"),
]


def scrub_secrets(text: str) -> str:
    """Replace every key-shaped run with "[redacted]". Only secrets: paths and
    email addresses are left alone, unlike a bug report's full redaction."""
    if not text:
        return ""
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out
