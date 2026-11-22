import assert from "node:assert/strict";
import test from "node:test";

import {
  applyColumnOrder,
  buildColumnFilterSearchState,
  isImeCompositionEvent,
  moveVisibleColumn,
} from "../../static/components/data_grid_controls.js";

test("keyboard and pointer destinations use visible positions while hidden columns keep their slots", () => {
  const order = ["hidden1", "name", "hidden2", "code", "status", "hidden3"];
  const visible = ["name", "code", "status"];
  assert.deepEqual(moveVisibleColumn(order, visible, "name", 2),
    ["hidden1", "code", "hidden2", "status", "name", "hidden3"]);
  assert.deepEqual(moveVisibleColumn(order, visible, "code", 0),
    ["hidden1", "code", "hidden2", "name", "status", "hidden3"]);
  assert.equal(moveVisibleColumn(order, visible, "name", -1), order);
  assert.equal(moveVisibleColumn(order, visible, "hidden1", 1), order);
});

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
