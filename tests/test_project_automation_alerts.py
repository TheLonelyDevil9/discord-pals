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
        self.attempts = []
        self.failure = None
        self.failing_bots = {}

    async def direct_message(self, bot, user_id, text):
        self.attempts.append(bot.name)
        failure = self.failing_bots.get(bot.name) or self.failure
        if failure:
            raise failure
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


def test_a_refused_dm_is_logged_once_per_attempt_and_counts_against_the_daily_limit(alerts, monkeypatch):
    logged = []
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: logged.append((message, fields)))
    alerts.bots["Other"] = SimpleNamespace(name="Other")
    alerts.transport.failure = project_automation.RecipientRefused("Cannot send messages to this user")
    late_job(alerts)
    asyncio.run(alerts.supervise_once())
    assert [fields["event"] for _, fields in logged] == ["alert_dm_failed", "alert_dm_failed"]
    assert all("RecipientRefused" in message and "Cannot send" not in message for message, _ in logged)
    # A refusal is final for that operator, so no other bot tries.
    assert alerts.transport.attempts == ["Helper", "Helper"]
    alerts.transport.failure = None
    # The attempt counts against the daily limit, and nothing was delivered that could be resolved.
    assert send(alerts) == []
    finish_jobs(alerts)
    assert send(alerts) == []


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), ConnectionError("reconnecting"),
                                     RuntimeError("503 Service Unavailable")])
def test_a_transient_dm_failure_tries_the_other_bots_in_the_same_pass(alerts, monkeypatch, failure):
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)
    alerts.bots["Other"] = SimpleNamespace(name="Other")
    alerts.transport.failing_bots["Helper"] = failure
    late_job(alerts)
    assert [(bot, user) for bot, user, _ in send(alerts)] == [("Other", "800"), ("Other", "801")]
    assert alerts.transport.attempts == ["Helper", "Other", "Helper", "Other"]
    assert send(alerts) == []


def test_a_dm_that_no_bot_could_deliver_stays_due_for_the_next_pass(alerts, monkeypatch):
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)
    alerts.transport.failure = TimeoutError("timed out")
    late_job(alerts)
    assert send(alerts) == []
    alerts.transport.failure = None
    assert len(send(alerts)) == 2
    assert send(alerts) == []


def test_a_restart_notice_that_no_bot_could_deliver_stays_due(alerts, monkeypatch):
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)

    async def scenario():
        async def crash():
            raise ValueError("private detail")

        async def idle():
            await asyncio.Event().wait()

        alerts._worker = asyncio.create_task(crash())
        await asyncio.sleep(0)
        monkeypatch.setattr(alerts, "_run", idle)
        alerts.transport.failure = TimeoutError("timed out")
        await alerts.supervise_once()
        alerts.transport.failure = None
        await alerts.supervise_once()
        await alerts.supervise_once()
        alerts._worker.cancel()
        return [text for _, _, text in alerts.transport.sent]

    assert asyncio.run(scenario()) == [
        "Project helper alert. Worker: restarted by the watchdog after ValueError. "
        "Check the logs for the cause. Dashboard → Project"] * 2


@pytest.mark.parametrize("mute", ["alerts off", "no operators"])
def test_muted_alerts_forget_their_open_episode_so_the_next_one_alerts(alerts, cfg, runtime, mute):
    def muted(on):
        if mute == "alerts off":
            cfg["alerts_enabled"] = not on
        else:
            runtime["operator_user_ids"] = [] if on else ["800", "801"]

    late_job(alerts)
    assert len(send(alerts)) == 2
    muted(True)
    finish_jobs(alerts)
    assert send(alerts) == []
    muted(False)
    # The episode that ended while alerts were muted gets no late resolved DM.
    assert send(alerts) == []
    add_job(alerts, "late again", "pending", available_ago=2 * 3600)
    sent = send(alerts)
    assert len(sent) == 2 and all(text.startswith("Project helper alert. Queue lag") for _, _, text in sent)


def locked_writes(store, monkeypatch, failing):
    """Make the store's link writes raise like a database locked past its busy timeout, for the write numbers in failing."""
    real_put, writes = store.put_link, []

    def put_link(kind, key, data):
        writes.append(data)
        if len(writes) in failing:
            raise sqlite3.OperationalError("database is locked")
        return real_put(kind, key, data)

    monkeypatch.setattr(store, "put_link", put_link)
    return writes


def test_an_alert_whose_record_cannot_be_written_is_not_sent(alerts, monkeypatch):
    logged = []
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: logged.append(fields["event"]))
    locked_writes(alerts.store, monkeypatch, failing={1, 2})
    late_job(alerts)
    for _ in range(2):
        asyncio.run(alerts.supervise_once())
    assert alerts.transport.sent == []
    assert logged == ["alert_record_failed", "alert_record_failed"]
    assert len(send(alerts)) == 2
    assert send(alerts) == []


def test_a_delivered_alert_is_not_repeated_when_marking_it_open_fails(alerts, monkeypatch):
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)
    writes = locked_writes(alerts.store, monkeypatch, failing={2})
    late_job(alerts)
    assert len(send(alerts)) == 2
    assert [write["open"] for write in writes] == [False, True]
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


def test_a_cancelled_error_raised_by_a_dm_send_does_not_end_the_watchdog(alerts, monkeypatch):
    class Cancelling(Messenger):
        async def direct_message(self, bot, user_id, text):
            raise asyncio.CancelledError

    alerts.transport = Cancelling()
    late_job(alerts)
    passes, logged = [], []

    async def sleep(_):
        if len(passes) == 3:
            raise asyncio.CancelledError
        passes.append(True)

    monkeypatch.setattr(project_automation.asyncio, "sleep", sleep)
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: logged.append(fields["event"]))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(alerts._watchdog())
    assert len(passes) == 3
    assert "alert_dm_failed" in logged and "watchdog_check_failed" not in logged


def test_cancelling_the_watchdog_during_a_dm_ends_the_watchdog_and_the_dm(alerts):
    sends = []

    class Hanging(Messenger):
        async def direct_message(self, bot, user_id, text):
            sends.append(asyncio.current_task())
            await asyncio.Event().wait()

    alerts.transport = Hanging()
    late_job(alerts)

    async def scenario():
        watchdog = asyncio.create_task(alerts.supervise_once())
        while not sends:
            await asyncio.sleep(0.01)
        watchdog.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watchdog
        await asyncio.sleep(0)
        return sends[0].cancelled()

    assert asyncio.run(scenario())


def test_a_failing_watchdog_check_does_not_stop_the_alert_check(alerts, monkeypatch):
    async def broken():
        raise RuntimeError("revive failed")

    monkeypatch.setattr(alerts, "_revive_worker", broken)
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: None)
    late_job(alerts)
    asyncio.run(alerts.supervise_once())
    assert len(alerts.transport.sent) == 2
