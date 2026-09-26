"""The ChatGPT plan, through OpenAI's own Codex SDK (Experimental).

OpenAI documents its Codex SDK and app-server for building Codex into other
apps, with "Sign in with ChatGPT" run by Codex itself: the sign-in page opens
in the user's browser, and Codex stores and refreshes the result (encrypted,
its key in Windows Credential Manager). Clips Kitty never sees a password or a
token; it only asks Codex who is signed in, what the plan's limits are, and to
run a task.

How it is kept to what OpenAI documents, and safe on a PC nobody is watching:
- Its own Codex folder (<data>/codex), so the user's own Codex CLI login is
  never read or reused, and its API-key variables are blanked so a key in the
  environment can't stand in for the plan.
- It says it is Clips Kitty (client_name), never Codex.
- Codex can't run commands, search the web, load plugins or update itself
  here (CONFIG); each job is an ephemeral, read-only thread in an empty folder,
  and the answer is held to the job's JSON schema.
- Before every task it reads the plan's limits and stops at 100%: after the
  included usage OpenAI spends purchased credits automatically, and there is no
  per-app switch to prevent that, so stopping is the only safe behaviour.

Not in the Store build or the installer yet: the runtime (openai-codex, about
400 MB) is only there when installed, and until then this option isn't shown.
"""

import threading
import time
from contextlib import suppress
from pathlib import Path

from core.scrub import scrub_secrets
from llm.base import LLMBackend
from llm.providers.base import LLMError, ModelInfo
from llm.signin.base import SignInProvider

# Written to <data>/codex/config.toml before Codex starts. Every key is from
# OpenAI's Codex configuration reference.
CONFIG = """\
# Written by Clips Kitty on every start; edits here are replaced.
cli_auth_credentials_store = "keyring"
check_for_update_on_startup = false
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"

[history]
persistence = "none"

[analytics]
enabled = false

[windows]
sandbox = "unelevated"

[features]
shell_tool = false
apps = false
memories = false
remote_plugin = false
multi_agent = false
goals = false
"""

# Codex prefers any of these over the ChatGPT sign-in when they are set. Blank
# (which Codex treats as unset) so only the plan is ever used.
BLANKED_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL")

INSTRUCTIONS = (
    "You are working inside Clips Kitty, a desktop app that turns long videos into short "
    "clips. Each request is a self-contained text task: everything you need is in the "
    "message. Don't look for files or run anything; answer the request directly, in the "
    "format it asks for."
)

USAGE_URL = "https://chatgpt.com/codex/settings/usage"
TERMS_URL = "https://learn.chatgpt.com/docs/pricing"
STATUS_TTL = 60.0
LIMITS_TTL = 30.0

FREE_PLAN = ("OpenAI refused this for a free ChatGPT account: it offers Codex on free "
             "accounts only in its own desktop app for now. A paid ChatGPT plan, an OpenAI API "
             "key, or OpenRouter will work.")


def _default_client(home: Path):
    from openai_codex import Codex, CodexConfig

    env = dict.fromkeys(BLANKED_ENV, "")
    env["CODEX_HOME"] = str(home)
    return Codex(CodexConfig(env=env, client_name="clips_kitty", client_title="Clips Kitty"))


def _dump(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj if isinstance(obj, dict) else {}


def _pick(d: dict, *names):
    for name in names:
        if isinstance(d, dict) and d.get(name) is not None:
            return d[name]
    return None


def _window_label(minutes: int) -> str:
    if minutes and minutes % 1440 == 0:
        days = minutes // 1440
        return "Weekly" if days == 7 else f"{days}-day"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour"
    return f"{minutes}-minute" if minutes else "Usage"


def parse_limits(raw: dict) -> dict:
    """OpenAI's account/rateLimits/read answer, as the card shows it."""
    buckets = raw.get("rate_limits_by_limit_id") or raw.get("rateLimitsByLimitId") or {}
    if not buckets:
        single = raw.get("rate_limits") or raw.get("rateLimits")
        buckets = {"codex": single} if single else {}
    windows, reached, resets = [], False, []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        if _pick(bucket, "rateLimitReachedType", "rate_limit_reached_type") or \
                _pick(bucket, "spendControlReached", "spend_control_reached"):
            reached = True
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            used = float(_pick(window, "used_percent", "usedPercent") or 0)
            minutes = int(_pick(window, "window_duration_mins", "windowDurationMins") or 0)
            reset = _pick(window, "resets_at", "resetsAt")
            windows.append({"label": _window_label(minutes), "used_percent": used,
                            "resets_at": reset, "minutes": minutes})
            if used >= 100:
                reached = True
                if reset:
                    resets.append(float(reset))
    return {"windows": windows, "reached": reached,
            "resets_at": max(resets) if resets else None, "known": bool(windows)}


def _when(ts: float | None) -> str:
    if not ts:
        return "it resets"
    return time.strftime("%a %d %b, %H:%M", time.localtime(ts))


class ChatGPTPlan(SignInProvider):
    id = "chatgpt"
    label = "ChatGPT plan"
    group = "openai"
    tagline = ("Use a paid ChatGPT plan's Codex limits instead of API credit. Free accounts can't "
               "use it outside OpenAI's own app yet.")
    privacy = ("Transcripts and prompts go to OpenAI through Codex, OpenAI's own software running on "
               "this PC, signed in to your ChatGPT account. Clips Kitty never sees your password or "
               "sign-in.")
    usage_url = USAGE_URL
    terms_url = TERMS_URL
    automation_note = ("OpenAI calls an API key the right way to automate; your plan's limits "
                       "are shared with your own ChatGPT and Codex use.")
    experimental = True
    signin_label = "Sign in with ChatGPT"
    limit_note = ("When your plan's limit is reached, jobs stop until it resets. Clips Kitty never "
                  "uses ChatGPT credits.")
    sign_out_note = ("Signs Clips Kitty out on this PC and asks OpenAI to cancel the sign-in; if that "
                     "request fails, the sign-in is still removed here.")
    disclaimer = "Clips Kitty isn't made by or affiliated with OpenAI."

    def __init__(self, data_dir, client_factory=None):
        self.home = Path(data_dir) / "codex"
        self._factory = client_factory or _default_client
        self._lock = threading.Lock()          # one request at a time on the shared client
        self._client = None
        self._flow_lock = threading.Lock()
        self._flow = {"state": "idle", "error": ""}
        self._flow_token = 0
        self._login = None                     # (client, handle) of a sign-in in progress
        self._status: dict | None = None
        self._status_at = 0.0
        self._limits: dict | None = None
        self._limits_at = 0.0

    # ---- runtime -----------------------------------------------------------

    def available(self) -> bool:
        if self._factory is not _default_client:
            return True
        try:
            import openai_codex  # noqa: F401
        except ImportError:
            return False
        return True

    def _prepare(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.toml").write_text(CONFIG, encoding="utf-8")
        (self.home / "work").mkdir(exist_ok=True)

    def _new_client(self):
        if not self.available():
            raise LLMError("not_configured", "The ChatGPT plan option isn't installed in this "
                                             "version of Clips Kitty.")
        self._prepare()
        try:
            return self._factory(self.home)
        except LLMError:
            raise
        except Exception as e:
            raise LLMError("provider_down", "Codex, which runs the ChatGPT plan, didn't start: "
                                            f"{scrub_secrets(type(e).__name__)}.") from e

    def _get_client(self):
        """The shared client. Call with self._lock held."""
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            # A client being thrown away may already be dead; closing it is best effort.
            with suppress(Exception):
                client.close()

    def _call(self, fn):
        """Run fn(client) on the shared client, starting a fresh one once if
        the last one died."""
        with self._lock:
            for attempt in (1, 2):
                client = self._get_client()
                try:
                    return fn(client)
                except LLMError:
                    raise
                except (BrokenPipeError, EOFError, ConnectionError, OSError):
                    self._drop_client()
                    if attempt == 2:
                        raise LLMError("provider_down", "Codex, which runs the ChatGPT plan, stopped "
                                                        "answering. Try again.") from None
                except Exception as e:
                    # The SDK's own errors can quote OpenAI's text; only the
                    # kind of failure goes further (see _task_error).
                    raise self._task_error(e) from None
            raise LLMError("provider_down", "Codex, which runs the ChatGPT plan, stopped answering.")

    # ---- sign-in -------------------------------------------------------------

    def start(self, device: bool = False) -> dict:
        self.cancel()
        client = self._new_client()
        try:
            handle = client.login_chatgpt_device_code() if device else client.login_chatgpt()
        except Exception as e:
            client.close()
            raise LLMError("provider_down", "ChatGPT sign-in couldn't start. Try again.") from e
        with self._flow_lock:
            self._flow_token += 1
            token = self._flow_token
            self._login = (client, handle)
            if device:
                self._flow = {"state": "waiting", "error": "", "device": True,
                              "verification_url": str(handle.verification_url),
                              "user_code": str(handle.user_code)}
                answer = {"verification_url": str(handle.verification_url),
                          "user_code": str(handle.user_code)}
            else:
                self._flow = {"state": "waiting", "error": "", "device": False}
                answer = {"auth_url": str(handle.auth_url)}
        threading.Thread(target=self._wait, args=(client, handle, token),
                         name="chatgpt-sign-in", daemon=True).start()
        return answer

    def _wait(self, client, handle, token: int) -> None:
        ok, error = False, ""
        try:
            done = _dump(handle.wait())
            ok = bool(done.get("success"))
            error = str(done.get("error") or "")
        except Exception as e:
            error = type(e).__name__
        finally:
            # The sign-in's own Codex process; its answer is already in hand.
            with suppress(Exception):
                client.close()
        with self._flow_lock:
            if token != self._flow_token:
                return  # cancelled, or a newer sign-in started
            self._login = None
            if ok:
                self._flow = {"state": "done", "error": ""}
            elif "timed out" in error.lower():
                self._flow = {"state": "error", "error": "The sign-in wasn't finished in time. Try again."}
            else:
                self._flow = {"state": "error",
                              "error": "ChatGPT sign-in didn't finish. Try again."}
        if ok:
            with self._lock:
                self._drop_client()  # the next request starts Codex with the new sign-in
            self._marker.touch()
            self._status = None
            self._limits = None

    def cancel(self) -> None:
        with self._flow_lock:
            login, self._login = self._login, None
            self._flow_token += 1
            self._flow = {"state": "idle", "error": ""}
        if login:
            client, handle = login
            # Stopping a sign-in that may have just finished or died: best effort.
            with suppress(Exception):
                handle.cancel()
            with suppress(Exception):
                client.close()

    @property
    def _marker(self) -> Path:
        # Set when a sign-in completes, removed on sign-out. Without it Codex
        # is never started just to ask: someone who never chose the ChatGPT
        # plan doesn't get OpenAI's runtime launched (or contacting OpenAI)
        # every time Settings opens.
        return self.home / ".signed-in"

    def status(self) -> dict:
        with self._flow_lock:
            flow = dict(self._flow)
        if not self.available() or not self._marker.exists():
            return {"signed_in": False, "plan": "", "email": "", "flow": flow}
        fresh = time.time() - self._status_at < STATUS_TTL
        if self._status is None or not fresh:
            # Never wait on a running job just to repaint the card: answer from
            # what was last known if the client is busy.
            if self._lock.acquire(blocking=self._status is None):
                try:
                    account = _dump(self._get_client().account()).get("account") or {}
                    self._status = {
                        "signed_in": str(account.get("type") or "") == "chatgpt",
                        "plan": str(account.get("plan_type") or account.get("planType") or ""),
                        "email": str(account.get("email") or ""),
                    }
                    self._status_at = time.time()
                    if not self._status["signed_in"]:
                        self._marker.unlink(missing_ok=True)  # signed out elsewhere, or expired
                except LLMError:
                    self._status = {"signed_in": False, "plan": "", "email": ""}
                except Exception:
                    self._drop_client()
                    self._status = self._status or {"signed_in": False, "plan": "", "email": ""}
                finally:
                    self._lock.release()
        return {**(self._status or {"signed_in": False, "plan": "", "email": ""}), "flow": flow}

    def sign_out(self) -> None:
        # Codex removes the stored sign-in and asks OpenAI to revoke it; the
        # local sign-in is removed even if that request fails.
        self.cancel()
        self._call(lambda client: client.logout())
        with self._lock:
            self._drop_client()
        self._marker.unlink(missing_ok=True)
        self._status = {"signed_in": False, "plan": "", "email": ""}
        self._status_at = time.time()
        self._limits = None

    # ---- plan --------------------------------------------------------------

    def _read_limits(self, client) -> dict:
        from openai_codex.generated.v2_all import GetAccountRateLimitsResponse

        raw = client._client.request("account/rateLimits/read", None,
                                     response_model=GetAccountRateLimitsResponse)
        return parse_limits(_dump(raw))

    def _limits_via(self, client) -> dict:
        try:
            limits = self._read_limits(client)
        except LLMError:
            raise
        except Exception:
            limits = {"windows": [], "reached": False, "resets_at": None, "known": False}
        self._limits, self._limits_at = limits, time.time()
        return limits

    def limits(self) -> dict:
        if self._limits is not None and time.time() - self._limits_at < LIMITS_TTL:
            return self._limits
        if not self.status().get("signed_in"):
            return {"windows": [], "reached": False, "resets_at": None, "known": False}
        if not self._lock.acquire(blocking=self._limits is None):
            return self._limits or {"windows": [], "reached": False, "resets_at": None, "known": False}
        try:
            return self._limits_via(self._get_client())
        finally:
            self._lock.release()

    def models(self) -> list[ModelInfo]:
        raw = self._call(lambda client: _dump(client.models()))
        entries = [e for e in raw.get("data") or [] if isinstance(e, dict) and not e.get("hidden")]
        entries.sort(key=lambda e: (not e.get("is_default"), str(e.get("display_name") or e.get("id"))))
        return [ModelInfo(id=str(e.get("id") or e.get("model")),
                          name=str(e.get("display_name") or e.get("id")),
                          note=str(e.get("description") or ""),
                          verified=bool(e.get("is_default")),
                          json_schema=True)
                for e in entries if e.get("id") or e.get("model")]

    # ---- jobs --------------------------------------------------------------

    def check_job(self, llm_config: dict) -> None:
        if not self.available():
            raise LLMError("not_configured", "The ChatGPT plan option isn't installed in this "
                                             "version of Clips Kitty. Choose another AI in Settings → AI.")
        if llm_config.get("unattended") and not llm_config.get("plan_automation"):
            raise LLMError("not_configured",
                           "Watched channels don't use your ChatGPT plan unless you allow it in "
                           "Settings → AI. (OpenAI recommends an API key for automation.)")
        if not self.status().get("signed_in"):
            raise LLMError("not_configured", "Sign in with ChatGPT in Settings → AI first.")
        self._guard(self.limits())

    def _guard(self, limits: dict) -> None:
        if limits.get("reached"):
            raise LLMError("rate_limited",
                           f"Your ChatGPT plan's limit is used up until {_when(limits.get('resets_at'))}. "
                           "Clips Kitty stops here rather than use ChatGPT credits.")

    def backend(self, model: str, llm_config: dict) -> LLMBackend:
        return ChatGPTPlanBackend(self, model, llm_config)

    def run_task(self, model: str, prompt: str, schema: dict | None) -> str:
        """One task, as an ephemeral read-only Codex thread. Checks the plan's
        limits first, every time."""
        from openai_codex import Sandbox

        def task(client) -> str:
            self._guard(self._limits_via(client))
            thread = client.thread_start(
                ephemeral=True,
                model=model or None,
                sandbox=Sandbox.read_only,
                cwd=str(self.home / "work"),
                developer_instructions=INSTRUCTIONS,
            )
            try:
                result = thread.run(prompt, output_schema=schema) if schema else thread.run(prompt)
            except LLMError:
                raise
            except Exception as e:
                raise self._task_error(e) from None
            self._limits_at = 0.0  # this task used some of the plan
            text = str(getattr(result, "final_response", "") or "")
            if not text.strip():
                raise LLMError("bad_response", "ChatGPT (Codex) sent back an empty answer.")
            return text

        return self._call(task)

    def _task_error(self, e: Exception) -> LLMError:
        # OpenAI's own text can carry a masked key and internals: it is never
        # passed on. Only which case it was.
        text = str(e).lower()
        if "401" in text or "unauthorized" in text:
            if (self._status or {}).get("plan") == "free":
                return LLMError("invalid_key", FREE_PLAN)
            self._status_at = 0.0
            return LLMError("invalid_key", "Your ChatGPT sign-in was refused. Sign in again in Settings → AI.")
        if "usage limit" in text or "rate limit" in text or "429" in text:
            self._limits_at = 0.0
            return LLMError("rate_limited", "Your ChatGPT plan's limit is used up for now. "
                                            "Clips Kitty stops here rather than use ChatGPT credits.")
        if "model" in text and ("not found" in text or "not supported" in text or "unavailable" in text):
            return LLMError("model_unavailable", "Your ChatGPT plan doesn't offer that model. "
                                                 "Pick another in Settings → AI.")
        return LLMError("provider_down", "ChatGPT (Codex) couldn't finish this request. Try again in a few minutes.")


class ChatGPTPlanBackend(LLMBackend):
    supports_schema = True

    def __init__(self, provider: ChatGPTPlan, model: str, llm_config: dict):
        self.provider = provider
        self.model = model
        self.llm_config = {k: llm_config.get(k) for k in ("unattended", "plan_automation")}

    def generate(self, prompt: str, *, json_mode: bool = False, schema: dict | None = None) -> str:
        self.provider.check_job({**self.llm_config})
        return self.provider.run_task(self.model, prompt, schema)

    @property
    def name(self) -> str:
        return f"chatgpt/{self.model}"

    def __repr__(self) -> str:
        return f"ChatGPTPlanBackend({self.name})"
