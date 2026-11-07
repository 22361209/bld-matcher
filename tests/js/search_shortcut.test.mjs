import assert from "node:assert/strict";
import test from "node:test";
import { focusPageSearch } from "../../static/components/search_shortcut.js";

globalThis.getComputedStyle = (element) => ({ visibility: element.visibility || "visible" });

const fixture = () => {
  const root = {
    activeElement: null,
    inputs: [],
    dialogs: [],
    querySelectorAll(selector) {
      return selector.startsWith("input") ? this.inputs : this.dialogs;
    },
  };
  const input = (options = {}) => {
    const field = {
      type: "text",
      value: "已有搜索词",
      closest: (selector) => selector.startsWith("dialog") ? field.dialog || null : field.hidden ? {} : null,
      getClientRects: () => field.rendered === false ? [] : [{}],
      matches: (selector) => selector === ":disabled" ? !!field.disabled : selector.includes("input"),
      focus: () => { if (!field.refuseFocus) root.activeElement = field; },
      select: () => { field.selected = true; },
      ...options,
    };
    root.inputs.push(field);
    return field;
  };
  const event = (options = {}) => ({
    key: "F", shiftKey: true, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    ...options,
  });
  return { root, input, event };
};

test("Shift+F focuses and selects search without changing its value", () => {
  for (const modifiers of [{ key: "F" }, { key: "f" }]) {
    const { root, input, event } = fixture();
    const field = input();
    const key = event(modifiers);
    focusPageSearch(key, root);
    assert.equal(root.activeElement, field);
    assert.equal(field.selected, true);
    assert.equal(field.value, "已有搜索词");
    assert.equal(key.defaultPrevented, true);
  }
});

test("search inputs replaced by partial navigation remain reachable", () => {
  const { root, input, event } = fixture();
  const original = input();
  focusPageSearch(event(), root);
  assert.equal(root.activeElement, original);
  root.inputs = [];
  root.activeElement = null;
  const replacement = input();
  focusPageSearch(event(), root);
  assert.equal(root.activeElement, replacement);
});

test("IME, ordinary keys and other modifier combinations are left alone", () => {
  for (const flags of [
    { isComposing: true }, { keyCode: 229 }, { shiftKey: false },
    { ctrlKey: true, shiftKey: false }, { metaKey: true, shiftKey: false },
    { ctrlKey: true }, { metaKey: true }, { altKey: true }, { key: "g" }, { defaultPrevented: true },
  ]) {
    const { root, input, event } = fixture();
    input();
    focusPageSearch(event(flags), root);
    assert.equal(root.activeElement, null);
  }
});

test("typing uppercase F in a search, form field or rich text editor is not intercepted", () => {
  for (const editor of ["search", "textarea", "select", "contenteditable"]) {
    const { root, input, event } = fixture();
    const field = input();
    const active = editor === "search" ? field : {
      isContentEditable: editor === "contenteditable",
      matches: () => editor !== "contenteditable",
    };
    root.activeElement = active;
    const key = event();
    focusPageSearch(key, root);
    assert.equal(root.activeElement, active);
    assert.equal(key.defaultPrevented, false);
    assert.equal(field.selected, undefined);
  }
});

test("unavailable searches and failed focus leave the key event untouched", () => {
  for (const flags of [
    { hidden: true }, { rendered: false }, { visibility: "hidden" },
    { disabled: true }, { readOnly: true }, { type: "number" }, { refuseFocus: true },
  ]) {
    const { root, input, event } = fixture();
    input(flags);
    const key = event();
    focusPageSearch(key, root);
    assert.equal(key.defaultPrevented, false);
    assert.equal(root.activeElement, null);
  }
});

test("an open modal never sends focus to a background page search", () => {
  const { root, input, event } = fixture();
  input();
  const dialog = {
    closest: () => null,
    getClientRects: () => [{}],
    matches: () => true,
    querySelectorAll: () => [],
  };
  root.dialogs = [dialog];
  const key = event();
  focusPageSearch(key, root);
  assert.equal(key.defaultPrevented, false);
  assert.equal(root.activeElement, null);
  const field = input();
  dialog.querySelectorAll = () => [field];
  focusPageSearch(event(), root);
  assert.equal(root.activeElement, field);
});
