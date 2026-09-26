"""Plans the user already pays for, signed in to instead of an API key.

ChatGPT runs through OpenAI's Codex SDK. These tests stand a fake Codex in
for the real one (which is 400 MB and needs an account), and check what
Clips Kitty itself promises: its own Codex folder, the plan's limits read
before every task, unattended jobs only with the user's say-so, OpenAI's raw
error text never passed on, and Codex never started for someone who hasn't
signed in.
"""

import sys
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from llm.base import generate_json
from llm.providers.base import LLMError
from llm.signin import catalog
from llm.signin.base import SignInProvider
from llm.signin.chatgpt import BLANKED_ENV, FREE_PLAN, ChatGPTPlan, parse_limits
from server import ai_api

SCHEMA = {"type": "object", "properties": {"clips": {"type": "array"}}, "required": ["clips"]}


def limits(used=10.0, reached=None, minutes=300):
    window = {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": 1_900_000_000}
    return {"rate_limits_by_limit_id": {"codex": {
        "limitId": "codex", "primary": window, "secondary": None,
        "rateLimitReachedType": reached, "spendControlReached": False, "planType": "plus"}}}


class World:
    """What the fake Codex knows: one account, shared by every client."""

    def __init__(self):
        self.signed_in = False
        self.plan = "plus"
        self.limits = limits()
        self.homes = []
        self.clients = 0
        self.logouts = 0
        self.threads = []
        self.prompts = []
        self.answer = '{"clips": []}'
        self.fail_with: Exception | None = None
        self.login_done = threading.Event()
        self.login_ok = True

    def factory(self, home):
        self.homes.append(home)
        return FakeClient(self)


class FakeHandle:
    def __init__(self, world, device):
        self.world = world
        self.auth_url = "https://auth.openai.com/oauth/authorize?fake=1"
        self.verification_url = "https://auth.openai.com/codex/device"
        self.user_code = "ABCD-1234"
        self.cancelled = False

    def wait(self):
        self.world.login_done.wait(5)
        if self.cancelled or not self.world.login_ok:
            return {"success": False, "error": "Login timed out"}
        self.world.signed_in = True
        return {"success": True}

    def cancel(self):
        self.cancelled = True
        self.world.login_done.set()


class FakeRaw:
    def __init__(self, world):
        self.world = world

    def request(self, method, params, *, response_model):
        assert method == "account/rateLimits/read"
        return self.world.limits


class FakeThread:
    def __init__(self, world):
        self.world = world

    def run(self, prompt, output_schema=None):
        self.world.prompts.append((prompt, output_schema))
        if self.world.fail_with:
            raise self.world.fail_with
        return types.SimpleNamespace(final_response=self.world.answer)


class FakeClient:
    def __init__(self, world):
        self.world = world
        world.clients += 1
        self._client = FakeRaw(world)

    def account(self):
        if not self.world.signed_in:
            return {"requires_openai_auth": True}
        return {"account": {"type": "chatgpt", "plan_type": self.world.plan, "email": "me@example.com"},
                "requires_openai_auth": True}

    def login_chatgpt(self):
        return FakeHandle(self.world, device=False)

    def login_chatgpt_device_code(self):
        return FakeHandle(self.world, device=True)

    def logout(self):
        self.world.signed_in = False
        self.world.logouts += 1

    def models(self):
        return {"data": [
            {"id": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "description": "Older.", "hidden": False},
            {"id": "gpt-6-luna", "display_name": "GPT-6-Luna", "is_default": True, "hidden": False},
            {"id": "secret-internal", "display_name": "Hidden", "hidden": True},
        ]}

    def thread_start(self, **kwargs):
        self.world.threads.append(kwargs)
        return FakeThread(self.world)

    def close(self):
        pass


@pytest.fixture
def fake_sdk(monkeypatch):
    """The names chatgpt.py imports from the SDK, without the SDK."""
    captured = {}

    class CodexConfig:
        def __init__(self, **kw):
            captured["config"] = kw

    def Codex(config):
        captured["codex"] = config
        return FakeClient(World())

    module = types.ModuleType("openai_codex")
    module.Codex = Codex
    module.CodexConfig = CodexConfig
    module.Sandbox = types.SimpleNamespace(read_only="read-only")
    generated = types.ModuleType("openai_codex.generated")
    v2 = types.ModuleType("openai_codex.generated.v2_all")
    v2.GetAccountRateLimitsResponse = dict
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    monkeypatch.setitem(sys.modules, "openai_codex.generated", generated)
    monkeypatch.setitem(sys.modules, "openai_codex.generated.v2_all", v2)
    return captured


class Env:
    def __init__(self, tmp_path, monkeypatch, world):
        self.world = world
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.settings = tmp_path / "settings.yaml"
        self.settings.write_text("model: gemma:7b\n", encoding="utf-8")
        self.config = {"llm": {"backend": "ollama/gemma:7b", "data_dir": str(self.data),
                               "ollama_host": "http://127.0.0.1:1"}}
        self.db_path = tmp_path / "state.db"
        monkeypatch.setattr(catalog, "_live", {})
        monkeypatch.setitem(catalog._FACTORIES, "chatgpt",
                            lambda d: ChatGPTPlan(d, client_factory=world.factory))
        app = FastAPI()
        ai_api.install(app, config=self.config, db=lambda: StateDB(self.db_path),
                       data_dir=self.data, settings_path=self.settings)
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def plan(self) -> ChatGPTPlan:
        return catalog.get("chatgpt", self.data)

    def sign_in(self):
        started = self.client.post("/ai/signin/chatgpt/start", json={}).json()
        self.world.login_done.set()
        for _ in range(100):
            view = self.client.get("/ai/signin/chatgpt").json()
            if view["flow"]["state"] != "waiting":
                return started, view
            time.sleep(0.02)
        raise AssertionError("sign-in never finished")


@pytest.fixture
def env(tmp_path, monkeypatch, fake_sdk):
    return Env(tmp_path, monkeypatch, World())


# ---- runtime and isolation -------------------------------------------------------


def test_the_option_is_hidden_when_the_runtime_isnt_installed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai_codex", None)  # import fails
    monkeypatch.setattr(catalog, "_live", {})
    plan = ChatGPTPlan(tmp_path)
    assert plan.available() is False
    assert plan.status()["signed_in"] is False
    with pytest.raises(LLMError) as e:
        plan.check_job({})
    assert "isn't installed" in e.value.message


def test_codex_runs_as_clips_kitty_in_its_own_folder_with_api_keys_blanked(tmp_path, fake_sdk, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "a" * 40)
    plan = ChatGPTPlan(tmp_path)
    plan.start()
    config = fake_sdk["config"]
    assert config["client_name"] == "clips_kitty" and config["client_title"] == "Clips Kitty"
    assert config["env"]["CODEX_HOME"] == str(tmp_path / "codex")  # never ~/.codex
    for name in BLANKED_ENV:
        assert config["env"][name] == ""  # a key in the environment can't stand in for the plan
    written = (tmp_path / "codex" / "config.toml").read_text(encoding="utf-8")
    for line in ('cli_auth_credentials_store = "keyring"', "check_for_update_on_startup = false",
                 'sandbox_mode = "read-only"', 'web_search = "disabled"', "shell_tool = false",
                 "apps = false", "remote_plugin = false", 'persistence = "none"',
                 'sandbox = "unelevated"', "enabled = false"):
        assert line in written
    plan.cancel()


def test_codex_isnt_started_for_someone_who_never_signed_in(env):
    body = env.client.get("/ai").json()
    row = next(p for p in body["signin"] if p["id"] == "chatgpt")
    assert row["signed_in"] is False and row["group"] == "openai" and row["experimental"] is True
    assert env.world.clients == 0


# ---- signing in and out ------------------------------------------------------------


def test_signing_in_opens_the_providers_page_and_is_kept_after_a_restart(env, monkeypatch):
    started, view = env.sign_in()
    assert started["auth_url"].startswith("https://auth.openai.com/")
    assert view["flow"]["state"] == "done"
    assert view["signed_in"] is True and view["plan"] == "plus"
    assert view["limits"]["known"] and view["limits"]["windows"][0]["label"] == "5-hour"

    monkeypatch.setattr(catalog, "_live", {})  # the app restarts
    assert env.plan().status()["signed_in"] is True


def test_the_device_code_flow_shows_the_code(env):
    started = env.client.post("/ai/signin/chatgpt/start", json={"device": True}).json()
    assert started == {"verification_url": "https://auth.openai.com/codex/device", "user_code": "ABCD-1234"}
    env.client.post("/ai/signin/chatgpt/cancel")
    assert env.client.get("/ai/signin/chatgpt").json()["flow"]["state"] == "idle"


def test_a_sign_in_that_times_out_says_so(env):
    env.world.login_ok = False
    _started, view = env.sign_in()
    assert view["flow"] == {"state": "error", "error": "The sign-in wasn't finished in time. Try again."}
    assert view["signed_in"] is False


def test_signing_out_removes_the_sign_in_and_jobs_then_refuse(env):
    env.sign_in()
    env.client.post("/ai/activate", json={"provider": "chatgpt", "model": "gpt-6-luna"})
    out = env.client.post("/ai/signin/chatgpt/sign-out").json()
    assert env.world.logouts == 1 and "Signed out" in out["message"]
    assert env.client.get("/ai/signin/chatgpt").json()["signed_in"] is False
    with pytest.raises(LLMError) as e:
        env.plan().check_job({})
    assert "Sign in with ChatGPT" in e.value.message
    refused = env.client.post("/ai/activate", json={"provider": "chatgpt", "model": "gpt-6-luna"})
    assert refused.status_code == 400


# ---- models and jobs --------------------------------------------------------------


def test_only_the_accounts_own_models_are_offered_default_first(env):
    env.sign_in()
    models = env.client.get("/ai/signin/chatgpt/models").json()["models"]
    assert [m["id"] for m in models] == ["gpt-6-luna", "gpt-5.6-terra"]


def test_choosing_the_plan_needs_a_sign_in_and_is_saved(env):
    refused = env.client.post("/ai/activate", json={"provider": "chatgpt", "model": "gpt-6-luna"})
    assert refused.status_code == 400
    env.sign_in()
    chosen = env.client.post("/ai/activate", json={"provider": "chatgpt", "model": "gpt-6-luna"}).json()
    assert chosen["active"] == {"provider": "chatgpt", "model": "gpt-6-luna", "local": False}
    assert "model: chatgpt/gpt-6-luna" in env.settings.read_text(encoding="utf-8")


def test_a_task_is_an_ephemeral_read_only_codex_thread_held_to_the_schema(env):
    from llm.registry import create_backend

    env.sign_in()
    backend = create_backend({"backend": "chatgpt/gpt-6-luna", "data_dir": str(env.data)})
    assert backend.name == "chatgpt/gpt-6-luna"
    assert generate_json(backend, "pick clips", SCHEMA) == '{"clips": []}'
    thread = env.world.threads[-1]
    assert thread["ephemeral"] is True and thread["sandbox"] == "read-only"
    assert thread["model"] == "gpt-6-luna" and "Clips Kitty" in thread["developer_instructions"]
    assert env.world.prompts[-1] == ("pick clips", SCHEMA)


def test_a_used_up_plan_stops_the_job_before_any_task_runs(env):
    env.sign_in()
    backend = env.plan().backend("gpt-6-luna", {})
    for used_up in (limits(used=100.0), limits(used=40.0, reached="rate_limit_reached")):
        env.world.limits = used_up
        env.plan()._limits_at = 0.0
        with pytest.raises(LLMError) as e:
            backend.generate("pick clips", json_mode=True)
        assert e.value.kind == "rate_limited"
        assert "rather than use ChatGPT credits" in e.value.message
    assert env.world.threads == []


def test_openais_own_error_text_is_never_passed_on(env):
    env.sign_in()
    env.world.plan = "free"
    env.plan()._status_at = 0.0
    env.plan().status()
    env.world.fail_with = RuntimeError(
        "unexpected status 401 Unauthorized: Incorrect API key provided: sk-svcac****fvMA, "
        "url: https://chatgpt.com/backend-api/codex/responses")
    with pytest.raises(LLMError) as e:
        env.plan().backend("gpt-6-luna", {}).generate("hi")
    assert e.value.message == FREE_PLAN
    assert "sk-" not in e.value.message and "backend-api" not in e.value.message

    tested = env.client.post("/ai/signin/chatgpt/test", json={"model": "gpt-6-luna"}).json()
    assert tested["ok"] is False and "free ChatGPT account" in tested["message"]


# ---- unattended jobs ---------------------------------------------------------------


def test_watched_channels_use_the_plan_only_when_allowed(env):
    from server.jobs import Worker

    env.sign_in()
    cfg = {"llm": {"backend": "chatgpt/gpt-6-luna", "data_dir": str(env.data)}}
    db = StateDB(env.db_path)
    try:
        with pytest.raises(LLMError) as e:
            Worker._check_plan(db, {"llm": dict(cfg["llm"])}, {"origin": "watch"})
        assert "Watched channels don't use your ChatGPT plan" in e.value.message
        Worker._check_plan(db, {"llm": dict(cfg["llm"])}, {"origin": "manual"})  # Clip this, a person
        Worker._check_plan(db, {"llm": dict(cfg["llm"])}, {})                    # a pasted link

        view = env.client.post("/ai/signin/chatgpt/automation", json={"allowed": True}).json()
        assert view["automation_allowed"] is True
        Worker._check_plan(db, {"llm": dict(cfg["llm"])}, {"origin": "stream"})
    finally:
        db.close()


def test_watch_jobs_say_where_they_came_from(tmp_path):
    from server.automation import job_payload

    watch = {"options": '{"preset": "standard"}'}
    assert job_payload(watch, "https://x/v")["origin"] == "watch"
    assert job_payload(watch, "https://x/v", "manual")["origin"] == "manual"
    db = StateDB(tmp_path / "state.db")
    try:
        db.set_watch_item(1, requested=1)  # the column exists and may be set
    finally:
        db.close()


# ---- the layer is open to more providers -------------------------------------------


class ExamplePlan(SignInProvider):
    id = "exampleplan"
    label = "Example plan"
    group = "example"

    def __init__(self, data_dir):
        self.signed = False

    def available(self):
        return True

    def start(self, device=False):
        self.signed = True
        return {"auth_url": "https://example.com/sign-in"}

    def cancel(self):
        pass

    def status(self):
        return {"signed_in": self.signed, "plan": "pro", "email": "", "flow": {"state": "idle", "error": ""}}

    def limits(self):
        return {"windows": [], "reached": False, "resets_at": None, "known": False}

    def models(self):
        from llm.providers.base import ModelInfo

        return [ModelInfo(id="example-1", name="Example 1")]

    def sign_out(self):
        self.signed = False

    def backend(self, model, llm_config):
        raise NotImplementedError

    def check_job(self, llm_config):
        pass


def test_another_plan_needs_only_a_catalogue_line(env, monkeypatch):
    monkeypatch.setitem(catalog._FACTORIES, "exampleplan", ExamplePlan)
    assert "exampleplan" in [p["id"] for p in env.client.get("/ai").json()["signin"]]
    assert env.client.post("/ai/signin/exampleplan/start", json={}).json()["auth_url"]
    assert [m["id"] for m in env.client.get("/ai/signin/exampleplan/models").json()["models"]] == ["example-1"]
    chosen = env.client.post("/ai/activate", json={"provider": "exampleplan", "model": "example-1"}).json()
    assert chosen["active"]["provider"] == "exampleplan"


# ---- the plans that can't be used say why ------------------------------------------


def test_claude_and_gemini_say_why_their_plans_cant_be_used(env):
    rows = {p["id"]: p for p in env.client.get("/ai").json()["providers"]}
    assert "Anthropic only allows them in its own apps" in rows["anthropic"]["plan_note"]
    assert rows["anthropic"]["plan_note_url"].startswith("https://code.claude.com/")
    assert "Google only allows them in its own apps" in rows["gemini"]["plan_note"]
    assert rows["openrouter"]["plan_note"] == ""


def test_limits_are_read_as_openai_reports_them():
    parsed = parse_limits({"rate_limits": {"primary": {"used_percent": 0, "window_duration_mins": 43200,
                                                       "resets_at": 1792970296}, "plan_type": "free"}})
    assert parsed == {"windows": [{"label": "30-day", "used_percent": 0.0, "resets_at": 1792970296,
                                   "minutes": 43200}],
                      "reached": False, "resets_at": None, "known": True}
    weekly = parse_limits(limits(used=100.0, minutes=10080))
    assert weekly["windows"][0]["label"] == "Weekly" and weekly["reached"] is True
    assert weekly["resets_at"] == 1_900_000_000
