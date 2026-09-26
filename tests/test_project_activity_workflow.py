"""Real SQLite workflow checks with no external messages or GitHub writes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import sys
import time
from types import SimpleNamespace

import pytest

from project_automation import AutomationService
from project_automation_activity import activate, mapping_key, source_key
from project_automation_config import PROJECT_DEFAULTS
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
    assert setup.service.status()["job_counts"] == {"pending": 0, "failed": 0, "recovery": 0}


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
