"""Plan the move of the Project helper's stored records from one helper bot name to another.

The helper keys its records by its settings, the bot name included: the mapping hash, the
post keys, the PR activity receipt keys, and the binding of each stored event. A Discord post
keeps the footer it was sent with, so each moved post record stores that footer as "marker"
and each moved activity receipt stores its message's footer prefix as "marker_prefix".
AutomationStore.rewrite_records reads, writes and verifies the rename in one transaction;
tools/project_data.py rename-helper is its command line.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
import json

from project_automation import MIRROR_CHANNELS, AutomationService, mirror_key
from project_automation_activity import (
    SOURCES, activity_receipt_key, head_key, mapping_key, marker_prefix, source_key,
)
from project_automation_config import normalize_project_config
from project_automation_store import MAX_DOCUMENT_BYTES, MAX_LINK_BYTES, RowChange, _json_object
from project_automation_webhook import webhook_binding


# Event key types per post kind, as project_automation_github.normalize_event and the sync root write them.
MIRROR_EVENT_TYPES = {"issue": ("issue",), "pull": ("pull",), "commit": ("push",),
                      "general": ("release", "workflow_run")}
# Link kinds keyed by a mapping hash, and the parts that follow the hash in their keys.
MAPPING_KEYED = {
    "pr_activation": (), "project_status": (), "pr_discovery_checkpoint": (),
    "pr_walk": ("number",), "pr_discovery_page": ("number",),
    "pr_checkpoint": ("number", "source"), "pr_missing_checkpoint": ("number", "source"),
}
IDENTITY_FREE = {"case_status", "conversation", "feedback", "feedback_delivery", "github_delivery",
                 "item_closed", "job_dismissal", "maintainer_question", "pr_discovery_cycle", "question_notice"}
# Document fields that may name the helper, per kind. A name anywhere else is not handled.
IDENTITY_PATHS = {
    "pr_source": {("event", "_binding"), ("event", "_binding", "bot_name")},
    "pr_head": {("event", "_binding"), ("event", "_binding", "bot_name")},
    "pr_prepared": {("_binding",), ("_binding", "bot_name")},
    "mirror": {("bot_name",)}, "pr_activity": {("bot_name",)}, "pr_activity_delivery": {("bot_name",)},
    "notification": {("bot_name",)}, "cases": {("bot_name",)},
}
# A commit receipt hashes its PR's head revision, which never exceeds the highest head_revision
# stored in a pr_source or pr_head record. The search for it stops this far past that value.
REVISION_MARGIN = 64


class Refused(Exception):
    """The settings fail a precondition of the rename, so nothing was read or changed."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class Unverifiable(Exception):
    """A record of the old identity differs from what the helper would have written."""


@dataclass(frozen=True)
class Identity:
    """Normalized Project settings for one helper name, with its mapping hash and event binding."""

    settings: dict
    mapping: str
    binding: dict

    @property
    def name(self):
        return self.settings["bot_name"]

    def mirror_prefix(self, kind):
        return mirror_key(self.settings, kind, "")

    def receipt_fields(self):
        """Return the identity fields of a PR activity receipt, under the names _record in project_automation_discord_activity gives them."""
        s = self.settings
        return {"guild_id": s["guild_id"], "bot_name": s["bot_name"], "forum_id": s["reviews_channel_id"],
                "repository": s["repository"]}


def _identity(settings):
    return Identity(settings, mapping_key(settings), webhook_binding(settings))


def identities(raw_settings, old_name, new_name):
    """Return the old and new identities, or raise Refused when the settings do not describe the old helper."""
    old = normalize_project_config(raw_settings)
    new = normalize_project_config({**old, "bot_name": new_name})
    reasons = []
    if old["enabled"]:
        reasons.append("The Project helper is enabled. Pause it on the Project page, then run this again.")
    if old["bot_name"] != old_name:
        reasons.append("The Project settings select a different helper bot than --from.")
    if new_name == old_name:
        reasons.append("--to is the same as --from.")
    elif not new_name or new["bot_name"] != new_name:
        reasons.append("--to is not a valid helper bot name: printable text without outer spaces, "
                       "at most 128 characters.")
    missing = [name for name in ("bot_name", "guild_id", "repository", "reviews_channel_id") if not old[name]]
    if missing:
        reasons.append("The Project settings lack " + ", ".join(missing) + ".")
    if reasons:
        raise Refused(reasons)
    return _identity(old), _identity(new)


def _digits(text):
    return isinstance(text, str) and text.isascii() and text.isdigit()


def _identity_fields(value, path=()):
    """Yield (path, value) for every bot_name field and every _binding object in a decoded document."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name in ("bot_name", "_binding"):
                yield path + (name,), item
            yield from _identity_fields(item, path + (name,))
    elif isinstance(value, list):
        for item in value:
            yield from _identity_fields(item, path + ("[]",))


def naming_paths(data, identity):
    """Return the paths of the bot_name fields and _binding objects in `data` that name `identity`."""
    return {path for path, value in _identity_fields(data)
            if value == identity.name or (isinstance(value, dict) and value.get("bot_name") == identity.name)}


def key_names(key, identity):
    """Return whether a key embeds the identity: its mapping hash, or a post key of its bot on any server."""
    server, _, rest = key.partition(":")
    return identity.mapping in key or (_digits(server) and rest.startswith(identity.name + ":"))


def _mirror_event_key(kind, event_key):
    event_type, separator, rest = event_key.partition(":")
    if event_type not in MIRROR_EVENT_TYPES[kind] or not separator or not rest:
        return False
    return _digits(rest) if kind in ("issue", "pull") else True


def _mirror_key_kind(key, identity, kinds):
    """Return the one post kind whose key format for `identity` matches `key`, or raise Unverifiable."""
    matches = [kind for kind in kinds if key.startswith(identity.mirror_prefix(kind))
               and _mirror_event_key(kind, key[len(identity.mirror_prefix(kind)):])]
    if len(matches) != 1:
        raise Unverifiable("key differs from every post key the old settings produce")
    return matches[0]


@dataclass
class _Context:
    old: Identity
    new: Identity
    rows: object
    revisions: int
    receipts: dict = field(default_factory=dict)
    old_cases: set = field(default_factory=set)
    markers: collections.Counter = field(default_factory=collections.Counter)

    def add_receipt(self, old_key, new_key):
        if self.receipts.setdefault(old_key, new_key) != new_key:
            raise Unverifiable("two stored records give one receipt different new keys")


def _mapping_keyed(kind, key, data, ctx):
    if not key.startswith(ctx.old.mapping):
        raise Unverifiable("key does not start with the old mapping hash")
    rest = key[len(ctx.old.mapping):]
    parts = rest[1:].split(":") if rest else []
    shape = MAPPING_KEYED[kind]
    if ((rest and rest[0] != ":") or len(parts) != len(shape)
            or not all(_digits(part) if expected == "number" else part in SOURCES
                       for part, expected in zip(parts, shape))):
        raise Unverifiable("key does not have the shape the helper writes")
    return ctx.new.mapping + rest, None


def _event_keyed(kind, key, data, ctx):
    function = source_key if kind == "pr_source" else head_key
    event = data.get("event")
    if not isinstance(event, dict) or key != function(ctx.old.settings, event):
        raise Unverifiable("key differs from the key the helper computes from the stored event")
    if event.get("_binding") != ctx.old.binding:
        raise Unverifiable("stored binding differs from the binding of the old settings")
    if event.get("kind") == "pr_activity":
        ctx.add_receipt(activity_receipt_key(ctx.old.settings, event), activity_receipt_key(ctx.new.settings, event))
    return function(ctx.new.settings, event), {**data, "event": {**event, "_binding": ctx.new.binding}}


def _receipt_event(receipt, data, ctx):
    """Return the activity event whose receipt key under the old settings is `receipt`.

    A commit receipt hashes its head revision, which the receipt does not store, so each
    revision up to the search bound is tried.
    """
    if any(data.get(name) != value for name, value in ctx.old.receipt_fields().items()):
        raise Unverifiable("receipt names a different server, forum or repository")
    source = data.get("source_key")
    if not isinstance(source, str):
        raise Unverifiable("receipt has no source key")
    events = [{"kind": "pr_activity", "key": source}]
    if data.get("activity_type") == "commits":
        events += ({"kind": "pr_activity", "key": source, "delivery_key": f"{source}:head:{revision}"}
                   for revision in range(1, ctx.revisions + 1))
    for event in events:
        if activity_receipt_key(ctx.old.settings, event) == receipt:
            return event
    raise Unverifiable("key differs from every receipt key the helper computes from the stored receipt")


def _pr_activity(kind, key, data, ctx):
    event = _receipt_event(key, data, ctx)
    new_key = activity_receipt_key(ctx.new.settings, event)
    ctx.add_receipt(key, new_key)
    new_data = {**data, "bot_name": ctx.new.name}
    if "marker_prefix" in data:
        ctx.markers["activity prefixes kept"] += 1
    else:
        new_data["marker_prefix"] = marker_prefix(key)
        ctx.markers["activity prefixes added"] += 1
    return new_key, new_data


def _pr_activity_delivery(kind, key, data, ctx):
    version = data.get("source_version")
    if not isinstance(version, str) or not key.endswith(":" + version):
        raise Unverifiable("key does not end with the stored source version")
    receipt = key[:-len(version) - 1]
    new_receipt = activity_receipt_key(ctx.new.settings, _receipt_event(receipt, data, ctx))
    ctx.add_receipt(receipt, new_receipt)
    return f"{new_receipt}:{version}", {**data, "bot_name": ctx.new.name}


def _mirror(kind, key, data, ctx):
    mirror_kind = data.get("kind")
    if mirror_kind not in MIRROR_CHANNELS:
        raise Unverifiable("post record has no post kind")
    prefix = ctx.old.mirror_prefix(mirror_kind)
    event_key = key[len(prefix):]
    if (not key.startswith(prefix) or not _mirror_event_key(mirror_kind, event_key)
            or (mirror_kind in ("issue", "pull") and event_key != f"{mirror_kind}:{data.get('number')}")):
        raise Unverifiable("key differs from the post key the helper computes from the record and the old settings")
    if any(data.get(name) != ctx.old.settings[name] for name in ("guild_id", "bot_name", "repository")):
        raise Unverifiable("post record names a different server or repository")
    new_data = {**data, "bot_name": ctx.new.name}
    if "marker" in data:
        ctx.markers["post footers kept"] += 1
    else:
        # This must equal the footer DiscordTransport.mirror wrote on the post under the old key.
        new_data["marker"] = "Project " + key
        ctx.markers["post footers added"] += 1
    return mirror_key(ctx.new.settings, mirror_kind, event_key), new_data


def _mirror_attempt(kind, key, data, ctx):
    mirror_kind = _mirror_key_kind(key, ctx.old, tuple(MIRROR_CHANNELS))
    record = ctx.rows.links.get(("mirror", key))
    if record is not None and json.loads(record[0]).get("kind") != mirror_kind:
        raise Unverifiable("attempt key and post record disagree on the post kind")
    return mirror_key(ctx.new.settings, mirror_kind, key[len(ctx.old.mirror_prefix(mirror_kind)):]), None


def _item_mirrored(kind, key, data, ctx):
    mirror_kind = _mirror_key_kind(key, ctx.old, ("issue", "pull"))
    return mirror_key(ctx.new.settings, mirror_kind, key[len(ctx.old.mirror_prefix(mirror_kind)):]), None


def _notification(kind, key, data, ctx):
    case_id = data.get("id")
    if (data.get("delivery_key") != key or not isinstance(case_id, str) or not key.startswith(case_id + ":")
            or case_id not in ctx.old_cases):
        raise Unverifiable("snapshot does not belong to a renamed feedback case")
    if any(data.get(name) != ctx.old.settings[name] for name in ("guild_id", "repository")):
        raise Unverifiable("snapshot names a different server or repository")
    return key, {**data, "bot_name": ctx.new.name}


def _pr_prepared(kind, key, data, ctx):
    if not _digits(key) or data.get("_binding") != ctx.old.binding:
        raise Unverifiable("prepared delivery has a binding that differs from the old settings")
    return key, {**data, "_binding": ctx.new.binding}


HANDLERS = {**{kind: _mapping_keyed for kind in MAPPING_KEYED},
            "pr_source": _event_keyed, "pr_head": _event_keyed,
            "pr_activity": _pr_activity, "pr_activity_delivery": _pr_activity_delivery,
            "mirror": _mirror, "mirror_attempt": _mirror_attempt, "item_mirrored": _item_mirrored,
            "notification": _notification, "pr_prepared": _pr_prepared}
# An activity attempt guard carries no identity of its own, so plan_rename moves it with its receipt.
KNOWN_KINDS = set(HANDLERS) | IDENTITY_FREE | {"pr_activity_attempt"}


@dataclass
class Plan:
    """The rewrites a rename needs, or why it is refused, with counts per kind for the report."""

    changes: list = field(default_factory=list)
    refusals: collections.Counter = field(default_factory=collections.Counter)
    counts: dict = field(default_factory=lambda: collections.defaultdict(collections.Counter))
    markers: collections.Counter = field(default_factory=collections.Counter)


def _revision_bound(rows):
    highest = 0
    for (kind, _), (document, _) in rows.links.items():
        if kind in ("pr_source", "pr_head"):
            revision = json.loads(document).get("event", {}).get("head_revision", 0)
            highest = max(highest, revision if isinstance(revision, int) else 0)
    return highest + REVISION_MARGIN


def _rewrite(kind, key, document, data, ctx):
    """Return the RowChange for one record of the old identity, or raise Unverifiable."""
    if ctx.old.mapping in document:
        raise Unverifiable("document embeds the old mapping hash")
    if naming_paths(data, ctx.old) - IDENTITY_PATHS.get(kind, set()):
        raise Unverifiable("document names the old helper in a field this kind does not store")
    new_key, new_data = HANDLERS[kind](kind, key, data, ctx)
    if new_data is None:
        return RowChange("links", kind, key, new_key, document, document)
    if _json_object(data, MAX_LINK_BYTES) != document:
        raise Unverifiable("document is not in the store's encoding, so a rewrite would change other bytes")
    try:
        new_document = _json_object(new_data, MAX_LINK_BYTES)
    except ValueError:
        raise Unverifiable("rewritten document exceeds the store's size limit") from None
    if naming_paths(new_data, ctx.old) or ctx.old.mapping in new_document:
        raise Unverifiable("rewritten document still names the old helper")
    return RowChange("links", kind, key, new_key, document, new_document)


def plan_rename(rows, old, new):
    """Classify every row, verify each record of the old identity, and return its rewrite or the refusal reasons."""
    plan = Plan()
    ctx = _Context(old, new, rows, _revision_bound(rows))
    bound_old = AutomationService(None, settings=lambda: old.settings, ai=object(), transport=object())._bound

    for case_id, row in sorted(rows.cases.items()):
        document = row["document"]
        case = json.loads(document)
        plan.counts["cases"]["rows"] += 1
        plan.counts["cases"]["new"] += bool(naming_paths(case, new)) or new.mapping in document
        if not naming_paths(case, old) and old.mapping not in document:
            continue
        plan.counts["cases"]["old"] += 1
        if naming_paths(case, old) != {("bot_name",)} or old.mapping in document:
            plan.refusals[("cases", "case names the old helper outside its bot_name field")] += 1
        elif not bound_old(case):
            plan.refusals[("cases", "case names the old helper but the old settings do not bind it")] += 1
        elif _json_object(case, MAX_DOCUMENT_BYTES) != document:
            plan.refusals[("cases", "case document is not in the store's encoding")] += 1
        else:
            ctx.old_cases.add(case_id)
            plan.changes.append(RowChange("cases", "cases", case_id, case_id, document,
                                          _json_object({**case, "bot_name": new.name}, MAX_DOCUMENT_BYTES)))

    other_receipts, new_receipts, attempts = set(), set(), []
    for (kind, key), (document, _) in sorted(rows.links.items()):
        plan.counts[kind]["rows"] += 1
        if kind not in KNOWN_KINDS:
            plan.refusals[(kind, "unknown record kind; cannot tell whether it names the helper")] += 1
            continue
        if kind == "pr_activity_attempt":
            attempts.append((key, document))
            continue
        data = json.loads(document)
        names_new = key_names(key, new) or new.mapping in document or bool(naming_paths(data, new))
        names_old = key_names(key, old) or old.mapping in document or bool(naming_paths(data, old))
        plan.counts[kind]["new"] += names_new
        if kind in ("pr_activity", "pr_activity_delivery") and not names_old:
            receipt = key if kind == "pr_activity" else key[:-len(str(data.get("source_version"))) - 1]
            other_receipts.add(receipt)
            if names_new:
                new_receipts.add(receipt)
        if not names_old:
            continue
        plan.counts[kind]["old"] += 1
        if kind not in HANDLERS:
            plan.refusals[(kind, "names the old helper, but this kind has no rewrite")] += 1
            continue
        try:
            plan.changes.append(_rewrite(kind, key, document, data, ctx))
        except Unverifiable as exc:
            plan.refusals[(kind, str(exc))] += 1
        except Exception as exc:
            plan.refusals[(kind, f"verification failed with {type(exc).__name__}")] += 1

    for key, document in attempts:
        data = json.loads(document)
        if naming_paths(data, old) or naming_paths(data, new) or old.mapping in document:
            plan.refusals[("pr_activity_attempt", "attempt document names a helper")] += 1
        elif key in ctx.receipts:
            plan.counts["pr_activity_attempt"]["old"] += 1
            plan.changes.append(RowChange("links", "pr_activity_attempt", key, ctx.receipts[key], document, document))
        elif key in other_receipts:
            plan.counts["pr_activity_attempt"]["new"] += key in new_receipts
        else:
            plan.refusals[("pr_activity_attempt", "attempt matches no stored receipt, so its helper is unknown")] += 1

    old_texts = [old.mapping] + [old.mirror_prefix(kind) for kind in MIRROR_CHANNELS
                                 if old.settings[MIRROR_CHANNELS[kind]]]
    for job in rows.jobs:
        key, payload = job["key"], job["payload"]
        if (naming_paths(json.loads(payload), old) or any(text in key or text in payload for text in old_texts)
                or any(receipt in key or receipt in payload for receipt in ctx.receipts)):
            plan.refusals[("jobs", "a job still carries the old identity; run Clear history and queue first")] += 1

    targets = collections.Counter((change.kind, change.new_key) for change in plan.changes
                                  if change.table == "links" and change.new_key != change.key)
    for (kind, new_key), count in targets.items():
        if count > 1:
            plan.refusals[(kind, "several records would move to one new key")] += count
        if (kind, new_key) in rows.links:
            plan.refusals[(kind, "the new key already exists")] += 1
    plan.counts["jobs"]["rows"] = len(rows.jobs)
    plan.markers = ctx.markers
    return plan


@dataclass
class Report:
    verdict: str
    reasons: list = field(default_factory=list)
    before: Plan | None = None
    after: Plan | None = None


def _reasons(plan):
    return [f"{kind}: {count} record(s): {reason}." for (kind, reason), count in sorted(plan.refusals.items())]


def rename_helper(store, raw_settings, old_name, new_name, *, apply):
    """Plan the move of every record of old_name to new_name and, with apply=True, write it.

    The report's verdict is "refused", "nothing", "dry-run" or "applied"; only "applied" wrote.
    AutomationStore.rewrite_records raises RuntimeError and rolls the rename back when the
    written result fails a check.
    """
    try:
        old, new = identities(raw_settings, old_name, new_name)
    except Refused as exc:
        return Report("refused", exc.reasons)
    before, after = store.rewrite_records(lambda rows: plan_rename(rows, old, new), apply=apply)
    if before.refusals:
        return Report("refused", _reasons(before), before)
    if not before.changes:
        return Report("nothing", before=before)
    return Report("applied", before=before, after=after) if apply else Report("dry-run", before=before)


def _counts_table(plan, projected=False):
    kinds = sorted(set(plan.counts) - {"cases", "jobs"}) + ["cases", "jobs"]
    rewrites = collections.Counter((change.kind, change.key != change.new_key, change.document != change.new_document)
                                   for change in plan.changes)
    lines = [f"  {'kind':<24}{'rows':>6}{'old':>6}{'new':>6}{'keys':>7}{'docs':>7}"]
    for kind in kinds:
        count = plan.counts[kind]
        old, new = count["old"], count["new"]
        if projected:
            old, new = 0, new + old
        keys = sum(n for (k, moved, _), n in rewrites.items() if k == kind and moved)
        docs = sum(n for (k, _, rewritten), n in rewrites.items() if k == kind and rewritten)
        lines.append(f"  {kind:<24}{count['rows']:>6}{old:>6}{new:>6}{keys:>7}{docs:>7}")
    return lines


def format_report(report, old_name, new_name, apply):
    """Return the report as text lines: counts per kind, any refusal reasons, and the verdict, never record contents."""
    mode = "apply" if apply else "dry run, read only"
    lines = [f'Project helper rename "{old_name}" -> "{new_name}" ({mode})']
    if report.before is not None:
        lines += ["Before (rows; records naming the old and the new helper; keys and documents to rewrite):"]
        lines += _counts_table(report.before)
    if report.verdict == "dry-run":
        lines += ["After, projected:"] + _counts_table(report.before, projected=True)
    if report.after is not None:
        lines += ["After:"] + _counts_table(report.after)
    if report.before is not None and report.before.markers and report.verdict in ("dry-run", "applied"):
        lines.append("Stored footers: " + ", ".join(f"{name} {count}" for name, count in
                                                    sorted(report.before.markers.items())))
    total = len(report.before.changes) if report.before is not None else 0
    lines += [f"REFUSED: {reason}" for reason in report.reasons]
    lines.append({
        "refused": "VERDICT: REFUSED. Nothing was changed.",
        "nothing": f'VERDICT: NOTHING TO DO. No record names "{old_name}". Nothing was changed.',
        "dry-run": f"VERDICT: DRY RUN OK. {total} records would be rewritten. Nothing was changed; "
                   "rerun with --apply to write.",
        "applied": f"VERDICT: APPLIED. {total} records rewritten in one transaction and verified.",
    }[report.verdict])
    return lines
