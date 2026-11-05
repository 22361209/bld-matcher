import assert from "node:assert/strict";
import test from "node:test";

import {
  applyColumnOrder,
  buildColumnFilterSearchState,
  isImeCompositionEvent,
} from "../../static/components/data_grid_controls.js";

test("moving the actions column reorders all table sections only for grids that enable it", () => {
  const columns = ["code", "name", "actions"];
  const rows = Array.from({length: 3}, () => ({
    children: columns.map((col) => ({dataset: {col}})),
    appendChild(cell) {
      this.children.splice(this.children.indexOf(cell), 1);
      this.children.push(cell);
    },
  }));
  const [colgroup, heading, body] = rows;
  const table = {querySelector: () => colgroup, tHead: {rows: [heading]}, tBodies: [{rows: [body]}]};
  for (const [available, expected] of [
    [columns, ["actions", "name", "code"]],
    [["code", "name"], ["name", "code", "actions"]],
  ]) {
    applyColumnOrder(table, ["actions", "name", "code"], available);
    for (const row of rows) assert.deepEqual(row.children.map((cell) => cell.dataset.col), expected);
  }
});

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
