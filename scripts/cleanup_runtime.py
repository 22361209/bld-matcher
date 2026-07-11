from __future__ import annotations

import argparse
import json

from app.platform.runtime_factory import get_runtime_retention_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply BLD runtime retention cleanup.")
    parser.add_argument("--apply", action="store_true", help="Apply the plan. Without this flag the command is dry-run only.")
    parser.add_argument("--actor", default="runtime-cleanup", help="Trusted audit actor stored for an applied cleanup.")
    args = parser.parse_args(argv)
    service = get_runtime_retention_service()
    plan = service.build_plan()
    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "plan": plan.summary(),
    }
    if args.apply:
        payload["result"] = service.apply(plan, actor=args.actor)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
