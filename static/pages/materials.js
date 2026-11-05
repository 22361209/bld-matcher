import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";

function setupMaterialEditor() {
  const modal = document.querySelector("#material-modal");
  if (!modal) return;
  const form = modal.querySelector("form");
  const title = modal.querySelector("#material-modal-title");
  const note = modal.querySelector(".modal-heading .note");
  const error = form.querySelector("[data-material-error]");
  const save = form.querySelector("[type='submit']");
  const newTitle = title.textContent;
  const newNote = note.textContent;
  let opener = null;

  document.querySelectorAll("[data-open-material-modal], [data-copy-material]").forEach((button) => {
    button.addEventListener("click", () => {
      const copy = button.dataset.copyMaterial ? JSON.parse(button.dataset.copyMaterial) : null;
      opener = button;
      form.reset();
      form.elements.copy_from.value = "";
      for (const [field, value] of Object.entries(copy ?? {})) form.elements[field].value = value ?? "";
      title.textContent = copy ? `从 ${button.textContent.trim()} 复制新建材料明细` : newTitle;
      note.textContent = copy ? "填写新零件编码即可保存。净重可按新件实测值填写。" : newNote;
      error.textContent = "";
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
      form.elements[copy ? "code" : "model"].focus();
    });
  });
  const close = () => {
    if (save.disabled) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    opener?.focus();
  };
  modal.querySelectorAll("[data-close-material-modal]").forEach((button) => button.addEventListener("click", close));
  modal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    if (event.key !== "Tab") return;
    const first = modal.querySelector("button");
    const last = form.querySelector("[data-close-material-modal]");
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  form.addEventListener("submit", async (event) => {
    if (!form.elements.copy_from.value) return;
    event.preventDefault();
    if (save.disabled) return;
    save.disabled = true;
    error.textContent = "";
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form), headers: {Accept: "application/json"},
      });
      const result = await response.json();
      if (response.ok && result.ok) window.location.assign(result.url);
      else error.textContent = result.error || "保存失败，请重试。";
    } catch {
      error.textContent = "未收到保存结果，请刷新列表核对新零件编码。";
    } finally {
      save.disabled = false;
    }
  });
}

if (document.body.dataset.page === "materials.workspace") {
  const materialStatusInput = document.querySelector("#material-status-input");
  document.querySelectorAll("[data-material-status]").forEach((button) => {
    button.addEventListener("click", () => {
      materialStatusInput.value = button.dataset.materialStatus;
    });
  });
  const toolbarPopovers = Array.from(document.querySelectorAll("details.toolbar-popover"));
  toolbarPopovers.forEach((popover) => {
    popover.addEventListener("toggle", () => {
      if (!popover.open) return;
      toolbarPopovers.forEach((other) => {
        if (other !== popover) other.open = false;
      });
    });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("details.toolbar-popover")) return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
  });
  setupMaterialEditor();
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    toolbarPopovers.forEach((popover) => {
      popover.open = false;
    });
  });
  setupDataGridControls(document.querySelector("#materials-table"), {
    columns: ["model", "code", "category", "car", "part", "specification", "pieces", "unit-weight", "net-weight", "scrap-weight", "scrap-percent", "status"],
    storagePrefix: "bld.materials",
    resultsHash: "materials-results",
    resetFilterParams: [
      "mf_model", "mf_code", "mf_category", "mf_car", "mf_part", "mf_spec_text", "mf_pieces",
      "mf_unit_weight", "mf_net_weight", "mf_scrap_weight", "mf_scrap_percent", "mf_active",
    ],
  });
}
