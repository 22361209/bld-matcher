const PRODUCT_IMAGE_LIMIT = 5;
const PRODUCT_IMAGE_MAX_BYTES = 30 * 1024 * 1024;
const PRODUCT_IMAGE_MAX_PIXELS = 50_000_000;
const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp"]);
const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export function availableProductImageSlots(existingSlots, pendingSlots) {
  const occupied = new Set([...existingSlots, ...pendingSlots]);
  return Array.from({ length: PRODUCT_IMAGE_LIMIT }, (_value, index) => index + 1)
    .filter((slot) => !occupied.has(slot));
}

export function productImageIntakeCopy(imageCount) {
  const count = Math.max(0, Math.min(PRODUCT_IMAGE_LIMIT, imageCount));
  if (count === 0) {
    return {
      title: "拖入图片或选择文件",
      note: "JPG / PNG / WEBP，源文件单张不超过 30 MB；保存后自动转为 WebP",
    };
  }
  if (count === PRODUCT_IMAGE_LIMIT) {
    return {
      title: "已达到 5 张上限",
      note: "如需添加新图片，请先删除不需要的图片。",
    };
  }
  return {
    title: "添加更多图片",
    note: `还可添加 ${PRODUCT_IMAGE_LIMIT - count} 张；也可拖入或粘贴图片。`,
  };
}

const extensionFor = (file) => {
  const name = String(file?.name || "").toLowerCase();
  const match = name.match(/\.[^.]+$/);
  return match ? match[0] : "";
};

export function isProductDrawingFile(file) {
  const extension = extensionFor(file);
  return extension === ".pdf" && (!file?.type || file.type === "application/pdf");
}

export function productDrawingIntakeCopy(fileName = "") {
  return fileName
    ? { title: "已选择 PDF 图纸", note: fileName }
    : { title: "上传 PDF 图纸", note: "可选择、拖入或粘贴一个 PDF 文件。" };
}

const basicImageValidationError = (file) => {
  if (!(file instanceof File)) return "未读取到图片文件。";
  const extension = extensionFor(file);
  if (!IMAGE_EXTENSIONS.has(extension) || (file.type && !IMAGE_TYPES.has(file.type))) {
    return "仅支持 JPG、PNG、WEBP 图片。";
  }
  if (file.size === 0) return "图片文件为空。";
  if (file.size > PRODUCT_IMAGE_MAX_BYTES) return "单张产品图片源文件不能超过 30 MB。";
  return "";
};

const dimensionsValidationError = async (file) => {
  if (typeof Image === "undefined" || typeof URL === "undefined") return "";
  const source = URL.createObjectURL(file);
  try {
    const dimensions = await new Promise((resolve) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => resolve(null);
      image.src = source;
    });
    if (dimensions && dimensions.width * dimensions.height > PRODUCT_IMAGE_MAX_PIXELS) {
      return "产品图片总像素不能超过 5000 万。";
    }
  } finally {
    URL.revokeObjectURL(source);
  }
  return "";
};

const imageValidationError = async (file) => basicImageValidationError(file) || dimensionsValidationError(file);

const assignInputFile = (input, file) => {
  if (typeof DataTransfer === "undefined") return false;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  return true;
};

const clipboardFiles = (event) => {
  const clipboard = event.clipboardData;
  if (!clipboard) return [];
  const directFiles = Array.from(clipboard.files || []).filter((file) => file instanceof File);
  if (directFiles.length) return directFiles;
  return Array.from(clipboard.items || [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file) => file instanceof File);
};

const clipboardImageFiles = (event) => clipboardFiles(event).filter((file) => file.type.startsWith("image/"));

function mountProductMediaUploader(root) {
  const form = root.closest("form");
  const batchInput = root.querySelector("[data-product-media-batch-input]");
  const intake = root.querySelector("[data-product-media-intake]");
  const browseButton = root.querySelector("[data-product-media-browse]");
  const intakeTitle = root.querySelector("[data-product-media-intake-title]");
  const intakeNote = root.querySelector("[data-product-media-intake-note]");
  const count = root.querySelector("[data-product-media-count]");
  const status = root.querySelector("[data-product-media-upload-status]");
  const tiles = Array.from(root.querySelectorAll("[data-product-media-slot]"))
    .map((tile) => {
      const slot = Number.parseInt(tile.dataset.productMediaSlot || "", 10);
      const input = tile.querySelector("[data-product-media-slot-input]");
      const pendingPreview = tile.querySelector("[data-product-media-pending-preview]");
      const existingPreview = tile.querySelector(".product-media-existing-preview");
      const clearButton = tile.querySelector("[data-product-media-clear-pending]");
      const deleteButton = tile.querySelector(".product-media-delete");
      return {
        slot,
        tile,
        input,
        pendingPreview,
        existingPreview,
        clearButton,
        deleteButton,
        hasExisting: tile.dataset.productMediaHasExisting === "true",
      };
    })
    .filter((entry) => Number.isInteger(entry.slot) && entry.input instanceof HTMLInputElement);
  if (!(batchInput instanceof HTMLInputElement) || !(intake instanceof HTMLElement) || !tiles.length) return;

  const bySlot = new Map(tiles.map((entry) => [entry.slot, entry]));
  const existingSlots = new Set(tiles.filter((entry) => entry.hasExisting).map((entry) => entry.slot));
  const pendingFiles = new Map();
  const previewUrls = new Map();
  let dragDepth = 0;

  const setStatus = (message = "", isError = false) => {
    if (!(status instanceof HTMLElement)) return;
    status.textContent = message;
    status.classList.toggle("error", isError);
  };

  const clearPreviewUrl = (slot) => {
    const source = previewUrls.get(slot);
    if (source) URL.revokeObjectURL(source);
    previewUrls.delete(slot);
  };

  const render = () => {
    const imageCount = new Set([...existingSlots, ...pendingFiles.keys()]).size;
    const copy = productImageIntakeCopy(imageCount);
    if (count instanceof HTMLElement) count.textContent = `${imageCount} / ${PRODUCT_IMAGE_LIMIT}`;
    if (intakeTitle instanceof HTMLElement) intakeTitle.textContent = copy.title;
    if (intakeNote instanceof HTMLElement) intakeNote.textContent = copy.note;
    intake.classList.toggle("has-media", imageCount > 0);

    tiles.forEach((entry) => {
      const file = pendingFiles.get(entry.slot);
      const visible = entry.hasExisting || Boolean(file);
      entry.tile.hidden = !visible;
      entry.tile.classList.toggle("has-pending-file", Boolean(file));
      if (entry.pendingPreview instanceof HTMLImageElement) {
        entry.pendingPreview.hidden = !file;
        if (file) {
          clearPreviewUrl(entry.slot);
          const source = URL.createObjectURL(file);
          previewUrls.set(entry.slot, source);
          entry.pendingPreview.src = source;
          entry.pendingPreview.alt = `待保存的图片 ${entry.slot}`;
        } else {
          entry.pendingPreview.removeAttribute("src");
          entry.pendingPreview.alt = "";
        }
      }
      if (entry.existingPreview instanceof HTMLElement) entry.existingPreview.hidden = Boolean(file);
      if (entry.clearButton instanceof HTMLButtonElement) entry.clearButton.hidden = !file;
      if (entry.deleteButton instanceof HTMLButtonElement) entry.deleteButton.hidden = Boolean(file);
    });
  };

  const resetPendingFiles = () => {
    pendingFiles.forEach((_file, slot) => {
      const entry = bySlot.get(slot);
      if (entry?.input instanceof HTMLInputElement) entry.input.value = "";
      clearPreviewUrl(slot);
    });
    pendingFiles.clear();
    batchInput.value = "";
    setStatus();
    render();
  };

  const appendFiles = async (files) => {
    const availableSlots = availableProductImageSlots(existingSlots, pendingFiles.keys());
    if (!availableSlots.length) {
      setStatus("最多只能保存 5 张产品图片；如需更换，请使用图片卡片中的“替换”。", true);
      return;
    }
    const selectedFiles = Array.from(files || []).filter((file) => file instanceof File);
    const errors = [];
    let added = 0;
    for (const file of selectedFiles) {
      const slot = availableSlots[added];
      if (!slot) break;
      const error = await imageValidationError(file);
      if (error) {
        errors.push(`${file.name || "该文件"}：${error}`);
        continue;
      }
      const entry = bySlot.get(slot);
      if (!entry || !assignInputFile(entry.input, file)) {
        setStatus("当前浏览器不支持此上传方式，请点击选择文件。", true);
        return;
      }
      pendingFiles.set(slot, file);
      added += 1;
    }
    batchInput.value = "";
    render();
    const skipped = Math.max(0, selectedFiles.length - added - errors.length);
    if (errors.length) {
      setStatus(`${added ? `已添加 ${added} 张。` : ""}${errors[0]}${skipped ? ` 其余 ${skipped} 张超过剩余位置。` : ""}`, true);
    } else if (added) {
      setStatus(`已添加 ${added} 张图片；保存后将自动生成 500 KB 以内的大图和缩略图${skipped ? `；其余 ${skipped} 张超过剩余位置，未添加。` : ""}`);
    }
  };

  browseButton?.addEventListener("click", () => batchInput.click());
  batchInput.addEventListener("change", () => appendFiles(batchInput.files));

  tiles.forEach((entry) => {
    entry.clearButton?.addEventListener("click", () => {
      entry.input.value = "";
      pendingFiles.delete(entry.slot);
      clearPreviewUrl(entry.slot);
      setStatus();
      render();
    });
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
    appendFiles(event.dataTransfer.files);
  });
  intake.addEventListener("paste", (event) => {
    const files = clipboardImageFiles(event);
    if (!files.length) return;
    event.preventDefault();
    appendFiles(files);
  });
  form?.addEventListener("reset", () => window.setTimeout(resetPendingFiles, 0));

  render();
}

function mountProductDrawingUploader(intake) {
  const root = intake.closest(".product-media-edit");
  const form = intake.closest("form");
  const input = root?.querySelector("[data-product-media-drawing-input]");
  const browseButton = intake.querySelector("[data-product-media-drawing-browse]");
  const title = intake.querySelector("[data-product-media-drawing-title]");
  const note = intake.querySelector("[data-product-media-drawing-note]");
  const status = intake.querySelector("[data-product-media-drawing-status]");
  if (!(input instanceof HTMLInputElement)) return;

  let selectedFile = input.files?.[0] || null;
  let dragDepth = 0;

  const render = () => {
    const copy = productDrawingIntakeCopy(selectedFile?.name || "");
    if (title instanceof HTMLElement) title.textContent = copy.title;
    if (note instanceof HTMLElement) note.textContent = copy.note;
    intake.classList.toggle("has-media", Boolean(selectedFile));
  };

  const setStatus = (message = "", isError = false) => {
    if (!(status instanceof HTMLElement)) return;
    status.textContent = message;
    status.classList.toggle("error", isError);
  };

  const setDrawingFile = (file) => {
    if (!(file instanceof File) || !isProductDrawingFile(file)) {
      setStatus("仅支持 PDF 图纸文件。", true);
      return;
    }
    if (input.files?.[0] !== file && !assignInputFile(input, file)) {
      setStatus("当前浏览器不支持此上传方式，请点击选择文件。", true);
      return;
    }
    selectedFile = file;
    setStatus(`已选择 ${file.name}；保存后生效。`);
    render();
  };

  browseButton?.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) setDrawingFile(file);
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
    setDrawingFile(Array.from(event.dataTransfer.files).find(isProductDrawingFile));
  });
  intake.addEventListener("paste", (event) => {
    const file = clipboardFiles(event).find(isProductDrawingFile);
    if (!file) return;
    event.preventDefault();
    setDrawingFile(file);
  });
  form?.addEventListener("reset", () => window.setTimeout(() => {
    selectedFile = null;
    setStatus();
    render();
  }, 0));

  render();
}

if (typeof document !== "undefined") {
  document.querySelectorAll("[data-product-media-upload]").forEach(mountProductMediaUploader);
  document.querySelectorAll("[data-product-media-drawing-intake]").forEach(mountProductDrawingUploader);
}
