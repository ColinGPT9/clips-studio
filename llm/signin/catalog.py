"""Every plan sign-in the app offers, and the one live object per data folder.

To add one: a SignInProvider subclass, and a line in _FACTORIES. It must use
the provider's own documented, unmodified SDK or sign-in flow, and the
provider's terms must allow third-party apps to use the plan. No cookies, no
tokens taken from another app, no private endpoints.
"""

import threading
from pathlib import Path

from llm.signin.base import SignInProvider


def _chatgpt(data_dir) -> SignInProvider:
    from llm.signin.chatgpt import ChatGPTPlan

    return ChatGPTPlan(data_dir)


_FACTORIES = {"chatgpt": _chatgpt}
_live: dict[tuple[str, str], SignInProvider] = {}
_lock = threading.Lock()

# Whether unattended jobs (Watched channels, stream VODs) may use a plan is the
# user's choice, kept per provider in the state database under this prefix.
AUTOMATION_FLAG = "signin_automation_"


def ids() -> tuple[str, ...]:
    return tuple(_FACTORIES)


def is_signin(provider_id: str) -> bool:
    return provider_id in _FACTORIES


def get(provider_id: str, data_dir) -> SignInProvider | None:
    factory = _FACTORIES.get(provider_id)
    if factory is None or not data_dir:
        return None
    key = (provider_id, str(Path(data_dir).resolve()))
    with _lock:
        if key not in _live:
            _live[key] = factory(data_dir)
        return _live[key]


def all_for(data_dir) -> list[SignInProvider]:
    return [p for p in (get(i, data_dir) for i in _FACTORIES) if p is not None]


def for_backend(llm_config: dict) -> SignInProvider | None:
    """The sign-in provider a job's backend runs on, or None."""
    provider = str(llm_config.get("backend") or "").partition("/")[0]
    return get(provider, llm_config.get("data_dir")) if is_signin(provider) else None
