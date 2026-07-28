// 通用 combobox：输入过滤，↓ 进入候选，↑↓ 移动，回车填入，Esc 关闭。
// 数据源可为本地数组或异步搜索函数；可选「新增 "X"」尾项（如客户快速登记）。

export function moveActiveIndex(current, delta, count) {
  if (count <= 0) return -1;
  if (current < 0) return delta > 0 ? 0 : count - 1;
  return (current + delta + count) % count;
}

export function filterComboboxOptions(options, query) {
  const normalized = String(query || "").trim().toLowerCase();
  if (!normalized) return [...options];
  return options.filter((option) => {
    const haystack = `${option.label ?? ""} ${option.value ?? ""} ${option.detail ?? ""}`.toLowerCase();
    return haystack.includes(normalized);
  });
}

export function setupCombobox(input, options = {}) {
  if (!(input instanceof HTMLInputElement)) return null;
  const { source, onSelect = null, create = null, debounceMs = 200 } = options;
  if (typeof source !== "function" && !Array.isArray(source)) return null;

  const host = input.closest(".combobox") || input.parentElement;
  if (!(host instanceof HTMLElement)) return null;
  host.classList.add("combobox");

  const listId = `combobox-list-${Math.random().toString(36).slice(2, 10)}`;
  const dropdown = document.createElement("div");
  dropdown.className = "combobox-dropdown";
  dropdown.id = listId;
  dropdown.setAttribute("role", "listbox");
  dropdown.hidden = true;
  host.appendChild(dropdown);

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", listId);
  input.setAttribute("aria-autocomplete", "list");
  input.autocomplete = "off";

  let items = [];
  let activeIndex = -1;
  let debounceTimer = null;
  let searchSequence = 0;

  const isOpen = () => !dropdown.hidden;

  const closeDropdown = () => {
    window.clearTimeout(debounceTimer);
    searchSequence += 1;
    dropdown.hidden = true;
    dropdown.replaceChildren();
    items = [];
    activeIndex = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };

  const commitOption = (option) => {
    input.value = option.value;
    closeDropdown();
    if (typeof onSelect === "function") onSelect(option);
  };

  const runCreate = (query) => {
    if (typeof create !== "function") return;
    Promise.resolve()
      .then(() => create(query))
      .then((created) => {
        if (!created || !created.value) return;
        commitOption({ value: created.value, label: created.label || created.value });
      })
      .catch((error) => {
        const detail = error && error.message && error.message !== "create failed" ? error.message : "";
        renderMessage(detail || "新增失败，请稍后重试。");
      });
  };

  const highlight = () => {
    dropdown.querySelectorAll("[data-combobox-index]").forEach((element) => {
      element.classList.toggle("active", Number(element.dataset.comboboxIndex) === activeIndex);
    });
    const active = dropdown.querySelector(`[data-combobox-index="${activeIndex}"]`);
    if (active) {
      input.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  };

  const renderMessage = (text) => {
    dropdown.replaceChildren();
    const message = document.createElement("p");
    message.className = "combobox-empty";
    message.textContent = text;
    dropdown.appendChild(message);
    items = [];
    activeIndex = -1;
  };

  const renderOptions = (matches, query) => {
    dropdown.replaceChildren();
    items = matches.map((option) => ({ kind: "option", ...option }));
    const trimmed = String(query || "").trim();
    if (
      typeof create === "function" &&
      trimmed &&
      !matches.some((option) => String(option.value).toLowerCase() === trimmed.toLowerCase())
    ) {
      items.push({ kind: "create", value: trimmed, label: `新增 "${trimmed}"` });
    }
    if (!items.length) {
      renderMessage("没有匹配项。");
      return;
    }
    items.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "combobox-option";
      if (item.kind === "create") button.classList.add("combobox-option-create");
      button.id = `${listId}-option-${index}`;
      button.dataset.comboboxIndex = String(index);
      button.setAttribute("role", "option");
      const label = document.createElement("span");
      label.className = "combobox-option-label";
      label.textContent = item.label || item.value;
      button.appendChild(label);
      if (item.detail) {
        const detail = document.createElement("span");
        detail.className = "combobox-option-detail";
        detail.textContent = item.detail;
        button.appendChild(detail);
      }
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        if (item.kind === "create") runCreate(item.value);
        else commitOption(item);
      });
      dropdown.appendChild(button);
    });
    activeIndex = -1;
    highlight();
  };

  const fetchOptions = (query) => {
    if (Array.isArray(source)) return Promise.resolve(filterComboboxOptions(source, query));
    return Promise.resolve()
      .then(() => source(query))
      .then((result) => (Array.isArray(result) ? result : []));
  };

  const openDropdown = (query) => {
    const sequence = ++searchSequence;
    dropdown.hidden = false;
    input.setAttribute("aria-expanded", "true");
    fetchOptions(query)
      .then((matches) => {
        if (sequence !== searchSequence) return;
        renderOptions(matches, query);
      })
      .catch(() => {
        if (sequence !== searchSequence) return;
        renderMessage("候选项加载失败，可直接输入。");
      });
  };

  const scheduleOpen = () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => openDropdown(input.value), debounceMs);
  };

  input.addEventListener("focus", () => openDropdown(input.value));
  input.addEventListener("input", scheduleOpen);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!isOpen()) return;
      event.stopPropagation();
      closeDropdown();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!isOpen()) {
        openDropdown(input.value);
        return;
      }
      activeIndex = moveActiveIndex(activeIndex, event.key === "ArrowDown" ? 1 : -1, items.length);
      highlight();
      return;
    }
    if (event.key === "Enter" && isOpen() && activeIndex >= 0 && items[activeIndex]) {
      event.preventDefault();
      const item = items[activeIndex];
      if (item.kind === "create") runCreate(item.value);
      else commitOption(item);
    }
  });
  input.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (!dropdown.contains(document.activeElement)) closeDropdown();
    }, 120);
  });
  const onDocumentClick = (event) => {
    if (!host.isConnected) {
      // 宿主已被片段刷新移除（如报价页搜索结果重载），自我注销避免监听器堆积。
      document.removeEventListener("click", onDocumentClick);
      return;
    }
    if (!host.contains(event.target)) closeDropdown();
  };
  document.addEventListener("click", onDocumentClick);

  return { close: closeDropdown, open: () => openDropdown(input.value) };
}
