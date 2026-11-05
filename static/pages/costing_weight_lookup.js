const codeOf = (item) => String(item.product_code || "").trim();
const isTubeLine = (item) => String(item.name || "").includes("焊接管");
const isPlateLine = (item) => /主板|副板|加强板|冲压件|冲压片|冲压板/.test(item.name || "");

// Key requests by row object, since inserting/deleting another row changes its table index.
export function createWeightLookup({items, lookup, notify}) {
  const states = new WeakMap();
  const state = (item) => states.get(item);
  const changed = (item) => {
    if (item.weight_source?.kind === "stamping") item.scrap_expected = true;
    delete item.weight_source;
    item.weight_choice = null;
    states.set(item, {phase: "edited"});
    notify(item);
  };
  const manual = (item) => {
    if (item.weight_source?.kind === "stamping" || isPlateLine(item)) item.scrap_expected = true;
    delete item.weight_source;
    item.weight_choice = null;
    states.set(item, {phase: "manual"});
    notify(item, "manual");
  };
  const choose = (item, candidate) => {
    item.weight_choice = candidate.token;
    item.weight_source = candidate;
    item.weight = candidate.gross_weight;
    item.scrap_expected = candidate.kind === "stamping";
    states.delete(item);
    notify(item, candidate.kind);
  };
  const clearMode = (item) => {
    states.delete(item);
    if (item.weight_source || item.weight_choice) {
      delete item.weight_source;
      item.weight_choice = null;
    }
    item.scrap_expected = false;
  };
  const resolve = (item, force = false, {kind = "", optional = false} = {}) => {
    if (item.mode !== "weight") return;
    const prior = state(item);
    if (prior?.phase === "loading") return prior.task;
    if (!force && prior?.phase !== "edited") return;
    const code = codeOf(item);
    if (!code) {
      if (item.weight) {
        states.set(item, {phase: "missing", message: "编码为空，请核对手填重量。"});
        notify(item);
      } else manual(item);
      return;
    }
    const request = {phase: "loading"};
    states.set(item, request);
    notify(item);
    const current = () => state(item) === request && item.mode === "weight" && codeOf(item) === code && items.includes(item);
    request.task = (async () => {
      try {
        const result = await lookup(code, kind || (isTubeLine(item) ? "tube" : ""));
        if (!current()) return;
        const candidates = result.candidates;
        if (optional && !candidates.length) {
          if (prior) states.set(item, prior);
          else states.delete(item);
          notify(item);
          return;
        }
        if (candidates.length && candidates.every((candidate) => candidate.kind === "stamping")) item.scrap_expected = true;
        // Refreshing a saved choice can reuse its exact catalog identity, even when the code has several specifications.
        const previousSource = item.weight_source;
        const match = candidates.length === 1 ? candidates[0] : candidates.find((option) =>
          previousSource && option.kind === previousSource.kind && option.source_id === previousSource.source_id);
        if (match?.gross_weight != null) {
          choose(item, match);
          return;
        }
        if (candidates.length && candidates.some((option) => option.gross_weight != null)) {
          states.set(item, {phase: "multiple", candidates});
        } else {
          const message = !result.available ? "当前无重量资料，请核对手填重量。" : candidates.length ? "来源缺少有效重量。" : "编码未匹配，请核对手填重量。";
          states.set(item, {phase: "missing", message});
        }
      } catch {
        if (!current()) return;
        states.set(item, {phase: "error", message: "取重失败，可重试或手填。"});
      }
      notify(item);
    })();
    return request.task;
  };
  const unresolved = () => items.find((item) => {
    const phase = state(item)?.phase;
    return item.mode === "weight" && phase && phase !== "manual";
  });
  const removed = (item) => {
    if (state(item)?.phase === "loading") states.set(item, {phase: "edited"});
  };
  const settle = async () => {
    await Promise.all(items.map((item) => resolve(item)));
    return unresolved();
  };
  const refreshStamps = () => Promise.all(items.map((item) => {
    if (item.mode !== "weight" || isTubeLine(item) || item.weight_source?.kind === "tube") return;
    const knownStamp = item.scrap_expected || item.weight_source?.kind === "stamping" || isPlateLine(item);
    if (knownStamp || codeOf(item)) return resolve(item, true, {kind: "stamping", optional: !knownStamp && !state(item)});
  }));
  return {state, changed, manual, choose, clearMode, resolve, settle, unresolved, removed, refreshStamps};
}

export function weightLookupControls(item, index, controller, editable) {
  const region = document.createElement("div");
  region.dataset.weightLookup = "";
  region.className = "costing-weight-lookup";
  const action = (label, fn) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-button";
    button.textContent = label;
    button.addEventListener("click", fn);
    region.append(button);
  };
  if (item.mode !== "weight") return region;
  const entry = controller.state(item);
  const note = (str) => {
    const text = document.createElement("small");
    text.setAttribute("role", "status");
    text.textContent = str;
    region.append(text);
  };
  if (entry?.phase === "multiple") {
    const select = document.createElement("select");
    select.setAttribute("aria-label", `第 ${index + 1} 项选择重量来源`);
    select.add(new Option("选择重量来源…", ""));
    entry.candidates.forEach((option, n) => {
      const label = [option.kind === "tube" ? "管件" : "冲压件", option.name, option.model, option.spec, option.pieces ? `每条出 ${option.pieces} 件` : "", option.net_weight ? `净重 ${option.net_weight} kg` : "", option.gross_weight == null ? "重量待补" : `毛重 ${option.gross_weight} kg`, option.source_id?.slice(-8)].filter(Boolean).join(" · ");
      const choice = new Option(label, String(n));
      choice.disabled = option.gross_weight == null;
      select.add(choice);
    });
    select.addEventListener("change", () => {
      if (select.value !== "") controller.choose(item, entry.candidates[Number(select.value)]);
    });
    region.append(select);
  } else if (entry?.message) note(entry.message);
  else if (entry?.phase === "loading") note("正在取重…");
  if (editable && ["error", "missing", "multiple"].includes(entry?.phase)) {
    if (entry.phase === "error") action("重试", () => controller.resolve(item, true));
    action(item.weight ? "确认手填重量" : "手填重量", () => {
      controller.manual(item);
      if (!item.weight) document.querySelector(`[data-item-index="${index}"][data-item-field="weight"]`)?.focus();
    });
  }
  return region;
}

export function weightSourceDetails(item, controller, editable) {
  const row = document.createElement("div");
  row.className = "costing-weight-source";
  const source = item.weight_source;
  const summary = document.createElement("span");
  if (source?.kind === "stamping") {
    summary.textContent = `冲压件 ${source.product_code} · ${source.name || ""} ${source.spec || ""} · 毛重 ${source.gross_weight} kg · 净重 ${source.net_weight ?? "待补"} kg · 单片废料 ${source.scrap_weight ?? "待补"} kg`;
  } else if (source?.kind === "tube") {
    summary.textContent = `焊接管 ${source.product_code} · ${source.spec || ""} · 管件重量 ${source.gross_weight} kg`;
  } else summary.textContent = "重量来源：手工录入";
  row.append(summary);
  if (editable && codeOf(item)) {
    const refresh = document.createElement("button");
    refresh.className = "link-button";
    refresh.type = "button";
    refresh.textContent = source ? "重新取重" : "按编码取重";
    refresh.addEventListener("click", () => controller.resolve(item, true));
    row.append(refresh);
  }
  return row;
}

export function showWeightOrigin(row, item) {
  const input = row.querySelector('[data-item-field="weight"]');
  const unit = input?.nextElementSibling;
  if (!unit) return;
  const source = item.weight_source;
  unit.textContent = source ? "kg ✓" : "kg";
  input.title = unit.title = source ? `已取${source.kind === "tube" ? "管件" : "冲压件"} ${source.product_code} 重量，可展开资料查看。` : "";
}
