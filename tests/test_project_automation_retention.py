"""Retention, bulk deletion, and compaction checks against real SQLite files."""

from contextlib import closing
import json
import sqlite3

import pytest

from project_automation_store import AutomationStore


DAY = 86400.0
NOW = 1_800_000_000.0


@pytest.fixture
def clock(monkeypatch):
    now = [NOW]
    monkeypatch.setattr("project_automation_store.time.time", lambda: now[0])
    return now


@pytest.fixture
def store(tmp_path, clock):
    return AutomationStore(tmp_path / "retention.sqlite3")


def add_job(store, key, *, kind="event", state="done", payload=None, age_days=0.0):
    stamp = NOW - age_days * DAY
    with closing(sqlite3.connect(store.path)) as connection, connection:
        return connection.execute(
            "INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, key, json.dumps(payload or {}), state, stamp, stamp, stamp),
        ).lastrowid


def add_cycle(store, name, child_states, *, age_days=0.0, root_state="done"):
    root = add_job(store, f"sync:{name}", kind="sync", state=root_state, age_days=age_days)
    children = [add_job(store, f"pr-page:{name}:{index}", kind="pr_sync_page", state=state,
                        payload={"_sync_cycle": root}, age_days=age_days)
                for index, state in enumerate(child_states)]
    return [root, *children]


def add_link(store, kind, key, document=None, *, age_days=0.0):
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("INSERT INTO links(kind, key, document, updated_at) VALUES (?, ?, ?, ?)",
                           (kind, str(key), json.dumps(document or {}), NOW - age_days * DAY))


def job_ids(store):
    with closing(sqlite3.connect(store.path)) as connection:
        return {row[0] for row in connection.execute("SELECT id FROM jobs")}


def link_keys(store, kind):
    with closing(sqlite3.connect(store.path)) as connection:
        return {row[0] for row in connection.execute("SELECT key FROM links WHERE kind = ?", (kind,))}


def prune_all(store, history_days=0):
    total = 0
    while (deleted := store.prune_jobs(history_days)):
        total += deleted
    return total


def test_retention_without_history_keeps_only_live_held_recent_failures_and_open_cycles(store):
    live_cycle = add_cycle(store, "live", ["done", "pending"])
    finished_cycle = add_cycle(store, "finished", ["done", "done"])
    failed_cycle = add_cycle(store, "failed-child", ["done", "failed"])
    held_cycle = add_cycle(store, "held-child", ["recovery"])
    running_root = add_cycle(store, "running-root", ["done"], root_state="running")
    webhook = add_job(store, "github:done")
    feedback = add_job(store, "notify:case:2", kind="notify")
    kept = {state: add_job(store, f"other:{state}", state=state)
            for state in ("pending", "running", "inflight", "recovery")}
    recent_failure = add_job(store, "failure:recent", state="failed", age_days=6.9)
    old_failure = add_job(store, "failure:old", state="failed", age_days=7.1)
    recent_cancel = add_job(store, "cancelled:recent", state="cancelled", age_days=6.9)
    old_cancel = add_job(store, "cancelled:old", state="cancelled", age_days=7.1)

    # finished cycle, failed-child cycle, held-child root, webhook, feedback, old failure, old cancel
    assert prune_all(store) == 3 + 2 + 1 + 1 + 1 + 1 + 1
    assert job_ids(store) == {
        *live_cycle, failed_cycle[2], held_cycle[1], *running_root, *kept.values(), recent_failure, recent_cancel,
    }
    assert not {*finished_cycle, webhook, feedback, old_failure, old_cancel} & job_ids(store)


def test_history_days_keep_finished_jobs_that_long_and_failures_at_least_as_long(store):
    young = add_job(store, "github:young", age_days=29)
    old = add_job(store, "github:old", age_days=31)
    young_cycle = add_cycle(store, "young", ["done"], age_days=29)
    old_cycle = add_cycle(store, "old", ["done"], age_days=31)
    old_open_cycle = add_cycle(store, "old-open", ["done", "pending"], age_days=31)
    failure_inside = add_job(store, "failure:inside", state="failed", age_days=29)
    failure_outside = add_job(store, "failure:outside", state="failed", age_days=31)

    assert prune_all(store, history_days=30) == 1 + 2 + 1
    assert job_ids(store) == {young, *young_cycle, *old_open_cycle, failure_inside}
    assert not {old, *old_cycle, failure_outside} & job_ids(store)


def test_deleted_jobs_take_their_dismissal_and_prepared_links(store):
    failure = add_job(store, "failure:old", state="failed", age_days=8)
    delivered = add_job(store, "pr-event:1:done")
    held = add_job(store, "pr-event:1:held", state="recovery")
    add_link(store, "job_dismissal", failure, {"actor": "dashboard"})
    add_link(store, "pr_prepared", delivered, {"kind": "pr_activity"})
    add_link(store, "pr_prepared", held, {"kind": "pr_activity"})
    assert prune_all(store) == 2
    assert link_keys(store, "job_dismissal") == set()
    assert link_keys(store, "pr_prepared") == {str(held)}


def test_prune_batches_respect_the_bound(store):
    for index in range(1_200):
        add_job(store, f"github:{index}")
    assert [store.prune_jobs(0) for _ in range(4)] == [500, 500, 200, 0]
    assert store.prune_jobs(0, limit=10) == 0


def test_ids_stay_above_the_deleted_maximum(store):
    store.enqueue("event", {}, "first")
    highest = store.enqueue("event", {}, "highest")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done'")
    assert prune_all(store) == 2
    assert store.enqueue("event", {}, "after-retention") > highest


def test_link_sweep_expires_markers_cycle_bookkeeping_and_old_release_posts(store):
    add_link(store, "github_delivery", "a" * 64, age_days=4.1)
    add_link(store, "github_delivery", "b" * 64, age_days=3.9)
    live_root = add_job(store, "sync:live", kind="sync", state="pending")
    mapping = "c" * 64
    for cycle in (live_root, 999_999):
        add_link(store, "pr_discovery_cycle", cycle, {"since": 1})
        add_link(store, "pr_discovery_page", f"{mapping}:{cycle}", {"complete": True})
    posts = {"release-old": ("general", 8), "release-new": ("general", 6), "push-old": ("commit", 8),
             "pull-old": ("pull", 30), "issue-old": ("issue", 30)}
    for key, (kind, age) in posts.items():
        add_link(store, "mirror", key, {"kind": kind}, age_days=age)
        add_link(store, "mirror_attempt", key, {"state": "delivered"}, age_days=age)
    while store.prune_links(0):
        pass
    assert link_keys(store, "github_delivery") == {"b" * 64}
    assert link_keys(store, "pr_discovery_cycle") == {str(live_root)}
    assert link_keys(store, "pr_discovery_page") == {f"{mapping}:{live_root}"}
    kept_posts = {"release-new", "pull-old", "issue-old"}
    assert link_keys(store, "mirror") == link_keys(store, "mirror_attempt") == kept_posts


def test_post_records_follow_a_longer_history_setting(store):
    add_link(store, "mirror", "release-old", {"kind": "general"}, age_days=8)
    add_link(store, "mirror", "release-older", {"kind": "general"}, age_days=31)
    while store.prune_links(30):
        pass
    assert link_keys(store, "mirror") == {"release-old"}


def test_link_batches_respect_the_bound(store):
    for index in range(700):
        add_link(store, "github_delivery", f"{index:064d}", age_days=5)
    assert [store.prune_links(0) for _ in range(3)] == [500, 200, 0]


def test_redelivery_inside_four_days_queues_nothing_after_retention(store, clock):
    first = store.enqueue_delivery("delivery-1", "event", {"kind": "issue"}, "github:delivery-1")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'done'")
    assert prune_all(store) == 1
    clock[0] += 3.9 * DAY
    assert not store.prune_links(0)
    assert store.enqueue_delivery("delivery-1", "event", {"kind": "issue"}, "github:delivery-1") is None
    clock[0] += 0.2 * DAY
    assert store.prune_links(0) == 1
    assert store.enqueue_delivery("delivery-1", "event", {"kind": "issue"}, "github:delivery-1") > first
