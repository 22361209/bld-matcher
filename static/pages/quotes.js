if (document.body.dataset.page === "quotes.list") {
  const openQuoteEdit = (dialog) => {
    if (dialog?.showModal) {
      dialog.showModal();
      dialog.querySelector("input[name='customer_name']")?.focus();
    }
  };

  document.querySelectorAll("[data-open-quote-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      openQuoteEdit(document.getElementById(button.dataset.openQuoteEdit));
    });
  });

  document.querySelectorAll("[data-close-quote-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      button.closest("dialog")?.close();
    });
  });

  document.querySelectorAll(".quote-edit-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
}
