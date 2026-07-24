document.querySelectorAll("[data-price-mode]").forEach((select) => {
  const form = select.closest("form");
  const rateField = form ? form.querySelector("[data-exchange-rate-field]") : null;
  const rateInput = form ? form.querySelector("[data-exchange-rate]") : null;
  const syncRateField = () => {
    const needsRate = select.value === "usd";
    if (rateField instanceof HTMLElement) {
      rateField.hidden = !needsRate;
    }
    if (rateInput instanceof HTMLInputElement) {
      rateInput.disabled = !needsRate;
      rateInput.required = needsRate;
    }
  };

  select.addEventListener("change", syncRateField);
  syncRateField();
});


const downloadModal = document.querySelector("#download-excel-modal");

const closeDownloadModal = () => {
  if (!downloadModal) return;
  downloadModal.classList.remove("open");
  downloadModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
};

document.querySelectorAll("[data-open-download-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!downloadModal) return;
    downloadModal.classList.add("open");
    downloadModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    const select = downloadModal.querySelector("[data-price-mode]");
    if (select instanceof HTMLElement) {
      select.focus();
    }
  });
});

document.querySelectorAll("[data-close-download-modal]").forEach((element) => {
  element.addEventListener("click", closeDownloadModal);
});


document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && downloadModal?.classList.contains("open")) {
    closeDownloadModal();
  }
  if (event.key === "Escape" && mapOeModal?.classList.contains("open")) {
    closeMapOeModal();
  }
});


const mapOeModal = document.querySelector("#map-oe-modal");
const mapOeForm = mapOeModal ? mapOeModal.querySelector("[data-map-oe-form]") : null;
let mapOeTrigger = null;
let mapOeSearchTimer = null;
let mapOeSearchSequence = 0;

const mapOeField = (selector) => (mapOeForm ? mapOeForm.querySelector(selector) : null);

const showMapOeError = (message) => {
  const error = mapOeField("[data-map-oe-error]");
  if (!(error instanceof HTMLElement)) return;
  error.textContent = message || "";
  error.hidden = !message;
};

const clearMapOeSelection = () => {
  const bldField = mapOeField("[data-map-oe-bld]");
  const selected = mapOeField("[data-map-oe-selected]");
  if (bldField instanceof HTMLInputElement) {
    bldField.value = "";
  }
  if (selected instanceof HTMLElement) {
    selected.textContent = "";
    selected.hidden = true;
  }
};

const hideMapOeResults = () => {
  const results = mapOeField("[data-map-oe-results]");
  if (results instanceof HTMLElement) {
    results.replaceChildren();
    results.hidden = true;
  }
};

const selectMapOeProduct = (product) => {
  const bldField = mapOeField("[data-map-oe-bld]");
  const selected = mapOeField("[data-map-oe-selected]");
  const search = mapOeField("[data-map-oe-search]");
  if (bldField instanceof HTMLInputElement) {
    bldField.value = product.bld_no;
  }
  if (selected instanceof HTMLElement) {
    const detail = [product.item, product.series].filter(Boolean).join(" / ");
    selected.textContent = `已选择：${product.bld_no}${detail ? `（${detail}）` : ""}`;
    selected.hidden = false;
  }
  if (search instanceof HTMLInputElement) {
    search.value = product.bld_no;
  }
  hideMapOeResults();
  showMapOeError("");
};

const renderMapOeResults = (products) => {
  const results = mapOeField("[data-map-oe-results]");
  if (!(results instanceof HTMLElement)) return;
  results.replaceChildren();
  if (!products.length) {
    const empty = document.createElement("p");
    empty.className = "map-oe-results-empty";
    empty.textContent = "没有匹配的产品。";
    results.append(empty);
  } else {
    products.forEach((product) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "map-oe-result";
      const code = document.createElement("strong");
      code.textContent = product.bld_no;
      option.append(code);
      const detail = [product.item, product.series].filter(Boolean).join(" / ");
      if (detail) {
        const label = document.createElement("span");
        label.textContent = detail;
        option.append(label);
      }
      option.addEventListener("click", () => selectMapOeProduct(product));
      results.append(option);
    });
  }
  results.hidden = false;
};

const searchMapOeProducts = (query) => {
  if (!mapOeForm) return;
  const sequence = ++mapOeSearchSequence;
  const url = new URL(mapOeForm.dataset.mapOeLookupUrl, window.location.origin);
  url.searchParams.set("q", query);
  fetch(url, { headers: { Accept: "application/json", "X-Requested-With": "fetch" } })
    .then((response) => (response.ok ? response.json() : []))
    .then((products) => {
      if (sequence !== mapOeSearchSequence) return;
      renderMapOeResults(Array.isArray(products) ? products : []);
    })
    .catch(() => {
      if (sequence !== mapOeSearchSequence) return;
      hideMapOeResults();
    });
};

const closeMapOeModal = () => {
  if (!mapOeModal) return;
  mapOeModal.classList.remove("open");
  mapOeModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  if (mapOeTrigger instanceof HTMLElement) {
    mapOeTrigger.focus();
  }
  mapOeTrigger = null;
};

const openMapOeModal = (code, trigger) => {
  if (!mapOeModal || !mapOeForm) return;
  mapOeTrigger = trigger;
  mapOeForm.reset();
  const source = mapOeField("[data-map-oe-source]");
  if (source instanceof HTMLInputElement) {
    source.value = code;
  }
  clearMapOeSelection();
  hideMapOeResults();
  showMapOeError("");
  mapOeModal.classList.add("open");
  mapOeModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  if (source instanceof HTMLElement) {
    source.focus();
    source.select();
  }
};

if (mapOeModal && mapOeForm) {
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-map-oe-code]") : null;
    if (button instanceof HTMLElement) {
      openMapOeModal(button.dataset.mapOeCode || "", button);
    }
  });

  mapOeModal.querySelectorAll("[data-close-map-oe-modal]").forEach((element) => {
    element.addEventListener("click", closeMapOeModal);
  });

  const searchInput = mapOeField("[data-map-oe-search]");
  if (searchInput instanceof HTMLInputElement) {
    searchInput.addEventListener("input", () => {
      clearMapOeSelection();
      window.clearTimeout(mapOeSearchTimer);
      const query = searchInput.value.trim();
      if (!query) {
        mapOeSearchSequence += 1;
        hideMapOeResults();
        return;
      }
      mapOeSearchTimer = window.setTimeout(() => searchMapOeProducts(query), 300);
    });
  }

  mapOeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const submitButton = mapOeField("[data-map-oe-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
    }
    showMapOeError("");
    fetch(mapOeForm.dataset.mapOeSubmitUrl, {
      method: "POST",
      body: new FormData(mapOeForm),
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    })
      .then((response) =>
        response
          .json()
          .catch(() => null)
          .then((payload) => ({ ok: response.ok, payload })),
      )
      .then(({ ok, payload }) => {
        if (!ok || !payload || payload.ok !== true) {
          showMapOeError((payload && payload.error) || "保存失败，请稍后重试。");
          return;
        }
        if (mapOeTrigger instanceof HTMLButtonElement) {
          mapOeTrigger.disabled = true;
          mapOeTrigger.classList.add("map-oe-code-added");
          mapOeTrigger.textContent = `${mapOeTrigger.dataset.mapOeCode}（已加入）`;
          mapOeTrigger = null;
        }
        closeMapOeModal();
      })
      .catch(() => {
        showMapOeError("网络错误，请稍后重试。");
      })
      .finally(() => {
        if (submitButton instanceof HTMLButtonElement) {
          submitButton.disabled = false;
        }
      });
  });
}
