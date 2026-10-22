const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TASK_TEXT_FIELDS = ["name", "procurement_mode", "material_category", "material_grade", "forming_process", "notes"];
const normalize = (value) => String(value || "").trim().toLocaleLowerCase();
const shortId = (id) => String(id).slice(-8);

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function suggestionFields(raw) {
  const result = {development_mode: "borrowed"};
  for (const key of TASK_TEXT_FIELDS) if (typeof raw?.[key] === "string") result[key] = raw[key];
  if (raw?.supplier_id === null || (typeof raw?.supplier_id === "string" && UUID.test(raw.supplier_id))) result.supplier_id = raw.supplier_id;
  return result;
}

export function createGridPicker(root, {getState, onSupplier, onSource, onError}) {
  const dialog = root.querySelector("[data-grid-picker]");
  const title = dialog?.querySelector("[data-grid-picker-title]");
  const query = dialog?.querySelector("[data-grid-picker-query]");
  const help = dialog?.querySelector("[data-grid-picker-help]");
  const results = dialog?.querySelector("[data-grid-picker-results]");
  const create = dialog?.querySelector("[data-grid-picker-create]");
  if (!dialog || !title || !query || !help || !results || !create) {
    const unavailable = () => onError("选择窗口尚未就绪，请刷新后重试。原有输入已保留。");
    return {openSupplier: unavailable, openSource: unavailable};
  }
  let mode = null;
  let keys = [];
  let trigger = null;
  let focusIdentity = null;
  let generation = 0;
  let controller = null;
  let debounce = null;
  let timeout = null;

  function cancelLookup() {
    generation += 1;
    clearTimeout(debounce); clearTimeout(timeout);
    controller?.abort(); controller = null;
    results.removeAttribute("aria-busy");
  }
  function restoreFocus() {
    if (trigger?.isConnected) { trigger.focus(); return; }
    if (!focusIdentity) return;
    const row = [...root.querySelectorAll("[data-grid-row]")].find((node) => node.dataset.gridRow === focusIdentity.key);
    const replacement = [...(row?.querySelectorAll("[data-grid-action]") || [])].find((node) => node.dataset.gridAction === focusIdentity.action);
    (replacement || row?.querySelector("[data-grid-field]") || root.querySelector("[data-grid-action='save']"))?.focus();
  }
  function close() {
    cancelLookup(); mode = null;
    if (dialog.open) dialog.close();
    restoreFocus();
  }
  function commit(callback) {
    close();
    callback();
    // The controller can render a replacement row during the callback.
    restoreFocus();
  }
  function button(text, action) {
    const node = element("button", text, "linear-button subtle");
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function message(text) { results.replaceChildren(element("p", text, "development-help")); }
  function showSupplier() {
    cancelLookup();
    const text = query.value.trim();
    const suppliers = (getState().suppliers || []).filter((item) => UUID.test(String(item.id)) && !item.archived);
    const matches = suppliers.filter((item) => normalize(item.name).includes(normalize(text)));
    create.hidden = !text || text.length > 200 || suppliers.some((item) => normalize(item.name) === normalize(text));
    create.textContent = `明确新建责任单位「${text}」`;
    results.replaceChildren();
    for (const supplier of matches.slice(0, 50)) {
      const row = element("div", "", "development-grid-picker-candidate");
      row.append(element("strong", String(supplier.name)), element("p", `已有单位 · ${shortId(supplier.id)}`, "development-help"));
      row.append(button("选择此责任单位", () => {
        const selectedKeys = [...keys];
        commit(() => onSupplier(selectedKeys, {id: supplier.id, name: supplier.name, create: false}));
      }));
      results.append(row);
    }
    if (!matches.length) message(text ? "没有匹配的已有责任单位。确认名称后，可点击下方明确新建。" : "尚无责任单位，输入名称后可明确新建。");
    if (matches.length > 50) results.append(element("p", "仅显示前 50 项，请继续输入名称缩小范围。", "development-help"));
  }
  function showSources(data) {
    results.replaceChildren();
    for (const item of data.candidates) {
      if (!UUID.test(String(item?.id)) || typeof item.code !== "string" || !item.code.trim()) continue;
      const source = {id: item.id, code: item.code, name: String(item.name || "")};
      const row = element("div", "", "development-grid-picker-candidate");
      row.append(element("strong", `${source.code} · ${source.name}`), element("p", `已登记开发来源 · ${shortId(source.id)}`, "development-help"));
      const actions = element("div", "", "command-actions");
      actions.append(button("只选此借用来源", () => {
        const key = keys[0];
        commit(() => onSource(key, {source}));
      }));
      row.append(actions);
      for (const suggestion of Array.isArray(item.task_suggestions) ? item.task_suggestions.slice(0, 5) : []) {
        const label = `${String(suggestion.project_title || "开发单")} · ${String(suggestion.component_code || suggestion.fields?.name || "任务")}`;
        row.append(element("p", `资料建议来自：${label}`, "development-help"));
        const choices = element("div", "", "command-actions");
        const choice = button("带入此任务资料", () => {
          const key = keys[0];
          const fields = suggestionFields(suggestion.fields);
          commit(() => onSource(key, {source, fields}));
        });
        choice.setAttribute("aria-label", `采用借用来源 ${source.code} 并带入 ${label} 的任务资料`);
        choices.append(choice); row.append(choices);
      }
      if (item.suggestions_truncated) row.append(element("p", "另有任务引用此来源，可在开发单中核对。", "development-help"));
      results.append(row);
    }
    if (!results.children.length) message("没有匹配的已登记开发来源。可关闭窗口保留手填借用编号，保存时建立新的稳定来源快照。");
    if (data.has_more) results.append(element("p", "仍有更多来源，请继续输入编号或名称缩小范围。", "development-help"));
  }
  async function fetchSources(token) {
    if (token !== generation || mode !== "source" || !dialog.open) return;
    const abort = new AbortController(); controller = abort;
    timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const url = new URL(root.dataset.sourceUrl, location.href);
      if (url.origin !== location.origin) throw new Error("invalid lookup origin");
      url.searchParams.set("q", query.value.trim());
      url.searchParams.set("limit", "20");
      const response = await fetch(url, {credentials: "same-origin", cache: "no-store", signal: abort.signal,
        headers: {Accept: "application/json"}});
      const data = await response.json();
      if (token !== generation || mode !== "source" || !dialog.open) return;
      if (!response.ok || !data.ok || !Array.isArray(data.candidates)) throw new Error("lookup failed");
      showSources(data);
    } catch {
      if (token !== generation || mode !== "source" || !dialog.open) return;
      message("开发来源检索失败或超时。请重新输入重试；当前行的借用编号仍保留。");
      onError("开发来源检索失败或超时，当前输入已保留。");
    } finally {
      if (token === generation) { clearTimeout(timeout); controller = null; results.removeAttribute("aria-busy"); }
    }
  }
  function searchSources(immediate = false) {
    cancelLookup(); create.hidden = true;
    if (query.value.trim().length > 100 || /[\u0000-\u001f]/.test(query.value)) {
      message("检索内容最多 100 字，不能包含控制字符。"); return;
    }
    message("正在检索已登记开发来源…"); results.setAttribute("aria-busy", "true");
    const token = generation;
    if (immediate) fetchSources(token);
    else debounce = setTimeout(() => fetchSources(token), 180);
  }
  function open(nextMode, selectedKeys, value, returnTrigger) {
    cancelLookup(); mode = nextMode; keys = [...selectedKeys]; trigger = returnTrigger;
    focusIdentity = trigger ? {key: trigger.closest("[data-grid-row]")?.dataset.gridRow, action: trigger.dataset.gridAction} : null;
    query.value = String(value || ""); query.maxLength = mode === "source" ? 100 : 200;
    title.textContent = mode === "source" ? "选择借用开发来源" : "选择责任单位";
    help.textContent = mode === "source"
      ? "仅检索已登记开发来源。本型号编码保留；任务资料须明确点选，完成情况、日期及图纸采用不带入。"
      : "选择已有责任单位；新名称需点击明确新建，保存表格时才建立主档。";
    create.hidden = true;
    if (!dialog.open) dialog.showModal();
    query.focus(); query.select();
    if (mode === "supplier") showSupplier(); else searchSources(true);
  }
  query.addEventListener("input", (event) => {
    if (event.isComposing) { cancelLookup(); create.hidden = true; message("输入完成后检索…"); return; }
    if (mode === "supplier") showSupplier(); else if (mode === "source") searchSources();
  });
  query.addEventListener("compositionend", () => { if (mode === "supplier") showSupplier(); else if (mode === "source") searchSources(); });
  query.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); if (mode === "source") searchSources(true); }
    if (event.key === "ArrowDown" && !event.isComposing) { const first = results.querySelector("button"); if (first) { event.preventDefault(); first.focus(); } }
  });
  create.addEventListener("click", () => {
    const name = query.value.trim();
    if (mode !== "supplier" || create.hidden || !name || name.length > 200) return;
    const suppliers = getState().suppliers || [];
    if (suppliers.some((item) => !item.archived && normalize(item.name) === normalize(name))) { showSupplier(); return; }
    const selectedKeys = [...keys];
    commit(() => onSupplier(selectedKeys, {id: null, name, create: true}));
  });
  for (const node of dialog.querySelectorAll("[data-grid-picker-close]")) node.addEventListener("click", close);
  dialog.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); event.stopPropagation(); }
  });
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("close", () => { if (!dialog.open) { cancelLookup(); mode = null; restoreFocus(); } });
  return {openSupplier: (selectedKeys, value, returnTrigger) => open("supplier", selectedKeys, value, returnTrigger),
    openSource: (key, value, returnTrigger) => open("source", [key], value, returnTrigger)};
}
