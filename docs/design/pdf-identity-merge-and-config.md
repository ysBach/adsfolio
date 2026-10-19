# PDF Identity Reconciliation, Annotation Merge, and Unified Configuration

Date: 2026-08-14

Status: unimplemented proposal, retained for future design work. The behavior and
configuration below are proposed requirements, not current package functionality.
Reconcile this historical proposal with the current code before implementation.

## Objective

Make PDF identification converge every representation of one scholarly work on
the canonical published ADS record, merge annotations into one user-selected PDF,
and preserve all displaced or failed inputs in visible, configurable directories.
Replace the split JSON/catalog configuration authorities with one global TOML file
and one optional library-local TOML override file.

## Scope

This design covers:

- arXiv identifier normalization and ADS published-Bibcode resolution;
- same-work grouping across arXiv, accepted, and publisher PDFs;
- merging two or more PDFs, including groups containing only arXiv PDFs or only
  non-arXiv PDFs;
- exact automatic migration and interactive review of PDF annotations;
- successful-obsolete and failed-merge file handling;
- unified global and per-library TOML configuration;
- migration from the existing JSON and catalog-backed settings.

It does not concatenate pages from different PDFs, automatically delete files,
or use fuzzy text similarity to place annotations without review.

## Terminology

- **Work**: one scholarly publication independent of PDF version.
- **Canonical ADS identity**: the published, non-arXiv ADS Bibcode when ADS has
  one; otherwise the best available ADS/arXiv identity.
- **Canonical PDF**: the PDF selected by the user to remain active after a merge.
- **Source PDF**: any other PDF whose annotations may be transferred.
- **Exact match**: normalized source annotation text occurs exactly once in the
  target text and maps to one unambiguous target character-bound geometry.
- **Merge group**: two or more PDFs established to represent the same work.

## Configuration

### Files and authority

The only non-secret configuration authorities are:

- global: `~/Library/Application Support/adsfolio/config.toml` on macOS, resolved via
  `platformdirs` on other systems;
- library: `<library root>/.adsfolio.toml`.

The library file stores only overrides. The NASA ADS token remains in the current
mode-`0600` token file because it is a secret, not normal configuration. The
central SQLite catalog may cache effective values but is never authoritative.

Precedence is:

1. explicit CLI arguments;
2. `.adsfolio.toml` library overrides;
3. global `config.toml`;
4. application defaults.

The GUI lets the user choose global or library scope when saving settings. Global
is the default scope.

### Schema

Both files use the same versioned schema. Global-only state is ignored with a
warning if present in a library file.

```toml
schema_version = 1

[identify]
orcid = ""
query_names = []
library_ids = []
# Missing years mean no bound; the UI displays empty fields.
# debut_year = 2010
# end_year = 2030
recursive = false
infer_from_filenames = false
verbose = false

[merge]
obsolete_directory = "_PDF-obsolete"
needs_merge_directory = "_needs-merge"

[appearance]
# Existing GUI appearance fields retain their current names and types.

[applications]
pdf_viewer = ""
web_browser = ""

[state]
# Global only: roots, recent selection, window geometry, splitter state, and
# other session-restoration fields.
```

Directory settings are normalized relative paths inside the library root.
Absolute paths, empty paths, the root itself, and any path containing `..` are
rejected. The two directories must be distinct. Identify excludes both configured
directory trees regardless of whether recursive scanning is enabled.

Writes use a temporary sibling file, flush and close it, then atomically replace
the destination. Unknown TOML keys are preserved when the application edits a
known setting so newer configuration is not destroyed by an older client.

### Legacy migration

When `config.toml` does not yet exist, adsfolio imports the existing `gui.json` and
`setup.json`. For each registered library, it imports root-specific Identify
settings from catalog metadata and legacy `.adsfolio-pdfs.json` settings into
`.adsfolio.toml` only when they differ from the global effective value.

Migration is deterministic and idempotent. Legacy files are left untouched so a
failed migration cannot destroy recoverable state. Removing those one-time traces
is a manual operation, not a permanent cleanup command.

## Publication Identity

### Preserved ADS fields

Normal publication fetches and Identify queries request and preserve at least:

- `bibcode`;
- `alternate_bibcode`;
- `identifier`;
- all DOI values;
- the existing bibliographic fields.

Records also store a normalized `arxiv_id` derived from ADS identifiers, arXiv
DOIs, arXiv Bibcodes, PDF metadata/text, or filenames. Version suffixes such as
`v2` are retained as source metadata but excluded from work-identity comparison.

### Query behavior

Modern and legacy arXiv forms are normalized by one shared parser. A known arXiv
ID is queried using ADS's arXiv/identifier search semantics, not as a Bibcode.
When ADS returns a published non-arXiv record, its Bibcode becomes canonical.
Ambiguous ADS results remain unresolved and are never selected by result order.

### Same-work evidence

Two PDFs join the same merge group when any authoritative identity agrees:

1. canonical published Bibcode;
2. ADS alternate-Bibcode relationship;
3. normalized arXiv ID returned by ADS;
4. an exact non-arXiv DOI shared by ADS records.

An identical canonical Bibcode is sufficient, including for old records that do
not contain an arXiv ID. Title/author similarity may suggest candidates to the
user but cannot automatically establish identity.

The catalog continues to model separate physical attachments and content hashes,
but all attachments in a merge group reference the same work document.

## Merge Workflow

### Discovery and selection

After Identify resolves identities, it reports merge groups containing multiple
active PDFs. Groups may contain:

- arXiv plus publisher PDFs;
- multiple downloaded copies of an arXiv PDF;
- multiple publisher/accepted PDFs;
- any mixture of the above.

The user invokes **Merge PDFs** and selects the canonical PDF. The UI recommends a
publisher PDF only when its role is supported by ADS metadata, but never chooses
irreversibly without user confirmation. Merge means annotation consolidation; it
does not concatenate pages.

The preview shows every input path, digest, identified role, annotation counts by
type, destination directories, and any identity ambiguity.

### Successful output

The merge builds a temporary copy of the canonical PDF and applies accepted
annotations from every source. It reopens and verifies the staged result before
changing any source path.

After verification:

1. atomically replace the canonical PDF with the staged PDF;
2. move every noncanonical PDF into
   `<obsolete_directory>/<group-key>/`, preserving unique filenames;
3. save `merge-report.json` beside the obsolete inputs;
4. update the catalog paths, hashes, metadata, and merge provenance in one
   catalog transaction.

The group key uses the canonical Bibcode when available and a stable generated ID
otherwise. Existing destination files are never overwritten; deterministic
numeric suffixes resolve filename collisions.

### Failed output

A failure after the user starts an approved merge must not leave a partial active
merge. The original canonical file remains byte-for-byte unchanged until staged
verification passes.

If extraction, user review, output writing, or staged verification fails, the
original active files stay in place. The failure is registered as a pending merge
and the GUI offers **Move group to needs-merge**. If the user accepts that action,
all files in the group move together to
`<needs_merge_directory>/<group-key>/` through a rollback-capable move
transaction.

After staged verification succeeds, final replacement, obsolete moves, and the
catalog update execute as one recovery-journaled commit. If that commit fails,
adsfolio first restores the original canonical file and source paths. Only after
successful restoration may the user move the group to `needs_merge_directory`.
If restoration itself fails, adsfolio does not perform additional moves; it writes
an emergency report at the library root listing every actual path and digest.

Every failed merge writes a `merge-report.json` in a dedicated report area or,
after an accepted group move, in the needs-merge group directory. It records the
stage, exception category, original and actual paths, digests, and recovery
instructions. If a needs-merge group move cannot be completed, adsfolio rolls back
completed moves; if rollback itself fails, it reports every actual path
explicitly and does not claim atomicity.

Merely discovering an ambiguous group does not move files. A group enters
`needs_merge_directory` only after an explicit merge attempt or explicit user
action to defer it there.

Neither managed directory is ever deleted automatically. Each Identify summary
reports counts and reminds the user to review and remove obsolete files when no
longer needed.

## Annotation Model

### Detection

Every PDF annotation object is enumerated and classified. Text markup includes:

- Highlight;
- Underline;
- Squiggly;
- StrikeOut.

Non-text or geometry-sensitive items include, but are not limited to:

- FreeText/text boxes and text notes;
- Ink;
- Line, Square/rectangle, Circle, Polygon, and PolyLine;
- Stamp;
- FileAttachment;
- any unknown or unsupported annotation subtype.

The preview and review queue prominently count all types. No annotation is
silently ignored. Link annotations generated as document navigation are reported
separately and are not treated as user annotations unless they carry user-edited
content or appearance evidence.

### Automatic text-markup transfer

Automatic placement is intentionally strict:

1. extract the source annotation's selected character text and surrounding
   normalized context;
2. normalize soft hyphens, line-break hyphenation, Unicode normalization, and
   whitespace without changing word content;
3. find the exact normalized character sequence in the target;
4. require exactly one occurrence and one unambiguous character-to-quad mapping;
5. recreate the original subtype using the target quads;
6. preserve color, opacity, and comment text when representable;
7. record an automatic outcome and the evidence used.

No edit-distance, embedding, title similarity, or arbitrary confidence threshold
may authorize automatic placement. Repeated text, changed wording, missing text,
textless markup, or ambiguous geometry enters manual review.

### Interactive review

The approved GUI is the focused dual-pane queue:

- left: source PDF, centered and zoomed on the current annotation;
- right: canonical PDF, centered on the best exact candidate or a suggested page;
- one annotation at a time;
- actions: accept candidate, select exact target, recreate geometry, waive, or
  abort the merge;
- visible progress and annotation subtype/style.

For text markup, user drag selection on the target obtains selected text and PDF
page bounds from Qt PDF's selection API. PyMuPDF writes the annotation using
those page-space bounds. For text boxes, shapes, stamps, and other geometry-based
annotations, the source geometry is highlighted and the user recreates the
target geometry and properties manually. These types are never positioned
automatically because page coordinate similarity does not prove semantic
equivalence across editions.

The source cannot be archived while any user annotation remains unresolved unless
the user explicitly waives that individual annotation. Aborting leaves all active
files unchanged.

### Verification and idempotency

The staged PDF is reopened before replacement. Every expected annotation is
checked for subtype, target page, finite/nonempty geometry, normalized selected
text where applicable, style properties, and comment content. The rendered pages
containing new annotations are visually renderable without parser errors.

Each transferred annotation has a stable fingerprint based on source digest,
source annotation identity/content, target digest lineage, and accepted target
geometry. A repeated merge recognizes fingerprints already present in merge
provenance and does not duplicate annotations.

The audit report records one outcome per annotation:

- `automatic`;
- `manual`;
- `waived`;
- `failed`.

It also records unsupported/nonportable details and the before/after file digests.

## Components and Boundaries

1. **Configuration service**: layered TOML parsing, validation, migration, and
   atomic writes. GUI, CLI, Identify, and merge consume one typed effective
   configuration.
2. **Work identity parser**: shared normalization for Bibcode, DOI, and modern or
   legacy arXiv identifiers.
3. **Publication resolver**: ADS query construction and explicit unique,
   ambiguous, or missing results with evidence provenance.
4. **Merge-group service**: forms groups only from authoritative identity and
   produces a user-reviewable plan.
5. **Annotation extractor/mapper**: enumerates all annotations, performs strict
   exact matching, and returns per-item outcomes rather than aggregate counts
   only.
6. **Merge transaction**: stages, verifies, replaces, moves, reports, and updates
   catalog state with rollback behavior.
7. **Dual-pane review dialog**: presents uncertain annotations and returns manual
   page-space selections or explicit waivers; it does not write files directly.

PyMuPDF becomes part of the GUI installation dependency because annotation
extraction, writing, and verification are required GUI behavior. Qt PDF remains
the viewing and manual-selection surface.

## User-Facing Commands and Actions

- Identify automatically promotes known arXiv identities to published ADS
  records and reports merge groups.
- GUI **Merge PDFs...** opens the group preview and dual-pane review flow.
- CLI merge supports a dry-run plan; any merge requiring manual review exits with
  a clear instruction to use the GUI rather than guessing.
- Identify summaries report counts in the configured obsolete and needs-merge
  directories.
- Configuration editing exposes global/library scope, defaults to global, and
  provides help text for every setting.

## Testing

Tests must cover:

- modern and legacy arXiv parsing, including version suffixes;
- arXiv ID to published Bibcode resolution using ADS identifier semantics;
- preservation of `identifier`, `alternate_bibcode`, all DOI values, and
  normalized `arxiv_id`;
- same-Bibcode grouping for legacy records without arXiv metadata;
- multiple arXiv-only and multiple non-arXiv-only PDF groups;
- user canonical-PDF selection;
- exact unique, repeated, multiline, hyphenated, Unicode, and missing text;
- all recognized text markup subtypes and representative non-text subtypes;
- manual text selection and manual geometry recreation;
- subtype/style/comment preservation;
- per-annotation audit outcomes and idempotency;
- staged verification failure, filesystem move failure, catalog failure, and
  rollback reporting;
- successful obsolete moves and failed needs-merge moves;
- collision-safe destinations;
- configurable directory validation and recursive scan exclusion;
- global/library TOML precedence, unknown-key preservation, atomic writes, and
  legacy JSON/catalog migration;
- GUI configuration scope, tooltips, merge preview, dual-pane queue, waiver, and
  abort behavior;
- CLI dry-run behavior when manual review is required.

At least one end-to-end fixture must use genuinely different source and target
PDF layouts. Verification must reopen the output and inspect its annotations; a
passing mocked call is insufficient evidence.

## Failure Modes and Guarantees

- ADS ambiguity never resolves by result ordering.
- Similar titles never establish same-work identity automatically.
- Repeated exact text never receives automatic annotation placement.
- Non-text annotations never receive automatic coordinate copying.
- Unresolved annotations never disappear silently.
- The canonical file is unchanged until staged verification passes.
- No obsolete or needs-merge file is deleted automatically.
- A successful report is emitted only after filesystem and catalog state agree.
- If rollback cannot restore agreement, the report lists actual paths and marks
  the group as requiring manual recovery.
