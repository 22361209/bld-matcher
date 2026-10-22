export const createCatalogVisibilityUpdater = (initial, { send, render }) => {
  let saved = { ...initial };
  let busy = false;
  let inFlight = saved;
  return {
    get state() { return { ...saved }; },
    get busy() { return busy; },
    async update(language, visible) {
      if (busy) {
        render(inFlight, true, "保存中…");
        return false;
      }
      if (!["en", "zh"].includes(language) || typeof visible !== "boolean") return false;
      busy = true;
      inFlight = { ...saved, [language]: visible };
      render(inFlight, true, "保存中…");
      let message = "已保存";
      let success = false;
      try {
        const result = await send({ language, visible, expected_visible: saved[language], expected_updated_at: saved.revision });
        if (!result?.ok || typeof result.updated_at !== "string"
            || typeof result.catalog_visible_en !== "boolean" || typeof result.catalog_visible_zh !== "boolean") {
          throw new Error(result?.error || "保存结果无效，请刷新目录确认。");
        }
        saved = { en: result.catalog_visible_en, zh: result.catalog_visible_zh, revision: result.updated_at };
        success = true;
      } catch (error) {
        message = `保存失败，已恢复勾选。${error?.message || "请刷新目录确认后重试。"}`;
      } finally {
        busy = false;
        render(saved, false, message);
      }
      return success;
    },
  };
};

export const setupCatalogVisibility = (root) => {
  const pending = new Map();
  root.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-catalog-visibility-controls]")) event.stopPropagation();
  });
  root.addEventListener("change", async (event) => {
    const input = event.target.closest?.("[data-catalog-visibility-toggle]");
    if (!input) return;
    const controls = input.closest("[data-catalog-visibility-controls]");
    if (controls.dataset.canEdit !== "1") return;
    const id = controls.dataset.productId;
    const groups = () => [...root.querySelectorAll("[data-catalog-visibility-controls]")]
      .filter((group) => group.dataset.productId === id);
    const render = (state, busy, message) => groups().forEach((group) => {
      group.dataset.version = state.revision;
      group.dataset.visibleEn = state.en ? "1" : "0";
      group.dataset.visibleZh = state.zh ? "1" : "0";
      group.setAttribute("aria-busy", String(busy));
      group.querySelectorAll("[data-catalog-visibility-toggle]").forEach((toggle) => {
        toggle.checked = state[toggle.dataset.language];
        toggle.disabled = busy || group.dataset.canEdit !== "1";
      });
      group.querySelector("[data-catalog-visibility-status]").textContent = message;
    });
    let updater = pending.get(id);
    if (!updater) {
      updater = createCatalogVisibilityUpdater({ en: controls.dataset.visibleEn === "1", zh: controls.dataset.visibleZh === "1",
        revision: controls.dataset.version }, {
        render,
        send: async (values) => {
          const body = new URLSearchParams({ language: values.language, visible: values.visible ? "1" : "0",
            expected_visible: values.expected_visible ? "1" : "0", expected_updated_at: values.expected_updated_at });
          const response = await fetch(controls.dataset.saveUrl, { method: "POST", body, credentials: "same-origin",
            headers: { Accept: "application/json", "X-CSRF-Token": controls.querySelector("[name=csrf_token]").value },
            signal: AbortSignal.timeout(15000) }).catch(() => {
              throw new Error("网络请求失败，请刷新目录确认后重试。");
            });
          const payload = await response.json().catch(() => ({ ok: false, error: "无法确认保存结果，请刷新目录重试。" }));
          if (!response.ok) throw new Error(payload?.error || "请刷新目录确认后重试。");
          return payload;
        },
      });
      pending.set(id, updater);
    }
    await updater.update(input.dataset.language, input.checked);
    if (!updater.busy) pending.delete(id);
  });
};
