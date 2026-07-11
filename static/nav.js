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
  window.addEventListener("resize", setNavOffset);
  window.addEventListener("pageshow", setNavOffset);
  setNavOffset();
}
