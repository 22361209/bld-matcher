const dialog = document.getElementById("global-confirm-dialog");
const message = document.querySelector("[data-global-confirm-message]");
const okButton = document.querySelector("[data-global-confirm-ok]");

let pendingForm = null;
let pendingSubmitter = null;
let skipConfirm = false;

const closeDialog = (value) => dialog?.close(value);

const submitPending = () => {
  if (!(pendingForm instanceof HTMLFormElement) || !(pendingSubmitter instanceof HTMLElement)) return;
  skipConfirm = true;
  try {
    pendingForm.requestSubmit(pendingSubmitter);
  } finally {
    skipConfirm = false;
    pendingForm = null;
    pendingSubmitter = null;
  }
};

dialog?.addEventListener("close", () => {
  if (dialog.returnValue === "confirm") submitPending();
  else {
    pendingForm = null;
    pendingSubmitter = null;
  }
});

dialog?.addEventListener("click", (event) => {
  if (event.target === dialog) closeDialog("cancel");
});

okButton?.addEventListener("click", () => closeDialog("confirm"));

document.querySelectorAll("[data-close-global-confirm]").forEach((button) => {
  button.addEventListener("click", () => closeDialog("cancel"));
});

export const confirmDestructive = (event) => {
  const submitter = event.submitter;
  if (!(submitter instanceof HTMLElement)) return false;
  const text = submitter.dataset.confirm;
  if (!text || skipConfirm) return false;
  event.preventDefault();
  pendingForm = event.target;
  pendingSubmitter = submitter;
  if (message instanceof HTMLElement) message.textContent = text;
  dialog?.showModal();
  return true;
};
