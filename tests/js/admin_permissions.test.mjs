import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";


const SCRIPT_SOURCE = readFileSync(
  new URL("../../static/pages/users.js", import.meta.url),
  "utf8"
);


class FakeElement {
  constructor({ dataset = {}, textContent = "", top = 0, height = 0 } = {}) {
    this.dataset = { ...dataset };
    this.textContent = textContent;
    this.disabled = false;
    this.hidden = false;
    this.scrollTop = 0;
    this.listeners = new Map();
    this.attributes = new Map();
    this.queries = new Map();
    this.queryLists = new Map();
    this.rect = { top, height };
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, { target = this } = {}) {
    const event = {
      target,
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
    };
    (this.listeners.get(type) || []).forEach((listener) => listener(event));
    return event;
  }

  getBoundingClientRect() {
    return { ...this.rect };
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelector(selector) {
    const result = this.queries.get(selector);
    return typeof result === "function" ? result() : result ?? null;
  }

  querySelectorAll(selector) {
    const result = this.queryLists.get(selector);
    return typeof result === "function" ? result() : result ?? [];
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }
}


class HTMLElement extends FakeElement {}


class HTMLInputElement extends HTMLElement {
  constructor({ type = "radio", name = "", value = "", checked = false } = {}) {
    super();
    this.type = type;
    this.name = name;
    this.value = value;
    this._checked = checked;
    this.group = [];
  }

  get checked() {
    return this._checked;
  }

  set checked(value) {
    this._checked = Boolean(value);
    if (this._checked && this.type === "radio") {
      this.group.forEach((input) => {
        if (input !== this) input._checked = false;
      });
    }
  }
}


class HTMLButtonElement extends HTMLElement {}


class HTMLAnchorElement extends HTMLElement {
  constructor({ hash, permissionCategory }) {
    super({ dataset: { permissionCategory } });
    this.hash = hash;
  }
}


const runUsersScript = ({ page, elementsById = new Map(), hash = "" }) => {
  const location = { hash };
  let animationFrame = 0;
  const window = {
    location,
    history: {
      replaceState(_state, _unused, nextHash) {
        location.hash = nextHash;
      },
    },
    cancelAnimationFrame() {},
    requestAnimationFrame(callback) {
      animationFrame += 1;
      callback();
      return animationFrame;
    },
  };
  const document = {
    getElementById(id) {
      return elementsById.get(id) ?? null;
    },
    querySelector(selector) {
      return selector === 'body[data-page="admin.users"]' ? page : null;
    },
  };
  const context = vm.createContext({
    console,
    document,
    window,
    HTMLElement,
    HTMLInputElement,
    HTMLButtonElement,
    HTMLAnchorElement,
  });
  vm.runInContext(SCRIPT_SOURCE, context, { filename: "static/pages/users.js" });
  return { location, window };
};


const buildPermissionRow = (permission, { assignable = true } = {}) => {
  const row = new HTMLElement({ dataset: { permission } });
  const finalResult = new HTMLElement();
  const roleResult = assignable ? new HTMLElement() : null;
  const radios = assignable
    ? ["inherit", "allow", "deny"].map((value) => new HTMLInputElement({
      type: "radio",
      name: `permission_${permission}`,
      value,
      checked: value === "inherit",
    }))
    : [];
  radios.forEach((radio) => {
    radio.group = radios;
  });

  row.queryLists.set('input[type="radio"]', radios);
  row.queries.set(
    'input[type="radio"]:checked',
    () => radios.find((radio) => radio.checked) ?? null
  );
  row.queries.set(
    'input[type="radio"][value="inherit"]',
    () => radios.find((radio) => radio.value === "inherit") ?? null
  );
  row.queries.set("[data-role-result]", roleResult);
  row.queries.set("[data-permission-result]", finalResult);
  return { row, radios, roleResult, finalResult };
};


const buildAccountHarness = () => {
  const permissions = Array.from({ length: 18 }, (_unused, index) => `permission_${index + 1}`);
  const rows = permissions.map((permission, index) => (
    buildPermissionRow(permission, { assignable: index !== permissions.length - 1 })
  ));
  const ordinaryRole = new HTMLElement({
    dataset: { permissions: `${permissions[0]} ${permissions[1]}`, systemRole: "0" },
    textContent: "销售",
  });
  const adminRole = new HTMLElement({
    dataset: { permissions: "", systemRole: "1" },
    textContent: "管理员",
  });
  const roleSelect = new HTMLElement();
  roleSelect.selectedOptions = [ordinaryRole];

  const roleSummary = new HTMLElement();
  const overrideCount = new HTMLElement();
  const allowCount = new HTMLElement();
  const denyCount = new HTMLElement();
  const summary = new HTMLElement();
  summary.queries.set("[data-role-summary]", roleSummary);
  summary.queries.set("[data-override-count]", overrideCount);
  summary.queries.set("[data-allow-count]", allowCount);
  summary.queries.set("[data-deny-count]", denyCount);

  const resetButton = new HTMLButtonElement();
  const waitMessage = new HTMLElement();
  const accountForm = new HTMLElement();
  accountForm.queries.set("[data-role-select]", roleSelect);
  accountForm.queries.set("[data-permission-summary]", summary);
  accountForm.queries.set("[data-reset-permission-overrides]", resetButton);
  accountForm.queries.set("[data-submit-wait-message]", waitMessage);
  accountForm.queryLists.set("[data-permission-row]", rows.map(({ row }) => row));

  const page = new HTMLElement();
  page.queries.set("[data-user-access-form]", accountForm);
  page.queries.set("[data-role-access-form]", null);
  page.queryLists.set(".access-permission-layout", []);
  runUsersScript({ page });

  return {
    accountForm,
    adminRole,
    allowCount,
    denyCount,
    ordinaryRole,
    overrideCount,
    permissions,
    resetButton,
    roleSelect,
    roleSummary,
    rows,
    waitMessage,
  };
};


test("account permission preview handles ordinary and administrator roles plus override reset", () => {
  const harness = buildAccountHarness();

  assert.equal(harness.roleSummary.textContent, "销售");
  assert.equal(harness.overrideCount.textContent, "0");
  assert.equal(harness.allowCount.textContent, "2");
  assert.equal(harness.denyCount.textContent, "16");

  const deniedBasePermission = harness.rows[0].radios.find((radio) => radio.value === "deny");
  const allowedExtraPermission = harness.rows[2].radios.find((radio) => radio.value === "allow");
  assert.ok(deniedBasePermission);
  assert.ok(allowedExtraPermission);
  deniedBasePermission.checked = true;
  harness.accountForm.dispatch("change", { target: deniedBasePermission });
  allowedExtraPermission.checked = true;
  harness.accountForm.dispatch("change", { target: allowedExtraPermission });

  assert.equal(harness.overrideCount.textContent, "2");
  assert.equal(harness.allowCount.textContent, "2");
  assert.equal(harness.denyCount.textContent, "16");
  assert.equal(harness.rows[0].finalResult.dataset.state, "deny");
  assert.equal(harness.rows[2].finalResult.dataset.state, "allow");
  assert.equal(harness.waitMessage.textContent, "权限调整尚未保存。");

  harness.roleSelect.selectedOptions = [harness.adminRole];
  harness.roleSelect.dispatch("change");

  assert.equal(harness.roleSummary.textContent, "管理员");
  assert.equal(harness.allowCount.textContent, "18");
  assert.equal(harness.denyCount.textContent, "0");
  assert.equal(harness.resetButton.disabled, true);
  assert.ok(harness.rows.slice(0, -1).every(({ radios }) => radios.every((radio) => radio.disabled)));
  assert.ok(harness.rows.every(({ finalResult }) => finalResult.dataset.state === "allow"));

  harness.roleSelect.selectedOptions = [harness.ordinaryRole];
  harness.roleSelect.dispatch("change");

  assert.equal(harness.roleSummary.textContent, "销售");
  assert.equal(harness.overrideCount.textContent, "2");
  assert.equal(harness.allowCount.textContent, "2");
  assert.equal(harness.denyCount.textContent, "16");
  assert.equal(harness.resetButton.disabled, false);
  assert.ok(harness.rows.slice(0, -1).every(({ radios }) => radios.every((radio) => !radio.disabled)));

  harness.resetButton.dispatch("click");

  assert.equal(harness.overrideCount.textContent, "0");
  assert.equal(harness.allowCount.textContent, "2");
  assert.equal(harness.denyCount.textContent, "16");
  assert.ok(harness.rows.slice(0, -1).every(({ radios }) => (
    radios.find((radio) => radio.value === "inherit")?.checked
  )));
  assert.equal(harness.waitMessage.textContent, "已全部切换为跟随角色，尚未保存。");
});


test("role permission summary counts checkbox changes", () => {
  const checkboxes = [true, false, true].map((checked) => new HTMLInputElement({
    type: "checkbox",
    name: "permissions",
    checked,
  }));
  const count = new HTMLElement();
  const roleForm = new HTMLElement();
  roleForm.queryLists.set('input[name="permissions"]', checkboxes);
  roleForm.queries.set("[data-role-permission-count]", count);

  const page = new HTMLElement();
  page.queries.set("[data-user-access-form]", null);
  page.queries.set("[data-role-access-form]", roleForm);
  page.queryLists.set(".access-permission-layout", []);
  runUsersScript({ page });

  assert.equal(count.textContent, "2");
  checkboxes[1].checked = true;
  roleForm.dispatch("change", { target: checkboxes[1] });
  assert.equal(count.textContent, "3");
  checkboxes[0].checked = false;
  roleForm.dispatch("change", { target: checkboxes[0] });
  assert.equal(count.textContent, "2");
});


test("permission category clicks and matrix scrolling keep aria-current location synchronized", () => {
  const allLink = new HTMLAnchorElement({
    hash: "#account-permission-matrix",
    permissionCategory: "all",
  });
  const firstGroupLink = new HTMLAnchorElement({
    hash: "#account-permission-group-1",
    permissionCategory: "group-1",
  });
  const secondGroupLink = new HTMLAnchorElement({
    hash: "#account-permission-group-2",
    permissionCategory: "group-2",
  });
  allLink.setAttribute("aria-current", "location");
  const links = [allLink, firstGroupLink, secondGroupLink];

  const matrix = new HTMLElement();
  const firstGroup = new HTMLElement({ top: 250 });
  const secondGroup = new HTMLElement({ top: 450 });
  const header = new HTMLElement({ height: 20 });
  const scroller = new HTMLElement({ top: 100 });
  scroller.queries.set("thead", header);
  const layout = new HTMLElement();
  layout.queryLists.set("[data-permission-category]", links);
  layout.queries.set("[data-permission-matrix-scroll]", scroller);

  const page = new HTMLElement();
  page.queries.set("[data-user-access-form]", null);
  page.queries.set("[data-role-access-form]", null);
  page.queryLists.set(".access-permission-layout", [layout]);
  const elementsById = new Map([
    ["account-permission-matrix", matrix],
    ["account-permission-group-1", firstGroup],
    ["account-permission-group-2", secondGroup],
  ]);
  const { location } = runUsersScript({ page, elementsById });

  const click = firstGroupLink.dispatch("click");
  assert.equal(click.defaultPrevented, true);
  assert.equal(scroller.scrollTop, 130);
  assert.equal(location.hash, "#account-permission-group-1");
  assert.equal(firstGroupLink.getAttribute("aria-current"), "location");
  assert.equal(allLink.getAttribute("aria-current"), null);

  scroller.dispatch("scroll");
  assert.equal(firstGroupLink.getAttribute("aria-current"), "location");
  secondGroup.rect.top = 110;
  scroller.dispatch("scroll");
  assert.equal(secondGroupLink.getAttribute("aria-current"), "location");
  assert.equal(firstGroupLink.getAttribute("aria-current"), null);

  allLink.dispatch("click");
  assert.equal(scroller.scrollTop, 0);
  assert.equal(location.hash, "#account-permission-matrix");
  assert.equal(allLink.getAttribute("aria-current"), "location");
  assert.equal(secondGroupLink.getAttribute("aria-current"), null);
});
