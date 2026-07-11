document.addEventListener("submit", (event) => {
  const submitter = event.submitter;
  if (!(submitter instanceof HTMLElement)) return;
  const message = submitter.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});
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
  const oeInput = picker ? picker.querySelector(".file-picker-text") : null;
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
    if (oeInput instanceof HTMLInputElement) {
      oeInput.value = "";
      oeInput.readOnly = false;
    }
    syncFilePicker();
  });

  if (!picker || !picker.hasAttribute("data-file-drop-zone")) return;

  const acceptedExts = (picker.dataset.fileDropAccept || input.getAttribute("accept") || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.startsWith("."));
  const acceptedExtLabel = acceptedExts.length ? acceptedExts.join(" / ") : "文件";
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
    setDropStatus(`松开即可导入 ${acceptedExtLabel} 文件`);
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
      setDropStatus(`仅支持 ${acceptedExtLabel} 文件`, true);
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
      setDropStatus(`已选择：${file.name}（多个文件时仅使用第一个可用文件）`);
    }
  });
});

document.querySelectorAll("form[data-submit-wait]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.submitInProgress === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitInProgress = "true";

    const waitText = form.dataset.submitWaitText || "正在处理，请稍候...";
    const buttonText = form.dataset.submitWaitButtonText || waitText;
    const buttons = Array.from(form.querySelectorAll("button[type='submit']"));
    const message = form.querySelector("[data-submit-wait-message]");
    buttons.forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) return;
      button.dataset.originalText = button.dataset.originalText || button.textContent || "";
      button.disabled = true;
      button.textContent = buttonText;
    });
    if (message instanceof HTMLElement) {
      message.textContent = waitText;
      message.classList.add("active");
      message.classList.remove("done", "error");
    }

    const resetAfter = Number.parseInt(form.dataset.submitWaitReset || "", 10);
    if (Number.isFinite(resetAfter) && resetAfter > 0) {
      window.setTimeout(() => {
        form.dataset.submitInProgress = "false";
        buttons.forEach((button) => {
          if (!(button instanceof HTMLButtonElement)) return;
          button.disabled = false;
          button.textContent = button.dataset.originalText || button.textContent;
        });
        if (message instanceof HTMLElement) {
          message.textContent = "已开始生成，请查看浏览器下载提示。";
          message.classList.remove("active", "error");
          message.classList.add("done");
        }
      }, resetAfter);
    }
  });
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
