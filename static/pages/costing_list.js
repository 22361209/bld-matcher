import { setupDataGridControls } from "../components/data_grid_controls.js?v=20261105-1";
import { formGetUrl } from "./inline_results_navigation.js?v=20260729-3";

if (document.body.dataset.page === "costing.list") {
  const results = document.querySelector("#costing-results");
  const form = results.querySelector("[data-costing-list-form]");
  const controls = form.querySelector("[data-quote-controls]");
  const query = form.elements.namedItem("q");
  let grid = results.querySelector("[data-costing-current-page]");
  let timer;
  let request;
  let pending = false;

  const cancelRefresh = () => {
    clearTimeout(timer);
    request?.abort();
  };

  // Preserve live toolbar choices if a column filter, sort or page is chosen
  // before a quote refresh has finished.
  const mergeToolbar = (target) => {
    const url = new URL(target, window.location.href);
    for (const input of controls.elements) {
      if (!input.name) continue;
      if (input.type === "checkbox" && !input.checked) url.searchParams.delete(input.name);
      else url.searchParams.set(input.name, input.value);
    }
    if (query.value.trim()) url.searchParams.set("q", query.value.trim());
    else url.searchParams.delete("q");
    return url;
  };

  const setupGrid = () => {
    const table = grid.querySelector("table");
    const columns = [...table.querySelectorAll("thead th[data-col]")].map(heading => heading.dataset.col);
    return setupDataGridControls(table, {
      columns, storagePrefix: "bld.costing", resultsHash: "costing-results", filterPortal: grid,
      navigate: target => {
        cancelRefresh();
        window.location.assign(mergeToolbar(target));
      },
      resetFilterParams: columns.filter(key => key !== "actions" && key !== "quote").map(key => `cf_${key}`),
    });
  };
  let cleanupGrid = setupGrid();
  const notifyGrids = action => document.dispatchEvent(
    new CustomEvent(`bld:data-grids:${action}`, { detail: { root: results } }),
  );

  const refresh = async () => {
    const target = new URL(formGetUrl(form, window.location.href));
    const current = new URL(window.location.href);
    const sameQuery = (target.searchParams.get("q") || "") === (current.searchParams.get("q") || "");
    if (sameQuery && Number(grid.dataset.costingCurrentPage) > 1) {
      target.searchParams.set("page", grid.dataset.costingCurrentPage);
    }
    const controller = new AbortController();
    request = controller;
    try {
      const response = await fetch(target, {
        headers: {Accept: "text/html"}, credentials: "same-origin", cache: "no-store", signal: controller.signal,
      });
      if (!response.ok) throw new Error("Could not refresh costs");
      const html = new DOMParser().parseFromString(await response.text(), "text/html");
      if (controller.signal.aborted) return;
      const next = html.querySelector('[data-page="costing.list"] [data-costing-current-page]');
      if (!next) throw new Error("Missing cost grid");
      const scroll = grid.querySelector("[data-grid-scroll]");
      const {scrollLeft, scrollTop} = scroll;
      cleanupGrid();
      notifyGrids("cleanup");
      grid.replaceWith(next);
      grid = next;
      cleanupGrid = setupGrid();
      notifyGrids("setup");
      const nextScroll = grid.querySelector("[data-grid-scroll]");
      nextScroll.scrollLeft = scrollLeft;
      nextScroll.scrollTop = sameQuery ? scrollTop : 0;
      if (target.href !== window.location.href) history.pushState({}, "", target);
      pending = false;
    } catch {
      if (!controller.signal.aborted) window.location.assign(target);
    }
  };

  const schedule = () => {
    cancelRefresh();
    pending = true;
    grid.setAttribute("aria-busy", "true");
    timer = setTimeout(refresh, 250);
  };
  controls.addEventListener("change", schedule);
  query.addEventListener("input", event => {
    if (pending && !event.isComposing) schedule();
  });
  query.addEventListener("compositionstart", () => { if (pending) cancelRefresh(); });
  query.addEventListener("compositionend", () => { if (pending) schedule(); });
  form.addEventListener("submit", cancelRefresh);
  results.addEventListener("click", event => {
    const link = event.target.closest(".data-grid-pagination a[href], .data-grid-sort-trigger");
    if (link && pending) {
      if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) cancelRefresh();
      link.href = mergeToolbar(link.href);
    }
  });
  results.addEventListener("submit", event => {
    if (!pending || !event.target.matches("[data-grid-page-jump]")) return;
    event.preventDefault();
    cancelRefresh();
    window.location.assign(mergeToolbar(formGetUrl(event.target, window.location.href)));
  });
  window.addEventListener("popstate", () => {
    cancelRefresh();
    window.location.reload();
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted && pending) schedule();
  });
  window.addEventListener("pagehide", cancelRefresh);
}
