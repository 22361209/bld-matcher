const hasFileDrag = (event) => {
  const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
  return types.includes("Files");
};

document.addEventListener("dragover", (event) => {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
});

document.addEventListener("drop", (event) => {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
});

document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  const oeInput = picker ? picker.querySelector(".file-picker-oe-input") : null;
  const clearButton = picker ? picker.querySelector(".file-picker-clear") : null;
  const dropStatus = picker ? picker.querySelector("[data-file-drop-status]") : null;
  const defaultDropStatus = dropStatus ? dropStatus.textContent.trim() : "";
  if (!name && !oeInput && !dropStatus) return;

  const setDropStatus = (message, isError = false) => {
    if (!(dropStatus instanceof HTMLElement)) return;
    dropStatus.textContent = message || defaultDropStatus;
    dropStatus.classList.toggle("error", isError);
  };

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
    setDropStatus(fileName ? `已选择：${fileName}` : defaultDropStatus);
  };

  input.addEventListener("change", syncFilePicker);
  clearButton?.addEventListener("click", () => {
    input.value = "";
    syncFilePicker();
  });

  if (!picker || !picker.hasAttribute("data-file-drop-zone")) return;

  const acceptedExts = (picker.dataset.fileDropAccept || input.getAttribute("accept") || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.startsWith("."));
  const acceptsFile = (file) => {
    if (!acceptedExts.length) return true;
    return acceptedExts.some((ext) => file.name.toLowerCase().endsWith(ext));
  };
  let dragDepth = 0;

  picker.addEventListener("dragenter", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    dragDepth += 1;
    picker.classList.add("drag-over");
    setDropStatus("松开即可导入 Excel 文件");
  });

  picker.addEventListener("dragover", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
  });

  picker.addEventListener("dragleave", (event) => {
    if (!hasFileDrag(event)) return;
    dragDepth -= 1;
    if (dragDepth > 0) return;
    picker.classList.remove("drag-over");
    syncFilePicker();
  });

  picker.addEventListener("drop", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    dragDepth = 0;
    picker.classList.remove("drag-over");

    const files = event.dataTransfer ? Array.from(event.dataTransfer.files || []) : [];
    const file = files.find(acceptsFile);
    if (!file) {
      setDropStatus("仅支持 .xls / .xlsx 文件", true);
      return;
    }
    if (typeof DataTransfer === "undefined") {
      setDropStatus("当前浏览器不支持拖入文件，请点击选择文件", true);
      return;
    }

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (files.length > 1) {
      setDropStatus(`已选择：${file.name}（多个文件时仅使用第一个 Excel）`);
    }
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
  const isSalesContract = form.dataset.contractKind === "sales";
  const productCache = new Map();

  const totalQuantityCell = form.querySelector("[data-purchase-total-quantity]");
  const totalAmountCell = form.querySelector("[data-purchase-total-amount]");
  const totalSmallCell = form.querySelector("[data-purchase-total-small]");
  const totalUpperCell = form.querySelector("[data-purchase-total-upper]");
  const buyerNameInput = form.querySelector('[name="buyer_name"]');
  const supplierNameInput = form.querySelector('[name="supplier_name"]');
  const buyerSignName = form.querySelector("[data-buyer-sign-name]");
  const supplierSignName = form.querySelector("[data-supplier-sign-name]");
  const submitButton = form.querySelector("[data-purchase-submit-button]");
  const submitButtonText = submitButton instanceof HTMLButtonElement ? submitButton.textContent : "生成 PDF";
  const feedback = form.querySelector("[data-purchase-feedback]");
  const confirmModal = document.querySelector("[data-purchase-confirm-modal]");
  const confirmSubmitButton = confirmModal?.querySelector("[data-purchase-confirm-submit]");
  const confirmCancelButtons = confirmModal?.querySelectorAll("[data-purchase-confirm-cancel]");
  let confirmedSubmit = false;

  const setFeedback = (text) => {
    if (!(feedback instanceof HTMLElement)) return;
    feedback.textContent = text;
    feedback.hidden = false;
  };

  const openConfirm = () => {
    if (!(confirmModal instanceof HTMLElement)) return;
    confirmModal.classList.add("open");
    confirmModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    if (confirmSubmitButton instanceof HTMLElement) {
      confirmSubmitButton.focus();
    }
  };

  const closeConfirm = () => {
    if (!(confirmModal instanceof HTMLElement)) return;
    confirmModal.classList.remove("open");
    confirmModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  };

  const parseNumber = (value) => {
    const number = Number.parseFloat(String(value || "").replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  };

  const formatMoney = (value) => value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const formatQuantity = (value) => {
    if (!value) return "";
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4))).replace(/\.0+$/, "");
  };

  const rmbUpper = (value) => {
    const digits = "零壹贰叁肆伍陆柒捌玖";
    const units = ["", "拾", "佰", "仟"];
    const sections = ["", "万", "亿", "兆"];
    const totalCents = Math.max(0, Math.round(value * 100));
    let yuan = Math.floor(totalCents / 100);
    const fraction = totalCents % 100;

    const sectionToUpper = (section) => {
      let result = "";
      let zeroPending = false;
      for (let index = 0; index < 4; index += 1) {
        const digit = section % 10;
        if (digit) {
          if (zeroPending) {
            result = digits[0] + result;
            zeroPending = false;
          }
          result = digits[digit] + units[index] + result;
        } else if (result) {
          zeroPending = true;
        }
        section = Math.floor(section / 10);
      }
      return result;
    };

    let yuanText = "零元";
    if (yuan > 0) {
      const parts = [];
      let sectionIndex = 0;
      let zeroPending = false;
      while (yuan > 0) {
        const section = yuan % 10000;
        if (section) {
          const prefix = zeroPending && parts.length ? digits[0] : "";
          parts.push(prefix + sectionToUpper(section) + sections[sectionIndex]);
          zeroPending = section < 1000;
        } else if (parts.length) {
          zeroPending = true;
        }
        yuan = Math.floor(yuan / 10000);
        sectionIndex += 1;
      }
      yuanText = parts.reverse().join("") + "元";
    }

    if (!fraction) return `${yuanText}整`;
    const jiao = Math.floor(fraction / 10);
    const fen = fraction % 10;
    let fractionText = "";
    if (jiao) {
      fractionText += `${digits[jiao]}角`;
    } else if (totalCents >= 100) {
      fractionText += digits[0];
    }
    if (fen) {
      fractionText += `${digits[fen]}分`;
    }
    return yuanText + fractionText;
  };

  const syncRows = () => {
    const rows = Array.from(body.querySelectorAll("tr"));
    let totalQuantity = 0;
    let totalAmount = 0;
    rows.forEach((row, index) => {
      const indexCell = row.querySelector("[data-purchase-index]");
      if (indexCell instanceof HTMLElement) {
        indexCell.textContent = String(index + 1);
      }
      ["product_code[]", "quantity[]", "unit_price[]"].forEach((name) => {
        const input = row.querySelector(`[name="${name}"]`);
        if (input instanceof HTMLInputElement) {
          input.required = index === 0;
        }
      });
      const quantity = parseNumber(row.querySelector('[name="quantity[]"]')?.value);
      const unitPrice = parseNumber(row.querySelector('[name="unit_price[]"]')?.value);
      const amountCell = row.querySelector("[data-purchase-amount]");
      if (quantity !== null && unitPrice !== null && quantity >= 0 && unitPrice >= 0) {
        const amount = Math.round(quantity * unitPrice * 100) / 100;
        totalQuantity += quantity;
        totalAmount += amount;
        if (amountCell instanceof HTMLElement) {
          amountCell.textContent = formatMoney(amount);
        }
      } else if (amountCell instanceof HTMLElement) {
        amountCell.textContent = "";
      }
    });
    if (totalQuantityCell instanceof HTMLElement) {
      totalQuantityCell.textContent = formatQuantity(totalQuantity);
    }
    if (totalAmountCell instanceof HTMLElement) {
      totalAmountCell.textContent = totalAmount ? formatMoney(totalAmount) : "";
    }
    if (totalSmallCell instanceof HTMLElement) {
      totalSmallCell.textContent = formatMoney(totalAmount);
    }
    if (totalUpperCell instanceof HTMLElement) {
      totalUpperCell.textContent = rmbUpper(totalAmount);
    }
  };

  const createRow = () => {
    const row = document.createElement("tr");
    row.innerHTML = [
      '<td class="purchase-row-index" data-purchase-index></td>',
      '<td><input name="product_code[]" data-purchase-bld></td>',
      isSalesContract ? '<td><input name="customer_code[]" data-customer-code></td>' : "",
      '<td><input name="oe_no[]" data-purchase-oe></td>',
      '<td><input name="product_name[]" data-purchase-name></td>',
      '<td><input name="models[]" data-purchase-models></td>',
      '<td><input name="quantity[]" inputmode="decimal"></td>',
      '<td><input name="unit_price[]" inputmode="decimal"></td>',
      '<td class="purchase-amount-cell" data-purchase-amount></td>',
      '<td><input name="item_note[]"></td>',
      '<td><input name="delivery_date[]"></td>',
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

  const applyProduct = (row, product) => {
    if (!product || !product.found) {
      setValue(row, "[data-purchase-oe]", "");
      setValue(row, "[data-purchase-name]", "");
      setValue(row, "[data-purchase-models]", "");
      return;
    }
    setValue(row, "[data-purchase-bld]", product.bld_no);
    setValue(row, "[data-purchase-oe]", product.oe_no);
    setValue(row, "[data-purchase-name]", product.product_name);
    setValue(row, "[data-purchase-models]", product.models);
    const unitPriceInput = row.querySelector('[name="unit_price[]"]');
    if (isSalesContract && unitPriceInput instanceof HTMLInputElement && !unitPriceInput.value && product.price_cny !== null && product.price_cny !== undefined) {
      unitPriceInput.value = String(product.price_cny);
      syncRows();
    }
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
    syncRows();
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
    syncRows();
  });

  body.addEventListener("input", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && (input.name === "quantity[]" || input.name === "unit_price[]")) {
      syncRows();
    }
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

  form.querySelectorAll(".paper-textarea").forEach((textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    const fit = () => {
      textarea.style.height = "auto";
      textarea.style.height = `${textarea.scrollHeight}px`;
    };
    textarea.addEventListener("input", fit);
    fit();
  });

  if (buyerNameInput instanceof HTMLInputElement && buyerSignName instanceof HTMLElement) {
    const syncBuyerSignName = () => {
      buyerSignName.textContent = buyerNameInput.value;
    };
    buyerNameInput.addEventListener("input", syncBuyerSignName);
    syncBuyerSignName();
  }

  if (supplierNameInput instanceof HTMLInputElement && supplierSignName instanceof HTMLElement) {
    const syncSupplierSignName = () => {
      supplierSignName.textContent = supplierNameInput.value;
    };
    supplierNameInput.addEventListener("input", syncSupplierSignName);
    syncSupplierSignName();
  }

  confirmCancelButtons?.forEach((button) => {
    button.addEventListener("click", closeConfirm);
  });

  confirmSubmitButton?.addEventListener("click", () => {
    confirmedSubmit = true;
    closeConfirm();
    form.requestSubmit();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && confirmModal instanceof HTMLElement && confirmModal.classList.contains("open")) {
      closeConfirm();
    }
  });

  form.addEventListener("submit", (event) => {
    if (!confirmedSubmit) {
      event.preventDefault();
      if (form.reportValidity()) {
        openConfirm();
      }
      return;
    }
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "正在生成...";
    }
    setFeedback("已确认生成 PDF，浏览器会开始下载；生成记录可在页面底部「最近合同」查看。");
    window.setTimeout(() => {
      confirmedSubmit = false;
      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = false;
        submitButton.textContent = submitButtonText || "生成 PDF";
      }
    }, 6000);
  });

  syncRows();
});
