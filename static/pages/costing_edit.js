import {mirrorCostCode, mirrorPartCode, pairKindExplanation, pairKindOptions, rowPairKind} from "./costing_pair_edit.js";

const text = (value) => (value == null ? "" : String(value));
const COST_FIELDS = ["code", "kind", "name", "brand", "models", "remark", "approved_cost", "scrap_credit", "theoretical_scrap", "suggested_tax_price_override"];
const PAIR_ITEM_FIELDS = new Set(["pair_kind", "peer_product_code", "peer_part_code"]);
const ITEM_FIELDS = ["id", "name", "category", "product_code", "material_attribute", "part_code", "supplier", "remark", "mode", "amount", "unit_price", "quantity", "weight", "processing_fee", "length", "width", "height", "strap_rate", "review_note", "pair_kind", "peer_product_code", "peer_part_code"];
const MODES = {fixed: "直接金额", unit: "按件计算", weight: "按重量计算", package: "包装分摊", freight: "按重量运费", adjustment: "调整 / 返还"};
const NUMERIC_FIELDS = new Set(["amount", "unit_price", "quantity", "weight", "processing_fee", "length", "width", "height", "strap_rate"]);
const SUMMARY_INPUTS = new Set(["approved_cost", "scrap_credit", "theoretical_scrap", "suggested_tax_price_override"]);
const PAIR_CAPTIONS = {shared: "共用", handed: "对应", local: "独有"};
const MISSING_ITEM_NAME = "请填写成本项名称";
const MODE_NUMBERS = {fixed: ["amount"], adjustment: ["amount"], unit: ["unit_price", "quantity"], weight: ["unit_price", "quantity", "weight", "processing_fee"], freight: ["unit_price", "weight"], package: ["unit_price", "quantity", "length", "width", "height", "strap_rate"]};

export const isCostCalculationField = (key) => key === "product_code" || key === "mode" || NUMERIC_FIELDS.has(key) || SUMMARY_INPUTS.has(key);

export const blankCostItem = () => ({name: "", category: "", material_attribute: "自制", mode: "fixed", amount: "", quantity: "1", processing_fee: "0", review_note: ""});

export const applyCostReference = (item, reference, confirmReplace, keepMaterialAttribute = false) => {
  if (item.mode === "fixed") {
    item.amount = reference.amount;
    if (!item.name) item.name = reference.name;
    if (!keepMaterialAttribute && item.name === reference.name && reference.material_attribute) {
      item.material_attribute = reference.material_attribute;
    }
    return true;
  }
  if (item.mode !== "package") return false;
  const spec = text(reference.name).trim();
  const current = text(item.product_code).trim();
  if (spec && current && spec !== current &&
      !confirmReplace(`当前产品编码为「${current}」，所选纸箱规格为「${spec}」。要用该规格替换产品编码，并带入纸箱参考资料吗？`)) {
    return false;
  }
  item.name = "纸箱";
  item.category = "外包装";
  if (!keepMaterialAttribute && reference.material_attribute) item.material_attribute = reference.material_attribute;
  if (spec) item.product_code = spec;
  const partCode = text(reference.code).trim();
  if (partCode) item.part_code = partCode;
  for (const key of ["unit_price", "length", "width", "height"]) item[key] = reference[key];
  return true;
};

export const createCostTemplate = (rows) => rows.map((row) => ({...blankCostItem(), ...row}));

export const editableCostPayload = (record, values = {}) => {
  const payload = {};
  COST_FIELDS.forEach((key) => { payload[key] = key in values ? values[key] : record[key] ?? ""; });
  if (record.pair) payload.peer_version = record.peer_version;
  if (values.create_pair === true) payload.create_pair = true;
  if (record.id) payload.id = record.id;
  if (record.version != null) payload.version = record.version;
  const paired = Boolean(record.pair || payload.create_pair);
  payload.items = (record.items || []).map((item) => Object.fromEntries(
    ITEM_FIELDS.filter((key) => key in item && (paired || !PAIR_ITEM_FIELDS.has(key)) && (!NUMERIC_FIELDS.has(key) || MODE_NUMBERS[item.mode]?.includes(key))).map((key) => [key, item[key]]),
  ));
  return payload;
};

export const createCostPreviewGate = () => {
  let serial = 0;
  return {invalidate: () => ++serial, current: () => serial, accepts: (token) => token === serial};
};

export const costingListReturnUrl = (list, referrer) => {
  try {
    const source = new URL(referrer);
    if (source.origin === list.origin && source.pathname === list.pathname) return source.href;
  } catch { /* Direct visits have no referring page. */ }
  return list.href;
};

export const costAmountLabel = (value) => {
  if (value === null || value === undefined || value === "") return "待补充";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 6}).format(parsed) : text(value);
};

const element = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = text(content);
  return node;
};

const issueText = (issue) => typeof issue === "string" ? issue : text(issue?.message || issue?.field || "待核对");

// The server owns numeric validation. Name and review-note warnings follow the
// current inputs, even if an earlier numeric preview only just finished.
export const calculationIssues = (result) => {
  const items = result.items || [];
  const rowMessages = new Set();
  const rows = items.map((item, index) => {
    const messages = (item.issues || []).map(issueText);
    for (const message of messages) rowMessages.add(`第 ${index + 1} 项：${message}`);
    if (item.review_note && messages.at(-1) === item.review_note) messages.pop();
    return messages.filter((message) => message !== MISSING_ITEM_NAME);
  });
  const issues = result.issues ?? (rows.length ? [] : ["请添加成本明细"]);
  return {rows, sheet: issues.map(issueText).filter((message) => !rowMessages.has(message))};
};

const rowButton = (symbol, label) => {
  const button = element("button", "costing-row-button");
  const face = element("span", "", symbol);
  face.setAttribute("aria-hidden", "true");
  button.append(face);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  return button;
};

const updateRemoveLabel = (row, index, name) => {
  const button = row.querySelector("[data-costing-remove]");
  button.title = `移除第 ${index + 1} 项 ${name || "未命名项目"}`;
  button.setAttribute("aria-label", button.title);
};

const updateDetailsButton = (button) => {
  const open = button.getAttribute("aria-expanded") === "true";
  const action = open ? "收起" : button.dataset.issueText ? "待核对" : "展开";
  button.textContent = button.dataset.relation ? `${button.dataset.relation} · ${action}` : action;
  button.setAttribute("aria-label", `第 ${Number(button.dataset.costingDetails) + 1} 项资料 · ${button.textContent}`);
};

const updateRowIssues = (row, item) => {
  const button = row.querySelector("[data-costing-details]");
  const messages = (item.issues || []).map(issueText).join("；") || text(item.review_note);
  button.dataset.issueText = messages;
  button.title = messages;
  updateDetailsButton(button);
  if (button.getAttribute("aria-expanded") === "true") {
    const note = row.nextElementSibling.querySelector("[data-line-issues]");
    note.textContent = messages;
    note.hidden = !messages;
  }
};

export const initializeCostEditor = (form) => {
  let record;
  try { record = JSON.parse(form.dataset.record); } catch (_error) { return; }
  let references = {};
  try { references = JSON.parse(form.dataset.references || "{}"); } catch (_error) { references = {}; }
  record.items = Array.isArray(record.items) ? record.items : [];
  // Older transfers may store the mode with surrounding spaces.
  for (const item of record.items) item.mode = text(item.mode ?? "fixed").trim();
  const savedOrSelectedAttributes = new WeakSet(record.items.filter((item) => "material_attribute" in item));
  const editable = form.dataset.editable === "true";
  const tbody = form.querySelector("[data-costing-items]");
  const attributeOptions = [["", "—"], ...JSON.parse(tbody.dataset.materialAttributes).map((value) => [value, value])];
  const modeOptions = Object.entries(MODES);
  const errorBox = form.querySelector("[data-costing-error]");
  const status = form.querySelector("[data-costing-status]");
  const modelCode = form.elements.namedItem("code");
  const createPair = form.querySelector("[data-create-pair]");
  const pairActive = () => Boolean(record.pair || createPair?.checked);
  const originalKinds = new Map(record.items.map((item) => [item.id, item.pair_kind]));
  const updatePairCode = () => {
    const otherCode = mirrorCostCode(modelCode.value);
    const preview = form.querySelector("[data-pair-code-preview]");
    if (preview) preview.textContent = createPair?.checked ? otherCode ? `对侧型号 ${otherCode}` : "型号应含一处 LA / LB / LC 或 RA / RB / RC" : "";
    const code = modelCode.value.trim();
    modelCode.setCustomValidity(!code ? "请填写型号。" : /\s/.test(code) ? "型号中不能含空格。" :
      createPair?.checked && !otherCode ? "请填写可识别的左右件型号。" : "");
  };
  const rowCount = form.querySelector("[data-costing-row-count]");
  const issueTrigger = form.querySelector("[data-costing-issue-trigger]");
  const issueDetails = form.querySelector("[data-costing-issues]");
  const compactPriceMode = form.querySelector("[data-costing-compact-price-mode]");
  const gate = createCostPreviewGate();
  let timer;
  let dirty = false;
  let saving = false;
  let deleted = null;
  let firstIssueRow = -1;
  let checks;

  const values = () => Object.fromEntries(COST_FIELDS.map((key) => [key, form.elements.namedItem(key)?.value ?? record[key] ?? ""]));
  const payload = () => editableCostPayload(record, {...values(), create_pair: Boolean(createPair?.checked)});
  const snapshot = () => JSON.stringify(payload(), (_key, value) => value == null || value === "" ? undefined : value);
  const initialSnapshot = editable ? snapshot() : "";
  const hasUnsavedChanges = () => dirty && (snapshot() !== initialSnapshot ||
    [...form.querySelectorAll('input[type="number"]:invalid')].some((input) => input.validity.badInput));
  const list = new URL(form.dataset.listUrl, window.location.href);
  const backUrl = costingListReturnUrl(list, document.referrer);
  form.querySelectorAll("[data-costing-back]").forEach((link) => { link.href = backUrl; });
  let leaveTo = list.href;
  const leaveDialog = document.querySelector("[data-costing-leave-dialog]");
  let reloadOnRestore = false;
  if (leaveDialog) {
    document.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest("a[href]");
      if (!link || (link.href !== list.href && link.href !== backUrl) ||
          (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
      if (saving) {
        event.preventDefault();
      } else if (hasUnsavedChanges()) {
        event.preventDefault();
        leaveTo = link.href;
        if (!leaveDialog.open) leaveDialog.showModal();
      }
    });
    leaveDialog.querySelector("[data-costing-leave-continue]").addEventListener("click", () => leaveDialog.close());
    leaveDialog.querySelector("[data-costing-leave-discard]").addEventListener("click", () => {
      dirty = false;
      reloadOnRestore = true;
      clearTimeout(timer);
      gate.invalidate();
      leaveDialog.close();
      window.location.assign(leaveTo);
    });
    leaveDialog.querySelector("[data-costing-leave-save]").addEventListener("click", (event) => {
      event.preventDefault();
      leaveDialog.close();
      form.requestSubmit(event.currentTarget);
    });
  }
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && reloadOnRestore) window.location.reload();
  });
  const showError = (message, focus = false) => {
    errorBox.textContent = text(message);
    errorBox.hidden = !message;
    if (message && focus) errorBox.focus();
  };
  const updateReview = () => {
    if (!checks) return;
    const issues = [];
    const rows = tbody.querySelectorAll("[data-costing-row]");
    record.items.forEach((item, index) => {
      item.issues = [...checks.rows[index]];
      if (!text(item.name).trim()) item.issues.push(MISSING_ITEM_NAME);
      if (item.review_note) item.issues.push(item.review_note);
      updateRowIssues(rows[index], item);
      for (const issue of item.issues) issues.push(`第 ${index + 1} 项：${issue}`);
    });
    issues.push(...checks.sheet);
    firstIssueRow = record.items.findIndex((item) => item.issues.length);
    const complete = !issues.length && modelCode.value.trim() && modelCode.validity.valid;
    form.querySelector("[data-costing-completeness]").textContent = complete ? "资料完整" : "待补充 / 核对";
    issueTrigger.textContent = `待核对 ${issues.length} 项`;
    issueTrigger.hidden = !issues.length;
    issueDetails.hidden = !issues.length;
    form.querySelector("[data-costing-issue-title]").textContent = `待补充 / 核对（${issues.length}）`;
    const issueList = form.querySelector("[data-costing-issue-list]");
    issueList.replaceChildren(...issues.map((issue) => element("li", "", issueText(issue))));
  };
  const updateTotals = (calculated) => {
    checks = calculationIssues(calculated);
    form.querySelectorAll("[data-costing-total]").forEach((node) => {
      const key = node.dataset.costingTotal;
      node.textContent = costAmountLabel(calculated[key] ?? (key === "scrap_credit" ? "0" : null));
    });
    form.querySelector("[data-costing-known]").textContent = calculated.calculated_cost == null ? `已知项目小计：${costAmountLabel(calculated.known_subtotal)} 元` : "";
    const manualPrice = calculated.suggested_tax_price_override != null && calculated.suggested_tax_price_override !== "";
    form.querySelector("[data-costing-price-mode]").textContent = manualPrice ? "手动定价，成本变化时保留。" : "自动跟随最低售价。";
    compactPriceMode.textContent = manualPrice ? "手动" : "自动";
    const priceInput = form.elements.namedItem("suggested_tax_price_override");
    priceInput.placeholder = calculated.minimum_tax_price == null ? "留空自动采用最低售价" : `自动：${costAmountLabel(calculated.minimum_tax_price)}`;
    rowCount.textContent = `${record.items.length} 行`;
    tbody.querySelectorAll("[data-line-total]").forEach((node, index) => {
      record.items[index].line_total = calculated.items[index].line_total;
      node.textContent = costAmountLabel(record.items[index].line_total);
    });
    updateReview();
  };
  const scrapToggle = form.querySelector("[data-costing-scrap-toggle]");
  const scrapDetail = document.getElementById(scrapToggle.getAttribute("aria-controls"));
  const showScrapDetail = (open) => {
    scrapDetail.hidden = !open;
    scrapToggle.setAttribute("aria-expanded", String(open));
  };
  scrapToggle.addEventListener("click", () => showScrapDetail(scrapDetail.hidden));
  scrapDetail.addEventListener("invalid", () => showScrapDetail(true), true);
  issueTrigger.addEventListener("click", () => {
    const row = tbody.querySelector(`[data-costing-row="${firstIssueRow}"]`);
    const disclosure = row?.querySelector("[data-costing-details]");
    if (disclosure) {
      if (disclosure.getAttribute("aria-expanded") !== "true") disclosure.click();
      disclosure.focus();
      row.scrollIntoView({block: "center", inline: "nearest"});
    } else {
      issueDetails.open = true;
      issueDetails.querySelector("summary").focus();
    }
  });
  const pendingTotals = () => {
    checks = null;
    form.querySelectorAll("[data-costing-total], [data-line-total]").forEach((node) => { node.textContent = "重算中…"; });
    form.querySelector("[data-costing-known]").textContent = "";
    form.querySelector("[data-costing-completeness]").textContent = "";
    rowCount.textContent = `${record.items.length} 行`;
    compactPriceMode.textContent = "";
    issueTrigger.hidden = true;
    issueDetails.hidden = true;
  };
  const post = async (url, body) => {
    const response = await fetch(url, {
      method: "POST", credentials: "same-origin", headers: {
        "Content-Type": "application/json", "Accept": "application/json",
        "X-CSRF-Token": form.querySelector('[name="csrf_token"]').value,
      }, body: JSON.stringify(body),
    });
    let result;
    try { result = await response.json(); } catch (_error) { throw new Error("未收到有效响应，请检查连接后重试。输入内容已保留。"); }
    if (!response.ok || !result.ok) {
      const raw = result.message || result.error;
      const message = typeof raw === "string" ? raw : raw?.message;
      throw new Error(response.status === 409 ? `${message || "此型号已被修改或已存在。"} 当前输入已保留，请另开页面核对。` : message || "操作失败，输入内容已保留。");
    }
    return result;
  };
  const preview = async () => {
    if (!editable || saving) return;
    const token = gate.current();
    status.textContent = "正在核算…";
    try {
      const body = payload();
      // The preview endpoint accepts an unnamed draft while its model code is being edited.
      if (!modelCode.validity.valid) body.code = "";
      const result = await post(form.dataset.previewUrl, body);
      if (!gate.accepts(token) || saving) return;
      updateTotals(result.record);
      showError("");
      status.textContent = hasUnsavedChanges() ? "有未保存修改" : "";
    } catch (error) {
      if (!gate.accepts(token) || saving) return;
      status.textContent = "核算未完成";
      form.querySelectorAll("[data-costing-total], [data-line-total]").forEach((node) => { node.textContent = "未计算"; });
      showError(error.message);
    }
  };
  const markEdited = () => {
    dirty = true;
    status.textContent = hasUnsavedChanges() ? "有未保存修改" : "";
  };
  const scheduleCalculation = () => {
    markEdited();
    gate.invalidate();
    clearTimeout(timer);
    pendingTotals();
    timer = setTimeout(preview, 350);
  };
  const columns = Array.from(tbody.closest("table").tHead.rows[0].cells, (header) => header.dataset.col);
  const field = (item, index, key, label, options = {}) => {
    const input = element(options.multiline ? "textarea" : "input");
    if (!options.multiline) input.type = NUMERIC_FIELDS.has(key) ? "number" : "text";
    if (NUMERIC_FIELDS.has(key)) {
      input.step = "any"; input.inputMode = "decimal";
      if (!(key === "amount" && item.mode === "adjustment")) input.min = "0";
    } else input.maxLength = key === "remark" ? 2000 : 240;
    input.value = text(item[key]); input.dataset.itemField = key; input.dataset.itemIndex = index;
    input.setAttribute("aria-label", `第 ${index + 1} 项 ${label}`);
    input.disabled = !editable;
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.inline) {
      if (!options.unit) return input;
      const group = element("div", "costing-cell-unit");
      const unit = element("span", "", options.unit);
      unit.setAttribute("aria-hidden", "true");
      group.append(input, unit);
      return group;
    }
    const wrapper = element("label", "", label);
    wrapper.append(input);
    return wrapper;
  };
  const selectField = (item, index, key, label, options) => {
    const select = element("select");
    select.dataset.itemField = key;
    select.dataset.itemIndex = index;
    select.setAttribute("aria-label", `第 ${index + 1} 项 ${label}`);
    select.disabled = !editable;
    for (const [value, caption] of options) {
      const option = element("option", "", caption);
      option.value = value;
      option.selected = value === text(item[key]);
      select.append(option);
    }
    return select;
  };
  const renderRow = (item, index) => {
    const row = element("tr");
    row.dataset.costingRow = index;
    const cells = {};
    for (const key of columns) {
      const cell = element("td");
      cell.dataset.col = key;
      row.append(cell);
      cells[key] = cell;
    }
    const controls = element("div", "costing-row-controls");
    controls.append(element("span", "costing-row-number", index + 1));
    if (editable) {
      const insert = rowButton("+", `在第 ${index + 1} 项下方插入一行`);
      insert.dataset.costingInsert = index;
      const remove = rowButton("−", `移除第 ${index + 1} 项 ${item.name || "未命名项目"}`);
      remove.dataset.costingRemove = index;
      controls.append(insert, remove);
    }
    cells.row.append(controls);
    cells.category.append(field(item, index, "category", "类别", {inline: true}));
    cells.name.append(field(item, index, "name", "项目名称", {inline: true}));
    cells.product_code.append(field(item, index, "product_code", "产品编码", {inline: true}));
    cells.supplier.append(field(item, index, "supplier", "首选供应商", {inline: true}));
    cells.material_attribute.append(selectField(item, index, "material_attribute", "物料属性", attributeOptions));
    cells.mode.append(selectField(item, index, "mode", "计价方式", modeOptions));

    const byWeight = item.mode === "weight" || item.mode === "freight";
    const perBox = item.mode === "package";
    const fixed = item.mode === "fixed" || item.mode === "adjustment";
    const unit = fixed ? "元" : byWeight ? "元/kg" : perBox ? "元/箱" : "元/件";
    const priceLabel = fixed ? "金额" : perBox ? "纸箱单价" : "单价";
    cells.price.append(field(item, index, fixed ? "amount" : "unit_price", `${priceLabel}（${unit}）`, {inline: true, unit}));
    if (byWeight) cells.usage.append(field(item, index, "weight", "重量（kg）", {inline: true, unit: "kg"}));
    else if (perBox || item.mode === "unit") cells.usage.append(field(item, index, "quantity", perBox ? "每箱装箱数（件）" : "每件用量", {inline: true, unit: perBox ? "件/箱" : "件"}));
    else cells.usage.append(element("span", "muted-text", "—"));

    const total = element("strong", "costing-line-total");
    total.dataset.lineTotal = "";
    cells.total.append(total);
    const button = element("button", "link-button costing-details-toggle");
    button.type = "button";
    button.dataset.costingDetails = index;
    if (pairActive()) button.dataset.relation = PAIR_CAPTIONS[rowPairKind(item)];
    button.setAttribute("aria-expanded", "false");
    cells.details.append(button);
    total.textContent = costAmountLabel(item.line_total);
    updateRowIssues(row, item);
    return row;
  };
  const renderDetails = (item, index, issueMessage) => {
    const row = element("tr", "costing-detail-row");
    row.id = `costing-row-details-${index}`;
    const cell = element("td");
    cell.colSpan = columns.length;
    const details = element("div", "costing-detail-fields");
    const note = element("p", "costing-review-note", issueMessage);
    note.dataset.lineIssues = "";
    note.hidden = !issueMessage;
    details.append(note);
    if (pairActive()) {
      const kind = rowPairKind(item);
      const wrapper = element("label", "", "左右件明细关系");
      wrapper.append(selectField({...item, pair_kind: kind}, index, "pair_kind", "明细关系", pairKindOptions));
      details.append(wrapper, element("p", "costing-source-note", pairKindExplanation(item, originalKinds.get(item.id))));
      if (kind === "handed" && (!item.peer_item_id || originalKinds.get(item.id) === "local")) {
        for (const [key, caption] of [["product_code", "对侧产品编码"], ["part_code", "对侧零件编码"]]) {
          const peerKey = "peer_" + key;
          details.append(field({...item, [peerKey]: item[peerKey] ?? mirrorPartCode(item[key])}, index, peerKey, caption));
        }
      }
    }
    const source = item.source_info;
    if (source?.sheet && source.amount_cell) {
      details.append(element("p", "costing-source-note", `原表位置：${source.sheet}!${source.amount_cell}`));
    }
    if (source?.original_formula) {
      details.append(element("p", "costing-source-note", `原表公式：${source.original_formula}`));
    }
    const options = item.mode === "package" ? references.cartons : item.mode === "fixed" ? references.rates : null;
    if (editable && Array.isArray(options) && options.length) {
      const label = element("label", "", item.mode === "package" ? "选择纸箱参考资料" : "选择常用费用参考");
      const referenceSelect = element("select"); referenceSelect.dataset.costingReference = index;
      const placeholder = element("option", "", "选择后带入，可继续修改"); placeholder.value = ""; referenceSelect.append(placeholder);
      options.forEach((option, optionIndex) => { const entry = element("option", "", `${option.code || ""} ${option.name || ""} · ${option.unit_price ?? option.amount ?? "未填价格"}`); entry.value = String(optionIndex); referenceSelect.append(entry); });
      label.append(referenceSelect); details.append(label);
    }
    if (item.mode === "weight") {
      details.append(field(item, index, "processing_fee", "单件加工费（元）", {placeholder: "留空按 0"}));
      details.append(field(item, index, "quantity", "每件用量", {placeholder: "留空按 1"}));
    }
    if (item.mode === "package") {
      [["length", "纸箱长（cm）"], ["width", "纸箱宽（cm）"], ["height", "纸箱高（cm）"], ["strap_rate", "打包带单价（元 / m）"]].forEach(([key, label]) => details.append(field(item, index, key, label)));
    }
    [["part_code", "零件编码（可选）"], ["remark", "备注（可选）"]].forEach(([key, label]) => details.append(field(item, index, key, label, {multiline: key === "remark"})));
    if (item.review_note && editable) {
      const confirm = element("button", "linear-button subtle", "已核对本项");
      confirm.type = "button";
      confirm.dataset.costingReviewed = index;
      details.append(confirm);
    }
    cell.append(details);
    row.append(cell);
    return row;
  };
  tbody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-costing-details]");
    if (!button) return;
    const row = button.closest("[data-costing-row]");
    const index = Number(button.dataset.costingDetails);
    const open = button.getAttribute("aria-expanded") === "true";
    if (open) {
      row.nextElementSibling.remove();
      button.removeAttribute("aria-controls");
    } else {
      const details = renderDetails(record.items[index], index, button.dataset.issueText);
      row.after(details);
      button.setAttribute("aria-controls", details.id);
    }
    button.setAttribute("aria-expanded", String(!open));
    updateDetailsButton(button);
  });
  const renderItems = () => {
    tbody.replaceChildren(...record.items.map(renderRow));
    if (!record.items.length) {
      const row = element("tr");
      const cell = element("td", "costing-empty-row", "还没有成本明细。");
      cell.colSpan = columns.length;
      if (editable) {
        const serialCell = element("td");
        serialCell.dataset.col = "row";
        const controls = element("div", "costing-row-controls");
        const insert = rowButton("+", "添加第一行成本明细");
        insert.dataset.costingInsert = -1;
        controls.append(insert);
        serialCell.append(controls);
        row.append(serialCell);
        cell.colSpan -= 1;
      }
      row.append(cell);
      tbody.append(row);
    }
  };
  const refreshRows = (focusIndex) => {
    renderItems();
    scheduleCalculation();
    const focusTarget = tbody.querySelector(`[data-costing-row="${focusIndex}"] [data-item-field="name"]`)
      || tbody.querySelector("[data-costing-insert]");
    focusTarget?.focus();
  };
  const updateItem = (target) => {
    const index = Number(target.dataset.itemIndex);
    const key = target.dataset.itemField;
    if (!Number.isInteger(index) || !record.items[index] || !ITEM_FIELDS.includes(key)) return;
    const previous = record.items[index][key];
    record.items[index][key] = target.value;
    if (pairActive() && ["pair_kind", "name", "product_code", "part_code"].includes(key)) {
      const disclosure = tbody.querySelector(`[data-costing-details="${index}"]`);
      const kind = rowPairKind(record.items[index]);
      disclosure.dataset.relation = PAIR_CAPTIONS[kind];
      updateDetailsButton(disclosure);
      const expanded = document.getElementById(disclosure.getAttribute("aria-controls"));
      if (expanded && key !== "pair_kind") {
        const relation = expanded.querySelector('[data-item-field="pair_kind"]');
        if (!record.items[index].pair_kind && relation?.value !== kind) {
          expanded.replaceWith(renderDetails(record.items[index], index, disclosure.dataset.issueText));
        }
      }
    }
    if (key === "pair_kind") {
      const current = target.closest(".costing-detail-row");
      const newDetails = renderDetails(record.items[index], index, current.querySelector("[data-line-issues]").textContent);
      current.replaceWith(newDetails);
      newDetails.querySelector('[data-item-field="pair_kind"]').focus();
    }
    if (key === "material_attribute") savedOrSelectedAttributes.add(record.items[index]);
    if (key === "name") updateRemoveLabel(target.closest("[data-costing-row]"), index, target.value);
    if (key === "mode") {
      const row = target.closest("[data-costing-row]");
      if (row.nextElementSibling?.classList.contains("costing-detail-row")) row.nextElementSibling.remove();
      const replacement = renderRow(record.items[index], index);
      row.replaceWith(replacement);
      replacement.querySelector('[data-item-field="mode"]').focus();
    }
    if (isCostCalculationField(key)) scheduleCalculation();
    else {
      markEdited();
      if (key === "name" && Boolean(text(previous).trim()) !== Boolean(target.value.trim())) updateReview();
    }
  };
  const updateHeader = (key) => {
    if (isCostCalculationField(key)) scheduleCalculation();
    else {
      markEdited();
      if (key === "code") {
        updatePairCode();
        updateReview();
      }
    }
  };
  form.addEventListener("input", (event) => {
    if (!editable || saving) return;
    if (event.target.dataset.itemField && event.target.tagName !== "SELECT") updateItem(event.target);
    else if (COST_FIELDS.includes(event.target.name) && event.target.tagName !== "SELECT") updateHeader(event.target.name);
  });
  tbody.addEventListener("keydown", (event) => {
    if (!editable || saving || event.key !== "Enter" || event.isComposing || event.keyCode === 229 ||
        event.altKey || event.ctrlKey || event.metaKey) return;
    const control = event.target;
    const row = control.closest("tr");
    if (!control.matches("input[data-item-field], select[data-item-field]") || !row?.hasAttribute("data-costing-row")) return;
    event.preventDefault();
    const column = control.closest("td").dataset.col;
    const sibling = event.shiftKey ? "previousElementSibling" : "nextElementSibling";
    for (let next = row[sibling]; next; next = next[sibling]) {
      if (!next.hasAttribute("data-costing-row")) continue;
      const field = next.querySelector(`td[data-col="${column}"] [data-item-field]:enabled`);
      if (field) {
        field.focus();
        if (field instanceof HTMLInputElement) field.select();
        break;
      }
    }
  });
  createPair?.addEventListener("change", () => {
    updatePairCode();
    renderItems();
    markEdited();
  });
  form.addEventListener("change", (event) => {
    if (!editable || saving || event.target.tagName !== "SELECT") return;
    if (event.target.hasAttribute("data-costing-reference")) {
      const index = Number(event.target.dataset.costingReference);
      const item = record.items[index];
      if (event.target.value === "") return;
      const options = item.mode === "package" ? references.cartons : references.rates;
      const chosen = options?.[Number(event.target.value)];
      if (!chosen) return;
      event.target.value = "";
      const before = {...item};
      if (!applyCostReference(item, chosen, (message) => window.confirm(message), savedOrSelectedAttributes.has(item))) return;
      const row = tbody.querySelector(`[data-costing-row="${index}"]`);
      updateRemoveLabel(row, index, item.name);
      if (pairActive() && (before.pair_kind || rowPairKind(before)) !== rowPairKind(item)) {
        const disclosure = row.querySelector("[data-costing-details]");
        disclosure.dataset.relation = PAIR_CAPTIONS[rowPairKind(item)];
        updateDetailsButton(disclosure);
        row.nextElementSibling.replaceWith(renderDetails(item, index, disclosure.dataset.issueText));
      }
      for (const section of [row, row.nextElementSibling]) {
        section.querySelectorAll("input[data-item-field], select[data-item-field]").forEach((input) => {
          input.value = text(item[input.dataset.itemField]);
        });
      }
      if (Object.keys(item).some((key) => isCostCalculationField(key) && text(before[key]) !== text(item[key]))) {
        scheduleCalculation();
      } else {
        markEdited();
        updateReview();
      }
    } else if (event.target.dataset.itemField) updateItem(event.target);
    else if (COST_FIELDS.includes(event.target.name)) updateHeader(event.target.name);
  });
  form.addEventListener("click", (event) => {
    if (!editable || saving) return;
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-costing-insert")) {
      const index = Number(button.dataset.costingInsert) + 1;
      record.items.splice(index, 0, blankCostItem());
      if (deleted && index < deleted.index) deleted.index += 1;
      refreshRows(index);
    } else if (button.hasAttribute("data-costing-template")) {
      record.items.push(...createCostTemplate(references.templates[values().kind])); renderItems(); scheduleCalculation();
    } else if (button.hasAttribute("data-costing-remove")) {
      const index = Number(button.dataset.costingRemove);
      const item = record.items[index];
      if (record.pair && item.peer_item_id && !window.confirm(`移除“${item.name || "未命名项目"}”？保存成本后，${record.peer_code} 的对应行也会移除。`)) return;
      deleted = {index, item: record.items.splice(index, 1)[0]};
      form.querySelector("[data-costing-undo]").hidden = false;
      refreshRows(Math.min(index, record.items.length - 1));
    } else if (button.hasAttribute("data-costing-undo") && deleted) {
      const {index, item} = deleted;
      record.items.splice(index, 0, item);
      deleted = null;
      button.hidden = true;
      refreshRows(index);
    } else if (button.hasAttribute("data-costing-reviewed")) {
      const index = Number(button.dataset.costingReviewed);
      record.items[index].review_note = "";
      button.remove();
      markEdited();
      updateReview();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editable || saving) return;
    const destination = event.submitter?.hasAttribute("data-costing-leave-save") ? leaveTo : null;
    const body = payload();
    saving = true; gate.invalidate(); clearTimeout(timer); showError("");
    status.textContent = "正在保存…";
    const controls = [...form.querySelectorAll("input, select, textarea, button")].filter((control) => !control.disabled);
    controls.forEach((control) => { control.disabled = true; });
    try {
      const result = await post(form.action, body);
      dirty = false;
      reloadOnRestore = true;
      status.textContent = "已保存";
      if (destination) {
        window.location.assign(destination);
        return;
      }
      const url = new URL(result.url || `/costs/${encodeURIComponent(result.record.id)}`, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith("/costs/")) throw new Error("保存成功，请返回成本列表查看。");
      window.location.assign(url.href);
    } catch (error) {
      showError(error.message, true); status.textContent = "保存未完成";
    } finally {
      saving = false; controls.forEach((control) => { control.disabled = false; });
    }
  });
  window.addEventListener("beforeunload", (event) => { if (hasUnsavedChanges()) { event.preventDefault(); event.returnValue = ""; } });
  renderItems(); updateTotals(record);
};

if (typeof document !== "undefined" && document.body?.dataset.page === "costing.edit") {
  const form = document.querySelector("[data-costing-form]");
  if (form) initializeCostEditor(form);
}
