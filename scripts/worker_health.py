from __future__ import annotations

from app.platform.runtime_factory import get_runtime_health_service


def main() -> int:
    if not get_runtime_health_service().worker_is_fresh():
        print("worker health: stale or missing")
        return 1
    print("worker health: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
