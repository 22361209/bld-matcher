from __future__ import annotations

import unittest

from app.modules.products.service import ProductService


class _Repository:
    def __init__(self) -> None:
        self.version = (1, "2026-07-20 10:00", 0, "", (1, 1), (0, 0), (0, 0))
        self.snapshots = 0

    def catalog_version(self) -> tuple[object, ...]:
        return self.version

    def catalog_snapshot(self) -> tuple[tuple[object, ...], list[dict], dict[str, str]]:
        self.snapshots += 1
        return self.version, [{"BLD NO.": "K-CACHE", "OE NO.1": "CACHE-OE"}], {}


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class ProductCatalogCacheTests(unittest.TestCase):
    def test_catalog_reuses_cached_rows_when_version_is_unchanged(self) -> None:
        repository = _Repository()
        service = ProductService(lambda: _UnitOfWork(repository), lambda: None, lambda: {})

        first = service.catalog()
        second = service.catalog()

        self.assertIs(first, second)
        self.assertEqual(repository.snapshots, 1)

        repository.version = (2, "2026-07-20 10:01", 0, "", (2, 2), (0, 0), (0, 0))
        service.catalog()
        self.assertEqual(repository.snapshots, 2)


if __name__ == "__main__":
    unittest.main()
