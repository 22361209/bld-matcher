import {
  createProductCatalogRequestGate,
  productCatalogFragmentUrl,
  productCatalogHistoryUrl,
  productCatalogState,
} from "./product_catalog_navigation.js?v=20260720-1";
import { setupProductTable } from "./product_table.js?v=20260720-1";

if (document.body.dataset.page === "products.list") {
  const searchForm = document.querySelector("[data-products-search-form]");
  const bldSearch = searchForm?.querySelector("#bld-search");
  const oeSearch = searchForm?.querySelector("#oe-search");
  const statusInput = searchForm?.querySelector("#status-input");
  const statusLabel = searchForm?.querySelector("[data-products-status-label]");
  const resultsHost = document.querySelector("[data-products-results-host]");
  const inlineStatus = document.querySelector("[data-products-inline-status]");
  const exportForm = document.querySelector(".toolbar-export-form");
  const requestGate = createProductCatalogRequestGate();
  let requestController = null;
  let cleanupProductTable = () => {};

  const notifyDataGrids = (action) => {
    document.dispatchEvent(new CustomEvent(`bld:data-grids:${action}`, { detail: { root: resultsHost } }));
  };

  const setStatus = (message = "", state = "") => {
    if (!(inlineStatus instanceof HTMLElement)) return;
    inlineStatus.textContent = message;
    inlineStatus.classList.remove("active", "done", "error");
    if (message && state) inlineStatus.classList.add(state);
  };

  const appendFilterInputs = (form, filters, marker) => {
    form.querySelectorAll(`[${marker}]`).forEach((input) => input.remove());
    Object.entries(filters).forEach(([name, values]) => {
      values.forEach((value) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        input.setAttribute(marker, "");
        form.appendChild(input);
      });
    });
  };

  const syncControls = (href) => {
    const state = productCatalogState(href);
    if (bldSearch instanceof HTMLInputElement) bldSearch.value = state.bld;
    if (oeSearch instanceof HTMLInputElement) oeSearch.value = state.oe;
    if (statusInput instanceof HTMLInputElement) statusInput.value = state.status;
    if (statusLabel instanceof HTMLElement) {
      statusLabel.textContent = state.status === "all"
        ? "包含停用"
        : (state.status === "inactive" ? "只看停用" : "只看启用");
    }
    searchForm?.querySelectorAll(".status-menu-option").forEach((button) => {
      const selected = button.dataset.status === state.status;
      button.classList.toggle("selected", selected);
    });
    if (searchForm instanceof HTMLFormElement) {
      appendFilterInputs(searchForm, state.filters, "data-products-filter-param");
    }
    if (exportForm instanceof HTMLFormElement) {
      const bldInput = exportForm.querySelector("input[name='bld']");
      const oeInput = exportForm.querySelector("input[name='oe']");
      const exportStatus = exportForm.querySelector("input[name='status']");
      if (bldInput instanceof HTMLInputElement) bldInput.value = state.bld;
      if (oeInput instanceof HTMLInputElement) oeInput.value = state.oe;
      if (exportStatus instanceof HTMLInputElement) exportStatus.value = state.status;
      appendFilterInputs(exportForm, state.filters, "data-products-export-filter-param");
    }
  };

  const searchTargetUrl = () => {
    const target = new URL(searchForm.action, window.location.href);
    target.search = "";
    new FormData(searchForm).forEach((value, key) => {
      if (typeof value !== "string") return;
      if (value || ["brand", "item", "product_status"].includes(key)) {
        target.searchParams.append(key, value);
      }
    });
    target.hash = "products-results";
    return target.toString();
  };

  const initializeResults = (navigate) => {
    cleanupProductTable();
    const table = resultsHost?.querySelector("#products-table");
    cleanupProductTable = setupProductTable(table, { navigate });
    notifyDataGrids("setup");
  };

  const loadProducts = async (targetHref, { history = "push" } = {}) => {
    if (!(resultsHost instanceof HTMLElement) || !resultsHost.dataset.productsFragmentUrl) {
      window.location.assign(targetHref);
      return false;
    }
    if (typeof window.fetch !== "function" || typeof window.AbortController !== "function") {
      window.location.assign(targetHref);
      return false;
    }

    requestController?.abort();
    requestController = new AbortController();
    const generation = requestGate.begin();
    const currentGridScroll = resultsHost.querySelector("[data-grid-scroll]");
    const scrollState = {
      windowX: window.scrollX,
      windowY: window.scrollY,
      gridLeft: currentGridScroll?.scrollLeft || 0,
      gridTop: currentGridScroll?.scrollTop || 0,
    };
    resultsHost.setAttribute("aria-busy", "true");
    setStatus();

    try {
      const response = await fetch(
        productCatalogFragmentUrl(
          resultsHost.dataset.productsFragmentUrl,
          targetHref,
          window.location.href
        ),
        {
          cache: "no-store",
          credentials: "same-origin",
          headers: { Accept: "text/html", "X-Requested-With": "fetch" },
          signal: requestController.signal,
        }
      );
      const contentType = response.headers.get("Content-Type") || "";
      if (!response.ok || !contentType.includes("text/html")) throw new Error("fragment unavailable");
      const html = await response.text();
      if (!requestGate.isCurrent(generation)) return false;
      const template = document.createElement("template");
      template.innerHTML = html.trim();
      const nextResults = template.content.querySelector("[data-products-results]");
      if (!(nextResults instanceof HTMLElement)) throw new Error("invalid fragment");

      cleanupProductTable();
      notifyDataGrids("cleanup");
      resultsHost.replaceChildren(template.content);
      const canonicalHref = new URL(nextResults.dataset.canonicalUrl || targetHref, window.location.href).toString();
      const historyUrl = productCatalogHistoryUrl(canonicalHref);
      if (history === "push" && historyUrl !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        window.history.pushState({}, "", historyUrl);
      } else if (history === "replace") {
        window.history.replaceState({}, "", historyUrl);
      } else if (history === "none" && historyUrl !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        window.history.replaceState({}, "", historyUrl);
      }
      syncControls(canonicalHref);
      initializeResults((url) => loadProducts(url, { history: "push" }));
      requestAnimationFrame(() => {
        const nextGridScroll = resultsHost.querySelector("[data-grid-scroll]");
        if (nextGridScroll instanceof HTMLElement) {
          nextGridScroll.scrollLeft = scrollState.gridLeft;
          nextGridScroll.scrollTop = scrollState.gridTop;
        }
        window.scrollTo(scrollState.windowX, scrollState.windowY);
      });
      return true;
    } catch (error) {
      if (error?.name === "AbortError" || !requestGate.isCurrent(generation)) return false;
      if (history === "none") window.location.reload();
      else window.location.assign(targetHref);
      return false;
    } finally {
      if (requestGate.isCurrent(generation)) resultsHost.setAttribute("aria-busy", "false");
    }
  };

  bldSearch?.addEventListener("input", () => {
    if (bldSearch.value.trim() && oeSearch instanceof HTMLInputElement) oeSearch.value = "";
  });
  oeSearch?.addEventListener("input", () => {
    if (oeSearch.value.trim() && bldSearch instanceof HTMLInputElement) bldSearch.value = "";
  });
  searchForm?.querySelectorAll(".status-menu-option").forEach((button) => {
    button.addEventListener("click", () => {
      if (statusInput instanceof HTMLInputElement) statusInput.value = button.dataset.status;
    });
  });
  searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    loadProducts(searchTargetUrl(), { history: "push" });
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
    toolbarPopovers.forEach((popover) => { popover.open = false; });
  });
  const catalogUploadInput = document.querySelector("[data-catalog-upload-input]");
  catalogUploadInput?.addEventListener("change", () => {
    if (catalogUploadInput.files?.length) catalogUploadInput.form?.requestSubmit();
  });

  const productModal = document.querySelector("#product-modal");
  const productForm = productModal?.querySelector("[data-product-create-form]");
  const productFormStatus = productForm?.querySelector("[data-product-form-status]");
  const productModalTitle = productModal?.querySelector("[data-product-modal-title]");
  const productModalNote = productModal?.querySelector("[data-product-modal-note]");
  const drawingUnavailableModal = document.querySelector("[data-drawing-unavailable-modal]");
  const drawingUnavailableMessage = drawingUnavailableModal?.querySelector("[data-drawing-unavailable-message]");
  const productEditModal = document.querySelector("#product-edit-modal");
  const productEditFrame = document.querySelector("#product-edit-frame");
  const resetModalPosition = (modal) => {
    const panel = modal?.querySelector("[data-draggable-modal-panel]");
    if (!panel) return;
    panel.style.transform = "";
    panel.dataset.dragX = "0";
    panel.dataset.dragY = "0";
  };
  const setProductFormStatus = (message = "", state = "") => {
    if (!(productFormStatus instanceof HTMLElement)) return;
    productFormStatus.textContent = message;
    productFormStatus.classList.remove("active", "done", "error");
    if (message && state) productFormStatus.classList.add(state);
  };
  const openProductModal = () => {
    resetModalPosition(productModal);
    productModal?.classList.add("open");
    productModal?.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    setProductFormStatus();
    productModal?.querySelector("input[name='bld_no']")?.focus();
  };
  const resetProductModalCopy = () => {
    const sourceInput = productForm?.querySelector("input[name='copy_source_product_id']");
    if (sourceInput instanceof HTMLInputElement) sourceInput.value = "";
    if (productModalTitle instanceof HTMLElement) productModalTitle.textContent = "新增产品";
    if (productModalNote instanceof HTMLElement) {
      productModalNote.textContent = "一个 BLD NO. 可以维护多个 OE、多个品牌和多个车型；也可同时上传最多 5 张图片和一份 PDF 图纸。";
    }
  };
  const openCopiedProductModal = (button) => {
    if (!(productForm instanceof HTMLFormElement)) return;
    let source;
    try {
      source = JSON.parse(button.dataset.copyProduct || "{}");
    } catch (_error) {
      setStatus("复制来源无效，请刷新目录后重试。", "error");
      return;
    }
    if (!source?.id) {
      setStatus("复制来源无效，请刷新目录后重试。", "error");
      return;
    }
    productForm.reset();
    const fields = ["series", "item", "oe_no_1", "oe_no_2", "models", "price_cny", "product_status"];
    fields.forEach((name) => {
      const input = productForm.elements.namedItem(name);
      if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
        input.value = source[name] ?? "";
      }
    });
    const active = productForm.elements.namedItem("active");
    if (active instanceof HTMLInputElement) active.checked = Boolean(source.active);
    const sourceInput = productForm.elements.namedItem("copy_source_product_id");
    if (sourceInput instanceof HTMLInputElement) sourceInput.value = String(source.id);
    if (productModalTitle instanceof HTMLElement) productModalTitle.textContent = `复制产品资料 · ${source.bld_no}`;
    if (productModalNote instanceof HTMLElement) {
      productModalNote.textContent = "请填写新的 BLD NO.；保存后会复制当前产品资料，所选图片或 PDF 图纸会覆盖对应的复制文件。";
    }
    openProductModal();
  };
  const closeProductModal = () => {
    productModal?.classList.remove("open");
    productModal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    resetModalPosition(productModal);
    productForm?.reset();
    resetProductModalCopy();
    setProductFormStatus();
  };
  const openDrawingUnavailableModal = (bldNo) => {
    if (drawingUnavailableMessage instanceof HTMLElement) {
      drawingUnavailableMessage.textContent = `${bldNo || "该产品"} 暂未上传图纸。`;
    }
    drawingUnavailableModal?.classList.add("open");
    drawingUnavailableModal?.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  };
  const closeDrawingUnavailableModal = () => {
    drawingUnavailableModal?.classList.remove("open");
    drawingUnavailableModal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
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

  document.querySelector("[data-open-product-modal]")?.addEventListener("click", () => {
    productForm?.reset();
    resetProductModalCopy();
    openProductModal();
  });
  document.querySelectorAll("[data-close-product-modal]").forEach((element) => {
    element.addEventListener("click", closeProductModal);
  });
  document.querySelectorAll("[data-close-drawing-unavailable-modal]").forEach((element) => {
    element.addEventListener("click", closeDrawingUnavailableModal);
  });
  document.querySelectorAll("[data-close-product-edit-modal]").forEach((element) => {
    element.addEventListener("click", closeProductEditModal);
  });

  productForm?.addEventListener("submit", async (event) => {
    if (typeof window.fetch !== "function") return;
    event.preventDefault();
    const submitButton = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
    if (submitButton) submitButton.disabled = true;
    setProductFormStatus("正在保存产品…", "active");
    try {
      const response = await fetch(productForm.action, {
        method: "POST",
        body: new FormData(productForm),
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "保存失败，请稍后重试。");
      closeProductModal();
      await loadProducts(payload.redirect_url || window.location.href, {
        history: "push",
      });
    } catch (error) {
      setProductFormStatus(error?.message || "保存结果不确定，请刷新目录确认。", "error");
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
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
    if (!imageGallery.length) imageGallery = [{ url: link.href, thumb: link.href, label: "" }];
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
  imageModalPrev?.addEventListener("click", () => {
    if (!imageGallery.length) return;
    imageGalleryIndex = (imageGalleryIndex + imageGallery.length - 1) % imageGallery.length;
    renderImageModal();
  });
  imageModalNext?.addEventListener("click", () => {
    if (!imageGallery.length) return;
    imageGalleryIndex = (imageGalleryIndex + 1) % imageGallery.length;
    renderImageModal();
  });

  resultsHost?.addEventListener("click", (event) => {
    const pageLink = event.target.closest(".data-grid-pagination a");
    if (pageLink) {
      event.preventDefault();
      loadProducts(pageLink.href, { history: "push" });
      return;
    }
    const editLink = event.target.closest("[data-open-edit-product-modal]");
    if (editLink) {
      event.preventDefault();
      openProductEditModal(editLink.href);
      return;
    }
    const copyButton = event.target.closest("[data-copy-product-action]");
    if (copyButton instanceof HTMLButtonElement) {
      event.preventDefault();
      copyButton.closest("[data-bld-action-menu]")?.removeAttribute("open");
      openCopiedProductModal(copyButton);
      return;
    }
    const drawingUnavailable = event.target.closest("[data-product-drawing-unavailable]");
    if (drawingUnavailable instanceof HTMLButtonElement) {
      event.preventDefault();
      drawingUnavailable.closest("[data-bld-action-menu]")?.removeAttribute("open");
      openDrawingUnavailableModal(drawingUnavailable.dataset.productBldNo);
      return;
    }
    const imageLink = event.target.closest(".image-link");
    if (!imageLink) return;
    event.preventDefault();
    openImageModal(imageLink);
  });
  const positionBldActionMenu = (menu) => {
    const trigger = menu.querySelector(".bld-action-trigger");
    const popover = menu.querySelector(".bld-action-popover");
    if (!(trigger instanceof HTMLElement) || !(popover instanceof HTMLElement)) return;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = popover.offsetWidth || 176;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuWidth - 8));
    const top = Math.min(rect.bottom + 6, window.innerHeight - popover.offsetHeight - 8);
    popover.style.setProperty("--bld-action-left", `${left}px`);
    popover.style.setProperty("--bld-action-top", `${Math.max(8, top)}px`);
  };
  document.addEventListener("toggle", (event) => {
    const menu = event.target;
    if (!(menu instanceof HTMLDetailsElement) || !menu.matches("[data-bld-action-menu]")) return;
    if (!menu.open) return;
    resultsHost?.querySelectorAll("[data-bld-action-menu][open]").forEach((other) => {
      if (other !== menu) other.removeAttribute("open");
    });
    requestAnimationFrame(() => positionBldActionMenu(menu));
  }, true);
  document.querySelectorAll("[data-close-image-modal]").forEach((element) => {
    element.addEventListener("click", closeImageModal);
  });

  window.addEventListener("message", async (event) => {
    if (event.origin !== window.location.origin || event.source !== productEditFrame?.contentWindow) return;
    if (event.data?.type !== "bld:product-mutated") return;
    if (!event.data.ok) {
      setStatus(event.data.message || "产品保存失败。", "error");
      return;
    }
    closeProductEditModal();
    await loadProducts(window.location.href, {
      history: "replace",
    });
  });
  window.addEventListener("popstate", () => {
    loadProducts(window.location.href, { history: "none" });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    toolbarPopovers.forEach((popover) => { popover.open = false; });
    if (drawingUnavailableModal?.classList.contains("open")) {
      closeDrawingUnavailableModal();
      return;
    }
    if (productModal?.classList.contains("open")) {
      closeProductModal();
      return;
    }
    if (productEditModal?.classList.contains("open")) {
      closeProductEditModal();
      return;
    }
    if (imageModal?.classList.contains("open")) closeImageModal();
  });

  syncControls(window.location.href);
  initializeResults((url) => loadProducts(url, { history: "push" }));
}
