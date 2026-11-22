export function inquiryAttachmentFilename(response) {
  if (!response?.ok || response.redirected) return null;
  const disposition = response.headers.get("Content-Disposition") || "";
  if (!/^attachment(?:\s*;|\s*$)/i.test(disposition)) return null;
  const encoded = /;\s*filename\*\s*=\s*UTF-8'[^']*'([^;]+)/i.exec(disposition);
  let name;
  if (encoded) {
    try {
      name = decodeURIComponent(encoded[1].trim());
    } catch {
      return null;
    }
  } else {
    const plain = /;\s*filename\s*=\s*(?:"([^"]+)"|([^;]+))/i.exec(disposition);
    name = (plain?.[1] || plain?.[2] || "").trim();
  }
  return /\.xlsx?$/i.test(name) ? name : null;
}

export async function chooseExcelDestination(win, suggestedName, { writeQuotes = false } = {}) {
  if (typeof win.showSaveFilePicker === "function") {
    const name = suggestedName || "报价结果.xlsx";
    const legacy = /\.xls$/i.test(name);
    const mime = legacy ? "application/vnd.ms-excel" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    try {
      return await win.showSaveFilePicker({
        id: "quote-excel",
        suggestedName: name,
        types: [{ description: "Excel 工作簿", accept: { [mime]: [legacy ? ".xls" : ".xlsx"] } }],
      });
    } catch (error) {
      if (error.name === "AbortError") return null;
      if (error.name !== "SecurityError") throw new Error("无法打开保存窗口，请重新选择保存位置。");
    }
  }
  const action = writeQuotes ? "继续将写入报价记录并准备附件。" : "继续将准备报价文件。";
  const accepted = win.confirm("当前浏览器需要通过“链接另存为…”选择保存位置。" + action);
  return accepted ? { kind: "manual", win } : null;
}

async function showManualSaveLink(win, blob, filename) {
  const doc = win.document;
  const dialog = doc.getElementById("excel-save-as-template").content.firstElementChild.cloneNode(true);
  const link = dialog.querySelector("a");
  const url = win.URL.createObjectURL(blob);
  link.href = url;
  link.download = link.textContent = filename;
  link.addEventListener("click", event => event.preventDefault());
  dialog.querySelector("button").addEventListener("click", () => dialog.close());
  doc.body.append(dialog);
  try {
    const closed = new Promise(resolve => dialog.addEventListener("close", resolve, { once: true }));
    dialog.showModal();
    await closed;
  } finally {
    dialog.remove();
    win.URL.revokeObjectURL(url);
  }
}

export async function saveExcelResponse(destination, response) {
  const filename = inquiryAttachmentFilename(response);
  if (!filename) throw new Error("未取得报价 Excel 附件，请检查登录状态后重试。");
  const blob = await response.blob();
  if (!blob.size) throw new Error("报价文件为空，请重新获取。");
  if (destination.kind === "manual") return showManualSaveLink(destination.win, blob, filename);

  let stream;
  try {
    stream = await destination.createWritable();
    await stream.write(blob);
    await stream.close();
  } catch {
    if (stream) await stream.abort().catch(() => {});
    throw new Error("文件保存失败，请检查目标文件夹的写入权限和剩余空间。");
  }
}
