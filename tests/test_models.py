"""Tests for the Model router (slice 23).

The HTTP layer is faked by monkeypatching ``http.client.HTTPConnection``
(the path ``urllib.request.urlopen`` itself travels), so no real network
is ever touched.
"""

import http.client
import io
import json
import shutil
import tempfile
import unittest
import urllib.error

from draupnir_forge.budget import Budget
from draupnir_forge.config import ForgeConfig
from draupnir_forge.models import ModelError, ModelRouter


SUCCESS_BODY = {
    "choices": [{"message": {"content": "hello forge"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}


class _FakeHTTPResponse:
    """Minimal stand-in for http.client.HTTPResponse."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self.code = status  # urllib's http_response reads .code
        self.reason = "OK" if status == 200 else "Error"
        self.msg = "OK" if status == 200 else "Error"

    def read(self):
        return self._body

    def info(self):
        import email.message

        return email.message.Message()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _HTTPMock:
    """Scripted fake for HTTPConnection.request/getresponse.

    The script is a list of outcomes consumed in order:
      ("ok", dict)   -> 200 with the dict as the JSON body
      ("http", code) -> raise urllib.error.HTTPError with that code
      ("http", code, headers)
                     -> same, but the fake response carries headers
                        (e.g. {"Retry-After": "2"})
      ("net",)       -> raise urllib.error.URLError
    """

    def __init__(self, test_case, script):
        self.test_case = test_case
        self.script = list(script)
        self.requests = []  # (method, url, body_bytes, headers)
        self.sleeps = []

    def install(self):
        mock = self
        test_case = self.test_case

        def fake_request(conn, method, url, body=None, headers=None, **kwargs):
            mock.requests.append((method, url, body, headers))

        def fake_getresponse(conn):
            if not mock.script:
                test_case.fail("HTTP mock script exhausted")
            outcome = mock.script.pop(0)
            if outcome[0] == "ok":
                body = json.dumps(outcome[1]).encode("utf-8")
                return _FakeHTTPResponse(body)
            if outcome[0] == "http":
                code = outcome[1]
                headers = outcome[2] if len(outcome) > 2 else {}
                raise urllib.error.HTTPError(
                    "http://localhost:11434/v1/chat/completions",
                    code,
                    "Error",
                    headers,
                    io.BytesIO(b"error"),
                )
            raise urllib.error.URLError("connection refused")

        test_case.addCleanup(
            lambda: _restore("request", mock._orig_request)
        )
        test_case.addCleanup(
            lambda: _restore("getresponse", mock._orig_getresponse)
        )
        mock._orig_request = http.client.HTTPConnection.request
        mock._orig_getresponse = http.client.HTTPConnection.getresponse
        http.client.HTTPConnection.request = fake_request
        http.client.HTTPConnection.getresponse = fake_getresponse

    def fake_sleep(self, seconds):
        self.sleeps.append(seconds)


def _restore(name, original):
    setattr(http.client.HTTPConnection, name, original)


def _ollama_config(**extra):
    overrides = {"model.provider": "ollama"}
    overrides.update(extra)
    return ForgeConfig.load(overrides=overrides)


class ModelRouterTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-models-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _router(self, script, config=None):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        router = ModelRouter(config or _ollama_config(), budget)
        mock = _HTTPMock(self, script)
        mock.install()
        router._sleep = mock.fake_sleep
        return router, budget, mock

    @staticmethod
    def _body_of(mock):
        return json.loads(mock.requests[0][2].decode("utf-8"))

    def test_success_returns_content_and_charges_budget(self):
        router, budget, mock = self._router([("ok", SUCCESS_BODY)])
        content = router.complete(
            [{"role": "user", "content": "speak, raven"}], max_tokens=50
        )
        self.assertEqual(content, "hello forge")
        body = self._body_of(mock)
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(
            body["messages"], [{"role": "user", "content": "speak, raven"}]
        )
        # usage block from the API: 10 + 5.
        self.assertEqual(budget.tokens_used, 15)

    def test_role_model_prefs_and_fallbacks(self):
        router, _, _ = self._router([("ok", SUCCESS_BODY)])
        model, fallbacks = router.model_for_role("architect")
        self.assertEqual(model, "gpt-4o")
        self.assertEqual(fallbacks, ["gpt-4o-mini"])
        # Unknown roles fall back to the default entry.
        model, _ = router.model_for_role("no_such_role")
        self.assertEqual(model, "gpt-4o-mini")

    def test_fallback_chain_on_500(self):
        router, _, mock = self._router(
            [("http", 500), ("http", 500), ("ok", SUCCESS_BODY)]
        )
        content = router.complete(
            [{"role": "user", "content": "x"}], role="architect"
        )
        self.assertEqual(content, "hello forge")
        models_tried = [
            json.loads(body.decode("utf-8"))["model"]
            for _, _, body, _ in mock.requests
        ]
        # Two attempts on gpt-4o, then the fallback gpt-4o-mini.
        self.assertEqual(models_tried, ["gpt-4o", "gpt-4o", "gpt-4o-mini"])
        # One backoff sleep between the two gpt-4o attempts.
        self.assertEqual(len(mock.sleeps), 1)

    def test_network_error_falls_back(self):
        router, _, mock = self._router(
            [("net",), ("net",), ("ok", SUCCESS_BODY)]
        )
        content = router.complete(
            [{"role": "user", "content": "x"}], role="architect"
        )
        self.assertEqual(content, "hello forge")
        models_tried = [
            json.loads(body.decode("utf-8"))["model"]
            for _, _, body, _ in mock.requests
        ]
        self.assertEqual(models_tried[-1], "gpt-4o-mini")

    def test_all_models_exhausted_raises_model_error(self):
        router, _, mock = self._router([("http", 500), ("http", 500)])
        with self.assertRaises(ModelError):
            router.complete([{"role": "user", "content": "x"}], role="tester")
        self.assertEqual(len(mock.requests), 2)

    def test_4xx_raises_immediately_without_retry_or_fallback(self):
        router, _, mock = self._router(
            [("http", 400), ("ok", SUCCESS_BODY)]
        )
        with self.assertRaises(ModelError) as ctx:
            router.complete(
                [{"role": "user", "content": "x"}], role="architect"
            )
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(len(mock.requests), 1)

    def test_missing_api_key_raises_clear_model_error(self):
        import os

        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        openai_config = ForgeConfig.load(
            overrides={
                "model.provider": "openai",
                "model.api_key_env": "DRAUPNIR_TEST_MISSING_KEY_XYZ",
            }
        )
        import os

        old = os.environ.pop("DRAUPNIR_TEST_MISSING_KEY_XYZ", None)
        try:
            with self.assertRaises(ModelError) as ctx:
                ModelRouter(openai_config, budget)
        finally:
            if old is not None:
                os.environ["DRAUPNIR_TEST_MISSING_KEY_XYZ"] = old
        self.assertIn("DRAUPNIR_TEST_MISSING_KEY_XYZ", str(ctx.exception))
        self.assertIn("not set", str(ctx.exception))

    def test_custom_provider_needs_api_base(self):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        config = ForgeConfig.load(
            overrides={"model.provider": "custom", "model.api_base": ""}
        )
        with self.assertRaises(ModelError) as ctx:
            ModelRouter(config, budget)
        self.assertIn("api_base", str(ctx.exception))

    def test_token_estimate_when_no_usage_returned(self):
        body = {"choices": [{"message": {"content": "hi there"}}]}
        router, budget, _ = self._router([("ok", body)])
        content = router.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(content, "hi there")
        # "hello" -> 5//4 = 1 in; "hi there" -> 8//4 = 2 out.
        self.assertEqual(budget.tokens_used, 3)

    def test_malformed_response_raises_model_error(self):
        router, _, _ = self._router([("ok", {"nope": True})])
        with self.assertRaises(ModelError):
            router.complete([{"role": "user", "content": "x"}])

    def test_request_goes_to_chat_completions(self):
        router, _, mock = self._router([("ok", SUCCESS_BODY)])
        router.complete([{"role": "user", "content": "x"}])
        method, url, body, headers = mock.requests[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/chat/completions"))
        # ollama provider sends no Authorization header.
        self.assertNotIn("Authorization", headers or {})


class ModelRouter429Test(unittest.TestCase):
    """Slice 6: HTTP 429 is retryable and honors Retry-After."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-models-429-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _router(self, script, config=None):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        router = ModelRouter(config or _ollama_config(), budget)
        mock = _HTTPMock(self, script)
        mock.install()
        router._sleep = mock.fake_sleep
        return router, mock

    def test_429_with_retry_after_sleeps_that_long_then_succeeds(self):
        router, mock = self._router(
            [("http", 429, {"Retry-After": "2"}), ("ok", SUCCESS_BODY)]
        )
        content = router.complete(
            [{"role": "user", "content": "x"}], role="architect"
        )
        self.assertEqual(content, "hello forge")
        self.assertEqual(len(mock.requests), 2)
        # The server's Retry-After won over the configured backoff.
        self.assertEqual(mock.sleeps, [2.0])

    def test_429_without_retry_after_falls_back_to_backoff(self):
        router, mock = self._router([("http", 429), ("ok", SUCCESS_BODY)])
        content = router.complete(
            [{"role": "user", "content": "x"}], role="architect"
        )
        self.assertEqual(content, "hello forge")
        # No hint from the server: the configured backoff (1.0) applies.
        self.assertEqual(mock.sleeps, [1.0])

    def test_429_retry_after_capped_by_config(self):
        config = _ollama_config(**{"model.max_retry_after_s": 30})
        router, mock = self._router(
            [("http", 429, {"Retry-After": "9999"}), ("ok", SUCCESS_BODY)],
            config=config,
        )
        router.complete([{"role": "user", "content": "x"}], role="architect")
        # Never sleeps longer than model.max_retry_after_s.
        self.assertEqual(mock.sleeps, [30.0])

    def test_429_retry_after_http_date(self):
        from email.utils import formatdate

        import time as _time

        retry_after = formatdate(_time.time() + 5, usegmt=True)
        router, mock = self._router(
            [("http", 429, {"Retry-After": retry_after}), ("ok", SUCCESS_BODY)]
        )
        router.complete([{"role": "user", "content": "x"}], role="architect")
        self.assertEqual(len(mock.sleeps), 1)
        # ~5 seconds, minus the instants spent between header and sleep.
        self.assertTrue(4.0 <= mock.sleeps[0] <= 5.5)

    def test_429_eventually_exhausts_like_other_retryables(self):
        router, mock = self._router([("http", 429), ("http", 429)])
        with self.assertRaises(ModelError):
            router.complete(
                [{"role": "user", "content": "x"}], role="tester"
            )
        self.assertEqual(len(mock.requests), 2)


class ModelRouterCircuitBreakerTest(unittest.TestCase):
    """Slice 7: per-provider circuit breaker."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-models-cb-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _config(self, failures=3, cooldown_s=60):
        return ForgeConfig.load(
            overrides={
                "model.provider": "ollama",
                "model.circuit_breaker.failures": failures,
                "model.circuit_breaker.cooldown_s": cooldown_s,
            }
        )

    def _router(self, script, config=None):
        budget = Budget(self.root, max_tokens=10**9, max_cost_usd=10**9)
        router = ModelRouter(config or self._config(), budget)
        mock = _HTTPMock(self, script)
        mock.install()
        router._sleep = mock.fake_sleep
        now = [1000.0]
        router._now = lambda: now[0]
        return router, mock, now

    def _failing_call(self, router):
        with self.assertRaises(ModelError):
            router.complete(
                [{"role": "user", "content": "x"}], role="tester"
            )

    def test_circuit_opens_after_threshold_consecutive_failures(self):
        # Threshold 3, attempts_per_model 2: two failing calls = 4
        # failures, enough to trip the breaker.
        router, _, _ = self._router([("http", 500)] * 4)
        self._failing_call(router)
        self.assertIn("ollama", router.provider_chain())
        self._failing_call(router)
        self.assertNotIn("ollama", router.provider_chain())

    def test_circuit_closes_again_after_cooldown(self):
        router, _, now = self._router([("http", 500)] * 4)
        self._failing_call(router)
        self._failing_call(router)
        self.assertNotIn("ollama", router.provider_chain())
        now[0] += 61  # past the 60s cooldown
        self.assertIn("ollama", router.provider_chain())

    def test_circuit_stays_open_inside_cooldown(self):
        router, _, now = self._router([("http", 500)] * 4)
        self._failing_call(router)
        self._failing_call(router)
        self.assertNotIn("ollama", router.provider_chain())
        now[0] += 59  # just inside the 60s cooldown
        self.assertNotIn("ollama", router.provider_chain())

    def test_success_resets_failure_count(self):
        # Threshold 3: 2 failures, then a success, then 2 more
        # failures = never 3 *consecutive* -> circuit stays closed.
        script = [("http", 500), ("http", 500), ("ok", SUCCESS_BODY)] + [
            ("http", 500)
        ] * 4
        router, _, _ = self._router(script)
        self._failing_call(router)  # 2 consecutive failures
        content = router.complete(
            [{"role": "user", "content": "x"}], role="tester"
        )
        self.assertEqual(content, "hello forge")  # resets the count
        self._failing_call(router)  # 2 more, but not consecutive-with-before
        self.assertIn("ollama", router.provider_chain())

    def test_circuit_open_provider_blocks_complete(self):
        router, _, _ = self._router([("http", 500)] * 4)
        self._failing_call(router)
        self._failing_call(router)
        with self.assertRaises(ModelError) as ctx:
            router.complete([{"role": "user", "content": "x"}], role="tester")
        self.assertIn("circuit-broken", str(ctx.exception))
if __name__ == "__main__":
    unittest.main()
