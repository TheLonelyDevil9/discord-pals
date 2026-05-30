import json
import tempfile
import unittest
from pathlib import Path

import logger as logger_module
import dashboard_provider_health
import diagnostic_events
from delivery_pipeline import ConfirmedDeliveryPart, DeliveryOutcome, DeliveryState


class LoggingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.originals = {
            "buffer": list(logger_module._log_buffer),
            "seq": logger_module._log_sequence,
            "reset": logger_module._log_reset_marker,
            "level": logger_module.LOG_LEVEL,
            "file_enabled": logger_module.FILE_LOGGING_ENABLED,
            "log_dir": logger_module.LOG_DIR,
            "log_file": logger_module.LOG_FILE,
            "log_file_max_bytes": logger_module.LOG_FILE_MAX_BYTES,
            "log_file_backups": logger_module.LOG_FILE_BACKUPS,
            "secrets": set(logger_module._registered_secrets),
        }
        logger_module.clear_logs()
        logger_module.LOG_LEVEL = logger_module.QUIET
        logger_module._registered_secrets.clear()
        logger_module.configure_file_logging(enabled=True, log_dir=self.temp_dir.name, max_bytes=1024 * 1024)

    def tearDown(self):
        logger_module._log_buffer = self.originals["buffer"]
        logger_module._log_sequence = self.originals["seq"]
        logger_module._log_reset_marker = self.originals["reset"]
        logger_module.LOG_LEVEL = self.originals["level"]
        logger_module._registered_secrets = self.originals["secrets"]
        logger_module.configure_file_logging(
            enabled=self.originals["file_enabled"],
            log_dir=self.originals["log_dir"],
            max_bytes=self.originals["log_file_max_bytes"],
            backups=self.originals["log_file_backups"],
        )
        logger_module.LOG_FILE = self.originals["log_file"]
        self.temp_dir.cleanup()

    def test_structured_log_fields_are_buffered_and_persisted(self):
        logger_module.diagnostic(
            "Context built",
            "Firefly",
            component="context",
            event="context_built",
            req_id="abc123ef",
            channel_id=42,
            messages_for_api=9,
        )

        buffered = logger_module.get_logs_after(0)["entries"]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["component"], "context")
        self.assertEqual(buffered[0]["event"], "context_built")
        self.assertEqual(buffered[0]["req_id"], "abc123ef")
        self.assertEqual(buffered[0]["fields"]["messages_for_api"], 9)

        log_path = Path(self.temp_dir.name) / "discord-pals.log"
        persisted = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(persisted["component"], "context")
        self.assertEqual(persisted["channel_id"], 42)

    def test_registered_and_pattern_secrets_are_redacted(self):
        logger_module.register_secret("super-secret-token")
        logger_module.error(
            "Provider failed with super-secret-token and Bearer abcdefghijklmnop",
            component="provider",
            event="provider_error",
            api_key="sk-abcdefghijklmnopqrstuvwxyz",
        )

        entry = logger_module.get_logs_after(0)["entries"][0]
        self.assertNotIn("super-secret-token", entry["message"])
        self.assertNotIn("abcdefghijklmnop", entry["message"])
        self.assertEqual(entry["fields"]["api_key"], "[REDACTED]")

        log_path = Path(self.temp_dir.name) / "discord-pals.log"
        persisted = log_path.read_text(encoding="utf-8")
        self.assertNotIn("super-secret-token", persisted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", persisted)

    def test_log_filters_match_top_level_and_field_values(self):
        logger_module.info("Routing", component="routing", event="message_received", req_id="req1")
        logger_module.info("Provider", component="provider", event="provider_response", req_id="req2", tier="primary")

        provider = logger_module.get_logs_after(0, component="provider")["entries"]
        req1 = logger_module.get_logs_after(0, req_id="req1")["entries"]
        tier = logger_module.get_logs_after(0, search="primary")["entries"]

        self.assertEqual([entry["message"] for entry in provider], ["Provider"])
        self.assertEqual([entry["message"] for entry in req1], ["Routing"])
        self.assertEqual([entry["message"] for entry in tier], ["Provider"])

    def test_provider_health_error_sanitizer_uses_registered_redaction(self):
        logger_module.register_secret("sk-live-secret")

        sanitized = dashboard_provider_health.sanitize_error_message(
            RuntimeError("Failed with Bearer sk-live-secret at C:\\Users\\dev\\providers.json")
        )

        self.assertNotIn("sk-live-secret", sanitized)
        self.assertIn("[REDACTED]", sanitized)
        self.assertIn("[path]", sanitized)

    def test_delivery_complete_logs_typed_outcome_without_visible_text(self):
        outcome = DeliveryOutcome(
            state=DeliveryState.PARTIAL,
            correlation_id="req-1",
            idempotency_key="idem-1",
            confirmed_parts=(
                ConfirmedDeliveryPart(part_index=0, visible_text="secret visible text", message_id="101"),
            ),
            persistable_visible_text="secret visible text",
            retry_count=1,
            ambiguous_part_index=1,
        )

        diagnostic_events.log_delivery_complete(
            "Nahida",
            "req-1",
            channel_id=77,
            user_id=42,
            sent_records=[{"message": object(), "content": "secret visible text"}],
            delivered_response="secret visible text",
            reactions=[],
            split_target=None,
            delivery_outcome=outcome,
        )

        entry = logger_module.get_logs_after(0)["entries"][0]
        self.assertEqual(entry["fields"]["delivery_state"], "partial")
        self.assertEqual(entry["fields"]["retry_count"], 1)
        self.assertEqual(entry["fields"]["ambiguous_part_index"], 1)
        self.assertEqual(entry["fields"]["confirmed_message_ids"], ["101"])
        self.assertNotIn("secret visible text", json.dumps(entry))


if __name__ == "__main__":
    unittest.main()
