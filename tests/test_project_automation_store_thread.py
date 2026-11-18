"""The helper's store calls run on one store thread, so a database lock never stalls the bots' event loop."""

import asyncio
import contextlib
from contextlib import closing
import sqlite3
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from project_automation import AutomationService, mirror_key, mirror_record
from project_automation_config import PROJECT_DEFAULTS
from project_automation_store import AutomationStore, run_store, store_thread_idle
from project_automation_webhook import webhook_binding
from test_project_automation import runtime as runtime_fixture

runtime = runtime_fixture


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(
        get=lambda key, default=None: default, is_server_response_allowed=lambda channel_id: (True, None)))
    cfg = {**PROJECT_DEFAULTS, "enabled": True, "repository": "Owner/Project", "bot_name": "Helper",
           "guild_id": "100", "reviews_channel_id": "200", "maintainer_user_ids": ["42"]}
    service = AutomationService(AutomationStore(tmp_path / "thread.sqlite3"), settings=lambda: cfg,
                                ai=object(), transport=object())
    service.bots["Helper"] = SimpleNamespace(name="Helper")
    return service


def missing_scan(service, key="scan"):
    """Queue a job that only reads and writes the store: a missing-comment scan of a PR with no records."""
    binding = webhook_binding(service.settings())
    return service.store.enqueue("pr_sync_missing", {
        "repository": "Owner/Project", "number": 7, "source": "comment", "after": "",
        "_binding": binding, "_sync_cycle": 1}, key)


def hold_write_lock(service):
    blocker = sqlite3.connect(str(service.store.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    return blocker


async def ticking(seconds, ticks):
    """Count passes of the event loop for `seconds`, at most one every 10 ms."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        ticks.append(time.monotonic())


def test_a_job_waiting_for_the_database_lock_leaves_the_event_loop_running(service):
    job = missing_scan(service)
    blocker = hold_write_lock(service)
    ticks = []

    async def scenario():
        worker = asyncio.create_task(service.run_once())
        await ticking(0.4, ticks)
        assert not worker.done()
        blocker.rollback()
        return await asyncio.wait_for(worker, 15)

    try:
        assert asyncio.run(scenario()) is True
    finally:
        blocker.close()
    assert len(ticks) >= 10
    assert service.store.get_job(job)["state"] == "done"


def test_a_retry_command_waiting_for_the_database_lock_leaves_the_event_loop_running(service):
    job = missing_scan(service)
    with closing(sqlite3.connect(service.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (job,))
    blocker = hold_write_lock(service)
    ticks = []

    async def scenario():
        command = asyncio.create_task(service.retry(job, user_id="42"))
        await ticking(0.4, ticks)
        assert not command.done()
        blocker.rollback()
        return await asyncio.wait_for(command, 15)

    try:
        assert asyncio.run(scenario()) == "Retry queued."
    finally:
        blocker.close()
    assert len(ticks) >= 10
    assert service.store.get_job(job)["state"] == "pending"


def test_a_worker_cancelled_during_its_claim_returns_the_job_without_counting_the_attempt(service):
    job = missing_scan(service)
    blocker = hold_write_lock(service)

    async def scenario():
        worker = asyncio.create_task(service.run_once())
        await asyncio.sleep(0.2)
        worker.cancel()
        await asyncio.sleep(0.2)
        # The cancelled worker still waits for its claim, so the claimed job is not left behind.
        assert not worker.done()
        blocker.rollback()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 15)

    try:
        asyncio.run(scenario())
    finally:
        blocker.close()
    row = service.store.get_job(job)
    assert (row["state"], row["attempts"], row["lease_token"]) == ("pending", 0, None)


def test_a_store_write_whose_caller_was_cancelled_still_runs(service):
    blocker = hold_write_lock(service)

    async def scenario():
        caller = asyncio.create_task(run_store(service.store.put_link, "project_status", "written", {"value": 1}))
        await asyncio.sleep(0.2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        blocker.rollback()
        await asyncio.wait_for(store_thread_idle(), 15)

    try:
        asyncio.run(scenario())
    finally:
        blocker.close()
    assert service.store.get_link("project_status", "written") == {"value": 1}


def test_detach_waits_for_the_store_calls_of_the_worker_it_cancels(service):
    job = missing_scan(service)
    blocker = hold_write_lock(service)

    async def scenario():
        service._worker = asyncio.create_task(service.run_once())
        await asyncio.sleep(0.2)
        detaching = asyncio.create_task(service.detach(SimpleNamespace(name="Helper")))
        await asyncio.sleep(0.2)
        assert not detaching.done()
        blocker.rollback()
        await asyncio.wait_for(detaching, 15)

    try:
        asyncio.run(scenario())
    finally:
        blocker.close()
    assert service._worker is None
    row = service.store.get_job(job)
    assert (row["state"], row["attempts"]) == ("pending", 0)


@pytest.fixture
def loop_connections(monkeypatch):
    """Record each database connection that the helper opens on a thread that runs an event loop."""
    opened = []
    real = AutomationStore._connection

    @contextlib.contextmanager
    def watched(self, write=False, timeout=10):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            opened.append("".join(traceback.format_stack(limit=6)))
        with real(self, write=write, timeout=timeout) as connection:
            yield connection

    monkeypatch.setattr(AutomationStore, "_connection", watched)
    return opened


def test_the_connection_watch_reports_a_store_call_made_on_the_event_loop(service, loop_connections):
    async def on_the_loop():
        service.store.get_link("project_status", "anything")
        await run_store(service.store.get_link, "project_status", "anything")

    asyncio.run(on_the_loop())
    assert len(loop_connections) == 1


class ActivityTransport:
    """A Discord stand-in that, like DiscordTransport, records mirrored issue and PR versions in the store."""

    def __init__(self, service):
        self.service = service
        self.activity = []

    async def mirror(self, event, *, recover_only=False, create=True):
        if event["kind"] in {"issue", "pull"} and not recover_only:
            key = mirror_key(self.service.settings(), event["kind"], event["key"])
            await run_store(self.service.store.put_link, "item_mirrored", key,
                            {**mirror_record(event), "mirrored_at": time.time()})
        return True

    async def mirror_activity(self, event, *, recover_only=False):
        await self.mirror({**event["pull"], "kind": "pull", "key": f"pull:{event['number']}"})
        self.activity.append(event["key"])
        return True

    async def notify(self, case, *, recover_only=False):
        return True


def test_feedback_webhook_recovery_and_alert_paths_open_no_connection_on_the_event_loop(tmp_path, runtime,
                                                                                         loop_connections):
    from test_project_automation import Harness
    harness = Harness(tmp_path / "feedback.sqlite3")
    preview = harness.preview()
    filed = harness.choose(preview, "submit")
    assert filed["state"] == "filed"
    harness.event({"kind": "issue", "repository": "SillyBunnyTeam/SillyBunny", "number": 42, "key": "issue:42",
                   "title": "Settings fail to save", "state": "closed", "action": "closed",
                   "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42"}, "github:closed")
    harness.event({"kind": "comment", "repository": "SillyBunnyTeam/SillyBunny", "number": 42, "key": "comment:9",
                   "body": "Which version?", "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42#issuecomment-9"},
                  "github:comment")
    asyncio.run(harness.drain())
    held = harness.event({"kind": "general", "repository": "SillyBunnyTeam/SillyBunny", "key": "release:1",
                          "title": "Release", "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/releases/1"},
                         "github:release")
    with closing(sqlite3.connect(harness.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'recovery' WHERE id = ?", (held,))
    harness.transport.confirm_not_delivered = lambda event: None
    assert not asyncio.run(harness.service.recover(held, user_id="700"))
    assert "retry" in asyncio.run(harness.service.retry(held, user_id="700", confirmed_not_delivered=True))
    harness.transport.attach = AsyncMock()
    harness.service._run = lambda: asyncio.sleep(0)

    async def attach_and_alert():
        bot = SimpleNamespace(name=harness.cfg["bot_name"])
        await harness.service.attach(bot)
        harness.service.transport.direct_message = lambda *args: asyncio.sleep(0)
        await harness.service.send_alerts()
        await harness.service.detach(bot)

    asyncio.run(attach_and_alert())
    assert harness.store.get_case(filed["id"])["state"] == "filed"
    assert loop_connections == []


def test_sync_and_pr_activity_paths_open_no_connection_on_the_event_loop(tmp_path, loop_connections, monkeypatch):
    import test_project_activity_workflow as workflow
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(get=lambda key, default=None: default))
    cfg = {**PROJECT_DEFAULTS, "enabled": True, "repository": "Owner/Project", "bot_name": "Helper",
           "guild_id": "100", "reviews_channel_id": "200", "issues_channel_id": "199"}
    client = workflow.Client()
    service = AutomationService(AutomationStore(tmp_path / "sync.sqlite3"), settings=lambda: cfg, ai=object(),
                                github_factory=lambda _: client)
    service.transport = ActivityTransport(service)
    service.bots["Helper"] = SimpleNamespace(name="Helper")
    items = {5: workflow.github_item("issue", 5), 7: workflow.github_item("pull", 7),
             9: workflow.github_item("issue", 9, closed_days_ago=1)}
    workflow.open_world(SimpleNamespace(client=client), items)
    service.store.put_link("mirror", mirror_key(cfg, "issue", "issue:9"), {
        "channel_id": "1", "message_id": "2", "kind": "issue", "number": 9, "repository": cfg["repository"],
        "guild_id": cfg["guild_id"], "bot_name": cfg["bot_name"]})
    client.current = workflow.activity()
    client.pages[("comment", None)] = {"items": [client.current], "next_cursor": None}
    assert service.queue_sync_cycle() is not None

    async def drain():
        for _ in range(200):
            if not await service.run_once():
                return
        raise AssertionError("The queue did not settle")

    asyncio.run(drain())
    assert service.transport.activity == [client.current["key"]]
    assert service.store.get_link("item_closed", "owner/project:issue:9")["closed_at"] > 0
    assert loop_connections == []
