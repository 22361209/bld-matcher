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
  const refresh = () => {
    kind.disabled = target && !target.value;
    for (const input of peerCodes) {
      if (!input.disabled) input.dataset.handedValue = input.value;
      input.disabled = kind.value !== "handed";
      input.value = kind.value === "local" ? "" : input.disabled ? input.dataset.sharedValue : input.dataset.handedValue;
    }
  };
  row.addEventListener("change", refresh);
  refresh();
}
