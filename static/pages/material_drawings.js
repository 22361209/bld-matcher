if (document.body.dataset.page === "materials.drawings") {
  (() => {
    let frame = document.querySelector("[data-material-drawing-frame]");
    const code = document.querySelector("[data-material-drawing-current-code]");
    const category = document.querySelector("[data-material-drawing-current-category]");
    const updated = document.querySelector("[data-material-drawing-current-updated]");
    const download = document.querySelector("[data-material-drawing-current-download]");
    const browser = document.querySelector(".material-drawing-browser");
    if (!frame || !code || !category || !updated || !download) return;

    const replaceFrame = (src) => {
      const nextFrame = frame.cloneNode(false);
      nextFrame.src = src;
      frame.replaceWith(nextFrame);
      frame = nextFrame;
    };

    document.querySelectorAll("[data-material-drawing-select]").forEach((item) => {
      item.addEventListener("click", (event) => {
        event.preventDefault();
        document.querySelectorAll("[data-material-drawing-select].active").forEach((activeItem) => {
          activeItem.classList.remove("active");
        });
        item.classList.add("active");
        replaceFrame(item.dataset.previewSrc || `${item.dataset.previewUrl}#page=1&zoom=100`);
        code.textContent = item.dataset.code || "";
        category.textContent = item.dataset.category || "";
        updated.textContent = item.dataset.updatedAt || "";
        const downloadUrl = item.dataset.downloadUrl || "#";
        download.href = downloadUrl;
        download.setAttribute("href", downloadUrl);
        browser?.scrollIntoView({ block: "start" });
        window.history.replaceState(null, "", item.href);
      });
    });
  })();
}
