import {initDevelopmentGrid} from "./development_grid.js";
import {initDevelopmentCopy} from "./development_copy.js";

if (document.body.dataset.page?.startsWith("development.")) {
  initDevelopmentGrid();
  initDevelopmentCopy();
  document.addEventListener("change", (event) => {
    const selector = event.target;
    if (!(selector instanceof HTMLSelectElement) || !selector.matches("[data-development-borrowing-source]")) return;
    const code = selector.selectedOptions[0]?.dataset.sourceCode;
    const input = selector.form?.querySelector("input[name='borrowed_code']");
    if (code && input instanceof HTMLInputElement) input.value = code;
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-development-form]")) return;
    if (event.defaultPrevented) return;
    if (form.dataset.developmentSubmitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.developmentSubmitting = "true";
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll("button[type='submit']").forEach((button) => {
      if (!button.disabled) {
        button.dataset.developmentDisabled = "true";
        button.disabled = true;
      }
    });
    const status = form.querySelector("[data-development-submit-status]");
    if (status) status.textContent = "正在保存，请稍候…";
  });

  window.addEventListener("pageshow", () => {
    document.querySelectorAll("[data-development-form]").forEach((form) => {
      delete form.dataset.developmentSubmitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll("[data-development-disabled]").forEach((button) => {
        button.disabled = false;
        delete button.dataset.developmentDisabled;
      });
      const status = form.querySelector("[data-development-submit-status]");
      if (status) status.textContent = "";
    });
  });

  const errors = document.querySelector("[data-development-errors]");
  if (errors) errors.focus();
}
