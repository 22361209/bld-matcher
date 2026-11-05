import assert from "node:assert/strict";
import test from "node:test";
import {applyCostReference, blankCostItem, calculationIssues, costAmountLabel, costingListReturnUrl, createCostPreviewGate, createCostTemplate, editableCostPayload, isCostCalculationField} from "../../static/pages/costing_edit.js";

import {mirrorCostCode, mirrorPartCode, rowPairKind} from "../../static/pages/costing_pair_edit.js";

test("finished model and component codes use separate handed suffix rules", () => {
  for (const [code, other] of [["K8299LA01-618", "K8299RA01-618"], ["K8057RB", "K8057LB"]]) {
    assert.equal(mirrorCostCode(code), other);
    assert.equal(mirrorPartCode(other), code);
  }
  for (const [code, other] of [["B6004L", "B6004R"], ["FA8121LD", "FA8121RD"], ["G8354R", "G8354L"]]) {
    assert.equal(mirrorCostCode(code), "");
    assert.equal(mirrorPartCode(code), other);
    assert.equal(rowPairKind({name: code}), "handed");
  }
  for (const code of ["10L", "BOLT027", "K8199LA-422RB"]) assert.equal(mirrorPartCode(code), "");
  assert.equal(rowPairKind({name: "左右共用胶套"}), "shared");
  assert.equal(rowPairKind({name: "左侧支架"}), "handed");
  assert.equal(rowPairKind({pair_kind: "local", name: "左侧支架"}), "local");
});

test("pair save sends the peer version and intentional row overrides while plain copies omit them", () => {
  const items = [{name: "支架", mode: "fixed", pair_kind: "handed", peer_product_code: "R2", peer_item_id: "server-owned"}];
  const paired = editableCostPayload({items, version: 2, pair: {id: "p"}, peer_version: 3});
  assert.equal(paired.peer_version, 3);
  assert.equal(paired.items[0].pair_kind, "handed");
  assert.equal(paired.items[0].peer_product_code, "R2");
  assert.equal("peer_item_id" in paired.items[0], false);
  const created = editableCostPayload({items}, {create_pair: true});
  assert.equal(created.create_pair, true);
  assert.equal(created.items[0].pair_kind, "handed");
  const plain = editableCostPayload({items});
  assert.equal("pair_kind" in plain.items[0], false);
  assert.equal("peer_product_code" in plain.items[0], false);
});

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

import {createWeightLookup} from "../../static/pages/costing_weight_lookup.js";

test("immediate save waits for the last row code and ignores an older lookup response", async () => {
  const item = {mode: "weight", product_code: "A", weight: "7"};
  const requests = new Map();
  const lookup = createWeightLookup({
    items: [item], lookup: (code) => new Promise((resolve) => requests.set(code, resolve)), notify() {},
  });
  const candidate = (code, weight) => ({kind: "stamping", product_code: code, token: code, gross_weight: weight});
  lookup.changed(item);
  const oldRequest = lookup.resolve(item);
  item.product_code = "B";
  lookup.changed(item);
  const submit = lookup.settle();
  requests.get("B")({candidates: [candidate("B", "2")], available: true});
  assert.equal(await submit, undefined);
  requests.get("A")({candidates: [candidate("A", "10")], available: true});
  await oldRequest;
  assert.equal(item.weight, "2");
  const body = editableCostPayload({items: [item], scrap_mode: "auto", scrap_unit_price: "2", scrap_credit_override: "0"});
  assert.equal(body.items[0].weight_choice, "B");
  assert.equal("weight_source" in body.items[0], false);
  assert.equal(body.scrap_credit_override, "0");
  assert.equal("scrap_credit" in body, false);
});

test("missing or ambiguous weight requires a choice, and manual weight can supersede a pending result", async () => {
  const item = {name: "焊接管", mode: "weight", product_code: "T03", weight: "7"};
  const options = [1, 2].map((n) => ({token: String(n), kind: "tube", product_code: "T03", gross_weight: String(n)}));
  let reply = {candidates: options, available: true};
  const calls = [];
  const lookup = createWeightLookup({items: [item], lookup: async (code, kind) => {
    calls.push([code, kind]); return reply;
  }, notify() {}});
  lookup.changed(item);
  assert.equal(await lookup.settle(), item);
  assert.deepEqual(calls, [["T03", "tube"]]);
  assert.equal(item.weight, "7");
  lookup.choose(item, options[1]);
  assert.equal(item.weight, "2");
  assert.equal(item.scrap_expected, false);
  item.product_code = "missing";
  lookup.changed(item);
  reply = {candidates: [], available: false};
  assert.equal(await lookup.settle(), item);
  assert.equal(item.weight, "2");
  lookup.manual(item);
  assert.equal(await lookup.settle(), undefined);
  assert.equal(item.weight_choice, null);
  reply = {candidates: [options[0]], available: true};
  const pending = lookup.resolve(item, true);
  item.weight = "8";
  lookup.manual(item);
  await pending;
  assert.equal(item.weight, "8");
  assert.equal(item.weight_choice, null);
});

test("batch recalculation refreshes existing plate codes, retains the saved specification and skips tubes", async () => {
  const stamp = {name: "旧板", mode: "weight", product_code: "P", weight_source: {kind: "stamping", source_id: "spec-2"}};
  const tube = {name: "焊接管", mode: "weight", product_code: "T", weight: "1"};
  const other = {name: "焊接支架", mode: "weight", product_code: "other", weight: "4"};
  const options = [1, 2].map((n) => ({kind: "stamping", product_code: "P", source_id: "spec-"+n, gross_weight: String(n), token: String(n)}));
  const calls = [];
  const lookup = createWeightLookup({items: [stamp, tube, other], lookup: async (code, kind) => {
    calls.push([code, kind]); return {available: true, candidates: code === "P" ? options : []};
  }, notify() {}});
  await lookup.refreshStamps();
  assert.deepEqual(calls, [["P", "stamping"], ["other", "stamping"]]);
  assert.equal(stamp.weight, "2");
  assert.equal(tube.weight, "1");
  assert.equal(other.weight, "4");
  assert.equal(lookup.unresolved(), undefined);
});
