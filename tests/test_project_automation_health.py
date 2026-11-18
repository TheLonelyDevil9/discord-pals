"""Health signals of the Project helper: thresholds, paused states, and cheap lock-free reads."""

import asyncio
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from project_automation import AutomationService
from project_automation_activity import mapping_key
from project_automation_config import PROJECT_DEFAULTS
from project_automation_health import HealthFacts, health_signals
from project_automation_store import AutomationStore
from test_project_automation_store import trace_store_statements

MB = 1024 * 1024
ENABLED_ONLY = {"worker", "heartbeat", "helper_bot", "queue_lag", "sync"}
ALWAYS = {"pending", "held", "failed", "database"}


def healthy(**changes):
    facts = HealthFacts(pause_reason=None, bot_online=True, worker_running=True, heartbeat_age=1.0,
                        pending=0, held=0, failed=0, queue_lag=None, sync_age=60.0, database_bytes=4096,
                        alerts_enabled=True, operators=1)
    return replace(facts, **changes)


def signal(signals, key):
    return next(item for item in signals if item["key"] == key)


@pytest.mark.parametrize("key,field,value,level", [
    ("heartbeat", "heartbeat_age", 120, "ok"), ("heartbeat", "heartbeat_age", 121, "warn"),
    ("heartbeat", "heartbeat_age", 600, "warn"), ("heartbeat", "heartbeat_age", 601, "alert"),
    ("queue_lag", "queue_lag", 900, "ok"), ("queue_lag", "queue_lag", 901, "warn"),
    ("queue_lag", "queue_lag", 3600, "warn"), ("queue_lag", "queue_lag", 3601, "alert"),
    ("pending", "pending", 2000, "ok"), ("pending", "pending", 2001, "warn"),
    ("pending", "pending", 10000, "warn"), ("pending", "pending", 10001, "alert"),
    ("sync", "sync_age", 3600, "ok"), ("sync", "sync_age", 3601, "warn"),
    ("sync", "sync_age", 10800, "warn"), ("sync", "sync_age", 10801, "alert"),
    ("held", "held", 0, "ok"), ("held", "held", 1, "warn"),
    ("failed", "failed", 0, "ok"), ("failed", "failed", 1, "warn"),
    ("database", "database_bytes", 512 * MB, "ok"), ("database", "database_bytes", 512 * MB + 1, "warn"),
])
def test_each_signal_takes_its_level_from_its_thresholds(key, field, value, level):
    assert signal(health_signals(healthy(**{field: value})), key)["level"] == level


def test_a_stopped_worker_and_an_offline_helper_bot_are_alerts():
    assert signal(health_signals(healthy()), "worker")["level"] == "ok"
    assert signal(health_signals(healthy(worker_running=False)), "worker")["level"] == "alert"
    assert signal(health_signals(healthy()), "helper_bot")["level"] == "ok"
    offline = health_signals(healthy(bot_online=False))
    assert signal(offline, "helper_bot")["level"] == "alert"
    # The worker check needs the helper bot online, and the heartbeat needs a live worker.
    assert "worker" not in {item["key"] for item in offline}
    assert "heartbeat" not in {item["key"] for item in health_signals(healthy(worker_running=False))}


def test_nothing_due_or_recorded_is_not_a_fault():
    signals = health_signals(healthy(queue_lag=None, sync_age=None))
    assert signal(signals, "queue_lag")["level"] == "ok"
    assert signal(signals, "sync")["level"] == "ok"


def test_every_signal_has_a_name_a_value_a_level_and_one_sentence():
    for facts in (healthy(), healthy(pending=20000, held=3, failed=2, queue_lag=7200, sync_age=20000,
                                     database_bytes=600 * MB, heartbeat_age=900),
                  healthy(pause_reason="disabled")):
        for item in health_signals(facts):
            assert set(item) == {"key", "name", "value", "level", "sentence"}
            assert item["level"] in {"ok", "warn", "alert"}
            assert item["name"] and item["value"] and item["sentence"].endswith(".")
            assert item["sentence"].count(". ") == 0


@pytest.mark.parametrize("reason,value", [("disabled", "Paused"), ("global_pause", "Globally paused"),
                                          ("setup", "Setup needed")])
def test_a_paused_helper_reports_its_state_once_instead_of_the_enabled_only_signals(reason, value):
    signals = health_signals(healthy(pause_reason=reason, bot_online=False, worker_running=False,
                                     heartbeat_age=10_000, queue_lag=10_000, sync_age=100_000))
    keys = [item["key"] for item in signals]
    assert keys.count("automation") == 1
    state = signal(signals, "automation")
    assert (state["level"], state["value"]) == ("ok", value)
    assert not ENABLED_ONLY & set(keys)
    assert ALWAYS <= set(keys)
    assert "automation" not in {item["key"] for item in health_signals(healthy())}


@pytest.fixture
def runtime(monkeypatch):
    state = {"global_paused": False, "operator_user_ids": []}
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(get=lambda key, default=None: state.get(key, default)))
    return state


@pytest.fixture
def cfg():
    return {**PROJECT_DEFAULTS, "enabled": True, "repository": "Owner/Project", "bot_name": "Helper",
            "guild_id": "100", "reviews_channel_id": "200"}


@pytest.fixture
def service(tmp_path, runtime, cfg):
    store = AutomationStore(tmp_path / "health.sqlite3")
    service = AutomationService(store, settings=lambda: cfg, ai=object(), transport=object())
    service.bots["Helper"] = SimpleNamespace(name="Helper")
    return service


def add_job(service, key, state, *, available_ago=0.0):
    stamp = time.time() - available_ago
    with closing(sqlite3.connect(service.store.path)) as connection, connection:
        connection.execute("INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
                           "VALUES ('event', ?, '{}', ?, ?, ?, ?)", (key, state, stamp, stamp, stamp))


def levels(service):
    return {item["key"]: item["level"] for item in service.health()}


def test_service_health_reads_the_queue_the_last_sync_and_the_database_size(service, cfg):
    add_job(service, "late", "pending", available_ago=20 * 60)
    add_job(service, "future", "pending", available_ago=-3600)
    add_job(service, "held", "recovery")
    add_job(service, "failed", "failed")
    add_job(service, "finished", "done", available_ago=30 * 86400)
    service.store.record_status(mapping_key(cfg), "last_sync_at", time.time() - 2 * 3600)
    result = {item["key"]: item for item in service.health()}
    assert result["queue_lag"]["level"] == "warn"
    assert result["pending"]["value"] == "2"
    assert (result["held"]["level"], result["failed"]["level"]) == ("warn", "warn")
    assert result["sync"]["level"] == "warn"
    assert result["database"]["level"] == "ok"
    assert result["helper_bot"]["level"] == "ok"
    # No worker task was started in this test.
    assert result["worker"]["level"] == "alert"


def test_service_health_reports_the_helper_bot_and_pause_state(service, cfg, runtime):
    del service.bots["Helper"]
    assert levels(service)["helper_bot"] == "alert"
    runtime["global_paused"] = True
    assert signal(service.health(), "automation")["value"] == "Globally paused"
    cfg["enabled"] = False
    assert signal(service.health(), "automation")["value"] == "Paused"
    assert not ENABLED_ONLY & set(levels(service))


def test_sync_age_counts_from_when_automation_last_started_running(service, cfg, monkeypatch):
    import project_automation
    service.store.record_status(mapping_key(cfg), "last_sync_at", time.time() - 2 * 86400)
    assert levels(service)["sync"] == "alert"

    async def stop(_):
        raise asyncio.CancelledError

    async def nothing():
        return None

    monkeypatch.setattr(project_automation.asyncio, "sleep", stop)
    monkeypatch.setattr(service, "maintain", nothing)
    monkeypatch.setattr(service, "run_once", nothing)
    service._queue_cleaned = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._run())
    assert levels(service)["sync"] == "ok"


def test_status_carries_the_health_signals_without_job_payloads_titles_or_user_names(service):
    service.store.enqueue("publish", {"title": "Private report title", "reporter_name": "private-user"}, "publish:private")
    status = service.status()
    assert [item["key"] for item in status["health"]] == [item["key"] for item in service.health()]
    assert signal(status["health"], "pending")["value"] == "1"
    shown = json.dumps(status["health"])
    assert "Private report title" not in shown and "private-user" not in shown


def test_health_reads_leave_the_write_lock_free(service, cfg, monkeypatch):
    add_job(service, "pending", "pending", available_ago=60)
    add_job(service, "failed", "failed")
    service.store.record_status(mapping_key(cfg), "last_sync_at")
    writer_could_start = []

    def probe(statement):
        if statement.lstrip().upper().startswith("SELECT"):
            other = original(str(service.store.path), timeout=0, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute("ROLLBACK")
                writer_could_start.append(True)
            except sqlite3.OperationalError:
                writer_could_start.append(False)
            finally:
                other.close()

    original = trace_store_statements(monkeypatch, probe)
    service.health()
    assert writer_could_start and all(writer_could_start)


def test_health_reads_search_indexes_and_never_scan_finished_jobs(service, monkeypatch):
    for index in range(50):
        add_job(service, f"done:{index}", "done", available_ago=86400)
    add_job(service, "late", "pending", available_ago=60)
    statements = []
    trace_store_statements(monkeypatch, statements.append)
    service.health()
    monkeypatch.undo()
    reads = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert any("jobs" in statement for statement in reads)
    with closing(sqlite3.connect(service.store.path)) as connection:
        for statement in reads:
            plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement)]
            assert not any(step.startswith("SCAN") for step in plan), (statement, plan)


def test_health_never_writes(service, cfg, monkeypatch):
    add_job(service, "inflight", "inflight", available_ago=60)
    with closing(sqlite3.connect(service.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET lease_until = ? WHERE key = 'inflight'", (time.time() - 60,))
    statements = []
    trace_store_statements(monkeypatch, statements.append)
    service.health()
    assert not [statement for statement in statements
                if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "BEGIN"))]
    assert json.loads(json.dumps(service.health()))
