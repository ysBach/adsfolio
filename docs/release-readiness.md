# Release readiness

**Status: preparation in progress; do not tag a formal release yet.**

This review covers the current working tree on `main`, including substantial
changes that predate the review. No release version was chosen, no files were
committed, and nothing was published.

## Full application rename and release preparation

The namespace, entry points, bundle IDs, source directory, documentation,
packaging spec, and release URLs now use ADSfolio. The previous Python namespace
and command aliases are not shipped. Old data identifiers remain only in migration
readers and tests, plus ignore rules for private files. Git history remains intact.

Validation after the rename: **1,128 tests passed, one skipped**. Strict Sphinx
and the static site build pass. The renamed wheel contains only `adsfolio` modules
and entry points; isolated installation, CLI, GUI dependency guidance, and offline
catalog operations pass. Homebrew cask Ruby syntax and workflow YAML parse checks
pass. These do not establish remote CI or Homebrew installation success.

Repository creation, versioning, release tagging, and tap scripts default to
preview. PyPI and Pages publishing require explicit repository configuration.
No remote repository, tag, release, or website was published during preparation.
See [Publishing releases](releasing.md) for the complete sequence.

## ADSfolio validation: 2026-10-19

All 231 reported test failures are fixed. The complete suite passes with
**1,117 passed and one platform-specific skip**. Tests cover external DOI
identification and renaming without an ADS token, provider priority, timeout
safety, retained metadata and attachment identity, and stale-content guards.

ADSfolio branding, Python-generated logo assets, non-ADS metadata readiness,
Crossref/DataCite lookup, and provider configuration are implemented. The full rename uses `adsfolio` imports and commands. Earlier settings and
catalog data migrate to new storage without deleting their source. The original
PDF title extraction fix remains covered by typography and ADS-verification
tests. Test catalogs are disposable; the personal PDF library was not modified.

Strict Sphinx, wheel and source builds, and an isolated base-wheel installation
pass. The Apple Silicon macOS bundle passes the frozen Qt/PDF/ISO4/CLI smoke
test and app-metadata check. The Identify dialog and generated logo were
visually inspected. These checks do not replace native acceptance on other OSs.

The current blocking Ruff rules, isort, and formatting checks pass. Full Ruff
reports 61 nonblocking findings: 22 BLE001, 11 SIM117, six TRY004, five SIM113,
four each DTZ005/SIM102/B008, three UP037, and two I001. Exception boundaries,
public exception types, local timestamps, Qt signatures, and progress counters
retain existing behavior. Nested test contexts and quoted annotations are
stylistic; isort owns import ordering. No broad suppression was added.

Release workflows target macOS ARM64/Intel, Windows x86-64, and Ubuntu x86-64.
Native Windows, Linux, and Intel macOS acceptance is not established by local
Apple Silicon tests. Signing, notarization, dependency redistribution review,
and clean-machine installation remain release gates. No release was published.

The following sections record earlier review checkpoints; their test and lint
counts are historical.

## Prepublication review: 2026-10-19

The independent review retained the earlier GUI layout, selection, notification,
citation validation, search completeness, and BibTeX parser fixes. DOI URL
recognition was correct, but batch matching still stripped punctuation from
ADS responses. Complete DOI inputs now preserve punctuation on both sides of
the comparison. Free-text extraction retains its existing punctuation rules.

Additional fixes:

- The table stores one sorted query result per reset. Loading subsequent pages
  does not repeat the full catalog query. Library switches and refreshes use
  one reset. Numeric file sizes and modification times sort numerically, and
  the arXiv column and its sort share one parser.
- Document, attachment, location, and digest lookups use SQL filters and indexes.
  Reference imports return the imported records without decoding unrelated
  documents. Legacy migration checks completed-source markers before reading
  files and preserves settings already saved in the catalog.
- PDF discovery recognizes mixed-case extensions. Watch scans report access
  failures without marking unseen files missing. Known inode matches at new or
  returning paths require matching content digests. Files are hashed before
  the catalog write transaction; unchanged paths are not rehashed by discovery.
- Downloads close streamed responses, deduplicate bibcodes, and write through
  unique temporary files. Failed streams and invalid fallback payloads preserve
  an existing destination. Temporary files are closed before replacement.
- Raw BibTeX summaries use the structured parser. Default LaTeX highlighting
  emits valid braces, while CV author separators retain their established
  output. Invalid infinite citation counts follow the existing fallback
  policy. Documentation and comments describe current behavior without review
  orchestration or uncertain historical narration.

### Performance evidence and tradeoffs

On an isolated catalog with 20,000 records and 800-character abstracts, loading
all 40 pages took approximately 7.8–20.5 seconds before snapshot paging and
0.19–0.43 seconds afterward, depending on the sort. These are local best-of-three
measurements, not a cross-platform performance guarantee. The snapshot retained
about 59 MB, with an 81 MB peak. Memory grows with the matching records, and
changes become visible at the next reset.

SQLite query plans use `idx_attachment_document` and `idx_location_attachment`
for document lookup, and `idx_version_sha256` and `idx_location_version` for
digest lookup. No SQLite JSON extension or new runtime dependency is required.

### Deliberate limits

- Title extraction uses typography candidates with exact ADS verification.
  Uniform-font OCR and mixed-size titles can still need a verified identifier.
  Broad heading searches can require several requests when a batch is split.
- Downloads share the configured HTTP session. Session calls are serialized,
  while response bodies stream in parallel. Callers must not mutate adapters
  or close the client during active downloads.
- An inode candidate without a stored digest retains the existing identity
  rule. A file edited and renamed together may receive a new attachment when
  its digest differs. Refresh preserves metadata on an existing path; Identify
  and Watch can re-identify changed content. These are distinct operations.
- Nested HTML conversion supports the documented formatting tags. Markdown CV
  titles retain their established LaTeX output, documented in the developer notes. Custom formatting templates are used
  literally; no automatic template-repair heuristic was added.

A proposed author-joining change was rejected because it changed established
CV punctuation and inserted a LaTeX conjunction into Markdown output.

Native desktop behavior, live ADS mutations, and the remaining release decisions
below still need their stated checks. Personal catalog cleanup is separate from
package validation and was not performed.

### Final local validation

- Python 3.13: 1,054 tests passed, one platform-specific test skipped.
- First-page-only identification of `aa34764-18.pdf` matched
  `2019A&A...630A..22B` against live ADS. Its default and saved strategy orders
  also matched through the metadata DOI. The final first-page-only live check
  took 54.4 seconds; broad heading probes remain costly. In eight previously
  oversized-query PDFs, the longest extracted candidates ranged from 51 to 682
  characters, rather than entire pages. Six matched metadata titles locally;
  this is not a claim that all eight resolved against ADS.
  The deleted Wright PDF could not be
  rerun; its DOI and correct Bibcode were checked against live ADS.
- Typography regression tests cover transformed sizes, larger mastheads,
  whitespace runs, ligatures, fontless form aggregates, same-line authors,
  conflicting matches, incomplete responses, and verified-only cache updates.
- SQL counts match record-query results across supported filters and avoid
  metadata decoding. A local 20,000-record benchmark measured 0.173 seconds
  for record-loading counts and 0.040 seconds for SQL counts, best of three.
  Source and duplicate filters retain their record-loading behavior.
- Nested HTML and literal math-mode characters compiled with `pdflatex`.
  CV author joining matches the original source and committed examples.
- Strict Sphinx, source and wheel builds, and an isolated base-wheel smoke test
  passed. The smoke test covered CLI version, missing-GUI guidance, SQL counts,
  local reference export, and nested formatting outside the source checkout.
- Blocking Ruff, formatting, isort, and whitespace checks passed.
- Test catalogs use temporary paths. Validation did not clean or modify the
  personal library. Native desktop behavior and live ADS mutations remain
  outside these checks.

### Ruff review

The broader check of `src tests packaging` fell from 116 to 47 findings.
No broad suppression was added. The retained findings are:

| Rule | Count | Reason retained |
| --- | ---: | --- |
| BLE001 | 23 | Parser, GUI-worker and download error boundaries retain their current failure behavior. |
| TRY004 | 5 | Changing `ValueError` to `TypeError` would change public exception contracts. |
| DTZ005 | 4 | Local timestamps and established CV output remain unchanged. |
| B008 | 4 | Qt virtual methods retain their `QModelIndex()` defaults. |
| I001 | 2 | isort owns import ordering; its check passes. |
| SIM102 / SIM113 | 9 | Existing guard structure and progress counters remain explicit. |

The repository-wide check also reports one EXE001 finding for the documented
example script's shebang without executable permissions. These findings are
reviewed debt, not a claim that full Ruff is clean. Blocking Ruff rules
(`E4,E7,E9,F`), formatting and isort remain required release checks.

## Verified improvements

- Top-level discovery preserves nested catalog records instead of hiding them.
- Custom-format exports send their format string; empty custom formats fail
  before making an ADS request.
- The base-wheel GUI launcher gives the installation command for the GUI extra.
- Single-reference lookup fetches one document and its locations instead of
  deserializing the complete catalog. SQL still joins attachments and locations;
  this is a reduction in Python work, not a measured latency guarantee.
- Build and runtime versions have one source. CI now covers Python 3.10–3.13,
  docs, formatting, core lint, and an installed-wheel smoke test.

## Defects resolved in the 2026-10-17 review pass

| Area | Trigger and consequence | Resolution implemented |
| --- | --- | --- |
| Legacy BibTeX parser | `parse_bibtex_entries("@article{k,title={A {nested} title}}")` returned no entries; multiline nested fields truncated. | Replaced regex parsing with `BibTexParser` and `pyparsing.Located` on the entry expression so nested braces, multiline/quoted fields, `@string` macros, comments, and exact `BibEntry.raw` slices are preserved. |
| Search completeness | `query_all()` returned a partial result if ADS reported remaining matches but supplied an empty later page. | `SearchAPI.query_all()` now raises `ADSError` when an empty page arrives before reaching the expected target count, while allowing zero-match queries and `max_records` caps. |
| Citation style validation | Direct construction accepted boolean author counts and non-string separators; invalid author-format containers raised `TypeError`. | `CitationStyle.__post_init__()` now rejects boolean limits, non-string separators/suffixes, and non-string `author_format` values with `CitationStyleError`, preserving `from_mapping()` normalization. |
| Identifier input | Update from ADS / `--identifier` classified `https://doi.org/...` and `http://dx.doi.org/...` resolver URLs as Bibcodes. | `_normalize_doi()` and `_parse_ads_identifier()` now extract and percent-decode HTTP(S) `doi.org` / `dx.doi.org` resolver paths while leaving non-resolver URLs untouched. |
| GUI refresh & layout | Deleting the selected file externally or filtering to an empty view left stale metadata in `DetailPane`; default splitter and detail-row geometry clipped wrapped fields and action buttons; `NoticeBar` rendered `&Rename` as `_Rename`. | Connected `modelReset` and selection synchronization to clear stale detail/action state; fixed `DetailPane` height-for-width container/row geometry and default splitter sizing; escaped `&` in `NoticeBar` button labels. |

## Release decisions and external checks

1. Select the release version and supported API. Keep GUI internals explicitly
   provisional; retain existing registry aliases for compatibility.
2. Run the new workflows on GitHub. Local macOS checks do not establish Linux,
   Windows, or Intel macOS compatibility. Validate Python 3.11 and 3.12 in CI.
3. Decide whether the release includes native binaries. Signing, notarization,
   clean-machine installation and desktop integration remain unverified here.
4. Verify ADS behavior with authorized disposable data before promising remote
   mutation or all-author exports. Mocked requests check local behavior only.
5. Review bundled dependency notices before distributing binaries. This pass
   changes license metadata syntax, not licensing terms.

## Maintainability decisions

Keep the existing boundaries: CLI and GUI call domain services; `PdfCatalog`
owns persistence. Reuse the ADS library service instead of duplicating request
batching in command handlers. Avoid moving the large CLI and GUI modules simply
to reduce their line counts; split a coherent operation when its tests can move
with it. Domain parsing must not depend on GUI or catalog modules.

The full Ruff run initially reported 121 findings. The blocking CI rule set
is explicit (`E4,E7,E9,F`), with independent formatting and isort checks. Broader
findings are debt, not a clean full-lint result. Review broad exception handlers,
mutable class defaults and exception-type changes before automatic fixes.

Further targeted work: consolidate repeated catalog resolution invariants.
Typography-based title extraction was approved explicitly; it remains a
heuristic whose candidates require ADS verification.

## Validation

Baseline: 874 tests passed, one platform-specific test skipped; strict Sphinx
and wheel/source builds passed. The repository had no open GitHub issues or
pull requests when checked during this review. That is not evidence that the
package has no defects.

The subsequent Gemini package review and independent patch verification passed
927 tests with one skip, 23 separate acceptance checks, strict Sphinx, the
blocking lint/format/import checks, distribution builds, and installed-wheel
smoke checks. Its baseline was 917 tests with one skip. Broader Ruff debt fell
from 120 findings to 119 with no additional findings. See the
[package review report](design/2026-10-17-gemini-package-review.md) for scope,
visual evidence, reviewer corrections, and remaining gaps.
