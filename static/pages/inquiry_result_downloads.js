import { chooseExcelDestination, saveExcelResponse } from "../components/excel_save.js?v=20261122-1";

export { inquiryAttachmentFilename } from "../components/excel_save.js?v=20261122-1";

export const clearQuoteCustomerValidity = (input) => {
  if (input && typeof input.setCustomValidity === "function") input.setCustomValidity("");
};

export function setupInquiryDownloadForm(form, { validate, close }) {
  const doc = form.ownerDocument;
  const win = doc.defaultView;
  const downloadButton = form.querySelector("[data-download-only-submit]");
  const writeButton = form.querySelector("[data-write-quotes-submit]");
  const customer = form.querySelector("input[name='customer_name']");
  const status = form.querySelector("[data-submit-wait-message]");
  const recovery = form.querySelector("[data-quote-recovery]");
  let busy = false;
  let writeLocked = false;

  const showStatus = (text, state) => {
    status.textContent = text;
    status.classList.remove("active", "error", "done");
    if (state) status.classList.add(state);
  };
  const showRecovery = (receipt) => {
    recovery.replaceChildren();
    if (receipt?.download_url && receipt.download_filename) {
      const attachment = doc.createElement("a");
      attachment.href = receipt.download_url;
      attachment.dataset.saveQuoteExcel = receipt.download_filename;
      attachment.download = receipt.download_filename;
      attachment.textContent = "重新保存本次报价附件";
      recovery.append(attachment, "　");
    }
    const quotes = doc.createElement("a");
    quotes.href = receipt?.quotes_url || form.dataset.quotesUrl;
    quotes.textContent = "前往报价记录";
    recovery.append(quotes);
    recovery.hidden = false;
  };
  const fetchAttachment = async (url, options) => {
    try {
      return await win.fetch(url, { credentials: "same-origin", ...options });
    } catch {
      throw new Error("获取 Excel 附件失败，请检查网络连接。");
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const writeQuotes = Boolean(writeButton && event.submitter === writeButton);
    if (busy || (writeQuotes && writeLocked) || !validate()) return;
    clearQuoteCustomerValidity(customer);
    if (writeQuotes && !customer.value.trim()) {
      const prompt = "写入报价前请填写客户名称。";
      showStatus(prompt, "error");
      customer.setCustomValidity(prompt);
      customer.reportValidity();
      clearQuoteCustomerValidity(customer);
      customer.focus();
      return;
    }
    if (!form.reportValidity()) return;

    busy = true;
    downloadButton.disabled = true;
    if (writeButton) writeButton.disabled = true;
    let awaitingWriteResponse = false;
    let receipt = null;
    try {
      const body = new win.FormData(form);
      showStatus("", "");
      const destination = await chooseExcelDestination(win, form.dataset.excelFilename, { writeQuotes });
      if (!destination) return;

      if (!writeQuotes) {
        showStatus("正在生成 Excel…", "active");
        const response = await fetchAttachment(form.action, { method: "POST", body });
        await saveExcelResponse(destination, response);
        showStatus(destination.kind === "manual" ? "Excel 附件已生成。" : "Excel 已保存。", "done");
        close();
        return;
      }

      showStatus("正在生成 Excel 并写入报价记录…", "active");
      awaitingWriteResponse = true;
      const response = await win.fetch(writeButton.formAction, {
        method: "POST", body, credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (payload?.ok === false) {
        awaitingWriteResponse = false;
        throw new Error(payload.error || "写入报价失败，请核对后重试。");
      }
      if (!response.ok || payload?.ok !== true) throw new Error("未收到报价写入确认。");
      receipt = payload;
      writeLocked = true;
      awaitingWriteResponse = false;
      if (!receipt.download_url) throw new Error("未取得本次报价的附件链接。");
      showStatus("报价已写入，正在保存本次附件…", "active");
      const attachment = await fetchAttachment(receipt.download_url);
      await saveExcelResponse(destination, attachment);
      showStatus(destination.kind === "manual" ? "本次报价附件已生成。" : "本次报价附件已保存。", "done");
      win.location.assign(receipt.quotes_url || form.dataset.quotesUrl);
    } catch (error) {
      const message = error instanceof TypeError ? "附件读取失败，请检查网络连接。" : error.message;
      if (receipt) {
        showStatus(`报价已写入，附件未保存。${message}`, "error");
        showRecovery(receipt);
      } else if (awaitingWriteResponse) {
        writeLocked = true;
        showStatus("未能确认报价是否已写入，请先到报价记录核对。", "error");
        showRecovery(null);
      } else {
        showStatus(message || "保存失败，请重试。", "error");
      }
    } finally {
      busy = false;
      downloadButton.disabled = false;
      if (writeButton) writeButton.disabled = writeLocked;
    }
  });
}

export const setupInquiryDownloads = (root, { validateAdjustments }) => {
  const doc = root.nodeType === 9 ? root : root.ownerDocument;
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

  const form = root.querySelector("[data-inquiry-download-form]");
  if (form) setupInquiryDownloadForm(form, { validate, close });

  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal?.classList.contains("open") && !doc.querySelector("dialog[open]")) close();
  });
};
