"""Age-based retention of the app's own log files under bot_data/logs."""

import asyncio
from datetime import datetime, timezone
import json
import os
import threading
import time

import pytest

import logger

DAY = 86400
# Noon UTC keeps every test's day boundaries half a day away from the clock.
NOW = (time.time() // DAY) * DAY + DAY / 2


@pytest.fixture
def logs(tmp_path, monkeypatch):
    for name in ("FILE_LOGGING_ENABLED", "LOG_DIR", "LOG_FILE", "LOG_FILE_MAX_BYTES", "LOG_FILE_BACKUPS",
                 "LOG_RETENTION_DAYS"):
        monkeypatch.setattr(logger, name, getattr(logger, name))
    monkeypatch.setattr(logger, "LOG_LEVEL", logger.QUIET)
    logger.configure_file_logging(enabled=True, log_dir=str(tmp_path), max_bytes=1024 * 1024, backups=5,
                                  retention_days=14)
    return tmp_path


def line(stamp):
    return json.dumps({"ts": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="milliseconds"),
                       "message": "routine"}) + "\n"


def write_log(path, first, last):
    """Write a log file whose lines run from the first to the last time, and date it by the last line."""
    path.write_text(line(first) + line(last), encoding="utf-8")
    os.utime(path, (last, last))
    return path


def names(directory):
    return sorted(entry.name for entry in directory.iterdir())


def test_rotated_files_older_than_the_setting_are_deleted_and_newer_ones_kept(logs):
    active = write_log(logs / "discord-pals.log", NOW - 3600, NOW - 60)
    for days in range(1, 21):
        start = (NOW // DAY - days) * DAY
        write_log(logs / f"discord-pals.log.{days}", start + 1, start + DAY - 1)
    assert logger.delete_expired_logs(NOW) == 7
    kept = sorted(int(name.rsplit(".", 1)[1]) for name in names(logs) if name != "discord-pals.log")
    assert kept == list(range(1, 14))
    assert active.read_text(encoding="utf-8") == line(NOW - 3600) + line(NOW - 60)


def first_line_time(path):
    with path.open(encoding="utf-8") as handle:
        return datetime.fromisoformat(json.loads(handle.readline())["ts"]).timestamp()


@pytest.mark.parametrize("retention", [1, 3, 14])
def test_no_line_older_than_the_setting_plus_one_day_outlives_a_daily_cleanup(logs, retention):
    logger.configure_file_logging(retention_days=retention)
    for days in range(1, retention + 6):
        start = (NOW // DAY - days) * DAY
        write_log(logs / f"discord-pals.log.{days}", start + 1, start + DAY - 1)
    write_log(logs / "discord-pals.log", NOW - 3600, NOW - 60)
    for cleanup in (NOW, NOW + DAY / 3, NOW + DAY / 2):
        logger.delete_expired_logs(cleanup)
        # Until the next daily cleanup, every line left on disk stays younger than the setting plus one day.
        next_cleanup = cleanup + DAY
        for name in names(logs):
            assert next_cleanup - first_line_time(logs / name) <= (retention + 1) * DAY, (cleanup, name)
    assert len(names(logs)) >= retention - 1


def test_the_active_file_is_kept_and_a_stale_one_is_rotated_instead_of_deleted(logs):
    logger.configure_file_logging(retention_days=1)
    active = write_log(logs / "discord-pals.log", NOW - 7200, NOW - 60)
    logger.delete_expired_logs(NOW)
    assert names(logs) == ["discord-pals.log"]
    stale = (NOW // DAY - 2) * DAY + 100
    logger.configure_file_logging(retention_days=14)
    write_log(active, stale, stale + 60)
    logger.delete_expired_logs(NOW)
    assert names(logs) == ["discord-pals.log.1"]
    assert (logs / "discord-pals.log.1").read_text(encoding="utf-8") == line(stale) + line(stale + 60)
    logger.info("after the cleanup")
    assert json.loads((logs / "discord-pals.log").read_text(encoding="utf-8"))["message"] == "after the cleanup"


def test_only_files_that_the_logger_created_are_deleted(logs):
    old = NOW - 90 * DAY
    for name in ("notes.txt", "discord-pals.log.bak", "discord-pals.log.1.gz", "other.log.3", "discord-pals.log.0",
                 "discord-pals.log.x"):
        write_log(logs / name, old, old + 60)
    write_log(logs / "discord-pals.log.4", old, old + 60)
    assert logger.delete_expired_logs(NOW) == 1
    assert "discord-pals.log.4" not in names(logs)
    assert len(names(logs)) == 6


def test_a_file_rotated_by_size_alone_is_judged_by_its_first_line(logs):
    write_log(logs / "discord-pals.log.1", NOW - 30 * DAY, NOW - 2 * DAY)
    write_log(logs / "discord-pals.log.2", NOW - 5 * DAY, NOW - 3 * DAY)
    logger.delete_expired_logs(NOW)
    assert names(logs) == ["discord-pals.log.2"]


def test_zero_keeps_the_size_only_behavior(logs):
    logger.configure_file_logging(retention_days=0)
    write_log(logs / "discord-pals.log.3", NOW - 300 * DAY, NOW - 299 * DAY)
    active = write_log(logs / "discord-pals.log", NOW - 40 * DAY, NOW - 40 * DAY)
    assert logger.delete_expired_logs(NOW) == 0
    logger.info("same file")
    assert names(logs) == ["discord-pals.log", "discord-pals.log.3"]
    assert active.read_text(encoding="utf-8").count("\n") == 3


def test_the_first_write_of_a_new_day_starts_a_new_file(logs):
    yesterday = time.time() - DAY
    write_log(logs / "discord-pals.log", yesterday - 60, yesterday)
    logger.info("today")
    assert names(logs) == ["discord-pals.log", "discord-pals.log.1"]
    assert json.loads((logs / "discord-pals.log").read_text(encoding="utf-8"))["message"] == "today"
    logger.info("same day")
    assert names(logs) == ["discord-pals.log", "discord-pals.log.1"]


@pytest.mark.parametrize("retention", [0, 14])
def test_the_size_cap_keeps_working(logs, retention):
    logger.configure_file_logging(max_bytes=1024, retention_days=retention)
    for index in range(400):
        logger.info(f"line {index:04d} " + "x" * 60)
    rotated = [name for name in names(logs) if name != "discord-pals.log"]
    assert sorted(rotated) == [f"discord-pals.log.{number}" for number in range(1, 6)]
    # A file rotates once it reaches the cap, so it holds at most one line more.
    assert all((logs / name).stat().st_size < 2 * 1024 for name in names(logs))


def test_small_daily_files_are_kept_past_five_files_within_the_same_total_size(logs):
    logger.configure_file_logging(max_bytes=1024, retention_days=30)
    for days in range(12, 0, -1):
        stamp = NOW - days * DAY
        write_log(logs / "discord-pals.log", stamp, stamp)
        logger.info(f"rotate after day {days}")
    rotated = [name for name in names(logs) if name != "discord-pals.log"]
    assert len(rotated) == 12
    total = sum((logs / name).stat().st_size for name in rotated)
    assert total <= 5 * 1024


def test_the_daily_task_runs_at_start_off_the_event_loop_and_survives_a_failure(monkeypatch):
    calls, warnings, sleeps = [], [], []
    loop_thread = threading.get_ident()

    def cleanup():
        calls.append(threading.get_ident() != loop_thread)
        if len(calls) == 1:
            raise OSError("disk detail")
        return 0

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(logger, "delete_expired_logs", cleanup)
    monkeypatch.setattr(logger.asyncio, "sleep", sleep)
    monkeypatch.setattr(logger, "warn", lambda message, **fields: warnings.append(message))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(logger.run_log_retention())
    assert calls == [True, True]
    assert sleeps == [logger.LOG_CLEANUP_SECONDS] * 2 and logger.LOG_CLEANUP_SECONDS <= DAY
    assert len(warnings) == 1 and "OSError" in warnings[0] and "disk detail" not in warnings[0]
