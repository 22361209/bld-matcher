export const productCatalogFragmentUrl = (endpointHref, targetHref, baseHref = targetHref) => {
  const target = new URL(targetHref, baseHref);
  const endpoint = new URL(endpointHref, target);
  endpoint.search = target.search;
  endpoint.hash = "";
  return endpoint.toString();
};

export const productCatalogState = (href) => {
  const url = new URL(href);
  return {
    bld: url.searchParams.get("bld") || url.searchParams.get("q") || "",
    oe: url.searchParams.get("oe") || "",
    status: url.searchParams.get("status") || "active",
    page: Math.max(1, Number.parseInt(url.searchParams.get("page") || "1", 10) || 1),
    filters: {
      brand: url.searchParams.getAll("brand"),
      item: url.searchParams.getAll("item"),
      product_status: url.searchParams.getAll("product_status"),
    },
  };
};

export const productCatalogHistoryUrl = (href) => {
  const url = new URL(href);
  url.hash = "products-results";
  return `${url.pathname}${url.search}${url.hash}`;
};

export const productCatalogRequiresFullNavigation = (
  currentHref,
  targetHref,
  shellBrandPreviewActive = false,
  shellBrandPreviewAvailable = false,
) => {
  const current = new URL(currentHref);
  const target = new URL(targetHref, current);
  return shellBrandPreviewAvailable && (
    shellBrandPreviewActive
    || current.searchParams.get("brand_preview") === "1"
    || target.searchParams.get("brand_preview") === "1"
  );
};

export const createProductCatalogRequestGate = () => {
  let generation = 0;
  return {
    begin() {
      generation += 1;
      return generation;
    },
    isCurrent(candidate) {
      return candidate === generation;
    },
  };
};
