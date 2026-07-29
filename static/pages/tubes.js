import {
  createInlineResultsRequestGate,
  formGetUrl,
  inlineResultsFragmentUrl,
  inlineResultsHistoryUrl,
  scrollDataGridToTop,
} from "./inline_results_navigation.js?v=20260729-3";
import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";

if (document.body.dataset.page === "tubes.list") {
  const resultsHost = document.querySelector("[data-tube-results-host]");
  const requestGate = createInlineResultsRequestGate();
  let requestController = null;
  let cleanupProductTable = () => {};

  const notifyDataGrids = (action) => {
    document.dispatchEvent(new CustomEvent(`bld:data-grids:${action}`, { detail: { root: resultsHost } }));
  };

  const setStatus = (message = "", state = "") => {
    const status = resultsHost?.querySelector("[data-tube-inline-status]");
    if (!(status instanceof HTMLElement)) return;
    status.textContent = message;
    status.classList.remove("active", "done", "error");
    if (message && state) status.classList.add(state);
  };

  const initializeResults = () => {
    cleanupProductTable();
    const table = resultsHost?.querySelector("#tubes-table");
    cleanupProductTable = setupDataGridControls(table, {
      columns: ["code", "type", "spec", "blank-length", "inner-tolerance", "purchase-base", "material", "weight", "tolerance", "consumption", "borrow"],
      storagePrefix: "bld.tubes",
      resultsHash: "tube-results",
      navigate: (url) => loadResults(url),
      resetFilterParams: [
        "type", "blank_length", "inner_tolerance", "purchase_base", "material", "tolerance",
        "consumption", "weight_eq", "weight_min", "weight_max", "outer_diameter", "inner_diameter",
      ],
    });
    resultsHost?.querySelector("[data-tube-search-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadResults(formGetUrl(event.currentTarget, window.location.href));
    });
    notifyDataGrids("setup");
  };

  const loadResults = async (targetHref, { history = "push", scroll = "preserve" } = {}) => {
    if (!(resultsHost instanceof HTMLElement) || !resultsHost.dataset.tubeResultsFragmentUrl) {
      window.location.assign(targetHref);
      return false;
    }
    if (typeof window.fetch !== "function" || typeof window.AbortController !== "function") {
      window.location.assign(targetHref);
      return false;
    }

    requestController?.abort();
    requestController = new AbortController();
    const generation = requestGate.begin();
    const currentGridScroll = resultsHost.querySelector("[data-grid-scroll]");
    const scrollState = {
      windowX: window.scrollX,
      windowY: window.scrollY,
      gridLeft: currentGridScroll?.scrollLeft || 0,
      gridTop: currentGridScroll?.scrollTop || 0,
    };
    resultsHost.setAttribute("aria-busy", "true");
    setStatus();

    try {
      const response = await fetch(
        inlineResultsFragmentUrl(resultsHost.dataset.tubeResultsFragmentUrl, targetHref, window.location.href),
        {
          cache: "no-store",
          credentials: "same-origin",
          headers: { Accept: "text/html", "X-Requested-With": "fetch" },
          signal: requestController.signal,
        }
      );
      const contentType = response.headers.get("Content-Type") || "";
      if (!response.ok || !contentType.includes("text/html")) throw new Error("fragment unavailable");
      const html = await response.text();
      if (!requestGate.isCurrent(generation)) return false;
      const template = document.createElement("template");
      template.innerHTML = html.trim();
      const nextResults = template.content.querySelector("[data-tube-results]");
      if (!(nextResults instanceof HTMLElement)) throw new Error("invalid fragment");

      cleanupProductTable();
      notifyDataGrids("cleanup");
      resultsHost.replaceChildren(template.content);
      const canonicalHref = new URL(nextResults.dataset.canonicalUrl || targetHref, window.location.href).toString();
      const historyUrl = inlineResultsHistoryUrl(canonicalHref);
      if (history === "push" && historyUrl !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        window.history.pushState({}, "", historyUrl);
      } else if (history === "replace") {
        window.history.replaceState({}, "", historyUrl);
      }
      initializeResults();
      requestAnimationFrame(() => {
        const nextGridScroll = resultsHost.querySelector("[data-grid-scroll]");
        if (nextGridScroll instanceof HTMLElement) {
          nextGridScroll.scrollLeft = scrollState.gridLeft;
          nextGridScroll.scrollTop = scroll === "grid-top" ? 0 : scrollState.gridTop;
        }
        if (scroll === "grid-top") scrollDataGridToTop(resultsHost);
        else window.scrollTo(scrollState.windowX, scrollState.windowY);
      });
      return true;
    } catch (error) {
      if (error?.name === "AbortError" || !requestGate.isCurrent(generation)) return false;
      window.location.assign(targetHref);
      return false;
    } finally {
      if (requestGate.isCurrent(generation)) resultsHost.setAttribute("aria-busy", "false");
    }
  };

  resultsHost?.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest("a[data-inline-results-link]");
    if (!(link instanceof HTMLAnchorElement) || event.defaultPrevented) return;
    if (event instanceof MouseEvent && event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = new URL(link.href, window.location.href);
    if (target.pathname !== window.location.pathname) return;
    event.preventDefault();
    loadResults(target.toString(), { scroll: link.closest(".data-grid-pagination") ? "grid-top" : "preserve" });
  });

  resultsHost?.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-grid-page-jump]")) return;
    event.preventDefault();
    const page = Number.parseInt(form.elements.page?.value || "", 10);
    const totalPages = Number.parseInt(form.dataset.totalPages || "", 10);
    if (!Number.isInteger(page) || page < 1 || page > totalPages) return;
    const target = new URL(window.location.href);
    target.searchParams.set("page", String(page));
    target.hash = "tube-results";
    loadResults(target.toString(), { scroll: "grid-top" });
  });

  window.addEventListener("popstate", () => loadResults(window.location.href, { history: "none" }));
  initializeResults();
}
