from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from project_automation_store import AutomationStore, Conflict


@pytest.fixture
def database(tmp_path):
    return tmp_path / "data" / "automation.sqlite3"


@pytest.fixture
def store(database):
    return AutomationStore(database)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("project_automation_store.time.time", lambda: now[0])
    return now


def report(**patch):
    return {
        "channel_id": "123", "reporter_id": "456", "guild_id": "789",
        "bot_name": "Project helper", "repository": "SillyBunnyTeam/SillyBunny",
        "transcript": [{"author_id": "456", "content": "Saving the settings fails."}],
        **patch,
    }


def gated_case(store):
    return store.create_case(report(
        state="awaiting_submission",
        gate={"kind": "review", "options": [{"key": "submit", "label": "Publish issue"}]},
        draft={"title": "Settings do not save", "body": "Observed result: an error."},
    ), source_key="discord:123:1")


def test_case_and_link_survive_restart_and_source_replay(database):
    first = AutomationStore(database)
    case = first.create_case(report(), "discord:123:1")
    first.put_link("gate", case["id"], {"message_id": "42"})
    second = AutomationStore(database)

    assert second.get_case(case["id"]) == case
    assert second.create_case(report(transcript=[]), "discord:123:1") == case
    assert second.get_link("gate", case["id"]) == {"message_id": "42"}
    assert second.get_case(case["id"])["revision"] == 1


def test_compare_and_swap_rejects_stale_case_and_preserves_identity(store):
    case = gated_case(store)
    updated = store.update_case(case["id"], {"state": "awaiting_details"}, expected_revision=1)
    assert updated["revision"] == 2
    with pytest.raises(Conflict):
        store.update_case(case["id"], {"state": "filed"}, expected_revision=1)
    for field in ("id", "reporter_id", "channel_id", "repository", "revision", "decisions"):
        with pytest.raises(ValueError):
            store.update_case(case["id"], {field: "changed"})
    assert store.get_case(case["id"]) == updated


def test_gate_consumption_and_publication_job_are_atomic(store):
    case = gated_case(store)
    job = {"kind": "publish", "payload": {"case_id": case["id"]}, "key": "publish:1"}
    updated = store.consume_gate(case["id"], 1, "submit", "456", job=job)

    assert updated["revision"] == 2
    assert updated["state"] == "processing"
    assert updated["gate"] is None
    assert updated["decisions"][0]["actor"] == "456"
    assert updated["decisions"][0]["action"] == "submit"
    assert updated["decisions"][0]["revision"] == 1
    assert store.claim_job()["payload"] == {"case_id": case["id"]}
    with pytest.raises(Conflict):
        store.consume_gate(case["id"], 1, "submit", "456", job=job)
    assert len(store.list_jobs()) == 1


def test_failed_enqueue_rolls_back_gate_and_case_patch(store):
    case = gated_case(store)
    store.enqueue("publish", {"case_id": "some other case"}, "occupied")
    conflict_job = {"kind": "publish", "payload": {"case_id": case["id"]}, "key": "occupied"}

    with pytest.raises(Conflict):
        store.consume_gate(case["id"], 1, "submit", "456", job=conflict_job)
    assert store.get_case(case["id"]) == case
    with pytest.raises(Conflict):
        store.update_case(case["id"], {"state": "queued"}, expected_revision=1, job=conflict_job)
    assert store.get_case(case["id"]) == case
    assert len(store.list_jobs()) == 1


def test_only_stored_gate_options_can_be_selected(store):
    case = gated_case(store)
    with pytest.raises(Conflict):
        store.consume_gate(case["id"], 1, "merge", "456")
    with pytest.raises(ValueError):
        store.consume_gate(case["id"], True, "submit", "456")
    assert store.get_case(case["id"]) == case


def test_two_connections_can_consume_a_gate_only_once(store, database):
    case = gated_case(store)
    other = AutomationStore(database)

    def consume(instance):
        try:
            return instance.consume_gate(case["id"], 1, "submit", "456")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, [store, other]))
    assert sum(result is not None for result in results) == 1
    assert len(store.get_case(case["id"])["decisions"]) == 1


def test_replayed_delivery_enqueues_one_job_with_stable_payload(store, database):
    first_id = store.enqueue("webhook", {"event": "opened", "number": 42}, "delivery:abc")
    second_id = AutomationStore(database).enqueue("webhook", {"number": 42, "event": "opened"}, "delivery:abc")
    assert first_id == second_id
    with pytest.raises(Conflict):
        store.enqueue("webhook", {"number": 99}, "delivery:abc")
    assert len(store.list_jobs()) == 1


def test_two_connections_cannot_claim_the_same_job(store, database):
    store.enqueue("assess", {"case_id": "abc"}, "assess:abc")
    other = AutomationStore(database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda instance: instance.claim_job(), [store, other]))
    assert sum(result is not None for result in results) == 1


def test_abandoned_read_lease_is_retried_after_restart(store, database, clock):
    job_id = store.enqueue("assess", {"case_id": "abc"}, "assess:abc")
    first_claim = store.claim_job(lease_seconds=20)
    assert store.claim_job() is None
    clock[0] += 21
    second = AutomationStore(database)
    second_claim = second.claim_job()

    assert second_claim["id"] == job_id
    assert second_claim["attempts"] == 2
    assert second_claim["lease_token"] != first_claim["lease_token"]
    with pytest.raises(Conflict):
        store.complete_job(job_id, lease_token=first_claim["lease_token"])
    second.complete_job(job_id, lease_token=second_claim["lease_token"])
    assert second.get_job(job_id)["state"] == "done"


def test_abandoned_remote_write_needs_recovery_and_never_automatic_retry(store, database, clock):
    job_id = store.enqueue("publish", {"case_id": "abc"}, "publish:abc")
    claimed = store.claim_job()
    store.mark_job_inflight(job_id, lease_token=claimed["lease_token"], lease_seconds=20)
    clock[0] += 21
    second = AutomationStore(database)

    assert second.claim_job() is None
    assert second.get_job(job_id)["state"] == "recovery"
    with pytest.raises(Conflict):
        second.retry_job(job_id)
    with pytest.raises(Conflict):
        second.complete_job(job_id, lease_token=claimed["lease_token"])
    assert second.list_jobs()[0]["state"] == "recovery"


@pytest.mark.parametrize("outcome,state", [("complete", "done"), ("retry", "pending")])
def test_recovery_records_operator_verification(store, outcome, state):
    job_id = store.enqueue("publish", {"case_id": "abc"}, "publish:abc")
    claimed = store.claim_job()
    store.mark_job_inflight(job_id, lease_token=claimed["lease_token"])
    store.fail_job(job_id, "Remote result is unknown.", lease_token=claimed["lease_token"])
    store.resolve_recovery(job_id, outcome, "maintainer:12", "Checked the remote marker and confirmed the result.")

    recovered = store.get_job(job_id)
    assert recovered["state"] == state
    assert recovered["recovery_notes"][0]["actor"] == "maintainer:12"
    assert recovered["recovery_notes"][0]["outcome"] == outcome
    with pytest.raises(Conflict):
        store.resolve_recovery(job_id, outcome, "maintainer:12", "Checked again.")


def test_remote_write_failure_is_recovery_even_when_permanent(store):
    job_id = store.enqueue("publish", {}, "publish:abc")
    store.claim_job()
    store.mark_job_inflight(job_id)
    store.fail_job(job_id, "The connection was interrupted.", permanent=True)
    assert store.get_job(job_id)["state"] == "recovery"


def test_known_read_failure_retries_after_delay_and_failed_job_can_be_retried(store, clock):
    job_id = store.enqueue("assess", {}, "assess:abc")
    store.claim_job()
    store.fail_job(job_id, "Provider unavailable.", delay=15)
    assert store.claim_job() is None
    clock[0] += 15
    assert store.claim_job()["id"] == job_id
    store.fail_job(job_id, "Provider configuration is missing.", permanent=True)
    assert store.claim_job() is None
    store.retry_job(job_id)
    assert store.claim_job()["id"] == job_id


def test_pending_running_inflight_and_done_jobs_are_not_manually_retried(store):
    job_id = store.enqueue("publish", {}, "publish:abc")
    with pytest.raises(Conflict):
        store.retry_job(job_id)
    store.claim_job()
    with pytest.raises(Conflict):
        store.retry_job(job_id)
    store.mark_job_inflight(job_id)
    with pytest.raises(Conflict):
        store.retry_job(job_id)
    store.complete_job(job_id)
    with pytest.raises(Conflict):
        store.retry_job(job_id)


def test_claim_filters_and_delay_leave_other_jobs_untouched(store, clock):
    delayed_id = store.enqueue("publish", {}, "publish:abc", delay=10)
    other_id = store.enqueue("assess", {}, "assess:abc")
    assert store.claim_job(kinds=[]) is None
    assert store.claim_job(kinds=["publish"]) is None
    clock[0] += 10
    assert store.claim_job(kinds=["publish"])["id"] == delayed_id
    assert store.claim_job(kinds=["assess"])["id"] == other_id


def test_pending_case_restore_has_no_dashboard_page_limit(store):
    for index in range(105):
        store.create_case(report(state="awaiting_choice", gate={"options": [{"key": "draft"}]}), f"discord:123:{index}")
    store.create_case(report(state="filed"), "discord:123:filed")
    assert len(store.list_cases()) == 100
    assert len(store.list_pending_cases()) == 105


def test_find_case_is_scoped_to_channel_and_optional_reporter(store, clock):
    first = store.create_case(report(), "first")
    clock[0] += 1
    other_person = store.create_case(report(reporter_id="999"), "second")
    store.create_case(report(channel_id="555"), "third")
    assert store.find_case("123", "456")["id"] == first["id"]
    assert store.find_case("123")["id"] == other_person["id"]
    assert store.find_case("missing") is None


def test_issue_reporters_include_old_filed_cases_and_repository_isolation(store):
    linked = store.create_case(report(state="filed", linked_issue_number=42), "old-filed")
    store.create_case(report(linked_issue_number=42, repository="other/project"), "other-project")
    store.create_case(report(linked_issue_number=43), "other-issue")
    store.create_case(report(), "unlinked")
    assert store.cases_for_issue("sillybunnyteam/sillybunny", 42) == [linked]


def test_active_case_count_is_reporter_and_guild_scoped(store):
    store.create_case(report(), "active")
    store.create_case(report(state="needs_maintainer"), "needs-maintainer")
    store.create_case(report(state="filed"), "filed")
    store.create_case(report(state="closed"), "closed")
    store.create_case(report(reporter_id="999"), "other-reporter")
    store.create_case(report(guild_id="555"), "other-guild")
    assert store.count_active_cases("456", "789") == 2
    assert store.count_active_cases("999", "789") == 1
    assert store.count_active_cases("456", "555") == 1


def test_links_are_namespaced_and_do_not_change_case_revision(store):
    case = gated_case(store)
    store.put_link("gate", case["id"], {"message_id": "1"})
    store.put_link("gate", case["id"], {"message_id": "2"})
    store.put_link("issue", case["id"], {"number": 20})
    assert store.list_links("gate") == [{"key": case["id"], "data": {"message_id": "2"}}]
    assert store.get_link("issue", case["id"]) == {"number": 20}
    assert store.get_case(case["id"]) == case


def test_document_bounds_preserve_original_report_and_recent_context(store):
    transcript = [{"content": f"Message {index}"} for index in range(110)]
    case = store.create_case(report(transcript=transcript), "source")
    assert len(case["transcript"]) == 100
    assert case["transcript"][0] == transcript[0]
    assert case["transcript"][-1] == transcript[-1]
    with pytest.raises(ValueError, match="too large"):
        store.update_case(case["id"], {"draft": {"body": "x" * (256 * 1024)}})
    assert store.get_case(case["id"]) == case
    with pytest.raises(ValueError):
        store.enqueue("event", {"value": float("nan")}, "invalid")


def test_returned_data_cannot_mutate_stored_case(store):
    case = gated_case(store)
    case["draft"]["title"] = "Changed after return"
    assert store.get_case(case["id"])["draft"]["title"] == "Settings do not save"


def test_sql_metacharacters_are_data(store):
    source = "source'); DROP TABLE cases; --"
    case = store.create_case(report(), source)
    assert store.create_case(report(), source)["id"] == case["id"]
    job_id = store.enqueue("event", {}, source)
    assert store.claim_job(kinds=["event' OR 1=1 --"]) is None
    assert store.get_job(job_id)["state"] == "pending"


def test_audit_history_is_bounded(store, database):
    case = gated_case(store)
    with sqlite3.connect(database) as connection:
        document = store.get_case(case["id"])
        document["decisions"] = [{"actor": "456", "action": "investigate", "revision": number, "time": 1} for number in range(100)]
        connection.execute("UPDATE cases SET document = ? WHERE id = ?", (json.dumps(document), case["id"]))
    consumed = store.consume_gate(case["id"], 1, "submit", "456")
    assert len(consumed["decisions"]) == 100
    assert consumed["decisions"][-1]["action"] == "submit"


def failed_snapshot(store, key="failed"):
    job_id = store.enqueue("assess", {"_sync_cycle": 123, "context": "private"}, key)
    assert store.claim_job()["id"] == job_id
    store.fail_job(job_id, "private provider error", permanent=True)
    job = store.get_job(job_id)
    return {"id": job_id, "updated_at": job["updated_at"]}


def test_dismiss_restore_persists_without_changing_work_or_sync_completion(store, database, clock):
    snapshot = failed_snapshot(store)
    before = store.get_job(snapshot["id"])
    clock[0] += 1
    assert store.dismiss_jobs([snapshot]) == 1
    reopened = AutomationStore(database)
    dismissed = reopened.list_job_activity(view="dismissed")["jobs"][0]
    assert dismissed["dismissed_at"] == clock[0]
    assert reopened.list_job_activity()["jobs"] == []
    assert reopened.list_job_activity(view="attention")["jobs"] == []
    assert reopened.job_counts() == {"pending": 0, "failed": 0, "recovery": 0, "dismissed": 1}
    assert reopened.unfinished_sync_jobs(123)
    after = reopened.get_job(snapshot["id"])
    assert {key: value for key, value in after.items() if key != "recovery_notes"} == {
        key: value for key, value in before.items() if key != "recovery_notes"}
    assert reopened.list_jobs() == [after]
    assert after["recovery_notes"][-1]["actor"] == "dashboard"
    assert after["recovery_notes"][-1]["outcome"] == "dismiss"
    assert after["recovery_notes"][-1]["time"] == clock[0]
    assert reopened.dismiss_jobs([snapshot]) == 1
    assert reopened.get_job(snapshot["id"]) == after
    clock[0] += 1
    reopened.restore_job(snapshot["id"], snapshot["updated_at"])
    assert reopened.list_job_activity(view="dismissed")["jobs"] == []
    assert reopened.list_job_activity(view="attention")["jobs"][0]["dismissed_at"] is None
    assert reopened.job_counts()["failed"] == 1
    assert reopened.job_counts()["dismissed"] == 0
    assert reopened.get_job(snapshot["id"])["recovery_notes"][-1]["outcome"] == "restore"
    assert reopened.claim_job() is None


def test_retry_clears_dismissal_and_new_failure_reappears(store, clock):
    old = failed_snapshot(store)
    store.dismiss_jobs([old])
    clock[0] += 1
    store.retry_job(old["id"])
    assert store.get_link("job_dismissal", old["id"]) is None
    assert store.job_counts() == {"pending": 1, "failed": 0, "recovery": 0, "dismissed": 0}
    store.claim_job()
    store.fail_job(old["id"], "new failure", permanent=True)
    assert store.list_job_activity(view="attention")["jobs"][0]["id"] == old["id"]
    with pytest.raises(Conflict):
        store.dismiss_jobs([old])
    with pytest.raises(Conflict):
        store.restore_job(old["id"], old["updated_at"])
    assert store.job_counts()["failed"] == 1


@pytest.mark.parametrize("state", ["pending", "running", "inflight", "recovery", "done", "cancelled"])
def test_only_failed_jobs_can_be_hidden_or_restored(store, database, state):
    snapshot = failed_snapshot(store)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, snapshot["id"]))
    with pytest.raises(Conflict):
        store.dismiss_jobs([snapshot])
    with pytest.raises(Conflict):
        store.restore_job(snapshot["id"], snapshot["updated_at"])
    assert store.get_link("job_dismissal", snapshot["id"]) is None
    assert store.list_job_activity()["jobs"][0]["state"] == state
    if state == "recovery":
        assert store.job_counts()["recovery"] == 1
        assert store.list_job_activity(view="attention")["jobs"][0]["id"] == snapshot["id"]


@pytest.mark.parametrize("changed", ["missing", "updated", "retried"])
def test_bulk_dismissal_rolls_back_all_jobs_on_stale_snapshot(store, database, clock, changed):
    first = failed_snapshot(store, "first")
    second = failed_snapshot(store, "second")
    before = store.get_job(first["id"])
    other = AutomationStore(database)
    clock[0] += 1
    if changed == "retried":
        other.retry_job(second["id"])
    elif changed == "updated":
        second = {**second, "updated_at": second["updated_at"] - 1}
    else:
        second = {**second, "id": 999}
    with pytest.raises(KeyError if changed == "missing" else Conflict):
        store.dismiss_jobs([first, second])
    assert store.get_job(first["id"]) == before
    assert store.list_job_activity(view="dismissed")["jobs"] == []


def test_concurrent_bulk_dismissal_and_retry_never_hide_new_work(store, database, clock):
    first = failed_snapshot(store, "first")
    second = failed_snapshot(store, "second")
    other = AutomationStore(database)
    clock[0] += 1

    def dismiss():
        try:
            store.dismiss_jobs([first, second])
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        dismissal = executor.submit(dismiss)
        retry = executor.submit(other.retry_job, second["id"])
        succeeded = dismissal.result()
        retry.result()
    assert store.get_job(second["id"])["state"] == "pending"
    assert store.get_link("job_dismissal", second["id"]) is None
    assert (store.get_link("job_dismissal", first["id"]) is not None) == succeeded


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "1", None, 2**63])
def test_dismissal_rejects_invalid_ids_without_mutation(store, value):
    snapshot = failed_snapshot(store)
    with pytest.raises(ValueError):
        store.dismiss_jobs([{**snapshot, "id": value}])
    with pytest.raises(ValueError):
        store.restore_job(value, snapshot["updated_at"])
    assert store.job_counts()["failed"] == 1


@pytest.mark.parametrize("value", [True, False, "1000", None, float("nan"), float("inf"), -float("inf"), 10**400])
def test_dismissal_rejects_nonfinite_or_nonnumeric_snapshots(store, value):
    snapshot = failed_snapshot(store)
    with pytest.raises(ValueError):
        store.dismiss_jobs([{**snapshot, "updated_at": value}])
    with pytest.raises(ValueError):
        store.restore_job(snapshot["id"], value)
    assert store.get_link("job_dismissal", snapshot["id"]) is None


@pytest.mark.parametrize("jobs", [None, {}, [], [None], [{"id": 1}], [{"id": 1, "updated_at": 1, "extra": 1}],
                                  [{"id": 1, "updated_at": 1}] * 2,
                                  [{"id": i, "updated_at": 1} for i in range(1, 102)]])
def test_dismissal_batch_shape_is_bounded_and_unique(store, jobs):
    with pytest.raises(ValueError):
        store.dismiss_jobs(jobs)


def test_activity_cursor_filters_before_limiting_and_ignores_updated_order(store, clock):
    snapshots = [failed_snapshot(store, str(index)) for index in range(5)]
    store.dismiss_jobs([snapshots[1], snapshots[3]])
    first = store.list_job_activity(limit=2)
    assert [job["id"] for job in first["jobs"]] == [snapshots[4]["id"], snapshots[2]["id"]]
    assert first["next_before_id"] == snapshots[2]["id"]
    clock[0] += 1
    store.retry_job(snapshots[0]["id"])
    newest = store.enqueue("assess", {}, "arrived-after-page")
    second = store.list_job_activity(limit=2, before_id=first["next_before_id"])
    assert [job["id"] for job in second["jobs"]] == [snapshots[0]["id"]]
    assert second["next_before_id"] is None
    assert store.list_job_activity(before_id=1) == {"jobs": [], "next_before_id": None}
    assert store.list_job_activity()["jobs"][0]["id"] == newest
    assert [job["id"] for job in store.list_job_activity(view="attention")["jobs"]] == [snapshots[4]["id"], snapshots[2]["id"]]
    dismissed = store.list_job_activity(view="dismissed", limit=2)
    assert [job["id"] for job in dismissed["jobs"]] == [snapshots[3]["id"], snapshots[1]["id"]]
    assert dismissed["next_before_id"] is None


@pytest.mark.parametrize("kwargs", [{"view": "all"}, {"view": []}, {"limit": 0}, {"limit": 101},
                                   {"limit": True}, {"before_id": 0}, {"before_id": True}])
def test_activity_rejects_unsupported_pages(store, kwargs):
    with pytest.raises(ValueError):
        store.list_job_activity(**kwargs)


def test_dismiss_restore_audit_retains_bounded_recent_history(store, database):
    snapshot = failed_snapshot(store)
    history = [{"actor": "maintainer", "outcome": "retry", "time": index} for index in range(100)]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET recovery_notes = ? WHERE id = ?", (json.dumps(history), snapshot["id"]))
    store.dismiss_jobs([snapshot])
    store.restore_job(snapshot["id"], snapshot["updated_at"])
    notes = store.get_job(snapshot["id"])["recovery_notes"]
    assert notes[:-2] == history[2:]
    assert [note["outcome"] for note in notes[-2:]] == ["dismiss", "restore"]
    assert all(note["actor"] == "dashboard" for note in notes[-2:])


def test_full_activity_page_bulk_dismissal_leaves_recovery_visible(store):
    snapshots = [failed_snapshot(store, f"failure:{index}") for index in range(100)]
    recovery_id = store.enqueue("publish", {}, "uncertain")
    store.claim_job()
    store.mark_job_inflight(recovery_id)
    store.fail_job(recovery_id, "unknown remote outcome", permanent=True)
    assert store.dismiss_jobs(snapshots) == 100
    assert store.job_counts() == {"pending": 0, "failed": 0, "recovery": 1, "dismissed": 100}
    assert [job["id"] for job in store.list_job_activity(view="attention")["jobs"]] == [recovery_id]
    dismissed = store.list_job_activity(view="dismissed", limit=100)
    assert [job["id"] for job in dismissed["jobs"]] == [snapshot["id"] for snapshot in reversed(snapshots)]
    assert dismissed["next_before_id"] is None
