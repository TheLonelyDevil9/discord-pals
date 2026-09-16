"""Workflow checks use real durable storage and fake external services."""

import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from project_automation import AutomationService, WorkflowError
from project_automation_github import GitHubAmbiguousWrite, GitHubError
from project_automation_store import AutomationStore, Conflict
from project_automation_webhook import webhook_binding


class ProcessStopped(BaseException):
    """Simulate process death after a remote write but before its local receipt."""


class FakeAI:
    def __init__(self):
        self.calls = []
        self.spoken = []
        self.duplicate_number = None
        self.recommendation = "ready"
        self.kind = "bug"

    async def assess(self, case, candidates, bot, settings):
        self.calls.append(deepcopy({"case": case, "candidates": candidates, "bot": bot.name}))
        return {
            "reply": "Which setting were you changing when this happened?" if self.recommendation == "investigate" else "You can write your report in your own words now.",
            "kind": self.kind,
            "recommendation": "link" if self.duplicate_number else self.recommendation,
            "duplicate_number": self.duplicate_number,
        }

    async def speak(self, case, event, bot, settings):
        self.spoken.append(deepcopy(event))
        return "Firefly's contextual reply: " + event["next_step"]


class FakeGitHub:
    def __init__(self):
        self.posts = []
        self.searches = []
        self.candidates = []
        self.items = {}
        self.markers = {}
        self.failure = None
        self.closed = False

    async def search_issues(self, query, limit=5):
        self.searches.append(query)
        return deepcopy(self.candidates[:limit])

    async def get_issue(self, number):
        if isinstance(self.items.get(number), BaseException):
            raise self.items[number]
        return deepcopy(self.items.get(number, {
            "number": number, "title": "Settings fail to save", "body": "Existing report",
            "state": "open", "html_url": f"https://github.com/SillyBunnyTeam/SillyBunny/issues/{number}",
        }))

    async def create_issue(self, title, body, marker):
        self.posts.append({"kind": "issue", "title": title, "body": body, "marker": marker})
        result = {"number": 42, "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42"}
        self.markers[marker] = result
        if self.failure:
            raise self.failure
        return result

    async def add_comment(self, number, body, marker):
        self.posts.append({"kind": "comment", "number": number, "body": body, "marker": marker})
        result = {"number": number, "html_url": f"https://github.com/SillyBunnyTeam/SillyBunny/issues/{number}#issuecomment-1"}
        self.markers[marker] = result
        if self.failure:
            raise self.failure
        return result

    async def find_issue_by_marker(self, marker):
        return deepcopy(self.markers.get(marker))

    async def find_comment_by_marker(self, number, marker):
        return deepcopy(self.markers.get(marker))

    async def list_open_items(self, kind):
        return []

    async def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.notifications = []
        self.mirrors = []
        self.messages = {}
        self.notify_failure = None

    async def notify(self, case, recover_only=False):
        if recover_only:
            return case["id"] in self.messages
        self.notifications.append(deepcopy(case))
        self.messages[case["id"]] = deepcopy(case)
        if self.notify_failure:
            raise self.notify_failure

    async def mirror(self, event):
        self.mirrors.append(deepcopy(event))


class Harness:
    def __init__(self, path, cfg=None, ai=None, github=None):
        self.path = path
        self.cfg = cfg or {
            "enabled": True, "bot_name": "Project helper", "character_name": "Firefly",
            "guild_id": "100", "feedback_channel_id": "101",
            "issues_channel_id": "201", "reviews_channel_id": "202",
            "commits_channel_id": "203", "github_channel_id": "204",
            "repository": "SillyBunnyTeam/SillyBunny", "max_questions": 3,
            "maintainer_user_ids": ["700"], "maintainer_role_ids": ["900"],
            "github_app_id": "11", "github_installation_id": "22",
        }
        self.ai = ai or FakeAI()
        self.github = github or FakeGitHub()
        self.transport = FakeTransport()
        self.store = AutomationStore(path)
        self.service = AutomationService(
            self.store, settings=lambda: self.cfg, ai=self.ai,
            github_factory=lambda settings: self.github, transport=self.transport,
        )
        self.service.bots[self.cfg["bot_name"]] = SimpleNamespace(name=self.cfg["bot_name"], character_name="Firefly")

    def restart(self):
        return Harness(self.path, cfg=self.cfg, ai=self.ai, github=self.github)

    def report(self):
        return self.service.receive_report(
            source_key="discord:100:102:103", channel_id="102", reporter_id="456",
            content="Saving settings shows an error.",
            reporter_name="reporter", title="Settings fail to save",
            source_url="https://discord.com/channels/100/102/103", message_id="103",
        )

    def event(self, event, key):
        return self.store.enqueue("event", {"_binding": webhook_binding(self.cfg), **event}, key)

    async def drain(self):
        for _ in range(30):
            if not await self.service.run_once():
                return
        raise AssertionError("Workflow did not settle within 30 jobs")

    def ready(self):
        case = self.report()
        asyncio.run(self.drain())
        return self.store.get_case(case["id"])

    def choose(self, case, action, **actor):
        self.service.choose(case["id"], case["revision"], action, **({"user_id": "456"} | actor))
        asyncio.run(self.drain())
        return self.store.get_case(case["id"])

    def preview(self):
        return self.submit(self.ready())

    def submit(self, case, *, body=None, title="Settings fail to save"):
        human_entries = [entry for entry in case["transcript"] if entry.get("role") != "assistant"]
        self.service.submit_report(case["id"], case["revision"], user_id="456", username="reporter",
                                   title=title, body=body if body is not None else human_entries[-1]["content"], human_authored=True)
        asyncio.run(self.drain())
        return self.store.get_case(case["id"])


@pytest.fixture
def runtime(monkeypatch):
    state = {"global_paused": False, "operator_user_ids": ["800"]}
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(
        get=lambda key, default=None: state.get(key, default),
        is_server_response_allowed=lambda channel_id: (not state.get("channel_blocked", False), None),
    ))
    return state


@pytest.fixture
def harness(tmp_path, runtime):
    return Harness(tmp_path / "automation.sqlite3")


def test_human_decision_persists_across_restart_without_automatic_progress(harness):
    pending = harness.ready()
    assert pending["state"] == "awaiting_report"
    assert pending["gate"]["kind"] == "conversation"
    assert harness.github.posts == []
    restarted = harness.restart()
    asyncio.run(restarted.drain())
    assert restarted.store.get_case(pending["id"]) == pending
    assert restarted.github.posts == []
    assert len(restarted.ai.calls) == 1


@pytest.mark.parametrize("user_id,role_ids", [("999", []), ("999", ["901"])])
def test_other_contributors_can_discuss_but_cannot_choose_for_reporter(harness, user_id, role_ids):
    pending = harness.ready()
    with pytest.raises(WorkflowError):
        harness.service.choose(pending["id"], pending["revision"], "draft", user_id=user_id, role_ids=role_ids)
    updated = harness.service.add_detail(pending["id"], user_id=user_id, role_ids=role_ids, content="I can reproduce this too.", message_id="104", source_url="source")
    assert updated["transcript"][-1]["author_id"] == user_id
    assert harness.github.posts == []


@pytest.mark.parametrize("user_id,role_ids", [("700", []), ("800", []), ("999", ["900"])])
def test_configured_maintainers_and_operators_can_choose(harness, user_id, role_ids):
    preview = harness.choose(harness.ready(), "human", user_id=user_id, role_ids=role_ids)
    assert preview["state"] == "needs_maintainer"
    assert preview["decisions"][-1]["actor"] == user_id
    assert harness.github.posts == []


def test_only_exact_reviewed_draft_is_published_after_submit(harness):
    pending = harness.ready()
    with pytest.raises(WorkflowError):
        harness.service.choose(pending["id"], pending["revision"], "submit", user_id="456")
    preview = harness.submit(pending)
    approved = deepcopy(preview["draft"])
    assert preview["gate"]["kind"] == "review"
    assert harness.github.posts == []

    filed = harness.choose(preview, "submit")
    assert filed["state"] == "filed"
    assert filed["linked_issue_number"] == 42
    assert len(harness.github.posts) == 1
    assert harness.github.posts[0]["title"] == approved["title"]
    assert harness.github.posts[0]["body"] == approved["body"]
    assert len(harness.ai.calls) == 2


def test_double_click_submission_is_rejected_and_does_not_duplicate(harness):
    preview = harness.preview()
    harness.service.choose(preview["id"], preview["revision"], "submit", user_id="456")
    with pytest.raises((Conflict, WorkflowError)):
        harness.service.choose(preview["id"], preview["revision"], "submit", user_id="456")
    asyncio.run(harness.drain())
    assert len(harness.github.posts) == 1


def test_correction_invalidates_older_preview_and_requires_another_review(harness):
    preview = harness.preview()
    harness.service.add_detail(
        preview["id"], user_id="456", content="Correction: only custom themes fail to save.",
        message_id="104", source_url="https://discord.com/channels/100/102/104",
    )
    with pytest.raises((Conflict, WorkflowError)):
        harness.service.choose(preview["id"], preview["revision"], "submit", user_id="456")
    asyncio.run(harness.drain())
    corrected = harness.store.get_case(preview["id"])
    assert corrected["state"] == "awaiting_submission"
    assert harness.github.posts == []
    corrected_preview = harness.submit(corrected)
    assert corrected_preview["draft"]["body"] != preview["draft"]["body"]
    assert "custom themes" in corrected_preview["draft"]["body"]
    harness.choose(corrected_preview, "submit")
    assert harness.github.posts[0]["body"] == corrected_preview["draft"]["body"]


def test_feedback_source_replay_is_idempotent_after_case_progresses(harness):
    pending = harness.ready()
    replay = harness.report()
    assert replay == pending
    asyncio.run(harness.drain())
    assert len(harness.ai.calls) == 1
    assert len([job for job in harness.store.list_jobs() if job["kind"] == "assess"]) == 1


def test_details_replay_does_not_schedule_another_assessment(harness):
    pending = harness.ready()
    kwargs = {"user_id": "456", "content": "It started after changing themes.", "message_id": "104", "source_url": "source"}
    first = harness.service.add_detail(pending["id"], **kwargs)
    assert harness.service.add_detail(pending["id"], **kwargs) == first
    asyncio.run(harness.drain())
    assert len(harness.ai.calls) == 2


def test_investigation_asks_directly_and_question_limit_never_creates_a_report(harness):
    harness.cfg["max_questions"] = 1
    harness.ai.recommendation = "investigate"
    pending = harness.ready()
    assert pending["state"] == "discussing"
    assert pending["question_count"] == 1
    assert harness.transport.notifications[-1]["reply"] == "Which setting were you changing when this happened?"
    harness.service.add_detail(pending["id"], user_id="456", content="Changing the theme.", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    assessed = harness.store.get_case(pending["id"])
    assert assessed["state"] == "needs_maintainer"
    assert not {"write", "submit"}.intersection(item["key"] for item in assessed["gate"]["options"])
    assert harness.github.posts == []


def test_duplicate_link_is_a_reviewed_comment_not_a_second_issue(harness):
    harness.ai.duplicate_number = 17
    harness.github.candidates = [{"number": 17, "title": "Theme save error", "body": "Existing report", "state": "open", "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/17"}]
    linked_preview = harness.submit(harness.choose(harness.ready(), "link"))
    assert linked_preview["draft"]["kind"] == "comment"
    assert linked_preview["draft"]["issue_number"] == 17
    assert harness.github.posts == []
    filed = harness.choose(linked_preview, "submit")
    assert filed["linked_issue_number"] == 17
    assert harness.github.posts[0]["kind"] == "comment"
    assert harness.github.posts[0]["body"] == linked_preview["draft"]["body"]


def test_followup_for_filed_feedback_requires_approved_comment(harness):
    filed = harness.choose(harness.preview(), "submit")
    harness.service.add_detail(filed["id"], user_id="456", content="It also happens on Linux.", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    pending = harness.store.get_case(filed["id"])
    preview = harness.submit(pending)
    assert len(harness.github.posts) == 1
    assert preview["draft"]["kind"] == "comment"
    harness.choose(preview, "submit")
    assert [post["kind"] for post in harness.github.posts] == ["issue", "comment"]


@pytest.mark.parametrize("error", [GitHubAmbiguousWrite("create_issue", "test-marker"), GitHubError("Request failed")])
def test_public_write_error_parks_case_and_cannot_trigger_second_post(harness, error):
    preview = harness.preview()
    harness.github.failure = error
    harness.choose(preview, "submit")
    recovered_case = harness.store.get_case(preview["id"])
    assert recovered_case["state"] == "recovery"
    recovery_job = next(job for job in harness.store.list_jobs() if job["kind"] == "publish")
    assert recovery_job["state"] == "recovery"
    restarted = harness.restart()
    asyncio.run(restarted.drain())
    assert len(restarted.github.posts) == 1
    with pytest.raises(Conflict):
        restarted.store.retry_job(recovery_job["id"])
    with pytest.raises(WorkflowError):
        asyncio.run(restarted.service.recover(recovery_job["id"], user_id="456"))
    assert asyncio.run(restarted.service.recover(recovery_job["id"], user_id="700")) is True
    asyncio.run(restarted.drain())
    assert restarted.store.get_case(preview["id"])["state"] == "filed"
    assert len(restarted.github.posts) == 1


def test_process_death_after_remote_write_recovers_by_marker_without_second_post(harness, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("project_automation_store.time.time", lambda: clock[0])
    preview = harness.preview()
    harness.github.failure = ProcessStopped()
    harness.service.choose(preview["id"], preview["revision"], "submit", user_id="456")
    with pytest.raises(ProcessStopped):
        asyncio.run(harness.drain())
    assert len(harness.github.posts) == 1
    clock[0] += 1000
    restarted = harness.restart()
    asyncio.run(restarted.drain())
    job = next(job for job in restarted.store.list_jobs() if job["kind"] == "publish")
    assert job["state"] == "recovery"
    assert len(restarted.github.posts) == 1
    assert asyncio.run(restarted.service.recover(job["id"], user_id="700")) is True
    assert restarted.store.get_case(preview["id"])["state"] == "filed"
    assert len(restarted.github.posts) == 1


def test_missing_recovery_marker_does_not_authorize_republication(harness):
    preview = harness.preview()
    harness.github.failure = GitHubAmbiguousWrite("create_issue", "test-marker")
    harness.choose(preview, "submit")
    job = next(job for job in harness.store.list_jobs() if job["kind"] == "publish")
    harness.github.markers.clear()
    assert asyncio.run(harness.service.recover(job["id"], user_id="700")) is False
    assert harness.store.get_job(job["id"])["state"] == "recovery"
    assert len(harness.github.posts) == 1


def test_local_conflict_after_remote_write_is_recovery_not_completed_job(harness, monkeypatch):
    preview = harness.preview()
    original_save = harness.service._save

    def conflict_on_receipt(case, patch, notify=True):
        if patch.get("state") == "filed":
            raise Conflict("Case changed after GitHub accepted the issue")
        return original_save(case, patch, notify=notify)

    monkeypatch.setattr(harness.service, "_save", conflict_on_receipt)
    harness.choose(preview, "submit")
    job = next(job for job in harness.store.list_jobs() if job["kind"] == "publish")
    assert job["state"] == "recovery"
    assert len(harness.github.posts) == 1
    asyncio.run(harness.drain())
    assert len(harness.github.posts) == 1
    monkeypatch.setattr(harness.service, "_save", original_save)
    assert asyncio.run(harness.service.recover(job["id"], user_id="700")) is True
    assert harness.store.get_case(preview["id"])["state"] == "filed"


def test_uncertain_discord_gate_delivery_requires_lookup_before_retry(harness):
    harness.transport.notify_failure = RuntimeError("Discord acknowledgement was lost")
    pending = harness.ready()
    job = next(job for job in harness.store.list_jobs() if job["kind"] == "notify")
    assert job["state"] == "recovery"
    assert len(harness.transport.messages) == 1
    assert len(harness.transport.notifications) == 1
    asyncio.run(harness.drain())
    assert len(harness.transport.notifications) == 1
    harness.transport.notify_failure = None
    assert asyncio.run(harness.service.recover(job["id"], user_id="700")) is True
    asyncio.run(harness.drain())
    assert harness.store.get_job(job["id"])["state"] == "done"
    assert len(harness.transport.messages) == 1
    assert harness.store.get_case(pending["id"]) == pending


@pytest.mark.parametrize("key,new_value", [
    ("repository", "Other/Project"), ("bot_name", "Another helper"),
    ("guild_id", "999"), ("feedback_channel_id", "999"),
])
def test_configuration_switch_holds_existing_cases_in_their_original_scope(harness, key, new_value):
    case = harness.report()
    harness.cfg[key] = new_value
    harness.service.bots[harness.cfg["bot_name"]] = SimpleNamespace(name=harness.cfg["bot_name"])
    asyncio.run(harness.drain())
    assert harness.ai.calls == []
    assert harness.github.posts == []
    assert harness.transport.notifications == []
    assert harness.store.get_case(case["id"])[key] != new_value
    with pytest.raises(WorkflowError):
        harness.service.choose(case["id"], case["revision"], "draft", user_id="456")


def test_global_pause_keeps_jobs_and_human_gates_pending(harness, runtime):
    pending = harness.ready()
    runtime["global_paused"] = True
    with pytest.raises(WorkflowError):
        harness.service.choose(pending["id"], pending["revision"], "draft", user_id="456")
    assert asyncio.run(harness.service.run_once()) is False
    assert harness.store.get_case(pending["id"]) == pending


def test_offline_selected_bot_does_not_claim_feedback(harness):
    harness.report()
    harness.service.bots.clear()
    assert asyncio.run(harness.service.run_once()) is False
    assert harness.store.list_jobs()[0]["state"] == "pending"
    assert harness.ai.calls == []


def test_stale_branch_replay_cannot_replace_a_newer_human_gate(harness):
    pending = harness.ready()
    harness.choose(pending, "human")
    previous_job = next(job for job in harness.store.list_jobs() if job["kind"] == "branch")
    detail = harness.store.get_case(pending["id"])
    harness.service.add_detail(detail["id"], user_id="456", content="Changing themes causes it.", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    current = harness.store.get_case(pending["id"])
    asyncio.run(harness.service.process_job(previous_job))
    assert harness.store.get_case(pending["id"]) == current


def test_latest_issue_status_updates_reporter_without_replacing_pending_gate(harness):
    preview = harness.preview()
    linked = harness.store.update_case(preview["id"], {"linked_issue_number": 42}, expected_revision=preview["revision"])
    harness.github.items[42] = {"number": 42, "title": "Settings fail to save", "body": "Report", "state": "closed", "state_reason": "not_planned", "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42", "updated_at": "2026-09-16T10:00:00Z"}
    harness.event({"repository": harness.cfg["repository"], "kind": "issue", "number": 42, "key": "issue:42", "state": "open"}, "event:42")
    asyncio.run(harness.drain())
    assert harness.store.get_case(linked["id"]) == linked
    assert harness.store.get_link("case_status", linked["id"])["state"] == "closed"
    assert harness.transport.mirrors[-1]["state"] == "closed"
    assert harness.transport.mirrors[-1]["state_reason"] == "not_planned"


def test_maintainer_question_preserves_an_existing_draft_approval(harness):
    preview = harness.preview()
    linked = harness.store.update_case(preview["id"], {"linked_issue_number": 42}, expected_revision=preview["revision"])
    harness.event({
        "repository": harness.cfg["repository"], "kind": "comment", "number": 42,
        "key": "comment:123", "body": "Which version are you running?",
        "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42#issuecomment-123",
    }, "question:123")
    asyncio.run(harness.drain())
    assert harness.store.get_case(linked["id"]) == linked
    assert harness.store.get_link("maintainer_question", linked["id"])["body"] == "Which version are you running?"
    assert harness.github.posts == []


@pytest.mark.parametrize("merged", [False, True])
def test_pull_request_mirror_uses_current_closed_or_merged_state(harness, merged):
    harness.github.items[51] = {"number": 51, "title": "Change settings", "body": "PR", "state": "closed", "merged": merged, "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/pull/51"}
    harness.event({"repository": harness.cfg["repository"], "kind": "pull", "number": 51, "key": "pull:51", "state": "open", "merged": not merged}, "event:51")
    asyncio.run(harness.drain())
    assert harness.transport.mirrors[-1]["state"] == "closed"
    assert harness.transport.mirrors[-1]["merged"] is merged


def test_wrong_repository_event_cannot_reach_discord(harness):
    harness.event({"repository": "Other/Project", "kind": "commit", "key": "push:abc"}, "other-event")
    asyncio.run(harness.drain())
    assert harness.transport.mirrors == []
    assert harness.store.list_jobs()[0]["state"] == "failed"


def test_event_from_old_channel_configuration_is_not_rerouted(harness):
    harness.event({
        "repository": harness.cfg["repository"], "kind": "commit", "key": "push:abc",
    }, "bound-event")
    harness.cfg["commits_channel_id"] = "999"
    asyncio.run(harness.drain())
    assert harness.transport.mirrors == []
    assert harness.store.list_jobs()[0]["state"] == "failed"


@pytest.mark.parametrize("binding", [None, {}, {"repository": "SillyBunnyTeam/SillyBunny"}])
def test_missing_or_partial_event_binding_is_rejected(harness, binding):
    event = {"repository": harness.cfg["repository"], "kind": "commit", "key": "push:abc"}
    if binding is not None:
        event["_binding"] = binding
    harness.store.enqueue("event", event, "invalid-binding")
    asyncio.run(harness.drain())
    assert harness.transport.mirrors == []
    assert harness.store.list_jobs()[0]["state"] == "failed"


def test_deleted_event_can_close_mirror_after_github_confirms_not_found(harness):
    harness.github.items[42] = GitHubError("Issue no longer exists", status=404)
    harness.event({
        "repository": harness.cfg["repository"], "kind": "issue", "number": 42,
        "key": "issue:42", "state": "deleted", "action": "deleted", "title": "Deleted issue",
        "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42",
    }, "delete:42")
    asyncio.run(harness.drain())
    assert harness.transport.mirrors[-1]["state"] == "deleted"
    assert harness.transport.mirrors[-1]["action"] == "deleted"


def test_github_not_found_without_deletion_event_is_not_claimed_as_deleted(harness):
    harness.github.items[42] = GitHubError("Not accessible", status=404)
    harness.event({
        "repository": harness.cfg["repository"], "kind": "issue", "number": 42,
        "key": "issue:42", "state": "closed", "action": "closed",
        "html_url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42",
    }, "close:42")
    asyncio.run(harness.drain())
    assert harness.transport.mirrors == []


def test_maintainer_question_enters_assessment_and_clears_after_approved_answer(harness):
    filed = harness.choose(harness.preview(), "submit")
    question = {"key": "comment:123", "body": "Which version are you running?", "url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42#issuecomment-123"}
    harness.store.put_link("maintainer_question", filed["id"], question)
    harness.service.add_detail(filed["id"], user_id="456", content="Version 1.2.3.", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    assert harness.ai.calls[-1]["case"]["maintainer_question"] == question
    pending = harness.store.get_case(filed["id"])
    preview = harness.submit(pending)
    assert harness.store.get_link("maintainer_question", filed["id"]) == question
    harness.choose(preview, "submit")
    assert not harness.store.get_link("maintainer_question", filed["id"])
    assert harness.github.posts[-1]["kind"] == "comment"
    assert "Version 1.2.3." in harness.github.posts[-1]["body"]


def test_question_arriving_after_preview_is_preserved_when_older_draft_is_published(harness):
    filed = harness.choose(harness.preview(), "submit")
    harness.service.add_detail(filed["id"], user_id="456", content="It also happens on Linux.", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    preview = harness.submit(harness.store.get_case(filed["id"]))
    question = {"key": "comment:124", "body": "Which version are you running?", "url": "https://github.com/SillyBunnyTeam/SillyBunny/issues/42#issuecomment-124"}
    harness.store.put_link("maintainer_question", filed["id"], question)
    harness.choose(preview, "submit")
    assert harness.store.get_link("maintainer_question", filed["id"]) == question


@pytest.mark.parametrize("kind,recommendation", [("other", "no_issue"), ("support", "investigate"), ("bug", "investigate")])
def test_test_support_and_incomplete_reports_have_no_publication_shortcut(harness, kind, recommendation):
    harness.ai.kind, harness.ai.recommendation = kind, recommendation
    case = harness.service.receive_report(source_key="test:852", channel_id="102", reporter_id="456",
        reporter_name="reporter", content="This is just a test to check how an issue is created.",
        message_id="852", source_url="https://discord.com/channels/100/102/852")
    asyncio.run(harness.drain())
    case = harness.store.get_case(case["id"])
    assert not {"write", "submit", "draft"}.intersection(item["key"] for item in case["gate"]["options"])
    assert case.get("draft") is None
    assert harness.github.posts == []


def test_github_body_contains_only_attribution_and_human_contents(harness):
    case = harness.ready()
    # Extra model fields can never become publication content, even with a buggy adapter.
    case = harness.store.update_case(case["id"], {"assessment": {**case["assessment"],
        "title": "AI headline", "body": "AI transcript, assessment and unknowns"}}, expected_revision=case["revision"])
    contents = "On Windows, changing my theme and clicking Save gives an error.\nI expected the new theme to persist."
    preview = harness.submit(case, title="Theme save error on Windows", body=contents)
    assert preview["draft"]["body"] == "Forwarded from Discord\n@\u200breporter\n\n" + contents
    assert preview["submitted_report"]["body"] == contents
    assert preview["draft"]["title"] == "Theme save error on Windows"
    assert "Source:" not in preview["draft"]["body"]
    harness.choose(preview, "submit")
    assert harness.github.posts[0]["body"] == preview["draft"]["body"]


@pytest.mark.parametrize("actor,roles", [("700", []), ("800", []), ("999", ["900"]), ("999", [])])
def test_only_reporter_can_write_and_approve_their_own_contents(harness, actor, roles):
    case = harness.ready()
    with pytest.raises(WorkflowError):
        harness.service.submit_report(case["id"], case["revision"], user_id=actor, role_ids=roles,
            username="another_user", title="A report", body="My words", human_authored=True)
    preview = harness.submit(case)
    with pytest.raises(WorkflowError):
        harness.service.choose(preview["id"], preview["revision"], "submit", user_id=actor, role_ids=roles)
    assert harness.github.posts == []


@pytest.mark.parametrize("confirmation", [False, None, "yes", 1])
def test_authorship_confirmation_must_be_explicit(harness, confirmation):
    case = harness.ready()
    with pytest.raises(WorkflowError, match="Confirm"):
        harness.service.submit_report(case["id"], case["revision"], user_id="456", username="reporter",
            title="Title", body="My words", human_authored=confirmation)
    assert harness.store.get_case(case["id"]) == case


def test_reporter_gets_nudges_without_the_model_rewriting_their_report(harness):
    case = harness.ready()
    harness.ai.recommendation = "investigate"
    case = harness.submit(case, body="It broke.")
    assert case["submitted_report"]["body"] == "It broke."
    assert case["draft"] is None
    assert "edit" in {option["key"] for option in case["gate"]["options"]}
    assert "submit" not in {option["key"] for option in case["gate"]["options"]}
    with pytest.raises(WorkflowError):
        harness.service.choose(case["id"], case["revision"], "submit", user_id="456")


def test_failed_duplicate_check_holds_approval_even_if_model_says_ready(harness):
    preview = harness.preview()
    async def unavailable(*args, **kwargs):
        raise GitHubError("Search unavailable", status=503)
    harness.github.search_issues = unavailable
    held = harness.submit(preview)
    assert held["state"] == "needs_maintainer"
    assert held["search_unavailable"] is True
    assert held["draft"] is None
    assert "submit" not in {item["key"] for item in held["gate"]["options"]}


def test_new_report_form_invalidates_older_approval(harness):
    old = harness.preview()
    new = harness.submit(old, body="I wrote a corrected report.")
    with pytest.raises((Conflict, WorkflowError)):
        harness.service.choose(old["id"], old["revision"], "submit", user_id="456")
    harness.choose(new, "submit")
    assert harness.github.posts[0]["body"].endswith("I wrote a corrected report.")


def test_assessment_receives_delivered_character_turns_and_contributors(harness):
    case = harness.ready()
    reply = harness.transport.notifications[-1]["reply"]
    harness.service.add_detail(case["id"], user_id="888", username="contributor", content="I can reproduce it on Linux.",
        message_id="104", source_url="source")
    asyncio.run(harness.drain())
    transcript = harness.ai.calls[-1]["case"]["transcript"]
    assert any(entry.get("role") == "assistant" and entry["content"] == reply for entry in transcript)
    assert transcript[-1]["author_name"] == "contributor"
    assert transcript[-1]["content"] == "I can reproduce it on Linux."
    assert len({item["delivery_key"] for item in harness.transport.notifications}) == 2


def test_unconfirmed_discord_delivery_does_not_create_assistant_history(harness):
    harness.transport.notify_failure = RuntimeError("Lost acknowledgement")
    case = harness.ready()
    assert harness.store.get_link("conversation", case["id"]) is None
    job = next(item for item in harness.store.list_jobs() if item["kind"] == "notify")
    harness.transport.notify_failure = None
    assert asyncio.run(harness.service.recover(job["id"], user_id="700"))
    turns = harness.store.get_link("conversation", case["id"])["turns"]
    assert len(turns) == 1
    assert turns[0]["content"] == harness.transport.notifications[0]["reply"]


def test_legacy_ai_draft_requires_a_new_human_report(harness):
    case = harness.preview()
    legacy = harness.store.update_case(case["id"], {"workflow_version": 1, "submitted_report": None},
                                      expected_revision=case["revision"])
    with pytest.raises(WorkflowError):
        harness.service.choose(legacy["id"], legacy["revision"], "submit", user_id="456")
    harness.service.add_detail(legacy["id"], user_id="456", content="I want to prepare this myself.",
        message_id="105", source_url="source")
    asyncio.run(harness.drain())
    resumed = harness.store.get_case(case["id"])
    assert resumed["state"] == "awaiting_report"
    assert resumed["workflow_version"] == 2
    assert resumed["draft"] is None


def test_legacy_queued_publication_cannot_post_an_ai_draft(harness):
    case = harness.preview()
    harness.service.choose(case["id"], case["revision"], "submit", user_id="456")
    assert asyncio.run(harness.service.run_once())
    queued = harness.store.get_case(case["id"])
    assert queued["state"] == "queued"
    harness.store.update_case(queued["id"], {"workflow_version": 1, "submitted_report": None},
                              expected_revision=queued["revision"])
    asyncio.run(harness.drain())
    assert harness.github.posts == []
    assert harness.store.get_case(case["id"])["state"] == "awaiting_report"


@pytest.mark.parametrize("action", ["draft", "investigate", "submit", "edit"])
def test_legacy_queued_decision_resumes_conversation_instead_of_getting_stuck(harness, action):
    case = harness.ready()
    legacy = harness.store.update_case(case["id"], {"workflow_version": 1,
        "gate": {"kind": "direction", "options": [{"key": action, "label": action}]}}, expected_revision=case["revision"])
    # Simulate a decision consumed by the old deployed code before restart.
    harness.store.consume_gate(legacy["id"], legacy["revision"], action, "456",
        job=harness.service._job("branch", legacy, action=action, revision=legacy["revision"] + 1))
    asyncio.run(harness.drain())
    resumed = harness.store.get_case(case["id"])
    assert resumed["workflow_version"] == 2
    assert resumed["state"] == "awaiting_report"
    assert resumed["submitted_report"] is None
    assert harness.github.posts == []


def test_open_report_form_survives_new_conversation_but_not_a_newer_human_report(harness):
    opened = harness.ready()
    harness.service.add_detail(opened["id"], user_id="888", username="contributor", content="I see this too.",
        message_id="104", source_url="source")
    asyncio.run(harness.drain())
    submitted = harness.service.submit_report(opened["id"], opened["revision"], report_revision=opened["report_revision"],
        issue_number=None, user_id="456", username="reporter", title="My report", body="My carefully written report.", human_authored=True)
    assert submitted["submitted_report"]["body"] == "My carefully written report."
    with pytest.raises(Conflict, match="newer report"):
        harness.service.submit_report(opened["id"], opened["revision"], report_revision=opened["report_revision"],
            user_id="456", username="reporter", title="Stale copy", body="Overwrite newer work", human_authored=True)


def test_report_form_cannot_silently_change_github_destination(harness):
    opened = harness.ready()
    harness.store.update_case(opened["id"], {"target_issue_number": 42}, expected_revision=opened["revision"])
    with pytest.raises(Conflict, match="destination"):
        harness.service.submit_report(opened["id"], opened["revision"], report_revision=opened["report_revision"],
            user_id="456", username="reporter", title="My report", body="My text", human_authored=True)


def test_recovered_reply_retains_original_delivery_order(harness, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("project_automation.time.time", lambda: clock[0])
    harness.transport.notify_failure = RuntimeError("Lost acknowledgement")
    case = harness.ready()
    job = next(item for item in harness.store.list_jobs() if item["kind"] == "notify")
    clock[0] = 200.0
    harness.service.add_detail(case["id"], user_id="456", content="My answer came after the question.", message_id="104", source_url="source")
    clock[0] = 300.0
    harness.transport.notify_failure = None
    assert asyncio.run(harness.service.recover(job["id"], user_id="700"))
    transcript = harness.service._context(harness.store.get_case(case["id"]))["transcript"]
    assert [entry["role"] for entry in transcript] == ["user", "assistant", "user"]
    assert transcript[-1]["content"] == "My answer came after the question."


def test_missing_search_terms_cannot_be_reported_as_a_successful_duplicate_check(harness):
    case = harness.service.receive_report(source_key="nonlatin", channel_id="102", reporter_id="456",
        reporter_name="reporter", content="設定が保存されません", title="保存できません", message_id="852", source_url="source")
    asyncio.run(harness.drain())
    case = harness.store.get_case(case["id"])
    assert case["search_unavailable"] is True
    assert case["state"] == "needs_maintainer"
    assert harness.github.searches == []


def test_forum_title_is_part_of_initial_duplicate_search(harness):
    harness.ready()
    assert "Settings" in harness.github.searches[0]
    assert "save" in harness.github.searches[0]


def test_legacy_uncertain_status_card_can_be_recovered_without_new_publication(harness):
    case = harness.ready()
    job_id = harness.store.enqueue("notify", {"case_id": case["id"]}, key="legacy-notify")
    job = harness.store.claim_job(kinds=["notify"])
    assert job["id"] == job_id
    harness.store.mark_job_inflight(job_id, lease_token=job["lease_token"])
    harness.store.fail_job(job_id, "Lost acknowledgement", lease_token=job["lease_token"])
    count = len(harness.transport.notifications)
    assert asyncio.run(harness.service.recover(job_id, user_id="700"))
    assert len(harness.transport.notifications) == count
    assert harness.store.get_job(job_id)["state"] == "done"
