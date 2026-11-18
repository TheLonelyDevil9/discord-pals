"""Operator alert DMs for the Project helper's health signals and watchdog restarts."""

import asyncio
from contextlib import closing
import sqlite3
import time
from types import SimpleNamespace

import pytest

import project_automation
from project_automation import AutomationService
from project_automation_activity import mapping_key
from test_project_automation_health import cfg as cfg_fixture, runtime as runtime_fixture, service as service_fixture
from test_project_automation_health import add_job, signal

cfg, runtime, service = cfg_fixture, runtime_fixture, service_fixture
DAY = 86400


class Messenger:
    def __init__(self):
        self.sent = []
        self.failure = None

    async def direct_message(self, bot, user_id, text):
        if self.failure:
            raise self.failure
        self.sent.append((bot.name, user_id, text))


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(project_automation, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic))
    return now


LIVE_WORKER = SimpleNamespace(done=lambda: False)


@pytest.fixture
def alerts(service, runtime, clock):
    runtime["operator_user_ids"] = ["800", "801"]
    service.transport = Messenger()
    service._worker = LIVE_WORKER
    return service


def late_job(service):
    add_job(service, "late", "pending", available_ago=2 * 3600)


def finish_jobs(service):
    with closing(sqlite3.connect(service.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done'")


def send(service):
    asyncio.run(service.send_alerts())
    sent, service.transport.sent = service.transport.sent, []
    return sent


def restarted(service, cfg):
    reopened = AutomationService(service.store, settings=lambda: cfg, ai=object(), transport=service.transport)
    reopened.bots.update(service.bots)
    reopened._worker = LIVE_WORKER
    return reopened


def run_pass(service, monkeypatch):
    """Run one pass of the worker loop, which records when automation started running."""
    async def stop(_):
        raise asyncio.CancelledError

    async def nothing():
        return None

    with monkeypatch.context() as patch:
        patch.setattr(project_automation.asyncio, "sleep", stop)
        patch.setattr(service, "maintain", nothing)
        patch.setattr(service, "run_once", nothing)
        patch.setattr(service, "queue_sync_cycle", lambda: None)
        service._queue_cleaned = True
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service._run())


def about(sent, name):
    return [text for _, _, text in sent if f". {name}: " in text]


def test_a_sync_alert_is_resolved_only_by_a_sync_that_completes_after_it(alerts, cfg, clock, monkeypatch):
    t0 = clock[0]
    alerts.store.record_status(mapping_key(cfg), "last_sync_at", t0 - 4 * 3600)
    clock[0] = t0 - 6 * 3600
    run_pass(alerts, monkeypatch)
    clock[0] = t0
    first = about(send(alerts), "Time since a sync completed")
    assert first == ["Project helper alert. Time since a sync completed: 4 hours. Check Delivery activity and the "
                     "logs for the sync jobs that keep a cycle open. Dashboard → Project"] * 2
    reopened = restarted(alerts, cfg)
    run_pass(reopened, monkeypatch)
    clock[0] = t0 + 60
    assert send(reopened) == []
    clock[0] = t0 + 4 * 3600
    assert signal(reopened.health(), "sync")["level"] == "alert"
    assert send(reopened) == []
    reopened.store.record_status(mapping_key(cfg), "last_sync_at", clock[0])
    resolved = about(send(reopened), "Time since a sync completed")
    assert len(resolved) == 2 and all(text.startswith("Project helper resolved.") for text in resolved)


def test_queue_lag_counts_from_when_automation_runs_and_resolves_on_the_whole_wait(alerts, cfg, runtime, clock,
                                                                                   monkeypatch):
    runtime["global_paused"] = True
    add_job(alerts, "queued during the pause", "pending", available_ago=5 * 3600)
    assert send(alerts) == []
    runtime["global_paused"] = False
    run_pass(alerts, monkeypatch)
    assert signal(alerts.health(), "queue_lag")["level"] == "ok"
    assert send(alerts) == []
    clock[0] += 2 * 3600
    assert about(send(alerts), "Queue lag") == ["Project helper alert. Queue lag: 2 hours. Check the helper bot and "
                                                 "the worker, then Delivery activity for the oldest waiting work. "
                                                 "Dashboard → Project"] * 2
    reopened = restarted(alerts, cfg)
    run_pass(reopened, monkeypatch)
    assert signal(reopened.health(), "queue_lag")["level"] == "ok"
    assert send(reopened) == []
    finish_jobs(reopened)
    assert [text.split(". ")[0] for text in about(send(reopened), "Queue lag")] == ["Project helper resolved"] * 2


def test_an_alert_reaches_each_operator_once_through_the_helper_bot(alerts):
    alerts.bots["Other"] = SimpleNamespace(name="Other")
    late_job(alerts)
    sent = send(alerts)
    assert [(bot, user) for bot, user, _ in sent] == [("Helper", "800"), ("Helper", "801")]
    text = sent[0][2]
    lag = signal(alerts.health(), "queue_lag")
    assert text == f"Project helper alert. Queue lag: {lag['value']}. {lag['sentence']} Dashboard → Project"
    assert send(alerts) == []


def test_another_online_bot_sends_the_alert_when_the_helper_bot_is_offline(alerts):
    del alerts.bots["Helper"]
    alerts.bots["Other"] = SimpleNamespace(name="Other")
    sent = send(alerts)
    assert {bot for bot, _, _ in sent} == {"Other"}
    assert all("Helper bot: Offline" in text for _, _, text in sent)


def test_at_most_one_alert_per_signal_per_day_and_the_limit_survives_a_restart(alerts, cfg, clock):
    late_job(alerts)
    assert len(send(alerts)) == 2
    finish_jobs(alerts)
    assert all("resolved" in text for _, _, text in send(alerts))
    add_job(alerts, "late again", "pending", available_ago=2 * 3600)
    reopened = restarted(alerts, cfg)
    clock[0] += 3600
    assert send(reopened) == []
    clock[0] += DAY
    sent = send(reopened)
    assert len(sent) == 2 and all(text.startswith("Project helper alert. Queue lag") for _, _, text in sent)


def test_a_recovered_signal_sends_one_resolved_dm(alerts):
    late_job(alerts)
    send(alerts)
    finish_jobs(alerts)
    lag = signal(alerts.health(), "queue_lag")
    sent = send(alerts)
    assert [user for _, user, _ in sent] == ["800", "801"]
    assert sent[0][2] == f"Project helper resolved. Queue lag: {lag['value']}. {lag['sentence']} Dashboard → Project"
    assert send(alerts) == []


def test_a_continuing_alert_is_not_repeated_while_its_dm_is_unresolved(alerts, clock):
    late_job(alerts)
    send(alerts)
    clock[0] += 3 * DAY
    assert send(alerts) == []


def test_no_dm_without_operators_and_one_informational_signal_says_so(alerts, runtime):
    runtime["operator_user_ids"] = []
    late_job(alerts)
    assert send(alerts) == []
    notice = signal(alerts.health(), "alerts")
    assert (notice["level"], notice["value"]) == ("ok", "No operators")
    runtime["operator_user_ids"] = ["800"]
    assert "alerts" not in {item["key"] for item in alerts.health()}


def test_no_dm_while_alerts_are_turned_off(alerts, cfg):
    cfg["alerts_enabled"] = False
    late_job(alerts)
    assert send(alerts) == []
    assert "alerts" not in {item["key"] for item in alerts.health()}
    cfg["alerts_enabled"] = True
    assert len(send(alerts)) == 2


def test_a_failed_dm_is_logged_once_per_attempt_and_never_raises(alerts, monkeypatch):
    logged = []
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: logged.append((message, fields)))
    alerts.transport.failure = RuntimeError("Cannot send messages to this user")
    late_job(alerts)
    asyncio.run(alerts.supervise_once())
    assert [fields["event"] for _, fields in logged] == ["alert_dm_failed", "alert_dm_failed"]
    assert all("RuntimeError" in message and "Cannot send" not in message for message, _ in logged)
    alerts.transport.failure = None
    # The attempt counts against the daily limit, and nothing was delivered that could be resolved.
    assert send(alerts) == []
    finish_jobs(alerts)
    assert send(alerts) == []


def test_no_online_bot_sends_nothing_and_a_later_pass_sends_the_alert(alerts):
    bots = dict(alerts.bots)
    alerts.bots.clear()
    late_job(alerts)
    assert send(alerts) == []
    alerts.bots.update(bots)
    assert len(send(alerts)) == 2


def test_a_watchdog_restart_sends_one_dm_with_the_exception_type_only(alerts, monkeypatch):
    async def run():
        await asyncio.Event().wait()

    async def scenario():
        async def crash():
            raise ValueError("private detail")

        alerts._worker = asyncio.create_task(crash())
        await asyncio.sleep(0)
        monkeypatch.setattr(alerts, "_run", run)
        await alerts.supervise_once()
        first = list(alerts.transport.sent)
        await alerts.supervise_once()
        alerts._worker.cancel()
        return first, alerts.transport.sent[len(first):]

    first, later = asyncio.run(scenario())
    assert [text for _, _, text in first] == [
        "Project helper alert. Worker: restarted by the watchdog after ValueError. "
        "Check the logs for the cause. Dashboard → Project"] * 2
    assert later == []


def test_a_failing_watchdog_check_does_not_stop_the_alert_check(alerts, monkeypatch):
    async def broken():
        raise RuntimeError("revive failed")

    monkeypatch.setattr(alerts, "_revive_worker", broken)
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)
    late_job(alerts)
    asyncio.run(alerts.supervise_once())
    assert len(alerts.transport.sent) == 2
