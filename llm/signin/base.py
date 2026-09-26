"""Signing in to an AI plan the user already pays for, instead of an API key.

Only where the provider officially lets a third-party app do it: through the
provider's own documented SDK or sign-in flow, unmodified, with the sign-in
held by that SDK and never seen by Clips Kitty. ChatGPT, through OpenAI's
Codex, is the one that does today. Anthropic and Google do not allow their
plans outside their own apps (docs/AI-BACKENDS.md has the sources), so they
stay API key only.

A new one is a SignInProvider in catalog.py. The API routes, the settings
card and the pipeline read the catalogue; nothing else changes.
"""

from abc import ABC, abstractmethod

from llm.base import LLMBackend
from llm.providers.base import ModelInfo


class SignInProvider(ABC):
    id: str = ""
    label: str = ""          # "ChatGPT plan"
    group: str = ""          # the direct provider it sits under in Settings → AI ("openai")
    tagline: str = ""
    privacy: str = ""        # what leaves the PC, said plainly
    usage_url: str = ""      # the provider's own page for this plan's usage
    terms_url: str = ""      # the provider's page on what the plan includes
    # Said beside the "Watched channels may use it" switch: what the provider
    # says about unattended use of the plan.
    automation_note: str = ""
    experimental: bool = False
    signin_label: str = "Sign in"   # the button: "Sign in with ChatGPT"
    limit_note: str = ""            # what happens when the plan's limit is reached
    sign_out_note: str = ""         # what signing out does, and doesn't, do
    disclaimer: str = ""            # e.g. not made by or affiliated with the provider

    @abstractmethod
    def available(self) -> bool:
        """Whether the provider's own runtime is installed in this build."""

    @abstractmethod
    def start(self, device: bool = False) -> dict:
        """Begin signing in. {"auth_url"} for the browser, or
        {"verification_url", "user_code"} for the device-code flow."""

    @abstractmethod
    def cancel(self) -> None:
        """Stop a sign-in that hasn't finished."""

    @abstractmethod
    def status(self) -> dict:
        """{"signed_in", "plan", "email", "flow": {"state", "error", ...}}.
        Answers from what it last knew rather than wait on a running job."""

    @abstractmethod
    def limits(self) -> dict:
        """The plan's usage, as the provider reports it:
        {"windows": [{"label", "used_percent", "resets_at", "minutes"}],
         "reached": bool, "resets_at": float | None, "known": bool}."""

    @abstractmethod
    def models(self) -> list[ModelInfo]:
        """The models this signed-in account can use."""

    @abstractmethod
    def sign_out(self) -> None:
        """Remove the sign-in from this PC."""

    @abstractmethod
    def backend(self, model: str, llm_config: dict) -> LLMBackend:
        """The backend a job uses."""

    @abstractmethod
    def check_job(self, llm_config: dict) -> None:
        """Raise LLMError, in words a creator can act on, if a job can't run on
        the plan: not signed in, the plan's limit used up, or an unattended
        job the user hasn't allowed. Called before the video is downloaded."""

    def public(self) -> dict:
        """What the UI is told. Never a token."""
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "auth": "signin",
            "local": False,
            "tier": 3,
            "tagline": self.tagline,
            "privacy": self.privacy,
            "usage_url": self.usage_url,
            "terms_url": self.terms_url,
            "automation_note": self.automation_note,
            "experimental": self.experimental,
            "signin_label": self.signin_label,
            "limit_note": self.limit_note,
            "sign_out_note": self.sign_out_note,
            "disclaimer": self.disclaimer,
            "available": self.available(),
        }
