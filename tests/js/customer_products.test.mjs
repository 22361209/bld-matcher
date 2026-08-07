import assert from "node:assert/strict";
import test from "node:test";

import {
  assignCustomerDrawingFile,
  customerDrawingDropError,
  isCustomerDrawingFileName,
} from "../../static/pages/customer_products.js";


test("customer drawing filename accepts supported formats case-insensitively", () => {
  for (const name of ["drawing.pdf", "来图.PNG", "photo.jpg", "photo.JPEG", "render.webp"]) {
    assert.equal(isCustomerDrawingFileName(name), true, name);
  }
});


test("customer drawing filename rejects unsupported or misleading names", () => {
  for (const name of ["drawing.docx", "drawing.pdf.exe", "drawing", "", null]) {
    assert.equal(isCustomerDrawingFileName(name), false, String(name));
  }
});


test("drop assignment stores the selected file", () => {
  const file = { name: "drawing.pdf" };
  const input = { files: [] };
  const droppedFiles = [file];
  assert.equal(assignCustomerDrawingFile(input, file, droppedFiles, undefined), true);
  assert.equal(input.files, droppedFiles);
});


test("multi-file drop is explicitly rejected and clears the existing selection", () => {
  const first = { name: "drawing.pdf" };
  const second = { name: "drawing-2.pdf" };
  const input = {
    files: [{ name: "old.pdf" }],
    set value(nextValue) {
      if (nextValue === "") this.files = [];
    },
  };
  const droppedFiles = [first, second];

  assert.equal(customerDrawingDropError(droppedFiles), "每次只能拖入一份图纸，请重新选择。");
  assert.equal(assignCustomerDrawingFile(input, first, droppedFiles, undefined), false);
  assert.deepEqual(input.files, []);
});


test("drop validation reports unsupported or missing files", () => {
  assert.equal(customerDrawingDropError([{ name: "drawing.docx" }]), "仅支持 PDF、PNG、JPG、WEBP 图纸。");
  assert.equal(customerDrawingDropError([]), "仅支持 PDF、PNG、JPG、WEBP 图纸。");
});


test("drawing version popup rejects multiple dropped files with the shared validator", () => {
  const droppedFiles = [{ name: "bld-v1.pdf" }, { name: "bld-v2.pdf" }];
  const error = customerDrawingDropError(droppedFiles);

  assert.equal(error, "每次只能拖入一份图纸，请重新选择。");
  assert.notEqual(error, "");
});


test("drop assignment falls back to DataTransfer construction", () => {
  const file = { name: "drawing.pdf" };
  const input = { files: [] };
  class FakeDataTransfer {
    constructor() {
      const files = [];
      this.items = { add: (item) => files.push(item) };
      this.files = files;
    }
  }
  assert.equal(assignCustomerDrawingFile(input, file, null, FakeDataTransfer), true);
  assert.equal(input.files[0], file);
});


test("drop assignment fails safely when DataTransfer is unavailable or input rejects assignment", () => {
  const file = { name: "drawing.pdf" };
  assert.equal(assignCustomerDrawingFile({ files: [] }, file, null, undefined), false);
  class FakeDataTransfer {
    constructor() {
      this.items = { add() {} };
      this.files = [file];
    }
  }
  const input = {};
  Object.defineProperty(input, "files", { set() { throw new TypeError("readonly"); } });
  assert.equal(assignCustomerDrawingFile(input, file, null, FakeDataTransfer), false);
});
