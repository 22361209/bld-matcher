import { chooseExcelDestination, saveExcelResponse } from "./excel_save.js?v=20261122-1";

export function setupExcelSaveLinks(doc) {
  const win = doc.defaultView;
  const busy = new WeakSet();
  doc.addEventListener("click", async event => {
    const link = event.target.closest?.("a[data-save-quote-excel]");
    if (!link || event.defaultPrevented) return;
    event.preventDefault();
    if (busy.has(link)) return;
    busy.add(link);
    try {
      const destination = await chooseExcelDestination(win, link.dataset.saveQuoteExcel);
      if (!destination) return;
      const response = await win.fetch(link.href, { credentials: "same-origin" });
      await saveExcelResponse(destination, response);
    } catch (error) {
      win.alert(error instanceof TypeError ? "无法取得报价文件，请检查网络后重试。" : error.message);
    } finally {
      busy.delete(link);
    }
  });
}

if (typeof document !== "undefined") setupExcelSaveLinks(document);
