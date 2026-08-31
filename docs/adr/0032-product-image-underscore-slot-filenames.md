# ADR 0032: Product Image Underscore Slot Filenames

- Status: accepted
- Date: 2026-08-31
- Owners: BLD

## Context

The original product image convention used `<BLD>.webp` for slot 1 and `<BLD>-2.webp` through `<BLD>-5.webp` for later slots. That convention is ambiguous when a real BLD number itself ends in a dash and digit. In production, product `K8080LA` slot 2 and product `K8080LA-2` slot 1 both legitimately referenced `K8080LA-2.webp`. Business data package v4 detected the duplicate target and stopped export to prevent an unsafe overwrite.

Operators also require multi-image filenames to identify every slot consistently as `_1`, `_2`, and so on instead of dash suffixes.

## Decision

1. Canonical product image and thumbnail filenames use `<safe BLD>_<slot>.<suffix>` for all five slots. Slot 1 is explicitly `_1`; slots 2 through 5 are `_2` through `_5`.
2. Upload, catalog import, product copy, BLD rename, business data import, and product image migration all use the same naming function.
3. Read fallback and migration continue recognizing the legacy unnumbered slot-1 name, legacy `-1`, and legacy `-2` through `-5` names so an interrupted upgrade does not hide images.
4. The deployment image migration relinks every local referenced image to the underscore name. A compliant WebP large image is copied byte-for-byte and receives a regenerated local thumbnail; it is not lossy-encoded again.
5. If one legacy source file is shared by multiple product slots, migration publishes one canonical underscore target per slot. The shared legacy source is removed only after every referencing slot succeeds.
6. Business data package v4 remains mapped by BLD number and slot. Its receiving device writes underscore filenames, updates every selected reference, regenerates thumbnails, and retains the existing atomic rollback behavior.
7. No public route, permission, or database schema changes are introduced. URLs backed by explicit database references change after migration because the stored filenames change.

## Alternatives Considered

- Permit shared dash targets indefinitely. This preserves existing files but keeps unrelated BLD/slot identities coupled to one mutable image.
- Number only slots 2 through 5. This still leaves two naming shapes and does not meet the explicit `_1`, `_2` operator convention.
- Re-encode every migrated WebP. This is unnecessary and introduces generation loss during a filename-only migration.
- Remove all legacy lookup immediately. This makes partial or interrupted deployments unnecessarily fragile.

## Consequences

- Product image filenames are unambiguous and sortable by slot.
- The first deployment rewrites image references and may create two underscore files from one historically shared dash file.
- Referenced legacy active filenames are removed after successful migration; archive and unreferenced files remain outside migration scope.
- Old explicit references remain readable until migration completes, but all new writes use underscore filenames.

## Verification

- Test slot naming for `_1` through `_5` across upload, catalog import, copy, rename, URL fallback, and business synchronization.
- Migrate a compliant legacy WebP and assert the new large file is byte-identical.
- Migrate a shared `K8080LA-2.webp` source into `K8080LA_2.webp` and `K8080LA-2_1.webp`, then require both references and safe old-source cleanup.
- Verify business package export/import and failure rollback with the historical shared-source case.
- Run the full repository verification suite and require an error-free NAS migration report before accepting deployment.
