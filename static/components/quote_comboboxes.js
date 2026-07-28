// 报价相关表单的客户 / BLD 号 combobox 接线（quotes 页、询价下载弹窗共用）。
import { setupCombobox } from "./combobox.js?v=20260728-1";

const JSON_HEADERS = { Accept: "application/json", "X-Requested-With": "fetch" };

export function customerComboboxSource(lookupUrl) {
  return (query) =>
    fetch(`${lookupUrl}?q=${encodeURIComponent(query)}`, {
      credentials: "same-origin",
      headers: JSON_HEADERS,
    })
      .then((response) => (response.ok ? response.json() : []))
      .then((rows) => (Array.isArray(rows) ? rows : []).map((row) => ({ value: row.name, label: row.name })));
}

export function productComboboxSource(lookupUrl) {
  return (query) =>
    fetch(`${lookupUrl}?q=${encodeURIComponent(query)}`, {
      credentials: "same-origin",
      headers: JSON_HEADERS,
    })
      .then((response) => (response.ok ? response.json() : []))
      .then((rows) =>
        (Array.isArray(rows) ? rows : []).map((row) => ({
          value: row.bld_no,
          label: row.bld_no,
          detail: [row.item, row.series].filter(Boolean).join(" / "),
        })),
      );
}

export function customerQuickCreate(input, saveUrl) {
  return (name) => {
    const form = input.closest("form");
    const token = form?.querySelector("input[name='csrf_token']")?.value || "";
    return fetch(saveUrl, {
      method: "POST",
      body: new URLSearchParams({ name }),
      credentials: "same-origin",
      headers: { ...JSON_HEADERS, "X-CSRF-Token": token },
    }).then((response) =>
      response
        .json()
        .catch(() => null)
        .then((payload) => {
          if (!response.ok || !payload || payload.ok !== true) {
            throw new Error((payload && payload.error) || "create failed");
          }
          return { value: payload.customer.name, label: payload.customer.name };
        }),
    );
  };
}

export function setupQuoteFieldComboboxes(root = document) {
  root.querySelectorAll("[data-combobox]").forEach((container) => {
    if (container.dataset.comboboxReady === "1") return;
    container.dataset.comboboxReady = "1";
    const input = container.querySelector("input");
    if (!(input instanceof HTMLInputElement)) return;
    const lookupUrl = container.dataset.lookupUrl || "";
    if (!lookupUrl) return;
    if (container.dataset.combobox === "customer") {
      const canCreate = container.dataset.canCreate === "1" && container.dataset.createUrl;
      setupCombobox(input, {
        source: customerComboboxSource(lookupUrl),
        create: canCreate ? customerQuickCreate(input, container.dataset.createUrl) : null,
      });
      return;
    }
    if (container.dataset.combobox === "bld") {
      setupCombobox(input, { source: productComboboxSource(lookupUrl) });
    }
  });
}
