"""Exercise the signed HTTP ingress and real SQLite deduplication locally."""

import hashlib
import hmac
import io
import json
import os
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from project_automation_config import PROJECT_DEFAULTS
from project_automation_store import AutomationStore
from project_automation_webhook import MAX_WEBHOOK_BYTES, register_github_webhook, webhook_binding


SECRET_ENV = "PROJECT_TEST_WEBHOOK_SECRET"
SECRET = "fake-webhook-secret-for-tests"
REPOSITORY = "SillyBunnyTeam/SillyBunny"


def event_payload():
    return {"repository": {"full_name": REPOSITORY, "private": False}, "action": "opened",
            "sender": {"login": "tester", "type": "User", "id": 7},
            "issue": {"number": 17, "title": "Import stalls", "body": "Details", "state": "open",
                      "html_url": f"https://github.com/{REPOSITORY}/issues/17"},
            "installation": {"id": 123}, "unrelated_private_data": "do-not-store-this"}


class ObservedStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("An unbounded webhook body read was attempted")
        return super().read(size)


class WebhookIngressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = AutomationStore(Path(self.temporary.name) / "webhooks.sqlite3")
        self.config = {**PROJECT_DEFAULTS, "enabled": True, "repository": REPOSITORY,
                       "bot_name": "project-firefly", "guild_id": "101",
                       "feedback_channel_id": "102", "support_channel_id": "103",
                       "issues_channel_id": "104", "reviews_channel_id": "105",
                       "commits_channel_id": "106", "github_channel_id": "107",
                       "github_webhook_secret_env": SECRET_ENV}
        self.service = types.SimpleNamespace(settings=lambda: dict(self.config), store=self.store)
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        register_github_webhook(self.app, lambda: self.service)
        self.environment = patch.dict(os.environ, {SECRET_ENV: SECRET, "DASHBOARD_PASS": "test-password"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def headers(body, *, event="issues", delivery="delivery-1", secret=SECRET):
        return {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
                "Content-Type": "application/json"}

    def post(self, payload=None, *, body=None, event="issues", delivery="delivery-1", headers=None, **kwargs):
        if body is None:
            body = json.dumps(event_payload() if payload is None else payload).encode()
        with self.app.test_client() as client:
            return client.post("/webhooks/github", data=body,
                               headers=headers or self.headers(body, event=event, delivery=delivery), **kwargs)

    def test_endpoint_and_successful_delivery_need_signature_instead_of_session(self):
        self.assertIn("project_github_webhook", self.app.view_functions)
        response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json, {"status": "accepted"})
        jobs = self.store.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["key"], "github:delivery-1")
        self.assertEqual(jobs[0]["kind"], "event")
        self.assertEqual(jobs[0]["payload"]["key"], "issue:17")
        self.assertNotIn("unrelated_private_data", jobs[0]["payload"])
        self.assertNotIn(SECRET, json.dumps(jobs))

    def test_concurrent_identical_delivery_is_one_durable_job(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            responses = list(executor.map(lambda _: self.post(), range(12)))
        self.assertEqual([r.status_code for r in responses], [202] * 12)
        reopened = AutomationStore(self.store.path)
        self.assertEqual(len(reopened.list_jobs()), 1)

    def test_conflicting_delivery_cannot_replace_accepted_payload(self):
        self.assertEqual(self.post().status_code, 202)
        changed = event_payload()
        changed["issue"]["body"] = "Changed after first delivery"
        self.assertEqual(self.post(changed).status_code, 503)
        self.assertEqual(self.store.list_jobs()[0]["payload"]["body"], "Details")

    def test_config_snapshot_binds_all_channels_and_bot(self):
        self.assertEqual(self.post().status_code, 202)
        original = self.store.list_jobs()[0]["payload"]["_binding"]
        self.assertEqual(original, webhook_binding(self.config))
        self.config.update(bot_name="other-bot", guild_id="201", issues_channel_id="204")
        self.assertNotEqual(original, webhook_binding(self.config))
        self.assertEqual(self.post(delivery="delivery-2").status_code, 202)
        self.assertEqual(self.store.list_jobs()[0]["payload"]["_binding"], webhook_binding(self.config))
        self.assertNotIn("github_webhook_secret_env", original)

    def test_changed_scope_with_same_delivery_is_held(self):
        self.assertEqual(self.post().status_code, 202)
        self.config["guild_id"] = "999"
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_missing_wrong_and_tampered_signatures_are_rejected(self):
        body = json.dumps(event_payload()).encode()
        for signature in ("", "sha1=" + "a" * 40, "sha256=" + "a" * 64):
            headers = self.headers(body)
            headers["X-Hub-Signature-256"] = signature
            self.assertEqual(self.post(body=body, headers=headers).status_code, 401)
        self.assertEqual(self.post(body=body + b" ", headers=self.headers(body)).status_code, 401)
        self.assertEqual(self.store.list_jobs(), [])

    def test_signed_ping_succeeds_without_enqueuing(self):
        response = self.post({"zen": "Keep it simple"}, event="ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok"})
        self.assertEqual(self.store.list_jobs(), [])

    def test_disabled_missing_secret_and_invalid_settings_are_unavailable(self):
        self.config["enabled"] = False
        self.assertEqual(self.post().status_code, 503)
        self.config["enabled"] = True
        with patch.dict(os.environ, {SECRET_ENV: ""}):
            self.assertEqual(self.post().status_code, 503)
        self.config["github_webhook_secret_env"] = "not an env name"
        self.assertEqual(self.post().status_code, 503)
        self.config["github_webhook_secret_env"] = SECRET_ENV
        self.config["repository"] = "https://evil.test/repo"
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.store.list_jobs(), [])

    def test_signed_malformed_json_and_non_objects_are_rejected(self):
        for body in (b"{broken", b"[]", b"null", b'"text"', b'{"x": NaN}', b"\xff"):
            response = self.post(body=body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json, {"status": "invalid"})
        self.assertEqual(self.store.list_jobs(), [])

    def test_foreign_repository_unknown_event_and_malformed_event_are_ignored(self):
        foreign = event_payload()
        foreign["repository"]["full_name"] = "Other/Repo"
        for response in (self.post(foreign), self.post(event="repository_vulnerability_alert"),
                         self.post({"repository": {"full_name": REPOSITORY}, "issue": []})):
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json, {"status": "ignored"})
        self.assertEqual(self.store.list_jobs(), [])

    def test_delivery_id_is_required_bounded_and_safe(self):
        for delivery in ("", "a" * 129, "../different", "has spaces", "a:b"):
            self.assertEqual(self.post(delivery=delivery).status_code, 400)
        self.assertEqual(self.store.list_jobs(), [])

    def test_oversized_declared_body_is_rejected_before_read(self):
        stream = ObservedStream(b"x" * (MAX_WEBHOOK_BYTES + 1))
        body = b"{}"
        with self.app.test_client() as client:
            response = client.post("/webhooks/github", headers=self.headers(body),
                environ_overrides={"CONTENT_LENGTH": str(MAX_WEBHOOK_BYTES + 1), "wsgi.input": stream})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(stream.read_sizes, [])

    def test_body_without_content_length_is_bounded_and_can_be_valid(self):
        body = json.dumps(event_payload()).encode()
        stream = ObservedStream(body)
        with self.app.test_client() as client:
            response = client.post("/webhooks/github", headers=self.headers(body),
                environ_overrides={"CONTENT_LENGTH": "", "wsgi.input": stream})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(stream.read_sizes, [MAX_WEBHOOK_BYTES + 1])
        oversized = ObservedStream(b"x" * (MAX_WEBHOOK_BYTES + 10))
        with self.app.test_client() as client:
            response = client.post("/webhooks/github", headers=self.headers(b"{}", delivery="delivery-2"),
                environ_overrides={"CONTENT_LENGTH": "", "wsgi.input": oversized})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(oversized.tell(), MAX_WEBHOOK_BYTES + 1)
        self.assertEqual(oversized.read_sizes, [MAX_WEBHOOK_BYTES + 1])

    def test_outbox_failure_is_retryable_and_never_echoes_exception(self):
        with patch.object(self.store, "enqueue", side_effect=OSError("private token " + SECRET)):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"status": "unavailable"})
        self.assertNotIn(SECRET, response.get_data(as_text=True))
        self.assertEqual(self.store.list_jobs(), [])

    def test_get_is_not_an_ingress_route(self):
        with self.app.test_client() as client:
            self.assertEqual(client.get("/webhooks/github").status_code, 405)


if __name__ == "__main__":
    unittest.main()
