from __future__ import annotations

import unittest


WEB_TEST_MODULES = (
    "tests.test_web_access_and_shell",
    "tests.test_web_api",
    "tests.test_web_inquiry_export",
    "tests.test_web_inquiry_interactions",
    "tests.test_web_inquiry_matching",
    "tests.test_web_materials_and_operations",
    "tests.test_web_product_catalog",
    "tests.test_web_product_import_and_sync",
    "tests.test_web_product_media",
    "tests.test_web_quotes_contracts_customers",
)


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Keep direct ``tests.test_app`` runs compatible without discovery duplicates."""
    if pattern is not None:
        return tests
    suite = unittest.TestSuite()
    for module_name in WEB_TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite
