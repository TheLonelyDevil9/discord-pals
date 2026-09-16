"""Cross-cutting regressions found during independent integration review."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from project_automation import WorkflowError
from project_automation_github import GitHubError
from test_project_automation import Harness, FakeGitHub, runtime as runtime_fixture

runtime = runtime_fixture


@pytest.fixture
def harness(tmp_path, runtime):
    return Harness(tmp_path / "automation.sqlite3")


def reject_publish(harness):
    preview = harness.preview()
    async def reject(*args):
        raise GitHubError("GitHub returned HTTP 403.", status=403)
    harness.github.create_issue = reject
    held = harness.choose(preview, "submit")
    job = next(job for job in harness.store.list_jobs() if job["kind"] == "publish")
    assert job["state"] == "recovery"
    return held, job


def test_retry_held_publication_requires_confirmation_and_new_draft_approval(harness):
    held, job = reject_publish(harness)
    with pytest.raises(WorkflowError, match="confirmed_not_delivered"):
        asyncio.run(harness.service.retry(job["id"], user_id="700"))
    assert harness.store.get_case(held["id"])["state"] == "recovery"
    asyncio.run(harness.service.retry(job["id"], user_id="700", confirmed_not_delivered=True))
    case = harness.store.get_case(held["id"])
    assert case["state"] == "awaiting_submission"
    assert case["gate"]["kind"] == "review"
    assert case["draft"] == job["payload"]["draft"]
    assert harness.store.get_job(job["id"])["state"] == "cancelled"
    asyncio.run(harness.drain())
    assert harness.github.posts == []


def test_reporter_cannot_authorize_retry_of_an_uncertain_write(harness):
    _, job = reject_publish(harness)
    with pytest.raises(WorkflowError, match="maintainer"):
        asyncio.run(harness.service.retry(job["id"], user_id="456", confirmed_not_delivered=True))


def test_failed_branch_job_can_resume_the_original_human_choice(harness):
    case = harness.ready()
    harness.service.choose(case["id"], case["revision"], "human", user_id="456")
    job = harness.store.claim_job(kinds=["branch"])
    harness.store.fail_job(job["id"], "Local interruption", permanent=True)
    asyncio.run(harness.service.retry(job["id"], user_id="700"))
    asyncio.run(harness.drain())
    assert harness.store.get_case(case["id"])["state"] == "needs_maintainer"
    assert harness.github.posts == []


def test_old_failed_assessment_cannot_cancel_new_reporter_details(harness):
    case = harness.report()
    for _ in range(2):
        job = harness.store.claim_job(kinds=["assess"])
        harness.store.fail_job(job["id"], "Temporary model error", delay=0)
    original_assess = harness.ai.assess
    async def receive_then_fail(*args):
        harness.service.add_detail(case["id"], user_id="456", content="The new error says permission denied.", message_id="104", source_url="source")
        harness.ai.assess = original_assess
        raise RuntimeError("Temporary provider failure")
    harness.ai.assess = receive_then_fail
    asyncio.run(harness.drain())
    updated = harness.store.get_case(case["id"])
    assert updated["state"] == "awaiting_report"
    assert "permission denied" in updated["transcript"][-1]["content"]


def test_startup_recovers_initial_intake_interrupted_before_queue_insert(harness):
    original_enqueue = harness.store.enqueue
    harness.store.enqueue = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Interrupted"))
    with pytest.raises(RuntimeError):
        harness.report()
    harness.store.enqueue = original_enqueue
    harness.transport.attach = AsyncMock()
    async def start():
        bot = harness.service.bots[harness.cfg["bot_name"]]
        await harness.service.attach(bot)
        try:
            assert any(job["kind"] == "assess" for job in harness.store.list_jobs())
        finally:
            await harness.service.detach(bot)
    asyncio.run(start())


def test_exact_preview_is_normalized_before_publication(harness):
    case = harness.ready()
    preview = harness.submit(case, title="Settings @team\r\n", body="Details\r\n<#123>\x00 @team")
    assert "\r" not in preview["draft"]["body"]
    assert "\x00" not in preview["draft"]["body"]
    assert "@\u200bteam" in preview["draft"]["body"]
    harness.choose(preview, "submit")
    assert harness.github.posts[0]["body"] == preview["draft"]["body"]


def test_recovery_and_restored_gate_roll_back_together(harness, monkeypatch):
    held, job = reject_publish(harness)
    def interrupted(*args):
        raise RuntimeError("Interrupted during outbox insertion")
    monkeypatch.setattr(harness.store, "_enqueue_spec", interrupted)
    with pytest.raises(RuntimeError):
        asyncio.run(harness.service.retry(job["id"], user_id="700", confirmed_not_delivered=True))
    assert harness.store.get_case(held["id"])["state"] == "recovery"
    assert harness.store.get_job(job["id"])["state"] == "recovery"


def test_old_publication_cannot_replace_a_newly_filed_case(harness):
    held, job = reject_publish(harness)
    asyncio.run(harness.service.retry(job["id"], user_id="700", confirmed_not_delivered=True))
    preview = harness.store.get_case(held["id"])
    harness.github.create_issue = FakeGitHub.create_issue.__get__(harness.github, FakeGitHub)
    filed = harness.choose(preview, "submit")
    assert filed["state"] == "filed"
    with pytest.raises(WorkflowError):
        asyncio.run(harness.service.retry(job["id"], user_id="700", confirmed_not_delivered=True))
    assert harness.store.get_case(filed["id"]) == filed
    assert len(harness.github.posts) == 1


def test_stale_failed_assessment_cannot_erase_a_later_approval(harness):
    case = harness.report()
    failed = harness.store.claim_job(kinds=["assess"])
    harness.store.fail_job(failed["id"], "Model error", permanent=True)
    harness.service.add_detail(case["id"], user_id="456", content="More detail", message_id="104", source_url="source")
    asyncio.run(harness.drain())
    preview = harness.submit(harness.store.get_case(case["id"]))
    with pytest.raises(WorkflowError, match="progressed"):
        asyncio.run(harness.service.retry(failed["id"], user_id="700"))
    assert harness.store.get_case(case["id"]) == preview


def test_channel_access_change_blocks_an_already_approved_publication(harness, runtime):
    preview = harness.preview()
    harness.service.choose(preview["id"], preview["revision"], "submit", user_id="456")
    runtime["channel_blocked"] = True
    asyncio.run(harness.drain())
    assert harness.github.posts == []


def test_large_detail_history_stays_bounded_and_keeps_original_report(harness):
    case = harness.ready()
    original = case["transcript"][0]
    for number in range(30):
        harness.service.add_detail(case["id"], user_id="456", content="🙂" * 8000,
                                    message_id=str(1000 + number), source_url="source")
    updated = harness.store.get_case(case["id"])
    assert updated["transcript"][0] == original
    assert sum(len(entry["content"]) for entry in updated["transcript"]) <= 24000
