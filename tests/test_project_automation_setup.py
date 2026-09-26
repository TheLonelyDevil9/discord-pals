import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from project_automation_config import normalize_project_config
import project_automation_setup as setup


def channel(identifier, name, kind, **permissions):
    grants = {key: True for key in setup.PERMISSION_LABELS}
    grants.update(permissions)
    return SimpleNamespace(id=identifier, name=name, type=SimpleNamespace(value=kind),
                           category=SimpleNamespace(name="Project"),
                           permissions_for=lambda member: SimpleNamespace(**grants))


def mapping():
    channels = [channel(100000000000000021, "feedback-renamed", 15),
                channel(100000000000000022, "issues-renamed", 15),
                channel(100000000000000023, "pulls-renamed", 15),
                channel(100000000000000024, "commits-renamed", 0),
                channel(100000000000000025, "repository-renamed", 0)]
    guild = SimpleNamespace(id=100000000000000019, name="Selected helper server", channels=channels, me=object())
    bot = SimpleNamespace(name="Helper", client=SimpleNamespace(guilds=[guild], is_ready=lambda: True,
                                                               intents=SimpleNamespace(members=True, message_content=True)))
    cfg = normalize_project_config({
        "bot_name": "Helper", "guild_id": str(guild.id), "repository": "team/project",
        "github_app_id": "12", "github_installation_id": "34", "maintainer_user_ids": ["56"],
        **{field: str(item.id) for (field, _, _, _), item in zip(setup.CHANNEL_PURPOSES, channels)},
    })
    return cfg, bot, guild


def by_key(checks):
    return {row["key"]: row for row in checks}


def test_choices_are_selected_helper_and_guild_only_with_string_ids():
    cfg, bot, guild = mapping()
    guild.channels += [channel(91, "hidden", 0, view_channel=False), channel(92, "voice", 2), channel(93, "announcement", 5)]
    second = SimpleNamespace(id=94, name="Second selected-helper server", channels=[channel(95, "not-requested", 0)], me=object())
    bot.client.guilds.append(second)
    result = asyncio.run(setup.discord_options(cfg, bot))
    assert result["status"] == "passed"
    assert result["guilds"] == [{"id": "94", "name": second.name}, {"id": str(guild.id), "name": guild.name}]
    assert {item["type"] for item in result["channels"]} == {"forum", "text"}
    assert len(result["channels"]) == 5
    assert all(isinstance(item["id"], str) for item in result["channels"] + result["guilds"])
    assert "100000000000000023" in {item["id"] for item in result["channels"]}
    assert "pulls-renamed" in json.dumps(result)
    assert all(name not in json.dumps(result) for name in ("hidden", "voice", "announcement", "not-requested"))


def test_unknown_guild_does_not_fall_back_to_other_guild_channels():
    cfg, bot, _ = mapping()
    cfg["guild_id"] = "987654"
    result = asyncio.run(setup.discord_options(cfg, bot))
    assert result["status"] == "attention"
    assert result["guild_id"] == "987654"
    assert result["channels"] == []


@pytest.mark.parametrize("state", ["offline", "unavailable", "unknown_member"])
def test_missing_discord_data_is_not_verified(state):
    cfg, bot, guild = mapping()
    if state == "offline":
        bot.client.is_ready = lambda: False
    elif state == "unavailable":
        guild.unavailable = True
    else:
        guild.me = None
    result = asyncio.run(setup.discord_options(cfg, bot))
    assert result["status"] == "unverified"
    assert result["channels"] == []
    checks = by_key(setup.discord_checks(cfg, bot))
    assert checks["discord_reviews_channel_id"]["status"] == "unverified"


def test_type_and_effective_permissions_are_checked_per_purpose():
    cfg, bot, guild = mapping()
    guild.channels[1] = channel(guild.channels[1].id, "wrong-type", 0)
    guild.channels[2] = channel(guild.channels[2].id, "pulls", 15, send_messages=False, manage_threads=False)
    checks = by_key(setup.discord_checks(cfg, bot))
    assert checks["discord_feedback_channel_id"]["status"] == "passed"
    assert checks["discord_issues_channel_id"]["status"] == "attention"
    assert "forum" in checks["discord_issues_channel_id"]["detail"]
    assert checks["discord_reviews_channel_id"]["status"] == "attention"
    assert "Send Messages" in checks["discord_reviews_channel_id"]["detail"]
    assert "Manage Threads" in checks["discord_reviews_channel_id"]["detail"]
    assert checks["discord_support_channel_id"]["status"] == "passed"


def test_deleted_channel_and_unknown_permissions_have_different_states():
    cfg, bot, guild = mapping()
    guild.channels.pop(0)
    guild.channels[0].permissions_for = lambda member: SimpleNamespace(view_channel=True)
    checks = by_key(setup.discord_checks(cfg, bot))
    assert checks["discord_feedback_channel_id"]["status"] == "attention"
    assert checks["discord_issues_channel_id"]["status"] == "unverified"


def test_text_feedback_requires_thread_creation_but_optional_support_does_not():
    cfg, bot, guild = mapping()
    guild.channels[0] = channel(guild.channels[0].id, "feedback", 0, create_public_threads=False)
    guild.channels.append(channel(99, "support", 0, create_public_threads=False, manage_threads=False))
    cfg["support_channel_id"] = "99"
    checks = by_key(setup.discord_checks(cfg, bot))
    assert checks["discord_feedback_channel_id"]["status"] == "attention"
    assert "Create Public Threads" in checks["discord_feedback_channel_id"]["detail"]
    assert checks["discord_support_channel_id"]["status"] == "passed"


def test_pr_excerpts_do_not_require_file_attachment_permission():
    cfg, bot, guild = mapping()
    guild.channels[2] = channel(guild.channels[2].id, "pulls", 15, attach_files=False)
    assert by_key(setup.discord_checks(cfg, bot))["discord_reviews_channel_id"]["status"] == "passed"


def test_local_checks_require_distinct_destinations_and_never_echo_credentials(monkeypatch):
    cfg, _, _ = mapping()
    cfg["reviews_channel_id"] = cfg["issues_channel_id"]
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "private-pem-no-echo")
    monkeypatch.setenv("PROJECT_GITHUB_WEBHOOK_SECRET", "private-secret-no-echo")
    rows = setup.local_checks(cfg)
    assert by_key(rows)["configuration"]["status"] == "attention"
    assert "separate" in by_key(rows)["configuration"]["detail"]
    assert "no-echo" not in json.dumps(rows)


def test_github_checks_use_draft_and_close_the_read_only_client(monkeypatch):
    cfg, bot, _ = mapping()
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "test-key")
    client = SimpleNamespace(setup_checks=AsyncMock(return_value=[setup.check("github_repo", "Repository", "passed", "Available.")]),
                             close=AsyncMock())
    received = []
    rows = asyncio.run(setup.setup_checks(cfg, bot, lambda draft: received.append(draft) or client))
    assert received == [cfg]
    assert by_key(rows)["github_repo"]["status"] == "passed"
    client.setup_checks.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("failure", [TimeoutError("private-token-path"), RuntimeError("private-token-path")])
def test_github_failures_are_unverified_and_secret_free(monkeypatch, failure):
    cfg, bot, _ = mapping()
    monkeypatch.setenv("PROJECT_GITHUB_PRIVATE_KEY", "test-key")
    client = SimpleNamespace(setup_checks=AsyncMock(side_effect=failure), close=AsyncMock())
    rows = asyncio.run(setup.setup_checks(cfg, bot, lambda draft: client))
    assert by_key(rows)["github_connection"]["status"] == "unverified"
    assert "private-token-path" not in json.dumps(rows)
    client.close.assert_awaited_once()


def test_incomplete_github_settings_do_not_attempt_requests(monkeypatch):
    cfg, bot, _ = mapping()
    cfg["github_app_id"] = ""
    def unexpected(_):
        pytest.fail("Incomplete settings attempted a network check")
    rows = asyncio.run(setup.setup_checks(cfg, bot, unexpected))
    assert by_key(rows)["github_connection"]["status"] == "unverified"
