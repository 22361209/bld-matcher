export function createScrapSummary(form, format, editable) {
  const mode = form.elements.namedItem("scrap_mode");
  const auto = () => mode.value === "auto";
  const applyMode = () => {
    for (const [selector, show] of [["[data-scrap-auto]", auto()], ["[data-scrap-legacy]", !auto()]]) {
      for (const node of form.querySelectorAll(selector)) {
        node.hidden = !show;
        for (const input of node.querySelectorAll("input")) input.disabled = !show;
      }
    }
  };
  const adopt = () => {
    if (!editable || auto()) return;
    mode.value = "auto";
    applyMode();
  };
  const update = (calculated) => {
    const known = calculated.scrap_known_weight;
    const knownText = known == null ? "—" : format(known);
    form.querySelector("[data-scrap-weight]").textContent = auto() ? knownText : "—";
    form.querySelector("[data-scrap-complete]").textContent = auto() ? calculated.scrap_complete === false ? `已知片件小计 ${format(calculated.scrap_known_amount)} 元，部分重量待补` : "" : "沿用历史抵减额";
    const actual = form.elements.namedItem("scrap_credit_override");
    actual.placeholder = "自动跟随";
    form.querySelector("[data-scrap-applied]").textContent = calculated.scrap_credit == null ? "自动抵减待核对。" :
      actual.value === "" ? `自动采用 ${format(calculated.scrap_credit)} 元/件。` : `手动采用 ${format(calculated.scrap_credit)} 元/件。`;
    const container = form.querySelector("[data-linked-scrap]");
    container.replaceChildren();
    if (!auto()) return;
    const lines = calculated.items.filter((item) => item.weight_source?.kind === "stamping" || item.scrap_expected);
    if (!lines.length) return;
    const table = document.createElement("table"); table.className = "data-table costing-scrap-table";
    table.createCaption().textContent = "本单冲压件废料";
    const head = table.createTHead().insertRow();
    for (const label of ["项目 / 编码", "毛重 kg", "净重 kg", "单片废料 kg", "用量", "本行废料 kg"]) {
      const th = document.createElement("th"); th.scope = "col"; th.textContent = label; head.append(th);
    }
    const body = table.createTBody();
    for (const item of lines) {
      const row = body.insertRow();
      const src = item.weight_source;
      const cells = [[item.name, item.product_code].filter(Boolean).join(" · "), src?.gross_weight, src?.net_weight, item.scrap_weight, item.quantity ?? "1", item.scrap_total_weight];
      for (const value of cells) row.insertCell().textContent = value == null ? "待补" : String(value);
    }
    const scroll = document.createElement("div"); scroll.className = "data-table-scroll"; scroll.append(table); container.append(scroll);
  };
  applyMode();
  return {auto, adopt, update};
}
