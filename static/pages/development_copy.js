function copyRows(form) {
  return Array.from(form.querySelectorAll('[data-copy-row]'));
}

export function updateCopySelection(form) {
  const rows = copyRows(form);
  const selected = rows.filter((row) => row.querySelector('[data-copy-selected]')?.checked);
  const all = form.querySelector('[data-copy-select-all]');
  if (all) {
    all.checked = rows.length > 0 && selected.length === rows.length;
    all.indeterminate = selected.length > 0 && selected.length < rows.length;
  }
  form.querySelectorAll('[data-copy-set-mode]').forEach((button) => {
    button.disabled = selected.length === 0;
  });
  const status = form.querySelector('[data-copy-selection-status]');
  if (status) status.textContent = `已选 ${selected.length} 项 / 共 ${rows.length} 项`;
  return selected;
}

export function setSelectedCopyMode(form, mode) {
  if (!['shared', 'independent'].includes(mode)) return 0;
  const selected = updateCopySelection(form);
  selected.forEach((row) => {
    const select = row.querySelector('[data-copy-mode]');
    if (!select) return;
    select.value = mode;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const status = form.querySelector('[data-copy-status]');
  if (status) status.textContent = selected.length
    ? `已将选中的 ${selected.length} 项设为${mode === 'shared' ? '共用开发任务' : '独立开发'}；其余项未改变。`
    : '请先勾选需要设置的零件。';
  return selected.length;
}

export function initDevelopmentCopy(root = document) {
  if (root.body?.dataset.page !== 'development.detail') return;
  root.querySelectorAll('[data-development-copy]').forEach((form) => {
    if (form.dataset.copyInitialized === 'true') return;
    form.dataset.copyInitialized = 'true';
    updateCopySelection(form);
    form.addEventListener('change', (event) => {
      if (event.target.matches('[data-copy-select-all]')) {
        copyRows(form).forEach((row) => { row.querySelector('[data-copy-selected]').checked = event.target.checked; });
      }
      if (event.target.matches('[data-copy-selected], [data-copy-select-all]')) updateCopySelection(form);
    });
    form.addEventListener('click', (event) => {
      const button = event.target.closest('[data-copy-set-mode]');
      if (!button || !form.contains(button)) return;
      setSelectedCopyMode(form, button.dataset.copySetMode);
    });
    form.addEventListener('submit', (event) => {
      if (form.dataset.copyStale !== 'true') return;
      event.preventDefault();
      const status = form.querySelector('[data-copy-status]');
      if (status) status.textContent = '来源结构已更新，需重新打开核对表后再复制。';
    });
  });
  root.querySelectorAll('[data-development-copy-disclosure]').forEach((disclosure) => {
    if (disclosure.dataset.copyInitialized === 'true') return;
    disclosure.dataset.copyInitialized = 'true';
    const summary = disclosure.querySelector('summary');
    summary?.addEventListener('click', (event) => {
      if (event.defaultPrevented) return;
      if (disclosure.dataset.copyReady === 'true') {
        if (event.target.closest('[data-development-copy-open]')) {
          event.preventDefault();
          disclosure.open = !disclosure.open;
        }
        return;
      }
      const link = summary.querySelector('[data-development-copy-open]');
      if (event.target.closest('[data-development-copy-open]')) return;
      event.preventDefault();
      link?.click();
    });
  });
  if (root.body.dataset.developmentCopyEvents === 'true') return;
  root.body.dataset.developmentCopyEvents = 'true';
  window.addEventListener('development:grid-saved', () => {
    root.querySelectorAll('[data-development-copy]').forEach((form) => {
      form.dataset.copyStale = 'true';
      const refresh = form.querySelector('[data-copy-refresh]');
      if (refresh) refresh.hidden = false;
      const disclosure = form.closest('[data-development-copy-disclosure]');
      if (disclosure) disclosure.dataset.copyReady = 'false';
    });
  });
}
