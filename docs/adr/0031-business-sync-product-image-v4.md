# ADR 0031: Business Sync Product Image Package V4

- Status: accepted
- Date: 2026-08-31
- Owners: BLD

## Context

Business data package v3 recursively archived `data/product_images/`. That could include thumbnails, archives, and unreferenced files, while the package did not record which product image slot owned each file. A receiving device therefore copied files without reliably updating product image references or regenerating thumbnails. Different local filenames and pre-existing images made NAS-to-Mac or Mac-to-NAS replacement ambiguous.

Product images now have a canonical web delivery contract: a WebP large image no greater than 500 KB and 1920 by 1920 pixels, plus a generated WebP thumbnail. Cross-device synchronization must preserve a compliant large image without another lossy encode, rebuild the local thumbnail, and replace the intended slot without deleting a file still used by another product.

## Decision

1. New business data packages use manifest v4. The reader continues accepting v1, v2, and v3 packages; versions newer than v4 are rejected with an upgrade instruction.
2. When product images are selected, v4 exports only large WebP files referenced by active products. It does not include `thumbs/`, `archive/`, nested files, or unreferenced files, and exporting does not modify the source device.
3. `media.product_image_slots` records one mapping per exported image slot with `bld_no`, `slot`, `file`, and the lowercase SHA-256 digest. The archive must contain exactly the files named by the mapping. Duplicate slots, unsafe or cross-platform-colliding names, hash mismatches, and incomplete mappings are rejected.
4. Each exported large image must already satisfy the product-image contract: valid non-animated WebP, no greater than 500 KB, and no greater than 1920 by 1920 pixels. A missing explicitly referenced image blocks export instead of silently producing an incomplete package.
5. Import applies product rows first and then resolves every selected v4 image by BLD number and slot. The target filename is canonical for that BLD number and slot, the database image reference is updated, the compliant large-image bytes are preserved exactly, and a new local WebP thumbnail is generated.
6. Main images and thumbnails are written atomically and participate in the existing database/media rollback transaction. Validation, image processing, a later database operation, or audit failure restores the prior files and references.
7. After all references have been updated successfully, the replaced active main image and its thumbnail are removed only when no final product slot still resolves to that file. Shared files remain. Existing import rollback backups keep following the established retention policy.
8. Product images in v1-v3 packages retain their legacy copy-only behavior. Both sending and receiving installations must be upgraded for v4 slot-aware replacement and thumbnail regeneration.
9. This changes the portable package format but does not add a public API, database migration, or new permission. Product images remain an explicit export and import selection under the existing business data synchronization permission.

## Alternatives Considered

- Export the entire product image directory. This preserves unrelated runtime files but makes packages unnecessarily large and cannot express slot ownership.
- Export generated thumbnails alongside large images. Thumbnails are deterministic local derivatives, so transferring them adds size and can preserve stale variants.
- Re-encode every imported large image. This simplifies one pipeline but introduces avoidable generation loss on every device transfer.
- Delete every replaced filename immediately. This can break another product slot that still shares the same file.
- Infer ownership only from filenames. Historical or manually assigned references do not always match the receiving device's canonical name.

## Consequences

- Business data packages contain only the product images needed by the exported active catalog and are smaller than recursive directory copies.
- A package imported on another upgraded installation reliably replaces the same BLD image slot and immediately has a local thumbnail.
- The receiving device stores canonical filenames even if the sender used a different valid filename.
- v4 packages are intentionally incompatible with older readers; upgrading the receiving installation is required before transfer.
- Existing v1-v3 packages remain readable, but they cannot gain slot-aware semantics retroactively.

## Verification

- Assert that export includes referenced large files only and excludes thumbnails, archives, nested files, and orphans.
- Assert exact v4 mapping, digest, media count, resource limits, path checks, collisions, missing references, and tamper rejection.
- Round-trip multiple slots into a device with different existing filenames; require exact preservation of large bytes, regenerated thumbnails, updated database references, and deletion of unreferenced old active files.
- Verify that shared old files remain, image selection can be disabled, legacy v3 remains copy-only, and a post-write failure restores the prior database and media state.
- Run the full repository verification suite, deploy both endpoints from the same Git revision, and verify NAS migration status and runtime health.
