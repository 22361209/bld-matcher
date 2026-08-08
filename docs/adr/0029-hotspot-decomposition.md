# ADR 0029: Hotspot Decomposition

- Status: accepted
- Date: 2026-08-08
- Owners: BLD

## Context

The modular-monolith migration removed route/database coupling and already split inquiry Excel, contract rendering, and material persistence. Continued delivery has since concentrated new behavior in a smaller set of files: the web integration test suite, inquiry-result page script, shared/page CSS, product repository, and business-sync infrastructure. Several CSS files are also within a few lines of their existing hard limits.

Large files are not automatically defects. Historical migrations remain an ordered compatibility ledger, and generated contracts remain committed snapshots. The current problem is files that combine independent responsibilities, change frequently, and broaden the regression surface.

## Decision

1. Decompose the hotspots in independently verifiable task packages: web integration tests, inquiry-result JavaScript, shared/page CSS and templates, product repository infrastructure, and business-sync infrastructure.
2. Preserve URLs, endpoint names, form fields, public Python import paths, JavaScript page entry paths, OpenAPI, database schema, migration identifiers and order, runtime data paths, export names, permissions, audit semantics, and rollback behavior.
3. Keep compatibility facades at the existing Python import paths and retain the existing inquiry-result JavaScript entry path and one page initializer.
4. Split tests before data-sensitive production modules. Test discovery identifiers and scenarios must be preserved without weakening assertions.
5. CSS extraction follows the existing foundation/component/page ownership model. Shared assets continue to load from `base.html`; page assets are explicitly owned by their templates. No `@import`, inline code, ID selector, new `!important`, or business selector in shared CSS is introduced.
6. Historical migrations are not rearranged as part of this work. Future migration packaging may be decided separately while preserving the existing migration ledger.
7. Do not add legacy allowlist entries or build a new general complexity-governance system as part of this refactor.

## Consequences

- More files are introduced, but each file has a narrower reason to change and can be tested independently.
- Existing consumers continue to use the same public entrypoints; the change is an internal structural refactor.
- Data-sensitive extraction requires stronger equivalence evidence for database state, archive contents, media hashes, audit events, and failure compensation.
- Frontend extraction requires both direct JavaScript tests and real desktop/mobile browser acceptance because static checks cannot prove event and CSS cascade equivalence.

## Verification

- Compare pre/post test discovery and require all prior test identifiers to remain present exactly once.
- Run focused unit and contract tests for each task package, direct Node tests for extracted JavaScript, Ruff, Pyright, compileall, route snapshot, and OpenAPI snapshot checks.
- For product and business-sync extraction, verify isolated database state, media/archive outputs, path-safety limits, audit failure handling, and rollback behavior.
- For CSS/templates and inquiry-result JavaScript, verify desktop and 390px layouts, keyboard interaction, long text, permission variants, zero console errors, zero asset 404s, and no root horizontal overflow.
- Complete with `uv run python scripts/verify.py --base-ref github/main`, managed local restart, and `scripts.runtime_probe`.
