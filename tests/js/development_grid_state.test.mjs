import test from "node:test";
import assert from "node:assert/strict";
import {
  addRow, commitSave, createGridState, dirtyRows, getRow, prepareSave,
  restoreDraft, setCell, treeRows,
} from "../../static/pages/development_grid_state.js";

function detail({version = 1, name = "Original", status = "not_started"} = {}) {
  return {
    project: {id: "project", version, owner_username: "owner", planned_date: "2026-11-01"},
    branches: [{id: "left", code: "K8461LA", owner_username: "owner"},
      {id: "right", code: "K8461RA", owner_username: "owner"}],
    components: [
      {id: "left-item", branch_id: "left", task_id: "shared-task", name, code: "L", quantity: 1},
      {id: "right-item", branch_id: "right", task_id: "shared-task", name: "Right original", code: "R", quantity: 1},
    ],
    tasks: [{id: "shared-task", name: "Shared task", status, planned_date: "2026-11-01",
      supplier_id: null, drawing_required: 1, drawing_status: "not_started"}],
    suppliers: [],
  };
}

function acknowledgeNew(snapshot, {version = 2} = {}) {
  const result = {detail: detail({version}), client_ids: {}};
  for (const row of snapshot.rows.filter((item) => item.client_id)) {
    result.client_ids[row.client_id] = `saved-${row.client_id}`;
  }
  for (const row of snapshot.rows.filter((item) => item.client_id)) {
    const id = result.client_ids[row.client_id];
    const parent = row.parent_client_id ? result.client_ids[row.parent_client_id] : row.parent_id;
    const {task = {}, client_id: ignoredClient, parent_client_id: ignoredParent, ...values} = row;
    result.detail.components.push({...values, id, branch_id: snapshot.branch_id, parent_id: parent,
      task_id: `task-${id}`});
    result.detail.tasks.push({...task, id: `task-${id}`});
  }
  return result;
}

test("preparing a request retains draft values and dirty state for 400, 403, 409, and network failure", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "name", "Unsaved input");
  const before = structuredClone(state);
  const request = prepareSave(state, "left");
  assert.equal(request.rows[0].name, "Unsaved input");
  assert.deepEqual(state, before);
  assert.equal(dirtyRows(state, "left").length, 1);
  assert.equal(state.version, 1);
});

test("inflight request contains a frozen copy of submitted values", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "name", "Submitted");
  const request = prepareSave(state, "left");
  setCell(state, "left-item", "name", "Typed afterwards");
  assert.equal(request.rows[0].name, "Submitted");
  assert.equal(request.before.find((row) => row.key === "left-item").name, "Submitted");
});

test("ordinary acknowledged edits clear dirty state and advance optimistic version", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "name", "Submitted");
  const request = prepareSave(state, "left");
  commitSave(state, request, {detail: detail({version: 2, name: "Submitted"}), client_ids: {}});
  assert.equal(state.version, 2);
  assert.equal(getRow(state, "left-item").name, "Submitted");
  assert.equal(dirtyRows(state).length, 0);
});

test("acknowledgement preserves a later edit and leaves it dirty against the accepted server value", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "name", "Submitted");
  const request = prepareSave(state, "left");
  setCell(state, "left-item", "name", "Typed afterwards");
  commitSave(state, request, {detail: detail({version: 2, name: "Submitted"}), client_ids: {}});
  assert.equal(getRow(state, "left-item").name, "Typed afterwards");
  assert.equal(dirtyRows(state, "left").length, 1);
  assert.equal(prepareSave(state, "left").rows[0].name, "Typed afterwards");
});

test("reverting a cell to its original value during a request must survive acknowledgement", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "name", "Submitted");
  const request = prepareSave(state, "left");
  setCell(state, "left-item", "name", "Original");
  commitSave(state, request, {detail: detail({version: 2, name: "Submitted"}), client_ids: {}});
  assert.equal(getRow(state, "left-item").name, "Original");
  assert.equal(dirtyRows(state, "left").length, 1);
  assert.equal(prepareSave(state, "left").rows[0].name, "Original");
});

test("reverting a shared task field during a request survives on both branches", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "task.status", "in_progress");
  const request = prepareSave(state, "left");
  setCell(state, "right-item", "task.status", "not_started");
  commitSave(state, request, {detail: detail({version: 2, status: "in_progress"}), client_ids: {}});
  assert.equal(getRow(state, "left-item").task.status, "not_started");
  assert.equal(getRow(state, "right-item").task.status, "not_started");
  assert.equal(dirtyRows(state).length, 2);
});

test("shared-task acknowledgement clears its copies while preserving another branch's local component edit", () => {
  const state = createGridState(detail());
  setCell(state, "left-item", "task.status", "in_progress");
  setCell(state, "right-item", "name", "Right unsaved component name");
  const request = prepareSave(state, "left");
  commitSave(state, request, {detail: detail({version: 2, status: "in_progress"}), client_ids: {}});
  assert.equal(dirtyRows(state, "left").length, 0);
  assert.equal(dirtyRows(state, "right").length, 1);
  assert.equal(getRow(state, "right-item").name, "Right unsaved component name");
  assert.equal(getRow(state, "right-item").task.status, "in_progress");
  assert.equal(prepareSave(state, "right").rows[0].task, undefined);
});

test("new child added while its draft parent is saving is remapped and remains visible", () => {
  const state = createGridState(detail());
  addRow(state, "left", {name: "Parent", clientId: "draft-parent"});
  const request = prepareSave(state, "left");
  addRow(state, "left", {name: "New child", clientId: "draft-child", parentKey: "draft-parent"});
  commitSave(state, request, acknowledgeNew(request));
  const child = getRow(state, "draft-child");
  assert.equal(child.parent_key, "saved-draft-parent");
  assert.equal(treeRows(state, "left").find((entry) => entry.row.key === "draft-child")?.depth, 1);
  const next = prepareSave(state, "left");
  assert.equal(next.rows.find((row) => row.client_id === "draft-child").parent_id, "saved-draft-parent");
  assert.equal(next.rows.find((row) => row.client_id === "draft-child").parent_client_id, undefined);
});

test("a newly saved row receives its permanent id without losing a later edit", () => {
  const state = createGridState(detail());
  addRow(state, "left", {name: "Submitted new row", clientId: "draft-new"});
  const request = prepareSave(state, "left");
  setCell(state, "draft-new", "name", "Later new-row edit");
  commitSave(state, request, acknowledgeNew(request));
  assert.equal(getRow(state, "draft-new"), undefined);
  assert.equal(getRow(state, "saved-draft-new").name, "Later new-row edit");
  const next = prepareSave(state, "left");
  assert.equal(next.rows[0].id, "saved-draft-new");
  assert.equal(next.rows[0].client_id, undefined);
  assert.equal(next.rows[0].name, "Later new-row edit");
});

test("a component's identity and audit fields cannot be changed through cell edits", () => {
  const state = createGridState(detail());
  const before = structuredClone(state);
  for (const field of ["id", "branch_id", "task_id", "created_by", "task.id", "task.created_by", "task.__proto__"]) {
    setCell(state, "left-item", field, "forged");
  }
  assert.deepEqual(state, before);
});

test("editing a borrowing code clears its old source id until an explicit new selection", () => {
  const initial = detail();
  initial.components[0].borrowed_source_id = "old-source";
  initial.components[0].borrowed_code = "OLD";
  const state = createGridState(initial);
  setCell(state, "left-item", "borrowed_code", "NEW");
  assert.equal(getRow(state, "left-item").borrowed_source_id, null);
});

test("restored stale-version drafts retain user values and explicitly mark a conflict", () => {
  const stored = createGridState(detail());
  setCell(stored, "left-item", "name", "Local persisted draft");
  const restored = restoreDraft(detail({version: 3, name: "Changed remotely"}), structuredClone(stored));
  assert.equal(restored.conflict, true);
  assert.equal(getRow(restored, "left-item").name, "Local persisted draft");
  assert.equal(restored.version, 1);
});

test("drafts from a different project cannot replace the current project's rows", () => {
  const stored = createGridState(detail());
  stored.project.id = "another-project";
  setCell(stored, "left-item", "name", "Foreign draft");
  const restored = restoreDraft(detail(), stored);
  assert.equal(restored.project.id, "project");
  assert.equal(getRow(restored, "left-item").name, "Original");
});

test("server UUID order cannot reorder siblings with explicit sort_order", () => {
  const initial = detail();
  initial.components = [
    {id: "a-second", branch_id: "left", task_id: "shared-task", name: "Second", sort_order: 2},
    {id: "b-child-second", branch_id: "left", task_id: "shared-task", name: "Child second", parent_id: "z-first", sort_order: 4},
    {id: "y-child-first", branch_id: "left", task_id: "shared-task", name: "Child first", parent_id: "z-first", sort_order: 3},
    {id: "z-first", branch_id: "left", task_id: "shared-task", name: "First", sort_order: 1},
  ];
  const state = createGridState(initial);
  assert.deepEqual(treeRows(state, "left").map(({row}) => row.name),
    ["First", "Child first", "Child second", "Second"]);
});

test("inserting a sibling keeps its position after the save response arrives in UUID order", () => {
  const initial = detail();
  initial.components.push({id: "last-existing", branch_id: "left", task_id: "shared-task",
    name: "Last existing", sort_order: 1});
  const state = createGridState(initial);
  addRow(state, "left", {afterKey: "left-item", clientId: "inserted", name: "Inserted sibling"});
  const request = prepareSave(state, "left");
  const result = {detail: structuredClone(initial), client_ids: {inserted: "z-saved-inserted"}};
  result.detail.project.version = 2;
  for (const sent of request.rows) {
    if (sent.id) Object.assign(result.detail.components.find((row) => row.id === sent.id), sent);
    else {
      const {task, ...values} = sent;
      result.detail.components.push({...values, id: "z-saved-inserted", branch_id: "left", task_id: "new-task"});
      result.detail.tasks.push({...task, id: "new-task"});
    }
  }
  result.detail.components.sort((a, b) => a.id.localeCompare(b.id));
  commitSave(state, request, result);
  assert.deepEqual(treeRows(state, "left").map(({row}) => row.name),
    ["Original", "Inserted sibling", "Last existing"]);
  assert.equal(dirtyRows(state).length, 0);
});
