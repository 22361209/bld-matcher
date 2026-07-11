if (document.body.dataset.page === "materials.workspace") {
  const materialStatusInput = document.querySelector("#material-status-input");
  document.querySelectorAll("[data-material-status]").forEach((button) => {
    button.addEventListener("click", () => {
      materialStatusInput.value = button.dataset.materialStatus;
    });
  });
  const toolbarPopovers = Array.from(document.querySelectorAll("details.toolbar-popover"));
  toolbarPopovers.forEach((popover) => {
    popover.addEventListener("toggle", () => {
      if (!popover.open) return;
      toolbarPopovers.forEach((other) => {
        if (other !== popover) other.open = false;
      });
    });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("details.toolbar-popover")) return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
  });
  const materialModal = document.querySelector("#material-modal");
  const materialForm = materialModal?.querySelector("form");
  const openMaterialModal = () => {
    materialModal.classList.add("open");
    materialModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    materialModal.querySelector("input[name='model']")?.focus();
  };
  const closeMaterialModal = () => {
    materialModal.classList.remove("open");
    materialModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    materialForm?.reset();
  };
  document.querySelector("[data-open-material-modal]")?.addEventListener("click", openMaterialModal);
  document.querySelectorAll("[data-close-material-modal]").forEach((element) => {
    element.addEventListener("click", closeMaterialModal);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
    if (materialModal?.classList.contains("open")) {
      closeMaterialModal();
    }
  });
}
