document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  const oeInput = picker ? picker.querySelector(".file-picker-oe-input") : null;
  const clearButton = picker ? picker.querySelector(".file-picker-clear") : null;
  if (!name && !oeInput) return;

  const syncFilePicker = () => {
    const fileName = input.files && input.files.length ? input.files[0].name : "";
    if (name) {
      name.textContent = fileName || "未选择任何文件";
    }
    if (clearButton instanceof HTMLButtonElement) {
      clearButton.disabled = !fileName;
    }
    if (oeInput && fileName) {
      oeInput.value = fileName;
      oeInput.readOnly = true;
    } else if (oeInput) {
      oeInput.readOnly = false;
    }
  };

  input.addEventListener("change", syncFilePicker);
  clearButton?.addEventListener("click", () => {
    input.value = "";
    syncFilePicker();
  });
});

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

document.querySelectorAll("form[data-enter-navigation]").forEach((form) => {
  const focusableSelector = [
    "input:not([type='hidden']):not([type='checkbox']):not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "button[type='submit']:not([disabled])",
  ].join(",");

  form.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.matches("input, select, textarea")) return;
    if (target instanceof HTMLTextAreaElement && event.shiftKey) return;

    event.preventDefault();
    const fields = Array.from(form.querySelectorAll(focusableSelector)).filter((field) => {
      return field instanceof HTMLElement && field.offsetParent !== null;
    });
    const index = fields.indexOf(target);
    const next = fields[index + 1];
    if (next instanceof HTMLElement) {
      next.focus();
      if (next instanceof HTMLInputElement) {
        next.select();
      }
    }
  });
});

const quickOeImageModal = document.querySelector("#quick-oe-image-modal");
const quickOeImageModalImg = document.querySelector("#quick-oe-image-modal-img");
const quickOeImageModalCaption = document.querySelector("#quick-oe-image-modal-caption");

const closeQuickOeImageModal = () => {
  if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
  quickOeImageModal.classList.remove("open");
  quickOeImageModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  quickOeImageModalImg.src = "";
  quickOeImageModalImg.alt = "";
  quickOeImageModalCaption.textContent = "";
};

document.querySelectorAll("[data-quick-oe-image]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
    event.preventDefault();
    const image = link.querySelector("img");
    quickOeImageModalImg.src = link.href;
    quickOeImageModalImg.alt = image?.alt || "产品图片";
    quickOeImageModalCaption.textContent = link.dataset.caption || "";
    quickOeImageModal.classList.add("open");
    quickOeImageModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  });
});

document.querySelectorAll("[data-close-quick-oe-image-modal]").forEach((element) => {
  element.addEventListener("click", closeQuickOeImageModal);
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

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && quickOeImageModal?.classList.contains("open")) {
    closeQuickOeImageModal();
  }
  if (event.key === "Escape" && downloadModal?.classList.contains("open")) {
    closeDownloadModal();
  }
});

document.querySelectorAll("[data-purchase-contract-form]").forEach((form) => {
  const body = form.querySelector("[data-purchase-rows]");
  const addButton = form.querySelector("[data-add-purchase-row]");
  if (!(body instanceof HTMLTableSectionElement) || !(addButton instanceof HTMLButtonElement)) return;
  const productCache = new Map();

  const syncRequiredRow = () => {
    const rows = Array.from(body.querySelectorAll("tr"));
    rows.forEach((row, index) => {
      ["product_code[]", "quantity[]", "unit_price[]"].forEach((name) => {
        const input = row.querySelector(`[name="${name}"]`);
        if (input instanceof HTMLInputElement) {
          input.required = index === 0;
        }
      });
    });
  };

  const createRow = () => {
    const row = document.createElement("tr");
    row.innerHTML = [
      '<td><input name="product_code[]" data-purchase-bld></td>',
      '<td><input name="oe_no[]" data-purchase-oe></td>',
      '<td><input name="product_name[]" data-purchase-name></td>',
      '<td><input name="models[]" data-purchase-models></td>',
      '<td><span class="purchase-image-preview" data-purchase-image></span></td>',
      '<td><input name="quantity[]" inputmode="decimal"></td>',
      '<td><input name="unit_price[]" inputmode="decimal"></td>',
      '<td><input name="delivery_date[]"></td>',
      '<td><input name="item_note[]"></td>',
      '<td><button class="link-button danger-link" type="button" data-remove-purchase-row>删除</button></td>',
    ].join("");
    return row;
  };

  const setValue = (row, selector, value) => {
    const input = row.querySelector(selector);
    if (input instanceof HTMLInputElement) {
      input.value = value || "";
    }
  };

  const setImage = (row, product) => {
    const holder = row.querySelector("[data-purchase-image]");
    if (!(holder instanceof HTMLElement)) return;
    holder.replaceChildren();
    if (product?.thumb_url || product?.image_url) {
      const image = document.createElement("img");
      image.src = product.thumb_url || product.image_url;
      image.alt = product.bld_no ? `${product.bld_no} 产品图片` : "产品图片";
      holder.appendChild(image);
    } else if (product?.found === false) {
      holder.textContent = "未找到";
    }
  };

  const applyProduct = (row, product) => {
    if (!product || !product.found) {
      setValue(row, "[data-purchase-oe]", "");
      setValue(row, "[data-purchase-name]", "");
      setValue(row, "[data-purchase-models]", "");
      setImage(row, { found: false });
      return;
    }
    setValue(row, "[data-purchase-bld]", product.bld_no);
    setValue(row, "[data-purchase-oe]", product.oe_no);
    setValue(row, "[data-purchase-name]", product.product_name);
    setValue(row, "[data-purchase-models]", product.models);
    setImage(row, product);
  };

  const lookupProduct = async (input) => {
    const bld = input.value.trim();
    const row = input.closest("tr");
    if (!bld || !(row instanceof HTMLTableRowElement)) return;
    const cacheKey = bld.toUpperCase();
    if (productCache.has(cacheKey)) {
      applyProduct(row, productCache.get(cacheKey));
      return;
    }
    try {
      const response = await fetch(`/purchase-contracts/product-lookup?bld=${encodeURIComponent(bld)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const product = await response.json();
      productCache.set(cacheKey, product);
      applyProduct(row, product);
    } catch (_error) {
      // 留在手动填写状态。
    }
  };

  addButton.addEventListener("click", () => {
    const row = createRow();
    body.appendChild(row);
    syncRequiredRow();
    row.querySelector("input")?.focus();
  });

  body.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-remove-purchase-row]") : null;
    if (!button) return;
    const row = button.closest("tr");
    row?.remove();
    if (!body.querySelector("tr")) {
      body.appendChild(createRow());
    }
    syncRequiredRow();
  });

  body.addEventListener("change", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-purchase-bld]")) {
      lookupProduct(input);
    }
  });

  body.addEventListener("blur", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-purchase-bld]")) {
      lookupProduct(input);
    }
  }, true);

  syncRequiredRow();
});
