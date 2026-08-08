import {
  createInlineResultsRequestGate,
  formGetUrl,
  inlineResultsFragmentUrl,
  inlineResultsHistoryUrl,
  scrollDataGridToTop,
} from "./inline_results_navigation.js?v=20260729-3";
import { setupQuoteFieldComboboxes } from "../components/quote_comboboxes.js?v=20260728-1";
import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";

const QUOTE_CURRENCIES = new Set(["CNY", "USD", "EUR"]);

const jsonObject = (raw) => {
  try {
    const value = typeof raw === "string" ? JSON.parse(raw) : raw;
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (_error) {
    return null;
  }
};

const quoteMutationUrl = (value, quoteId, action) => {
  const expected = `/quotes/${quoteId}/${action}`;
  return typeof value === "string" && value === expected ? value : "";
};

const quoteText = (value) => (value == null ? "" : String(value));

export const parseQuoteEditPayload = (raw) => {
  const value = jsonObject(raw);
  if (!value) return null;
  const id = Number(value.id);
  const version = Number(value.version);
  if (!Number.isInteger(id) || id <= 0 || !Number.isInteger(version) || version < 0) return null;
  const editUrl = quoteMutationUrl(value.edit_url, id, "edit");
  if (!editUrl) return null;
  const deleteUrl = value.delete_url == null ? "" : quoteMutationUrl(value.delete_url, id, "delete");
  if (value.delete_url != null && !deleteUrl) return null;
  return {
    id,
    version,
    customer_name: quoteText(value.customer_name),
    bld_no: quoteText(value.bld_no),
    customer_product_code: quoteText(value.customer_product_code),
    tax_price: value.tax_price == null ? "" : quoteText(value.tax_price),
    net_price: value.net_price == null ? "" : quoteText(value.net_price),
    currency: QUOTE_CURRENCIES.has(value.currency) ? value.currency : "CNY",
    quote_date: quoteText(value.quote_date),
    remark: quoteText(value.remark),
    edit_url: editUrl,
    delete_url: deleteUrl,
  };
};

export const quoteDeleteConfirmation = (payload) => (
  `确认删除这条报价记录（${payload.customer_name} / ${payload.bld_no} / ${payload.quote_date}）？删除后不能恢复。`
);

export const parseQuoteFilterState = (raw) => {
  const value = jsonObject(raw) || {};
  const rawOptions = jsonObject(value.options) || {};
  const rawSelected = jsonObject(value.selected) || {};
  const options = {};
  const selected = {};
  Object.entries(rawOptions).forEach(([key, entries]) => {
    if (!Array.isArray(entries)) return;
    options[key] = entries
      .filter((entry) => entry && typeof entry === "object" && !Array.isArray(entry))
      .map((entry) => ({
        value: quoteText(entry.value),
        label: quoteText(entry.label),
        count: Number.isFinite(Number(entry.count)) ? Number(entry.count) : 0,
      }));
  });
  Object.entries(rawSelected).forEach(([key, entries]) => {
    if (Array.isArray(entries)) selected[key] = entries.map(quoteText);
  });
  return { options, selected };
};

export const quoteFilterOptionState = (state, key) => {
  const options = Array.isArray(state?.options?.[key]) ? state.options[key] : [];
  const selected = Array.isArray(state?.selected?.[key]) ? state.selected[key] : [];
  const selectedValues = new Set(selected);
  return options.map((option) => ({
    ...option,
    checked: selected.length === 0 || selectedValues.has(option.value),
  }));
};

if (typeof document !== "undefined" && document.body?.dataset.page === "quotes.list") {
  const resultsHost = document.querySelector("[data-quote-results-host]");
  const requestGate = createInlineResultsRequestGate();
  let requestController = null;
  let cleanupQuoteTable = () => {};
  const editDialog = document.querySelector("#quote-edit-dialog");
  const editForm = editDialog?.querySelector("[data-quote-edit-form]");
  let quoteEditTrigger = null;

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

  const resetQuoteEdit = () => {
    if (!(editForm instanceof HTMLFormElement)) return;
    editForm.reset();
    editForm.setAttribute("action", "/quotes");
    editForm.querySelector("[data-quote-edit-save]")?.setAttribute("disabled", "");
    const deleteButton = editForm.querySelector("[data-quote-edit-delete]");
    if (deleteButton instanceof HTMLButtonElement) {
      deleteButton.disabled = true;
      deleteButton.removeAttribute("formaction");
      deleteButton.removeAttribute("data-confirm");
    }
  };

  const closeQuoteEdit = ({ restoreFocus = true } = {}) => {
    if (!(editDialog instanceof HTMLDialogElement) || !editDialog.open) return;
    if (!restoreFocus) quoteEditTrigger = null;
    editDialog.close();
  };

  const setQuoteEditValue = (name, value) => {
    if (!(editForm instanceof HTMLFormElement)) return;
    const field = editForm.elements.namedItem(name);
    if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement) {
      field.value = value;
    }
  };

  const openQuoteEdit = (button) => {
    if (!(editDialog instanceof HTMLDialogElement) || !(editForm instanceof HTMLFormElement)) return;
    const payload = parseQuoteEditPayload(button.dataset.quoteEditRecord || "");
    if (!payload) return;
    editForm.setAttribute("action", payload.edit_url);
    setQuoteEditValue("version", String(payload.version));
    setQuoteEditValue("customer_name", payload.customer_name);
    setQuoteEditValue("bld_no", payload.bld_no);
    setQuoteEditValue("customer_product_code", payload.customer_product_code);
    setQuoteEditValue("tax_price", payload.tax_price);
    setQuoteEditValue("net_price", payload.net_price);
    setQuoteEditValue("currency", payload.currency);
    setQuoteEditValue("quote_date", payload.quote_date);
    setQuoteEditValue("remark", payload.remark);
    const saveButton = editForm.querySelector("[data-quote-edit-save]");
    if (saveButton instanceof HTMLButtonElement) saveButton.disabled = false;
    const deleteButton = editForm.querySelector("[data-quote-edit-delete]");
    if (deleteButton instanceof HTMLButtonElement) {
      deleteButton.disabled = !payload.delete_url;
      if (payload.delete_url) {
        deleteButton.setAttribute("formaction", payload.delete_url);
        deleteButton.dataset.confirm = quoteDeleteConfirmation(payload);
      } else {
        deleteButton.removeAttribute("formaction");
        deleteButton.removeAttribute("data-confirm");
      }
    }
    quoteEditTrigger = button;
    if (!editDialog.open) editDialog.showModal();
    editForm.elements.namedItem("customer_name")?.focus();
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

  if (editDialog instanceof HTMLDialogElement) {
    setupQuoteFieldComboboxes(editDialog);
    editDialog.querySelectorAll("[data-close-quote-edit]").forEach((button) => {
      button.addEventListener("click", () => closeQuoteEdit());
    });
    editDialog.addEventListener("click", (event) => {
      if (event.target === editDialog) closeQuoteEdit();
    });
    editDialog.addEventListener("close", () => {
      const trigger = quoteEditTrigger;
      quoteEditTrigger = null;
      resetQuoteEdit();
      if (trigger?.isConnected) trigger.focus();
    });
  }

  const hydrateQuoteFilterPanel = (panel, state) => {
    if (!(panel instanceof HTMLElement) || panel.dataset.quoteFilterHydrated === "1") return;
    const container = panel.querySelector("[data-quote-filter-options]");
    if (!(container instanceof HTMLElement)) return;
    const options = quoteFilterOptionState(state, panel.dataset.quoteFilterOptionsKey || "");
    const fragment = document.createDocumentFragment();
    options.forEach((option) => {
      const label = document.createElement("label");
      label.className = "data-grid-filter-option";
      label.dataset.columnFilterOption = "";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = option.value;
      input.checked = option.checked;
      input.dataset.initialChecked = String(option.checked);
      const text = document.createElement("span");
      text.textContent = option.label;
      const count = document.createElement("small");
      count.textContent = String(option.count);
      label.append(input, text, count);
      fragment.appendChild(label);
    });
    container.replaceChildren(fragment);
    const empty = panel.querySelector("[data-quote-filter-empty]");
    if (empty instanceof HTMLElement) empty.hidden = options.length > 0;
    panel.dataset.quoteFilterHydrated = "1";
  };

  const setupLazyQuoteFilters = (table, filterPortal) => {
    if (!(table instanceof HTMLTableElement) || !(filterPortal instanceof HTMLElement)) return () => {};
    const results = table.closest("[data-quote-results]");
    const state = parseQuoteFilterState(results?.dataset.quoteFilterState || "");
    const hydrateTriggeredPanel = (event) => {
      if (!(event.target instanceof Element)) return;
      const trigger = event.target.closest("[data-column-filter-trigger]");
      if (!(trigger instanceof HTMLButtonElement)) return;
      const panelId = trigger.getAttribute("aria-controls");
      hydrateQuoteFilterPanel(panelId ? document.getElementById(panelId) : null, state);
    };
    filterPortal.addEventListener("click", hydrateTriggeredPanel, true);
    return () => filterPortal.removeEventListener("click", hydrateTriggeredPanel, true);
  };

  const initializeResults = () => {
    cleanupQuoteTable();
    const table = resultsHost?.querySelector("#quotes-table");
    const filterPortal = table?.closest("[data-quote-results]");
    const cleanupLazyQuoteFilters = setupLazyQuoteFilters(table, filterPortal);
    const cleanupDataGridControls = setupDataGridControls(table, {
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
      filterPortal,
      resetFilterParams: [
        "qf_quote_no", "qf_quote_date", "qf_customer_name", "qf_bld_no",
        "qf_customer_product_code", "qf_tax_price", "qf_net_price", "qf_currency",
        "qf_quoted_by", "qf_source_type", "qf_remark",
      ],
    });
    cleanupQuoteTable = () => {
      cleanupDataGridControls();
      cleanupLazyQuoteFilters();
    };
    setupQuoteFieldComboboxes(resultsHost || document);
    resultsHost?.querySelector("[data-quote-search-form]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadResults(formGetUrl(event.currentTarget, window.location.href));
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

      closeQuoteEdit({ restoreFocus: false });
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
    const editButton = event.target.closest("[data-open-quote-edit]");
    if (editButton instanceof HTMLButtonElement) {
      openQuoteEdit(editButton);
      return;
    }
    const quoteNumberButton = event.target.closest("[data-quote-number-url]");
    if (quoteNumberButton instanceof HTMLButtonElement) {
      openQuoteNumber(quoteNumberButton);
      return;
    }
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
