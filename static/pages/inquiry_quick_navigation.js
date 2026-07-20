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
