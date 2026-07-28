import assert from "node:assert/strict";
import test from "node:test";

import {
  buildColumnFilterSearchState,
  isImeCompositionEvent,
} from "../../static/components/data_grid_controls.js";

test("data-grid filter search trims and matches labels case-insensitively", () => {
  assert.deepEqual(
    buildColumnFilterSearchState("  kia  ", ["HYUNDAI", "KIA", "Kia Motors"]),
    { query: "kia", matches: [false, true, true] }
  );
});

test("empty column filter search keeps every option visible", () => {
  assert.deepEqual(
    buildColumnFilterSearchState("   ", ["HYUNDAI", "KIA"]),
    { query: "", matches: [true, true] }
  );
});

test("IME composition detection supports the standard flag and keyCode fallback", () => {
  assert.equal(isImeCompositionEvent({ isComposing: true, keyCode: 13 }), true);
  assert.equal(isImeCompositionEvent({ isComposing: false, keyCode: 229 }), true);
  assert.equal(isImeCompositionEvent({ isComposing: false, keyCode: 13 }), false);
});
