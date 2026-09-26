import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, Mock, mock_open, patch

import module_stubs  # noqa: F401
import dashboard
import dashboard_provider_health as health
import endpoint_adapters
import providers
from endpoint_adapters import EndpointAdapterError
from provider_contracts import GenerationResult, ProviderError


class DashboardProviderHealthTests(unittest.TestCase):
    def setUp(self):
        dashboard.app.config["TESTING"] = True
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["csrf_token"] = "test-csrf"
        self.headers = {"X-CSRF-Token": "test-csrf"}
        self.provider = {
            "name": "Selected Gemini", "url": "https://provider.invalid",
            "endpoint_type": "gemini", "model": "chosen-model",
            "key_env": "PALS_PROBE_FIXTURE_KEY", "timeout": 120,
            "include_body": "generationConfig:\n  topP: 0.7",
        }

    def request_test(self, provider=None, *, payload=None, index=0, probe=None, document=None):
        data = document if document is not None else {"providers": [provider or self.provider], "timeout": 60}
        probe = probe if probe is not None else AsyncMock(return_value=GenerationResult(text="Fixture reply"))
        with patch.object(dashboard, "Path", return_value=types.SimpleNamespace(exists=lambda: True)), \
                patch("builtins.open", mock_open(read_data=json.dumps(data))), \
                patch.dict("os.environ", {"PALS_PROBE_FIXTURE_KEY": "private-fixture-key"}), \
                patch.object(providers, "probe_provider", probe, create=True):
            response = self.client.post(f"/api/test-provider/{index}", json={} if payload is None else payload,
                                        headers=self.headers)
        return response, probe

    def test_post_calls_only_selected_saved_provider_and_returns_no_reply_or_secret(self):
        response, probe = self.request_test()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["provider_name"], "Selected Gemini")
        self.assertEqual(result["model"], "chosen-model")
        self.assertEqual(result["endpoint_type"], "gemini")
        self.assertGreaterEqual(result["duration_ms"], 0)
        probe.assert_awaited_once()
        sent = probe.await_args.args[0]
        self.assertEqual(sent["model"], "chosen-model")
        self.assertEqual(sent["key"], "private-fixture-key")
        self.assertEqual(sent["include_body"], self.provider["include_body"])
        self.assertEqual(probe.await_args.kwargs["timeout"], 30)
        self.assertEqual([check["status"] for check in result["checks"]],
                         ["passed", "passed", "not_verified"])
        serialized = json.dumps(result)
        for hidden in ("private-fixture-key", "Fixture reply", "generationConfig", "https://provider.invalid"):
            self.assertNotIn(hidden, serialized)
        self.assertIn("30", serialized)

    def test_empty_post_body_is_accepted(self):
        probe = AsyncMock(return_value=GenerationResult(text="Fixture reply"))
        with patch.object(dashboard, "Path", return_value=types.SimpleNamespace(exists=lambda: True)), \
                patch("builtins.open", mock_open(read_data=json.dumps({"providers": [self.provider]}))), \
                patch.dict("os.environ", {"PALS_PROBE_FIXTURE_KEY": "private-fixture-key"}), \
                patch.object(providers, "probe_provider", probe, create=True):
            response = self.client.post("/api/test-provider/0", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_get_is_not_a_paid_generation(self):
        response = self.client.get("/api/test-provider/0")
        self.assertEqual(response.status_code, 405)

    def test_authentication_and_csrf_are_required(self):
        client = dashboard.app.test_client()
        with patch.dict("os.environ", {"DASHBOARD_PASS": "fixture-password"}), \
                patch.object(providers, "probe_provider", AsyncMock(), create=True) as probe:
            unauthenticated = client.post("/api/test-provider/0", json={})
            self.assertEqual(unauthenticated.status_code, 302)
            missing_csrf = self.client.post("/api/test-provider/0", json={})
            self.assertEqual(missing_csrf.status_code, 403)
            probe.assert_not_awaited()

    def test_malformed_or_draft_request_never_calls_provider(self):
        for payload in ([], "invalid", {"model": "another-model"}, {"provider": self.provider}):
            with self.subTest(payload_type=type(payload).__name__):
                response, probe = self.request_test(payload=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["success"])
                probe.assert_not_awaited()
        for body in ("{", "null", "not-json"):
            response = self.client.post("/api/test-provider/0", data=body, content_type="application/json",
                                        headers=self.headers)
            self.assertEqual(response.status_code, 400)

    def test_missing_file_and_index_have_clear_errors(self):
        with patch.object(dashboard, "Path", return_value=types.SimpleNamespace(exists=lambda: False)):
            missing = self.client.post("/api/test-provider/0", json={}, headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertIn("providers.json", missing.get_json()["error"])
        response, probe = self.request_test(index=5)
        self.assertEqual(response.status_code, 404)
        probe.assert_not_awaited()

    def test_malformed_saved_config_does_not_send_a_request(self):
        documents = ([], {"providers": "invalid"}, {"providers": [None]}, {"providers": [{}]},
                     {"providers": [{**self.provider, "max_tokens": float("inf")}]})
        for document in documents:
            with self.subTest(document=document):
                response, probe = self.request_test(document=document)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["success"])
                probe.assert_not_awaited()
        with patch.object(dashboard, "Path", return_value=types.SimpleNamespace(exists=lambda: True)), \
                patch("builtins.open", mock_open(read_data="{")):
            response = self.client.post("/api/test-provider/0", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_unconfigured_key_and_unknown_endpoint_fail_before_model_call(self):
        for edit in ({"key_env": "ABSENT_PROBE_KEY"}, {"model": ""}, {"endpoint_type": "bogus"},
                     {"supports_chat": False}, {"url": "not-an-http-url"}):
            with self.subTest(edit=edit):
                response, probe = self.request_test(provider={**self.provider, **edit})
                self.assertEqual(response.status_code, 400)
                checks = response.get_json()["checks"]
                self.assertEqual(checks[0]["status"], "failed")
                self.assertEqual(checks[1]["status"], "not_verified")
                probe.assert_not_awaited()

    def test_keyless_provider_still_requires_a_model_reply(self):
        response, probe = self.request_test(provider={**self.provider, "requires_key": False, "key_env": ""})
        self.assertTrue(response.get_json()["success"])
        probe.assert_awaited_once()

    def test_test_limit_respects_shorter_saved_provider_or_global_timeout(self):
        response, probe = self.request_test(provider={**self.provider, "timeout": 5})
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(probe.await_args.kwargs["timeout"], 5)
        provider = {key: value for key, value in self.provider.items() if key != "timeout"}
        response, probe = self.request_test(document={"providers": [provider], "timeout": 7})
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(probe.await_args.kwargs["timeout"], 7)

    def test_selected_provider_index_cannot_call_another_saved_provider(self):
        document = {"providers": [
            {**self.provider, "name": "First", "model": "first-model"},
            {**self.provider, "name": "Second", "model": "second-model"},
        ]}
        response, probe = self.request_test(document=document, index=1)
        self.assertEqual(response.get_json()["model"], "second-model")
        self.assertEqual(probe.await_args.args[0]["model"], "second-model")
        probe.assert_awaited_once()

    def test_native_model_failure_is_observed_through_the_real_probe(self):
        post = AsyncMock(side_effect=endpoint_adapters.EndpointHTTPStatusError(404, "fixture-only failure"))
        with patch.object(endpoint_adapters, "post_json_request", post):
            response, _ = self.request_test(probe=providers.probe_provider)
        self.assertFalse(response.get_json()["success"])
        self.assertIn("model", response.get_json()["error"])
        post.assert_awaited_once()

    def test_native_empty_or_private_only_reply_fails_through_the_real_probe(self):
        for payload in ({"candidates": []}, {"candidates": [{"content": {"parts": [
            {"text": "fixture thought", "thought": True}
        ]}}]}, {"candidates": [{"content": {"parts": [
            {"text": "<think>fixture thought</think>"}
        ]}}]}):
            with self.subTest(payload=payload):
                post = AsyncMock(return_value=payload)
                with patch.object(endpoint_adapters, "post_json_request", post):
                    response, _ = self.request_test(probe=providers.probe_provider)
                self.assertFalse(response.get_json()["success"])
                self.assertIn("usable", response.get_json()["error"])
                self.assertNotIn("fixture thought", json.dumps(response.get_json()))
                post.assert_awaited_once()

    def test_native_success_uses_real_request_settings_and_hides_output(self):
        post = AsyncMock(return_value={"candidates": [{"content": {"parts": [
            {"text": "A usable fixture reply."}
        ]}}]})
        with patch.object(endpoint_adapters, "post_json_request", post):
            response, _ = self.request_test(probe=providers.probe_provider)
        self.assertTrue(response.get_json()["success"])
        post.assert_awaited_once()
        self.assertIn("chosen-model:generateContent", post.await_args.args[0])
        self.assertEqual(post.await_args.args[2]["generationConfig"]["topP"], 0.7)
        self.assertNotIn("A usable fixture reply", json.dumps(response.get_json()))

    def test_native_rate_limit_does_not_retry_or_fall_back(self):
        post = AsyncMock(side_effect=endpoint_adapters.EndpointHTTPStatusError(429, "fixture-only failure"))
        document = {"providers": [self.provider, {**self.provider, "model": "fallback-model"}]}
        with patch.object(endpoint_adapters, "post_json_request", post):
            response, _ = self.request_test(document=document, probe=providers.probe_provider)
        self.assertFalse(response.get_json()["success"])
        self.assertIn("rate limit", response.get_json()["error"])
        post.assert_awaited_once()

    def test_chat_catalog_success_cannot_hide_failed_generation(self):
        create = AsyncMock(return_value=types.SimpleNamespace(choices=[]))
        catalog = Mock(return_value=types.SimpleNamespace(data=[{"id": "some-model"}]))

        class Client:
            models = types.SimpleNamespace(list=catalog)
            chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        provider = {**self.provider, "endpoint_type": "openai-chat"}
        with patch.object(providers, "AsyncOpenAI", return_value=Client()) as constructor:
            response, _ = self.request_test(provider=provider, probe=providers.probe_provider)
        self.assertFalse(response.get_json()["success"])
        self.assertIn("usable", response.get_json()["error"])
        create.assert_awaited_once()
        catalog.assert_not_called()
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)

    def test_chat_probe_uses_runtime_custom_headers_reasoning_and_openrouter_settings(self):
        create = AsyncMock(return_value=types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="Fixture hello", refusal=None), finish_reason="stop")]))

        class Client:
            chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        provider = {**self.provider, "endpoint_type": "openai-chat", "url": "https://openrouter.ai/api/v1",
                    "reasoning_effort": "high", "include_body": "temperature: 0.7",
                    "include_headers": "X-Fixture: selected", "extra_body": {"top_p": 0.4, "top_k": 20},
                    "openrouter": {"provider": {"allow_fallbacks": False}}}
        with patch.object(providers, "AsyncOpenAI", return_value=Client()) as constructor:
            response, _ = self.request_test(provider=provider, probe=providers.probe_provider)
        self.assertTrue(response.get_json()["success"])
        create.assert_awaited_once()
        sent = create.await_args.kwargs
        self.assertEqual(sent["model"], "chosen-model")
        self.assertEqual(sent["extra_body"]["reasoning_effort"], "high")
        self.assertEqual(sent["extra_body"]["top_k"], 20)
        self.assertEqual(sent["extra_body"]["top_p"], 0.4)
        self.assertEqual(sent["temperature"], 0.7)
        self.assertFalse(sent["extra_body"]["provider"]["allow_fallbacks"])
        self.assertEqual(sent["extra_headers"]["X-Fixture"], "selected")
        self.assertIn("HTTP-Referer", constructor.call_args.kwargs["default_headers"])

    def test_http_errors_have_safe_concrete_explanations(self):
        for code, status, expected in (("auth", 401, "credentials"), ("rate_limit", 429, "rate limit"),
                                       ("bad_request", 400, "settings"), ("unknown", 404, "model"),
                                       ("incomplete_response", None, "output limit")):
            with self.subTest(code=code):
                error = EndpointAdapterError(ProviderError(code=code, message="private-fixture-key raw body",
                                                            diagnostics={"status": status}))
                response, probe = self.request_test(probe=AsyncMock(side_effect=error))
                result = response.get_json()
                self.assertEqual(response.status_code, 200)
                self.assertFalse(result["success"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["checks"][0]["status"], "passed")
                self.assertEqual(result["checks"][1]["status"], "failed")
                self.assertIn(expected, result["error"].lower())
                self.assertNotIn("private-fixture-key", json.dumps(result))
                self.assertNotIn("raw body", json.dumps(result))
                probe.assert_awaited_once()

    def test_empty_or_reasoning_only_results_cannot_pass(self):
        for result in (None, GenerationResult(text=""), GenerationResult(text="  ", reasoning_text="secret thought")):
            with self.subTest(result=result):
                response, _ = self.request_test(probe=AsyncMock(return_value=result))
                self.assertFalse(response.get_json()["success"])
                self.assertIn("usable", response.get_json()["error"])
                self.assertNotIn("secret thought", json.dumps(response.get_json()))

    def test_timeout_is_bounded_and_cancels_the_call(self):
        cancelled = []

        async def stalled(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        with patch.object(health, "PROVIDER_TEST_TIMEOUT", 0.01, create=True):
            response, probe = self.request_test(probe=AsyncMock(side_effect=stalled))
        self.assertFalse(response.get_json()["success"])
        self.assertIn("time", response.get_json()["error"].lower())
        self.assertEqual(cancelled, [True])
        probe.assert_awaited_once()

    def test_unknown_error_text_is_never_exposed(self):
        response, _ = self.request_test(probe=AsyncMock(side_effect=RuntimeError(
            "Authorization: Bearer private-fixture-key; C:\\private\\configuration.json"
        )))
        result = response.get_json()
        self.assertFalse(result["success"])
        for hidden in ("private-fixture-key", "Authorization", "configuration.json"):
            self.assertNotIn(hidden, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
