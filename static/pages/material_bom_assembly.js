export function bomPartCatalog(options) {
  return new Map(Array.from(options, option => [option.value.trim().toLocaleLowerCase(), option]));
}

export function setupAssemblyEditor(page) {
  const form = page.querySelector('[data-bom-assembly-form]');
  const rows = page.querySelector('[data-bom-child-rows]');
  const template = page.querySelector('[data-bom-child-template]');
  const dialog = page.querySelector('[data-bom-child-dialog]');
  if (!form || !rows || !template || !dialog) return;

  const catalog = bomPartCatalog(page.querySelectorAll('#bom-child-codes option'));
  const dialogFields = Object.fromEntries(Array.from(dialog.querySelectorAll('[data-bom-new-field]'), field => [field.dataset.bomNewField, field]));
  const hiddenFields = {
    name: 'bomNewName', kind: 'bomNewKind', spec: 'bomNewSpec', pieces: 'bomNewPieces', netWeight: 'bomNewNetWeight',
  };
  let activeRow = null;

  const field = (row, key) => row.querySelector(`[data-${hiddenFields[key].replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())}]`);
  const setNewValue = (row, key, value) => { field(row, key).value = value; };
  const clearNew = row => Object.keys(hiddenFields).forEach(key => setNewValue(row, key, ''));
  const renumber = () => Array.from(rows.querySelectorAll('[data-bom-child-row]'), (row, index) => {
    row.querySelector('[data-bom-row-number]').textContent = String(index + 1);
  });
  const showDialogFields = () => {
    const stamp = dialogFields.kind.value === '冲压件';
    dialog.querySelectorAll('[data-bom-new-stamp]').forEach(label => { label.hidden = !stamp; });
  };
  const openNew = row => {
    const code = row.querySelector('[data-bom-child-code]').value.trim();
    if (!code) return;
    activeRow = row;
    Object.values(dialogFields).forEach(input => { input.value = ''; });
    dialogFields.code.value = code;
    dialogFields.kind.value = '冲压件';
    dialog.querySelector('[data-bom-child-error]').textContent = '';
    showDialogFields();
    dialog.showModal();
    dialogFields.name.focus();
  };
  const applyCode = row => {
    const input = row.querySelector('[data-bom-child-code]');
    const code = input.value.trim();
    input.value = code;
    clearNew(row);
    row.querySelector('[data-bom-child-name]').value = '';
    row.querySelector('[data-bom-child-kind]').value = '';
    row.querySelector('[data-bom-new-child]').hidden = !code;
    if (!code) return;
    const option = catalog.get(code.toLocaleLowerCase());
    if (!option) {
      openNew(row);
      return;
    }
    row.querySelector('[data-bom-child-name]').value = option.dataset.name || '';
    row.querySelector('[data-bom-child-kind]').value = option.dataset.kind || '';
    row.querySelector('[data-bom-new-child]').hidden = true;
    if (option.dataset.reference === '1') {
      setNewValue(row, 'name', option.dataset.name || '');
      setNewValue(row, 'kind', option.dataset.kind || '焊接管');
    }
  };

  rows.addEventListener('input', event => {
    const input = event.target.closest('[data-bom-child-code]');
    if (!input) return;
    const row = input.closest('[data-bom-child-row]');
    row.querySelector('[data-bom-child-name]').value = '';
    row.querySelector('[data-bom-child-kind]').value = '';
    row.querySelector('[data-bom-new-child]').hidden = !input.value.trim();
    clearNew(row);
  });
  rows.addEventListener('change', event => {
    const input = event.target.closest('[data-bom-child-code]');
    if (input) applyCode(input.closest('[data-bom-child-row]'));
  });
  rows.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const row = button.closest('[data-bom-child-row]');
    if (button.hasAttribute('data-bom-new-child')) openNew(row);
    if (button.hasAttribute('data-bom-add-row')) {
      const next = template.content.firstElementChild.cloneNode(true);
      row.after(next);
      renumber();
      next.querySelector('[data-bom-child-code]').focus();
    }
    if (button.hasAttribute('data-bom-remove-row')) {
      if (rows.querySelectorAll('[data-bom-child-row]').length > 1) row.remove();
      else {
        row.querySelectorAll('input').forEach(input => { input.value = input.name === 'child_qty' ? '1' : ''; });
        row.querySelector('[data-bom-new-child]').hidden = true;
      }
      renumber();
    }
  });
  dialogFields.kind.addEventListener('change', showDialogFields);
  dialog.querySelectorAll('[data-bom-close-child]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.querySelector('[data-bom-confirm-child]').addEventListener('click', () => {
    if (!activeRow) return;
    const code = dialogFields.code.value.trim();
    const name = dialogFields.name.value.trim();
    const kind = dialogFields.kind.value;
    const error = dialog.querySelector('[data-bom-child-error]');
    if (!name) {
      error.textContent = '请填写子件名称。';
      dialogFields.name.focus();
      return;
    }
    if (kind === '冲压件' && (!dialogFields.spec.value.trim() || !dialogFields.pieces.value)) {
      error.textContent = '冲压件需要板料规格和下料只数。';
      return;
    }
    if (['焊接管', '焊接座管', '轴类'].includes(kind) && catalog.get(code.toLocaleLowerCase())?.dataset.reference !== '1') {
      error.textContent = '管件必须先在管件资料页登记编码，再回这里引用。';
      return;
    }
    activeRow.querySelector('[data-bom-child-code]').value = code;
    activeRow.querySelector('[data-bom-child-name]').value = name;
    activeRow.querySelector('[data-bom-child-kind]').value = kind;
    setNewValue(activeRow, 'name', name);
    setNewValue(activeRow, 'kind', kind);
    setNewValue(activeRow, 'spec', dialogFields.spec.value.trim());
    setNewValue(activeRow, 'pieces', dialogFields.pieces.value);
    setNewValue(activeRow, 'netWeight', dialogFields.net_weight.value);
    activeRow.querySelector('[data-bom-new-child]').hidden = true;
    dialog.close();
    activeRow.querySelector('[data-bom-child-qty]').focus();
  });
  renumber();
}
