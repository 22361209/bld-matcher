import { setupProductTable } from "./product_table.js?v=20260714-1";

if (document.body.dataset.page === "products.list") {
  const bldSearch = document.querySelector("#bld-search");
  const oeSearch = document.querySelector("#oe-search");
  bldSearch.addEventListener("input", () => {
    if (bldSearch.value.trim()) oeSearch.value = "";
  });
  oeSearch.addEventListener("input", () => {
    if (oeSearch.value.trim()) bldSearch.value = "";
  });
  const statusInput = document.querySelector("#status-input");
  document.querySelectorAll(".status-menu-option").forEach((button) => {
    button.addEventListener("click", () => {
      statusInput.value = button.dataset.status;
    });
  });
  const toolbarPopovers = Array.from(document.querySelectorAll("details.toolbar-popover"));
  toolbarPopovers.forEach((popover) => {
    popover.addEventListener("toggle", () => {
      if (!popover.open) return;
      toolbarPopovers.forEach((other) => {
        if (other !== popover) other.open = false;
      });
    });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("details.toolbar-popover")) return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
  });
  const catalogUploadInput = document.querySelector("[data-catalog-upload-input]");
  catalogUploadInput?.addEventListener("change", () => {
    if (catalogUploadInput.files?.length) catalogUploadInput.form?.requestSubmit();
  });

  const table = document.querySelector("#products-table");
  const stickyCommand = document.querySelector(".products-sticky-command");
  let productHeaderFrame = 0;
  let lastProductHeaderTop = -1;
  const updateProductsTableHeaderTop = () => {
    if (!table || !stickyCommand) return;
    const navOffset = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--revealed-nav-height")
    ) || 0;
    const commandRect = stickyCommand.getBoundingClientRect();
    const tableRect = table.getBoundingClientRect();
    const commandIsPinned = commandRect.top <= navOffset + 1;
    const headerWouldTouchCommand = tableRect.top <= commandRect.bottom;
    const headerTop = commandIsPinned && headerWouldTouchCommand ? Math.round(navOffset + commandRect.height) : 0;
    if (headerTop === lastProductHeaderTop) return;
    lastProductHeaderTop = headerTop;
    table.style.setProperty("--products-table-header-top", `${headerTop}px`);
  };
  const scheduleProductsTableHeaderTop = () => {
    if (productHeaderFrame) return;
    productHeaderFrame = requestAnimationFrame(() => {
      productHeaderFrame = 0;
      updateProductsTableHeaderTop();
    });
  };
  window.addEventListener("scroll", scheduleProductsTableHeaderTop, { passive: true });
  window.addEventListener("resize", scheduleProductsTableHeaderTop);
  window.addEventListener("app-nav-offset-change", scheduleProductsTableHeaderTop);
  updateProductsTableHeaderTop();
  requestAnimationFrame(updateProductsTableHeaderTop);
  window.addEventListener("load", scheduleProductsTableHeaderTop);
  window.addEventListener("pageshow", scheduleProductsTableHeaderTop);
  setupProductTable(table);

  const productModal = document.querySelector("#product-modal");
  const productForm = productModal?.querySelector("form");
  const productEditModal = document.querySelector("#product-edit-modal");
  const productEditFrame = document.querySelector("#product-edit-frame");
  const resetModalPosition = (modal) => {
    const panel = modal?.querySelector("[data-draggable-modal-panel]");
    if (!panel) return;
    panel.style.transform = "";
    panel.dataset.dragX = "0";
    panel.dataset.dragY = "0";
  };
  const openProductModal = () => {
    resetModalPosition(productModal);
    productModal.classList.add("open");
    productModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    productModal.querySelector("input[name='bld_no']")?.focus();
  };
  const closeProductModal = () => {
    productModal.classList.remove("open");
    productModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    resetModalPosition(productModal);
    productForm?.reset();
  };
  const openProductEditModal = (url) => {
    if (!productEditModal || !productEditFrame) return;
    resetModalPosition(productEditModal);
    productEditFrame.src = `${url}${url.includes("?") ? "&" : "?"}embedded=1`;
    productEditModal.classList.add("open");
    productEditModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  };
  const closeProductEditModal = () => {
    if (!productEditModal || !productEditFrame) return;
    productEditModal.classList.remove("open");
    productEditModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    productEditFrame.src = "";
    resetModalPosition(productEditModal);
  };
  document.querySelector("[data-open-product-modal]")?.addEventListener("click", openProductModal);
  document.querySelectorAll("[data-close-product-modal]").forEach((element) => {
    element.addEventListener("click", closeProductModal);
  });
  document.querySelectorAll("[data-close-product-edit-modal]").forEach((element) => {
    element.addEventListener("click", closeProductEditModal);
  });

  document.querySelectorAll("[data-draggable-modal-panel]").forEach((panel) => {
    const handle = panel.querySelector("[data-modal-drag-handle]");
    if (!handle) return;
    panel.dataset.dragX = "0";
    panel.dataset.dragY = "0";
    handle.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = Number(panel.dataset.dragX || 0);
      const originY = Number(panel.dataset.dragY || 0);

      const onMove = (moveEvent) => {
        const nextX = originX + moveEvent.clientX - startX;
        const nextY = originY + moveEvent.clientY - startY;
        panel.dataset.dragX = String(nextX);
        panel.dataset.dragY = String(nextY);
        panel.style.transform = `translate(${nextX}px, ${nextY}px)`;
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });

  const imageModal = document.querySelector("#image-modal");
  const imageModalImg = document.querySelector("#image-modal-img");
  const imageModalCaption = document.querySelector("#image-modal-caption");
  const imageModalThumbs = document.querySelector("#image-modal-thumbs");
  const imageModalPrev = document.querySelector("[data-image-modal-prev]");
  const imageModalNext = document.querySelector("[data-image-modal-next]");
  let imageGallery = [];
  let imageGalleryIndex = 0;
  const renderImageModal = () => {
    const current = imageGallery[imageGalleryIndex];
    if (!current) return;
    imageModalImg.src = current.url;
    imageModalImg.alt = `${imageModalCaption.dataset.baseCaption || "产品图片"} ${current.label || ""}`.trim();
    imageModalCaption.textContent = [imageModalCaption.dataset.baseCaption, current.label].filter(Boolean).join(" · ");
    const multiple = imageGallery.length > 1;
    imageModalPrev.hidden = !multiple;
    imageModalNext.hidden = !multiple;
    imageModalThumbs.innerHTML = "";
    imageModalThumbs.hidden = !multiple;
    if (!multiple) return;
    imageGallery.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `image-modal-thumb${index === imageGalleryIndex ? " active" : ""}`;
      button.setAttribute("aria-label", item.label || `图片 ${index + 1}`);
      const img = document.createElement("img");
      img.src = item.thumb || item.url;
      img.alt = item.label || "";
      button.appendChild(img);
      button.addEventListener("click", () => {
        imageGalleryIndex = index;
        renderImageModal();
      });
      imageModalThumbs.appendChild(button);
    });
  };
  const openImageModal = (link) => {
    try {
      imageGallery = JSON.parse(link.dataset.gallery || "[]");
    } catch (_error) {
      imageGallery = [];
    }
    if (!imageGallery.length) {
      imageGallery = [{ url: link.href, thumb: link.href, label: "" }];
    }
    imageGalleryIndex = 0;
    imageModalCaption.dataset.baseCaption = link.dataset.caption || link.title.replace("打开 ", "").replace(" 原图", "");
    renderImageModal();
    imageModal.classList.add("open");
    imageModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  };
  const closeImageModal = () => {
    imageModal.classList.remove("open");
    imageModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    imageModalImg.src = "";
    imageModalImg.alt = "";
    imageModalCaption.textContent = "";
    imageModalCaption.dataset.baseCaption = "";
    imageModalThumbs.innerHTML = "";
    imageGallery = [];
    imageGalleryIndex = 0;
  };
  imageModalPrev.addEventListener("click", () => {
    if (!imageGallery.length) return;
    imageGalleryIndex = (imageGalleryIndex + imageGallery.length - 1) % imageGallery.length;
    renderImageModal();
  });
  imageModalNext.addEventListener("click", () => {
    if (!imageGallery.length) return;
    imageGalleryIndex = (imageGalleryIndex + 1) % imageGallery.length;
    renderImageModal();
  });

  table?.addEventListener("click", (event) => {
    const editLink = event.target.closest("[data-open-edit-product-modal]");
    if (editLink) {
      event.preventDefault();
      openProductEditModal(editLink.href);
      return;
    }
    const link = event.target.closest(".image-link");
    if (!link) return;
    event.preventDefault();
    openImageModal(link);
  });
  document.querySelectorAll("[data-close-image-modal]").forEach((element) => {
    element.addEventListener("click", closeImageModal);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
    if (productModal?.classList.contains("open")) {
      closeProductModal();
      return;
    }
    if (productEditModal?.classList.contains("open")) {
      closeProductEditModal();
      return;
    }
    if (imageModal.classList.contains("open")) {
      closeImageModal();
    }
  });
}
