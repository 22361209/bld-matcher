const MIN_COLUMN_WIDTH = 56;
const MAX_COLUMN_WIDTH = 640;
const WIDTH_STORAGE_VERSION = 1;

export const clampColumnWidth = (value) => {
  const width = Number(value);
  if (!Number.isFinite(width)) return MIN_COLUMN_WIDTH;
  return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(width)));
};

export const normalizeStoredWidths = (candidate, columnKeys) => {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return {};
  return columnKeys.reduce((widths, key) => {
    const width = Number(candidate[key]);
    if (Number.isFinite(width) && width >= MIN_COLUMN_WIDTH && width <= MAX_COLUMN_WIDTH) {
      widths[key] = Math.round(width);
    }
    return widths;
  }, {});
};

export const bindResizeSession = ({ handle, windowTarget, onMove, onFinish }) => {
  const finishEvents = ["pointerup", "pointercancel", "lostpointercapture"];
  let active = true;
  const finish = () => {
    if (!active) return;
    active = false;
    handle.removeEventListener("pointermove", onMove);
    finishEvents.forEach((eventName) => handle.removeEventListener(eventName, finish));
    windowTarget.removeEventListener("blur", finish);
    onFinish();
  };

  handle.addEventListener("pointermove", onMove);
  finishEvents.forEach((eventName) => handle.addEventListener(eventName, finish));
  windowTarget.addEventListener("blur", finish);
  return finish;
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
    // Resizing remains available for the current page when storage is unavailable.
  }
};

const safeStorageRemove = (key) => {
  try {
    window.localStorage.removeItem(key);
  } catch (_error) {
    // The in-memory reset remains effective when storage is unavailable.
  }
};

const columnLabel = (heading, index) => {
  const explicit = heading.dataset.columnLabel;
  const interactive = heading.querySelector("[data-column-label]")?.dataset.columnLabel;
  const visibleText = heading.textContent?.trim().replace(/\s+/g, " ");
  return explicit || interactive || visibleText || `第 ${index + 1} 列`;
};

const storagePayload = (widths) => JSON.stringify({
  version: WIDTH_STORAGE_VERSION,
  widths,
});

const readStoredWidths = (storageKey, columnKeys) => {
  const raw = safeStorageGet(storageKey);
  if (!raw) return {};
  try {
    const payload = JSON.parse(raw);
    if (payload.version !== WIDTH_STORAGE_VERSION) return {};
    return normalizeStoredWidths(payload.widths, columnKeys);
  } catch (_error) {
    return {};
  }
};

export function setupDataGrid(grid) {
  if (!(grid instanceof HTMLElement)) return;
  const scroll = grid.querySelector("[data-grid-scroll]");
  const table = scroll?.querySelector("table");
  const colgroup = table?.querySelector("colgroup");
  const headingRow = table?.tHead?.rows[0];
  if (!(scroll instanceof HTMLElement) || !(table instanceof HTMLTableElement) || !colgroup || !headingRow) return;

  const columns = Array.from(colgroup.children).filter((column) => column instanceof HTMLTableColElement);
  const headings = Array.from(headingRow.cells);
  if (!columns.length || columns.length !== headings.length) return;

  const columnKeys = columns.map((column, index) => column.dataset.col || `column-${index + 1}`);
  const scope = table.dataset.columnStorageScope || "guest";
  const gridKey = grid.dataset.gridKey || table.id || document.body.dataset.page || "table";
  const storageKey = `bld.data-grid.${gridKey}.widths.v${WIDTH_STORAGE_VERSION}.u${scope}`;
  const status = grid.querySelector("[data-column-resize-status]");
  const initialWidths = {};
  const currentWidths = {};

  headings.forEach((heading, index) => {
    initialWidths[columnKeys[index]] = clampColumnWidth(heading.getBoundingClientRect().width);
  });
  const storedWidths = readStoredWidths(storageKey, columnKeys);
  columnKeys.forEach((key) => {
    currentWidths[key] = storedWidths[key] || initialWidths[key];
  });

  const syncTableWidth = () => {
    let total = 0;
    columns.forEach((column, index) => {
      const width = clampColumnWidth(currentWidths[columnKeys[index]]);
      column.style.width = `${width}px`;
      total += width;
    });
    table.style.width = `${total}px`;
  };
  const persistWidths = () => safeStorageSet(storageKey, storagePayload(currentWidths));
  const announce = (message) => {
    if (status instanceof HTMLElement) status.textContent = message;
  };
  syncTableWidth();

  headings.forEach((heading, index) => {
    const key = columnKeys[index];
    const label = columnLabel(heading, index);
    const handle = document.createElement("span");
    handle.className = "data-column-resize-handle";
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", `调整${label}列宽`);
    handle.setAttribute("aria-valuemin", String(MIN_COLUMN_WIDTH));
    handle.setAttribute("aria-valuemax", String(MAX_COLUMN_WIDTH));
    handle.setAttribute("aria-valuenow", String(currentWidths[key]));
    handle.title = `拖动调整${label}列宽；双击恢复默认宽度`;
    heading.appendChild(handle);

    const setWidth = (width, { persist = false, announceChange = false } = {}) => {
      currentWidths[key] = clampColumnWidth(width);
      handle.setAttribute("aria-valuenow", String(currentWidths[key]));
      syncTableWidth();
      if (persist) persistWidths();
      if (announceChange) announce(`${label}列宽已调整为 ${currentWidths[key]} 像素。`);
    };

    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startWidth = currentWidths[key];
      const pointerId = event.pointerId;
      handle.classList.add("active");
      document.body.classList.add("data-grid-resizing");

      const onMove = (moveEvent) => {
        setWidth(startWidth + moveEvent.clientX - startX);
      };
      const finish = bindResizeSession({
        handle,
        windowTarget: window,
        onMove,
        onFinish: () => {
          if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
          handle.classList.remove("active");
          document.body.classList.remove("data-grid-resizing");
          persistWidths();
          announce(`${label}列宽已调整为 ${currentWidths[key]} 像素。`);
        },
      });
      try {
        handle.setPointerCapture(pointerId);
      } catch (_error) {
        finish();
      }
    });

    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const step = event.shiftKey ? 32 : 8;
      setWidth(currentWidths[key] + (event.key === "ArrowRight" ? step : -step), {
        persist: true,
        announceChange: true,
      });
    });

    handle.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setWidth(initialWidths[key], { persist: true, announceChange: true });
    });
  });

  grid.querySelector("[data-reset-column-widths]")?.addEventListener("click", () => {
    columnKeys.forEach((key) => {
      currentWidths[key] = initialWidths[key];
    });
    safeStorageRemove(storageKey);
    syncTableWidth();
    headings.forEach((heading, index) => {
      heading.querySelector(":scope > .data-column-resize-handle")?.setAttribute(
        "aria-valuenow",
        String(currentWidths[columnKeys[index]])
      );
    });
    announce("所有列宽已恢复为默认值。");
  });
}

export function setupDataGrids(root = document) {
  root.querySelectorAll("[data-resizable-grid]").forEach(setupDataGrid);
}

if (typeof document !== "undefined") setupDataGrids(document);
