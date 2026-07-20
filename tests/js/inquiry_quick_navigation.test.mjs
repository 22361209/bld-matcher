import assert from "node:assert/strict";
import test from "node:test";

import {
  createQuickInquiryRequestGate,
  quickInquiryFragmentUrl,
  quickInquiryState,
  quickInquiryUrl,
  shouldUseQuickInquiryNavigation,
} from "../../static/pages/inquiry_quick_navigation.js";

test("single quick inquiry navigates directly when no workbook is selected", () => {
  assert.equal(
    shouldUseQuickInquiryNavigation({ query: " 54500-2F000 ", hasFile: false }),
    true,
  );
  assert.equal(
    quickInquiryUrl("http://127.0.0.1:5055/?quick_oe=old&quick_filter=oe", " 54500-2F000 "),
    "http://127.0.0.1:5055/?quick_oe=54500-2F000",
  );
});

test("workbooks and pasted multi-number inquiries keep the POST workflow", () => {
  assert.equal(
    shouldUseQuickInquiryNavigation({ query: "54500-2F000", hasFile: true }),
    false,
  );
  for (const query of ["54500-2F000 54500-2D000", "54500-2F000/54500-2D000", "54500-2F000，54500-2D000"]) {
    assert.equal(
      shouldUseQuickInquiryNavigation({ query, hasFile: false }),
      false,
      query,
    );
  }
});

test("inline inquiry uses a dedicated fragment URL and keeps shareable page state", () => {
  assert.equal(
    quickInquiryFragmentUrl(
      "/inquiry/quick-search",
      "http://127.0.0.1:5055/",
      " 54500-2D000 ",
      "oe",
    ),
    "http://127.0.0.1:5055/inquiry/quick-search?quick_oe=54500-2D000&quick_filter=oe",
  );
  assert.deepEqual(
    quickInquiryState("http://127.0.0.1:5055/?quick_oe=54500-2D000&quick_filter=oe"),
    { query: "54500-2D000", filter: "oe" },
  );
});

test("stale inline inquiry responses cannot replace the latest result", () => {
  const gate = createQuickInquiryRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);

  gate.invalidate();
  assert.equal(gate.isCurrent(second), false);
});
