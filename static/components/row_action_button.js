export function rowActionButton(symbol, label) {
  const button = document.createElement("button");
  button.className = "row-action-button";
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  if (symbol === "−") button.classList.add("row-action-remove");
  const face = document.createElement("span");
  face.textContent = symbol;
  face.setAttribute("aria-hidden", "true");
  button.append(face);
  return button;
}
