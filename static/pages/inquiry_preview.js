import { setupPreviewScrolling } from "./inquiry_preview_rows.js";

const form = document.querySelector("[data-inquiry-preview]");
if (form) {
  const sheetChoice = form.querySelector("[data-preview-sheet]");
  const inputs = [...form.querySelectorAll('input[name="match_columns"]')];
  const initialColumns = new Set(inputs.filter(input => input.defaultChecked).map(input => Number(input.value)));
  const selected = form.querySelector("[data-selected-columns]");
  const submit = form.querySelector("[data-start-match]");
  const headerUpdates = [];
  let columnsChanged = false;
  let hasColumns = false;

  const updateSelected = () => {
    const choices = new Map(inputs.filter(input => input.checked).map(input => [Number(input.value), input.dataset.letter]));
    selected.replaceChildren();
    for (const [, letter] of [...choices].sort(([a], [b]) => a - b)) {
      const item = document.createElement("span");
      item.textContent = `${letter} 列`;
      selected.appendChild(item);
    }
    if (!choices.size) selected.textContent = "尚未选择";
    submit.disabled = !choices.size;
    hasColumns = choices.size > 0;
    columnsChanged = choices.size !== initialColumns.size || [...choices.keys()].some(key => !initialColumns.has(key));
    headerUpdates.forEach(update => update());
  };

  sheetChoice?.addEventListener("change", () => {
    for (const part of form.querySelectorAll("[data-sheet-part]")) {
      part.hidden = part.dataset.sheetPart !== sheetChoice.value;
    }
  });

  form.addEventListener("change", event => {
    const input = event.target;
    if (input.name !== "match_columns") return;
    for (const peer of inputs) {
      if (peer.value === input.value) peer.checked = input.checked;
    }
    updateSelected();
  });

  for (const setting of form.querySelectorAll("[data-header-setting]")) {
    const mode = setting.querySelector("[data-header-mode]");
    const number = setting.querySelector("input");
    const pane = form.querySelector(`[data-preview-pane="${setting.dataset.headerSetting}"]`);
    const rows = pane.querySelector("tbody")?.rows ?? [];
    const status = setting.querySelector("[data-auto-header-status]");
    const detectedText = status.textContent;
    const refresh = setting.querySelector("[data-refresh-auto-header]");
    const markHeader = () => {
      let headerRow = 0;
      if (mode.value === "manual" && /^[0-9]{1,7}$/.test(number.value.trim())) headerRow = Number(number.value);
      if (!mode.value && !columnsChanged) headerRow = Number(setting.dataset.detectedHeader);
      for (const row of rows) {
        const isHeader = headerRow === Number(row.dataset.previewRow);
        row.classList.toggle("inquiry-header-row", isHeader);
        if (isHeader) row.cells[0].setAttribute("aria-label", `第 ${headerRow} 行，表头`);
        else row.cells[0].removeAttribute("aria-label");
      }
      let message = detectedText;
      if (!hasColumns) message = "请选择要匹配的号码列。";
      else if (columnsChanged) message = "匹配列已更改，更新预览可重新识别表头。";
      if (status.textContent !== message) status.textContent = message;
      refresh.hidden = !columnsChanged || !hasColumns;
    };
    setupPreviewScrolling(form, pane, markHeader);
    headerUpdates.push(markHeader);
    mode.addEventListener("change", () => {
      if (mode.value === "manual") number.focus();
      markHeader();
    });
    number.addEventListener("input", markHeader);
  }

  // Back/forward navigation can restore the enabled state after a submission.
  window.addEventListener("pageshow", () => {
    submit.textContent = "确认并查看结果";
    updateSelected();
  });
  form.addEventListener("submit", event => {
    if (event.submitter !== submit) return;
    submit.disabled = true;
    submit.textContent = "正在匹配…";
  });
  updateSelected();
}
