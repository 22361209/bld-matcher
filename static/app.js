document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  if (!name) return;

  input.addEventListener("change", () => {
    name.textContent = input.files && input.files.length ? input.files[0].name : "未选择任何文件";
  });
});
