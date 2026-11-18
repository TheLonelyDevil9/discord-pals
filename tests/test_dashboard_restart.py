import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module_stubs  # noqa: F401
import dashboard as dashboard_module
import update as update_module
from test_support import MemorySandboxMixin


class SystemdDetectionTests(unittest.TestCase):
    def detect(self, invocation_id, cgroup):
        with tempfile.TemporaryDirectory() as temp:
            cgroup_file = Path(temp, "cgroup")
            if cgroup is not None:
                cgroup_file.write_text(cgroup, encoding="utf-8")
            environ = {key: value for key, value in os.environ.items() if key != "INVOCATION_ID"}
            if invocation_id:
                environ["INVOCATION_ID"] = invocation_id
            with patch.dict(os.environ, environ, clear=True), \
                    patch.object(dashboard_module, "_PROC_CGROUP", cgroup_file):
                return dashboard_module._running_under_systemd()

    def test_without_invocation_id_the_process_is_not_a_service(self):
        self.assertFalse(self.detect(None, "0::/system.slice/discord-pals.service\n"))

    def test_a_system_or_user_service_is_detected(self):
        self.assertTrue(self.detect("abc", "0::/system.slice/discord-pals.service\n"))
        self.assertTrue(self.detect("abc", "0::/user.slice/user-1000.slice/user@1000.service/app.slice/discord-pals.service\n"))
        self.assertTrue(self.detect("abc", "12:pids:/system.slice/discord-pals.service\n1:name=systemd:/system.slice/discord-pals.service\n"))

    def test_a_terminal_that_inherited_invocation_id_is_not_a_service(self):
        self.assertFalse(self.detect("abc", "0::/user.slice/user-1000.slice/session-3.scope\n"))
        self.assertFalse(self.detect("abc", "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.gnome.Terminal.slice/vte-spawn-1.scope\n"))

    def test_invocation_id_counts_when_the_cgroup_cannot_be_read(self):
        self.assertTrue(self.detect("abc", None))


class RestartProcessTests(unittest.TestCase):
    def restart(self, under_systemd):
        with patch.object(dashboard_module, "_running_under_systemd", return_value=under_systemd), \
                patch.object(dashboard_module.time, "sleep"), \
                patch.object(dashboard_module, "bot_instances", []), \
                patch.object(dashboard_module.os, "_exit") as exit_process, \
                patch.object(dashboard_module.os, "execv") as execv, \
                patch.object(dashboard_module.subprocess, "run") as run:
            dashboard_module._restart_process()
        return exit_process, execv, run

    def test_under_systemd_the_process_exits_for_the_unit_to_restart_it(self):
        exit_process, execv, run = self.restart(True)

        exit_process.assert_called_once_with(dashboard_module._RESTART_EXIT_CODE)
        self.assertEqual(dashboard_module._RESTART_EXIT_CODE, 75)
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
        self.assertIs(thread.call_args.kwargs["target"], dashboard_module._restart_process)


if __name__ == "__main__":
    unittest.main()
