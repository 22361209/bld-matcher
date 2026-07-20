import {
  createInlineResultsRequestGate,
  formGetUrl,
  inlineResultsFragmentUrl,
  inlineResultsHistoryUrl,
} from "./inline_results_navigation.js?v=20260721-2";

if (document.body.dataset.page === "quotes.list") {
  const resultsHost = document.querySelector("[data-quote-results-host]");
  const requestGate = createInlineResultsRequestGate();
  let requestController = null;

  const notifyDataGrids = (action) => {
    document.dispatchEvent(new CustomEvent(`bld:data-grids:${action}`, { detail: { root: resultsHost } }));
  };

  const setStatus = (message = "", state = "") => {
    const status = resultsHost?.querySelector("[data-quote-inline-status]");
    if (!(status instanceof HTMLElement)) return;
    status.textContent = message;
    status.classList.remove("active", "done", "error");
    if (message && state) status.classList.add(state);
  };

  const openQuoteEdit = (dialog) => {
    if (dialog?.showModal) {
      dialog.showModal();
      dialog.querySelector("input[name='customer_name']")?.focus();
    }
  };

  const initializeResults = () => {
    resultsHost?.querySelector("[data-quote-search-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadResults(formGetUrl(event.currentTarget, window.location.href));
    });
    resultsHost?.querySelectorAll("[data-open-quote-edit]").forEach((button) => {
      button.addEventListener("click", () => openQuoteEdit(document.getElementById(button.dataset.openQuoteEdit)));
    });
    resultsHost?.querySelectorAll("[data-close-quote-edit]").forEach((button) => {
      button.addEventListener("click", () => button.closest("dialog")?.close());
    });
    resultsHost?.querySelectorAll(".quote-edit-dialog").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
    });
  };

  const loadResults = async (targetHref, { history = "push" } = {}) => {
    if (!(resultsHost instanceof HTMLElement) || !resultsHost.dataset.quoteResultsFragmentUrl) {
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
        inlineResultsFragmentUrl(resultsHost.dataset.quoteResultsFragmentUrl, targetHref, window.location.href),
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
      const nextResults = template.content.querySelector("[data-quote-results]");
      if (!(nextResults instanceof HTMLElement)) throw new Error("invalid fragment");

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
      notifyDataGrids("setup");
      requestAnimationFrame(() => {
        const nextGridScroll = resultsHost.querySelector("[data-grid-scroll]");
        if (nextGridScroll instanceof HTMLElement) {
          nextGridScroll.scrollLeft = scrollState.gridLeft;
          nextGridScroll.scrollTop = scrollState.gridTop;
        }
        window.scrollTo(scrollState.windowX, scrollState.windowY);
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
    const link = event.target.closest("a[data-inline-results-link], a[data-quote-results-link]");
    if (!(link instanceof HTMLAnchorElement) || event.defaultPrevented) return;
    if (event instanceof MouseEvent && event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = new URL(link.href, window.location.href);
    if (target.pathname !== window.location.pathname) return;
    event.preventDefault();
    loadResults(target.toString());
  });

  window.addEventListener("popstate", () => loadResults(window.location.href, { history: "none" }));
  initializeResults();
}
