const clientMode = document.querySelector("[data-login-client-mode]");

const syncClientMode = () => {
  if (!(clientMode instanceof HTMLInputElement)) return;
  clientMode.value = window.matchMedia("(max-width: 760px)").matches ? "mobile" : "desktop";
};

syncClientMode();
clientMode?.form?.addEventListener("submit", syncClientMode);
