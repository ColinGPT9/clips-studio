"""Stand-ins for the local voice apps the voice layer can use (editor suite
step V1): a fake Voicebox and a fake OpenAI-style speech server, each on a
free loopback port for one test. Standard library only.

Every request is recorded (method, path, headers, JSON body), so a test can
say exactly what the engine asked for, and that it asked for nothing else.
"""

from __future__ import annotations

import io
import json
import socket
import threading
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def wav_bytes(seconds: float = 0.25, rate: int = 24000, channels: int = 1, fill: bytes = b"\x00\x10") -> bytes:
    """A PCM WAV of one repeated sample; the default is loud enough to be
    sound (b"\x00\x00" is silence)."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(fill * int(rate * seconds) * channels)
    return out.getvalue()


def closed_port() -> int:
    """A loopback port nothing is listening on right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fake:
    """A local HTTP server answering from `routes`:
    {(method, path): (status, content type, bytes) or a function(body) -> that}."""

    def __init__(self, routes: dict):
        self.routes = dict(routes)
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = raw
                path = self.path.split("?")[0]
                fake.requests.append({"method": self.command, "path": path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                answer = fake.routes.get((self.command, path))
                if callable(answer):
                    answer = answer(body)
                if answer is None:
                    answer = (404, "application/json", b'{"detail":"Not Found"}')
                status, kind, data = answer[:3]
                extra = answer[3] if len(answer) > 3 else {}
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def seen(self) -> list[tuple[str, str]]:
        return [(r["method"], r["path"]) for r in self.requests]

    def bodies(self, path: str) -> list:
        return [r["body"] for r in self.requests if r["path"] == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _json(data, status: int = 200):
    return (status, "application/json", json.dumps(data).encode("utf-8"))


PROFILES = [
    {"id": "p-heart", "name": "Heart", "description": None, "language": "en", "voice_type": "preset",
     "preset_engine": "kokoro", "preset_voice_id": "af_heart", "default_engine": "kokoro",
     "created_at": "2026-11-01T00:00:00", "updated_at": "2026-11-01T00:00:00"},
    {"id": "p-dora", "name": "Dora", "description": None, "language": "es", "voice_type": "preset",
     "preset_engine": "kokoro", "preset_voice_id": "ef_dora", "default_engine": "kokoro",
     "created_at": "2026-11-01T00:00:00", "updated_at": "2026-11-01T00:00:00"},
    {"id": "p-me", "name": "My own voice", "description": None, "language": "en", "voice_type": "cloned",
     "preset_engine": None, "preset_voice_id": None, "default_engine": "chatterbox",
     "created_at": "2026-11-01T00:00:00", "updated_at": "2026-11-01T00:00:00"},
    {"id": "p-tada", "name": "Tada voice", "description": None, "language": "en", "voice_type": "cloned",
     "preset_engine": None, "preset_voice_id": None, "default_engine": "tada",
     "created_at": "2026-11-01T00:00:00", "updated_at": "2026-11-01T00:00:00"},
]
MODELS = {"models": [
    {"model_name": "kokoro", "display_name": "Kokoro", "downloaded": True},
    {"model_name": "chatterbox-tts", "display_name": "Chatterbox", "downloaded": False},
    {"model_name": "tada-1b", "display_name": "TADA", "downloaded": True},
]}


@contextmanager
def voicebox(changes: dict | None = None):
    """A fake Voicebox (v0.5.0's four routes). `changes` replaces routes."""
    routes = {
        ("GET", "/"): _json({"message": "voicebox API", "version": "0.5.0"}),
        ("GET", "/profiles"): _json(PROFILES),
        ("GET", "/models/status"): _json(MODELS),
        ("POST", "/generate/stream"): (200, "audio/wav", wav_bytes()),
    }
    routes.update(changes or {})
    fake = Fake(routes)
    try:
        yield fake
    finally:
        fake.close()


@contextmanager
def openai_server(changes: dict | None = None):
    """A fake OpenAI-style speech server with VoiceStudio's voice list."""
    routes = {
        ("POST", "/v1/audio/speech"): (200, "audio/wav", wav_bytes()),
        ("GET", "/v1/audio/voices"): _json({"voices": [
            {"voice_id": "alloy", "id": "alloy", "name": "Alloy", "type": "openai_alias"},
            {"voice_id": "v-1", "id": "v-1", "name": "Narrator", "type": "profile", "language": "en"},
            {"voice_id": "v-2", "id": "v-2", "name": "Narradora", "type": "profile", "language": "es"},
        ], "engines": []}),
    }
    routes.update(changes or {})
    fake = Fake(routes)
    try:
        yield fake
    finally:
        fake.close()


json_answer = _json
