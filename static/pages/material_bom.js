const page = document.querySelector('body[data-page="materials.bom"]');
if (page) {
  const kind = page.querySelector('[data-bom-kind]');
  const fields = page.querySelectorAll('[data-bom-stamp]');
  function showFields() {
    for (const field of fields) field.hidden = kind.value !== '冲压件';
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
      tubeRole.hidden = select.selectedOptions[0]?.dataset.kind !== '管件资料';
      tubeRole.querySelector('select').disabled = tubeRole.hidden;
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
}
