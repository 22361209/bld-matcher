# ADR 0034: Product Development Register and Immutable Engineering Revisions

- Status: accepted for implementation; runtime migration and activation pending separate approval
- Date: 2026-10-02
- Owners: BLD

## Context

One development order coordinates a four-digit family such as 8461 and one or more explicitly named finished products such as K8461LA and K8461RA. Each product contains a hierarchy of components. Display identity, borrowed source identity and development work identity are different concepts. Existing material imports can recreate integer IDs; neither those IDs nor assumed unique product codes provide durable references.

## Decision

1. Add a `development` domain within the existing modular monolith. Web adapters use application services; repositories implement SQLite persistence through the existing single-process migration entry point. No framework, database, job queue, external provider or production ERP integration is introduced.
2. Persist permanent UUIDs for orders, finished-product branches, component occurrences, development tasks, operations, suppliers, borrowed-source records, documents and revisions. Keep component display code and borrowed source code in separate fields, with a foreign key to an immutable borrowed-source UUID. Source codes are not unique: reusing a source is an explicit selection, and changing source identity creates a new record while preserving history. A four-digit family is grouping information only; the system never invents prefixes, suffixes or shared relationships from code similarity.
3. Each component occurrence owns its branch, hierarchy, code, borrowed code, quantity, position and notes. Explicitly shared occurrences reference one task. Work status and shared engineering information therefore stay synchronized after reopening. Independent copies retain useful input but reset completion and actual dates. Drawing adoption is explicit during copying. This release excludes detailed handedness, press-fit direction, angle and depth models.
4. New development, borrowing and existing selection form one dimension; in-house, purchased and subcontracted production form another. Arm material shortcuts do not conflate material grade with forming process. Track named owners, planned and actual dates, suppliers and ordered operations. Completion requires production readiness and responsible-person confirmation; partial finished-product completion produces a partially completed order.
5. Engineering files use immutable revision UUIDs and SHA-256 digests, allowlisted attachments and PDF preview. Capture document number, revision label, change description, uploader and time. Tasks and operations adopt specific revisions. A newer revision prompts manual adoption and never silently replaces a fixed revision. Legacy file adoption creates a stable snapshot. Process documents use draft, released and obsolete states without online authoring or complex approval.
6. Every mutation validates ownership, references and acyclicity, uses the order version for optimistic concurrency, writes audit information from authenticated server identity and commits transactionally. Files are staged and atomically promoted with compensation on failure. Archive preserves referenced history. Separate view, edit and file-view permissions use existing roles, CSRF and permission checks.
7. Extend the existing business package with a new compatible version and an optional development dataset. Treat a changed existing order as a whole-order conflict requiring explicit acceptance, independent of timestamps. Absent records do not request deletion; repeated packages are idempotent. Rebuild references with permanent UUIDs and reject missing, cyclic or foreign-owned dependencies.
8. Development file transfer is isolated from legacy media replacement. A revision UUID with a different digest is rejected. Only accepted records and their files are applied. Preview binds package data and local revision-file contents, and apply verifies digests again before committing. Use the existing lock, consistent SQLite backup, audit and compensating transaction patterns. Do not change unrelated legacy media behavior.

## Operations and data ownership

Implementation and acceptance use synthetic databases and files only. No production Mac migration, port 5055 restart, NAS connection, deployment, push or live synchronization is authorized by this implementation decision. Runtime rollout requires a consistent database backup and explicit confirmation after reporting the migration and application changes. Mac and NAS remain independent owners of their business data until a reviewed sync decision is made.

On 2026-10-02 the user separately authorized local Mac mini activation. After stopping the verified 5055 instance, a complete SQLite Backup API snapshot passed integrity, foreign-key and table-fingerprint checks. Migration 043 was applied before the managed launcher restarted 5055; all 31 pre-existing non-migration tables retained their fingerprints, and readiness and runtime probes passed at version 043. No NAS operation, cross-device synchronization, push, account creation or security-setting change was performed.

## Verification

Exercise historical migrations; hierarchy and borrowing; shared and independent copies; responsible-person completion; pinned revisions and manual adoption; rejected uploads; permissions, CSRF, concurrency, audit and rollback; package compatibility, conflicts, idempotency, dependency validation and file integrity. Run the unified verification entry point and desktop/mobile browser flows with isolated data. Preserve every unrelated pre-existing working-tree change and retain baseline/diff evidence.
