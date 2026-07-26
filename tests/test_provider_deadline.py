import asyncio
import unittest
from unittest.mock import patch

import module_stubs  # noqa: F401
import provider_retry
import providers as providers_module
import runtime_config


class ProviderRetryDeadlineTests(unittest.IsolatedAsyncioTestCase):
    """A provider outage must not be able to retry for an unbounded time.

    attempts x cycles x tiers x per-attempt timeouts previously had no
    wall-clock ceiling, so one message could occupy a coordinator slot for
    tens of minutes.
    """

    async def test_retries_stop_once_the_deadline_passes(self):
        calls = []
        clock = {"now": 0.0}

        async def generate():
            calls.append(1)
            clock["now"] += 10.0  # each attempt burns 10s
            return None

        policy = provider_retry.ProviderRetryPolicy(
            attempts=5, window_seconds=0, total_deadline_seconds=25
        )

        with patch.object(provider_retry.time, "monotonic", lambda: clock["now"]):
            result = await provider_retry.generate_with_silent_retry(
                generate, policy=policy
            )

        self.assertIsNone(result)
        # Deadline is 25s: attempts at 10s and 20s proceed, the third trips it.
        self.assertEqual(len(calls), 3)

    async def test_all_attempts_run_when_deadline_is_disabled(self):
        calls = []

        async def generate():
            calls.append(1)
            return None

        policy = provider_retry.ProviderRetryPolicy(
            attempts=3, window_seconds=0, total_deadline_seconds=0
        )
        result = await provider_retry.generate_with_silent_retry(generate, policy=policy)

        self.assertIsNone(result)
        self.assertEqual(len(calls), 3)

    async def test_a_success_still_returns_immediately(self):
        async def generate():
            return "ok"

        policy = provider_retry.ProviderRetryPolicy(
            attempts=4, window_seconds=0, total_deadline_seconds=1
        )
        self.assertEqual(
            await provider_retry.generate_with_silent_retry(generate, policy=policy),
            "ok",
        )

    def test_negative_deadline_is_rejected(self):
        with self.assertRaises(ValueError):
            provider_retry.ProviderRetryPolicy(total_deadline_seconds=-1)


class ProviderDeadlineHelperTests(unittest.TestCase):
    def test_disabled_when_config_is_zero(self):
        with patch.object(runtime_config, "get", return_value=0):
            self.assertIsNone(providers_module._total_deadline_from_now())
        self.assertFalse(providers_module._deadline_passed(None))

    def test_enabled_when_config_is_positive(self):
        with patch.object(runtime_config, "get", return_value=120):
            deadline = providers_module._total_deadline_from_now()
        self.assertIsNotNone(deadline)
        self.assertFalse(providers_module._deadline_passed(deadline))

    def test_elapsed_deadline_is_detected(self):
        with patch.object(providers_module.time, "monotonic", return_value=1000.0):
            self.assertTrue(providers_module._deadline_passed(999.0))
            self.assertFalse(providers_module._deadline_passed(1001.0))


class ProviderDeadlineConfigTests(unittest.TestCase):
    def test_setting_is_registered_and_bounded(self):
        self.assertIn("provider_total_deadline_seconds", runtime_config.DEFAULTS)
        field = runtime_config.CONFIG_FIELDS["provider_total_deadline_seconds"]
        self.assertEqual(field.min_value, 0)
        self.assertEqual(field.max_value, 3600)


if __name__ == "__main__":
    unittest.main()
