# ADR 0030: Product Image WebP Pipeline And Deployment Migration

- Status: accepted
- Date: 2026-08-31
- Owners: BLD

## Context

Product catalog photos are uploaded from phones and cameras as JPEG, PNG, or WebP files. The original files can be tens of megabytes, while the catalog is primarily used for web browsing. Loading those originals in the catalog list wastes bandwidth and memory, and embedding them in catalog exports produces unnecessarily large workbooks.

Existing NAS data predates a canonical image format and thumbnail contract. The deployment account cannot access the Docker daemon directly, the NAS host does not provide Pillow, and the existing privileged wrapper exposes only rebuild and status operations. The migration therefore has to run inside the normal Git-driven deployment without requiring manual re-upload or an additional full data backup.

## Decision

1. Accept JPEG, PNG, and WebP product image uploads up to 30 MB and 50 megapixels. Correct EXIF orientation, preserve transparency where present, and publish a canonical WebP large image with a maximum long edge of 1920 pixels and a hard size limit of 500 KB.
2. Generate a separate WebP thumbnail within 320 by 240 pixels and 100 KB. Do not retain the raw uploaded file after both generated files have been published successfully.
3. Catalog desktop and mobile lists load only the generated thumbnail. The large image URL remains available for the image viewer but is assigned to the viewer only after the user clicks the thumbnail.
4. Catalog Excel exports embed the generated thumbnail. When required by the workbook library, convert that thumbnail to PNG in memory for the XLSX package; do not read or embed the large image.
5. Apply the same processing contract to catalog-import images and manual image replacement. Publish the large image and thumbnail atomically and compensate filesystem changes if the database update fails.
6. Migrate active product image references with an idempotent command. Convert and relink one image at a time, commit each successful reference update, remove the replaced active source only after all of its usages succeed, and retain failed sources for diagnosis. Write a machine-readable report.
7. Run the migration as a one-shot Compose service before the web service starts. A maintenance marker makes the previous running web container return 503 while migration overlaps with a rebuild. Individual conversion failures are reported but do not permanently prevent the web service from starting; deployment acceptance still requires a report with no failures.
8. Limit migration scope to active product catalog images. Product image archives, drawings, customer media, and unreferenced files are not rewritten or deleted. No database schema or public API contract changes are introduced.
9. Rely on the NAS daily backup requested by the operator instead of creating another full temporary copy. Per-file atomic replacement and failure retention provide operational recovery within this migration.

## Alternatives Considered

- Retain every original photo. This preserves print-quality sources but conflicts with the web-first storage and bandwidth goal.
- Keep JPEG and PNG according to input type. This complicates delivery and makes the hard size contract less predictable.
- Generate thumbnails on demand. This moves expensive processing into user requests and leaves list performance dependent on first access.
- Require manual re-upload of historical images. This is slow, error-prone, and unnecessary because the existing files can be decoded automatically.
- Run migration in normal application startup. This mixes a potentially long data operation with every web process start and is harder to observe and retry.

## Consequences

- Active catalog images have a consistent, browser-friendly representation and predictable maximum transfer size.
- Lists and exports use substantially less bandwidth and memory.
- Uploaded originals are intentionally unavailable after successful processing, so this system is not a print-quality photo archive.
- The first deployment can remain in maintenance for several minutes while historical images are converted.
- Deployment gains a one-shot migration service and an operational report that must be inspected.
- Unreferenced legacy files can remain on disk and may be reviewed separately; this decision does not authorize their deletion.

## Verification

- Test orientation handling, transparency preservation, dimension bounds, hard byte limits, thumbnail generation, invalid input rejection, and migration idempotency.
- Assert that catalog list image elements reference only thumbnails and that the large URL is assigned only after interaction.
- Inspect XLSX media entries and require dimensions no larger than 320 by 240 pixels.
- Run the full repository verification suite and real browser checks for desktop and mobile catalog behavior.
- After NAS deployment, require an error-free migration report, canonical WebP references, existing large and thumbnail files, large files no greater than 500 KB, matching Git revisions, healthy runtime probes, and a successful second dry run.
