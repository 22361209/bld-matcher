from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "policy" / "legacy_allowlist.json"
ROUTE_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
UI_WRITE_GUARDS = {"login_required", "permission_required"}
LEGACY_API_GUARDS = {"internal_api_required", "api_scope_required"}
REQUIRED_DOCKER_IGNORES = {".env*", "data/", "logs/", "outputs/", "uploads/"}
REQUIRED_DOCS = {
    "PROJECT_CONSTITUTION.md",
    "docs/adr/0001-project-governance-and-stack-baseline.md",
    "docs/api/ai-contract.md",
    "docs/architecture/overview.md",
    "docs/governance/enforcement-matrix.md",
    "docs/ui/page-protocol.md",
    "pyproject.toml",
    "templates/base.html",
    "uv.lock",
}
PAGE_TYPES = {"workbench", "list", "edit", "import-preview", "background-job", "system-admin"}
INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>", re.I | re.S)
INLINE_EVENT_RE = re.compile(r"\son[a-z]+\s*=", re.I)
INLINE_STYLE_RE = re.compile(r"<style\b|\sstyle\s*=", re.I)
PAGE_TYPE_RE = re.compile(
    r"{%\s*block\s+page_type\s*%}\s*([a-z-]+)\s*{%\s*endblock\s*%}",
    re.I,
)
ADR_REQUIRED_PATHS = {
    "Dockerfile",
    "PROJECT_CONSTITUTION.md",
    "docker-compose.yml",
    "docs/api/ai-contract.md",
    "docs/ui/page-protocol.md",
    "pyproject.toml",
}


def _run_git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _load_policy() -> dict[str, list[str]]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _policy_at_ref(ref: str | None) -> dict[str, list[str]] | None:
    if not ref:
        return None
    result = subprocess.run(
        ["git", "show", f"{ref}:policy/legacy_allowlist.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _decorator_name(decorator: ast.expr) -> str:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _literal_strings(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            values.update(_literal_strings(item))
        return values
    return set()


def _route_declaration(decorator: ast.expr) -> tuple[str, set[str]] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    method_name = decorator.func.attr.lower()
    if method_name not in ROUTE_METHODS and method_name != "route":
        return None
    path = decorator.args[0] if decorator.args else next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "rule"),
        None,
    )
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        return None
    if method_name in ROUTE_METHODS:
        return path.value, {ROUTE_METHODS[method_name]}
    methods = {"GET"}
    for keyword in decorator.keywords:
        if keyword.arg != "methods":
            continue
        literal_methods = {value.upper() for value in _literal_strings(keyword.value)}
        methods = literal_methods or {"UNKNOWN"}
    return path.value, methods


def _is_route_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr.lower() in {*ROUTE_METHODS, "route"}
    )


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_v1_api_path(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def _imports_database(tree: ast.AST) -> bool:
    return _database_import_count(tree) > 0


def _database_import_count(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"app.database", "sqlite3"}:
            count += len(node.names)
        if isinstance(node, ast.Import):
            count += sum(alias.name in {"app.database", "sqlite3"} for alias in node.names)
    return count


def _imports_route_adapter(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        for module in modules:
            if module.startswith("app.routes."):
                return True
            if re.fullmatch(r"app\.modules\.[^.]+\.(?:web|api)", module):
                return True
    return False


def _sql_call_count(tree: ast.AST) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    )


def _exception_exposure_count(tree: ast.AST) -> int:
    count = 0
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        if not isinstance(handler.type, ast.Name) or handler.type.id != "Exception" or not handler.name:
            continue
        exposed = False
        for statement in handler.body:
            for node in ast.walk(statement):
                if isinstance(node, ast.FormattedValue):
                    if any(isinstance(item, ast.Name) and item.id == handler.name for item in ast.walk(node.value)):
                        exposed = True
                        break
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
                    if any(isinstance(item, ast.Name) and item.id == handler.name for item in ast.walk(node)):
                        exposed = True
                        break
            if exposed:
                break
        if exposed:
            count += 1
    return count


def _daemon_thread_count(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if function_name != "Thread":
            continue
        if any(
            keyword.arg == "daemon"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            count += 1
    return count


def _check_count_policy(
    label: str,
    allowed: dict[str, int],
    actual: dict[str, int],
    errors: list[str],
) -> None:
    for path in sorted(set(allowed) | set(actual)):
        allowed_count = int(allowed.get(path, 0))
        actual_count = int(actual.get(path, 0))
        if actual_count > allowed_count:
            errors.append(f"{label} 禁止增加: {path} ({allowed_count} -> {actual_count})")
        elif actual_count < allowed_count:
            errors.append(f"{label} 已减少，请同步收紧白名单: {path} ({allowed_count} -> {actual_count})")


def _check_route_contracts(
    path: Path,
    tree: ast.AST,
    errors: list[str],
    public_endpoints: set[str],
    legacy_api_routes: set[str],
) -> tuple[bool, set[str]]:
    relative = path.relative_to(ROOT).as_posix()
    found_legacy_api_routes: set[str] = set()
    has_routes = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route_decorators = [decorator for decorator in node.decorator_list if _is_route_decorator(decorator)]
        if not route_decorators:
            continue
        has_routes = True
        endpoint = f"{relative}:{node.name}"
        declarations = []
        for decorator in route_decorators:
            declaration = _route_declaration(decorator)
            if declaration is None:
                errors.append(f"路由路径必须是静态字符串: {endpoint}")
                continue
            declarations.append(declaration)
        guards = {_decorator_name(decorator) for decorator in node.decorator_list}
        for route, methods in declarations:
            if "UNKNOWN" in methods:
                errors.append(f"路由 methods 必须使用静态字符串数组: {endpoint} ({route})")
            if _is_api_path(route):
                if _is_v1_api_path(route):
                    if "api_scope_required" not in guards:
                        errors.append(f"/api/v1 路由必须声明 Scope: {endpoint} ({route})")
                else:
                    legacy_key = f"{endpoint} {route}"
                    found_legacy_api_routes.add(legacy_key)
                    if legacy_key not in legacy_api_routes:
                        errors.append(f"新增 API 必须使用 /api/v1: {endpoint} ({route})")
                    if not guards.intersection(LEGACY_API_GUARDS):
                        errors.append(f"兼容 API 路由缺少认证装饰器: {endpoint} ({route})")
                continue
            if methods.intersection(WRITE_METHODS) and endpoint not in public_endpoints:
                if not guards.intersection(UI_WRITE_GUARDS):
                    errors.append(f"UI 写路由缺少鉴权装饰器: {endpoint} ({route})")
    return has_routes, found_legacy_api_routes


def _changed_paths(base_ref: str | None) -> set[str]:
    changed = set(_run_git("diff", "--name-only"))
    changed.update(_run_git("diff", "--cached", "--name-only"))
    changed.update(_run_git("ls-files", "--others", "--exclude-standard"))
    if base_ref:
        changed.update(_run_git("diff", "--name-only", f"{base_ref}...HEAD"))
    return changed


def _check_change_fragments(changed: set[str], errors: list[str]) -> None:
    fragments = sorted((ROOT / "changes").glob("*.json"))
    for fragment in fragments:
        try:
            payload = json.loads(fragment.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"变更片段无法读取: {fragment.relative_to(ROOT)}: {exc}")
            continue
        for field in ("date", "title", "entries", "impact"):
            if not payload.get(field):
                errors.append(f"变更片段缺少 {field}: {fragment.relative_to(ROOT)}")
        date = str(payload.get("date") or "")
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            errors.append(f"变更片段 date 必须使用 YYYY-MM-DD: {fragment.relative_to(ROOT)}")
        if date and not fragment.name.startswith(date.replace("-", "")):
            errors.append(f"变更片段文件名必须以日期开头: {fragment.relative_to(ROOT)}")
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries or not all(
            isinstance(item, str) and item.strip() for item in entries
        ):
            errors.append(f"变更片段 entries 必须是非空字符串数组: {fragment.relative_to(ROOT)}")
        if not isinstance(payload.get("impact"), dict):
            errors.append(f"变更片段 impact 必须是对象: {fragment.relative_to(ROOT)}")

    relevant = {
        path
        for path in changed
        if not path.startswith("changes/")
        and path != "项目交接说明.md"
        and not path.endswith(".md")
    }
    changed_fragments = {path for path in changed if path.startswith("changes/") and path.endswith(".json")}
    if relevant and not changed_fragments:
        errors.append("代码、配置或页面发生变化时，必须在 changes/ 中新增或更新 JSON 变更片段。")
    if changed.intersection(ADR_REQUIRED_PATHS):
        changed_adrs = {
            path
            for path in changed
            if path.startswith("docs/adr/") and path.endswith(".md") and not path.endswith("0000-template.md")
        }
        if not changed_adrs:
            errors.append("技术栈或核心协议发生变化时，必须新增或更新 ADR。")


def check(base_ref: str | None = None) -> list[str]:
    errors: list[str] = []
    policy = _load_policy()
    baseline_policy = _policy_at_ref(base_ref or "HEAD")
    tracked = set(_run_git("ls-files"))

    if baseline_policy:
        for key, values in policy.items():
            baseline_values = baseline_policy.get(key, {} if isinstance(values, dict) else [])
            if isinstance(values, dict):
                increases = [
                    f"{path} ({baseline_values.get(path, 0)} -> {count})"
                    for path, count in values.items()
                    if int(count) > int(baseline_values.get(path, 0))
                ]
                if increases:
                    errors.append(f"遗留计数白名单 {key} 禁止扩大: {', '.join(sorted(increases))}")
            else:
                additions = set(values) - set(baseline_values)
                if additions:
                    errors.append(f"遗留白名单 {key} 禁止扩大: {', '.join(sorted(additions))}")

    forbidden = [
        path
        for path in tracked
        if path == ".env"
        or path.startswith(("data/", "logs/", "outputs/", "uploads/"))
        or (path.startswith(".env.") and path != ".env.example")
    ]
    if forbidden:
        errors.append("Git 跟踪了运行数据或密钥文件: " + ", ".join(sorted(forbidden)))

    docker_ignores = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing_docker_ignores = REQUIRED_DOCKER_IGNORES - docker_ignores
    if missing_docker_ignores:
        errors.append(".dockerignore 缺少运行数据规则: " + ", ".join(sorted(missing_docker_ignores)))

    missing_docs = [path for path in REQUIRED_DOCS if not (ROOT / path).is_file()]
    if missing_docs:
        errors.append("缺少项目治理文档: " + ", ".join(sorted(missing_docs)))

    constitution = (ROOT / "PROJECT_CONSTITUTION.md").read_text(encoding="utf-8")
    enforcement_matrix = (ROOT / "docs" / "governance" / "enforcement-matrix.md").read_text(encoding="utf-8")
    rule_ids = set(re.findall(r"`([A-Z]+-\d{3})\s+(?:MUST|MUST NOT)`", constitution))
    matrix_ids = set(re.findall(r"`([A-Z]+-\d{3})`", enforcement_matrix))
    missing_rule_ids = rule_ids - matrix_ids
    unknown_rule_ids = matrix_ids - rule_ids
    if missing_rule_ids:
        errors.append("治理执行矩阵缺少规则: " + ", ".join(sorted(missing_rule_ids)))
    if unknown_rule_ids:
        errors.append("治理执行矩阵包含未知规则: " + ", ".join(sorted(unknown_rule_ids)))

    legacy_pages = set(policy["legacy_full_page_templates"])
    allowed_inline_counts = policy["legacy_inline_script_counts"]
    allowed_style_counts = policy["legacy_inline_style_counts"]
    actual_inline_counts: dict[str, int] = {}
    actual_style_counts: dict[str, int] = {}
    for path in sorted((ROOT / "templates").rglob("*.html")):
        relative = path.relative_to(ROOT).as_posix()
        template = path.read_text(encoding="utf-8")
        is_full_page = bool(re.search(r"<!doctype\s+html>|<html\b", template, re.I))
        extends_base = bool(re.search(r"{%\s*extends\s+['\"]base\.html['\"]\s*%}", template))
        inline_count = len(INLINE_SCRIPT_RE.findall(template)) + len(INLINE_EVENT_RE.findall(template))
        style_count = len(INLINE_STYLE_RE.findall(template))
        if inline_count:
            actual_inline_counts[relative] = inline_count
        if style_count:
            actual_style_counts[relative] = style_count

        if relative == "templates/base.html":
            if not is_full_page or extends_base:
                errors.append("templates/base.html 必须是唯一基础文档模板。")
        elif relative in legacy_pages:
            if not is_full_page or extends_base:
                errors.append(f"页面已完成基础模板迁移，请从 legacy_full_page_templates 删除: {relative}")
        elif not extends_base and not path.name.startswith("_"):
            errors.append(f"新页面必须继承 base.html；共享片段文件名必须以下划线开头: {relative}")

        if extends_base:
            page_type_match = PAGE_TYPE_RE.search(template)
            if not re.search(r"{%\s*block\s+page_id\s*%}", template):
                errors.append(f"协议页面必须声明 page_id: {relative}")
            if not page_type_match or page_type_match.group(1) not in PAGE_TYPES:
                errors.append(f"协议页面必须声明有效 page_type: {relative}")
    _check_count_policy("模板内联脚本或事件处理器", allowed_inline_counts, actual_inline_counts, errors)
    _check_count_policy("模板内联样式", allowed_style_counts, actual_style_counts, errors)

    legacy_database = set(policy["legacy_route_database_access"])
    legacy_route_imports = set(policy["legacy_route_imports"])
    legacy_api_routes = set(policy["legacy_api_routes"])
    found_database_access: set[str] = set()
    found_route_imports: set[str] = set()
    found_legacy_api_routes: set[str] = set()
    actual_sql_counts: dict[str, int] = {}
    actual_database_import_counts: dict[str, int] = {}
    actual_exception_counts: dict[str, int] = {}
    actual_daemon_counts: dict[str, int] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        daemon_count = _daemon_thread_count(tree)
        if daemon_count:
            actual_daemon_counts[relative] = daemon_count
        has_routes, found_api = _check_route_contracts(
            path,
            tree,
            errors,
            set(policy["public_write_endpoints"]),
            legacy_api_routes,
        )
        found_legacy_api_routes.update(found_api)
        if not has_routes:
            continue
        database_import_count = _database_import_count(tree)
        sql_count = _sql_call_count(tree)
        exception_count = _exception_exposure_count(tree)
        if database_import_count:
            actual_database_import_counts[relative] = database_import_count
        if sql_count:
            actual_sql_counts[relative] = sql_count
        if exception_count:
            actual_exception_counts[relative] = exception_count
        if _imports_database(tree):
            found_database_access.add(relative)
            if relative not in legacy_database:
                errors.append(f"新路由模块禁止直接访问数据库: {relative}")
        if _imports_route_adapter(tree):
            found_route_imports.add(relative)
            if relative not in legacy_route_imports:
                errors.append(f"路由模块禁止互相导入: {relative}")

    stale_database = legacy_database - found_database_access
    if stale_database:
        errors.append(
            "路由已移除数据库直连，请从 legacy_route_database_access 删除: "
            + ", ".join(sorted(stale_database))
        )
    stale_route_imports = legacy_route_imports - found_route_imports
    if stale_route_imports:
        errors.append(
            "路由已移除跨路由导入，请从 legacy_route_imports 删除: "
            + ", ".join(sorted(stale_route_imports))
        )
    stale_legacy_api_routes = legacy_api_routes - found_legacy_api_routes
    if stale_legacy_api_routes:
        errors.append(
            "兼容 API 已迁移，请从 legacy_api_routes 删除: "
            + ", ".join(sorted(stale_legacy_api_routes))
        )

    _check_count_policy(
        "路由数据库导入",
        policy["legacy_route_database_import_counts"],
        actual_database_import_counts,
        errors,
    )
    _check_count_policy(
        "路由 SQL 调用",
        policy["legacy_route_sql_call_counts"],
        actual_sql_counts,
        errors,
    )
    _check_count_policy(
        "异常文本直接返回",
        policy["legacy_exception_exposure_counts"],
        actual_exception_counts,
        errors,
    )
    _check_count_policy(
        "daemon 后台线程",
        policy["legacy_daemon_thread_counts"],
        actual_daemon_counts,
        errors,
    )

    _check_change_fragments(_changed_paths(base_ref), errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 BLD 项目宪章中的机器可验证规则。")
    parser.add_argument("--base-ref", help="CI 中用于检查本分支变更片段的基准引用，例如 origin/main。")
    args = parser.parse_args()
    errors = check(args.base_ref)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("project contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
