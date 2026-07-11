from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def _read_json(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        payload = json.loads(exc.read().decode("utf-8"))
    return status, payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deployment health and minimum-business probes.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    live_status, live = _read_json(f"{base_url}/health/live")
    ready_status, ready = _read_json(f"{base_url}/health/ready")
    with urllib.request.urlopen(f"{base_url}/login", timeout=5) as response:
        login_status = int(response.status)
        login_body = response.read().decode("utf-8", errors="replace")
    result = {
        "live": live,
        "ready": ready,
        "login": {"status": login_status, "page_marker": "BLD" in login_body},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if live_status != 200 or ready_status != 200 or login_status != 200 or "BLD" not in login_body:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
