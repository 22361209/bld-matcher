/* Import preview interactions: bulk conflict actions and per-dataset search. */

document.addEventListener("DOMContentLoaded", () => {
  // Bulk "keep current" / "use package" buttons within each dataset card.
  document.querySelectorAll("[data-bulk-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const datasetCard = button.closest(".import-preview-dataset");
      if (!datasetCard) return;
      const checked = button.dataset.bulkAction === "use-package";
      datasetCard.querySelectorAll('input[type="checkbox"][data-dataset]').forEach((checkbox) => {
        checkbox.checked = checked;
      });
    });
  });

  // Per-dataset real-time search on conflict and new/updated rows.
  document.querySelectorAll(".import-preview-search").forEach((input) => {
    input.addEventListener("input", () => {
      const datasetCard = input.closest(".import-preview-dataset");
      if (!datasetCard) return;
      const query = input.value.trim().toLowerCase();
      datasetCard.querySelectorAll("tr[data-search-text]").forEach((row) => {
        const text = (row.dataset.searchText || "").toLowerCase();
        row.hidden = query !== "" && !text.includes(query);
      });
    });
  });
});
