"""Restart of the running bot process, started from the dashboard."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import logger as log

# Exit status of a dashboard restart under systemd. docs/operations.md lists the unit
# settings that start the service again after it.
RESTART_EXIT_CODE = 75
PROC_CGROUP = Path("/proc/self/cgroup")
SYSTEMCTL_TIMEOUT = 5
RESTART_PROPERTIES = ("Restart", "SuccessExitStatus", "RestartForceExitStatus", "RestartPreventExitStatus")


def systemd_unit() -> str | None:
    """Return the system service whose main process this is, or None.

    SYSTEMD_EXEC_PID (systemd 248 and later) names the process that ExecStart= ran; a shell
    inside a terminal's or tmux's unit inherits it with another PID. A unit below
    user@<uid>.service belongs to a user manager, which systemctl without --user does not read.
    """
    if os.environ.get("SYSTEMD_EXEC_PID") != str(os.getpid()):
        return None
    try:
        cgroups = PROC_CGROUP.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in cgroups.splitlines():
        parts = line.split(":", 2)[-1].strip().split("/")
        services = [part for part in parts if part.endswith(".service")]
        if services and not any(part.startswith("user@") for part in parts):
            return services[-1]
    return None


def restarts_after_exit(properties: dict[str, str], status: int = RESTART_EXIT_CODE) -> bool:
    """True when systemd starts a unit with these `systemctl show` properties again after status.

    These are the rules of systemd.service: RestartPreventExitStatus= wins over everything,
    RestartForceExitStatus= wins over Restart=, and a status listed in SuccessExitStatus=
    counts as a clean exit.
    """
    def listed(name: str) -> bool:
        return str(status) in properties.get(name, "").split()

    if listed("RestartPreventExitStatus"):
        return False
    if listed("RestartForceExitStatus"):
        return True
    clean = listed("SuccessExitStatus")
    policy = properties.get("Restart", "no")
    return policy == "always" or (policy == "on-success" and clean) or (policy == "on-failure" and not clean)


def exits_for_systemd() -> bool:
    """True when exiting with RESTART_EXIT_CODE makes systemd start this service again.

    Any doubt, such as a failed or slow systemctl, gives False, and the restart then
    re-executes the process, which keeps its PID and works under any supervisor.
    """
    unit = systemd_unit()
    if not unit:
        return False
    command = ["systemctl", "show", "--no-pager", *[f"--property={name}" for name in RESTART_PROPERTIES], unit]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return restarts_after_exit(dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line))


def restart_process(bots) -> None:
    """Close the bots, then exit for systemd to start the service again, or replace this process."""
    time.sleep(1)  # Give time for response to be sent
    for bot in bots:
        try:
            asyncio.run_coroutine_threadsafe(bot.close(), bot.client.loop)
        except Exception:
            pass
    time.sleep(2)  # Wait for connections to close

    if exits_for_systemd():
        log.info(f"Exiting with status {RESTART_EXIT_CODE} so that systemd starts the service again")
        # os._exit skips the interpreter's shutdown, so write out buffered output first.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(RESTART_EXIT_CODE)
    else:
        os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:])
