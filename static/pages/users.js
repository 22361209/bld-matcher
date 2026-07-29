const page = document.querySelector('body[data-page="admin.users"]');
const form = page?.querySelector("[data-user-access-form]");

if (form) {
  const roleSelect = form.querySelector("[data-role-select]");
  const rows = Array.from(form.querySelectorAll("[data-permission-row]"));

  const selectedRole = () => roleSelect?.selectedOptions[0] ?? null;

  const refreshPermissionPreview = () => {
    const option = selectedRole();
    const rolePermissions = new Set((option?.dataset.permissions || "").split(" ").filter(Boolean));
    const isSystemRole = option?.dataset.systemRole === "1";

    rows.forEach((row) => {
      const permission = row.dataset.permission || "";
      const radios = Array.from(row.querySelectorAll('input[type="radio"]'));
      if (!radios.length) return;
      radios.forEach((radio) => {
        radio.disabled = isSystemRole;
      });
      const roleAllows = isSystemRole || rolePermissions.has(permission);
      const override = row.querySelector('input[type="radio"]:checked')?.value || "inherit";
      const finalAllows = isSystemRole || override === "allow" || (override === "inherit" && roleAllows);
      const roleResult = row.querySelector("[data-role-result]");
      const finalResult = row.querySelector("[data-permission-result]");
      if (roleResult) roleResult.textContent = `角色当前：${roleAllows ? "允许" : "禁止"}`;
      if (finalResult) {
        finalResult.textContent = `最终结果：${finalAllows ? "允许" : "禁止"}`;
        finalResult.dataset.state = finalAllows ? "allow" : "deny";
      }
    });
  };

  roleSelect?.addEventListener("change", refreshPermissionPreview);
  form.addEventListener("change", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.type === "radio") {
      refreshPermissionPreview();
    }
  });
  form.querySelector("[data-reset-permission-overrides]")?.addEventListener("click", () => {
    rows.forEach((row) => {
      const inherit = row.querySelector('input[type="radio"][value="inherit"]');
      if (inherit) inherit.checked = true;
    });
    refreshPermissionPreview();
  });
  refreshPermissionPreview();
}
