const SEARCH_SELECTOR = "input[data-search-shortcut]";
const DIALOG_SELECTOR = "dialog[open], [role='dialog']";

const isVisible = (element) => {
  if (element.closest("[hidden], [inert], [aria-hidden='true'], details:not([open])")) return false;
  if (!element.getClientRects().length) return false;
  const style = getComputedStyle(element);
  return style.visibility !== "hidden" && style.visibility !== "collapse";
};

export const focusPageSearch = (event, root = document) => {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
  if (!event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key.toLowerCase() !== "f") return;
  const active = root.activeElement;
  if (active?.isContentEditable) return;
  if (active?.matches("input, textarea, select") && !active.disabled && !active.readOnly) return;

  // Keep focus inside an open dialog, including the legacy modal panels.
  const dialogs = Array.from(root.querySelectorAll(DIALOG_SELECTOR)).filter(isVisible);
  const modal = dialogs.findLast((dialog) => dialog.matches(":modal"));
  const activeDialog = active?.closest(DIALOG_SELECTOR);
  const scope = modal || (dialogs.includes(activeDialog) ? activeDialog : dialogs.at(-1)) || root;
  const inputs = Array.from(scope.querySelectorAll(SEARCH_SELECTOR)).filter((input) => {
    return ["text", "search"].includes(input.type) && !input.matches(":disabled") && !input.readOnly && isVisible(input);
  });
  const input = inputs[0];
  if (!input) return;

  input.focus();
  if (root.activeElement !== input) return;
  input.select();
  event.preventDefault();
};

if (typeof document !== "undefined") {
  document.addEventListener("keydown", focusPageSearch);
}
