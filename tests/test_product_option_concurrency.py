from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProductOptionConcurrencyTest(unittest.TestCase):
    def setUp(self):
        from app.database import connect
        from app.modules.products import option_values, repository_options
        from app.modules.products.repository import SQLiteProductUnitOfWork
        from app.modules.products.service import ProductService

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "products.sqlite3"
        with connect(self.path):
            pass
        self.options = option_values
        self.repository_options = repository_options
        self.unit_of_work = lambda: SQLiteProductUnitOfWork(self.path, connection_factory=self._connect)
        self.service = ProductService(self.unit_of_work, lambda: None, lambda: {})
        self.rival = self._connect(self.path)
        self.addCleanup(self.rival.close)

    def _connect(self, path):
        connection = sqlite3.connect(path, timeout=0)
        connection.row_factory = sqlite3.Row
        return connection

    def _register(self, connection, value):
        self.options.register_product_option_values(connection, series="", item="", product_status=value)

    def _values(self):
        return [row[0] for row in self.rival.execute("SELECT value FROM product_option_values WHERE kind = 'product_status'")]

    def _interleave_after_lookup(self, competing_write):
        lookup = self.options.option_value_exists
        attempted = False

        def interleaved(connection, kind, value, **kwargs):
            nonlocal attempted
            exists = lookup(connection, kind, value, **kwargs)
            if not attempted:
                attempted = True
                self.assertFalse(exists)
                # Run another connection at the exact check/write gap. A zero timeout
                # makes lock ownership observable without timing or background sleeps.
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    competing_write()
            return exists

        return interleaved

    def test_manual_add_serializes_automatic_registration(self):
        callback = self._interleave_after_lookup(lambda: self._register(self.rival, "41球头42衬套"))
        with patch.object(self.options, "option_value_exists", side_effect=callback):
            self.service.save_option_value("product_status", "41 个球头 42 个衬套", actor="test")
        self._register(self.rival, "41球头42衬套")
        self.rival.commit()
        self.assertEqual(self._values(), ["41 个球头 42 个衬套"])

    def test_automatic_registration_serializes_manual_add(self):
        callback = self._interleave_after_lookup(
            lambda: self.service.save_option_value("product_status", "51 个球头 52 个衬套", actor="test")
        )
        with patch.object(self.options, "option_value_exists", side_effect=callback):
            self._register(self.rival, "51球头52衬套")
            self.rival.commit()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.service.save_option_value("product_status", "51 个球头 52 个衬套", actor="test")
        self.assertEqual(self._values(), ["51球头52衬套"])

    def test_manual_rename_serializes_equivalent_add(self):
        self.service.save_option_value("product_status", "并发改名前", actor="test")
        option_id = self.rival.execute("SELECT id FROM product_option_values WHERE value = '并发改名前'").fetchone()[0]
        callback = self._interleave_after_lookup(lambda: self._register(self.rival, "61球头62衬套"))
        with patch.object(self.repository_options, "option_value_exists", side_effect=callback):
            self.service.save_option_value(
                "product_status", "61 个球头 62 个衬套", option_id=option_id, actor="test"
            )
        self._register(self.rival, "61球头62衬套")
        self.rival.commit()
        self.assertEqual(self._values(), ["61 个球头 62 个衬套"])

    def test_read_only_option_lookup_does_not_take_write_lock(self):
        with self.unit_of_work() as unit_of_work:
            unit_of_work.repository.option_values()
            self.assertFalse(unit_of_work.connection.in_transaction)
            self._register(self.rival, "71球头72衬套")
            self.rival.commit()
        self.assertEqual(self._values(), ["71球头72衬套"])
