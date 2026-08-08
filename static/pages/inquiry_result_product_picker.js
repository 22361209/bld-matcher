import { renderInquiryProductImage } from "./inquiry_result_images.js?v=20260808-1";
import {
  createInquiryRequestGate,
  inquiryProductDisplay,
  inquiryTargetAdjustment,
  rankInquiryProducts,
  validateInquiryPrice,
} from "./inquiry_result_rules.js?v=20260808-1";

export const setupInquiryProductPicker = (root, adjustments) => {
  const doc = root.nodeType === 9 ? root : root.ownerDocument;
  const win = doc.defaultView;
  const optionsPanel = root.querySelector("[data-inquiry-bld-options]");
  const statusRegion = root.querySelector("[data-inquiry-bld-status]");
  const requestGate = createInquiryRequestGate();
  let activeInput = null;
  let activeRow = null;
  let products = [];
  let activeIndex = -1;
  let searchTimer = null;
  let positionFrame = null;

  const close = () => {
    win.clearTimeout(searchTimer);
    requestGate.invalidate();
    if (positionFrame !== null) {
      win.cancelAnimationFrame(positionFrame);
      positionFrame = null;
    }
    if (activeInput instanceof HTMLInputElement) {
      activeInput.setAttribute("aria-expanded", "false");
      activeInput.removeAttribute("aria-activedescendant");
    }
    if (optionsPanel instanceof HTMLElement) {
      optionsPanel.hidden = true;
      optionsPanel.replaceChildren();
      optionsPanel.removeAttribute("aria-busy");
    }
    products = [];
    activeIndex = -1;
    activeInput = null;
    activeRow = null;
  };

  const position = () => {
    if (!(optionsPanel instanceof HTMLElement) || !(activeInput instanceof HTMLInputElement)) return;
    if (optionsPanel.hidden || !activeInput.isConnected) return;
    const rect = activeInput.getBoundingClientRect();
    const viewportWidth = doc.documentElement.clientWidth || win.innerWidth;
    const viewportHeight = doc.documentElement.clientHeight || win.innerHeight;
    const margin = 8;
    const scrollContainer = activeInput.closest("[data-grid-scroll]");
    const scrollRect = scrollContainer instanceof HTMLElement
      ? scrollContainer.getBoundingClientRect()
      : { top: 0, right: viewportWidth, bottom: viewportHeight, left: 0 };
    const stickyHeader = scrollContainer instanceof HTMLElement ? scrollContainer.querySelector("thead th") : null;
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
      close();
      return;
    }
    const availableWidth = Math.max(160, viewportWidth - margin * 2);
    const width = Math.min(360, Math.max(rect.width, Math.min(300, availableWidth)));
    optionsPanel.style.width = `${Math.round(width)}px`;
    const height = optionsPanel.offsetHeight;
    const left = Math.max(margin, Math.min(viewportWidth - width - margin, rect.left));
    const below = viewportHeight - rect.bottom - margin;
    const above = rect.top - margin;
    const preferredTop = below >= Math.min(height, 240) || below >= above
      ? rect.bottom + 4
      : rect.top - height - 4;
    const top = Math.max(margin, Math.min(viewportHeight - height - margin, preferredTop));
    optionsPanel.style.left = `${Math.round(left)}px`;
    optionsPanel.style.top = `${Math.round(top)}px`;
  };

  const queuePosition = () => {
    if (positionFrame !== null) return;
    positionFrame = win.requestAnimationFrame(() => {
      positionFrame = null;
      position();
    });
  };

  const open = (input, row) => {
    if (!(optionsPanel instanceof HTMLElement)) return false;
    if (activeInput !== input) close();
    activeInput = input;
    activeRow = row;
    optionsPanel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    return true;
  };

  const renderMessage = (message, { busy = false, error = false } = {}) => {
    if (!(optionsPanel instanceof HTMLElement) || !(activeInput instanceof HTMLInputElement)) return;
    optionsPanel.replaceChildren();
    products = [];
    activeIndex = -1;
    activeInput.removeAttribute("aria-activedescendant");
    const status = doc.createElement("p");
    status.className = `inquiry-bld-options-message${error ? " error" : ""}`;
    status.setAttribute("role", "option");
    status.setAttribute("aria-disabled", "true");
    status.textContent = message;
    optionsPanel.append(status);
    if (statusRegion instanceof HTMLElement) statusRegion.textContent = message;
    optionsPanel.setAttribute("aria-busy", String(busy));
    optionsPanel.hidden = false;
    activeInput.setAttribute("aria-expanded", "true");
    queuePosition();
  };

  const setActiveIndex = (index) => {
    if (!(optionsPanel instanceof HTMLElement) || !(activeInput instanceof HTMLInputElement)) return;
    const options = Array.from(optionsPanel.querySelectorAll("[data-inquiry-bld-option]"));
    activeIndex = options.length ? (index + options.length) % options.length : -1;
    options.forEach((option, optionIndex) => {
      const selected = optionIndex === activeIndex;
      option.classList.toggle("active", selected);
      option.setAttribute("aria-selected", String(selected));
    });
    const active = options[activeIndex];
    if (active instanceof HTMLElement) {
      activeInput.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({ block: "nearest" });
    } else {
      activeInput.removeAttribute("aria-activedescendant");
    }
  };

  const applyProduct = (row, input, product) => {
    const display = inquiryProductDisplay(product);
    if (!display.bldNo) return;
    const adjustment = adjustments.current(row);
    const targetBldNo = inquiryTargetAdjustment(display.bldNo, row.dataset.defaultBld);
    if (targetBldNo) adjustment.target_bld_no = targetBldNo;
    else delete adjustment.target_bld_no;
    delete adjustment.tax_price;
    adjustments.persist(row, adjustment);

    row.dataset.currentBld = display.bldNo;
    row.dataset.bldConfirmed = "1";
    input.value = display.bldNo;
    adjustments.setBldError(input, adjustments.bldState(row, input), { reveal: false });
    const status = row.querySelector("[data-col='status']");
    if (status) status.textContent = product.product_status || "";
    renderInquiryProductImage(row, product.image_gallery);
    const catalogPrice = product.price_cny ?? "";
    row.dataset.catalogPrice = String(catalogPrice);
    row.dataset.priceTouched = "0";
    const price = row.querySelector("[data-inquiry-tax-price]");
    if (price instanceof HTMLInputElement) {
      price.value = catalogPrice === "" ? "" : Number(catalogPrice).toFixed(2);
      adjustments.setPriceError(
        price,
        validateInquiryPrice(price.value, { allowBlank: true }),
        { reveal: false },
      );
    }
    adjustments.setRowState(row);
    if (statusRegion instanceof HTMLElement) {
      const priceMessage = catalogPrice === ""
        ? "目录含税价未填写"
        : `含税单价更新为 ¥${Number(catalogPrice).toFixed(2)}`;
      statusRegion.textContent = `已改为 ${display.bldNo}，${priceMessage}。`;
    }
    close();
  };

  const renderProducts = (candidates, query) => {
    if (!(optionsPanel instanceof HTMLElement) || !(activeInput instanceof HTMLInputElement)) return;
    const input = activeInput;
    const row = activeRow;
    if (!(row instanceof HTMLTableRowElement)) return;
    optionsPanel.replaceChildren();
    products = rankInquiryProducts(candidates, query);
    if (!products.length) {
      renderMessage("没有匹配的启用产品。");
      return;
    }
    products.forEach((product, index) => {
      const display = inquiryProductDisplay(product);
      const option = doc.createElement("button");
      option.type = "button";
      option.tabIndex = -1;
      option.id = `inquiry-bld-option-${index}`;
      option.className = "inquiry-bld-option";
      option.dataset.inquiryBldOption = String(index);
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      const code = doc.createElement("strong");
      code.textContent = display.bldNo;
      option.append(code);
      if (display.item) {
        const item = doc.createElement("span");
        item.textContent = display.item;
        option.append(item);
      }
      const meta = doc.createElement("span");
      meta.className = "inquiry-bld-option-meta";
      meta.textContent = `${display.status} · ${display.price}`;
      option.append(meta);
      option.addEventListener("mousedown", (event) => {
        if (event.button === 0) event.preventDefault();
      });
      option.addEventListener("click", (event) => {
        event.preventDefault();
        applyProduct(row, input, product);
      });
      optionsPanel.append(option);
    });
    optionsPanel.setAttribute("aria-busy", "false");
    optionsPanel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (statusRegion instanceof HTMLElement) statusRegion.textContent = `找到 ${products.length} 个启用产品候选。`;
    setActiveIndex(0);
    queuePosition();
  };

  const search = async (input, query) => {
    if (!(optionsPanel instanceof HTMLElement) || activeInput !== input) return;
    const sequence = requestGate.begin();
    const url = new URL(optionsPanel.dataset.productLookupUrl, win.location.origin);
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
      if (!requestGate.isCurrent(sequence) || activeInput !== input) return;
      if (!response.ok || !Array.isArray(payload)) {
        throw new Error(payload?.error || "产品候选加载失败，请稍后重试。");
      }
      renderProducts(payload, query);
    } catch (error) {
      if (!requestGate.isCurrent(sequence) || activeInput !== input) return;
      renderMessage(error instanceof Error ? error.message : "产品候选加载失败，请稍后重试。", {
        error: true,
      });
    }
  };

  const scheduleSearch = (input, row, { markUnconfirmed = false } = {}) => {
    if (!open(input, row)) return;
    win.clearTimeout(searchTimer);
    requestGate.invalidate();
    if (markUnconfirmed) {
      const normalizedInput = input.value.trim().toUpperCase();
      const normalizedCurrent = String(row.dataset.currentBld || "").trim().toUpperCase();
      row.dataset.bldConfirmed = normalizedInput && normalizedInput === normalizedCurrent ? "1" : "0";
    }
    const query = input.value.trim();
    const validation = adjustments.bldState(row, input);
    adjustments.setBldError(input, validation, { reveal: !validation.valid });
    adjustments.setRowState(row);
    if (query.length < 2) {
      renderMessage(query ? "请再输入 1 个字符。" : "请输入至少 2 个字符。");
      return;
    }
    renderMessage("正在搜索启用产品...", { busy: true });
    searchTimer = win.setTimeout(() => search(input, query), 180);
  };

  root.querySelectorAll("[data-inquiry-bld-input]").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) return;
    const row = input.closest("[data-inquiry-result-row]");
    if (!(row instanceof HTMLTableRowElement)) return;
    input.addEventListener("focus", () => scheduleSearch(input, row));
    input.addEventListener("input", () => scheduleSearch(input, row, { markUnconfirmed: true }));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        input.value = row.dataset.currentBld || "";
        row.dataset.bldConfirmed = "1";
        adjustments.setBldError(input, adjustments.bldState(row, input), { reveal: false });
        adjustments.setRowState(row);
        close();
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (activeInput !== input) scheduleSearch(input, row);
        else if (products.length) setActiveIndex(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
        return;
      }
      if (event.key === "Enter" && activeInput === input && activeIndex >= 0) {
        const product = products[activeIndex];
        if (!product) return;
        event.preventDefault();
        applyProduct(row, input, product);
      }
    });
    input.addEventListener("blur", () => {
      win.setTimeout(() => {
        if (activeInput === input) close();
      }, 0);
    });
    row.querySelector("[data-reset-inquiry-product]")?.addEventListener("click", () => {
      if (activeInput === input) close();
      adjustments.remove(row);
      const status = row.querySelector("[data-col='status']");
      row.dataset.currentBld = row.dataset.defaultBld || "";
      row.dataset.bldConfirmed = "1";
      input.value = row.dataset.currentBld;
      adjustments.setBldError(input, adjustments.bldState(row, input), { reveal: false });
      if (status) status.textContent = row.dataset.defaultStatus ?? "";
      const imageCell = row.querySelector("[data-inquiry-image-cell]");
      renderInquiryProductImage(row, imageCell?.dataset.defaultImageGallery || "[]");
      row.dataset.catalogPrice = row.dataset.defaultPrice || "";
      row.dataset.priceTouched = "0";
      const price = row.querySelector("[data-inquiry-tax-price]");
      if (price instanceof HTMLInputElement) {
        price.value = row.dataset.defaultPrice ? Number(row.dataset.defaultPrice).toFixed(2) : "";
        adjustments.setPriceError(
          price,
          validateInquiryPrice(price.value, { allowBlank: true }),
          { reveal: false },
        );
      }
      adjustments.setRowState(row);
      input.focus();
      close();
      if (statusRegion instanceof HTMLElement) statusRegion.textContent = `已恢复原匹配 ${row.dataset.currentBld}。`;
    });
  });

  doc.addEventListener("pointerdown", (event) => {
    if (!(activeInput instanceof HTMLInputElement) || !(optionsPanel instanceof HTMLElement)) return;
    if (event.target === activeInput || optionsPanel.contains(event.target)) return;
    close();
  });
  doc.addEventListener("scroll", queuePosition, true);
  win.addEventListener("resize", queuePosition, { passive: true });
};
