"""Verify and publish a committed main to Synology over the project's SSH Git remote."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BARE = "/volume1/git/bld-matcher.git"
WRAPPER = "/usr/local/sbin/quick-bld-matcher"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def nas_target(remote: str) -> tuple[str, int | None]:
    if "\n" in remote:
        raise ValueError("nas 只能配置一个 push URL。")
    if remote.startswith("ssh://"):
        url = urlsplit(remote)
        if url.username != "deploy" or url.password or url.path != BARE or url.query or url.fragment:
            raise ValueError("nas 必须使用 deploy 账号和项目指定的 NAS Git 仓库。")
        host, port = url.hostname or "", url.port
    else:
        match = re.fullmatch(r"deploy@([a-zA-Z0-9.-]+):" + re.escape(BARE), remote)
        if not match:
            raise ValueError("nas 必须是指向项目指定 NAS Git 仓库的 SSH remote。")
        host, port = match[1], None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
        raise ValueError("nas 的 SSH 主机名无效。")
    return "deploy@" + host, port


def clean_main() -> str:
    if git("branch", "--show-current") != "main" or git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("请先在 main 完成提交，使工作区保持干净。")
    return git("rev-parse", "--verify", "HEAD^{commit}")


def check_protected_paths(paths: bytes) -> None:
    for path in paths.split(b"\0"):
        root = path.split(b"/", 1)[0]
        if root in {b".env", b"data", b"uploads", b"outputs", b"logs"} or (
            root.startswith(b".env.") and root != b".env.example"
        ) or path == b"static/product_images" or path.startswith(b"static/product_images/"):
            raise ValueError("提交包含运行数据或环境文件，已停止发布。")


def check_protected_tree(revision: str) -> None:
    check_protected_paths(subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", revision], cwd=ROOT))


def check_outgoing_history(base: str, revision: str) -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", base, revision], cwd=ROOT, check=True)
    check_protected_tree(base)
    # Git transfers intermediate commits too, even when the tip no longer tracks a private file.
    paths = subprocess.check_output(
        ["git", "log", "--full-history", "-m", "--no-renames", "--format=", "--name-only", "-z", f"{base}..{revision}"],
        cwd=ROOT,
    )
    check_protected_paths(paths)


def verify_commit(revision: str, base: str) -> None:
    # A second editor or Codex session can change the worktree during verification.
    # A local object-sharing clone tests the exact bytes we will deliver over Git.
    with tempfile.TemporaryDirectory(prefix="bld-nas-verify-") as folder:
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), folder], check=True)
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", "--detach", revision],
                       cwd=folder, check=True)
        subprocess.run(["uv", "run", "python", "scripts/verify.py", "--base-ref", base], cwd=folder, check=True)


def publish(identity_file: Path | None) -> None:
    revision = clean_main()
    check_protected_tree(revision)
    remote = git("remote", "get-url", "--push", "--all", "nas")
    target, port = nas_target(remote)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12"]
    if identity_file is None:
        default_key = Path.home() / ".ssh" / "bld_matcher_deploy"
        identity_file = default_key if default_key.is_file() else None
    if identity_file is not None:
        key = identity_file.expanduser().resolve(strict=True)
        ssh += ["-i", key.as_posix()]
    print(f"验证 main {revision}，通过后推送到 {target}。", flush=True)
    # Git invokes SSH through a POSIX shell, including Git for Windows.
    git_env = {**os.environ, "GIT_SSH_COMMAND": shlex.join(ssh), "GIT_SSH_VARIANT": "ssh", "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "fetch", "--no-tags", "--recurse-submodules=no", remote, "refs/heads/main"],
                   cwd=ROOT, env=git_env, check=True)
    base = git("rev-parse", "FETCH_HEAD")
    check_outgoing_history(base, revision)
    verify_commit(revision, base)
    if clean_main() != revision or git("remote", "get-url", "--push", "--all", "nas") != remote:
        raise ValueError("验证期间 main 或推送目标已变更，请重新发布。")
    subprocess.run(["git", "push", "--no-follow-tags", "--recurse-submodules=no", remote, f"{revision}:refs/heads/main"],
                   cwd=ROOT, env=git_env, check=True)
    print("Git 推送成功，正在让 NAS 启用该提交。", flush=True)
    ssh_port = ["-p", str(port)] if port else []
    subprocess.run([*ssh, *ssh_port, "-T", target, f"sudo -n {WRAPPER}"], input=(revision + "\n").encode(), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-file", type=Path, help="本机部署用的 SSH 私钥；也可使用 SSH config/agent。")
    args = parser.parse_args()
    try:
        publish(args.identity_file)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"NAS 快速更新未完成：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
