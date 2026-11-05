from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import TypedDict
import unittest


class CommandCall(TypedDict):
    command: str
    args: list[str]


FAKE_COMMAND = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"command": name, "args": args}) + "\n")
if name == "lsof":
    sys.exit(0 if os.environ.get("FAKE_PORT_BUSY") == "1" else 1)
if name == "uv":
    if os.environ.get("FAKE_FAIL") == args[-1]:
        sys.exit(12)
    sys.exit(0)
if args[:2] != ["--context", "desktop-linux"]:
    sys.exit(90)
args = args[2:]
if args[:2] == ["context", "inspect"]:
    print(os.environ.get("FAKE_ENDPOINT", "unix:///fake/docker.sock"))
elif args[0] == "info":
    print("linux")
elif args[0] == "inspect":
    if os.environ.get("FAKE_EXISTS") != "1":
        sys.exit(1)
    workspace = os.environ.get("FAKE_WORKSPACE", os.environ["FAKE_PROJECT"])
    running = "true" if os.environ.get("FAKE_RUNNING") == "1" else "false"
    print(workspace + "|bld-local|" + running)
elif args[0] == "compose":
    if os.environ.get("FAKE_FAIL") in args:
        sys.exit(13)
else:
    sys.exit(91)
'''


class DockerLocalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "tools").mkdir()
        (self.root / "bin").mkdir()
        (self.root / ".env").write_text("TEST_FIXTURE=1\n")
        source = Path(__file__).resolve().parents[1] / "tools" / "docker_local.sh"
        self.script = self.root / "tools" / "docker_local.sh"
        shutil.copyfile(source, self.script)
        for name in ("docker", "uv", "lsof"):
            path = self.root / "bin" / name
            path.write_text(FAKE_COMMAND, encoding="utf-8")
            path.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
            "FAKE_LOG": str(self.log),
            "FAKE_PROJECT": str(self.root),
            "DOCKER_HOST": "ssh://nas.invalid",
            "DOCKER_CONTEXT": "nas",
        }

    def run_action(self, action: str, **settings: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), action],
            env={**self.env, **settings},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def calls(self) -> list[CommandCall]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def compose_actions(self) -> list[str]:
        actions = []
        for call in self.calls():
            args = call["args"]
            if call["command"] == "docker" and "compose" in args:
                actions.append(args[args.index("-f") + 2])
        return actions

    def test_rejects_remote_desktop_context_before_daemon_access(self) -> None:
        result = self.run_action("update", FAKE_ENDPOINT="ssh://nas.invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unix socket", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_rejects_same_container_name_owned_by_another_workspace(self) -> None:
        result = self.run_action("stop", FAKE_EXISTS="1", FAKE_WORKSPACE="/another/workspace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不属于当前工作区", result.stderr)
        self.assertEqual(self.compose_actions(), [])

    def test_start_refuses_an_occupied_port_without_stopping_anything(self) -> None:
        result = self.run_action("start", FAKE_EXISTS="1", FAKE_PORT_BUSY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("5055", result.stderr)
        self.assertEqual(self.compose_actions(), [])

    def test_build_failure_leaves_the_previous_service_untouched(self) -> None:
        result = self.run_action("update", FAKE_FAIL="build")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_actions(), ["build"])
        self.assertFalse(any("tools/backup_local_runtime.py" in c["args"] for c in self.calls()))

    def test_verification_failure_does_not_build_or_stop(self) -> None:
        result = self.run_action("update", FAKE_FAIL="scripts/verify.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_actions(), [])

    def test_backup_failure_does_not_migrate_or_start(self) -> None:
        result = self.run_action("update", FAKE_FAIL="tools/backup_local_runtime.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_actions(), ["build", "stop"])

    def test_migration_failure_does_not_start_the_new_service(self) -> None:
        result = self.run_action("update", FAKE_FAIL="run")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_actions(), ["build", "stop", "run"])

    def test_update_uses_local_context_and_isolated_compose_after_gates(self) -> None:
        result = self.run_action("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compose_actions(), ["build", "stop", "run", "up", "exec"])
        calls = self.calls()
        verify = next(i for i, c in enumerate(calls) if "scripts/verify.py" in c["args"])
        backup = next(i for i, c in enumerate(calls) if "tools/backup_local_runtime.py" in c["args"])
        migrate = next(
            i for i, c in enumerate(calls)
            if c["command"] == "docker" and "run" in c["args"]
        )
        self.assertLess(verify, backup)
        self.assertLess(backup, migrate)
        for call in calls:
            if call["command"] == "docker":
                self.assertEqual(call["args"][:2], ["--context", "desktop-linux"])
                if "compose" in call["args"]:
                    self.assertIn(str(self.root / "compose.local.yml"), call["args"])
        migration = calls[migrate]["args"]
        self.assertNotIn("--allow-failures", " ".join(migration))
        self.assertIn("--rm", migration)

    def test_start_reuses_existing_container_without_build_or_migration(self) -> None:
        result = self.run_action("start", FAKE_EXISTS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compose_actions(), ["start", "exec"])
        self.assertFalse(any(c["command"] == "uv" for c in self.calls()))
