import {
  createQuickInquiryRequestGate,
  quickInquiryFragmentUrl,
  quickInquiryState,
  quickInquiryUrl,
  shouldUseQuickInquiryNavigation,
} from "./inquiry_quick_navigation.js";

const initializeQuickResults = (panel) => {
  if (!(panel instanceof HTMLElement) || panel.dataset.quickResultsReady === "true") return;
  panel.dataset.quickResultsReady = "true";
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
};

const initializeQuickResultsWithin = (root) => {
  root.querySelectorAll("[data-quick-results]").forEach(initializeQuickResults);
};

initializeQuickResultsWithin(document);

const quickForm = document.querySelector("form[data-quick-inquiry-form]");
const quickResultsHost = document.querySelector("[data-quick-results-host]");

if (quickForm instanceof HTMLFormElement && quickResultsHost instanceof HTMLElement) {
  const fileInput = quickForm.querySelector("input[type='file'][name='inquiry']");
  const queryInput = quickForm.querySelector("input[name='quick_oe']");
  const submitButton = quickForm.querySelector("button[type='submit']");
  const status = quickForm.querySelector("[data-submit-wait-message]");
  const fragmentEndpoint = quickResultsHost.dataset.quickResultsUrl || "";
  const requestGate = createQuickInquiryRequestGate();
  let requestController = null;

  const setLoading = (loading, message = "") => {
    quickResultsHost.setAttribute("aria-busy", loading ? "true" : "false");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.dataset.originalText ||= submitButton.textContent || "开始匹配";
      submitButton.textContent = loading ? "匹配中..." : submitButton.dataset.originalText;
    }
    if (status instanceof HTMLElement) status.textContent = message;
  };

  const replaceQuickResults = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    quickResultsHost.replaceChildren(template.content.cloneNode(true));
    initializeQuickResultsWithin(quickResultsHost);
  };

  const updateQueryHistory = (query) => {
    const nextUrl = quickInquiryUrl(window.location.href, query);
    if (nextUrl === window.location.href) {
      window.history.replaceState({}, "", nextUrl);
    } else {
      window.history.pushState({}, "", nextUrl);
    }
  };

  const loadQuickResults = async (query, { filter = "", historyMode = "push", fallback = "navigate" } = {}) => {
    const requestId = requestGate.begin();
    requestController?.abort();
    requestController = new AbortController();
    setLoading(true, "正在匹配...");
    try {
      const response = await fetch(
        quickInquiryFragmentUrl(fragmentEndpoint, window.location.href, query, filter),
        {
          cache: "no-store",
          headers: { Accept: "text/html", "X-Requested-With": "fetch" },
          signal: requestController.signal,
        },
      );
      const contentType = response.headers.get("Content-Type") || "";
      if (!response.ok || !contentType.includes("text/html")) throw new Error("quick inquiry failed");
      const html = await response.text();
      if (!requestGate.isCurrent(requestId)) return;
      replaceQuickResults(html);
      if (queryInput instanceof HTMLInputElement) queryInput.value = query;
      if (historyMode === "push") updateQueryHistory(query);
      setLoading(false, "匹配完成");
    } catch (error) {
      if (error?.name === "AbortError" || !requestGate.isCurrent(requestId)) return;
      if (fallback === "reload") {
        window.location.reload();
      } else {
        window.location.assign(quickInquiryUrl(window.location.href, query));
      }
    } finally {
      if (requestGate.isCurrent(requestId)) setLoading(false, status?.textContent || "");
    }
  };

  quickForm.addEventListener("submit", (event) => {
    const hasFile = fileInput instanceof HTMLInputElement && Boolean(fileInput.files?.length);
    const query = queryInput instanceof HTMLInputElement ? queryInput.value : "";
    if (!shouldUseQuickInquiryNavigation({ query, hasFile })) return;

    event.preventDefault();
    if (!fragmentEndpoint || typeof window.fetch !== "function" || typeof AbortController !== "function") {
      window.location.assign(quickInquiryUrl(window.location.href, query));
      return;
    }
    loadQuickResults(query.trim());
  }, { capture: true });

  window.addEventListener("popstate", () => {
    const state = quickInquiryState(window.location.href);
    if (queryInput instanceof HTMLInputElement) queryInput.value = state.query;
    if (!state.query) {
      requestGate.invalidate();
      requestController?.abort();
      quickResultsHost.replaceChildren();
      setLoading(false, "");
      return;
    }
    loadQuickResults(state.query, { filter: state.filter, historyMode: "none", fallback: "reload" });
  });
}

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

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target.closest("[data-quick-oe-image]") : null;
  if (!(target instanceof HTMLAnchorElement)) return;
  if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
  event.preventDefault();
  const image = target.querySelector("img");
  quickOeImageModalImg.src = target.href;
  quickOeImageModalImg.alt = image?.alt || "产品图片";
  quickOeImageModalCaption.textContent = target.dataset.caption || "";
  quickOeImageModal.classList.add("open");
  quickOeImageModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
});

document.querySelectorAll("[data-close-quick-oe-image-modal]").forEach((element) => {
  element.addEventListener("click", closeQuickOeImageModal);
});


document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && quickOeImageModal?.classList.contains("open")) {
    closeQuickOeImageModal();
  }
});
