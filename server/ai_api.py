"""Settings → AI: which model does the AI work, local or on the user's own key.

Local (Ollama on this PC) is the default and is listed first. The cloud
providers are an opt-in for PCs that cannot run the models, and every one of
them is bring-your-own-key: the key is the user's, requests go from this PC
straight to the provider, and the provider bills the user. There is no Clips
Kitty key, account or proxy.

The same rules as the WoopSocial key (server/woopsocial_api.py): a key is
checked with the provider before it is kept, and no route ever returns it; the
UI gets whether one is saved and its last four characters, nothing more.
"""

import re
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from core.scrub import scrub_secrets
from llm.providers import keys
from llm.providers.adapters import adapter_for
from llm.providers.base import LLMError
from llm.providers.catalog import PROVIDERS, get
from llm.spec import LOCAL, is_local, parse_spec

# The local entry the UI lists first. Not a ProviderSpec: nothing about it is
# a key, a bill or a request leaving the PC.
LOCAL_ENTRY = {
    "id": LOCAL,
    "label": "This PC — Ollama",
    "local": True,
    "key_label": "",
    "key_url": "",
    "pricing_url": "",
    "privacy": "Runs on this PC. Nothing is sent anywhere.",
    "stt": False,
    "has_key": False,
    "key_tail": "",
}

# The local model in use before switching to a cloud one, so switching back
# returns to it rather than to whatever happens to be installed.
LAST_LOCAL_FLAG = "ai_last_local_model"
MODELS_CACHE_SECONDS = 24 * 60 * 60


class KeyIn(BaseModel):
    api_key: str = ""


class TestIn(BaseModel):
    model: str = ""


class ActivateIn(BaseModel):
    provider: str
    model: str = ""


class TranscriptionIn(BaseModel):
    backend: str = "local"
    model: str = ""


_TRANSCRIPTION_BLOCK = re.compile(r"(?m)^transcription:[^\n]*\n(?:[ \t]+[^\n]*(?:\n|$))*")


def write_transcription(settings_path: Path, backend: str, model: str) -> None:
    """Rewrite the `transcription:` section of settings.yaml, or add it: an
    install's own copy predates the section, and local is what it meant."""
    text = settings_path.read_text(encoding="utf-8")
    block = f'transcription:\n  backend: {backend}\n  model: "{model}"\n'
    if _TRANSCRIPTION_BLOCK.search(text):
        text = _TRANSCRIPTION_BLOCK.sub(lambda _m: block, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n" + block
    settings_path.write_text(text, encoding="utf-8")


def install(app, *, config, db, data_dir, settings_path) -> None:
    data_path = Path(data_dir)
    models_cache: dict[str, tuple[float, list[dict]]] = {}

    def _spec(provider_id: str):
        spec = get(provider_id)
        if spec is None:
            raise HTTPException(404, f"Unknown AI provider '{provider_id}'.")
        return spec

    def _fail(e: LLMError) -> HTTPException:
        return HTTPException(400, scrub_secrets(e.message)[:500])

    def _key(spec) -> str:
        key = keys.load_key(data_path, spec.id)
        if not key:
            raise HTTPException(400, f"Add your {spec.key_label} first.")
        return key

    def _models(spec, key: str, refresh: bool = False) -> list[dict]:
        cached = models_cache.get(spec.id)
        if cached and not refresh and time.time() - cached[0] < MODELS_CACHE_SECONDS:
            return cached[1]
        models = [m.as_dict() for m in adapter_for(spec).list_models(spec, key)]
        models_cache[spec.id] = (time.time(), models)
        return models

    def status() -> dict:
        provider, model = parse_spec(config["llm"].get("backend") or "")
        transcription = config.get("transcription") or {}
        return {
            "active": {"provider": provider, "model": model, "local": provider == LOCAL},
            "transcription": {"backend": str(transcription.get("backend") or "local"),
                              "model": str(transcription.get("model") or "")},
            "providers": [LOCAL_ENTRY] + [
                {**spec.public(),
                 "has_key": keys.has_key(data_path, spec.id),
                 "key_tail": keys.key_tail(data_path, spec.id)}
                for spec in PROVIDERS.values()
            ],
        }

    @app.get("/ai")
    def ai_status():
        return status()

    @app.put("/ai/providers/{provider_id}/key")
    def put_key(provider_id: str, body: KeyIn):
        spec = _spec(provider_id)
        key = (body.api_key or "").strip()
        if not key:
            raise HTTPException(400, f"Enter your {spec.key_label}.")
        # Checked before it is kept: a key that does not work never replaces
        # one that does, and the card never claims a connection that is not there.
        try:
            message = adapter_for(spec).check_key(spec, key)
        except LLMError as e:
            raise _fail(e) from e
        keys.save_key(data_path, spec.id, key)
        models_cache.pop(spec.id, None)
        return {**status(), "message": message}

    @app.delete("/ai/providers/{provider_id}/key")
    def delete_key(provider_id: str):
        spec = _spec(provider_id)
        removed = keys.wipe_key(data_path, spec.id)
        models_cache.pop(spec.id, None)
        return {**status(), "removed": removed}

    @app.get("/ai/providers/{provider_id}/models")
    def provider_models(provider_id: str, refresh: bool = False):
        spec = _spec(provider_id)
        try:
            return {"models": _models(spec, _key(spec), refresh)}
        except LLMError as e:
            raise _fail(e) from e

    @app.post("/ai/providers/{provider_id}/test")
    def test_provider(provider_id: str, body: TestIn):
        """Key, reachability and model, without spending a single token."""
        spec = _spec(provider_id)
        key = _key(spec)
        model = body.model.strip()
        try:
            if spec.key_check_path or not model:
                adapter_for(spec).check_key(spec, key)
            if model:
                listed = [m["id"] for m in _models(spec, key, refresh=True)]
                if listed and model not in listed:
                    return {"ok": False, "kind": "model_unavailable",
                            "message": f"{spec.label} doesn't list {model} for your key."}
        except LLMError as e:
            return {"ok": False, "kind": e.kind, "message": scrub_secrets(e.message)}
        return {"ok": True, "message": f"Connected to {spec.label}."
                + (f" {model} is available." if model else "")}

    @app.post("/ai/activate")
    def activate(body: ActivateIn):
        from llm.manager import resolve_usable_model, switch_model

        provider = body.provider.strip()
        model = body.model.strip()
        current = config["llm"].get("backend") or ""
        d = db()
        try:
            if provider == LOCAL:
                tag = (model or d.get_flag(LAST_LOCAL_FLAG)
                       or resolve_usable_model(config["llm"].get("ollama_host", "http://localhost:11434"), "")
                       or "gemma:7b")
                spec_text = switch_model(settings_path, tag)
            else:
                spec = _spec(provider)
                if not model:
                    raise HTTPException(400, f"Choose a {spec.label} model.")
                if not keys.has_key(data_path, spec.id):
                    raise HTTPException(400, f"Add your {spec.key_label} first.")
                if is_local(current):
                    d.set_flag(LAST_LOCAL_FLAG, parse_spec(current)[1])
                spec_text = switch_model(settings_path, f"{spec.id}/{model}")
        finally:
            d.close()
        config["llm"]["backend"] = spec_text  # live config follows the file
        return status()

    @app.post("/ai/transcription")
    def set_transcription(body: TranscriptionIn):
        """Whisper on this PC (the default), or online on the user's own key."""
        backend = body.backend.strip() or "local"
        model = body.model.strip()
        if backend != "local":
            spec = _spec(backend)
            if not spec.stt:
                raise HTTPException(400, f"{spec.label} can't transcribe with the word timings captions need.")
            if not keys.has_key(data_path, spec.id):
                raise HTTPException(400, f"Add your {spec.key_label} first.")
            model = model if model in spec.stt["models"] else spec.stt["models"][0]
        else:
            model = ""
        write_transcription(settings_path, backend, model)
        config["transcription"] = {"backend": backend, "model": model}
        return status()
