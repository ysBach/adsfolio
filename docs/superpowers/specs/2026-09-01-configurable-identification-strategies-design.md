# Configurable PDF Identification Strategies Design

## Problem

`infer_from_filenames` is currently an exclusive fast path. When enabled, it
skips normal PDF DOI, Bibcode, and title extraction. A filename such as
`aa43983-22.pdf` therefore stays unresolved even though the PDF metadata and
first page contain the valid DOI `10.1051/0004-6361/202243983`.

The GUI exposes this exclusive mode as one checkbox, but it does not expose the
individual identification methods or their priority. Bulk Identify and watch
also use different resolver entry points, so their effective ordering can
diverge.

## Goals

- Replace the exclusive filename mode with an explicit ordered strategy list.
- Let every strategy be enabled or disabled and reordered in the GUI.
- Use the same strategy semantics for bulk Identify, manual rename-time
  identification, and watch processing.
- Preserve ADS batching and cached-document reuse without changing strategy
  priority.
- Continue to the next strategy when a candidate is absent, invalid, missing in
  ADS, or ambiguous.
- Surface transport, authentication, and rate-limit failures as failures rather
  than silently treating them as “no match.”
- Keep automatic watch limited to files added after watch starts. Existing
  unresolved files are reconsidered only by an explicit user-run Identify or
  Rename operation.

## Non-goals

- User-editable DOI or Bibcode regular expressions.
- OCR of image-only PDFs.
- Treating AI CSV import as an automatic evidence strategy. It remains an
  explicit post-processing input because it depends on a user-selected file.
- Preserving the `infer_from_filenames` API or `--infer` CLI option. Breaking
  compatibility is explicitly accepted.

## Strategy Catalog and Recommended Order

The canonical stable IDs, labels, and recommended order are:

1. `filename_bibcode` — Filename Bibcode
2. `filename_doi` — Filename DOI
3. `pdf_metadata_bibcode` — PDF metadata Bibcode
4. `pdf_metadata_doi` — PDF metadata DOI
5. `first_page_bibcode` — First-page Bibcode
6. `first_page_doi` — First-page DOI
7. `structured_filename` — Structured filename fields
8. `pdf_metadata_title` — PDF metadata title
9. `first_page_title` — First-page title

The exact identifier strategies precede structured and title matching. Filename
identifiers are first because they are cheap and can avoid parsing a PDF, but a
filename that yields no valid candidate immediately falls through. Thus
`aa43983-22.pdf` reaches PDF metadata DOI and first-page DOI normally.

`IdentifySettings.strategies` is an ordered tuple of enabled strategy IDs.
Omission disables a strategy. At least one strategy is required. Unknown,
duplicate, or malformed IDs are rejected when supplied interactively or by the
CLI; persisted unknown IDs are logged and removed, with the recommended policy
used if no valid IDs remain.

## Resolution Semantics

The resolver processes stages in configured order. For a bulk set, each stage
extracts evidence only for files still unresolved, deduplicates equal
candidates, batches compatible ADS queries, applies unique validated matches,
then sends unresolved files to the next stage. A one-file watch call uses the
same bulk engine with a one-item list.

“First valid unique match wins” means:

- Bibcode: the returned document Bibcode equals the normalized candidate.
- DOI: exactly one distinct ADS document contains the normalized candidate DOI.
- Structured filename: exactly one ADS document matches all normalized parsed
  fields.
- Title: ADS returns one distinct document and its normalized title equals the
  extracted normalized title. A non-unique or non-equivalent result falls
  through.

Equal candidates encountered in later strategies reuse the previous lookup
result, but each strategy still receives its own outcome attribution. A
zero-match or ambiguous result is a normal strategy miss. ADS authentication,
transport, malformed-response, and rate-limit errors abort or defer the
affected operation and remain visible to the caller.

## Cache and Profile Matching

The publication profile cache is a query backend optimization, not an evidence
strategy. Strategies produce candidates in configured order; candidate lookup
checks the local ADS-document registry/publication cache before making a network
query. Refreshing the publication cache remains a one-run operation. This
prevents the old profile-prefetch phase from silently imposing its own
metadata-DOI-first order.

The query estimator uses the enabled ordered strategies and counts only
unresolved candidates that may reach each stage. Its estimate may be an upper
bound for title queries, but it must not report disabled strategies.

## GUI

The Identify dialog contains an “Identification priority” ordered checklist.
Every catalog strategy appears exactly once with a checkbox. The user can drag
rows or use Up/Down buttons; keyboard operation remains available. “Reset to
Recommended” restores the catalog order and enables every recommended strategy.

Reusable controls are:

- strategy order and enabled state;
- recursive scan;
- unresolved destination;
- skip/include unresolved directory;
- verbose operation log.

One-run controls are:

- refresh publication cache;
- allow ADS requests;
- apply unresolved moves;
- show PDF parser warnings;
- optional AI-enriched CSV path.

“Save Defaults” persists only reusable controls for the selected global/library
scope and does not scan or modify PDFs. “Run Identify” runs the current controls
without implicitly saving them. This permits a one-time manual run with
“Include unresolved directory” without changing the watch defaults. Cancel
does neither.

## CLI and API

Remove `--infer/--no-infer` and the `infer_mode` parameters. Add repeatable
`--strategy ID`; when provided, the occurrence order is the complete enabled
policy for that run. When absent, settings from the selected scope apply.

`PdfIdentifier.resolve_many(paths, *, strategies, ...)` is the canonical
resolver. `resolve()` delegates to it for one path. Watch passes
`IdentifySettings.strategies`; Rename’s `--allow-ads` path loads the selected
Identify defaults instead of hard-coding a separate resolver mode.

## Persistence and Migration

Remove `infer_from_filenames` from `IdentifySettings`, global setup JSON, and
library sidecar settings. On reading an old mapping, ignore
`infer_from_filenames` and install the full recommended strategy policy. This is
intentional: preserving `true` would preserve the bug. Writers emit only
`strategies`.

Existing files are not automatically rescanned during migration. Watch applies
the migrated policy only to future arrivals. The user can manually include the
unresolved directory for a one-run repair.

## User Feedback

Resolution records and watch notices include the strategy ID that resolved the
file. Unresolved summaries distinguish “no enabled strategy matched” from an ADS
operation failure. GUI notifications remain concise, while the job log contains
stage counts and failure details.

## Verification

- Settings normalization, persistence, legacy migration, and CLI ordering.
- Exact configured priority, disabled stages, fallthrough after invalid and
  ambiguous candidates, and deduplicated candidate lookup.
- Bulk and one-file resolver parity.
- Regression using `aa43983-22.pdf` or a deterministic fixture with its DOI:
  an uninformative filename falls through to PDF metadata/first-page DOI and
  resolves `2022A&A...668A.132B`.
- Watch receives the same policy but does not revisit preexisting unresolved
  files.
- Identify dialog reordering, check state, recommended reset, scope loading,
  Save Defaults without execution, and Run without persistence.
- Full test suite, Ruff formatting/checks, and relevant documentation examples.

## Assumptions and Tradeoffs

- ADS remains the authority for accepted records; regex extraction alone never
  marks a PDF resolved.
- Enabling all recommended strategies can use more ADS queries than the old
  filename-only mode. Batching, candidate deduplication, and local cache lookup
  control that cost without sacrificing correctness.
- Exact identifiers in filenames are trusted only after ADS validation. This
  avoids treating arbitrary filename text as bibliographic truth.
- Title matching remains last because it is less reliable and potentially one
  query per unresolved candidate.
