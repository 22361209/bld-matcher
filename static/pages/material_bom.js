import {setupDataGridControls} from "../components/data_grid_controls.js?v=20261105-1";
import {setupAssemblyEditor} from "./material_bom_assembly.js";

export function bomTreeVisibility(rows, expanded) {
  const hidden = new Map();
  for (const row of rows) {
    hidden.set(row.key, Boolean(row.parent && (hidden.get(row.parent) || !expanded.has(row.parent))));
  }
  return hidden;
}

const page = typeof document === 'undefined' ? null : document.querySelector('body[data-page="materials.bom"]');
if (page) {
  setupAssemblyEditor(page);
  const library = page.querySelector('.bom-library-table');
  if (library) {
    const columns = Array.from(library.tHead.rows[0].cells, heading => heading.dataset.col);
    setupDataGridControls(library, {
      columns,
      storagePrefix: 'bld.material-bom.library',
      resultsHash: 'material-bom-library',
      filterPortal: library.closest('[data-resizable-grid]'),
      resetFilterParams: columns.map(column => `cf_${column}`),
    });
  }

  const kind = page.querySelector('[data-bom-kind]');
  const fields = page.querySelectorAll('[data-bom-stamp]');
  const codeInput = page.querySelector('[data-bom-code]');
  const codeLabel = page.querySelector('[data-bom-code-label]');
  const tubeReference = page.querySelector('[data-bom-tube-reference]');
  function showFields() {
    const isTube = ['焊接管', '焊接座管', '轴类'].includes(kind.value);
    for (const field of fields) field.hidden = kind.value !== '冲压件';
    if (codeInput && !codeInput.readOnly) {
      if (isTube) codeInput.setAttribute('list', 'bom-tube-codes');
      else codeInput.removeAttribute('list');
    }
    if (codeLabel && isTube) codeLabel.textContent = '管件资料编码';
    if (tubeReference) tubeReference.hidden = !isTube;
  }
  if (kind) {
    kind.addEventListener('change', showFields);
    showFields();
  }

  const select = page.querySelector('[data-bom-options]');
  if (select) {
    const options = Array.from(select.options);
    const query = page.querySelector('[data-bom-find]');
    const filter = page.querySelector('[data-bom-filter-kind]');
    const tubeRole = page.querySelector('[data-bom-tube-role]');
    function showTubeRole() {
      const option = select.selectedOptions[0];
      tubeRole.hidden = option?.dataset.kind !== '管件资料';
      const role = tubeRole.querySelector('select');
      role.disabled = tubeRole.hidden;
      if (!tubeRole.hidden && option.dataset.tubeKind) role.value = option.dataset.tubeKind;
    }
    select.addEventListener('change', showTubeRole);
    showTubeRole();
    function refresh() {
      select.replaceChildren(...options.filter(option =>
        (!filter.value || filter.value === option.dataset.kind) &&
        option.textContent.toLowerCase().includes(query.value.toLowerCase().trim())
      ));
      showTubeRole();
    }
    query.addEventListener('input', refresh);
    filter.addEventListener('change', refresh);
  }

  const rows = Array.from(page.querySelectorAll('[data-bom-row]'));
  const toggles = Array.from(page.querySelectorAll('[data-bom-toggle]'));
  const expandAll = page.querySelector('[data-bom-expand-all]');
  if (rows.length && toggles.length) {
    const open = new Set(toggles.filter(button => button.getAttribute('aria-expanded') === 'true').map(button => button.dataset.bomToggle));
    function refreshTree() {
      const visibility = bomTreeVisibility(rows.map(row => ({key: row.dataset.bomKey, parent: row.dataset.bomParent})), open);
      for (const row of rows) row.hidden = visibility.get(row.dataset.bomKey);
      for (const button of toggles) {
        const expanded = open.has(button.dataset.bomToggle);
        button.setAttribute('aria-expanded', String(expanded));
        button.textContent = expanded ? '−' : '+';
      }
      if (expandAll) expandAll.textContent = open.size === toggles.length ? '全部收起' : '全部展开';
    }
    page.addEventListener('click', event => {
      const button = event.target.closest('[data-bom-toggle]');
      if (!button || !page.contains(button)) return;
      const key = button.dataset.bomToggle;
      if (open.has(key)) open.delete(key);
      else open.add(key);
      refreshTree();
    });
    expandAll?.addEventListener('click', () => {
      const collapse = open.size === toggles.length;
      open.clear();
      if (!collapse) {
        for (const button of toggles) open.add(button.dataset.bomToggle);
      }
      refreshTree();
    });
    refreshTree();
  } else if (expandAll) {
    expandAll.hidden = true;
  }
}
