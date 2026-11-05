import { setupDataGridControls } from "../components/data_grid_controls.js?v=20261105-1";

if (document.body.dataset.page === "costing.list") {
  const table = document.querySelector("#costing-table");
  const columns = [...table.querySelectorAll("thead th[data-col]")].map((heading) => heading.dataset.col);
  setupDataGridControls(table, {
    columns,
    storagePrefix: "bld.costing",
    resultsHash: "costing-results",
    resetFilterParams: columns.filter((column) => column !== "actions").map((column) => `cf_${column}`),
  });
}
