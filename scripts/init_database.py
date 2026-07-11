from __future__ import annotations

from app.config import DB_PATH, assert_production_secrets
from app.database import connect, ensure_default_admin


def main() -> int:
    assert_production_secrets()
    with connect(DB_PATH) as conn:
        ensure_default_admin(conn)
    print(f"database initialization: ok ({DB_PATH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
