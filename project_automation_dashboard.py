"""Dashboard routes for configuration and inspection of project automation."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import jsonify, render_template, request

import runtime_config
from project_automation_config import (
    CHANNEL_PURPOSES, config_errors, config_input_errors, credential_status, normalize_project_config,
)
import project_automation_setup as setup
from project_automation_store import Conflict
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


def _discord_url(guild_id, channel_id, message_id=None) -> str:
    ids = (str(guild_id or ""), str(channel_id or ""))
    if message_id is not None:
        ids += (str(message_id),)
    if all(re.fullmatch(r"[0-9]{1,20}", value) and int(value) > 0 for value in ids):
        return "https://discord.com/channels/" + "/".join(ids)
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
    elif job.get("kind") in {"event", "pr_activity"}:
        repository = _repository(payload.get("repository"))
        binding = payload.get("_binding") if isinstance(payload.get("_binding"), dict) else {}
        store = getattr(service, "store", None)
        activity = job.get("kind") == "pr_activity" or payload.get("kind") == "pr_activity"
        if activity and store is not None:
            prepared = store.get_link("pr_prepared", str(job.get("id")))
            if (isinstance(prepared, dict) and prepared.get("kind") == "pr_activity"
                    and prepared.get("_binding") == binding and prepared.get("repository") == repository
                    and prepared.get("key") == payload.get("key")):
                payload = prepared
        fields = {"issue": "issues_channel_id", "pull": "reviews_channel_id", "pr_activity": "reviews_channel_id", "commit": "commits_channel_id", "general": "github_channel_id"}
        field = "reviews_channel_id" if activity else fields.get(payload.get("kind"))
        if repository:
            target["repository"] = repository
            target["github_url"] = _github_url(payload.get("html_url") or payload.get("url"), repository)
        number = _issue_number(payload.get("number"))
        if number:
            target["issue_number"] = number
        if field and repository and binding.get("repository") == repository:
            target["discord_url"] = _discord_url(binding.get("guild_id"), binding.get(field))
            # If a receipt exists, open its exact thread instead of the parent forum.
            if store is not None:
                key = f"{binding.get('guild_id')}:{binding.get('bot_name')}:{binding.get(field)}:{repository}:{payload.get('key')}"
                if activity:
                    if (not isinstance(payload.get("key"), str) or not all(isinstance(binding.get(name), str)
                            for name in ("guild_id", "bot_name", "reviews_channel_id", "repository"))):
                        return {key: value for key, value in target.items() if value not in (None, "")}
                    from project_automation_activity import activity_receipt_key
                    key = activity_receipt_key(binding, payload)
                link = store.get_link("pr_activity_delivery", key + ":" + str(payload.get("source_version", ""))) if activity else store.get_link("mirror", key)
                if activity and not link:
                    link = store.get_link("pr_activity", key)
                    if not isinstance(link, dict) or link.get("source_version") != payload.get("source_version"):
                        link = store.get_link("mirror", f"{binding.get('guild_id')}:{binding.get('bot_name')}:{binding.get(field)}:{repository}:pull:{number}")
                        if isinstance(link, dict) and all(link.get(name) == binding.get(name) for name in ("guild_id", "bot_name", "repository")):
                            target["discord_url"] = _discord_url(binding.get("guild_id"), link.get("channel_id")) or target["discord_url"]
                        link = None
                if isinstance(link, dict) and all(link.get(name) == binding.get(name) for name in ("guild_id", "bot_name", "repository")):
                    if not activity or link.get("forum_id") == binding.get(field):
                        target["discord_url"] = _discord_url(binding.get("guild_id"), link.get("channel_id"), link.get("message_id") if activity else None) or target["discord_url"]
    elif job.get("kind") in {"pr_sync_page", "pr_sync_check", "pr_sync_missing", "pr_sync_discover"}:
        repository = _repository(payload.get("repository"))
        binding = payload.get("_binding") if isinstance(payload.get("_binding"), dict) else {}
        if repository and repository == binding.get("repository"):
            number = _issue_number(payload.get("number"))
            target.update(repository=repository, issue_number=number,
                          github_url=f"https://github.com/{repository}/pull/{number}" if number else f"https://github.com/{repository}/pulls",
                          discord_url=_discord_url(binding.get("guild_id"), binding.get("reviews_channel_id")))
            store = getattr(service, "store", None)
            if number and store is not None:
                key = f"{binding.get('guild_id')}:{binding.get('bot_name')}:{binding.get('reviews_channel_id')}:{repository}:pull:{number}"
                link = store.get_link("mirror", key)
                if isinstance(link, dict) and all(link.get(name) == binding.get(name) for name in ("guild_id", "bot_name", "repository")):
                    target["discord_url"] = _discord_url(binding.get("guild_id"), link.get("channel_id")) or target["discord_url"]
    return {key: value for key, value in target.items() if value not in (None, "")}


def _job_summary(job: dict, service) -> dict:
    # Outbox payloads can include provider context; expose only recovery locations.
    keys = ("id", "kind", "state", "status", "attempts", "created_at", "updated_at",
            "available_at", "lease_until", "error", "last_error")
    summary = {**{key: job[key] for key in keys if key in job}, "target": _job_target(job, service),
               "dismissed_at": job.get("dismissed_at")}
    if isinstance(job.get("payload"), dict) and job["payload"].get("kind") == "pr_activity":
        summary["activity"] = "pr_activity"
    return summary


def register_project_routes(app, get_service, *, get_bot_names=None, get_character_names=None, get_bot_instances=None):
    """Register routes without importing the Discord service or creating a worker."""
    bot_names = get_bot_names or _bot_names
    character_names = get_character_names or _character_names
    bot_instances = get_bot_instances or (lambda: ())

    def selected_bot(config):
        return next((bot for bot in bot_instances() if getattr(bot, "name", None) == config["bot_name"]), None)

    def on_bot_loop(bot, factory, timeout):
        """Bound a read-only request without borrowing another helper's loop."""
        loop = getattr(getattr(bot, "client", None), "loop", None)
        if not callable(getattr(loop, "is_running", None)) or not loop.is_running():
            return None
        coroutine = factory()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()
            return None
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            return None
        except Exception:
            return None

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
            "channel_purposes": CHANNEL_PURPOSES,
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
            if config["enabled"]:
                # The mapping starts when the operator enables it, even when
                # its helper/worker has not connected yet. Re-saves preserve it.
                from project_automation_activity import activate
                activate(get_service().store, config)
        return jsonify({"status": "ok", **snapshot()})

    @app.route("/api/project-automation/discord-options")
    @requires_auth
    def project_automation_discord_options():
        existing = normalize_project_config(runtime_config.get("project_automation"))
        draft = {key: request.args.get(key, existing[key]) for key in ("bot_name", "guild_id")}
        errors = config_input_errors(draft)
        if errors:
            return jsonify({"message": "Discord choices could not be loaded.", "errors": errors}), 400
        config = normalize_project_config({**existing, **draft})
        if config["bot_name"] not in _names(bot_names):
            result = setup.unavailable_options(config, "Choose a configured helper bot to load its servers and channels.")
            result["status"] = "attention"
            return jsonify(result)
        bot = selected_bot(config)
        result = on_bot_loop(bot, lambda: setup.discord_options(config, bot), setup.OPTIONS_TIMEOUT)
        if result is None:
            result = setup.unavailable_options(config, "The selected helper is offline or did not respond. Start it and retry; saved IDs are preserved.")
        return jsonify(result)

    @app.route("/api/project-automation/check", methods=["POST"])
    @requires_auth
    @requires_csrf
    def project_automation_check():
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and set(payload) == {"config"}:
            payload = payload["config"]
        errors = config_input_errors(payload)
        if errors:
            return jsonify({"message": "Setup was not checked. Fix the draft fields and retry.", "errors": errors}), 400
        existing = normalize_project_config(runtime_config.get("project_automation"))
        config = normalize_project_config({**existing, **payload})
        checks = setup.local_checks(config)
        bot = selected_bot(config)
        result = on_bot_loop(bot, lambda: setup.setup_checks(config, bot), setup.CHECK_TIMEOUT)
        checks.extend(result if result is not None else setup.unavailable_checks("The selected helper is offline or did not respond. Start it and run Check setup again."))
        if config["bot_name"] not in _names(bot_names):
            checks.append(setup.check("helper_configured", "Helper configuration", "attention", "Add the dedicated helper in Config and select it here."))
        if config["character_name"] and config["character_name"] not in _names(character_names):
            checks.append(setup.check("character", "Personality", "attention", "Choose an installed character or use this bot's character."))
        return jsonify({"checks": checks, "saved": False})

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
        try:
            view = request.args.get("view", "recent")
            raw_limit = request.args.get("limit", "100")
            raw_cursor = request.args.get("before_id")
            if (view not in {"recent", "attention", "dismissed"}
                    or not re.fullmatch(r"[0-9]{1,3}", raw_limit)
                    or not 1 <= int(raw_limit) <= 100
                    or (raw_cursor is not None and (not re.fullmatch(r"[0-9]{1,19}", raw_cursor)
                        or not 0 < int(raw_cursor) <= 2**63 - 1))):
                raise ValueError
            service = get_service()
            page = service.list_job_activity(view=view, limit=int(raw_limit),
                                             before_id=int(raw_cursor) if raw_cursor is not None else None)
            return jsonify({"jobs": [_job_summary(job, service) for job in page["jobs"]],
                            "next_before_id": page["next_before_id"]})
        except ValueError:
            return jsonify({"message": "Invalid activity view, cursor, or limit."}), 400
        except Exception:
            return jsonify({"message": "Activity could not be loaded."}), 500

    def activity_mutation(operation):
        try:
            return jsonify(operation())
        except ValueError:
            return jsonify({"message": "Provide valid job IDs and updated_at snapshots."}), 400
        except KeyError:
            return jsonify({"message": "Activity job not found."}), 404
        except Conflict:
            return jsonify({"message": "The failure changed. Refresh activity before trying again."}), 409
        except Exception:
            return jsonify({"message": "Activity could not be updated."}), 500

    @app.route("/api/project-automation/jobs/dismiss", methods=["POST"])
    @requires_auth
    @requires_csrf
    def project_automation_dismiss_jobs():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"jobs"}:
            return jsonify({"message": "Provide a jobs list."}), 400
        return activity_mutation(lambda: {"dismissed": get_service().dismiss_jobs(payload["jobs"])})

    @app.route("/api/project-automation/jobs/<job_id>/restore", methods=["POST"])
    @requires_auth
    @requires_csrf
    def project_automation_restore_job(job_id):
        payload = request.get_json(silent=True)
        if (not re.fullmatch(r"[0-9]{1,19}", job_id) or not 0 < int(job_id) <= 2**63 - 1
                or not isinstance(payload, dict) or set(payload) != {"updated_at"}):
            return jsonify({"message": "Provide a valid job ID and updated_at snapshot."}), 400

        def restore():
            get_service().restore_job(int(job_id), payload["updated_at"])
            return {"restored": True}

        return activity_mutation(restore)
