import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import dashboard_restart
import update as update_module
from test_support import MemorySandboxMixin


SYSTEM_UNIT = "0::/system.slice/discord-pals.service\n"
USER_UNIT = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/discord-pals.service\n"


class SystemdUnitTests(unittest.TestCase):
    def unit(self, cgroup, exec_pid="self"):
        with tempfile.TemporaryDirectory() as temp:
            cgroup_file = Path(temp, "cgroup")
            if cgroup is not None:
                cgroup_file.write_text(cgroup, encoding="utf-8")
            environ = {key: value for key, value in os.environ.items() if key != "SYSTEMD_EXEC_PID"}
            environ["INVOCATION_ID"] = "0123456789abcdef"
            if exec_pid is not None:
                environ["SYSTEMD_EXEC_PID"] = str(os.getpid() if exec_pid == "self" else exec_pid)
            with patch.dict(os.environ, environ, clear=True), \
                    patch.object(dashboard_restart, "PROC_CGROUP", cgroup_file):
                return dashboard_restart.systemd_unit()

    def test_the_main_process_of_a_system_service_finds_its_unit(self):
        self.assertEqual(self.unit(SYSTEM_UNIT), "discord-pals.service")
        self.assertEqual(self.unit("12:pids:/system.slice/discord-pals.service\n"
                                   "1:name=systemd:/system.slice/discord-pals.service\n"), "discord-pals.service")
        self.assertEqual(self.unit("0::/system.slice/discord-pals.service/payload\n"), "discord-pals.service")

    def test_without_systemd_exec_pid_there_is_no_unit(self):
        self.assertIsNone(self.unit(SYSTEM_UNIT, exec_pid=None))

    def test_a_shell_inside_a_terminal_or_tmux_unit_is_not_the_service(self):
        self.assertIsNone(self.unit("0::/system.slice/tmux.service\n", exec_pid=1))
        self.assertIsNone(self.unit("0::/user.slice/user-1000.slice/user@1000.service/app.slice/tmux.service\n", exec_pid=1))
        self.assertIsNone(self.unit("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                                    "gnome-terminal-server.service\n", exec_pid=1))

    def test_a_user_unit_is_left_to_re_execution(self):
        self.assertIsNone(self.unit(USER_UNIT))

    def test_an_unreadable_cgroup_has_no_unit(self):
        self.assertIsNone(self.unit(None))


class RestartPolicyTests(unittest.TestCase):
    POLICIES = ("always", "on-failure", "on-success", "no", "on-abnormal", "on-abort", "on-watchdog")

    def restarts(self, policy, **lists):
        properties = {"Restart": policy, "SuccessExitStatus": "", "RestartForceExitStatus": "", "RestartPreventExitStatus": ""}
        properties.update(lists)
        return dashboard_restart.restarts_after_exit(properties)

    def check(self, expected, **lists):
        for policy in self.POLICIES:
            with self.subTest(policy=policy, **lists):
                self.assertEqual(self.restarts(policy, **lists), policy in expected)

    def test_without_extra_lines_always_and_on_failure_restart(self):
        self.check({"always", "on-failure"})

    def test_success_exit_status_75_stops_on_failure_and_starts_on_success(self):
        self.check({"always", "on-success"}, SuccessExitStatus="75")

    def test_restart_force_exit_status_75_restarts_under_every_policy(self):
        self.check(set(self.POLICIES), RestartForceExitStatus="75")
        self.check(set(self.POLICIES), RestartForceExitStatus="75", SuccessExitStatus="75")

    def test_restart_prevent_exit_status_75_wins_over_everything(self):
        self.check(set(), RestartPreventExitStatus="75")
        self.check(set(), RestartPreventExitStatus="75", RestartForceExitStatus="75")

    def test_other_listed_statuses_do_not_count(self):
        self.check({"always", "on-failure"}, SuccessExitStatus="0 7 SIGTERM", RestartForceExitStatus="76")


class ExitDecisionTests(unittest.TestCase):
    def decide(self, unit="discord-pals.service", stdout="", returncode=0, error=None):
        result = subprocess.CompletedProcess([], returncode, stdout, "")
        with patch.object(dashboard_restart, "systemd_unit", return_value=unit), \
                patch.object(dashboard_restart.subprocess, "run", return_value=result, side_effect=error) as run:
            return dashboard_restart.exits_for_systemd(), run

    def test_a_unit_that_restarts_after_the_exit_status_gets_the_exit(self):
        decided, run = self.decide(stdout="Restart=on-failure\nSuccessExitStatus=\nRestartForceExitStatus=\n")

        self.assertTrue(decided)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["systemctl", "show"])
        self.assertEqual(command[-1], "discord-pals.service")
        self.assertEqual(run.call_args.kwargs["timeout"], dashboard_restart.SYSTEMCTL_TIMEOUT)

    def test_a_unit_that_would_stay_down_is_re_executed_instead(self):
        self.assertFalse(self.decide(stdout="Restart=on-failure\nSuccessExitStatus=75\n")[0])
        self.assertFalse(self.decide(stdout="Restart=no\n")[0])

    def test_a_failed_or_slow_systemctl_falls_back_to_re_execution(self):
        self.assertFalse(self.decide(stdout="Restart=always\n", returncode=1)[0])
        self.assertFalse(self.decide(error=subprocess.TimeoutExpired(["systemctl"], 5))[0])
        self.assertFalse(self.decide(error=FileNotFoundError("systemctl"))[0])

    def test_no_unit_needs_no_systemctl(self):
        decided, run = self.decide(unit=None)

        self.assertFalse(decided)
        run.assert_not_called()


class RestartProcessTests(unittest.TestCase):
    def restart(self, under_systemd, windows=False, flush=None, execv=None, popen=None):
        """Run restart_process with the exits recorded; return (events, execv mock, Popen mock)."""
        events = []
        with patch.object(dashboard_restart, "exits_for_systemd", return_value=under_systemd), \
                patch.object(dashboard_restart, "REPLACES_WITH_NEW_PROCESS", windows), \
                patch.object(dashboard_restart.time, "sleep"), \
                patch.object(dashboard_restart, "persist_runtime_state", side_effect=lambda: events.append("saved")), \
                patch.object(dashboard_restart, "flush_output", side_effect=flush), \
                patch.object(dashboard_restart.os, "_exit", side_effect=lambda status: events.append(("exit", status))), \
                patch.object(dashboard_restart.os, "execv", side_effect=execv) as execv_mock, \
                patch.object(dashboard_restart.subprocess, "Popen", side_effect=popen) as popen_mock, \
                patch.object(sys, "argv", [os.path.join("installed here", "main.py"), "--flag"]):
            dashboard_restart.restart_process([])
        return events, execv_mock, popen_mock

    def expected_command(self):
        return [sys.executable, os.path.abspath(os.path.join("installed here", "main.py")), "--flag"]

    def test_under_systemd_the_process_exits_for_the_unit_to_restart_it(self):
        events, execv, popen = self.restart(True)

        self.assertEqual(events, ["saved", ("exit", 75)])
        self.assertEqual(dashboard_restart.RESTART_EXIT_CODE, 75)
        execv.assert_not_called()
        popen.assert_not_called()

    def test_outside_systemd_on_posix_the_process_re_executes_in_place(self):
        events, execv, popen = self.restart(False)

        execv.assert_called_once_with(sys.executable, self.expected_command())
        popen.assert_not_called()
        self.assertEqual(events[0], "saved")

    def test_on_windows_the_restart_starts_a_new_process_and_exits(self):
        events, execv, popen = self.restart(False, windows=True)

        popen.assert_called_once_with(self.expected_command())
        execv.assert_not_called()
        self.assertEqual(events, ["saved", ("exit", 0)])

    def test_a_replacement_that_cannot_start_ends_the_process_with_status_1(self):
        events, _execv, _popen = self.restart(False, windows=True, popen=OSError("no such file"))

        self.assertEqual(events[-1], ("exit", 1))

    def test_a_failed_exec_ends_the_process_non_zero(self):
        self.assertEqual(self.restart(False, execv=OSError("exec format error"))[0][-1], ("exit", 1))

    def test_a_process_without_console_streams_still_restarts(self):
        flush = dashboard_restart.flush_output
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            windows = self.restart(False, windows=True, flush=flush)
            posix = self.restart(False, flush=flush)
            systemd = self.restart(True, flush=flush)

        self.assertEqual(windows[0], ["saved", ("exit", 0)])
        windows[2].assert_called_once_with(self.expected_command())
        posix[1].assert_called_once_with(sys.executable, self.expected_command())
        self.assertEqual(systemd[0], ["saved", ("exit", 75)])

    def test_a_closed_or_broken_stream_does_not_stop_the_flush(self):
        closed = io.StringIO()
        closed.close()
        broken = types.SimpleNamespace(flush=Mock(side_effect=BrokenPipeError(32, "Broken pipe")))
        with patch.object(sys, "stdout", closed), patch.object(sys, "stderr", broken):
            dashboard_restart.flush_output()

        broken.flush.assert_called_once()

    def test_paths_with_spaces_reach_the_replacement_intact(self):
        with tempfile.TemporaryDirectory(prefix="restart test ") as temp:
            script = Path(temp, "main with space.py")
            output = Path(temp, "arguments out.json")
            script.write_text("import json, sys\nopen(sys.argv[1], 'w').write(json.dumps(sys.argv))\n", encoding="utf-8")
            with patch.object(sys, "argv", [str(script), str(output)]):
                command = dashboard_restart.replacement_command()
            subprocess.Popen(command).wait(timeout=60)

            self.assertEqual(json.loads(output.read_text()), [str(script), str(output)])


class RestartOrderTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(5)
        self.loop.close()

    def bot(self, events, hangs=False):
        loop = self.loop

        class Bot:
            client = types.SimpleNamespace(loop=loop)

            async def close(self):
                if hangs:
                    await asyncio.Event().wait()
                await asyncio.sleep(0.05)
                events.append("closed")

        return Bot()

    def restart(self, bots, events, drain=10.0):
        with patch.object(dashboard_restart, "exits_for_systemd", return_value=True), \
                patch.object(dashboard_restart.time, "sleep"), \
                patch("bot_instance.SHUTDOWN_DRAIN_TIMEOUT_SECONDS", drain), \
                patch.object(dashboard_restart, "CLOSE_MARGIN_SECONDS", 0), \
                patch.object(dashboard_restart, "persist_runtime_state", side_effect=lambda: events.append("saved")), \
                patch.object(dashboard_restart, "flush_output"), \
                patch.object(dashboard_restart.os, "_exit", side_effect=lambda status: events.append("exit")):
            started = time.monotonic()
            dashboard_restart.restart_process(bots)
            return time.monotonic() - started

    def test_the_bots_close_before_the_state_is_saved_and_the_process_exits(self):
        events = []

        self.restart([self.bot(events), self.bot(events)], events)

        self.assertEqual(events, ["closed", "closed", "saved", "exit"])

    def test_a_bot_that_does_not_close_is_waited_for_at_most_the_drain_timeout(self):
        events = []

        elapsed = self.restart([self.bot(events, hangs=True)], events, drain=0.3)

        self.assertLess(elapsed, 3)
        self.assertEqual(events, ["saved", "exit"])


class RestartEndpointTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
        # An accepted restart keeps the update lock until the process ends.
        self.addCleanup(lambda: dashboard_module._update_lock.locked() and dashboard_module._update_lock.release())

    def tearDown(self):
        self.tearDownMemorySandbox()

    def test_a_restart_is_refused_while_an_update_runs(self):
        self.assertTrue(dashboard_module._update_lock.acquire(blocking=False))

        response, check, thread, _saves = self.post_restart()

        self.assertEqual(response.status_code, 409)
        self.assertIn("an update is running", response.get_json()["message"])
        thread.assert_not_called()
        check.assert_not_called()

    def test_no_update_starts_once_a_restart_is_accepted(self):
        self.assertEqual(self.post_restart()[0].status_code, 200)

        response = self.client.post("/api/update", json={"update_branch": "main"}, headers=self.csrf_headers())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["message"], "An update is already running")

    def test_an_install_without_git_can_still_restart(self):
        with patch.object(update_module, "preflight", side_effect=RuntimeError("not a git repository")):
            response, check, thread, _saves = self.post_restart()

        self.assertEqual(response.status_code, 200)
        check.assert_called_once()
        thread.assert_called_once()

    def post_restart(self, import_error=None):
        with patch.object(update_module, "check_imports", return_value=import_error) as check, \
                patch("discord_utils.save_history") as save_history, \
                patch.object(self.manager, "save_all") as save_memories, \
                patch("reminders.reminder_manager.save") as save_reminders, \
                patch("stats.stats_manager.flush") as flush_stats, \
                patch.object(dashboard_restart, "start_restart") as start:
            response = self.client.post("/api/restart", headers=self.csrf_headers())
        saves = (save_history, save_memories, save_reminders, flush_stats)
        return response, check, start, saves

    def test_the_restart_saves_every_store_even_when_one_fails(self):
        with patch("discord_utils.save_history", side_effect=OSError("disk full")) as save_history, \
                patch.object(self.manager, "save_all") as save_memories, \
                patch("reminders.reminder_manager.save") as save_reminders, \
                patch("stats.stats_manager.flush") as flush_stats:
            dashboard_restart.persist_runtime_state()

        for save in (save_history, save_memories, save_reminders, flush_stats):
            save.assert_called_once()

    def test_restart_is_refused_when_the_code_on_disk_does_not_import(self):
        response, check, thread, saves = self.post_restart("ImportError: broken on purpose")

        self.assertEqual(response.status_code, 409)
        self.assertIn("ImportError: broken on purpose", response.get_json()["message"])
        self.assertIn("failed its import check", response.get_json()["message"])
        self.assertFalse(dashboard_module._update_lock.locked())
        thread.assert_not_called()
        self.assertEqual(check.call_args.args[0], Path(dashboard_module.__file__).resolve().parent)
        for save in saves:
            save.assert_not_called()

    def test_restart_starts_the_restart_thread_which_saves_after_the_bots_close(self):
        response, _check, start, saves = self.post_restart()

        self.assertEqual(response.status_code, 200)
        for save in saves:
            save.assert_not_called()
        start.assert_called_once_with(dashboard_module.bot_instances)

    def test_the_restart_thread_runs_the_restart_with_the_bots(self):
        bots = [object()]
        with patch.object(dashboard_restart, "restart_process") as restart:
            dashboard_restart.start_restart(bots).join(5)

        restart.assert_called_once_with(bots)


if __name__ == "__main__":
    unittest.main()
