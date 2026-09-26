"""PR activity uses current source state and fixed API hosts; no public writes."""

import asyncio
import base64
import copy
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import project_automation_github as github
import project_automation_github_activity as activity


REPO = "SillyBunnyTeam/SillyBunny"
CONFIG = {"repository": REPO, "github_app_id": "123", "github_installation_id": "456"}
BEFORE, AFTER = "a" * 40, "b" * 40
CREATED, UPDATED = "2026-09-25T01:00:00Z", "2026-09-26T02:00:00Z"


def pull(**updates):
    return {"number": 18, "title": "Review changes", "state": "open", "draft": False,
            "html_url": f"https://github.com/{REPO}/pull/18", "updated_at": UPDATED,
            "head": {"sha": AFTER, "ref": "work", "repo": {"full_name": "Contributor/Fork"}},
            **updates}


def comment(**updates):
    return {"id": 99, "body": "Original text", "created_at": CREATED, "updated_at": CREATED,
            "user": {"id": 7, "login": "author", "type": "User"},
            "html_url": f"https://github.com/{REPO}/pull/18#issuecomment-99",
            "issue_url": f"https://api.github.com/repos/{REPO}/issues/18", **updates}


def review_comment(**updates):
    raw = comment(html_url=f"https://github.com/{REPO}/pull/18#discussion_r99")
    raw.pop("issue_url")
    raw.update(pull_request_url=f"https://api.github.com/repos/{REPO}/pulls/18",
               pull_request_review_id=101, path="source/main.py", line=12,
               start_line=10, in_reply_to_id=77, **updates)
    return raw


def review(**updates):
    return {"id": 101, "body": "Please address this", "submitted_at": CREATED,
            "state": "CHANGES_REQUESTED", "user": {"id": 7, "login": "reviewer", "type": "User"},
            "html_url": f"https://github.com/{REPO}/pull/18#pullrequestreview-101",
            "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/18", **updates}


def thread(**updates):
    return {"id": "PRRT_kwExample", "isResolved": True, "isOutdated": False,
            "path": "source/main.py", "line": 12, "startLine": 10,
            "repository": {"nameWithOwner": REPO}, "pullRequest": {"number": 18},
            "resolvedBy": {"login": "maintainer"},
            "comments": {"nodes": [{"databaseId": 99, "url": review_comment()["html_url"],
                "body": "Original text", "createdAt": CREATED, "updatedAt": CREATED,
                "author": {"login": "author", "__typename": "User"},
                "pullRequestReview": {"databaseId": 101, "state": "COMMENTED"}}]}, **updates}


def webhook(event="issue_comment", action="created", **updates):
    payload = {"repository": {"full_name": REPO}, "sender": {"login": "actor", "id": 2, "type": "User"},
               "action": action, "pull_request": pull(), "issue": pull(pull_request={}),
               "comment": review_comment() if event == "pull_request_review_comment" else comment(),
               "review": review(), "thread": {"node_id": "PRRT_kwExample", "comments": [review_comment()]},
               "before": BEFORE, "after": AFTER, **updates}
    return github.normalize_event(event, payload, REPO)


class FakeContent:
    def __init__(self, raw):
        self.raw = raw

    async def iter_chunked(self, size):
        yield self.raw


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.status, self.headers = status, headers or {}
        raw = json.dumps(data).encode()
        self.content, self.content_length = FakeContent(raw), len(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def client(*responses, config=None):
    session = Session(responses)
    result = github.GitHubClient(config or CONFIG, session)
    result._token, result._token_expiry = "private-installation-token", time.time() + 3600
    return result, session


def thread_page(rows, has_next=False, cursor=None):
    return Response({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": rows, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}}})


class NormalizeActivityTests(unittest.TestCase):
    def test_ordinary_pr_comments_include_bot_authors_and_keep_object_identity(self):
        items = [webhook(action=action, comment=comment(user={"id": 42, "login": "helper[bot]", "type": "Bot"}))
                 for action in ("created", "edited", "deleted")]
        self.assertTrue(all(item["kind"] == "pr_activity" for item in items))
        self.assertEqual({item["key"] for item in items}, {"pr:18:comment:99"})
        self.assertEqual({item["source_version"] for item in items}, {items[0]["source_version"]})
        self.assertEqual(items[0]["author"], "helper[bot]")
        self.assertEqual(items[0]["author_type"], "Bot")

    def test_inline_reply_preserves_source_location_and_separate_identity(self):
        inline = webhook("pull_request_review_comment")
        self.assertNotEqual(inline["key"], webhook()["key"])
        self.assertEqual((inline["path"], inline["line"], inline["start_line"]), ("source/main.py", 12, 10))
        self.assertEqual(inline["review_id"], 101)
        self.assertEqual(inline["reply_to_id"], 77)
        self.assertTrue(inline["reply_to_url"].endswith("/pull/18#discussion_r77"))

    def test_reviews_cover_submission_edit_and_dismissal_and_hide_pending(self):
        for action, state in (("submitted", "APPROVED"), ("edited", "COMMENTED"), ("dismissed", "DISMISSED")):
            item = webhook("pull_request_review", action, review=review(state=state))
            self.assertEqual(item["state"], state.lower())
            self.assertEqual(item["source_id"], "101")
        for pending in (review(state="PENDING"), review(submitted_at=None)):
            self.assertIsNone(webhook("pull_request_review", "submitted", review=pending))

    def test_thread_normalizes_node_identity_and_resolve_unresolve(self):
        resolved = webhook("pull_request_review_thread", "resolved")
        unresolved = webhook("pull_request_review_thread", "unresolved")
        self.assertEqual(resolved["key"], unresolved["key"])
        self.assertNotEqual(resolved["source_version"], unresolved["source_version"])
        self.assertEqual(resolved["thread_id"], "PRRT_kwExample")
        self.assertEqual(resolved["state"], "resolved")

    def test_synchronize_uses_base_repository_and_fork_head(self):
        item = webhook("pull_request", "synchronize")
        self.assertEqual(item["activity_type"], "commits")
        self.assertEqual(item["source_id"], AFTER)
        self.assertEqual(item["head_repository"], "Contributor/Fork")
        self.assertEqual(item["before"], BEFORE)
        self.assertNotIn("commit_count", item)
        self.assertIsNone(webhook("pull_request", "synchronize", after="c" * 40))

    def test_source_body_is_preserved_beyond_old_display_limit_and_mentions_are_inert(self):
        body = "@everyone <@123> <#456>\n" + "x" * 20000
        item = webhook(comment=comment(body=body))
        self.assertGreater(len(item["body"]), github.MAX_BODY)
        self.assertFalse(item["body_truncated"])
        self.assertNotIn("@everyone", item["body"])
        self.assertNotIn("<@123>", item["body"])
        self.assertNotIn("<#456>", item["body"])
        long = webhook(comment=comment(body="x" * 70000))
        self.assertEqual(len(long["body"]), activity.MAX_ACTIVITY_BODY)
        self.assertTrue(long["body_truncated"])

    def test_source_version_catches_equal_time_edits_and_changes_after_excerpt(self):
        old = webhook(comment=comment(body="x" * 70000))
        edited = webhook(comment=comment(body="x" * 69999 + "y"))
        self.assertEqual(old["body"], edited["body"])
        self.assertNotEqual(old["source_version"], edited["source_version"])

    def test_rejects_forged_hosts_wrong_pr_and_mismatched_api_identity(self):
        for raw in (comment(html_url="https://evil.example/pull/18#issuecomment-99"),
                    comment(html_url=f"https://github.com/{REPO}/pull/19#issuecomment-99"),
                    comment(html_url=f"https://github.com/{REPO}/pull/18#issuecomment-100"),
                    comment(issue_url="https://api.github.com/repos/Other/Repo/issues/18"),
                    comment(id=True)):
            self.assertIsNone(webhook(comment=raw))
        self.assertIsNone(webhook(repository={"full_name": "Other/Repo"}))

    def test_issue_comments_keep_existing_ask_reporter_contract(self):
        issue = {**pull(), "html_url": f"https://github.com/{REPO}/issues/18"}
        raw = comment(body="/ask-reporter Which version?", author_association="MEMBER",
                      html_url=f"https://github.com/{REPO}/issues/18#issuecomment-99")
        self.assertIsNone(webhook(issue=issue))
        result = webhook(issue=issue, comment=raw)
        self.assertEqual(result["kind"], "comment")
        self.assertEqual(result["body"], "Which version?")


class CurrentActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_event_fetches_current_edited_text(self):
        api, session = client(Response(comment(body="Current text", updated_at=UPDATED)))
        current = await api.get_pr_activity(webhook())
        self.assertEqual(current["body"], "Current text")
        self.assertNotEqual(current["source_version"], webhook()["source_version"])
        self.assertTrue(session.calls[0][1].endswith("/issues/comments/99"))

    async def test_stale_delete_cannot_hide_existing_source(self):
        api, _ = client(Response(comment(body="Still here")))
        current = await api.get_pr_activity(webhook(action="deleted"))
        self.assertFalse(current["deleted"])
        self.assertNotEqual(current["action"], "deleted")
        self.assertEqual(current["body"], "Still here")

    async def test_confirmed_comment_404_plus_accessible_pr_returns_text_free_tombstone(self):
        for event in (webhook(), webhook("pull_request_review_comment")):
            api, session = client(Response({}, 404), Response(pull()))
            result = await api.get_pr_activity(event)
            self.assertEqual(result["state"], "deleted")
            self.assertEqual(result["action"], "deleted")
            self.assertEqual(result["body"], "")
            self.assertNotEqual(result["source_version"], event["source_version"])
            self.assertEqual(len(session.calls), 2)

    async def test_404_access_loss_or_forbidden_is_never_deletion(self):
        for responses in ((Response({}, 403),), (Response({}, 404), Response({}, 404))):
            api, _ = client(*responses)
            with self.assertRaises(github.GitHubError):
                await api.get_pr_activity(webhook(action="deleted"))

    async def test_source_scope_and_identifier_validated_before_network(self):
        for changes in ({"repository": "Other/Repo"}, {"source_id": "99/../../issues"},
                        {"key": "pr:19:comment:99"}, {"number": True}, {"activity_type": []}):
            api, session = client()
            with self.assertRaises(github.GitHubError):
                await api.get_pr_activity({**webhook(), **changes})
            self.assertFalse(session.calls)

    async def test_wrong_current_object_is_rejected_even_if_webhook_was_valid(self):
        api, _ = client(Response(comment(id=100, html_url=f"https://github.com/{REPO}/pull/18#issuecomment-100")))
        with self.assertRaises(github.GitHubError):
            await api.get_pr_activity(webhook())

    async def test_pending_review_and_its_inline_comment_are_private(self):
        api, _ = client(Response(review(state="PENDING", submitted_at=None)))
        self.assertIsNone(await api.get_pr_activity(webhook("pull_request_review", "submitted")))
        api, session = client(Response(review_comment()), Response(review(state="PENDING", submitted_at=None)))
        self.assertIsNone(await api.get_pr_activity(webhook("pull_request_review_comment")))
        self.assertTrue(session.calls[-1][1].endswith("/pulls/18/reviews/101"))

    async def test_published_inline_comment_and_dismissed_review_are_current(self):
        api, _ = client(Response(review_comment()), Response(review(state="DISMISSED")))
        item = await api.get_pr_activity(webhook("pull_request_review_comment"))
        self.assertEqual(item["review_id"], 101)
        api, _ = client(Response(review(state="DISMISSED", body="Current rationale")))
        item = await api.get_pr_activity(webhook("pull_request_review", "submitted"))
        self.assertEqual((item["state"], item["action"]), ("dismissed", "dismissed"))

    async def test_stale_resolution_fetches_graphql_current_state(self):
        api, session = client(Response({"data": {"node": thread(isResolved=False, resolvedBy=None)}}))
        item = await api.get_pr_activity(webhook("pull_request_review_thread", "resolved"))
        self.assertEqual(item["state"], "unresolved")
        self.assertEqual(item["action"], "unresolved")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", github.API_ROOT + "/graphql"))
        self.assertTrue(kwargs["json"]["query"].startswith("query"))
        self.assertFalse(kwargs["allow_redirects"])

    async def test_thread_comment_edits_do_not_create_a_resolution_version(self):
        original = thread()
        edited = copy.deepcopy(original)
        edited["comments"]["nodes"][0].update(body="Edited comment text", updatedAt=UPDATED)
        api, _ = client(Response({"data": {"node": original}}), Response({"data": {"node": edited}}))
        event = webhook("pull_request_review_thread", "resolved")
        first = await api.get_pr_activity(event)
        second = await api.get_pr_activity(event)
        self.assertEqual(first["source_version"], second["source_version"])
        self.assertNotEqual(first["body"], second["body"])
        self.assertEqual(second["updated_at"], "")
        self.assertTrue(second["context_updated_at"].startswith("2026-09-26"))

    async def test_graphql_uses_full_64_bit_database_identifiers(self):
        raw = thread()
        raw["comments"]["nodes"][0].update(databaseId="4000000000",
            url=f"https://github.com/{REPO}/pull/18#discussion_r4000000000")
        raw["comments"]["nodes"][0]["pullRequestReview"]["databaseId"] = "5000000000"
        api, session = client(Response({"data": {"node": raw}}))
        item = await api.get_pr_activity(webhook("pull_request_review_thread", "resolved"))
        self.assertEqual(item["comment_id"], 4000000000)
        self.assertEqual(item["review_id"], 5000000000)
        self.assertIn("fullDatabaseId", session.calls[0][2]["json"]["query"])

    async def test_pending_review_thread_is_private(self):
        raw = thread()
        raw["comments"]["nodes"][0]["pullRequestReview"]["state"] = "PENDING"
        api, _ = client(Response({"data": {"node": raw}}))
        self.assertIsNone(await api.get_pr_activity(webhook("pull_request_review_thread", "resolved")))

    async def test_graphql_missing_partial_and_foreign_thread_state_fails_closed(self):
        for data in ({"data": {"node": None}}, {"data": {"node": thread()}, "errors": [{"message": "private-secret"}]},
                     {"data": {"node": thread(repository={"nameWithOwner": "Other/Repo"})}},
                     {"data": {"node": thread(pullRequest={"number": 19})}}):
            api, _ = client(Response(data))
            with self.assertRaises(github.GitHubError) as caught:
                await api.get_pr_activity(webhook("pull_request_review_thread", "resolved"))
            self.assertNotIn("private-secret", str(caught.exception))

    async def test_graphql_timeout_is_a_read_error_not_ambiguous_write(self):
        api, _ = client(asyncio.TimeoutError("secret"))
        with self.assertRaises(github.GitHubError) as caught:
            await api.get_pr_activity(webhook("pull_request_review_thread", "resolved"))
        self.assertNotIsInstance(caught.exception, github.GitHubAmbiguousWrite)
        self.assertTrue(caught.exception.retryable)

    async def test_superseded_synchronize_is_suppressed(self):
        current = pull(head={"sha": "c" * 40, "ref": "work", "repo": {"full_name": "Contributor/Fork"}})
        api, session = client(Response(current))
        self.assertIsNone(await api.get_pr_activity(webhook("pull_request", "synchronize")))
        self.assertEqual(len(session.calls), 1)

    async def test_fork_comparison_is_scoped_and_preserves_real_commit_count(self):
        comparison = {"status": "diverged", "total_commits": 23,
                      "commits": [{"sha": AFTER, "html_url": f"https://github.com/{REPO}/commit/{AFTER}",
                                   "commit": {"message": "Fix @everyone\nDetails"}}]}
        api, session = client(Response(pull()), Response(comparison))
        item = await api.get_pr_activity(webhook("pull_request", "synchronize"))
        self.assertEqual(item["commit_count"], 23)
        self.assertTrue(item["commits_truncated"])
        self.assertEqual(item["compare_status"], "diverged")
        self.assertNotIn("@everyone", item["commits"][0]["message"])
        self.assertIn(f"/repos/{REPO}/compare/{BEFORE}...{AFTER}", session.calls[-1][1])
        self.assertEqual(item["author"], "actor")

    async def test_unavailable_force_push_comparison_does_not_invent_count(self):
        for status in (404, 409, 422):
            api, _ = client(Response(pull()), Response({}, status))
            item = await api.get_pr_activity(webhook("pull_request", "synchronize"))
            self.assertNotIn("commit_count", item)
            self.assertTrue(item["comparison_unavailable"])
            self.assertTrue(item["url"].endswith("/pull/18/commits"))


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_page_returns_durable_scoped_cursor_and_never_follows_link_host(self):
        api, session = client(Response([comment()], headers={"Link": '<https://evil.example/steal>; rel="next"'}), Response([]))
        first = await api.list_pr_activity(18, "comment")
        self.assertEqual(len(first["items"]), 1)
        second = await api.list_pr_activity(18, "comment", first["next_cursor"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(session.calls[1][2]["params"]["page"], 2)
        self.assertTrue(all(call[1].startswith(github.API_ROOT + f"/repos/{REPO}/") for call in session.calls))
        for number, source in ((19, "comment"), (18, "review")):
            with self.assertRaises(github.GitHubError):
                await api.list_pr_activity(number, source, first["next_cursor"])

    async def test_large_discussion_resumes_beyond_two_thousand_items(self):
        responses = []
        for page in range(21):
            rows = [comment(id=page * 100 + index,
                html_url=f"https://github.com/{REPO}/pull/18#issuecomment-{page * 100 + index}")
                    for index in range(1, 101)]
            responses.append(Response(rows, headers={"Link": '<ignored>; rel="next"'}))
        api, session = client(*responses, Response([]))
        cursor, total = None, 0
        for _ in range(22):
            result = await api.list_pr_activity(18, "comment", cursor)
            total += len(result["items"])
            cursor = result["next_cursor"]
        self.assertEqual(total, 2100)
        self.assertIsNone(cursor)
        self.assertEqual(session.calls[-1][2]["params"], {"page": 22, "per_page": 100})

    async def test_thread_cursor_also_resumes_after_page_twenty(self):
        api, session = client(thread_page([thread()], True, "cursor21"))
        cursor = activity._cursor(REPO, 18, "thread", 21, "cursor20")
        result = await api.list_pr_activity(18, "thread", cursor)
        self.assertTrue(result["next_cursor"])
        self.assertEqual(session.calls[0][2]["json"]["variables"]["after"], "cursor20")

    async def test_reviews_exclude_pending_and_keep_dismissal(self):
        api, _ = client(Response([review(state="PENDING", submitted_at=None), review(state="DISMISSED")]))
        items = (await api.list_pr_activity(18, "review"))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["state"], "dismissed")

    async def test_graphql_thread_pages_use_opaque_api_cursor(self):
        api, session = client(thread_page([thread()], True, "cursor-one"), thread_page([thread(id="PRRT_second")]))
        first = await api.list_pr_activity(18, "thread")
        second = await api.list_pr_activity(18, "thread", first["next_cursor"])
        self.assertEqual(session.calls[1][2]["json"]["variables"]["after"], "cursor-one")
        self.assertIsNone(second["next_cursor"])

    async def test_repeating_thread_cursor_and_malformed_rows_fail(self):
        api, _ = client(thread_page([], True, "same"), thread_page([], True, "same"))
        first = await api.list_pr_activity(18, "thread")
        with self.assertRaises(github.GitHubError):
            await api.list_pr_activity(18, "thread", first["next_cursor"])
        for rows in ([None], [{"id": 99}], {}, [comment()] * 101):
            api, _ = client(Response(rows))
            with self.assertRaises(github.GitHubError):
                await api.list_pr_activity(18, "comment")

    async def test_commit_reconciliation_head_matches_webhook_identity_without_fabricated_author_time(self):
        api, _ = client(Response(pull()))
        item = (await api.list_pr_activity(18, "commits"))["items"][0]
        event = webhook("pull_request", "synchronize")
        self.assertEqual(item["key"], event["key"])
        self.assertEqual(item["source_version"], event["source_version"])
        self.assertEqual(item["author"], "")
        self.assertEqual(item["created_at"], "")
        self.assertNotIn("commit_count", item)

    async def test_cursor_does_not_accept_unscoped_json_or_invalid_encoding(self):
        for cursor in ("https://evil.example", "%%%", {}, base64.b64encode(b"[]").decode(),
                       base64.b64encode(json.dumps({"page": 1}).encode()).decode()):
            api, session = client()
            with self.assertRaises(github.GitHubError):
                await api.list_pr_activity(18, "comment", cursor)
            self.assertFalse(session.calls)


class PullDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    since = datetime(2026, 9, 26, 2, tzinfo=timezone.utc).timestamp()

    def row(self, number, **updates):
        return pull(number=number, id=number * 100,
                    html_url=f"https://github.com/{REPO}/pull/{number}", **updates)

    async def test_discovers_previously_unseen_closed_and_merged_prs_since_cutoff(self):
        api, session = client(Response([self.row(21, state="closed", merged_at=UPDATED),
                                       self.row(20, state="closed"), self.row(19)]))
        result = await api.list_updated_pulls(self.since)
        self.assertEqual([row["number"] for row in result["items"]], [21, 20, 19])
        self.assertTrue(result["items"][0]["merged"])
        self.assertTrue(result["items"][1]["closed"])
        self.assertFalse(result["items"][2]["closed"])
        self.assertIsNone(result["next_cursor"])
        self.assertEqual(session.calls[0][0], "GET")
        self.assertEqual(session.calls[0][1], f"{github.API_ROOT}/repos/{REPO}/pulls")
        self.assertEqual(session.calls[0][2]["params"], {
            "state": "all", "sort": "updated", "direction": "desc", "per_page": 100, "page": 1})

    async def test_includes_exact_cutoff_and_stops_at_sorted_older_boundary(self):
        api, _ = client(Response([self.row(21), self.row(20, updated_at=CREATED)],
                                 headers={"Link": '<ignored>; rel="next"'}))
        result = await api.list_updated_pulls(self.since)
        self.assertEqual([row["number"] for row in result["items"]], [21])
        self.assertIsNone(result["next_cursor"])

    async def test_discovery_cursor_resumes_and_is_bound_to_repository_and_cutoff(self):
        api, session = client(Response([self.row(21)], headers={"Link": '<https://evil.example>; rel="next"'}),
                              Response([self.row(20)]))
        first = await api.list_updated_pulls(self.since)
        second = await api.list_updated_pulls(self.since, first["next_cursor"])
        self.assertEqual(second["items"][0]["number"], 20)
        self.assertEqual(session.calls[1][2]["params"]["page"], 2)
        self.assertTrue(all(call[1].startswith(github.API_ROOT + f"/repos/{REPO}/") for call in session.calls))
        with self.assertRaises(github.GitHubError):
            await api.list_updated_pulls(self.since - 1, first["next_cursor"])
        other, other_session = client(config={**CONFIG, "repository": "Other/Repo"})
        with self.assertRaises(github.GitHubError):
            await other.list_updated_pulls(self.since, first["next_cursor"])
        self.assertFalse(other_session.calls)

    async def test_discovery_also_has_no_total_page_limit(self):
        api, session = client(Response([self.row(21)], headers={"Link": '<ignored>; rel="next"'}))
        cursor = activity._pull_discovery_cursor(REPO, self.since, 21)
        result = await api.list_updated_pulls(self.since, cursor)
        self.assertTrue(result["next_cursor"])
        self.assertEqual(session.calls[0][2]["params"]["page"], 21)

    async def test_invalid_cutoff_and_cursor_never_reach_network(self):
        for since in (True, "2026-09-26", None, float("inf"), float("nan"), -1, 1e100):
            api, session = client()
            with self.assertRaises(github.GitHubError):
                await api.list_updated_pulls(since)
            self.assertFalse(session.calls)
        for cursor in ("%%%", {}, activity._cursor(REPO, 18, "comment", 2),
                       base64.b64encode(json.dumps({"repository": REPO.lower(), "source": "updated_pulls",
                                                   "since": self.since, "page": True}).encode()).decode()):
            api, session = client()
            with self.assertRaises(github.GitHubError):
                await api.list_updated_pulls(self.since, cursor)
            self.assertFalse(session.calls)

    async def test_malformed_timestamps_ids_repository_or_page_fail_closed(self):
        rows = [self.row(21, updated_at=None), self.row(21, updated_at="2026-09-26T02:00:00"),
                self.row(21, updated_at="invalid"), {**self.row(21), "number": True},
                {**self.row(21), "id": []},
                {**self.row(21), "html_url": "https://github.com/Other/Repo/pull/21"}]
        for row in rows:
            api, _ = client(Response([row]))
            with self.assertRaises(github.GitHubError):
                await api.list_updated_pulls(self.since)
        for response in ([self.row(21)] * 101, [None], {}):
            api, _ = client(Response(response))
            with self.assertRaises(github.GitHubError):
                await api.list_updated_pulls(self.since)

    async def test_unsorted_page_cannot_claim_completion_at_false_old_boundary(self):
        api, _ = client(Response([self.row(20, updated_at=CREATED), self.row(21)]))
        with self.assertRaises(github.GitHubError):
            await api.list_updated_pulls(self.since)


class SetupChecksTests(unittest.IsolatedAsyncioTestCase):
    def installation(self, **updates):
        return {"id": 456, "app_id": 123, "suspended_at": None,
                "permissions": {"metadata": "read", "issues": "write", "pull_requests": "read", "contents": "read", "actions": "read"},
                "events": ["issues", "issue_comment", "pull_request", "pull_request_review", "pull_request_review_comment",
                           "pull_request_review_thread", "push", "release", "workflow_run"], **updates}

    async def checks(self, installation=None):
        api, session = client(Response(installation or self.installation()), Response({"full_name": REPO}))
        with patch.object(api, "_app_jwt", return_value="private-app-token"), patch.dict("os.environ", {"PROJECT_GITHUB_WEBHOOK_SECRET": "private-webhook-value"}):
            checks = await api.setup_checks()
        return checks, session

    async def test_read_only_checks_cover_installation_permissions_subscriptions_and_repository(self):
        checks, session = await self.checks()
        self.assertTrue(all(row["status"] == "passed" for row in checks))
        self.assertTrue(all(set(row) == {"key", "label", "status", "detail"} for row in checks))
        self.assertEqual({call[0] for call in session.calls}, {"GET"})
        self.assertNotIn("private-", json.dumps(checks))

    async def test_missing_permissions_subscriptions_and_suspension_are_actionable(self):
        checks, _ = await self.checks(self.installation(permissions={"issues": "read"}, events=["pull_request"], suspended_at=CREATED))
        by_key = {item["key"]: item for item in checks}
        self.assertEqual(by_key["github_installation"]["status"], "attention")
        self.assertEqual(by_key["github_permissions"]["status"], "attention")
        self.assertIn("issues: write", by_key["github_permissions"]["detail"])
        self.assertIn("pull_request_review_thread", by_key["github_subscriptions"]["detail"])

    async def test_missing_permissions_metadata_is_unverified_not_passed(self):
        checks, _ = await self.checks(self.installation(permissions=None, events=None))
        by_key = {item["key"]: item for item in checks}
        self.assertEqual(by_key["github_permissions"]["status"], "unverified")
        self.assertEqual(by_key["github_subscriptions"]["status"], "unverified")

    async def test_authentication_errors_are_sanitized_and_not_reported_as_success(self):
        api, session = client()
        with patch.object(api, "_app_jwt", side_effect=github.GitHubError("private-secret-key")):
            checks = await api.setup_checks()
        self.assertFalse(session.calls)
        self.assertNotIn("private-secret-key", json.dumps(checks))
        by_key = {item["key"]: item for item in checks}
        self.assertEqual(by_key["github_credentials"]["status"], "attention")
        self.assertEqual(by_key["github_repository"]["status"], "unverified")

    async def test_network_failure_and_repository_403_are_not_false_confirmation(self):
        api, _ = client(asyncio.TimeoutError("private-secret"), Response({}, 403))
        with patch.object(api, "_app_jwt", return_value="private-app-token"):
            checks = await api.setup_checks()
        by_key = {item["key"]: item for item in checks}
        self.assertEqual(by_key["github_installation"]["status"], "unverified")
        self.assertEqual(by_key["github_repository"]["status"], "attention")
        self.assertNotIn("private-secret", json.dumps(checks))

    async def test_malformed_permission_values_are_attention_without_type_errors(self):
        permissions = copy.deepcopy(self.installation()["permissions"])
        permissions["issues"] = []
        checks, _ = await self.checks(self.installation(permissions=permissions))
        self.assertEqual(next(row for row in checks if row["key"] == "github_permissions")["status"], "attention")


if __name__ == "__main__":
    unittest.main()
