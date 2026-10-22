export const syncCatalogControls = (searchForm, exportForm, state) => {
  const language = searchForm?.querySelector("[data-catalog-language]");
  const visibility = searchForm?.querySelector("[data-catalog-visibility]");
  if (language) language.value = state.language;
  if (visibility) visibility.value = state.visibility;
  if (!exportForm) return;
  const pageLanguage = exportForm.querySelector("input[name='lang']");
  const exportLanguage = exportForm.querySelector("select[name='export_language']");
  const scope = exportForm.querySelector("input[name='visibility']");
  if (exportLanguage && pageLanguage?.value !== state.language) exportLanguage.value = state.language;
  if (pageLanguage) pageLanguage.value = state.language;
  if (scope) scope.value = state.visibility;
};

export const setupCatalogControlChanges = (form) => {
  form?.querySelectorAll("[data-catalog-language], [data-catalog-visibility]").forEach((select) => {
    select.addEventListener("change", () => form.requestSubmit());
  });
};
