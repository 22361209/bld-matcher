document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  const oeInput = picker ? picker.querySelector(".file-picker-oe-input") : null;
  if (!name && !oeInput) return;

  input.addEventListener("change", () => {
    const fileName = input.files && input.files.length ? input.files[0].name : "";
    if (name) {
      name.textContent = fileName || "未选择任何文件";
    }
    if (oeInput && fileName) {
      oeInput.value = fileName;
      oeInput.readOnly = true;
    } else if (oeInput) {
      oeInput.readOnly = false;
    }
  });
});
