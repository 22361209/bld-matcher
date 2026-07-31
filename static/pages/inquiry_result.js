import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";
import { setupQuoteFieldComboboxes } from "../components/quote_comboboxes.js?v=20260728-1";

const MAX_INQUIRY_PRICE = 99999999.99;

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

export const inquiryBldSelectionState = (rawValue, currentBldNo, { confirmed = true } = {}) => {
  const value = String(rawValue || "").trim();
  const current = String(currentBldNo || "").trim();
  const valid = Boolean(confirmed && value && current && value.toUpperCase() === current.toUpperCase());
  return {
    valid,
    error: valid ? "" : "请从启用产品候选中选择 BLD NO.。",
  };
};

export const rankInquiryProducts = (products, rawQuery) => {
  const query = String(rawQuery || "").trim().toUpperCase();
  const score = (product) => {
    const bldNo = String(product?.bld_no || "").trim().toUpperCase();
    if (!query || !bldNo) return 3;
    if (bldNo === query) return 0;
    if (bldNo.startsWith(query)) return 1;
    if (bldNo.includes(query)) return 2;
    return 3;
  };
  return (Array.isArray(products) ? products : [])
    .map((product, index) => ({ product, index, score: score(product) }))
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map(({ product }) => product);
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
  columns: ["row", "oe", "customer-code", "bld", "image", "price", "status", "score", "reason"],
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

const inquiryBldState = (row, input) => inquiryBldSelectionState(
  input.value,
  row.dataset.currentBld,
  { confirmed: row.dataset.bldConfirmed === "1" },
);

const setInquiryBldError = (input, validation, { reveal = true } = {}) => {
  const row = input.closest("[data-inquiry-result-row]");
  const error = row?.querySelector("[data-inquiry-bld-error]");
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
  const bldInput = row.querySelector("[data-inquiry-bld-input]");
  const priceReset = row.querySelector("[data-reset-inquiry-price]");
  const productReset = row.querySelector("[data-reset-inquiry-product]");
  const priceState = priceInput instanceof HTMLInputElement ? inquiryPriceState(row, priceInput) : null;
  const bldState = bldInput instanceof HTMLInputElement ? inquiryBldState(row, bldInput) : null;
  if (priceReset instanceof HTMLButtonElement) {
    priceReset.hidden = adjustment.tax_price === undefined && !(priceState && !priceState.valid);
  }
  if (productReset instanceof HTMLButtonElement) productReset.hidden = !adjustment.target_bld_no;
  row.classList.toggle("inquiry-row-adjusted", Boolean(adjustment.target_bld_no || adjustment.tax_price !== undefined));
  row.classList.toggle(
    "inquiry-row-invalid",
    Boolean((priceState && !priceState.valid) || (bldState && !bldState.valid)),
  );
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

const inquiryImageGallery = (value) => {
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(String(value || "[]"));
    return Array.isArray(parsed) ? parsed : [];
  } catch (_error) {
    return [];
  }
};

const renderInquiryProductImage = (row, galleryValue) => {
  const cell = row.querySelector("[data-inquiry-image-cell]");
  if (!(cell instanceof HTMLElement)) return;
  const gallery = inquiryImageGallery(galleryValue);
  cell.replaceChildren();
  const first = gallery[0];
  if (!first?.url) {
    const empty = document.createElement("span");
    empty.className = "inquiry-image-empty";
    empty.dataset.inquiryImageEmpty = "";
    empty.textContent = "无图";
    cell.append(empty);
    return;
  }
  const link = document.createElement("a");
  link.className = "inquiry-image-link";
  link.href = first.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = `打开 ${row.dataset.currentBld || row.dataset.defaultBld || ""} 产品图片`;
  link.dataset.inquiryProductImage = "";
  const image = document.createElement("img");
  image.className = "inquiry-product-thumb";
  image.src = first.thumb || first.url;
  image.alt = `${row.dataset.currentBld || row.dataset.defaultBld || ""} 产品图片`;
  image.loading = "lazy";
  image.decoding = "async";
  link.append(image);
  cell.append(link);
};

root.querySelectorAll("[data-inquiry-result-row][data-adjustment-key]").forEach((row) => {
  const priceInput = row.querySelector("[data-inquiry-tax-price]");
  const bldInput = row.querySelector("[data-inquiry-bld-input]");
  row.dataset.catalogPrice = row.dataset.defaultPrice || "";
  row.dataset.currentBld = row.dataset.defaultBld || "";
  row.dataset.bldConfirmed = "1";
  row.dataset.priceTouched = "0";
  if (bldInput instanceof HTMLInputElement) {
    setInquiryBldError(bldInput, inquiryBldState(row, bldInput), { reveal: false });
  }
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
    if (activeInquiryBldInput === bldInput) closeInquiryBldOptions();
    inquiryAdjustments.delete(row.dataset.adjustmentKey);
    const status = row.querySelector("[data-col='status']");
    row.dataset.currentBld = row.dataset.defaultBld || "";
    row.dataset.bldConfirmed = "1";
    if (bldInput instanceof HTMLInputElement) {
      bldInput.value = row.dataset.currentBld;
      setInquiryBldError(bldInput, inquiryBldState(row, bldInput), { reveal: false });
    }
    if (status) status.textContent = row.dataset.defaultStatus ?? "";
    const imageCell = row.querySelector("[data-inquiry-image-cell]");
    renderInquiryProductImage(row, imageCell?.dataset.defaultImageGallery || "[]");
    row.dataset.catalogPrice = row.dataset.defaultPrice || "";
    row.dataset.priceTouched = "0";
    if (priceInput instanceof HTMLInputElement) {
      priceInput.value = row.dataset.defaultPrice ? Number(row.dataset.defaultPrice).toFixed(2) : "";
      setInquiryPriceError(priceInput, validateInquiryPrice(priceInput.value, { allowBlank: true }), { reveal: false });
    }
    setInquiryRowState(row);
    if (bldInput instanceof HTMLInputElement) {
      bldInput.focus();
      closeInquiryBldOptions();
      if (inquiryBldStatus instanceof HTMLElement) {
        inquiryBldStatus.textContent = `已恢复原匹配 ${row.dataset.currentBld}。`;
      }
    } else if (priceInput instanceof HTMLInputElement) priceInput.focus();
  });
  setInquiryRowState(row);
});

const inquiryBldOptions = root.querySelector("[data-inquiry-bld-options]");
const inquiryBldStatus = root.querySelector("[data-inquiry-bld-status]");
const inquiryBldRequestGate = createInquiryRequestGate();
let activeInquiryBldInput = null;
let activeInquiryBldRow = null;
let inquiryBldProducts = [];
let inquiryBldActiveIndex = -1;
let inquiryBldSearchTimer = null;
let inquiryBldPositionFrame = null;

const positionInquiryBldOptions = () => {
  if (!(inquiryBldOptions instanceof HTMLElement) || !(activeInquiryBldInput instanceof HTMLInputElement)) return;
  if (inquiryBldOptions.hidden || !activeInquiryBldInput.isConnected) return;
  const rect = activeInquiryBldInput.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const margin = 8;
  const scrollContainer = activeInquiryBldInput.closest("[data-grid-scroll]");
  const scrollRect = scrollContainer instanceof HTMLElement
    ? scrollContainer.getBoundingClientRect()
    : { top: 0, right: viewportWidth, bottom: viewportHeight, left: 0 };
  const stickyHeader = scrollContainer instanceof HTMLElement
    ? scrollContainer.querySelector("thead th")
    : null;
  const stickyHeaderBottom = stickyHeader instanceof HTMLElement
    ? stickyHeader.getBoundingClientRect().bottom
    : scrollRect.top;
  const visibleTop = Math.max(0, scrollRect.top, stickyHeaderBottom);
  const visibleRight = Math.min(viewportWidth, scrollRect.right);
  const visibleBottom = Math.min(viewportHeight, scrollRect.bottom);
  const visibleLeft = Math.max(0, scrollRect.left);
  if (
    rect.bottom <= visibleTop
    || rect.top >= visibleBottom
    || rect.right <= visibleLeft
    || rect.left >= visibleRight
  ) {
    closeInquiryBldOptions();
    return;
  }
  const availableWidth = Math.max(160, viewportWidth - margin * 2);
  const width = Math.min(360, Math.max(rect.width, Math.min(300, availableWidth)));
  inquiryBldOptions.style.width = `${Math.round(width)}px`;
  const height = inquiryBldOptions.offsetHeight;
  const left = Math.max(margin, Math.min(viewportWidth - width - margin, rect.left));
  const below = viewportHeight - rect.bottom - margin;
  const above = rect.top - margin;
  const preferredTop = below >= Math.min(height, 240) || below >= above
    ? rect.bottom + 4
    : rect.top - height - 4;
  const top = Math.max(margin, Math.min(viewportHeight - height - margin, preferredTop));
  inquiryBldOptions.style.left = `${Math.round(left)}px`;
  inquiryBldOptions.style.top = `${Math.round(top)}px`;
};

const queueInquiryBldPosition = () => {
  if (inquiryBldPositionFrame !== null) return;
  inquiryBldPositionFrame = window.requestAnimationFrame(() => {
    inquiryBldPositionFrame = null;
    positionInquiryBldOptions();
  });
};

const closeInquiryBldOptions = () => {
  window.clearTimeout(inquiryBldSearchTimer);
  inquiryBldRequestGate.invalidate();
  if (inquiryBldPositionFrame !== null) {
    window.cancelAnimationFrame(inquiryBldPositionFrame);
    inquiryBldPositionFrame = null;
  }
  if (activeInquiryBldInput instanceof HTMLInputElement) {
    activeInquiryBldInput.setAttribute("aria-expanded", "false");
    activeInquiryBldInput.removeAttribute("aria-activedescendant");
  }
  if (inquiryBldOptions instanceof HTMLElement) {
    inquiryBldOptions.hidden = true;
    inquiryBldOptions.replaceChildren();
    inquiryBldOptions.removeAttribute("aria-busy");
  }
  inquiryBldProducts = [];
  inquiryBldActiveIndex = -1;
  activeInquiryBldInput = null;
  activeInquiryBldRow = null;
};

const openInquiryBldOptions = (input, row) => {
  if (!(inquiryBldOptions instanceof HTMLElement)) return false;
  if (activeInquiryBldInput !== input) closeInquiryBldOptions();
  activeInquiryBldInput = input;
  activeInquiryBldRow = row;
  inquiryBldOptions.hidden = false;
  input.setAttribute("aria-expanded", "true");
  return true;
};

const renderInquiryBldMessage = (message, { busy = false, error = false } = {}) => {
  if (!(inquiryBldOptions instanceof HTMLElement) || !(activeInquiryBldInput instanceof HTMLInputElement)) return;
  inquiryBldOptions.replaceChildren();
  inquiryBldProducts = [];
  inquiryBldActiveIndex = -1;
  activeInquiryBldInput.removeAttribute("aria-activedescendant");
  const status = document.createElement("p");
  status.className = `inquiry-bld-options-message${error ? " error" : ""}`;
  status.setAttribute("role", "option");
  status.setAttribute("aria-disabled", "true");
  status.textContent = message;
  inquiryBldOptions.append(status);
  if (inquiryBldStatus instanceof HTMLElement) inquiryBldStatus.textContent = message;
  inquiryBldOptions.setAttribute("aria-busy", String(busy));
  inquiryBldOptions.hidden = false;
  activeInquiryBldInput.setAttribute("aria-expanded", "true");
  queueInquiryBldPosition();
};

const setInquiryBldActiveIndex = (index) => {
  if (!(inquiryBldOptions instanceof HTMLElement) || !(activeInquiryBldInput instanceof HTMLInputElement)) return;
  const options = Array.from(inquiryBldOptions.querySelectorAll("[data-inquiry-bld-option]"));
  inquiryBldActiveIndex = options.length ? (index + options.length) % options.length : -1;
  options.forEach((option, optionIndex) => {
    const selected = optionIndex === inquiryBldActiveIndex;
    option.classList.toggle("active", selected);
    option.setAttribute("aria-selected", String(selected));
  });
  const active = options[inquiryBldActiveIndex];
  if (active instanceof HTMLElement) {
    activeInquiryBldInput.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  } else {
    activeInquiryBldInput.removeAttribute("aria-activedescendant");
  }
};

const applyInquiryBldProduct = (row, input, product) => {
  const display = inquiryProductDisplay(product);
  if (!display.bldNo) return;
  const current = { ...(inquiryAdjustments.get(row.dataset.adjustmentKey) || {}) };
  const targetBldNo = inquiryTargetAdjustment(display.bldNo, row.dataset.defaultBld);
  if (targetBldNo) current.target_bld_no = targetBldNo;
  else delete current.target_bld_no;
  delete current.tax_price;
  persistInquiryAdjustment(row, current);

  row.dataset.currentBld = display.bldNo;
  row.dataset.bldConfirmed = "1";
  input.value = display.bldNo;
  setInquiryBldError(input, inquiryBldState(row, input), { reveal: false });
  const status = row.querySelector("[data-col='status']");
  if (status) status.textContent = product.product_status || "";
  renderInquiryProductImage(row, product.image_gallery);
  const catalogPrice = product.price_cny ?? "";
  row.dataset.catalogPrice = String(catalogPrice);
  row.dataset.priceTouched = "0";
  const price = row.querySelector("[data-inquiry-tax-price]");
  if (price instanceof HTMLInputElement) {
    price.value = catalogPrice === "" ? "" : Number(catalogPrice).toFixed(2);
    setInquiryPriceError(price, validateInquiryPrice(price.value, { allowBlank: true }), { reveal: false });
  }
  setInquiryRowState(row);
  if (inquiryBldStatus instanceof HTMLElement) {
    const priceMessage = catalogPrice === "" ? "目录含税价未填写" : `含税单价更新为 ¥${Number(catalogPrice).toFixed(2)}`;
    inquiryBldStatus.textContent = `已改为 ${display.bldNo}，${priceMessage}。`;
  }
  closeInquiryBldOptions();
};

const renderInquiryBldProducts = (products, query) => {
  if (!(inquiryBldOptions instanceof HTMLElement) || !(activeInquiryBldInput instanceof HTMLInputElement)) return;
  const input = activeInquiryBldInput;
  const row = activeInquiryBldRow;
  if (!(row instanceof HTMLTableRowElement)) return;
  inquiryBldOptions.replaceChildren();
  inquiryBldProducts = rankInquiryProducts(products, query);
  if (!inquiryBldProducts.length) {
    renderInquiryBldMessage("没有匹配的启用产品。");
    return;
  }
  inquiryBldProducts.forEach((product, index) => {
    const display = inquiryProductDisplay(product);
    const option = document.createElement("button");
    option.type = "button";
    option.tabIndex = -1;
    option.id = `inquiry-bld-option-${index}`;
    option.className = "inquiry-bld-option";
    option.dataset.inquiryBldOption = String(index);
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", "false");
    const code = document.createElement("strong");
    code.textContent = display.bldNo;
    option.append(code);
    if (display.item) {
      const item = document.createElement("span");
      item.textContent = display.item;
      option.append(item);
    }
    const meta = document.createElement("span");
    meta.className = "inquiry-bld-option-meta";
    meta.textContent = `${display.status} · ${display.price}`;
    option.append(meta);
    option.addEventListener("mousedown", (event) => {
      if (event.button === 0) event.preventDefault();
    });
    option.addEventListener("click", (event) => {
      event.preventDefault();
      applyInquiryBldProduct(row, input, product);
    });
    inquiryBldOptions.append(option);
  });
  inquiryBldOptions.setAttribute("aria-busy", "false");
  inquiryBldOptions.hidden = false;
  input.setAttribute("aria-expanded", "true");
  if (inquiryBldStatus instanceof HTMLElement) {
    inquiryBldStatus.textContent = `找到 ${inquiryBldProducts.length} 个启用产品候选。`;
  }
  setInquiryBldActiveIndex(0);
  queueInquiryBldPosition();
};

const searchInquiryBldProducts = async (input, query) => {
  if (!(inquiryBldOptions instanceof HTMLElement) || activeInquiryBldInput !== input) return;
  const sequence = inquiryBldRequestGate.begin();
  const url = new URL(inquiryBldOptions.dataset.productLookupUrl, window.location.origin);
  url.searchParams.set("q", query);
  url.searchParams.set("details", "1");
  url.searchParams.set("active_only", "1");
  url.searchParams.set("media", "1");
  try {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    });
    const payload = await response.json().catch(() => null);
    if (!inquiryBldRequestGate.isCurrent(sequence) || activeInquiryBldInput !== input) return;
    if (!response.ok || !Array.isArray(payload)) {
      throw new Error(payload?.error || "产品候选加载失败，请稍后重试。");
    }
    renderInquiryBldProducts(payload, query);
  } catch (error) {
    if (!inquiryBldRequestGate.isCurrent(sequence) || activeInquiryBldInput !== input) return;
    renderInquiryBldMessage(
      error instanceof Error ? error.message : "产品候选加载失败，请稍后重试。",
      { error: true },
    );
  }
};

const scheduleInquiryBldSearch = (input, row, { markUnconfirmed = false } = {}) => {
  if (!openInquiryBldOptions(input, row)) return;
  window.clearTimeout(inquiryBldSearchTimer);
  inquiryBldRequestGate.invalidate();
  if (markUnconfirmed) {
    const normalizedInput = input.value.trim().toUpperCase();
    const normalizedCurrent = String(row.dataset.currentBld || "").trim().toUpperCase();
    row.dataset.bldConfirmed = normalizedInput && normalizedInput === normalizedCurrent ? "1" : "0";
  }
  const query = input.value.trim();
  const validation = inquiryBldState(row, input);
  setInquiryBldError(input, validation, { reveal: !validation.valid });
  setInquiryRowState(row);
  if (query.length < 2) {
    renderInquiryBldMessage(query ? "请再输入 1 个字符。" : "请输入至少 2 个字符。");
    return;
  }
  renderInquiryBldMessage("正在搜索启用产品...", { busy: true });
  inquiryBldSearchTimer = window.setTimeout(() => searchInquiryBldProducts(input, query), 180);
};

root.querySelectorAll("[data-inquiry-bld-input]").forEach((input) => {
  if (!(input instanceof HTMLInputElement)) return;
  const row = input.closest("[data-inquiry-result-row]");
  if (!(row instanceof HTMLTableRowElement)) return;
  input.addEventListener("focus", () => scheduleInquiryBldSearch(input, row));
  input.addEventListener("input", () => scheduleInquiryBldSearch(input, row, { markUnconfirmed: true }));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      input.value = row.dataset.currentBld || "";
      row.dataset.bldConfirmed = "1";
      setInquiryBldError(input, inquiryBldState(row, input), { reveal: false });
      setInquiryRowState(row);
      closeInquiryBldOptions();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (activeInquiryBldInput !== input) {
        scheduleInquiryBldSearch(input, row);
        return;
      }
      if (inquiryBldProducts.length) {
        setInquiryBldActiveIndex(inquiryBldActiveIndex + (event.key === "ArrowDown" ? 1 : -1));
      }
      return;
    }
    if (event.key === "Enter" && activeInquiryBldInput === input && inquiryBldActiveIndex >= 0) {
      const product = inquiryBldProducts[inquiryBldActiveIndex];
      if (!product) return;
      event.preventDefault();
      applyInquiryBldProduct(row, input, product);
    }
  });
  input.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (activeInquiryBldInput === input) closeInquiryBldOptions();
    }, 0);
  });
});

document.addEventListener("pointerdown", (event) => {
  if (!(activeInquiryBldInput instanceof HTMLInputElement) || !(inquiryBldOptions instanceof HTMLElement)) return;
  if (event.target === activeInquiryBldInput || inquiryBldOptions.contains(event.target)) return;
  closeInquiryBldOptions();
});
document.addEventListener("scroll", queueInquiryBldPosition, true);
window.addEventListener("resize", queueInquiryBldPosition, { passive: true });

const validateAllInquiryAdjustments = () => {
  let firstInvalid = null;
  root.querySelectorAll("[data-inquiry-result-row][data-adjustment-key]").forEach((row) => {
    if (!(row instanceof HTMLTableRowElement)) return;
    const bldInput = row.querySelector("[data-inquiry-bld-input]");
    if (bldInput instanceof HTMLInputElement) {
      const validation = inquiryBldState(row, bldInput);
      setInquiryBldError(bldInput, validation, { reveal: !validation.valid });
      if (!validation.valid && firstInvalid === null) firstInvalid = bldInput;
    }
    if (!updateInquiryPrice(row) && firstInvalid === null) {
      firstInvalid = row.querySelector("[data-inquiry-tax-price]");
    }
    setInquiryRowState(row);
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
  if (validateAllInquiryAdjustments()) return;
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
    if (!validateAllInquiryAdjustments()) return;
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
