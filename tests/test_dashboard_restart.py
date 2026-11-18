import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def restart(self, under_systemd):
        with patch.object(dashboard_restart, "exits_for_systemd", return_value=under_systemd), \
                patch.object(dashboard_restart.time, "sleep"), \
                patch.object(dashboard_restart.os, "_exit") as exit_process, \
                patch.object(dashboard_restart.os, "execv") as execv, \
                patch("subprocess.run") as run:
            dashboard_restart.restart_process([])
        return exit_process, execv, run

    def test_under_systemd_the_process_exits_for_the_unit_to_restart_it(self):
        exit_process, execv, run = self.restart(True)

        exit_process.assert_called_once_with(dashboard_restart.RESTART_EXIT_CODE)
        self.assertEqual(dashboard_restart.RESTART_EXIT_CODE, 75)
        execv.assert_not_called()
        run.assert_not_called()

    def test_outside_systemd_the_process_replaces_itself(self):
        exit_process, execv, run = self.restart(False)

        exit_process.assert_not_called()
        execv.assert_called_once()
        self.assertEqual(execv.call_args.args[0], sys.executable)
        run.assert_not_called()


class RestartEndpointTests(MemorySandboxMixin, unittest.TestCase):
    def setUp(self):
        self.setUpMemorySandbox()
        self.client = self.make_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True

    def tearDown(self):
        self.tearDownMemorySandbox()

    def post_restart(self, import_error=None):
        with patch.object(update_module, "check_imports", return_value=import_error) as check, \
                patch("discord_utils.save_history") as save_history, \
                patch.object(self.manager, "save_all") as save_memories, \
                patch("reminders.reminder_manager.save") as save_reminders, \
                patch("stats.stats_manager.flush") as flush_stats, \
                patch.object(dashboard_module.threading, "Thread") as thread:
            response = self.client.post("/api/restart", headers=self.csrf_headers())
        saves = (save_history, save_memories, save_reminders, flush_stats)
        return response, check, thread, saves

    def test_restart_is_refused_when_the_code_on_disk_does_not_import(self):
        response, check, thread, saves = self.post_restart("ImportError: broken on purpose")

        self.assertEqual(response.status_code, 409)
        self.assertIn("ImportError: broken on purpose", response.get_json()["message"])
        thread.assert_not_called()
        self.assertEqual(check.call_args.args[0], Path(dashboard_module.__file__).resolve().parent)
        for save in saves:
            save.assert_not_called()

    def test_restart_saves_every_store_and_starts_the_restart(self):
        response, _check, thread, saves = self.post_restart()

        self.assertEqual(response.status_code, 200)
        for save in saves:
            save.assert_called_once()
        thread.assert_called_once()
        self.assertIs(thread.call_args.kwargs["target"], dashboard_restart.restart_process)


if __name__ == "__main__":
    unittest.main()
