const createDialog = document.querySelector("#customer-create-dialog");
const createForm = createDialog?.querySelector(".customer-create-form");

const closeCreateDialog = () => {
  if (!createDialog?.open) return;
  createDialog.close();
  createForm?.reset();
};

document.querySelector("[data-open-customer-create-modal]")?.addEventListener("click", () => {
  if (!createDialog || createDialog.open) return;
  createDialog.showModal();
  createDialog.querySelector("input[name='name']")?.focus();
});

document.querySelectorAll("[data-close-customer-create-modal]").forEach((element) => {
  element.addEventListener("click", closeCreateDialog);
});

createDialog?.addEventListener("click", (event) => {
  if (event.target === createDialog) closeCreateDialog();
});
