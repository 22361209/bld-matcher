import {
  quickInquiryUrl,
  shouldUseQuickInquiryNavigation,
} from "./inquiry_quick_navigation.js";

document.querySelectorAll("form[data-quick-inquiry-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const fileInput = form.querySelector("input[type='file'][name='inquiry']");
    const queryInput = form.querySelector("input[name='quick_oe']");
    const hasFile = fileInput instanceof HTMLInputElement && Boolean(fileInput.files?.length);
    const query = queryInput instanceof HTMLInputElement ? queryInput.value : "";
    if (!shouldUseQuickInquiryNavigation({ query, hasFile })) return;

    event.preventDefault();
    window.location.assign(quickInquiryUrl(window.location.href, query));
  }, { capture: true });
});

document.querySelectorAll("[data-quick-results]").forEach((panel) => {
  const cards = Array.from(panel.querySelectorAll("[data-quick-card]"));
  const filters = Array.from(panel.querySelectorAll("[data-quick-filter]"));
  const count = panel.querySelector("[data-quick-result-count]");
  const prefix = panel.querySelector("[data-quick-filter-prefix]");
  const empty = panel.querySelector("[data-quick-filter-empty]");
  let activeFilter = panel.dataset.initialFilter || "";

  const updateUrl = (filter) => {
    if (!window.history?.replaceState) return;
    const url = new URL(window.location.href);
    if (filter) {
      url.searchParams.set("quick_filter", filter);
    } else {
      url.searchParams.delete("quick_filter");
    }
    window.history.replaceState({}, "", url);
  };

  const applyFilter = (filter, updateHistory = false) => {
    activeFilter = filter || "";
    let visibleCount = 0;
    cards.forEach((card) => {
      const visible = !activeFilter || card.dataset.matchType === activeFilter;
      card.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    filters.forEach((filterButton) => {
      const active = filterButton.dataset.quickFilter === activeFilter;
      filterButton.classList.toggle("active", active);
      filterButton.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const activeButton = filters.find((filterButton) => filterButton.dataset.quickFilter === activeFilter);
    if (prefix instanceof HTMLElement) {
      prefix.textContent = activeButton ? `${activeButton.dataset.filterLabel || activeButton.textContent} · ` : "";
    }
    if (count instanceof HTMLElement) {
      count.textContent = `${visibleCount}`;
    }
    if (empty instanceof HTMLElement) {
      empty.hidden = visibleCount > 0;
    }
    if (updateHistory) {
      updateUrl(activeFilter);
    }
  };

  filters.forEach((filterButton) => {
    filterButton.addEventListener("click", (event) => {
      event.preventDefault();
      const nextFilter = activeFilter === filterButton.dataset.quickFilter ? "" : filterButton.dataset.quickFilter || "";
      applyFilter(nextFilter, true);
    });
  });

  applyFilter(activeFilter, false);
});

document.querySelectorAll("[data-history-loader]").forEach((drawer) => {
  const url = drawer.dataset.historyUrl || "";
  const count = drawer.querySelector("[data-history-count]");
  const tableCount = drawer.querySelector("[data-history-table-count]");
  const rows = drawer.querySelector("[data-history-rows]");
  const tableWrap = drawer.querySelector("[data-history-table-wrap]");
  const empty = drawer.querySelector("[data-history-empty]");
  const searchInput = drawer.querySelector("input[name='history_q']");
  let loaded = drawer.dataset.historyLoaded === "true";
  let loading = false;

  const setCount = (value) => {
    const text = `${value} 条`;
    if (count instanceof HTMLElement) count.textContent = text;
    if (tableCount instanceof HTMLElement) tableCount.textContent = text;
  };

  const appendCell = (row, text, href = "") => {
    const cell = document.createElement("td");
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = text;
      cell.appendChild(link);
    } else {
      cell.textContent = text;
    }
    row.appendChild(cell);
  };

  const renderRows = (items) => {
    if (!(rows instanceof HTMLElement)) return;
    rows.replaceChildren();
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, item.name || "", item.download_url || "");
      appendCell(row, item.kind || "");
      appendCell(row, item.operator || "");
      appendCell(row, item.updated_at || "");
      appendCell(row, "下载", item.download_url || "");
      rows.appendChild(row);
    });
    if (tableWrap instanceof HTMLElement) tableWrap.hidden = items.length === 0;
    if (empty instanceof HTMLElement) {
      empty.hidden = items.length > 0;
      empty.textContent = items.length ? "" : "还没有历史报价文件。";
    }
    setCount(items.length);
  };

  const loadHistory = async () => {
    if (!url || loaded || loading) return;
    loading = true;
    if (count instanceof HTMLElement) count.textContent = "加载中";
    if (tableCount instanceof HTMLElement) tableCount.textContent = "加载中";
    if (empty instanceof HTMLElement) {
      empty.hidden = false;
      empty.textContent = "正在加载历史报价文件...";
    }
    try {
      const requestUrl = new URL(url, window.location.origin);
      const query = searchInput instanceof HTMLInputElement ? searchInput.value.trim() : "";
      if (query) requestUrl.searchParams.set("history_q", query);
      const response = await fetch(requestUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("load failed");
      const payload = await response.json();
      renderRows(Array.isArray(payload.rows) ? payload.rows : []);
      loaded = true;
      drawer.dataset.historyLoaded = "true";
    } catch (_error) {
      if (count instanceof HTMLElement) count.textContent = "加载失败";
      if (tableCount instanceof HTMLElement) tableCount.textContent = "加载失败";
      if (empty instanceof HTMLElement) {
        empty.hidden = false;
        empty.textContent = "历史报价文件加载失败，请刷新后再试。";
      }
    } finally {
      loading = false;
    }
  };

  if (drawer.open) {
    loadHistory();
  }
  drawer.addEventListener("toggle", () => {
    if (drawer.open) loadHistory();
  });
});


const quickOeImageModal = document.querySelector("#quick-oe-image-modal");
const quickOeImageModalImg = document.querySelector("#quick-oe-image-modal-img");
const quickOeImageModalCaption = document.querySelector("#quick-oe-image-modal-caption");

const closeQuickOeImageModal = () => {
  if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
  quickOeImageModal.classList.remove("open");
  quickOeImageModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  quickOeImageModalImg.src = "";
  quickOeImageModalImg.alt = "";
  quickOeImageModalCaption.textContent = "";
};

document.querySelectorAll("[data-quick-oe-image]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
    event.preventDefault();
    const image = link.querySelector("img");
    quickOeImageModalImg.src = link.href;
    quickOeImageModalImg.alt = image?.alt || "产品图片";
    quickOeImageModalCaption.textContent = link.dataset.caption || "";
    quickOeImageModal.classList.add("open");
    quickOeImageModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  });
});

document.querySelectorAll("[data-close-quick-oe-image-modal]").forEach((element) => {
  element.addEventListener("click", closeQuickOeImageModal);
});


document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && quickOeImageModal?.classList.contains("open")) {
    closeQuickOeImageModal();
  }
});
