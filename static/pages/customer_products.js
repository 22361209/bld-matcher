/* 客户详情页「客户产品编码」页签：新增/编辑弹窗、图纸预览与上传弹窗。 */

const JSON_HEADERS = { Accept: "application/json", "X-Requested-With": "fetch" };

const csrfToken = () => document.querySelector("input[name='csrf_token']")?.value || "";

export const isCustomerDrawingFileName = (name) => /\.(pdf|png|jpe?g|webp)$/.test(String(name || "").toLowerCase());

export const customerDrawingDropError = (files) => {
  const count = Number(files?.length || 0);
  if (count > 1) return "每次只能拖入一份图纸，请重新选择。";
  const file = files?.[0];
  if (!file || !isCustomerDrawingFileName(file.name)) return "仅支持 PDF、PNG、JPG、WEBP 图纸。";
  return "";
};

export const assignCustomerDrawingFile = (
  input,
  file,
  droppedFiles = null,
  DataTransferConstructor = globalThis.DataTransfer,
) => {
  if (!input || !file) return false;
  if (droppedFiles && droppedFiles.length !== 1) {
    try {
      input.value = "";
    } catch (_error) {
      // The drop handler also clears real file inputs; keep this helper safe for non-DOM callers.
    }
    return false;
  }
  if (droppedFiles) {
    try {
      input.files = droppedFiles;
      if (input.files?.[0] === file) return true;
    } catch (_error) {
      // Some engines only accept a FileList created by their own DataTransfer implementation.
    }
  }
  if (typeof DataTransferConstructor !== "function") return false;
  try {
    const transfer = new DataTransferConstructor();
    transfer.items.add(file);
    input.files = transfer.files;
    return input.files?.[0] === file;
  } catch (_error) {
    return false;
  }
};

if (typeof document !== "undefined" && document.body?.dataset.page === "customers.detail") {
  const resetModalPosition = (modal) => {
    const panel = modal?.querySelector("[data-draggable-modal-panel]");
    if (!panel) return;
    panel.style.transform = "";
    panel.dataset.dragX = "0";
    panel.dataset.dragY = "0";
  };
  const openModal = (modal) => {
    if (!modal) return;
    resetModalPosition(modal);
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  };
  const closeModal = (modal) => {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    resetModalPosition(modal);
  };

  document.querySelectorAll("[data-draggable-modal-panel]").forEach((panel) => {
    const handle = panel.querySelector("[data-modal-drag-handle]");
    if (!handle) return;
    panel.dataset.dragX = "0";
    panel.dataset.dragY = "0";
    handle.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = Number(panel.dataset.dragX || 0);
      const originY = Number(panel.dataset.dragY || 0);
      const onMove = (moveEvent) => {
        const nextX = originX + moveEvent.clientX - startX;
        const nextY = originY + moveEvent.clientY - startY;
        panel.dataset.dragX = String(nextX);
        panel.dataset.dragY = String(nextY);
        panel.style.transform = `translate(${nextX}px, ${nextY}px)`;
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });

  // 新增商品弹窗：BLD 号候选联动客户产品编码。
  const productModal = document.querySelector("[data-customer-product-modal]");
  const productForm = document.querySelector("[data-customer-product-form]");
  const bldInput = productForm?.querySelector("[data-customer-product-bld]");
  const codeInput = productForm?.querySelector("[data-customer-product-code]");
  const createDrawingIntake = productForm?.querySelector("[data-customer-product-create-drawing-intake]");
  const createDrawingBrowse = productForm?.querySelector("[data-customer-product-create-drawing-browse]");
  const createDrawingInput = productForm?.querySelector("[data-customer-product-create-drawing-input]");
  const createDrawingTitle = productForm?.querySelector("[data-customer-product-create-drawing-title]");
  const createDrawingNote = productForm?.querySelector("[data-customer-product-create-drawing-note]");
  const createDrawingStatus = productForm?.querySelector("[data-customer-product-create-drawing-status]");
  let createDrawingDragDepth = 0;

  const resetCreateDrawingSelection = () => {
    createDrawingDragDepth = 0;
    createDrawingIntake?.classList.remove("drag-over", "has-media");
    if (createDrawingTitle instanceof HTMLElement) createDrawingTitle.textContent = "拖入客户图纸或点击选择";
    if (createDrawingNote instanceof HTMLElement) {
      createDrawingNote.textContent = "支持 PDF / PNG / JPG / WEBP，单个文件不超过 20 MB";
    }
    if (createDrawingStatus instanceof HTMLElement) {
      createDrawingStatus.textContent = "";
      createDrawingStatus.classList.remove("error");
    }
  };

  const showCreateDrawingSelection = (file) => {
    if (!(file instanceof File) || !isCustomerDrawingFileName(file.name)) {
      if (createDrawingInput instanceof HTMLInputElement) createDrawingInput.value = "";
      resetCreateDrawingSelection();
      if (createDrawingStatus instanceof HTMLElement) {
        createDrawingStatus.textContent = "仅支持 PDF、PNG、JPG、WEBP 客户图纸。";
        createDrawingStatus.classList.add("error");
      }
      return false;
    }
    createDrawingIntake?.classList.add("has-media");
    if (createDrawingTitle instanceof HTMLElement) createDrawingTitle.textContent = file.name;
    if (createDrawingNote instanceof HTMLElement) createDrawingNote.textContent = "保存商品时将一并上传为客户图纸 V1";
    if (createDrawingStatus instanceof HTMLElement) {
      createDrawingStatus.textContent = `已选择 ${file.name}`;
      createDrawingStatus.classList.remove("error");
    }
    return true;
  };

  if (createDrawingIntake instanceof HTMLElement && createDrawingInput instanceof HTMLInputElement) {
    createDrawingBrowse?.addEventListener("click", () => createDrawingInput.click());
    createDrawingInput.addEventListener("change", () => {
      const file = createDrawingInput.files?.[0];
      if (file) showCreateDrawingSelection(file);
      else resetCreateDrawingSelection();
    });
    createDrawingIntake.addEventListener("dragenter", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      createDrawingDragDepth += 1;
      createDrawingIntake.classList.add("drag-over");
    });
    createDrawingIntake.addEventListener("dragover", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    createDrawingIntake.addEventListener("dragleave", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      createDrawingDragDepth -= 1;
      if (createDrawingDragDepth > 0) return;
      createDrawingIntake.classList.remove("drag-over");
    });
    createDrawingIntake.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      createDrawingDragDepth = 0;
      createDrawingIntake.classList.remove("drag-over");
      const droppedFiles = event.dataTransfer.files;
      const dropError = customerDrawingDropError(droppedFiles);
      if (dropError) {
        createDrawingInput.value = "";
        resetCreateDrawingSelection();
        if (createDrawingStatus instanceof HTMLElement) {
          createDrawingStatus.textContent = dropError;
          createDrawingStatus.classList.add("error");
        }
        return;
      }
      const file = droppedFiles[0];
      if (!assignCustomerDrawingFile(createDrawingInput, file, droppedFiles)) {
        createDrawingInput.value = "";
        resetCreateDrawingSelection();
        if (createDrawingStatus instanceof HTMLElement) {
          createDrawingStatus.textContent = "当前浏览器无法接收拖入文件，请点击选择图纸。";
          createDrawingStatus.classList.add("error");
        }
        return;
      }
      showCreateDrawingSelection(file);
    });
  }

  document.querySelector("[data-open-customer-product-modal]")?.addEventListener("click", () => {
    productForm?.reset();
    resetCreateDrawingSelection();
    openModal(productModal);
    bldInput?.focus();
  });
  document.querySelectorAll("[data-close-customer-product-modal]").forEach((element) => {
    element.addEventListener("click", () => closeModal(productModal));
  });
  bldInput?.addEventListener("input", () => {
    const value = bldInput.value.trim().toUpperCase();
    if (!value || !(codeInput instanceof HTMLInputElement)) return;
    const option = Array.from(document.querySelectorAll("#customer-product-bld-options option"))
      .find((candidate) => candidate.value.toUpperCase() === value);
    if (option) codeInput.value = option.dataset.code || "";
  });

  // 编辑商品弹窗：普通表单 POST，动作地址与字段来自行按钮。
  const editModal = document.querySelector("[data-customer-product-edit-modal]");
  const editForm = document.querySelector("[data-customer-product-edit-form]");
  document.querySelectorAll("[data-close-customer-product-edit-modal]").forEach((element) => {
    element.addEventListener("click", () => closeModal(editModal));
  });

  // 图纸预览/上传弹窗。
  const drawingModal = document.querySelector("[data-customer-drawing-modal]");
  const stage = drawingModal?.querySelector("[data-drawing-stage]");
  const caption = drawingModal?.querySelector("[data-drawing-caption]");
  const prevButton = drawingModal?.querySelector("[data-drawing-prev]");
  const nextButton = drawingModal?.querySelector("[data-drawing-next]");
  const downloadLink = drawingModal?.querySelector("[data-drawing-download]");
  const setCurrentButton = drawingModal?.querySelector("[data-drawing-set-current]");
  const importButton = drawingModal?.querySelector("[data-drawing-import-catalog]");
  const intake = drawingModal?.querySelector("[data-drawing-intake]");
  const browseButton = drawingModal?.querySelector("[data-drawing-browse]");
  const fileInput = drawingModal?.querySelector("[data-drawing-file-input]");
  const revisionInput = drawingModal?.querySelector("[data-drawing-revision-input]");
  const uploadStatus = drawingModal?.querySelector("[data-drawing-upload-status]");
  let drawingState = null;
  let dragDepth = 0;

  const setUploadStatus = (message = "", isError = false) => {
    if (!(uploadStatus instanceof HTMLElement)) return;
    uploadStatus.textContent = message;
    uploadStatus.classList.toggle("error", isError);
  };

  const versionText = (version) => {
    const parts = [`V${version.version_no}`];
    if (version.revision_label) parts.push(version.revision_label);
    const date = String(version.created_at || "").slice(0, 10);
    if (date) parts.push(date);
    return parts.join(" · ");
  };

  const renderDrawing = () => {
    if (!drawingState || !(stage instanceof HTMLElement)) return;
    const version = drawingState.versions[drawingState.index] || null;
    stage.innerHTML = "";
    if (!version) {
      const empty = document.createElement("p");
      empty.className = "drawing-modal-empty";
      empty.textContent = "暂无图纸，可在下方上传第一个版本。";
      stage.appendChild(empty);
    } else if (version.previewable && version.content_type === "application/pdf") {
      const frame = document.createElement("iframe");
      frame.className = "drawing-modal-frame";
      frame.src = version.preview_url;
      frame.title = `${drawingState.bldNo} ${drawingState.kindLabel} V${version.version_no}`;
      stage.appendChild(frame);
    } else if (version.previewable) {
      const image = document.createElement("img");
      image.src = version.preview_url;
      image.alt = `${drawingState.bldNo} ${drawingState.kindLabel} V${version.version_no}`;
      stage.appendChild(image);
    } else {
      const unsupported = document.createElement("p");
      unsupported.className = "drawing-modal-empty";
      unsupported.textContent = "该格式不支持在线预览，请下载查看。";
      stage.appendChild(unsupported);
    }
    if (caption instanceof HTMLElement) {
      caption.textContent = version
        ? `${drawingState.bldNo} · ${drawingState.kindLabel} — ${version.is_current ? "当前版本" : "历史版本"}：${versionText(version)}`
        : `${drawingState.bldNo} · ${drawingState.kindLabel} — 暂无版本`;
    }
    const multiple = drawingState.versions.length > 1;
    if (prevButton) prevButton.hidden = !multiple;
    if (nextButton) nextButton.hidden = !multiple;
    if (downloadLink instanceof HTMLAnchorElement) {
      downloadLink.hidden = !version;
      if (version) downloadLink.href = version.download_url;
    }
    if (setCurrentButton) setCurrentButton.hidden = !version || version.is_current;
    if (importButton) {
      importButton.hidden = drawingState.kind !== "bld";
      importButton.disabled = !drawingState.catalogHasDrawing;
      importButton.title = drawingState.catalogHasDrawing
        ? "将产品目录图纸引入为新的 BLD 图纸版本"
        : "产品目录暂无图纸";
    }
  };

  const updateOpenerBadge = () => {
    const opener = drawingState?.opener;
    if (!(opener instanceof HTMLElement)) return;
    const current = drawingState.versions.find((version) => version.is_current);
    let badge = opener.querySelector(".customer-drawing-badge");
    const label = opener.querySelector(".customer-drawing-open-label");
    if (current) {
      const text = `V${current.version_no}${current.revision_label ? ` · ${current.revision_label}` : ""}`;
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "customer-drawing-badge";
        opener.prepend(badge);
      }
      badge.textContent = text;
      if (label) label.textContent = "预览";
    } else {
      badge?.remove();
      if (label) label.textContent = "未上传";
    }
  };

  const refreshDrawingVersions = async (keepVersionNo = 0) => {
    const response = await fetch(drawingState.versionsUrl, {
      credentials: "same-origin",
      headers: JSON_HEADERS,
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "图纸版本读取失败，请稍后重试。");
    drawingState.versions = payload.versions || [];
    drawingState.catalogHasDrawing = Boolean(payload.catalog_has_drawing);
    let index = keepVersionNo
      ? drawingState.versions.findIndex((version) => version.version_no === keepVersionNo)
      : drawingState.versions.findIndex((version) => version.is_current);
    if (index < 0) index = 0;
    drawingState.index = index;
    renderDrawing();
    updateOpenerBadge();
  };

  const openDrawingModal = async (button) => {
    drawingState = {
      opener: button,
      kind: button.dataset.kind || "bld",
      kindLabel: button.dataset.kindLabel || "图纸",
      bldNo: button.dataset.bldNo || "",
      versionsUrl: button.dataset.versionsUrl,
      uploadUrl: button.dataset.uploadUrl,
      currentUrl: button.dataset.currentUrl,
      importUrl: button.dataset.importUrl || "",
      catalogHasDrawing: button.dataset.catalogHasDrawing === "true",
      versions: [],
      index: 0,
    };
    setUploadStatus();
    if (stage instanceof HTMLElement) stage.innerHTML = "";
    if (caption instanceof HTMLElement) caption.textContent = "正在读取图纸版本…";
    openModal(drawingModal);
    try {
      await refreshDrawingVersions();
    } catch (error) {
      if (caption instanceof HTMLElement) caption.textContent = error?.message || "图纸版本读取失败。";
    }
  };

  const closeDrawingModal = () => {
    closeModal(drawingModal);
    if (stage instanceof HTMLElement) stage.innerHTML = "";
    drawingState = null;
    dragDepth = 0;
  };

  document.querySelectorAll("[data-close-customer-drawing-modal]").forEach((element) => {
    element.addEventListener("click", closeDrawingModal);
  });

  prevButton?.addEventListener("click", () => {
    if (!drawingState?.versions.length) return;
    drawingState.index = (drawingState.index + drawingState.versions.length - 1) % drawingState.versions.length;
    renderDrawing();
  });
  nextButton?.addEventListener("click", () => {
    if (!drawingState?.versions.length) return;
    drawingState.index = (drawingState.index + 1) % drawingState.versions.length;
    renderDrawing();
  });

  setCurrentButton?.addEventListener("click", async () => {
    const version = drawingState?.versions[drawingState.index];
    if (!version || !drawingState.currentUrl) return;
    setCurrentButton.disabled = true;
    try {
      const body = new FormData();
      body.append("version_no", String(version.version_no));
      const response = await fetch(drawingState.currentUrl, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { ...JSON_HEADERS, "X-CSRF-Token": csrfToken() },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "设置当前版本失败，请稍后重试。");
      await refreshDrawingVersions(version.version_no);
      setUploadStatus(`已切换为 V${version.version_no} 作为当前版本。`);
    } catch (error) {
      setUploadStatus(error?.message || "设置结果不确定，请刷新页面确认。", true);
    } finally {
      setCurrentButton.disabled = false;
    }
  });

  importButton?.addEventListener("click", async () => {
    if (!drawingState?.importUrl) return;
    importButton.disabled = true;
    setUploadStatus("正在引入产品目录图纸…");
    try {
      const response = await fetch(drawingState.importUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { ...JSON_HEADERS, "X-CSRF-Token": csrfToken() },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "引入失败，请稍后重试。");
      await refreshDrawingVersions(payload.version_no);
      setUploadStatus(`产品目录图纸已引入为 V${payload.version_no}，并设为当前版本。`);
    } catch (error) {
      setUploadStatus(error?.message || "引入结果不确定，请刷新页面确认。", true);
    } finally {
      importButton.disabled = !drawingState?.catalogHasDrawing;
    }
  });

  const uploadDrawingFile = async (file) => {
    if (!drawingState?.uploadUrl) return;
    if (!(file instanceof File)) {
      setUploadStatus("未读取到图纸文件。", true);
      return;
    }
    if (!isCustomerDrawingFileName(file.name)) {
      setUploadStatus("仅支持 PDF、PNG、JPG、WEBP 图纸文件。", true);
      return;
    }
    const body = new FormData();
    body.append("files", file);
    const revision = revisionInput instanceof HTMLInputElement ? revisionInput.value.trim() : "";
    if (revision) body.append("revision_label", revision);
    setUploadStatus(`正在上传 ${file.name}…`);
    try {
      const response = await fetch(drawingState.uploadUrl, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { ...JSON_HEADERS, "X-CSRF-Token": csrfToken() },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "上传失败，请稍后重试。");
      if (revisionInput instanceof HTMLInputElement) revisionInput.value = "";
      await refreshDrawingVersions(payload.version_no);
      setUploadStatus(`已上传 V${payload.version_no}，并设为当前版本。`);
    } catch (error) {
      setUploadStatus(error?.message || "上传结果不确定，请刷新页面确认。", true);
    }
  };

  if (intake instanceof HTMLElement && fileInput instanceof HTMLInputElement) {
    browseButton?.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      fileInput.value = "";
      if (file) uploadDrawingFile(file);
    });
    intake.addEventListener("dragenter", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      dragDepth += 1;
      intake.classList.add("drag-over");
    });
    intake.addEventListener("dragover", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    intake.addEventListener("dragleave", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      dragDepth -= 1;
      if (dragDepth > 0) return;
      intake.classList.remove("drag-over");
    });
    intake.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      dragDepth = 0;
      intake.classList.remove("drag-over");
      const droppedFiles = event.dataTransfer.files;
      const dropError = customerDrawingDropError(droppedFiles);
      if (dropError) {
        setUploadStatus(dropError, true);
        return;
      }
      uploadDrawingFile(droppedFiles[0]);
    });
  }

  document.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-open-customer-product-edit]");
    if (editButton && editForm instanceof HTMLFormElement) {
      editForm.action = editButton.dataset.updateUrl || "";
      const bldField = editForm.querySelector("[data-customer-product-edit-bld]");
      const codeField = editForm.querySelector("[data-customer-product-edit-code]");
      const nameField = editForm.querySelector("[data-customer-product-edit-name]");
      if (bldField instanceof HTMLInputElement) bldField.value = editButton.dataset.bldNo || "";
      if (codeField instanceof HTMLInputElement) codeField.value = editButton.dataset.code || "";
      if (nameField instanceof HTMLInputElement) nameField.value = editButton.dataset.name || "";
      openModal(editModal);
      codeField?.focus();
      return;
    }
    const drawingButton = event.target.closest("[data-open-drawing-modal]");
    if (drawingButton) openDrawingModal(drawingButton);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (drawingModal?.classList.contains("open")) closeDrawingModal();
    if (editModal?.classList.contains("open")) closeModal(editModal);
    if (productModal?.classList.contains("open")) closeModal(productModal);
  });
}
