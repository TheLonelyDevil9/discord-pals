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
    add_finished_rows(store, 1_200)
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
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.executemany("INSERT INTO links(kind, key, document, updated_at) VALUES ('github_delivery', ?, '{}', ?)",
                               ((f"{index:064d}", NOW - 5 * DAY) for index in range(700)))
    assert [store.prune_links(0) for _ in range(3)] == [500, 200, 0]


def add_finished_rows(store, count, *, body_bytes=60):
    body = "x" * body_bytes
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.executemany(
            "INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
            "VALUES ('event', ?, ?, 'done', ?, ?, ?)",
            ((f"github:bulk:{index}", json.dumps({"body": body}), NOW, NOW, NOW) for index in range(count)))


def add_kept_rows(store):
    """Store one row per surviving state, with notes and a dismissal, and return them exactly."""
    ids = [add_job(store, f"kept:{state}", state=state, payload={"body": f"{state} payload"})
           for state in ("pending", "running", "inflight", "recovery", "failed")]
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET recovery_notes = ?, last_error = 'kept error', attempts = 2, "
                           "lease_token = 'token' WHERE id IN (?, ?, ?, ?, ?)",
                           (json.dumps([{"actor": "dashboard", "outcome": "dismiss"}]), *ids))
    add_link(store, "job_dismissal", ids[-1], {"actor": "dashboard"})
    return snapshot_rows(store, ids)


def snapshot_rows(store, ids):
    with closing(sqlite3.connect(store.path)) as connection:
        marks = ",".join("?" * len(ids))
        return connection.execute(f"SELECT * FROM jobs WHERE id IN ({marks}) ORDER BY id", ids).fetchall()


def sequence(store):
    with closing(sqlite3.connect(store.path)) as connection:
        return connection.execute("SELECT seq FROM sqlite_sequence WHERE name = 'jobs'").fetchone()


def trace_job_deletes(monkeypatch):
    statements = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(
            lambda sql: statements.append(" ".join(sql.split())) if sql.lstrip().upper().startswith("DELETE FROM JOBS") else None)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return statements


def test_large_backlog_is_cleared_in_one_step_and_kept_rows_stay_exact(store, monkeypatch):
    add_finished_rows(store, 20_100)
    expired = [add_job(store, f"failure:old:{index}", state="failed", age_days=8) for index in range(3)]
    add_link(store, "job_dismissal", expired[0], {"actor": "dashboard"})
    kept = add_kept_rows(store)
    before = sequence(store)
    deletes = trace_job_deletes(monkeypatch)
    assert store.prune_jobs(0) == 20_103
    assert deletes == ["DELETE FROM jobs"]
    assert snapshot_rows(store, [row[0] for row in kept]) == kept
    assert job_ids(store) == {row[0] for row in kept}
    assert sequence(store) == before
    assert link_keys(store, "job_dismissal") == {str(kept[-1][0])}
    assert store.enqueue("event", {}, "after-bulk") > before[0]


def test_small_backlog_is_deleted_by_id(store, monkeypatch):
    add_finished_rows(store, 50)
    deletes = trace_job_deletes(monkeypatch)
    assert store.prune_jobs(0) == 50
    assert deletes ==["DELETE FROM jobs WHERE id IN (SELECT id FROM temp.doomed)"]


def database_bytes(store):
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return store.path.stat().st_size


def test_fat_history_shrinks_to_near_its_live_size_after_compaction(store):
    add_finished_rows(store, 50_000, body_bytes=1_000)
    kept = add_kept_rows(store)
    before = database_bytes(store)
    assert before > 50_000 * 1_000
    assert store.prune_jobs(0) == 50_000
    assert store.compact() is True
    after = database_bytes(store)
    assert after < 1024 * 1024
    assert snapshot_rows(store, [row[0] for row in kept]) == kept
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_compaction_waits_for_enough_free_space(store):
    add_finished_rows(store, 2_000, body_bytes=1_000)
    assert store.prune_jobs(0) == 500
    assert store.compact() is False


def test_compaction_skips_cleanly_while_another_connection_holds_the_lock(store):
    add_finished_rows(store, 30_000, body_bytes=1_000)
    store.prune_jobs(0)
    size = database_bytes(store)
    with closing(sqlite3.connect(store.path, timeout=0, isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        assert store.compact() is False
        holder.execute("ROLLBACK")
    assert database_bytes(store) == size
    assert store.compact() is True


def test_sweeps_release_freed_pages_once_compaction_switched_on_incremental_mode(store):
    add_finished_rows(store, 30_000, body_bytes=1_000)
    store.prune_jobs(0)
    assert store.compact() is True
    add_finished_rows(store, 3_000, body_bytes=1_000)
    grown = database_bytes(store)
    assert store.prune_jobs(0) == 500
    assert store.release_free_pages() > 0
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert database_bytes(store) < grown


def test_store_connections_bound_the_write_ahead_log(store):
    with store._connection() as connection:
        assert connection.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024


def old_backlog(store):
    """Store rows shaped like the overlapping-cycle backlog; return (kept IDs, stale count, expired count)."""
    kept, stale, expired = set(), 0, 0

    def child(root, kind, state, key):
        return add_job(store, f"{key}:{root}", kind=kind, state=state, payload={"_sync_cycle": root})

    finished = add_job(store, "sync:finished", kind="sync")
    for kind, key in (("pr_sync_page", "pr-page"), ("pr_sync_missing", "pr-missing"), ("pr_sync_check", "pr-check"),
                      ("pr_sync_discover", "pr-discover"), ("event", "sync-item"), ("event", "pr-event")):
        child(finished, kind, "done", key)
    expired += 7
    overlapping = add_job(store, "sync:overlapping", kind="sync")
    kept |= {overlapping, child(overlapping, "pr_sync_page", "done", "pr-page:done"),
             child(overlapping, "event", "pending", "pr-event")}
    for kind, key in (("pr_sync_page", "pr-page"), ("pr_sync_missing", "pr-missing"), ("pr_sync_check", "pr-check"),
                      ("pr_sync_discover", "pr-discover"), ("event", "sync-item")):
        child(overlapping, kind, "pending", key)
    stale += 5
    unstarted = add_job(store, "sync:unstarted", kind="sync", state="pending")
    stale += 1
    interrupted = add_job(store, "sync:interrupted", kind="sync")
    kept |= {interrupted, child(interrupted, "pr_sync_page", "done", "pr-page:done"),
             child(interrupted, "event", "inflight", "sync-item")}
    child(interrupted, "pr_sync_page", "pending", "pr-page")
    stale += 1
    kept |= {add_job(store, "github:pending"), add_job(store, "assess:case:2", kind="assess", state="pending"),
             add_job(store, "failure:recent", state="failed", age_days=1),
             add_job(store, "held", state="recovery", payload={"_sync_cycle": unstarted})}
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = 'pending' WHERE key IN ('github:pending', 'assess:case:2')")
    add_job(store, "github:done")
    add_job(store, "notify:case:3", kind="notify")
    add_job(store, "failure:old", state="failed", age_days=8)
    expired += 3
    return kept, stale, expired


def test_first_start_cleanup_keeps_only_live_held_and_recent_work_and_runs_once(store):
    kept, stale, expired = old_backlog(store)
    assert store.clean_up_old_queue(0) == {"stale": stale, "expired": expired}
    assert job_ids(store) == kept
    before = snapshot_rows(store, sorted(kept))
    assert store.clean_up_old_queue(0) is None
    assert snapshot_rows(store, sorted(kept)) == before
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("PRAGMA user_version = 0")
    assert store.clean_up_old_queue(0) == {"stale": 0, "expired": 0}
    assert snapshot_rows(store, sorted(kept)) == before


def test_first_start_cleanup_of_a_large_backlog_uses_the_bulk_path(store, monkeypatch):
    root = add_job(store, "sync:overlapping", kind="sync")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.executemany(
            "INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
            "VALUES ('pr_sync_page', ?, ?, 'pending', ?, ?, ?)",
            ((f"pr-page:{root}:{index}", json.dumps({"_sync_cycle": root}), NOW, NOW, NOW) for index in range(20_050)))
    kept = add_kept_rows(store)
    before = sequence(store)
    deletes = trace_job_deletes(monkeypatch)
    assert store.clean_up_old_queue(0) == {"stale": 20_050, "expired": 1}
    assert deletes == ["DELETE FROM jobs"]
    assert job_ids(store) == {row[0] for row in kept}
    assert snapshot_rows(store, [row[0] for row in kept]) == kept
    assert sequence(store) == before


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
