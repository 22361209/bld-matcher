from pathlib import Path
import unittest


class LocalLauncherTests(unittest.TestCase):
    def test_launcher_restarts_only_its_own_service_and_closes_its_old_terminal(self):
        launcher = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "start_local_5055.applescript"
        ).read_text(encoding="utf-8")

        self.assertIn("if runningCwd is not projectPath then", launcher)
        self.assertIn('do shell script "kill -TERM "', launcher)
        self.assertIn('set shellPid to do shell script "ps -o ppid= -p "', launcher)
        self.assertIn('if shellTTY is runningTTY then do shell script "kill -HUP "', launcher)
        self.assertNotIn('tell application "Terminal"', launcher)
        self.assertIn('& linefeed & "exit"', launcher)
