"""Health signals of the Project helper, computed from facts that the service reads cheaply.

Each signal is a dict with a stable key, a name, a display value, a level ("ok", "warn" or
"alert") and one sentence that says what to do when the level is not ok. A value above a
warn threshold is a warning and a value above an alert threshold is an alert.
"""

from __future__ import annotations

from dataclasses import dataclass


HEARTBEAT_WARN_SECONDS = 2 * 60
HEARTBEAT_ALERT_SECONDS = 10 * 60
QUEUE_LAG_WARN_SECONDS = 15 * 60
QUEUE_LAG_ALERT_SECONDS = 60 * 60
PENDING_WARN_JOBS = 2_000
PENDING_ALERT_JOBS = 10_000
SYNC_AGE_WARN_SECONDS = 60 * 60
SYNC_AGE_ALERT_SECONDS = 3 * 60 * 60
DATABASE_WARN_BYTES = 512 * 1024 * 1024
ALERT_REPEAT_SECONDS = 24 * 60 * 60
DASHBOARD = "Dashboard → Project"


@dataclass(frozen=True)
class HealthFacts:
    """The facts the signals are computed from.

    pause_reason is None while automation runs, otherwise "disabled", "setup" or "global_pause".
    Ages are in seconds. queue_lag and sync_age count from when automation last started running
    if that is later, so time spent stopped does not raise them; queue_wait is the oldest due
    job's whole wait and last_sync_at the time of the last completed sync. A queue_lag or
    queue_wait of None means no pending job is due, and a sync_age or last_sync_at of None
    means no completed sync is recorded.
    """

    pause_reason: str | None
    bot_online: bool
    worker_running: bool
    heartbeat_age: float | None
    pending: int
    held: int
    failed: int
    queue_lag: float | None
    sync_age: float | None
    database_bytes: int
    alerts_enabled: bool
    operators: int
    restarts: int = 0
    restart_reason: str | None = None
    queue_wait: float | None = None
    last_sync_at: float | None = None


PAUSED = {
    "disabled": ("Paused", "The worker, helper bot, queue lag and sync checks start again when you enable automation."),
    "global_pause": ("Globally paused", "The global pause in Config also stops automation, and its checks return when the pause ends."),
    "setup": ("Setup needed", "Finish Helper setup, because automation and its checks stay paused until setup is complete."),
}


def _signal(key, name, value, level, sentences):
    return {"key": key, "name": name, "value": value, "level": level, "sentence": sentences[level]}


def _level(value, warn=None, alert=None):
    if alert is not None and value > alert:
        return "alert"
    if warn is not None and value > warn:
        return "warn"
    return "ok"


def _count(value, unit):
    return f"{value:,} {unit}{'' if value == 1 else 's'}"


def _duration(seconds):
    seconds = max(0, int(seconds))
    for unit, size, limit in (("second", 1, 120), ("minute", 60, 2 * 3600), ("hour", 3600, 2 * 86400)):
        if seconds < limit:
            return _count(seconds // size, unit)
    return _count(seconds // 86400, "day")


def health_signals(facts: HealthFacts) -> list[dict]:
    """Return the signals for facts in display order.

    While automation is paused, one informational signal names the pause instead of the
    worker, heartbeat, helper bot, queue lag and sync signals, which only measure a running
    helper. The heartbeat needs a live worker task, and the worker needs the helper bot online.
    """
    signals = []
    if facts.pause_reason is not None:
        value, sentence = PAUSED[facts.pause_reason]
        signals.append({"key": "automation", "name": "Automation", "value": value, "level": "ok", "sentence": sentence})
    else:
        if facts.bot_online:
            value = "Running" if facts.worker_running else "Stopped"
            if facts.restarts:
                value += f", restarted {_count(facts.restarts, 'time')}"
            signals.append(_signal("worker", "Worker", value, "ok" if facts.worker_running else "alert", {
                "ok": (f"The watchdog restarted it after {facts.restart_reason}, so check the logs for the cause."
                       if facts.restarts else "The worker is processing jobs."),
                "alert": "Check the logs for the error, and the watchdog starts the worker again within a minute."}))
        if facts.worker_running and facts.heartbeat_age is not None:
            signals.append(_signal("heartbeat", "Worker heartbeat", _duration(facts.heartbeat_age) + " ago",
                                   _level(facts.heartbeat_age, HEARTBEAT_WARN_SECONDS, HEARTBEAT_ALERT_SECONDS), {
                                       "ok": "The worker loop is passing normally.",
                                       "warn": "A long job or a slow database call holds the worker, and this clears when it ends.",
                                       "alert": "Check the logs and restart Discord Pals if the worker loop stays blocked."}))
        signals.append(_signal("helper_bot", "Helper bot", "Online" if facts.bot_online else "Offline",
                               "ok" if facts.bot_online else "alert", {
                                   "ok": "The helper bot is connected.",
                                   "alert": "Start the helper bot or fix its token in Config, because Project work waits for it."}))
        lag = facts.queue_lag
        signals.append(_signal("queue_lag", "Queue lag", "Nothing due" if lag is None else _duration(lag),
                               "ok" if lag is None else _level(lag, QUEUE_LAG_WARN_SECONDS, QUEUE_LAG_ALERT_SECONDS), {
                                   "ok": "Due jobs start on time.",
                                   "warn": "Jobs wait longer than usual, so check Delivery activity for slow or failing work.",
                                   "alert": "Check the helper bot and the worker, then Delivery activity for the oldest waiting work."}))
        age = facts.sync_age
        signals.append(_signal("sync", "Time since a sync completed", "Not recorded" if age is None else _duration(age),
                               "ok" if age is None else _level(age, SYNC_AGE_WARN_SECONDS, SYNC_AGE_ALERT_SECONDS), {
                                   "ok": "Reconciliation completes on schedule.",
                                   "warn": "Reconciliation runs late, so check Delivery activity for failing sync jobs.",
                                   "alert": "Check Delivery activity and the logs for the sync jobs that keep a cycle open."}))
    signals.append(_signal("pending", "Pending jobs", f"{facts.pending:,}",
                           _level(facts.pending, PENDING_WARN_JOBS, PENDING_ALERT_JOBS), {
                               "ok": "The queue is short.",
                               "warn": "The queue grows, so check Delivery activity for failures and let it drain.",
                               "alert": "Check the worker, or use Clear history and queue under Project data."}))
    signals.append(_signal("held", "Held jobs", f"{facts.held:,}", "warn" if facts.held else "ok", {
        "ok": "No delivery waits for a check.",
        "warn": "Check each one under Delivery activity → Needs attention, because its public write may exist."}))
    signals.append(_signal("failed", "Failed jobs", f"{facts.failed:,}", "warn" if facts.failed else "ok", {
        "ok": "No failed job needs review.",
        "warn": "Review them under Delivery activity → Needs attention, then retry or dismiss them."}))
    signals.append(_signal("database", "Database size", f"{facts.database_bytes / (1024 * 1024):,.0f} MB",
                           _level(facts.database_bytes, DATABASE_WARN_BYTES), {
                               "ok": "The database file is within its expected size.",
                               "warn": "Lower Job history or use Clear history and queue so that compaction can shrink the file."}))
    if facts.alerts_enabled and not facts.operators:
        signals.append({"key": "alerts", "name": "Operator alerts", "value": "No operators", "level": "ok",
                        "sentence": "Add Discord user IDs under Config → Operators to get alert DMs."})
    return signals


def _recovered(item, facts, record):
    """Return whether the signal of an open alert recovered, not only reads OK.

    Queue lag and sync age count from when automation last started running, so a restart or
    a resumed pause makes them read OK before anything recovered. A sync alert resolves once a
    sync completed after its DM, and a queue lag alert once the oldest due job's whole wait is
    short again.
    """
    if item["level"] != "ok":
        return False
    if item["key"] == "sync":
        return facts.last_sync_at is not None and facts.last_sync_at > record["sent_at"]
    if item["key"] == "queue_lag":
        return facts.queue_wait is None or facts.queue_wait <= QUEUE_LAG_WARN_SECONDS
    return True


def due_alerts(facts, records, now, *, new_restart_reason=None):
    """Return the operator messages due now as (key, kind, text) tuples, kind "alert", "resolved" or "restart".

    records maps a signal key to {"sent_at": when its last alert DM was attempted, "open":
    whether an alert DM arrived that no resolved DM has followed}. An alert is due when no
    open alert DM exists and the last attempt is ALERT_REPEAT_SECONDS old, so a signal that
    stays in alert is not repeated. A resolved DM is due when the signal of an open alert
    recovered. new_restart_reason names a watchdog restart that no message reported yet, and
    its message follows the same daily limit.
    """
    due = []
    for item in health_signals(facts):
        record = records.get(item["key"])
        if item["level"] == "alert" and not (record and (record.get("open") or now - record["sent_at"] < ALERT_REPEAT_SECONDS)):
            due.append((item["key"], "alert", f"Project helper alert. {item['name']}: {item['value']}. {item['sentence']} {DASHBOARD}"))
        elif record and record.get("open") and _recovered(item, facts, record):
            due.append((item["key"], "resolved", f"Project helper resolved. {item['name']}: {item['value']}. {item['sentence']} {DASHBOARD}"))
    record = records.get("worker_restart")
    if new_restart_reason and not (record and now - record["sent_at"] < ALERT_REPEAT_SECONDS):
        due.append(("worker_restart", "restart", f"Project helper alert. Worker: restarted by the watchdog after "
                                                 f"{new_restart_reason}. Check the logs for the cause. {DASHBOARD}"))
    return due
