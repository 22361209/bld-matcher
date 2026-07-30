const page = document.querySelector('body[data-page="admin.users"]');
const accountForm = page?.querySelector("[data-user-access-form]");

const setCategoryCurrent = (links, current) => {
  links.forEach((link) => {
    if (link === current) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });
};

page?.querySelectorAll(".access-permission-layout").forEach((layout) => {
  const links = Array.from(layout.querySelectorAll("[data-permission-category]"));
  const scroller = layout.querySelector("[data-permission-matrix-scroll]");
  if (!links.length || !(scroller instanceof HTMLElement)) return;

  let lockedCategory = null;
  let initialCategoryLock = null;

  const scrollToCategory = (link) => {
    if (!(link instanceof HTMLAnchorElement)) return;
    const targetId = link.hash.slice(1);
    const target = targetId ? document.getElementById(targetId) : null;
    if (!target) return;

    setCategoryCurrent(links, link);
    const previousScrollTop = scroller.scrollTop;
    if (link.dataset.permissionCategory === "all") {
      scroller.scrollTop = 0;
      lockedCategory = scroller.scrollTop !== previousScrollTop ? link : null;
      return;
    }
    const header = scroller.querySelector("thead");
    const headerHeight = header?.getBoundingClientRect().height || 0;
    const targetTop = target.getBoundingClientRect().top;
    const scrollerTop = scroller.getBoundingClientRect().top;
    scroller.scrollTop += targetTop - scrollerTop - headerHeight;
    lockedCategory = scroller.scrollTop !== previousScrollTop ? link : null;
  };

  links.forEach((link) => {
    link.addEventListener("click", (event) => {
      if (!(link instanceof HTMLAnchorElement)) return;
      event.preventDefault();
      scrollToCategory(link);
      window.history.replaceState(null, "", link.hash);
    });
  });

  let scrollFrame = 0;
  const syncCategoryToScroll = () => {
    if (scroller.scrollTop <= 1) {
      setCategoryCurrent(links, links[0]);
      return;
    }
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1) {
      setCategoryCurrent(links, links.at(-1));
      return;
    }
    const header = scroller.querySelector("thead");
    const threshold = scroller.getBoundingClientRect().top
      + (header?.getBoundingClientRect().height || 0)
      + 1;
    let current = links[0];
    links.slice(1).forEach((link) => {
      if (!(link instanceof HTMLAnchorElement)) return;
      const target = document.getElementById(link.hash.slice(1));
      if (target && target.getBoundingClientRect().top <= threshold) current = link;
    });
    setCategoryCurrent(links, current);
  };
  scroller.addEventListener("scroll", () => {
    if (initialCategoryLock) {
      setCategoryCurrent(links, initialCategoryLock);
      return;
    }
    if (lockedCategory) {
      setCategoryCurrent(links, lockedCategory);
      lockedCategory = null;
      return;
    }
    window.cancelAnimationFrame(scrollFrame);
    scrollFrame = window.requestAnimationFrame(syncCategoryToScroll);
  }, { passive: true });

  const initialLink = links.find((link) => (
    link instanceof HTMLAnchorElement && link.hash === window.location.hash
  ));
  if (initialLink) {
    const applyInitialCategory = () => {
      initialCategoryLock = initialLink;
      const settleInitialCategory = () => {
        window.scrollTo({ top: 0, left: window.scrollX, behavior: "auto" });
        scrollToCategory(initialLink);
        setCategoryCurrent(links, initialLink);
      };
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(settleInitialCategory);
      });
      window.setTimeout(settleInitialCategory, 120);
      window.setTimeout(() => {
        initialCategoryLock = null;
        lockedCategory = null;
      }, 300);
    };
    if (document.readyState === "complete") applyInitialCategory();
    else window.addEventListener("load", applyInitialCategory, { once: true });
  } else {
    syncCategoryToScroll();
  }
});

if (accountForm) {
  const roleSelect = accountForm.querySelector("[data-role-select]");
  const rows = Array.from(accountForm.querySelectorAll("[data-permission-row]"));
  const summary = accountForm.querySelector("[data-permission-summary]");
  const resetButton = accountForm.querySelector("[data-reset-permission-overrides]");
  const waitMessage = accountForm.querySelector("[data-submit-wait-message]");

  const selectedRole = () => roleSelect?.selectedOptions[0] ?? null;

  const refreshPermissionPreview = () => {
    const option = selectedRole();
    const rolePermissions = new Set((option?.dataset.permissions || "").split(" ").filter(Boolean));
    const isSystemRole = option?.dataset.systemRole === "1";
    let allowedCount = 0;
    let overrideCount = 0;

    rows.forEach((row) => {
      const permission = row.dataset.permission || "";
      const radios = Array.from(row.querySelectorAll('input[type="radio"]'));
      radios.forEach((radio) => {
        radio.disabled = isSystemRole;
      });

      const roleAllows = isSystemRole || rolePermissions.has(permission);
      const override = radios.length
        ? row.querySelector('input[type="radio"]:checked')?.value || "inherit"
        : "inherit";
      const finalAllows = radios.length
        ? isSystemRole || override === "allow" || (override === "inherit" && roleAllows)
        : isSystemRole;
      if (finalAllows) allowedCount += 1;
      if (!isSystemRole && radios.length && override !== "inherit") overrideCount += 1;

      const roleResult = row.querySelector("[data-role-result]");
      const finalResult = row.querySelector("[data-permission-result]");
      if (roleResult) {
        roleResult.textContent = roleAllows ? "允许" : "禁止";
        roleResult.dataset.state = roleAllows ? "allow" : "deny";
      }
      if (finalResult) {
        finalResult.textContent = finalAllows ? "允许" : "禁止";
        finalResult.dataset.state = finalAllows ? "allow" : "deny";
      }
    });

    if (summary) {
      const roleSummary = summary.querySelector("[data-role-summary]");
      const overrideSummary = summary.querySelector("[data-override-count]");
      const allowSummary = summary.querySelector("[data-allow-count]");
      const denySummary = summary.querySelector("[data-deny-count]");
      if (roleSummary) roleSummary.textContent = option?.textContent?.trim() || "未选择";
      if (overrideSummary) overrideSummary.textContent = String(overrideCount);
      if (allowSummary) allowSummary.textContent = String(allowedCount);
      if (denySummary) denySummary.textContent = String(rows.length - allowedCount);
    }
    if (resetButton instanceof HTMLButtonElement) {
      resetButton.hidden = false;
      resetButton.disabled = isSystemRole;
    }
  };

  roleSelect?.addEventListener("change", () => {
    refreshPermissionPreview();
    if (waitMessage) waitMessage.textContent = "角色变更尚未保存。";
  });
  accountForm.addEventListener("change", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.type === "radio") {
      refreshPermissionPreview();
      if (waitMessage) waitMessage.textContent = "权限调整尚未保存。";
    }
  });
  resetButton?.addEventListener("click", () => {
    rows.forEach((row) => {
      const inherit = row.querySelector('input[type="radio"][value="inherit"]');
      if (inherit instanceof HTMLInputElement) inherit.checked = true;
    });
    refreshPermissionPreview();
    if (waitMessage) waitMessage.textContent = "已全部切换为跟随角色，尚未保存。";
  });
  refreshPermissionPreview();
}

const roleForm = page?.querySelector("[data-role-access-form]");
if (roleForm) {
  const checkboxes = Array.from(roleForm.querySelectorAll('input[name="permissions"]'));
  const count = roleForm.querySelector("[data-role-permission-count]");
  const refreshRolePermissionCount = () => {
    if (count) count.textContent = String(checkboxes.filter((checkbox) => checkbox.checked).length);
  };
  roleForm.addEventListener("change", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.name === "permissions") {
      refreshRolePermissionCount();
    }
  });
  refreshRolePermissionCount();
}
