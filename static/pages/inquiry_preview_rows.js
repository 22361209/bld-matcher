const PAGE_SIZE = 100;

function sourceRow(number, cells, columns) {
  const row = document.createElement("tr");
  row.dataset.previewRow = number;
  row.insertCell().textContent = number;
  for (const column of columns) {
    const value = cells[column] ?? "";
    const content = document.createElement("div");
    content.className = "match-preview-cell";
    content.textContent = content.title = value;
    row.insertCell().append(content);
  }
  return row;
}

export function setupPreviewScrolling(form, pane, markHeader) {
  const scroll = pane.querySelector("[data-grid-scroll]");
  if (!scroll) return;
  const table = scroll.querySelector("table");
  const body = table.tBodies[0];
  const headingHeight = () => table.tHead.getBoundingClientRect().height;
  const alignRow = (row, margin) => {
    scroll.scrollTop += row.getBoundingClientRect().top - scroll.getBoundingClientRect().top - headingHeight() - margin;
  };
  let placed = false;
  function placeHeader() {
    if (placed || pane.hidden) return;
    placed = true;
    const row = body.querySelector(".inquiry-header-row");
    if (row && row.getBoundingClientRect().bottom > scroll.getBoundingClientRect().bottom) alignRow(row, 48);
  }
  requestAnimationFrame(placeHeader);
  form.querySelector("[data-preview-sheet]")?.addEventListener("change", placeHeader);

  const paging = pane.querySelector("[data-preview-paging]");
  if (!paging) return;
  const before = paging.querySelector("[data-load-before]");
  const after = paging.querySelector("[data-load-after]");
  const status = paging.querySelector("[data-load-status]");
  const columns = [...table.querySelectorAll('input[name="match_columns"]')].map(input => input.value);
  const last = Number(pane.dataset.lastRow);
  const batchSize = Math.min(PAGE_SIZE, Math.max(1, Math.floor(10000 / columns.length)));
  const firstRow = () => Number(body.rows[0].dataset.previewRow);
  const lastRow = () => Number(body.rows[body.rows.length - 1].dataset.previewRow);
  let busy = false;
  let failed = false;
  let previousTop = scroll.scrollTop;

  const nearEdge = forward => forward
    ? scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 200
    : scroll.scrollTop < 200;

  async function load(forward, reveal = false) {
    if (busy || pane.hidden || (!reveal && (failed || !nearEdge(forward)))) return;
    const start = forward ? lastRow() + 1 : Math.max(1, firstRow() - batchSize);
    const end = forward ? Math.min(start + batchSize - 1, last) : firstRow() - 1;
    if (start > end) return;
    busy = true;
    failed = false;
    before.disabled = after.disabled = true;
    status.textContent = "正在读取…";
    try {
      const params = new URLSearchParams({
        preview_action: "rows",
        csrf_token: form.elements.namedItem("csrf_token").value,
        upload_path: form.elements.namedItem("upload_path").value,
        sheet_number: pane.dataset.previewPane,
        start_row: start,
        end_row: end,
      });
      const response = await fetch(form.dataset.rowsUrl, {
        method: "POST", body: params, headers: {Accept: "application/json"},
      });
      if (!response.ok) throw new Error();
      const page = await response.json();
      if (page.sheet_number !== Number(pane.dataset.previewPane) || page.row_start !== start
          || page.row_end !== end || page.rows.length !== end - start + 1) throw new Error();
      if (pane.hidden || (!reveal && !nearEdge(forward))) return;

      const chunk = document.createDocumentFragment();
      for (const {number, cells} of page.rows) chunk.append(sourceRow(number, cells, columns));
      const edge = forward ? chunk.firstElementChild : chunk.lastElementChild;
      const anchor = forward ? body.lastElementChild : body.firstElementChild;
      const anchorTop = anchor.getBoundingClientRect().top;
      if (forward) body.append(chunk);
      else body.prepend(chunk);
      // Keep nearby rows in the DOM and preserve the current row as the window moves.
      while (body.rows.length > 3 * batchSize) body.deleteRow(forward ? 0 : -1);
      markHeader();
      if (reveal) alignRow(edge, 0);
      else scroll.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
      previousTop = scroll.scrollTop;
    } catch {
      failed = true;
      status.textContent = "读取失败，请点击按钮重试。";
    } finally {
      busy = false;
      if (!failed) status.textContent = "";
      before.hidden = firstRow() <= 1;
      after.hidden = lastRow() >= last;
      before.disabled = after.disabled = false;
    }
  }
  before.addEventListener("click", () => load(false, true));
  after.addEventListener("click", () => load(true, true));
  scroll.addEventListener("scroll", () => {
    const top = scroll.scrollTop;
    if (top !== previousTop) load(top > previousTop);
    previousTop = top;
  }, {passive: true});
  // A wheel at the first or last loaded row may not produce a scroll event.
  scroll.addEventListener("wheel", event => {
    if (!event.ctrlKey && Math.abs(event.deltaY) > Math.abs(event.deltaX)) load(event.deltaY > 0);
  }, {passive: true});
}
