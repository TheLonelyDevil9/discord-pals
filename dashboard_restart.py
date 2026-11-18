"""Restart of the running bot process, started from the dashboard."""

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import logger as log
import update

# Exit status of a dashboard restart under systemd. docs/operations.md lists the unit
# settings that start the service again after it.
RESTART_EXIT_CODE = 75
PROC_CGROUP = Path("/proc/self/cgroup")
SYSTEMCTL_TIMEOUT = 5
RESTART_PROPERTIES = ("Restart", "SuccessExitStatus", "RestartForceExitStatus", "RestartPreventExitStatus")
# Covers the client close that follows a bot's queue drain of up to SHUTDOWN_DRAIN_TIMEOUT_SECONDS.
CLOSE_MARGIN_SECONDS = 5
# Windows has no exec: os.execv starts a new process from arguments joined without quotes,
# so a path with a space breaks it, and this process has already ended by then.
REPLACES_WITH_NEW_PROCESS = os.name == "nt"


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


def refusal(code_dir: Path) -> str | None:
    """Return why the code in code_dir must not be started now, or None.

    The update pre-flight refuses a checkout with unresolved conflicts or an unfinished Git
    operation, and the import check refuses code that does not import.
    """
    try:
        update.preflight(code_dir)
    except update.UpdateRefused as e:
        return f"Restart refused: {e}"
    except (RuntimeError, OSError, subprocess.SubprocessError):
        pass  # Not a Git checkout, so there is no Git state to refuse.
    import_error = update.check_imports(code_dir, sys.executable)
    if import_error:
        return f"Restart cancelled: the code on disk failed its import check ({import_error}). The bot keeps running."
    return None


def replacement_command() -> list[str]:
    return [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]


def close_bots(bots, timeout: float) -> None:
    """Close every bot and wait, at most timeout seconds in all, for the closes to finish."""
    deadline = time.monotonic() + timeout
    closing = []
    for bot in bots:
        try:
            closing.append(asyncio.run_coroutine_threadsafe(bot.close(), bot.client.loop))
        except Exception as e:
            log.warn(f"Could not close a bot before the restart: {e}")
    for future in closing:
        try:
            future.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception as e:
            future.cancel()
            log.warn(f"A bot did not finish closing before the restart: {e!r}")


def persist_runtime_state() -> None:
    from discord_utils import save_history
    from memory import memory_manager
    from reminders import reminder_manager
    from stats import stats_manager

    # main._persist_runtime_state saves the same stores on SIGTERM; keep both lists in step.
    for label, save in (
        ("history", lambda: save_history(force=True)),
        ("memories", memory_manager.save_all),
        ("reminders", reminder_manager.save),
        ("stats", stats_manager.flush),
    ):
        try:
            save()
        except Exception as e:
            log.error(f"Failed to save {label} before restart: {e}")


def flush_output() -> None:
    # os._exit and os.execv skip the interpreter's shutdown, so write out buffered output first.
    sys.stdout.flush()
    sys.stderr.flush()


def start_restart(bots) -> threading.Thread:
    thread = threading.Thread(target=restart_process, args=(bots,), daemon=True, name="dashboard-restart")
    thread.start()
    return thread


def restart_process(bots) -> None:
    """Close the bots, save the state, then start the code on disk; this never returns.

    Under a systemd unit that restarts after RESTART_EXIT_CODE the process exits with it.
    Otherwise it re-executes in place, or on Windows starts a new process and exits. Any
    failure ends the process with status 1, so that a supervisor can act.
    """
    status = 1
    try:
        time.sleep(1)  # Give time for response to be sent
        from bot_instance import SHUTDOWN_DRAIN_TIMEOUT_SECONDS
        close_bots(bots, SHUTDOWN_DRAIN_TIMEOUT_SECONDS + CLOSE_MARGIN_SECONDS)
        persist_runtime_state()
        if exits_for_systemd():
            status = RESTART_EXIT_CODE
            log.info(f"Exiting with status {status} so that systemd starts the service again")
            flush_output()
        elif REPLACES_WITH_NEW_PROCESS:
            flush_output()
            subprocess.Popen(replacement_command())
            status = 0
        else:
            flush_output()
            os.execv(sys.executable, replacement_command())
    except Exception as e:
        log.error(f"The restart failed, so the process ends with status {status}: {e}")
    finally:
        os._exit(status)
