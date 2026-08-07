if (typeof document !== "undefined" && document.body?.dataset.page === "customers.detail") {
  const dialogs = Array.from(document.querySelectorAll("[data-customer-identity-dialog]"));

  const closeDialog = (dialog) => {
    if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
    dialog.close();
  };

  document.querySelectorAll("[data-open-customer-identity-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = dialogs.find((candidate) => candidate.dataset.customerIdentityDialog === button.dataset.openCustomerIdentityDialog);
      if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
      dialog.showModal();
      dialog.querySelector("[data-customer-identity-value]")?.focus();
    });
  });

  dialogs.forEach((dialog) => {
    dialog.querySelectorAll("[data-close-customer-identity-dialog]").forEach((button) => {
      button.addEventListener("click", () => closeDialog(dialog));
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
    dialog.addEventListener("close", () => dialog.querySelector("form")?.reset());
  });
}
