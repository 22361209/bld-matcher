import {
  createInlineResultsRequestGate,
  formGetUrl,
  inlineResultsFragmentUrl,
  inlineResultsHistoryUrl,
  scrollDataGridToTop,
} from "./inline_results_navigation.js?v=20260729-3";
import { setupQuoteFieldComboboxes } from "../components/quote_comboboxes.js?v=20260728-1";
import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";

if (document.body.dataset.page === "quotes.list") {
  const resultsHost = document.querySelector("[data-quote-results-host]");
  const requestGate = createInlineResultsRequestGate();
  let requestController = null;
  let cleanupQuoteTable = () => {};

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

  const numberDialog = document.querySelector("#quote-number-dialog");

  const setupQuoteContractForm = (root) => {
    const form = root?.querySelector("[data-quote-contract-form]");
    if (!(form instanceof HTMLFormElement)) return;
    const feedback = form.querySelector("[data-quote-contract-feedback]");
    const count = form.querySelector("[data-quote-contract-count]");
    const checkboxes = Array.from(form.querySelectorAll('input[name="quote_id"]'));
    const versionOptions = Array.from(form.querySelectorAll('input[name="language"]'));
    const selectedVersion = () => versionOptions.find(
      (input) => input instanceof HTMLInputElement && input.checked,
    );
    const syncSelection = () => {
      const selected = checkboxes.filter((input) => input instanceof HTMLInputElement && input.checked).length;
      if (count instanceof HTMLElement) count.textContent = String(selected);
      if (selected && selectedVersion() && feedback instanceof HTMLElement) {
        feedback.hidden = true;
        feedback.textContent = "";
      }
      return selected;
    };
    checkboxes.forEach((input) => input.addEventListener("change", syncSelection));
    versionOptions.forEach((input) => input.addEventListener("change", syncSelection));
    form.addEventListener("submit", (event) => {
      const selected = syncSelection();
      if (!selected || !selectedVersion()) {
        event.preventDefault();
        if (feedback instanceof HTMLElement) {
          feedback.textContent = selected ? "请选择销售合同版本。" : "请至少选择一条报价明细。";
          feedback.hidden = false;
        }
        (selected ? versionOptions[0] : checkboxes[0])?.focus();
      }
    });
    syncSelection();
  };

  const openQuoteNumber = (button) => {
    if (!(numberDialog instanceof HTMLDialogElement)) return;
    const label = numberDialog.querySelector("[data-quote-number-label]");
    const detail = numberDialog.querySelector("[data-quote-number-detail]");
    if (label instanceof HTMLElement) {
      label.textContent = button.dataset.quoteNumber || "";
    }
    if (detail instanceof HTMLElement) {
      detail.textContent = "正在加载...";
    }
    numberDialog.showModal();
    fetch(button.dataset.quoteNumberUrl, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "text/html", "X-Requested-With": "fetch" },
    })
      .then((response) => {
        if (!response.ok) throw new Error("load failed");
        return response.text();
      })
      .then((html) => {
        if (detail instanceof HTMLElement) {
          detail.innerHTML = html;
          setupQuoteContractForm(detail);
        }
      })
      .catch(() => {
        if (detail instanceof HTMLElement) {
          detail.textContent = "加载失败，请稍后重试。";
        }
      });
  };

  if (numberDialog instanceof HTMLDialogElement) {
    numberDialog.querySelector("[data-close-quote-number]")?.addEventListener("click", () => numberDialog.close());
    numberDialog.addEventListener("click", (event) => {
      if (event.target === numberDialog) numberDialog.close();
    });
  }

  const initializeResults = () => {
    cleanupQuoteTable();
    const table = resultsHost?.querySelector("#quotes-table");
    cleanupQuoteTable = setupDataGridControls(table, {
      columns: [
        "quote-no",
        "date",
        "customer",
        "bld",
        "customer-code",
        "tax-price",
        "net-price",
        "currency",
        "quoted-by",
        "source",
        "remark",
      ],
      storagePrefix: "bld.quotes",
      resultsHash: "quote-results",
      navigate: (url) => loadResults(url),
      filterPortal: table?.closest("[data-quote-results]"),
      resetFilterParams: [
        "qf_quote_no", "qf_quote_date", "qf_customer_name", "qf_bld_no",
        "qf_customer_product_code", "qf_tax_price", "qf_net_price", "qf_currency",
        "qf_quoted_by", "qf_source_type", "qf_remark",
      ],
    });
    setupQuoteFieldComboboxes(resultsHost || document);
    resultsHost?.querySelector("[data-quote-search-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadResults(formGetUrl(event.currentTarget, window.location.href));
    });
    resultsHost?.querySelectorAll("[data-open-quote-edit]").forEach((button) => {
      button.addEventListener("click", () => openQuoteEdit(document.getElementById(button.dataset.openQuoteEdit)));
    });
    resultsHost?.querySelectorAll("[data-quote-number-url]").forEach((button) => {
      button.addEventListener("click", () => openQuoteNumber(button));
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

  const loadResults = async (targetHref, { history = "push", scroll = "preserve" } = {}) => {
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

      cleanupQuoteTable();
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
    const link = event.target.closest("a[data-inline-results-link], a[data-quote-results-link]");
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
    target.hash = "quote-results";
    loadResults(target.toString(), { scroll: "grid-top" });
  });

  window.addEventListener("popstate", () => loadResults(window.location.href, { history: "none" }));
  initializeResults();
}
