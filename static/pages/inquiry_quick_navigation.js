const PASTED_QUERY_SEPARATOR = /[\s,，;；、/]/u;

export const shouldUseQuickInquiryNavigation = ({ query, hasFile = false }) => {
  const text = String(query || "").trim();
  return Boolean(text && !hasFile && !PASTED_QUERY_SEPARATOR.test(text));
};

export const quickInquiryUrl = (currentHref, query) => {
  const url = new URL("/", currentHref);
  url.searchParams.set("quick_oe", String(query || "").trim());
  return url.toString();
};

export const quickInquiryFragmentUrl = (endpointHref, currentHref, query, filter = "") => {
  const url = new URL(endpointHref, currentHref);
  url.searchParams.set("quick_oe", String(query || "").trim());
  if (filter) url.searchParams.set("quick_filter", String(filter));
  return url.toString();
};

export const quickInquiryState = (currentHref) => {
  const url = new URL(currentHref);
  return {
    query: url.searchParams.get("quick_oe")?.trim() || "",
    filter: url.searchParams.get("quick_filter")?.trim() || "",
  };
};

export const createQuickInquiryRequestGate = () => {
  let generation = 0;
  return {
    begin: () => {
      generation += 1;
      return generation;
    },
    invalidate: () => {
      generation += 1;
    },
    isCurrent: (requestId) => requestId === generation,
  };
};
