import { createInquiryRequestGate } from "./inquiry_result_rules.js?v=20260808-1";

export const setupInquiryManualMapping = (root) => {
  const doc = root.nodeType === 9 ? root : root.ownerDocument;
  const win = doc.defaultView;
  const modal = root.querySelector("#map-oe-modal");
  const form = modal?.querySelector("[data-map-oe-form]");
  if (!modal || !form) return;

  const requestGate = createInquiryRequestGate();
  let trigger = null;
  let searchTimer = null;
  const field = (selector) => form.querySelector(selector);

  const showError = (message) => {
    const error = field("[data-map-oe-error]");
    if (!(error instanceof HTMLElement)) return;
    error.textContent = message || "";
    error.hidden = !message;
  };

  const clearSelection = () => {
    const bldField = field("[data-map-oe-bld]");
    const selected = field("[data-map-oe-selected]");
    if (bldField instanceof HTMLInputElement) bldField.value = "";
    if (selected instanceof HTMLElement) {
      selected.textContent = "";
      selected.hidden = true;
    }
  };

  const hideResults = () => {
    const results = field("[data-map-oe-results]");
    if (results instanceof HTMLElement) {
      results.replaceChildren();
      results.hidden = true;
    }
  };

  const selectProduct = (product) => {
    const bldField = field("[data-map-oe-bld]");
    const selected = field("[data-map-oe-selected]");
    const search = field("[data-map-oe-search]");
    if (bldField instanceof HTMLInputElement) bldField.value = product.bld_no;
    if (selected instanceof HTMLElement) {
      const detail = [product.item, product.series].filter(Boolean).join(" / ");
      selected.textContent = `已选择：${product.bld_no}${detail ? `（${detail}）` : ""}`;
      selected.hidden = false;
    }
    if (search instanceof HTMLInputElement) search.value = product.bld_no;
    hideResults();
    showError("");
  };

  const renderResults = (products) => {
    const results = field("[data-map-oe-results]");
    if (!(results instanceof HTMLElement)) return;
    results.replaceChildren();
    if (!products.length) {
      const empty = doc.createElement("p");
      empty.className = "map-oe-results-empty";
      empty.textContent = "没有匹配的产品。";
      results.append(empty);
    } else {
      products.forEach((product) => {
        const option = doc.createElement("button");
        option.type = "button";
        option.className = "map-oe-result";
        const code = doc.createElement("strong");
        code.textContent = product.bld_no;
        option.append(code);
        const detail = [product.item, product.series].filter(Boolean).join(" / ");
        if (detail) {
          const label = doc.createElement("span");
          label.textContent = detail;
          option.append(label);
        }
        option.addEventListener("click", () => selectProduct(product));
        results.append(option);
      });
    }
    results.hidden = false;
  };

  const searchProducts = async (query) => {
    const sequence = requestGate.begin();
    const url = new URL(form.dataset.mapOeLookupUrl, win.location.origin);
    url.searchParams.set("q", query);
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const products = response.ok ? await response.json() : [];
      if (!requestGate.isCurrent(sequence)) return;
      renderResults(Array.isArray(products) ? products : []);
    } catch (_error) {
      if (!requestGate.isCurrent(sequence)) return;
      hideResults();
    }
  };

  const close = () => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    doc.body.classList.remove("modal-open");
    if (trigger instanceof HTMLElement) trigger.focus();
    trigger = null;
  };

  const open = (code, sourceTrigger) => {
    trigger = sourceTrigger;
    form.reset();
    const source = field("[data-map-oe-source]");
    if (source instanceof HTMLInputElement) source.value = code;
    clearSelection();
    hideResults();
    showError("");
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    doc.body.classList.add("modal-open");
    if (source instanceof HTMLInputElement) {
      source.focus();
      source.select();
    }
  };

  doc.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-map-oe-code]") : null;
    if (button instanceof HTMLElement) open(button.dataset.mapOeCode || "", button);
  });
  modal.querySelectorAll("[data-close-map-oe-modal]").forEach((element) => {
    element.addEventListener("click", close);
  });

  const searchInput = field("[data-map-oe-search]");
  if (searchInput instanceof HTMLInputElement) {
    searchInput.addEventListener("input", () => {
      clearSelection();
      win.clearTimeout(searchTimer);
      requestGate.invalidate();
      const query = searchInput.value.trim();
      if (!query) {
        hideResults();
        return;
      }
      searchTimer = win.setTimeout(() => searchProducts(query), 300);
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = field("[data-map-oe-submit]");
    if (submitButton instanceof HTMLButtonElement) submitButton.disabled = true;
    showError("");
    try {
      const response = await fetch(form.dataset.mapOeSubmitUrl, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload || payload.ok !== true) {
        showError(payload?.error || "保存失败，请稍后重试。");
        return;
      }
      if (trigger instanceof HTMLButtonElement) {
        trigger.disabled = true;
        trigger.classList.add("map-oe-code-added");
        trigger.textContent = `${trigger.dataset.mapOeCode}（已加入）`;
        trigger = null;
      }
      close();
    } catch (_error) {
      showError("网络错误，请稍后重试。");
    } finally {
      if (submitButton instanceof HTMLButtonElement) submitButton.disabled = false;
    }
  });

  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal.classList.contains("open")) close();
  });
};
