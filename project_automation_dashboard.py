"""Dashboard routes for configuration and inspection of project automation."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import jsonify, render_template, request

import runtime_config
from project_automation_config import (
    config_errors, config_input_errors, credential_status, normalize_project_config,
)
from security import requires_auth, requires_csrf


def _bot_names() -> list[str]:
    from env_config import load_bot_mode_config
    return [entry["name"] for entry in load_bot_mode_config().get("bots", [])]


def _character_names() -> list[str]:
    return sorted(path.stem for path in Path("characters").glob("*.md") if path.stem != "template")


def _names(callback) -> list[str]:
    return sorted(set(item for item in callback() if isinstance(item, str) and len(item) <= 128))[:200]


def _limit() -> int:
    try:
        return max(1, min(100, int(request.args.get("limit", "100"))))
    except (ValueError, TypeError):
        return 100


def _discord_url(guild_id, channel_id) -> str:
    ids = (str(guild_id or ""), str(channel_id or ""))
    if all(re.fullmatch(r"[0-9]{1,20}", value) and int(value) > 0 for value in ids):
        return f"https://discord.com/channels/{ids[0]}/{ids[1]}"
    return ""


def _repository(value) -> str:
    if not isinstance(value, str):
        return ""
    return normalize_project_config({"repository": value})["repository"]


def _github_url(value, repository) -> str:
    if not repository or not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        parsed = urlsplit(value)
        parts = unquote(parsed.path).split("/")
        if (parsed.scheme != "https" or parsed.netloc != "github.com"
                or parts[1:3] != repository.split("/") or any(part in {".", ".."} for part in parts)):
            return ""
        # Tracking/auth query parameters are never useful recovery metadata.
        fragment = parsed.fragment if re.fullmatch(r"[A-Za-z0-9_-]{0,100}", parsed.fragment) else ""
        return urlunsplit(("https", "github.com", parsed.path, "", fragment))
    except ValueError:
        return ""


def _issue_number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**12 else None


def _job_target(job: dict, service) -> dict:
    """Locate recovery work using recorded routing, never arbitrary payload data."""
    payload = job.get("payload")
    if not isinstance(payload, dict):
        return {}
    target = {}
    case_id = payload.get("case_id")
    case = service.get_case(case_id) if isinstance(case_id, str) and len(case_id) <= 128 else None
    if isinstance(case, dict):
        target["case_id"] = case["id"]
        repository = _repository(case.get("repository"))
        target["discord_url"] = _discord_url(case.get("guild_id"), case.get("channel_id"))
        draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
        number = _issue_number(draft.get("issue_number")) if job.get("kind") == "publish" else None
        number = number or _issue_number(case.get("linked_issue_number"))
        if repository:
            target["repository"] = repository
            target["github_url"] = (
                f"https://github.com/{repository}/issues/{number}" if number
                else _github_url(case.get("github_url"), repository) or f"https://github.com/{repository}/issues"
            )
        if number:
            target["issue_number"] = number
        if job.get("kind") == "publish" and isinstance(payload.get("marker"), str):
            from project_automation_github import GitHubError, marker_comment
            try:
                target["publication_marker"] = marker_comment(payload["marker"])
            except GitHubError:
                pass
    elif job.get("kind") == "event":
        repository = _repository(payload.get("repository"))
        binding = payload.get("_binding") if isinstance(payload.get("_binding"), dict) else {}
        fields = {"issue": "issues_channel_id", "pull": "reviews_channel_id", "commit": "commits_channel_id", "general": "github_channel_id"}
        field = fields.get(payload.get("kind"))
        if repository:
            target["repository"] = repository
            target["github_url"] = _github_url(payload.get("html_url") or payload.get("url"), repository)
        number = _issue_number(payload.get("number"))
        if number:
            target["issue_number"] = number
        if field and repository and binding.get("repository") == repository:
            target["discord_url"] = _discord_url(binding.get("guild_id"), binding.get(field))
            # If a receipt exists, open its exact thread instead of the parent forum.
            store = getattr(service, "store", None)
            if store is not None:
                key = f"{binding.get('guild_id')}:{binding.get('bot_name')}:{binding.get(field)}:{repository}:{payload.get('key')}"
                link = store.get_link("mirror", key)
                if isinstance(link, dict) and all(link.get(name) == binding.get(name) for name in ("guild_id", "bot_name", "repository")):
                    target["discord_url"] = _discord_url(binding.get("guild_id"), link.get("channel_id")) or target["discord_url"]
    return {key: value for key, value in target.items() if value not in (None, "")}


def _job_summary(job: dict, service) -> dict:
    # Outbox payloads can include provider context; expose only recovery locations.
    keys = ("id", "kind", "state", "status", "attempts", "created_at", "updated_at",
            "available_at", "lease_until", "error", "last_error")
    return {**{key: job[key] for key in keys if key in job}, "target": _job_target(job, service)}


def register_project_routes(app, get_service, *, get_bot_names=None, get_character_names=None):
    """Register routes without importing the Discord service or creating a worker."""
    bot_names = get_bot_names or _bot_names
    character_names = get_character_names or _character_names

    def snapshot():
        config = normalize_project_config(runtime_config.get("project_automation"))
        credentials = credential_status(config)
        errors = config_errors(config)
        if not credentials["private_key"]:
            errors.append("The GitHub App private key is not available in the configured environment variable or file.")
        if not credentials["webhook_secret"]:
            errors.append("The GitHub webhook secret is not available in the configured environment variable.")
        service = get_service()
        return {
            "config": config,
            "credentials": credentials,
            "errors": errors,
            "ready": not errors,
            "service": service.status(),
            "choices": {"bots": _names(bot_names), "characters": _names(character_names)},
        }

    @app.route("/project-automation")
    @requires_auth
    def project_automation_page():
        return render_template("project_automation.html", project=snapshot())

    @app.route("/api/project-automation", methods=["GET", "POST"])
    @requires_auth
    @requires_csrf
    def project_automation_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True)
            if isinstance(payload, dict) and set(payload) == {"config"}:
                payload = payload["config"]
            errors = config_input_errors(payload)
            if errors:
                return jsonify({"message": "Settings were not saved.", "errors": errors}), 400
            existing = normalize_project_config(runtime_config.get("project_automation"))
            config = normalize_project_config({**existing, **payload})
            if config["enabled"]:
                errors = config_errors(config)
                credentials = credential_status(config)
                if not credentials["private_key"]:
                    errors.append("Configure the GitHub App private key before enabling.")
                if not credentials["webhook_secret"]:
                    errors.append("Configure the GitHub webhook secret before enabling.")
                if config["bot_name"] not in _names(bot_names):
                    errors.append("Add the dedicated helper bot in Config before enabling.")
                if config["character_name"] and config["character_name"] not in _names(character_names):
                    errors.append("Choose an installed character before enabling.")
                if errors:
                    return jsonify({"message": "Settings were not saved. Complete setup before enabling.", "errors": errors}), 400
            runtime_config.set("project_automation", config)
        return jsonify({"status": "ok", **snapshot()})

    @app.route("/api/project-automation/cases")
    @requires_auth
    def project_automation_cases():
        return jsonify({"cases": get_service().list_cases(limit=_limit())})

    @app.route("/api/project-automation/cases/<case_id>")
    @requires_auth
    def project_automation_case(case_id):
        case = get_service().get_case(case_id)
        if case is None:
            return jsonify({"message": "Feedback case not found."}), 404
        return jsonify({"case": case})

    @app.route("/api/project-automation/jobs")
    @requires_auth
    def project_automation_jobs():
        service = get_service()
        return jsonify({"jobs": [_job_summary(job, service) for job in service.list_jobs(limit=_limit())]})
