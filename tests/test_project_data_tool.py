"""The operator command-line tool drives the same store functions as the dashboard."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from project_automation import AutomationService
from project_automation_config import PROJECT_DEFAULTS
from project_automation_store import AutomationStore


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_data", ROOT / "tools" / "project_data.py")
project_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project_data)


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "runtime_config", SimpleNamespace(get=lambda key, default=None: default))
    settings = {**PROJECT_DEFAULTS, "enabled": False}
    store = AutomationStore(tmp_path / "project.sqlite3")
    return SimpleNamespace(service=AutomationService(store, settings=lambda: settings), settings=settings, store=store)


def set_state(store, job_id, state):
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job_id))


def job_count(store):
    with closing(sqlite3.connect(store.path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_status_prints_counts_without_payload_text(tool, capsys):
    tool.store.enqueue("event", {"body": "private payload text"}, "github:queued")
    held = tool.store.enqueue("event", {"body": "private payload text"}, "github:held")
    set_state(tool.store, held, "recovery")
    assert project_data.run(["status"], tool.service) == 0
    output = capsys.readouterr().out
    assert "1 pending" in output and "1 held" in output and "MB" in output
    assert "private payload text" not in output


def test_clear_history_keeps_jobs_in_progress(tool, capsys):
    tool.store.enqueue("event", {}, "queued")
    sending = tool.store.enqueue("event", {}, "sending")
    set_state(tool.store, sending, "inflight")
    assert project_data.run(["clear-history"], tool.service) == 0
    assert "Deleted 1 job" in capsys.readouterr().out
    assert job_count(tool.store) == 1 and tool.store.get_job(sending)["state"] == "inflight"


def test_reset_refuses_while_enabled_on_a_wrong_name_and_with_a_job_in_flight(tool, capsys):
    busy = tool.store.enqueue("event", {}, "busy")
    tool.settings["enabled"] = True
    assert project_data.run(["reset", "--confirm", "SillyBunnyTeam/SillyBunny"], tool.service) == 1
    assert "is enabled" in capsys.readouterr().err
    tool.settings["enabled"] = False
    assert project_data.run(["reset", "--confirm", "SillyBunnyTeam/Other"], tool.service) == 1
    assert "does not match" in capsys.readouterr().err
    set_state(tool.store, busy, "running")
    assert project_data.run(["reset", "--confirm", "SillyBunnyTeam/SillyBunny"], tool.service) == 1
    assert "in progress" in capsys.readouterr().err
    assert job_count(tool.store) == 1
    with pytest.raises(SystemExit):
        project_data.run(["reset"], tool.service)


def test_reset_deletes_all_project_data_with_the_exact_name(tool, capsys):
    tool.store.enqueue("event", {}, "queued")
    tool.store.put_link("mirror", "post", {"channel_id": "1"})
    assert project_data.run(["reset", "--confirm", "SillyBunnyTeam/SillyBunny"], tool.service) == 0
    assert "jobs: 1, records: 1, feedback cases: 0" in capsys.readouterr().out
    assert job_count(tool.store) == 0 and tool.store.get_link("mirror", "post") is None


def test_compact_reports_whether_it_ran(tool, capsys):
    assert project_data.run(["compact"], tool.service) == 0
    assert "Skipped" in capsys.readouterr().out
    with closing(sqlite3.connect(tool.store.path)) as connection, connection:
        connection.executemany(
            "INSERT INTO jobs(kind, key, payload, state, created_at, updated_at, available_at) "
            "VALUES ('event', ?, ?, 'done', 1, 1, 1)",
            ((f"done:{index}", json.dumps({"body": "x" * 1000})) for index in range(30_000)))
    tool.store.prune_jobs(0)
    assert project_data.run(["compact"], tool.service) == 0
    assert "Compacted" in capsys.readouterr().out


def test_clear_history_reports_a_failed_compaction_without_failing(tool, capsys, monkeypatch):
    tool.store.enqueue("event", {}, "queued")

    def broken_compact():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(tool.store, "compact", broken_compact)
    assert project_data.run(["clear-history"], tool.service) == 0
    assert "Deleted 1 job. Compaction skipped." in capsys.readouterr().out


def renamed_helper(tool):
    """Configure the helper as "Old Helper" and store one issue post and the activation baseline under that name."""
    from project_automation import mirror_key
    from project_automation_activity import activate
    tool.settings.update(bot_name="Old Helper", guild_id="100", repository="example/project",
                         issues_channel_id="201", reviews_channel_id="202")
    activate(tool.store, tool.settings)
    key = mirror_key(tool.settings, "issue", "issue:7")
    tool.store.put_link("mirror", key, {"channel_id": "301", "message_id": "987654321", "kind": "issue", "number": 7,
                                        "repository": "example/project", "guild_id": "100", "bot_name": "Old Helper"})
    return key, mirror_key({**tool.settings, "bot_name": "New Helper"}, "issue", "issue:7")


RENAME = ["rename-helper", "--from", "Old Helper", "--to", "New Helper"]


def test_rename_helper_dry_run_prints_counts_only_and_apply_moves_the_records(tool, capsys):
    old_key, new_key = renamed_helper(tool)
    assert project_data.run(RENAME, tool.service) == 0
    output = capsys.readouterr().out
    assert "VERDICT: DRY RUN OK. 2 records would be rewritten." in output
    assert "mirror" in output and "987654321" not in output
    assert tool.store.get_link("mirror", old_key) is not None
    assert project_data.run(RENAME + ["--apply"], tool.service) == 0
    assert "VERDICT: APPLIED. 2 records rewritten" in capsys.readouterr().out
    assert tool.store.get_link("mirror", old_key) is None
    assert tool.store.get_link("mirror", new_key)["marker"] == "Project " + old_key
    assert project_data.run(RENAME + ["--apply"], tool.service) == 0
    assert "VERDICT: NOTHING TO DO" in capsys.readouterr().out


def test_rename_helper_refuses_while_enabled_and_needs_both_names(tool, capsys):
    old_key, _ = renamed_helper(tool)
    tool.settings["enabled"] = True
    assert project_data.run(RENAME + ["--apply"], tool.service) == 1
    output = capsys.readouterr().out
    assert "REFUSED: The Project helper is enabled" in output and "VERDICT: REFUSED. Nothing was changed." in output
    assert tool.store.get_link("mirror", old_key) is not None
    with pytest.raises(SystemExit):
        project_data.run(["rename-helper", "--from", "Old Helper"], tool.service)


@pytest.mark.parametrize("error", [RuntimeError("the database differs from the planned result"),
                                   sqlite3.OperationalError("database is locked")])
def test_rename_helper_reports_a_rolled_back_write_without_a_traceback(tool, capsys, monkeypatch, error):
    renamed_helper(tool)

    def fail(plan, *, apply):
        raise error

    monkeypatch.setattr(tool.store, "rewrite_records", fail)
    assert project_data.run(RENAME + ["--apply"], tool.service) == 1
    assert str(error) in capsys.readouterr().err


def test_main_refuses_a_missing_database(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(project_data, "ROOT", tmp_path)
    assert project_data.main(["status"]) == 1
    assert "No Project database" in capsys.readouterr().err
    assert not (tmp_path / "bot_data").exists()
