import test from "node:test";
import assert from "node:assert/strict";

import {
  createInlineResultsRequestGate,
  inlineResultsFragmentUrl,
  inlineResultsHistoryUrl,
} from "../../static/pages/inline_results_navigation.js";

test("inline list fragments preserve filters and never send a results hash", () => {
  assert.equal(
    inlineResultsFragmentUrl(
      "/quotes/fragment",
      "http://127.0.0.1:5055/quotes?customer_name=Bosch&page=2#quote-results"
    ),
    "http://127.0.0.1:5055/quotes/fragment?customer_name=Bosch&page=2"
  );
});

test("inline list history keeps a shareable path and query without anchor scrolling", () => {
  assert.equal(
    inlineResultsHistoryUrl("http://127.0.0.1:5055/tubes?q=KE8036#tube-results"),
    "/tubes?q=KE8036"
  );
});

test("inline list request generations reject stale results", () => {
  const gate = createInlineResultsRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
});
