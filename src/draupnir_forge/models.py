"""models.py — Model router (slice 23).

Muninn carries the question to the right mind and brings the answer
back. :class:`ModelRouter` speaks to any OpenAI-compatible chat API
using only the stdlib ``urllib.request`` — no extra dependencies. The
provider (``openai`` | ``ollama`` | ``custom``) comes from the Forge
config; per-role model preferences and fallback chains come from
``data/role_models.yaml`` (data, never hardcoded).

A call tries each model in ``[primary, *fallbacks]``; each model gets up
to two attempts with backoff between them. HTTP 5xx and network errors
move on to the next model; HTTP 4xx and malformed responses raise
:class:`ModelError` immediately. Usage is recorded to the :class:`Budget`
via ``budget.charge`` — estimated at ``len(text)//4`` when the API
returns no usage block. A missing API key raises :class:`ModelError`
with a clear message rather than crashing silently.
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

_OPENAI_API_BASE = "https://api.openai.com/v1"
_OLLAMA_API_BASE = "http://localhost:11434/v1"
_CHAT_COMPLETIONS_PATH = "/chat/completions"


class ModelError(Exception):
    """A model call failed in a way no fallback could fix."""


class _RetryableError(Exception):
    """HTTP 5xx or network failure: worth retrying / falling back."""


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def _resolve_provider(config: Any) -> Tuple[str, Optional[str]]:
    """Return ``(api_base, api_key)`` for the configured provider.

    Raises:
        ModelError: On an unknown provider, a ``custom`` provider with no
            ``api_base``, or an ``openai`` provider whose key env var is
            unset. Clear messages, never a silent crash.
    """
    provider = str(config.get("model.provider", "openai"))
    api_base = str(config.get("model.api_base", "") or "").rstrip("/")
    key_env = str(config.get("model.api_key_env", "") or "")

    def _key_from_env() -> Optional[str]:
        return os.environ.get(key_env) if key_env else None

    if provider == "openai":
        key = _key_from_env()
        if not key:
            raise ModelError(
                "Model provider 'openai' needs an API key: environment "
                f"variable {key_env!r} is not set. Export it or switch "
                "model.provider to 'ollama' or 'custom'."
            )
        return api_base or _OPENAI_API_BASE, key
    if provider == "ollama":
        return api_base or _OLLAMA_API_BASE, None
    if provider == "custom":
        if not api_base:
            raise ModelError(
                "Model provider 'custom' needs model.api_base set to the "
                "OpenAI-compatible base URL (e.g. http://host:port/v1)."
            )
        return api_base, _key_from_env()
    raise ModelError(
        f"Unknown model provider {provider!r}; expected one of "
        "openai | ollama | custom."
    )


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class ModelRouter:
    """Routes role-scoped chat calls to OpenAI-compatible model APIs.

    Args:
        config: A :class:`ForgeConfig` (model.provider, model.api_base,
            model.api_key_env, model.timeout_s).
        budget: The :class:`Budget` to charge token usage against.
    """

    def __init__(self, config: Any, budget: Budget) -> None:
        self.config = config
        self.budget = budget
        self._role_models: Dict[str, Any] = self._load_role_models()
        retry = self._role_models.get("retry", {}) or {}
        self._attempts_per_model = int(retry.get("attempts_per_model", 2) or 2)
        raw_backoff = retry.get("backoff_seconds", [1.0]) or [1.0]
        self._backoffs = [float(b) for b in raw_backoff]
        self._timeout_s = float(config.get("model.timeout_s", 120))
        self._api_base, self._api_key = _resolve_provider(config)
        # Swappable in tests to avoid real sleeping.
        self._sleep = time.sleep

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

        Unknown roles fall back to the ``default`` entry; malformed
        entries degrade to the built-in default model.
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

        Picks the role's model (plus fallbacks) from
        ``data/role_models.yaml``, posts to
        ``{api_base}/chat/completions``, retries each model up to twice
        with backoff, and charges the budget for usage.

        Args:
            messages: OpenAI-style ``[{"role": ..., "content": ...}]``.
            max_tokens: Cap for the completion.
            role: Forge role name selecting the model preference.

        Returns:
            The assistant's message content.

        Raises:
            ModelError: When every model and fallback fails, when the
                provider returns HTTP 4xx, or when the response is
                malformed.
            BudgetExhausted: When charging the usage would blow a cap.
        """
        model, fallbacks = self.model_for_role(role)
        last_error: Optional[Exception] = None
        for attempt_model in [model] + fallbacks:
            for attempt in range(self._attempts_per_model):
                try:
                    content, in_tok, out_tok = self._post(
                        attempt_model, messages, max_tokens
                    )
                except _RetryableError as exc:
                    last_error = exc
                    log.warning(
                        "Model %s attempt %d failed (%s); %s",
                        attempt_model,
                        attempt + 1,
                        exc,
                        "backing off"
                        if attempt < self._attempts_per_model - 1
                        else "trying next model",
                    )
                    if attempt < self._attempts_per_model - 1:
                        index = min(attempt, len(self._backoffs) - 1)
                        self._sleep(self._backoffs[index])
                    continue
                self._charge(attempt_model, in_tok, out_tok)
                return content
        raise ModelError(
            f"All models exhausted for role {role!r} "
            f"(tried {[model] + fallbacks}): {last_error}"
        )

    # -- HTTP ------------------------------------------------------------
    def _post(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
    ) -> Tuple[str, int, int]:
        """POST one chat completion; return ``(content, in_tok, out_tok)``.

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
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self._api_base + _CHAT_COMPLETIONS_PATH,
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


__all__ = ["ModelError", "ModelRouter"]
