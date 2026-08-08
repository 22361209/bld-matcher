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

export const setupInquiryDownloads = (root, { validateAdjustments }) => {
  const doc = root.nodeType === 9 ? root : root.ownerDocument;
  const win = doc.defaultView;
  const modal = root.querySelector("#download-excel-modal");

  const close = () => {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    doc.body.classList.remove("modal-open");
  };

  const validate = () => validateAdjustments({ beforeFocusInvalid: close });

  root.querySelectorAll("[data-price-mode]").forEach((select) => {
    const form = select.closest("form");
    const rateField = form?.querySelector("[data-exchange-rate-field]");
    const rateInput = form?.querySelector("[data-exchange-rate]");
    const syncRateField = () => {
      const needsRate = select.value === "usd";
      if (rateField instanceof HTMLElement) rateField.hidden = !needsRate;
      if (rateInput instanceof HTMLInputElement) {
        rateInput.disabled = !needsRate;
        rateInput.required = needsRate;
      }
    };
    select.addEventListener("change", syncRateField);
    syncRateField();
  });

  root.querySelectorAll("[data-open-download-modal]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!modal) return;
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      doc.body.classList.add("modal-open");
      const select = modal.querySelector("[data-price-mode]");
      if (select instanceof HTMLElement) select.focus();
    });
  });
  root.querySelectorAll("[data-close-download-modal]").forEach((element) => {
    element.addEventListener("click", close);
  });

  const triggerBlobDownload = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const anchor = doc.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    doc.body.append(anchor);
    anchor.click();
    anchor.remove();
    win.setTimeout(() => URL.revokeObjectURL(url), 10000);
  };

  root.querySelector("[data-inquiry-download-form]")?.addEventListener("submit", (event) => {
    if (validate()) return;
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
      if (!validate()) return;
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
          message.replaceChildren(doc.createTextNode(text));
          if (quotesUrl) {
            const link = doc.createElement("a");
            link.href = quotesUrl;
            link.textContent = "前往报价记录";
            message.append(" ", link);
          }
          message.classList.add("active", "error");
          message.classList.remove("done");
        }
        button.disabled = !allowRetry;
      };
      const showWait = (text) => {
        if (message instanceof HTMLElement) {
          message.textContent = text;
          message.classList.add("active");
          message.classList.remove("done", "error");
        }
      };

      button.disabled = true;
      const body = new FormData(form);
      showWait("正在生成 Excel 并写入报价记录...");
      try {
        const response = await fetch(button.formAction, {
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
        win.location.assign(payload.quotes_url);
      } catch (_error) {
        showError("网络错误，无法确认报价是否已写入；请先到报价记录核对后再重试。", {
          allowRetry: false,
        });
      }
    });
  });

  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal?.classList.contains("open")) close();
  });
};
