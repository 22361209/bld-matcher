"""Exercise privileged command boundaries using shell functions, never Docker/sudo."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Installed wrapper runs on the Linux NAS")
class NasBackupWrapperTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        for name in ("data", "uploads", "outputs"):
            (self.checkout / name).mkdir()
        (self.checkout / ".env").write_text("fixture")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.helper = self.root / "backup.py"
        self.helper.write_text("fixture")
        self.log = self.root / "calls"
        self.lock = self.root / "lock"
        wrapper = (ROOT / "tools/nas/rebuild-bld-matcher").read_text()
        functions, main = wrapper.split('[ "$(id -u)" = 0 ]', 1)
        self.script = functions + f"""
CHECKOUT={shlex.quote(str(self.checkout))}
BACKUPS={shlex.quote(str(self.backups))}
HELPER={shlex.quote(str(self.helper))}
id() {{ echo 0; }}
docker_local() {{
    printf '%s\\n' "$*" >> {shlex.quote(str(self.log))}
    case "$1" in
        inspect)
            case "$5" in
                '{{{{.Id}}}}') echo container-id ;;
                '{{{{.Image}}}}') echo sha256:fixture ;;
                *) echo "$CHECKOUT|${{SERVICE:-bld-matcher}}" ;;
            esac ;;
        stop) [ "${{FAIL:-}}" != stop ] ;;
        run) [ "${{FAIL:-}}" != run ] ;;
    esac
}}
compose_local() {{ echo "compose $*" >> {shlex.quote(str(self.log))}; }}
[ "$(id -u)" = 0 ]""" + main.replace("lock=/var/run/quick-bld-matcher.lock", f"lock={shlex.quote(str(self.lock))}")

    def run_wrapper(self, *args, **env):
        return subprocess.run(["sh", "-s", "--", *args], input=self.script, text=True,
                              capture_output=True, env={**os.environ, **env}, check=False)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_backup_stops_exact_container_and_mounts_only_fixed_runtime_paths(self):
        result = self.run_wrapper("backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertLess(calls.index("stop container-id"), calls.index("run --rm"))
        for name in ("data", "uploads", "outputs", ".env"):
            self.assertIn(f"src={self.checkout}/{name},dst=/runtime/{name},readonly", calls)
        self.assertIn(f"src={self.helper},dst=/opt/bld-backup/backup_local_runtime.py,readonly", calls)
        self.assertIn("--entrypoint python sha256:fixture /opt/bld-backup/backup_local_runtime.py", calls)
        self.assertNotIn(f"src={self.checkout},", calls)
        self.assertFalse(any(line.startswith("compose ") for line in calls.splitlines()))
        self.assertFalse(self.lock.exists())

    def test_backup_refuses_wrong_container_symlinks_and_concurrent_release(self):
        self.assertNotEqual(self.run_wrapper("backup", SERVICE="other").returncode, 0)
        self.assertNotIn("stop ", self.calls())
        self.lock.mkdir()
        self.assertNotEqual(self.run_wrapper("backup").returncode, 0)
        self.lock.rmdir()
        self.helper.unlink()
        self.helper.symlink_to(self.root / "outside")
        self.assertNotEqual(self.run_wrapper("backup").returncode, 0)
        self.assertNotIn("stop ", self.calls())

    def test_failures_do_not_rebuild_or_leave_lock_and_extra_arguments_are_rejected(self):
        for failure in ("stop", "run"):
            result = self.run_wrapper("backup", FAIL=failure)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(line.startswith("compose ") for line in self.calls().splitlines()))
            self.assertFalse(self.lock.exists())
        for args in ((), ("backup", "anything"), ("stop",), ("unknown",)):
            self.assertNotEqual(self.run_wrapper(*args).returncode, 0)
        self.assertEqual(self.run_wrapper("status").returncode, 0)
        self.assertEqual(self.run_wrapper("rebuild").returncode, 0)
        self.assertIn("compose up -d --build", self.calls())
