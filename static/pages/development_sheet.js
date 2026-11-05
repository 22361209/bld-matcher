import {rowActionButton} from "../components/row_action_button.js";

const field = (row, key) => row.querySelector(`[data-field="${key}"]`);

function applyMode(row) {
  const mode = field(row, "mode").value;
  const borrowed = field(row, "borrowed_code");
  borrowed.disabled = mode !== "borrowed";
  borrowed.required = !borrowed.disabled;
  for (const key of ["developer", "start_date", "due_date"]) field(row, key).disabled = mode !== "new";
  field(row, "due_date").min = field(row, "start_date").value;
}

function navigateCells(event) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229 ||
      event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
      !["Tab", "Enter", "ArrowLeft", "ArrowUp"].includes(event.key)) return;
  const control = event.target;
  if (!control.matches("input[data-field]:enabled, select[data-field]:enabled") || control.readOnly) return;
  const row = control.closest("[data-sheet-row]");
  const enabled = "input[data-field]:enabled:not([readonly]), select[data-field]:enabled";
  let next;
  if (event.key === "Tab" || event.key === "ArrowLeft") {
    const controls = [...row.querySelectorAll(enabled)];
    next = controls[controls.indexOf(control) + (event.key === "Tab" ? 1 : -1)];
    if (!next && event.key === "Tab") {
      next = row.nextElementSibling?.querySelector(enabled) || row.closest("[data-development-sheet]").querySelector("[name=notes]");
      if (!next) return;
    }
  } else {
    const sibling = event.key === "Enter" ? "nextElementSibling" : "previousElementSibling";
    for (let candidate = row[sibling]; candidate; candidate = candidate[sibling]) {
      const sameColumn = field(candidate, control.dataset.field);
      if (sameColumn?.matches(enabled)) {
        next = sameColumn;
        break;
      }
    }
  }
  event.preventDefault();
  next?.focus();
}

function initSheet(form) {
  form.querySelector("[data-development-errors]")?.focus();
  if (form.dataset.editable !== "true") return;
  const body = form.querySelector("[data-sheet-body]");
  const template = form.querySelector("[data-sheet-row-template]");
  const undo = form.querySelector("[data-sheet-undo]");
  const save = form.querySelector("[data-sheet-save]");
  const feedback = form.querySelector("[data-sheet-feedback]");
  const maxRows = Number(form.dataset.maxRows);
  body.addEventListener("keydown", navigateCells);
  let removed = null;
  let dirty = false;

  function numberRows() {
    Array.from(body.rows).forEach((row, index) => {
      const number = index + 1;
      const numberCell = row.querySelector("[data-sheet-number]");
      numberCell.textContent = String(number);
      if (!numberCell.parentElement.querySelector("button")) {
        const add = rowActionButton("+", "");
        add.dataset.sheetInsert = "";
        const remove = rowActionButton("−", "");
        remove.dataset.sheetRemove = "";
        numberCell.after(add, remove);
      }
      const name = field(row, "name").value.trim() || "未命名零件";
      const add = row.querySelector("[data-sheet-insert]");
      add.title = `在第 ${number} 行「${name}」下方插入零件`;
      add.setAttribute("aria-label", add.title);
      const remove = row.querySelector("[data-sheet-remove]");
      remove.title = `移除第 ${number} 行「${name}」`;
      remove.setAttribute("aria-label", remove.title);
    });
    form.querySelector("[data-sheet-empty]").hidden = body.rows.length > 0;
  }

  function insertAfter(source) {
    const row = template.content.firstElementChild.cloneNode(true);
    applyMode(row);
    if (source) source.after(row);
    else body.append(row);
    numberRows();
    return row;
  }

  function changed() {
    dirty = true;
    feedback.textContent = "有未保存的修改";
  }

  form.addEventListener("input", (event) => {
    changed();
    const input = event.target;
    if (input.dataset.field === "start_date") {
      field(input.closest("[data-sheet-row]"), "due_date").min = input.value;
    }
  });

  form.addEventListener("change", (event) => {
    const input = event.target;
    const row = input.closest("[data-sheet-row]");
    if (!row) return;
    if (input.dataset.field === "mode") applyMode(row);
    if (input.dataset.field === "name") {
      const material = field(row, "material");
      if (["主体", "臂体"].includes(input.value.trim())) material.setAttribute("list", "development-body-materials");
      else material.removeAttribute("list");
      numberRows();
    }
  });

  form.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const row = button.closest("[data-sheet-row]");
    if (button.hasAttribute("data-sheet-insert") || button.hasAttribute("data-sheet-first")) {
      if (body.rows.length >= maxRows) {
        feedback.textContent = `每张开发表最多登记 ${maxRows} 行零件。`;
        return;
      }
      changed();
      field(insertAfter(row), "name").focus();
    } else if (button.hasAttribute("data-sheet-remove")) {
      changed();
      const name = field(row, "name").value || "未命名零件";
      removed = {row, next: row.nextElementSibling, index: row.sectionRowIndex};
      row.remove();
      numberRows();
      undo.hidden = false;
      undo.focus();
      feedback.textContent = `已移除「${name}」，保存前可撤销。`;
    } else if (button === undo && removed) {
      const {row: restored, next, index} = removed;
      body.insertBefore(restored, next?.parentElement === body ? next : body.rows[index] || null);
      removed = null;
      undo.hidden = true;
      numberRows();
      field(restored, "name").focus();
      changed();
    }
  });

  form.addEventListener("submit", (event) => {
    if (save.disabled) { event.preventDefault(); return; }
    const rows = Array.from(body.rows, row => Object.fromEntries(
      Array.from(row.querySelectorAll("[data-field]"), control => [
        control.dataset.field, control.disabled ? "" : control.value.trim(),
      ]),
    ));
    const encoded = JSON.stringify(rows);
    if (rows.length > maxRows || [...encoded].length > Number(form.dataset.maxRowsChars)) {
      event.preventDefault();
      feedback.textContent = rows.length > maxRows ? `请将零件缩减到 ${maxRows} 行以内再保存。` : "明细过长，请缩减填写内容后再保存。";
      return;
    }
    form.elements.rows.value = encoded;
    dirty = false;
    save.disabled = true;
    form.setAttribute("aria-busy", "true");
    feedback.textContent = "正在保存…";
  });

  window.addEventListener("beforeunload", (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pageshow", () => {
    if (form.hasAttribute("aria-busy")) {
      form.removeAttribute("aria-busy");
      save.disabled = false;
      feedback.textContent = "";
    }
  });
  Array.from(body.rows).forEach(applyMode);
  numberRows();
  save.disabled = false;
}

if (document.body.dataset.page === "development.sheet") {
  initSheet(document.querySelector("[data-development-sheet]"));
}
