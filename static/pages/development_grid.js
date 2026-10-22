import {addCommonStructure, addRow, commitSave, dirtyRows, getRow, prepareSave, removeDraftRow, restoreDraft, setCell} from "./development_grid_state.js";
import {GRID_LABELS, refreshCells, renderGrid} from "./development_grid_render.js";
import {createGridPicker} from "./development_grid_picker.js";

function adaptMobileGrid(root) {
  const table = root.querySelector(".development-tree-table");
  const columns = [...table.querySelectorAll("col")];
  const media = window.matchMedia("(max-width: 640px)");
  let desktopWidth = null;
  const setWidth = (width) => {
    columns[0].style.width = `${width}px`;
    const total = columns.reduce((sum, col) => sum + (parseFloat(col.style.width) || col.getBoundingClientRect().width), 0);
    table.style.width = `${total}px`;
    table.querySelector("th:first-child [role='separator']")?.setAttribute("aria-valuenow", String(width));
  };
  const adapt = () => {
    const width = parseFloat(columns[0].style.width) || columns[0].getBoundingClientRect().width;
    if (media.matches && width > 185) {
      desktopWidth ??= width;
      setWidth(185);
    } else if (!media.matches && desktopWidth !== null) {
      const restore = desktopWidth; desktopWidth = null; setWidth(restore);
    }
  };
  media.addEventListener("change", adapt);
  // Shared resizing writes inline column widths; retain a reachable frozen column on phones.
  new MutationObserver(adapt).observe(columns[0], {attributes: true, attributeFilter: ["style"]});
  adapt();
}

export function initDevelopmentGrid() {
  const root = document.querySelector("[data-development-grid]");
  if (!root) return;
  const detail = JSON.parse(root.dataset.initial);
  const branchId = root.dataset.branchId;
  const editable = root.dataset.editable === "true";
  const storageKey = `bld.development-grid.v1:${root.dataset.userId}:${detail.project.id}`;
  let stored = null;
  try { stored = JSON.parse(sessionStorage.getItem(storageKey) || "null"); } catch { stored = null; }
  let state;
  try { state = restoreDraft(detail, stored); } catch { state = restoreDraft(detail, null); }
  let selectedKey = new URL(location.href).searchParams.get("component") || null;
  const checked = new Set();
  let saving = false;
  let allowNavigation = false;
  let storageFailed = false;
  let formsStale = false;
  const status = root.querySelector("[data-grid-status]");
  const error = root.querySelector("[data-grid-error]");
  const inspector = root.querySelector("[data-grid-inspector]");
  const persist = () => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); storageFailed = false; }
    catch { storageFailed = true; }
  };
  const showError = (text = "") => { error.textContent = text; error.hidden = !text; };
  const selected = () => getRow(state, selectedKey);
  function updateStatus(message = "") {
    const changedRows = dirtyRows(state);
    const ownKeys = new Set(changedRows.filter((row) => row.branch_id === branchId).map((row) => row.key));
    const own = ownKeys.size;
    const total = changedRows.length;
    status.dataset.dirty = String(total > 0);
    status.textContent = message || (saving ? "正在保存本侧修改；可继续输入，新输入会保留为草稿。" : state.conflict ? "服务器版本已变化，草稿保留。请核对后重新载入，当前禁止覆盖保存。" : total ? `本侧 ${own} 行未保存${total > own ? `，其他分支另有 ${total - own} 行草稿` : ""}。` : "所有修改已保存。可继续录入。");
    if (storageFailed) status.textContent += " 浏览器无法暂存草稿，请在离开前保存。";
    root.querySelector("[data-grid-dirty-count]").textContent = total ? `${total} 行草稿` : "已保存";
    const row = selected();
    root.querySelector("[data-grid-selection-label]").textContent = row ? `已选：${row.code || row.name || "新零件"}` : "点击单元格选中行";
    const save = root.querySelector("[data-grid-action='save']");
    if (save) save.disabled = saving || state.conflict || !own;
    for (const tr of root.querySelectorAll("[data-grid-row]")) {
      tr.classList.toggle("development-grid-selected", tr.dataset.gridRow === selectedKey);
      tr.classList.toggle("development-grid-dirty", ownKeys.has(tr.dataset.gridRow));
    }
  }
  function updateInspector() {
    const row = selected();
    root.querySelector("[data-grid-inspector-name]").textContent = row ? `${row.code} ${row.name}` : "尚未选中";
    for (const control of root.querySelectorAll("[data-inspector-field]")) {
      const field = control.dataset.inspectorField;
      control.disabled = !editable || !row;
      if (control !== document.activeElement) control.value = row ? (field.startsWith("task.") ? row.task[field.slice(5)] : row[field]) ?? "" : "";
    }
  }
  function render(focusKey = null, field = "code") {
    renderGrid(root, state, branchId, {selectedKey, checked, editable});
    updateInspector(); updateStatus();
    if (focusKey) root.querySelector(`[data-row-key="${CSS.escape(focusKey)}"][data-grid-field="${field}"]`)?.focus();
  }
  function changed(message = "") { persist(); refreshCells(root, state); updateInspector(); updateStatus(message); }
  function applyField(key, field, value) {
    if (field === "task.drawing_status") {
      setCell(state, key, "task.drawing_required", value === "waived" ? 0 : 1);
      setCell(state, key, field, value === "waived" ? "not_started" : value);
      if (value === "waived" && !getRow(state, key)?.task.drawing_reason) inspector.open = true;
    } else if (field === "task.supplier_name") {
      setCell(state, key, "task.supplier_id", null); setCell(state, key, "task.create_supplier", false);
      setCell(state, key, field, value);
    } else setCell(state, key, field, value);
    if (field === "task.status" && value === "completed") inspector.open = true;
  }
  const picker = createGridPicker(root, {
    getState: () => state,
    onError: showError,
    onSupplier: (keys, supplier) => {
      for (const key of keys) {
        setCell(state, key, "task.supplier_id", supplier.id || null);
        setCell(state, key, "task.supplier_name", supplier.name || "");
        setCell(state, key, "task.create_supplier", Boolean(supplier.create));
      }
      persist(); render();
    },
    onSource: (key, {source, fields}) => {
      setCell(state, key, "borrowed_code", source.code);
      setCell(state, key, "borrowed_source_id", source.id);
      setCell(state, key, "task.development_mode", "borrowed");
      for (const field of ["name", "development_mode", "procurement_mode", "supplier_id", "material_category", "material_grade", "forming_process", "notes"]) {
        if (fields && field in fields) setCell(state, key, `task.${field}`, fields[field]);
      }
      if (fields && "supplier_id" in fields) {
        setCell(state, key, "task.supplier_name", state.suppliers.find((item) => item.id === fields.supplier_id)?.name || "");
        setCell(state, key, "task.create_supplier", false);
      }
      persist(); render();
    },
  });
  function detailLink(row, view = "components") {
    const url = new URL(root.dataset.detailUrl, location.origin);
    url.searchParams.set("view", view); url.searchParams.set("branch", branchId);
    url.searchParams.set("component", row.id); if (view === "components") url.searchParams.set("detail", "1");
    return url.href;
  }
  function leaveFor(row, view) {
    if (saving) { showError("保存尚未结束，请完成后再打开资料。"); return; }
    if (!row) { showError("先点击要操作的行。"); return; }
    if (!row.id || dirtyRows(state).length) { showError("请先保存各分支草稿，再打开该行文件或完整资料。未保存内容仍保留在表格中。"); return; }
    allowNavigation = true; location.assign(detailLink(row, view));
  }
  async function save() {
    if (!editable || saving || state.conflict) return;
    const snapshot = prepareSave(state, branchId);
    if (!snapshot.rows.length) { updateStatus("本侧没有未保存修改。"); return; }
    saving = true; showError(); updateStatus();
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 30000);
    try {
      const response = await fetch(`/development/${encodeURIComponent(detail.project.id)}/branches/${encodeURIComponent(branchId)}/grid/save`, {
        method: "POST", credentials: "same-origin", signal: abort.signal,
        headers: {"Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": root.querySelector("input[name='csrf_token']").value},
        body: JSON.stringify({expected_version: snapshot.expected_version, rows: snapshot.rows}),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        if (response.status === 409) state.conflict = true;
        showError(data.error || "保存失败，草稿已保留。");
        const match = /^rows\.(\d+)\.(?:task\.)?(.*)$/.exec(data.field || "");
        if (match) {
          const sent = snapshot.rows[Number(match[1])]; selectedKey = sent?.id || sent?.client_id || selectedKey;
          if (["owner_username", "actual_date", "drawing_reason"].includes(match[2])) inspector.open = true;
        }
        persist(); return;
      }
      const active = document.activeElement;
      const focusKey = active?.dataset?.rowKey;
      const focusField = active?.dataset?.gridField;
      const selection = active instanceof HTMLInputElement && ["text", "search"].includes(active.type) ? [active.selectionStart, active.selectionEnd] : null;
      commitSave(state, snapshot, data);
      selectedKey = data.client_ids[selectedKey] || selectedKey;
      const mappedChecks = [...checked].map((key) => data.client_ids[key] || key);
      checked.clear(); for (const key of mappedChecks) checked.add(key);
      formsStale = true;
      for (const form of document.querySelectorAll("[data-development-form]")) form.dataset.gridStale = "true";
      const staleNotice = document.querySelector("[data-grid-stale-forms]");
      if (staleNotice) staleNotice.hidden = false;
      for (const node of document.querySelectorAll("[data-development-summary]")) node.textContent = data.detail.summary[node.dataset.developmentSummary] ?? "0";
      const projectStatus = document.querySelector("[data-development-project-status]");
      if (projectStatus) projectStatus.textContent = ({not_started: "未开始", in_progress: "进行中", partial_completed: "部分完成", completed: "已完成"})[data.detail.project.status] || "进行中";
      persist(); render(data.client_ids[focusKey] || focusKey, focusField || "code");
      if (selection && document.activeElement instanceof HTMLInputElement) document.activeElement.setSelectionRange(...selection);
      window.dispatchEvent(new CustomEvent("development:grid-saved", {detail: data}));
    } catch {
      showError("连接或保存失败，输入与草稿已保留。请确认连接后重试；若版本已变化，系统会阻止覆盖。"); persist();
    } finally { clearTimeout(timeout); saving = false; updateInspector(); updateStatus(); }
  }
  function setBulkChoices() {
    const field = root.querySelector("[data-grid-bulk-field]")?.value;
    if (!field) return;
    const choice = root.querySelector("[data-grid-bulk-choice]");
    const date = root.querySelector("[data-grid-bulk-date]");
    choice.hidden = field === "task.planned_date" || field === "task.supplier_id";
    date.hidden = field !== "task.planned_date";
    choice.replaceChildren();
    for (const [value, label] of GRID_LABELS[field.slice(5)] || []) { const option = document.createElement("option"); option.value = value; option.textContent = label; choice.append(option); }
  }
  root.addEventListener("focusin", (event) => {
    const key = event.target.closest("[data-grid-row]")?.dataset.gridRow;
    if (key) { selectedKey = key; updateInspector(); updateStatus(); }
  });
  root.addEventListener("input", (event) => {
    const field = event.target.dataset.gridField || event.target.dataset.inspectorField;
    const key = event.target.dataset.rowKey || selectedKey;
    if (!editable || !field || !key) return;
    applyField(key, field, event.target.value); changed();
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-grid-bulk-field]")) setBulkChoices();
    if (event.target.dataset.gridCheck) {
      if (event.target.checked) checked.add(event.target.dataset.gridCheck); else checked.delete(event.target.dataset.gridCheck);
    }
  });
  root.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
    if (event.key !== "Tab" || event.isComposing || !event.target.matches("[data-grid-field]")) return;
    const cells = [...root.querySelectorAll("[data-development-grid-body] [data-grid-field]")].filter((node) => !node.disabled && !node.closest("tr").hidden);
    const next = cells[cells.indexOf(event.target) + (event.shiftKey ? -1 : 1)];
    if (next) { event.preventDefault(); next.focus(); }
  });
  root.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-grid-action]"); if (!trigger) return;
    const key = trigger.closest("[data-grid-row]")?.dataset.gridRow || selectedKey;
    if (key) selectedKey = key;
    const row = selected(); const action = trigger.dataset.gridAction;
    if (action === "save") { save(); return; }
    if (action === "toggle") { state.collapsed[key] = !state.collapsed[key]; persist(); render(); return; }
    if (action === "expand" || action === "collapse") { for (const item of state.rows.filter((item) => item.branch_id === branchId)) state.collapsed[item.key] = action === "collapse"; persist(); render(); return; }
    if (action === "inspector") { if (!row) showError("先点击需要修改的行。"); else { inspector.open = true; updateInspector(); inspector.scrollIntoView({block: "nearest"}); } return; }
    if (action === "files" || action === "full-detail") { leaveFor(row, action === "files" ? "documents" : "components"); return; }
    if (!editable) return;
    if (action === "source" && row) { picker.openSource(key, row.borrowed_code, trigger); return; }
    if (action === "supplier" && row) { picker.openSupplier([key], row.task.supplier_name, trigger); return; }
    if (action === "sibling" || action === "child") {
      if (action === "child" && !row) { showError("先点击父组件，再加子件。"); return; }
      const added = addRow(state, branchId, {parentKey: action === "child" ? key : row?.parent_key || null, afterKey: key});
      selectedKey = added.key; persist(); render(added.key); return;
    }
    if (action === "structure") { const added = addCommonStructure(state, branchId); selectedKey = added[0].key; persist(); render(selectedKey); return; }
    if (action === "remove-draft") {
      if (saving) { showError("保存请求已发出，完成前不能移除草稿行。"); return; }
      if (!row || !removeDraftRow(state, key)) showError("只能移除尚未保存的草稿行；已有组件历史保留。");
      else { selectedKey = null; persist(); render(); } return;
    }
    if (action === "select-all" || action === "clear-selection") { checked.clear(); if (action === "select-all") for (const item of state.rows.filter((item) => item.branch_id === branchId)) checked.add(item.key); render(); return; }
    if (action === "bulk") {
      if (!checked.size) { showError("请先勾选要赋值的行。"); return; }
      const field = root.querySelector("[data-grid-bulk-field]").value;
      if (field === "task.supplier_id") { picker.openSupplier([...checked], "", trigger); return; }
      const value = root.querySelector(field === "task.planned_date" ? "[data-grid-bulk-date]" : "[data-grid-bulk-choice]").value;
      for (const checkedKey of checked) applyField(checkedKey, field, value);
      persist(); render();
    }
  });
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]"); if (!link || event.ctrlKey || event.metaKey) return;
    if (link.matches("[data-grid-branch-link]")) {
      persist();
      if (saving || storageFailed) { event.preventDefault(); showError("请完成当前保存后再切换分支。"); return; }
      allowNavigation = true; return;
    }
    if (link.matches("[data-development-copy-open]") && dirtyRows(state).length) { event.preventDefault(); showError("复制前请先保存各分支的主表草稿。"); }
  });
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    if (event.target.matches("[data-grid-discard-form]")) {
      event.preventDefault(); if (saving) { showError("保存请求尚未结束，请稍后再重新载入。"); return; }
      try { sessionStorage.removeItem(storageKey); } catch { /* Explicit discard can still reload memory state. */ }
      allowNavigation = true; location.reload(); return;
    }
    if (event.target.matches("[data-development-form], [data-development-copy]")) {
      if (saving || dirtyRows(state).length) {
        event.preventDefault(); showError("请先完成保存各分支主表草稿，再提交其他资料或复制。");
      } else if (formsStale) {
        event.preventDefault(); showError("主表已经保存，旧详情或复制表尚未刷新。请重新打开资料后再提交，避免覆盖刚保存的值。");
      }
    }
  }, false);
  window.addEventListener("beforeunload", (event) => { if (!allowNavigation && (saving || dirtyRows(state).length)) { persist(); event.preventDefault(); event.returnValue = ""; } });
  window.addEventListener("pageshow", () => { allowNavigation = false; });
  setBulkChoices(); render(); adaptMobileGrid(root);
  if (state.conflict) showError("已恢复未保存草稿，但服务器版本已变化。草稿未被覆盖；请先核对当前资料，再明确丢弃草稿重新载入。");
}
