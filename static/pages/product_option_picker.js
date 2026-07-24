const PICKER_PAGE_IDS = new Set(["products.list", "products.edit"]);
const OPTIONS_ENDPOINT = "/products/options";
const FIELD_SOURCE_KEYS = {
  brand: "brands",
  item: "items",
  product_status: "statuses",
};

export function parsePickerMultiValue(text) {
  const values = [];
  const seen = new Set();
  String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .forEach((line) => {
      const value = line.trim();
      if (!value) return;
      const key = value.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      values.push(value);
    });
  return values;
}

export function serializePickerMultiValue(values) {
  return values.join("\n");
}

export function filterPickerOptions(options, query) {
  const normalized = String(query || "").trim().toLowerCase();
  if (!normalized) return [...options];
  return options.filter((option) => option.toLowerCase().includes(normalized));
}

export function pickerNewValue(query, options) {
  const value = String(query || "").trim();
  if (!value) return null;
  const key = value.toLowerCase();
  return options.some((option) => option.toLowerCase() === key) ? null : value;
}

export function createOptionPickerState(mode, raw = "") {
  const state = {
    mode: mode === "multi" ? "multi" : "single",
    values: [],
    add(value) {
      const text = String(value || "").trim();
      if (!text) return false;
      if (state.mode === "single") {
        state.values = [text];
        return true;
      }
      const key = text.toLowerCase();
      if (state.values.some((item) => item.toLowerCase() === key)) return false;
      state.values.push(text);
      return true;
    },
    remove(value) {
      const key = String(value || "").toLowerCase();
      const next = state.values.filter((item) => item.toLowerCase() !== key);
      if (next.length === state.values.length) return false;
      state.values = next;
      return true;
    },
    set(raw) {
      if (state.mode === "single") {
        const text = String(raw ?? "");
        state.values = text ? [text] : [];
        return;
      }
      state.values = parsePickerMultiValue(raw);
    },
    serialized() {
      return state.mode === "single" ? state.values[0] || "" : serializePickerMultiValue(state.values);
    },
  };
  state.set(raw);
  return state;
}

let cachedOptions = null;
let optionsPromise = null;

const fetchPickerOptions = () => {
  if (cachedOptions) return Promise.resolve(cachedOptions);
  if (!optionsPromise) {
    optionsPromise = fetch(OPTIONS_ENDPOINT, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((response) => {
        if (!response.ok) throw new Error("options unavailable");
        return response.json();
      })
      .then((payload) => {
        cachedOptions = {
          brands: Array.isArray(payload?.brands) ? payload.brands : [],
          items: Array.isArray(payload?.items) ? payload.items : [],
          statuses: Array.isArray(payload?.statuses) ? payload.statuses : [],
        };
        return cachedOptions;
      })
      .catch((error) => {
        optionsPromise = null;
        throw error;
      });
  }
  return optionsPromise;
};

const clearProductOptionCache = () => {
  cachedOptions = null;
  optionsPromise = null;
};

export function invalidateProductOptionCache() {
  clearProductOptionCache();
  if (typeof document !== "undefined") {
    document.dispatchEvent(new CustomEvent("bld:product-options-refresh"));
  }
}

export function setupProductOptionPicker(container) {
  if (!(container instanceof HTMLElement)) return null;
  if (container.dataset.pickerReady === "1") return container._optionPicker || null;
  container.dataset.pickerReady = "1";

  const mode = container.dataset.pickerMode === "multi" ? "multi" : "single";
  const field = container.dataset.pickerField || "";
  const input = container.querySelector("[data-picker-input]");
  const dropdown = container.querySelector("[data-picker-dropdown]");
  const chipsHost = container.querySelector("[data-picker-chips]");
  const valueField = container.querySelector("[data-picker-value]") || input;
  if (!(input instanceof HTMLInputElement) || !(dropdown instanceof HTMLElement) || !valueField) return null;

  const state = createOptionPickerState(mode, valueField.value);

  const syncValueField = () => {
    if (valueField !== input) valueField.value = state.serialized();
  };
  const renderChips = () => {
    if (!(chipsHost instanceof HTMLElement)) return;
    chipsHost.replaceChildren();
    chipsHost.hidden = state.values.length === 0;
    state.values.forEach((value) => {
      const chip = document.createElement("span");
      chip.className = "product-option-picker-chip";
      chip.dataset.pickerChip = value;
      const label = document.createElement("span");
      label.textContent = value;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.pickerChipRemove = value;
      remove.setAttribute("aria-label", `移除 ${value}`);
      remove.textContent = "×";
      chip.append(label, remove);
      chipsHost.appendChild(chip);
    });
  };
  const closeDropdown = () => {
    dropdown.hidden = true;
    dropdown.replaceChildren();
  };
  const commitValue = (value, { keepOpen = false } = {}) => {
    if (mode === "single") {
      input.value = String(value || "");
      state.set(input.value);
      closeDropdown();
      return;
    }
    if (!state.add(value)) return;
    syncValueField();
    renderChips();
    input.value = "";
    if (keepOpen) renderDropdown();
    else closeDropdown();
  };
  const renderDropdown = () => {
    const source = (cachedOptions && cachedOptions[FIELD_SOURCE_KEYS[field]]) || [];
    const selectedKeys = new Set(state.values.map((value) => value.toLowerCase()));
    const available = mode === "multi" ? source.filter((option) => !selectedKeys.has(option.toLowerCase())) : source;
    const matches = filterPickerOptions(available, input.value);
    const newValue = pickerNewValue(input.value, source);
    dropdown.replaceChildren();
    matches.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "product-option-picker-option";
      button.dataset.pickerOption = option;
      button.textContent = option;
      dropdown.appendChild(button);
    });
    if (newValue && (mode === "single" || !selectedKeys.has(newValue.toLowerCase()))) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "product-option-picker-option product-option-picker-add";
      button.dataset.pickerAdd = newValue;
      button.textContent = `新增 "${newValue}"`;
      dropdown.appendChild(button);
    }
    if (!dropdown.children.length) {
      const empty = document.createElement("p");
      empty.className = "product-option-picker-empty";
      empty.textContent = "没有匹配项。";
      dropdown.appendChild(empty);
    }
    dropdown.hidden = false;
  };
  const openDropdown = () => {
    if (cachedOptions) {
      renderDropdown();
      return;
    }
    dropdown.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "product-option-picker-empty";
    loading.textContent = "正在加载候选项…";
    dropdown.appendChild(loading);
    dropdown.hidden = false;
    fetchPickerOptions()
      .then(() => renderDropdown())
      .catch(() => {
        dropdown.replaceChildren();
        const failure = document.createElement("p");
        failure.className = "product-option-picker-empty";
        failure.textContent = "候选项加载失败，可直接输入新值。";
        dropdown.appendChild(failure);
      });
  };

  input.addEventListener("focus", openDropdown);
  input.addEventListener("input", () => {
    if (mode === "single") state.set(input.value);
    openDropdown();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (dropdown.hidden) return;
      event.stopPropagation();
      closeDropdown();
      return;
    }
    if (event.key === "Enter" && mode === "multi") {
      event.preventDefault();
      commitValue(input.value, { keepOpen: true });
    }
  });
  dropdown.addEventListener("click", (event) => {
    const option = event.target.closest("[data-picker-option]");
    if (option) {
      commitValue(option.dataset.pickerOption, { keepOpen: mode === "multi" });
      return;
    }
    const added = event.target.closest("[data-picker-add]");
    if (added) commitValue(added.dataset.pickerAdd, { keepOpen: mode === "multi" });
  });
  chipsHost?.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-picker-chip-remove]");
    if (!remove) return;
    if (!state.remove(remove.dataset.pickerChipRemove)) return;
    syncValueField();
    renderChips();
  });
  document.addEventListener("click", (event) => {
    if (!container.contains(event.target)) closeDropdown();
  });
  container.closest("form")?.addEventListener("reset", () => {
    setTimeout(() => {
      state.set(valueField.value);
      if (mode === "single") input.value = state.serialized();
      renderChips();
      closeDropdown();
    });
  });

  const controller = {
    state,
    setValue(raw) {
      state.set(raw);
      syncValueField();
      if (mode === "single") input.value = state.serialized();
      renderChips();
    },
  };
  container._optionPicker = controller;

  if (mode === "multi" && valueField instanceof HTMLTextAreaElement) valueField.hidden = true;
  renderChips();
  return controller;
}

export function setProductOptionPickerValue(scope, field, value) {
  const container = scope?.querySelector?.(`[data-option-picker][data-picker-field="${field}"]`);
  const controller = container?._optionPicker;
  if (!controller) return false;
  controller.setValue(value ?? "");
  return true;
}

const initProductOptionPickers = () => {
  document.querySelectorAll("[data-option-picker]").forEach((container) => {
    setupProductOptionPicker(container);
  });
};

if (typeof document !== "undefined" && PICKER_PAGE_IDS.has(document.body?.dataset.page || "")) {
  initProductOptionPickers();
  document.addEventListener("bld:product-options-refresh", clearProductOptionCache);
}
