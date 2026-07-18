import test from "node:test";
import assert from "node:assert/strict";

import {
  bindResizeSession,
  clampColumnWidth,
  normalizeStoredWidths,
} from "../../static/components/data_grid.js";

test("column widths stay inside the supported range", () => {
  assert.equal(clampColumnWidth(12), 56);
  assert.equal(clampColumnWidth(123.6), 124);
  assert.equal(clampColumnWidth(900), 640);
  assert.equal(clampColumnWidth("not-a-number"), 56);
});

test("stored widths only restore known, valid columns", () => {
  assert.deepEqual(
    normalizeStoredWidths(
      { code: 120, description: 240.4, actions: 12, unknown: 180 },
      ["code", "description", "actions"]
    ),
    { code: 120, description: 240 }
  );
  assert.deepEqual(normalizeStoredWidths(null, ["code"]), {});
});

test("resize sessions clean up once for every supported interruption", () => {
  for (const terminalEvent of ["pointerup", "pointercancel", "lostpointercapture", "blur"]) {
    const handle = new EventTarget();
    const windowTarget = new EventTarget();
    let moveCount = 0;
    let finishCount = 0;
    bindResizeSession({
      handle,
      windowTarget,
      onMove: () => { moveCount += 1; },
      onFinish: () => { finishCount += 1; },
    });

    handle.dispatchEvent(new Event("pointermove"));
    const target = terminalEvent === "blur" ? windowTarget : handle;
    target.dispatchEvent(new Event(terminalEvent));
    handle.dispatchEvent(new Event("pointercancel"));
    windowTarget.dispatchEvent(new Event("blur"));
    handle.dispatchEvent(new Event("pointermove"));

    assert.equal(moveCount, 1, `${terminalEvent} removes the move listener`);
    assert.equal(finishCount, 1, `${terminalEvent} finishes exactly once`);
  }
});
