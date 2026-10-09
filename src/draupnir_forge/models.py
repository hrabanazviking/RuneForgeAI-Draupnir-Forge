"""models.py — Model router (slices 23 + 44).

Muninn carries the question to the right mind and brings the answer
back. :class:`ModelRouter` speaks to any OpenAI-compatible chat API
using only the stdlib ``urllib.request`` — no extra dependencies.

Slice 23 built the single-provider router: the provider (``openai`` |
``ollama`` | ``custom``) comes from the Forge config, per-role model
preferences and fallback chains come from ``data/role_models.yaml``
(data, never hardcoded), and usage is recorded to the :class:`Budget`.

Slice 44 adds multi-provider failover. The provider registry below
knows the three providers (``openai`` default, ``ollama`` local,
``custom`` for any OpenAI-compatible base URL); every provider speaks
the same ``/chat/completions`` shape, so request building is shared and
model names travel unchanged across failover. The failover order comes
from the config's ``model.fallback_providers`` list: when a provider is
exhausted by repeated HTTP 5xx / network failures, the router tries the
next provider in the list.

A call tries each provider in ``[primary, *fallbacks]``; on each
provider it tries each model in ``[primary, *fallbacks]`` with up to
two attempts and backoff between them. HTTP 5xx and network errors
move on; HTTP 4xx and malformed responses raise :class:`ModelError`
immediately. Usage is charged to the budget — estimated at
``len(text)//4`` when the API returns no usage block. A missing API
key raises :class:`ModelError` with a clear message rather than
crashing silently.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from draupnir_forge import _paths
from draupnir_forge.budget import Budget

log = logging.getLogger("draupnir_forge.models")

ROLE_MODELS_FILE = "role_models.yaml"

_CHAT_COMPLETIONS_PATH = "/chat/completions"


class ModelError(Exception):
    """A model call failed in a way no fallback could fix."""


class _RetryableError(Exception):
    """HTTP 5xx or network failure: worth retrying / falling back."""


# ---------------------------------------------------------------------------
# Provider registry (slice 44)
# ---------------------------------------------------------------------------
# Every registered provider speaks the OpenAI-compatible chat-completions
# API; what differs is the default base URL and the key policy.
# ``model.api_base`` always overrides the default, so a custom Ollama
# port or an OpenAI proxy needs no code change. The registry is closed
# on purpose: adding a provider means adding one entry here.
#
# ``auth``: "required" (missing key is a ModelError), "optional" (send
# the key when the env var is set), "never" (no Authorization header).
_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "default_base": "https://api.openai.com/v1",
        "auth": "required",
        "needs_base": False,
    },
    "ollama": {
        "default_base": "http://localhost:11434/v1",
        "auth": "never",
        "needs_base": False,
    },
    "custom": {
        "default_base": "",
        "auth": "optional",
        "needs_base": True,
    },
}


def available_providers() -> List[str]:
    """Names of every provider in the registry, sorted."""
    return sorted(_PROVIDERS)


def _resolve_provider(
    config: Any, provider: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
    """Return ``(provider_name, api_base, api_key)`` for a provider.

    Args:
        config: A :class:`ForgeConfig` (``model.api_base``,
            ``model.api_key_env``).
        provider: Provider name; defaults to ``model.provider``.

    Raises:
        ModelError: On an unknown provider, a ``custom`` provider with no
            ``model.api_base``, or a key-demanding provider whose key env
            var is unset. Clear messages, never a silent crash.
    """
    name = str(provider if provider is not None else config.get("model.provider", "openai"))
    spec = _PROVIDERS.get(name)
    if spec is None:
        raise ModelError(
            f"Unknown model provider {name!r}; expected one of "
            f"{available_providers()}."
        )
    api_base = str(config.get("model.api_base", "") or "").rstrip("/")
    key_env = str(config.get("model.api_key_env", "") or "")
    key = os.environ.get(key_env) if key_env else None

    base = api_base or str(spec["default_base"])
    if spec["needs_base"] and not base:
        raise ModelError(
            f"Model provider {name!r} needs model.api_base set to the "
            "OpenAI-compatible base URL (e.g. http://host:port/v1)."
        )
    if spec["auth"] == "required" and not key:
        raise ModelError(
            f"Model provider {name!r} needs an API key: environment "
            f"variable {key_env!r} is not set. Export it or switch "
            "model.provider to 'ollama' or 'custom'."
        )
    # "never" providers (ollama) send no Authorization header at all.
    return name, base, key if spec["auth"] != "never" else None


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class ModelRouter:
    """Routes role-scoped chat calls to OpenAI-compatible model APIs.

    Args:
        config: A :class:`ForgeConfig` (model.provider,
            model.fallback_providers, model.api_base, model.api_key_env,
            model.timeout_s).
        budget: The :class:`Budget` to charge token usage against.

    Attributes:
        provider: The primary provider name from ``model.provider``.
    """

    def __init__(self, config: Any, budget: Budget) -> None:
        self.config = config
        self.budget = budget
        self.provider = str(config.get("model.provider", "openai"))
        raw_fallbacks = config.get("model.fallback_providers", []) or []
        self._fallback_providers = [
            name for name in (str(p).strip() for p in raw_fallbacks) if name
        ]
        self._role_models: Dict[str, Any] = self._load_role_models()
        retry = self._role_models.get("retry", {}) or {}
        self._attempts_per_model = int(retry.get("attempts_per_model", 2) or 2)
        raw_backoff = retry.get("backoff_seconds", [1.0]) or [1.0]
        self._backoffs = [float(b) for b in raw_backoff]
        self._timeout_s = float(config.get("model.timeout_s", 120))
        # Eager validation: a misconfigured primary provider fails here
        # with a clear error instead of mid-run.
        _resolve_provider(config, self.provider)
        # Swappable in tests to avoid real sleeping.
        self._sleep = time.sleep

    def provider_chain(self) -> List[str]:
        """Ordered provider names tried by :meth:`complete`.

        The primary provider first, then ``model.fallback_providers``
        with duplicates removed (the primary is never retried).
        """
        chain = [self.provider]
        for name in self._fallback_providers:
            if name not in chain:
                chain.append(name)
        return chain

    # -- data ------------------------------------------------------------
    @staticmethod
    def _load_role_models() -> Dict[str, Any]:
        """Load ``data/role_models.yaml``; degrade to safe defaults."""
        defaults: Dict[str, Any] = {
            "retry": {"attempts_per_model": 2, "backoff_seconds": [1.0]},
            "default": {"model": "gpt-4o-mini", "fallbacks": []},
            "roles": {},
        }
        try:
            data = _paths.load_data_yaml(ROLE_MODELS_FILE)
        except Exception as exc:  # data loss degrades, never crashes.
            log.warning("Using default role models: %s", exc)
            return defaults
        if not isinstance(data, dict):
            return defaults
        merged = dict(defaults)
        merged.update(data)
        return merged

    def model_for_role(self, role: str) -> Tuple[str, List[str]]:
        """Return ``(primary_model, fallbacks)`` for *role*.

        Model names are provider-agnostic on purpose: every registered
        provider speaks the OpenAI-compatible API, so the same names
        travel with the call when the router fails over to the next
        provider (slice 44). Unknown roles fall back to the ``default``
        entry; malformed entries degrade to the built-in default model.
        """
        roles = self._role_models.get("roles", {}) or {}
        entry = roles.get(role)
        if not isinstance(entry, dict):
            entry = self._role_models.get("default", {}) or {}
        model = str(entry.get("model", "") or "").strip()
        if not model:
            default = self._role_models.get("default", {}) or {}
            model = str(default.get("model", "gpt-4o-mini") or "gpt-4o-mini")
        raw_fallbacks = entry.get("fallbacks", []) or []
        fallbacks = [str(m) for m in raw_fallbacks if str(m).strip()]
        return model, fallbacks

    # -- the call --------------------------------------------------------
    def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 2000,
        role: str = "forge_worker",
    ) -> str:
        """Complete a chat conversation for *role*.

        Walks the provider chain from :meth:`provider_chain`: on each
        provider the role's model (plus fallbacks, with per-provider
        name tables when defined) is tried with retries and backoff.
        When a provider is exhausted by repeated HTTP 5xx / network
        failures, the router fails over to the next provider in
        ``model.fallback_providers``. Usage is charged to the budget.

        Args:
            messages: OpenAI-style ``[{"role": ..., "content": ...}]``.
            max_tokens: Cap for the completion.
            role: Forge role name selecting the model preference.

        Returns:
            The assistant's message content.

        Raises:
            ModelError: When every provider and model fails, when any
                provider returns HTTP 4xx, or when a response is
                malformed.
            BudgetExhausted: When charging the usage would blow a cap.
        """
        chain = self.provider_chain()
        last_error: Optional[Exception] = None
        for position, provider in enumerate(chain):
            try:
                _, api_base, api_key = _resolve_provider(
                    self.config, provider
                )
            except ModelError as exc:
                raise ModelError(
                    f"Cannot use model provider {provider!r}: {exc}"
                ) from exc
            model, fallbacks = self.model_for_role(role)
            if position > 0:
                log.warning(
                    "Model router failing over to provider %r "
                    "(model %r) after repeated failures",
                    provider,
                    model,
                )
            models = [model] + fallbacks
            for attempt_model in models:
                for attempt in range(self._attempts_per_model):
                    try:
                        content, in_tok, out_tok = self._post(
                            api_base,
                            api_key,
                            attempt_model,
                            messages,
                            max_tokens,
                        )
                    except _RetryableError as exc:
                        last_error = exc
                        if attempt < self._attempts_per_model - 1:
                            next_step = "backing off"
                        elif attempt_model != models[-1]:
                            next_step = "trying next model"
                        elif position < len(chain) - 1:
                            next_step = "trying next provider"
                        else:
                            next_step = "giving up"
                        log.warning(
                            "Provider %r model %s attempt %d failed (%s); %s",
                            provider,
                            attempt_model,
                            attempt + 1,
                            exc,
                            next_step,
                        )
                        if attempt < self._attempts_per_model - 1:
                            index = min(attempt, len(self._backoffs) - 1)
                            self._sleep(self._backoffs[index])
                        continue
                    self._charge(attempt_model, in_tok, out_tok)
                    if position > 0:
                        log.warning(
                            "Model router now serving from provider %r",
                            provider,
                        )
                    return content
        raise ModelError(
            f"All providers exhausted for role {role!r} "
            f"(providers tried: {chain}): {last_error}"
        )

    # -- HTTP ------------------------------------------------------------
    def _post(
        self,
        api_base: str,
        api_key: Optional[str],
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
    ) -> Tuple[str, int, int]:
        """POST one chat completion; return ``(content, in_tok, out_tok)``.

        All registered providers speak the OpenAI-compatible
        ``/chat/completions`` shape, so request building is shared: the
        only per-provider differences are the base URL and whether an
        ``Authorization`` header is sent.

        Raises:
            _RetryableError: HTTP 5xx or network-level failure.
            ModelError: HTTP 4xx or a malformed response body.
        """
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "draupnir-forge/0.1",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            api_base + _CHAT_COMPLETIONS_PATH,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_s
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600:
                raise _RetryableError(f"HTTP {exc.code}") from exc
            raise ModelError(
                f"Model API returned HTTP {exc.code} for model {model!r}; "
                "check the model name, provider, and API key."
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise _RetryableError(f"network error: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"Malformed chat-completions response from "
                f"model {model!r}: {exc}"
            ) from exc
        if not isinstance(content, str):
            content = "" if content is None else str(content)

        usage = data.get("usage") if isinstance(data, dict) else None
        in_tok, out_tok = self._token_counts(messages, content, usage)
        return content, in_tok, out_tok

    @staticmethod
    def _token_counts(
        messages: List[Dict[str, str]],
        content: str,
        usage: Any,
    ) -> Tuple[int, int]:
        """Derive ``(input, output)`` tokens from usage or estimates."""
        if isinstance(usage, dict):
            try:
                in_tok = int(usage.get("prompt_tokens", 0))
                out_tok = int(usage.get("completion_tokens", 0))
                if in_tok >= 0 and out_tok >= 0:
                    return in_tok, out_tok
            except (TypeError, ValueError):
                pass
        # No trustworthy usage block: estimate at len(text)//4.
        in_tok = sum(len(str(m.get("content", ""))) for m in messages) // 4
        out_tok = len(content) // 4
        return in_tok, out_tok

    def _charge(self, model: str, in_tok: int, out_tok: int) -> None:
        """Charge usage to the budget; never let bookkeeping crash a call.

        :class:`BudgetExhausted` propagates (the orchestrator must know),
        but an unpriceable model only earns a warning, not a crash.
        """
        try:
            self.budget.charge(model, in_tok, out_tok)
        except ValueError as exc:
            log.warning("Could not charge model usage: %s", exc)


__all__ = ["ModelError", "ModelRouter", "available_providers"]
