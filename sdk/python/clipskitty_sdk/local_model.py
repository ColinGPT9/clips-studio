# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Asking the creator's local model, the one they chose in Clips Kitty, on
this PC.

    from clipskitty_sdk import local_model, media

    def main(job):
        for m in job.moments:
            if local_model.can_see(job):
                said = local_model.ask(job, "What is on screen? Answer in one sentence.",
                                       images=[media.jpeg(job, (m.start + m.end) / 2)])
                job.understand(m, said)

The plugin needs `ollama` in its permissions, and the creator needs a local
model in Clips Kitty: when Clips Kitty's AI runs at a cloud provider there is
no model to ask, and this refuses. It only talks to a model on this PC
(127.0.0.1, localhost or ::1): it refuses any other address, never falls
back to another one, and never calls a cloud service. Its requests ignore
proxy settings on purpose, so the prompt and the frames never leave the PC.
The model is always the one the job names; a plugin can't choose another
here.

It uses Ollama's own HTTP API (/api/generate and /api/show). Standard
library only.
"""

from __future__ import annotations

import base64
import http.client
import json as jsonlib
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import _loopback

NO_PERMISSION = "This pipeline doesn't ask for the local model: add ollama to permissions in clipskitty.yaml"
NO_MODEL = ("No local model is set in Clips Kitty (its AI may run at a cloud provider), and this helper only "
            "uses a model on this PC")
OFF_THIS_PC = ("Clips Kitty's model address {host} isn't on this PC, and this helper only talks to a model on "
               "this PC")
UNREADABLE = "The local model gave an answer this helper can't read"
BAD_PORT = "Clips Kitty's model address {host} has a port this helper can't use"

# What Ollama said each model can do ("vision", "thinking", ...), by address
# and model, once it has answered.
_CAPABILITIES: dict[tuple[str, str], tuple[str, ...]] = {}


class LocalModelError(RuntimeError):
    """The local model can't be asked (the permission, the model or its
    address), or it didn't answer. The message says which, in plain words."""


def _ollama(job) -> dict:
    ollama = job.tools.ollama
    if not isinstance(ollama, dict):  # Clips Kitty hands it over only with the permission
        raise LocalModelError(NO_PERMISSION)
    return ollama


def model(job) -> str:
    """The name of the creator's local model, as Clips Kitty hands it over."""
    name = str(_ollama(job).get("model") or "").strip()
    if not name:
        raise LocalModelError(NO_MODEL)
    return name


def _address(job) -> str:
    """The model's address, refused unless it is on this PC."""
    host = str(_ollama(job).get("host") or "").strip()
    url = host if "://" in host else f"http://{host}"
    if not host or not _loopback.is_this_pc(url):
        raise LocalModelError(OFF_THIS_PC.format(host=host or "(none)"))
    if not _loopback.port_ok(url):
        raise LocalModelError(BAD_PORT.format(host=host))
    return url.rstrip("/")


def _post(address: str, path: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(address + path, data=jsonlib.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with _loopback.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        try:
            said = jsonlib.loads(e.read() or b"null")
            said = said.get("error") if isinstance(said, dict) else said
        except (ValueError, http.client.HTTPException, OSError):
            said = None
        raise LocalModelError(f"The local model couldn't answer: {said or e.reason}") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise LocalModelError(f"The local model didn't answer at {address} ({reason}): is Ollama running?") from e
    except (http.client.HTTPException, ValueError) as e:  # cut off part way, or not HTTP at all
        raise LocalModelError(UNREADABLE) from e
    try:
        data = jsonlib.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise LocalModelError(UNREADABLE)
    return data


def _capabilities(job, *, strict: bool) -> tuple[str, ...]:
    """What Ollama says the model can do (/api/show). Asked once for each
    address and model. Without strict, a failed lookup reads as "nothing
    special" and is asked again next time."""
    name, address = model(job), _address(job)
    key = (address, name)
    if key not in _CAPABILITIES:
        try:
            data = _post(address, "/api/show", {"model": name}, timeout=15)
        except LocalModelError:
            if strict:
                raise
            return ()
        found = data.get("capabilities")
        _CAPABILITIES[key] = tuple(str(c) for c in found) if isinstance(found, list) else ()
    return _CAPABILITIES[key]


def _image(image) -> str:
    """An image for Ollama: JPEG or PNG bytes (media.jpeg gives them), or a file's path, as base64."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
    elif isinstance(image, (str, os.PathLike)):
        data = Path(image).read_bytes()
    else:
        raise TypeError(f"an image is bytes (such as media.jpeg gives) or a file's path, not {type(image).__name__}")
    return base64.b64encode(data).decode("ascii")


def ask(job, prompt: str, *, images=(), json: bool = False, timeout: float = 120) -> str:
    """Ask the creator's local model `prompt`, and return its answer.

    `images` are pictures to show it with the prompt (bytes, such as
    media.jpeg() gives, or files), for a model that can see (can_see).
    With `json`, the model is asked to answer with JSON only (the answer
    is still text: read it with json.loads), and a model that can think
    first is told not to, so the answer isn't cut short. `timeout` is in
    seconds. Raises LocalModelError."""
    name, address = model(job), _address(job)
    body: dict = {"model": name, "prompt": str(prompt), "stream": False}
    if images:
        body["images"] = [_image(image) for image in images]
    if json:
        body["format"] = "json"
        if "thinking" in _capabilities(job, strict=False):
            body["think"] = False
    answer = _post(address, "/api/generate", body, timeout=float(timeout))
    if answer.get("error"):
        raise LocalModelError(f"The local model couldn't answer: {answer['error']}")
    return str(answer.get("response") or "")


def can_see(job) -> bool:
    """Whether the creator's local model can look at pictures: Ollama lists
    "vision" in what it can do. Raises LocalModelError when it can't be
    asked."""
    return "vision" in _capabilities(job, strict=True)


__all__ = ["NO_MODEL", "NO_PERMISSION", "OFF_THIS_PC", "LocalModelError", "ask", "can_see", "model"]
