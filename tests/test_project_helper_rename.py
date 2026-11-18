"""rename-helper moves every stored record of one helper bot name to another, and nothing else."""

import ast
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace

import pytest

from project_automation import AutomationService, mirror_key, mirror_record
from project_automation_activity import (
    activate, activity_receipt_key, head_key, mapping_key, marker_prefix, source_key,
)
from project_automation_config import normalize_project_config
import project_automation_rename as rename
from project_automation_store import MAX_DOCUMENT_BYTES, MAX_LINK_BYTES, AutomationStore, _json_object
from project_automation_webhook import webhook_binding


ROOT = Path(__file__).resolve().parents[1]
OLD, NEW, OTHER = "Old Helper", "New Helper", "Firefly"
DAY = 86400


def settings(**changes):
    return normalize_project_config({
        "enabled": False, "bot_name": OLD, "guild_id": "100", "repository": "example/project",
        "feedback_channel_id": "101", "issues_channel_id": "201", "reviews_channel_id": "202",
        "commits_channel_id": "203", "github_channel_id": "204", "character_name": OLD, **changes,
    })


def item(kind, number):
    return {"kind": kind, "number": number, "updated_at": "2026-11-01T00:00:00Z", "state": "open",
            "title": f"Item {number}", "body": "Private body text", "merged": False, "draft": False,
            "html_url": f"https://github.com/example/project/issues/{number}"}


def put(connection, kind, key, data, updated_at):
    connection.execute("INSERT INTO links(kind, key, document, updated_at) VALUES (?, ?, ?, ?)",
                       (kind, key, _json_object(data, MAX_LINK_BYTES), updated_at))


def put_case(connection, cfg, case_id, now):
    case = {"id": case_id, "source_key": f"discord:100:{case_id}", "revision": 3, "created_at": now - DAY,
            "updated_at": now - DAY, "decisions": [], "workflow_version": 2,
            **{name: cfg[name] for name in ("repository", "bot_name", "guild_id", "feedback_channel_id")},
            "channel_id": "301", "reporter_id": "19", "state": "discussing",
            "gate": {"kind": "conversation", "options": [{"key": "write", "label": "Write my report"}]},
            "transcript": [{"role": "user", "content": "Private report text", "created_at": now - DAY}]}
    connection.execute(
        "INSERT INTO cases(id, source_key, channel_id, reporter_id, state, updated_at, document) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, case["source_key"], "301", "19", "discussing", now - DAY, _json_object(case, MAX_DOCUMENT_BYTES)))
    return case


def seed_identity(connection, cfg, now, *, case_id, job_id):
    """Write one helper's records in the shapes the helper writes them, with old and recent update times."""
    mapping, binding, old_age = mapping_key(cfg), webhook_binding(cfg), now - 10 * DAY
    put(connection, "pr_activation", mapping, {"since": old_age}, old_age)
    put(connection, "project_status", mapping, {"last_sync_at": now}, now)
    put(connection, "pr_discovery_checkpoint", mapping, {"completed_through": now}, now)
    put(connection, "pr_discovery_page", f"{mapping}:9", {"cursor": None, "complete": True}, now)
    put(connection, "pr_walk", f"{mapping}:17", {"updated_at": item("pull", 17)["updated_at"], "queued_at": now}, now)
    for source in ("comment", "commits"):
        put(connection, "pr_checkpoint", f"{mapping}:17:{source}", {"cycle": 9, "cursor": None, "complete": True}, now)
    put(connection, "pr_missing_checkpoint", f"{mapping}:17:comment", {"cycle": 9, "cursor": "", "complete": True}, now)
    comment = {"kind": "pr_activity", "activity_type": "comment", "number": 17, "key": "pr:17:comment:42",
               "source_version": "v1", "repository": cfg["repository"], "body": "Private comment", "_binding": binding}
    commits = {"kind": "pr_activity", "activity_type": "commits", "number": 17, "key": "pr:17:commits",
               "after": "b" * 40, "head_revision": 3, "delivery_key": "pr:17:commits:head:3",
               "source_version": "v3", "repository": cfg["repository"], "_binding": binding}
    put(connection, "pr_source", source_key(cfg, comment), {"version": "v1", "event": comment, "cycle": 9}, now)
    put(connection, "pr_source", source_key(cfg, commits), {"version": "v3", "event": commits, "cycle": 9}, now)
    put(connection, "pr_head", head_key(cfg, commits), {"version": "v3", "event": commits, "cycle": 9}, now)
    for message_id, event in ((401, comment), (402, commits)):
        receipt = activity_receipt_key(cfg, event)
        record = {"channel_id": "302", "message_id": str(message_id), "source_version": event["source_version"],
                  "source_key": event["key"], "number": 17, "activity_type": event["activity_type"],
                  "forum_id": cfg["reviews_channel_id"], "repository": cfg["repository"],
                  "guild_id": cfg["guild_id"], "bot_name": cfg["bot_name"],
                  "jump_url": f"https://discord.com/channels/100/302/{message_id}"}
        put(connection, "pr_activity", receipt, record, now)
        put(connection, "pr_activity_delivery", f"{receipt}:{event['source_version']}", record, now)
        put(connection, "pr_activity_attempt", receipt, {"state": "delivered", "source_version": event["source_version"]}, now)
    for kind, event_key, number, updated_at in (
            ("issue", "issue:7", 7, now), ("pull", "pull:17", 17, now), ("commit", "push:" + "a" * 24, 0, old_age),
            ("general", "release:5", 0, now), ("general", "workflow_run:6", 0, old_age)):
        key = mirror_key(cfg, kind, event_key)
        put(connection, "mirror", key, {"channel_id": "302", "message_id": "302", "kind": kind, "number": number,
                                        "repository": cfg["repository"], "guild_id": cfg["guild_id"],
                                        "bot_name": cfg["bot_name"], "digest": "d" * 64}, updated_at)
        put(connection, "mirror_attempt", key, {"state": "delivered"}, updated_at)
        if kind in ("issue", "pull"):
            put(connection, "item_mirrored", key, {**mirror_record(item(kind, number)), "mirrored_at": now}, now)
    put_case(connection, cfg, case_id, now)
    put(connection, "notification", f"{case_id}:55", {
        "id": case_id, "revision": 3, "bot_name": cfg["bot_name"], "guild_id": cfg["guild_id"], "channel_id": "301",
        "reporter_id": "19", "repository": cfg["repository"], "state": "discussing", "reply": "Private reply",
        "delivery_key": f"{case_id}:55", "prepared_at": now}, now)
    put(connection, "pr_prepared", str(job_id), {**comment, "pull": {"number": 17, "state": "open"}}, now)


def seed(path, now=None):
    """Build a database holding the old helper's records, another helper's records on the same channels, and shared rows."""
    now = time.time() if now is None else now
    store = AutomationStore(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        seed_identity(connection, settings(), now, case_id="a" * 32, job_id=88)
        seed_identity(connection, settings(bot_name=OTHER), now, case_id="b" * 32, job_id=89)
        put(connection, "conversation", "a" * 32, {"turns": [{"role": "assistant", "content": "Hi",
                                                              "author_name": OLD, "created_at": now}]}, now)
        put(connection, "feedback", "a" * 32, {"channel_id": "301", "message_id": "501", "revision": 3,
                                               "delivery_key": "a" * 32 + ":55", "created_at": now}, now)
        put(connection, "feedback_delivery", "a" * 32 + ":55", {"channel_id": "301", "message_id": "501"}, now)
        put(connection, "case_status", "a" * 32, {"state": "open", "url": "https://github.com/example/project/issues/7"}, now)
        put(connection, "maintainer_question", "a" * 32, {}, now)
        put(connection, "question_notice", "a" * 32 + ":comment:9", {}, now)
        put(connection, "github_delivery", hashlib.sha256(b"old delivery").hexdigest(), {}, now - 5 * DAY)
        put(connection, "github_delivery", hashlib.sha256(b"new delivery").hexdigest(), {}, now)
        put(connection, "job_dismissal", "12", {"actor": "dashboard", "dismissed_at": now, "updated_at": now}, now)
        put(connection, "item_closed", "example/project:issue:3", {"closed_at": now - 2 * DAY}, now)
        put(connection, "pr_discovery_cycle", "9", {"since": now - DAY, "started_at": now}, now)
        connection.execute("INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
                           "VALUES ('assess', 'assess:aaaa:4', ?, 'pending', ?, ?, ?)",
                           (json.dumps({"case_id": "a" * 32, "revision": 3}), now, now, now))
        connection.execute("COMMIT")
    return store


def dump(store):
    with closing(sqlite3.connect(store.path)) as connection:
        return "\n".join(connection.iterdump())


def rows(store):
    with closing(sqlite3.connect(store.path)) as connection:
        links = {(kind, key): (document, updated_at) for kind, key, document, updated_at
                 in connection.execute("SELECT kind, key, document, updated_at FROM links")}
        cases = {row[0]: row for row in connection.execute("SELECT * FROM cases")}
        return links, cases, connection.execute("PRAGMA integrity_check").fetchall()


def names(data_or_key, name):
    """Return whether a key or decoded document names the helper `name` the way the helper records it."""
    if isinstance(data_or_key, str):
        server, _, rest = data_or_key.partition(":")
        return server.isdigit() and rest.startswith(name + ":")
    return any(path[-1] in ("bot_name", "_binding") for path, _ in rename._identity_fields(data_or_key)
               if _ == name or (isinstance(_, dict) and _.get("bot_name") == name))


def old_identity_rows(links, cfg):
    mapping = mapping_key(cfg)
    receipts = {key for (kind, key), (document, _) in links.items()
                if kind == "pr_activity" and json.loads(document).get("bot_name") == cfg["bot_name"]}
    return {item for item, (document, _) in links.items()
            if mapping in item[1] or mapping in document or names(item[1], cfg["bot_name"])
            or names(json.loads(document), cfg["bot_name"])
            or (item[0] == "pr_activity_attempt" and item[1] in receipts)}


@pytest.fixture
def seeded(tmp_path):
    store = seed(tmp_path / "project.sqlite3")
    return SimpleNamespace(store=store, before=rows(store), dump=dump(store))


def run(store, cfg=None, old=OLD, new=NEW, apply=True):
    return rename.rename_helper(store, settings() if cfg is None else cfg, old, new, apply=apply)


def test_rename_moves_every_record_of_the_old_name_and_leaves_every_other_row_byte_identical(seeded):
    report = run(seeded.store)
    assert report.verdict == "applied"
    (before_links, before_cases, _), (after_links, after_cases, integrity) = seeded.before, rows(seeded.store)
    assert integrity == [("ok",)]
    assert Counter(kind for kind, _ in before_links) == Counter(kind for kind, _ in after_links)
    assert len(before_cases) == len(after_cases)
    moved = old_identity_rows(before_links, settings())
    # seed_identity writes 31 link rows and one case for each helper.
    assert len(moved) == 31 and len(report.before.changes) == len(moved) + 1
    for item, value in before_links.items():
        if item not in moved:
            assert after_links[item] == value
    other = old_identity_rows(before_links, settings(bot_name=OTHER))
    assert other and all(after_links[item] == before_links[item] for item in other)
    assert after_cases["b" * 32] == before_cases["b" * 32]
    assert not old_identity_rows(after_links, settings())
    for (kind, _), (document, _) in after_links.items():
        if OLD in document:
            data = json.loads(document)
            assert (kind == "mirror" and OLD in data["marker"]) or (kind == "conversation" and data["turns"][0]["author_name"] == OLD)


def test_rename_keeps_update_times_so_retention_deletes_the_same_records(tmp_path):
    now = time.time()
    renamed, untouched = seed(tmp_path / "renamed.sqlite3", now), seed(tmp_path / "untouched.sqlite3", now)
    seeded_rows = set(rows(untouched)[0])
    report = run(renamed)
    moves = {(change.kind, change.key): (change.kind, change.new_key) for change in report.before.changes}
    for store in (renamed, untouched):
        while store.prune_links(0) or store.prune_jobs(0):
            pass
    kept = set(rows(untouched)[0])
    assert ("mirror", mirror_key(settings(), "commit", "push:" + "a" * 24)) in seeded_rows - kept
    assert {moves.get(item, item) for item in kept} == set(rows(renamed)[0])


def test_renamed_mapping_records_are_found_under_the_new_mapping(seeded):
    store, old, new = seeded.store, settings(), settings(bot_name=NEW)
    expected = {kind: store.get_link(kind, mapping_key(old))
                for kind in ("pr_activation", "project_status", "pr_discovery_checkpoint")}
    run(store)
    for kind, value in expected.items():
        assert value is not None and store.get_link(kind, mapping_key(new)) == value
        assert store.get_link(kind, mapping_key(old)) is None
    assert activate(store, new) == expected["pr_activation"]
    assert len(store.list_links("pr_activation")) == 2
    for suffix in ("17:comment", "17:commits"):
        assert store.get_link("pr_checkpoint", f"{mapping_key(new)}:{suffix}") is not None
    assert store.get_link("pr_missing_checkpoint", f"{mapping_key(new)}:17:comment") is not None
    assert store.get_link("pr_discovery_page", f"{mapping_key(new)}:9") is not None


def test_renamed_posts_are_found_with_the_new_settings_and_store_the_footer_they_show(seeded):
    store, old, new = seeded.store, settings(), settings(bot_name=NEW)
    in_scope = lambda cfg: sum(1 for link in store.list_links("mirror") if link["data"].get("kind") in {"issue", "pull"}
                               and all(link["data"].get(name) == cfg[name] for name in ("repository", "guild_id", "bot_name")))
    assert in_scope(old) == 2
    run(store)
    assert in_scope(new) == 2 and in_scope(old) == 0
    for kind, event_key in (("issue", "issue:7"), ("pull", "pull:17"), ("commit", "push:" + "a" * 24),
                            ("general", "release:5"), ("general", "workflow_run:6")):
        record = store.get_link("mirror", mirror_key(new, kind, event_key))
        assert record["bot_name"] == NEW and record["marker"] == "Project " + mirror_key(old, kind, event_key)
        assert store.get_link("mirror_attempt", mirror_key(new, kind, event_key)) == {"state": "delivered"}
    for kind, number in (("issue", 7), ("pull", 17)):
        assert store.get_link("item_mirrored", mirror_key(new, kind, f"{kind}:{number}")) is not None


def test_renamed_sources_and_bindings_pass_the_helpers_own_checks(seeded):
    store, old, new = seeded.store, settings(), settings(bot_name=NEW)
    strip = lambda records: sorted(json.dumps({**record["data"], "event": {**record["data"]["event"], "_binding": None}},
                                              sort_keys=True) for record in records)
    before = {source: strip(store.list_pr_sources(mapping_key(old), 17, source)) for source in ("comment", "commits")}
    run(store)
    service = AutomationService(store, settings=lambda: new, ai=object(), transport=object())
    events = []
    for source in ("comment", "commits"):
        records = store.list_pr_sources(mapping_key(new), 17, source)
        assert strip(records) == before[source] and len(records) == 1
        events += [record["data"]["event"] for record in records]
    events.append(store.get_link("pr_head", head_key(new, {"number": 17}))["event"])
    for event in events:
        assert event["_binding"] == webhook_binding(new)
        service._check_event_binding(event)
    assert store.get_link("pr_prepared", "88")["_binding"] == webhook_binding(new)


def test_renamed_receipts_are_found_from_the_stored_events_and_keep_their_message_prefix(seeded):
    store, old, new = seeded.store, settings(), settings(bot_name=NEW)
    sources = [record["data"]["event"] for source in ("comment", "commits")
               for record in store.list_pr_sources(mapping_key(old), 17, source)]
    before = {event["key"]: store.get_link("pr_activity", activity_receipt_key(old, event)) for event in sources}
    run(store)
    for event in sources:
        receipt = store.get_link("pr_activity", activity_receipt_key(new, event))
        assert {**receipt, "bot_name": OLD, "marker_prefix": None} == {**before[event["key"]], "marker_prefix": None}
        assert receipt["marker_prefix"] == marker_prefix(activity_receipt_key(old, event))
        version = event["source_version"]
        assert store.get_link("pr_activity_delivery", f"{activity_receipt_key(new, event)}:{version}")["bot_name"] == NEW
        assert store.get_link("pr_activity_attempt", activity_receipt_key(new, event))["source_version"] == version


def test_the_same_feedback_cases_stay_bound_with_their_revisions_and_controls(seeded):
    store, old, new = seeded.store, settings(), settings(bot_name=NEW)
    bound = lambda cfg: {case["id"] for case in store.list_cases()
                         if AutomationService(store, settings=lambda: cfg, ai=object(), transport=object())._bound(case)}
    assert bound(old) == {"a" * 32}
    run(store)
    assert bound(new) == {"a" * 32} and not bound(old)
    pending = {case["id"]: case for case in store.list_pending_cases()}
    assert pending["a" * 32]["bot_name"] == NEW and pending["a" * 32]["gate"]
    after_cases = rows(store)[1]
    for case_id, row in seeded.before[1].items():
        assert after_cases[case_id][:-1] == row[:-1]
        assert json.loads(after_cases[case_id][-1])["revision"] == json.loads(row[-1])["revision"]
    assert store.get_link("notification", "a" * 32 + ":55")["bot_name"] == NEW
    assert store.get_link("notification", "b" * 32 + ":55")["bot_name"] == OTHER


def test_the_sync_root_queues_no_refresh_or_walk_for_renamed_items(seeded):
    store, new = seeded.store, settings(bot_name=NEW)
    run(store)
    service = AutomationService(store, settings=lambda: new, ai=object(), transport=object())
    changed, daily = service._refreshes([("issue", item("issue", 7)), ("pull", item("pull", 17))])
    assert (changed, daily) == ([], [])
    assert service.pr_activity.enqueue_walks([item("pull", 17)], 9) == (0, 0)


def test_existing_stored_footers_are_never_overwritten(seeded):
    store, old = seeded.store, settings()
    earlier = {"marker": "Project 100:Earlier Helper:201:example/project:issue:7"}
    store.put_link("mirror", mirror_key(old, "issue", "issue:7"),
                   {**store.get_link("mirror", mirror_key(old, "issue", "issue:7")), **earlier})
    comment = {"kind": "pr_activity", "key": "pr:17:comment:42"}
    store.put_link("pr_activity", activity_receipt_key(old, comment),
                   {**store.get_link("pr_activity", activity_receipt_key(old, comment)), "marker_prefix": "PR activity 0 "})
    report = run(store)
    new = settings(bot_name=NEW)
    assert store.get_link("mirror", mirror_key(new, "issue", "issue:7"))["marker"] == earlier["marker"]
    assert store.get_link("pr_activity", activity_receipt_key(new, comment))["marker_prefix"] == "PR activity 0 "
    assert report.before.markers == Counter({"post footers added": 4, "post footers kept": 1,
                                             "activity prefixes added": 1, "activity prefixes kept": 1})


def test_a_dry_run_writes_nothing_and_a_second_run_finds_nothing_to_do(seeded):
    dry = run(seeded.store, apply=False)
    assert dry.verdict == "dry-run" and dump(seeded.store) == seeded.dump
    applied = run(seeded.store)
    assert applied.verdict == "applied" and len(applied.before.changes) == len(dry.before.changes)
    after = dump(seeded.store)
    for apply in (False, True):
        assert run(seeded.store, apply=apply).verdict == "nothing"
        assert dump(seeded.store) == after


def test_renaming_back_restores_every_row_and_keeps_the_stored_footers(seeded):
    run(seeded.store)
    assert run(seeded.store, settings(bot_name=NEW), NEW, OLD).verdict == "applied"
    (before_links, before_cases, _), (after_links, after_cases, _) = seeded.before, rows(seeded.store)
    assert set(after_links) == set(before_links) and after_cases == before_cases
    for item, (document, updated_at) in before_links.items():
        restored = json.loads(after_links[item][0])
        if item[0] == "mirror" and restored["bot_name"] == OLD:
            assert restored.pop("marker") == "Project " + item[1]
        if item[0] == "pr_activity" and restored["bot_name"] == OLD:
            assert restored.pop("marker_prefix") == marker_prefix(item[1])
        assert (_json_object(restored, MAX_LINK_BYTES), after_links[item][1]) == (document, updated_at)


def plant_job(connection, key, payload, state="pending"):
    connection.execute("INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
                       "VALUES ('event', ?, ?, ?, 1, 1, 1)", (key, json.dumps(payload), state))


def change_case(connection, **changes):
    document = connection.execute("SELECT document FROM cases WHERE id = ?", ("a" * 32,)).fetchone()[0]
    connection.execute("UPDATE cases SET document = ? WHERE id = ?",
                       (_json_object({**json.loads(document), **changes}, MAX_DOCUMENT_BYTES), "a" * 32))


def stale_source_binding(connection):
    key, document = connection.execute("SELECT key, document FROM links WHERE kind = 'pr_source' AND substr(key, 1, 64) = ?",
                                       (mapping_key(settings()),)).fetchone()
    data = json.loads(document)
    data["event"]["_binding"]["commits_channel_id"] = "1"
    connection.execute("UPDATE links SET document = ? WHERE kind = 'pr_source' AND key = ?",
                       (_json_object(data, MAX_LINK_BYTES), key))


def set_guard(connection, kind, key, state):
    connection.execute("UPDATE links SET document = ? WHERE kind = ? AND key = ?",
                       (_json_object({"state": state, "source_version": "v2"}, MAX_LINK_BYTES), kind, key))


COMMENT = {"kind": "pr_activity", "key": "pr:17:comment:42"}
NEW_CFG = settings(bot_name=NEW)
REFUSALS = {
    "wrong --from": ({}, None, "Old Helpe", NEW, "different helper bot"),
    "--from names another helper": ({}, None, OTHER, NEW, "different helper bot"),
    "settings already select the new name": ({"bot_name": NEW}, None, OLD, NEW, "different helper bot"),
    "helper enabled": ({"enabled": True}, None, OLD, NEW, "is enabled"),
    "--to equals --from": ({}, None, OLD, OLD, "same as --from"),
    "activation exists under the new mapping": (
        {}, lambda c: put(c, "pr_activation", mapping_key(NEW_CFG), {"since": 1}, 1), OLD, NEW, "new key already exists"),
    "issue post exists under the new key": (
        {}, lambda c: put(c, "mirror", mirror_key(NEW_CFG, "issue", "issue:7"), {}, 1), OLD, NEW, "new key already exists"),
    "job with the old binding": (
        {}, lambda c: plant_job(c, "github:queued", {"kind": "issue", "_binding": webhook_binding(settings())}),
        OLD, NEW, "Clear history and queue"),
    "failed job keyed by the old mapping": (
        {}, lambda c: plant_job(c, f"sync:{mapping_key(settings())}:1", {}, "failed"), OLD, NEW, "Clear history and queue"),
    "source with a stale binding": ({}, stale_source_binding, OLD, NEW, "stored binding differs"),
    "post on a channel the settings do not use": (
        {}, lambda c: put(c, "mirror", f"100:{OLD}:999:example/project:issue:9",
                          {"kind": "issue", "number": 9, "bot_name": OLD, "guild_id": "100",
                           "repository": "example/project"}, 1), OLD, NEW, "key differs from the post key"),
    "unknown record kind": ({}, lambda c: put(c, "future_kind", "x", {"bot_name": OLD}, 1), OLD, NEW, "unknown record kind"),
    "old name in a kind without identity": (
        {}, lambda c: put(c, "feedback", "planted", {"bot_name": OLD}, 1), OLD, NEW, "this kind has no rewrite"),
    "unconfirmed activity send without a receipt": (
        {}, lambda c: put(c, "pr_activity_attempt", "pr:" + "0" * 64, {"state": "pending"}, 1), OLD, NEW,
        "its helper is unknown; resolve that delivery first"),
    "unconfirmed activity send of the old name": (
        {}, lambda c: set_guard(c, "pr_activity_attempt", activity_receipt_key(settings(), COMMENT), "pending"),
        OLD, NEW, "unconfirmed; resolve that delivery"),
    "unconfirmed post send of the old name": (
        {}, lambda c: set_guard(c, "mirror_attempt", mirror_key(settings(), "issue", "issue:7"), "pending"),
        OLD, NEW, "unconfirmed; resolve that delivery"),
    "old case the settings do not bind": (
        {}, lambda c: change_case(c, feedback_channel_id="1"), OLD, NEW, "do not bind it"),
}


@pytest.mark.parametrize("scenario", REFUSALS)
def test_rename_refuses_and_changes_nothing(tmp_path, scenario):
    changes, plant, old, new, reason = REFUSALS[scenario]
    store = seed(tmp_path / "project.sqlite3")
    if plant:
        with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            plant(connection)
            connection.execute("COMMIT")
    before = dump(store)
    report = run(store, settings(**changes), old, new)
    assert report.verdict == "refused"
    assert any(reason in line for line in report.reasons), report.reasons
    assert dump(store) == before
    lines = rename.format_report(report, old, new, True)
    assert lines[-1] == "VERDICT: REFUSED. Nothing was changed."
    assert not any("Private" in line for line in lines)


def test_a_failure_part_way_through_the_writes_rolls_everything_back(seeded, monkeypatch):
    real = AutomationStore._apply_changes

    def half(connection, changes):
        real(connection, changes[:len(changes) // 2])
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(AutomationStore, "_apply_changes", staticmethod(half))
    with pytest.raises(RuntimeError, match="simulated failure"):
        run(seeded.store)
    assert dump(seeded.store) == seeded.dump


def test_a_written_result_that_fails_its_checks_is_rolled_back(seeded):
    old, new = rename.identities(settings(), OLD, NEW)
    plans = []

    def plan(rows):
        plans.append(rename.plan_rename(rows, old, new))
        return plans[0]

    with pytest.raises(RuntimeError, match="old identity remain"):
        seeded.store.rewrite_records(plan, apply=True)
    assert dump(seeded.store) == seeded.dump


def test_the_report_holds_counts_and_a_verdict_but_no_record_contents(seeded):
    lines = rename.format_report(run(seeded.store, apply=False), OLD, NEW, False)
    assert lines[-1].startswith("VERDICT: DRY RUN OK. 32 records would be rewritten.")
    text = "\n".join(lines)
    assert "mirror" in text and "Stored footers: activity prefixes added 2, post footers added 5" in text
    for private in ("Private", "302", mapping_key(settings()), "jump_url"):
        assert private not in text


def _literal_kind(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def written_link_kinds():
    """Return every link kind the helper's modules write, read from their source."""
    kinds = set()
    for path in sorted(ROOT.glob("project_automation*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                first = node.args[0]
                if node.func.attr in ("put_link", "ensure_link"):
                    kinds.add(_literal_kind(first) or f"{path.name}:{node.lineno} writes a kind that is not a literal")
                elif node.func.attr == "save_links_with_jobs" or (
                        node.func.attr == "append" and getattr(node.func.value, "id", None) == "links"):
                    entries = first.elts if isinstance(first, ast.List) else [first]
                    kinds.update(_literal_kind(entry.elts[0]) for entry in entries
                                 if isinstance(entry, ast.Tuple) and _literal_kind(entry.elts[0]))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                kinds.update(re.findall(r"INTO links\b[^;]*?(?:VALUES\s*\(|SELECT\s+)'([a-z_]+)'", node.value))
    return kinds


def test_the_planner_knows_every_link_kind_the_helper_writes():
    # A kind the planner does not know makes every rename refuse, and a kind it knows but
    # classifies wrongly could leave records of the old name behind.
    assert written_link_kinds() == rename.KNOWN_KINDS
