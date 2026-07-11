from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


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
APPROVED_API_SCOPES = {
    "api:read",
    "products:read",
    "inquiries:run",
    "artifacts:read",
    "quotes:read",
    "quotes:write",
    "contracts:generate",
    "jobs:read",
    "jobs:cancel",
}
UI_WRITE_GUARDS = {"login_required", "permission_required"}
LEGACY_API_GUARDS = {"internal_api_required", "api_scope_required"}
REQUIRED_DOCKER_IGNORES = {".env*", "data/", "logs/", "outputs/", "uploads/"}
REQUIRED_DOCS = {
    "PROJECT_CONSTITUTION.md",
    "docs/adr/0001-project-governance-and-stack-baseline.md",
    "docs/adr/0002-api-platform-foundation.md",
    "docs/adr/0003-quote-vertical-slice.md",
    "docs/adr/0004-product-inquiry-core-and-artifacts.md",
    "docs/adr/0005-domain-and-page-protocol-migration.md",
    "docs/adr/0006-persistent-jobs-ai-and-runtime-governance.md",
    "docs/api/ai-contract.md",
    "docs/api/product-inquiry-v1.md",
    "docs/api/quote-v1.md",
    "docs/architecture/overview.md",
    "docs/governance/enforcement-matrix.md",
    "docs/operations/runtime.md",
    "docs/ui/page-protocol.md",
    "contracts/openapi-v1.json",
    "contracts/routes.json",
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
PAGE_ID_RE = re.compile(
    r"{%\s*block\s+page_id\s*%}\s*([a-z0-9]+(?:[_.-][a-z0-9]+)+)\s*{%\s*endblock\s*%}",
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
ROUTE_ADAPTER_MAX_LINES = 320
ROUTE_ADAPTER_MAX_ENDPOINTS = 15
CSS_FOUNDATION_MAX_LINES = 1400
CSS_COMPONENT_MAX_LINES = 1000
CSS_PAGE_MAX_LINES = 600
CSS_SHARED_DOMAIN_MARKERS = (
    "api-key",
    "bld",
    "catalog",
    "contract",
    "customer",
    "drawing",
    "history",
    "inquiry",
    "login",
    "match",
    "material",
    "oe",
    "price",
    "product",
    "purchase",
    "quote",
    "shipment",
    "shipping",
    "sync",
    "update",
)
CSS_DEPRECATED_CLASS_NAMES = {
    "catalog-export-form",
    "catalog-import-form",
    "file-picker-oe-input",
    "history-search",
    "inquiry-command-center",
    "inquiry-command-row",
    "inquiry-directory-note",
    "inquiry-file-oe-picker",
    "inquiry-history-search",
    "inquiry-landing",
    "inquiry-search-page",
    "inquiry-stat-strip",
    "inquiry-upload-form",
    "match-column-choice",
    "materials-actions",
    "materials-command",
    "materials-empty",
    "materials-file-meta",
    "materials-flow",
    "materials-generate-form",
    "materials-grid-table",
    "materials-hero",
    "materials-linear",
    "materials-messages",
    "materials-metrics",
    "materials-muted",
    "materials-recent",
    "materials-search-form",
    "materials-table-section",
    "materials-table-title",
    "materials-table-title-actions",
    "materials-table-wrap",
    "modal-product-form",
    "price-cell",
    "price-import-form",
    "product-form",
    "product-modal",
    "product-modal-backdrop",
    "product-modal-panel",
    "shipment-hidden-input",
}
CSS_DEPRECATED_RAW_TOKENS = ("--inquiry-", "shipment-spin")
CSS_IMPORTANT_ALLOWLIST = {
    "static/styles.css": {
        "display: none !important;": 1,
        "display: inline-flex !important;": 1,
    }
}
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
CSS_CLASS_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")
TEMPLATE_CLASS_RE = re.compile(r"\bclass\s*=\s*['\"]([^'\"]*)['\"]", re.I | re.S)


def _run_git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _css_selectors(source: str) -> list[str]:
    clean = CSS_COMMENT_RE.sub("", source)
    selectors: list[str] = []
    segment_start = 0
    quote = ""
    escaped = False
    for index, character in enumerate(clean):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote:
            escaped = True
            continue
        if character in {'"', "'"}:
            if quote == character:
                quote = ""
            elif not quote:
                quote = character
            continue
        if quote or character not in "{}":
            continue
        if character == "{":
            prelude = clean[segment_start:index].strip()
            if prelude and not prelude.startswith("@"):
                selectors.extend(item.strip() for item in prelude.split(",") if item.strip())
        segment_start = index + 1
    return selectors


def _css_domain_marker(selector: str) -> str | None:
    normalized_selector = re.sub(r"[^a-z0-9]", "", selector.lower())
    for marker in CSS_SHARED_DOMAIN_MARKERS:
        normalized_marker = re.sub(r"[^a-z0-9]", "", marker.lower())
        if normalized_marker in normalized_selector:
            return marker
    return None


def _template_class_names(source: str) -> set[str]:
    names: set[str] = set()
    for value in TEMPLATE_CLASS_RE.findall(source):
        names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", value))
    return names


def _check_css_source(
    relative: str,
    source: str,
    max_lines: int,
    errors: list[str],
    *,
    shared: bool,
) -> None:
    line_count = len(source.splitlines())
    if line_count > max_lines:
        errors.append(f"CSS 文件超过 {max_lines} 行，必须继续拆分: {relative} ({line_count})")

    clean = CSS_COMMENT_RE.sub("", source)
    if re.search(r"@import\b", clean, re.I):
        errors.append(f"CSS 禁止 @import，资产必须由模板显式加载: {relative}")

    selectors = _css_selectors(source)
    id_selectors = [selector for selector in selectors if re.search(r"#[A-Za-z_]", selector)]
    if id_selectors:
        errors.append(f"CSS 禁止 ID 选择器: {relative} ({', '.join(id_selectors[:4])})")

    important_counts = Counter(
        line.strip()
        for line in clean.splitlines()
        if "!important" in line
    )
    allowed_important = CSS_IMPORTANT_ALLOWLIST.get(relative, {})
    important_violations = [
        declaration
        for declaration, count in important_counts.items()
        if count > allowed_important.get(declaration, 0)
    ]
    if important_violations:
        errors.append(
            f"CSS 禁止新增 !important: {relative} ({', '.join(sorted(important_violations))})"
        )

    if not shared:
        return
    domain_selectors = [
        (selector, marker)
        for selector in selectors
        if (marker := _css_domain_marker(selector)) is not None
    ]
    if domain_selectors:
        details = ", ".join(
            f"{marker}:{selector}" for selector, marker in domain_selectors[:4]
        )
        errors.append(f"共享 CSS 禁止业务选择器: {relative} ({details})")


def _check_css_governance(errors: list[str]) -> None:
    static_root = ROOT / "static"
    foundation = static_root / "styles.css"
    component_paths = sorted((static_root / "components").glob("*.css"))
    page_paths = sorted((static_root / "pages").glob("*.css"))
    allowed_paths = {foundation, *component_paths, *page_paths}
    unexpected_paths = [
        path.relative_to(ROOT).as_posix()
        for path in static_root.rglob("*.css")
        if path not in allowed_paths
    ]
    if unexpected_paths:
        errors.append(
            "CSS 只能位于 static/styles.css、static/components/ 或 static/pages/: "
            + ", ".join(sorted(unexpected_paths))
        )

    _check_css_source(
        "static/styles.css",
        foundation.read_text(encoding="utf-8"),
        CSS_FOUNDATION_MAX_LINES,
        errors,
        shared=True,
    )
    for path in component_paths:
        relative = path.relative_to(ROOT).as_posix()
        _check_css_source(
            relative,
            path.read_text(encoding="utf-8"),
            CSS_COMPONENT_MAX_LINES,
            errors,
            shared=True,
        )
    for path in page_paths:
        relative = path.relative_to(ROOT).as_posix()
        _check_css_source(
            relative,
            path.read_text(encoding="utf-8"),
            CSS_PAGE_MAX_LINES,
            errors,
            shared=False,
        )

    template_sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "templates").rglob("*.html"))
    }
    base_source = template_sources["templates/base.html"]
    if "filename='styles.css'" not in base_source:
        errors.append("templates/base.html 必须加载 static/styles.css。")
    if "pages/" in base_source:
        errors.append("templates/base.html 禁止加载页面 CSS。")

    for path in component_paths:
        asset = f"components/{path.name}"
        owners = [relative for relative, source in template_sources.items() if asset in source]
        if owners != ["templates/base.html"]:
            errors.append(f"共享组件 CSS 必须且只能由 base.html 加载: {asset} ({owners})")

    for path in page_paths:
        asset = f"pages/{path.name}"
        owners = [relative for relative, source in template_sources.items() if asset in source]
        if not owners:
            errors.append(f"页面 CSS 没有所属模板: {asset}")
        elif "templates/base.html" in owners:
            errors.append(f"页面 CSS 禁止进入 base.html: {asset}")

    class_names: set[str] = set()
    for source in template_sources.values():
        class_names.update(_template_class_names(source))
    for path in allowed_paths:
        if not path.is_file():
            continue
        for selector in _css_selectors(path.read_text(encoding="utf-8")):
            class_names.update(CSS_CLASS_RE.findall(selector))
    deprecated_classes = sorted(class_names.intersection(CSS_DEPRECATED_CLASS_NAMES))
    if deprecated_classes:
        errors.append("前端禁止恢复废弃的跨域类名: " + ", ".join(deprecated_classes))

    css_source = "\n".join(
        path.read_text(encoding="utf-8") for path in allowed_paths if path.is_file()
    )
    stale_raw_tokens = [token for token in CSS_DEPRECATED_RAW_TOKENS if token in css_source]
    if stale_raw_tokens:
        errors.append("CSS 禁止恢复废弃 token/动画名: " + ", ".join(stale_raw_tokens))


def _load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _policy_at_ref(ref: str | None) -> dict[str, Any] | None:
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


def _legacy_api_path_counts(entries: list[str]) -> Counter[str]:
    return Counter(entry.rsplit(" ", 1)[-1] for entry in entries)


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


def _route_endpoint_count(tree: ast.AST) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_route_decorator(decorator) for decorator in node.decorator_list)
    )


def _check_route_adapter_limits(relative: str, source: str, tree: ast.AST, errors: list[str]) -> None:
    dynamic_routes = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_url_rule"
    )
    if dynamic_routes:
        errors.append(f"路由必须使用可静态检查的装饰器，禁止 add_url_rule: {relative}")

    endpoint_count = _route_endpoint_count(tree)
    if not endpoint_count:
        return
    line_count = len(source.splitlines())
    if line_count > ROUTE_ADAPTER_MAX_LINES:
        errors.append(
            f"路由适配器超过 {ROUTE_ADAPTER_MAX_LINES} 行，必须按职责拆分: "
            f"{relative} ({line_count})"
        )
    if endpoint_count > ROUTE_ADAPTER_MAX_ENDPOINTS:
        errors.append(
            f"路由适配器超过 {ROUTE_ADAPTER_MAX_ENDPOINTS} 个 endpoint，必须按职责拆分: "
            f"{relative} ({endpoint_count})"
        )


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_v1_api_path(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def _normalize_api_path(path: str) -> str:
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)


def _openapi_declarations(tree: ast.AST) -> dict[tuple[str, str], set[str]]:
    declarations: dict[tuple[str, str], set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "OpenApiOperation":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        path_node = keywords.get("path")
        method_node = keywords.get("method")
        scopes_node = keywords.get("scopes")
        if not (
            isinstance(path_node, ast.Constant)
            and isinstance(path_node.value, str)
            and isinstance(method_node, ast.Constant)
            and isinstance(method_node.value, str)
        ):
            continue
        declarations[(_normalize_api_path(path_node.value), method_node.value.upper())] = (
            _literal_strings(scopes_node) if scopes_node is not None else set()
        )
    return declarations


def _openapi_request_model_operations(tree: ast.AST) -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "OpenApiOperation":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        path_node = keywords.get("path")
        method_node = keywords.get("method")
        request_model = keywords.get("request_model")
        if (
            isinstance(path_node, ast.Constant)
            and isinstance(path_node.value, str)
            and isinstance(method_node, ast.Constant)
            and isinstance(method_node.value, str)
            and request_model is not None
            and not (isinstance(request_model, ast.Constant) and request_model.value is None)
        ):
            operations.add((_normalize_api_path(path_node.value), method_node.value.upper()))
    return operations


def _imports_database(tree: ast.AST) -> bool:
    return _database_import_count(tree) > 0


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _check_module_layer(relative: str, tree: ast.AST, errors: list[str]) -> None:
    match = re.fullmatch(r"app/modules/[^/]+/([^/]+)\.py", relative)
    if not match:
        return
    filename = match.group(1)
    imported = _imported_modules(tree)
    forbidden_prefixes: tuple[str, ...] = ()
    if filename == "domain" or filename.endswith("_domain"):
        forbidden_prefixes = (
            "flask",
            "sqlite3",
            "pathlib",
            "app.config",
            "app.database",
            "app.platform",
            "app.routes",
            "openai",
            "httpx",
            "requests",
        )
    elif filename == "service" or filename.endswith("_service"):
        forbidden_prefixes = ("flask", "sqlite3", "app.config", "app.database", "app.routes")
    elif filename in {"web", "api"} or filename.endswith(("_web", "_api")):
        forbidden_prefixes = ("sqlite3", "app.database")
    violations = sorted(
        module
        for module in imported
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes)
    )
    if violations:
        errors.append(f"模块层依赖方向错误: {relative} 禁止导入 {', '.join(violations)}")


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


def _database_boundary_errors(tree: ast.Module) -> list[str]:
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    unexpected = functions - {"connect"}
    errors = []
    if unexpected:
        errors.append("app/database.py 只能保留 connect，业务函数必须位于领域或平台模块: " + ", ".join(sorted(unexpected)))
    return errors


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
) -> tuple[bool, set[str], dict[tuple[str, str], set[str]]]:
    relative = path.relative_to(ROOT).as_posix()
    found_legacy_api_routes: set[str] = set()
    found_v1_operations: dict[tuple[str, str], set[str]] = {}
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
        scope_decorator = next(
            (decorator for decorator in node.decorator_list if _decorator_name(decorator) == "api_scope_required"),
            None,
        )
        declared_scopes = set()
        if isinstance(scope_decorator, ast.Call):
            for argument in scope_decorator.args:
                declared_scopes.update(_literal_strings(argument))
        for route, methods in declarations:
            if "UNKNOWN" in methods:
                errors.append(f"路由 methods 必须使用静态字符串数组: {endpoint} ({route})")
            if _is_api_path(route):
                if _is_v1_api_path(route):
                    if "api_scope_required" not in guards:
                        errors.append(f"/api/v1 路由必须声明 Scope: {endpoint} ({route})")
                    elif not declared_scopes:
                        errors.append(f"/api/v1 Scope 必须使用静态字符串且不能为空: {endpoint} ({route})")
                    unknown_scopes = declared_scopes - APPROVED_API_SCOPES
                    if unknown_scopes:
                        errors.append(
                            f"/api/v1 路由使用未批准 Scope: {endpoint} ({', '.join(sorted(unknown_scopes))})"
                        )
                    for method in methods:
                        found_v1_operations[(_normalize_api_path(route), method)] = declared_scopes
                    if methods.intersection(WRITE_METHODS) and "idempotency_required" not in guards:
                        errors.append(f"/api/v1 写路由必须声明幂等保护: {endpoint} ({route})")
                    if methods.intersection(WRITE_METHODS) and "api_schema" not in guards:
                        errors.append(f"/api/v1 写路由必须声明 Pydantic Schema: {endpoint} ({route})")
                    if "PATCH" in methods and "if_match_required" not in guards:
                        errors.append(f"/api/v1 PATCH 路由必须声明 If-Match 保护: {endpoint} ({route})")
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
    return has_routes, found_legacy_api_routes, found_v1_operations


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

    database_tree = ast.parse((ROOT / "app" / "database.py").read_text(encoding="utf-8"))
    errors.extend(_database_boundary_errors(database_tree))

    if baseline_policy:
        for key, values in policy.items():
            baseline_values = baseline_policy.get(key, {} if isinstance(values, dict) else [])
            if isinstance(values, dict):
                compared_values = (
                    {path: count for path, count in values.items() if path in baseline_values}
                    if key == "legacy_file_line_counts"
                    else values
                )
                increases = [
                    f"{path} ({baseline_values.get(path, 0)} -> {count})"
                    for path, count in compared_values.items()
                    if int(count) > int(baseline_values.get(path, 0))
                ]
                if increases:
                    errors.append(f"遗留计数白名单 {key} 禁止扩大: {', '.join(sorted(increases))}")
            else:
                if key == "legacy_api_routes":
                    current_counts = _legacy_api_path_counts(values)
                    baseline_counts = _legacy_api_path_counts(baseline_values)
                    additions = [
                        f"{path} ({baseline_counts[path]} -> {count})"
                        for path, count in current_counts.items()
                        if count > baseline_counts[path]
                    ]
                elif key == "public_write_endpoints":
                    baseline_functions = Counter(entry.rsplit(":", 1)[-1] for entry in baseline_values)
                    current_functions = Counter(entry.rsplit(":", 1)[-1] for entry in values)
                    additions = [
                        function_name
                        for function_name, count in current_functions.items()
                        if count > baseline_functions[function_name]
                    ]
                else:
                    additions = sorted(set(values) - set(baseline_values))
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

    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime_markers = {
        "docker-compose.yml:bld-worker": "bld-worker:" in compose_text,
        "docker-compose.yml:run_worker": "scripts.run_worker" in compose_text,
        "docker-compose.yml:retention": "scripts.cleanup_runtime --apply" in compose_text,
        "Dockerfile:readiness": "/health/ready" in dockerfile_text,
    }
    missing_runtime_markers = [name for name, present in runtime_markers.items() if not present]
    if missing_runtime_markers:
        errors.append("运行治理配置缺失: " + ", ".join(sorted(missing_runtime_markers)))

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

    _check_css_governance(errors)

    legacy_pages = set(policy["legacy_full_page_templates"])
    allowed_inline_counts = policy["legacy_inline_script_counts"]
    allowed_style_counts = policy["legacy_inline_style_counts"]
    actual_inline_counts: dict[str, int] = {}
    actual_style_counts: dict[str, int] = {}
    page_id_owners: dict[str, list[str]] = {}
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
            page_id_match = PAGE_ID_RE.search(template)
            if not page_id_match:
                errors.append(f"协议页面必须声明有效的字面量 page_id: {relative}")
            else:
                page_id_owners.setdefault(page_id_match.group(1).lower(), []).append(relative)
            if not page_type_match or page_type_match.group(1) not in PAGE_TYPES:
                errors.append(f"协议页面必须声明有效 page_type: {relative}")
    duplicate_page_ids = {
        page_id: owners for page_id, owners in page_id_owners.items() if len(owners) > 1
    }
    for page_id, owners in sorted(duplicate_page_ids.items()):
        errors.append(f"page_id 必须全局唯一: {page_id} ({', '.join(owners)})")
    _check_count_policy("模板内联脚本或事件处理器", allowed_inline_counts, actual_inline_counts, errors)
    _check_count_policy("模板内联样式", allowed_style_counts, actual_style_counts, errors)

    allowed_file_lines = policy["legacy_file_line_counts"]
    actual_file_lines = {
        relative: len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        for relative in allowed_file_lines
        if (ROOT / relative).is_file()
    }
    _check_count_policy("聚集文件行数", allowed_file_lines, actual_file_lines, errors)

    legacy_database = set(policy["legacy_route_database_access"])
    legacy_route_imports = set(policy["legacy_route_imports"])
    legacy_api_routes = set(policy["legacy_api_routes"])
    found_database_access: set[str] = set()
    found_route_imports: set[str] = set()
    found_legacy_api_routes: set[str] = set()
    found_v1_operations: dict[tuple[str, str], set[str]] = {}
    documented_v1_operations: dict[tuple[str, str], set[str]] = {}
    documented_v1_request_models: set[tuple[str, str]] = set()
    actual_sql_counts: dict[str, int] = {}
    actual_database_import_counts: dict[str, int] = {}
    actual_exception_counts: dict[str, int] = {}
    actual_daemon_counts: dict[str, int] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        _check_module_layer(relative, tree, errors)
        _check_route_adapter_limits(relative, source, tree, errors)
        documented_v1_operations.update(_openapi_declarations(tree))
        documented_v1_request_models.update(_openapi_request_model_operations(tree))
        daemon_count = _daemon_thread_count(tree)
        if daemon_count:
            actual_daemon_counts[relative] = daemon_count
        has_routes, found_api, found_v1 = _check_route_contracts(
            path,
            tree,
            errors,
            set(policy["public_write_endpoints"]),
            legacy_api_routes,
        )
        found_legacy_api_routes.update(found_api)
        found_v1_operations.update(found_v1)
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

    missing_openapi = set(found_v1_operations) - set(documented_v1_operations)
    if missing_openapi:
        errors.append(
            "/api/v1 路由缺少 OpenAPI 登记: "
            + ", ".join(f"{method} {path}" for path, method in sorted(missing_openapi))
        )
    stale_openapi = set(documented_v1_operations) - set(found_v1_operations)
    if stale_openapi:
        errors.append(
            "OpenAPI 登记没有对应 /api/v1 路由: "
            + ", ".join(f"{method} {path}" for path, method in sorted(stale_openapi))
        )
    for operation in sorted(set(found_v1_operations) & set(documented_v1_operations)):
        route_scopes = found_v1_operations[operation]
        documented_scopes = documented_v1_operations[operation]
        if route_scopes != documented_scopes:
            path, method = operation
            errors.append(
                f"OpenAPI Scope 与路由不一致: {method} {path} "
                f"({sorted(route_scopes)} != {sorted(documented_scopes)})"
            )
    missing_request_models = {
        operation
        for operation in found_v1_operations
        if operation[1] in WRITE_METHODS and operation not in documented_v1_request_models
    }
    if missing_request_models:
        errors.append(
            "/api/v1 写路由的 OpenAPI 登记缺少 request_model: "
            + ", ".join(f"{method} {path}" for path, method in sorted(missing_request_models))
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
