"""Exercise Git publication boundaries without a NAS or elevated privileges."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import nas_quick_update as client


ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE).strip()


class NasQuickUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bare = self.root / "server.git"
        self.client = self.root / "client"
        self.checkout = self.root / "server"
        git(self.root, "init", "--bare", "-b", "main", str(self.bare))
        git(self.root, "clone", str(self.bare), str(self.client))
        git(self.client, "config", "user.email", "test@example.invalid")
        git(self.client, "config", "user.name", "Test Fixture")
        (self.client / ".gitignore").write_text("data/\n.env\n")
        (self.client / "style.css").write_text("body { color: black; }")
        self.first = self.commit_and_push("initial")
        git(self.root, "clone", str(self.bare), str(self.checkout))
        (self.checkout / "data").mkdir()
        self.product_db = self.checkout / "data/products.sqlite3"
        self.product_db.write_bytes(b"fixture: private NAS data")
        self.wrapper = (ROOT / "tools/nas/quick-bld-matcher").read_text()

    def commit_and_push(self, message: str) -> str:
        git(self.client, "add", "-A")
        git(self.client, "commit", "-m", message)
        git(self.client, "push", "origin", "HEAD:main")
        return git(self.client, "rev-parse", "HEAD")

    def prepare_on_nas(self, revision: str) -> subprocess.CompletedProcess[bytes]:
        # Exercise the installed wrapper's unprivileged Git phase on two real
        # clones. The sudo and Docker phases require the installed NAS service.
        functions, call_main = self.wrapper.rsplit('\nmain "$@"', 1)
        self.assertFalse(call_main.strip())
        script = functions + '\nBARE=$1\nCHECKOUT=$2\narchive_as_deploy "$3"\n'
        return subprocess.run(
            ["sh", "-s", "--", str(self.bare), str(self.checkout), revision], input=script.encode(),
            capture_output=True, check=False,
        )

    def test_fast_forward_packages_exact_commit_and_preserves_runtime_files(self) -> None:
        (self.client / "style.css").write_text("body { color: blue; }")
        target = self.commit_and_push("style")
        result = self.prepare_on_nas(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        tar_path = self.root / "source.tar"
        tar_path.write_bytes(result.stdout)
        with tarfile.open(tar_path) as stream:
            self.assertEqual(set(stream.getnames()), {".gitignore", "style.css"})
            self.assertEqual(stream.extractfile("style.css").read(), b"body { color: blue; }")
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), target)
        self.assertEqual(self.product_db.read_bytes(), b"fixture: private NAS data")

    def test_competing_push_dirty_checkout_and_other_origin_are_refused(self) -> None:
        (self.client / "style.css").write_text("body { color: red; }")
        target = self.commit_and_push("new main")
        self.assertNotEqual(self.prepare_on_nas(self.first).returncode, 0)
        (self.checkout / "style.css").write_text("NAS work in progress")
        self.assertNotEqual(self.prepare_on_nas(target).returncode, 0)
        git(self.checkout, "restore", "style.css")
        git(self.checkout, "remote", "set-url", "origin", str(self.client))
        self.assertNotEqual(self.prepare_on_nas(target).returncode, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.first)

    def test_protected_git_tree_rejected_before_checkout_changes(self) -> None:
        (self.client / ".env").write_text("fixture")
        git(self.client, "add", "-f", ".env")
        target = self.commit_and_push("unsafe fixture")
        with patch.object(client, "ROOT", self.client), self.assertRaises(ValueError):
            client.check_protected_tree(target)
        self.assertNotEqual(self.prepare_on_nas(target).returncode, 0)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.first)

    def test_client_pushes_and_activates_the_verified_sha_via_separate_ssh_calls(self) -> None:
        (self.client / "style.css").write_text("body { color: orange; }")
        git(self.client, "commit", "-am", "target")
        target = git(self.client, "rev-parse", "HEAD")
        git(self.client, "remote", "add", "nas", "deploy@nas-test:/volume1/git/bld-matcher.git")
        bindir = self.root / "bin"
        bindir.mkdir()
        verifier = bindir / "uv"
        verifier.write_text('#!/bin/sh\n[ "$*" = "run python scripts/verify.py --base-ref $BASE" ] || exit 1\n'
                            'git rev-parse HEAD > "$CHECKED"\n')
        verifier.chmod(0o755)
        ssh = bindir / "ssh"
        ssh.write_text("""#!/bin/sh
set -eu
for last do :; done
case "$last" in
    "git-upload-pack "*) exec git-upload-pack "$NAS_BARE";;
    "git-receive-pack "*) test -f "$CHECKED" && exec git-receive-pack "$NAS_BARE";;
    "sudo -n /usr/local/sbin/quick-bld-matcher")
        cat > "$APPLIED"
        [ "$(cat "$APPLIED")" = "$(git -C "$NAS_BARE" rev-parse main)" ];;
    *) exit 42;;
esac
""")
        ssh.chmod(0o755)
        applied = self.root / "activated"
        env = {"PATH": str(bindir) + os.pathsep + os.environ["PATH"], "CHECKED": str(self.root / "checked"),
               "NAS_BARE": str(self.bare), "APPLIED": str(applied), "BASE": self.first}
        with patch.object(client, "ROOT", self.client), patch.dict(os.environ, env):
            client.publish(None)
        self.assertEqual(git(self.bare, "rev-parse", "main"), target)
        self.assertEqual(applied.read_text().strip(), target)
        self.assertEqual((self.root / "checked").read_text().strip(), target)

    def test_secret_in_intermediate_commit_rejects_an_otherwise_clean_tip(self) -> None:
        (self.client / ".env").write_text("fixture")
        git(self.client, "add", "-f", ".env")
        git(self.client, "commit", "-m", "temporarily tracked file")
        git(self.client, "rm", ".env")
        git(self.client, "commit", "-m", "removed file")
        head = git(self.client, "rev-parse", "HEAD")
        with patch.object(client, "ROOT", self.client):
            client.check_protected_tree(head)
            with self.assertRaises(ValueError):
                client.check_outgoing_history(self.first, head)

    def test_ssh_destination_uses_the_configured_nas_git_remote(self) -> None:
        self.assertEqual(client.nas_target("ssh://deploy@office-nas:9922/volume1/git/bld-matcher.git"),
                         ("deploy@office-nas", 9922))
        self.assertEqual(client.nas_target("deploy@192.168.110.93:/volume1/git/bld-matcher.git"),
                         ("deploy@192.168.110.93", None))
        for invalid in ("https://github.com/org/repo", "deploy@-ProxyCommand:/volume1/git/bld-matcher.git",
                        "deploy@nas:/some-other-repo", "deploy@nas:/volume1/git/bld-matcher.git\nx"):
            with self.assertRaises(ValueError):
                client.nas_target(invalid)
