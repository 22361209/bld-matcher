from pathlib import Path
import unittest


class LocalLauncherTests(unittest.TestCase):
    def test_launcher_restarts_only_its_own_service_and_closes_its_managed_terminal(self):
        launcher = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "start_local_5055.applescript"
        ).read_text(encoding="utf-8")

        self.assertIn("if runningCwd is not projectPath then", launcher)
        self.assertIn('do shell script "kill -TERM "', launcher)
        self.assertIn('property windowStatePath : "/tmp/bld-local-5055-window-id"', launcher)
        self.assertIn('set serviceTab to do script ""', launcher)
        self.assertIn("set serviceWindowId to id of front window", launcher)
        self.assertIn("set targetWindow to first window whose id is (windowId as integer)", launcher)
        self.assertIn("close targetWindow", launcher)
        self.assertIn('do shell script "rm -f " & quoted form of windowStatePath', launcher)
        self.assertNotIn("set closeCommand", launcher)
        self.assertNotIn("osascript -e", launcher)
        self.assertLess(
            launcher.index("set managedWindowId"),
            launcher.index('do shell script "kill -TERM "'),
        )

    def test_bld_app_and_command_line_share_the_same_restart_script(self):
        root = Path(__file__).resolve().parents[1]
        restart_script = (root / "tools" / "restart_local_5055.sh").read_text(
            encoding="utf-8"
        )
        app_launcher = (root / "tools" / "bld_launcher.applescript").read_text(
            encoding="utf-8"
        )
        installer = (root / "tools" / "install_bld_launcher.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('TEMPLATE="$PROJECT_DIR/tools/start_local_5055.applescript"', restart_script)
        self.assertIn('exec osascript "$TMP_SCRIPT"', restart_script)
        self.assertIn('property restartScriptPath : "__RESTART_SCRIPT_PATH__"', app_launcher)
        self.assertIn('do shell script quoted form of restartScriptPath', app_launcher)
        self.assertIn('TEMPLATE="$PROJECT_DIR/tools/bld_launcher.applescript"', installer)
        self.assertIn('"$PROJECT_DIR/tools/restart_local_5055.sh"', installer)
