"""Repository-scoped GitHub App access and webhook normalization.

The caller owns durable publication state. Save an in-flight operation before a
write, and recover interrupted operations by marker instead of issuing another
POST. This module never retries a potentially successful write.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import jwt


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
MARKER_PREFIX = "<!-- discord-pals-project:"
MAX_BODY = 12000
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
_MARKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_RESERVED_COMMENT = re.compile(r"<!-- discord-pals-project:[A-Za-z0-9][A-Za-z0-9:_.-]{0,127} -->")


class GitHubError(Exception):
    """A sanitized API/configuration error, safe for an operator status view."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class GitHubAmbiguousWrite(GitHubError):
    """A write may have succeeded. Reconcile or request human recovery."""

    def __init__(self, operation: str, marker: str):
        super().__init__("GitHub publication could not be confirmed; recovery is required.")
        self.operation = operation
        self.marker = marker


def validate_repository(repository: Any) -> str:
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise GitHubError("Configure a GitHub repository as owner/repository.")
    if repository.split("/", 1)[1] in {".", ".."}:
        raise GitHubError("Configure a GitHub repository as owner/repository.")
    return repository


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and 0 < value < 2**63:
        return value
    return 0


def _text(value: Any, limit: int = MAX_BODY) -> str:
    if not isinstance(value, str):
        return ""
    # Keep report content as text, including character voice, but do not relay
    # active GitHub or Discord mentions. Discord sends should also disable them.
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    # User-authored text cannot mint a second recovery marker through our App.
    # Our own marker is added only after preview/public-text normalization.
    value = _RESERVED_COMMENT.sub("", value)
    value = value.replace("<#", "<\u200b#")
    return re.sub(r"@(?!\u200b)", "@\u200b", value)[:limit]


def prepare_public_text(value: Any, limit: int) -> str:
    """Apply the same idempotent normalization before preview and publication."""
    return _text(value, limit).strip()


def _repo_url(value: Any, repository: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https" and parts.netloc.lower() == "github.com"
            and parts.path.lower().startswith(f"/{repository.lower()}/")
            and not parts.query and not any(ord(c) < 33 for c in value)
        )
        return value if valid else ""
    except ValueError:
        return ""


def _item(raw: Any, repository: str, kind: str = "issue") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    number = _number(raw.get("number"))
    state = raw.get("state")
    title = _text(raw.get("title"), 256).strip()
    url = _repo_url(raw.get("html_url"), repository)
    expected_path = f"/{repository}/{'pull' if kind == 'pull' else 'issues'}/{number}"
    if (not number or state not in ("open", "closed") or not title or not url
            or urlsplit(url).path.lower() != expected_path.lower()):
        return None
    labels = raw.get("labels") if isinstance(raw.get("labels"), list) else []
    labels = [_text(label.get("name") if isinstance(label, dict) else label, 80)
              for label in labels[:30]]
    merged_at = raw.get("merged_at")
    merged = kind == "pull" and (raw.get("merged") is True
                                 or (isinstance(merged_at, str) and bool(merged_at)))
    return {
        "kind": kind, "number": number, "title": title,
        "body": _text(raw.get("body")), "state": state, "html_url": url,
        "closed": state == "closed", "merged": merged,
        "draft": kind == "pull" and raw.get("draft") is True,
        "state_reason": _text(raw.get("state_reason"), 40),
        "labels": [label for label in labels if label],
        "updated_at": _text(raw.get("updated_at"), 40),
    }


def marker_comment(marker: str) -> str:
    if not isinstance(marker, str) or not _MARKER.fullmatch(marker):
        raise GitHubError("Invalid publication recovery marker.")
    return f"{MARKER_PREFIX}{marker} -->"


def validate_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate the exact, unparsed request bytes using X-Hub-Signature-256."""
    if (not isinstance(body, bytes) or not isinstance(secret, str) or not secret
            or not isinstance(signature, str)
            or not re.fullmatch(r"sha256=[a-f0-9]{64}", signature)):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _sender(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("sender")
    raw = raw if isinstance(raw, dict) else {}
    return {"sender": _text(raw.get("login"), 100), "sender_id": _number(raw.get("id")),
            "sender_type": _text(raw.get("type"), 30)}


def normalize_event(event: str, payload: Any, repository: str) -> dict[str, Any] | None:
    """Return bounded display data. Call only after signature validation.

    ``key`` identifies the mirrored object. Store the webhook delivery ID
    separately to deduplicate deliveries and retain lifecycle updates.
    """
    try:
        repository = validate_repository(repository)
    except GitHubError:
        return None
    if not isinstance(payload, dict) or not isinstance(event, str):
        return None
    repo = payload.get("repository")
    if not isinstance(repo, dict) or not isinstance(repo.get("full_name"), str):
        return None
    if repo["full_name"].lower() != repository.lower():
        return None
    action = payload.get("action", "")
    if not isinstance(action, str):
        return None
    action = _text(action, 60)
    base = {"event": event, "action": _text(action, 60), "repository": repository,
            **_sender(payload)}
    if event in {"issues", "pull_request"}:
        kind = "issue" if event == "issues" else "pull"
        raw = payload.get("issue" if kind == "issue" else "pull_request")
        if kind == "issue" and isinstance(raw, dict) and "pull_request" in raw:
            return None
        item = _item(raw, repository, kind)
        if item is None or not action:
            return None
        if action == "deleted":
            item.update(state="deleted", closed=True)
        return {**base, **item, "key": f"{kind}:{item['number']}", "url": item["html_url"]}
    if event == "issue_comment":
        comment = payload.get("comment")
        issue = payload.get("issue")
        if (action != "created" or not isinstance(comment, dict) or not isinstance(issue, dict)
                or "pull_request" in issue or base["sender_type"] == "Bot"
                or comment.get("author_association") not in ("OWNER", "MEMBER", "COLLABORATOR")):
            return None
        author = comment.get("user")
        if not isinstance(author, dict) or author.get("type") == "Bot":
            return None
        body = comment.get("body")
        if not isinstance(body, str) or MARKER_PREFIX in body:
            return None
        match = re.fullmatch(r"\s*/ask-reporter(?:[ \t]+|\r?\n)(.+)", body, re.DOTALL)
        item = _item(issue, repository)
        comment_id = _number(comment.get("id"))
        url = _repo_url(comment.get("html_url"), repository)
        if not match or not match.group(1).strip() or item is None or not comment_id or not url:
            return None
        if (urlsplit(url).path.lower() != f"/{repository}/issues/{item['number']}".lower()
                or urlsplit(url).fragment != f"issuecomment-{comment_id}"):
            return None
        return {**base, "kind": "comment", "key": f"comment:{comment_id}",
                "number": item["number"], "comment_id": comment_id,
                "title": item["title"], "body": _text(match.group(1).strip()),
                "url": url, "html_url": url, "state": item["state"],
                "closed": item["closed"], "merged": False,
                "author_association": comment["author_association"]}
    if event == "push":
        ref, before, after = payload.get("ref"), payload.get("before"), payload.get("after")
        if (not isinstance(ref, str) or not ref.startswith(("refs/heads/", "refs/tags/"))
                or len(ref) > 512 or not isinstance(before, str) or not isinstance(after, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", before)
                or not re.fullmatch(r"[0-9a-f]{40,64}", after)):
            return None
        commits = payload.get("commits")
        if not isinstance(commits, list):
            return None
        summaries = []
        for commit in commits[:20]:
            if not isinstance(commit, dict):
                continue
            sha = commit.get("id")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
                continue
            message = _text(commit.get("message"), 200).split("\n", 1)[0]
            summaries.append({"sha": sha, "message": message,
                              "url": f"https://github.com/{repository}/commit/{sha}"})
        url = _repo_url(payload.get("compare"), repository) or f"https://github.com/{repository}/commits"
        branch = _text(ref.split("/", 2)[2], 200)
        verb = "Deleted" if payload.get("deleted") is True else "Pushed to"
        key = hashlib.sha256(f"{ref}:{before}:{after}".encode()).hexdigest()[:24]
        return {**base, "kind": "commit", "key": f"push:{key}",
                "title": f"{verb} {branch}", "body": "\n".join(
                    f"{c['sha'][:7]} {c['message']}" for c in summaries),
                "url": url, "html_url": url, "number": 0, "state": "pushed",
                "closed": False, "merged": False, "ref": _text(ref, 512),
                "commits": summaries, "commit_count": len(commits),
                "forced": payload.get("forced") is True, "deleted": payload.get("deleted") is True}
    if event == "release":
        release = payload.get("release")
        if not isinstance(release, dict) or release.get("draft") is True:
            return None
        identifier = _number(release.get("id"))
        tag = _text(release.get("tag_name"), 160)
        url = _repo_url(release.get("html_url"), repository)
        if not identifier or not tag or not url or not action:
            return None
        title = _text(release.get("name"), 200) or tag
        return {**base, "kind": "general", "key": f"release:{identifier}",
                "title": f"Release {action}: {title}"[:256], "body": _text(release.get("body")),
                "url": url, "html_url": url, "number": 0, "state": action,
                "closed": False, "merged": False, "tag": tag,
                "prerelease": release.get("prerelease") is True}
    if event == "workflow_run":
        run = payload.get("workflow_run")
        if not isinstance(run, dict):
            return None
        identifier = _number(run.get("id"))
        url = _repo_url(run.get("html_url"), repository)
        status = _text(run.get("conclusion") or run.get("status"), 60)
        name = _text(run.get("name"), 160)
        if not identifier or not url or not status or not name:
            return None
        return {**base, "kind": "general", "key": f"workflow_run:{identifier}",
                "title": f"{name}: {status}"[:256], "body": _text(run.get("display_title"), 500),
                "url": url, "html_url": url, "number": 0, "state": status,
                "closed": run.get("status") == "completed", "merged": False,
                "branch": _text(run.get("head_branch"), 200)}
    return None


class GitHubClient:
    """Small async client restricted to one configured repository on github.com."""

    def __init__(self, config: dict[str, Any], session: Any = None):
        self.repository = validate_repository(config.get("repository"))
        self._config = dict(config)
        self._session = session
        self._owns_session = session is None
        self._token = ""
        self._token_expiry = 0.0
        self._auth_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._ambiguous: set[tuple[str, str]] = set()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
        self._token = ""
        self._token_expiry = 0.0

    def _app_jwt(self) -> str:
        app_id = self._config.get("github_app_id")
        if (not isinstance(app_id, (str, int)) or isinstance(app_id, bool)
                or not str(app_id).isdigit() or int(app_id) <= 0):
            raise GitHubError("Configure the GitHub App ID.")
        key_env = self._config.get("github_private_key_env", "PROJECT_GITHUB_PRIVATE_KEY")
        file_env = self._config.get("github_private_key_file_env", "PROJECT_GITHUB_PRIVATE_KEY_FILE")
        if not all(isinstance(v, str) and _ENV_NAME.fullmatch(v) for v in (key_env, file_env)):
            raise GitHubError("Invalid GitHub private-key environment variable name.")
        key = os.environ.get(key_env, "")
        if not key:
            filename = os.environ.get(file_env, "")
            if filename:
                try:
                    with Path(filename).open("r", encoding="utf-8") as key_file:
                        key = key_file.read(32769)
                except (OSError, ValueError, UnicodeError):
                    raise GitHubError("The GitHub App private key could not be read.") from None
        if not key or len(key) > 32768:
            raise GitHubError("Configure a GitHub App private key through the environment.")
        now = int(time.time())
        try:
            return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
                              key.replace("\\n", "\n"), algorithm="RS256")
        except Exception:
            raise GitHubError("The GitHub App private key could not sign an authentication token.") from None

    async def _http(self, method: str, path: str, token: str, *, params=None, data=None,
                    mutation: bool = False) -> tuple[Any, dict[str, str]]:
        # No caller-controlled hosts, redirects, or URLs from pagination headers.
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise GitHubError("Invalid GitHub API path.")
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                   "X-GitHub-Api-Version": API_VERSION, "User-Agent": "discord-pals-project-automation"}
        try:
            async with self._session.request(method, API_ROOT + path, headers=headers,
                                             params=params, json=data, allow_redirects=False) as response:
                status = response.status
                if not 200 <= status < 300:
                    if mutation and (status >= 500 or status == 408):
                        raise GitHubAmbiguousWrite("request", "")
                    raise GitHubError(f"GitHub returned HTTP {status}.", status=status,
                                      retryable=status in {403, 429} or status >= 500)
                if response.content_length and response.content_length > MAX_RESPONSE_BYTES:
                    raise ValueError("Response too large")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("Response too large")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                return result, dict(response.headers)
        except GitHubError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            if mutation:
                raise GitHubAmbiguousWrite("request", "") from None
            raise GitHubError("GitHub could not be reached or returned an invalid response.",
                              retryable=True) from None

    async def _installation_token(self) -> str:
        async with self._auth_lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            installation = self._config.get("github_installation_id")
            if (not isinstance(installation, (str, int)) or isinstance(installation, bool)
                    or not str(installation).isdigit() or int(installation) <= 0):
                raise GitHubError("Configure the GitHub App installation ID.")
            result, _ = await self._http(
                "POST", f"/app/installations/{installation}/access_tokens", self._app_jwt(),
                data={"repositories": [self.repository.split("/", 1)[1]]})
            try:
                token = result["token"]
                expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
                if (not isinstance(token, str) or not token or len(token) > 16384
                        or any(c.isspace() for c in token) or expiry.tzinfo is None
                        or expiry.timestamp() <= time.time() + 60):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise GitHubError("GitHub returned an invalid installation token.") from None
            self._token, self._token_expiry = token, expiry.timestamp()
            return token

    async def _call(self, path: str, *, method="GET", params=None, data=None):
        for attempt in range(2):
            token = await self._installation_token()
            try:
                return await self._http(method, path, token, params=params, data=data,
                                        mutation=method == "POST")
            except GitHubError as exc:
                if exc.status != 401 or attempt:
                    raise
                self._token, self._token_expiry = "", 0.0

    async def _pages(self, path: str, params: dict[str, Any]):
        for page in range(1, MAX_PAGES + 1):
            rows, headers = await self._call(path, params={**params, "per_page": 100, "page": page})
            if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
                raise GitHubError("GitHub returned an invalid item list.")
            yield rows
            link = next((v for k, v in headers.items() if k.lower() == "link"), "")
            if not re.search(r'rel\s*=\s*"next"', link):
                return
        raise GitHubError("GitHub reconciliation exceeded its page limit; operator review is required.")

    async def search_issues(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise GitHubError("Issue search requires text.")
        words = re.findall(r"[\w-]+", query[:400])[:20]
        if not words:
            return []
        if not isinstance(limit, int) or isinstance(limit, bool):
            limit = 5
        limit = min(max(limit, 1), 20)
        # Quote every term so user/model text cannot broaden the repository scope.
        terms = " ".join(f'"{word}"' for word in words)
        result, _ = await self._call("/search/issues", params={
            "q": f"repo:{self.repository} is:issue {terms}", "per_page": limit})
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise GitHubError("GitHub returned invalid search results.")
        found = []
        for raw in result["items"][:limit]:
            if not isinstance(raw, dict) or "pull_request" in raw:
                continue
            item = _item(raw, self.repository)
            if item is not None:
                found.append(item)
        return found

    async def get_issue(self, number: int) -> dict[str, Any]:
        if not _number(number):
            raise GitHubError("Invalid GitHub issue number.")
        raw, _ = await self._call(f"/repos/{self.repository}/issues/{number}")
        kind = "issue"
        if isinstance(raw, dict) and "pull_request" in raw:
            kind = "pull"
            raw, _ = await self._call(f"/repos/{self.repository}/pulls/{number}")
        item = _item(raw, self.repository, kind)
        if item is None or item["number"] != number:
            raise GitHubError("GitHub returned an invalid issue.")
        return item

    async def list_open_items(self, kind: str = "issue") -> list[dict[str, Any]]:
        if kind not in {"issue", "pull"}:
            raise GitHubError("Choose issue or pull for GitHub reconciliation.")
        endpoint = "issues" if kind == "issue" else "pulls"
        result = []
        async for rows in self._pages(f"/repos/{self.repository}/{endpoint}", {"state": "open"}):
            for raw in rows:
                if kind == "issue" and isinstance(raw, dict) and "pull_request" in raw:
                    continue
                item = _item(raw, self.repository, kind)
                if item is not None:
                    result.append(item)
        return result

    async def find_issue_by_marker(self, marker: str) -> dict[str, Any] | None:
        needle = marker_comment(marker)
        async for rows in self._pages(f"/repos/{self.repository}/issues", {
                "state": "all", "sort": "created", "direction": "desc"}):
            for raw in rows:
                kind = "pull" if "pull_request" in raw else "issue"
                item = _item(raw, self.repository, kind)
                if item is None:
                    raise GitHubError("GitHub returned an invalid item during publication recovery.")
                if (kind == "issue" and isinstance(raw.get("body"), str) and needle in raw["body"]
                        and self._authored_by_app(raw)):
                    return item
        return None

    async def find_comment_by_marker(self, number: int, marker: str) -> dict[str, Any] | None:
        if not _number(number):
            raise GitHubError("Invalid GitHub issue number.")
        needle = marker_comment(marker)
        async for rows in self._pages(f"/repos/{self.repository}/issues/{number}/comments", {}):
            for raw in rows:
                item = self._comment(raw, number)
                if item is None:
                    raise GitHubError("GitHub returned an invalid comment during publication recovery.")
                if (isinstance(raw.get("body"), str) and needle in raw["body"]
                        and self._authored_by_app(raw)):
                    return item
        return None

    def _authored_by_app(self, raw: dict[str, Any]) -> bool:
        # This server-owned field exists on GitHub's issue and issue-comment
        # schemas. A copied marker in a contributor's text is not our delivery.
        app = raw.get("performed_via_github_app")
        configured = self._config.get("github_app_id")
        if (not isinstance(app, dict) or isinstance(configured, bool)
                or not re.fullmatch(r"[0-9]{1,20}", str(configured))):
            return False
        return bool(_number(app.get("id"))) and _number(app.get("id")) == int(configured)

    def _comment(self, raw: Any, number: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or not _number(raw.get("id")):
            return None
        url = _repo_url(raw.get("html_url"), self.repository)
        if (not url or urlsplit(url).path.lower() != f"/{self.repository}/issues/{number}".lower()
                or urlsplit(url).fragment != f"issuecomment-{raw['id']}"):
            return None
        return {"kind": "comment", "id": raw["id"], "number": number,
                "body": _text(raw.get("body")), "html_url": url}

    async def _publish(self, operation: str, marker: str, path: str, data: dict[str, str],
                       find_existing, normalize) -> dict[str, Any]:
        async with self._write_lock:
            existing = await find_existing()
            if existing is not None:
                self._ambiguous.discard((operation, marker))
                return existing
            if (operation, marker) in self._ambiguous:
                raise GitHubAmbiguousWrite(operation, marker)
            try:
                raw, _ = await self._call(path, method="POST", data=data)
                result = normalize(raw)
                if result is None:
                    raise GitHubAmbiguousWrite(operation, marker)
                return result
            except asyncio.CancelledError:
                # The caller's durable inflight record survives shutdown. Do not
                # extend cancellation with a potentially long pagination scan.
                self._ambiguous.add((operation, marker))
                raise
            except GitHubAmbiguousWrite:
                self._ambiguous.add((operation, marker))
                try:
                    existing = await find_existing()
                    if existing is not None:
                        self._ambiguous.discard((operation, marker))
                        return existing
                except GitHubError:
                    pass
                raise GitHubAmbiguousWrite(operation, marker) from None

    async def create_issue(self, title: str, body: str, marker: str) -> dict[str, Any]:
        if (not isinstance(title, str) or not isinstance(body, str)
                or len(title) > 256 or len(body) > 60000):
            raise GitHubError("An issue needs a title up to 256 characters and body up to 60000 characters.")
        title, body = prepare_public_text(title, 256), prepare_public_text(body, 60000)
        if not title or not body:
            raise GitHubError("An issue needs a title and body.")
        stamp = marker_comment(marker)
        return await self._publish("create_issue", marker, f"/repos/{self.repository}/issues",
                                   {"title": title, "body": f"{body}\n\n{stamp}"},
                                   lambda: self.find_issue_by_marker(marker),
                                   lambda raw: _item(raw, self.repository)
                                   if isinstance(raw, dict) and isinstance(raw.get("body"), str)
                                   and stamp in raw["body"] else None)

    async def add_comment(self, number: int, body: str, marker: str) -> dict[str, Any]:
        if not _number(number):
            raise GitHubError("Invalid GitHub issue number.")
        if not isinstance(body, str) or len(body) > 60000:
            raise GitHubError("A GitHub comment must be text up to 60000 characters.")
        body = prepare_public_text(body, 60000)
        if not body:
            raise GitHubError("A GitHub comment needs a body.")
        stamp = marker_comment(marker)
        return await self._publish(f"add_comment:{number}", marker,
                                   f"/repos/{self.repository}/issues/{number}/comments",
                                   {"body": f"{body}\n\n{stamp}"},
                                   lambda: self.find_comment_by_marker(number, marker),
                                   lambda raw: self._comment(raw, number)
                                   if isinstance(raw, dict) and isinstance(raw.get("body"), str)
                                   and stamp in raw["body"] else None)
