# ADR 0033: Bilingual Product Catalog

- Status: accepted
- Date: 2026-10-02
- Owners: BLD

## Context

A product needs independent English and Chinese catalog visibility. Chinese vehicle descriptions may describe different domestic names and years, so they cannot be generated, copied from, or kept synchronized with English models. Both catalogs must share the same product identity and existing filters.

## Decision

1. Preserve products.item and products.models. Add item_zh, models_zh, catalog_visible_en and catalog_visible_zh. Migration 042 and new products default both visibility flags to 1. Both flags may be 0 without deleting, disabling, or duplicating the product.
2. Backfill only blank Chinese names using the reviewed exact-name mapping in app/modules/products/catalog_language.py. Leave three ambiguous names unfilled. Never backfill Chinese vehicle descriptions. Existing Chinese names and explicit visibility choices remain intact.
3. The catalog URL carries lang=en|zh and visibility=visible|all|neither. Management scopes retain products hidden from both catalogs. Existing brand/item/status filter values remain canonical; labels and visible product data use the selected language.
4. New/edit forms show English names first, followed by editable Chinese names. Picker suggestions show both while keeping the English value as the stored key. Chinese descriptions and both flags are independently editable.
5. Explicit display exports accept export_language=en|zh independently of the page language and require the corresponding product visibility flag. Both existing export layouts remain available. Empty Chinese fields remain empty; the directory information sheet reports missing counts. Calls omitting export language retain the legacy output shape.
6. Chinese display exports use distinct Chinese field headers and a language marker. Both Excel import paths reject them. The controlled import template accepts optional independent Chinese and visibility columns, with omitted/blank optional cells preserving existing data. The source workbook includes the additional fields for bootstrap recovery.
7. Business package v4 carries the additional columns. Missing legacy fields inherit existing local values, or defaults for new rows, before preview comparison and writes. This is explicit product-data synchronization, not a translation or mirroring of English vehicle descriptions.
8. Preserve public API original fields and routes. Follow existing Web -> Service -> Repository and page protocols; no new framework, background job, or permission bypass.

## Consequences

The data schema changes through the existing migration entry point. Deployment requires coordinated application upgrade before exchanging new optional import columns. The migration is transactional and does not alter English data, product activity, or existing Chinese values. This implementation does not execute the migration against the running Mac or NAS database.

A Chinese display export is intentionally not an English catalog import template. Older application versions cannot round-trip the added data; use the upgraded versions for full fidelity. Existing retired product-only database packages retain their previous schema compatibility constraints.

## Follow-up: Brand Labels and Visibility Controls (2026-10-02)

Brand names use a display-only mapping in `catalog_brands.py` across rows, mobile cards/details, filter labels and both display export layouts. English source values, canonical filter keys, database data and grouping/order remain unchanged. The 49 existing distinct brand tokens include 47 translated labels; DS and GMC retain their original names, as do unknown future values. Established Chinese names are used; additional checked sources include [Stellantis Chinese brand labels](https://www.media.stellantis.com/cn-zh/), [Proton's Chinese releases](https://emas.proton.com/proton-launches-proton-e-mas-5-malaysias-first-affordable-electric-vehicle/) and the [joint Renault/Valeo release naming Dacia](https://www.valeo.com/cn/%E9%9B%B7%E8%AF%BA%E9%9B%86%E5%9B%A2%E3%80%81%E6%B3%95%E9%9B%B7%E5%A5%A5%E5%92%8C%E6%B3%95%E9%9B%B7%E5%A5%A5%E8%A5%BF%E9%97%A8%E5%AD%90%E6%96%B0%E8%83%BD%E6%BA%90%E6%B1%BD%E8%BD%A6%E5%90%88%E8%B5%84/).

`POST /products/{product_id}/catalog-visibility` requires existing view/edit product permissions and CSRF. Its service and repository update only the requested visibility flag and timestamp, with atomic audit logging. Both the expected timestamp and prior flag must match. Visibility writes use microsecond timestamps within the existing string revision contract, preventing same-second stale toggles. No schema or migration is added.

Desktop and mobile controls share state. Each product permits one request at a time and disables both controls until it completes. Success updates the saved revision; failure restores saved values and explains how to retry. Current rows remain available for a second flag adjustment until the next search/refresh, after which normal visibility filtering applies. Export filtering applies immediately. Read-only users receive disabled controls; no existing flags are automatically changed.

Tests cover both languages/layouts, multiline and unknown brands, zero-count selected filter labels, single-field writes, audit rollback, permissions, CSRF, stale requests, management/export consistency and client network/permission failures plus rapid clicks.

## Verification

Cover historical/default migration, all four visibility combinations, independent Chinese descriptions, blank values, form edits, canonical filters, both export formats, Chinese export import rejection, source workbook recovery, optional/legacy template imports and v4/v1 business package compatibility. Run the complete project verification and desktop/mobile browser checks with isolated data.
