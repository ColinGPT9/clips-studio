"""Feedback Hub backend: diagnostics collection, redaction, and submission.

Reports are for NON-TECHNICAL users: they answer a few plain questions in
the UI, and this module contributes everything a developer needs to
reproduce the problem — versions, hardware, the exact AI model (name,
parameter size, quantization), settings, and the recent log tail. The
finished Markdown goes to the feedback relay (a Cloudflare Worker holding
the GitHub token — see feedback-relay/README.md), which files it as a
GitHub Issue. No GitHub account, no telemetry: nothing is ever sent unless
the user presses Submit, and the exact payload is previewable in the UI.

Diagnostics are built from an ALLOWLIST of known-safe fields — we never
dump raw config or environment. Free text and the log excerpt additionally
pass through redact(), which scrubs token-shaped strings and usernames in
Windows paths.
"""

import collections
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

# ---- log ring buffer ---------------------------------------------------------

_LOG_LINES = 400
_ring: collections.deque[str] = collections.deque(maxlen=_LOG_LINES)


class _Tee:
    """Wraps a stream so pipeline prints also land in the ring buffer."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            for line in str(text).splitlines():
                if line.strip():
                    _ring.append(line[:500])
        except Exception:
            pass
        return self._stream.write(text)

    def __getattr__(self, name):  # flush, encoding, isatty, ...
        return getattr(self._stream, name)


def install_log_capture() -> None:
    """Idempotent: tee stdout/stderr into the in-memory log ring."""
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)


def recent_log(lines: int = 120) -> str:
    return "\n".join(list(_ring)[-lines:])


# ---- redaction ---------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),          # Google API keys
    re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),        # GitHub tokens
    re.compile(r"oauth:[0-9a-zA-Z]{10,}"),            # Twitch chat oauth
    re.compile(r"eyJ[0-9A-Za-z_\-]{20,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),  # JWTs
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,}"),
    re.compile(r"[A-Za-z0-9+/]{48,}={0,2}"),          # long base64 blobs
    re.compile(r"[0-9a-fA-F]{40,}"),                  # long hex blobs
]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_USERPATH = re.compile(r"(?i)([A-Z]:\\Users\\)([^\\\s/]+)")


def redact(text: str) -> str:
    """Scrub anything secret-shaped or personally identifying from text
    that goes into a public GitHub issue."""
    if not text:
        return ""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[redacted]", out)
    out = _EMAIL.sub("[email]", out)
    out = _USERPATH.sub(r"\1<user>", out)
    return out


# ---- diagnostics (allowlist only) ---------------------------------------------


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _gpu() -> dict:
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader"])
    if smi:
        parts = [p.strip() for p in smi.splitlines()[0].split(",")]
        if len(parts) >= 3:
            return {"name": parts[0], "vram": parts[1], "driver": parts[2]}
    wmic = _run(["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name"])
    return {"name": wmic.splitlines()[0] if wmic else "unknown", "vram": "?", "driver": "?"}


def _ram_gb() -> float:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return round(st.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _model_info(config: dict) -> dict:
    """The exact AI model a bug reporter is running — name, parameter size,
    quantization, Ollama version — so 'works on my PC' is answerable."""
    info: dict = {"model": config.get("model", "?"), "backend": "ollama"}
    host = config.get("llm", {}).get("ollama_host", "http://localhost:11434")
    try:
        info["ollama_version"] = requests.get(f"{host}/api/version", timeout=3).json().get("version", "?")
        for m in requests.get(f"{host}/api/tags", timeout=3).json().get("models", []):
            if m.get("name") == info["model"]:
                det = m.get("details", {})
                info["parameter_size"] = det.get("parameter_size", "?")
                info["quantization"] = det.get("quantization_level", "?")
                info["family"] = det.get("family", "?")
                info["model_disk_size"] = f"{m.get('size', 0) / 1e9:.1f} GB"
    except Exception:
        info["ollama_version"] = "unreachable"
    return info


def _versions() -> dict:
    v: dict = {"python": platform.python_version()}
    ff = _run(["ffmpeg", "-version"])
    v["ffmpeg"] = ff.splitlines()[0].replace("ffmpeg version ", "") if ff else "?"
    for mod, key in (("cv2", "opencv"), ("faster_whisper", "faster_whisper"),
                     ("yt_dlp.version", "yt_dlp"), ("ultralytics", "ultralytics")):
        try:
            m = __import__(mod, fromlist=["__version__"])
            v[key] = getattr(m, "__version__", getattr(m, "version", "?"))
        except Exception:
            v[key] = "not installed"
    return v


def _app_version() -> dict:
    root = Path(__file__).parent.parent
    out: dict = {}
    try:
        pkg = json.loads((root / "ui" / "package.json").read_text(encoding="utf-8"))
        out["app"] = pkg.get("version", "?")
    except Exception:
        out["app"] = "?"
    commit = _run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])
    if commit:
        out["commit"] = commit
    return out


# Settings keys that are safe and useful in a public report.
_SETTINGS_ALLOWLIST = ("clips", "scoring", "video", "tracking", "analysis")


def collect_diagnostics(config: dict, db, video_id: str | None = None) -> dict:
    """Everything a contributor needs to reproduce, from safe fields only."""
    d: dict = {
        "version": _app_version(),
        "os": platform.platform(),
        "cpu": {"name": platform.processor() or "?", "cores": __import__("os").cpu_count()},
        "gpu": _gpu(),
        "ram_gb": _ram_gb(),
        "ai": _model_info(config),
        "versions": _versions(),
        "whisper": {"model": config.get("whisper", {}).get("model", "?"),
                    "device": config.get("whisper", {}).get("device", "?")},
        "settings": {k: config.get(k) for k in _SETTINGS_ALLOWLIST if k in config},
    }
    # Context for "it broke on this video" — platform/creator are public info
    # and essential for reproduction; nothing private is included.
    try:
        row = None
        if video_id:
            row = db.conn.execute(
                "SELECT video_id, channel_name, status FROM videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        if row is None:
            row = db.conn.execute(
                "SELECT video_id, channel_name, status FROM videos ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if row:
            vid = row["video_id"]
            plat = ("twitch" if vid.startswith("tw_") else "kick" if vid.startswith("kick_")
                    else "local file" if vid.startswith("local_") else "youtube")
            d["video"] = {"platform": plat, "channel": row["channel_name"], "status": row["status"]}
            job = db.conn.execute(
                "SELECT status, error FROM jobs WHERE payload LIKE ? ORDER BY job_id DESC LIMIT 1",
                (f"%{vid}%",),
            ).fetchone()
            if job and job["error"]:
                d["video"]["last_error"] = redact(str(job["error"])[:800])
    except Exception:
        pass
    d["log_excerpt"] = redact(recent_log())
    return d


# ---- report building -----------------------------------------------------------


def build_markdown(kind: str, answers: dict, diagnostics: dict | None) -> str:
    """The GitHub issue body. answers are the wizard's plain-question fields."""
    a = {k: redact(str(v).strip()) for k, v in answers.items() if str(v).strip()}
    lines: list[str] = []

    def sec(title: str, key: str) -> None:
        if a.get(key):
            lines.append(f"### {title}\n{a[key]}\n")

    if kind == "bug":
        sec("What were you trying to do?", "trying")
        sec("What happened?", "happened")
        sec("What did you expect to happen?", "expected")
        if a.get("repro"):
            lines.append(f"**Reproducible:** {a['repro']}\n")
        if a.get("severity"):
            lines.append(f"**Severity:** {a['severity']}\n")
        sec("Additional notes", "notes")
    elif kind == "feature":
        sec("What feature would you like?", "what")
        sec("Why would it be useful?", "why")
        sec("How would it improve your workflow?", "workflow")
        if a.get("importance"):
            lines.append(f"**Importance:** {a['importance']}\n")
    else:  # improvement
        if a.get("inspiration"):
            lines.append(f"**Inspired by:** {a['inspiration']}\n")
        sec("What would you like improved?", "what")
        sec("Why would it improve Clips Studio?", "why")
        sec("Links / references", "links")

    if diagnostics:
        pretty = json.dumps(diagnostics, indent=2, ensure_ascii=False, default=str)
        lines.append(
            "<details><summary>Diagnostics (auto-collected, secrets redacted)</summary>\n\n"
            f"```json\n{pretty}\n```\n</details>\n"
        )
    lines.append("_Sent from the in-app Feedback Hub._")
    return "\n".join(lines)


def encode_images(images: list[dict]) -> list[dict]:
    """File paths (from the UI's picker) -> capped base64 payloads for the
    relay. Only png/jpg, max 3 files, 2MB each — screen recordings are too
    big for an issue and are politely refused in the UI."""
    import base64

    out: list[dict] = []
    for img in images[:3]:
        p = Path(str(img.get("path", "")))
        ext = p.suffix.lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        if ext not in ("png", "jpg"):
            continue
        try:
            data = p.read_bytes()
        except Exception:
            continue
        if not data or len(data) > 2 * 1024 * 1024:
            continue
        out.append({"b64": base64.b64encode(data).decode(), "ext": ext})
    return out


# ---- relay client ---------------------------------------------------------------


def _solve_pow(challenge: dict) -> dict:
    """Find a nonce so sha256(salt.nonce) starts with `difficulty` zero bits.
    ~1s of CPU — the anti-spam cost of sending one report."""
    salt = challenge["salt"]
    difficulty = int(challenge["difficulty"])
    prefix_bytes, rem_bits = divmod(difficulty, 8)
    n = 0
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        nonce = str(n)
        digest = hashlib.sha256(f"{salt}.{nonce}".encode()).digest()
        if digest[:prefix_bytes] == b"\x00" * prefix_bytes and (
            rem_bits == 0 or digest[prefix_bytes] >> (8 - rem_bits) == 0
        ):
            return {"salt": salt, "expires": challenge["expires"],
                    "sig": challenge["sig"], "nonce": nonce}
        n += 1
    raise TimeoutError("could not solve the anti-spam challenge")


def submit_to_relay(relay_url: str, kind: str, title: str, markdown: str,
                    areas: list[str], severity: str, images: list[dict]) -> dict:
    """Returns {"ok": True, "url": ...} or raises with a friendly message."""
    base = relay_url.rstrip("/")
    challenge = requests.get(f"{base}/challenge", timeout=15).json()
    pow_solution = _solve_pow(challenge)
    resp = requests.post(
        f"{base}/submit",
        json={
            "type": kind,
            "title": title,
            "markdown": markdown,
            "areas": areas,
            "severity": severity,
            "images": images[:3],
            "pow": pow_solution,
        },
        timeout=60,
    )
    data = resp.json()
    if not resp.ok or not data.get("ok"):
        raise RuntimeError(data.get("error", f"relay returned {resp.status_code}"))
    return data
