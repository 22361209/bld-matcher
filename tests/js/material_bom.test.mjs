import test from "node:test";
import assert from "node:assert/strict";

import {bomTreeVisibility} from "../../static/pages/material_bom.js";

const rows = [
  {key: "W"},
  {key: "W.A", parent: "W"},
  {key: "W.A.K", parent: "W.A"},
  {key: "W.K2", parent: "W"},
];

test("BOM visibility follows every expanded ancestor", () => {
  let hidden = bomTreeVisibility(rows, new Set());
  assert.deepEqual([...hidden.values()], [false, true, true, true]);

  hidden = bomTreeVisibility(rows, new Set(["W"]));
  assert.deepEqual([...hidden.values()], [false, false, true, false]);

  hidden = bomTreeVisibility(rows, new Set(["W", "W.A"]));
  assert.deepEqual([...hidden.values()], [false, false, false, false]);

  hidden = bomTreeVisibility(rows, new Set(["W.A"]));
  assert.deepEqual([...hidden.values()], [false, true, true, true]);
});
