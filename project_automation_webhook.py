"""Signed, bounded GitHub webhook ingress for the durable project outbox."""

from __future__ import annotations

import json
import os
import re

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from project_automation_config import CHANNEL_FIELDS
from project_automation_github import (
    GitHubError, normalize_event, validate_repository, validate_webhook_signature,
)


MAX_WEBHOOK_BYTES = 1024 * 1024
_DELIVERY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_EVENTS = {"issues", "pull_request", "issue_comment", "push", "release", "workflow_run"}
_BINDING_FIELDS = ("repository", "bot_name", "guild_id") + CHANNEL_FIELDS


def webhook_binding(config: dict) -> dict[str, str]:
    """Snapshot only routing fields; queued work must match this before sending."""
    values = {key: config.get(key, "") for key in _BINDING_FIELDS}
    if any(not isinstance(value, str) or len(value) > 256 for value in values.values()):
        raise ValueError("Invalid project webhook routing configuration")
    return values


def _reject_constant(_value):
    raise ValueError("Nonstandard JSON constant")


def register_github_webhook(app, get_service) -> None:
    """Register endpoint ``project_github_webhook`` at POST /webhooks/github.

    The dashboard exempts this single endpoint from session login; the exact
    request bytes are authenticated with the configured GitHub webhook secret.
    No model call or remote publication takes place in the HTTP request.
    """
    def receive():
        try:
            service = get_service()
            config = dict(service.settings())
            if config.get("enabled") is not True:
                return jsonify(status="unavailable"), 503
            repository = validate_repository(config.get("repository"))
            binding = webhook_binding(config)
            env_name = config.get("github_webhook_secret_env", "PROJECT_GITHUB_WEBHOOK_SECRET")
            if not isinstance(env_name, str) or not _ENV_NAME.fullmatch(env_name):
                return jsonify(status="unavailable"), 503
            secret = os.environ.get(env_name, "")
            if not secret:
                return jsonify(status="unavailable"), 503
        except (GitHubError, TypeError, ValueError, AttributeError):
            return jsonify(status="unavailable"), 503
        except Exception:
            return jsonify(status="unavailable"), 503

        delivery = request.headers.get("X-GitHub-Delivery", "")
        if not _DELIVERY.fullmatch(delivery):
            return jsonify(status="invalid"), 400
        length = request.content_length if request.environ.get("CONTENT_LENGTH") else None
        if length is not None and length > MAX_WEBHOOK_BYTES:
            return jsonify(status="too_large"), 413
        try:
            # Without a declared length, Werkzeug's safe stream may be empty.
            # The raw WSGI stream is still read with an explicit hard bound.
            stream = request.stream if length is not None else request.input_stream
            body = stream.read(MAX_WEBHOOK_BYTES + 1)
        except RequestEntityTooLarge:
            return jsonify(status="too_large"), 413
        except (BadRequest, OSError, ValueError):
            return jsonify(status="invalid"), 400
        if len(body) > MAX_WEBHOOK_BYTES:
            return jsonify(status="too_large"), 413
        if not validate_webhook_signature(body, request.headers.get("X-Hub-Signature-256", ""), secret):
            return jsonify(status="unauthorized"), 401
        try:
            payload = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError):
            return jsonify(status="invalid"), 400
        if not isinstance(payload, dict):
            return jsonify(status="invalid"), 400

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return jsonify(status="ok"), 200
        if event not in _EVENTS:
            return jsonify(status="ignored"), 202
        normalized = normalize_event(event, payload, repository)
        if normalized is None:
            return jsonify(status="ignored"), 202
        normalized["_binding"] = binding
        try:
            # SQLite's unique key makes simultaneous GitHub redeliveries one job.
            # Conflicting payloads never replace previously accepted work.
            service.store.enqueue("event", normalized, key=f"github:{delivery}")
        except Exception:
            return jsonify(status="unavailable"), 503
        return jsonify(status="accepted"), 202

    app.add_url_rule("/webhooks/github", endpoint="project_github_webhook", view_func=receive,
                     methods=["POST"])
