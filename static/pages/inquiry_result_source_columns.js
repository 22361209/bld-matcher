export function setupInquirySourceColumns(root, table) {
  const dialog = root.querySelector("#inquiry-source-columns");
  if (!dialog || !table) return;

  const openButton = root.querySelector("[data-open-source-columns]");
  openButton.addEventListener("click", () => dialog.showModal());
  const count = openButton.querySelector("[data-source-column-count]");
  const inputs = Array.from(dialog.querySelectorAll("[data-source-column]"));
  const storageKey = `bld.inquiry-result.hidden-source-columns.u${table.dataset.columnStorageScope}.${dialog.dataset.sourceFile}`;

  const updateGrid = () => {
    const hidden = inputs.filter(input => !input.checked).map(input => input.value);
    count.textContent = hidden.length ? `（${inputs.length - hidden.length}/${inputs.length}）` : "";
    table.closest("[data-resizable-grid]").dispatchEvent(new Event("bld:column-visibility"));
    return hidden;
  };

  const applyChoice = (input) => {
    for (const element of table.querySelectorAll(`[data-col="${CSS.escape(input.value)}"]`)) {
      element.hidden = !input.checked;
    }
  };

  let saved = [];
  try {
    saved = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
  } catch {
    // The original columns remain visible if storage is unavailable.
  }
  const hidden = new Set(Array.isArray(saved) ? saved : []);
  for (const input of inputs) {
    input.checked = !hidden.has(input.value);
    if (!input.checked) applyChoice(input);
  }
  updateGrid();

  dialog.addEventListener("change", (event) => {
    const input = event.target;
    if (!input.matches("[data-source-column]")) return;
    applyChoice(input);
    const hidden = updateGrid();
    try {
      if (hidden.length) sessionStorage.setItem(storageKey, JSON.stringify(hidden));
      else sessionStorage.removeItem(storageKey);
    } catch {
      // The current page still reflects the user's selection.
    }
  });
}
