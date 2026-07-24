import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import provider_retry
from provider_retry import ProviderRetryPolicy, generate_with_silent_retry


class ProviderRetryPolicyTests(unittest.TestCase):
    def test_default_policy_spreads_three_retries_across_thirty_seconds(self):
        policy = ProviderRetryPolicy()

        self.assertEqual(policy.attempts, 4)
        self.assertEqual(policy.retry_delay_seconds, 10.0)

    def test_single_attempt_has_no_retry_delay(self):
        self.assertEqual(ProviderRetryPolicy(attempts=1).retry_delay_seconds, 0.0)

    def test_zero_window_retries_immediately(self):
        self.assertEqual(ProviderRetryPolicy(attempts=4, window_seconds=0).retry_delay_seconds, 0.0)

    def test_rejects_an_empty_attempt_budget(self):
        with self.assertRaises(ValueError):
            ProviderRetryPolicy(attempts=0)

    def test_rejects_a_negative_window(self):
        with self.assertRaises(ValueError):
            ProviderRetryPolicy(window_seconds=-1)

    def test_reads_the_dashboard_settings(self):
        config = {"provider_retry_attempts": 2, "provider_retry_window_seconds": 8}
        policy = ProviderRetryPolicy.from_runtime_config(
            type("Cfg", (), {"get": staticmethod(lambda key, default=None: config.get(key, default))})
        )

        self.assertEqual(policy.attempts, 2)
        self.assertEqual(policy.window_seconds, 8.0)


class SilentRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.slept = []

        async def sleep(seconds):
            self.slept.append(seconds)

        self.sleep = sleep
        warn = patch.object(provider_retry.log, "warn")
        error = patch.object(provider_retry.log, "error")
        self.warn = warn.start()
        self.error = error.start()
        self.addCleanup(warn.stop)
        self.addCleanup(error.stop)

    async def test_first_success_skips_every_retry(self):
        calls = []

        async def generate():
            calls.append(1)
            return "hello"

        result = await generate_with_silent_retry(
            generate,
            policy=ProviderRetryPolicy(),
            sleep=self.sleep,
        )

        self.assertEqual(result, "hello")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [])
        self.warn.assert_not_called()

    async def test_recovers_silently_on_a_later_attempt(self):
        responses = [None, None, "recovered"]

        async def generate():
            return responses.pop(0)

        result = await generate_with_silent_retry(
            generate,
            policy=ProviderRetryPolicy(),
            sleep=self.sleep,
        )

        self.assertEqual(result, "recovered")
        self.assertEqual(self.slept, [10.0, 10.0])
        self.assertEqual(self.warn.call_count, 2)
        self.error.assert_not_called()

    async def test_exhausted_budget_returns_none_after_every_attempt(self):
        calls = []

        async def generate():
            calls.append(1)
            return None

        result = await generate_with_silent_retry(
            generate,
            policy=ProviderRetryPolicy(),
            sleep=self.sleep,
        )

        self.assertIsNone(result)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.slept, [10.0, 10.0, 10.0])
        self.error.assert_called_once()

    async def test_non_provider_failures_are_not_retried(self):
        calls = []

        async def generate():
            calls.append(1)
            return None

        result = await generate_with_silent_retry(
            generate,
            policy=ProviderRetryPolicy(),
            sleep=self.sleep,
            should_retry=lambda: False,
        )

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [])
        self.warn.assert_not_called()
        self.error.assert_not_called()

    async def test_empty_string_counts_as_a_delivered_response(self):
        async def generate():
            return ""

        result = await generate_with_silent_retry(
            generate,
            policy=ProviderRetryPolicy(),
            sleep=self.sleep,
        )

        self.assertEqual(result, "")
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
