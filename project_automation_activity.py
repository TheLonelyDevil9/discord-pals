"""PR activity orchestration: activation boundaries and resumable read jobs."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import time

from project_automation_github import GitHubError


SOURCES = ("comment", "review_comment", "review", "thread", "commits")


def activity_receipt_key(config, event):
    parts = [config['guild_id'], config['bot_name'], config['reviews_channel_id'], config['repository'],
             event.get('delivery_key', event['key'])]
    return "pr:" + hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def mapping_key(config):
    identity = {key: config.get(key, "") for key in
                ("repository", "guild_id", "bot_name", "reviews_channel_id")}
    identity["repository"] = identity["repository"].lower()
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def activate(store, config):
    return store.ensure_link("pr_activation", mapping_key(config), {"since": time.time()})


def source_key(config, event):
    return mapping_key(config) + ":" + hashlib.sha256(event["key"].encode()).hexdigest()


def head_key(config, event):
    return f"{mapping_key(config)}:{event['number']}"


def head_occurrence(event, prior):
    """A return to an earlier SHA is a new branch transition, not a replay."""
    same = prior is not None and prior["event"].get("after") == event.get("after")
    revision = (prior or {}).get("event", {}).get("head_revision", 0) + (0 if same else 1)
    previous = (prior or {}).get("event", {})
    lower_bound = previous.get("occurrence_after", 0) if same else max(
        event_time(previous), event_time({"updated_at": previous.get("observed_updated_at", "")}))
    return {**event, "head_revision": max(1, revision),
            "occurrence_after": lower_bound,
            "observed_before": prior["event"].get("observed_before", "") if same else (prior or {}).get("event", {}).get("after", ""),
            "delivery_key": f"{event['key']}:head:{max(1, revision)}"}


def prepare_head(event, prior):
    """Keep late webhook metadata from claiming a reused head's new occurrence."""
    previous = (prior or {}).get("event", {})
    same = previous.get("after") == event.get("after")
    source_time, previous_time = event_time(event), event_time(previous)
    prepared = head_occurrence(event, prior)
    if source_time and source_time < prepared["occurrence_after"]:
        return None
    # Reconciliation has no push author/time. Preserve previously verified metadata.
    # A newer synchronize can enrich a reconciled occurrence by editing its message.
    preferable_transition = (event.get("before") == previous.get("observed_before")
                            and previous.get("before") != previous.get("observed_before"))
    if same and previous.get("delivery_prepared") and (
            not source_time or source_time < previous_time
            or (source_time == previous_time and not preferable_transition)):
        prepared = {**previous, "_binding": event["_binding"]}
    fields = ("key", "before", "after", "author", "author_type", "updated_at")
    prepared["source_version"] = hashlib.sha256(json.dumps(
        {key: prepared.get(key) for key in fields}, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    prepared["delivery_prepared"] = True
    return prepared


def event_time(event):
    for field in ("updated_at", "created_at"):
        try:
            value = datetime.fromisoformat(event.get(field, "").replace("Z", "+00:00"))
            if value.tzinfo is not None:
                return value.timestamp()
        except (ValueError, TypeError, AttributeError):
            pass
    return 0


def compact_event(event):
    result = dict(event)
    result.pop("pull", None)
    if len(result.get("body", "")) > 4000:
        result["body"] = result["body"][:4000]
        result["body_truncated"] = True
    return result


class PRActivity:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def enqueue_sync(self, item, cycle):
        from project_automation_webhook import webhook_binding
        cfg = self.service.settings()
        activate(self.store, cfg)
        self.store.save_links_with_jobs([], self._source_jobs(item["number"], cycle, webhook_binding(cfg)))

    def _source_jobs(self, number, cycle, binding):
        return [{"kind": "pr_sync_page", "payload": {
            "repository": binding["repository"], "number": number, "source": source,
            "cursor": None, "page": 1, "_binding": binding, "_sync_cycle": cycle,
        }, "key": f"pr-page:{cycle}:{number}:{source}:1"} for source in SOURCES]

    def enqueue_discovery(self, cycle):
        cfg, binding = self.service.settings(), self._binding()
        baseline = activate(self.store, cfg)
        checkpoint = self.store.get_link("pr_discovery_checkpoint", mapping_key(cfg)) or {}
        # A retried root job must use its original window even if another cycle
        # completed meanwhile. Source times have second precision, hence overlap.
        window = self.store.ensure_link("pr_discovery_cycle", str(cycle), {
            "since": max(baseline["since"], checkpoint.get("completed_through", 0) - 1),
            "started_at": self.store.get_job(cycle)["created_at"],
        })
        self.store.enqueue("pr_sync_discover", {**window, "cursor": None, "page": 1,
                           "repository": cfg["repository"], "_binding": binding,
                           "_sync_cycle": cycle}, key=f"pr-discover:{cycle}:1")

    async def sync_discover(self, payload):
        self.service._check_event_binding(payload)
        result = await (await self.service.client()).list_updated_pulls(payload["since"], payload["cursor"])
        self.service._check_event_binding(payload)
        cycle, jobs, links = payload["_sync_cycle"], [], []
        for item in result["items"]:
            jobs.extend(self._source_jobs(item["number"], cycle, payload["_binding"]))
        cursor, mapping = result.get("next_cursor"), mapping_key(self.service.settings())
        if cursor is not None:
            next_payload = {**payload, "cursor": cursor, "page": payload["page"] + 1}
            jobs.append({"kind": "pr_sync_discover", "payload": next_payload,
                         "key": f"pr-discover:{cycle}:{next_payload['page']}"})
        else:
            prior = self.store.get_link("pr_discovery_checkpoint", mapping) or {}
            links.append(("pr_discovery_checkpoint", mapping, {
                "completed_through": max(payload["started_at"], prior.get("completed_through", 0)),
            }))
        links.append(("pr_discovery_page", f"{mapping}:{cycle}", {"cursor": cursor, "complete": cursor is None}))
        self.store.save_links_with_jobs(links, jobs)

    def _job(self, event, cycle):
        digest = hashlib.sha256((event["key"] + ":" + event["source_version"]).encode()).hexdigest()
        return {"kind": "event", "payload": {**event, "_sync_cycle": cycle},
                "key": f"pr-event:{cycle}:{mapping_key(self.service.settings())}:{digest}"}

    async def sync_page(self, payload):
        self.service._check_event_binding(payload)
        client = await self.service.client()
        cfg = self.service.settings()
        baseline = activate(self.store, cfg)
        result = await client.list_pr_activity(payload["number"], payload["source"], payload["cursor"])
        self.service._check_event_binding(payload)
        links, jobs = [], []
        cycle = payload["_sync_cycle"]
        for raw in result["items"]:
            event = {**compact_event(raw), "_binding": payload["_binding"]}
            key = source_key(cfg, event)
            prior = self.store.get_link("pr_source", key)
            if event.get("activity_type") == "commits":
                prior = self.store.get_link("pr_head", head_key(cfg, event))
                if prior:
                    event["before"] = prior["event"].get("after", "")
                event = head_occurrence(event, prior)
            changed = prior is not None and prior["version"] != event["source_version"]
            recent = (prior is None and event_time(event) >= baseline["since"]
                      and event.get("activity_type") != "commits"
                      and not (event.get("activity_type") == "thread" and event.get("state") == "unresolved"))
            if changed or recent:
                jobs.append(self._job(event, cycle))
            links.append(("pr_source", key, {"version": event["source_version"], "event": event, "cycle": cycle}))
            if event.get("activity_type") == "commits":
                head_event = event
                if prior and prior["event"].get("after") == event.get("after"):
                    head_event = {**prior["event"], "observed_updated_at": max(
                        event.get("observed_updated_at", ""), prior["event"].get("observed_updated_at", ""))}
                links.append(("pr_head", head_key(cfg, event),
                              {"version": event["source_version"], "event": head_event, "cycle": cycle}))
        cursor = result.get("next_cursor")
        if cursor is not None:
            next_payload = {**payload, "cursor": cursor, "page": payload["page"] + 1}
            jobs.append({"kind": "pr_sync_page", "payload": next_payload,
                         "key": f"pr-page:{cycle}:{payload['number']}:{payload['source']}:{next_payload['page']}"})
        # Persist both observed versions and their jobs, including the next page, atomically.
        links.append(("pr_checkpoint", f"{mapping_key(cfg)}:{payload['number']}:{payload['source']}",
                      {"cycle": cycle, "cursor": cursor, "complete": cursor is None}))
        if cursor is None and payload["source"] in {"comment", "review_comment"}:
            jobs.append({"kind": "pr_sync_missing", "payload": {**payload, "after": ""},
                         "key": f"pr-missing:{cycle}:{payload['number']}:{payload['source']}:first"})
        self.store.save_links_with_jobs(links, jobs)

    async def sync_missing(self, payload):
        # Listing endpoints omit deletions. Page only this PR's previously observed
        # comments, and enqueue each remote check separately so the worker can yield.
        self.service._check_event_binding(payload)
        cycle, cfg = payload["_sync_cycle"], self.service.settings()
        records = self.store.list_pr_sources(mapping_key(cfg), payload["number"], payload["source"],
                                             after=payload["after"], limit=100)
        jobs = []
        for record in records:
            old = record["data"]
            if old.get("cycle") != cycle and old["event"].get("action") != "deleted":
                jobs.append({"kind": "pr_sync_check", "payload": {**old["event"], "_sync_cycle": cycle},
                             "key": f"pr-check:{cycle}:{record['key']}"})
        cursor = records[-1]["key"] if len(records) == 100 else ""
        if cursor:
            jobs.append({"kind": "pr_sync_missing", "payload": {**payload, "after": cursor},
                         "key": f"pr-missing:{cycle}:{payload['number']}:{payload['source']}:{cursor}"})
        self.store.save_links_with_jobs([
            ("pr_missing_checkpoint", f"{mapping_key(cfg)}:{payload['number']}:{payload['source']}",
             {"cycle": cycle, "cursor": cursor, "complete": not cursor})], jobs)

    async def sync_check(self, event):
        self.service._check_event_binding(event)
        current = await (await self.service.client()).get_pr_activity(event)
        self.service._check_event_binding(event)
        if current is None:
            return
        if current.get("key") != event.get("key") or current.get("number") != event.get("number"):
            raise GitHubError("The activity source changed identity.")
        cfg = self.service.settings()
        current = {**compact_event(current), "_binding": event["_binding"]}
        key = source_key(cfg, current)
        prior = self.store.get_link("pr_source", key)
        jobs = []
        if prior and prior["version"] != current["source_version"]:
            jobs.append(self._job(current, event["_sync_cycle"]))
        self.store.save_links_with_jobs([
            ("pr_source", key, {"version": current["source_version"], "event": current,
                                "cycle": event["_sync_cycle"]})], jobs)

    async def deliver(self, event, job):
        self.service._check_event_binding(event)
        cfg = self.service.settings()
        baseline = activate(self.store, cfg)
        client = await self.service.client()
        current = await client.get_pr_activity(event)
        if current is None:
            return
        if current.get("key") != event.get("key") or current.get("number") != event.get("number"):
            raise GitHubError("The activity source changed identity.")
        self.service._check_event_binding(event)
        key = source_key(cfg, current)
        prior = self.store.get_link("pr_source", key)
        # Replayed historical creation events cannot import pre-activation discussion.
        # Resolution/deletion events have no dependable source timestamp; their accepted
        # delivery time is the boundary for otherwise unobserved sources.
        recent = event_time(current) >= baseline["since"]
        transition = event.get("action") in {"deleted", "resolved", "unresolved", "dismissed"}
        # GitHub reviews retain submitted_at when their text is edited and do
        # not expose updated_at. The accepted edit is the observable transition.
        transition = transition or (event.get("activity_type") == "review" and event.get("action") == "edited")
        changed = prior is not None and prior["version"] != current["source_version"]
        if not (recent or changed or (transition and job["created_at"] >= baseline["since"])
                or event.get("_sync_cycle") is not None):
            return
        prepared = {**compact_event(current), "_binding": event["_binding"]}
        if current.get("activity_type") == "commits":
            prior_head = self.store.get_link("pr_head", head_key(cfg, current))
            prepared = prepare_head(prepared, prior_head)
            if prepared is None:
                return
            # Reserve the observed occurrence before sending. A second delivery or
            # restart then checks the same receipt, even when a force-push reused a SHA.
            self.store.put_link("pr_head", head_key(cfg, current), {
                "version": current["source_version"], "event": prepared,
                "cycle": event.get("_sync_cycle"),
            })
        pull = await client.get_issue(event["number"])
        if pull.get("kind") != "pull":
            raise GitHubError("PR activity did not resolve to a pull request.")
        prepared["pull"] = {**pull, "body": pull.get("body", "")[:3800]}
        self.service._check_event_binding(event)
        # Keep the original payload immutable for GitHub delivery-ID deduplication.
        self.store.put_link("pr_prepared", str(job["id"]), prepared)
        self.store.mark_job_inflight(job["id"], lease_token=job["lease_token"], lease_seconds=600)
        await self.service.transport.mirror_activity(prepared)
        self.store.put_link("pr_source", key, {
            "version": current["source_version"], "event": compact_event(prepared),
            "cycle": event.get("_sync_cycle", (prior or {}).get("cycle")),
        })
        self.store.record_status(mapping_key(cfg), "last_activity_at")
        if current.get("activity_type") == "commits":
            self.store.put_link("pr_head", head_key(cfg, current), {
                "version": current["source_version"], "event": compact_event(prepared),
                "cycle": event.get("_sync_cycle"),
            })

    def complete_cycle(self, cycle):
        job = self.store.get_job(cycle)
        if job is None or job["state"] != "done" or self.store.unfinished_sync_jobs(cycle):
            return
        cfg = self.service.settings()
        # Only the configuration that started this cycle receives its success stamp.
        if job["payload"].get("_binding") != self._binding():
            return
        self.store.record_status(mapping_key(cfg), "last_sync_at")

    def _binding(self):
        from project_automation_webhook import webhook_binding
        return webhook_binding(self.service.settings())
