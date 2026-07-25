const TEXT_INPUT_SELECTOR = [
  "form.search-form input[type='text']",
  "form.search-form input:not([type])",
  ".embedded-input-control input[type='text']",
  ".embedded-input-control input:not([type])",
].join(",");

const enhance = (input) => {
  if (!(input instanceof HTMLInputElement)) return;
  if (input.dataset.searchClearEnhanced === "true") return;
  if (input.disabled || input.readOnly) return;
  input.dataset.searchClearEnhanced = "true";

  const embeddedControl = input.closest(".embedded-input-control");
  const clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.className = "search-clear";
  clearButton.setAttribute("aria-label", "清除");
  clearButton.tabIndex = -1;
  clearButton.textContent = "×";

  if (embeddedControl) {
    input.insertAdjacentElement("afterend", clearButton);
  } else {
    const wrap = document.createElement("span");
    wrap.className = "search-input-wrap";
    input.insertAdjacentElement("beforebegin", wrap);
    wrap.append(input, clearButton);
  }

  const syncVisibility = () => {
    clearButton.hidden = !input.value;
  };
  input.addEventListener("input", syncVisibility);
  syncVisibility();

  clearButton.addEventListener("click", () => {
    if (!input.value) return;
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    syncVisibility();
    const form = input.closest("form");
    if (form) {
      form.requestSubmit();
    }
  });
};

const enhanceAll = (root) => {
  if (root instanceof HTMLInputElement && root.matches(TEXT_INPUT_SELECTOR)) {
    enhance(root);
    return;
  }
  if (root instanceof Element || root instanceof Document) {
    root.querySelectorAll(TEXT_INPUT_SELECTOR).forEach(enhance);
  }
};

enhanceAll(document);

new MutationObserver((records) => {
  records.forEach((record) => {
    record.addedNodes.forEach((node) => enhanceAll(node));
  });
}).observe(document.documentElement, { childList: true, subtree: true });
