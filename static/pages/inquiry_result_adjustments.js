import {
  inquiryBldSelectionState,
  inquiryPriceAdjustment,
  validateInquiryPrice,
} from "./inquiry_result_rules.js?v=20260808-1";

export const setupInquiryAdjustments = (root) => {
  const adjustments = new Map();
  const adjustmentFields = root.querySelectorAll("[data-inquiry-adjustments-field]");

  const sync = () => {
    const payload = JSON.stringify(Object.fromEntries(adjustments));
    adjustmentFields.forEach((field) => {
      if (field instanceof HTMLInputElement) field.value = payload;
    });
  };

  const current = (row) => ({ ...(adjustments.get(row.dataset.adjustmentKey) || {}) });

  const persist = (row, adjustment) => {
    const key = row.dataset.adjustmentKey;
    if (!key) return;
    if (adjustment.target_bld_no || adjustment.tax_price !== undefined) {
      adjustment.expected_bld_no = row.dataset.defaultBld || "";
      adjustments.set(key, adjustment);
    } else {
      adjustments.delete(key);
    }
  };

  const remove = (row) => {
    adjustments.delete(row.dataset.adjustmentKey);
  };

  const priceState = (row, input) => inquiryPriceAdjustment(
    input.value,
    row.dataset.catalogPrice,
    { allowBlank: row.dataset.priceTouched !== "1" && !input.value.trim() },
  );

  const setPriceError = (input, validation, { reveal = true } = {}) => {
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

  const bldState = (row, input) => inquiryBldSelectionState(
    input.value,
    row.dataset.currentBld,
    { confirmed: row.dataset.bldConfirmed === "1" },
  );

  const setBldError = (input, validation, { reveal = true } = {}) => {
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

  const setRowState = (row) => {
    const adjustment = adjustments.get(row.dataset.adjustmentKey) || {};
    const priceInput = row.querySelector("[data-inquiry-tax-price]");
    const bldInput = row.querySelector("[data-inquiry-bld-input]");
    const priceReset = row.querySelector("[data-reset-inquiry-price]");
    const productReset = row.querySelector("[data-reset-inquiry-product]");
    const currentPriceState = priceInput instanceof HTMLInputElement ? priceState(row, priceInput) : null;
    const currentBldState = bldInput instanceof HTMLInputElement ? bldState(row, bldInput) : null;
    if (priceReset instanceof HTMLButtonElement) {
      priceReset.hidden = adjustment.tax_price === undefined && !(currentPriceState && !currentPriceState.valid);
    }
    if (productReset instanceof HTMLButtonElement) productReset.hidden = !adjustment.target_bld_no;
    row.classList.toggle(
      "inquiry-row-adjusted",
      Boolean(adjustment.target_bld_no || adjustment.tax_price !== undefined),
    );
    row.classList.toggle(
      "inquiry-row-invalid",
      Boolean((currentPriceState && !currentPriceState.valid) || (currentBldState && !currentBldState.valid)),
    );
    sync();
  };

  const updatePrice = (row, { revealError = true } = {}) => {
    const input = row.querySelector("[data-inquiry-tax-price]");
    if (!(input instanceof HTMLInputElement)) return true;
    const validation = priceState(row, input);
    const adjustment = current(row);
    if (validation.valid) {
      if (validation.override === null) delete adjustment.tax_price;
      else adjustment.tax_price = validation.override;
    } else {
      delete adjustment.tax_price;
    }
    persist(row, adjustment);
    setPriceError(input, validation, { reveal: revealError });
    setRowState(row);
    return validation.valid;
  };

  const rows = root.querySelectorAll("[data-inquiry-result-row][data-adjustment-key]");
  rows.forEach((row) => {
    const priceInput = row.querySelector("[data-inquiry-tax-price]");
    const bldInput = row.querySelector("[data-inquiry-bld-input]");
    row.dataset.catalogPrice = row.dataset.defaultPrice || "";
    row.dataset.currentBld = row.dataset.defaultBld || "";
    row.dataset.bldConfirmed = "1";
    row.dataset.priceTouched = "0";
    if (bldInput instanceof HTMLInputElement) {
      setBldError(bldInput, bldState(row, bldInput), { reveal: false });
    }
    if (priceInput instanceof HTMLInputElement) {
      priceInput.addEventListener("input", () => {
        row.dataset.priceTouched = "1";
        updatePrice(row);
      });
      priceInput.addEventListener("change", () => {
        const validation = validateInquiryPrice(priceInput.value, {
          allowBlank: row.dataset.priceTouched !== "1" && !priceInput.value.trim(),
        });
        if (validation.valid && validation.normalized) priceInput.value = validation.normalized;
        updatePrice(row);
      });
    }
    row.querySelector("[data-reset-inquiry-price]")?.addEventListener("click", () => {
      if (!(priceInput instanceof HTMLInputElement)) return;
      const adjustment = current(row);
      delete adjustment.tax_price;
      persist(row, adjustment);
      const catalogPrice = String(row.dataset.catalogPrice || "").trim();
      priceInput.value = catalogPrice ? Number(catalogPrice).toFixed(2) : "";
      row.dataset.priceTouched = "0";
      setPriceError(priceInput, validateInquiryPrice(priceInput.value, { allowBlank: true }), { reveal: false });
      setRowState(row);
      priceInput.focus();
    });
    setRowState(row);
  });

  const validateAll = ({ beforeFocusInvalid } = {}) => {
    let firstInvalid = null;
    rows.forEach((row) => {
      if (!(row instanceof HTMLTableRowElement)) return;
      const bldInput = row.querySelector("[data-inquiry-bld-input]");
      if (bldInput instanceof HTMLInputElement) {
        const validation = bldState(row, bldInput);
        setBldError(bldInput, validation, { reveal: !validation.valid });
        if (!validation.valid && firstInvalid === null) firstInvalid = bldInput;
      }
      if (!updatePrice(row) && firstInvalid === null) {
        firstInvalid = row.querySelector("[data-inquiry-tax-price]");
      }
      setRowState(row);
    });
    if (!(firstInvalid instanceof HTMLInputElement)) return true;
    if (typeof beforeFocusInvalid === "function") beforeFocusInvalid();
    firstInvalid.focus();
    firstInvalid.scrollIntoView({ block: "center", inline: "nearest" });
    firstInvalid.reportValidity();
    return false;
  };

  return {
    bldState,
    current,
    persist,
    remove,
    setBldError,
    setPriceError,
    setRowState,
    validateAll,
  };
};
