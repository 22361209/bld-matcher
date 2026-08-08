export const inquiryImageGallery = (value) => {
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(String(value || "[]"));
    return Array.isArray(parsed) ? parsed : [];
  } catch (_error) {
    return [];
  }
};

export const renderInquiryProductImage = (row, galleryValue) => {
  const cell = row.querySelector("[data-inquiry-image-cell]");
  if (!(cell instanceof HTMLElement)) return;
  const gallery = inquiryImageGallery(galleryValue);
  cell.replaceChildren();
  const first = gallery[0];
  if (!first?.url) {
    const empty = document.createElement("span");
    empty.className = "inquiry-image-empty";
    empty.dataset.inquiryImageEmpty = "";
    empty.textContent = "无图";
    cell.append(empty);
    return;
  }
  const currentBld = row.dataset.currentBld || row.dataset.defaultBld || "";
  const link = document.createElement("a");
  link.className = "inquiry-image-link";
  link.href = first.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = `打开 ${currentBld} 产品图片`;
  link.dataset.inquiryProductImage = "";
  const image = document.createElement("img");
  image.className = "inquiry-product-thumb";
  image.src = first.thumb || first.url;
  image.alt = `${currentBld} 产品图片`;
  image.loading = "lazy";
  image.decoding = "async";
  link.append(image);
  cell.append(link);
};
