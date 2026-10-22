import test from "node:test";
import assert from "node:assert/strict";
import { createCatalogVisibilityUpdater } from "../../static/pages/catalog_visibility.js";

const initial = { en: true, zh: true, revision: "first" };
const success = (en, zh, revision) => ({ ok: true, catalog_visible_en: en, catalog_visible_zh: zh, updated_at: revision });

test("one flag changes independently and each successful write advances revision", async () => {
  const sent = [], rendered = [];
  const updater = createCatalogVisibilityUpdater(initial, { render: (...args) => rendered.push(args),
    send: async values => { sent.push(values); return sent.length === 1 ? success(true, false, "second") : success(false, false, "third"); } });
  assert.equal(await updater.update("zh", false), true);
  assert.deepEqual(updater.state, { en: true, zh: false, revision: "second" });
  assert.equal(await updater.update("en", false), true);
  assert.equal(sent[1].expected_updated_at, "second");
  assert.equal(sent[1].expected_visible, true);
  assert.equal(rendered.at(-1)[2], "已保存");
});

test("permission and network failures restore saved values and unlock controls", async () => {
  for (const error of ["当前账号没有权限", "网络不可用"]) {
    const rendered = [];
    const updater = createCatalogVisibilityUpdater(initial, { render: (...args) => rendered.push(args), send: async () => { throw new Error(error); } });
    assert.equal(await updater.update("zh", false), false);
    assert.deepEqual(updater.state, initial);
    assert.equal(updater.busy, false);
    assert.deepEqual(rendered.at(-1)[0], initial);
    assert.match(rendered.at(-1)[2], /已恢复勾选/);
  }
});

test("rapid clicks cannot create overlapping or out-of-order writes", async () => {
  let complete, calls = 0;
  const rendered = [];
  const updater = createCatalogVisibilityUpdater(initial, { render: (...args) => rendered.push(args), send: () => {
    calls += 1; return new Promise(resolve => { complete = resolve; });
  } });
  const pending = updater.update("zh", false);
  assert.equal(updater.busy, true);
  assert.equal(await updater.update("zh", true), false);
  assert.equal(await updater.update("en", false), false);
  assert.equal(calls, 1);
  assert.equal(rendered.at(-1)[0].zh, false);
  complete(success(true, false, "next"));
  assert.equal(await pending, true);
  assert.deepEqual(updater.state, { en: true, zh: false, revision: "next" });
});

test("malformed success payload does not overwrite saved values", async () => {
  const updater = createCatalogVisibilityUpdater(initial, { render: () => {}, send: async () => ({ ok: true }) });
  assert.equal(await updater.update("en", false), false);
  assert.deepEqual(updater.state, initial);
});
