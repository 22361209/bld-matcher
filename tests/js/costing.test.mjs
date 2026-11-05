import assert from "node:assert/strict";
import test from "node:test";
import {applyCostReference, blankCostItem, calculationIssues, costAmountLabel, costingListReturnUrl, createCostPreviewGate, createCostTemplate, editableCostPayload, isCostCalculationField} from "../../static/pages/costing_edit.js";

test("preview runs for the line product code and financial inputs, while descriptive edits stay local", () => {
  for (const key of ["code", "name", "kind", "brand", "models", "category", "supplier", "part_code", "material_attribute", "remark", "review_note"]) {
    assert.equal(isCostCalculationField(key), false, key);
  }
  for (const key of ["product_code", "mode", "amount", "unit_price", "quantity", "weight", "processing_fee",
    "length", "width", "height", "strap_rate", "approved_cost", "scrap_credit", "theoretical_scrap", "suggested_tax_price_override"]) {
    assert.equal(isCostCalculationField(key), true, key);
  }
});

test("back links keep filters from the same cost list and ignore unrelated or external referrers", () => {
  for (const path of ["/costs", "/"]) {
    const list = new URL(path, "https://example.test");
    const filtered = `${list.href}?q=K8057&sort=updated&page=3`;
    assert.equal(costingListReturnUrl(list, filtered), filtered);
    for (const source of ["", "invalid", "https://example.test/costs/new", "https://another.test" + path]) {
      assert.equal(costingListReturnUrl(list, source), list.href);
    }
  }
});

test("preview gate rejects response arriving after newer user input", () => {
  const gate = createCostPreviewGate();
  const first = gate.current();
  gate.invalidate();
  const second = gate.current();
  assert.equal(gate.accepts(first), false);
  assert.equal(gate.accepts(second), true);
  gate.invalidate();
  assert.equal(gate.accepts(second), false);
});

test("a late preview supplies numeric warnings separately from the editable name and review note", () => {
  const note = "请填写金额";
  const items = [
    {review_note: note, issues: [note, "请填写成本项名称", note]},
    {review_note: "", issues: ["请填写重量"]},
  ];
  const issues = items.flatMap((item, i) => item.issues.map((issue) => `第 ${i + 1} 项：${issue}`));
  const sheetWarning = "废料抵减额超过采用成本，请核对";
  assert.deepEqual(calculationIssues({items, issues: [...issues, sheetWarning]}), {
    rows: [[note], ["请填写重量"]], sheet: [sheetWarning],
  });
  assert.equal(items[0].issues.length, 3);
});

test("write payload preserves zeros, blanks, decimal strings and item identities without client attribution", () => {
  const payload = editableCostPayload({
    id: "old", code: "K0001", version: 7, approved_cost: "0", source_info: {filename: "private"},
    actor: "forged", calculated_cost: "900", theoretical_scrap: "-0.4",
    items: [{id: "item1", name: "<script>", mode: "weight", quantity: "", unit_price: "4.500000001", weight: "0.5", processing_fee: "0", source_info: {row: 8}, line_total: "999", review_note: ""}],
  }, {code: "K0002", scrap_credit: ""});
  assert.equal(payload.code, "K0002");
  assert.equal(payload.version, 7);
  assert.equal(payload.approved_cost, "0");
  assert.equal(payload.scrap_credit, "");
  assert.equal(payload.theoretical_scrap, "-0.4");
  assert.equal(payload.items[0].id, "item1");
  assert.equal(payload.items[0].quantity, "");
  assert.equal(payload.items[0].unit_price, "4.500000001");
  assert.equal(payload.items[0].name, "<script>");
  assert.equal(payload.id, "old");
  assert.equal("actor" in payload, false);
  assert.equal("source_info" in payload, false);
  assert.equal("source_info" in payload.items[0], false);
  assert.equal("line_total" in payload.items[0], false);
});

test("changing a return row to weight pricing omits inactive negative amount without losing local value", () => {
  const item = {name: "毛坯", mode: "weight", amount: "-2", unit_price: "4", weight: "0.5", quantity: "1"};
  const record = {code: "K0001", items: [item]};
  assert.equal("amount" in editableCostPayload(record).items[0], false);
  assert.equal(item.amount, "-2");
  item.mode = "adjustment";
  const returnPayload = editableCostPayload(record).items[0];
  assert.equal(returnPayload.amount, "-2");
  assert.equal("unit_price" in returnPayload, false);
});

test("suggested price override preserves zero and can return to automatic without sending derived prices", () => {
  const record = {code: "K0001", suggested_tax_price_override: "140", minimum_tax_price: "125", suggested_tax_price: "140", items: []};
  assert.equal(editableCostPayload(record).suggested_tax_price_override, "140");
  const cleared = editableCostPayload(record, {suggested_tax_price_override: ""});
  assert.equal(cleared.suggested_tax_price_override, "");
  assert.equal("minimum_tax_price" in cleared, false);
  assert.equal("suggested_tax_price" in cleared, false);
  assert.equal(editableCostPayload(record, {suggested_tax_price_override: "0"}).suggested_tax_price_override, "0");
});

test("new items and templates require actual costs, and copies do not share mutable rows", () => {
  assert.equal(blankCostItem().amount, "");
  assert.equal(blankCostItem().material_attribute, "自制");
  const rows = [{name: "主板", category: "焊接组件", material_attribute: "自制"},
    {name: "电泳", category: "表面处理", material_attribute: "委外加工"}];
  const first = createCostTemplate(rows);
  const second = createCostTemplate(rows);
  first[0].amount = "8.5";
  assert.equal(second[0].amount, "");
  assert.ok(first.every((item) => item.mode === "fixed"));
  assert.deepEqual(first.map((item) => item.material_attribute), ["自制", "委外加工"]);
});

test("material attribute is optional and explicitly cleared selections are submitted", () => {
  const item = {id: "part", product_code: "P-21", amount: "0", mode: "fixed"};
  const submitted = () => editableCostPayload({items: [item]}).items[0];
  assert.equal("material_attribute" in submitted(), false);
  for (const attribute of ["自制", "外购", "委外加工", ""]) {
    item.material_attribute = attribute;
    assert.equal(submitted().material_attribute, attribute);
    assert.equal(submitted().product_code, "P-21");
  }
});

test("display distinguishes unknown values, actual zero and negative adjustments", () => {
  assert.equal(costAmountLabel(null), "待补充");
  assert.equal(costAmountLabel(""), "待补充");
  assert.equal(costAmountLabel("0"), "0");
  assert.equal(costAmountLabel("-0.4"), "-0.4");
  assert.equal(costAmountLabel("19.783652"), "19.783652");
});

test("choosing a carton keeps its number and specification in separate editable fields", () => {
  const item = {mode: "package", name: "", quantity: "12", strap_rate: "0.06"};
  const reference = {code: "ZX-01", name: "42×31×15.5", material_attribute: "外购", unit_price: "0", length: "42", width: "31", height: "15.5"};
  const expectNoPrompt = () => assert.fail("blank or matching codes need no confirmation");
  assert.equal(applyCostReference(item, reference, expectNoPrompt), true);
  assert.equal(applyCostReference(item, reference, expectNoPrompt), true);
  const [payload] = editableCostPayload({items: [item]}).items;
  assert.deepEqual(payload, {
    mode: "package", name: "纸箱", category: "外包装", part_code: "ZX-01", product_code: "42×31×15.5", material_attribute: "外购",
    unit_price: "0", length: "42", width: "31", height: "15.5", quantity: "12", strap_rate: "0.06",
  });
});

test("declining a different carton specification leaves every field intact", () => {
  const item = {mode: "package", name: "手填名称", category: "包装", part_code: "ZX-A", product_code: "手填编码", unit_price: "9"};
  const original = {...item};
  const reference = {code: "ZX-B", name: "40*20*10", unit_price: "4.5", length: "40", width: "20", height: "10"};
  assert.equal(applyCostReference(item, reference, (message) => {
    assert.match(message, /手填编码/);
    assert.ok(message.includes(reference.name));
    return false;
  }), false);
  assert.deepEqual(item, original);
  assert.equal(applyCostReference(item, reference, () => true), true);
  assert.equal(item.product_code, reference.name);
  assert.equal(item.part_code, reference.code);
  assert.equal(item.unit_price, reference.unit_price);
});

test("choosing references can replace defaults while preserving explicit attributes, including blank", () => {
  for (const attribute of ["自制", "委外加工", ""]) {
    const item = {...blankCostItem(), mode: "package", material_attribute: attribute};
    applyCostReference(item, {name: "42×31×15.5", material_attribute: "外购"}, () => true, true);
    assert.equal(item.material_attribute, attribute);
  }
  const item = blankCostItem();
  applyCostReference(item, {name: "球座", amount: "2", material_attribute: "外购"});
  assert.equal(item.material_attribute, "外购");
});

test("fixed fee references preserve the row identity and enter a name only when empty", () => {
  const item = {mode: "fixed", name: "自定工费", category: "安装", part_code: "P1", product_code: "P2"};
  const reference = {name: "安装费", amount: "0"};
  applyCostReference(item, reference);
  assert.deepEqual(item, {mode: "fixed", name: "自定工费", category: "安装", part_code: "P1", product_code: "P2", amount: "0"});
  item.name = "";
  applyCostReference(item, reference);
  assert.equal(item.name, "安装费");
});
