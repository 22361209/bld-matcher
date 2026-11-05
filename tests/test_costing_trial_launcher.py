from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import costing_trial as launcher


class CostingTrialLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.state = {
            "pid": 32761,
            "port": 5056,
            "instance_id": "a" * 32,
            "read_only": False,
        }

    def write_state(self):
        launcher._write_state(self.data_dir, self.state)

    def test_formal_port_and_non_user_ports_are_rejected(self):
        for value in ("5055", "80", "0", "65536", "bad"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                launcher._port(value)
        self.assertEqual(launcher._port("5056"), 5056)

    def test_stop_refuses_reused_pid_without_sending_signal(self):
        self.write_state()
        with (
            patch.object(launcher, "_pid_exists", return_value=True),
            patch.object(launcher, "_process_matches", return_value=False),
            patch.object(launcher.os, "kill") as kill,
            self.assertRaisesRegex(launcher.LauncherError, "拒绝停止其他进程"),
        ):
            launcher.stop(self.data_dir)
        kill.assert_not_called()
        self.assertTrue((self.data_dir / launcher.STATE_FILE).exists())

    def test_stop_owned_process_preserves_application_data(self):
        self.write_state()
        cost_file = self.data_dir / "costs.sqlite3"
        cost_file.write_bytes(b"unchanged fixture")
        with (
            patch.object(launcher, "_pid_exists", side_effect=(True, False)),
            patch.object(launcher, "_process_matches", return_value=True),
            patch.object(launcher.os, "kill") as kill,
            redirect_stdout(io.StringIO()),
        ):
            launcher.stop(self.data_dir)
        kill.assert_called_once_with(self.state["pid"], signal.SIGTERM)
        self.assertFalse((self.data_dir / launcher.STATE_FILE).exists())
        self.assertEqual(cost_file.read_bytes(), b"unchanged fixture")

    def test_occupied_port_prevents_initialization_and_spawn(self):
        with (
            patch.object(launcher, "_check_port", side_effect=launcher.LauncherError("占用")),
            patch.object(launcher, "initialize") as initialize,
            patch.object(launcher.subprocess, "Popen") as spawn,
            self.assertRaises(launcher.LauncherError),
        ):
            launcher.start(self.data_dir, 5056)
        initialize.assert_not_called()
        spawn.assert_not_called()

    def test_existing_ready_service_does_not_initialize_or_spawn_again(self):
        self.write_state()
        with (
            patch.object(launcher, "_pid_exists", return_value=True),
            patch.object(launcher, "_process_matches", return_value=True),
            patch.object(launcher, "_health", return_value=True),
            patch.object(launcher, "initialize") as initialize,
            patch.object(launcher.subprocess, "Popen") as spawn,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(launcher.start(self.data_dir, 5056), self.state)
        initialize.assert_not_called()
        spawn.assert_not_called()

    def test_existing_mode_difference_requires_stop(self):
        self.write_state()
        with (
            patch.object(launcher, "_pid_exists", return_value=True),
            patch.object(launcher, "_process_matches", return_value=True),
            patch.object(launcher, "_health", return_value=True),
            self.assertRaisesRegex(launcher.LauncherError, "只读设置不同"),
        ):
            launcher.start(self.data_dir, 5056, read_only=True)

    def test_start_uses_isolated_loopback_process_and_drops_password_environment(self):
        child = Mock(pid=32761)
        child.poll.return_value = None
        with (
            patch.object(launcher, "_check_port"),
            patch.object(launcher, "initialize"),
            patch.object(launcher.subprocess, "Popen", return_value=child) as spawn,
            patch.object(launcher, "_health", return_value=True),
            patch.dict(launcher.os.environ, {"BLD_COSTING_TRIAL_PASSWORD": "not-for-child"}),
            redirect_stdout(io.StringIO()),
        ):
            result = launcher.start(self.data_dir, 5056, read_only=True)
        args, kwargs = spawn.call_args
        self.assertIn("--read-only", args[0])
        self.assertIn(str(self.data_dir), args[0])
        self.assertIn("5056", args[0])
        self.assertNotIn("5055", args[0])
        self.assertNotIn("BLD_COSTING_TRIAL_PASSWORD", kwargs["env"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(launcher._read_state(self.data_dir), result)
        self.assertEqual((self.data_dir / launcher.STATE_FILE).stat().st_mode & 0o777, 0o600)

    def test_early_child_exit_removes_only_launcher_state(self):
        child = Mock(pid=32761)
        child.poll.return_value = 1
        with (
            patch.object(launcher, "_check_port"),
            patch.object(launcher, "initialize"),
            patch.object(launcher.subprocess, "Popen", return_value=child),
            self.assertRaisesRegex(launcher.LauncherError, "启动失败"),
        ):
            launcher.start(self.data_dir, 5056)
        self.assertFalse((self.data_dir / launcher.STATE_FILE).exists())
        child.terminate.assert_not_called()

    def test_identity_requires_script_and_instance_token(self):
        correct = f"python {launcher.SCRIPT} serve --instance-id {self.state['instance_id']}"
        for command, expected in (
            (correct, True),
            ("python unrelated.py serve --instance-id " + self.state["instance_id"], False),
            (f"python {launcher.SCRIPT} serve --instance-id {'b' * 32}", False),
        ):
            with self.subTest(command=command), patch.object(launcher.subprocess, "run") as run:
                run.return_value = Mock(returncode=0, stdout=command)
                self.assertEqual(launcher._process_matches(self.state), expected)

    def test_status_is_read_only_and_json_has_no_secret(self):
        self.write_state()
        output = io.StringIO()
        before = (self.data_dir / launcher.STATE_FILE).read_bytes()
        with (
            patch.object(launcher, "_pid_exists", return_value=True),
            patch.object(launcher, "_process_matches", return_value=True),
            patch.object(launcher, "_health", return_value=True),
            redirect_stdout(output),
        ):
            launcher.status(self.data_dir, as_json=True)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["running"])
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["url"], "http://127.0.0.1:5056/")
        self.assertNotIn("password", payload)
        self.assertEqual(before, (self.data_dir / launcher.STATE_FILE).read_bytes())

    def test_invalid_pid_state_is_not_acted_on(self):
        (self.data_dir / launcher.STATE_FILE).write_text(
            json.dumps({**self.state, "pid": 1}), encoding="utf-8"
        )
        with self.assertRaises(launcher.LauncherError):
            launcher._read_state(self.data_dir)


if __name__ == "__main__":
    unittest.main()
