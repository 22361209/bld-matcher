import assert from "node:assert/strict";
import test from "node:test";

import { setupExcelSaveLinks } from "../../static/components/quote_excel_links.js";
import { chooseExcelDestination, saveExcelResponse } from "../../static/components/excel_save.js";
import { setupInquiryDownloadForm } from "../../static/pages/inquiry_result_downloads.js";

const GENERATED_URL = "/match/download";
const WRITE_URL = "/match/write-quotes";
const ATTACHMENT_URL = "/download/quote/one.xlsx";
const RECORD_URL = "/quotes?quote_no=Q123";
const BOOK = Uint8Array.from([0x50, 0x4b, 3, 4, 0, 0xff, 0x10]);

function attachment(bytes = BOOK) {
  return new Response(bytes, { headers: {
    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "Content-Disposition": 'attachment; filename="one.xlsx"',
  } });
}

test("the file picker preserves XLS type and a cancelled native picker uses no fallback", async () => {
  const win = {
    isSecureContext: true,
    async showSaveFilePicker(options) {
      assert.match(options.suggestedName, /\.xls$/);
      assert.deepEqual(options.types[0].accept, { "application/vnd.ms-excel": [".xls"] });
      throw new DOMException("User cancelled", "AbortError");
    },
    confirm() { assert.fail("A cancelled picker must not prompt to download another way"); },
  };
  assert.equal(await chooseExcelDestination(win, "中文询价.xls"), null);
});

test("a failed Excel stream aborts the writer without committing the partial file", async () => {
  const actions = [];
  const win = {
    isSecureContext: true,
    async showSaveFilePicker() {
      return { async createWritable() {
        return {
          async write() { actions.push("write"); throw new Error("disk unavailable"); },
          async close() { actions.push("close"); },
          async abort() { actions.push("abort"); },
        };
      } };
    },
  };
  const destination = await chooseExcelDestination(win, "one.xlsx");
  await assert.rejects(saveExcelResponse(destination, attachment()));
  assert.deepEqual(actions, ["write", "abort"]);
});

class FormControl {
  constructor() {
    this.dataset = {};
    this.children = [];
    this.listeners = new Map();
    this.disabled = false;
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  reportValidity() { return true; }
  setCustomValidity() {}
}

function downloadForm({ picker, writeAccess = true, confirm = () => assert.fail("unexpected fallback") }) {
  const form = new FormControl();
  const download = new FormControl();
  const write = writeAccess ? new FormControl() : null;
  const customer = writeAccess ? new FormControl() : null;
  const status = new FormControl();
  const recovery = new FormControl();
  const requests = [];
  const navigations = [];
  const win = {
    isSecureContext: true,
    showSaveFilePicker: picker,
    confirm,
    FormData: class { constructor(source) { assert.equal(source, form); } },
    location: { assign: url => navigations.push(url) },
    async fetch(url, options) {
      requests.push([url, options?.method || "GET"]);
      if (url === WRITE_URL) return Response.json({
        ok: true, download_url: ATTACHMENT_URL, download_filename: "one.xlsx", quotes_url: RECORD_URL,
      });
      if (url === GENERATED_URL || url === ATTACHMENT_URL) return attachment();
      throw new Error("Unexpected request: " + url);
    },
  };
  const doc = new FormControl();
  doc.defaultView = win;
  doc.createElement = () => new FormControl();
  form.ownerDocument = win.document = doc;
  form.action = GENERATED_URL;
  form.dataset = { excelFilename: "中文结果.xlsx", quotesUrl: "/quotes" };
  const controls = new Map([
    ["[data-write-quotes-submit]", write], ["[data-download-only-submit]", download],
    ["input[name='customer_name']", customer], ["[data-submit-wait-message]", status],
    ["[data-quote-recovery]", recovery],
  ]);
  form.querySelector = selector => controls.get(selector) ?? null;
  if (write) {
    write.formAction = WRITE_URL;
    customer.value = "示例客户";
  }
  setupInquiryDownloadForm(form, { validate: () => true, close() {} });
  const submit = (submitter = download) => {
    const event = { submitter, preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() {} };
    const completed = form.listeners.get("submit")(event);
    assert.equal(event.defaultPrevented, true, "must prevent the native form POST synchronously");
    return completed;
  };
  return { submit, download, write, recovery, requests, navigations, win };
}

for (const [label, writeAccess, writesQuote] of [
  ["download only", true, false], ["write and download", true, true], ["read-only confirmation", false, false],
]) {
  test(label + " chooses a file before the first request and writes the returned attachment bytes", async () => {
    let releasePicker;
    const form = downloadForm({
      writeAccess,
      picker(options) {
        assert.equal(options.suggestedName, "中文结果.xlsx");
        return new Promise(resolve => { releasePicker = resolve; });
      },
    });
    const pending = form.submit(writesQuote ? form.write : form.download);
    assert.equal(typeof releasePicker, "function", "picker must open within the submit gesture");
    assert.deepEqual(form.requests, []);
    let written;
    let closed = false;
    releasePicker({ async createWritable() {
      return {
        async write(blob) { written = new Uint8Array(await blob.arrayBuffer()); },
        async close() { closed = true; }, async abort() { assert.fail("unexpected abort"); },
      };
    } });
    await pending;
    assert.deepEqual(written, BOOK);
    assert.equal(closed, true);
    assert.deepEqual(form.requests, writesQuote
      ? [[WRITE_URL, "POST"], [ATTACHMENT_URL, "GET"]]
      : [[GENERATED_URL, "POST"]]);
    assert.deepEqual(form.navigations, writesQuote ? [RECORD_URL] : []);
  });
}

test("cancelling either form action leaves both the server and the user's file untouched", async () => {
  for (const writesQuote of [false, true]) {
    const form = downloadForm({ picker: async () => { throw new DOMException("cancel", "AbortError"); } });
    await form.submit(writesQuote ? form.write : form.download);
    assert.deepEqual(form.requests, []);
    assert.deepEqual(form.navigations, []);
    assert.equal(form.write.disabled, false);
  }
});

test("if the browser cannot open the picker, declining the fallback leaves the quotation unwritten", async () => {
  for (const picker of [undefined, async () => { throw new DOMException("unavailable", "SecurityError"); }]) {
    let prompt;
    const form = downloadForm({ picker, confirm(message) { prompt = message; return false; } });
    await form.submit(form.write);
    assert.match(prompt, /另存为/);
    assert.match(prompt, /写入报价/);
    assert.deepEqual(form.requests, []);
    assert.deepEqual(form.navigations, []);
  }
});

test("a disk failure after the quote is written exposes the original attachment and locks out a second write", async () => {
  const form = downloadForm({ picker: async () => ({
    createWritable: async () => { throw new Error("disk unavailable"); },
  }) });
  await form.submit(form.write);
  assert.equal(form.write.disabled, true);
  const links = form.recovery.children.filter(node => node?.href);
  const retry = links.find(link => link.href === ATTACHMENT_URL && link.dataset.saveQuoteExcel === "one.xlsx");
  assert.ok(retry);
  assert.ok(links.some(link => link.href === RECORD_URL));
  await form.submit(form.write);
  assert.deepEqual(form.requests, [[WRITE_URL, "POST"], [ATTACHMENT_URL, "GET"]]);
  assert.deepEqual(form.navigations, []);
  let saved;
  form.win.showSaveFilePicker = async () => ({ createWritable: async () => ({
    async write(blob) { saved = new Uint8Array(await blob.arrayBuffer()); }, async close() {},
  }) });
  setupExcelSaveLinks(form.win.document);
  await form.win.document.listeners.get("click")({ target: { closest: () => retry }, preventDefault() {} });
  assert.deepEqual(saved, BOOK);
  assert.deepEqual(form.requests, [[WRITE_URL, "POST"], [ATTACHMENT_URL, "GET"], [ATTACHMENT_URL, "GET"]]);
});

test("the fallback exposes the received file as a manual save link, without starting another download", async () => {
  const link = new FormControl();
  const button = new FormControl();
  const dialog = new FormControl();
  let displayed;
  const shown = new Promise(resolve => { displayed = resolve; });
  dialog.querySelector = selector => selector === "a" ? link : button;
  dialog.showModal = displayed;
  dialog.remove = () => {};
  link.click = () => assert.fail("save location must be chosen by the user");
  let received;
  let revoked;
  const win = {
    confirm: () => true,
    document: {
      getElementById: () => ({ content: { firstElementChild: { cloneNode: () => dialog } } }),
      body: { append: element => assert.equal(element, dialog) },
    },
    URL: {
      createObjectURL(blob) { received = blob; return "blob:test/one"; },
      revokeObjectURL(url) { revoked = url; },
    },
  };
  const destination = await chooseExcelDestination(win, "one.xlsx");
  const saving = saveExcelResponse(destination, attachment());
  await shown;
  assert.equal(link.href, "blob:test/one");
  assert.equal(link.download, "one.xlsx");
  assert.deepEqual(new Uint8Array(await received.arrayBuffer()), BOOK);
  assert.equal(revoked, undefined);
  dialog.listeners.get("close")();
  await saving;
  assert.equal(revoked, "blob:test/one");
});
