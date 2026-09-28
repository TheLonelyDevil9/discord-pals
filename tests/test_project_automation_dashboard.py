import json
import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

import project_automation_dashboard as dashboard
from project_automation_config import normalize_project_config
from security import generate_csrf_token


class FakeService:
    def __init__(self):
        self.case = {
            "id": "case-one", "state": "awaiting_submission", "reporter_id": "100000000000000019",
            "reporter_name": "reporter", "title": "My original feedback topic",
            "guild_id": "100000000000000001", "channel_id": "100000000000000002",
            "repository": "SillyBunnyTeam/SillyBunny",
            "submitted_report": {
                "title": "Text size resets on reload", "body": "I choose 18px, reload, and get 14px again.\nI expected 18px to stay selected.",
                "author_id": "100000000000000019", "author_name": "reporter", "human_authored": True,
            },
            "draft": {"title": "Text size resets on reload", "body": "Forwarded from Discord\n@reporter\n\nI choose 18px, reload, and get 14px again.\nI expected 18px to stay selected."},
            "gate": {"kind": "review", "options": [{"key": "submit", "label": "Approve and post"}, {"key": "edit", "label": "Edit my report"}]},
            "notice_event": {"action": "report_ready", "facts": {"published": False}},
        }
        self.limit = None
        self.bots = []
        self.links = {}
        self.store = SimpleNamespace(
            get_link=lambda kind, key: self.links.get((kind, key)),
            ensure_link=lambda kind, key, value: self.links.setdefault((kind, key), value),
        )
        self.jobs = [{"id": 1, "kind": "publish", "status": "recovery", "attempts": 1,
                      "error": "Write result unknown.", "payload": {"provider_context": "private-internal-context"}}]

    def status(self):
        return {"worker_running": False, "enabled": False}

    def list_cases(self, limit=100):
        self.limit = limit
        return [self.case]

    def get_case(self, case_id):
        return self.case if case_id == self.case["id"] else None

    def list_job_activity(self, **kwargs):
        return {"jobs": self.jobs, "next_before_id": None}



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
                                     get_character_names=lambda: ["firefly"],
                                     get_bot_instances=lambda: service.bots)
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
                                 "/api/project-automation/cases/case-one", "/api/project-automation/jobs",
                                 "/api/project-automation/discord-options"])
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


def test_offline_enable_persists_activity_baseline_and_later_activation_preserves_it(setup, monkeypatch):
    from project_automation_activity import activate, mapping_key
    client, saved, service = setup
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "test-key")
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "test-webhook")
    monkeypatch.setattr("project_automation_activity.time", SimpleNamespace(time=lambda: 100.0))
    assert service.bots == []
    response = client.post("/api/project-automation", json=valid_config(), headers=csrf())
    assert response.status_code == 200
    key = ("pr_activation", mapping_key(saved["config"]))
    assert service.links[key] == {"since": 100.0}
    monkeypatch.setattr("project_automation_activity.time", SimpleNamespace(time=lambda: 200.0))
    # The same persistent store is consulted by a worker after startup/restart.
    assert activate(service.store, saved["config"]) == {"since": 100.0}
    assert client.post("/api/project-automation", json={"max_questions": 4}, headers=csrf()).status_code == 200
    assert service.links[key] == {"since": 100.0}
    assert client.post("/api/project-automation", json={"enabled": False}, headers=csrf()).status_code == 200
    assert client.post("/api/project-automation", json={"enabled": True}, headers=csrf()).status_code == 200
    assert service.links[key] == {"since": 100.0}
    response = client.post("/api/project-automation", json={"reviews_channel_id": "100000000000000090"}, headers=csrf())
    assert response.status_code == 200
    new_key = ("pr_activation", mapping_key(saved["config"]))
    assert new_key != key
    assert service.links[new_key] == {"since": 200.0}
    assert service.links[key] == {"since": 100.0}


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


def test_cases_keep_exact_preview_and_limit_is_bounded(setup):
    client, _, service = setup
    response = client.get("/api/project-automation/cases?limit=50000")
    assert service.limit == 100
    assert response.get_json()["cases"][0]["reporter_id"] == "100000000000000019"
    response = client.get("/api/project-automation/cases/case-one")
    case = response.get_json()["case"]
    assert case["draft"]["body"] == "Forwarded from Discord\n@reporter\n\nI choose 18px, reload, and get 14px again.\nI expected 18px to stay selected."
    assert case["submitted_report"] == service.case["submitted_report"]
    assert case["reporter_name"] == "reporter"
    assert case["gate"]["options"][0]["label"] == "Approve and post"
    assert client.get("/api/project-automation/cases/missing").status_code == 404


def test_case_inspection_preserves_human_punctuation_and_untrusted_text(setup):
    client, _, service = setup
    text = 'My <input> literally says "YES".\n\nSteps:\n1. Open settings.\n2. Reload.\n<script>alert(1)</script>'
    service.case["submitted_report"]["body"] = text
    service.case["draft"]["body"] = "Forwarded from Discord\n@reporter\n\n" + text
    case = client.get("/api/project-automation/cases/case-one").get_json()["case"]
    assert case["submitted_report"]["body"] == text
    assert case["draft"]["body"] == "Forwarded from Discord\n@reporter\n\n" + text
    page = client.get("/project-automation").get_data(as_text=True)
    assert text not in page


def test_legacy_case_remains_inspectable_without_human_authorship_claim(setup):
    client, _, service = setup
    service.case.pop("submitted_report")
    service.case.pop("reporter_name")
    service.case.pop("notice_event")
    service.case.update(state="awaiting_choice", notice="An earlier decision is pending.",
                        assessment={"title": "Earlier generated title", "reply": "Can you add the version?"})
    case = client.get("/api/project-automation/cases/case-one").get_json()["case"]
    assert "submitted_report" not in case
    assert case["draft"] == service.case["draft"]
    assert case["notice"] == "An earlier decision is pending."
    assert client.get("/project-automation").status_code == 200


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


@pytest.fixture
def bot_loop():
    loop = asyncio.new_event_loop()
    started = threading.Event()
    def run():
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(2)
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


def test_check_requires_auth_and_csrf_and_does_not_save(setup, monkeypatch):
    client, saved, _ = setup
    before = dict(saved["config"])
    monkeypatch.setenv("DASHBOARD_PASS", "test-password")
    assert client.post("/api/project-automation/check", json={}, headers=csrf()).status_code == 401
    with client.session_transaction() as session:
        session["logged_in"] = True
    assert client.post("/api/project-automation/check", json={}).status_code == 403
    response = client.post("/api/project-automation/check", json=valid_config(), headers=csrf())
    assert response.status_code == 200
    assert response.get_json()["saved"] is False
    assert saved["config"] == before


@pytest.mark.parametrize("payload", [None, [], {"guild_id": 1e18}, {"private_key": "do-not-echo-secret"}, {"enabled": "true"}])
def test_check_rejects_malformed_draft_without_echoing_values(setup, payload):
    client, saved, _ = setup
    before = dict(saved["config"])
    response = client.post("/api/project-automation/check", data=json.dumps(payload), content_type="application/json", headers=csrf())
    assert response.status_code == 400
    assert "do-not-echo-secret" not in response.get_data(as_text=True)
    assert saved["config"] == before


def test_check_executes_only_on_selected_helper_loop_with_normalized_unsaved_draft(setup, bot_loop, monkeypatch):
    client, saved, service = setup
    helper = SimpleNamespace(name="Project Helper", client=SimpleNamespace(loop=bot_loop))
    service.bots = [SimpleNamespace(name="Other helper", client=SimpleNamespace(loop=None)), helper]
    received = []
    async def checks(config, bot):
        assert asyncio.get_running_loop() is bot_loop
        received.append((config, bot))
        return [{"key": "test", "label": "Draft check", "status": "passed", "detail": "Read only."}]
    monkeypatch.setattr(dashboard.setup, "setup_checks", checks)
    response = client.post("/api/project-automation/check", json={"config": valid_config()}, headers=csrf())
    assert response.status_code == 200
    assert received[0][0]["guild_id"] == "100000000000000001"
    assert received[0][1] is helper
    assert saved["config"]["bot_name"] == ""
    assert response.get_json()["checks"][-1]["key"] == "test"


def test_offline_selected_helper_never_borrows_another_bots_topology_or_loop(setup, bot_loop, monkeypatch):
    client, _, service = setup
    service.bots = [SimpleNamespace(name="Other helper", client=SimpleNamespace(loop=bot_loop))]
    async def forbidden(*_):
        pytest.fail("Read another helper's topology")
    monkeypatch.setattr(dashboard.setup, "discord_options", forbidden)
    monkeypatch.setattr(dashboard.setup, "setup_checks", forbidden)
    result = client.get("/api/project-automation/discord-options?bot_name=Project+Helper&guild_id=100000000000000019").get_json()
    assert result["status"] == "unverified"
    assert result["guild_id"] == "100000000000000019"
    assert result["guilds"] == result["channels"] == []
    assert "Other helper" not in json.dumps(result)
    result = client.post("/api/project-automation/check", json=valid_config(), headers=csrf()).get_json()
    assert next(row for row in result["checks"] if row["key"] == "discord_helper")["status"] == "unverified"


def test_discord_options_use_exact_selected_helper_and_do_not_save(setup, bot_loop, monkeypatch):
    client, saved, service = setup
    helper = SimpleNamespace(name="Project Helper", client=SimpleNamespace(loop=bot_loop))
    service.bots = [helper]
    async def options(config, bot):
        assert bot is helper
        assert asyncio.get_running_loop() is bot_loop
        return {"status": "passed", "guild_id": config["guild_id"], "guilds": [], "channels": []}
    monkeypatch.setattr(dashboard.setup, "discord_options", options)
    result = client.get("/api/project-automation/discord-options?bot_name=Project+Helper&guild_id=100000000000000019").get_json()
    assert result["guild_id"] == "100000000000000019"
    assert saved["config"]["guild_id"] == ""
    assert client.get("/api/project-automation/discord-options?guild_id=wrong").status_code == 400
    assert client.get("/api/project-automation/discord-options?bot_name=Unknown").get_json()["status"] == "attention"


def test_timed_out_setup_is_cancelled_and_reported_unverified(setup, bot_loop, monkeypatch):
    client, _, service = setup
    service.bots = [SimpleNamespace(name="Project Helper", client=SimpleNamespace(loop=bot_loop))]
    cancelled = threading.Event()
    async def slow(*_):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
    monkeypatch.setattr(dashboard.setup, "CHECK_TIMEOUT", 0.03)
    monkeypatch.setattr(dashboard.setup, "setup_checks", slow)
    result = client.post("/api/project-automation/check", json=valid_config(), headers=csrf()).get_json()
    assert cancelled.wait(2)
    assert next(row for row in result["checks"] if row["key"] == "discord_helper")["status"] == "unverified"


def test_pr_activity_jobs_link_to_exact_recorded_receipt_with_pinned_binding(setup):
    from unittest.mock import Mock
    client, saved, service = setup
    saved["config"]["reviews_channel_id"] = "999"
    receipt = {"channel_id": "789", "message_id": "790", "forum_id": "456",
               "repository": "team/project", "guild_id": "123", "bot_name": "Helper"}
    service.store = SimpleNamespace(get_link=Mock(side_effect=lambda kind, key: None if kind == "pr_prepared" else receipt))
    service.jobs = [{"id": 2, "kind": "pr_activity", "payload": {
        "kind": "pr_activity", "key": "review:42", "source_version": "version-2", "number": 7,
        "repository": "team/project", "html_url": "https://github.com/team/project/pull/7#pullrequestreview-42",
        "body": "private-event-body", "_binding": {"guild_id": "123", "bot_name": "Helper", "reviews_channel_id": "456", "repository": "team/project"},
    }}]
    response = client.get("/api/project-automation/jobs")
    target = response.get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/789/790"
    assert target["github_url"] == "https://github.com/team/project/pull/7#pullrequestreview-42"
    assert "private-event-body" not in response.get_data(as_text=True)
    receipt["forum_id"] = "other-forum"
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/456"


@pytest.mark.parametrize("kind", ["pr_sync_page", "pr_sync_check", "pr_sync_missing", "pr_sync_discover"])
def test_failed_reconciliation_links_to_its_original_destination(setup, kind):
    client, saved, service = setup
    saved["config"].update(guild_id="999", reviews_channel_id="888")
    service.jobs = [{"id": 3, "kind": kind, "state": "failed", "payload": {
        "repository": "Owner/Project", "number": 7,
        "_binding": {"repository": "Owner/Project", "guild_id": "123", "bot_name": "Helper", "reviews_channel_id": "456"},
    }}]
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/456"
    assert target["github_url"] == "https://github.com/Owner/Project/pull/7"
    service.links[("mirror", "123:Helper:456:Owner/Project:pull:7")] = {
        "repository": "Owner/Project", "guild_id": "123", "bot_name": "Helper", "channel_id": "789",
    }
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/789"


def test_pr_activity_target_uses_canonical_prepared_version_and_thread_fallback(setup):
    from project_automation_activity import activity_receipt_key
    client, _, service = setup
    binding = {"guild_id": "123", "bot_name": "Helper", "reviews_channel_id": "456", "repository": "team/project"}
    payload = {"kind": "pr_activity", "key": "comment:55", "number": 7, "source_version": "old", "repository": "team/project", "_binding": binding}
    prepared = {**payload, "source_version": "current", "delivery_key": "comment:55:occurrence-9",
                "html_url": "https://github.com/team/project/pull/7#issuecomment-55"}
    key = activity_receipt_key(binding, prepared)
    records = {
        ("pr_prepared", "11"): prepared,
        ("pr_activity_delivery", key + ":current"): {
            **binding, "forum_id": "456", "channel_id": "700", "message_id": "701", "source_version": "current"},
        ("pr_activity", key): {
            **binding, "forum_id": "456", "channel_id": "700", "message_id": "702", "source_version": "later"},
        ("mirror", "123:Helper:456:team/project:pull:7"): {**binding, "channel_id": "700"},
    }
    service.store = SimpleNamespace(get_link=lambda kind, key: records.get((kind, key)))
    service.jobs = [{"id": 11, "kind": "event", "payload": payload}]
    result = client.get("/api/project-automation/jobs").get_json()["jobs"][0]
    assert result["activity"] == "pr_activity"
    assert result["target"]["discord_url"] == "https://discord.com/channels/123/700/701"
    assert result["target"]["github_url"].endswith("#issuecomment-55")
    del records[("pr_activity_delivery", key + ":current")]
    target = client.get("/api/project-automation/jobs").get_json()["jobs"][0]["target"]
    assert target["discord_url"] == "https://discord.com/channels/123/700"


@pytest.fixture
def activity_api(setup, tmp_path):
    from project_automation_store import AutomationStore
    client, saved, service = setup
    store = AutomationStore(tmp_path / "activity.sqlite3")
    service.store = store
    service.list_job_activity = store.list_job_activity
    service.dismiss_jobs = store.dismiss_jobs
    service.restore_job = store.restore_job
    identifier = store.enqueue("assess", {"provider_context": "private-provider-context"}, "failed")
    store.claim_job()
    store.fail_job(identifier, "WorkflowError: event needs attention", permanent=True)
    job = store.get_job(identifier)
    return client, store, {"id": identifier, "updated_at": job["updated_at"]}


def test_activity_api_dismiss_restore_preserves_failure_and_suppresses_provider_data(activity_api):
    client, store, snapshot = activity_api
    url = "/api/project-automation/jobs"
    before = store.get_job(snapshot["id"])
    page = client.get(url).get_json()
    assert page["next_before_id"] is None
    assert page["jobs"][0]["dismissed_at"] is None
    assert page["jobs"][0]["state"] == "failed"
    assert "private-provider-context" not in json.dumps(page)
    assert page["jobs"][0]["last_error"] == before["last_error"]
    response = client.post(url + "/dismiss", json={"jobs": [snapshot]}, headers=csrf())
    assert response.status_code == 200
    assert response.get_json() == {"dismissed": 1}
    assert client.get(url).get_json() == {"jobs": [], "next_before_id": None}
    assert client.get(url + "?view=attention").get_json()["jobs"] == []
    dismissed = client.get(url + "?view=dismissed").get_json()["jobs"][0]
    assert dismissed["id"] == snapshot["id"]
    assert isinstance(dismissed["dismissed_at"], (int, float))
    assert dismissed["updated_at"] == snapshot["updated_at"]
    assert client.post(url + "/dismiss", json={"jobs": [snapshot]}, headers=csrf()).get_json() == {"dismissed": 1}
    response = client.post(url + f'/{snapshot["id"]}/restore',
                           json={"updated_at": snapshot["updated_at"]}, headers=csrf())
    assert response.status_code == 200
    assert response.get_json() == {"restored": True}
    assert client.get(url + "?view=dismissed").get_json()["jobs"] == []
    restored = client.get(url + "?view=attention").get_json()["jobs"][0]
    assert restored["id"] == snapshot["id"] and restored["dismissed_at"] is None
    after = store.get_job(snapshot["id"])
    for key in ("state", "payload", "last_error", "attempts", "updated_at"):
        assert after[key] == before[key]


@pytest.mark.parametrize("action", ["dismiss", "restore"])
def test_activity_mutations_require_auth_and_csrf(activity_api, monkeypatch, action):
    client, store, snapshot = activity_api
    path = "/api/project-automation/jobs/" + ("dismiss" if action == "dismiss" else f'{snapshot["id"]}/restore')
    payload = {"jobs": [snapshot]} if action == "dismiss" else {"updated_at": snapshot["updated_at"]}
    before = store.get_job(snapshot["id"])
    monkeypatch.setenv("DASHBOARD_PASS", "test-password")
    assert client.post(path, json=payload, headers=csrf()).status_code == 401
    with client.session_transaction() as session:
        session["logged_in"] = True
    assert client.post(path, json=payload).status_code == 403
    assert store.get_job(snapshot["id"]) == before
    assert store.get_link("job_dismissal", snapshot["id"]) is None


@pytest.mark.parametrize("query", ["view=unknown", "view=", "before_id=0", "before_id=-1", "before_id=true",
                                  "before_id=1.5", "before_id=", "before_id=9223372036854775808",
                                  "limit=0", "limit=101", "limit=true", "limit=1.5", "limit=garbage"])
def test_activity_api_rejects_invalid_query(activity_api, query):
    client, _, _ = activity_api
    assert client.get("/api/project-automation/jobs?" + query).status_code == 400


@pytest.mark.parametrize("payload", [None, [], "private-input", {}, {"jobs": []}, {"jobs": "private-input"},
                                    {"jobs": [{"id": True, "updated_at": 1}]},
                                    {"jobs": [{"id": 1, "updated_at": False}]},
                                    {"jobs": [{"id": 1, "updated_at": "private-input"}]},
                                    {"jobs": [{"id": 1, "updated_at": float("nan")}]},
                                    {"jobs": [{"id": 1, "updated_at": float("inf")}]},
                                    {"jobs": [{"id": 1, "updated_at": 1}] * 2},
                                    {"jobs": [{"id": number, "updated_at": 1} for number in range(1, 102)]}])
def test_activity_api_rejects_malformed_bulk_without_echo(activity_api, payload):
    client, store, snapshot = activity_api
    response = client.post("/api/project-automation/jobs/dismiss", data=json.dumps(payload),
                           content_type="application/json", headers=csrf())
    assert response.status_code == 400
    assert "private-input" not in response.get_data(as_text=True)
    assert store.get_link("job_dismissal", snapshot["id"]) is None


@pytest.mark.parametrize("identifier,payload", [("true", {"updated_at": 1}), ("0", {"updated_at": 1}),
                                              ("-1", {"updated_at": 1}), ("1", []), ("1", {}),
                                              ("1", {"updated_at": True}), ("1", {"updated_at": "private-input"}),
                                              ("1", {"updated_at": float("inf")})])
def test_activity_restore_rejects_malformed_snapshot(activity_api, identifier, payload):
    client, _, _ = activity_api
    response = client.post(f"/api/project-automation/jobs/{identifier}/restore", data=json.dumps(payload),
                           content_type="application/json", headers=csrf())
    assert response.status_code == 400
    assert "private-input" not in response.get_data(as_text=True)


def test_activity_api_bulk_missing_and_stale_snapshots_are_atomic(activity_api):
    client, store, snapshot = activity_api
    path = "/api/project-automation/jobs"
    response = client.post(path + "/dismiss", json={"jobs": [snapshot, {"id": 999, "updated_at": 1}]}, headers=csrf())
    assert response.status_code == 404
    assert store.get_link("job_dismissal", snapshot["id"]) is None
    assert store.get_job(snapshot["id"])["recovery_notes"] == []
    response = client.post(path + "/999/restore", json={"updated_at": 1}, headers=csrf())
    assert response.status_code == 404
    stale = {**snapshot, "updated_at": snapshot["updated_at"] - 1}
    assert client.post(path + "/dismiss", json={"jobs": [stale]}, headers=csrf()).status_code == 409
    assert client.post(path + f'/{snapshot["id"]}/restore', json={"updated_at": stale["updated_at"]}, headers=csrf()).status_code == 409
    store.retry_job(snapshot["id"])
    assert client.post(path + "/dismiss", json={"jobs": [snapshot]}, headers=csrf()).status_code == 409


def test_activity_api_recovery_cannot_be_dismissed(activity_api):
    client, store, snapshot = activity_api
    store.retry_job(snapshot["id"])
    store.claim_job()
    store.mark_job_inflight(snapshot["id"])
    store.fail_job(snapshot["id"], "Remote write needs verification", permanent=True)
    current = {"id": snapshot["id"], "updated_at": store.get_job(snapshot["id"])["updated_at"]}
    response = client.post("/api/project-automation/jobs/dismiss", json={"jobs": [current]}, headers=csrf())
    assert response.status_code == 409
    jobs = client.get("/api/project-automation/jobs?view=attention").get_json()["jobs"]
    assert jobs[0]["state"] == "recovery"
    assert jobs[0]["last_error"] == "Remote write needs verification"


def test_activity_api_cursor_does_not_skip_filtered_rows(activity_api):
    client, store, snapshot = activity_api
    middle = store.enqueue("assess", {}, "middle")
    newest = store.enqueue("assess", {}, "newest")
    page = client.get("/api/project-automation/jobs?limit=1").get_json()
    assert page["jobs"][0]["id"] == newest
    assert page["next_before_id"] == newest
    store.dismiss_jobs([snapshot])
    page = client.get(f"/api/project-automation/jobs?limit=1&before_id={newest}").get_json()
    assert page["jobs"][0]["id"] == middle
    assert page["next_before_id"] is None


@pytest.mark.parametrize("action", ["list", "dismiss", "restore"])
def test_activity_api_never_returns_internal_exceptions(activity_api, setup, monkeypatch, action):
    client, _, snapshot = activity_api
    service = setup[2]

    def broken(*args, **kwargs):
        raise RuntimeError("private-database-provider-secret")

    if action == "list":
        monkeypatch.setattr(service, "list_job_activity", broken)
        response = client.get("/api/project-automation/jobs")
    elif action == "dismiss":
        monkeypatch.setattr(service, "dismiss_jobs", broken)
        response = client.post("/api/project-automation/jobs/dismiss", json={"jobs": [snapshot]}, headers=csrf())
    else:
        monkeypatch.setattr(service, "restore_job", broken)
        response = client.post(f'/api/project-automation/jobs/{snapshot["id"]}/restore',
                               json={"updated_at": snapshot["updated_at"]}, headers=csrf())
    assert response.status_code == 500
    assert "private-database-provider-secret" not in response.get_data(as_text=True)


def test_activity_actor_cannot_be_supplied_by_client_and_audit_stays_private(activity_api):
    client, store, snapshot = activity_api
    path = "/api/project-automation/jobs"
    for payload in ({"jobs": [snapshot], "actor": "private-forged-actor"},
                    {"jobs": [{**snapshot, "actor": "private-forged-actor"}]}):
        response = client.post(path + "/dismiss", json=payload, headers=csrf())
        assert response.status_code == 400
        assert "private-forged-actor" not in response.get_data(as_text=True)
    assert store.get_job(snapshot["id"])["recovery_notes"] == []
    assert client.post(path + "/dismiss", json={"jobs": [snapshot]}, headers=csrf()).status_code == 200
    assert store.get_job(snapshot["id"])["recovery_notes"][-1]["actor"] == "dashboard"
    dismissed = client.get(path + "?view=dismissed").get_json()["jobs"][0]
    assert "recovery_notes" not in dismissed
    assert "payload" not in dismissed
    assert "lease_token" not in dismissed
