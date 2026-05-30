import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import module_stubs  # noqa: F401
import provider_contracts as contracts
import providers
from provider_gateway import ProviderGateway


class LegacyProviderRequestCharacterizationTests(unittest.TestCase):
    def test_base_chat_request_shape_is_canonical_and_uses_original_messages(self):
        messages = [{"role": "user", "content": "hello"}]

        built = providers.build_legacy_chat_request_kwargs(
            model="test-model",
            messages=messages,
            temperature=0.7,
            max_tokens=123,
        )

        self.assertIs(built.kwargs["messages"], messages)
        self.assertEqual(built.passthrough_keys, ())
        self.assertEqual(built.extra_body_keys, ())
        self.assertEqual(built.extra_header_keys, ())
        self.assertTrue(
            contracts.provider_bodies_equal(
                built.kwargs,
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "temperature": 0.7,
                    "max_tokens": 123,
                },
            )
        )

    def test_include_body_exclude_body_and_passthrough_merge_match_legacy_shape(self):
        messages = [{"role": "user", "content": "hello"}]
        include_body = """
top_p: 0.8
reasoning:
  effort: high
tools:
  - type: web_search_preview
"""
        exclude_body = """
- temperature
"""

        built = providers.build_legacy_chat_request_kwargs(
            model="test-model",
            messages=messages,
            temperature=1.0,
            max_tokens=512,
            extra_body={
                "provider": {"order": ["alpha"]},
                "reasoning": {"effort": "low"},
            },
            include_body=include_body,
            exclude_body=exclude_body,
        )

        self.assertNotIn("temperature", built.kwargs)
        self.assertNotIn("reasoning", built.kwargs)
        self.assertNotIn("tools", built.kwargs)
        self.assertEqual(built.passthrough_keys, ("reasoning", "tools"))
        self.assertTrue(
            contracts.provider_bodies_equal(
                built.kwargs,
                {
                    "model": "test-model",
                    "messages": messages,
                    "max_tokens": 512,
                    "top_p": 0.8,
                    "extra_body": {
                        "provider": {"order": ["alpha"]},
                        "reasoning": {"effort": "high"},
                        "tools": [{"type": "web_search_preview"}],
                    },
                },
            )
        )

    def test_include_headers_yaml_dict_and_list_are_stringified(self):
        messages = [{"role": "user", "content": "hello"}]

        dict_headers = providers.build_legacy_chat_request_kwargs(
            model="test-model",
            messages=messages,
            temperature=1.0,
            max_tokens=512,
            include_headers="""
X-Trace: 123
X-Mode: fast
""",
        )
        list_headers = providers.build_legacy_chat_request_kwargs(
            model="test-model",
            messages=messages,
            temperature=1.0,
            max_tokens=512,
            include_headers="""
- X-Trace: 123
- X-Mode: fast
""",
        )

        expected_headers = {"X-Trace": "123", "X-Mode": "fast"}
        self.assertEqual(dict_headers.kwargs["extra_headers"], expected_headers)
        self.assertEqual(list_headers.kwargs["extra_headers"], expected_headers)
        self.assertEqual(dict_headers.extra_header_keys, ("X-Trace", "X-Mode"))
        self.assertEqual(list_headers.extra_header_keys, ("X-Trace", "X-Mode"))

    def test_include_body_extra_body_is_sent_without_changing_legacy_extra_body_logs(self):
        messages = [{"role": "user", "content": "hello"}]

        built = providers.build_legacy_chat_request_kwargs(
            model="test-model",
            messages=messages,
            temperature=1.0,
            max_tokens=512,
            include_body="""
extra_body:
  provider:
    sort: price
""",
        )

        self.assertEqual(built.extra_body_keys, ())
        self.assertTrue(
            contracts.provider_bodies_equal(
                built.kwargs["extra_body"],
                {"provider": {"sort": "price"}},
            )
        )

    def test_reasoning_extra_body_formats_are_preserved(self):
        self.assertEqual(
            providers.build_reasoning_extra_body({
                "reasoning_format": "openai_responses",
                "reasoning_effort": "extra-high",
                "extra_body": {"reasoning": {"summary": "auto"}},
            }),
            {"reasoning": {"effort": "xhigh", "summary": "auto"}},
        )
        self.assertEqual(
            providers.build_reasoning_extra_body({
                "reasoning_format": "openai_responses",
                "reasoning_effort": "high",
                "extra_body": {"reasoning": {"effort": "low"}},
            }),
            {"reasoning": {"effort": "low"}},
        )
        self.assertEqual(
            providers.build_reasoning_extra_body({
                "reasoning_format": "openai_chat",
                "reasoning_effort": "high",
            }),
            {"reasoning_effort": "high"},
        )
        self.assertEqual(
            providers.build_reasoning_extra_body({
                "reasoning_format": "claude",
                "reasoning_effort": "medium",
                "extra_body": {"metadata": {"route": "legacy"}},
            }),
            {
                "output_config": {"effort": "medium"},
                "metadata": {"route": "legacy"},
            },
        )

    def test_client_init_preserves_legacy_base_url_auth_timeout_and_openrouter_headers(self):
        with patch.dict(
            providers.PROVIDERS,
            {
                "primary": {
                    "name": "Router",
                    "url": "https://openrouter.ai/api/v1",
                    "key": "sk-test",
                    "model": "router-model",
                    "timeout": 17,
                }
            },
            clear=True,
        ), patch.dict(providers.IMAGE_PROVIDERS, {}, clear=True), patch.object(providers, "AsyncOpenAI") as openai_cls:
            manager = providers.AIProviderManager()

        self.assertIn("primary", manager.providers)
        openai_cls.assert_called_once_with(
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
            timeout=17,
            default_headers={
                "HTTP-Referer": "https://github.com/TheLonelyDevil9/discord-pals",
                "X-OpenRouter-Title": "Router",
            },
        )

    def test_preferred_tier_order_preserves_existing_order(self):
        with patch.dict(
            providers.PROVIDERS,
            {
                "primary": {"name": "A", "url": "u", "key": "k", "model": "m"},
                "secondary": {"name": "B", "url": "u", "key": "k", "model": "m"},
                "fallback": {"name": "C", "url": "u", "key": "k", "model": "m"},
            },
            clear=True,
        ), patch.dict(providers.IMAGE_PROVIDERS, {}, clear=True):
            manager = providers.AIProviderManager()

            self.assertEqual(
                manager._build_tier_order("fallback"),
                ["fallback", "primary", "secondary"],
            )
            self.assertEqual(
                manager._build_tier_order("missing"),
                ["primary", "secondary", "fallback"],
            )


class LegacyProviderRequestRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_try_generate_sends_same_kwargs_as_pure_request_builder(self):
        captured = {}

        class FakeCompletions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="Generated text."),
                            finish_reason="stop",
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=2,
                        total_tokens=12,
                    ),
                )

        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=FakeCompletions())
        )
        manager = object.__new__(providers.AIProviderManager)
        messages = [{"role": "user", "content": "hello"}]
        kwargs = {
            "model": "test-model",
            "messages": messages,
            "temperature": 1.0,
            "max_tokens": 512,
            "extra_body": {"provider": {"order": ["alpha"]}},
            "include_body": """
top_p: 0.8
reasoning:
  effort: high
""",
            "exclude_body": """
- temperature
""",
            "include_headers": """
X-Trace: req-1
""",
        }

        result = await manager._try_generate(
            client,
            kwargs["model"],
            kwargs["messages"],
            kwargs["temperature"],
            kwargs["max_tokens"],
            "primary",
            timeout=5,
            extra_body=kwargs["extra_body"],
            include_body=kwargs["include_body"],
            exclude_body=kwargs["exclude_body"],
            include_headers=kwargs["include_headers"],
            req_id="req-1",
            cycle=1,
        )
        expected = providers.build_legacy_chat_request_kwargs(**kwargs)

        self.assertEqual(result, "Generated text.")
        self.assertTrue(contracts.provider_bodies_equal(captured, expected.kwargs))

    async def test_generate_passes_openrouter_body_merge_to_try_generate(self):
        with patch.dict(
            providers.PROVIDERS,
            {
                "primary": {
                    "name": "Router",
                    "url": "https://openrouter.ai/api/v1",
                    "key": "sk-test",
                    "model": "router-model",
                    "max_tokens": 1000,
                    "temperature": 0.6,
                    "reasoning_format": "openai_responses",
                    "reasoning_effort": "medium",
                    "openrouter": {"provider": {"sort": "price"}},
                }
            },
            clear=True,
        ), patch.dict(providers.IMAGE_PROVIDERS, {}, clear=True):
            manager = providers.AIProviderManager()
            manager._try_generate = AsyncMock(return_value="done")

            result = await manager.generate(
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                use_single_user=False,
                req_id="req-1",
            )

        self.assertEqual(result, "done")
        _, _, sent_messages, sent_temperature, sent_max_tokens, sent_tier = manager._try_generate.await_args.args
        sent_kwargs = manager._try_generate.await_args.kwargs
        self.assertEqual(sent_messages, [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}])
        self.assertEqual(sent_temperature, 0.6)
        self.assertEqual(sent_max_tokens, 1000)
        self.assertEqual(sent_tier, "primary")
        self.assertTrue(
            contracts.provider_bodies_equal(
                sent_kwargs["extra_body"],
                {
                    "reasoning": {"effort": "medium"},
                    "provider": {"sort": "price"},
                },
            )
        )


class ProviderGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_request_builder_delegates_to_legacy_builder(self):
        messages = [{"role": "user", "content": "hello"}]
        gateway = ProviderGateway(types.SimpleNamespace())

        built = gateway.build_chat_completion_kwargs(
            model="test-model",
            messages=messages,
            temperature=0.5,
            max_tokens=99,
            include_body="reasoning_effort: high",
        )

        self.assertTrue(
            contracts.provider_bodies_equal(
                built.kwargs,
                {
                    "model": "test-model",
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 99,
                    "extra_body": {"reasoning_effort": "high"},
                },
            )
        )

    async def test_generate_delegates_exact_legacy_arguments(self):
        legacy = types.SimpleNamespace(
            generate=AsyncMock(return_value="ok"),
            generate_image=AsyncMock(return_value={"bytes": b"img"}),
            get_embedding=AsyncMock(return_value=[0.1]),
            can_use_vision=Mock(return_value=True),
            get_status=Mock(return_value="status"),
            reload=Mock(),
        )
        gateway = ProviderGateway(legacy)
        messages = [{"role": "user", "content": "hello"}]

        result = await gateway.generate(
            messages=messages,
            system_prompt="system",
            temperature=0.2,
            max_tokens=50,
            use_single_user=False,
            preferred_tier="secondary",
            req_id="req-1",
        )

        self.assertEqual(result, "ok")
        legacy.generate.assert_awaited_once_with(
            messages=messages,
            system_prompt="system",
            temperature=0.2,
            max_tokens=50,
            use_single_user=False,
            preferred_tier="secondary",
            req_id="req-1",
        )

    async def test_gateway_exposes_existing_auxiliary_provider_methods(self):
        legacy = types.SimpleNamespace(
            generate=AsyncMock(return_value="ok"),
            generate_image=AsyncMock(return_value={"bytes": b"img"}),
            get_embedding=AsyncMock(return_value=[0.1]),
            can_use_vision=Mock(return_value=True),
            get_status=Mock(return_value="status"),
            reload=Mock(),
        )
        gateway = ProviderGateway(legacy)

        self.assertEqual(await gateway.generate_text([{"role": "user", "content": "hi"}], "sys"), "ok")
        self.assertEqual(await gateway.generate_image("draw this", preferred_tier="primary", req_id="r"), {"bytes": b"img"})
        self.assertEqual(await gateway.get_embedding("memory"), [0.1])
        self.assertTrue(gateway.can_use_vision("primary"))
        self.assertEqual(gateway.get_status(), "status")
        gateway.reload()

        legacy.generate_image.assert_awaited_once_with("draw this", preferred_tier="primary", req_id="r")
        legacy.get_embedding.assert_awaited_once_with("memory")
        legacy.can_use_vision.assert_called_once_with("primary")
        legacy.get_status.assert_called_once_with()
        legacy.reload.assert_called_once_with()

    async def test_gateway_proxies_legacy_provider_state_for_runtime_checks(self):
        legacy = types.SimpleNamespace(
            providers={"primary": object()},
            image_providers={"image": object()},
            status={"primary": "ok"},
            image_status={"image": "ok"},
            generate=AsyncMock(return_value="ok"),
        )
        gateway = ProviderGateway(legacy)

        self.assertTrue(gateway.has_text_providers())
        self.assertIs(gateway.providers, legacy.providers)
        self.assertIs(gateway.image_providers, legacy.image_providers)
        self.assertIs(gateway.status, legacy.status)
        self.assertIs(gateway.image_status, legacy.image_status)

    async def test_gateway_generate_result_uses_typed_result_when_available(self):
        expected = contracts.GenerationResult(
            text="visible",
            reasoning_text="private",
            provider_name="NewAPI",
            tier="primary",
            model="responses-model",
        )
        legacy = types.SimpleNamespace(generate_result=AsyncMock(return_value=expected))
        gateway = ProviderGateway(legacy)

        result = await gateway.generate_result(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="system",
            preferred_tier="primary",
            req_id="req-1",
        )

        self.assertIs(result, expected)
        legacy.generate_result.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
