"""
Discord Pals - Timezone Utilities
Shared timezone resolution and per-user timezone persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import threading
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from config import USER_TIMEZONES_FILE
from discord_utils import safe_json_load, safe_json_save
import runtime_config


def normalize_timezone_name(value: str | None) -> Optional[str]:
    """Validate and normalize an IANA timezone name."""
    if value is None:
        return None

    candidate = str(value).strip()
    if not candidate:
        return None

    try:
        zone = ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return None

    return getattr(zone, "key", candidate)


def _format_utc_offset_label(offset: timedelta | None) -> str:
    """Format a timedelta offset as a UTC label."""
    total_seconds = int((offset or timedelta()).total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


@lru_cache(maxsize=1)
def get_timezone_options() -> list[dict[str, str]]:
    """Return a cached list of valid IANA timezone options for UI pickers."""
    names = set(available_timezones())
    names.add("UTC")
    now_utc = datetime.now(timezone.utc)
    options: list[dict[str, str]] = []
    seen: set[str] = set()

    for name in sorted(names):
        normalized = normalize_timezone_name(name)
        if not normalized or normalized in seen:
            continue

        try:
            local_now = now_utc.astimezone(ZoneInfo(normalized))
        except ZoneInfoNotFoundError:
            continue

        seen.add(normalized)
        options.append({
            "value": normalized,
            "label": f"{normalized} ({_format_utc_offset_label(local_now.utcoffset())})",
        })

    return options


def search_timezone_options(query: str | None, *, limit: int = 25) -> list[dict[str, str]]:
    """Search the cached timezone list for dashboard and slash-command pickers."""
    options = get_timezone_options()
    if limit <= 0:
        return []

    needle = (query or "").strip().lower()
    if not needle:
        return options[:limit]

    startswith_matches = []
    contains_matches = []

    for option in options:
        value = option["value"].lower()
        label = option["label"].lower()
        if value.startswith(needle) or label.startswith(needle):
            startswith_matches.append(option)
        elif needle in value or needle in label:
            contains_matches.append(option)

    return (startswith_matches + contains_matches)[:limit]


def _best_effort_timezone_name(now: datetime) -> str:
    """Return a readable timezone identifier for the active process timezone."""
    tzinfo = now.tzinfo
    zone_key = getattr(tzinfo, "key", None)
    if zone_key:
        return zone_key

    label = now.strftime("%Z")
    if label:
        return label

    offset = now.strftime("%z")
    if offset and len(offset) == 5:
        return f"UTC{offset[:3]}:{offset[3:]}"
    return "UTC"


def _tzinfo_from_name_or_offset(name: str | None, offset_minutes: int | None):
    """Build a tzinfo from a saved name or fallback offset."""
    normalized_name = normalize_timezone_name(name)
    if normalized_name:
        return ZoneInfo(normalized_name)

    try:
        minutes = int(offset_minutes)
    except (TypeError, ValueError):
        minutes = 0

    offset = timedelta(minutes=minutes)
    label = name or f"UTC{offset}"
    return timezone(offset, label)


class TimezoneManager:
    """Persist per-user timezone overrides."""

    def __init__(self):
        self._lock = threading.RLock()
        self._user_timezones: dict[str, str] = {}
        self._load()

    def _load(self):
        data = safe_json_load(USER_TIMEZONES_FILE, default={})
        normalized = {}

        if isinstance(data, dict):
            for user_id, timezone_name in data.items():
                normalized_name = normalize_timezone_name(timezone_name)
                if normalized_name:
                    normalized[str(user_id)] = normalized_name

        self._user_timezones = normalized

    def _save(self):
        safe_json_save(USER_TIMEZONES_FILE, self._user_timezones)

    def get_user_timezone(self, user_id: int | str | None) -> Optional[str]:
        if user_id is None:
            return None
        with self._lock:
            return self._user_timezones.get(str(user_id))

    def set_user_timezone(self, user_id: int | str, timezone_name: str) -> str:
        normalized = normalize_timezone_name(timezone_name)
        if not normalized:
            raise ValueError("Invalid IANA timezone")

        with self._lock:
            self._user_timezones[str(user_id)] = normalized
            self._save()

        return normalized

    def clear_user_timezone(self, user_id: int | str) -> bool:
        with self._lock:
            removed = self._user_timezones.pop(str(user_id), None)
            if removed is not None:
                self._save()
                return True
        return False

    def get_all_user_timezones(self) -> dict[str, str]:
        with self._lock:
            return dict(self._user_timezones)


def get_bot_timezone(bot_name: str | None) -> Optional[str]:
    """Return a bot-level timezone override from runtime config."""
    return runtime_config.get_bot_timezone(bot_name)


def set_bot_timezone(bot_name: str, timezone_name: str | None) -> Optional[str]:
    """Persist a bot-level timezone override in runtime config."""
    normalized = normalize_timezone_name(timezone_name) if timezone_name else None
    runtime_config.set_bot_timezone(bot_name, normalized)
    return normalized


def get_timezone_context(
    *,
    user_id: int | str | None = None,
    bot_name: str | None = None
) -> dict:
    """Resolve the effective timezone context for a request."""
    user_timezone = timezone_manager.get_user_timezone(user_id)
    if user_timezone:
        tzinfo = ZoneInfo(user_timezone)
        now = datetime.now(tzinfo)
        return {
            "timezone_name": user_timezone,
            "timezone_source": "user",
            "offset_minutes": int(now.utcoffset().total_seconds() // 60),
            "tzinfo": tzinfo,
        }

    bot_timezone = get_bot_timezone(bot_name)
    if bot_timezone:
        tzinfo = ZoneInfo(bot_timezone)
        now = datetime.now(tzinfo)
        return {
            "timezone_name": bot_timezone,
            "timezone_source": "bot",
            "offset_minutes": int(now.utcoffset().total_seconds() // 60),
            "tzinfo": tzinfo,
        }

    now = datetime.now().astimezone()
    return {
        "timezone_name": _best_effort_timezone_name(now),
        "timezone_source": "process",
        "offset_minutes": int((now.utcoffset() or timedelta()).total_seconds() // 60),
        "tzinfo": now.tzinfo,
    }


def get_context_now(
    *,
    user_id: int | str | None = None,
    bot_name: str | None = None
) -> datetime:
    """Return the current aware datetime in the resolved context timezone."""
    context = get_timezone_context(user_id=user_id, bot_name=bot_name)
    return datetime.now(context["tzinfo"])


def local_naive_iso_to_utc(
    local_iso: str,
    *,
    timezone_name: str | None,
    offset_minutes: int | None = None
) -> datetime:
    """Convert a local ISO timestamp without offset into UTC."""
    if not isinstance(local_iso, str) or not local_iso.strip():
        raise ValueError("Missing local timestamp")

    local_dt = datetime.fromisoformat(local_iso.strip())
    if local_dt.tzinfo is None:
        tzinfo = _tzinfo_from_name_or_offset(timezone_name, offset_minutes)
        local_dt = local_dt.replace(tzinfo=tzinfo)

    return local_dt.astimezone(timezone.utc)


def utc_iso_to_local_display(
    utc_iso: str,
    *,
    timezone_name: str | None,
    offset_minutes: int | None = None
) -> str:
    """Render a stored UTC timestamp in the reminder's timezone."""
    if not utc_iso:
        return ""

    utc_dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)

    tzinfo = _tzinfo_from_name_or_offset(timezone_name, offset_minutes)
    local_dt = utc_dt.astimezone(tzinfo)
    label = timezone_name or local_dt.strftime("%Z") or "local time"
    return (
        f"{local_dt.strftime('%Y-%m-%d')} "
        f"{local_dt.strftime('%I:%M %p').lstrip('0') or '12:00 AM'} "
        f"({label})"
    )


timezone_manager = TimezoneManager()
