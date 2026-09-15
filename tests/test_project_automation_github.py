"""GitHub boundaries use fake HTTP only; no live repository writes."""

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import project_automation_github as github


REPOSITORY = "SillyBunnyTeam/SillyBunny"
CONFIG = {"repository": REPOSITORY, "github_app_id": "123", "github_installation_id": "456"}
MARKER = "case-a1:revision-2"


def issue(number=17, **updates):
    return {"number": number, "title": "PNG import stalls", "body": "Observed import failure.",
            "state": "open", "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
            "updated_at": "2026-09-16T02:00:00Z", **updates}


def pull(number=18, **updates):
    return issue(number, **{"html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
                            "draft": False, "merged": False, **updates})


def comment(**updates):
    return {"id": 99, "body": "/ask-reporter Can you share the exact version?",
            "html_url": f"https://github.com/{REPOSITORY}/issues/17#issuecomment-99",
            "author_association": "MEMBER", "user": {"login": "maintainer", "type": "User"},
            **updates}


def payload(**updates):
    return {"repository": {"full_name": REPOSITORY},
            "sender": {"login": "maintainer", "id": 7, "type": "User"}, **updates}


class FakeContent:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset:offset + size]


class FakeResponse:
    def __init__(self, data=None, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(raw if raw is not None else json.dumps(data).encode())
        self.content_length = len(self.content.data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        self.closed = True


def token_response(token="fake-installation-secret"):
    expires = datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()
    return FakeResponse({"token": token, "expires_at": expires}, status=201)


def authenticated_client(*responses):
    session = FakeSession(*responses)
    client = github.GitHubClient(CONFIG, session)
    client._token = "fake-installation-secret"
    client._token_expiry = time.time() + 3600
    return client, session


class WebhookTests(unittest.TestCase):
    def test_signature_matches_official_vector_and_exact_bytes(self):
        signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        self.assertTrue(github.validate_webhook_signature(
            b"Hello, World!", signature, "It's a Secret to Everybody"))
        self.assertFalse(github.validate_webhook_signature(
            b"Hello, World!\n", signature, "It's a Secret to Everybody"))
        body = '{"text":"日本語"}'.encode()
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(github.validate_webhook_signature(body, signature, "secret"))

    def test_signature_rejects_missing_malformed_and_legacy_values(self):
        for signature in (None, "", "sha1=" + "a" * 40, "sha256=" + "x" * 64, []):
            self.assertFalse(github.validate_webhook_signature(b"{}", signature, "secret"))
        self.assertFalse(github.validate_webhook_signature(b"{}", "sha256=" + "a" * 64, ""))

    def test_issue_lifecycle_keeps_mapping_key_and_precise_status(self):
        for action, state in (("opened", "open"), ("edited", "open"), ("closed", "closed"),
                              ("reopened", "open")):
            result = github.normalize_event("issues", payload(action=action, issue=issue(
                state=state, state_reason="not_planned" if state == "closed" else None)), REPOSITORY)
            self.assertEqual(result["key"], "issue:17")
            self.assertEqual(result["closed"], state == "closed")
            self.assertFalse(result["merged"])
            self.assertEqual(result["sender"], "maintainer")
            self.assertEqual(result["url"], issue()["html_url"])

    def test_pr_distinguishes_merge_close_draft_and_reopen(self):
        result = github.normalize_event("pull_request", payload(action="closed", pull_request=pull(
            state="closed", merged_at="2026-09-16T02:00:00Z")), REPOSITORY)
        self.assertEqual(result["key"], "pull:18")
        self.assertTrue(result["merged"])
        result = github.normalize_event("pull_request", payload(action="closed", pull_request=pull(
            state="closed")), REPOSITORY)
        self.assertFalse(result["merged"])
        result = github.normalize_event("pull_request", payload(action="reopened", pull_request=pull(
            draft=True)), REPOSITORY)
        self.assertFalse(result["closed"])
        self.assertTrue(result["draft"])

    def test_malformed_payloads_and_wrong_repositories_are_ignored(self):
        for candidate in (None, [], {}, payload(issue=[], action="opened"),
                          payload(issue=issue(state=[]), action="opened"),
                          payload(issue=issue(number=True), action="opened"),
                          payload(issue=issue(html_url="https://evil.test/foo"), action="opened"),
                          payload(issue=issue(), action=[])):
            self.assertIsNone(github.normalize_event("issues", candidate, REPOSITORY))
        foreign = payload(issue=issue(), action="opened", repository={"full_name": "other/repo"})
        self.assertIsNone(github.normalize_event("issues", foreign, REPOSITORY))
        self.assertIsNone(github.normalize_event("issues", payload(), "https://github.com/a/b"))
        self.assertIsNone(github.normalize_event("issues", payload(
            action="opened", issue=issue(pull_request={})), REPOSITORY))

    def test_bounded_text_does_not_activate_mentions(self):
        result = github.normalize_event("issues", payload(action="edited", issue=issue(
            title="@everyone <@123> <#456> " + "a" * 500,
            body="@here " + "b" * 20000, labels=[{"name": "@role"}])), REPOSITORY)
        self.assertEqual(len(result["title"]), 256)
        self.assertEqual(len(result["body"]), github.MAX_BODY)
        self.assertNotIn("@everyone", result["title"])
        self.assertNotIn("<#456>", result["title"])
        self.assertEqual(result["labels"], ["@\u200brole"])

    def test_only_explicit_trusted_maintainer_questions_are_relayed(self):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            result = github.normalize_event("issue_comment", payload(
                action="created", issue=issue(), comment=comment(author_association=association)),
                REPOSITORY)
            self.assertEqual(result["key"], "comment:99")
            self.assertEqual(result["body"], "Can you share the exact version?")
            self.assertEqual(result["number"], 17)
        invalid = [comment(author_association="CONTRIBUTOR"), comment(author_association=[]),
                   comment(body="Can you share the exact version?"), comment(body="/ask-reporter"),
                   comment(body="/ask-reporter "), comment(body="/ask-reporterX hello"),
                   comment(body="/ask-reporter Hi " + github.marker_comment(MARKER)),
                   comment(html_url=f"https://github.com/{REPOSITORY}/issues/18#issuecomment-99"),
                   comment(user={"type": "Bot"})]
        for candidate in invalid:
            self.assertIsNone(github.normalize_event("issue_comment", payload(
                action="created", issue=issue(), comment=candidate), REPOSITORY))
        self.assertIsNone(github.normalize_event("issue_comment", payload(
            action="edited", issue=issue(), comment=comment()), REPOSITORY))
        self.assertIsNone(github.normalize_event("issue_comment", payload(
            action="created", issue=issue(), comment=comment(), sender={"type": "Bot"}), REPOSITORY))

    def test_push_groups_commits_and_has_stable_per_push_key(self):
        event = payload(ref="refs/heads/main", before="a" * 40, after="b" * 40,
                        commits=[{"id": "b" * 40, "message": "Fix @everyone\nDetails"},
                                 {"id": "c" * 40, "message": "Add tests"}])
        result = github.normalize_event("push", event, REPOSITORY)
        self.assertEqual(result["kind"], "commit")
        self.assertEqual(result["commit_count"], 2)
        self.assertEqual(len(result["commits"]), 2)
        self.assertNotIn("Details", result["body"])
        self.assertNotIn("@everyone", result["body"])
        self.assertEqual(result["key"], github.normalize_event("push", event, REPOSITORY)["key"])
        event["after"] = "d" * 40
        self.assertNotEqual(result["key"], github.normalize_event("push", event, REPOSITORY)["key"])
        event["after"] = "https://evil.test"
        self.assertIsNone(github.normalize_event("push", event, REPOSITORY))

    def test_general_events_preserve_release_and_workflow_status(self):
        release = {"id": 77, "tag_name": "v1.0", "name": "Release", "body": "New features",
                   "html_url": f"https://github.com/{REPOSITORY}/releases/tag/v1.0"}
        result = github.normalize_event("release", payload(action="published", release=release), REPOSITORY)
        self.assertEqual(result["key"], "release:77")
        self.assertEqual(result["state"], "published")
        release["draft"] = True
        self.assertIsNone(github.normalize_event("release", payload(
            action="created", release=release), REPOSITORY))
        run = {"id": 88, "name": "CI", "status": "completed", "conclusion": "failure",
               "html_url": f"https://github.com/{REPOSITORY}/actions/runs/88"}
        result = github.normalize_event("workflow_run", payload(action="completed", workflow_run=run), REPOSITORY)
        self.assertEqual(result["state"], "failure")
        self.assertEqual(result["key"], "workflow_run:88")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_validation_precedes_network(self):
        for name in ("a/b/../../", "https://github.com/a/b", "a/b?x=y", "a/..", "", None):
            with self.assertRaises(github.GitHubError):
                github.GitHubClient({**CONFIG, "repository": name})

    async def test_real_jwt_claims_and_private_key_file(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        client, _ = authenticated_client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.pem"
            path.write_text(pem, encoding="utf-8")
            with patch.dict(os.environ, {"PROJECT_GITHUB_PRIVATE_KEY": "",
                                         "PROJECT_GITHUB_PRIVATE_KEY_FILE": str(path)}):
                encoded = client._app_jwt()
        decoded = jwt.decode(encoded, private.public_key(), algorithms=["RS256"])
        self.assertEqual(decoded["iss"], "123")
        self.assertLessEqual(decoded["iat"], time.time() - 59)
        self.assertLessEqual(decoded["exp"], time.time() + 600)

    async def test_installation_token_is_cached_and_repository_scoped(self):
        session = FakeSession(token_response("x" * 2000), FakeResponse(issue()), FakeResponse(issue()))
        client = github.GitHubClient(CONFIG, session)
        with patch.object(client, "_app_jwt", return_value="fake-app-secret"):
            await client.get_issue(17)
            await client.get_issue(17)
        self.assertEqual(len(session.calls), 3)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, github.API_ROOT + "/app/installations/456/access_tokens")
        self.assertEqual(kwargs["json"], {"repositories": ["SillyBunny"]})
        self.assertEqual(session.calls[1][2]["headers"]["Authorization"], "Bearer " + "x" * 2000)
        self.assertTrue(all(call[2]["allow_redirects"] is False for call in session.calls))

    async def test_expired_and_rejected_tokens_refresh_once(self):
        session = FakeSession(token_response(), FakeResponse({}, 401), token_response("replacement"),
                              FakeResponse(issue()))
        client = github.GitHubClient(CONFIG, session)
        client._token, client._token_expiry = "expired", time.time() - 1
        with patch.object(client, "_app_jwt", return_value="fake-app-secret"):
            self.assertEqual((await client.get_issue(17))["number"], 17)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(session.calls[-1][2]["headers"]["Authorization"], "Bearer replacement")

    async def test_search_quotes_terms_filters_foreign_results_and_prs(self):
        foreign = issue(19, html_url="https://github.com/Other/Repo/issues/19")
        client, session = authenticated_client(FakeResponse({"items": [issue(), foreign,
                                                issue(18, pull_request={})]}))
        found = await client.search_issues("crash OR repo:Other/Repo", 8)
        self.assertEqual([row["number"] for row in found], [17])
        query = session.calls[0][2]["params"]["q"]
        self.assertTrue(query.startswith(f"repo:{REPOSITORY} is:issue "))
        self.assertNotIn("repo:Other", query)
        self.assertIn('"OR"', query)

    async def test_get_pull_fetches_authoritative_merge_status(self):
        client, session = authenticated_client(FakeResponse(issue(18, pull_request={})),
                                               FakeResponse(pull(state="closed", merged=True)))
        result = await client.get_issue(18)
        self.assertEqual(result["kind"], "pull")
        self.assertTrue(result["merged"])
        self.assertTrue(session.calls[-1][1].endswith("/pulls/18"))

    async def test_pagination_reconstructs_urls_instead_of_following_headers(self):
        client, session = authenticated_client(FakeResponse([issue(), issue(18, pull_request={})],
            headers={"Link": '<https://evil.test/steal>; rel="next"'}), FakeResponse([issue(19)]))
        result = await client.list_open_items()
        self.assertEqual([row["number"] for row in result], [17, 19])
        self.assertTrue(all(call[1].startswith(github.API_ROOT + f"/repos/{REPOSITORY}/")
                            for call in session.calls))
        self.assertEqual(session.calls[-1][2]["params"]["page"], 2)

    async def test_pagination_limit_fails_instead_of_claiming_complete_scan(self):
        client, _ = authenticated_client(FakeResponse([], headers={"Link": '<x>; rel="next"'}))
        with patch.object(github, "MAX_PAGES", 1), self.assertRaises(github.GitHubError):
            await client.find_issue_by_marker(MARKER)

    async def test_malformed_recovery_scan_cannot_authorize_a_new_issue(self):
        for rows in ([None], [{"number": 17}], [issue(html_url="https://evil.test/item")]):
            client, session = authenticated_client(FakeResponse(rows))
            with self.assertRaises(github.GitHubError):
                await client.create_issue("Title", "Body", MARKER)
            self.assertEqual([call[0] for call in session.calls], ["GET"])

    async def test_existing_issue_marker_prevents_another_post(self):
        existing = issue(state="closed", body="Old issue\n" + github.marker_comment(MARKER),
                         performed_via_github_app={"id": 123})
        client, session = authenticated_client(FakeResponse([existing]))
        result = await client.create_issue("New title", "New body", MARKER)
        self.assertEqual(result["number"], 17)
        self.assertEqual([call[0] for call in session.calls], ["GET"])
        self.assertEqual(session.calls[0][2]["params"]["state"], "all")

    async def test_issue_publication_uses_marker_and_neutralizes_mentions(self):
        created = issue(body="Details\n" + github.marker_comment(MARKER))
        client, session = authenticated_client(FakeResponse([]), FakeResponse(created, 201))
        result = await client.create_issue("@everyone Import broken", "Hi <@123>", MARKER)
        self.assertEqual(result["number"], 17)
        outgoing = session.calls[-1][2]["json"]
        self.assertNotIn("@everyone", outgoing["title"])
        self.assertNotIn("<@123>", outgoing["body"])
        self.assertTrue(outgoing["body"].endswith(github.marker_comment(MARKER)))

    async def test_timeout_after_write_is_reconciled_without_second_post(self):
        created = issue(body="Details\n" + github.marker_comment(MARKER),
                        performed_via_github_app={"id": 123})
        client, session = authenticated_client(FakeResponse([]), asyncio.TimeoutError("secret"),
                                               FakeResponse([created]))
        result = await client.create_issue("Title", "Body", MARKER)
        self.assertEqual(result["number"], 17)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST", "GET"])

    async def test_unresolved_write_requires_recovery_even_if_called_again(self):
        client, session = authenticated_client(FakeResponse([]), asyncio.TimeoutError("secret"),
                                               FakeResponse([]), FakeResponse([]))
        for _ in range(2):
            with self.assertRaises(github.GitHubAmbiguousWrite) as caught:
                await client.create_issue("Title", "Body", MARKER)
            self.assertEqual(caught.exception.operation, "create_issue")
            self.assertEqual(caught.exception.marker, MARKER)
            self.assertFalse(caught.exception.retryable)
            self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(sum(call[0] == "POST" for call in session.calls), 1)

    async def test_server_failure_and_invalid_success_require_recovery(self):
        failures = [FakeResponse({"error": "secret"}, 502), FakeResponse(raw=b"not json"),
                    FakeResponse(issue(body=None), 201)]
        for failed in failures:
            client, session = authenticated_client(FakeResponse([]), failed, FakeResponse([]))
            with self.assertRaises(github.GitHubAmbiguousWrite):
                await client.create_issue("Title", "Body", MARKER)
            self.assertEqual(sum(call[0] == "POST" for call in session.calls), 1)

    async def test_known_rejection_is_not_reported_as_ambiguous(self):
        client, _ = authenticated_client(FakeResponse([]), FakeResponse({"error": "secret"}, 422))
        with self.assertRaises(github.GitHubError) as caught:
            await client.create_issue("Title", "Body", MARKER)
        self.assertNotIsInstance(caught.exception, github.GitHubAmbiguousWrite)
        self.assertEqual(caught.exception.status, 422)
        self.assertNotIn("secret", str(caught.exception))

    async def test_comment_is_recovered_after_ambiguous_delivery(self):
        created = comment(body="Approved reply\n" + github.marker_comment(MARKER),
                          performed_via_github_app={"id": 123})
        client, session = authenticated_client(FakeResponse([]), OSError("private detail"),
                                               FakeResponse([created]))
        result = await client.add_comment(17, "Approved reply", MARKER)
        self.assertEqual(result["id"], 99)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST", "GET"])

    async def test_recovery_requires_matching_server_owned_app_attribution(self):
        for attribution in (None, {}, {"id": 999}, {"id": "123"}, {"id": True}, []):
            forged_issue = issue(body=github.marker_comment(MARKER), performed_via_github_app=attribution)
            forged_comment = comment(body=github.marker_comment(MARKER), performed_via_github_app=attribution)
            client, _ = authenticated_client(FakeResponse([forged_issue]), FakeResponse([forged_comment]))
            self.assertIsNone(await client.find_issue_by_marker(MARKER))
            self.assertIsNone(await client.find_comment_by_marker(17, MARKER))
        without_field = issue(body=github.marker_comment(MARKER))
        client, _ = authenticated_client(FakeResponse([without_field]))
        self.assertIsNone(await client.find_issue_by_marker(MARKER))

    async def test_copied_contributor_marker_does_not_bind_the_wrong_issue(self):
        forged = issue(17, body=github.marker_comment(MARKER), performed_via_github_app=None)
        actual = issue(18, body="Approved report\n" + github.marker_comment(MARKER),
                       performed_via_github_app={"id": 123})
        client, session = authenticated_client(FakeResponse([forged]), FakeResponse(actual, 201))
        result = await client.create_issue("Title", "Approved report", MARKER)
        self.assertEqual(result["number"], 18)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST"])

    async def test_cancelled_write_propagates_without_running_reconciliation(self):
        client, session = authenticated_client(FakeResponse([]), asyncio.CancelledError(), FakeResponse([]))
        with self.assertRaises(asyncio.CancelledError):
            await client.create_issue("Title", "Body", MARKER)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST"])
        self.assertIn(("create_issue", MARKER), client._ambiguous)
        with self.assertRaises(github.GitHubAmbiguousWrite):
            await client.create_issue("Title", "Body", MARKER)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST", "GET"])

    async def test_public_text_is_idempotent_and_preview_matches_submission(self):
        prepared = github.prepare_public_text("  Hi @everyone\r\nSee <#123> <@456>\x00  ", 60000)
        self.assertEqual(prepared, github.prepare_public_text(prepared, 60000))
        self.assertNotIn("@everyone", prepared)
        self.assertNotIn("<#123>", prepared)
        self.assertNotIn("\r", prepared)
        created = issue(body=prepared + "\n\n" + github.marker_comment(MARKER))
        client, session = authenticated_client(FakeResponse([]), FakeResponse(created, 201))
        await client.create_issue("Title", prepared, MARKER)
        self.assertEqual(session.calls[-1][2]["json"]["body"],
                         prepared + "\n\n" + github.marker_comment(MARKER))

    async def test_approved_draft_cannot_copy_another_cases_recovery_marker(self):
        copied_marker = github.marker_comment("another-case:revision-4")
        prepared = github.prepare_public_text("Evidence " + copied_marker, 60000)
        self.assertEqual(prepared, "Evidence")
        created = issue(body=prepared + "\n\n" + github.marker_comment(MARKER),
                        performed_via_github_app={"id": 123})
        client, session = authenticated_client(FakeResponse([]), FakeResponse(created, 201))
        result = await client.create_issue("Title", prepared, MARKER)
        self.assertEqual(session.calls[-1][2]["json"]["body"].count(github.MARKER_PREFIX), 1)
        self.assertNotIn(copied_marker, session.calls[-1][2]["json"]["body"])
        self.assertNotIn(github.MARKER_PREFIX, result["body"])

    async def test_redirects_errors_and_bad_keys_do_not_expose_secrets(self):
        for failed in (FakeResponse({"message": "private-secret"}, 302), OSError("private-secret")):
            client, session = authenticated_client(failed)
            with self.assertRaises(github.GitHubError) as caught:
                await client.get_issue(17)
            self.assertNotIn("private-secret", str(caught.exception))
            self.assertEqual(len(session.calls), 1)
        client, _ = authenticated_client()
        with patch.dict(os.environ, {"PROJECT_GITHUB_PRIVATE_KEY": "private-secret"}):
            with self.assertRaises(github.GitHubError) as caught:
                client._app_jwt()
            self.assertNotIn("private-secret", str(caught.exception))

    async def test_invalid_numbers_and_oversized_publication_fail_before_network(self):
        client, session = authenticated_client()
        for number in (0, -1, "17", True):
            with self.assertRaises(github.GitHubError):
                await client.get_issue(number)
        with self.assertRaises(github.GitHubError):
            await client.create_issue("Title", "b" * 60001, MARKER)
        with self.assertRaises(github.GitHubError):
            await client.create_issue("Title", "Body", "unsafe --> marker")
        self.assertEqual(session.calls, [])

    async def test_injected_session_is_not_closed_by_client(self):
        client, session = authenticated_client()
        await client.close()
        self.assertFalse(session.closed)


if __name__ == "__main__":
    unittest.main()
