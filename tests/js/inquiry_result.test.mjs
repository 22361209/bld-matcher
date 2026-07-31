import assert from "node:assert/strict";
import test from "node:test";

import {
  clearQuoteCustomerValidity,
  createInquiryRequestGate,
  inquiryAttachmentFilename,
  inquiryPriceAdjustment,
  inquiryProductDisplay,
  inquiryTargetAdjustment,
  validateInquiryPrice,
} from "../../static/pages/inquiry_result.js";

test("inquiry prices reject touched blanks, out-of-range values, and fractional cents", () => {
  assert.equal(validateInquiryPrice("").valid, false);
  assert.deepEqual(validateInquiryPrice("", { allowBlank: true }), {
    valid: true,
    normalized: "",
    error: "",
  });
  assert.equal(validateInquiryPrice("100000000").valid, false);
  assert.equal(validateInquiryPrice("80.001").valid, false);
  assert.deepEqual(validateInquiryPrice(" 80.5 "), {
    valid: true,
    normalized: "80.50",
    error: "",
  });
});

test("zero remains an explicit override when the catalog price is missing or nonzero", () => {
  assert.equal(inquiryPriceAdjustment("0", "").override, "0.00");
  assert.equal(inquiryPriceAdjustment("0.00", "58").override, "0.00");
  assert.equal(inquiryPriceAdjustment("58", "58.00").override, null);
  assert.equal(inquiryPriceAdjustment("", "", { allowBlank: true }).override, null);
});

test("stale product searches cannot replace the latest request", () => {
  const gate = createInquiryRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
  gate.invalidate();
  assert.equal(gate.isCurrent(second), false);
});

test("product candidates expose the status and catalog price needed to distinguish variants", () => {
  assert.deepEqual(
    inquiryProductDisplay({
      bld_no: "K8053LA",
      item: "Front Left Lower Control Arm",
      product_status: "2个衬套1个球头",
      price_cny: 80,
    }),
    {
      bldNo: "K8053LA",
      item: "Front Left Lower Control Arm",
      status: "2个衬套1个球头",
      price: "目录含税价 ¥80.00",
    },
  );
  assert.equal(inquiryProductDisplay({ bld_no: "X" }).price, "目录含税价未填写");
});

test("selecting the original product does not create a false product adjustment", () => {
  assert.equal(inquiryTargetAdjustment("K8053LB", "K8053LB"), null);
  assert.equal(inquiryTargetAdjustment(" k8053lb ", "K8053LB"), null);
  assert.equal(inquiryTargetAdjustment("K8053LA", "K8053LB"), "K8053LA");
});

test("quote attachment download rejects redirects and non-Excel responses", () => {
  const response = (disposition, { ok = true, redirected = false } = {}) => ({
    ok,
    redirected,
    headers: { get: (name) => (name === "Content-Disposition" ? disposition : "") },
  });
  const encoded = "attachment; filename*=UTF-8''%E6%8A%A5%E4%BB%B7%E7%BB%93%E6%9E%9C.xlsx";

  assert.equal(inquiryAttachmentFilename(response(encoded)), "报价结果.xlsx");
  assert.equal(inquiryAttachmentFilename(response(encoded, { redirected: true })), null);
  assert.equal(inquiryAttachmentFilename(response("text/html")), null);
  assert.equal(inquiryAttachmentFilename(response("attachment; filename=error.html")), null);
});

test("download-only flow can clear a stale quote-customer validation error", () => {
  const messages = [];
  clearQuoteCustomerValidity({ setCustomValidity: (message) => messages.push(message) });
  assert.deepEqual(messages, [""]);
});
