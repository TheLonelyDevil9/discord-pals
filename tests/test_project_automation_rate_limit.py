"""GitHub rate limits hold reconciliation without using attempts, while webhooks and publications keep a reserve."""

import asyncio
from contextlib import closing
import sqlite3
import time

import pytest

import project_automation_github as github
from project_automation_webhook import webhook_binding
from test_project_automation import FakeGitHub, Harness, runtime as runtime_fixture
from test_project_automation_github import FakeResponse, FakeSession

runtime = runtime_fixture
REPOSITORY = "SillyBunnyTeam/SillyBunny"


def limits(remaining, *, reset_in=900, limit=5000):
    return {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(int(time.time()) + reset_in)}


def real_client(*responses):
    """A GitHubClient with a valid token whose requests get `responses` in order."""
    session = FakeSession(*responses)
    client = github.GitHubClient({"repository": REPOSITORY, "github_app_id": "11", "github_installation_id": "22"}, session)
    client._token, client._token_expiry = "fake-installation-secret", time.time() + 3600
    return client, session


def page_job(harness, number, key):
    return harness.store.enqueue("pr_sync_page", {
        "repository": REPOSITORY, "number": number, "source": "comment", "cursor": None, "page": 1,
        "_binding": webhook_binding(harness.cfg), "_sync_cycle": 1}, key)


def issue_event(harness, number, key):
    return harness.event({"kind": "issue", "repository": REPOSITORY, "number": number, "key": f"issue:{number}",
                          "action": "edited"}, key)


def issue(number):
    return {"number": number, "title": "Settings fail to save", "body": "", "state": "open",
            "html_url": f"https://github.com/{REPOSITORY}/issues/{number}", "updated_at": "2026-11-01T00:00:00Z"}


class Limited(FakeGitHub):
    """A FakeGitHub whose reads and publications GitHub refuses while `refusal` is set."""

    def __init__(self):
        super().__init__()
        self.rate_limit = github.RateLimit()
        self.refusal = None

    async def list_pr_activity(self, number, source, cursor=None):
        if self.refusal:
            raise self.refusal
        return {"items": [], "next_cursor": None}

    async def create_issue(self, title, body, marker):
        if self.refusal:
            raise self.refusal
        return await super().create_issue(title, body, marker)


def row(harness, job):
    return harness.store.get_job(job)


def test_a_rate_limited_reconciliation_job_waits_for_the_reset_without_using_an_attempt(tmp_path, runtime):
    harness = Harness(tmp_path / "limited.sqlite3", github=Limited())
    reset = time.time() + 600
    harness.github.refusal = github.GitHubRateLimited(reset, status=403)
    job = page_job(harness, 7, "pr-page:1:7:comment:1")
    assert asyncio.run(harness.service.run_once()) is True
    waiting = row(harness, job)
    assert (waiting["state"], waiting["attempts"], waiting["available_at"]) == ("pending", 0, reset)
    assert "rate limit" in waiting["last_error"]
    assert harness.service._last_error is None
    # The cycle stays open, so no new cycle starts while its work waits.
    assert harness.service.queue_sync_cycle() is None


def test_reconciliation_waits_near_the_limit_while_webhook_deliveries_use_the_reserve(tmp_path, runtime):
    client, session = real_client(FakeResponse([], headers=limits(900)), FakeResponse(issue(5), headers=limits(899)))
    harness = Harness(tmp_path / "reserve.sqlite3", github=client)
    first, second = page_job(harness, 7, "pr-page:1:7:comment:1"), page_job(harness, 8, "pr-page:1:8:comment:1")
    webhook = issue_event(harness, 5, "github:edited")
    # The first page reports that the reserve is reached, so the second page waits.
    assert asyncio.run(harness.service.run_once()) is True
    assert row(harness, first)["state"] == "done"
    assert asyncio.run(harness.service.run_once()) is True
    assert row(harness, webhook)["state"] == "done"
    assert asyncio.run(harness.service.run_once()) is False
    held = row(harness, second)
    assert (held["state"], held["attempts"]) == ("pending", 0)
    assert len(session.calls) == 2
    assert harness.service.queue_sync_cycle() is None


def test_reconciliation_resumes_once_the_window_resets(tmp_path, runtime):
    client, session = real_client(FakeResponse([], headers=limits(900, reset_in=1)), FakeResponse([], headers=limits(4999)))
    harness = Harness(tmp_path / "resume.sqlite3", github=client)
    first, second = page_job(harness, 7, "pr-page:1:7:comment:1"), page_job(harness, 8, "pr-page:1:8:comment:1")
    assert asyncio.run(harness.service.run_once()) is True
    assert asyncio.run(harness.service.run_once()) is False
    time.sleep(1.1)
    assert asyncio.run(harness.service.run_once()) is True
    assert (row(harness, first)["state"], row(harness, second)["state"]) == ("done", "done")


def test_a_secondary_limit_holds_a_webhook_delivery_without_failing_it(tmp_path, runtime):
    client, session = real_client(FakeResponse({"message": "You have exceeded a secondary rate limit."}, 403,
                                                headers={"Retry-After": "120"}))
    harness = Harness(tmp_path / "secondary.sqlite3", github=client)
    webhook = issue_event(harness, 5, "github:edited")
    before = time.time()
    assert asyncio.run(harness.service.run_once()) is True
    held = row(harness, webhook)
    assert (held["state"], held["attempts"]) == ("pending", 0)
    assert before + 120 <= held["available_at"] <= time.time() + 120
    assert harness.transport.mirrors == []


def test_a_rate_limited_publication_returns_to_the_queue_and_publishes_after_the_reset(tmp_path, runtime):
    harness = Harness(tmp_path / "publication.sqlite3", github=Limited())
    preview = harness.preview()
    harness.github.refusal = github.GitHubRateLimited(time.time() + 600, status=429)
    queued = harness.choose(preview, "submit")
    publish = next(job for job in harness.store.list_jobs() if job["kind"] == "publish")
    assert queued["state"] == "queued"
    assert (publish["state"], publish["attempts"]) == ("pending", 0)
    assert harness.github.posts == []
    harness.github.refusal = None
    with closing(sqlite3.connect(harness.store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET available_at = 0 WHERE id = ?", (publish["id"],))
    asyncio.run(harness.drain())
    assert harness.store.get_case(preview["id"])["state"] == "filed"
    assert len(harness.github.posts) == 1


@pytest.mark.parametrize("failure", [github.GitHubError("GitHub returned HTTP 403.", status=403, retryable=True),
                                     github.GitHubAmbiguousWrite("create_issue", "marker")])
def test_other_github_failures_keep_their_handling(tmp_path, runtime, failure):
    harness = Harness(tmp_path / "failure.sqlite3", github=Limited())
    harness.github.refusal = failure
    job = page_job(harness, 7, "pr-page:1:7:comment:1")
    asyncio.run(harness.service.run_once())
    failed = row(harness, job)
    assert failed["attempts"] == 1
    assert failed["state"] == ("failed" if isinstance(failure, github.GitHubAmbiguousWrite) else "pending")
