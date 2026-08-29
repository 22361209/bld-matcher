import { renderInquiryProductImage } from "./inquiry_result_images.js?v=20260808-1";
import {
  inquiryProductDisplay,
  inquiryTargetAdjustment,
  validateInquiryPrice,
} from "./inquiry_result_rules.js?v=20260808-1";

export const parseInquiryRowOptions = (row, key) => {
  try {
    const parsed = JSON.parse(row?.dataset?.[key] || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

export const findInquiryOption = (options, bldNo) => {
  const target = String(bldNo || "").trim().toUpperCase();
  if (!target) return null;
  return (Array.isArray(options) ? options : []).find(
    (option) => String(option?.bld_no || "").trim().toUpperCase() === target,
  ) || null;
};

export const applyInquiryRowProduct = (row, product, adjustments, { bldInput = null } = {}) => {
  const display = inquiryProductDisplay(product);
  if (!display.bldNo) return false;
  const adjustment = adjustments.current(row);
  const targetBldNo = inquiryTargetAdjustment(display.bldNo, row.dataset.defaultBld);
  if (targetBldNo) adjustment.target_bld_no = targetBldNo;
  else delete adjustment.target_bld_no;
  delete adjustment.tax_price;
  adjustments.persist(row, adjustment);

  row.dataset.currentBld = display.bldNo;
  row.dataset.bldConfirmed = "1";
  if (bldInput instanceof HTMLInputElement) {
    bldInput.value = display.bldNo;
    adjustments.setBldError(bldInput, adjustments.bldState(row, bldInput), { reveal: false });
  }
  const variantSelect = row.querySelector("[data-inquiry-variant-select]");
  if (variantSelect instanceof HTMLSelectElement) {
    const available = Array.from(variantSelect.options).some((option) => option.value === display.bldNo);
    variantSelect.value = available ? display.bldNo : "";
  } else {
    const status = row.querySelector("[data-col='status']");
    if (status) status.textContent = product.product_status || "";
  }
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
  } else if (row.dataset.adjustmentAllowed !== "1") {
    const priceCell = row.querySelector("[data-col='price']");
    if (priceCell) priceCell.textContent = catalogPrice === "" ? "" : `¥${Number(catalogPrice).toFixed(2)}`;
  }
  adjustments.setRowState(row);
  return true;
};

const setupConflictSelect = (select, adjustments) => {
  const row = select.closest("[data-inquiry-result-row]");
  if (!(row instanceof HTMLTableRowElement)) return;
  select.addEventListener("change", () => {
    const option = findInquiryOption(parseInquiryRowOptions(row, "conflictCandidates"), select.value);
    if (option) applyInquiryRowProduct(row, option, adjustments);
  });
  row.querySelector("[data-reset-inquiry-product]")?.addEventListener("click", () => {
    adjustments.remove(row);
    select.value = "";
    row.dataset.currentBld = row.dataset.defaultBld || "";
    const status = row.querySelector("[data-col='status']");
    if (status) status.textContent = row.dataset.defaultStatus ?? "";
    const imageCell = row.querySelector("[data-inquiry-image-cell]");
    renderInquiryProductImage(row, imageCell?.dataset.defaultImageGallery || "[]");
    row.dataset.catalogPrice = row.dataset.defaultPrice || "";
    row.dataset.priceTouched = "0";
    const priceCell = row.querySelector("[data-col='price']");
    if (priceCell) priceCell.textContent = "";
    adjustments.setRowState(row);
    select.focus();
  });
};

const setupVariantSelect = (select, adjustments) => {
  const row = select.closest("[data-inquiry-result-row]");
  if (!(row instanceof HTMLTableRowElement)) return;
  select.addEventListener("change", () => {
    const option = findInquiryOption(parseInquiryRowOptions(row, "variantOptions"), select.value);
    if (!option) return;
    applyInquiryRowProduct(row, option, adjustments, {
      bldInput: row.querySelector("[data-inquiry-bld-input]"),
    });
  });
};

export const setupInquiryResultOptions = (root, adjustments) => {
  root.querySelectorAll("[data-inquiry-conflict-select]").forEach((select) => {
    if (select instanceof HTMLSelectElement) setupConflictSelect(select, adjustments);
  });
  root.querySelectorAll("[data-inquiry-variant-select]").forEach((select) => {
    if (select instanceof HTMLSelectElement) setupVariantSelect(select, adjustments);
  });
};
