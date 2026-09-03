# Custom Citation Styles and ADS Library Integration Design

## Goal

Add two independent capabilities to the adsapi desktop application:

1. Users define an ordered global collection of plain-text citation styles.
   Each style appears as a copyable table column and a copyable detail-pane row.
2. Users add either the selected identified records or every identified record
   in the current local PDF library to an existing or newly created ADS library.

Citation rendering is local and read-only. It never renames a file and never
contacts ADS. ADS library operations are explicit remote mutations and require
confirmation immediately before documents are submitted.

Backward-incompatible configuration and internal API changes are acceptable.
The implementation nevertheless preserves existing user-visible Rename, ADS
link, arXiv link, file-open, file-reveal, and PDF-watch behavior.

## Existing system constraints

The application is a PySide6 desktop GUI. `MainWindow` owns a paged
`FileTableModel`, a `QTreeView`, and a `DetailPane`. Table columns are currently
declared in a static `COLUMNS` tuple. Detail values already use a
`CopyableLabel`, while Bibcode and arXiv table cells open external links.

Local PDF libraries are filesystem roots backed by the central SQLite catalog.
They are distinct from personal ADS libraries. A resolved catalog record has an
ADS document association and a Bibcode. The existing ADS client already lists,
retrieves, creates, and adds documents to ADS libraries.

The ADS/SciX library contract accepts at most 2,000 Bibcodes in one document
management request. The implementation uses that contract limit, not a local
performance heuristic.

## Chosen architecture

### Plain-text citation domain

Add a Qt-free citation module containing an immutable `CitationStyle` value
object, validation, metadata-to-template-value conversion, and rendering. It
must not call `PdfRenamer.render`: filename normalization is lossy, appends
`.pdf`, and has filesystem-specific length and character rules.

Each `CitationStyle` contains:

- a stable generated ID;
- a user-visible non-empty name;
- a non-empty template;
- author format: `surname`, `surname_initials`, or `last_comma_first`;
- inclusive maximum author count before truncation;
- number of leading authors retained after truncation;
- author separator;
- author truncation suffix.

The author-count invariant is
`1 <= authors_when_truncated <= max_authors <= 99`, matching the Rename model.
The `ads_custom:` author mode is excluded because citation columns must render
from local metadata without per-cell network access.

Style names are unique after trimming and case folding. Stable IDs, rather than
names or list indexes, form table/detail preference keys. Renaming or reordering
a style therefore does not discard its visibility, font, color, or placement.

Template validation uses Python's formatter parser but permits only the exact
field names documented below. Attribute access, item access, conversions, and
format specifications are rejected. Unmatched braces and unknown fields are
reported before saving. Literal braces use `{{` and `}}`.

Rendering follows these rules:

- scalar values and the first element of sequence metadata are accepted;
- ADS HTML markup and entities are converted to plain text;
- spaces, punctuation, and Unicode are preserved;
- missing known metadata produces an empty string;
- no filename sanitation, whitespace substitution, byte cap, or `.pdf` suffix
  is applied;
- a rendered empty string is displayed as an empty cell/row and cannot be
  copied.

### Complete field-help text

The style editor shows the complete reference in a visible, scrollable help
panel. The Template control also exposes the same reference through its help
button and accessible description.

- `{bibcode}` — ADS Bibcode, unchanged.
- `{year}` — publication year.
- `{title}` — first title value, with ADS HTML converted to plain text.
- `{first_author_surname}` — surname of the first author only. For
  `Bach, Yoonsoo P.`, this renders `Bach`.
- `{first_author_initials}` — first-author surname followed by given-name
  initials. For `Bach, Yoonsoo P.`, this renders `BachYP`.
- `{authors}` — author list using the selected author format and separator. If
  the total author count exceeds **Max authors**, retain **Authors when
  truncated** leading authors and append the configured truncation suffix.
- `{authors_without_suffix}` — the same rendered author list, but never append
  the truncation suffix.
- `{journal}` — first `bibstem` value, falling back to `pub` when `bibstem` is
  absent.
- `{bibstem}` — first ADS `bibstem` value only; it has no fallback.
- `{pub}` — ADS publication or journal text.
- `{volume}` — publication volume.
- `{page}` — first page or article identifier.
- `{{` and `}}` — literal opening and closing braces.

The helper also states:

- Missing metadata renders as an empty string.
- Spaces, punctuation, and Unicode are preserved.
- ADS HTML is converted to plain text.
- No `.pdf` suffix is added.
- Unknown fields, unmatched braces, duplicate names, and empty names/templates
  block saving and identify the affected style.
- The ambiguous Rename placeholders `{first_author}`, `{first_author_full}`,
  `{authors_N}`, and `{journal}`'s historical undocumented behavior are not
  silently aliased. The citation field names above are authoritative.

The initial style for a configuration that has never stored citation styles is:

```text
Name: My bibcode 1
Template: {first_author_initials} ({year}) {bibstem}, {volume}, {page}
Author format: surname_initials
Max authors: 3
Authors when truncated: 1
Author separator: ", "
Truncation suffix: " et al."
```

An explicitly saved empty style list remains empty; it does not recreate the
initial style on the next launch.

### Persistence and style management

Advance the GUI configuration schema and persist citation styles as one global
ordered list. Loading validates each field independently. Structurally invalid
entries are skipped with a warning; malformed individual settings fall back to
documented defaults. Duplicate IDs or names are resolved by retaining the first
valid entry and warning about later entries. A legacy configuration with no
citation-style key receives the initial style above.

Add `Tools -> Custom Citation Styles...`. The dialog contains an ordered style
list and an editor for the selected style. It supports Add, Duplicate, Rename,
Move Up, Move Down, Delete, Preview, Save, and Cancel. Add chooses the lowest
unused positive `My bibcode N` display name. Duplicate creates a new stable ID
and a unique `Copy of ...` name. Delete is allowed until no styles remain.

Preview uses the currently selected table record when one record is selected.
Otherwise it uses a clearly labeled example record. Preview never saves and
never contacts ADS. Save validates the entire collection atomically; any error
keeps the dialog open and identifies the style and control. Cancel leaves the
configuration and live table/detail layout unchanged.

After Save, `MainWindow` replaces its runtime style collection, resets the
table model once, and refreshes the detail pane. Newly created styles are
visible by default. Deleted style IDs are removed from column/detail order,
visibility, font, and color maps. Reordering styles changes only the relative
order of custom-style keys while retaining their placement relative to ordinary
columns and rows where possible.

### Dynamic table columns and detail rows

Replace the static-column dependency with runtime column definitions owned by
the table model. Existing columns remain base definitions. One generated column
per citation style uses the preference key `citation:<stable-id>`, the style
name as its header, and the plain-text renderer as its getter.

Generated citation columns initially appear immediately after Bibcode, in style
order. They support the existing hide/show, drag ordering, width, font, color,
tooltip, and accessibility behavior. They are not sortable because sorting the
paged SQLite result by a computed display string would require materializing
the entire result set. Attempting to sort one leaves the active database sort
unchanged.

A primary-button click on a non-empty citation cell copies the full rendered
text, shows the standard `Copied` feedback, and keeps normal row selection.
Bibcode and arXiv cells continue to open their links; filename cells and table
double-click behavior remain unchanged.

The detail pane creates a computed row for each style immediately after
Bibcode. These rows use `CopyableLabel`, participate in existing ordering,
visibility, font, color, and spacing preferences, and have no edit or metadata
lock buttons. Editing the source bibliographic metadata or refreshing the
record recomputes every citation row.

### ADS library domain service

Add a Qt-free service over `ADSClient.libraries` that provides behavior beyond
the raw endpoint wrapper:

- paginate the user's ADS libraries until the endpoint returns no additional
  entries;
- expose only `owner`, `admin`, and `write` libraries as selectable, while
  retaining read-only or unknown permissions for disabled display;
- create a library without implicitly adding the pending document set;
- retrieve every destination Bibcode by advancing the endpoint's `start`
  offset in pages of at most 2,000, using `num_documents` when available and an
  empty/short page as the termination condition;
- submit additions in stable-order chunks of at most 2,000;
- stop after the first failed chunk and return a structured partial result.

The service accepts an already deduplicated immutable Bibcode sequence. It does
not read Qt widgets or SQLite. It returns typed result objects containing
successful, already-present, failed-or-uncertain, and unattempted Bibcodes,
along with the original exception category when applicable.

A transport failure after a request is sent has an uncertain server outcome.
Those Bibcodes are labeled **status unknown**, not definitely failed. Retry
first reloads the destination library and submits only Bibcodes still absent.
If reconciliation also fails, the UI explains that retry may resubmit the
uncertain batch and requires another explicit confirmation.

### ADS library dialog and jobs

Add `Add to ADS Library...` to the Tools menu and table context menu. If the
context-clicked row is not part of the current selection, that row becomes the
sole selection before the dialog opens.

The dialog offers two source scopes:

- **Selected rows**, enabled only when at least one currently loaded table row
  is selected;
- **All identified files in this local library**, independent of the active
  search, virtual view, and table pagination.

Selected scope snapshots the selected records. All-identified scope queries the
current registry directly with `resolved=True` and no search/view filter.
Records are eligible only when they are resolved and contain a non-empty
Bibcode. The GUI computes and displays:

- source file count;
- unique eligible Bibcode count;
- unresolved/ineligible file count;
- duplicate local Bibcode count.

The destination section loads ADS libraries asynchronously and shows name,
document count, visibility, and permission. It has Refresh and Create Library
actions. Create Library collects a non-empty name, optional description, and
public/private choice, creates the empty library asynchronously, then selects
it in the destination list. It does not submit pending documents.

When a destination is selected, the dialog asynchronously fetches all existing
destination Bibcodes. A complete fetch gives an exact already-present count and
excludes those Bibcodes from submission. If the fetch fails, the duplicate
count is shown as unknown and the confirmation states that all eligible
Bibcodes will be submitted.

One final confirmation shows destination name, destination permission, unique
eligible count, already-present count or unknown status, excluded count, local
duplicate count, and number to submit. No document mutation occurs before this
confirmation.

Network work runs outside the GUI thread. A worker owns and closes its own
`ADSClient`; no requests session, SQLite connection, mutable catalog, or Qt
widget crosses the thread boundary. The GUI thread snapshots immutable
Bibcodes before starting the worker. Only one operation for this dialog runs at
a time, and the active operation can be cancelled before the next request
starts. Closing the dialog requests cancellation and waits for clean worker
shutdown without blocking indefinitely.

### Result and error handling

On complete success, the dialog reports newly added, already present, locally
duplicated, and excluded counts. On partial success, successfully acknowledged
batches remain in ADS. The result lists successful, status-unknown,
unattempted, already-present, locally duplicated, and excluded counts and
offers Retry Remaining.

Errors are distinguished as follows:

- missing or rejected token: authentication guidance;
- `read` or unknown destination permission: mutation disabled;
- HTTP 429: rate-limit message including reset information when available;
- transport failure: uncertain outcome and reconciliation requirement;
- other ADS response failure: endpoint message without exposing credentials;
- local registry busy/error before submission: no ADS request starts;
- library deleted or permission changed between confirmation and submission:
  stop and refresh destinations.

No filesystem path, PDF, local metadata, catalog record, Rename setting, or
watch state is mutated by this workflow.

## Data flow

### Citation display and copy

1. GUI configuration loads and normalizes global `CitationStyle` objects.
2. `MainWindow` builds base plus generated table columns and configures the
   detail pane with the same style objects.
3. The model or detail pane asks the pure renderer for a value using the
   record's cached metadata.
4. Display strips ADS HTML but otherwise preserves text exactly.
5. A click copies that same complete rendered value.

### Add to ADS library

1. `MainWindow` snapshots either selected records or all resolved records in
   the current root.
2. The GUI derives unique Bibcodes and exclusion/duplicate counts locally.
3. A worker lists destinations and, after selection, fetches destination
   documents for duplicate detection.
4. The user reviews one final confirmation.
5. A worker submits missing Bibcodes in API-sized chunks and emits structured
   progress/results.
6. The dialog reports exact acknowledged and unresolved states; retry begins
   with remote reconciliation.

## Testing strategy

Implementation follows test-driven development. Tests cover:

- pure rendering for every field, HTML conversion, Unicode/punctuation,
  missing metadata, author thresholds, literal braces, and invalid syntax;
- style normalization, stable IDs, unique names, legacy default creation,
  explicit empty lists, and configuration round trips;
- style-manager add/duplicate/rename/reorder/delete, full help text, preview,
  atomic save, and Cancel behavior;
- runtime dynamic columns, non-sortability, header/layout persistence,
  click-to-copy behavior, empty cells, and unchanged link/file actions;
- dynamic detail rows, recomputation, copy behavior, ordering, visibility,
  appearance controls, and absence of edit/lock actions;
- all-identified versus selected collection, filter/pagination independence,
  eligibility, deterministic deduplication, and count calculation;
- ADS library pagination, permission filtering, empty library creation,
  existing-document reconciliation, 2,000-item request boundaries, complete
  success, partial success, uncertain transport outcomes, and retry;
- worker ownership/cleanup, GUI responsiveness signals, confirmation gating,
  cancellation boundaries, and distinct user-facing error categories.

Verification runs focused tests during development, then isort, Ruff format,
Ruff check, and the complete pytest suite under the selected Python environment.

## Documentation

Update the GUI documentation with:

- creating and managing citation styles;
- the complete field reference and escaping rules;
- table/detail copy behavior;
- selected and all-identified ADS library workflows;
- confirmation, duplicate detection, batching, partial results, and retry;
- the distinction between local PDF libraries and remote ADS libraries.

## Isolation and delivery

Development occurs on `codex/custom-citations-ads-libraries` in an isolated Git
worktree based on a non-invasive snapshot of the previously verified working
tree. The snapshot does not alter the original checkout's index, tracked-file
changes, or untracked files. Integration with later repository commits uses a
normal rebase or merge after the feature is verified.
