import { setupProductTable } from "./product_table.js?v=20260717-1";

if (document.body.dataset.page === "tubes.list") {
  setupProductTable(document.querySelector("#tubes-table"), {
    columns: ["code", "type", "spec", "blank-length", "inner-tolerance", "purchase-base", "material", "weight", "tolerance", "consumption", "borrow"],
    storagePrefix: "bld.tubes",
    resultsHash: "tube-results",
  });
}
