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
from llm.providers.speech import list_stt_models
from llm.signin import catalog as signin
from llm.spec import LOCAL, is_local, parse_spec

# The local entry the UI lists first. Not a ProviderSpec: nothing about it is
# a key, a bill or a request leaving the PC.
LOCAL_ENTRY = {
    "id": LOCAL,
    "label": "This PC — Ollama",
    "local": True,
    "tier": 1,
    "recommended": False,
    "tagline": "Runs on your computer. No API key needed.",
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
# Models, capabilities and prices are fetched together and kept this long;
# Refresh models fetches them again at once. Prices change, so not for days.
MODELS_CACHE_SECONDS = 6 * 60 * 60


class KeyIn(BaseModel):
    api_key: str = ""


class TestIn(BaseModel):
    model: str = ""


class ActivateIn(BaseModel):
    provider: str
    model: str = ""


class SttCheckIn(BaseModel):
    model: str


class TranscriptionIn(BaseModel):
    backend: str = "local"
    model: str = ""


class SignInStartIn(BaseModel):
    device: bool = False  # the device-code flow, for when the browser page doesn't come back


class AutomationIn(BaseModel):
    allowed: bool


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
    # (provider, "text" | "stt") -> (fetched_at, models)
    models_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    def _spec(provider_id: str):
        spec = get(provider_id)
        if spec is None:
            raise HTTPException(404, f"Unknown AI provider '{provider_id}'.")
        return spec

    def _fail(e: LLMError) -> HTTPException:
        return HTTPException(400, scrub_secrets(e.message)[:500])

    def _keyed(spec):
        """(spec at the address of the saved key's region, key)."""
        spec, key = keys.resolve(data_path, spec)
        if not key:
            raise HTTPException(400, f"Add your {spec.key_label} first.")
        return spec, key

    def _check_key(spec, key: str) -> tuple[str, str]:
        """(message, region). A provider with regions is tried in each, in
        order, until one accepts the key: its keys only work where they were
        made, and the user shouldn't have to know which that was."""
        adapter = adapter_for(spec)
        if not spec.regions:
            return adapter.check_key(spec, key), ""
        for region_id, label, _url in spec.regions:
            try:
                message = adapter.check_key(spec.in_region(region_id), key)
            except LLMError as e:
                if e.kind == "invalid_key":
                    continue
                raise
            return f"{message} ({label} region)", region_id
        names = ", ".join(label for _id, label, _url in spec.regions)
        raise LLMError("invalid_key", f"{spec.label} didn't accept this key in any region it's "
                                      f"checked in ({names}). Check it was copied whole.")

    def _models(spec, key: str, refresh: bool = False, kind: str = "text") -> tuple[float, list[dict]]:
        """(fetched_at, models). A failed fetch raises: old prices are never
        passed off as current."""
        cached = models_cache.get((spec.id, kind))
        if cached and not refresh and time.time() - cached[0] < MODELS_CACHE_SECONDS:
            return cached
        if kind == "stt":
            listed = list_stt_models(spec, key)
        else:
            listed = adapter_for(spec).list_models(spec, key)
        fresh = (time.time(), [m.as_dict() for m in listed])
        models_cache[(spec.id, kind)] = fresh
        return fresh

    def _forget_models(provider_id: str) -> None:
        for kind in ("text", "stt"):
            models_cache.pop((provider_id, kind), None)

    def _save_transcription(backend: str, model: str) -> None:
        write_transcription(settings_path, backend, model)
        config["transcription"] = {"backend": backend, "model": model}

    def _plan(provider_id: str):
        plan = signin.get(provider_id, data_path)
        if plan is None:
            raise HTTPException(404, f"Unknown plan sign-in '{provider_id}'.")
        if not plan.available():
            raise HTTPException(400, f"{plan.label} isn't installed in this version of Clips Kitty.")
        return plan

    def _automation_allowed(provider_id: str) -> bool:
        d = db()
        try:
            return d.get_flag(signin.AUTOMATION_FLAG + provider_id) == "1"
        finally:
            d.close()

    def _plan_view(plan) -> dict:
        """What the card shows for a plan sign-in. Never a token."""
        state = plan.status()
        view = {**plan.public(), **state, "automation_allowed": _automation_allowed(plan.id),
                "limits": {"windows": [], "reached": False, "resets_at": None, "known": False}}
        if state.get("signed_in"):
            view["limits"] = plan.limits()
        return view

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
                 "key_tail": keys.key_tail(data_path, spec.id),
                 "key_region": spec.region_label(keys.load_region(data_path, spec.id))}
                for spec in PROVIDERS.values()
            ],
            # Plans the user already pays for, signed in to instead of a key
            # (llm/signin/). Only the ones whose runtime this build has.
            "signin": [_plan_view(plan) for plan in signin.all_for(data_path) if plan.available()],
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
            message, region = _check_key(spec, key)
        except LLMError as e:
            raise _fail(e) from e
        keys.save_key(data_path, spec.id, key, region)
        _forget_models(spec.id)  # a new key can see different models
        return {**status(), "message": message}

    @app.delete("/ai/providers/{provider_id}/key")
    def delete_key(provider_id: str):
        spec = _spec(provider_id)
        removed = keys.wipe_key(data_path, spec.id)
        _forget_models(spec.id)
        return {**status(), "removed": removed}

    @app.get("/ai/providers/{provider_id}/models")
    def provider_models(provider_id: str, refresh: bool = False, kind: str = "text"):
        """The models, capabilities and current prices, from the provider,
        with the user's key. kind=stt lists voice (transcription) models."""
        spec = _spec(provider_id)
        if kind not in ("text", "stt"):
            raise HTTPException(400, "kind is text or stt")
        if kind == "stt" and not spec.stt:
            raise HTTPException(400, f"{spec.label} doesn't transcribe here.")
        try:
            fetched_at, models = _models(*_keyed(spec), refresh, kind)
        except LLMError as e:
            raise _fail(e) from e
        return {"models": models, "fetched_at": fetched_at, "kind": kind}

    @app.post("/ai/providers/{provider_id}/test")
    def test_provider(provider_id: str, body: TestIn):
        """Key, reachability and model, without spending a single token."""
        spec, key = _keyed(_spec(provider_id))
        model = body.model.strip()
        try:
            if spec.key_check_path or not model:
                adapter_for(spec).check_key(spec, key)
            if model:
                listed = [m["id"] for m in _models(spec, key, refresh=True)[1]]
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
            elif signin.is_signin(provider):
                plan = _plan(provider)
                if not model:
                    raise HTTPException(400, f"Choose a {plan.label} model.")
                if not plan.status().get("signed_in"):
                    raise HTTPException(400, f"Sign in to your {plan.label} first.")
                if is_local(current):
                    d.set_flag(LAST_LOCAL_FLAG, parse_spec(current)[1])
                spec_text = switch_model(settings_path, f"{plan.id}/{model}")
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

    # ---- a plan the user already pays for, signed in to (llm/signin/) --------

    @app.get("/ai/signin/{provider_id}")
    def plan_status(provider_id: str):
        """Signed in or not, the plan, its usage, and how a sign-in is going
        (the card polls this while the browser page is open)."""
        return _plan_view(_plan(provider_id))

    @app.post("/ai/signin/{provider_id}/start")
    def plan_start(provider_id: str, body: SignInStartIn):
        """Begin signing in: a page for the user's own browser, or a code to
        type at the provider's address. Only ever run because they asked."""
        plan = _plan(provider_id)
        try:
            return plan.start(device=body.device)
        except LLMError as e:
            raise _fail(e) from e

    @app.post("/ai/signin/{provider_id}/cancel")
    def plan_cancel(provider_id: str):
        plan = _plan(provider_id)
        plan.cancel()
        return _plan_view(plan)

    @app.post("/ai/signin/{provider_id}/sign-out")
    def plan_sign_out(provider_id: str):
        """Remove the sign-in from this PC. The provider's runtime also asks
        the provider to revoke it; the local sign-in goes either way."""
        plan = _plan(provider_id)
        try:
            plan.sign_out()
        except LLMError as e:
            raise _fail(e) from e
        return {**status(), "message": f"Signed out of your {plan.label} on this PC."}

    @app.get("/ai/signin/{provider_id}/models")
    def plan_models(provider_id: str):
        plan = _plan(provider_id)
        if not plan.status().get("signed_in"):
            raise HTTPException(400, f"Sign in to your {plan.label} first.")
        try:
            models = [m.as_dict() for m in plan.models()]
        except LLMError as e:
            raise _fail(e) from e
        return {"models": models, "fetched_at": time.time(), "kind": "text"}

    @app.post("/ai/signin/{provider_id}/automation")
    def plan_automation(provider_id: str, body: AutomationIn):
        """Whether Watched channels and stream VODs, which run with nobody
        watching, may use the plan. Off until the user turns it on."""
        plan = _plan(provider_id)
        d = db()
        try:
            d.set_flag(signin.AUTOMATION_FLAG + plan.id, "1" if body.allowed else "0")
        finally:
            d.close()
        return _plan_view(plan)

    @app.post("/ai/signin/{provider_id}/test")
    def plan_test(provider_id: str, body: TestIn):
        """One tiny task on the plan: the only way to know it works for this
        account (OpenAI refuses free accounts outside its own app). Uses a
        little of the plan, which is why the card says so."""
        plan = _plan(provider_id)
        try:
            plan.check_job({})
            answer = plan.backend(body.model.strip(), {}).generate("Reply with the single word OK.")
        except LLMError as e:
            return {"ok": False, "kind": e.kind, "message": scrub_secrets(e.message)}
        return {"ok": True, "message": f"Connected to your {plan.label}."
                + (" It answered." if answer.strip() else "")}

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
            if model and model not in spec.stt["models"]:
                # Any other model has to show it returns word timings first.
                raise HTTPException(400, f"Check {model} first: it has to return the word timings captions need.")
            model = model or spec.stt["models"][0]
        else:
            model = ""
        _save_transcription(backend, model)
        return status()

    @app.post("/ai/providers/{provider_id}/stt-check")
    def check_voice_model(provider_id: str, body: SttCheckIn):
        """Choose a voice model, checking it first if it is not a known one.

        A model known to return word timings is saved straight away. Any
        other is sent a three-second test clip once, through the same code a
        job uses, and saved only if word timings come back. Only ever run
        because the user picked the model; nothing changes if it fails.
        """
        from transcription.cloud import check_model

        spec = _spec(provider_id)
        model = body.model.strip()
        if not spec.stt or not model:
            raise HTTPException(400, f"{spec.label} doesn't transcribe here.")
        spec, key = _keyed(spec)
        if model in spec.stt["models"]:
            ok, message = True, f"{model} is ready."
        else:
            ok, message = check_model(spec, key, model)
        if ok:
            _save_transcription(spec.id, model)
        return {**status(), "ok": ok, "message": scrub_secrets(message)}
