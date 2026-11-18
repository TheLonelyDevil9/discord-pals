#!/usr/bin/env python3
"""Inspect, clear, reset, or compact the Project helper's database, or move it to a renamed helper bot.

Run it from any directory; it reads bot_data in this checkout, the same files the
bot uses. Each command calls the store functions behind the dashboard's Project
data controls and prints counts only.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _megabytes(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project_data.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show queue counts and database size.")
    commands.add_parser("clear-history", help="Delete every job except running and inflight ones, then compact.")
    reset = commands.add_parser("reset", help="Delete all jobs, records and feedback cases while the helper is disabled.")
    reset.add_argument("--confirm", required=True, metavar="REPOSITORY",
                       help="The configured owner/repository, typed exactly.")
    commands.add_parser("compact", help="Return free space to the file system when at least 25%% and 16 MB is free.")
    rename = commands.add_parser("rename-helper", help="Move the stored records of helper bot OLD to NEW while the "
                                                       "helper is disabled. Without --apply it only reports.")
    rename.add_argument("--from", dest="old", required=True, metavar="OLD", help="The helper bot name the records use now.")
    rename.add_argument("--to", dest="new", required=True, metavar="NEW", help="The helper bot name to move them to.")
    rename.add_argument("--apply", action="store_true", help="Write the changes. Without it nothing is written.")
    return parser


def run(argv, service) -> int:
    args = _parser().parse_args(argv)

    from project_automation import WorkflowError
    from project_automation_store import Conflict

    store = service.store
    if args.command == "status":
        counts = store.job_counts()
        usage = store.database_usage()
        print(f"Jobs: {counts['pending']} pending, {counts['failed']} failed, "
              f"{counts['recovery']} held, {counts['dismissed']} dismissed.")
        share = usage["free_bytes"] * 100 // usage["bytes"] if usage["bytes"] else 0
        print(f"Database: {_megabytes(usage['bytes'])}, of which {_megabytes(usage['free_bytes'])} ({share}%) is free.")
        return 0
    if args.command == "clear-history":
        result = service.clear_history()
        print(f"Deleted {result['deleted']} {'job' if result['deleted'] == 1 else 'jobs'}. "
              f"Compaction {'ran' if result['compacted'] else 'skipped'}.")
        return 0
    if args.command == "reset":
        try:
            result = service.reset_project_data(args.confirm)
        except WorkflowError:
            print("Refused: the Project helper is enabled. Pause it in the dashboard, or stop the bot and set "
                  "project_automation.enabled to false in bot_data/runtime_config.json, then retry. "
                  "Nothing was deleted.", file=sys.stderr)
            return 1
        except ValueError:
            print("Refused: --confirm does not match the configured repository. Nothing was deleted.", file=sys.stderr)
            return 1
        except Conflict:
            print("Refused: a job is in progress. Wait for it to finish, then retry. Nothing was deleted.",
                  file=sys.stderr)
            return 1
        deleted = result["deleted"]
        print(f"Deleted jobs: {deleted['jobs']}, records: {deleted['links']}, feedback cases: {deleted['cases']}. "
              f"Compaction {'ran' if result['compacted'] else 'skipped'}.")
        return 0
    if args.command == "rename-helper":
        from project_automation_rename import format_report, rename_helper
        try:
            report = rename_helper(store, service.settings(), args.old, args.new, apply=args.apply)
        except (RuntimeError, sqlite3.Error) as exc:
            print(f"Rolled back: {exc}. Nothing was changed.", file=sys.stderr)
            return 1
        print("\n".join(format_report(report, args.old, args.new, args.apply)))
        return 1 if report.verdict == "refused" else 0
    if store.compact():
        print("Compacted the database.")
    else:
        print("Skipped: free space is under 25% of the file or under 16 MB, or another process holds the "
              "database. A later sweep tries again.")
    return 0


def main(argv=None) -> int:
    _parser().parse_args(argv)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from config import DATA_DIR

    path = Path(DATA_DIR) / "project_automation.sqlite3"
    if not path.is_file():
        print(f"No Project database at {path}.", file=sys.stderr)
        return 1
    import runtime_config
    from project_automation import AutomationService
    from project_automation_store import AutomationStore

    settings = runtime_config.get("project_automation")
    return run(argv, AutomationService(AutomationStore(path), settings=lambda: settings))


if __name__ == "__main__":
    sys.exit(main())
