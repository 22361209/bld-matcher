const batch = document.querySelector("[data-costing-pair-batch]");
if (batch) {
  const boxes = [...batch.querySelectorAll('[name="candidate"]')];
  const all = batch.querySelector("[data-pair-select-all]");
  const submit = batch.querySelector("[data-mirror-submit]");
  const refresh = () => {
    const selected = boxes.filter((box) => box.checked);
    all.disabled = !boxes.length;
    all.checked = Boolean(boxes.length && selected.length === boxes.length);
    all.indeterminate = selected.length > 0 && selected.length < boxes.length;
    submit.disabled = !selected.length;
    submit.textContent = selected.length ? `建立所选对侧（${selected.length}）` : "建立所选对侧";
    const examples = selected.slice(0, 4).map((box) => box.closest("tr").cells[2].textContent.trim()).join("、");
    submit.dataset.confirm = `建立 ${examples}${selected.length > 4 ? " 等" : ""} 共 ${selected.length} 个对侧型号，并与原成本单配对？`;
  };
  batch.addEventListener("change", (event) => {
    if (event.target === all) boxes.forEach((box) => { box.checked = all.checked; });
    refresh();
  });
  refresh();
}

for (const row of document.querySelectorAll("[data-pair-review-row]")) {
  const kind = row.querySelector("[data-pair-kind]");
  const target = row.querySelector("[data-pair-target]");
  const peerCodes = row.querySelectorAll("[data-pair-peer-code]");
  if (target) {
    // Materialize a complete dropdown only when it is opened. A 500-line pair
    // otherwise renders a quarter of a million options on every page visit.
    const loadOptions = () => {
      if (target.dataset.loaded) return;
      const selected = target.value;
      const options = target.form.querySelector("[data-pair-options]").content.cloneNode(true);
      target.replaceChildren(options);
      target.value = selected;
      target.dataset.loaded = "true";
    };
    target.addEventListener("focus", loadOptions);
    target.addEventListener("pointerdown", loadOptions);
  }
  const refresh = (event) => {
    if (target) {
      if (event?.target === kind && kind.value === "local") target.value = "";
      kind.disabled = !target.value;
      if (kind.disabled) kind.value = "local";
      else if (kind.value === "local") kind.value = "shared";
      row.querySelector("[data-selected-peer-codes]").textContent = target.selectedOptions[0]?.dataset.peerCodes || "—";
    }
    for (const input of peerCodes) {
      if (!input.disabled) input.dataset.handedValue = input.value;
      input.disabled = kind.value !== "handed";
      input.value = kind.value === "local" ? "" : input.disabled ? input.dataset.sharedValue : input.dataset.handedValue;
    }
  };
  row.addEventListener("change", refresh);
  refresh();
}

const review = document.querySelector("[data-pair-review]");
if (review) {
  const saveUrl = review.getAttribute("action");
  const saveButton = review.querySelector('button[type="submit"]');
  const error = review.querySelector("[data-pair-error]");
  let dirty = false;
  review.addEventListener("change", () => { dirty = true; });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  // Run after the document-level confirmation. If the dialog is open, its
  // confirmed resubmission reaches this listener a second time.
  window.addEventListener("submit", async (event) => {
    if (event.target !== review || event.defaultPrevented) return;
    event.preventDefault();
    if (saveButton.disabled) return;
    const data = new URLSearchParams(new FormData(review));
    const controls = [...review.querySelectorAll("input:not(:disabled), select:not(:disabled), button:not(:disabled)")];
    controls.forEach((control) => { control.disabled = true; });
    error.hidden = true;
    try {
      const response = await fetch(saveUrl, {method: "POST", body: data, headers: {Accept: "application/json"}});
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "未能保存，请重试。");
      dirty = false;
      window.location.assign(saveUrl);
    } catch (failure) {
      error.textContent = failure instanceof Error ? failure.message : "未能保存，请重试。";
      error.hidden = false;
      error.focus();
      controls.forEach((control) => { control.disabled = false; });
    }
  });
}
