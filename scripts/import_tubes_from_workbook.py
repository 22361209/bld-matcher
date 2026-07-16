from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DB_PATH  # noqa: E402
from app.modules.tubes.repository import TubeUnitOfWork  # noqa: E402
from app.modules.tubes.importer import load_tube_rows  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="从管件工作簿的 2026 明细表导入管件资料。")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--actor", default="system-import")
    args = parser.parse_args()
    rows = load_tube_rows(args.workbook)
    with TubeUnitOfWork(DB_PATH) as unit_of_work:
        imported = unit_of_work.repository.import_rows(rows, actor=args.actor)
        unit_of_work.commit()
    print(f"管件导入完成：{imported} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
