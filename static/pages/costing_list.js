import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";

const columns = [
  "code", "name", "kind", "calculated", "approved", "scrap", "net",
  "minimum_price", "suggested_price", "status",
];

if (document.body.dataset.page === "costing.list") {
  setupDataGridControls(document.querySelector("#costing-table"), {
    columns,
    storagePrefix: "bld.costing",
    resultsHash: "costing-results",
    resetFilterParams: columns.map((column) => `cf_${column}`),
  });
}
