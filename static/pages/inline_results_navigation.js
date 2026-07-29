export const inlineResultsFragmentUrl = (endpointHref, targetHref, baseHref = targetHref) => {
  const target = new URL(targetHref, baseHref);
  const endpoint = new URL(endpointHref, target);
  endpoint.search = target.search;
  endpoint.hash = "";
  return endpoint.toString();
};

export const inlineResultsHistoryUrl = (href) => {
  const url = new URL(href);
  url.hash = "";
  return `${url.pathname}${url.search}`;
};

export const formGetUrl = (form, baseHref) => {
  const target = new URL(form.action, baseHref);
  target.search = "";
  for (const [name, value] of new FormData(form)) {
    if (typeof value === "string" && value.trim()) target.searchParams.append(name, value.trim());
  }
  target.hash = "";
  return target.toString();
};

export const scrollDataGridToTop = (root = document) => {
  const grid = root instanceof Element ? root.querySelector("[data-resizable-grid]") : null;
  if (!(grid instanceof HTMLElement)) return;
  const scroll = grid.querySelector("[data-grid-scroll]");
  if (scroll instanceof HTMLElement) scroll.scrollTop = 0;
  const navHeight = Number.parseFloat(
    window.getComputedStyle(document.documentElement).getPropertyValue("--revealed-nav-height")
  ) || 0;
  const top = Math.max(0, Math.round(grid.getBoundingClientRect().top + window.scrollY - navHeight - 12));
  window.scrollTo({ left: window.scrollX, top });
};

export const createInlineResultsRequestGate = () => {
  let generation = 0;
  return {
    begin: () => {
      generation += 1;
      return generation;
    },
    isCurrent: (candidate) => candidate === generation,
  };
};
