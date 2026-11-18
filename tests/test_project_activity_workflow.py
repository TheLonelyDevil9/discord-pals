"""Real SQLite workflow checks with no external messages or GitHub writes."""

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import itertools
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from project_automation import AutomationService
from project_automation_activity import activate, mapping_key, source_key
from project_automation_config import PROJECT_DEFAULTS
from project_automation_github import GitHubError
from project_automation_store import AutomationStore, Conflict
from project_automation_webhook import webhook_binding


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def activity(version="v1", *, timestamp=None, source="comment", source_id="10"):
    return {"kind": "pr_activity", "activity_type": source, "source_id": source_id,
            "repository": "Owner/Project", "number": 7, "key": f"pr:7:{source}:{source_id}",
            "source_version": version, "action": "created", "body": "A useful comment",
            "author": "reviewer", "author_type": "User", "state": "published",
            "url": "https://github.com/Owner/Project/pull/7#issuecomment-10",
            "html_url": "https://github.com/Owner/Project/pull/7#issuecomment-10",
            "created_at": iso(timestamp if timestamp is not None else time.time() + 5),
            "updated_at": iso(timestamp if timestamp is not None else time.time() + 5)}


class Client:
    def __init__(self):
        self.current = activity()
        self.pages = {}
        self.page_calls = []
        self.updated_pages = {}

    async def list_updated_pulls(self, since, cursor=None):
        return deepcopy(self.updated_pages.get(cursor, {"items": [], "next_cursor": None}))

    async def get_pr_activity(self, event):
        return deepcopy(self.current)

    async def get_issue(self, number):
        return {"kind": "pull", "number": number, "title": "Improve setup", "body": "Details",
                "state": "open", "closed": False, "html_url": "https://github.com/Owner/Project/pull/7"}

    async def list_pr_activity(self, number, source, cursor=None):
        self.page_calls.append((number, source, cursor))
        value = self.pages.get((source, cursor), {"items": [], "next_cursor": None})
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    async def list_open_items(self, kind):
        return [await self.get_issue(7)] if kind == "pull" else []


class Transport:
    def __init__(self):
        self.attempts = []
        self.receipts = set()
        self.fail_after_send = False
        self.recoveries = []
        self.confirmations = []

    async def mirror_activity(self, event, *, recover_only=False):
        identity = (event.get("delivery_key", event["key"]), event["source_version"])
        if recover_only:
            self.recoveries.append(deepcopy(event))
            return identity in self.receipts
        if identity not in self.receipts:
            self.attempts.append(deepcopy(event))
            self.receipts.add(identity)
        if self.fail_after_send:
            raise TimeoutError("Acknowledgment lost")
        return True

    async def mirror(self, event, **kwargs):
        return True

    def confirm_not_delivered(self, event):
        self.confirmations.append(deepcopy(event))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(get=lambda key, default=None: default))
    cfg = {**PROJECT_DEFAULTS, "enabled": True, "repository": "Owner/Project", "bot_name": "Helper",
           "guild_id": "100", "reviews_channel_id": "200", "maintainer_user_ids": ["42"]}
    store = AutomationStore(tmp_path / "jobs.sqlite3")
    client, transport = Client(), Transport()
    service = AutomationService(store, settings=lambda: cfg, github_factory=lambda _: client, transport=transport)
    service.bots["Helper"] = SimpleNamespace(name="Helper")
    activate(store, cfg)
    return SimpleNamespace(cfg=cfg, store=store, client=client, transport=transport, service=service)


def enqueue(setup, event=None, key="incoming"):
    return setup.store.enqueue("event", {**(event or setup.client.current), "_binding": webhook_binding(setup.cfg)}, key)


def run(setup):
    return asyncio.run(setup.service.run_once())


def test_current_source_is_frozen_for_uncertain_delivery_and_recovery(setup):
    identifier = enqueue(setup)
    setup.transport.fail_after_send = True
    run(setup)
    assert setup.store.get_job(identifier)["state"] == "recovery"
    setup.client.current = activity("v2")
    assert asyncio.run(setup.service.recover(identifier, user_id="42"))
    assert setup.transport.recoveries[-1]["source_version"] == "v1"
    setup.transport.fail_after_send = False
    run(setup)
    assert [a["source_version"] for a in setup.transport.attempts] == ["v1", "v2"]


def test_prepared_delivery_does_not_change_original_webhook_payload(setup):
    event = deepcopy(setup.client.current)
    identifier = enqueue(setup, event)
    setup.client.current = activity("v2")
    run(setup)
    assert enqueue(setup, event) == identifier
    assert setup.store.get_link("pr_prepared", str(identifier))["source_version"] == "v2"


def test_historical_creation_is_not_imported_but_new_edit_is(setup):
    old = activity(timestamp=time.time() - 3600)
    setup.client.current = old
    enqueue(setup)
    run(setup)
    assert setup.transport.attempts == []
    setup.client.current = {**old, "source_version": "edited", "updated_at": iso(time.time() + 5), "action": "edited"}
    enqueue(setup, key="edit")
    run(setup)
    assert len(setup.transport.attempts) == 1


def test_baseline_survives_restart_pause_and_changes_only_with_destination(setup):
    baseline = activate(setup.store, setup.cfg)
    reopened = AutomationStore(setup.store.path)
    setup.cfg["enabled"] = False
    assert activate(reopened, setup.cfg) == baseline
    setup.cfg["enabled"] = True
    assert activate(reopened, setup.cfg) == baseline
    old_key = mapping_key(setup.cfg)
    setup.cfg["reviews_channel_id"] = "201"
    assert mapping_key(setup.cfg) != old_key
    assert activate(reopened, setup.cfg)["since"] >= baseline["since"]


def test_edit_of_old_review_before_first_scan_is_new_activity(setup):
    setup.client.current = {**activity(source="review", timestamp=time.time() - 86400),
                            "action": "edited", "state": "approved", "body": "Corrected review"}
    identifier = enqueue(setup)
    run(setup)
    assert setup.store.get_job(identifier)["state"] == "done"
    assert len(setup.transport.attempts) == 1
    assert setup.transport.attempts[0]["body"] == "Corrected review"


def test_changed_destination_holds_work_before_source_fetch_or_send(setup):
    identifier = enqueue(setup)
    setup.cfg["reviews_channel_id"] = "201"
    run(setup)
    assert setup.store.get_job(identifier)["state"] == "failed"
    assert setup.transport.attempts == []


def page_job(setup, cycle=123):
    return {"repository": setup.cfg["repository"], "number": 7, "source": "comment", "cursor": None,
            "page": 1, "_sync_cycle": cycle, "_binding": webhook_binding(setup.cfg)}


def test_paged_baseline_does_not_send_history_and_queues_next_page_atomically(setup):
    old = activity(timestamp=time.time() - 3600)
    setup.client.pages[("comment", None)] = {"items": [old], "next_cursor": "next"}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup)))
    assert setup.store.get_link("pr_source", source_key(setup.cfg, old))["version"] == "v1"
    jobs = setup.store.list_jobs()
    assert len(jobs) == 1 and jobs[0]["kind"] == "pr_sync_page"
    assert jobs[0]["payload"]["cursor"] == "next"
    assert setup.transport.attempts == []


def test_old_source_change_is_recovered_without_reliable_updated_timestamp(setup):
    old = activity(timestamp=time.time() - 3600)
    setup.client.pages[("comment", None)] = {"items": [old], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup)))
    updated = {**old, "source_version": "new", "body": "Edited old report"}
    setup.client.pages[("comment", None)] = {"items": [updated], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup, 124)))
    events = [job for job in setup.store.list_jobs() if job["kind"] == "event"]
    assert len(events) == 1 and events[0]["payload"]["source_version"] == "new"


def test_source_checkpoint_rolls_back_when_enqueue_conflicts(setup):
    raw = setup.client.current
    event = {**raw, "_binding": webhook_binding(setup.cfg)}
    spec = setup.service.pr_activity._job(event, 123)
    setup.store.enqueue("event", {"wrong": True}, spec["key"])
    setup.client.pages[("comment", None)] = {"items": [raw], "next_cursor": None}
    with pytest.raises(Conflict):
        asyncio.run(setup.service.pr_activity.sync_page(page_job(setup)))
    assert setup.store.get_link("pr_source", source_key(setup.cfg, raw)) is None


def test_missing_seen_comment_gets_separate_current_source_check(setup):
    old = activity(timestamp=time.time() - 3600)
    setup.client.pages[("comment", None)] = {"items": [old], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup)))
    run(setup)  # Finish the first cycle's bounded missing-source scan.
    setup.client.pages[("comment", None)] = {"items": [], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup, 124)))
    run(setup)
    checks = [job for job in setup.store.list_jobs() if job["kind"] == "pr_sync_check"]
    assert len(checks) == 1
    setup.client.current = {**old, "source_version": "gone", "body": "", "action": "deleted", "deleted": True}
    run(setup)
    assert any(j["kind"] == "event" and j["payload"]["deleted"] for j in setup.store.list_jobs())


def test_missing_scan_is_scoped_bounded_and_resumable(setup, monkeypatch):
    links = []
    for index in range(250):
        event = {**activity(source_id=str(index)), "_binding": webhook_binding(setup.cfg)}
        links.append(("pr_source", source_key(setup.cfg, event), {"event": event, "cycle": 122, "version": "v1"}))
        for other_cfg, other_event in (
            ({**setup.cfg, "reviews_channel_id": "other"}, event),
            (setup.cfg, {**event, "number": 8, "key": f"pr:8:comment:{index}"}),
            (setup.cfg, {**event, "activity_type": "review_comment", "key": f"pr:7:review_comment:{index}"}),
        ):
            links.append(("pr_source", source_key(other_cfg, other_event),
                          {"event": other_event, "cycle": 122, "version": "v1"}))
    setup.store.save_links_with_jobs(links)
    monkeypatch.setattr(setup.store, "list_links", lambda *args: pytest.fail("Unbounded source read"))
    payload = {**page_job(setup), "after": ""}
    asyncio.run(setup.service.pr_activity.sync_missing(payload))
    jobs = setup.store.list_jobs(limit=500)
    assert len([job for job in jobs if job["kind"] == "pr_sync_check"]) == 100
    continuation = [job for job in jobs if job["kind"] == "pr_sync_missing"]
    assert len(continuation) == 1
    # Restart and replay must retain progress and cannot duplicate queued checks.
    setup.service.pr_activity.store = AutomationStore(setup.store.path)
    asyncio.run(setup.service.pr_activity.sync_missing(payload))
    asyncio.run(setup.service.pr_activity.sync_missing(continuation[0]["payload"]))
    jobs = setup.store.list_jobs(limit=500)
    continuation = max((job for job in jobs if job["kind"] == "pr_sync_missing"), key=lambda job: job["id"])
    asyncio.run(setup.service.pr_activity.sync_missing(continuation["payload"]))
    checks = [job for job in setup.store.list_jobs(limit=500) if job["kind"] == "pr_sync_check"]
    assert len(checks) == 250
    assert all(job["payload"]["number"] == 7 and job["payload"]["activity_type"] == "comment"
               and job["payload"]["_binding"] == webhook_binding(setup.cfg) for job in checks)
    checkpoint = setup.store.get_link("pr_missing_checkpoint", f"{mapping_key(setup.cfg)}:7:comment")
    assert checkpoint["complete"]


def test_successful_sync_time_waits_for_all_child_work(setup):
    setup.client.pages[("comment", None)] = {"items": [setup.client.current], "next_cursor": None}
    cycle = setup.store.enqueue("sync", {"repository": setup.cfg["repository"], "_binding": webhook_binding(setup.cfg)}, "sync-test")
    run(setup)
    assert setup.service.status()["last_sync_at"] is None
    for _ in range(20):
        if not run(setup):
            break
    assert setup.store.get_job(cycle)["state"] == "done"
    assert setup.service.status()["last_sync_at"] is not None
    assert setup.service.status()["last_activity_at"] is not None
    assert setup.service.status()["job_counts"] == {"pending": 0, "failed": 0, "recovery": 0, "dismissed": 0}


def test_failed_reconciliation_does_not_claim_success(setup):
    setup.client.pages[("review", None)] = ValueError("cannot read")
    setup.store.enqueue("sync", {"repository": setup.cfg["repository"], "_binding": webhook_binding(setup.cfg)}, "sync-test")
    for _ in range(10):
        if not run(setup):
            break
    assert setup.service.status()["last_sync_at"] is None


def test_explicit_absent_delivery_confirmation_releases_attempt_guard(setup):
    identifier = enqueue(setup)
    setup.transport.fail_after_send = True
    run(setup)
    setup.transport.receipts.clear()
    asyncio.run(setup.service.retry(identifier, user_id="42", confirmed_not_delivered=True))
    assert setup.transport.confirmations[0]["source_version"] == "v1"
    assert setup.store.get_job(identifier)["state"] == "pending"


def test_paused_worker_keeps_jobs_and_baseline(setup):
    identifier = enqueue(setup)
    setup.cfg["enabled"] = False
    assert not run(setup)
    assert setup.store.get_job(identifier)["state"] == "pending"


def test_large_pr_continues_in_another_bounded_job(setup):
    setup.client.pages[("comment", None)] = {"items": [setup.client.current], "next_cursor": "page-21"}
    asyncio.run(setup.service.pr_activity.sync_page({**page_job(setup), "page": 20}))
    next_jobs = [job for job in setup.store.list_jobs() if job["kind"] == "pr_sync_page"]
    assert len(next_jobs) == 1 and next_jobs[0]["payload"]["page"] == 21
    assert setup.client.page_calls == [(7, "comment", None)]


def test_first_head_is_silent_but_a_different_head_is_reconciled(setup):
    first = {**activity("head-a", source="commits", source_id="a" * 40), "after": "a" * 40}
    payload = {**page_job(setup), "source": "commits"}
    setup.client.pages[("commits", None)] = {"items": [first], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(payload))
    assert setup.store.list_jobs() == []
    second = {**first, "key": "pr:7:commits:" + "b" * 40, "source_id": "b" * 40,
              "source_version": "head-b", "after": "b" * 40}
    setup.client.pages[("commits", None)] = {"items": [second], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**payload, "_sync_cycle": 124}))
    job = setup.store.list_jobs()[0]
    assert job["payload"]["before"] == "a" * 40
    assert job["payload"]["after"] == "b" * 40


def test_new_unresolved_thread_does_not_announce_reopening(setup):
    item = {**activity(source="thread"), "state": "unresolved"}
    setup.client.pages[("thread", None)] = {"items": [item], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**page_job(setup), "source": "thread"}))
    assert setup.store.list_jobs() == []


def test_force_push_return_to_previous_head_is_a_new_occurrence(setup):
    before = "x" * 40
    for index, head in enumerate(("a" * 40, "b" * 40, "a" * 40, "b" * 40)):
        setup.client.current = {**activity(head, source="commits", source_id=head),
                                "before": before, "after": head, "action": "synchronize"}
        enqueue(setup, key=f"push:{index}")
        run(setup)
        # A different webhook delivery for the same observed head is a replay.
        enqueue(setup, key=f"push:{index}:duplicate")
        run(setup)
        before = head
    assert len(setup.transport.attempts) == 4
    assert [event["head_revision"] for event in setup.transport.attempts] == [1, 2, 3, 4]
    assert len({event["delivery_key"] for event in setup.transport.attempts}) == 4


def test_reconciled_head_and_webhook_share_the_same_occurrence(setup):
    payload = {**page_job(setup), "source": "commits"}
    first = {**activity("a", source="commits", source_id="a" * 40), "after": "a" * 40}
    setup.client.pages[("commits", None)] = {"items": [first], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(payload))
    second = {**activity("b", source="commits", source_id="b" * 40), "after": "b" * 40}
    setup.client.pages[("commits", None)] = {"items": [second], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**payload, "_sync_cycle": 124}))
    setup.client.current = {**second, "action": "synchronize", "before": "a" * 40}
    run(setup)
    enqueue(setup)
    run(setup)
    assert len(setup.transport.attempts) == 1
    assert setup.transport.attempts[0]["head_revision"] == 2


@pytest.mark.parametrize("first_by_sync", [False, True])
def test_stale_synchronize_cannot_claim_a_reused_head(setup, first_by_sync):
    start = time.time() + 5
    first = {**activity("a", source="commits", source_id="a" * 40, timestamp=start),
             "before": "x" * 40, "after": "a" * 40, "action": "synchronize", "author": "first",
             "observed_updated_at": iso(start)}
    if first_by_sync:
        setup.client.pages[("commits", None)] = {"items": [{**first, "created_at": "", "updated_at": ""}], "next_cursor": None}
        asyncio.run(setup.service.pr_activity.sync_page({**page_job(setup), "source": "commits"}))
    else:
        setup.client.current = first
        enqueue(setup, key="first-a")
        run(setup)
    second = {**activity("b", source="commits", source_id="b" * 40, timestamp=start + 10),
              "before": "a" * 40, "after": "b" * 40, "action": "synchronize", "author": "second",
              "observed_updated_at": iso(start + 10)}
    setup.client.current = second
    enqueue(setup, key="then-b")
    run(setup)
    count = len(setup.transport.attempts)
    # Current GitHub head is A again, but this previously delayed A webhook
    # carries the old transition and author. Matching the SHA is insufficient.
    setup.client.current = {**first, "observed_updated_at": iso(start + 20)}
    enqueue(setup, key="delayed-first-a")
    run(setup)
    assert len(setup.transport.attempts) == count
    third = {**first, "before": "b" * 40, "author": "third", "updated_at": iso(start + 20),
             "observed_updated_at": iso(start + 20)}
    setup.client.current = third
    enqueue(setup, key="genuine-new-a")
    run(setup)
    assert len(setup.transport.attempts) == count + 1
    assert setup.transport.attempts[-1]["before"] == "b" * 40
    assert setup.transport.attempts[-1]["author"] == "third"


def test_webhook_enriches_reconciled_occurrence_without_losing_metadata_on_next_scan(setup):
    payload = {**page_job(setup), "source": "commits"}
    first = {**activity("a", source="commits", source_id="a" * 40), "after": "a" * 40,
             "created_at": "", "updated_at": "", "author": ""}
    setup.client.pages[("commits", None)] = {"items": [first], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(payload))
    second = {**first, "key": "pr:7:commits:" + "b" * 40, "source_id": "b" * 40,
              "source_version": "b", "after": "b" * 40, "before": "a" * 40, "action": "reconciled"}
    setup.client.pages[("commits", None)] = {"items": [second], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**payload, "_sync_cycle": 124}))
    setup.client.current = second
    run(setup)
    setup.client.current = {**second, "author": "known-pusher", "updated_at": iso(time.time() + 10), "action": "synchronize"}
    enqueue(setup)
    run(setup)
    before, after = setup.transport.attempts
    assert before["delivery_key"] == after["delivery_key"]
    assert before["source_version"] != after["source_version"]
    asyncio.run(setup.service.pr_activity.sync_page({**payload, "_sync_cycle": 125}))
    from project_automation_activity import head_key
    assert setup.store.get_link("pr_head", head_key(setup.cfg, second))["event"]["author"] == "known-pusher"


def test_late_old_webhook_cannot_enrich_a_reconciled_return_to_previous_sha(setup):
    start = time.time() + 5
    events = []
    for index, (before, after) in enumerate((("x", "a"), ("a", "b"))):
        current = {**activity(after, source="commits", source_id=after * 40, timestamp=start + index * 10),
                   "before": before * 40, "after": after * 40, "action": "synchronize",
                   "observed_updated_at": iso(start + index * 10)}
        events.append(current)
        setup.client.current = current
        enqueue(setup, key=f"original:{index}")
        run(setup)
    returned = {**events[0], "before": "b" * 40, "created_at": "", "updated_at": "", "author": "",
                "action": "reconciled", "observed_updated_at": iso(start + 20)}
    setup.client.current = returned
    setup.client.pages[("commits", None)] = {"items": [returned], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**page_job(setup), "source": "commits"}))
    run(setup)
    assert len(setup.transport.attempts) == 3
    setup.client.current = {**events[0], "observed_updated_at": iso(start + 20)}
    enqueue(setup, key="late-original-a")
    run(setup)
    assert len(setup.transport.attempts) == 3
    assert setup.transport.attempts[-1]["before"] == "b" * 40
    assert setup.transport.attempts[-1]["author"] == ""


def test_historical_synchronize_delivery_does_not_import_old_code_activity(setup):
    setup.client.current = {**activity("a", source="commits", source_id="a" * 40, timestamp=time.time() - 86400),
                            "after": "a" * 40, "action": "synchronize"}
    enqueue(setup)
    run(setup)
    assert setup.transport.attempts == []


def test_pr_closed_during_outage_is_discovered_and_discussion_recovered(setup):
    async def no_open_items(kind):
        return []
    setup.client.list_open_items = no_open_items
    setup.client.updated_pages[None] = {"items": [{"number": 7, "state": "closed"}], "next_cursor": "next"}
    setup.client.pages[("comment", None)] = {"items": [setup.client.current], "next_cursor": None}
    cycle = setup.store.enqueue("sync", {"repository": setup.cfg["repository"], "_binding": webhook_binding(setup.cfg)}, "closed-outage")
    run(setup)
    assert setup.store.list_links("mirror") == []
    run(setup)  # First discovery page atomically persists its source work and continuation.
    assert setup.store.get_link("pr_discovery_checkpoint", mapping_key(setup.cfg)) is None
    for _ in range(20):
        if not run(setup):
            break
    assert len(setup.transport.attempts) == 1
    checkpoint = setup.store.get_link("pr_discovery_checkpoint", mapping_key(setup.cfg))
    assert checkpoint["completed_through"] == setup.store.get_job(cycle)["created_at"]
    assert setup.service.status()["last_sync_at"] is not None


def cycle_with_child(setup, child_state, *, root_state="done"):
    """Store a sync root and one of its PR page jobs in the given states."""
    root = setup.store.enqueue("sync", {"repository": setup.cfg["repository"],
                                        "_binding": webhook_binding(setup.cfg)}, "sync:earlier")
    child = setup.store.enqueue("pr_sync_page", page_job(setup, root), f"pr-page:{root}:7:comment:1")
    with closing(sqlite3.connect(setup.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = ? WHERE id = ?", (root_state, root))
        connection.execute("UPDATE jobs SET state = ? WHERE id = ?", (child_state, child))
    return root, child


def sync_roots(store):
    return [job["id"] for job in store.list_jobs(limit=500) if job["kind"] == "sync"]


@pytest.mark.parametrize("state", ["pending", "running", "inflight"])
def test_no_new_sync_root_while_a_cycle_has_live_work(setup, state):
    root, _ = cycle_with_child(setup, state)
    assert setup.service.queue_sync_cycle() is None
    assert sync_roots(setup.store) == [root]


def test_live_sync_root_blocks_the_next_cycle(setup):
    root, _ = cycle_with_child(setup, "done", root_state="pending")
    assert setup.service.queue_sync_cycle() is None
    assert sync_roots(setup.store) == [root]


@pytest.mark.parametrize("state", ["failed", "recovery"])
def test_failed_or_held_cycle_work_does_not_block_the_next_cycle(setup, state):
    root, _ = cycle_with_child(setup, state)
    queued = setup.service.queue_sync_cycle()
    assert queued is not None and queued != root
    assert sync_roots(setup.store) == [queued, root]


def test_restart_mid_cycle_queues_no_duplicate_root(setup):
    root, _ = cycle_with_child(setup, "pending")
    restarted = AutomationService(AutomationStore(setup.store.path), settings=lambda: setup.cfg,
                                  github_factory=lambda _: setup.client, transport=setup.transport)
    restarted.bots["Helper"] = SimpleNamespace(name="Helper")
    assert restarted.queue_sync_cycle() is None
    assert sync_roots(setup.store) == [root]


def test_next_root_waits_for_the_interval_and_an_idle_queue(setup, monkeypatch):
    import project_automation
    clock = [50_000.0]
    monkeypatch.setattr(project_automation, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))
    first = setup.service.queue_sync_cycle()
    assert sync_roots(setup.store) == [first]
    clock[0] += 1000
    assert setup.service.queue_sync_cycle() is None
    with closing(sqlite3.connect(setup.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done'")
    clock[0] -= 101
    assert setup.service.queue_sync_cycle() is None
    clock[0] += 102
    second = setup.service.queue_sync_cycle()
    assert second is not None and sync_roots(setup.store) == [second, first]


def stored_source(setup, source_id, cycle, version="v1"):
    event = {**activity(version, timestamp=time.time() - 3600, source_id=source_id),
             "_binding": webhook_binding(setup.cfg)}
    record = {"event": event, "version": version}
    if cycle is not None:
        record["cycle"] = cycle
    setup.store.put_link("pr_source", source_key(setup.cfg, event), record)
    return event


def test_missing_scan_checks_only_records_stamped_by_older_cycles(setup):
    for source_id, cycle in (("newer", 125), ("current", 124), ("older", 123), ("unstamped", None)):
        stored_source(setup, source_id, cycle)
    asyncio.run(setup.service.pr_activity.sync_missing({**page_job(setup, 124), "after": ""}))
    checked = sorted(job["payload"]["source_id"] for job in setup.store.list_jobs()
                     if job["kind"] == "pr_sync_check")
    assert checked == ["older", "unstamped"]


def test_page_and_check_never_lower_a_stored_stamp(setup):
    newer = stored_source(setup, "10", 125)
    setup.client.pages[("comment", None)] = {"items": [newer], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup, 124)))
    assert setup.store.get_link("pr_source", source_key(setup.cfg, newer))["cycle"] == 125
    setup.client.current = newer
    asyncio.run(setup.service.pr_activity.sync_check({**newer, "_sync_cycle": 123}))
    assert setup.store.get_link("pr_source", source_key(setup.cfg, newer))["cycle"] == 125
    asyncio.run(setup.service.pr_activity.sync_page(page_job(setup, 126)))
    assert setup.store.get_link("pr_source", source_key(setup.cfg, newer))["cycle"] == 126


def test_page_never_lowers_a_stored_head_stamp(setup):
    from project_automation_activity import head_key
    head = {**activity("a", source="commits", source_id="a" * 40), "after": "a" * 40}
    setup.client.pages[("commits", None)] = {"items": [head], "next_cursor": None}
    payload = {**page_job(setup, 125), "source": "commits"}
    asyncio.run(setup.service.pr_activity.sync_page(payload))
    asyncio.run(setup.service.pr_activity.sync_page({**payload, "_sync_cycle": 124}))
    assert setup.store.get_link("pr_head", head_key(setup.cfg, head))["cycle"] == 125
    assert setup.store.get_link("pr_source", source_key(setup.cfg, head))["cycle"] == 125


def test_older_cycle_delivery_keeps_newer_stamps(setup):
    from project_automation_activity import head_key
    stored = stored_source(setup, "10", 125)
    setup.client.current = {**stored, "source_version": "v2", "body": "Edited"}
    enqueue(setup, {**setup.client.current, "_sync_cycle": 124}, key="older-cycle-event")
    run(setup)
    assert len(setup.transport.attempts) == 1
    assert setup.store.get_link("pr_source", source_key(setup.cfg, stored))["cycle"] == 125
    first = {**activity("a", source="commits", source_id="a" * 40), "after": "a" * 40}
    setup.client.pages[("commits", None)] = {"items": [first], "next_cursor": None}
    asyncio.run(setup.service.pr_activity.sync_page({**page_job(setup, 126), "source": "commits"}))
    second = {**activity("b", source="commits", source_id="b" * 40), "after": "b" * 40,
              "before": "a" * 40, "action": "synchronize"}
    setup.client.current = second
    enqueue(setup, {**second, "_sync_cycle": 124}, key="older-cycle-head")
    run(setup)
    assert len(setup.transport.attempts) == 2
    assert setup.store.get_link("pr_head", head_key(setup.cfg, second))["cycle"] == 126


def test_worker_applies_retention_while_paused(setup, monkeypatch):
    import project_automation
    finished = setup.store.enqueue("event", {}, "github:finished")
    held = setup.store.enqueue("event", {}, "github:held")
    with closing(sqlite3.connect(setup.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (finished,))
        connection.execute("UPDATE jobs SET state = 'recovery' WHERE id = ?", (held,))
    setup.cfg["enabled"] = False

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(project_automation.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(setup.service._run())
    assert setup.store.get_job(finished) is None
    assert setup.store.get_job(held)["state"] == "recovery"


def test_first_start_cleans_up_the_old_queue_before_claiming_work(setup, monkeypatch):
    import project_automation
    stale_root = setup.store.enqueue("sync", {"repository": setup.cfg["repository"],
                                              "_binding": webhook_binding(setup.cfg)}, "sync:old")
    stale_page = setup.store.enqueue("pr_sync_page", page_job(setup, stale_root), f"pr-page:{stale_root}:7:comment:1")
    delivery = enqueue(setup, key="github:kept")
    logged = []
    monkeypatch.setattr(project_automation, "info", lambda message, **fields: logged.append((message, fields)))

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(project_automation.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(setup.service._run())
    assert setup.store.get_job(stale_root) is None and setup.store.get_job(stale_page) is None
    assert setup.client.page_calls == []
    assert setup.store.get_job(delivery)["state"] == "done"
    assert len(sync_roots(setup.store)) == 1 and stale_root not in sync_roots(setup.store)
    assert len(logged) == 1 and "2 queued reconciliation jobs" in logged[0][0]
    with closing(sqlite3.connect(setup.store.path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


DAY = 86400


def mirror_post(setup, kind, number):
    setup.store.put_link("mirror", f"100:Helper:200:Owner/Project:{kind}:{number}", {
        "kind": kind, "number": number, "repository": "Owner/Project", "guild_id": "100",
        "bot_name": "Helper", "channel_id": "300", "message_id": "300"})


def github_item(kind, number, *, closed_days_ago=None):
    item = {"kind": kind, "number": number, "title": "Old item", "body": "", "state": "open", "closed": False,
            "html_url": f"https://github.com/Owner/Project/{'pull' if kind == 'pull' else 'issues'}/{number}"}
    if closed_days_ago is not None:
        item.update(state="closed", closed=True, closed_at=iso(time.time() - closed_days_ago * DAY))
    return item


def closed_world(setup, items, open_items=()):
    fetched = []

    async def get_issue(number):
        fetched.append(number)
        return deepcopy(items[number])

    async def list_open_items(kind):
        return [deepcopy(item) for item in open_items if item["kind"] == kind]

    setup.client.get_issue = get_issue
    setup.client.list_open_items = list_open_items
    for item in items.values():
        mirror_post(setup, item["kind"], item["number"])
    return fetched


ROOT_NUMBERS = itertools.count(1)


def run_sync_root(setup):
    root = setup.store.enqueue("sync", {"repository": setup.cfg["repository"],
                                        "_binding": webhook_binding(setup.cfg)}, f"sync:test:{next(ROOT_NUMBERS)}")
    asyncio.run(setup.service.process_job(setup.store.get_job(root)))
    refreshed = sorted(job["payload"]["number"] for job in setup.store.list_jobs(limit=500)
                       if job["kind"] == "event" and job["key"].startswith(f"sync-item:{root}:"))
    walked = sorted({job["payload"]["number"] for job in setup.store.list_jobs(limit=500)
                     if job["kind"] == "pr_sync_page" and job["payload"]["_sync_cycle"] == root})
    return refreshed, walked


def test_items_closed_past_the_cutoff_cost_no_fetch_refresh_or_walk(setup):
    fetched = closed_world(setup, {8: github_item("pull", 8, closed_days_ago=8),
                                   9: github_item("issue", 9, closed_days_ago=8)})
    for kind, number in (("pull", 8), ("issue", 9)):
        setup.store.put_link("item_closed", f"owner/project:{kind}:{number}", {"closed_at": time.time() - 8 * DAY})
    assert run_sync_root(setup) == ([], [])
    assert fetched == []


def test_recently_closed_items_are_still_refreshed_and_walked(setup):
    fetched = closed_world(setup, {8: github_item("pull", 8, closed_days_ago=2),
                                   9: github_item("issue", 9, closed_days_ago=2)})
    for kind, number in (("pull", 8), ("issue", 9)):
        setup.store.put_link("item_closed", f"owner/project:{kind}:{number}", {"closed_at": time.time() - 2 * DAY})
    assert run_sync_root(setup) == ([8, 9], [8])
    assert sorted(fetched) == [8, 9]


def test_unknown_close_time_costs_one_fetch_and_is_then_stored(setup):
    fetched = closed_world(setup, {8: github_item("pull", 8, closed_days_ago=8),
                                   9: github_item("issue", 9, closed_days_ago=2)})
    assert run_sync_root(setup) == ([9], [])
    assert sorted(fetched) == [8, 9]
    stored = setup.store.get_link("item_closed", "owner/project:pull:8")["closed_at"]
    assert abs(stored - (time.time() - 8 * DAY)) < 60
    fetched.clear()
    assert run_sync_root(setup) == ([9], [])
    assert fetched == [9]


def test_close_time_without_closed_at_counts_from_when_it_was_first_seen_closed(setup, monkeypatch):
    import project_automation
    start = time.time()
    clock = [start]
    monkeypatch.setattr(project_automation, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    item = {**github_item("issue", 9, closed_days_ago=0), "closed_at": ""}
    fetched = closed_world(setup, {9: item})
    refreshed = []
    for day in (0, 3, 6, 9):
        clock[0] = start + day * DAY
        refreshed.append(run_sync_root(setup)[0])
    assert refreshed == [[9], [9], [9], []]
    assert fetched == [9, 9, 9]
    assert setup.store.get_link("item_closed", "owner/project:issue:9") == {"closed_at": start}


def test_reopened_item_returns_to_scope_and_loses_its_close_time(setup):
    old = github_item("pull", 8, closed_days_ago=30)
    fetched = closed_world(setup, {8: old}, open_items=[github_item("pull", 8)])
    setup.store.put_link("item_closed", "owner/project:pull:8", {"closed_at": time.time() - 30 * DAY})
    assert run_sync_root(setup) == ([8], [8])
    assert setup.store.get_link("item_closed", "owner/project:pull:8") is None
    assert fetched == []


def gone_world(setup, number, status, items, open_items=()):
    """A closed_world in which GitHub answers the fetch of issue `number` with HTTP `status`."""
    fetched = closed_world(setup, items, open_items)
    mirror_post(setup, "issue", number)
    found = setup.client.get_issue

    async def get_issue(requested):
        if requested == number:
            fetched.append(requested)
            raise GitHubError(f"GitHub returned HTTP {status}.", status=status,
                              retryable=status in {403, 429} or status >= 500)
        return await found(requested)

    setup.client.get_issue = get_issue
    return fetched


def discovery_jobs(setup):
    return [job for job in setup.store.list_jobs(limit=500) if job["kind"] == "pr_sync_discover"]


@pytest.mark.parametrize("status", [301, 404, 410])
def test_a_moved_or_deleted_item_leaves_the_sync_without_stopping_it(setup, monkeypatch, status):
    import project_automation
    logged = []
    monkeypatch.setattr(project_automation, "info", lambda message, **fields: logged.append(message))
    fetched = gone_world(setup, 9, status, {8: github_item("pull", 8, closed_days_ago=2)},
                         open_items=[github_item("issue", 5)])
    assert run_sync_root(setup) == ([5, 8], [8])
    assert sorted(fetched) == [8, 9]
    assert len(discovery_jobs(setup)) == 1
    assert [message for message in logged if "#9" in message] == [
        "Project sync no longer refreshes issue #9: GitHub reports it moved or deleted."]
    fetched.clear()
    assert run_sync_root(setup) == ([5, 8], [8])
    assert fetched == [8]
    assert len(discovery_jobs(setup)) == 2

    async def listed_again(kind):
        return [github_item("issue", 9)] if kind == "issue" else []

    setup.client.list_open_items = listed_again
    assert run_sync_root(setup)[0] == [8, 9]


@pytest.mark.parametrize("status", [None, 403, 429, 500, 502])
def test_a_failed_fetch_of_a_closed_item_still_fails_and_retries_the_root(setup, status):
    gone_world(setup, 9, status or 500, {}, open_items=[github_item("issue", 5)])
    if status is None:
        async def unreachable(number):
            raise GitHubError("GitHub could not be reached or returned an invalid response.", retryable=True)
        setup.client.get_issue = unreachable
    root = setup.store.enqueue("sync", {"repository": setup.cfg["repository"],
                                        "_binding": webhook_binding(setup.cfg)}, "sync:transient")
    assert run(setup)
    job = setup.store.get_job(root)
    assert job["state"] == "pending" and job["attempts"] == 1 and job["available_at"] > time.time()
    assert discovery_jobs(setup) == []
    assert setup.store.get_link("item_closed", "owner/project:issue:9") is None


def test_attach_during_the_first_start_cleanup_waits_before_writing(setup, monkeypatch):
    import contextlib
    import threading
    from unittest.mock import AsyncMock
    import project_automation
    case = setup.store.create_case({"channel_id": "1", "reporter_id": "2", "guild_id": "100", "bot_name": "Helper",
                                    "repository": "Owner/Project", "feedback_channel_id": "", "state": "assessing",
                                    "transcript": []}, "source:waiting")
    started, release = threading.Event(), threading.Event()
    real_cleanup, real_connection = setup.store.clean_up_old_queue, setup.store._connection
    loop_thread, loop_writes = threading.get_ident(), []

    def slow_cleanup(history_days=0):
        started.set()
        assert release.wait(5)
        return real_cleanup(history_days)

    @contextlib.contextmanager
    def watched(write=False, timeout=10):
        if write and threading.get_ident() == loop_thread:
            loop_writes.append(release.is_set())
        with real_connection(write=write, timeout=timeout) as connection:
            yield connection

    monkeypatch.setattr(setup.store, "clean_up_old_queue", slow_cleanup)
    monkeypatch.setattr(setup.store, "_connection", watched)
    monkeypatch.setattr(setup.service, "_run", lambda: asyncio.sleep(0))
    monkeypatch.setattr(project_automation, "info", lambda *args, **kwargs: None)
    setup.transport.attach = AsyncMock()

    async def scenario():
        first = asyncio.create_task(setup.service.attach(SimpleNamespace(name="Other")))
        assert await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(setup.service.attach(SimpleNamespace(name="Helper")))
        await asyncio.sleep(0.2)
        assert not first.done() and not second.done()
        assert loop_writes == []
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 10)

    asyncio.run(scenario())
    assert loop_writes and all(loop_writes)
    assert setup.transport.attach.await_count == 2
    assert any(job["key"] == f"initial:{case['id']}" for job in setup.store.list_jobs())


@pytest.mark.parametrize("history_days,kept", [(30, True), (0, False)])
@pytest.mark.parametrize("deletion", ["sweep", "first start"])
def test_saved_history_days_reach_the_sweep_and_the_first_start_cleanup(setup, monkeypatch, deletion,
                                                                        history_days, kept):
    import project_automation
    monkeypatch.setattr(project_automation, "info", lambda *args, **kwargs: None)
    setup.cfg["history_days"] = history_days
    finished = setup.store.enqueue("event", {}, "github:yesterday")
    yesterday = time.time() - DAY
    with closing(sqlite3.connect(setup.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done', created_at = ?, updated_at = ?, available_at = ? "
                           "WHERE id = ?", (yesterday, yesterday, yesterday, finished))
    asyncio.run(setup.service.maintain() if deletion == "sweep" else setup.service.clean_up_old_queue())
    assert (setup.store.get_job(finished) is not None) is kept


def test_worker_sweeps_at_most_every_ten_minutes(setup, monkeypatch):
    import project_automation
    clock = [10_000.0]
    monkeypatch.setattr(project_automation, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    sweeps = []

    async def maintain():
        sweeps.append(clock[0])

    steps = iter([300, 299, 1, 600, 0])

    async def sleep(_):
        try:
            clock[0] += next(steps)
        except StopIteration:
            raise asyncio.CancelledError from None

    monkeypatch.setattr(setup.service, "maintain", maintain)
    monkeypatch.setattr(project_automation.asyncio, "sleep", sleep)
    setup.service._queue_cleaned = True
    setup.cfg["enabled"] = False
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(setup.service._run())
    assert sweeps == [10_000.0, 10_600.0, 11_200.0]


def test_discovery_window_survives_restart_and_failed_scan(setup):
    baseline = activate(setup.store, setup.cfg)
    cycle = setup.store.enqueue("sync", {"_binding": webhook_binding(setup.cfg)}, "discovery-window")
    setup.service.pr_activity.enqueue_discovery(cycle)
    initial = next(job for job in setup.store.list_jobs() if job["kind"] == "pr_sync_discover")
    assert initial["payload"]["since"] == baseline["since"]
    setup.store.put_link("pr_discovery_checkpoint", mapping_key(setup.cfg), {"completed_through": time.time() + 10})
    setup.service.pr_activity.store = AutomationStore(setup.store.path)
    setup.service.pr_activity.enqueue_discovery(cycle)
    assert setup.store.get_job(initial["id"])["payload"] == initial["payload"]
