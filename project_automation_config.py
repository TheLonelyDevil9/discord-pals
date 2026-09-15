"""Validated, secret-free settings for the Discord project helper."""

from __future__ import annotations

import os
import re
from pathlib import Path


PROJECT_DEFAULTS = {
    "enabled": False,
    "bot_name": "",
    "guild_id": "",
    "repository": "SillyBunnyTeam/SillyBunny",
    "feedback_channel_id": "",
    "support_channel_id": "",
    "issues_channel_id": "",
    "reviews_channel_id": "",
    "commits_channel_id": "",
    "github_channel_id": "",
    "maintainer_role_ids": [],
    "maintainer_user_ids": [],
    "character_name": "",
    "provider_tier": "",
    "max_questions": 3,
    "github_app_id": "",
    "github_installation_id": "",
    "github_private_key_env": "PROJECT_GITHUB_PRIVATE_KEY",
    "github_private_key_file_env": "PROJECT_GITHUB_PRIVATE_KEY_FILE",
    "github_webhook_secret_env": "PROJECT_GITHUB_WEBHOOK_SECRET",
}
CHANNEL_FIELDS = (
    "feedback_channel_id", "support_channel_id", "issues_channel_id",
    "reviews_channel_id", "commits_channel_id", "github_channel_id",
)
ID_FIELDS = ("guild_id", "github_app_id", "github_installation_id") + CHANNEL_FIELDS
ID_LIST_FIELDS = ("maintainer_role_ids", "maintainer_user_ids")
ENV_FIELDS = (
    "github_private_key_env", "github_private_key_file_env", "github_webhook_secret_env",
)
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z")
_ID_RE = re.compile(r"[0-9]{1,20}\Z")


def _text(value, limit=128) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.strip() if ch.isprintable())[:limit]


def _identifier(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    if not _ID_RE.fullmatch(text) or int(text) == 0:
        return ""
    return str(int(text))


def _ids(value) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value.strip())
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for raw in value[:100] if (item := _identifier(raw))))


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}


def normalize_project_config(value) -> dict:
    """Drop unknown fields and normalize storage/API input into a complete shape."""
    source = value if isinstance(value, dict) else {}
    result = {key: list(default) if isinstance(default, list) else default
              for key, default in PROJECT_DEFAULTS.items()}
    result["enabled"] = _bool(source.get("enabled", False))
    for key in ID_FIELDS:
        result[key] = _identifier(source.get(key, result[key]))
    for key in ID_LIST_FIELDS:
        result[key] = _ids(source.get(key, []))
    for key in ("bot_name", "character_name", "provider_tier"):
        result[key] = _text(source.get(key, result[key]))
    if any(ch in result["character_name"] for ch in '/\\:') or result["character_name"].startswith("."):
        result["character_name"] = ""
    repository = _text(source.get("repository", result["repository"]), 140)
    result["repository"] = repository if _REPO_RE.fullmatch(repository) and repository.split("/")[-1] not in {".", ".."} else ""
    for key in ENV_FIELDS:
        candidate = source.get(key, result[key])
        result[key] = candidate.strip() if isinstance(candidate, str) and _ENV_RE.fullmatch(candidate.strip()) else ""
    try:
        question_limit = source.get("max_questions", 3)
        if isinstance(question_limit, bool):
            raise ValueError
        result["max_questions"] = max(1, min(10, int(question_limit)))
    except (TypeError, ValueError, OverflowError):
        result["max_questions"] = 3
    return result


def config_input_errors(value) -> list[str]:
    """Reject malformed dashboard edits without repeating submitted values."""
    if not isinstance(value, dict):
        return ["Settings must be a JSON object."]
    errors = []
    if any(key not in PROJECT_DEFAULTS for key in value):
        errors.append("Unknown setting. Only the displayed project settings can be saved.")
    for key in ID_FIELDS:
        raw = value.get(key, "")
        if raw not in (None, "") and not _identifier(raw):
            errors.append(f"{key}: enter a positive numeric ID.")
    for key in ID_LIST_FIELDS:
        raw = value.get(key, [])
        if isinstance(raw, str):
            raw = [item for item in re.split(r"[\s,]+", raw.strip()) if item]
        if not isinstance(raw, (list, tuple)) or len(raw) > 100 or any(not _identifier(item) for item in raw):
            errors.append(f"{key}: enter up to 100 numeric IDs, separated by commas or spaces.")
    for key in ENV_FIELDS:
        if key in value and (not isinstance(value[key], str) or not _ENV_RE.fullmatch(value[key].strip())):
            errors.append(f"{key}: enter an environment variable name, not a secret or file path.")
    normalized = normalize_project_config(value)
    if value.get("repository") and not normalized["repository"]:
        errors.append("repository: use owner/repository, without a URL.")
    for key in ("bot_name", "character_name", "provider_tier"):
        if key in value and (not isinstance(value[key], str) or value[key].strip() != normalized[key]):
            errors.append(f"{key}: enter a name of up to 128 printable characters.")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        errors.append("enabled: use true or false.")
    if "max_questions" in value:
        raw = value["max_questions"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 10:
            errors.append("max_questions: choose a whole number from 1 to 10.")
    return errors


def config_errors(config) -> list[str]:
    """Return missing routing/identity settings required before enabling."""
    normalized = normalize_project_config(config)
    required = {
        "bot_name": "Choose the dedicated helper bot.",
        "guild_id": "Set the Discord server ID.",
        "repository": "Set a GitHub owner/repository.",
        "feedback_channel_id": "Set the #submit-feedback channel ID.",
        "issues_channel_id": "Set the #issue-tracker channel ID.",
        "reviews_channel_id": "Set the #review-please channel ID.",
        "commits_channel_id": "Set the #commit-log channel ID.",
        "github_channel_id": "Set the #github channel ID.",
        "github_app_id": "Set the GitHub App ID.",
        "github_installation_id": "Set the GitHub App installation ID.",
    }
    errors = [message for key, message in required.items() if not normalized[key]]
    channels = [normalized[key] for key in CHANNEL_FIELDS if normalized[key]]
    if len(channels) != len(set(channels)):
        errors.append("Use a separate Discord channel for each project function.")
    if not normalized["maintainer_role_ids"] and not normalized["maintainer_user_ids"]:
        errors.append("Set at least one maintainer role or user ID for human handoffs.")
    if any(not normalized[key] for key in ENV_FIELDS):
        errors.append("Set valid environment variable names for GitHub credentials.")
    return errors


def credential_status(config) -> dict[str, bool]:
    """Report presence only; never return secret values or configured file paths."""
    normalized = normalize_project_config(config)
    inline_key = bool(os.getenv(normalized["github_private_key_env"], "").strip())
    file_value = os.getenv(normalized["github_private_key_file_env"], "").strip()
    try:
        file_key = bool(file_value and Path(file_value).is_file())
    except (OSError, ValueError):
        file_key = False
    webhook = bool(os.getenv(normalized["github_webhook_secret_env"], "").strip())
    return {"private_key": inline_key or file_key, "webhook_secret": webhook}
