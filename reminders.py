"""
Discord Pals - Durable Reminder System
Stores reminders and reminder clarifications shared across all bot instances.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import uuid
from typing import Optional

from config import REMINDERS_FILE
from discord_utils import safe_json_load, safe_json_save
from time_utils import utc_iso_to_local_display


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _parse_iso_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean_text(value: str | None, fallback: str = "") -> str:
    if value is None:
        return fallback
    cleaned = " ".join(str(value).split())
    return cleaned or fallback


def _normalize_status(value: str | None, *, default: str) -> str:
    allowed = {"pending", "sent", "skipped", "failed", "cancelled", "completed"}
    if value in allowed:
        return value
    return default


def _normalize_source_type(value: str | None) -> str:
    return "dm" if str(value).strip().lower() == "dm" else "channel"


def _normalize_creation_mode(value: str | None) -> str:
    return "explicit" if str(value).strip().lower() == "explicit" else "auto"


def _reminder_signature(reminder: dict) -> tuple:
    due_iso = reminder.get("due_at_utc", "")
    minute_due = due_iso[:16] if isinstance(due_iso, str) else ""
    return (
        reminder.get("bot_name") or "",
        int(reminder.get("target_user_id") or 0),
        _clean_text(reminder.get("normalized_event")),
        minute_due,
    )


class ReminderManager:
    """Thread-safe reminder persistence shared by bots and dashboard."""

    _GRACE_HOURS = 6
    _CLARIFICATION_TTL_HOURS = 24

    def __init__(self):
        self._lock = threading.RLock()
        self._in_flight: set[str] = set()
        self.reminders: list[dict] = []
        self.pending_clarifications: list[dict] = []
        self._load()

    def _load(self):
        raw = safe_json_load(REMINDERS_FILE, default={})
        if not isinstance(raw, dict):
            raw = {}

        self.reminders = []
        for reminder in raw.get("reminders", []):
            normalized = self._normalize_reminder(reminder)
            if normalized:
                self.reminders.append(normalized)

        self.pending_clarifications = []
        for draft in raw.get("pending_clarifications", []):
            normalized = self._normalize_clarification(draft)
            if normalized:
                self.pending_clarifications.append(normalized)

        self._prune_expired_clarifications_locked()
        self._save_locked()

    def _save_locked(self):
        safe_json_save(REMINDERS_FILE, {
            "reminders": self.reminders,
            "pending_clarifications": self.pending_clarifications,
        })

    def save(self):
        with self._lock:
            self._save_locked()

    def _normalize_reminder(self, reminder: dict | None) -> Optional[dict]:
        if not isinstance(reminder, dict):
            return None

        bot_name = _clean_text(reminder.get("bot_name"))
        event_summary = _clean_text(reminder.get("event_summary"))
        due_at_utc = reminder.get("due_at_utc")
        due_dt = _parse_iso_datetime(due_at_utc)

        try:
            reminder_id = _clean_text(reminder.get("id")) or uuid.uuid4().hex[:12]
            target_user_id = int(reminder.get("target_user_id"))
            source_channel_id = int(reminder.get("source_channel_id"))
        except (TypeError, ValueError):
            return None

        if not bot_name or not event_summary or not due_dt or target_user_id <= 0 or source_channel_id <= 0:
            return None

        timezone_name = _clean_text(reminder.get("timezone_name"), "UTC")

        try:
            timezone_offset_minutes = int(reminder.get("timezone_offset_minutes", 0))
        except (TypeError, ValueError):
            timezone_offset_minutes = 0

        normalized = {
            "id": reminder_id,
            "bot_name": bot_name,
            "target_user_id": target_user_id,
            "target_user_name": _clean_text(reminder.get("target_user_name"), f"User {target_user_id}"),
            "source_type": _normalize_source_type(reminder.get("source_type")),
            "source_channel_id": source_channel_id,
            "source_channel_name": _clean_text(reminder.get("source_channel_name"), "Unknown"),
            "source_guild_id": reminder.get("source_guild_id"),
            "source_guild_name": _clean_text(reminder.get("source_guild_name")),
            "timezone_name": timezone_name,
            "timezone_offset_minutes": timezone_offset_minutes,
            "timezone_source": _clean_text(reminder.get("timezone_source"), "process"),
            "event_summary": event_summary,
            "normalized_event": _clean_text(reminder.get("normalized_event"), event_summary.lower()),
            "due_at_utc": due_dt.isoformat(timespec="seconds"),
            "pre_due_at_utc": None,
            "creation_mode": _normalize_creation_mode(reminder.get("creation_mode")),
            "created_at_utc": _parse_iso_datetime(reminder.get("created_at_utc")).isoformat(timespec="seconds")
            if _parse_iso_datetime(reminder.get("created_at_utc")) else _utc_now_iso(),
            "status": _normalize_status(reminder.get("status"), default="pending"),
            "pre_status": "none",
            "due_status": _normalize_status(reminder.get("due_status"), default="pending"),
            "sent_pre_at_utc": reminder.get("sent_pre_at_utc"),
            "sent_due_at_utc": reminder.get("sent_due_at_utc"),
            "last_error": _clean_text(reminder.get("last_error")),
        }

        pre_due_dt = _parse_iso_datetime(reminder.get("pre_due_at_utc"))
        if pre_due_dt and pre_due_dt < due_dt:
            normalized["pre_due_at_utc"] = pre_due_dt.isoformat(timespec="seconds")
            normalized["pre_status"] = _normalize_status(reminder.get("pre_status"), default="pending")

        if normalized["due_status"] == "sent":
            normalized["status"] = "completed"
        elif normalized["status"] not in {"cancelled", "skipped", "failed", "completed"}:
            normalized["status"] = "pending"

        return normalized

    def _normalize_clarification(self, draft: dict | None) -> Optional[dict]:
        if not isinstance(draft, dict):
            return None

        try:
            target_user_id = int(draft.get("target_user_id"))
            source_channel_id = int(draft.get("source_channel_id"))
        except (TypeError, ValueError):
            return None

        if target_user_id <= 0 or source_channel_id <= 0:
            return None

        bot_name = _clean_text(draft.get("bot_name"))
        if not bot_name:
            return None

        expires_at = _parse_iso_datetime(draft.get("expires_at_utc"))
        if not expires_at:
            expires_at = _utc_now() + timedelta(hours=self._CLARIFICATION_TTL_HOURS)

        return {
            "id": _clean_text(draft.get("id")) or uuid.uuid4().hex[:12],
            "bot_name": bot_name,
            "target_user_id": target_user_id,
            "target_user_name": _clean_text(draft.get("target_user_name"), f"User {target_user_id}"),
            "source_channel_id": source_channel_id,
            "source_channel_name": _clean_text(draft.get("source_channel_name"), "Unknown"),
            "source_guild_id": draft.get("source_guild_id"),
            "source_guild_name": _clean_text(draft.get("source_guild_name")),
            "source_type": _normalize_source_type(draft.get("source_type")),
            "timezone_name": _clean_text(draft.get("timezone_name"), "UTC"),
            "timezone_offset_minutes": int(draft.get("timezone_offset_minutes", 0) or 0),
            "timezone_source": _clean_text(draft.get("timezone_source"), "process"),
            "event_summary": _clean_text(draft.get("event_summary")),
            "normalized_event": _clean_text(draft.get("normalized_event")),
            "creation_mode": _normalize_creation_mode(draft.get("creation_mode")),
            "clarification_prompt": _clean_text(draft.get("clarification_prompt")),
            "created_at_utc": _parse_iso_datetime(draft.get("created_at_utc")).isoformat(timespec="seconds")
            if _parse_iso_datetime(draft.get("created_at_utc")) else _utc_now_iso(),
            "expires_at_utc": expires_at.isoformat(timespec="seconds"),
        }

    def _prune_expired_clarifications_locked(self, now: datetime | None = None):
        now = now or _utc_now()
        self.pending_clarifications = [
            draft for draft in self.pending_clarifications
            if (_parse_iso_datetime(draft.get("expires_at_utc")) or now) > now
        ]

    def create_or_update_reminder(
        self,
        *,
        bot_name: str,
        target_user_id: int,
        target_user_name: str,
        source_type: str,
        source_channel_id: int,
        source_channel_name: str,
        source_guild_id: int | None,
        source_guild_name: str | None,
        timezone_name: str,
        timezone_offset_minutes: int,
        timezone_source: str,
        event_summary: str,
        normalized_event: str,
        due_at_utc: datetime,
        pre_due_at_utc: datetime | None,
        creation_mode: str,
    ) -> tuple[str, dict]:
        """Create or update a pending reminder using the dedup signature."""
        with self._lock:
            reminder = self._normalize_reminder({
                "id": uuid.uuid4().hex[:12],
                "bot_name": bot_name,
                "target_user_id": target_user_id,
                "target_user_name": target_user_name,
                "source_type": source_type,
                "source_channel_id": source_channel_id,
                "source_channel_name": source_channel_name,
                "source_guild_id": source_guild_id,
                "source_guild_name": source_guild_name,
                "timezone_name": timezone_name,
                "timezone_offset_minutes": timezone_offset_minutes,
                "timezone_source": timezone_source,
                "event_summary": event_summary,
                "normalized_event": normalized_event,
                "due_at_utc": due_at_utc.isoformat(timespec="seconds"),
                "pre_due_at_utc": pre_due_at_utc.isoformat(timespec="seconds") if pre_due_at_utc else None,
                "creation_mode": creation_mode,
                "status": "pending",
                "pre_status": "pending" if pre_due_at_utc else "none",
                "due_status": "pending",
                "created_at_utc": _utc_now_iso(),
            })

            if not reminder:
                raise ValueError("Invalid reminder payload")

            signature = _reminder_signature(reminder)
            for existing in self.reminders:
                if existing.get("status") not in {"pending", "failed"}:
                    continue
                if _reminder_signature(existing) != signature:
                    continue

                existing.update({
                    "target_user_name": reminder["target_user_name"],
                    "source_type": reminder["source_type"],
                    "source_channel_id": reminder["source_channel_id"],
                    "source_channel_name": reminder["source_channel_name"],
                    "source_guild_id": reminder["source_guild_id"],
                    "source_guild_name": reminder["source_guild_name"],
                    "timezone_name": reminder["timezone_name"],
                    "timezone_offset_minutes": reminder["timezone_offset_minutes"],
                    "timezone_source": reminder["timezone_source"],
                    "event_summary": reminder["event_summary"],
                    "normalized_event": reminder["normalized_event"],
                    "pre_due_at_utc": reminder["pre_due_at_utc"],
                    "pre_status": reminder["pre_status"],
                    "due_at_utc": reminder["due_at_utc"],
                    "due_status": "pending",
                    "status": "pending",
                    "last_error": "",
                })
                self.clear_pending_clarification(
                    bot_name=bot_name,
                    target_user_id=target_user_id,
                    source_channel_id=source_channel_id,
                )
                self._save_locked()
                return "updated", dict(existing)

            self.reminders.append(reminder)
            self.clear_pending_clarification(
                bot_name=bot_name,
                target_user_id=target_user_id,
                source_channel_id=source_channel_id,
            )
            self._save_locked()
            return "created", dict(reminder)

    def list_reminders(
        self,
        *,
        bot_name: str | None = None,
        user_id: int | None = None,
        status: str | None = None,
    ) -> list[dict]:
        with self._lock:
            results = []
            for reminder in self.reminders:
                if bot_name and reminder.get("bot_name") != bot_name:
                    continue
                if user_id is not None and int(reminder.get("target_user_id") or 0) != int(user_id):
                    continue
                if status and reminder.get("status") != status:
                    continue

                item = dict(reminder)
                item["due_display"] = utc_iso_to_local_display(
                    reminder.get("due_at_utc"),
                    timezone_name=reminder.get("timezone_name"),
                    offset_minutes=reminder.get("timezone_offset_minutes"),
                )
                item["pre_due_display"] = utc_iso_to_local_display(
                    reminder.get("pre_due_at_utc"),
                    timezone_name=reminder.get("timezone_name"),
                    offset_minutes=reminder.get("timezone_offset_minutes"),
                )
                results.append(item)

            results.sort(key=lambda reminder: reminder.get("due_at_utc", ""))
            return results

    def cancel_reminders(
        self,
        reminder_ids: list[str],
        *,
        bot_name: str | None = None,
        target_user_id: int | None = None,
    ) -> int:
        with self._lock:
            id_set = {str(reminder_id).strip() for reminder_id in reminder_ids if str(reminder_id).strip()}
            cancelled = 0

            for reminder in self.reminders:
                if reminder.get("id") not in id_set:
                    continue
                if bot_name and reminder.get("bot_name") != bot_name:
                    continue
                if target_user_id is not None and int(reminder.get("target_user_id") or 0) != int(target_user_id):
                    continue
                if reminder.get("status") not in {"pending", "failed"}:
                    continue

                reminder["status"] = "cancelled"
                if reminder.get("pre_status") == "pending":
                    reminder["pre_status"] = "cancelled"
                if reminder.get("due_status") == "pending":
                    reminder["due_status"] = "cancelled"
                cancelled += 1

            if cancelled:
                self._save_locked()
            return cancelled

    def is_pending(self, reminder_id: str) -> bool:
        """Check whether a reminder is still pending delivery."""
        with self._lock:
            for reminder in self.reminders:
                if reminder.get("id") == reminder_id:
                    return reminder.get("status") == "pending"
        return False

    def create_pending_clarification(
        self,
        *,
        bot_name: str,
        target_user_id: int,
        target_user_name: str,
        source_channel_id: int,
        source_channel_name: str,
        source_guild_id: int | None,
        source_guild_name: str | None,
        source_type: str,
        timezone_name: str,
        timezone_offset_minutes: int,
        timezone_source: str,
        event_summary: str,
        normalized_event: str,
        creation_mode: str,
        clarification_prompt: str,
    ) -> dict:
        with self._lock:
            self.pending_clarifications = [
                draft for draft in self.pending_clarifications
                if not (
                    draft.get("bot_name") == bot_name
                    and int(draft.get("target_user_id") or 0) == int(target_user_id)
                    and int(draft.get("source_channel_id") or 0) == int(source_channel_id)
                )
            ]

            draft = self._normalize_clarification({
                "id": uuid.uuid4().hex[:12],
                "bot_name": bot_name,
                "target_user_id": target_user_id,
                "target_user_name": target_user_name,
                "source_channel_id": source_channel_id,
                "source_channel_name": source_channel_name,
                "source_guild_id": source_guild_id,
                "source_guild_name": source_guild_name,
                "source_type": source_type,
                "timezone_name": timezone_name,
                "timezone_offset_minutes": timezone_offset_minutes,
                "timezone_source": timezone_source,
                "event_summary": event_summary,
                "normalized_event": normalized_event,
                "creation_mode": creation_mode,
                "clarification_prompt": clarification_prompt,
                "created_at_utc": _utc_now_iso(),
                "expires_at_utc": (_utc_now() + timedelta(hours=self._CLARIFICATION_TTL_HOURS)).isoformat(timespec="seconds"),
            })

            if not draft:
                raise ValueError("Invalid clarification payload")

            self.pending_clarifications.append(draft)
            self._save_locked()
            return dict(draft)

    def get_pending_clarification(
        self,
        *,
        bot_name: str,
        target_user_id: int,
        source_channel_id: int,
    ) -> Optional[dict]:
        with self._lock:
            self._prune_expired_clarifications_locked()
            for draft in self.pending_clarifications:
                if (
                    draft.get("bot_name") == bot_name
                    and int(draft.get("target_user_id") or 0) == int(target_user_id)
                    and int(draft.get("source_channel_id") or 0) == int(source_channel_id)
                ):
                    return dict(draft)
            return None

    def clear_pending_clarification(
        self,
        *,
        bot_name: str,
        target_user_id: int,
        source_channel_id: int,
    ) -> bool:
        with self._lock:
            before = len(self.pending_clarifications)
            self.pending_clarifications = [
                draft for draft in self.pending_clarifications
                if not (
                    draft.get("bot_name") == bot_name
                    and int(draft.get("target_user_id") or 0) == int(target_user_id)
                    and int(draft.get("source_channel_id") or 0) == int(source_channel_id)
                )
            ]

            changed = len(self.pending_clarifications) != before
            if changed:
                self._save_locked()
            return changed

    def get_due_deliveries(self, bot_name: str, *, now: datetime | None = None) -> list[dict]:
        """Return pending reminder deliveries for a bot and mark them in-flight."""
        with self._lock:
            now = now or _utc_now()
            grace_deadline = now - timedelta(hours=self._GRACE_HOURS)
            deliveries = []

            for reminder in self.reminders:
                if reminder.get("bot_name") != bot_name:
                    continue
                if reminder.get("status") != "pending":
                    continue
                reminder_id = reminder.get("id")
                if reminder_id in self._in_flight:
                    continue

                due_dt = _parse_iso_datetime(reminder.get("due_at_utc"))
                pre_dt = _parse_iso_datetime(reminder.get("pre_due_at_utc"))
                if not due_dt:
                    continue

                if due_dt <= grace_deadline and reminder.get("due_status") == "pending":
                    reminder["due_status"] = "skipped"
                    if reminder.get("pre_status") == "pending":
                        reminder["pre_status"] = "skipped"
                    reminder["status"] = "skipped"
                    continue

                if due_dt <= now and reminder.get("due_status") == "pending":
                    if reminder.get("pre_status") == "pending":
                        reminder["pre_status"] = "skipped"
                    self._in_flight.add(reminder_id)
                    deliveries.append({"stage": "due", "reminder": dict(reminder)})
                    continue

                if (
                    pre_dt
                    and pre_dt <= now < due_dt
                    and reminder.get("pre_status") == "pending"
                ):
                    self._in_flight.add(reminder_id)
                    deliveries.append({"stage": "pre", "reminder": dict(reminder)})

            if deliveries:
                self._save_locked()

            return deliveries

    def mark_delivery_result(
        self,
        reminder_id: str,
        *,
        stage: str,
        success: bool,
        error_message: str = "",
        sent_at: datetime | None = None,
    ):
        with self._lock:
            sent_at = sent_at or _utc_now()
            for reminder in self.reminders:
                if reminder.get("id") != reminder_id:
                    continue

                if stage == "pre":
                    reminder["pre_status"] = "sent" if success else "failed"
                    if success:
                        reminder["sent_pre_at_utc"] = sent_at.isoformat(timespec="seconds")
                else:
                    reminder["due_status"] = "sent" if success else "failed"
                    if success:
                        reminder["sent_due_at_utc"] = sent_at.isoformat(timespec="seconds")

                reminder["last_error"] = "" if success else _clean_text(error_message)

                if reminder.get("due_status") == "sent":
                    reminder["status"] = "completed"
                elif stage == "due" and not success:
                    reminder["status"] = "failed"
                elif reminder.get("status") not in {"cancelled", "skipped"}:
                    reminder["status"] = "pending"

                break

            self._in_flight.discard(reminder_id)
            self._save_locked()

    def mark_skipped(self, reminder_id: str, *, stage: str, reason: str = ""):
        """Mark a reminder delivery as intentionally skipped."""
        with self._lock:
            for reminder in self.reminders:
                if reminder.get("id") != reminder_id:
                    continue

                if stage == "pre":
                    reminder["pre_status"] = "skipped"
                else:
                    reminder["due_status"] = "skipped"
                    reminder["status"] = "skipped"

                reminder["last_error"] = _clean_text(reason)
                break

            self._in_flight.discard(reminder_id)
            self._save_locked()


reminder_manager = ReminderManager()
