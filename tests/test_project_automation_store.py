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
