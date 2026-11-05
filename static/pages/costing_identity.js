const form = document.querySelector("[data-costing-form]");
const toggle = form?.querySelector("[data-costing-identity-toggle]");

if (toggle) {
  const fields = document.getElementById(toggle.getAttribute("aria-controls"));
  const title = form.querySelector("[data-costing-code]");
  const description = form.querySelector("[data-costing-description]");
  const expand = (open) => {
    fields.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "收起资料" : form.dataset.editable === "true" ? "编辑资料" : "查看资料";
  };

  toggle.addEventListener("click", () => {
    expand(fields.hidden);
    if (!fields.hidden) fields.querySelector("input:enabled")?.focus();
  });
  // Reveal collapsed fields before the browser tries to focus an invalid input.
  fields.addEventListener("invalid", () => expand(true), true);
  fields.addEventListener("input", () => {
    const value = (name) => fields.elements.namedItem(name).value.trim();
    const kind = fields.elements.namedItem("kind");
    const vehicle = [value("brand"), value("models")].filter(Boolean).join(" ");
    description.textContent = [value("name") || kind.selectedOptions[0].textContent, vehicle].filter(Boolean).join(" · ");
    title.textContent = value("code") || title.dataset.emptyTitle;
  });
}
