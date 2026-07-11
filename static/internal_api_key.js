const generatedButton = document.querySelector("[data-copy-api-key]");
const generatedKey = document.querySelector("#generated-api-key");

if (generatedButton instanceof HTMLButtonElement && generatedKey instanceof HTMLElement) {
  generatedButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(generatedKey.textContent.trim());
      generatedButton.textContent = "已复制";
    } catch (_error) {
      generatedButton.textContent = "请手动复制";
    }
  });
}
