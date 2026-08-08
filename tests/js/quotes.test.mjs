import assert from "node:assert/strict";
import test from "node:test";

import {
  parseQuoteEditPayload,
  parseQuoteFilterState,
  quoteDeleteConfirmation,
  quoteFilterOptionState,
} from "../../static/pages/quotes.js";


test("quote edit payload preserves special characters and only accepts matching mutation URLs", () => {
  const special = `客户 </script><script>alert('x')</script> & "quoted"`;
  const payload = parseQuoteEditPayload(JSON.stringify({
    id: 42,
    version: 7,
    customer_name: special,
    bld_no: "BLD<&>'\"",
    customer_product_code: "客户编码 & < >",
    tax_price: 12.34,
    net_price: null,
    currency: "USD",
    quote_date: "2026-08-08",
    remark: special,
    edit_url: "/quotes/42/edit",
    delete_url: "/quotes/42/delete",
  }));

  assert.equal(payload.customer_name, special);
  assert.equal(payload.remark, special);
  assert.equal(payload.net_price, "");
  assert.equal(payload.tax_price, "12.34");
  assert.equal(payload.edit_url, "/quotes/42/edit");
  assert.equal(payload.delete_url, "/quotes/42/delete");
  assert.equal(
    quoteDeleteConfirmation(payload),
    `确认删除这条报价记录（${special} / BLD<&>'\" / 2026-08-08）？删除后不能恢复。`,
  );

  assert.equal(parseQuoteEditPayload('{"id":42,"version":7,"edit_url":"https://evil.example/quotes/42/edit"}'), null);
  assert.equal(parseQuoteEditPayload('{"id":42,"version":7,"edit_url":"/quotes/41/edit"}'), null);
  assert.equal(parseQuoteEditPayload("not-json"), null);
});


test("quote edit payload defaults unsupported legacy currency like the former select markup", () => {
  const payload = parseQuoteEditPayload({
    id: 1,
    version: 0,
    currency: "JPY",
    edit_url: "/quotes/1/edit",
  });
  assert.equal(payload.currency, "CNY");
  assert.equal(payload.delete_url, "");
});


test("lazy quote filter state preserves server candidate order and selection semantics", () => {
  const state = parseQuoteFilterState(JSON.stringify({
    options: {
      customer_name: [
        { value: "乙", label: "客户乙", count: 8 },
        { value: "甲", label: "客户甲", count: 3 },
      ],
      currency: [
        { value: "USD", label: "USD", count: 4 },
        { value: "CNY", label: "CNY", count: 2 },
      ],
    },
    selected: { customer_name: ["甲"] },
  }));

  assert.deepEqual(quoteFilterOptionState(state, "customer_name"), [
    { value: "乙", label: "客户乙", count: 8, checked: false },
    { value: "甲", label: "客户甲", count: 3, checked: true },
  ]);
  assert.deepEqual(quoteFilterOptionState(state, "currency"), [
    { value: "USD", label: "USD", count: 4, checked: true },
    { value: "CNY", label: "CNY", count: 2, checked: true },
  ]);
  assert.deepEqual(quoteFilterOptionState(state, "missing"), []);
});


test("malformed filter JSON fails closed to an empty candidate state", () => {
  assert.deepEqual(parseQuoteFilterState("{"), { options: {}, selected: {} });
});
