"""Restart of the running bot process, started from the dashboard."""

import asyncio
import os
import sys
import time
from pathlib import Path

import logger as log

# Exit status of a dashboard restart under systemd. docs/operations.md lists the unit
# settings that start the service again after it.
RESTART_EXIT_CODE = 75
PROC_CGROUP = Path("/proc/self/cgroup")


def running_under_systemd() -> bool:
    """True when systemd started this process as part of a service unit.

    INVOCATION_ID alone is not enough: a shell in a terminal that runs as a systemd user
    service can inherit it, and exiting there would leave nothing to start the bot again.
    The processes of a service run in a cgroup whose name ends in ".service".
    """
    if not os.environ.get("INVOCATION_ID"):
        return False
    try:
        cgroups = PROC_CGROUP.read_text(encoding="utf-8")
    except OSError:
        return True
    return any(line.split(":", 2)[-1].strip().endswith(".service") for line in cgroups.splitlines())


def restart_process(bots) -> None:
    """Close the bots, then exit for systemd to start the service again, or replace this process."""
    time.sleep(1)  # Give time for response to be sent
    for bot in bots:
        try:
            asyncio.run_coroutine_threadsafe(bot.close(), bot.client.loop)
        except Exception:
            pass
    time.sleep(2)  # Wait for connections to close

    if running_under_systemd():
        log.info(f"Exiting with status {RESTART_EXIT_CODE} so that systemd starts the service again")
        # os._exit skips the interpreter's shutdown, so write out buffered output first.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(RESTART_EXIT_CODE)
    else:
        os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:])
