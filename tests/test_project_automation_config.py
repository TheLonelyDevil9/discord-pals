import json

import pytest

from project_automation_config import (
    PROJECT_DEFAULTS, config_errors, config_input_errors, credential_status,
    normalize_project_config,
)


def complete_config():
    return normalize_project_config({
        "bot_name": "Project Helper", "guild_id": "100000000000000001",
        "feedback_channel_id": "100000000000000002", "issues_channel_id": "100000000000000003",
        "reviews_channel_id": "100000000000000004", "commits_channel_id": "100000000000000005",
        "github_channel_id": "100000000000000006", "maintainer_role_ids": ["100000000000000007"],
        "github_app_id": "123", "github_installation_id": "456",
    })


@pytest.mark.parametrize("value", [None, [], "wrong", 4])
def test_invalid_objects_get_complete_safe_defaults(value):
    result = normalize_project_config(value)
    assert result == PROJECT_DEFAULTS
    assert result is not PROJECT_DEFAULTS
    result["maintainer_role_ids"].append("99")
    assert not PROJECT_DEFAULTS["maintainer_role_ids"]


def test_ids_preserve_exact_digits_and_reject_floats_or_embedded_numbers():
    result = normalize_project_config({
        "guild_id": "100000000000000019",
        "feedback_channel_id": 100000000000000017,
        "issues_channel_id": 1e18,
        "reviews_channel_id": "channel-123",
        "commits_channel_id": False,
        "maintainer_role_ids": ["000123", "123", "10bad", "456", 789],
        "maintainer_user_ids": "123, 456 123",
    })
    assert result["guild_id"] == "100000000000000019"
    assert result["feedback_channel_id"] == "100000000000000017"
    assert result["issues_channel_id"] == result["reviews_channel_id"] == result["commits_channel_id"] == ""
    assert result["maintainer_role_ids"] == ["123", "456", "789"]
    assert result["maintainer_user_ids"] == ["123", "456"]


@pytest.mark.parametrize("repository", ["https://github.com/team/repo", "a/b/c", "a/..", "a/", "a/b?x=1"])
def test_repository_cannot_change_destination_host_or_path(repository):
    assert normalize_project_config({"repository": repository})["repository"] == ""
    assert config_input_errors({"repository": repository})


def test_malformed_names_and_secret_values_never_survive_normalization():
    result = normalize_project_config({
        "github_private_key_env": "-----BEGIN PRIVATE KEY-----\nprivate-value",
        "github_private_key_file_env": "C:/private-key.pem",
        "github_webhook_secret_env": {"token": "private-value"},
        "character_name": "../../secret",
        "token": "private-value", "github_private_key": "private-value",
    })
    assert "private-value" not in json.dumps(result)
    assert not result["github_private_key_env"]
    assert not result["github_private_key_file_env"]
    assert not result["github_webhook_secret_env"]
    assert not result["character_name"]
    errors = config_input_errors({"token": "private-value", "github_private_key_env": "private-value"})
    assert errors
    assert "private-value" not in json.dumps(errors)


@pytest.mark.parametrize("value,expected", [(0, 1), (99, 10), (None, 3), (float("inf"), 3), (True, 3)])
def test_question_limit_has_a_finite_bound(value, expected):
    assert normalize_project_config({"max_questions": value})["max_questions"] == expected


def test_enabled_is_not_python_truthiness():
    assert not normalize_project_config({"enabled": "false"})["enabled"]
    assert not normalize_project_config({"enabled": {"true": 1}})["enabled"]
    assert normalize_project_config({"enabled": True})["enabled"]
    assert config_input_errors({"enabled": "false"})


def test_readiness_requires_routing_and_human_handoff():
    assert config_errors({})
    config = complete_config()
    assert config_errors(config) == []
    config["reviews_channel_id"] = config["issues_channel_id"]
    assert any("separate" in message for message in config_errors(config))
    config["maintainer_role_ids"] = []
    assert any("maintainer" in message for message in config_errors(config))


def test_credential_status_is_only_boolean_presence(monkeypatch, tmp_path):
    config = normalize_project_config({})
    for key in ("PROJECT_GITHUB_PRIVATE_KEY", "PROJECT_GITHUB_PRIVATE_KEY_FILE", "PROJECT_GITHUB_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert credential_status(config) == {"private_key": False, "webhook_secret": False}
    key_file = tmp_path / "key.pem"
    key_file.write_text("secret-private-key", encoding="utf-8")
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "secret-webhook-value")
    result = credential_status(config)
    assert result == {"private_key": True, "webhook_secret": True}
    assert "secret" not in json.dumps(result).replace("webhook_secret", "")
    assert str(key_file) not in json.dumps(result)


def test_runtime_config_normalizes_and_persists_nested_settings(monkeypatch, tmp_path):
    import runtime_config
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(runtime_config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runtime_config, "RUNTIME_CONFIG_FILE", str(path))
    monkeypatch.setattr(runtime_config, "_apply_logging_config", lambda value: None)
    runtime_config.invalidate_cache()
    try:
        runtime_config.set("project_automation", {"guild_id": "100000000000000019", "enabled": "false", "max_questions": 99})
        runtime_config.invalidate_cache()
        result = runtime_config.get("project_automation")
        assert result["guild_id"] == "100000000000000019"
        assert result["enabled"] is False
        assert result["max_questions"] == 10
        assert set(result) == set(PROJECT_DEFAULTS)
        assert set(runtime_config.DEFAULTS) == set(runtime_config.CONFIG_FIELDS)
    finally:
        runtime_config.invalidate_cache()
