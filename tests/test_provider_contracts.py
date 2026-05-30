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

    def test_newapi_base_url_policy_by_protocol(self):
        self.assertEqual(
            contracts.newapi_base_url("https://gateway.example", contracts.ProviderProtocol.OPENAI_COMPATIBLE),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.newapi_base_url("https://gateway.example/v1", contracts.ProviderProtocol.OPENAI),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.newapi_base_url("https://gateway.example", contracts.ProviderProtocol.GEMINI),
            "https://gateway.example/v1beta",
        )
        self.assertEqual(
            contracts.newapi_base_url("https://gateway.example", contracts.ProviderProtocol.ANTHROPIC),
            "https://gateway.example",
        )
        self.assertEqual(
            contracts.newapi_base_url(
                "https://gateway.example/proxy/",
                contracts.ProviderProtocol.GEMINI,
                append_base_path=False,
            ),
            "https://gateway.example/proxy",
        )

    def test_newapi_base_url_policy_by_endpoint_type(self):
        self.assertEqual(
            contracts.newapi_base_url_for_endpoint("https://gateway.example", "openai-chat"),
            "https://gateway.example/v1",
        )
        self.assertEqual(
            contracts.newapi_base_url_for_endpoint("https://gateway.example", "gemini"),
            "https://gateway.example/v1beta",
        )
        self.assertEqual(
            contracts.newapi_base_url_for_endpoint("https://gateway.example", "anthropic-messages"),
            "https://gateway.example",
        )
        self.assertEqual(
            contracts.newapi_base_url_for_endpoint(
                "https://gateway.example/custom/",
                "anthropic-messages",
                append_base_path=False,
            ),
            "https://gateway.example/custom",
        )

    def test_auth_header_policy_redacts_diagnostics(self):
        secret = "sk-test-secret-value"
        openai_auth = contracts.select_newapi_auth_headers(secret, contracts.ProviderProtocol.OPENAI_COMPATIBLE)
        gemini_auth = contracts.select_newapi_auth_headers(secret, contracts.ProviderProtocol.GEMINI)
        anthropic_auth = contracts.select_newapi_auth_headers(secret, contracts.ProviderProtocol.ANTHROPIC)

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

    def test_newapi_endpoint_auth_defaults(self):
        secret = "sk-test-secret-value"
        responses_auth = contracts.select_newapi_auth_headers_for_endpoint(secret, "openai-responses")
        anthropic_auth = contracts.select_newapi_auth_headers_for_endpoint(secret, "anthropic-messages")
        gemini_auth = contracts.select_newapi_auth_headers_for_endpoint(secret, "gemini")
        no_key_auth = contracts.select_newapi_auth_headers_for_endpoint("", "openai-chat", requires_key=False)
        not_needed_auth = contracts.select_newapi_auth_headers_for_endpoint("not-needed", "openai-chat", requires_key=False)

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
