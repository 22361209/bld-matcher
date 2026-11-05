"""Prepare a code-only release compatible with the files baked into the running image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile


class ReleaseError(ValueError):
    pass


class UnsupportedFile(ReleaseError):
    pass


# Only these locations get published into the application root. All other operational
# inputs (including our own publisher and Dockerfile) must match the built image.
DATA_ROOTS = {"data", "uploads", "outputs"}
PRIVATE_ROOTS = DATA_ROOTS | {"logs", "产品目录", "询价", "单价"}
CACHE_NAMES = {"__pycache__", ".DS_Store", ".git", ".venv", "venv", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".playwright-cli"}
GUARDED_PYTHON = {
    "app.py", "wsgi.py", "app/__init__.py", "app/bld_sort.py", "app/config.py", "app/database.py",
    "app/migrations.py", "app/product_image_processing.py", "app/product_image_migration.py",
    "app/product_media.py", "app/security.py", "app/modules/products/option_values.py",
    "app/modules/products/catalog_language.py",
}
RUNTIME_SUFFIXES = {
    "static": {".css", ".js", ".svg", ".woff2", ".txt"},
    "templates": {".html"},
}
SAFE_DATA = {"none", "no-schema-change"}
SAFE_OPERATIONS = {"none", "restart-required", "quick-update", "documentation-only"}


def excluded(path: PurePosixPath) -> bool:
    parts = path.parts
    return (
        bool(CACHE_NAMES.intersection(parts))
        or path.suffix in {".pyc", ".pyo"}
        or parts[0] in {"tests", ".github", ".env.example", ".quick-baseline.json", ".deployment-version"}
        or (parts[0] == "docs" and len(parts) > 1 and parts[1] != "api")
        or parts[:2] == ("tools", "icon_build")
        or (len(parts) == 1 and path.suffix == ".md" and path.name != "项目交接说明.md")
        or str(path) == "changes/README.md"
    )


def private(path: PurePosixPath) -> bool:
    return (
        path.parts[0] in PRIVATE_ROOTS
        or path.parts[:2] == ("static", "product_images")
        or any(part.lower().startswith(".env") and part != ".env.example" for part in path.parts)
        or path.suffix.lower() in {".pem", ".key", ".p12", ".sqlite", ".sqlite3", ".db"}
    )


def category(path: PurePosixPath) -> str:
    """hot and frozen files belong in the candidate, guard files only in the compatibility check."""
    name = str(path)
    top = path.parts[0]
    if name in GUARDED_PYTHON or name == "static/catalog-import-template.xlsx":
        return "frozen"
    if top == "app" and path.suffix == ".py":
        if (path.parts[:2] == ("app", "platform") or path.parts[:3] == ("app", "modules", "admin")
                or any(token in path.stem for token in ("migrat", "schema", "permission", "bootstrap", "auth"))):
            return "frozen"
        return "hot"
    if (path.suffix in RUNTIME_SUFFIXES.get(top, set()) or name == "项目交接说明.md"
            or (len(path.parts) == 2 and top == "changes" and path.suffix == ".json")
            or (path.parts[:2] == ("docs", "api") and path.suffix == ".md")):
        return "hot"
    if name in {".dockerignore", ".gitignore", "Dockerfile", "requirements.txt", "pyproject.toml", "uv.lock",
                "docker-compose.yml", "compose.local.yml", "NOTICE"}:
        return "guard"
    if str(path) == "tools/nas/quick-bld-matcher":
        return "guard"
    if top in {"contracts", "policy"} and path.suffix == ".json":
        return "guard"
    if top in {"scripts", "tools"} and path.suffix in {".py", ".sh", ".applescript", ".icns", ".sudoers"}:
        return "guard"
    raise UnsupportedFile(f"快速发布未识别文件类型，请使用完整更新并核对发布规则：{path}")


def source_files(source: Path):
    def walk(directory):
        for child in sorted(directory.iterdir()):
            path = PurePosixPath(child.relative_to(source).as_posix())
            if excluded(path):
                continue
            if private(path):
                # The full-image build intentionally creates these empty mount points.
                if len(path.parts) == 1 and path.name in DATA_ROOTS and not child.is_symlink() and child.is_dir() and not any(child.iterdir()):
                    continue
                raise ReleaseError(f"候选含运行数据或敏感路径：{path}")
            mode = child.lstat().st_mode
            if stat.S_ISDIR(mode):
                yield from walk(child)
            elif stat.S_ISREG(mode):
                yield str(path), category(path)
            else:
                raise ReleaseError(f"候选只允许普通文件：{path}")
    yield from walk(source)


def scan(source: Path, destination: Path | None = None) -> dict:
    files = {}
    for name, kind in source_files(source):
        contents = (source / name).read_bytes()
        files[name] = hashlib.sha256(contents).hexdigest()
        if destination is not None and kind in {"hot", "frozen"}:
            published = destination / name
            published.parent.mkdir(parents=True, exist_ok=True)
            published.write_bytes(contents)
            published.chmod(0o644)
    if not {"wsgi.py", "app.py", "app/config.py", "requirements.txt"} <= files.keys():
        raise ReleaseError("镜像或候选缺少应用入口、配置或依赖清单。")
    return {"format": 1, "files": files}


def baseline(source: Path, output: Path) -> None:
    try:
        manifest = scan(source)
    except UnsupportedFile as exc:
        # A future resource type can still be shipped in a full image. Disable
        # quick releases until the snapshot policy can preserve the resource.
        manifest = {"format": 1, "quick_disabled": str(exc)}
    output.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def check_changes(built: dict, new: dict, destination: Path) -> None:
    if isinstance(built, dict) and built.get("quick_disabled"):
        raise ReleaseError(f"此镜像仅支持完整更新：{built['quick_disabled']}")
    if not isinstance(built, dict) or built.get("format") != 1 or not isinstance(built.get("files"), dict):
        raise ReleaseError("镜像缺少兼容的快速发布基线，请先完整更新。")
    old, candidate = built["files"], new["files"]
    changed = sorted(p for p in old.keys() | candidate.keys() if old.get(p) != candidate.get(p))
    blocked = [p for p in changed if category(PurePosixPath(p)) != "hot"]
    if blocked:
        raise ReleaseError("这些文件需要完整更新：" + "、".join(blocked))
    fragments = [p for p in changed if p.startswith("changes/") and p in candidate]
    if changed and not fragments:
        raise ReleaseError("应用差异缺少新增或更新的 changes/*.json 片段。")
    for fragment in fragments:
        payload = json.loads((destination / fragment).read_text(encoding="utf-8"))
        impact = payload.get("impact") if isinstance(payload, dict) else None
        if (not isinstance(impact, dict) or impact.get("data") not in SAFE_DATA
                or impact.get("operations") not in SAFE_OPERATIONS
                or impact.get("api") not in {"none", "unchanged", "compatible"}):
            raise ReleaseError(f"{fragment} 未声明可快速发布的数据、接口与运维影响，请使用完整更新。")


def prepare(source: Path, baseline: Path, output: Path, revision: str, previous: Path | None = None) -> None:
    if not re.fullmatch(r"[0-9a-f]{7,64}(?:-[0-9a-f]{12})?", revision):
        raise ReleaseError("版本必须为 Git 哈希，可附 12 位工作树摘要。")
    if output.exists() or output.is_symlink():
        raise ReleaseError("候选目录已存在，拒绝覆盖。")
    built = json.loads(baseline.read_text(encoding="utf-8"))
    stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=output.parent))
    try:
        manifest = scan(source, stage)
        check_changes(built, manifest, stage)
        if previous is not None and manifest != built:
            check_changes(json.loads(previous.read_text(encoding="utf-8")), manifest, stage)
        (stage / ".quick-baseline.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        (stage / ".deployment-version").write_text(revision + "\n", encoding="utf-8")
        # Some legacy font, workbook and image readers resolve these from the code root.
        for name in DATA_ROOTS:
            (stage / name).symlink_to("/app/" + name, target_is_directory=True)
        for directory, _, _ in os.walk(stage):
            Path(directory).chmod(0o755)
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def pack(source: Path, output: Path) -> None:
    """Package only reviewed project paths. Ignored business files never enter the tar."""
    listing = subprocess.check_output(["git", "-C", str(source), "ls-files", "-coz", "--exclude-standard"])
    entries = sorted(set(name for name in os.fsdecode(listing).split("\0") if name))
    # Build alongside the destination so a failure or Ctrl-C leaves no partial tar.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".pack-", delete=False) as temp:
        archive = Path(temp.name)
    try:
        with tarfile.open(archive, "w") as stream:
            for name in entries:
                path = PurePosixPath(name)
                if private(path):
                    raise ReleaseError(f"Git 文件清单含运行数据或敏感路径：{path}")
                if excluded(path):
                    continue
                category(path)
                entry = source / name
                if entry.resolve() != source.resolve() / name:
                    raise ReleaseError(f"快速发布拒绝符号链接：{name}")
                if not entry.exists():  # tracked deletion in a local working tree
                    continue
                if not stat.S_ISREG(entry.lstat().st_mode):
                    raise ReleaseError(f"候选只允许普通文件：{name}")
                # Construct headers explicitly so owner, mtime and macOS pax fields
                # never change the ID of an otherwise identical release.
                info = tarfile.TarInfo(name)
                with os.fdopen(os.open(entry, os.O_RDONLY | os.O_NOFOLLOW), "rb") as data:
                    info.size = os.fstat(data.fileno()).st_size
                    stream.addfile(info, data)
        archive.replace(output)
    finally:
        if archive.exists():
            archive.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("baseline", "prepare", "pack"):
        action = sub.add_parser(command)
        action.add_argument("--source", required=True, type=Path)
        action.add_argument("--output", required=True, type=Path)
        if command == "prepare":
            action.add_argument("--baseline", required=True, type=Path)
            action.add_argument("--previous", type=Path)
            action.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.source, args.baseline, args.output, args.revision, args.previous)
        elif args.command == "pack":
            pack(args.source, args.output)
        else:
            baseline(args.source, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"快速发布停止：{exc}\n")


if __name__ == "__main__":
    main()
