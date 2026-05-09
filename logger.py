"""
Discord Pals - Logging Utilities
Local diagnostic logging with dashboard buffering, JSONL file persistence, and redaction.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Log levels
QUIET = 0   # Only errors
NORMAL = 1  # Errors + important events
VERBOSE = 2 # Detailed operational logs
DIAGNOSTIC = 3  # High-volume request lifecycle traces

# Set this to control terminal verbosity (QUIET=errors only, NORMAL=+events, VERBOSE=+debug)
# NOTE: Dashboard and file logs receive all logs regardless of this setting.
LOG_LEVEL = NORMAL

MAX_LOG_BUFFER = 1000
LOG_DIR = os.path.join("bot_data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "discord-pals.log")
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUPS = 5
FILE_LOGGING_ENABLED = True

_LEVEL_NAMES = {
    QUIET: "error",
    NORMAL: "info",
    VERBOSE: "debug",
    DIAGNOSTIC: "diagnostic",
}


class Colors:
    """ANSI color codes for terminal output."""
    OK = '\033[92m'      # Green
    WARN = '\033[93m'    # Yellow
    FAIL = '\033[91m'    # Red
    INFO = '\033[94m'    # Blue
    DIM = '\033[90m'     # Gray
    BOLD = '\033[1m'
    END = '\033[0m'


def _timestamp():
    """Get current time as HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def _timestamp_iso():
    """Get current timestamp for file logs."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# In-memory log buffer for dashboard
_log_buffer = []
_log_sequence = 0
_log_reset_marker = 0
_log_lock = threading.Lock()
_registered_secrets: set[str] = set()

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"key-[A-Za-z0-9_-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"(?i)(authorization|api[_-]?key|token|password|secret)(\s*[:=]\s*)([^\s,'\"]{8,})"),
    re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,8}\.[A-Za-z0-9_-]{27,45}"),
)


def configure_file_logging(
    *,
    enabled: bool | None = None,
    log_dir: str | None = None,
    max_bytes: int | None = None,
    backups: int | None = None,
) -> None:
    """Configure local JSONL file logging."""
    global FILE_LOGGING_ENABLED, LOG_DIR, LOG_FILE, LOG_FILE_MAX_BYTES, LOG_FILE_BACKUPS
    if enabled is not None:
        FILE_LOGGING_ENABLED = bool(enabled)
    if log_dir:
        LOG_DIR = str(log_dir)
        LOG_FILE = os.path.join(LOG_DIR, "discord-pals.log")
    if max_bytes is not None:
        LOG_FILE_MAX_BYTES = max(1024, int(max_bytes))
    if backups is not None:
        LOG_FILE_BACKUPS = max(1, int(backups))


def new_request_id() -> str:
    """Return a compact correlation ID for one request lifecycle."""
    return secrets.token_hex(4)


def register_secret(value: str | None) -> None:
    """Register a known secret value for redaction before any log sink sees it."""
    if value is None:
        return
    value = str(value)
    if len(value) < 8 or value == "not-needed":
        return
    with _log_lock:
        _registered_secrets.add(value)


def register_secrets(values) -> None:
    """Register multiple secret values for redaction."""
    for value in values or []:
        register_secret(value)


def redact(value: Any) -> Any:
    """Redact secrets from strings, lists, and dictionaries."""
    if isinstance(value, str):
        redacted = value
        with _log_lock:
            secrets_snapshot = tuple(_registered_secrets)
        for secret in secrets_snapshot:
            redacted = redacted.replace(secret, "[REDACTED]")
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(_redact_match, redacted)
        return redacted
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _redact_match(match: re.Match) -> str:
    if match.lastindex and match.lastindex >= 3:
        return f"{match.group(1)}{match.group(2)}[REDACTED]"
    if match.group(0).lower().startswith("bearer "):
        return "Bearer [REDACTED]"
    return "[REDACTED]"


def preview(text: Any, limit: int = 80) -> str:
    """Return a redacted short preview for explicitly opt-in content diagnostics."""
    cleaned = " ".join(str(text or "").split())
    cleaned = redact(cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _field_matches(entry: dict, key: str, expected: str) -> bool:
    if not expected:
        return True
    value = entry.get(key)
    if value is None and isinstance(entry.get("fields"), dict):
        value = entry["fields"].get(key)
    return str(value or "").lower() == str(expected).lower()


def get_logs(limit: int = 100, **filters) -> list:
    """Get recent logs for dashboard."""
    with _log_lock:
        entries = [dict(entry) for entry in _log_buffer]

    entries = _apply_filters(entries, filters)
    return entries[-limit:]


def get_logs_after(after_seq: int = 0, limit: int = 100, **filters) -> dict:
    """Get log entries after a cursor, with reset detection for clear/rollover."""
    try:
        after_seq = int(after_seq or 0)
    except (TypeError, ValueError):
        after_seq = 0

    with _log_lock:
        oldest_seq = _log_buffer[0]["seq"] if _log_buffer else None
        latest_seq = _log_buffer[-1]["seq"] if _log_buffer else max(after_seq, _log_reset_marker)
        reset = bool(
            after_seq and (
                after_seq <= _log_reset_marker or
                (oldest_seq is not None and after_seq < oldest_seq)
            )
        )

        if reset:
            entries = list(_log_buffer)
        else:
            entries = [entry for entry in _log_buffer if entry["seq"] > after_seq]

    entries = _apply_filters([dict(entry) for entry in entries], filters)[-limit:]
    return {
        "entries": entries,
        "cursor": latest_seq,
        "reset": reset,
    }


def _apply_filters(entries: list[dict], filters: dict) -> list[dict]:
    level = filters.get("level")
    bot = filters.get("bot")
    req_id = filters.get("req_id") or filters.get("request_id")
    component = filters.get("component")
    event = filters.get("event")
    search = str(filters.get("search") or "").strip().lower()

    filtered = []
    for entry in entries:
        if level and not _field_matches(entry, "level", level):
            continue
        if bot and not _field_matches(entry, "bot", bot):
            continue
        if req_id and not _field_matches(entry, "req_id", req_id):
            continue
        if component and not _field_matches(entry, "component", component):
            continue
        if event and not _field_matches(entry, "event", event):
            continue
        if search:
            haystack = " ".join(
                str(part or "") for part in (
                    entry.get("message"),
                    entry.get("bot"),
                    entry.get("req_id"),
                    entry.get("component"),
                    entry.get("event"),
                    json.dumps(entry.get("fields", {}), sort_keys=True, default=str),
                )
            ).lower()
            if search not in haystack:
                continue
        filtered.append(entry)
    return filtered


def clear_logs():
    """Clear the in-memory log buffer."""
    global _log_buffer, _log_reset_marker
    with _log_lock:
        _log_buffer = []
        _log_reset_marker = _log_sequence


def _level_for_icon(icon: str, numeric_level: int) -> str:
    if icon == "✓":
        return "ok"
    if icon == "⚠":
        return "warn"
    if icon == "✗":
        return "error"
    return _LEVEL_NAMES.get(numeric_level, "info")


def _write_file_log(entry: dict) -> None:
    if not FILE_LOGGING_ENABLED:
        return

    path = Path(LOG_FILE)
    try:
        line = json.dumps(entry, ensure_ascii=True, default=str, sort_keys=True) + "\n"
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size >= LOG_FILE_MAX_BYTES:
                _rotate_logs(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # Logging must never break bot runtime.
        return


def _rotate_logs(path: Path) -> None:
    for index in range(LOG_FILE_BACKUPS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            try:
                if target.exists():
                    target.unlink()
                source.rename(target)
            except OSError:
                pass
    try:
        first = path.with_name(f"{path.name}.1")
        if first.exists():
            first.unlink()
        path.rename(first)
    except OSError:
        pass


def _normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    for key, value in fields.items():
        if value is None:
            continue
        normalized[str(key)] = redact(value)
    return normalized


def _log(
    icon: str,
    color: str,
    msg: str,
    bot_name: str = None,
    level: int = NORMAL,
    *,
    req_id: str | None = None,
    request_id: str | None = None,
    component: str | None = None,
    event: str | None = None,
    **fields,
):
    """Internal logging function."""
    global _log_sequence
    ts_str = _timestamp()
    redacted_msg = redact(str(msg))
    req_id = req_id or request_id
    redacted_bot = redact(bot_name) if bot_name else None
    redacted_req_id = redact(req_id) if req_id else None
    redacted_component = redact(component) if component else None
    redacted_event = redact(event) if event else None
    normalized_fields = _normalize_fields(fields)

    with _log_lock:
        _log_sequence += 1
        log_entry = {
            "seq": _log_sequence,
            "time": ts_str,
            "ts": _timestamp_iso(),
            "icon": icon,
            "level": _level_for_icon(icon, level),
            "bot": redacted_bot,
            "message": redacted_msg,
            "req_id": redacted_req_id,
            "component": redacted_component,
            "event": redacted_event,
            "fields": normalized_fields,
        }
        for key in ("channel_id", "guild_id", "user_id", "message_id", "tier", "model"):
            if key in normalized_fields:
                log_entry[key] = normalized_fields[key]
        _log_buffer.append(log_entry)
        if len(_log_buffer) > MAX_LOG_BUFFER:
            del _log_buffer[: len(_log_buffer) - MAX_LOG_BUFFER]
        file_entry = dict(log_entry)

    _write_file_log(file_entry)

    # Only print to terminal if level allows
    if level <= LOG_LEVEL:
        ts = f"{Colors.DIM}{ts_str}{Colors.END}"
        prefix_parts = []
        if bot_name:
            prefix_parts.append(f"[{bot_name}]")
        if req_id:
            prefix_parts.append(f"[req:{req_id}]")
        if component:
            prefix_parts.append(f"[{component}]")
        prefix = "".join(prefix_parts) + " " if prefix_parts else ""
        print(f"{ts} {color}{icon}{Colors.END} {prefix}{redacted_msg}")


# Public logging functions
def ok(msg: str, bot: str = None, **fields):
    """Log success message."""
    _log("✓", Colors.OK, msg, bot, NORMAL, **fields)


def warn(msg: str, bot: str = None, **fields):
    """Log warning message."""
    _log("⚠", Colors.WARN, msg, bot, NORMAL, **fields)


def error(msg: str, bot: str = None, **fields):
    """Log error message."""
    _log("✗", Colors.FAIL, msg, bot, QUIET, **fields)


def info(msg: str, bot: str = None, **fields):
    """Log info message."""
    _log("ℹ", Colors.INFO, msg, bot, NORMAL, **fields)


def debug(msg: str, bot: str = None, **fields):
    """Log debug message."""
    _log("•", Colors.DIM, msg, bot, VERBOSE, **fields)


def diagnostic(msg: str, bot: str = None, **fields):
    """Log high-volume diagnostics to dashboard/file with optional terminal output."""
    _log("•", Colors.DIM, msg, bot, DIAGNOSTIC, **fields)


def startup(msg: str):
    """Log startup message (always shown)."""
    print(f"{Colors.BOLD}{redact(msg)}{Colors.END}")


def online(msg: str, bot: str = None, **fields):
    """Log bot online status (always shown and persisted)."""
    _log("●", Colors.OK, msg, bot, NORMAL, component=fields.pop("component", "lifecycle"), **fields)


def divider():
    """Print a divider line."""
    print(f"{Colors.DIM}{'─' * 50}{Colors.END}")
