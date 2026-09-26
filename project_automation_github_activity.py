"""Current, repository-scoped PR activity and read-only GitHub setup checks.

Webhook payloads identify sources; they are not authority for their current text
or resolution state. Versions are equality tokens, not sortable revision numbers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from project_automation_github import (
    GitHubError, _ENV_NAME, _item, _number, _repo_url, _text,
    validate_repository,
)


ACTIVITY_SOURCES = ("comment", "review_comment", "review", "thread", "commits")
MAX_ACTIVITY_BODY = 65536
_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_NODE = re.compile(r"[A-Za-z0-9_+=/-]{1,200}\Z")
_REVIEW_STATES = {"approved", "changes_requested", "commented", "dismissed"}
_THREAD_FIELDS = """
    id isResolved isOutdated path line startLine originalLine originalStartLine
    resolvedBy { login }
    repository { nameWithOwner }
    pullRequest { number }
    comments(first: 1) { nodes {
        databaseId: fullDatabaseId url body createdAt updatedAt
        author { login __typename }
        pullRequestReview { databaseId: fullDatabaseId state }
    } }
"""
_THREAD_QUERY = "query($id: ID!) { node(id: $id) { ... on PullRequestReviewThread { " + _THREAD_FIELDS + " } } }"
_THREADS_QUERY = """
    query($owner: String!, $name: String!, $number: Int!, $after: String) {
        repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
                reviewThreads(first: 100, after: $after) {
                    pageInfo { hasNextPage endCursor }
                    nodes { """ + _THREAD_FIELDS + """ }
                }
            }
        }
    }
"""


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return ""


def _actor(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"author": _text(raw.get("login"), 100),
            "author_type": "Bot" if raw.get("type", raw.get("__typename")) == "Bot" else "User",
            "author_id": _number(raw.get("id"))}


def _database_id(value: Any) -> int:
    # GraphQL BigInt may arrive as a decimal string. REST IDs remain integers.
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        value = int(value)
    return _number(value)


def _version(item: dict) -> dict:
    # A commit's identity is its head SHA. A PR title edit or a newly available
    # comparison must not turn the same head into a second code update.
    if item["activity_type"] == "commits":
        content = {"key": item["key"]}
    elif item["activity_type"] == "thread":
        # Editing the opening comment is a review-comment update, not a new
        # resolution. GitHub exposes no resolution timestamp on the thread.
        fields = ("key", "state", "resolved_by", "outdated", "path", "line",
                  "start_line", "comment_id", "url")
        content = {key: item.get(key) for key in fields}
    else:
        fields = ("key", "body", "body_truncated", "body_digest", "state", "deleted",
                  "updated_at", "author", "author_type", "path", "line", "start_line",
                  "reply_to_id", "review_id", "resolved_by", "outdated")
        content = {key: item.get(key) for key in fields}
    item["source_version"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return item


def _base(repository: str, number: int, source: str, identifier: str, raw: dict) -> dict:
    body = raw.get("body") if isinstance(raw.get("body"), str) else ""
    safe_body = _text(body.encode("utf-8", errors="replace").decode("utf-8"), MAX_ACTIVITY_BODY * 2)
    created = _timestamp(raw.get("created_at") or raw.get("submitted_at"))
    updated = _timestamp(raw.get("updated_at")) or created
    return {"kind": "pr_activity", "repository": repository, "number": number,
            "activity_type": source, "source_id": identifier,
            "key": f"pr:{number}:{source}:{identifier}",
            **_actor(raw.get("user")), "body": safe_body[:MAX_ACTIVITY_BODY],
            "body_truncated": len(safe_body) > MAX_ACTIVITY_BODY,
            "body_digest": hashlib.sha256(body.encode("utf-8", errors="surrogatepass")).hexdigest(),
            "created_at": created, "updated_at": updated, "deleted": False}


def _source_url(raw: dict, repository: str, number: int, source: str, identifier: int) -> str:
    url = _repo_url(raw.get("html_url"), repository)
    if not url:
        return ""
    parts = urlsplit(url)
    path = parts.path.lower()
    pull_path = f"/{repository}/pull/{number}".lower()
    if source == "comment":
        valid = (path in {pull_path, f"/{repository}/issues/{number}".lower()}
                 and parts.fragment == f"issuecomment-{identifier}")
    elif source == "review_comment":
        valid = ((path == pull_path and parts.fragment == f"discussion_r{identifier}")
                 or (path == pull_path + "/files" and parts.fragment in
                     {f"r{identifier}", f"discussion_r{identifier}"}))
    else:
        valid = path == pull_path and parts.fragment == f"pullrequestreview-{identifier}"
    # Never follow API URLs from a response. If present, verify they corroborate
    # the PR identity instead of letting a plausible HTML URL conceal a mismatch.
    field, endpoint = ("issue_url", "issues") if source == "comment" else ("pull_request_url", "pulls")
    if field in raw:
        valid = (valid and isinstance(raw[field], str)
                 and raw[field].lower() == f"https://api.github.com/repos/{repository}/{endpoint}/{number}".lower())
    return url if valid else ""


def _source(raw: Any, repository: str, number: int, source: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    identifier = _number(raw.get("id"))
    url = _source_url(raw, repository, number, source, identifier)
    if not identifier or not url:
        return None
    item = _base(repository, number, source, str(identifier), raw)
    item.update(url=url, html_url=url)
    if source == "review":
        state = raw.get("state")
        state = state.lower() if isinstance(state, str) else ""
        if state not in _REVIEW_STATES or not _timestamp(raw.get("submitted_at")):
            return None
        item.update(state=state, review_id=identifier, submitted_at=_timestamp(raw["submitted_at"]))
    else:
        # Some API shapes expose draft status explicitly. Canonical reads also
        # inspect the parent review before delivering a review comment.
        if isinstance(raw.get("state"), str) and raw["state"].lower() == "pending":
            return None
        item.update(state="published", comment_id=identifier)
        if source == "review_comment":
            reply = _number(raw.get("in_reply_to_id"))
            item.update(path=_text(raw.get("path"), 1024),
                        line=_number(raw.get("line")) or _number(raw.get("original_line")) or None,
                        start_line=_number(raw.get("start_line")) or _number(raw.get("original_start_line")) or None,
                        reply_to_id=reply, review_id=_number(raw.get("pull_request_review_id")),
                        reply_to_url=f"https://github.com/{repository}/pull/{number}#discussion_r{reply}" if reply else "")
    return _version(item)


def _thread(raw: Any, repository: str, number: int, *, action: str = "") -> dict | None:
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("node_id", raw.get("id"))
    if not isinstance(identifier, str) or not _NODE.fullmatch(identifier):
        return None
    comments = raw.get("comments")
    graphql = isinstance(comments, dict)
    if graphql:
        repo = raw.get("repository")
        pull = raw.get("pullRequest")
        if (not isinstance(repo, dict) or not isinstance(repo.get("nameWithOwner"), str)
                or repo["nameWithOwner"].lower() != repository.lower()
                or not isinstance(pull, dict) or pull.get("number") != number
                or not isinstance(raw.get("isResolved"), bool)):
            return None
        comments = comments.get("nodes")
    if not isinstance(comments, list) or not comments or not isinstance(comments[0], dict):
        return None
    first = comments[0]
    if graphql:
        review = first.get("pullRequestReview")
        if (not isinstance(review, dict) or not isinstance(review.get("state"), str)
                or review["state"].lower() not in _REVIEW_STATES):
            return None
        first = {"id": _database_id(first.get("databaseId")), "html_url": first.get("url"),
                 "body": first.get("body"), "created_at": first.get("createdAt"),
                 "updated_at": first.get("updatedAt"), "user": first.get("author"),
                 "pull_request_review_id": _database_id(review.get("databaseId"))}
    source = _source(first, repository, number, "review_comment")
    if source is None:
        return None
    item = _base(repository, number, "thread", identifier, first)
    resolved = raw.get("isResolved") if graphql else action == "resolved"
    resolver = raw.get("resolvedBy")
    item.update(url=source["url"], html_url=source["url"], thread_id=identifier,
                state="resolved" if resolved else "unresolved",
                updated_at="", context_updated_at=item["updated_at"],
                path=_text(raw.get("path") if graphql else first.get("path"), 1024),
                line=_number(raw.get("line")) or
                _number(raw.get("originalLine")) or source.get("line"),
                start_line=_number(raw.get("startLine")) or
                _number(raw.get("originalStartLine")) or source.get("start_line"),
                resolved_by=_text(resolver.get("login"), 100) if isinstance(resolver, dict) else "",
                outdated=raw.get("isOutdated") is True,
                comment_id=source["comment_id"], review_id=source["review_id"])
    return _version(item)


def _commits(raw: Any, repository: str, number: int, before: str = "") -> dict | None:
    if not isinstance(raw, dict) or _item(raw, repository, "pull") is None or raw.get("number") != number:
        return None
    head = raw.get("head")
    if not isinstance(head, dict) or not isinstance(head.get("sha"), str) or not _SHA.fullmatch(head["sha"]):
        return None
    head_repo = head.get("repo")
    try:
        head_repository = validate_repository(head_repo.get("full_name")) if isinstance(head_repo, dict) else ""
    except GitHubError:
        return None
    sha = head["sha"]
    item = _base(repository, number, "commits", sha, {})
    # The PR author's identity and PR.updated_at do not identify who/when a code
    # push happened. Reconciliation can only establish its current head.
    item.update(url=f"https://github.com/{repository}/pull/{number}/commits",
                html_url=f"https://github.com/{repository}/pull/{number}/commits",
                observed_updated_at=_timestamp(raw.get("updated_at")),
                state="updated", before=before if isinstance(before, str) and _SHA.fullmatch(before) else "",
                after=sha, head_ref=_text(head.get("ref"), 512), head_repository=head_repository,
                commits=[], compare_url="", comparison_unavailable=True)
    return _version(item)


def normalize_pr_activity(event: str, payload: dict, repository: str, base: dict) -> dict | None:
    action = base["action"]
    raw_pull = payload.get("issue" if event == "issue_comment" else "pull_request")
    if not isinstance(raw_pull, dict):
        return None
    number = _number(raw_pull.get("number"))
    # The issue_comment payload uses issue fields, but marks PRs explicitly.
    if (not number or _item(raw_pull, repository, "pull") is None):
        return None
    if event == "issue_comment" and action in {"created", "edited", "deleted"}:
        item = _source(payload.get("comment"), repository, number, "comment")
    elif event == "pull_request_review_comment" and action in {"created", "edited", "deleted"}:
        item = _source(payload.get("comment"), repository, number, "review_comment")
    elif event == "pull_request_review" and action in {"submitted", "edited", "dismissed"}:
        item = _source(payload.get("review"), repository, number, "review")
    elif event == "pull_request_review_thread" and action in {"resolved", "unresolved"}:
        item = _thread(payload.get("thread"), repository, number, action=action)
    elif event == "pull_request" and action == "synchronize":
        item = _commits(raw_pull, repository, number, payload.get("before", ""))
        after = payload.get("after")
        if item is not None and after is not None and after != item["after"]:
            return None
        if item is not None:
            item.update(author=base.get("sender", ""), author_type=base.get("sender_type", "User"),
                        author_id=base.get("sender_id", 0), updated_at=_timestamp(raw_pull.get("updated_at")))
    else:
        return None
    return {**base, **item} if item is not None else None


async def _graphql(client, query: str, variables: dict) -> dict:
    # POST here is a fixed read-only query, not a publication. Do not classify a
    # network failure as an ambiguous public write or retry mutations.
    for attempt in range(2):
        token = await client._installation_token()
        try:
            result, _ = await client._http("POST", "/graphql", token,
                                          data={"query": query, "variables": variables}, mutation=False)
            break
        except GitHubError as exc:
            if exc.status != 401 or attempt:
                raise
            client._token, client._token_expiry = "", 0.0
    if (not isinstance(result, dict) or result.get("errors")
            or not isinstance(result.get("data"), dict)):
        raise GitHubError("GitHub could not verify current review-thread state.")
    return result["data"]


def _identity(event: Any, repository: str) -> tuple[int, str, str]:
    if not isinstance(event, dict) or event.get("kind") != "pr_activity":
        raise GitHubError("Invalid pull-request activity.")
    number, source, identifier = event.get("number"), event.get("activity_type"), event.get("source_id")
    repo = event.get("repository")
    if (not isinstance(repo, str) or repo.lower() != repository.lower() or not _number(number)
            or not isinstance(source, str) or source not in ACTIVITY_SOURCES
            or not isinstance(identifier, str)):
        raise GitHubError("Invalid pull-request activity identity.")
    if source == "thread":
        valid = bool(_NODE.fullmatch(identifier))
    elif source == "commits":
        valid = bool(_SHA.fullmatch(identifier))
    else:
        valid = bool(re.fullmatch(r"[1-9][0-9]{0,18}", identifier)) and bool(_number(int(identifier)))
    if not valid or event.get("key") != f"pr:{number}:{source}:{identifier}":
        raise GitHubError("Invalid pull-request activity source.")
    return number, source, identifier


async def _pull(client, number: int) -> dict:
    raw, _ = await client._call(f"/repos/{client.repository}/pulls/{number}")
    item = _item(raw, client.repository, "pull")
    if item is None or item["number"] != number:
        raise GitHubError("GitHub returned an invalid pull request.")
    return raw


async def _published_review(client, number: int, review_id: int) -> bool:
    if not review_id:
        raise GitHubError("GitHub did not identify the review for this comment.")
    raw, _ = await client._call(f"/repos/{client.repository}/pulls/{number}/reviews/{review_id}")
    if not isinstance(raw, dict) or raw.get("id") != review_id:
        raise GitHubError("GitHub returned an invalid comment review.")
    state = raw.get("state")
    if isinstance(state, str) and state.lower() == "pending":
        return False
    if _source(raw, client.repository, number, "review") is None:
        raise GitHubError("GitHub could not verify that the comment review was published.")
    return True


def _pending_thread(raw: Any) -> bool:
    if not isinstance(raw, dict) or not isinstance(raw.get("comments"), dict):
        return False
    nodes = raw["comments"].get("nodes")
    if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
        return False
    review = nodes[0].get("pullRequestReview")
    return isinstance(review, dict) and str(review.get("state", "")).lower() == "pending"


async def get_pr_activity(client, event: dict) -> dict | None:
    number, source, identifier = _identity(event, client.repository)
    if source == "thread":
        data = await _graphql(client, _THREAD_QUERY, {"id": identifier})
        if _pending_thread(data.get("node")):
            return None
        item = _thread(data.get("node"), client.repository, number)
        if item is None or item["source_id"] != identifier:
            raise GitHubError("GitHub could not verify current review-thread state.")
    elif source == "commits":
        item = _commits(await _pull(client, number), client.repository, number, event.get("before", ""))
        if item is None:
            raise GitHubError("GitHub returned an invalid pull-request head.")
        if item["source_id"] != identifier:
            return None  # An older synchronize delivery cannot revive an old head.
        item.update(**_actor({"login": event.get("author"), "type": event.get("author_type"),
                              "id": event.get("author_id")}))
        # Only the source event's timestamp is known. The current PR may since
        # have been edited without another push; never substitute that time.
        item["updated_at"] = _timestamp(event.get("updated_at"))
        if item["before"] and item["before"] != item["after"]:
            try:
                raw, _ = await client._call(
                    f"/repos/{client.repository}/compare/{item['before']}...{item['after']}",
                    params={"per_page": 20, "page": 1})
            except GitHubError as exc:
                if exc.status not in {404, 409, 422}:
                    raise
            else:
                _comparison(item, raw, client.repository)
    else:
        endpoint = {"comment": f"issues/comments/{identifier}",
                    "review_comment": f"pulls/comments/{identifier}",
                    "review": f"pulls/{number}/reviews/{identifier}"}[source]
        try:
            raw, _ = await client._call(f"/repos/{client.repository}/{endpoint}")
        except GitHubError as exc:
            if exc.status != 404 or source == "review":
                raise
            await _pull(client, number)  # Repository/PR access must still work.
            # Keep identity and safe author context, never deleted source text.
            raw = {"id": int(identifier), "body": "", "user": {
                "login": event.get("author"), "type": event.get("author_type")},
                "html_url": event.get("html_url"), "created_at": event.get("created_at"),
                "updated_at": event.get("updated_at")}
            item = _source(raw, client.repository, number, source)
            if item is None:
                raise GitHubError("GitHub activity deletion could not be verified.") from None
            item.update(action="deleted", state="deleted", deleted=True)
            return _version(item)
        if source == "review" and isinstance(raw, dict) and str(raw.get("state", "")).lower() == "pending":
            return None
        item = _source(raw, client.repository, number, source)
        if item is None or item["source_id"] != identifier:
            raise GitHubError("GitHub returned an invalid pull-request activity source.")
        if source == "review_comment" and not await _published_review(client, number, item["review_id"]):
            return None
    # Transport/action fields do not participate in source versioning. Preserve
    # edited metadata, but never repeat a stale delete after a successful read.
    action = event.get("action", "reconciled")
    if source == "thread":
        action = item["state"]
    elif source == "review" and item["state"] == "dismissed":
        action = "dismissed"
    elif action == "deleted":
        action = "reconciled"
    return {**item, "event": _text(event.get("event"), 60), "action": _text(action, 60)}


def _comparison(item: dict, raw: Any, repository: str) -> None:
    if (not isinstance(raw, dict) or not isinstance(raw.get("status"), str)
            or raw["status"] not in {"ahead", "behind", "diverged", "identical"}):
        raise GitHubError("GitHub returned an invalid commit comparison.")
    rows, count = raw.get("commits"), raw.get("total_commits")
    if (not isinstance(rows, list) or len(rows) > 20 or not isinstance(count, int)
            or isinstance(count, bool) or count < 0):
        raise GitHubError("GitHub returned an invalid commit comparison.")
    summaries = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("sha"), str) or not _SHA.fullmatch(row["sha"]):
            raise GitHubError("GitHub returned an invalid comparison commit.")
        commit = row.get("commit")
        if not isinstance(commit, dict):
            raise GitHubError("GitHub returned an invalid comparison commit.")
        url = _repo_url(row.get("html_url"), repository)
        if not url or urlsplit(url).path.lower() != f"/{repository}/commit/{row['sha']}".lower():
            raise GitHubError("GitHub returned a comparison outside the configured repository.")
        summaries.append({"sha": row["sha"], "message": _text(commit.get("message"), 300).split("\n", 1)[0],
                          "url": url, **_actor(row.get("author"))})
    item.update(commits=summaries, commit_count=count, commits_truncated=count > len(summaries),
                compare_status=raw["status"], comparison_unavailable=False,
                compare_url=f"https://github.com/{repository}/compare/{item['before']}...{item['after']}")


def _cursor(repository: str, number: int, source: str, page: int, after: str | None = None) -> str:
    payload = {"repository": repository.lower(), "number": number, "source": source, "page": page, "after": after}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _parse_cursor(cursor: str | None, repository: str, number: int, source: str) -> tuple[int, str | None]:
    if cursor is None:
        return 1, None
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError
        raw = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(raw, dict) or raw.get("repository") != repository.lower()
                or raw.get("number") != number or raw.get("source") != source
                or not _number(raw.get("page"))
                or (raw.get("after") is not None and (not isinstance(raw["after"], str)
                                                     or len(raw["after"]) > 2048))):
            raise ValueError
        return raw["page"], raw.get("after")
    except (ValueError, TypeError, UnicodeError):
        raise GitHubError("Invalid pull-request activity reconciliation cursor.") from None


async def list_pr_activity(client, number: int, source: str, cursor: str | None = None) -> dict:
    if not _number(number) or not isinstance(source, str) or source not in ACTIVITY_SOURCES:
        raise GitHubError("Invalid pull-request activity source.")
    page, after = _parse_cursor(cursor, client.repository, number, source)
    next_after = None
    if source == "commits":
        if cursor is not None:
            raise GitHubError("A pull-request head has no next page.")
        item = _commits(await _pull(client, number), client.repository, number)
        if item is None:
            raise GitHubError("GitHub returned an invalid pull-request head.")
        return {"items": [{**item, "action": "reconciled"}], "next_cursor": None}
    if source == "thread":
        owner, name = client.repository.split("/", 1)
        data = await _graphql(client, _THREADS_QUERY, {"owner": owner, "name": name, "number": number, "after": after})
        try:
            connection = data["repository"]["pullRequest"]["reviewThreads"]
            rows, info = connection["nodes"], connection["pageInfo"]
            has_next, next_after = info["hasNextPage"], info["endCursor"]
            if (not isinstance(has_next, bool) or (has_next and
                    (not isinstance(next_after, str) or not next_after or next_after == after or len(next_after) > 2048))):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise GitHubError("GitHub returned invalid review-thread pagination.") from None
    else:
        endpoint = {"comment": f"issues/{number}/comments", "review_comment": f"pulls/{number}/comments",
                    "review": f"pulls/{number}/reviews"}[source]
        rows, headers = await client._call(f"/repos/{client.repository}/{endpoint}",
                                          params={"per_page": 100, "page": page})
        # Construct subsequent requests locally, never follow a Link URL.
        link = next((value for key, value in headers.items() if key.lower() == "link"), "")
        has_next = bool(re.search(r'rel\s*=\s*"next"', link))
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise GitHubError("GitHub returned an invalid PR activity list.")
    items = []
    for raw in rows:
        if source == "review" and str(raw.get("state", "")).lower() == "pending":
            continue
        if source == "thread" and _pending_thread(raw):
            continue
        item = _thread(raw, client.repository, number) if source == "thread" else _source(raw, client.repository, number, source)
        if item is None:
            raise GitHubError("GitHub returned an invalid PR activity item.")
        items.append({**item, "action": "reconciled"})
    return {"items": items,
            "next_cursor": _cursor(client.repository, number, source, page + 1, next_after) if has_next else None}


def _pull_discovery_cursor(repository: str, since: float, page: int) -> str:
    payload = {"repository": repository.lower(), "source": "updated_pulls", "since": since, "page": page}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _discovery_page(cursor: str | None, repository: str, since: float) -> int:
    if cursor is None:
        return 1
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError
        raw = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(raw, dict) or raw.get("repository") != repository.lower()
                or raw.get("source") != "updated_pulls" or isinstance(raw.get("since"), bool)
                or raw.get("since") != since or not _number(raw.get("page"))):
            raise ValueError
        return raw["page"]
    except (ValueError, TypeError, UnicodeError):
        raise GitHubError("Invalid updated-pull-request discovery cursor.") from None


async def list_updated_pulls(client, since: float, cursor: str | None = None) -> dict:
    """Discover closed/merged PRs missed by open-item reconciliation.

    The service persists the cursor after each bounded page and repeats a scan
    from its saved watermark. No lifetime page ceiling can strand a busy PR or
    repository; API failures remain visible instead of claiming a complete scan.
    """
    try:
        if isinstance(since, bool) or not isinstance(since, (int, float)) or not math.isfinite(since) or since < 0:
            raise ValueError
        since = float(since)
        datetime.fromtimestamp(since, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise GitHubError("Pull-request discovery requires a valid cutoff timestamp.") from None
    page = _discovery_page(cursor, client.repository, since)
    rows, headers = await client._call(f"/repos/{client.repository}/pulls", params={
        "state": "all", "sort": "updated", "direction": "desc", "per_page": 100, "page": page})
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise GitHubError("GitHub returned an invalid updated pull-request list.")
    items, previous, older = [], float("inf"), False
    for raw in rows:
        item = _item(raw, client.repository, "pull")
        updated = _timestamp(raw.get("updated_at"))
        if item is None or not updated or ("id" in raw and not _number(raw["id"])):
            raise GitHubError("GitHub returned an invalid updated pull request.")
        timestamp = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
        if timestamp > previous:
            raise GitHubError("GitHub returned pull requests outside the requested update order.")
        previous = timestamp
        if timestamp < since:
            older = True
        else:
            items.append({**item, "updated_at": updated})
    link = next((value for key, value in headers.items() if key.lower() == "link"), "")
    has_next = bool(re.search(r'rel\s*=\s*"next"', link))
    return {"items": items, "next_cursor": _pull_discovery_cursor(client.repository, since, page + 1)
            if has_next and not older else None}


async def setup_checks(client) -> list[dict[str, str]]:
    checks = []

    def add(key: str, label: str, status: str, detail: str) -> None:
        checks.append({"key": "github_" + key, "label": label, "status": status, "detail": detail})

    secret_env = client._config.get("github_webhook_secret_env", "PROJECT_GITHUB_WEBHOOK_SECRET")
    secret_present = isinstance(secret_env, str) and bool(_ENV_NAME.fullmatch(secret_env)) and bool(os.getenv(secret_env, "").strip())
    add("webhook_secret", "Webhook secret", "passed" if secret_present else "attention",
        "A webhook secret is present in the service environment." if secret_present else
        "Set the configured webhook-secret environment variable on the service.")
    try:
        app_token = client._app_jwt()
    except GitHubError:
        add("credentials", "GitHub App credentials", "attention",
            "Check the App ID and private-key environment configuration.")
        for key, label in (("repository", "Repository access"), ("permissions", "App permissions"),
                           ("subscriptions", "Webhook subscriptions")):
            add(key, label, "unverified", "Configure App credentials, then run this check again.")
        return checks
    add("credentials", "GitHub App credentials", "passed", "The configured private key can sign an App token.")
    installation = client._config.get("github_installation_id")
    if (isinstance(installation, bool) or not isinstance(installation, (str, int))
            or not re.fullmatch(r"[1-9][0-9]{0,19}", str(installation))):
        add("installation", "App installation", "attention", "Configure a valid GitHub App installation ID.")
        for key, label in (("repository", "Repository access"), ("permissions", "App permissions"),
                           ("subscriptions", "Webhook subscriptions")):
            add(key, label, "unverified", "Configure the installation ID, then run this check again.")
        return checks
    installed = None
    try:
        installed, _ = await client._http("GET", f"/app/installations/{installation}", app_token)
        if (not isinstance(installed, dict) or str(installed.get("id")) != str(installation)
                or str(installed.get("app_id")) != str(client._config.get("github_app_id"))):
            raise GitHubError("Invalid App installation.")
    except GitHubError as exc:
        installed = None
        status = "attention" if exc.status in {401, 403, 404} else "unverified"
        add("installation", "App installation", status,
            "Check the installation ID and App credentials, then retry." if status == "attention" else
            "GitHub could not verify the installation. Retry when API access is available.")
    if installed is not None:
        suspended = bool(installed.get("suspended_at"))
        add("installation", "App installation", "attention" if suspended else "passed",
            "The App installation is suspended; restore it in GitHub." if suspended else "The App installation is active.")
    try:
        repository, _ = await client._call(f"/repos/{client.repository}")
        if (not isinstance(repository, dict) or not isinstance(repository.get("full_name"), str)
                or repository["full_name"].lower() != client.repository.lower()):
            raise GitHubError("Invalid repository access response.")
    except GitHubError as exc:
        add("repository", "Repository access", "attention" if exc.status in {401, 403, 404, 422} else "unverified",
            "The installation could not read this repository. Check repository selection and access, then retry.")
    else:
        add("repository", "Repository access", "passed", "The installation can read the configured repository.")
    permissions = installed.get("permissions") if installed is not None else None
    if not isinstance(permissions, dict):
        add("permissions", "App permissions", "unverified", "GitHub did not provide installation permissions. Inspect the App installation.")
    else:
        required = {"metadata": "read", "issues": "write", "pull_requests": "read", "contents": "read", "actions": "read"}
        missing = [f"{key.replace('_', ' ')}: {level}" for key, level in required.items()
                   if not isinstance(permissions.get(key), str)
                   or permissions[key] not in ({"read", "write"} if level == "read" else {"write"})]
        add("permissions", "App permissions", "attention" if missing else "passed",
            "Grant these repository permissions: " + "; ".join(missing) + "." if missing else
            "The installation has the required repository permissions.")
    events = installed.get("events") if installed is not None else None
    if not isinstance(events, list) or any(not isinstance(event, str) for event in events):
        add("subscriptions", "Webhook subscriptions", "unverified",
            "GitHub did not provide installation subscriptions. Inspect the App webhook settings.")
    else:
        required_events = {"issues", "issue_comment", "pull_request", "pull_request_review", "pull_request_review_comment",
                           "pull_request_review_thread", "push", "release", "workflow_run"}
        missing = sorted(required_events - set(events))
        add("subscriptions", "Webhook subscriptions", "attention" if missing else "passed",
            "Subscribe the App to: " + ", ".join(missing) + "." if missing else
            "All required event subscriptions are present. Delivery reachability still needs a received webhook.")
    return checks
