const shippingModals = Array.from(document.querySelectorAll("#shipping-template-select-modal, #shipping-template-menu-modal, #shipping-template-upload-modal, #shipping-template-batch-modal"));

const closeShippingModals = () => {
  shippingModals.forEach((modal) => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  });
  document.body.classList.remove("modal-open");
};

const openShippingModal = (modal) => {
  if (!(modal instanceof HTMLElement)) return;
  closeShippingModals();
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  const focusTarget = modal.querySelector("select, input, textarea, button:not([data-close-shipping-modal])");
  if (focusTarget instanceof HTMLElement) {
    focusTarget.focus();
  }
};

document.querySelector("[data-open-shipping-template-select-modal]")?.addEventListener("click", () => {
  const fileInput = document.querySelector("#shipping-data-input");
  if (fileInput instanceof HTMLInputElement && (!fileInput.files || fileInput.files.length === 0)) {
    const picker = fileInput.closest(".file-picker");
    const dropStatus = picker?.querySelector("[data-file-drop-status]");
    if (dropStatus instanceof HTMLElement) {
      dropStatus.textContent = "请先选择或拖入发货数据文件。";
      dropStatus.classList.add("error");
    }
    return;
  }
  openShippingModal(document.querySelector("#shipping-template-select-modal"));
});

document.querySelector("[data-open-shipping-template-menu-modal]")?.addEventListener("click", () => {
  openShippingModal(document.querySelector("#shipping-template-menu-modal"));
});

document.querySelectorAll("[data-open-shipping-template-action-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.openShippingTemplateActionModal;
    openShippingModal(document.querySelector(`#shipping-template-${target}-modal`));
  });
});

document.querySelectorAll("[data-close-shipping-modal]").forEach((element) => {
  element.addEventListener("click", closeShippingModals);
});


document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && shippingModals.some((modal) => modal.classList.contains("open"))) {
    closeShippingModals();
  }
});
