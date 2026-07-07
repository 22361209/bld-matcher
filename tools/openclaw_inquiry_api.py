#!/usr/bin/env python3
"""Thin local client for the BLD internal inquiry API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


API_BASE = "http://127.0.0.1:5055/api/internal/inquiry"
SERVICE_CHECK_URL = "http://127.0.0.1:5055/"

# 优先使用 Claw 项目下的 key，兼容旧位置
KEY_CANDIDATES = [
    Path("/Users/linzhenyue/WorkBuddy/Claw/.workbuddy/.inquiry_api_key"),
    Path("/Users/linzhenyue/Documents/Playground/.inquiry_api_key"),
]

PROJECT_DIR = Path("/Users/linzhenyue/Projects/bld-matcher")
VENV_PYTHON = PROJECT_DIR / ".venv/bin/python"


def _find_key_file() -> Path:
    """查找可用的 API key 文件。"""
    for candidate in KEY_CANDIDATES:
        if candidate.exists():
            return candidate
    return KEY_CANDIDATES[0]  # 返回第一个，用于报错提示


def fail(error: str, message: str, code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": error, "message": message}, ensure_ascii=False))
    return code


def read_api_key() -> str:
    key_file = _find_key_file()
    try:
        token = key_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(fail("key_unavailable", str(exc)))
    if not token or token == "PASTE_INQUIRY_API_KEY_HERE":
        raise SystemExit(fail("key_missing", f"Please put the inquiry API key into {key_file}"))
    return token


def ensure_server() -> bool:
    """检查 5055 服务是否在运行，不在则自动拉起。"""
    try:
        req = urllib.request.Request(SERVICE_CHECK_URL)
        with urllib.request.urlopen(req, timeout=3) as _:
            return True
    except urllib.error.URLError:
        pass

    # 服务未启动，尝试自动拉起
    if not VENV_PYTHON.exists():
        print(json.dumps({"ok": False, "error": "startup_failed",
                          "message": f"项目虚拟环境不存在：{VENV_PYTHON}。请先初始化 venv。"}),
              file=sys.stderr)
        return False

    try:
        subprocess.Popen(
            [str(VENV_PYTHON), "wsgi.py"],
            cwd=str(PROJECT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        return True
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "startup_failed",
                          "message": f"自动启动服务失败：{exc}"}),
              file=sys.stderr)
        return False


def post_json(path: str, payload: dict) -> dict:
    if not ensure_server():
        return {"ok": False, "error": "connection_failed",
                "message": "无法连接到 BLD Inquiry API 服务，自动启动也失败了。"}

    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {read_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"HTTP_{exc.code}", "message": body}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": "connection_failed",
                "message": f"无法连接到 BLD Inquiry API 服务 ({exc.reason})。请确认服务正在运行。"}
    except TimeoutError:
        return {"ok": False, "error": "timeout",
                "message": "请求超时，BLD Inquiry API 服务无响应。"}


def build_common_payload(args: argparse.Namespace) -> dict:
    payload: dict[str, object] = {
        "price_mode": args.price_mode,
        "rows_limit": args.rows_limit,
        "unmatched_limit": args.unmatched_limit,
    }
    if args.exchange_rate is not None:
        payload["exchange_rate"] = args.exchange_rate
    if getattr(args, "source_name", None):
        payload["source_name"] = args.source_name
    if getattr(args, "match_column", None) not in (None, ""):
        payload["match_column"] = args.match_column
    return payload


def add_text_or_numbers(payload: dict, args: argparse.Namespace) -> None:
    if args.text:
        payload["text"] = args.text
    elif args.number:
        payload["numbers"] = args.number
    else:
        raise SystemExit(fail("missing_input", "Pass --text or at least one --number"))


def run_analyze_text(args: argparse.Namespace) -> int:
    payload = build_common_payload(args)
    add_text_or_numbers(payload, args)
    print(json.dumps(post_json("analyze", payload), ensure_ascii=False, indent=2))
    return 0


def run_export_text(args: argparse.Namespace) -> int:
    payload = build_common_payload(args)
    add_text_or_numbers(payload, args)
    payload["export"] = True
    print(json.dumps(post_json("numbers", payload), ensure_ascii=False, indent=2))
    return 0


def run_analyze_file(args: argparse.Namespace) -> int:
    payload = build_common_payload(args)
    payload["file_path"] = args.file_path
    print(json.dumps(post_json("analyze", payload), ensure_ascii=False, indent=2))
    return 0


def run_export_file(args: argparse.Namespace) -> int:
    payload = build_common_payload(args)
    payload["file_path"] = args.file_path
    payload["export"] = True
    print(json.dumps(post_json("file", payload), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call the local BLD internal inquiry API for OpenClaw robots")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_flags(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--price-mode", default="tax", choices=["none", "tax", "net", "usd"],
                         help="价格模式：none 不导出价格 / tax 含税价 / net 不含税价 / usd 美金价")
        cmd.add_argument("--exchange-rate", type=float,
                         help="汇率（仅 usd 模式需要）")
        cmd.add_argument("--rows-limit", type=int, default=200,
                         help="最多返回行数（默认 200）")
        cmd.add_argument("--unmatched-limit", type=int, default=100,
                         help="未匹配条目显示上限（默认 100）")

    analyze_text = sub.add_parser("analyze-text", help="分析号码列表（纯分析，不生成文件）")
    analyze_text.add_argument("--text", help="原始文本（含询价号，自动拆分）")
    analyze_text.add_argument("--number", action="append", help="单个询价号，可重复传递")
    add_common_flags(analyze_text)
    analyze_text.set_defaults(func=run_analyze_text)

    export_text = sub.add_parser("export-text", help="从号码列表生成 Excel 工作簿")
    export_text.add_argument("--text", help="原始文本（含询价号，自动拆分）")
    export_text.add_argument("--number", action="append", help="单个询价号，可重复传递")
    export_text.add_argument("--source-name", required=True,
                             help="源名称，用于输出文件名（例如：上海客户询价 → re260711_上海客户询价.xlsx）")
    add_common_flags(export_text)
    export_text.set_defaults(func=run_export_text)

    analyze_file = sub.add_parser("analyze-file", help="分析客户工作簿（纯分析，不生成文件）")
    analyze_file.add_argument("--file-path", required=True,
                              help="源文件绝对路径（.xls/.xlsx）")
    analyze_file.add_argument("--match-column",
                              help="匹配列：0-based 列索引或 Excel 列字母（如 A），不传时自动识别")
    add_common_flags(analyze_file)
    analyze_file.set_defaults(func=run_analyze_file)

    export_file = sub.add_parser("export-file", help="从客户文件生成增强版 Excel 工作簿")
    export_file.add_argument("--file-path", required=True,
                             help="源文件绝对路径（.xls/.xlsx）")
    export_file.add_argument("--match-column",
                             help="匹配列：0-based 列索引或 Excel 列字母（如 A），不传时自动识别")
    export_file.add_argument("--source-name",
                             help="自定义输出文件名标签。不传则沿用源文件名")
    add_common_flags(export_file)
    export_file.set_defaults(func=run_export_file)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
