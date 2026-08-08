import { setupDataGridControls } from "../components/data_grid_controls.js?v=20260729-2";
import { setupQuoteFieldComboboxes } from "../components/quote_comboboxes.js?v=20260728-1";
import { setupInquiryAdjustments } from "./inquiry_result_adjustments.js?v=20260808-1";
import { setupInquiryDownloads } from "./inquiry_result_downloads.js?v=20260808-1";
import { setupInquiryManualMapping } from "./inquiry_result_manual_mapping.js?v=20260808-1";
import { setupInquiryProductPicker } from "./inquiry_result_product_picker.js?v=20260808-1";
import { createInquiryInitializationGate } from "./inquiry_result_rules.js?v=20260808-1";

export {
  clearQuoteCustomerValidity,
  inquiryAttachmentFilename,
} from "./inquiry_result_downloads.js?v=20260808-1";
export { inquiryImageGallery } from "./inquiry_result_images.js?v=20260808-1";
export {
  createInquiryInitializationGate,
  createInquiryRequestGate,
  inquiryBldSelectionState,
  inquiryPriceAdjustment,
  inquiryProductDisplay,
  inquiryTargetAdjustment,
  rankInquiryProducts,
  validateInquiryPrice,
} from "./inquiry_result_rules.js?v=20260808-1";

const initializationGate = createInquiryInitializationGate();

export function setupInquiryResultPage(root = document) {
  if (!initializationGate.claim(root)) return false;

  setupQuoteFieldComboboxes(root);
  setupDataGridControls(root.querySelector(".data-grid[data-grid-key='inquiry-result'] table.data-table"), {
    columns: ["row", "oe", "customer-code", "bld", "image", "price", "status", "score", "reason"],
    storagePrefix: "bld.inquiry-result",
  });

  const adjustments = setupInquiryAdjustments(root);
  setupInquiryProductPicker(root, adjustments);
  setupInquiryDownloads(root, { validateAdjustments: adjustments.validateAll });
  setupInquiryManualMapping(root);
}

if (typeof document !== "undefined") setupInquiryResultPage(document);
