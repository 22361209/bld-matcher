import assert from "node:assert/strict";
import test from "node:test";

import { filterComboboxOptions, moveActiveIndex } from "../../static/components/combobox.js";

test("moveActiveIndex enters the list with arrow down and wraps around", () => {
  assert.equal(moveActiveIndex(-1, 1, 3), 0);
  assert.equal(moveActiveIndex(-1, -1, 3), 2);
  assert.equal(moveActiveIndex(0, 1, 3), 1);
  assert.equal(moveActiveIndex(2, 1, 3), 0);
  assert.equal(moveActiveIndex(0, -1, 3), 2);
  assert.equal(moveActiveIndex(1, 1, 0), -1);
});

test("filterComboboxOptions matches label and value case-insensitively", () => {
  const options = [
    { value: "K48620", label: "K48620", detail: "控制臂" },
    { value: "K6004LB", label: "K6004LB", detail: "球头" },
  ];
  assert.deepEqual(filterComboboxOptions(options, ""), options);
  assert.deepEqual(filterComboboxOptions(options, "k48"), [options[0]]);
  assert.deepEqual(filterComboboxOptions(options, "球头"), [options[1]]);
  assert.deepEqual(filterComboboxOptions(options, "不存在"), []);
});
