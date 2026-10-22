const COMPONENT_FIELDS = ["code", "name", "borrowed_code", "borrowed_source_id", "quantity", "position", "notes", "sort_order"];
const TASK_FIELDS = ["name", "drawing_status", "drawing_required", "drawing_reason", "development_mode", "procurement_mode", "status", "supplier_id", "supplier_name", "create_supplier", "owner_username", "planned_date", "actual_date", "material_category", "material_grade", "forming_process", "notes"];
const clone = (value) => JSON.parse(JSON.stringify(value));
const equal = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

function serverRows(detail) {
  const tasks = new Map(detail.tasks.map((task) => [task.id, task]));
  const suppliers = new Map((detail.suppliers || []).map((supplier) => [supplier.id, supplier.name]));
  return detail.components.filter((item) => !item.archived).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0)).map((item) => {
    const task = tasks.get(item.task_id) || item.task || {};
    return {key: item.id, id: item.id, branch_id: item.branch_id, parent_key: item.parent_id || null,
      task_id: item.task_id, ...Object.fromEntries(COMPONENT_FIELDS.map((key) => [key, item[key] ?? (key === "quantity" ? 1 : key === "sort_order" ? 0 : "")])),
      task: {...Object.fromEntries(TASK_FIELDS.filter((key) => key in task).map((key) => [key, task[key]])),
        supplier_name: suppliers.get(task.supplier_id) || "", create_supplier: false}};
  });
}

export function createGridState(detail) {
  const rows = serverRows(detail);
  return {version: detail.project.version, project: clone(detail.project), branches: clone(detail.branches),
    suppliers: clone(detail.suppliers || []), rows, baseline: clone(rows), collapsed: {}, conflict: false};
}

export function getRow(state, key) { return state.rows.find((row) => row.key === key); }

export function setCell(state, key, field, value) {
  const row = getRow(state, key);
  if (!row) return;
  if (field.startsWith("task.")) {
    const name = field.slice(5);
    if (!TASK_FIELDS.includes(name)) return;
    for (const related of state.rows.filter((item) => item.key === key || (row.task_id && item.task_id === row.task_id))) related.task[name] = value;
  } else if (COMPONENT_FIELDS.includes(field) || field === "parent_key") {
    row[field] = value;
    if (field === "borrowed_code") row.borrowed_source_id = null;
  }
}

export function addRow(state, branchId, {parentKey = null, afterKey = null, name = "", clientId = null} = {}) {
  const branch = state.branches.find((item) => item.id === branchId) || {};
  const key = clientId || `draft-${globalThis.crypto.randomUUID()}`;
  const row = {key, client_id: key, branch_id: branchId, parent_key: parentKey, task_id: null,
    code: "", name, borrowed_code: "", borrowed_source_id: null, quantity: 1, position: "", notes: "", sort_order: state.rows.length,
    task: {drawing_status: "not_started", drawing_required: 1, drawing_reason: "", development_mode: "new",
      procurement_mode: "made", status: "not_started", supplier_id: null, supplier_name: "", create_supplier: false,
      owner_username: branch.owner_username || state.project.owner_username || "",
      planned_date: branch.planned_date || state.project.planned_date || "", actual_date: ""}};
  const index = state.rows.findIndex((item) => item.key === afterKey);
  if (index >= 0) state.rows.splice(index + 1, 0, row); else state.rows.push(row);
  state.rows.filter((item) => item.branch_id === branchId).forEach((item, position) => { item.sort_order = position; });
  if (parentKey) delete state.collapsed[parentKey];
  return row;
}

export function addCommonStructure(state, branchId) {
  const rows = ["焊接组件", "大衬套", "小衬套", "悬挂球头"].map((name) => addRow(state, branchId, {name}));
  for (const name of ["球壳", "球销", "防尘罩"]) rows.push(addRow(state, branchId, {parentKey: rows[3].key, name}));
  return rows;
}

export function removeDraftRow(state, key) {
  const row = getRow(state, key);
  if (!row || row.id) return false;
  const remove = new Set([key]);
  let previous = 0;
  while (remove.size !== previous) {
    previous = remove.size;
    for (const item of state.rows) if (!item.id && remove.has(item.parent_key)) remove.add(item.key);
  }
  state.rows = state.rows.filter((item) => !remove.has(item.key));
  return true;
}

export function dirtyRows(state, branchId = null) {
  return state.rows.filter((row) => (!branchId || row.branch_id === branchId) && (!row.id || !equal(row, state.baseline.find((base) => base.key === row.key))));
}

export function treeRows(state, branchId) {
  const rows = state.rows.filter((row) => row.branch_id === branchId);
  const result = [];
  const visited = new Set();
  function visit(parent, depth, hidden) {
    for (const row of rows.filter((item) => (item.parent_key || null) === parent)) {
      if (visited.has(row.key)) continue;
      visited.add(row.key);
      const children = rows.some((item) => item.parent_key === row.key);
      result.push({row, depth, hidden, children});
      visit(row.key, depth + 1, hidden || Boolean(state.collapsed[row.key]));
    }
  }
  visit(null, 0, false);
  return result;
}

export function prepareSave(state, branchId) {
  const rows = dirtyRows(state, branchId).map((row) => {
    const base = state.baseline.find((item) => item.key === row.key);
    const payload = row.id ? {id: row.id} : {client_id: row.client_id};
    for (const field of COMPONENT_FIELDS) if (!base || !equal(row[field], base[field])) payload[field] = row[field];
    if (!base || !equal(row.parent_key, base.parent_key)) {
      const parent = getRow(state, row.parent_key);
      if (parent && !parent.id) payload.parent_client_id = parent.client_id;
      else payload.parent_id = row.parent_key || null;
    }
    const task = {};
    for (const field of TASK_FIELDS) if (field in row.task && (!base || !equal(row.task[field], base.task[field]))) task[field] = row.task[field];
    if (Object.keys(task).length) payload.task = task;
    return payload;
  });
  return {branch_id: branchId, expected_version: state.version, rows: clone(rows), before: clone(state.rows)};
}

export function commitSave(state, snapshot, result) {
  const detail = result.detail;
  const idMap = result.client_ids || {};
  const translate = (key) => idMap[key] || key;
  const fresh = createGridState(detail);
  const taskAcks = new Map();
  for (const sent of snapshot.rows) {
    const before = snapshot.before.find((row) => row.key === (sent.id || sent.client_id));
    if (before?.task_id) taskAcks.set(before.task_id, {...taskAcks.get(before.task_id), ...(sent.task || {})});
  }
  for (const local of state.rows) {
    const server = getRow(fresh, translate(local.key));
    if (!server) { if (!local.id) fresh.rows.push({...clone(local), parent_key: translate(local.parent_key)}); continue; }
    const base = state.baseline.find((row) => row.key === local.key);
    const atRequest = snapshot.before.find((row) => row.key === local.key);
    const sent = snapshot.rows.find((row) => (row.id || row.client_id) === local.key) || {};
    for (const field of COMPONENT_FIELDS) {
      if ((!base || !equal(local[field], base[field]) || (atRequest && !equal(local[field], atRequest[field]))) && !(field in sent && equal(local[field], sent[field]))) server[field] = clone(local[field] ?? null);
    }
    const parentSent = "parent_id" in sent || "parent_client_id" in sent;
    const sentParent = sent.parent_id || sent.parent_client_id || null;
    if ((!base || !equal(local.parent_key, base.parent_key) || (atRequest && !equal(local.parent_key, atRequest.parent_key))) && !(parentSent && equal(local.parent_key, sentParent))) server.parent_key = translate(local.parent_key);
    const ack = local.task_id ? taskAcks.get(local.task_id) || {} : sent.task || {};
    for (const field of TASK_FIELDS) {
      if (field in local.task && (!base || !equal(local.task[field], base.task[field]) || (atRequest && !equal(local.task[field], atRequest.task[field]))) && !(field in ack && equal(local.task[field], ack[field]))) server.task[field] = clone(local.task[field]);
    }
  }
  fresh.collapsed = Object.fromEntries(Object.entries(state.collapsed).map(([key, value]) => [translate(key), value]));
  const supplierMap = new Map([...state.suppliers, ...fresh.suppliers].map((supplier) => [supplier.id, supplier]));
  fresh.suppliers = [...supplierMap.values()];
  Object.assign(state, fresh);
  return state;
}

export function restoreDraft(detail, stored) {
  const fresh = createGridState(detail);
  if (!stored || stored.project?.id !== detail.project.id || !Array.isArray(stored.rows) || stored.rows.length > 2000) return fresh;
  if (!dirtyRows(stored).length) { fresh.collapsed = stored.collapsed || {}; return fresh; }
  stored.conflict = stored.version !== detail.project.version;
  return stored;
}
