import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createOptionPickerState,
  filterPickerOptions,
  parsePickerMultiValue,
  pickerNewValue,
  serializePickerMultiValue,
} from "../../static/pages/product_option_picker.js";

test("multi value parsing splits lines, trims and dedupes case-insensitively", () => {
  assert.deepEqual(parsePickerMultiValue("HONDA\r\n toyota \nHONDA\n\nKIA"), ["HONDA", "toyota", "KIA"]);
  assert.deepEqual(parsePickerMultiValue(""), []);
  assert.deepEqual(parsePickerMultiValue(null), []);
});

test("multi serialization writes one value per line", () => {
  assert.equal(serializePickerMultiValue(["HONDA", "TOYOTA"]), "HONDA\nTOYOTA");
  assert.equal(serializePickerMultiValue([]), "");
});

test("multi state adds unique values, removes chips and serializes back to lines", () => {
  const state = createOptionPickerState("multi", "HONDA\nTOYOTA");
  assert.equal(state.add("honda"), false);
  assert.equal(state.add("  KIA "), true);
  assert.equal(state.add("   "), false);
  assert.equal(state.serialized(), "HONDA\nTOYOTA\nKIA");
  assert.equal(state.remove("toyota"), true);
  assert.equal(state.remove("missing"), false);
  assert.equal(state.serialized(), "HONDA\nKIA");
});

test("single state keeps one value and serializes it back", () => {
  const state = createOptionPickerState("single", "Front Left Lower Control Arm");
  assert.equal(state.serialized(), "Front Left Lower Control Arm");
  state.add("Rear Arm");
  assert.deepEqual(state.values, ["Rear Arm"]);
  assert.equal(state.serialized(), "Rear Arm");
  state.set("");
  assert.equal(state.serialized(), "");
});

test("copied product values hydrate multi and single picker state", () => {
  const multi = createOptionPickerState("multi", "");
  multi.set("HYUNDAI\nhyundai\nKIA");
  assert.deepEqual(multi.values, ["HYUNDAI", "KIA"]);
  assert.equal(multi.serialized(), "HYUNDAI\nKIA");

  const single = createOptionPickerState("single", "");
  single.set("1 个球头 2 个衬套");
  assert.equal(single.serialized(), "1 个球头 2 个衬套");
});

test("new-value candidate appears only when the query has no exact match", () => {
  const options = ["HONDA", "TOYOTA"];
  assert.equal(pickerNewValue(" hon ", options), "hon");
  assert.equal(pickerNewValue("honda", options), null);
  assert.equal(pickerNewValue("   ", options), null);
});

test("option filtering matches substrings case-insensitively and keeps all on blank query", () => {
  const options = ["HYUNDAI", "KIA", "Kia Motors"];
  assert.deepEqual(filterPickerOptions(options, "kia"), ["KIA", "Kia Motors"]);
  assert.deepEqual(filterPickerOptions(options, "  "), options);
  assert.deepEqual(filterPickerOptions(options, "vw"), []);
});

test("multi picker macro renders chips inside the combobox before the bare input", () => {
  const macroSource = readFileSync(
    new URL("../../templates/_product_option_fields.html", import.meta.url),
    "utf8",
  );
  const brandMacro = macroSource.slice(
    macroSource.indexOf("{% macro brand_picker"),
    macroSource.indexOf("{% endmacro %}"),
  );
  const comboboxIndex = brandMacro.indexOf("data-picker-combobox");
  const chipsIndex = brandMacro.indexOf("data-picker-chips");
  const inputIndex = brandMacro.indexOf("data-picker-input");
  const dropdownIndex = brandMacro.indexOf("data-picker-dropdown");
  const valueIndex = brandMacro.indexOf("data-picker-value");
  for (const index of [comboboxIndex, chipsIndex, inputIndex, dropdownIndex, valueIndex]) {
    assert.notEqual(index, -1, "brand_picker macro is missing a picker hook");
  }
  assert.ok(comboboxIndex < chipsIndex, "chips host must live inside the combobox container");
  assert.ok(chipsIndex < inputIndex, "chips must render before the inline text input");
  assert.ok(inputIndex < dropdownIndex, "dropdown must stay outside the combobox");
  assert.ok(dropdownIndex < valueIndex, "hidden textarea remains the submitted field");
});
