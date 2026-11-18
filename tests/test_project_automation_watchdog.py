"""The worker heartbeat and the watchdog that restarts a dead worker task."""

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import project_automation
from test_project_automation_health import cfg as cfg_fixture, runtime as runtime_fixture, service as service_fixture
from test_project_automation_health import signal

cfg, runtime, service = cfg_fixture, runtime_fixture, service_fixture


async def idle():
    await asyncio.Event().wait()


def test_the_worker_records_a_heartbeat_on_every_pass(service, monkeypatch):
    clock = [5_000.0]
    monkeypatch.setattr(project_automation, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    beats = []

    async def run_once():
        beats.append(service._heartbeat)
        return False

    async def sleep(_):
        if len(beats) == 3:
            raise asyncio.CancelledError
        clock[0] += 7

    monkeypatch.setattr(service, "run_once", run_once)
    monkeypatch.setattr(service, "maintain", AsyncMock())
    monkeypatch.setattr(project_automation.asyncio, "sleep", sleep)
    service._queue_cleaned = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._run())
    assert beats == [5_000.0, 5_007.0, 5_014.0]


@pytest.mark.parametrize("death,reason", [("raise", "RuntimeError"), ("cancel", "CancelledError")])
def test_a_dead_worker_is_restarted_counted_and_its_reason_kept(service, monkeypatch, death, reason):
    started = []

    async def run():
        started.append(True)
        await idle()

    async def scenario():
        async def crash():
            raise RuntimeError("private detail of the failure")

        service._worker = asyncio.create_task(crash() if death == "raise" else idle())
        await asyncio.sleep(0)
        if death == "cancel":
            service._worker.cancel()
            await asyncio.gather(service._worker, return_exceptions=True)
        monkeypatch.setattr(service, "_run", run)
        await service.supervise_once()
        await asyncio.sleep(0)
        restarted = service._worker
        await service.supervise_once()
        try:
            return restarted, service._worker, service.health()
        finally:
            restarted.cancel()

    restarted, current, signals = asyncio.run(scenario())
    assert started == [True] and current is restarted
    worker = signal(signals, "worker")
    assert (worker["level"], worker["value"]) == ("ok", "Running, restarted 1 time")
    assert reason in worker["sentence"] and "private detail" not in str(signals)


def test_a_stale_heartbeat_on_a_live_worker_is_reported_and_never_killed(service):
    async def scenario():
        live = asyncio.create_task(idle())
        service._worker = live
        service._heartbeat = time.monotonic() - 11 * 60
        await service.supervise_once()
        try:
            return live, service._worker, live.done(), service.health()
        finally:
            live.cancel()

    live, current, stopped, signals = asyncio.run(scenario())
    assert current is live and not stopped
    assert signal(signals, "heartbeat")["level"] == "alert"
    assert signal(signals, "worker")["value"] == "Running"


def test_the_watchdog_does_not_start_a_worker_for_a_detached_service(service):
    service.bots.clear()
    asyncio.run(service.supervise_once())
    assert service._worker is None


def test_a_failing_check_is_logged_and_the_next_check_still_runs(service, monkeypatch):
    calls, logged, passes = [], [], []

    async def revive():
        calls.append(len(passes))
        if len(calls) == 1:
            raise RuntimeError("private failure text")

    async def sleep(_):
        if len(passes) == 3:
            raise asyncio.CancelledError
        passes.append(True)

    monkeypatch.setattr(service, "_revive_worker", revive)
    monkeypatch.setattr(project_automation.asyncio, "sleep", sleep)
    monkeypatch.setattr(project_automation, "warn", lambda message, **fields: logged.append((message, fields)))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._watchdog())
    assert calls == [1, 2, 3]
    assert len(logged) == 1 and "RuntimeError" in logged[0][0] and "private failure text" not in logged[0][0]


def test_an_attach_that_raises_still_leaves_a_watchdog_that_starts_the_worker(service, monkeypatch):
    started = []

    async def run():
        started.append(True)
        await idle()

    async def locked(bot):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service, "_run", run)
    service.transport = SimpleNamespace(attach=locked)
    service.bots.clear()
    helper = SimpleNamespace(name="Helper")

    async def scenario():
        with pytest.raises(sqlite3.OperationalError):
            await service.attach(helper)
        watchdogs = [task for task in asyncio.all_tasks() if task.get_name().endswith("watchdog") and not task.done()]
        await service.supervise_once()
        await asyncio.sleep(0)
        try:
            return len(watchdogs), service.health()
        finally:
            await service.detach(helper)

    watchdogs, signals = asyncio.run(scenario())
    assert watchdogs == 1 and started == [True]
    # Starting a worker that an attach never started is not a restart.
    assert signal(signals, "worker")["value"] == "Running"


def test_attach_starts_one_watchdog_and_the_last_detach_stops_it(service, monkeypatch):
    monkeypatch.setattr(service, "_run", idle)
    service.transport = SimpleNamespace(attach=AsyncMock())
    service.bots.clear()
    first, second = SimpleNamespace(name="Helper"), SimpleNamespace(name="Other")

    def watchdogs():
        return [task for task in asyncio.all_tasks() if task.get_name().endswith("watchdog") and not task.done()]

    async def scenario():
        await service.attach(first)
        await service.attach(second)
        running = len(watchdogs())
        await service.detach(first)
        still = len(watchdogs())
        await service.detach(second)
        return running, still, len(watchdogs()), service._worker

    assert asyncio.run(scenario()) == (1, 1, 0, None)
