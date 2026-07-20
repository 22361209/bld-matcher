const generatedButton = document.querySelector("[data-copy-api-key]");
const generatedKey = document.querySelector("#generated-api-key");
const agentGuideButton = document.querySelector("[data-copy-agent-guide]");
const agentGuide = document.querySelector("#api-agent-guide");

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

if (agentGuideButton instanceof HTMLButtonElement && agentGuide instanceof HTMLElement) {
  agentGuideButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(agentGuide.textContent.trim());
      agentGuideButton.textContent = "已复制";
    } catch (_error) {
      agentGuideButton.textContent = "请手动复制";
    }
  });
}
