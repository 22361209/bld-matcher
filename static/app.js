document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  const oeInput = picker ? picker.querySelector(".file-picker-oe-input") : null;
  if (!name && !oeInput) return;

  input.addEventListener("change", () => {
    const fileName = input.files && input.files.length ? input.files[0].name : "";
    if (name) {
      name.textContent = fileName || "未选择任何文件";
    }
    if (oeInput && fileName) {
      oeInput.value = fileName;
      oeInput.readOnly = true;
    } else if (oeInput) {
      oeInput.readOnly = false;
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

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && quickOeImageModal?.classList.contains("open")) {
    closeQuickOeImageModal();
  }
});
