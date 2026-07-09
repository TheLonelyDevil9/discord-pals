import json
import types
import unittest
from unittest.mock import AsyncMock, mock_open, patch

import module_stubs  # noqa: F401
import config
import endpoint_adapters
import provider_contracts as contracts
import providers


class _PostRecorder:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, url, headers, body, timeout):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": dict(body),
            "timeout": timeout,
        })
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _descriptor(endpoint_type, *, base_url="https://gateway.example", capabilities=None):
    return contracts.ProviderDescriptor(
        name="Endpoint Provider",
        tier="primary",
        protocol=contracts.ProviderProtocol.OPENAI_COMPATIBLE,
        endpoint_type=endpoint_type,
        base_url=base_url,
        model="test-model",
        capabilities=capabilities or contracts.CapabilityFlags(
            chat=True,
            vision=True,
            reasoning=True,
        ),
    )


class EndpointAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_chat_uses_bearer_auth_and_keeps_reasoning_separate(self):
        post = _PostRecorder({
            "choices": [{
                "message": {
                    "content": "Visible reply.",
                    "reasoning_content": "Private reasoning.",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 12},
        })
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        result = await adapter.generate(
            descriptor=_descriptor(contracts.EndpointType.CHAT_COMPLETIONS),
            request=contracts.ProviderRequest(
                endpoint_type=contracts.EndpointType.CHAT_COMPLETIONS,
                model="chat-model",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.4,
                max_tokens=50,
                extra_body={"reasoning": {"effort": "high"}},
            ),
            api_key="sk-test",
            timeout=20,
            include_body="top_p: 0.9",
            include_headers="X-Lane: endpoint",
        )

        call = post.calls[0]
        self.assertEqual(call["url"], "https://gateway.example/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(call["headers"]["X-Lane"], "endpoint")
        self.assertTrue(
            contracts.provider_bodies_equal(
                call["body"],
                {
                    "model": "chat-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "temperature": 0.4,
                    "max_tokens": 50,
                    "top_p": 0.9,
                    "reasoning": {"effort": "high"},
                },
            )
        )
        self.assertEqual(result.deliverable_text, "Visible reply.")
        self.assertEqual(result.reasoning_text, "Private reasoning.")
        self.assertEqual(result.usage, {"total_tokens": 12})

    async def test_openai_responses_uses_responses_body_and_output_text(self):
        post = _PostRecorder({
            "output_text": "Final answer.",
            "reasoning": {"summary": [{"text": "Reasoning summary."}]},
        })
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        result = await adapter.generate(
            descriptor=_descriptor(contracts.EndpointType.RESPONSES),
            request=contracts.ProviderRequest(
                endpoint_type=contracts.EndpointType.RESPONSES,
                model="responses-model",
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "hello"},
                ],
                temperature=0.2,
                max_tokens=80,
                extra_body={"reasoning": {"effort": "medium"}},
            ),
            api_key="sk-test",
            timeout=30,
        )

        call = post.calls[0]
        self.assertEqual(call["url"], "https://gateway.example/v1/responses")
        self.assertTrue(
            contracts.provider_bodies_equal(
                call["body"],
                {
                    "model": "responses-model",
                    "input": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "hello"},
                    ],
                    "temperature": 0.2,
                    "max_output_tokens": 80,
                    "reasoning": {"effort": "medium"},
                },
            )
        )
        self.assertEqual(result.deliverable_text, "Final answer.")
        self.assertEqual(result.reasoning_text, "Reasoning summary.")

    async def test_anthropic_messages_uses_x_api_key_and_system_field(self):
        post = _PostRecorder({
            "content": [
                {"type": "thinking", "thinking": "Private scratchpad."},
                {"type": "text", "text": "Claude reply."},
            ],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        })
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        result = await adapter.generate(
            descriptor=_descriptor(contracts.EndpointType.ANTHROPIC_MESSAGES),
            request=contracts.ProviderRequest(
                endpoint_type=contracts.EndpointType.ANTHROPIC_MESSAGES,
                model="claude-model",
                messages=[
                    {"role": "system", "content": "be careful"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe this"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                        ],
                    },
                ],
                temperature=0.3,
                max_tokens=120,
            ),
            api_key="anthropic-key",
            timeout=45,
        )

        call = post.calls[0]
        self.assertEqual(call["url"], "https://gateway.example/v1/messages")
        self.assertEqual(call["headers"]["x-api-key"], "anthropic-key")
        self.assertEqual(call["body"]["system"], "be careful")
        self.assertEqual(call["body"]["messages"][0]["role"], "user")
        self.assertEqual(call["body"]["messages"][0]["content"][1]["source"]["type"], "base64")
        self.assertEqual(result.deliverable_text, "Claude reply.")
        self.assertEqual(result.reasoning_text, "Private scratchpad.")

    async def test_anthropic_messages_does_not_double_append_v1(self):
        post = _PostRecorder({"content": [{"type": "text", "text": "ok"}]})
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        await adapter.generate(
            descriptor=_descriptor(
                contracts.EndpointType.ANTHROPIC_MESSAGES,
                base_url="https://gateway.example/v1",
            ),
            request=contracts.ProviderRequest(
                endpoint_type=contracts.EndpointType.ANTHROPIC_MESSAGES,
                model="claude-model",
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=50,
            ),
            api_key="anthropic-key",
            timeout=30,
        )

        self.assertEqual(post.calls[0]["url"], "https://gateway.example/v1/messages")

    async def test_gemini_uses_google_auth_and_generate_content_shape(self):
        post = _PostRecorder({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Gemini reply."}],
                }
            }],
            "usageMetadata": {"totalTokenCount": 5},
        })
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        result = await adapter.generate(
            descriptor=_descriptor(contracts.EndpointType.GEMINI),
            request=contracts.ProviderRequest(
                endpoint_type=contracts.EndpointType.GEMINI,
                model="gemini-2.5-pro",
                messages=[
                    {"role": "system", "content": "system"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,zzz"}},
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=70,
            ),
            api_key="google-key",
            timeout=25,
        )

        call = post.calls[0]
        self.assertEqual(
            call["url"],
            "https://gateway.example/v1beta/models/gemini-2.5-pro:generateContent",
        )
        self.assertEqual(call["headers"]["x-goog-api-key"], "google-key")
        self.assertEqual(call["body"]["systemInstruction"]["parts"][0]["text"], "system")
        self.assertEqual(call["body"]["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/jpeg")
        self.assertEqual(call["body"]["generationConfig"]["maxOutputTokens"], 70)
        self.assertEqual(result.deliverable_text, "Gemini reply.")
        self.assertEqual(result.usage, {"totalTokenCount": 5})

    async def test_image_generation_modeled_but_disabled_rejects_before_http(self):
        post = _PostRecorder({})
        adapter = endpoint_adapters.EndpointProviderAdapter(post_json=post)

        with self.assertRaises(endpoint_adapters.EndpointAdapterError) as raised:
            await adapter.generate(
                descriptor=_descriptor(
                    contracts.EndpointType.IMAGE_GENERATIONS,
                    capabilities=contracts.CapabilityFlags(
                        chat=True,
                        image_generation=False,
                        image_generation_modeled=True,
                    ),
                ),
                request=contracts.ProviderRequest(endpoint_type=contracts.EndpointType.IMAGE_GENERATIONS),
                api_key="sk-test",
                timeout=20,
            )

        self.assertEqual(raised.exception.provider_error.code, "capability_unsupported")
        self.assertEqual(post.calls, [])

    async def test_http_status_errors_map_to_typed_provider_errors(self):
        adapter = endpoint_adapters.EndpointProviderAdapter(
            post_json=_PostRecorder(endpoint_adapters.EndpointHTTPStatusError(429, "rate limited"))
        )

        with self.assertRaises(endpoint_adapters.EndpointAdapterError) as raised:
            await adapter.generate(
                descriptor=_descriptor(contracts.EndpointType.CHAT_COMPLETIONS),
                request=contracts.ProviderRequest(
                    endpoint_type=contracts.EndpointType.CHAT_COMPLETIONS,
                    model="chat-model",
                    messages=[{"role": "user", "content": "hello"}],
                ),
                api_key="sk-test",
                timeout=20,
            )

        self.assertEqual(raised.exception.provider_error.code, "rate_limit")
        self.assertTrue(raised.exception.provider_error.retryable)

    async def test_http_400_safety_errors_map_to_content_filter(self):
        adapter = endpoint_adapters.EndpointProviderAdapter(
            post_json=_PostRecorder(endpoint_adapters.EndpointHTTPStatusError(400, "blocked by safety policy"))
        )

        with self.assertRaises(endpoint_adapters.EndpointAdapterError) as raised:
            await adapter.generate(
                descriptor=_descriptor(contracts.EndpointType.CHAT_COMPLETIONS),
                request=contracts.ProviderRequest(
                    endpoint_type=contracts.EndpointType.CHAT_COMPLETIONS,
                    model="chat-model",
                    messages=[{"role": "user", "content": "hello"}],
                ),
                api_key="sk-test",
                timeout=20,
            )

        self.assertEqual(raised.exception.provider_error.code, "content_filter")


class EndpointProviderManagerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_routes_explicit_endpoint_provider_without_legacy_sdk_client(self):
        manager = object.__new__(providers.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._endpoint_adapter = types.SimpleNamespace(
            generate=AsyncMock(return_value=contracts.GenerationResult(
                text="Endpoint provider says hi.",
                reasoning_text="Private reasoning.",
                provider_name="Endpoint Provider",
                tier="primary",
                model="responses-model",
            ))
        )
        provider_cfg = {
            "name": "Endpoint Provider",
            "url": "https://gateway.example",
            "key": "sk-test",
            "requires_key": True,
            "endpoint_type": "openai-responses",
            "model": "responses-model",
            "max_tokens": 256,
            "temperature": 0.5,
            "supports_vision": True,
            "supports_reasoning": True,
        }

        with patch.dict(providers.PROVIDERS, {"primary": provider_cfg}, clear=True), \
                patch.object(providers.log, "diagnostic"), \
                patch.object(providers.log, "ok"):
            result = await manager.generate(
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                use_single_user=False,
                req_id="req-1",
            )

        self.assertEqual(result, "Endpoint provider says hi.")
        call = manager._endpoint_adapter.generate.await_args.kwargs
        self.assertIs(call["descriptor"].endpoint_type, contracts.EndpointType.RESPONSES)
        self.assertEqual(
            call["request"].messages,
            [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
        )

    async def test_generate_result_preserves_endpoint_reasoning_outside_visible_text(self):
        manager = object.__new__(providers.AIProviderManager)
        manager.providers = {"primary": object()}
        manager.status = {}
        manager._vision_support_overrides = {}
        manager._build_tier_order = lambda preferred_tier="": ["primary"]
        manager._endpoint_adapter = types.SimpleNamespace(
            generate=AsyncMock(return_value=contracts.GenerationResult(
                text="<thinking>draft</thinking>Visible reply.",
                reasoning_text="Private reasoning.",
                provider_name="Endpoint Provider",
                tier="primary",
                model="responses-model",
            ))
        )
        provider_cfg = {
            "name": "Endpoint Provider",
            "url": "https://gateway.example",
            "key": "sk-test",
            "requires_key": True,
            "endpoint_type": "openai-responses",
            "model": "responses-model",
            "max_tokens": 256,
            "temperature": 0.5,
        }

        with patch.dict(providers.PROVIDERS, {"primary": provider_cfg}, clear=True), \
                patch.object(providers.log, "diagnostic"), \
                patch.object(providers.log, "ok"):
            result = await manager.generate_result(
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                use_single_user=False,
                req_id="req-1",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.deliverable_text, "Visible reply.")
        self.assertEqual(result.reasoning_text, "Private reasoning.")
        self.assertTrue(result.has_reasoning)

    async def test_requires_key_false_does_not_send_not_needed_auth(self):
        captured = {}

        async def fake_generate(**kwargs):
            captured.update(kwargs)
            return contracts.GenerationResult(text="ok")

        manager = object.__new__(providers.AIProviderManager)
        manager._endpoint_adapter = types.SimpleNamespace(generate=AsyncMock(side_effect=fake_generate))

        await manager._try_generate_endpoint(
            {
                "name": "Local Endpoint",
                "url": "http://localhost:3000",
                "key": "not-needed",
                "requires_key": False,
                "endpoint_type": "openai-chat",
                "model": "local-model",
            },
            "local-model",
            [{"role": "user", "content": "hello"}],
            0.5,
            128,
            "primary",
            timeout=20,
        )

        self.assertEqual(captured["api_key"], "")
        self.assertFalse(captured["requires_key"])

    async def test_endpoint_rate_limit_retries_before_fallback(self):
        provider_error = contracts.ProviderError(
            code="rate_limit",
            message="rate limited",
            provider_name="Endpoint Provider",
            tier="primary",
            endpoint_type=contracts.EndpointType.CHAT_COMPLETIONS,
            retryable=True,
        )
        manager = object.__new__(providers.AIProviderManager)
        manager._endpoint_adapter = types.SimpleNamespace(
            generate=AsyncMock(side_effect=[
                endpoint_adapters.EndpointAdapterError(provider_error),
                contracts.GenerationResult(text="Recovered."),
            ])
        )

        with patch.object(providers.asyncio, "sleep", new=AsyncMock()) as sleep_mock:
            result = await manager._try_generate_endpoint(
                {
                    "name": "Endpoint Provider",
                    "url": "https://gateway.example",
                    "key": "sk-test",
                    "endpoint_type": "openai-chat",
                    "model": "chat-model",
                },
                "chat-model",
                [{"role": "user", "content": "hello"}],
                0.5,
                128,
                "primary",
                timeout=20,
                req_id="req-1",
                cycle=1,
            )

        self.assertEqual(result, "Recovered.")
        self.assertEqual(manager._endpoint_adapter.generate.await_count, 2)
        sleep_mock.assert_awaited_once_with(providers.RETRY_DELAYS[0])


class EndpointAdapterRoutingTests(unittest.TestCase):
    def test_uses_endpoint_adapter_routes_by_endpoint_type(self):
        self.assertTrue(endpoint_adapters.uses_endpoint_adapter({"endpoint_type": "openai-responses"}))
        self.assertTrue(endpoint_adapters.uses_endpoint_adapter({"endpoint_type": "anthropic-messages"}))
        self.assertTrue(endpoint_adapters.uses_endpoint_adapter({"endpoint_type": "gemini"}))
        self.assertFalse(endpoint_adapters.uses_endpoint_adapter({"endpoint_type": "openai-chat"}))
        self.assertFalse(endpoint_adapters.uses_endpoint_adapter({}))
        self.assertFalse(endpoint_adapters.uses_endpoint_adapter({"endpoint_type": "bogus"}))

    def test_uses_endpoint_adapter_accepts_deprecated_newapi_alias(self):
        self.assertTrue(endpoint_adapters.uses_endpoint_adapter({"provider_protocol": "newapi"}))
        self.assertTrue(endpoint_adapters.uses_endpoint_adapter({"protocol": "new-api"}))


class EndpointProviderConfigTests(unittest.TestCase):
    def test_load_providers_preserves_endpoint_runtime_fields(self):
        # provider_protocol "newapi" is a deprecated alias; loading must keep
        # preserving it so existing configs continue to route to the adapter.
        payload = {
            "providers": [{
                "name": "Local Endpoint",
                "url": "http://localhost:3000",
                "model": "local-model",
                "provider_protocol": "newapi",
                "endpoint_type": "gemini",
                "requires_key": False,
                "append_base_path": False,
                "supports_vision": False,
                "supports_reasoning": True,
                "image_generation_modeled": True,
                "image_generation_disabled": True,
            }],
            "timeout": 60,
        }

        with patch.object(config.os.path, "exists", return_value=True), \
                patch("builtins.open", mock_open(read_data=json.dumps(payload))):
            loaded, _, _ = config.load_providers()

        provider = loaded["primary"]
        self.assertEqual(provider["provider_protocol"], "newapi")
        self.assertEqual(provider["endpoint_type"], "gemini")
        self.assertFalse(provider["requires_key"])
        self.assertFalse(provider["append_base_path"])
        self.assertFalse(provider["supports_vision"])
        self.assertTrue(provider["supports_reasoning"])
        self.assertTrue(provider["image_generation_modeled"])
        self.assertTrue(provider["image_generation_disabled"])


if __name__ == "__main__":
    unittest.main()
