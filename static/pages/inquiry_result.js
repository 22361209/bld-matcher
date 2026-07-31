import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";
import { setupQuoteFieldComboboxes } from "../components/quote_comboboxes.js?v=20260728-1";

const MAX_INQUIRY_PRICE = 99999999.99;
const MODAL_FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export const validateInquiryPrice = (rawValue, { allowBlank = false } = {}) => {
  const text = String(rawValue ?? "").trim();
  if (!text) {
    return allowBlank
      ? { valid: true, normalized: "", error: "" }
      : { valid: false, normalized: "", error: "请填写本次报价含税单价。" };
  }
  const value = Number(text);
  if (!Number.isFinite(value)) {
    return { valid: false, normalized: "", error: "含税单价必须是有效数字。" };
  }
  if (value < 0 || value > MAX_INQUIRY_PRICE) {
    return { valid: false, normalized: "", error: `含税单价必须在 0 到 ${MAX_INQUIRY_PRICE.toFixed(2)} 之间。` };
  }
  const cents = value * 100;
  if (Math.abs(cents - Math.round(cents)) > 1e-7) {
    return { valid: false, normalized: "", error: "含税单价最多保留两位小数。" };
  }
  return { valid: true, normalized: (Math.round(cents) / 100).toFixed(2), error: "" };
};

export const inquiryPriceAdjustment = (rawValue, catalogPrice, { allowBlank = false } = {}) => {
  const validation = validateInquiryPrice(rawValue, { allowBlank });
  if (!validation.valid) return { ...validation, override: undefined };
  if (!validation.normalized) return { ...validation, override: null };
  const catalogText = String(catalogPrice ?? "").trim();
  if (!catalogText) return { ...validation, override: validation.normalized };
  const catalogValidation = validateInquiryPrice(catalogText);
  const override = catalogValidation.valid && catalogValidation.normalized === validation.normalized
    ? null
    : validation.normalized;
  return { ...validation, override };
};

export const createInquiryRequestGate = () => {
  let sequence = 0;
  return {
    begin: () => ++sequence,
    invalidate: () => ++sequence,
    isCurrent: (candidate) => candidate === sequence,
  };
};

export const inquiryProductDisplay = (product = {}) => {
  const bldNo = String(product.bld_no || "").trim();
  const item = String(product.item || "").trim();
  const status = String(product.product_status || "").trim() || "产品状态未填写";
  const price = product.price_cny === null || product.price_cny === undefined || product.price_cny === ""
    ? "目录含税价未填写"
    : `目录含税价 ¥${Number(product.price_cny).toFixed(2)}`;
  return { bldNo, item, status, price };
};

export const inquiryTargetAdjustment = (targetBldNo, defaultBldNo) => {
  const target = String(targetBldNo || "").trim();
  const original = String(defaultBldNo || "").trim();
  if (!target || target.toUpperCase() === original.toUpperCase()) return null;
  return target;
};

export const inquiryAttachmentFilename = (response) => {
  if (!response?.ok || response.redirected || typeof response.headers?.get !== "function") return null;
  const disposition = response.headers.get("Content-Disposition") || "";
  if (!/\battachment\b/i.test(disposition)) return null;
  const encoded = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
  if (encoded) {
    try {
      const filename = decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
      return /\.xlsx?$/i.test(filename) ? filename : null;
    } catch {
      return null;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = plain ? plain[1].trim() : "";
  return /\.xlsx?$/i.test(filename) ? filename : null;
};

export const clearQuoteCustomerValidity = (input) => {
  if (input && typeof input.setCustomValidity === "function") input.setCustomValidity("");
};

export function setupInquiryResultPage(root = document) {
setupQuoteFieldComboboxes(root);

setupDataGridControls(root.querySelector(".data-grid[data-grid-key='inquiry-result'] table.data-table"), {
  columns: ["row", "oe", "customer-code", "bld", "price", "status", "score", "reason"],
  storagePrefix: "bld.inquiry-result",
});

const inquiryAdjustments = new Map();
const adjustmentFields = root.querySelectorAll("[data-inquiry-adjustments-field]");

const syncInquiryAdjustments = () => {
  const payload = Object.fromEntries(inquiryAdjustments);
  adjustmentFields.forEach((field) => {
    if (field instanceof HTMLInputElement) field.value = JSON.stringify(payload);
  });
};

const persistInquiryAdjustment = (row, adjustment) => {
  const key = row.dataset.adjustmentKey;
  if (!key) return;
  if (adjustment.target_bld_no || adjustment.tax_price !== undefined) {
    adjustment.expected_bld_no = row.dataset.defaultBld || "";
    inquiryAdjustments.set(key, adjustment);
  } else {
    inquiryAdjustments.delete(key);
  }
};

const inquiryPriceState = (row, input) => inquiryPriceAdjustment(
  input.value,
  row.dataset.catalogPrice,
  { allowBlank: row.dataset.priceTouched !== "1" && !input.value.trim() },
);

const setInquiryPriceError = (input, validation, { reveal = true } = {}) => {
  const row = input.closest("[data-inquiry-result-row]");
  const error = row?.querySelector("[data-inquiry-price-error]");
  const message = validation.valid ? "" : validation.error;
  input.setCustomValidity(message);
  input.setAttribute("aria-invalid", String(!validation.valid));
  if (error instanceof HTMLElement) {
    error.textContent = reveal ? message : "";
    error.hidden = !reveal || !message;
  }
};

const setInquiryRowState = (row) => {
  const adjustment = inquiryAdjustments.get(row.dataset.adjustmentKey) || {};
  const priceInput = row.querySelector("[data-inquiry-tax-price]");
  const priceReset = row.querySelector("[data-reset-inquiry-price]");
  const productReset = row.querySelector("[data-reset-inquiry-product]");
  const priceState = priceInput instanceof HTMLInputElement ? inquiryPriceState(row, priceInput) : null;
  if (priceReset instanceof HTMLButtonElement) {
    priceReset.hidden = adjustment.tax_price === undefined && !(priceState && !priceState.valid);
  }
  if (productReset instanceof HTMLButtonElement) productReset.hidden = !adjustment.target_bld_no;
  row.classList.toggle("inquiry-row-adjusted", Boolean(adjustment.target_bld_no || adjustment.tax_price !== undefined));
  row.classList.toggle("inquiry-row-invalid", Boolean(priceState && !priceState.valid));
  syncInquiryAdjustments();
};

const updateInquiryPrice = (row, { revealError = true } = {}) => {
  const priceInput = row.querySelector("[data-inquiry-tax-price]");
  if (!(priceInput instanceof HTMLInputElement)) return true;
  const priceState = inquiryPriceState(row, priceInput);
  const current = { ...(inquiryAdjustments.get(row.dataset.adjustmentKey) || {}) };
  if (priceState.valid) {
    if (priceState.override === null) delete current.tax_price;
    else current.tax_price = priceState.override;
  } else {
    delete current.tax_price;
  }
  persistInquiryAdjustment(row, current);
  setInquiryPriceError(priceInput, priceState, { reveal: revealError });
  setInquiryRowState(row);
  return priceState.valid;
};

root.querySelectorAll("[data-inquiry-result-row][data-adjustment-key]").forEach((row) => {
  const priceInput = row.querySelector("[data-inquiry-tax-price]");
  row.dataset.catalogPrice = row.dataset.defaultPrice || "";
  row.dataset.priceTouched = "0";
  if (priceInput instanceof HTMLInputElement) {
    priceInput.addEventListener("input", () => {
      row.dataset.priceTouched = "1";
      updateInquiryPrice(row);
    });
    priceInput.addEventListener("change", () => {
      const validation = validateInquiryPrice(priceInput.value, {
        allowBlank: row.dataset.priceTouched !== "1" && !priceInput.value.trim(),
      });
      if (validation.valid && validation.normalized) priceInput.value = validation.normalized;
      updateInquiryPrice(row);
    });
  }
  row.querySelector("[data-reset-inquiry-price]")?.addEventListener("click", () => {
    if (!(priceInput instanceof HTMLInputElement)) return;
    const current = { ...(inquiryAdjustments.get(row.dataset.adjustmentKey) || {}) };
    delete current.tax_price;
    persistInquiryAdjustment(row, current);
    const catalogPrice = String(row.dataset.catalogPrice || "").trim();
    priceInput.value = catalogPrice ? Number(catalogPrice).toFixed(2) : "";
    row.dataset.priceTouched = "0";
    setInquiryPriceError(priceInput, validateInquiryPrice(priceInput.value, { allowBlank: true }), { reveal: false });
    setInquiryRowState(row);
    priceInput.focus();
  });
  row.querySelector("[data-reset-inquiry-product]")?.addEventListener("click", () => {
    inquiryAdjustments.delete(row.dataset.adjustmentKey);
    const currentBld = row.querySelector("[data-current-bld]");
    const status = row.querySelector("[data-col='status']");
    if (currentBld) currentBld.textContent = row.dataset.defaultBld || "";
    if (status) status.textContent = row.dataset.defaultStatus ?? "";
    row.dataset.catalogPrice = row.dataset.defaultPrice || "";
    row.dataset.priceTouched = "0";
    if (priceInput instanceof HTMLInputElement) {
      priceInput.value = row.dataset.defaultPrice ? Number(row.dataset.defaultPrice).toFixed(2) : "";
      setInquiryPriceError(priceInput, validateInquiryPrice(priceInput.value, { allowBlank: true }), { reveal: false });
    }
    setInquiryRowState(row);
    if (priceInput instanceof HTMLInputElement) priceInput.focus();
    else row.querySelector("[data-open-product-adjustment]")?.focus();
  });
  setInquiryRowState(row);
});

const validateAllInquiryPrices = () => {
  let firstInvalid = null;
  root.querySelectorAll("[data-inquiry-result-row][data-adjustment-key]").forEach((row) => {
    if (!(row instanceof HTMLTableRowElement)) return;
    if (!updateInquiryPrice(row) && firstInvalid === null) {
      firstInvalid = row.querySelector("[data-inquiry-tax-price]");
    }
  });
  if (firstInvalid instanceof HTMLInputElement) {
    if (downloadModal?.classList.contains("open")) closeDownloadModal();
    firstInvalid.focus();
    firstInvalid.scrollIntoView({ block: "center", inline: "nearest" });
    firstInvalid.reportValidity();
    return false;
  }
  return true;
};

const productAdjustmentModal = root.querySelector("#product-adjustment-modal");
const productAdjustmentForm = productAdjustmentModal?.querySelector("[data-product-adjustment-form]");
const productAdjustmentGate = createInquiryRequestGate();
let productAdjustmentRow = null;
let productAdjustmentProduct = null;
let productAdjustmentTimer = null;
let productAdjustmentTrigger = null;

const productAdjustmentField = (selector) => productAdjustmentForm?.querySelector(selector) || null;

const setProductAdjustmentError = (message) => {
  const error = productAdjustmentField("[data-product-adjustment-error]");
  if (!(error instanceof HTMLElement)) return;
  error.textContent = message || "";
  error.hidden = !message;
};

const setProductAdjustmentResults = (message = "") => {
  const results = productAdjustmentField("[data-product-adjustment-results]");
  const search = productAdjustmentField("[data-product-adjustment-search]");
  if (!(results instanceof HTMLElement)) return;
  results.replaceChildren();
  if (message) {
    const state = document.createElement("p");
    state.className = "map-oe-results-empty";
    state.textContent = message;
    results.append(state);
    results.hidden = false;
  } else {
    results.hidden = true;
  }
  if (search instanceof HTMLInputElement) search.setAttribute("aria-expanded", String(!results.hidden));
};

const clearAdjustmentProductSelection = () => {
  productAdjustmentProduct = null;
  const field = productAdjustmentField("[data-product-adjustment-bld]");
  const selected = productAdjustmentField("[data-product-adjustment-selected]");
  const submit = productAdjustmentField("[data-product-adjustment-submit]");
  if (field instanceof HTMLInputElement) field.value = "";
  if (selected instanceof HTMLElement) {
    selected.textContent = "";
    selected.hidden = true;
  }
  if (submit instanceof HTMLButtonElement) submit.disabled = true;
};

const closeProductAdjustment = () => {
  if (!productAdjustmentModal) return;
  window.clearTimeout(productAdjustmentTimer);
  productAdjustmentGate.invalidate();
  productAdjustmentModal.classList.remove("open");
  productAdjustmentModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  if (productAdjustmentTrigger instanceof HTMLElement && productAdjustmentTrigger.isConnected) {
    productAdjustmentTrigger.focus();
  }
  productAdjustmentTrigger = null;
  productAdjustmentRow = null;
  productAdjustmentProduct = null;
};

const trapProductAdjustmentFocus = (event) => {
  const panel = productAdjustmentModal?.querySelector("[data-product-adjustment-panel]");
  if (!(panel instanceof HTMLElement)) return;
  const focusable = Array.from(panel.querySelectorAll(MODAL_FOCUSABLE_SELECTOR)).filter(
    (element) => element instanceof HTMLElement && !element.closest("[hidden]") && !element.hasAttribute("disabled"),
  );
  if (!focusable.length) {
    event.preventDefault();
    panel.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!panel.contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
};

const chooseAdjustmentProduct = (product) => {
  if (!productAdjustmentForm) return;
  productAdjustmentProduct = product;
  const field = productAdjustmentField("[data-product-adjustment-bld]");
  const search = productAdjustmentField("[data-product-adjustment-search]");
  const selected = productAdjustmentField("[data-product-adjustment-selected]");
  const submit = productAdjustmentField("[data-product-adjustment-submit]");
  const display = inquiryProductDisplay(product);
  if (field instanceof HTMLInputElement) field.value = display.bldNo;
  if (search instanceof HTMLInputElement) search.value = display.bldNo;
  if (selected instanceof HTMLElement) {
    const name = display.item ? ` · ${display.item}` : "";
    selected.textContent = `已选择：${display.bldNo}${name} · ${display.status} · ${display.price}`;
    selected.hidden = false;
  }
  setProductAdjustmentResults();
  setProductAdjustmentError("");
  if (submit instanceof HTMLButtonElement) {
    submit.disabled = false;
    submit.focus();
  }
};

const renderAdjustmentProducts = (products) => {
  const results = productAdjustmentField("[data-product-adjustment-results]");
  const search = productAdjustmentField("[data-product-adjustment-search]");
  if (!(results instanceof HTMLElement)) return;
  results.replaceChildren();
  if (!products.length) {
    setProductAdjustmentResults("没有匹配的启用产品。");
    return;
  }
  products.forEach((product) => {
    const display = inquiryProductDisplay(product);
    const option = document.createElement("button");
    option.type = "button";
    option.className = "map-oe-result inquiry-product-option";
    const code = document.createElement("strong");
    code.textContent = display.bldNo;
    option.append(code);
    if (display.item) {
      const item = document.createElement("span");
      item.textContent = display.item;
      option.append(item);
    }
    const meta = document.createElement("span");
    meta.className = "inquiry-product-option-meta";
    meta.textContent = `${display.status} · ${display.price}`;
    option.append(meta);
    option.addEventListener("click", () => chooseAdjustmentProduct(product));
    results.append(option);
  });
  results.hidden = false;
  if (search instanceof HTMLInputElement) search.setAttribute("aria-expanded", "true");
};

const searchAdjustmentProducts = async (query) => {
  if (!productAdjustmentForm) return;
  const sequence = productAdjustmentGate.begin();
  const url = new URL(productAdjustmentForm.dataset.productLookupUrl, window.location.origin);
  url.searchParams.set("q", query);
  url.searchParams.set("details", "1");
  url.searchParams.set("active_only", "1");
  try {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    });
    const payload = await response.json().catch(() => null);
    if (!productAdjustmentGate.isCurrent(sequence)) return;
    if (!response.ok || !Array.isArray(payload)) {
      throw new Error(payload?.error || "产品候选加载失败，请稍后重试。");
    }
    renderAdjustmentProducts(payload);
  } catch (error) {
    if (!productAdjustmentGate.isCurrent(sequence)) return;
    setProductAdjustmentResults();
    setProductAdjustmentError(error instanceof Error ? error.message : "产品候选加载失败，请稍后重试。");
  }
};

root.querySelectorAll("[data-open-product-adjustment]").forEach((button) => {
  button.addEventListener("click", () => {
    productAdjustmentRow = button.closest("[data-inquiry-result-row]");
    if (!productAdjustmentModal || !productAdjustmentForm || !productAdjustmentRow) return;
    productAdjustmentTrigger = button;
    window.clearTimeout(productAdjustmentTimer);
    productAdjustmentGate.invalidate();
    productAdjustmentForm.reset();
    clearAdjustmentProductSelection();
    setProductAdjustmentResults();
    setProductAdjustmentError("");
    const current = productAdjustmentField("[data-product-adjustment-current]");
    const currentBld = productAdjustmentRow.querySelector("[data-current-bld]")?.textContent?.trim() || "";
    const search = productAdjustmentField("[data-product-adjustment-search]");
    if (current) current.textContent = currentBld;
    productAdjustmentModal.classList.add("open");
    productAdjustmentModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    search?.focus();
  });
});

productAdjustmentField("[data-product-adjustment-search]")?.addEventListener("input", (event) => {
  const search = event.currentTarget;
  if (!(search instanceof HTMLInputElement)) return;
  window.clearTimeout(productAdjustmentTimer);
  productAdjustmentGate.invalidate();
  clearAdjustmentProductSelection();
  setProductAdjustmentError("");
  const query = search.value.trim();
  if (!query) {
    setProductAdjustmentResults();
    return;
  }
  setProductAdjustmentResults("正在搜索启用产品...");
  productAdjustmentTimer = window.setTimeout(() => searchAdjustmentProducts(query), 180);
});

productAdjustmentForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!productAdjustmentRow || !productAdjustmentProduct) {
    setProductAdjustmentError("请先从候选结果中选择一个产品。");
    productAdjustmentField("[data-product-adjustment-search]")?.focus();
    return;
  }
  const current = { ...(inquiryAdjustments.get(productAdjustmentRow.dataset.adjustmentKey) || {}) };
  const targetBldNo = inquiryTargetAdjustment(
    productAdjustmentProduct.bld_no,
    productAdjustmentRow.dataset.defaultBld,
  );
  if (targetBldNo) current.target_bld_no = targetBldNo;
  else delete current.target_bld_no;
  delete current.tax_price;
  persistInquiryAdjustment(productAdjustmentRow, current);
  const currentBld = productAdjustmentRow.querySelector("[data-current-bld]");
  const status = productAdjustmentRow.querySelector("[data-col='status']");
  const price = productAdjustmentRow.querySelector("[data-inquiry-tax-price]");
  if (currentBld) currentBld.textContent = productAdjustmentProduct.bld_no;
  if (status) status.textContent = productAdjustmentProduct.product_status || "";
  const catalogPrice = productAdjustmentProduct.price_cny ?? "";
  productAdjustmentRow.dataset.catalogPrice = String(catalogPrice);
  productAdjustmentRow.dataset.priceTouched = "0";
  if (price instanceof HTMLInputElement) {
    price.value = catalogPrice === "" ? "" : Number(catalogPrice).toFixed(2);
    setInquiryPriceError(price, validateInquiryPrice(price.value, { allowBlank: true }), { reveal: false });
  }
  setInquiryRowState(productAdjustmentRow);
  closeProductAdjustment();
});

root.querySelectorAll("[data-close-product-adjustment]").forEach((element) => element.addEventListener("click", closeProductAdjustment));

document.querySelectorAll("[data-price-mode]").forEach((select) => {
  const form = select.closest("form");
  const rateField = form ? form.querySelector("[data-exchange-rate-field]") : null;
  const rateInput = form ? form.querySelector("[data-exchange-rate]") : null;
  const syncRateField = () => {
    const needsRate = select.value === "usd";
    if (rateField instanceof HTMLElement) {
      rateField.hidden = !needsRate;
    }
    if (rateInput instanceof HTMLInputElement) {
      rateInput.disabled = !needsRate;
      rateInput.required = needsRate;
    }
  };

  select.addEventListener("change", syncRateField);
  syncRateField();
});


const downloadModal = document.querySelector("#download-excel-modal");

const closeDownloadModal = () => {
  if (!downloadModal) return;
  downloadModal.classList.remove("open");
  downloadModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
};

document.querySelectorAll("[data-open-download-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!downloadModal) return;
    downloadModal.classList.add("open");
    downloadModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    const select = downloadModal.querySelector("[data-price-mode]");
    if (select instanceof HTMLElement) {
      select.focus();
    }
  });
});

document.querySelectorAll("[data-close-download-modal]").forEach((element) => {
  element.addEventListener("click", closeDownloadModal);
});

const triggerBlobDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
};

const inquiryDownloadForm = root.querySelector("[data-inquiry-download-form]");
inquiryDownloadForm?.addEventListener("submit", (event) => {
  if (validateAllInquiryPrices()) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}, { capture: true });

root.querySelectorAll("[data-download-only-submit]").forEach((button) => {
  button.addEventListener("click", () => {
    const customerInput = button.closest("form")?.querySelector("input[name='customer_name']");
    clearQuoteCustomerValidity(customerInput);
  });
});

root.querySelectorAll("[data-write-quotes-submit]").forEach((button) => {
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    const form = button.closest("form");
    if (!(form instanceof HTMLFormElement) || !(button instanceof HTMLButtonElement)) return;
    if (!validateAllInquiryPrices()) return;
    const customerInput = form.querySelector("input[name='customer_name']");
    if (!(customerInput instanceof HTMLInputElement)) return;
    clearQuoteCustomerValidity(customerInput);
    if (!customerInput.value.trim()) {
      customerInput.setCustomValidity("写入报价前请填写客户名称。");
      customerInput.reportValidity();
      customerInput.focus();
      customerInput.addEventListener("input", () => customerInput.setCustomValidity(""), { once: true });
      return;
    }
    if (!form.reportValidity()) return;

    const message = form.querySelector("[data-submit-wait-message]");
    const showError = (text, { allowRetry = true, quotesUrl = "" } = {}) => {
      if (message instanceof HTMLElement) {
        message.replaceChildren(document.createTextNode(text));
        if (quotesUrl) {
          const link = document.createElement("a");
          link.href = quotesUrl;
          link.textContent = "前往报价记录";
          message.append(" ", link);
        }
        message.classList.add("active", "error");
        message.classList.remove("done");
      }
      button.disabled = !allowRetry;
    };

    button.disabled = true;
    const writeUrl = button.formAction;
    const body = new FormData(form);
    const showWait = (text) => {
      if (message instanceof HTMLElement) {
        message.textContent = text;
        message.classList.add("active");
        message.classList.remove("done", "error");
      }
    };

    showWait("正在生成 Excel 并写入报价记录...");
    try {
      const response = await fetch(writeUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload || payload.ok !== true) {
        showError(payload?.error || "写入报价失败，未生成下载文件；请稍后重试。");
        return;
      }

      showWait("报价已写入，正在下载本次唯一附件...");
      try {
        const downloadResponse = await fetch(payload.download_url, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "fetch" },
        });
        const filename = inquiryAttachmentFilename(downloadResponse);
        if (!filename) throw new Error("attachment-download-failed");
        triggerBlobDownload(await downloadResponse.blob(), filename);
      } catch (_error) {
        showError("报价已写入，但附件下载失败；请前往报价记录重新下载。", {
          allowRetry: false,
          quotesUrl: payload.quotes_url,
        });
        return;
      }
      window.location.assign(payload.quotes_url);
    } catch (_error) {
      showError("网络错误，无法确认报价是否已写入；请先到报价记录核对后再重试。", {
        allowRetry: false,
      });
    }
  });
});


document.addEventListener("keydown", (event) => {
  if (productAdjustmentModal?.classList.contains("open")) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeProductAdjustment();
      return;
    }
    if (event.key === "Tab") {
      trapProductAdjustmentFocus(event);
      return;
    }
  }
  if (event.key === "Escape" && downloadModal?.classList.contains("open")) {
    closeDownloadModal();
  }
  if (event.key === "Escape" && mapOeModal?.classList.contains("open")) {
    closeMapOeModal();
  }
});


const mapOeModal = document.querySelector("#map-oe-modal");
const mapOeForm = mapOeModal ? mapOeModal.querySelector("[data-map-oe-form]") : null;
let mapOeTrigger = null;
let mapOeSearchTimer = null;
let mapOeSearchSequence = 0;

const mapOeField = (selector) => (mapOeForm ? mapOeForm.querySelector(selector) : null);

const showMapOeError = (message) => {
  const error = mapOeField("[data-map-oe-error]");
  if (!(error instanceof HTMLElement)) return;
  error.textContent = message || "";
  error.hidden = !message;
};

const clearMapOeSelection = () => {
  const bldField = mapOeField("[data-map-oe-bld]");
  const selected = mapOeField("[data-map-oe-selected]");
  if (bldField instanceof HTMLInputElement) {
    bldField.value = "";
  }
  if (selected instanceof HTMLElement) {
    selected.textContent = "";
    selected.hidden = true;
  }
};

const hideMapOeResults = () => {
  const results = mapOeField("[data-map-oe-results]");
  if (results instanceof HTMLElement) {
    results.replaceChildren();
    results.hidden = true;
  }
};

const selectMapOeProduct = (product) => {
  const bldField = mapOeField("[data-map-oe-bld]");
  const selected = mapOeField("[data-map-oe-selected]");
  const search = mapOeField("[data-map-oe-search]");
  if (bldField instanceof HTMLInputElement) {
    bldField.value = product.bld_no;
  }
  if (selected instanceof HTMLElement) {
    const detail = [product.item, product.series].filter(Boolean).join(" / ");
    selected.textContent = `已选择：${product.bld_no}${detail ? `（${detail}）` : ""}`;
    selected.hidden = false;
  }
  if (search instanceof HTMLInputElement) {
    search.value = product.bld_no;
  }
  hideMapOeResults();
  showMapOeError("");
};

const renderMapOeResults = (products) => {
  const results = mapOeField("[data-map-oe-results]");
  if (!(results instanceof HTMLElement)) return;
  results.replaceChildren();
  if (!products.length) {
    const empty = document.createElement("p");
    empty.className = "map-oe-results-empty";
    empty.textContent = "没有匹配的产品。";
    results.append(empty);
  } else {
    products.forEach((product) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "map-oe-result";
      const code = document.createElement("strong");
      code.textContent = product.bld_no;
      option.append(code);
      const detail = [product.item, product.series].filter(Boolean).join(" / ");
      if (detail) {
        const label = document.createElement("span");
        label.textContent = detail;
        option.append(label);
      }
      option.addEventListener("click", () => selectMapOeProduct(product));
      results.append(option);
    });
  }
  results.hidden = false;
};

const searchMapOeProducts = (query) => {
  if (!mapOeForm) return;
  const sequence = ++mapOeSearchSequence;
  const url = new URL(mapOeForm.dataset.mapOeLookupUrl, window.location.origin);
  url.searchParams.set("q", query);
  fetch(url, { headers: { Accept: "application/json", "X-Requested-With": "fetch" } })
    .then((response) => (response.ok ? response.json() : []))
    .then((products) => {
      if (sequence !== mapOeSearchSequence) return;
      renderMapOeResults(Array.isArray(products) ? products : []);
    })
    .catch(() => {
      if (sequence !== mapOeSearchSequence) return;
      hideMapOeResults();
    });
};

const closeMapOeModal = () => {
  if (!mapOeModal) return;
  mapOeModal.classList.remove("open");
  mapOeModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  if (mapOeTrigger instanceof HTMLElement) {
    mapOeTrigger.focus();
  }
  mapOeTrigger = null;
};

const openMapOeModal = (code, trigger) => {
  if (!mapOeModal || !mapOeForm) return;
  mapOeTrigger = trigger;
  mapOeForm.reset();
  const source = mapOeField("[data-map-oe-source]");
  if (source instanceof HTMLInputElement) {
    source.value = code;
  }
  clearMapOeSelection();
  hideMapOeResults();
  showMapOeError("");
  mapOeModal.classList.add("open");
  mapOeModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  if (source instanceof HTMLElement) {
    source.focus();
    source.select();
  }
};

if (mapOeModal && mapOeForm) {
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-map-oe-code]") : null;
    if (button instanceof HTMLElement) {
      openMapOeModal(button.dataset.mapOeCode || "", button);
    }
  });

  mapOeModal.querySelectorAll("[data-close-map-oe-modal]").forEach((element) => {
    element.addEventListener("click", closeMapOeModal);
  });

  const searchInput = mapOeField("[data-map-oe-search]");
  if (searchInput instanceof HTMLInputElement) {
    searchInput.addEventListener("input", () => {
      clearMapOeSelection();
      window.clearTimeout(mapOeSearchTimer);
      const query = searchInput.value.trim();
      if (!query) {
        mapOeSearchSequence += 1;
        hideMapOeResults();
        return;
      }
      mapOeSearchTimer = window.setTimeout(() => searchMapOeProducts(query), 300);
    });
  }

  mapOeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const submitButton = mapOeField("[data-map-oe-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
    }
    showMapOeError("");
    fetch(mapOeForm.dataset.mapOeSubmitUrl, {
      method: "POST",
      body: new FormData(mapOeForm),
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    })
      .then((response) =>
        response
          .json()
          .catch(() => null)
          .then((payload) => ({ ok: response.ok, payload })),
      )
      .then(({ ok, payload }) => {
        if (!ok || !payload || payload.ok !== true) {
          showMapOeError((payload && payload.error) || "保存失败，请稍后重试。");
          return;
        }
        if (mapOeTrigger instanceof HTMLButtonElement) {
          mapOeTrigger.disabled = true;
          mapOeTrigger.classList.add("map-oe-code-added");
          mapOeTrigger.textContent = `${mapOeTrigger.dataset.mapOeCode}（已加入）`;
          mapOeTrigger = null;
        }
        closeMapOeModal();
      })
      .catch(() => {
        showMapOeError("网络错误，请稍后重试。");
      })
      .finally(() => {
        if (submitButton instanceof HTMLButtonElement) {
          submitButton.disabled = false;
        }
      });
  });
}

}

if (typeof document !== "undefined") {
  setupInquiryResultPage(document);
}
