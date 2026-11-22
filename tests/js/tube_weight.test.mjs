import assert from "node:assert/strict";
import test from "node:test";
import {previewTubeWeight, setupTubeWeightForm} from "../../static/pages/tube_form.js";

const tube = {weight_mode: "auto", outer_diameter_mm: "41", inner_diameter_mm: "34.5", weight_length_mm: "41.5", tolerance_mm: "0.25", consumption_mm: "3"};

test("single-tube preview matches workbook with mm input and does not multiply purchase base", () => {
  const result = previewTubeWeight({...tube, purchase_base: "2", weight_kg: "999"});
  assert.equal(result.state, "auto");
  assert.ok(Math.abs(result.weight - 0.1353899503125) < 1e-14);
  assert.equal(previewTubeWeight({...tube, purchase_base: "100"}).weight, result.weight);
});

test("missing factors keep weight absent while real zero allowances are calculable", () => {
  const missing = previewTubeWeight({...tube, tolerance_mm: ""});
  assert.equal(missing.state, "missing");
  assert.equal(missing.weight, null);
  assert.deepEqual(missing.errors, {});
  assert.match(missing.message, /公差/);
  const zero = previewTubeWeight({...tube, tolerance_mm: "0", consumption_mm: "0", inner_diameter_mm: "0"});
  assert.equal(zero.state, "auto");
  assert.ok(zero.weight > 0);
});

test("invalid dimensions and nonfinite inputs are rejected instead of made zero", () => {
  for (const changes of [
    {weight_length_mm: "0"}, {weight_length_mm: "-1"}, {outer_diameter_mm: "34.5"},
    {inner_diameter_mm: "42"}, {inner_diameter_mm: "-1"}, {tolerance_mm: "-0.1"},
    {consumption_mm: "NaN"}, {consumption_mm: "Infinity"}, {outer_diameter_mm: "1e309"},
    {outer_diameter_mm: "1e200"}, {weight_length_mm: "0x20"},
    {outer_diameter_mm: "0", inner_diameter_mm: ""},
  ]) {
    const result = previewTubeWeight({...tube, ...changes});
    assert.equal(result.state, "invalid", JSON.stringify(changes));
    assert.equal(result.weight, null);
    assert.ok(Object.keys(result.errors).length);
  }
});

test("manual values are independent of dimensions and preserve zero or absence", () => {
  assert.equal(previewTubeWeight({...tube, weight_mode: "manual", weight_kg: "0.123456789123"}).weight, 0.123456789123);
  assert.equal(previewTubeWeight({weight_mode: "manual", weight_kg: "0"}).weight, 0);
  assert.equal(previewTubeWeight({weight_mode: "manual", weight_kg: ""}).weight, null);
  for (const weight_kg of ["-1", "NaN", "Infinity"]) assert.equal(previewTubeWeight({weight_mode: "manual", weight_kg}).state, "invalid");
});

test("manual editing accepts legacy geometric anomalies but validates optional weight length", () => {
  const legacy = {...tube, weight_mode: "manual", weight_kg: "0.25", outer_diameter_mm: "1", inner_diameter_mm: "9", tolerance_mm: "-1", consumption_mm: "Infinity"};
  assert.equal(previewTubeWeight(legacy).state, "manual");
  assert.equal(previewTubeWeight({...legacy, weight_length_mm: ""}).weight, 0.25);
  for (const weight_length_mm of ["0", "-1", "NaN", "Infinity"]) {
    const result = previewTubeWeight({...legacy, weight_length_mm});
    assert.equal(result.state, "invalid");
    assert.deepEqual(Object.keys(result.errors), ["weight_length_mm"]);
  }
  assert.equal(previewTubeWeight({...legacy, weight_mode: "auto"}).state, "invalid");
});

test("only exact server-provided manual codes bypass the general formula", () => {
  const options = {manualWeightCodes: ["KG6028"]};
  for (const code of ["KG6028", "kg6028", " KG6028 "]) {
    const result = previewTubeWeight({...tube, code, weight_kg: "0.139480128"}, options);
    assert.equal(result.state, "manual");
    assert.equal(result.weight, 0.139480128);
    assert.match(result.message, /特殊公式.*待核对.*手工重量/);
  }
  for (const code of ["KG60280", "KG6028-1", "KG6028（借用）", "XKG6028", "KG6027"]) {
    assert.equal(previewTubeWeight({...tube, code}, options).state, "auto");
  }
  assert.equal(previewTubeWeight({...tube, code: "KG6028"}).state, "auto");
  assert.equal(previewTubeWeight({...tube, code: "KG6028", weight_kg: "-1"}, options).state, "invalid");
});

test("legacy manual rows without a weight length retain their weight with a completion hint", () => {
  const legacy = {...tube, weight_mode: "manual", weight_length_mm: "", weight_kg: "0.123456789123"};
  const result = previewTubeWeight(legacy, {editing: true});
  assert.equal(result.weight, 0.123456789123);
  assert.match(result.message, /原重量暂保留.*补齐计重长度.*切换自动计算/);
  assert.doesNotMatch(previewTubeWeight(legacy).message, /原重量暂保留/);
  assert.doesNotMatch(previewTubeWeight({...legacy, weight_length_mm: "41.5"}, {editing: true}).message, /原重量暂保留/);
});

test("automatic preview rejects a result that underflows to zero", () => {
  const result = previewTubeWeight({...tube, outer_diameter_mm: "1e-200", inner_diameter_mm: "0", weight_length_mm: "1", tolerance_mm: "0", consumption_mm: "0"});
  assert.equal(result.state, "invalid");
  assert.equal(result.weight, null);
  assert.match(result.message, /超出/);
});

test("borrowed records follow the entered source without guessing its root or weight", () => {
  const result = previewTubeWeight({...tube, borrowed_from: " KE8090 ", weight_kg: "999"});
  assert.equal(result.state, "borrowed");
  assert.equal(result.weight, null);
  assert.match(result.message, /KE8090/);
  assert.deepEqual(result.errors, {});
});

test("borrowed records validate optional weight length without calculating their own weight", () => {
  const borrowed = {...tube, borrowed_from: "KE8090", outer_diameter_mm: "-1", weight_kg: "999"};
  for (const weight_length_mm of ["", "40"]) {
    const result = previewTubeWeight({...borrowed, weight_length_mm});
    assert.equal(result.state, "borrowed");
    assert.equal(result.weight, null);
  }
  for (const weight_length_mm of ["-1", "0", "NaN", "Infinity"]) {
    const result = previewTubeWeight({...borrowed, weight_length_mm});
    assert.equal(result.state, "invalid");
    assert.deepEqual(Object.keys(result.errors), ["weight_length_mm"]);
    assert.match(result.message, /修正计重长度/);
    assert.match(result.message, /仍跟随.*KE8090/);
  }
});

function formFixture(values, {manualWeightCodes = [], editing = false} = {}) {
  const control = (value = "") => ({value, dataset: {}, disabled: false, readOnly: false, setCustomValidity(message) {this.validation = message;}, setAttribute(key, value) {this[key] = value;}});
  const inputs = Object.fromEntries(Object.entries({...tube, code: "KE8001", weight_kg: "0.123456789123", borrowed_from: "", ...values}).map(([key, value]) => [key, control(value)]));
  const mirror = control(), status = control(), formula = control(), submit = control();
  const hooks = {"[data-tube-weight-mode]": inputs.weight_mode, "[data-tube-weight-mode-mirror]": mirror,
    "[data-tube-borrowed-from]": inputs.borrowed_from, "[data-tube-weight-status]": status, "[data-tube-weight-formula]": formula, 'button[type="submit"]': submit};
  const handlers = {};
  const form = {dataset: {tubeManualWeightCodes: JSON.stringify(manualWeightCodes), tubeEditing: String(editing)},
    querySelector(selector) {return hooks[selector] || inputs[selector.match(/^\[name="(.+)"\]$/)?.[1]];},
    addEventListener(name, callback) {handlers[name] = callback;}, checkValidity() {return true;}, reportValidity() {}};
  return {form, inputs, mirror, status, handlers, submit, formula};
}

test("typing an exceptional code locks manual mode and restores the prior mode on leaving", () => {
  const {form, inputs, mirror, status, formula, handlers} = formFixture({}, {manualWeightCodes: ["KG6028"]});
  setupTubeWeightForm(form);
  const inputCode = (value) => {
    inputs.code.value = value;
    handlers.input({target: {matches: (selector) => selector.includes("[data-tube-code]")}});
  };
  assert.equal(inputs.weight_mode.value, "auto");
  inputCode("kg6028");
  assert.equal(inputs.weight_mode.value, "manual");
  assert.equal(inputs.weight_mode.disabled, true);
  assert.equal(mirror.disabled, false);
  assert.equal(mirror.value, "manual");
  assert.equal(inputs.weight_kg.readOnly, false);
  assert.equal(inputs.weight_kg.value, "0.123456789123");
  assert.equal(formula.hidden, true);
  assert.match(status.textContent, /KG6028.*特殊公式/);
  inputs.weight_kg.value = "0.139480128";
  inputCode("KE8001");
  assert.equal(inputs.weight_mode.value, "auto");
  assert.equal(inputs.weight_mode.disabled, false);
  assert.equal(mirror.disabled, true);
  assert.equal(inputs.weight_kg.readOnly, true);
  inputCode("KG6028");
  assert.equal(inputs.weight_kg.value, "0.139480128");
  assert.equal(inputs.weight_length_mm.value, "41.5");
});

test("manual exceptions preserve hand-entered weight through borrowing and mode changes", () => {
  const {form, inputs, mirror, status} = formFixture({code: "KG6028", weight_mode: "manual", weight_kg: "0.139480128"}, {manualWeightCodes: ["KG6028"], editing: true});
  const {update} = setupTubeWeightForm(form);
  inputs.weight_mode.value = "auto";
  update();
  assert.equal(inputs.weight_mode.value, "manual");
  assert.equal(inputs.weight_kg.value, "0.139480128");
  inputs.borrowed_from.value = "KE8090";
  update();
  assert.equal(status.dataset.state, "borrowed");
  assert.equal(inputs.weight_kg.value, "");
  assert.equal(inputs.weight_kg.readOnly, true);
  assert.equal(inputs.outer_diameter_mm.readOnly, true);
  assert.equal(mirror.value, "manual");
  inputs.borrowed_from.value = "";
  update();
  assert.equal(inputs.weight_kg.value, "0.139480128");
  assert.equal(inputs.weight_kg.readOnly, false);
  inputs.code.value = "KE8001";
  update();
  assert.equal(inputs.weight_mode.value, "manual");
  assert.equal(inputs.weight_mode.disabled, false);
  assert.equal(inputs.weight_kg.value, "0.139480128");
});

test("switching auto or borrowing preserves manual precision and dimensional inputs", () => {
  const {form, inputs, mirror, status} = formFixture({weight_mode: "manual"});
  const {update} = setupTubeWeightForm(form);
  inputs.weight_mode.value = "auto";
  update();
  assert.equal(inputs.weight_kg.readOnly, true);
  assert.notEqual(inputs.weight_kg.value, "0.123456789123");
  inputs.borrowed_from.value = "KE8090";
  update();
  assert.equal(inputs.outer_diameter_mm.value, "41");
  assert.equal(inputs.outer_diameter_mm.readOnly, true);
  assert.equal(inputs.outer_diameter_mm.disabled, false);
  assert.equal(inputs.weight_kg.value, "");
  assert.equal(inputs.weight_mode.disabled, true);
  assert.equal(mirror.disabled, false);
  inputs.borrowed_from.value = "";
  inputs.weight_mode.value = "manual";
  update();
  assert.equal(inputs.weight_kg.value, "0.123456789123");
  assert.equal(inputs.weight_kg.readOnly, false);
  assert.equal(inputs.outer_diameter_mm.readOnly, false);
  assert.equal(inputs.weight_mode.disabled, false);
  assert.equal(mirror.disabled, true);
  assert.equal(status.dataset.state, "manual");
});

test("preview reacts to missing and corrected inputs without rewriting descriptive lengths", () => {
  const {form, inputs, status} = formFixture({weight_length_mm: "", spec_text: "ø41×34.5×41", blank_length_text: "41±0.1"});
  const {update} = setupTubeWeightForm(form);
  assert.equal(inputs.weight_kg.value, "");
  assert.equal(status.dataset.state, "missing");
  inputs.weight_length_mm.value = "41.5";
  update();
  assert.ok(Number(inputs.weight_kg.value) > 0);
  inputs.inner_diameter_mm.value = "41";
  update();
  assert.equal(inputs.weight_kg.value, "");
  assert.equal(status.dataset.state, "invalid");
  assert.equal(inputs.outer_diameter_mm['aria-invalid'], "true");
  assert.equal(inputs.spec_text.value, "ø41×34.5×41");
  assert.equal(inputs.blank_length_text.value, "41±0.1");
});

test("borrowed length errors stay editable throughout correction without clearing values", () => {
  const {form, inputs, status, handlers, submit} = formFixture({borrowed_from: "KE8090", weight_length_mm: "-1"});
  const {update} = setupTubeWeightForm(form);
  assert.equal(inputs.weight_length_mm.value, "-1");
  assert.equal(inputs.weight_length_mm.readOnly, false);
  assert.equal(inputs.outer_diameter_mm.readOnly, true);
  assert.equal(inputs.weight_kg.readOnly, true);
  assert.equal(status.dataset.state, "invalid");
  let prevented = false;
  handlers.submit({preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(submit.disabled, false);
  for (const digit of ["4", "40"]) {
    inputs.weight_length_mm.value = digit;
    update();
    assert.equal(inputs.weight_length_mm.readOnly, false);
    assert.equal(inputs.weight_length_mm.value, digit);
    assert.equal(inputs.weight_kg.value, "");
    assert.equal(status.dataset.state, "borrowed");
  }
  assert.equal(inputs.outer_diameter_mm.value, "41");
});

test("restoring a submitted form through pageshow enables saving again", () => {
  const {form, handlers, submit} = formFixture({weight_mode: "manual"});
  const pageHandlers = {};
  form.ownerDocument = {defaultView: {addEventListener(name, callback) {pageHandlers[name] = callback;}}};
  setupTubeWeightForm(form);
  handlers.submit({preventDefault() {assert.fail("valid submission must proceed");}});
  assert.equal(submit.disabled, true);
  pageHandlers.pageshow({persisted: true});
  assert.equal(submit.disabled, false);
});
