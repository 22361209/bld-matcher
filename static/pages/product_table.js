const DEFAULT_COLUMNS = Object.freeze([
  "bld",
  "series",
  "item",
  "oe1",
  "oe2",
  "models",
  "image",
  "price",
  "product-status",
]);
const COLUMN_ORDER_VERSION = 1;
const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
].join(",");

export const buildColumnFilterSearchState = (rawQuery, labels) => {
  const query = String(rawQuery ?? "").trim().toLocaleLowerCase();
  return {
    query,
    matches: labels.map((label) => !query || String(label ?? "").toLocaleLowerCase().includes(query)),
  };
};

export const isImeCompositionEvent = (event) => Boolean(event?.isComposing || event?.keyCode === 229);

const normalizeOrder = (candidate, availableColumns = DEFAULT_COLUMNS) => {
  const requested = Array.isArray(candidate) ? candidate : [];
  const valid = requested.filter(
    (column, index) => availableColumns.includes(column) && requested.indexOf(column) === index
  );
  availableColumns.forEach((column) => {
    if (!valid.includes(column)) valid.push(column);
  });
  return valid;
};

const safeStorageGet = (key) => {
  try {
    return window.localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
};

const safeStorageSet = (key, value) => {
  try {
    window.localStorage.setItem(key, value);
  } catch (_error) {
    // The table remains usable when browser storage is unavailable.
  }
};

const safeStorageRemove = (key) => {
  try {
    window.localStorage.removeItem(key);
  } catch (_error) {
    // The in-memory reset still applies when browser storage is unavailable.
  }
};

const elementsByColumn = (elements) => new Map(
  Array.from(elements)
    .filter((element) => element.dataset.col)
    .map((element) => [element.dataset.col, element])
);

const applyColumnOrder = (table, order, availableColumns = DEFAULT_COLUMNS) => {
  const completeOrder = [...normalizeOrder(order, availableColumns), "actions"];
  const colgroup = table.querySelector("colgroup");
  const headingRow = table.tHead?.rows[0];
  if (!colgroup || !headingRow) return;

  const columns = elementsByColumn(colgroup.children);
  const headings = elementsByColumn(headingRow.children);
  completeOrder.forEach((column) => {
    if (columns.has(column)) colgroup.appendChild(columns.get(column));
    if (headings.has(column)) headingRow.appendChild(headings.get(column));
  });
  Array.from(table.tBodies).forEach((body) => {
    Array.from(body.rows).forEach((row) => {
      const cells = elementsByColumn(row.children);
      completeOrder.forEach((column) => {
        if (cells.has(column)) row.appendChild(cells.get(column));
      });
    });
  });
};

const tableColumnLabel = (table, column) => (
  table.querySelector(`th[data-col="${column}"] [data-column-label]`)?.dataset.columnLabel || column
);

export function setupProductTable(table, options = {}) {
  if (!(table instanceof HTMLTableElement)) return;

  const availableColumns = options.columns || DEFAULT_COLUMNS;
  const storagePrefix = options.storagePrefix || "bld.products";
  const resultsHash = options.resultsHash || "products-results";
  const storageScope = table.dataset.columnStorageScope || "guest";
  const orderStorageKey = `${storagePrefix}.column-order.v${COLUMN_ORDER_VERSION}.u${storageScope}`;
  const widthStorageKey = `${storagePrefix}.models-width.v1.u${storageScope}`;
  const orderStatus = document.querySelector("[data-column-order-status]");
  const filterPortal = table.closest(".app-surface") || document.body;
  let currentOrder = [...availableColumns];
  const savedOrder = safeStorageGet(orderStorageKey);
  if (savedOrder) {
    try {
      const payload = JSON.parse(savedOrder);
      if (payload.version === COLUMN_ORDER_VERSION) currentOrder = normalizeOrder(payload.columns, availableColumns);
    } catch (_error) {
      currentOrder = [...availableColumns];
    }
  }
  applyColumnOrder(table, currentOrder, availableColumns);

  const announceOrder = (message) => {
    if (orderStatus) orderStatus.textContent = message;
  };
  const persistOrder = () => {
    safeStorageSet(orderStorageKey, JSON.stringify({
      version: COLUMN_ORDER_VERSION,
      columns: currentOrder,
    }));
  };
  const moveColumn = (column, targetIndex) => {
    const sourceIndex = currentOrder.indexOf(column);
    if (sourceIndex < 0) return false;
    const boundedIndex = Math.max(0, Math.min(currentOrder.length - 1, targetIndex));
    if (boundedIndex === sourceIndex) return false;
    const nextOrder = currentOrder.filter((item) => item !== column);
    nextOrder.splice(boundedIndex, 0, column);
    currentOrder = nextOrder;
    applyColumnOrder(table, currentOrder, availableColumns);
    persistOrder();
    announceOrder(`${tableColumnLabel(table, column)}列已移动到第 ${currentOrder.indexOf(column) + 1} 列。`);
    return true;
  };

  let activeFilterTrigger = null;
  let activeFilterPanel = null;
  let activeFilterAnchor = null;

  const panelCheckboxes = (panel) => Array.from(panel.querySelectorAll("input[type='checkbox']"));
  const updatePanelSelection = (panel) => {
    const checkboxes = panelCheckboxes(panel);
    const checked = checkboxes.filter((input) => input.checked).length;
    const selection = panel.querySelector("[data-column-filter-selection]");
    if (selection) selection.textContent = `已选 ${checked} / ${checkboxes.length}`;
  };
  const setPanelSelection = (panel, checked) => {
    panelCheckboxes(panel).forEach((input) => {
      input.checked = checked;
    });
    const validation = panel.querySelector("[data-column-filter-validation]");
    if (validation) validation.textContent = "";
    updatePanelSelection(panel);
  };

  const resetPanelDraft = (panel) => {
    panelCheckboxes(panel).forEach((input) => {
      input.checked = input.dataset.initialChecked === "true";
    });
    const search = panel.querySelector("[data-column-filter-search]");
    if (search) search.value = "";
    panel.querySelectorAll("[data-column-filter-option]").forEach((option) => {
      option.hidden = false;
    });
    const noMatch = panel.querySelector("[data-column-filter-no-match]");
    if (noMatch) noMatch.hidden = true;
    const validation = panel.querySelector("[data-column-filter-validation]");
    if (validation) validation.textContent = "";
    updatePanelSelection(panel);
  };

  const closeColumnFilter = ({ restoreFocus = false } = {}) => {
    if (!activeFilterPanel || !activeFilterTrigger) return;
    const panel = activeFilterPanel;
    const trigger = activeFilterTrigger;
    panel.hidden = true;
    panel.style.left = "";
    panel.style.top = "";
    trigger.setAttribute("aria-expanded", "false");
    if (activeFilterAnchor?.isConnected) activeFilterAnchor.appendChild(panel);
    activeFilterPanel = null;
    activeFilterTrigger = null;
    activeFilterAnchor = null;
    if (restoreFocus && trigger.isConnected) trigger.focus();
  };

  const positionColumnFilter = () => {
    if (!activeFilterPanel || !activeFilterTrigger) return;
    const triggerRect = activeFilterTrigger.getBoundingClientRect();
    const panelRect = activeFilterPanel.getBoundingClientRect();
    const margin = 12;
    const left = Math.max(
      margin,
      Math.min(window.innerWidth - panelRect.width - margin, triggerRect.right - panelRect.width)
    );
    const spaceBelow = window.innerHeight - triggerRect.bottom - margin;
    const spaceAbove = triggerRect.top - margin;
    const preferredTop = spaceBelow >= Math.min(panelRect.height, 260) || spaceBelow >= spaceAbove
      ? triggerRect.bottom + 6
      : triggerRect.top - panelRect.height - 6;
    const top = Math.max(margin, Math.min(window.innerHeight - panelRect.height - margin, preferredTop));
    activeFilterPanel.style.left = `${Math.round(left)}px`;
    activeFilterPanel.style.top = `${Math.round(top)}px`;
  };

  const openColumnFilter = (trigger) => {
    const panelId = trigger.getAttribute("aria-controls");
    const panel = panelId ? document.getElementById(panelId) : null;
    if (!panel) return;
    if (activeFilterTrigger === trigger) {
      closeColumnFilter({ restoreFocus: true });
      return;
    }
    closeColumnFilter();
    activeFilterTrigger = trigger;
    activeFilterPanel = panel;
    activeFilterAnchor = trigger.closest("th");
    resetPanelDraft(panel);
    filterPortal.appendChild(panel);
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    positionColumnFilter();
    panel.querySelector("[data-column-filter-search]")?.focus();
  };

  const navigateWithColumnFilter = (key, values) => {
    const url = new URL(window.location.href);
    url.searchParams.delete(key);
    url.searchParams.delete("page");
    values.forEach((value) => url.searchParams.append(key, value));
    url.hash = resultsHash;
    window.location.assign(url.toString());
  };

  const navigateWithRangeFilter = (panel) => {
    const url = new URL(window.location.href);
    const inputs = Array.from(panel.querySelectorAll("[data-range-param]"));
    inputs.forEach((input) => {
      const key = input.dataset.rangeParam;
      if (!key) return;
      url.searchParams.delete(key);
      if (input.value.trim()) url.searchParams.set(key, input.value.trim());
    });
    url.searchParams.delete("page");
    url.hash = resultsHash;
    window.location.assign(url.toString());
  };

  const applyColumnFilter = (panel) => {
    const checkboxes = panelCheckboxes(panel);
    const checked = checkboxes.filter((input) => input.checked);
    const validation = panel.querySelector("[data-column-filter-validation]");
    if (checkboxes.length > 0 && checked.length === 0) {
      if (validation) validation.textContent = "请至少选择一项；如需取消筛选，请使用重置筛选。";
      panel.querySelector("[data-column-filter-select-all]")?.focus();
      return;
    }
    const values = checked.length === checkboxes.length ? [] : checked.map((input) => input.value);
    navigateWithColumnFilter(panel.dataset.filterKey, values);
  };

  table.querySelectorAll("[data-column-filter-panel] input[type='checkbox']").forEach((input) => {
    input.dataset.initialChecked = String(input.checked);
  });

  const updateColumnFilterSearch = (search) => {
    const panel = search.closest("[data-column-filter-panel]");
    if (!(panel instanceof HTMLElement)) return;
    const options = Array.from(panel.querySelectorAll("[data-column-filter-option]"));
    const labels = options.map((option) => option.querySelector("span")?.textContent || "");
    const searchState = buildColumnFilterSearchState(search.value, labels);
    let visibleOptions = 0;
    options.forEach((option, index) => {
      const matches = searchState.matches[index];
      const checkbox = option.querySelector("input[type='checkbox']");
      option.hidden = !matches;
      if (searchState.query && checkbox instanceof HTMLInputElement) checkbox.checked = matches;
      if (!option.hidden) visibleOptions += 1;
    });
    const noMatch = panel.querySelector("[data-column-filter-no-match]");
    if (noMatch) noMatch.hidden = options.length === 0 || visibleOptions > 0;
    const validation = panel.querySelector("[data-column-filter-validation]");
    if (validation) validation.textContent = "";
    updatePanelSelection(panel);
  };

  filterPortal.addEventListener("input", (event) => {
    const search = event.target;
    if (!(search instanceof HTMLInputElement) || !search.matches("[data-column-filter-search]")) return;
    if (isImeCompositionEvent(event)) return;
    updateColumnFilterSearch(search);
  });

  filterPortal.addEventListener("compositionend", (event) => {
    const search = event.target;
    if (!(search instanceof HTMLInputElement) || !search.matches("[data-column-filter-search]")) return;
    updateColumnFilterSearch(search);
  });

  filterPortal.addEventListener("change", (event) => {
    const checkbox = event.target;
    if (!(checkbox instanceof HTMLInputElement) || !checkbox.matches("[data-column-filter-option] input")) return;
    const panel = checkbox.closest("[data-column-filter-panel]");
    if (!(panel instanceof HTMLElement)) return;
    const validation = panel.querySelector("[data-column-filter-validation]");
    if (validation) validation.textContent = "";
    updatePanelSelection(panel);
  });

  filterPortal.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const trigger = event.target.closest("[data-column-filter-trigger]");
    if (trigger instanceof HTMLButtonElement) {
      openColumnFilter(trigger);
      return;
    }
    if (event.target.closest("[data-column-filter-close]")) {
      closeColumnFilter({ restoreFocus: true });
      return;
    }
    const panel = event.target.closest("[data-column-filter-panel]");
    if (panel instanceof HTMLElement) {
      if (event.target.closest("[data-column-filter-select-all]")) setPanelSelection(panel, true);
      else if (event.target.closest("[data-column-filter-select-none]")) setPanelSelection(panel, false);
      else if (event.target.closest("[data-column-filter-reset]")) {
        if (panel.dataset.filterMode === "range") {
          panel.querySelectorAll("[data-range-param]").forEach((input) => { input.value = ""; });
          navigateWithRangeFilter(panel);
        } else navigateWithColumnFilter(panel.dataset.filterKey, []);
      } else if (event.target.closest("[data-column-filter-apply]")) {
        if (panel.dataset.filterMode === "range") navigateWithRangeFilter(panel);
        else applyColumnFilter(panel);
      }
      return;
    }
    if (event.target.closest("[data-reset-product-columns]")) {
      closeColumnFilter();
      currentOrder = [...availableColumns];
      applyColumnOrder(table, currentOrder, availableColumns);
      safeStorageRemove(orderStorageKey);
      announceOrder("产品目录列顺序已恢复为默认顺序。");
    }
  });

  filterPortal.addEventListener("keydown", (event) => {
    if (!(event.target instanceof Element)) return;
    const search = event.target.closest("[data-column-filter-search]");
    const panel = event.target.closest("[data-column-filter-panel]");
    const isComposing = isImeCompositionEvent(event);
    if (search && panel instanceof HTMLElement && event.key === "Enter" && !isComposing) {
      event.preventDefault();
      applyColumnFilter(panel);
      return;
    }
    const handle = event.target.closest("[data-column-drag-handle]");
    if (handle instanceof HTMLElement && event.altKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      closeColumnFilter();
      const column = handle.closest("th")?.dataset.col;
      const sourceIndex = currentOrder.indexOf(column);
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      if (moveColumn(column, sourceIndex + direction)) requestAnimationFrame(() => handle.focus());
      return;
    }
    if (!(panel instanceof HTMLElement)) return;
    if (isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeColumnFilter({ restoreFocus: true });
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(panel.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
      (element) => !element.closest("[hidden]") && element.getClientRects().length > 0
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (!activeFilterPanel || !activeFilterTrigger) return;
    if (activeFilterPanel.contains(event.target) || activeFilterTrigger.contains(event.target)) return;
    closeColumnFilter({ restoreFocus: activeFilterPanel.contains(document.activeElement) });
  });
  window.addEventListener("scroll", positionColumnFilter, { capture: true, passive: true });
  window.addEventListener("resize", positionColumnFilter);

  const clearDropMarker = () => {
    table.querySelectorAll(".products-column-drop-before, .products-column-drop-after").forEach((heading) => {
      heading.classList.remove("products-column-drop-before", "products-column-drop-after");
    });
  };
  const insertionAt = (sourceColumn, clientX) => {
    const remaining = currentOrder.filter((column) => column !== sourceColumn);
    let index = remaining.length;
    for (let candidateIndex = 0; candidateIndex < remaining.length; candidateIndex += 1) {
      const heading = table.querySelector(`th[data-col="${remaining[candidateIndex]}"]`);
      if (!heading) continue;
      const rect = heading.getBoundingClientRect();
      if (clientX < rect.left + rect.width / 2) {
        index = candidateIndex;
        break;
      }
    }
    return { remaining, index };
  };
  const showDropMarker = ({ remaining, index }) => {
    clearDropMarker();
    const markerColumn = index < remaining.length ? remaining[index] : remaining[remaining.length - 1];
    const marker = markerColumn ? table.querySelector(`th[data-col="${markerColumn}"]`) : null;
    if (marker) marker.classList.add(index < remaining.length ? "products-column-drop-before" : "products-column-drop-after");
  };

  const startColumnDrag = (handle, event) => {
    if (event.button !== 0) return;
    const sourceColumn = handle.closest("th")?.dataset.col;
    if (!availableColumns.includes(sourceColumn)) return;
    closeColumnFilter();
    const startX = event.clientX;
    const startY = event.clientY;
    let dragging = false;
    let insertion = insertionAt(sourceColumn, event.clientX);

    const cleanup = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onBlur);
      document.body.classList.remove("products-column-dragging");
      handle.setAttribute("aria-grabbed", "false");
      clearDropMarker();
    };
    const onMove = (moveEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const deltaY = moveEvent.clientY - startY;
      if (!dragging) {
        if (Math.abs(deltaX) < 6 || Math.abs(deltaX) <= Math.abs(deltaY)) return;
        dragging = true;
        document.body.classList.add("products-column-dragging");
        handle.setAttribute("aria-grabbed", "true");
        window.getSelection()?.removeAllRanges();
      }
      moveEvent.preventDefault();
      insertion = insertionAt(sourceColumn, moveEvent.clientX);
      showDropMarker(insertion);
    };
    const onUp = (upEvent) => {
      if (dragging) {
        upEvent.preventDefault();
        const nextOrder = [...insertion.remaining];
        nextOrder.splice(insertion.index, 0, sourceColumn);
        currentOrder = normalizeOrder(nextOrder, availableColumns);
        applyColumnOrder(table, currentOrder, availableColumns);
        persistOrder();
        announceOrder(`${tableColumnLabel(table, sourceColumn)}列已移动到第 ${currentOrder.indexOf(sourceColumn) + 1} 列。`);
      }
      cleanup();
    };
    const onBlur = () => cleanup();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onBlur);
  };

  const columns = elementsByColumn(table.querySelectorAll("col"));
  const modelsCol = columns.get("models");
  const savedModelWidth = Number(safeStorageGet(widthStorageKey) || safeStorageGet("bldProductTableModelsWidth"));
  if (modelsCol && savedModelWidth) modelsCol.style.width = `${savedModelWidth}px`;
  const startModelsResize = (event) => {
    if (event.button !== 0 || !modelsCol) return;
    event.preventDefault();
    event.stopPropagation();
    closeColumnFilter();
    const startX = event.clientX;
    const startWidth = modelsCol.getBoundingClientRect().width;
    let resizing = true;
    let widthToPersist = null;
    const onMove = (moveEvent) => {
      const width = Math.min(420, Math.max(100, Math.round(startWidth + moveEvent.clientX - startX)));
      modelsCol.style.width = `${width}px`;
      widthToPersist = width;
    };
    const cleanup = () => {
      if (!resizing) return;
      resizing = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onBlur);
      if (widthToPersist !== null) safeStorageSet(widthStorageKey, String(widthToPersist));
    };
    const onUp = () => cleanup();
    const onBlur = () => cleanup();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onBlur);
  };

  filterPortal.addEventListener("mousedown", (event) => {
    if (!(event.target instanceof Element)) return;
    if (event.target.closest("th[data-col='models'] .resize-handle")) {
      startModelsResize(event);
      return;
    }
    const handle = event.target.closest("[data-column-drag-handle]");
    if (handle instanceof HTMLElement) startColumnDrag(handle, event);
  });
}
