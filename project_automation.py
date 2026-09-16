"""Durable human-gated feedback workflow and bounded background processing."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import threading
import time
from pathlib import Path

from project_automation_ai import FeedbackAI, search_terms
from project_automation_github import GitHubClient, GitHubAmbiguousWrite, GitHubError, prepare_public_text
from project_automation_store import AutomationStore, Conflict


CHOICES = {
    "write": "Write my report", "human": "Ask a maintainer",
    "link": "Add to existing issue", "submit": "Approve and post", "edit": "Edit my report",
    "back": "Keep discussing", "cancel": "Close feedback",
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

    def validate_report_author(self, case_id, revision, user_id, role_ids=(), *, report_revision=None, issue_number=None):
        case = self.get_case(case_id)
        if not case:
            raise WorkflowError("Feedback was not found.")
        self._authorize(case, user_id, role_ids)
        if str(user_id) != case["reporter_id"]:
            raise WorkflowError("The reporter writes and approves their own report. You can help in the thread.")
        if report_revision is None:
            if case["revision"] != revision:
                raise Conflict("The conversation changed. Use the latest report controls.")
            if not {"write", "edit"}.intersection(option["key"] for option in (case.get("gate") or {}).get("options", [])):
                raise WorkflowError("Continue the conversation before submitting a report.")
        else:
            if (type(report_revision) is not int or report_revision != case.get("report_revision", 0)
                    or issue_number != (case.get("target_issue_number") or case.get("linked_issue_number"))):
                raise Conflict("A newer report or destination replaced this form. Open the current report to edit it.")
            if case["state"] in {"processing", "queued", "publishing", "recovery", "filed", "closed"}:
                raise WorkflowError("This report is busy or closed. Use its current controls when it is ready.")
        return case

    def _check_case_access(self, case):
        import runtime_config
        if not runtime_config.is_server_response_allowed(case["feedback_channel_id"])[0]:
            raise WorkflowError("Response access settings block this feedback channel.")

    @staticmethod
    def _job(kind, case, **payload):
        return {"kind": kind, "payload": {"case_id": case["id"], **payload},
                "key": f"{kind}:{case['id']}:{case['revision'] + 1}"}

    def _save(self, case, patch, notify=True):
        patch = {"reply": "", **patch}
        return self.store.update_case(case["id"], patch, expected_revision=case["revision"],
                                      job=self._job("notify", case, revision=case["revision"] + 1) if notify else None)

    @staticmethod
    def _transcript(entries):
        entries = entries[:1] + entries[-19:] if len(entries) > 20 else list(entries)
        while sum(len(entry.get("content", "")) for entry in entries) > 24000 and len(entries) > 2:
            del entries[1]
        return entries

    def _context(self, case):
        delivered = self.store.get_link("conversation", case["id"]) or {}
        entries = case.get("transcript", []) + delivered.get("turns", [])
        entries.sort(key=lambda item: item.get("created_at", 0))
        return {**case, "transcript": self._transcript(entries),
                "controls": dict(CHOICES),
                "maintainer_question": self.store.get_link("maintainer_question", case["id"])}

    async def say(self, case, event):
        context = self._context(case) if case else {"id": "project-command", "repository": self.settings()["repository"]}
        try:
            return await self.ai.speak(context, event, self.bots[self.settings()["bot_name"]], self.settings())
        except Exception:
            # A provider outage is an operational fact, not an invented character line.
            detail = event.get("facts", {}).get("error") or event.get("next_step") or "Try again in this thread."
            return "System notice: character response unavailable. " + str(detail)[:600]

    async def _notify(self, case, job, *, recover_only=False):
        key = f"{case['id']}:{job['id']}"
        snapshot = self.store.get_link("notification", key)
        if snapshot is None:
            if recover_only:
                # Before conversational turns, one receipt represented the case's status card.
                if "revision" in job["payload"]:
                    return False
                return await self.transport.notify({**case, "gate": None}, recover_only=True)
            event = job["payload"].get("event") or case.get("notice_event")
            reply = await self.say(case, event) if event else case.get("reply", "")
            if not reply:
                reply = await self.say(case, {"action": "resume_conversation", "facts": {"state": case["state"]},
                                              "next_step": "Continue this report in the Discord thread."})
            keys = ("id", "revision", "bot_name", "guild_id", "channel_id", "reporter_id", "repository",
                    "state", "gate", "draft", "submitted_report", "github_url", "review_url")
            snapshot = {name: deepcopy(case[name]) for name in keys if name in case}
            if self.get_case(case["id"])["revision"] != case["revision"]:
                return False
            snapshot.update(reply=reply, delivery_key=key, prepared_at=time.time())
            self.store.put_link("notification", key, snapshot)
        if not recover_only:
            self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
        found = await self.transport.notify(snapshot, recover_only=recover_only)
        if not recover_only or found:
            receipt = self.store.get_link("feedback_delivery", key) or {}
            delivered_at = receipt.get("created_at", snapshot.get("prepared_at", case["created_at"]))
            history = self.store.get_link("conversation", case["id"]) or {"turns": []}
            if not any(turn.get("delivery_key") == key for turn in history["turns"]):
                history["turns"].append({"role": "assistant", "content": snapshot["reply"],
                    "author_name": self.settings().get("character_name", ""), "created_at": delivered_at, "delivery_key": key})
                history["turns"] = sorted(history["turns"], key=lambda turn: turn["created_at"])[-8:]
                self.store.put_link("conversation", case["id"], history)
        return found

    def check_new_report(self, reporter_id, content):
        if self.paused():
            raise WorkflowError("Project feedback is paused.")
        if not isinstance(content, str) or not content.strip() or len(content) > 8000:
            raise WorkflowError("Please describe the feedback in 1–8,000 characters.")
        if self.store.count_active_cases(str(reporter_id), self.settings()["guild_id"]) >= 3:
            raise WorkflowError("You already have three open reports. Finish or close one before starting another.")

    def receive_report(self, *, source_key, channel_id, reporter_id, content, source_url, message_id,
                       reporter_name="", title="") -> dict:
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
            "reporter_name": str(reporter_name)[:80], "title": str(title)[:180], "workflow_version": 2,
            "report_revision": 0,
            "state": "assessing", "source_url": source_url, "question_count": 0,
            "transcript": [{"role": "user", "author_id": str(reporter_id), "author_name": str(reporter_name)[:80],
                            "content": content, "created_at": time.time(),
                            "message_id": str(message_id), "source_url": source_url}],
        }, source_key=source_key)
        self.store.enqueue("assess", {"case_id": case["id"], "revision": case["revision"]},
                           key=f"initial:{case['id']}")
        return case

    def add_detail(self, case_id, *, user_id, content, message_id, source_url, role_ids=(), username="") -> dict:
        case = self.get_case(case_id)
        if not case:
            raise WorkflowError("Feedback was not found.")
        if self.paused() or not self._bound(case):
            raise WorkflowError("Feedback is paused or its project configuration has changed.")
        self._check_case_access(case)
        if case["state"] in {"processing", "queued", "publishing", "recovery", "closed"}:
            raise WorkflowError("This report is busy or closed. Its current status is shown above.")
        transcript = case.get("transcript", [])
        if any(item.get("message_id") == str(message_id) for item in transcript):
            return case
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 8000:
            raise WorkflowError("Please keep this update within 8,000 characters.")
        transcript = transcript + [{"role": "user", "author_id": str(user_id), "author_name": str(username)[:80],
                                    "content": content, "created_at": time.time(),
                                    "message_id": str(message_id), "source_url": source_url}]
        transcript = self._transcript(transcript)
        question = self.store.get_link("maintainer_question", case_id) or {}
        return self.store.update_case(case_id, {"transcript": transcript, "state": "assessing", "gate": None,
                                               "workflow_version": 2, "draft": None, "reply": "", "notice_event": None,
                                               "submitted_report": None if case["state"] == "filed" else case.get("submitted_report"),
                                               "report_revision": case.get("report_revision", 0) + (case["state"] == "filed"),
                                               "question_count": 0 if case["state"] == "filed" else case.get("question_count", 0),
                                               "answering_question_key": question.get("key")},
                                      expected_revision=case["revision"],
                                      job=self._job("assess", case, revision=case["revision"] + 1))

    def submit_report(self, case_id, revision, *, user_id, username, title, body, role_ids=(), human_authored=False,
                      report_revision=None, issue_number=None):
        case = self.validate_report_author(case_id, revision, user_id, role_ids,
                                          report_revision=report_revision, issue_number=issue_number)
        if human_authored is not True:
            raise WorkflowError("Confirm that you wrote the report contents yourself.")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 180:
            raise WorkflowError("Write a report title within 180 characters.")
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 2800:
            raise WorkflowError("Write the report contents in your own words, within 2,800 characters.")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.]{2,32}", username):
            raise WorkflowError("Your Discord username could not be verified for attribution.")
        report = {"title": title, "body": body, "author_id": str(user_id), "author_name": username,
                  "human_authored": True, "submitted_at": time.time()}
        return self.store.update_case(case_id, {"workflow_version": 2, "submitted_report": report,
            "report_revision": case.get("report_revision", 0) + 1,
            "draft": None, "state": "assessing", "gate": None, "reply": "", "notice_event": None},
            expected_revision=case["revision"], job=self._job("assess", case, revision=case["revision"] + 1))

    def choose(self, case_id, revision, action, *, user_id, role_ids=()) -> dict:
        case = self.get_case(case_id)
        if not case:
            raise WorkflowError("Feedback was not found.")
        self._authorize(case, user_id, role_ids)
        if action in {"write", "edit", "draft", "investigate"}:
            raise WorkflowError("Use the current report form, or reply directly in this thread.")
        if action == "submit":
            if str(user_id) != case["reporter_id"]:
                raise WorkflowError("Only the reporter can approve publication of their own text.")
            if (case.get("workflow_version") != 2 or not self._human_draft(case)
                    or (case.get("gate") or {}).get("kind") != "review"):
                raise WorkflowError("Write and review your own report before approving publication.")
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
            target = case.get("target_issue_number") or case.get("linked_issue_number")
            if target:
                candidates = [await client.get_issue(target)]
            else:
                report = case.get("submitted_report") or {}
                query = search_terms(report.get("title", "") + " " + report.get("body", "")) if report else search_terms(
                    case.get("title", "") + " " + " ".join(item["content"] for item in case.get("transcript", [])[-4:]))
                if query:
                    candidates = await client.search_issues(query, limit=5)
                else:
                    search_unavailable = True
        except GitHubError:
            search_unavailable = True
        candidates = [{**item, "body": item.get("body", "")[:2000]} for item in candidates[:5]]
        context = {**self._context(case), "search_unavailable": search_unavailable,
                   "linked_issue_number": case.get("target_issue_number") or case.get("linked_issue_number")}
        result = await self.ai.assess(context, candidates, self.bots[case["bot_name"]], self.settings())
        eligible = (result.get("kind") in {"bug", "feature"}
                    and result.get("recommendation") in {"ready", "link"} and not search_unavailable)
        report = case.get("submitted_report") or {}
        count = case.get("question_count", 0)
        state, actions, draft = "discussing", ["human", "cancel"], None
        if report.get("human_authored") is True:
            actions.insert(0, "edit")
        if eligible:
            if result.get("duplicate_number") and not (case.get("target_issue_number") or case.get("linked_issue_number")):
                state = "awaiting_report"
                actions.insert(0, "link")
            elif report.get("human_authored") is True:
                state, actions = "awaiting_submission", ["submit", "edit", "back", "cancel"]
                draft = self._draft_from_report(case)
            else:
                state, actions = "awaiting_report", ["write", "human", "cancel"]
        elif result.get("recommendation") == "human" or search_unavailable:
            state = "needs_maintainer"
        elif result.get("recommendation") == "investigate":
            count += 1
            if count > self.settings()["max_questions"]:
                state = "needs_maintainer"
                result["reply"] = await self.say(case, {"action": "clarification_limit", "facts": {"report_saved": True},
                    "next_step": "Ask a maintainer for help in this thread; the reporter can still add or edit their own details."})
        self._save(case, {"workflow_version": 2, "assessment": result, "candidates": candidates,
                          "assessment_question_key": case.get("answering_question_key"),
                          "search_unavailable": search_unavailable, "question_count": count,
                          "reply": result["reply"], "notice_event": None, "draft": draft,
                          "review_url": f"https://discord.com/channels/{case['guild_id']}/{case['channel_id']}",
                          "state": state, "gate": gate("review" if draft else "conversation", actions)})

    @staticmethod
    def _draft_from_report(case):
        report = case.get("submitted_report") or {}
        if report.get("human_authored") is not True or report.get("author_id") != case["reporter_id"]:
            raise WorkflowError("A human-written report from its reporter is required.")
        number = case.get("target_issue_number") or case.get("linked_issue_number")
        body = prepare_public_text(f"Forwarded from Discord\n@{report['author_name']}\n\n{report['body']}", 3800)
        return {"kind": "comment" if number else "issue", "title": prepare_public_text(report["title"], 180),
                "body": body, "issue_number": number, "question_key": case.get("answering_question_key"),
                "author_id": report["author_id"], "human_authored": True}

    def _human_draft(self, case):
        try:
            return case.get("draft") == self._draft_from_report(case)
        except (WorkflowError, KeyError, TypeError):
            return False

    async def _branch(self, case, action):
        if case.get("workflow_version") != 2:
            self.store.update_case(case["id"], {"workflow_version": 2, "state": "assessing", "gate": None,
                "draft": None, "submitted_report": None, "reply": "", "notice_event": None},
                expected_revision=case["revision"], job=self._job("assess", case, revision=case["revision"] + 1))
            return
        assessment = case.get("assessment", {})
        if action == "link":
            number = assessment.get("duplicate_number")
            if (type(number) is not int or number not in {item["number"] for item in case.get("candidates", [])}
                    or case.get("search_unavailable")):
                raise WorkflowError("The suggested issue is no longer available.")
            self.store.update_case(case["id"], {"target_issue_number": number, "state": "assessing", "gate": None},
                expected_revision=case["revision"], job=self._job("assess", case, revision=case["revision"] + 1))
        elif action == "submit":
            if case.get("workflow_version") != 2 or not self._human_draft(case):
                raise WorkflowError("The reporter must write and approve their own report.")
            marker = f"{case['id']}-{case['revision']}"
            self.store.update_case(case["id"], {"state": "queued", "publication_marker": marker}, expected_revision=case["revision"],
                                   job=self._job("publish", case, draft=case["draft"],
                                                 marker=marker))
        elif action == "back":
            actions = (["edit"] if case.get("submitted_report") else []) + ["human", "cancel"]
            self._save(case, {"state": "discussing", "draft": None, "gate": gate("conversation", actions),
                "notice_event": {"action": "continue_discussion", "facts": {"published": False},
                                 "next_step": "Reply in this thread with what you want to work through."}})
        elif action == "human":
            actions = (["edit"] if case.get("submitted_report") else []) + ["back", "cancel"]
            self._save(case, {"state": "needs_maintainer", "draft": None, "gate": gate("conversation", actions),
                "notice_event": {"action": "ask_maintainer", "facts": {"thread_url": case.get("review_url", case["source_url"])},
                                 "next_step": "Share this Discord thread with a maintainer for help. You can keep discussing it here."}})
        elif action == "cancel":
            self._save(case, {"state": "closed", "gate": None,
                "notice_event": {"action": "close_feedback", "facts": {"closed_by_request": True},
                                 "next_step": "The feedback conversation is closed."}})
        else:
            raise WorkflowError("Unknown feedback action.")

    async def _publish(self, case, job):
        if case["state"] != "queued":
            return
        draft, marker = job["payload"]["draft"], job["payload"]["marker"]
        if case.get("workflow_version") != 2 or not self._human_draft(case):
            self.store.update_case(case["id"], {"workflow_version": 2, "state": "assessing", "gate": None,
                "draft": None, "submitted_report": None}, expected_revision=case["revision"],
                job=self._job("assess", case, revision=case["revision"] + 1))
            return
        if draft != case["draft"] or marker != case.get("publication_marker"):
            raise WorkflowError("The queued publication does not match the approved report.")
        client = await self.client()
        self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
        try:
            if draft["kind"] == "issue":
                result = await client.create_issue(draft["title"], draft["body"], marker)
            else:
                result = await client.add_comment(draft["issue_number"], draft["body"], marker)
        except (GitHubAmbiguousWrite, asyncio.CancelledError):
            self._save(case, {"state": "recovery", "notice_event": {"action": "uncertain_publication",
                "facts": {"publication_confirmed": False}, "next_step": "A maintainer must check GitHub before another attempt."}})
            raise
        number = draft.get("issue_number") or result["number"]
        question = self.store.get_link("maintainer_question", case["id"]) or {}
        if draft.get("question_key") and question.get("key") == draft["question_key"]:
            self.store.put_link("maintainer_question", case["id"], {})
        self._save(case, {"state": "filed", "linked_issue_number": number,
                          "github_url": result["html_url"], "gate": None,
                          "notice_event": {"action": "published", "facts": {"github_url": result["html_url"], "number": number},
                                           "next_step": "You can follow the report on GitHub; updates will also appear in this thread."}})

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
                found = await self._notify(case, job, recover_only=True)
            else:
                self._check_event_binding(job["payload"])
                found = await self.transport.mirror(job["payload"], recover_only=True)
            if found:
                self.store.resolve_recovery(job_id, outcome="complete" if job["kind"] == "notify" else "retry",
                    actor=str(user_id), note="Found existing Discord delivery")
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
                          "github_url": result["html_url"], "gate": None,
                          "notice_event": {"action": "publication_recovered", "facts": {"github_url": result["html_url"]},
                                           "next_step": "Follow the existing report on GitHub."}})
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
            fresh_report = case.get("workflow_version") == 2 and self._human_draft(case)
            patch = ({"state": "awaiting_submission", "draft": job["payload"]["draft"], "reply": "",
                      "gate": gate("review", ["submit", "edit", "back", "cancel"]),
                      "notice_event": {"action": "review_again", "facts": {"publication_confirmed_absent": True},
                                       "next_step": "The reporter must review and approve their exact text again."}}
                     if fresh_report else {"workflow_version": 2, "state": "assessing", "draft": None,
                                           "submitted_report": None, "gate": None, "reply": "", "notice_event": None})
            self.store.resolve_recovery(job_id, outcome="cancel", actor=str(user_id),
                                        note="Maintainer confirmed no delivery; returned draft for fresh approval",
                                        case_update={"case_id": case["id"], "expected_revision": case["revision"],
                                                     "patch": patch,
                                                     "job": self._job("notify" if fresh_report else "assess", case,
                                                                      revision=case["revision"] + 1)})
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
                    self.store.enqueue("notify", {"case_id": case["id"], "event": {"action": "maintainer_question",
                        "facts": {"question": event["body"], "github_url": event["html_url"]},
                        "next_step": "Discuss the question in this thread. Any GitHub reply needs your own written text and approval."}},
                        key=f"question:{case['id']}:{event['key']}")
        else:
            self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
            await self.transport.mirror(event)
            if event["kind"] == "issue":
                for case in self.store.cases_for_issue(event["repository"], event["number"]):
                    if self._bound(case):
                        # Status is separate from draft/gate state; no pending approval is lost.
                        self.store.put_link("case_status", case["id"], {"state": event["state"], "url": event["html_url"]})
                        self.store.enqueue("notify", {"case_id": case["id"], "event": {"action": "issue_status",
                            "facts": {"state": event["state"], "github_url": event["html_url"]},
                            "next_step": "Follow the linked issue for details; closed does not by itself mean fixed."}},
                            key=f"status:{case['id']}:{event['state']}:{event.get('updated_at', time.time())}")

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
            if payload.get("revision", case["revision"]) == case["revision"]:
                await self._notify(case, job)
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
                    self._save(case, {"state": "recovery", "notice_event": {"action": "uncertain_publication",
                        "facts": {"publication_confirmed": False}, "next_step": "A maintainer must check GitHub before another attempt."}})
            if permanent and job["kind"] == "assess":
                case = self.get_case(job["payload"]["case_id"])
                if case and case["state"] == "assessing" and case["revision"] == job["payload"].get("revision"):
                    self._save(case, {"state": "needs_maintainer", "gate": gate("conversation", ["human", "cancel"]),
                        "notice_event": {"action": "assessment_failed", "facts": {"report_saved": True},
                                         "next_step": "Add details in this thread to retry, or ask a maintainer for help."}})
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
