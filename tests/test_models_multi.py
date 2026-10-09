"""Tests for multi-provider model support (slice 44).

The HTTP layer is faked by patching ``urllib.request.urlopen`` itself,
so no real network is ever touched and the *full request URL* is
captured — which is exactly what proves requests land on the right
provider's base URL when the router fails over.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request

from draupnir_forge.budget import Budget
from draupnir_forge.config import ForgeConfig
from draupnir_forge.models import (
    ModelError,
    ModelRouter,
    available_providers,
)


SUCCESS_BODY = {
    "choices": [{"message": {"content": "hello from the other forge"}}],
    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
}

_TEST_KEY_ENV = "FORGE_TEST_MULTI_API_KEY"


class _FakeHTTPResponse:
    """Minimal stand-in for an http.client.HTTPResponse."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _UrlopenMock:
    """Scripted fake for urllib.request.urlopen.

    The script is a list of outcomes consumed in order:
      ("ok", dict)   -> 200 with the dict as the JSON body
      ("http", code) -> raise urllib.error.HTTPError with that code
      ("net",)       -> raise urllib.error.URLError

    Every call is recorded in ``requests`` as
    (full_url, body_dict, headers_dict).
    """

    def __init__(self, test_case, script):
        self.test_case = test_case
        self.script = list(script)
        self.requests = []
        self._original = urllib.request.urlopen

    def install(self):
        mock = self

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            body = json.loads(request.data.decode("utf-8"))
            headers = dict(request.headers)
            mock.requests.append((url, body, headers))
            if not mock.script:
                mock.test_case.fail("HTTP mock script exhausted")
            outcome = mock.script.pop(0)
            if outcome[0] == "ok":
                payload = json.dumps(outcome[1]).encode("utf-8")
                return _FakeHTTPResponse(payload)
            if outcome[0] == "http":
                raise urllib.error.HTTPError(
                    url, outcome[1], "Error", {}, io.BytesIO(b"error")
                )
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = fake_urlopen
        self.test_case.addCleanup(self._restore)

    def _restore(self):
        urllib.request.urlopen = self._original


def _config(**overrides):
    base = {"model.api_key_env": _TEST_KEY_ENV}
    base.update(overrides)
    return ForgeConfig.load(overrides=base)


class MultiProviderTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-models-multi-")
        self._old_key = os.environ.get(_TEST_KEY_ENV)
        os.environ[_TEST_KEY_ENV] = "test-key-for-mocked-calls"

    def tearDown(self):
        if self._old_key is None:
            os.environ.pop(_TEST_KEY_ENV, None)
        else:
            os.environ[_TEST_KEY_ENV] = self._old_key
        shutil.rmtree(self.root, ignore_errors=True)

    def _router(self, script, **overrides):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        router = ModelRouter(_config(**overrides), budget)
        mock = _UrlopenMock(self, script)
        mock.install()
        router._sleep = lambda seconds: None
        return router, budget, mock

    # -- registry ------------------------------------------------------
    def test_registry_lists_all_three_providers(self):
        self.assertEqual(
            available_providers(), ["custom", "ollama", "openai"]
        )

    def test_openai_uses_default_base(self):
        router, _, mock = self._router(
            [("ok", SUCCESS_BODY)], **{"model.provider": "openai"}
        )
        router.complete([{"role": "user", "content": "x"}], role="tester")
        url, body, headers = mock.requests[0]
        self.assertTrue(
            url.startswith("https://api.openai.com/v1/chat/completions"),
            url,
        )
        self.assertEqual(
            headers.get("Authorization"), "Bearer test-key-for-mocked-calls"
        )

    def test_ollama_uses_default_base_and_no_auth(self):
        router, _, mock = self._router(
            [("ok", SUCCESS_BODY)], **{"model.provider": "ollama"}
        )
        router.complete([{"role": "user", "content": "x"}], role="tester")
        url, _, headers = mock.requests[0]
        self.assertTrue(
            url.startswith("http://localhost:11434/v1/chat/completions"),
            url,
        )
        self.assertNotIn("Authorization", headers)

    def test_custom_provider_uses_configured_base(self):
        router, _, mock = self._router(
            [("ok", SUCCESS_BODY)],
            **{
                "model.provider": "custom",
                "model.api_base": "https://models.example.com/v1",
            },
        )
        router.complete([{"role": "user", "content": "x"}], role="tester")
        url, _, _ = mock.requests[0]
        self.assertTrue(
            url.startswith("https://models.example.com/v1/chat/completions"),
            url,
        )

    def test_custom_provider_needs_api_base(self):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        with self.assertRaises(ModelError) as ctx:
            ModelRouter(
                _config(**{"model.provider": "custom", "model.api_base": ""}),
                budget,
            )
        self.assertIn("api_base", str(ctx.exception))

    def test_unknown_provider_raises(self):
        # The config schema already rejects unknown providers at load
        # time; _resolve_provider defends the same boundary for any
        # config-like object that reaches it another way.
        from draupnir_forge.models import _resolve_provider

        class _StubConfig:
            def get(self, key, default=None):
                return {"model.api_base": "", "model.api_key_env": ""}.get(
                    key, default
                )

        with self.assertRaises(ModelError) as ctx:
            _resolve_provider(_StubConfig(), "anthropic")
        self.assertIn("Unknown model provider", str(ctx.exception))

    # -- failover chain --------------------------------------------------
    def test_provider_chain_defaults_to_primary_only(self):
        router, _, _ = self._router([], **{"model.provider": "ollama"})
        self.assertEqual(router.provider_chain(), ["ollama"])

    def test_provider_chain_appends_fallbacks_without_duplicates(self):
        router, _, _ = self._router(
            [],
            **{
                "model.provider": "openai",
                "model.fallback_providers": ["ollama", "openai", "custom"],
                "model.api_base": "https://models.example.com/v1",
            },
        )
        self.assertEqual(
            router.provider_chain(), ["openai", "ollama", "custom"]
        )

    def test_failover_to_next_provider_on_repeated_5xx(self):
        # tester has no model fallbacks: 2 attempts on openai, then ollama.
        router, budget, mock = self._router(
            [("http", 500), ("http", 500), ("ok", SUCCESS_BODY)],
            **{
                "model.provider": "openai",
                "model.fallback_providers": ["ollama"],
            },
        )
        content = router.complete(
            [{"role": "user", "content": "x"}], role="tester"
        )
        self.assertEqual(content, "hello from the other forge")
        urls = [url for url, _, _ in mock.requests]
        self.assertEqual(len(urls), 3)
        self.assertTrue(
            all(u.startswith("https://api.openai.com/v1") for u in urls[:2]),
            urls,
        )
        self.assertTrue(
            urls[2].startswith("http://localhost:11434/v1"), urls
        )
        # Usage block from the one successful call: 8 + 4.
        self.assertEqual(budget.tokens_used, 12)

    def test_failover_reuses_model_names_on_next_provider(self):
        # Model names are provider-agnostic: architect tries gpt-4o x2,
        # then gpt-4o-mini x2 on openai, then the same names on ollama.
        router, _, mock = self._router(
            [("http", 500)] * 4 + [("ok", SUCCESS_BODY)],
            **{
                "model.provider": "openai",
                "model.fallback_providers": ["ollama"],
            },
        )
        content = router.complete(
            [{"role": "user", "content": "x"}], role="architect"
        )
        self.assertEqual(content, "hello from the other forge")
        models = [body["model"] for _, body, _ in mock.requests]
        self.assertEqual(
            models,
            ["gpt-4o", "gpt-4o", "gpt-4o-mini", "gpt-4o-mini", "gpt-4o"],
        )
        urls = [url for url, _, _ in mock.requests]
        self.assertTrue(
            all(u.startswith("https://api.openai.com/v1") for u in urls[:4]),
            urls,
        )
        self.assertTrue(
            urls[4].startswith("http://localhost:11434/v1"), urls
        )

    def test_model_names_are_provider_agnostic(self):
        router, _, _ = self._router([], **{"model.provider": "openai"})
        # Slice 23 semantics intact: the same table serves every provider.
        model, fallbacks = router.model_for_role("architect")
        self.assertEqual(model, "gpt-4o")
        self.assertEqual(fallbacks, ["gpt-4o-mini"])

    def test_4xx_does_not_fail_over(self):
        router, _, mock = self._router(
            [("http", 400), ("ok", SUCCESS_BODY)],
            **{
                "model.provider": "openai",
                "model.fallback_providers": ["ollama"],
            },
        )
        with self.assertRaises(ModelError) as ctx:
            router.complete(
                [{"role": "user", "content": "x"}], role="architect"
            )
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(len(mock.requests), 1)

    def test_all_providers_exhausted_raises(self):
        router, _, mock = self._router(
            [("http", 500)] * 4,
            **{
                "model.provider": "openai",
                "model.fallback_providers": ["ollama"],
            },
        )
        with self.assertRaises(ModelError) as ctx:
            router.complete(
                [{"role": "user", "content": "x"}], role="tester"
            )
        self.assertIn("All providers exhausted", str(ctx.exception))
        self.assertIn("ollama", str(ctx.exception))
        self.assertEqual(len(mock.requests), 4)

    def test_misconfigured_fallback_provider_raises_clearly(self):
        # custom fallback with no api_base: loud error, not a silent skip.
        router, _, _ = self._router(
            [("http", 500), ("http", 500)],
            **{
                "model.provider": "ollama",
                "model.fallback_providers": ["custom"],
            },
        )
        with self.assertRaises(ModelError) as ctx:
            router.complete(
                [{"role": "user", "content": "x"}], role="tester"
            )
        self.assertIn("custom", str(ctx.exception))
        self.assertIn("api_base", str(ctx.exception))

    # -- config plumbing -------------------------------------------------
    def test_fallback_providers_list_from_overrides(self):
        cfg = _config(**{"model.fallback_providers": ["ollama", "custom"],
                         "model.api_base": "https://models.example.com/v1"})
        self.assertEqual(cfg.get("model.fallback_providers"), ["ollama", "custom"])

    def test_fallback_providers_from_env_comma_string(self):
        env_name = "DRAUPNIR_MODEL__FALLBACK_PROVIDERS"
        old = os.environ.get(env_name)
        os.environ[env_name] = "ollama, custom"
        try:
            cfg = _config(**{"model.api_base": "https://models.example.com/v1"})
            self.assertEqual(
                cfg.get("model.fallback_providers"), ["ollama", "custom"]
            )
        finally:
            if old is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = old

    def test_fallback_providers_rejects_unknown_names(self):
        with self.assertRaises(ValueError) as ctx:
            _config(**{"model.fallback_providers": ["anthropic"]})
        self.assertIn("fallback_providers", str(ctx.exception))

    def test_fallback_providers_rejects_non_list(self):
        with self.assertRaises(ValueError):
            _config(**{"model.fallback_providers": [42]})


if __name__ == "__main__":
    unittest.main()
