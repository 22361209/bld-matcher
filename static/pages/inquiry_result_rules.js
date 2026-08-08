const MAX_INQUIRY_PRICE = 99999999.99;

export const validateInquiryPrice = (rawValue, { allowBlank = false } = {}) => {
  const text = String(rawValue ?? "").trim();
  if (!text) {
    return allowBlank
      ? { valid: true, normalized: "", error: "" }
      : { valid: false, normalized: "", error: "请填写本次报价含税单价。" };
  }
  const value = Number(text);
  if (!Number.isFinite(value)) {
    return { valid: false, normalized: "", error: "含税单价必须是有效数字。" };
  }
  if (value < 0 || value > MAX_INQUIRY_PRICE) {
    return {
      valid: false,
      normalized: "",
      error: `含税单价必须在 0 到 ${MAX_INQUIRY_PRICE.toFixed(2)} 之间。`,
    };
  }
  const cents = value * 100;
  if (Math.abs(cents - Math.round(cents)) > 1e-7) {
    return { valid: false, normalized: "", error: "含税单价最多保留两位小数。" };
  }
  return { valid: true, normalized: (Math.round(cents) / 100).toFixed(2), error: "" };
};

export const inquiryPriceAdjustment = (rawValue, catalogPrice, { allowBlank = false } = {}) => {
  const validation = validateInquiryPrice(rawValue, { allowBlank });
  if (!validation.valid) return { ...validation, override: undefined };
  if (!validation.normalized) return { ...validation, override: null };
  const catalogText = String(catalogPrice ?? "").trim();
  if (!catalogText) return { ...validation, override: validation.normalized };
  const catalogValidation = validateInquiryPrice(catalogText);
  const override = catalogValidation.valid && catalogValidation.normalized === validation.normalized
    ? null
    : validation.normalized;
  return { ...validation, override };
};

export const createInquiryRequestGate = () => {
  let sequence = 0;
  return {
    begin: () => ++sequence,
    invalidate: () => ++sequence,
    isCurrent: (candidate) => candidate === sequence,
  };
};

export const createInquiryInitializationGate = () => {
  const initializedRoots = new WeakSet();
  return {
    claim(root) {
      if ((typeof root !== "object" && typeof root !== "function") || root === null) return false;
      if (initializedRoots.has(root)) return false;
      initializedRoots.add(root);
      return true;
    },
  };
};

export const inquiryProductDisplay = (product = {}) => {
  const bldNo = String(product.bld_no || "").trim();
  const item = String(product.item || "").trim();
  const status = String(product.product_status || "").trim() || "产品状态未填写";
  const price = product.price_cny === null || product.price_cny === undefined || product.price_cny === ""
    ? "目录含税价未填写"
    : `目录含税价 ¥${Number(product.price_cny).toFixed(2)}`;
  return { bldNo, item, status, price };
};

export const inquiryTargetAdjustment = (targetBldNo, defaultBldNo) => {
  const target = String(targetBldNo || "").trim();
  const original = String(defaultBldNo || "").trim();
  if (!target || target.toUpperCase() === original.toUpperCase()) return null;
  return target;
};

export const inquiryBldSelectionState = (rawValue, currentBldNo, { confirmed = true } = {}) => {
  const value = String(rawValue || "").trim();
  const current = String(currentBldNo || "").trim();
  const valid = Boolean(confirmed && value && current && value.toUpperCase() === current.toUpperCase());
  return {
    valid,
    error: valid ? "" : "请从启用产品候选中选择 BLD NO.。",
  };
};

export const rankInquiryProducts = (products, rawQuery) => {
  const query = String(rawQuery || "").trim().toUpperCase();
  const score = (product) => {
    const bldNo = String(product?.bld_no || "").trim().toUpperCase();
    if (!query || !bldNo) return 3;
    if (bldNo === query) return 0;
    if (bldNo.startsWith(query)) return 1;
    if (bldNo.includes(query)) return 2;
    return 3;
  };
  return (Array.isArray(products) ? products : [])
    .map((product, index) => ({ product, index, score: score(product) }))
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map(({ product }) => product);
};
