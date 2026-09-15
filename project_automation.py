"""Durable human-gated feedback workflow and bounded background processing."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from project_automation_ai import FeedbackAI, search_terms
from project_automation_github import GitHubClient, GitHubAmbiguousWrite, GitHubError, prepare_public_text
from project_automation_store import AutomationStore, Conflict


CHOICES = {
    "investigate": "Answer a question", "draft": "Draft now", "human": "Ask a maintainer",
    "link": "Use existing issue", "submit": "Publish this draft", "edit": "Edit draft",
    "back": "Choose another path", "cancel": "Close feedback",
}


def gate(kind: str, actions: list[str]) -> dict:
    return {"kind": kind, "options": [{"key": action, "label": CHOICES[action]} for action in actions]}


class WorkflowError(ValueError):
    """An action is not currently available to this actor."""


class AutomationService:
    def __init__(self, store, *, settings=None, ai=None, github_factory=None, transport=None):
        self.store = store
        self._settings = settings
        self.ai = ai or FeedbackAI()
        self.github_factory = github_factory or GitHubClient
        self.transport = transport
        self.bots = {}
        self._worker = None
        self._github = None
        self._github_config = None
        self._last_sync = 0.0
        self._last_error = None

    def settings(self) -> dict:
        if self._settings:
            return self._settings()
        import runtime_config
        return runtime_config.get("project_automation")

    def paused(self) -> bool:
        cfg = self.settings()
        if not cfg.get("enabled"):
            return True
        if self._settings is None:
            from project_automation_config import config_errors, credential_status
            if config_errors(cfg) or not all(credential_status(cfg).values()):
                return True
        import runtime_config
        return bool(runtime_config.get("global_paused", False))

    def status(self) -> dict:
        cfg = self.settings()
        return {"enabled": cfg.get("enabled", False), "paused": self.paused(),
                "bot_online": cfg.get("bot_name") in self.bots,
                "worker_running": bool(self._worker and not self._worker.done()),
                "last_error": self._last_error,
                "repository": cfg.get("repository", "")}

    def list_cases(self, limit=100):
        return self.store.list_cases(limit=limit)

    def get_case(self, case_id):
        return self.store.get_case(case_id)

    def list_jobs(self, limit=100):
        return self.store.list_jobs(limit=limit)

    def _bound(self, case: dict) -> bool:
        cfg = self.settings()
        return all(str(case.get(key, "")) == str(cfg.get(key, ""))
                   for key in ("repository", "bot_name", "guild_id", "feedback_channel_id"))

    def is_maintainer(self, user_id, role_ids=()) -> bool:
        cfg = self.settings()
        import runtime_config
        users = cfg.get("maintainer_user_ids", []) + runtime_config.get("operator_user_ids", [])
        return str(user_id) in users or bool(set(map(str, role_ids)) & set(cfg.get("maintainer_role_ids", [])))

    def _authorize(self, case, user_id, role_ids=()):
        if self.paused() or not self._bound(case):
            raise WorkflowError("Feedback is paused or its project configuration has changed.")
        if str(user_id) != case["reporter_id"] and not self.is_maintainer(user_id, role_ids):
            raise WorkflowError("Only the reporter or a configured maintainer can choose for this report.")
        self._check_case_access(case)

    def _check_case_access(self, case):
        import runtime_config
        if not runtime_config.is_server_response_allowed(case["feedback_channel_id"])[0]:
            raise WorkflowError("Response access settings block this feedback channel.")

    @staticmethod
    def _job(kind, case, **payload):
        return {"kind": kind, "payload": {"case_id": case["id"], **payload},
                "key": f"{kind}:{case['id']}:{case['revision'] + 1}"}

    def _save(self, case, patch, notify=True):
        return self.store.update_case(case["id"], patch, expected_revision=case["revision"],
                                      job=self._job("notify", case) if notify else None)

    def check_new_report(self, reporter_id, content):
        if self.paused():
            raise WorkflowError("Project feedback is paused.")
        if not isinstance(content, str) or not content.strip() or len(content) > 8000:
            raise WorkflowError("Please describe the feedback in 1–8,000 characters.")
        if self.store.count_active_cases(str(reporter_id), self.settings()["guild_id"]) >= 3:
            raise WorkflowError("You already have three open reports. Finish or close one before starting another.")

    def receive_report(self, *, source_key, channel_id, reporter_id, content, source_url, message_id) -> dict:
        cfg = self.settings()
        existing = self.store.get_case_by_source(source_key)
        if existing:
            if existing["revision"] == 1 and existing["state"] == "assessing":
                self.store.enqueue("assess", {"case_id": existing["id"], "revision": 1}, key=f"initial:{existing['id']}")
            return existing
        self.check_new_report(reporter_id, content)
        case = self.store.create_case({
            **{key: cfg[key] for key in ("repository", "bot_name", "guild_id", "feedback_channel_id")},
            "channel_id": str(channel_id), "reporter_id": str(reporter_id),
            "state": "assessing", "source_url": source_url, "question_count": 0,
            "transcript": [{"author_id": str(reporter_id), "content": content,
                            "message_id": str(message_id), "source_url": source_url}],
        }, source_key=source_key)
        self.store.enqueue("assess", {"case_id": case["id"], "revision": case["revision"]},
                           key=f"initial:{case['id']}")
        return case

    def add_detail(self, case_id, *, user_id, content, message_id, source_url, role_ids=()) -> dict:
        case = self.get_case(case_id)
        if not case:
            raise WorkflowError("Feedback was not found.")
        self._authorize(case, user_id, role_ids)
        if case["state"] in {"processing", "queued", "publishing", "recovery", "closed"}:
            raise WorkflowError("This report is busy or closed. Its current status is shown above.")
        transcript = case.get("transcript", [])
        if any(item.get("message_id") == str(message_id) for item in transcript):
            return case
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 8000:
            raise WorkflowError("Please keep this update within 8,000 characters.")
        transcript = transcript + [{"author_id": str(user_id), "content": content,
                                    "message_id": str(message_id), "source_url": source_url}]
        if len(transcript) > 20:
            transcript = transcript[:1] + transcript[-19:]
        while sum(len(entry["content"]) for entry in transcript) > 24000 and len(transcript) > 2:
            del transcript[1]
        question = self.store.get_link("maintainer_question", case_id) or {}
        return self.store.update_case(case_id, {"transcript": transcript, "state": "assessing", "gate": None,
                                               "answering_question_key": question.get("key")},
                                      expected_revision=case["revision"],
                                      job=self._job("assess", case, revision=case["revision"] + 1))

    def choose(self, case_id, revision, action, *, user_id, role_ids=()) -> dict:
        case = self.get_case(case_id)
        if not case:
            raise WorkflowError("Feedback was not found.")
        self._authorize(case, user_id, role_ids)
        if action == "submit" and (not case.get("draft") or (case.get("gate") or {}).get("kind") != "review"):
            raise WorkflowError("Review the current draft before publishing.")
        # The decision and job are one transaction: double-clicks cannot publish twice.
        return self.store.consume_gate(case_id, revision, action, str(user_id),
                                       job=self._job("branch", case, action=action, revision=revision + 1))

    async def client(self):
        cfg = self.settings()
        keys = ("repository", "github_app_id", "github_installation_id", "github_private_key_env", "github_private_key_file_env")
        current = {key: cfg.get(key) for key in keys}
        if self._github is None or current != self._github_config:
            if self._github:
                await self._github.close()
            self._github = self.github_factory(cfg)
            self._github_config = current
        return self._github

    async def attach(self, bot):
        self.bots[bot.name] = bot
        if self.transport is None:
            from project_automation_discord import DiscordTransport
            self.transport = DiscordTransport(self)
        await self.transport.attach(bot)
        for case in self.store.list_pending_cases():
            if case["revision"] == 1 and case["state"] == "assessing":
                self.store.enqueue("assess", {"case_id": case["id"], "revision": 1}, key=f"initial:{case['id']}")
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="project-automation")

    async def detach(self, bot):
        self.bots.pop(bot.name, None)
        if not self.bots:
            if self._worker:
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
                self._worker = None
            if self._github:
                await self._github.close()
                self._github = None

    async def handle_message(self, bot, message) -> bool:
        if self.transport is None:
            return False
        return await self.transport.handle_message(bot, message)

    async def _assess(self, case, job):
        if case["revision"] != job["payload"].get("revision", case["revision"]) or case["state"] != "assessing":
            return
        candidates = []
        search_unavailable = False
        try:
            client = await self.client()
            if case.get("linked_issue_number"):
                candidates = [await client.get_issue(case["linked_issue_number"])]
            else:
                query = search_terms(case["transcript"][0]["content"])
                if query:
                    candidates = await client.search_issues(query, limit=5)
        except GitHubError:
            search_unavailable = True
        candidates = [{**item, "body": item.get("body", "")[:2000]} for item in candidates[:5]]
        context = {**case, "maintainer_question": self.store.get_link("maintainer_question", case["id"])}
        result = await self.ai.assess(context, candidates, self.bots[case["bot_name"]], self.settings())
        actions = ["draft", "human", "cancel"]
        if case.get("question_count", 0) < self.settings()["max_questions"]:
            actions.insert(0, "investigate")
        if result.get("duplicate_number") and not case.get("linked_issue_number"):
            actions.insert(2, "link")
        self._save(case, {"assessment": result, "candidates": candidates,
                          "assessment_question_key": case.get("answering_question_key"),
                          "search_unavailable": search_unavailable,
                          "state": "awaiting_choice", "gate": gate("direction", actions)})

    async def _branch(self, case, action):
        assessment = case.get("assessment", {})
        if action == "investigate":
            question = assessment["question"]
            self._save(case, {"state": "awaiting_details", "notice": question,
                              "question_count": case.get("question_count", 0) + 1,
                              "asked_questions": case.get("asked_questions", []) + [question],
                              "gate": gate("direction", ["draft", "human", "cancel"])})
        elif action in {"draft", "link"}:
            number = case.get("linked_issue_number")
            if action == "link":
                number = assessment["duplicate_number"]
                if number not in {item["number"] for item in case.get("candidates", [])}:
                    raise WorkflowError("The suggested issue is no longer available.")
            body = prepare_public_text(assessment["body"] + f"\n\nSource: {case['source_url']}", 3800)
            draft = {"kind": "comment" if number else "issue", "title": prepare_public_text(assessment["title"], 180),
                     "body": body, "issue_number": number, "question_key": case.get("assessment_question_key")}
            self._save(case, {"state": "awaiting_submission", "draft": draft,
                              "gate": gate("review", ["submit", "edit", "back", "cancel"])})
        elif action == "submit":
            marker = f"{case['id']}-{case['revision']}"
            self.store.update_case(case["id"], {"state": "queued", "publication_marker": marker}, expected_revision=case["revision"],
                                   job=self._job("publish", case, draft=case["draft"],
                                                 marker=marker))
        elif action == "edit":
            self._save(case, {"state": "awaiting_details", "notice": "Tell me what to change. I’ll prepare a new preview for approval.",
                              "gate": gate("direction", ["back", "human", "cancel"])})
        elif action == "back":
            actions = ["draft", "human", "cancel"]
            if case.get("question_count", 0) < self.settings()["max_questions"]:
                actions.insert(0, "investigate")
            self._save(case, {"state": "awaiting_choice", "gate": gate("direction", actions)})
        elif action == "human":
            self._save(case, {"state": "needs_maintainer", "notice": "A maintainer can review this report here. You can still add useful details.",
                              "gate": gate("direction", ["draft", "back", "cancel"])})
        elif action == "cancel":
            self._save(case, {"state": "closed", "notice": "Feedback closed by request.", "gate": None})
        else:
            raise WorkflowError("Unknown feedback action.")

    async def _publish(self, case, job):
        if case["state"] != "queued":
            return
        draft, marker = job["payload"]["draft"], job["payload"]["marker"]
        client = await self.client()
        self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
        try:
            if draft["kind"] == "issue":
                result = await client.create_issue(draft["title"], draft["body"], marker)
            else:
                result = await client.add_comment(draft["issue_number"], draft["body"], marker)
        except (GitHubAmbiguousWrite, asyncio.CancelledError):
            self._save(case, {"state": "recovery", "notice": "GitHub may have accepted this draft. A maintainer must check its delivery before anything is sent again."})
            raise
        number = draft.get("issue_number") or result["number"]
        question = self.store.get_link("maintainer_question", case["id"]) or {}
        if draft.get("question_key") and question.get("key") == draft["question_key"]:
            self.store.put_link("maintainer_question", case["id"], {})
        self._save(case, {"state": "filed", "linked_issue_number": number,
                          "github_url": result["html_url"], "notice": "Published to GitHub. Updates will appear here.", "gate": None})

    async def recover(self, job_id, *, user_id, role_ids=()) -> bool:
        """Read-only marker lookup; a missing match never authorizes a second POST."""
        if not self.is_maintainer(user_id, role_ids) or self.paused():
            raise WorkflowError("A configured maintainer must run recovery while the helper is enabled.")
        job = self.store.get_job(job_id)
        if not job or job["state"] != "recovery":
            raise WorkflowError("That job is not awaiting delivery recovery.")
        if job["kind"] in {"notify", "event"}:
            if job["kind"] == "notify":
                case = self.get_case(job["payload"]["case_id"])
                if not self._bound(case):
                    raise WorkflowError("Restore the report's project configuration before recovery.")
                found = await self.transport.notify(case, recover_only=True)
            else:
                self._check_event_binding(job["payload"])
                found = await self.transport.mirror(job["payload"], recover_only=True)
            if found:
                self.store.resolve_recovery(job_id, outcome="retry", actor=str(user_id), note="Found existing Discord delivery; retry will edit its saved message")
            return found
        if job["kind"] != "publish":
            raise WorkflowError("This job requires operator inspection in the dashboard.")
        case = self.get_case(job["payload"]["case_id"])
        if not self._bound(case):
            raise WorkflowError("Restore the report's project configuration before recovery.")
        if case["state"] not in {"queued", "recovery"} or case.get("publication_marker") != job["payload"]["marker"]:
            raise WorkflowError("This report has progressed beyond that publication job.")
        client = await self.client()
        draft, marker = job["payload"]["draft"], job["payload"]["marker"]
        if draft["kind"] == "issue":
            result = await client.find_issue_by_marker(marker)
        else:
            result = await client.find_comment_by_marker(draft["issue_number"], marker)
        if not result:
            return False
        self._save(case, {"state": "filed", "linked_issue_number": draft.get("issue_number") or result["number"],
                          "github_url": result["html_url"], "notice": "Existing GitHub publication recovered.", "gate": None})
        self.store.resolve_recovery(job_id, outcome="complete", actor=str(user_id), note="Matched GitHub publication marker")
        return True

    async def retry(self, job_id, *, user_id, role_ids=(), confirmed_not_delivered=False):
        """A maintainer can retry failed reads or explicitly resolve an uncertain write."""
        if not self.is_maintainer(user_id, role_ids) or self.paused():
            raise WorkflowError("A configured maintainer must retry work while the helper is enabled.")
        job = self.store.get_job(job_id)
        if not job or job["state"] not in {"failed", "recovery"}:
            raise WorkflowError("That job is not held or failed.")
        case = self.get_case(job["payload"]["case_id"]) if "case_id" in job["payload"] else None
        if case and not self._bound(case):
            raise WorkflowError("Restore this report's project configuration first.")
        if job["kind"] == "event":
            self._check_event_binding(job["payload"])
        if job["state"] == "failed":
            if job["kind"] == "branch" and (case["state"] != "processing" or case["revision"] != job["payload"]["revision"]):
                raise WorkflowError("This report has progressed beyond that decision.")
            if job["kind"] == "assess" and not (
                (case["state"] == "assessing" and case["revision"] == job["payload"]["revision"])
                or (case["state"] == "needs_maintainer" and case["revision"] == job["payload"]["revision"] + 1)
            ):
                raise WorkflowError("This report has progressed beyond that assessment.")
            if job["kind"] == "assess" and case["state"] != "assessing":
                self.store.update_case(case["id"], {"state": "assessing", "gate": None},
                                       expected_revision=case["revision"], job=self._job("assess", case, revision=case["revision"] + 1))
            else:
                self.store.retry_job(job_id)
            return "Retry queued."
        if await self.recover(job_id, user_id=user_id, role_ids=role_ids):
            return "Existing delivery recovered; no new publication was created."
        if not confirmed_not_delivered:
            raise WorkflowError("No delivery was found. Check GitHub or Discord, then rerun with confirmed_not_delivered:true only if the original was not delivered.")
        if job["kind"] == "publish":
            self.store.resolve_recovery(job_id, outcome="cancel", actor=str(user_id),
                                        note="Maintainer confirmed no delivery; returned draft for fresh approval",
                                        case_update={"case_id": case["id"], "expected_revision": case["revision"],
                                                     "patch": {"state": "awaiting_submission", "draft": job["payload"]["draft"],
                                                               "gate": gate("review", ["submit", "edit", "back", "cancel"])},
                                                     "job": self._job("notify", case)})
            return "Draft returned to Discord for a fresh approval. Nothing has been published."
        self.store.resolve_recovery(job_id, outcome="retry", actor=str(user_id), note="Maintainer inspected destination and explicitly confirmed no delivery")
        return "Discord delivery retry queued after your confirmation."

    def _check_event_binding(self, event):
        from project_automation_webhook import webhook_binding
        if event["repository"].lower() != self.settings()["repository"].lower():
            raise WorkflowError("The queued event belongs to another project configuration.")
        if event.get("_binding") != webhook_binding(self.settings()):
            raise WorkflowError("The queued event belongs to an earlier channel configuration.")

    async def _event(self, event, job):
        self._check_event_binding(event)
        client = await self.client()
        if event["kind"] in {"issue", "pull"}:
            # Fetch current state so out-of-order delivery cannot reopen a closed mirror.
            try:
                item = await client.get_issue(event["number"])
                event = {**event, **item, "key": event["key"], "kind": event["kind"]}
            except GitHubError as exc:
                if not (exc.status == 404 and event.get("action") == "deleted"):
                    raise
        self._check_event_binding(event)
        if event["kind"] == "comment":
            for case in self.store.cases_for_issue(event["repository"], event["number"]):
                if self._bound(case):
                    # Preserve any current draft approval. The question is an independent status note.
                    self.store.put_link("maintainer_question", case["id"], {"key": event["key"], "body": event["body"], "url": event["html_url"]})
                    self.store.enqueue("notify", {"case_id": case["id"]}, key=f"question:{case['id']}:{event['key']}")
        else:
            self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
            await self.transport.mirror(event)
            if event["kind"] == "issue":
                for case in self.store.cases_for_issue(event["repository"], event["number"]):
                    if self._bound(case):
                        # Status is separate from draft/gate state; no pending approval is lost.
                        self.store.put_link("case_status", case["id"], {"state": event["state"], "url": event["html_url"]})
                        self.store.enqueue("notify", {"case_id": case["id"]}, key=f"status:{case['id']}:{event['state']}:{event.get('updated_at', time.time())}")

    async def process_job(self, job):
        payload = job["payload"]
        case = self.get_case(payload["case_id"]) if "case_id" in payload else None
        if case and not self._bound(case):
            raise WorkflowError("Project settings changed; this case is held for its original configuration.")
        if case:
            self._check_case_access(case)
        if job["kind"] == "assess":
            await self._assess(case, job)
        elif job["kind"] == "branch":
            if case["state"] == "processing" and case["revision"] == payload["revision"]:
                await self._branch(case, payload["action"])
        elif job["kind"] == "publish":
            await self._publish(case, job)
        elif job["kind"] == "notify":
            self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
            await self.transport.notify(case)
        elif job["kind"] == "event":
            await self._event(payload, job)
        elif job["kind"] == "sync":
            client = await self.client()
            seen = set()
            for kind in ("issue", "pull"):
                for item in await client.list_open_items(kind):
                    seen.add((kind, item["number"]))
                    self._enqueue_sync_item(item, kind, job["id"])
            for link in self.store.list_links("mirror"):
                data = link["data"]
                cfg = self.settings()
                if data.get("kind") in {"issue", "pull"} and all(data.get(key) == cfg[key] for key in ("repository", "guild_id", "bot_name")) and (data["kind"], data["number"]) not in seen:
                    self._enqueue_sync_item(await client.get_issue(data["number"]), data["kind"], job["id"])
        else:
            raise WorkflowError("Unknown job type.")

    def _enqueue_sync_item(self, item, kind, sync_id):
        from project_automation_webhook import webhook_binding
        cfg = self.settings()
        event = {**item, "kind": kind, "key": f"{kind}:{item['number']}", "repository": cfg["repository"], "_binding": webhook_binding(cfg)}
        self.store.enqueue("event", event, key=f"sync-item:{sync_id}:{kind}:{item['number']}")

    async def run_once(self) -> bool:
        if self.paused() or self.settings().get("bot_name") not in self.bots:
            return False
        job = self.store.claim_job(lease_seconds=600)
        if not job:
            return False
        try:
            await asyncio.wait_for(self.process_job(job), timeout=480)
            self.store.complete_job(job["id"], lease_token=job["lease_token"])
            self._last_error = None
        except asyncio.CancelledError:
            raise
        except Conflict:
            current = self.store.get_job(job["id"])
            if current["state"] == "inflight":
                self.store.fail_job(job["id"], "Delivery completed with a local state conflict; check its destination", lease_token=job["lease_token"])
            elif current["state"] == "running":
                self.store.complete_job(job["id"], lease_token=job["lease_token"])
        except Exception as exc:
            # Persist only a fixed category, never provider responses, credentials, or request headers.
            error = f"{type(exc).__name__}: {job['kind']} needs attention"
            self._last_error = error
            permanent = isinstance(exc, (WorkflowError, GitHubAmbiguousWrite)) or job["attempts"] >= 3
            self.store.fail_job(job["id"], error, delay=30 * job["attempts"], permanent=permanent, lease_token=job["lease_token"])
            if job["kind"] == "publish":
                case = self.get_case(job["payload"]["case_id"])
                if case and case["state"] == "queued" and self.store.get_job(job["id"])["state"] == "recovery":
                    self._save(case, {"state": "recovery", "notice": "Publication needs a maintainer to check its delivery. This draft will not be sent again automatically."})
            if permanent and job["kind"] == "assess":
                case = self.get_case(job["payload"]["case_id"])
                if case and case["state"] == "assessing" and case["revision"] == job["payload"].get("revision"):
                    self._save(case, {"state": "needs_maintainer", "notice": "I couldn’t complete the assessment. Your report is saved; add detail to retry or ask a maintainer."})
        return True

    async def _run(self):
        while True:
            try:
                if not self.paused() and self.settings().get("bot_name") in self.bots:
                    if not self._last_sync or time.monotonic() - self._last_sync > 900:
                        self._last_sync = time.monotonic()
                        self.store.enqueue("sync", {}, key=f"sync:{int(time.time() // 900)}")
                    await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._last_error = "Background processing needs attention"
            await asyncio.sleep(1)


_service = None
_service_lock = threading.Lock()


def get_automation() -> AutomationService:
    global _service
    with _service_lock:
        if _service is None:
            from config import DATA_DIR
            _service = AutomationService(AutomationStore(Path(DATA_DIR) / "project_automation.sqlite3"))
    return _service


async def detach_automation(bot):
    if _service is not None:
        await _service.detach(bot)
