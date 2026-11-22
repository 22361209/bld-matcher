const DIMENSIONS = {
  outer_diameter_mm: "外径",
  inner_diameter_mm: "内径",
  weight_length_mm: "计重长度",
  tolerance_mm: "公差",
  consumption_mm: "消耗",
};
const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const text = (value) => value == null ? "" : String(value).trim();
const isManualWeightCode = (code, codes) => codes.some((candidate) => text(candidate).toUpperCase() === text(code).toUpperCase());

const numberInput = (value, label, positive = false) => {
  const raw = text(value);
  if (!raw) return {value: null};
  const number = Number(raw);
  if (!DECIMAL.test(raw) || !Number.isFinite(number)) return {error: `${label}必须是有限数字`};
  if (number < 0 || (positive && number === 0)) return {error: `${label}必须${positive ? "大于" : "大于或等于"} 0`};
  return {value: number};
};

export function previewTubeWeight(values, {manualWeightCodes = [], editing = false} = {}) {
  const borrowed = text(values.borrowed_from);
  if (borrowed) {
    const length = numberInput(values.weight_length_mm, "计重长度", true);
    if (length.error) return {state: "invalid", weight: null, errors: {weight_length_mm: length.error}, message: `${length.error}；请先修正计重长度，重量仍跟随借用来源 ${borrowed}。`};
    return {state: "borrowed", weight: null, errors: {}, message: `重量跟随借用来源 ${borrowed}，不独立计算或填写；保存时核对来源管件。`};
  }
  const errors = {};
  const manualOnly = isManualWeightCode(values.code, manualWeightCodes);
  const mode = manualOnly ? "manual" : text(values.weight_mode);
  if (!["auto", "manual"].includes(mode)) return {state: "invalid", weight: null, errors: {weight_mode: "请选择计重方式"}, message: "请选择计重方式"};
  if (mode === "manual") {
    const manual = numberInput(values.weight_kg, "重量");
    const length = numberInput(values.weight_length_mm, "计重长度", true);
    if (manual.error) errors.weight_kg = manual.error;
    if (length.error) errors.weight_length_mm = length.error;
    if (Object.keys(errors).length) return {state: "invalid", weight: null, errors, message: Object.values(errors).join("；")};
    const message = manualOnly ? `${text(values.code).toUpperCase()} 使用特殊公式，待核对；保留手工重量，不按通用公式计算。`
      : editing && length.value === null ? "原重量暂保留；补齐计重长度等参数后可切换自动计算。"
      : "手工重量独立保存，修改尺寸不会自动改写。";
    return {state: "manual", weight: manual.value, errors, message};
  }
  const parsed = {};
  const missing = [];
  for (const [key, label] of Object.entries(DIMENSIONS)) {
    const result = numberInput(values[key], label, key === "weight_length_mm" || key === "outer_diameter_mm");
    parsed[key] = result.value;
    if (result.error) errors[key] = result.error;
    else if (result.value === null) missing.push(label);
  }
  if (parsed.outer_diameter_mm != null && parsed.inner_diameter_mm != null &&
      parsed.outer_diameter_mm <= parsed.inner_diameter_mm) errors.outer_diameter_mm = "外径必须大于内径";
  if (Object.keys(errors).length) return {state: "invalid", weight: null, errors, message: Object.values(errors).join("；")};
  if (missing.length) return {state: "missing", weight: null, errors, message: `待补充：${missing.join("、")}。可先保存资料，重量留空。`};
  const {outer_diameter_mm: outer, inner_diameter_mm: inner, weight_length_mm: length, tolerance_mm: tolerance, consumption_mm: consumption} = parsed;
  const weight = ((outer ** 2 - inner ** 2) / 4) * 0.02466 * (length + tolerance + consumption) / 1000;
  if (!Number.isFinite(weight) || weight <= 0) return {state: "invalid", weight: null, errors: {outer_diameter_mm: "计重结果超出可计算范围，请核对尺寸"}, message: "计重结果超出可计算范围，请核对尺寸。"};
  return {state: "auto", weight, errors, message: "已按尺寸预览单根耗料重量；保存时按当前参数重新计算。"};
}

export function setupTubeWeightForm(form) {
  const mode = form.querySelector("[data-tube-weight-mode]");
  const mirror = form.querySelector("[data-tube-weight-mode-mirror]");
  const weight = form.querySelector('[name="weight_kg"]');
  const code = form.querySelector('[name="code"]');
  const borrowed = form.querySelector("[data-tube-borrowed-from]");
  const status = form.querySelector("[data-tube-weight-status]");
  const formula = form.querySelector("[data-tube-weight-formula]");
  if (!mode || !mirror || !weight || !borrowed || !status) return;
  const numeric = Object.fromEntries([...Object.keys(DIMENSIONS), "weight_kg"].map((key) => [key, form.querySelector(`[name="${key}"]`)]));
  const manualWeightCodes = JSON.parse(form.dataset?.tubeManualWeightCodes || "[]");
  const editing = form.dataset?.tubeEditing === "true";
  let manualWeight = weight.value;
  let previousMode = mode.value;
  let regularMode = mode.value;
  let wasManualOnly = false;
  let wasBorrowed = Boolean(text(borrowed.value));
  let editableBorrowedLength = false;
  const update = () => {
    const isBorrowed = Boolean(text(borrowed.value));
    const manualOnly = isManualWeightCode(code?.value, manualWeightCodes);
    if (previousMode === "manual" && !wasBorrowed) manualWeight = weight.value;
    if (manualOnly) {
      if (!wasManualOnly) regularMode = mode.value;
      mode.value = "manual";
    } else if (wasManualOnly) mode.value = regularMode;
    else regularMode = mode.value;
    if (mode.value === "manual" && !isBorrowed) weight.value = manualWeight;
    const values = Object.fromEntries(Object.entries(numeric).map(([key, control]) => [key, control.value]));
    const result = previewTubeWeight({...values, code: code?.value, weight_mode: mode.value, borrowed_from: borrowed.value}, {manualWeightCodes, editing});
    // Keep a correction editable while typing, even after its first valid digit.
    if (!isBorrowed) editableBorrowedLength = false;
    else if (result.errors.weight_length_mm) editableBorrowedLength = true;
    mode.disabled = isBorrowed || manualOnly;
    mirror.disabled = !mode.disabled;
    mirror.value = mode.value;
    mode.setCustomValidity(result.errors.weight_mode || "");
    for (const [key, control] of Object.entries(numeric)) {
      control.readOnly = (isBorrowed && !(key === "weight_length_mm" && editableBorrowedLength)) || (key === "weight_kg" && mode.value === "auto");
      control.setCustomValidity(result.errors[key] || "");
      control.setAttribute("aria-invalid", result.errors[key] ? "true" : "false");
    }
    if (isBorrowed || mode.value === "auto") weight.value = result.weight == null ? "" : String(Number(result.weight.toPrecision(15)));
    weight.placeholder = isBorrowed ? "跟随借用来源" : mode.value === "auto" ? "参数齐全后计算" : "";
    status.textContent = result.message;
    status.dataset.state = result.state;
    if (formula) formula.hidden = isBorrowed || mode.value !== "auto";
    previousMode = mode.value;
    wasManualOnly = manualOnly;
    wasBorrowed = isBorrowed;
    return result;
  };
  const onInput = (event) => {
    if (event.target.matches("[data-tube-weight-field], [data-tube-weight-mode], [data-tube-borrowed-from], [data-tube-code]")) update();
  };
  form.addEventListener("input", onInput);
  form.addEventListener("change", onInput);
  const submit = form.querySelector('button[type="submit"]');
  form.addEventListener("submit", (event) => {
    const result = update();
    if (result.state === "invalid") {
      event.preventDefault();
      form.reportValidity();
      return;
    }
    if (submit && form.checkValidity()) submit.disabled = true;
  });
  const pageWindow = form.ownerDocument?.defaultView ?? (typeof window !== "undefined" ? window : null);
  pageWindow?.addEventListener("pageshow", () => {
    if (submit) submit.disabled = false;
  });
  update();
  return {update};
}

if (typeof document !== "undefined" && document.body.dataset.page === "tubes.edit") {
  const form = document.querySelector("[data-tube-form]");
  if (form) setupTubeWeightForm(form);
}
