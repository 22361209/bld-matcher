const nav = document.querySelector(".app-nav");

if (nav instanceof HTMLElement && nav.dataset.navReady !== "true") {
  nav.dataset.navReady = "true";

  const menu = nav.querySelector("[data-admin-menu]");
  if (menu instanceof HTMLDetailsElement) {
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") menu.removeAttribute("open");
    });
  }

  const navigationMenus = [...nav.querySelectorAll("[data-nav-menu]")];
  const navMenus = [...nav.querySelectorAll(".nav-menu")];
  const narrowViewport = window.matchMedia("(max-width: 760px)");
  const mobileWorkspaceTrigger = nav.querySelector("[data-mobile-workspace-trigger]");
  const mobileWorkspaceMenu = nav.querySelector("[data-mobile-workspace-menu]");

  if (mobileWorkspaceTrigger instanceof HTMLButtonElement && mobileWorkspaceMenu instanceof HTMLElement) {
    const closeMobileWorkspaceMenu = ({ restoreFocus = false } = {}) => {
      nav.classList.remove("is-mobile-menu-open");
      mobileWorkspaceTrigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) mobileWorkspaceTrigger.focus();
    };

    mobileWorkspaceTrigger.addEventListener("click", () => {
      if (!narrowViewport.matches) return;
      const willOpen = !nav.classList.contains("is-mobile-menu-open");
      nav.classList.toggle("is-mobile-menu-open", willOpen);
      mobileWorkspaceTrigger.setAttribute("aria-expanded", String(willOpen));
    });
    mobileWorkspaceMenu.addEventListener("click", (event) => {
      if (event.target instanceof Element && event.target.closest("a")) closeMobileWorkspaceMenu();
    });
    document.addEventListener("click", (event) => {
      if (!nav.contains(event.target)) closeMobileWorkspaceMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("is-mobile-menu-open")) {
        closeMobileWorkspaceMenu({ restoreFocus: true });
      }
    });
    narrowViewport.addEventListener("change", () => closeMobileWorkspaceMenu());
  }

  const positionMobileMenuPanel = (navigationMenu) => {
    if (!narrowViewport.matches) return;

    const trigger = navigationMenu.querySelector("[data-nav-menu-trigger], :scope > a");
    const panel = navigationMenu.querySelector(":scope > .nav-menu-panel");
    if (!(trigger instanceof HTMLElement) || !(panel instanceof HTMLElement)) return;

    const bounds = (navigationMenu.hasAttribute("data-nav-split") ? navigationMenu : trigger).getBoundingClientRect();
    panel.style.setProperty("--nav-menu-panel-top", `${Math.round(bounds.bottom - 1)}px`);
    panel.style.setProperty("--nav-menu-panel-left", `${Math.round(bounds.left)}px`);
    panel.style.setProperty("--nav-menu-panel-width", `${Math.round(bounds.width)}px`);
  };

  const positionMobileMenuPanels = () => {
    navMenus.forEach(positionMobileMenuPanel);
  };

  navMenus.forEach((navigationMenu) => {
    navigationMenu.addEventListener("pointerenter", () => positionMobileMenuPanel(navigationMenu));
    navigationMenu.addEventListener("focusin", () => positionMobileMenuPanel(navigationMenu));
  });
  for (const navigationMenu of navigationMenus) {
    const trigger = navigationMenu.querySelector("[data-nav-menu-trigger]");
    if (!(trigger instanceof HTMLButtonElement)) continue;

    const closeMenu = () => {
      navigationMenu.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
    };
    const openMenu = () => {
      navigationMenus.forEach((item) => {
        item.classList.remove("is-open");
        item.querySelector("[data-nav-menu-trigger]")?.setAttribute("aria-expanded", "false");
      });
      navigationMenu.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      positionMobileMenuPanel(navigationMenu);
    };
    trigger.addEventListener("click", () => {
      if (navigationMenu.classList.contains("is-open")) closeMenu();
      else openMenu();
    });
    if (navigationMenu.hasAttribute("data-nav-split")) {
      navigationMenu.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "mouse") openMenu();
      });
      navigationMenu.addEventListener("pointerleave", (event) => {
        if (event.pointerType === "mouse" && !navigationMenu.contains(document.activeElement)) closeMenu();
      });
      navigationMenu.addEventListener("focusout", (event) => {
        if (!navigationMenu.contains(event.relatedTarget)) closeMenu();
      });
      navigationMenu.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" && !event.target.closest(".nav-menu-panel")) {
          event.preventDefault();
          openMenu();
          navigationMenu.querySelector(".nav-menu-panel a")?.focus();
        } else if (event.key === "Escape" && navigationMenu.classList.contains("is-open")) {
          event.preventDefault();
          event.stopPropagation();
          closeMenu();
          trigger.focus();
        }
      });
    }
    document.addEventListener("click", (event) => {
      if (!navigationMenu.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });
  }

  const root = document.documentElement;
  let lastScrollY = window.scrollY;
  let upwardDistance = 0;
  let revealed = false;
  let lastOffset = -1;

  const setNavOffset = () => {
    const height = revealed ? Math.ceil(nav.getBoundingClientRect().height) : 0;
    if (height === lastOffset) return;
    lastOffset = height;
    root.style.setProperty("--revealed-nav-height", `${height}px`);
    window.dispatchEvent(new CustomEvent("app-nav-offset-change"));
  };

  const setRevealed = (nextRevealed) => {
    if (revealed === nextRevealed) return;
    revealed = nextRevealed;
    nav.classList.toggle("nav-revealed", revealed);
    setNavOffset();
  };

  const updateNavReveal = () => {
    const currentY = window.scrollY;
    const delta = currentY - lastScrollY;
    const threshold = Math.max(420, window.innerHeight || 0);

    if (currentY <= 4) {
      upwardDistance = 0;
      setRevealed(false);
      lastScrollY = currentY;
      return;
    }

    if (delta < 0) {
      upwardDistance += Math.abs(delta);
      if (upwardDistance >= threshold) setRevealed(true);
    } else if (delta > 0) {
      upwardDistance = 0;
      setRevealed(false);
    }

    lastScrollY = currentY;
  };

  window.addEventListener("scroll", updateNavReveal, { passive: true });
  window.addEventListener("resize", () => {
    setNavOffset();
    positionMobileMenuPanels();
  });
  nav.querySelector(".nav-primary")?.addEventListener("scroll", positionMobileMenuPanels, { passive: true });
  window.addEventListener("pageshow", setNavOffset);
  setNavOffset();
}
