const fixed = new Intl.NumberFormat("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});

export function costAmountLabel(value, empty = "待补充") {
  if (value == null || value === "") return empty;
  // Pass the decimal string directly. Conversion to Number would lose precision near rounding boundaries.
  const label = fixed.format(value);
  return label === "-0.00" ? "0.00" : label;
}

export function setCostInput(input, value) {
  const raw = String(value ?? "");
  if (input.type === "number") {
    input.dataset.exactDecimal = raw;
    input.value = costAmountLabel(raw, "").replaceAll(",", "");
  } else {
    input.value = raw;
  }
}
