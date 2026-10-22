import {dirtyRows, treeRows} from "./development_grid_state.js";

export const GRID_LABELS = {
  drawing_status: [["not_started", "未出图"], ["in_progress", "出图中"], ["ready", "已出图"], ["waived", "不需图"]],
  development_mode: [["new", "新开发"], ["borrowed", "借用"], ["selected", "现成选型"]],
  status: [["not_started", "未开始"], ["in_progress", "开发中"], ["completed", "已完成"]],
};
const FIELD_LABELS = {code: "产品编码", name: "产品名称", drawing_status: "图纸状态", development_mode: "借用状态", borrowed_code: "借用编码", status: "开发状态", "task.supplier_name": "责任单位", "task.planned_date": "目标日期"};
export function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}
function button(text, action, label = text) {
  const node = element("button", "linear-button subtle compact", text);
  node.type = "button"; node.dataset.gridAction = action; node.setAttribute("aria-label", label);
  return node;
}
function input(row, field, value, editable, type = "text") {
  const node = element("input", "development-grid-input");
  node.type = type; node.value = value ?? ""; node.dataset.gridField = field;
  node.dataset.rowKey = row.key; node.disabled = !editable;
  node.setAttribute("aria-label", `${row.name || row.code || "新零件"} · ${FIELD_LABELS[field] || field}`);
  if (type === "text") node.maxLength = 200;
  return node;
}
function select(row, field, value, options, editable) {
  const node = element("select", "development-grid-input");
  node.dataset.gridField = `task.${field}`; node.dataset.rowKey = row.key; node.disabled = !editable;
  node.setAttribute("aria-label", `${row.name || row.code || "新零件"} · ${FIELD_LABELS[field] || field}`);
  for (const [key, label] of options) { const option = element("option", "", label); option.value = key; node.append(option); }
  node.value = value;
  return node;
}

export function renderGrid(root, state, branchId, {selectedKey = null, checked = new Set(), editable = true} = {}) {
  const body = root.querySelector("[data-development-grid-body]");
  const dirty = new Set(dirtyRows(state).map((row) => row.key));
  const rows = treeRows(state, branchId);
  const branch = state.branches.find((item) => item.id === branchId);
  body.replaceChildren();
  if (branch) {
    const tr = element("tr", "development-grid-root"); tr.dataset.gridRoot = "";
    for (const text of [branch.code, branch.name || "完整成品", "", "", "", "", "", branch.planned_date]) tr.append(element("td", "", text || ""));
    body.append(tr);
  }
  for (const {row, depth, hidden, children} of rows) {
    const tr = element("tr", `${dirty.has(row.key) ? "development-grid-dirty " : ""}${row.key === selectedKey ? "development-grid-selected" : ""}`);
    tr.dataset.gridRow = row.key; tr.hidden = hidden;
    const cells = Array.from({length: 8}, () => element("td"));
    const code = element("div", "development-grid-code");
    code.style.paddingInlineStart = `${Math.min(depth, 20) * 14}px`;
    const check = input(row, "checked", "", editable, "checkbox"); check.checked = checked.has(row.key);
    delete check.dataset.gridField; check.dataset.gridCheck = row.key; check.setAttribute("aria-label", `选择 ${row.name || "新零件"}`);
    code.append(check);
    if (children) { const toggle = button(state.collapsed[row.key] ? "▸" : "▾", "toggle", `${state.collapsed[row.key] ? "展开" : "收起"} ${row.name}`); toggle.setAttribute("aria-expanded", String(!state.collapsed[row.key])); code.append(toggle); }
    else code.append(element("span", "development-grid-toggle-space"));
    code.append(input(row, "code", row.code, editable)); cells[0].append(code);
    const name = element("div", "development-grid-cell"); name.append(input(row, "name", row.name, editable));
    const count = row.task_id ? state.rows.filter((item) => item.task_id === row.task_id).length : 0;
    if (count > 1) { const tag = element("span", "status-badge", `共用 ${count}`); tag.title = "同一开发任务的状态与资料同步"; name.append(tag); }
    cells[1].append(name);
    const drawing = element("div", "development-grid-cell");
    drawing.append(select(row, "drawing_status", row.task.drawing_required === 0 ? "waived" : row.task.drawing_status || "not_started", GRID_LABELS.drawing_status, editable));
    if (root.dataset.filesAllowed === "true") drawing.append(button("文件", "files", `${row.name} 图纸与文件`));
    cells[2].append(drawing);
    cells[3].append(select(row, "development_mode", row.task.development_mode || "new", GRID_LABELS.development_mode, editable));
    const borrowing = element("div", "development-grid-cell"); borrowing.append(input(row, "borrowed_code", row.borrowed_code, editable));
    if (editable) borrowing.append(button("查找", "source", `${row.name} 查找借用来源`)); cells[4].append(borrowing);
    cells[5].append(select(row, "status", row.task.status || "not_started", GRID_LABELS.status, editable));
    const supplier = element("div", "development-grid-cell"); supplier.append(input(row, "task.supplier_name", row.task.supplier_name, editable));
    if (editable) supplier.append(button(row.task.create_supplier ? "待新建" : "选择", "supplier", `${row.name} 选择责任单位`)); cells[6].append(supplier);
    cells[7].append(input(row, "task.planned_date", row.task.planned_date, editable, "date"));
    tr.append(...cells); body.append(tr);
  }
  if (!rows.length) { const tr = element("tr"); const td = element("td", "empty-state", "直接添加同级行，或生成常用结构草稿后集中填写。产品编码由你录入。"); td.colSpan = 8; tr.append(td); body.append(tr); }
  root.querySelector("[data-grid-row-count]").textContent = `${rows.length} 个组件`;
}

export function refreshCells(root, state) {
  for (const control of root.querySelectorAll("[data-grid-field]")) {
    if (control === document.activeElement) continue;
    const row = state.rows.find((item) => item.key === control.dataset.rowKey);
    if (!row) continue;
    const field = control.dataset.gridField;
    let value = field.startsWith("task.") ? row.task[field.slice(5)] : row[field];
    if (field === "task.drawing_status" && row.task.drawing_required === 0) value = "waived";
    control.value = value ?? "";
  }
}
