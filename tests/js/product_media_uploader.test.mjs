import assert from "node:assert/strict";
import test from "node:test";

import {
  availableProductImageSlots,
  productImageIntakeCopy,
} from "../../static/pages/product_media_uploader.js";

test("multi-image intake fills empty persisted slots before adding more", () => {
  assert.deepEqual(
    availableProductImageSlots(new Set([1, 3]), new Set([2])),
    [4, 5],
  );
});

test("intake copy makes the single-image path primary and exposes remaining capacity", () => {
  assert.deepEqual(productImageIntakeCopy(0), {
    title: "拖入图片或选择文件",
    note: "可一次选择多张；JPG / PNG / WEBP，单张不超过 5 MB",
  });
  assert.match(productImageIntakeCopy(1).title, /添加更多图片/);
  assert.match(productImageIntakeCopy(1).note, /还可添加 4 张/);
  assert.match(productImageIntakeCopy(5).title, /5 张上限/);
});
