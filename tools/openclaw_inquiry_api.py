#!/usr/bin/env python3
"""Thin local client for the BLD internal inquiry API."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_BASE = "http://127.0.0.1:5055/api/internal/inquiry"
KEY_FILE = Path("/Users/linzhenyue/Documents/Playground/.inquiry_api_key")


def fail(error: str, message: str, code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": error, "message": message}, ensure_ascii=False))
    return code


def read_api_key() -> str:
    try:
        token = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(fail("key_unavailable", str(exc)))
    if not token or token == "PASTE_INQUIRY_API_KEY_HERE":
        raise SystemExit(fail("key_missing", f"Please put the inquiry API key into {KEY_FILE}"))
    return token


def post_json(path: str, payload: dict) -> dict:
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
        cmd.add_argument("--price-mode", default="tax", choices=["none", "tax", "net", "usd"])
        cmd.add_argument("--exchange-rate", type=float)
        cmd.add_argument("--rows-limit", type=int, default=200)
        cmd.add_argument("--unmatched-limit", type=int, default=100)

    analyze_text = sub.add_parser("analyze-text", help="Analyze text or number list without generating a file")
    analyze_text.add_argument("--text", help="Raw text containing inquiry numbers")
    analyze_text.add_argument("--number", action="append", help="One inquiry number; can be repeated")
    add_common_flags(analyze_text)
    analyze_text.set_defaults(func=run_analyze_text)

    export_text = sub.add_parser("export-text", help="Generate a new workbook from text or number list")
    export_text.add_argument("--text", help="Raw text containing inquiry numbers")
    export_text.add_argument("--number", action="append", help="One inquiry number; can be repeated")
    export_text.add_argument("--source-name", "--output-name", dest="source_name", required=True, help="Required source label used in the standardized output filename")
    add_common_flags(export_text)
    export_text.set_defaults(func=run_export_text)

    analyze_file = sub.add_parser("analyze-file", help="Analyze a customer workbook without generating a file")
    analyze_file.add_argument("--file-path", required=True, help="Absolute path to a source .xls/.xlsx file")
    analyze_file.add_argument("--match-column", help="0-based column index or Excel column letter, e.g. A")
    add_common_flags(analyze_file)
    analyze_file.set_defaults(func=run_analyze_file)

    export_file = sub.add_parser("export-file", help="Generate an augmented workbook from a customer file")
    export_file.add_argument("--file-path", required=True, help="Absolute path to a source .xls/.xlsx file")
    export_file.add_argument("--match-column", help="0-based column index or Excel column letter, e.g. A")
    export_file.add_argument("--source-name", "--output-name", dest="source_name", help="Optional source label used in the standardized output filename")
    add_common_flags(export_file)
    export_file.set_defaults(func=run_export_file)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
