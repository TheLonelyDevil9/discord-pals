import json
from pathlib import Path

import pytest
from flask import Flask

import project_automation_dashboard as dashboard
from project_automation_config import normalize_project_config
from security import generate_csrf_token


class FakeService:
    def __init__(self):
        self.case = {
            "id": "case-one", "state": "awaiting_submission", "reporter_id": "100000000000000019",
            "guild_id": "100000000000000001", "channel_id": "100000000000000002",
            "repository": "SillyBunnyTeam/SillyBunny",
            "draft": {"title": "A useful report", "body": "The exact issue preview."},
        }
        self.limit = None
        self.jobs = [{"id": 1, "kind": "publish", "status": "recovery", "attempts": 1,
                      "error": "Write result unknown.", "payload": {"provider_context": "private-internal-context"}}]

    def status(self):
        return {"worker_running": False, "enabled": False}

    def list_cases(self, limit=100):
        self.limit = limit
        return [self.case]

    def get_case(self, case_id):
        return self.case if case_id == self.case["id"] else None

    def list_jobs(self, limit=100):
        return self.jobs


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASS", raising=False)
    for key in ("PROJECT_GITHUB_PRIVATE_KEY", "PROJECT_GITHUB_PRIVATE_KEY_FILE", "PROJECT_GITHUB_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    saved = {"config": normalize_project_config({})}
    monkeypatch.setattr(dashboard.runtime_config, "get", lambda key: saved["config"])
    monkeypatch.setattr(dashboard.runtime_config, "set", lambda key, value: saved.update(config=value))
    root = Path(__file__).resolve().parents[1]
    app = Flask("project-dashboard-test", template_folder=str(root / "templates"))
    app.config.update(TESTING=True, SECRET_KEY="test-only-session-secret")
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    service = FakeService()
    dashboard.register_project_routes(app, lambda: service,
                                     get_bot_names=lambda: ["Project Helper"],
                                     get_character_names=lambda: ["firefly"])
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "test-csrf"
    return client, saved, service


def csrf():
    return {"X-CSRF-Token": "test-csrf"}


def valid_config():
    return {
        "enabled": True, "bot_name": "Project Helper", "guild_id": "100000000000000001",
        "feedback_channel_id": "100000000000000002", "issues_channel_id": "100000000000000003",
        "reviews_channel_id": "100000000000000004", "commits_channel_id": "100000000000000005",
        "github_channel_id": "100000000000000006", "maintainer_role_ids": ["100000000000000007"],
        "github_app_id": "123", "github_installation_id": "456", "character_name": "firefly",
    }


@pytest.mark.parametrize("path", ["/project-automation", "/api/project-automation", "/api/project-automation/cases",
                                 "/api/project-automation/cases/case-one", "/api/project-automation/jobs"])
def test_routes_require_auth_when_password_is_set(setup, monkeypatch, path):
    client, _, _ = setup
    monkeypatch.setenv("DASHBOARD_PASS", "test-password")
    assert client.get(path).status_code == 401
    with client.session_transaction() as session:
        session["logged_in"] = True
    assert client.get(path).status_code == 200


def test_save_requires_csrf_and_never_mutates_on_failure(setup):
    client, saved, _ = setup
    before = dict(saved["config"])
    assert client.post("/api/project-automation", json={"max_questions": 5}).status_code == 403
    assert saved["config"] == before


@pytest.mark.parametrize("payload", [[], None, "text", {"max_questions": "5"}, {"enabled": "yes"}, {"github_token": "secret-value"}])
def test_malformed_json_is_rejected_without_echoing_secrets(setup, payload):
    client, saved, _ = setup
    response = client.post("/api/project-automation", data=json.dumps(payload), content_type="application/json", headers=csrf())
    assert response.status_code == 400
    assert "secret-value" not in response.get_data(as_text=True)
    assert saved["config"] == normalize_project_config({})


def test_partial_save_preserves_existing_values_and_large_ids(setup):
    client, saved, _ = setup
    response = client.post("/api/project-automation", json={"guild_id": "100000000000000019", "max_questions": 4}, headers=csrf())
    assert response.status_code == 200
    assert saved["config"]["guild_id"] == "100000000000000019"
    assert saved["config"]["repository"] == "SillyBunnyTeam/SillyBunny"
    assert saved["config"]["max_questions"] == 4


def test_enable_rejects_missing_configuration_and_credentials(setup):
    client, saved, _ = setup
    response = client.post("/api/project-automation", json={"enabled": True}, headers=csrf())
    assert response.status_code == 400
    assert not saved["config"]["enabled"]
    response = client.post("/api/project-automation", json=valid_config(), headers=csrf())
    assert response.status_code == 400
    assert any("private key" in error for error in response.get_json()["errors"])


def test_enable_known_bot_with_ready_connection_then_pause(setup, monkeypatch):
    client, saved, _ = setup
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "test-key-not-a-real-credential")
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "test-webhook-not-a-real-credential")
    response = client.post("/api/project-automation", json=valid_config(), headers=csrf())
    assert response.status_code == 200
    assert saved["config"]["enabled"]
    assert response.get_json()["ready"]
    response = client.post("/api/project-automation", json={"enabled": False}, headers=csrf())
    assert response.status_code == 200
    assert not saved["config"]["enabled"]
    assert saved["config"]["bot_name"] == "Project Helper"


def test_enable_rejects_unknown_bot_or_personality(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "test-key")
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "test-webhook")
    config = valid_config()
    config.update(bot_name="Missing bot", character_name="missing-character")
    response = client.post("/api/project-automation", json=config, headers=csrf())
    assert response.status_code == 400
    assert len(response.get_json()["errors"]) == 2


def test_api_and_page_never_return_environment_secret_values(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "private-key-must-not-leak")
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "webhook-must-not-leak")
    for path in ("/project-automation", "/api/project-automation"):
        text = client.get(path).get_data(as_text=True)
        assert "private-key-must-not-leak" not in text
        assert "webhook-must-not-leak" not in text
    assert client.get("/api/project-automation").get_json()["credentials"] == {"private_key": True, "webhook_secret": True}


def test_page_escapes_untrusted_configuration_in_markup_and_script(setup):
    client, saved, _ = setup
    saved["config"]["bot_name"] = '"><img src=x onerror=alert(1)></script><script>alert(2)</script>'
    response = client.get("/project-automation")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert saved["config"]["bot_name"] not in text
    assert '<img src=x onerror=alert(1)>' not in text
    page_script = text[text.index("const initial ="):].split("</script>", 1)[0]
    assert 'innerHTML' not in page_script


def test_cases_keep_exact_preview_and_limit_is_bounded(setup):
    client, _, service = setup
    response = client.get("/api/project-automation/cases?limit=50000")
    assert service.limit == 100
    assert response.get_json()["cases"][0]["reporter_id"] == "100000000000000019"
    response = client.get("/api/project-automation/cases/case-one")
    assert response.get_json()["case"]["draft"]["body"] == "The exact issue preview."
    assert client.get("/api/project-automation/cases/missing").status_code == 404


def test_jobs_expose_recovery_metadata_without_internal_payload(setup):
    client, _, _ = setup
    response = client.get("/api/project-automation/jobs")
    job = response.get_json()["jobs"][0]
    assert job["status"] == "recovery"
    assert "payload" not in job
    assert "private-internal-context" not in response.get_data(as_text=True)
    assert client.post("/api/project-automation/jobs/1/retry", json={}, headers=csrf()).status_code == 404


def test_publish_job_exposes_its_pinned_destination_and_public_marker_only(setup):
    client, _, service = setup
    service.case["linked_issue_number"] = 77
    service.jobs[0]["payload"].update({
        "case_id": "case-one", "marker": "case-one-4",
        "draft": {"kind": "comment", "issue_number": 42, "title": "private-draft-title", "body": "private-draft-body"},
    })
    response = client.get("/api/project-automation/jobs")
    job = response.get_json()["jobs"][0]
    assert job["target"] == {
        "case_id": "case-one", "repository": "SillyBunnyTeam/SillyBunny", "issue_number": 42,
        "discord_url": "https://discord.com/channels/100000000000000001/100000000000000002",
        "github_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42",
        "publication_marker": "<!-- discord-pals-project:case-one-4 -->",
    }
    for private in ("private-internal-context", "private-draft-title", "private-draft-body"):
        assert private not in response.get_data(as_text=True)
    assert "payload" not in job


def test_new_issue_recovery_has_repository_issue_list_and_known_case_link(setup):
    client, _, service = setup
    service.jobs[0]["payload"].update(case_id="case-one", marker="case-one-4")
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["github_url"] == "https://github.com/SillyBunnyTeam/SillyBunny/issues"
    assert target["case_id"] == "case-one"
    assert "issue_number" not in target


def test_missing_case_does_not_disclose_unverified_payload_destinations(setup):
    client, _, service = setup
    service.jobs[0]["payload"].update(case_id="not-a-known-case", marker="case-one-4",
                                     repository="unverified/repository", guild_id="123", channel_id="456")
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target == {}


def test_event_recovery_uses_recorded_binding_and_discards_url_query_values(setup):
    client, saved, service = setup
    saved["config"]["guild_id"] = "999"
    saved["config"]["reviews_channel_id"] = "888"
    service.jobs = [{"id": 2, "kind": "event", "state": "recovery", "payload": {
        "kind": "pull", "key": "pull:42", "number": 42, "repository": "SillyBunnyTeam/SillyBunny",
        "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/pull/42?token=not-public#issuecomment-123",
        "body": "private-event-body", "sender": "private-sender-context",
        "_binding": {"guild_id": "123", "reviews_channel_id": "456", "repository": "SillyBunnyTeam/SillyBunny", "bot_name": "Helper"},
    }}]
    response = client.get("/api/project-automation/jobs")
    assert response.get_json()["jobs"][0]["target"] == {
        "repository": "SillyBunnyTeam/SillyBunny", "issue_number": 42,
        "github_url": "https://github.com/SillyBunnyTeam/SillyBunny/pull/42#issuecomment-123",
        "discord_url": "https://discord.com/channels/123/456",
    }
    for private in ("not-public", "private-event-body", "private-sender-context"):
        assert private not in response.get_data(as_text=True)


def test_event_recovery_prefers_a_matching_recorded_mirror_thread(setup):
    from types import SimpleNamespace
    from unittest.mock import Mock
    client, _, service = setup
    service.store = SimpleNamespace(get_link=Mock(return_value={
        "channel_id": "789", "message_id": "789", "repository": "SillyBunnyTeam/SillyBunny",
        "bot_name": "Helper", "guild_id": "123",
    }))
    service.jobs = [{"id": 2, "kind": "event", "payload": {
        "kind": "issue", "key": "issue:42", "number": 42, "repository": "SillyBunnyTeam/SillyBunny",
        "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42",
        "_binding": {"guild_id": "123", "issues_channel_id": "456", "repository": "SillyBunnyTeam/SillyBunny", "bot_name": "Helper"},
    }}]
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/789"
    service.store.get_link.assert_called_once_with("mirror", "123:Helper:456:SillyBunnyTeam/SillyBunny:issue:42")
    service.store.get_link.return_value["bot_name"] = "Different helper"
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/456"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://github.com.evil.test/SillyBunnyTeam/SillyBunny/issues/1",
                               "https://github.com/elsewhere/project/issues/1", "https://github.com/SillyBunnyTeam/SillyBunny/%2e%2e/elsewhere"])
def test_job_target_rejects_unexpected_github_locations(setup, url):
    client, _, service = setup
    service.jobs = [{"id": 2, "kind": "event", "payload": {
        "kind": "issue", "repository": "SillyBunnyTeam/SillyBunny", "html_url": url,
    }}]
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert "github_url" not in target
