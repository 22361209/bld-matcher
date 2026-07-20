import test from "node:test";
import assert from "node:assert/strict";

import {
  createProductCatalogRequestGate,
  productCatalogFragmentUrl,
  productCatalogHistoryUrl,
  productCatalogState,
} from "../../static/pages/product_catalog_navigation.js";

test("fragment requests preserve repeated filters and drop the hash", () => {
  assert.equal(
    productCatalogFragmentUrl(
      "/products/fragment",
      "http://127.0.0.1:5055/products?brand=HONDA&brand=TOYOTA&page=2#products-results"
    ),
    "http://127.0.0.1:5055/products/fragment?brand=HONDA&brand=TOYOTA&page=2"
  );
});

test("fragment requests resolve server-provided relative catalog URLs", () => {
  assert.equal(
    productCatalogFragmentUrl(
      "/products/fragment",
      "/products?bld=K-CODEX-INLINE-ACCEPT#products-results",
      "http://localhost:5071/products?bld=K6001B"
    ),
    "http://localhost:5071/products/fragment?bld=K-CODEX-INLINE-ACCEPT"
  );
});

test("catalog state preserves blank and repeated filters", () => {
  assert.deepEqual(
    productCatalogState(
      "http://127.0.0.1:5055/products?bld=K8&status=all&brand=&brand=HONDA&item=Arm&page=3"
    ),
    {
      bld: "K8",
      oe: "",
      status: "all",
      page: 3,
      filters: { brand: ["", "HONDA"], item: ["Arm"], product_status: [] },
    }
  );
});

test("history URLs keep query state and normalize the results hash", () => {
  assert.equal(
    productCatalogHistoryUrl("http://127.0.0.1:5055/products?oe=54500#old"),
    "/products?oe=54500#products-results"
  );
});

test("request generations reject stale responses", () => {
  const gate = createProductCatalogRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
});
