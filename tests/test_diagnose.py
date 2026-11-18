"""diagnose.py tests each saved provider the way the dashboard's Test model button does."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import module_stubs  # noqa: F401
import diagnose
import providers
from endpoint_adapters import EndpointAdapterError
from provider_contracts import GenerationResult, ProviderError


class DiagnoseProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = Path(self.temp_dir.name)

    def run_check(self, provider_entries, probe):
        document = {"providers": provider_entries, "timeout": 60}
        (self.base_dir / "providers.json").write_text(json.dumps(document), encoding="utf-8")
        output = io.StringIO()
        with patch.object(providers, "probe_provider", probe, create=True), contextlib.redirect_stdout(output):
            issues = diagnose.check_providers(self.base_dir)
        return issues, output.getvalue()

    def test_key_saved_in_the_dashboard_on_the_anthropic_api_passes(self):
        provider = {"name": "Saved Claude", "url": "https://provider.invalid", "endpoint_type": "anthropic",
                    "model": "fixture-model", "api_key": "dashboard-saved-fixture-key"}
        probe = AsyncMock(return_value=GenerationResult(text="Fixture reply"))

        issues, _ = self.run_check([provider], probe)

        self.assertEqual(issues, [])
        probe.assert_awaited_once()
        sent = probe.await_args.args[0]
        self.assertEqual(sent["key"], "dashboard-saved-fixture-key")
        self.assertEqual(sent["endpoint_type"], "anthropic")

    def test_rejected_credentials_are_reported_with_the_dashboard_explanation(self):
        provider = {"name": "Rejected Gemini", "url": "https://provider.invalid", "endpoint_type": "gemini",
                    "model": "fixture-model", "api_key": "rejected-fixture-key"}
        error = EndpointAdapterError(ProviderError(code="auth", message="rejected-fixture-key raw body",
                                                    diagnostics={"status": 401}))

        issues, output = self.run_check([provider], AsyncMock(side_effect=error))

        self.assertEqual(len(issues), 1)
        self.assertIn("Rejected Gemini", issues[0])
        self.assertIn("rejected the credentials", output)
        self.assertNotIn("rejected-fixture-key", output)

    def test_unset_key_variable_fails_without_a_request_and_names_the_variable(self):
        provider = {"name": "Unset Key", "url": "https://provider.invalid", "model": "fixture-model",
                    "key_env": "PALS_DIAGNOSE_UNSET_FIXTURE_KEY"}
        probe = AsyncMock()

        issues, output = self.run_check([provider], probe)

        self.assertEqual(len(issues), 1)
        self.assertIn("Unset Key", issues[0])
        self.assertIn("PALS_DIAGNOSE_UNSET_FIXTURE_KEY", output)
        probe.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
