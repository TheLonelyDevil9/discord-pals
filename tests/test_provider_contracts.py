import unittest

import provider_contracts as contracts


class ProviderContractTests(unittest.TestCase):
    def test_endpoint_type_parsing_defaults_and_aliases(self):
        self.assertIs(contracts.parse_endpoint_type(None), contracts.EndpointType.CHAT_COMPLETIONS)
        self.assertIs(contracts.parse_endpoint_type(""), contracts.EndpointType.CHAT_COMPLETIONS)
        self.assertIs(
            contracts.parse_endpoint_type("openai-chat"),
            contracts.EndpointType.CHAT_COMPLETIONS,
        )
        self.assertIs(
            contracts.parse_endpoint_type("responses"),
            contracts.EndpointType.RESPONSES,
        )
        self.assertIs(
            contracts.parse_endpoint_type("anthropic-messages"),
            contracts.EndpointType.ANTHROPIC_MESSAGES,
        )
        self.assertIs(
            contracts.parse_endpoint_type("gemini"),
            contracts.EndpointType.GEMINI,
        )
        self.assertIs(
            contracts.parse_endpoint_type("/v1/images/generations"),
            contracts.EndpointType.IMAGE_GENERATIONS,
        )
        self.assertIs(
            contracts.parse_endpoint_type("image-generation-disabled"),
            contracts.EndpointType.IMAGE_GENERATIONS,
        )
        self.assertIs(
            contracts.parse_endpoint_type(None, default=contracts.EndpointType.MESSAGES),
            contracts.EndpointType.MESSAGES,
        )

    def test_provider_base_url_policy_by_protocol(self):
        self.assertEqual(
            contracts.provider_base_url("https://gateway.example", contracts.ProviderProtocol.OPENAI_COMPATIBLE),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.provider_base_url("https://gateway.example/v1", contracts.ProviderProtocol.OPENAI),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.provider_base_url("https://gateway.example", contracts.ProviderProtocol.GEMINI),
            "https://gateway.example/v1beta",
        )
        self.assertEqual(
            contracts.provider_base_url("https://gateway.example", contracts.ProviderProtocol.ANTHROPIC),
            "https://gateway.example",
        )
        self.assertEqual(
            contracts.provider_base_url(
                "https://gateway.example/proxy/",
                contracts.ProviderProtocol.GEMINI,
                append_base_path=False,
            ),
            "https://gateway.example/proxy",
        )

    def test_provider_base_url_policy_by_endpoint_type(self):
        self.assertEqual(
            contracts.provider_base_url_for_endpoint("https://gateway.example", "openai-chat"),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.provider_base_url_for_endpoint("https://gateway.example", "gemini"),
            "https://gateway.example/v1beta",
        )
        self.assertEqual(
            contracts.provider_base_url_for_endpoint("https://gateway.example", "anthropic-messages"),
            "https://gateway.example",
        )
        self.assertEqual(
            contracts.provider_base_url_for_endpoint(
                "https://gateway.example/custom/",
                "anthropic-messages",
                append_base_path=False,
            ),
            "https://gateway.example/custom",
        )

    def test_auth_header_policy_redacts_diagnostics(self):
        secret = "sk-test-secret-value"
        openai_auth = contracts.select_auth_headers(secret, contracts.ProviderProtocol.OPENAI_COMPATIBLE)
        gemini_auth = contracts.select_auth_headers(secret, contracts.ProviderProtocol.GEMINI)
        anthropic_auth = contracts.select_auth_headers(secret, contracts.ProviderProtocol.ANTHROPIC)

        self.assertEqual(openai_auth.headers, {"Authorization": f"Bearer {secret}"})
        self.assertEqual(gemini_auth.headers, {"x-goog-api-key": secret})
        self.assertEqual(anthropic_auth.headers, {"x-api-key": secret})

        for selection in (openai_auth, gemini_auth, anthropic_auth):
            self.assertNotIn(secret, repr(selection))
            self.assertNotIn(secret, str(selection.diagnostics))
            self.assertEqual(selection.diagnostics["header_value"], "[REDACTED]")
            self.assertTrue(selection.diagnostics["has_api_key"])

    def test_image_generation_disabled_yields_capability_unsupported(self):
        descriptor = contracts.ProviderDescriptor(
            name="Text Only",
            tier="primary",
            capabilities=contracts.CapabilityFlags(
                image_generation=False,
                image_generation_modeled=True,
            ),
        )
        request = contracts.ProviderRequest(endpoint_type=contracts.EndpointType.IMAGE_GENERATIONS)

        error = contracts.reject_image_generation_disabled(descriptor, request)

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "capability_unsupported")
        self.assertEqual(error.provider_name, "Text Only")
        self.assertIs(error.endpoint_type, contracts.EndpointType.IMAGE_GENERATIONS)

    def test_image_generation_can_be_modeled_but_disabled(self):
        capabilities = contracts.CapabilityFlags.from_config({
            "image_generation_modeled": True,
            "image_generation_disabled": True,
        })

        self.assertTrue(capabilities.image_generation_modeled)
        self.assertFalse(capabilities.image_generation)

    def test_endpoint_auth_defaults(self):
        secret = "sk-test-secret-value"
        responses_auth = contracts.select_auth_headers_for_endpoint(secret, "openai-responses")
        anthropic_auth = contracts.select_auth_headers_for_endpoint(secret, "anthropic-messages")
        gemini_auth = contracts.select_auth_headers_for_endpoint(secret, "gemini")
        no_key_auth = contracts.select_auth_headers_for_endpoint("", "openai-chat", requires_key=False)
        not_needed_auth = contracts.select_auth_headers_for_endpoint("not-needed", "openai-chat", requires_key=False)

        self.assertEqual(responses_auth.headers, {"Authorization": f"Bearer {secret}"})
        self.assertEqual(anthropic_auth.headers, {"x-api-key": secret})
        self.assertEqual(gemini_auth.headers, {"x-goog-api-key": secret})
        self.assertEqual(no_key_auth.headers, {})
        self.assertEqual(not_needed_auth.headers, {})
        self.assertNotIn(secret, repr(responses_auth))

    def test_image_generation_supported_does_not_reject(self):
        descriptor = contracts.ProviderDescriptor(
            name="Image Provider",
            capabilities=contracts.CapabilityFlags(image_generation=True),
        )
        request = contracts.ProviderRequest(endpoint_type=contracts.EndpointType.IMAGE_GENERATIONS)

        self.assertIsNone(contracts.reject_image_generation_disabled(descriptor, request))

    def test_generation_result_keeps_reasoning_separate(self):
        result = contracts.GenerationResult(
            text="Final Discord reply.",
            reasoning_text="Private chain of thought or provider reasoning.",
        )

        self.assertEqual(result.deliverable_text, "Final Discord reply.")
        self.assertEqual(result.text, "Final Discord reply.")
        self.assertEqual(result.reasoning_text, "Private chain of thought or provider reasoning.")
        self.assertNotIn(result.reasoning_text, result.deliverable_text)
        self.assertTrue(result.has_reasoning)

    def test_provider_fallback_policy_table_matches_locked_decisions(self):
        self.assertTrue(
            contracts.provider_error_policy(contracts.ProviderErrorCode.RATE_LIMIT).retry_current_provider
        )
        self.assertTrue(contracts.provider_error_policy("rate_limit").fallback_eligible)
        self.assertFalse(contracts.provider_error_policy("timeout").retry_current_provider)
        self.assertTrue(contracts.provider_error_policy("timeout").fallback_eligible)
        self.assertFalse(contracts.provider_error_policy("cancelled").fallback_eligible)
        self.assertTrue(contracts.provider_error_policy("unknown").fallback_eligible)

    def test_provider_exception_classification_is_typed_and_redacted(self):
        class FakeAPIError(Exception):
            status_code = 503

            def __str__(self):
                return "server failed with bearer sk-secret-value and prompt text"

        error = contracts.provider_error_from_exception(
            FakeAPIError(),
            provider_name="Primary",
            tier="primary",
            endpoint_type="openai-chat",
        )

        self.assertEqual(error.code, "server_5xx")
        self.assertEqual(error.provider_name, "Primary")
        self.assertEqual(error.tier, "primary")
        self.assertIs(error.endpoint_type, contracts.EndpointType.CHAT_COMPLETIONS)
        self.assertEqual(error.diagnostics["status_class"], "5xx")
        self.assertNotIn("sk-secret-value", error.message)
        self.assertNotIn("prompt text", str(error.diagnostics))

    def test_provider_exception_classifies_safety_bad_request_as_content_filter(self):
        class FakeBadRequest(Exception):
            status_code = 400
            body = "blocked by safety policy"

        error = contracts.provider_error_from_exception(FakeBadRequest())

        self.assertEqual(error.code, "content_filter")

    def test_provider_exception_classification_respects_cancel_and_rate_limit_policy(self):
        class FakeRateLimitError(Exception):
            pass

        rate_limit = contracts.provider_error_from_exception(FakeRateLimitError())
        cancelled = contracts.provider_error_from_exception(__import__("asyncio").CancelledError())

        self.assertEqual(rate_limit.code, "rate_limit")
        self.assertTrue(rate_limit.retryable)
        self.assertEqual(cancelled.code, "cancelled")
        self.assertFalse(cancelled.retryable)

    def test_unset_differs_from_explicit_none(self):
        omitted = contracts.ProviderRequest(temperature=contracts.UNSET)
        explicit_null = contracts.ProviderRequest(temperature=None)

        self.assertIs(omitted.temperature, contracts.UNSET)
        self.assertIsNone(explicit_null.temperature)
        self.assertNotEqual(omitted, explicit_null)
        self.assertNotEqual(contracts.UNSET, None)

    def test_canonical_provider_body_structural_equality(self):
        left = {
            "messages": [{"content": "hi", "role": "user"}],
            "extra_body": {"reasoning": {"effort": "high"}, "temperature": 1},
        }
        right = {
            "extra_body": {"temperature": 1, "reasoning": {"effort": "high"}},
            "messages": [{"role": "user", "content": "hi"}],
        }

        self.assertTrue(contracts.provider_bodies_equal(left, right))


if __name__ == "__main__":
    unittest.main()
